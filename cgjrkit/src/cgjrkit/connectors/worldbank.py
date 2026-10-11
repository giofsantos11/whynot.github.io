"""World Bank Indicators API v2 connector (WDI, WGI, GFDD, WDI Database Archives).

Endpoints used (all ``GET``, keyless, JSON via ``format=json``)::

    https://api.worldbank.org/v2/country/{c1;c2}/indicator/{i1;i2}?source=2&date=2000:2024
    https://api.worldbank.org/v2/sources/57/country/{c}/series/{s}/time/{YR2000;...}/version/{YYYYMM}
    https://api.worldbank.org/v2/country                    (economies and aggregates)
    https://api.worldbank.org/v2/indicator/{code}           (indicator metadata)
    https://api.worldbank.org/v2/sources                    (database list)
    https://api.worldbank.org/v2/sources/57/version/all     (WDI archive versions)

Response formats (see ``docs/apis/worldbank.md`` for sources and what is
verified):

* *Indicator queries* return a two-element JSON array ``[header, rows]``. The
  header carries ``page``, ``pages``, ``per_page``, ``total``, ``sourceid`` and
  ``lastupdated``; ``rows`` is a list or ``null``. Each row has
  ``indicator{id,value}``, ``country{id,value}``, ``countryiso3code``,
  ``date``, ``value`` (number or null), ``unit``, ``obs_status`` and
  ``decimal``.
* *Advanced (multi-dimensional) queries*, used for the WDI Database Archives
  (source 57, dimensions country/series/time/version), return a JSON object
  ``{"page", "pages", "per_page", "total", "lastupdated", "source": {"id",
  "name", "data": [{"variable": [{"concept", "id", "value"}, ...], "value"}]}}``.
* *Errors* come back as ``[{"message": [{"id", "key", "value"}]}]`` (often
  with HTTP 200), or as XML ``<wb:error><wb:message id=.. key=..>`` when the
  API ignores ``format=json``.

Design: :func:`build_request`, :func:`parse`, :func:`parse_page`,
:func:`parse_countries`, :func:`parse_indicator_meta` and helpers are pure;
:class:`WorldBank` wires them through :func:`cgjrkit.core.http.fetch`.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from cgjrkit.connectors.base import Connector
from cgjrkit.core import http
from cgjrkit.core.cache import Cache
from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import empty_frame, make_frame, ppp_base, validate_frame, year_of

BASE_URL = "https://api.worldbank.org/v2"

#: rows per page; the API default is 50. 10,000 matches wbwdi's default and
#: keeps typical panels to one request.
DEFAULT_PER_PAGE = 10_000
#: upper bound enforced by the wbwdi R client (third-party; UNVERIFIED upstream).
MAX_PER_PAGE = 32_500
#: "A maximum of 60 indicators can be used" (API Basic Call Structures).
MAX_INDICATORS_PER_REQUEST = 60
#: "A maximum of 1,500 characters are allowed between two back-slashes" and
#: "4,000 characters ... in the entire URL" (API Basic Call Structures). We keep
#: head room, as wbgapi does (it chunks at 1,400).
MAX_SEGMENT_CHARS = 1_400
MAX_URL_CHARS = 4_000
#: safety cap on pagination loops
MAX_PAGES = 1_000
#: WDI Database Archives
ARCHIVE_SOURCE = 57


@dataclass(frozen=True)
class SourceInfo:
    dataset: str
    citation: str


#: Known World Bank API source ids. 2, 3 and 57 are verified (wbgapi README,
#: official API docs mirror); 32 (GFDD) is UNVERIFIED: it is the id listed for
#: "Global Financial Development" in /v2/sources as far as we know, and a live
#: test checks it. Unknown ids fall back to ``"WB source <id>"``.
SOURCES: dict[int, SourceInfo] = {
    1: SourceInfo("DB", "World Bank, Doing Business"),
    2: SourceInfo("WDI", "World Bank, World Development Indicators"),
    3: SourceInfo("WGI", "World Bank, Worldwide Governance Indicators"),
    6: SourceInfo("IDS", "World Bank, International Debt Statistics"),
    32: SourceInfo("GFDD", "World Bank, Global Financial Development Database"),
    57: SourceInfo("WDI archive", "World Bank, WDI Database Archives"),
}

#: World Bank aggregate codes (regions, income and lending groups, other
#: groupings) as returned by /v2/country with ``region.id == "NA"``. This is a
#: static snapshot used so that :func:`parse` stays pure; it is refreshed at
#: runtime once :meth:`WorldBank.fetch_countries` has been called.
KNOWN_AGGREGATES: frozenset[str] = frozenset(
    {
        "AFE", "AFW", "ARB", "CEB", "CSS", "EAP", "EAR", "EAS", "ECA", "ECS",
        "EMU", "EUU", "FCS", "HIC", "HPC", "IBD", "IBT", "IDA", "IDB", "IDX",
        "INX", "LAC", "LCN", "LDC", "LIC", "LMC", "LMY", "LTE", "MEA", "MIC",
        "MNA", "NAC", "OED", "OSS", "PRE", "PSS", "PST", "SAS", "SSA", "SSF",
        "SST", "TEA", "TEC", "TLA", "TMN", "TSA", "TSS", "UMC", "WLD",
    }
)

#: Observation-status flags. The World Bank documents only ``F`` ("forecast").
#: The other letters follow the SDMX CL_OBS_STATUS code list (A normal,
#: B break, E estimated, F forecast, I imputed, M missing, P provisional) and
#: are UNVERIFIED for this API. Unknown flags map to "" and are reported in
#: ``Result.meta["obs_status_raw"]`` plus a caveat.
OBS_STATUS_MAP: dict[str, str] = {
    "": "",
    "A": "",
    "F": "forecast",
    "E": "estimate",
    "P": "provisional",
    "B": "break",
    "I": "imputed",
    "M": "",
}

#: Error ids from the official "API Error Codes" table, with hints.
ERROR_HINTS: dict[str, str] = {
    "105": "service temporarily unavailable; retry later",
    "110": "API version not found",
    "111": "response format not found",
    "112": "method not found",
    "115": "missing required parameter",
    "120": (
        "invalid parameter value: check the country and indicator codes, and pass source= for "
        "indicators outside WDI (economies the World Bank does not cover also return 120)"
    ),
    "140": "endpoint not found",
    "150": "language not supported",
    "160": "filtering on an indicator value needs a date range",
    "175": (
        "indicator deleted or archived: look for its successor code, or query a past "
        "WDI release through the WDI Database Archives (source=57, version=YYYYMM)"
    ),
    "199": "unexpected server error; retry later",
}

#: error ids worth one retry with a fresh request (bypassing the cache)
TRANSIENT_ERROR_IDS = {"105", "199"}

_INDICATOR_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
_COUNTRY_RE = re.compile(r"^[A-Za-z0-9]{2,3}$")
_VERSION_RE = re.compile(r"^(19|20)\d{2}(0[1-9]|1[0-2])$")


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------
class WorldBankAPIError(RuntimeError):
    """The API answered with its error envelope (``{"message": [...]}``)."""

    def __init__(self, message_id: str, key: str, value: str, url: str = ""):
        self.message_id = str(message_id or "")
        self.key = key or ""
        self.value = value or ""
        self.url = url
        hint = ERROR_HINTS.get(self.message_id, "")
        text = f"World Bank API error {self.message_id or '?'} ({self.key}): {self.value}"
        if hint:
            text += f" -- {hint}"
        if url:
            text += f" [{url}]"
        super().__init__(text)

    @property
    def transient(self) -> bool:
        return self.message_id in TRANSIENT_ERROR_IDS


class WorldBankResponseError(RuntimeError):
    """The payload is not a response format this connector understands
    (HTML error page, truncated JSON, unknown structure)."""

    transient = True


# ---------------------------------------------------------------------------
# small pure helpers
# ---------------------------------------------------------------------------
def _as_list(values: Sequence[str] | str) -> list[str]:
    """Normalise a code or list of codes; ``"A;B"`` and ``"A,B"`` are split."""
    if isinstance(values, str):
        values = re.split(r"[;,]", values)
    out: list[str] = []
    for v in values:
        v = str(v).strip()
        if v and v not in out:
            out.append(v)
    return out


def normalize_indicators(indicators: Sequence[str] | str) -> list[str]:
    codes = _as_list(indicators)
    if not codes:
        raise ValueError("at least one indicator code is required")
    bad = [c for c in codes if not _INDICATOR_RE.match(c)]
    if bad:
        raise ValueError(f"invalid indicator code(s): {bad}")
    return codes


def normalize_countries(countries: Sequence[str] | str) -> list[str] | str:
    """Return ``"all"`` or a list of upper-case ISO2/ISO3/World Bank codes."""
    if isinstance(countries, str) and countries.strip().lower() == "all":
        return "all"
    codes = [c.upper() for c in _as_list(countries)]
    if not codes:
        raise ValueError("countries must be 'all' or a non-empty list of codes")
    if any(c == "ALL" for c in codes):
        return "all"
    bad = [c for c in codes if not _COUNTRY_RE.match(c)]
    if bad:
        raise ValueError(f"invalid country code(s): {bad}")
    out: list[str] = []
    for c in codes:
        if c not in out:
            out.append(c)
    return out


def normalize_version(version: str | int | None) -> str | None:
    """WDI archive versions are ``YYYYMM`` strings, e.g. ``"202004"``."""
    if version is None:
        return None
    v = str(version).strip()
    if not _VERSION_RE.match(v):
        raise ValueError(f"WDI archive version must look like YYYYMM (e.g. '202004'), got {version!r}")
    return v


def resolve_source(source: int, version: str | int | None) -> tuple[int, str | None]:
    """Route versioned requests to the WDI Database Archives (source 57).

    ``version`` with ``source=2`` (WDI) is read as "this WDI release" and sent
    to source 57; ``source=57`` requires a version; ``version`` with any other
    source is an error because only source 57 is known to have a version
    dimension.
    """
    src = int(source)
    ver = normalize_version(version)
    if ver is not None:
        if src in (2, ARCHIVE_SOURCE):
            return ARCHIVE_SOURCE, ver
        raise ValueError(
            f"version= applies to the WDI Database Archives (source 57), not source {src}; "
            "for other sources pin a snapshot instead (CGJRKIT_SNAPSHOT)"
        )
    if src == ARCHIVE_SOURCE:
        raise ValueError("source 57 (WDI Database Archives) needs version='YYYYMM'; list them with fetch_archive_versions()")
    return src, None


def dataset_for(source: int, version: str | None = None) -> str:
    if version is not None:
        return f"WDI archive {version}"
    info = SOURCES.get(int(source))
    return info.dataset if info else f"WB source {source}"


def citation_for(source: int) -> str:
    info = SOURCES.get(int(source))
    return info.citation if info else f"World Bank, World Bank Open Data (API source {source})"


def normalize_period(date: Any) -> str:
    """API dates to canonical periods: ``2020``, ``2020Q1``, ``2020M03`` -> ``2020-03``, ``YR2020`` -> ``2020``."""
    s = str(date if date is not None else "").strip()
    m = re.fullmatch(r"(?i)YR(\d{4})", s)
    if m:
        return m.group(1)
    m = re.fullmatch(r"(\d{4})M(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.fullmatch(r"(\d{4})\s*Q([1-4])", s)
    if m:
        return f"{m.group(1)}Q{m.group(2)}"
    return s


_PAREN_TAIL = re.compile(r"\(([^()]*)\)\s*$")
_QUALIFIER = re.compile(r"^(?:modell?ed\s+)?(?:ilo\s+|national\s+)?estimates?$", re.IGNORECASE)
_PPP_YEAR = (
    re.compile(r"\b((?:19|20)\d{2})\s*(?:PPP|international)", re.IGNORECASE),
    re.compile(r"PPP\s*\(?\s*((?:19|20)\d{2})", re.IGNORECASE),
)


def _unit_parenthetical(name: str) -> str:
    """Last parenthetical of a WDI name, skipping qualifiers such as
    "(modeled ILO estimate)" or "(national estimate)"."""
    s = (name or "").strip()
    while True:
        m = _PAREN_TAIL.search(s)
        if not m:
            return ""
        inner = m.group(1).strip()
        s = s[: m.start()].rstrip()
        if _QUALIFIER.match(inner):
            continue
        return inner


def ppp_year(text: str) -> str | None:
    """PPP base year written in an indicator name or unit, if any."""
    for rx in _PPP_YEAR:
        m = rx.search(text or "")
        if m:
            return m.group(1)
    return None


def unit_from_name(name: str, api_unit: str = "") -> str:
    """Unit label for a series, following CONVENTIONS.

    WDI puts the unit in the trailing parenthetical of the indicator name
    ("GDP growth (annual %)" -> "annual %"); the API's own ``unit`` field is
    usually empty and wins when set. PPP-based units get the base year
    appended ("constant 2021 international $ (PPP 2021)"); PPP units without
    a stated year (current international $) get "(PPP)" so that
    :func:`cgjrkit.core.schema.ppp_base` reports them as "unknown".
    """
    unit = (api_unit or "").strip() or _unit_parenthetical(name)
    year = ppp_year(name) or ppp_year(unit)
    if year:
        if not re.search(rf"PPP\s*{year}", unit, re.IGNORECASE):
            unit = f"{unit} (PPP {year})".strip()
    elif "international $" in unit.lower() and "ppp" not in unit.lower():
        unit = f"{unit} (PPP)".strip()
    return unit


def is_modelled_ilo(label: str) -> bool:
    return bool(re.search(r"modell?ed\s+ilo\s+estimate", label or "", re.IGNORECASE))


def map_obs_status(raw: str | None, label: str = "") -> tuple[str, bool]:
    """Map an API ``obs_status`` flag to ``schema.OBS_STATUS``.

    Returns ``(status, known)``. Series labelled "(modeled ILO estimate)" are
    tagged ``modelled`` when the API sets no flag, so caveat checks can catch
    splices of modelled and survey-based labour data.
    """
    flag = (raw or "").strip().upper()
    known = flag in OBS_STATUS_MAP
    status = OBS_STATUS_MAP.get(flag, "")
    if status == "" and is_modelled_ilo(label):
        status = "modelled"
    return status, known


# ---------------------------------------------------------------------------
# payload decoding
# ---------------------------------------------------------------------------
_XML_MSG = re.compile(r"<wb:message\b([^>]*)>(.*?)</wb:message>", re.DOTALL | re.IGNORECASE)
_XML_ATTR = re.compile(r'(\w+)="([^"]*)"')


def _raise_message(messages: Any, url: str) -> None:
    first = messages[0] if isinstance(messages, list) and messages else messages
    if not isinstance(first, dict):
        first = {"value": str(first)}
    raise WorldBankAPIError(first.get("id", ""), first.get("key", ""), first.get("value", ""), url)


def decode(payload: bytes | str | Any, url: str = "") -> Any:
    """Decode a payload to JSON and raise on the API's error formats."""
    if isinstance(payload, (bytes, bytearray)):
        text = bytes(payload).decode("utf-8-sig", errors="replace")
    elif isinstance(payload, str):
        text = payload.lstrip("﻿")
    else:
        obj = payload
        text = None
    if text is not None:
        stripped = text.strip()
        if stripped.startswith("<"):
            m = _XML_MSG.search(stripped)
            if m:
                attrs = dict(_XML_ATTR.findall(m.group(1)))
                raise WorldBankAPIError(attrs.get("id", ""), attrs.get("key", ""), m.group(2).strip(), url)
            raise WorldBankResponseError(f"non-JSON response (XML/HTML) from {url or 'World Bank API'}: {stripped[:200]!r}")
        if not stripped:
            raise WorldBankResponseError(f"empty response from {url or 'World Bank API'}")
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise WorldBankResponseError(f"invalid JSON from {url or 'World Bank API'}: {exc}") from exc
    if isinstance(obj, list) and obj and isinstance(obj[0], dict) and obj[0].get("message"):
        _raise_message(obj[0]["message"], url)
    if isinstance(obj, dict) and obj.get("message"):
        _raise_message(obj["message"], url)
    return obj


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


@dataclass
class PageInfo:
    page: int = 1
    pages: int = 1
    per_page: int = 0
    total: int = 0
    lastupdated: str = ""
    sourceid: str = ""


def page_info(obj: Any) -> PageInfo:
    """Pagination header of either response format (numbers may be strings)."""
    if isinstance(obj, list):
        hdr = obj[0] if obj and isinstance(obj[0], dict) else {}
    elif isinstance(obj, dict):
        hdr = obj
    else:
        raise WorldBankResponseError(f"unrecognised response object: {type(obj).__name__}")
    src = hdr.get("source")
    sourceid = hdr.get("sourceid")
    if sourceid in (None, "") and isinstance(src, dict):
        sourceid = src.get("id")
    return PageInfo(
        page=_int(hdr.get("page"), 1),
        pages=_int(hdr.get("pages"), 1),
        per_page=_int(hdr.get("per_page"), 0),
        total=_int(hdr.get("total"), 0),
        lastupdated=str(hdr.get("lastupdated") or ""),
        sourceid="" if sourceid is None else str(sourceid),
    )


# ---------------------------------------------------------------------------
# requests (pure)
# ---------------------------------------------------------------------------
def _chunk(codes: list[str], max_items: int | None, max_chars: int) -> list[list[str]]:
    chunks: list[list[str]] = []
    cur: list[str] = []
    for code in codes:
        trial = cur + [code]
        too_long = len(";".join(trial)) > max_chars
        too_many = max_items is not None and len(trial) > max_items
        if cur and (too_long or too_many):
            chunks.append(cur)
            cur = [code]
        else:
            cur = trial
    if cur:
        chunks.append(cur)
    return chunks


def plan_chunks(indicators: list[str], countries: list[str] | str) -> list[tuple[list[str], list[str] | str]]:
    """Split a request so each URL respects the API limits (60 indicators,
    1,500 characters per path segment)."""
    ind_chunks = _chunk(indicators, MAX_INDICATORS_PER_REQUEST, MAX_SEGMENT_CHARS)
    cty_chunks: list[list[str] | str] = ["all"] if countries == "all" else list(_chunk(list(countries), None, MAX_SEGMENT_CHARS))
    return [(i, c) for i in ind_chunks for c in cty_chunks]


def _year_bounds(start: int | str | None, end: int | str | None) -> tuple[int | None, int | None]:
    def as_year(v: int | str | None) -> int | None:
        if v is None:
            return None
        s = str(v).strip()
        return int(s) if re.fullmatch(r"\d{4}", s) else None

    return as_year(start), as_year(end)


def build_request(
    indicators: Sequence[str] | str,
    countries: Sequence[str] | str = "all",
    start: int | str | None = None,
    end: int | str | None = None,
    source: int = 2,
    version: str | int | None = None,
    mrv: int | None = None,
    *,
    mrnev: int | None = None,
    per_page: int = DEFAULT_PER_PAGE,
    page: int = 1,
) -> tuple[str, dict[str, str]]:
    """Return ``(url, params)`` for one page of one indicator/country chunk.

    * Ordinary sources use ``/country/{c}/indicator/{i}?source=..``. The
      ``source`` parameter is always sent: the API requires it for
      multi-indicator requests and for indicators outside WDI (e.g. WGI).
    * ``start`` and ``end`` together become ``date=start:end`` (years, or
      ``2013Q1``/``2012M01`` for sub-annual sources). A one-sided range is not
      sent; :meth:`WorldBank.fetch_indicators` filters it client side.
    * ``version`` (WDI archives) uses the advanced endpoint
      ``/sources/57/country/{c}/series/{s}/time/{t}/version/{v}``.
    """
    inds = normalize_indicators(indicators)
    ctys = normalize_countries(countries)
    src, ver = resolve_source(source, version)
    if len(inds) > MAX_INDICATORS_PER_REQUEST:
        raise ValueError(f"at most {MAX_INDICATORS_PER_REQUEST} indicators per request; use plan_chunks()")
    if mrv is not None and mrnev is not None:
        raise ValueError("pass mrv or mrnev, not both")
    if (mrv is not None or mrnev is not None) and (start is not None or end is not None):
        raise ValueError("mrv/mrnev replace the date range; do not combine them with start/end")
    for name, n in (("mrv", mrv), ("mrnev", mrnev)):
        if n is not None and int(n) < 1:
            raise ValueError(f"{name} must be a positive integer")
    if not 1 <= int(per_page) <= MAX_PER_PAGE:
        raise ValueError(f"per_page must be between 1 and {MAX_PER_PAGE}")
    ys, ye = _year_bounds(start, end)
    if ys is not None and ye is not None and ys > ye:
        raise ValueError(f"start ({start}) is after end ({end})")

    ind_seg = ";".join(inds)
    cty_seg = "all" if ctys == "all" else ";".join(ctys)
    for label, seg in (("indicator", ind_seg), ("country", cty_seg)):
        if len(seg) > MAX_SEGMENT_CHARS:
            raise ValueError(f"{label} path segment exceeds {MAX_SEGMENT_CHARS} characters; use plan_chunks()")

    params: dict[str, str] = {"format": "json", "per_page": str(int(per_page)), "page": str(int(page))}
    if ver is not None:
        # Advanced query on the WDI Database Archives. Time members are
        # "YR<year>" (official docs example); an explicit list is sent because
        # range syntax is not documented for this endpoint.
        if ys is not None and ye is not None:
            time_seg = ";".join(f"YR{y}" for y in range(ys, ye + 1))
        else:
            time_seg = "all"
        url = f"{BASE_URL}/sources/{src}/country/{cty_seg}/series/{ind_seg}/time/{time_seg}/version/{ver}"
    else:
        url = f"{BASE_URL}/country/{cty_seg}/indicator/{ind_seg}"
        params["source"] = str(src)
        if start is not None and end is not None:
            params["date"] = f"{start}:{end}"
    if mrv is not None:
        params["mrv"] = str(int(mrv))
    if mrnev is not None:
        params["mrnev"] = str(int(mrnev))
    approx_len = len(url) + 1 + len("&".join(f"{k}={v}" for k, v in params.items()))
    if approx_len > MAX_URL_CHARS:
        raise ValueError(f"request URL would be ~{approx_len} characters (limit {MAX_URL_CHARS}); request fewer codes or years")
    return url, params


# ---------------------------------------------------------------------------
# parsing (pure)
# ---------------------------------------------------------------------------
@dataclass
class ParsedPage:
    """One page of indicator data plus what the connector needs for provenance."""

    frame: pd.DataFrame
    info: PageInfo
    obs_status_raw: dict[str, int] = field(default_factory=dict)
    unknown_obs_status: dict[str, int] = field(default_factory=dict)
    version_labels: dict[str, str] = field(default_factory=dict)
    missing_iso3: list[str] = field(default_factory=list)


def _records_v2(rows: list[dict], dataset: str) -> tuple[list[dict], Counter, Counter, list[str]]:
    records: list[dict] = []
    raw_flags: Counter = Counter()
    unknown: Counter = Counter()
    missing_iso3: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ind = row.get("indicator") or {}
        cty = row.get("country") or {}
        code = str(ind.get("id") or "").strip()
        label = str(ind.get("value") or "").strip()
        iso3 = str(row.get("countryiso3code") or "").strip()
        if not iso3:
            iso3 = str(cty.get("id") or "").strip()
            if iso3 and len(iso3) != 3 and iso3 not in missing_iso3:
                missing_iso3.append(iso3)
        raw = str(row.get("obs_status") or "").strip()
        status, known = map_obs_status(raw, label)
        if raw:
            raw_flags[raw] += 1
            if not known:
                unknown[raw] += 1
        records.append(
            {
                "dataset": dataset,
                "indicator": code,
                "indicator_label": label,
                "iso3": iso3,
                "country": str(cty.get("value") or "").strip(),
                "period": normalize_period(row.get("date")),
                "value": row.get("value"),
                "unit": unit_from_name(label, str(row.get("unit") or "")),
                "obs_status": status,
            }
        )
    return records, raw_flags, unknown, missing_iso3


def _records_advanced(data: list[dict], dataset: str) -> tuple[list[dict], dict[str, str]]:
    records: list[dict] = []
    versions: dict[str, str] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        dims: dict[str, dict] = {}
        for var in row.get("variable") or []:
            if isinstance(var, dict) and var.get("concept"):
                dims[str(var["concept"]).strip().lower()] = var
        country = dims.get("country") or dims.get("economy") or {}
        series = dims.get("series") or dims.get("indicator") or {}
        time = dims.get("time") or dims.get("year") or {}
        version = dims.get("version") or {}
        if not time or not series or not country:
            continue  # incomplete row (the docs example shows such a fragment)
        if version.get("id"):
            versions[str(version["id"])] = str(version.get("value") or "")
        label = str(series.get("value") or "").strip()
        tval = str(time.get("value") or "").strip()
        period = normalize_period(tval if re.fullmatch(r"\d{4}(Q[1-4]|M\d{1,2})?", tval) else time.get("id"))
        status, _ = map_obs_status("", label)
        records.append(
            {
                "dataset": dataset,
                "indicator": str(series.get("id") or "").strip(),
                "indicator_label": label,
                "iso3": str(country.get("id") or "").strip(),
                "country": str(country.get("value") or "").strip(),
                "period": period,
                "value": row.get("value"),
                "unit": unit_from_name(label),
                "obs_status": status,
            }
        )
    return records, versions


def parse_page(
    payload: bytes | str | Any,
    *,
    source: int = 2,
    version: str | None = None,
    url: str = "",
) -> ParsedPage:
    """Parse one page of an indicator or advanced (archive) data response."""
    obj = decode(payload, url)
    info = page_info(obj)
    dataset = dataset_for(source, version)
    raw_flags: Counter = Counter()
    unknown: Counter = Counter()
    missing: list[str] = []
    versions: dict[str, str] = {}
    if isinstance(obj, list):
        rows = obj[1] if len(obj) > 1 and isinstance(obj[1], list) else []
        records, raw_flags, unknown, missing = _records_v2(rows, dataset)
    elif isinstance(obj, dict) and isinstance(obj.get("source"), dict):
        records, versions = _records_advanced(obj["source"].get("data") or [], dataset)
    elif isinstance(obj, dict) and int(info.total) == 0:
        records = []
    else:
        raise WorldBankResponseError(f"unrecognised data response structure from {url or 'World Bank API'}")
    frame = make_frame(records, source="worldbank", dataset=dataset) if records else empty_frame()
    return ParsedPage(frame, info, dict(raw_flags), dict(unknown), versions, missing)


def parse(payload: bytes | str | Any, *, source: int = 2, version: str | None = None, url: str = "") -> pd.DataFrame:
    """Canonical frame for one data payload (either response format), sorted."""
    frame = parse_page(payload, source=source, version=version, url=url).frame
    return Connector.finalize(frame) if len(frame) else frame


def parse_countries(payload: bytes | str | Any, url: str = "") -> pd.DataFrame:
    """``/v2/country`` page -> one row per economy or aggregate.

    Aggregates are the entries whose region is ``NA``/"Aggregates" (the rule
    wbgapi and wbwdi use).
    """
    obj = decode(payload, url)
    rows = obj[1] if isinstance(obj, list) and len(obj) > 1 and isinstance(obj[1], list) else []

    def sub(row: dict, key: str, field_: str) -> str:
        val = row.get(key) or {}
        return str(val.get(field_) or "").strip() if isinstance(val, dict) else ""

    def coord(v: Any) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return float("nan")

    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        region_id, region = sub(row, "region", "id"), sub(row, "region", "value")
        out.append(
            {
                "iso3": str(row.get("id") or "").strip(),
                "iso2": str(row.get("iso2Code") or "").strip(),
                "name": str(row.get("name") or "").strip(),
                "region_id": region_id,
                "region": region,
                "admin_region_id": sub(row, "adminregion", "id"),
                "admin_region": sub(row, "adminregion", "value"),
                "income_level_id": sub(row, "incomeLevel", "id"),
                "income_level": sub(row, "incomeLevel", "value"),
                "lending_type_id": sub(row, "lendingType", "id"),
                "lending_type": sub(row, "lendingType", "value"),
                "capital_city": str(row.get("capitalCity") or "").strip(),
                "longitude": coord(row.get("longitude")),
                "latitude": coord(row.get("latitude")),
                "is_aggregate": region_id == "NA" or region.lower() == "aggregates",
            }
        )
    cols = [
        "iso3", "iso2", "name", "region_id", "region", "admin_region_id", "admin_region",
        "income_level_id", "income_level", "lending_type_id", "lending_type",
        "capital_city", "longitude", "latitude", "is_aggregate",
    ]
    df = pd.DataFrame(out, columns=cols)
    df["is_aggregate"] = df["is_aggregate"].astype(bool)
    return df


def parse_indicator_meta(payload: bytes | str | Any, source: int | None = None, url: str = "") -> dict:
    """``/v2/indicator/{code}`` -> metadata dict.

    When an indicator belongs to several sources the API may list it more
    than once; the entry for ``source`` is returned when given.
    """
    obj = decode(payload, url)
    rows = obj[1] if isinstance(obj, list) and len(obj) > 1 and isinstance(obj[1], list) else []
    rows = [r for r in rows if isinstance(r, dict)]
    if not rows:
        raise WorldBankResponseError(f"no indicator metadata in response from {url or 'World Bank API'}")
    pick = rows[0]
    if source is not None:
        for r in rows:
            if str((r.get("source") or {}).get("id")) == str(source):
                pick = r
                break
    name = str(pick.get("name") or "").strip()
    api_unit = str(pick.get("unit") or "").strip()
    unit = unit_from_name(name, api_unit)
    src = pick.get("source") or {}
    return {
        "id": str(pick.get("id") or "").strip(),
        "name": name,
        "unit": unit,
        "unit_api": api_unit,
        "ppp_base": ppp_base(unit),
        "source_id": str(src.get("id") or ""),
        "source_name": str(src.get("value") or "").strip(),
        "source_note": str(pick.get("sourceNote") or "").strip(),
        "source_organization": str(pick.get("sourceOrganization") or "").strip(),
        "topics": [str(t.get("value") or "").strip() for t in (pick.get("topics") or []) if isinstance(t, dict)],
        "modelled_ilo": is_modelled_ilo(name),
    }


def parse_concept_variables(payload: bytes | str | Any, url: str = "") -> pd.DataFrame:
    """Members of one dimension of a multi-dimensional source, e.g. the
    versions of the WDI Database Archives: ``id`` and ``label`` columns."""
    obj = decode(payload, url)
    out = []
    srcs = obj.get("source") if isinstance(obj, dict) else None
    if isinstance(srcs, list):
        for src in srcs:
            for concept in (src or {}).get("concept") or []:
                for var in (concept or {}).get("variable") or []:
                    if isinstance(var, dict):
                        out.append({"id": str(var.get("id") or "").strip(), "label": str(var.get("value") or "").strip()})
    elif isinstance(obj, list) and len(obj) > 1 and isinstance(obj[1], list):
        out = [{"id": str(r.get("id") or "").strip(), "label": str(r.get("value") or r.get("name") or "").strip()} for r in obj[1] if isinstance(r, dict)]
    return pd.DataFrame(out, columns=["id", "label"])


def parse_sources(payload: bytes | str | Any, url: str = "") -> pd.DataFrame:
    """``/v2/sources`` page -> id, code, name, lastupdated, availability flags."""
    obj = decode(payload, url)
    rows = obj[1] if isinstance(obj, list) and len(obj) > 1 and isinstance(obj[1], list) else []
    out = [
        {
            "id": _int(r.get("id"), 0),
            "code": str(r.get("code") or "").strip(),
            "name": str(r.get("name") or "").strip(),
            "lastupdated": str(r.get("lastupdated") or ""),
            "data_available": str(r.get("dataavailability") or "").upper() == "Y",
            "metadata_available": str(r.get("metadataavailability") or "").upper() == "Y",
            "concepts": _int(r.get("concepts"), 0),
        }
        for r in rows
        if isinstance(r, dict)
    ]
    return pd.DataFrame(out, columns=["id", "code", "name", "lastupdated", "data_available", "metadata_available", "concepts"])


def filter_periods(df: pd.DataFrame, start: int | str | None, end: int | str | None) -> pd.DataFrame:
    """Client-side period filter, used for one-sided ranges."""
    if df.empty or (start is None and end is None):
        return df
    keep = pd.Series(True, index=df.index)
    ys, ye = _year_bounds(start, end)
    years = pd.to_numeric(df["period"].map(year_of), errors="coerce")
    if start is not None:
        keep &= (years >= ys) if ys is not None else (df["period"] >= normalize_period(start))
    if end is not None:
        keep &= (years <= ye) if ye is not None else (df["period"] <= normalize_period(end))
    return df[keep.fillna(False).astype(bool)].reset_index(drop=True)


def build_caveats(
    df: pd.DataFrame,
    *,
    source: int,
    version: str | None,
    unknown_obs_status: dict[str, int] | None = None,
    missing_iso3: Iterable[str] = (),
    duplicates_dropped: int = 0,
    incomplete_dropped: int = 0,
) -> list[Caveat]:
    """Data caveats for an indicator result (pure)."""
    cav: list[Caveat] = []
    if incomplete_dropped:
        cav.append(
            Caveat("WB_INCOMPLETE_ROWS", "warn", "Rows without an indicator, economy or period code were dropped.", {"rows": int(incomplete_dropped)})
        )
    if df.empty:
        cav.append(Caveat("WB_NO_DATA", "warn", "The World Bank API returned no observations for this request.", {"source": source, "version": version}))
        return cav
    fc = df[df["obs_status"] == "forecast"]
    if len(fc):
        cav.append(
            Caveat(
                "WB_FORECAST",
                "warn",
                "Some observations are flagged as forecasts (obs_status F); label them as projections.",
                {"indicators": sorted(fc["indicator"].unique()), "periods": sorted(fc["period"].unique())},
            )
        )
    mod = df[df["obs_status"] == "modelled"]
    if len(mod):
        cav.append(
            Caveat(
                "ILO_MODELLED",
                "warn",
                "Series are ILO modelled estimates (WDI '(modeled ILO estimate)'); do not splice them with survey-based national series.",
                {"indicators": sorted(mod["indicator"].unique())},
            )
        )
    units = df[["indicator", "unit"]].drop_duplicates()
    bases = {row.indicator: ppp_base(row.unit) for row in units.itertuples() if ppp_base(row.unit)}
    if bases:
        known = sorted({b for b in bases.values() if b != "unknown"})
        cav.append(Caveat("PPP_BASE", "info", f"PPP-based series present (bases: {', '.join(sorted(set(bases.values())))}).", {"ppp_base": bases}))
        if len(known) > 1:
            cav.append(
                Caveat(
                    "PPP_MIXED_BASES",
                    "warn",
                    f"Series on different PPP bases ({', '.join(known)}) are in the same result; do not combine them in one chart or calculation.",
                    {"ppp_base": bases},
                )
            )
        if "unknown" in bases.values():
            cav.append(
                Caveat(
                    "PPP_BASE_UNSTATED",
                    "info",
                    "Current international $ series follow the ICP benchmark in force at this WDI release; the base year is not in the series name.",
                    {"indicators": sorted(k for k, v in bases.items() if v == "unknown")},
                )
            )
    if version is not None:
        cav.append(
            Caveat(
                "WDI_ARCHIVE_CODE_REUSE",
                "info",
                "WDI reuses series codes across base years and valuations (e.g. NY.GDP.MKTP.KD in constant 2010 vs 2015 US$) "
                "and aggregate definitions change between releases; check unit labels before comparing vintages.",
                {"version": version},
            )
        )
    if int(source) == 3:
        cav.append(
            Caveat(
                "WGI_2025_REVISION",
                "info",
                "The WGI were revised in 2025 (new 0-100 scores, history recalculated to 1996, GOV_WGI_* codes); charts from earlier reports may not replicate from the current release.",
                {},
            )
        )
    if int(source) == 32:
        cav.append(Caveat("GFDD_STALE", "info", "The Global Financial Development Database was last updated in 2022 (data to 2021); check coverage before use.", {}))
    inds = set(df["indicator"])
    lpi = sorted(i for i in inds if i.upper().startswith("LP.LPI"))
    if lpi:
        cav.append(
            Caveat(
                "LPI_SURVEY_SERIES",
                "info",
                "WDI LP.LPI.* series are the survey-based Logistics Performance Index (editions to 2023); LPI 2.0 (tracking data) is a separate series and a break.",
                {"indicators": lpi},
            )
        )
    ent = sorted(i for i in inds if i.upper() in {"IC.BUS.NREG", "IC.BUS.NDNS.ZS"})
    if ent:
        cav.append(
            Caveat(
                "ENTREPRENEURSHIP_LLC_ONLY",
                "info",
                "Entrepreneurship Database series count newly registered limited liability companies only; legal definitions differ across countries.",
                {"indicators": ent},
            )
        )
    if unknown_obs_status:
        cav.append(
            Caveat(
                "WB_UNMAPPED_OBS_STATUS",
                "warn",
                "The API returned observation flags this connector does not map; they are left blank in obs_status.",
                {"flags": dict(unknown_obs_status)},
            )
        )
    missing = sorted(set(missing_iso3))
    if missing:
        cav.append(Caveat("WB_ISO3_MISSING", "info", "Some rows had no countryiso3code; the API's country id was used instead.", {"codes": missing}))
    if duplicates_dropped:
        cav.append(
            Caveat("WB_DUPLICATES_DROPPED", "info", "Duplicate observations (same indicator, economy and period) were dropped, keeping the first.", {"rows": int(duplicates_dropped)})
        )
    return cav


# ---------------------------------------------------------------------------
# connector
# ---------------------------------------------------------------------------
class WorldBank(Connector):
    """World Bank Indicators API v2: WDI, WGI, GFDD, other sources, WDI archives.

    WDI also carries the Entrepreneurship Database series (``IC.BUS.NREG``,
    ``IC.BUS.NDNS.ZS``) and the survey-based Logistics Performance Index
    (``LP.LPI.*``); both are fetched like any WDI indicator.
    """

    source = "worldbank"
    license = "CC BY 4.0"
    citation = "World Bank, World Development Indicators"
    domains = ("api.worldbank.org",)

    def __init__(self, cache: Cache | None = None):
        super().__init__(cache)
        self._aggregates: frozenset[str] | None = None

    # -- plumbing -------------------------------------------------------------
    def _get(self, url: str, params: dict, refresh: bool = False) -> tuple[bytes, Any, Any]:
        """Fetch through the cache and decode; retry once, bypassing the
        cache, when the payload is a transient error (HTML page, 105, 199)."""
        try:
            payload, retrieval = http.fetch(self.source, url, params, cache=self.cache, refresh=refresh)
        except http.FetchError as exc:
            self._raise_from_fetch_error(exc, url)
            raise
        try:
            return payload, retrieval, decode(payload, url)
        except (WorldBankAPIError, WorldBankResponseError) as exc:
            if refresh or not getattr(exc, "transient", False):
                raise
            try:
                payload, retrieval = http.fetch(self.source, url, params, cache=self.cache, refresh=True)
            except http.NetworkBlocked:
                raise exc from None
            return payload, retrieval, decode(payload, url)

    @staticmethod
    def _raise_from_fetch_error(exc: Exception, url: str) -> None:
        """Turn an HTTP error whose body is the API's JSON envelope into
        :class:`WorldBankAPIError`; otherwise return and let it propagate."""
        text = str(exc)
        i = text.find("[{")
        if i < 0:
            return
        try:
            decode(text[i:], url)
        except WorldBankAPIError as api_exc:
            raise api_exc from exc
        except WorldBankResponseError:
            return

    def _prov(self, retrieval: Any, *, dataset: str, vintage: str, citation: str, tool: str, notes: list[str] | None = None) -> Provenance:
        prov = self.provenance(retrieval, dataset=dataset, vintage=vintage, tool=tool, notes=notes)
        prov.citation = citation
        return prov

    def aggregate_codes(self) -> frozenset[str]:
        """Aggregate codes: the static snapshot, plus the live ISO3/ISO2 codes
        once fetch_countries() has run (the union keeps codes that older WDI
        archive versions still use)."""
        return KNOWN_AGGREGATES | self._aggregates if self._aggregates is not None else KNOWN_AGGREGATES

    # -- data -----------------------------------------------------------------
    def fetch(self, indicators: Sequence[str] | str, countries: Sequence[str] | str = "all", **kwargs: Any) -> Result:
        """Alias of :meth:`fetch_indicators`."""
        return self.fetch_indicators(indicators, countries, **kwargs)

    def fetch_indicators(
        self,
        indicators: Sequence[str] | str,
        countries: Sequence[str] | str = "all",
        start: int | str | None = None,
        end: int | str | None = None,
        source: int = 2,
        version: str | int | None = None,
        mrv: int | None = None,
        *,
        mrnev: int | None = None,
        per_page: int = DEFAULT_PER_PAGE,
        resolve_aggregates: bool = False,
        refresh: bool = False,
    ) -> Result:
        """Fetch indicator observations as a canonical :class:`Result`.

        Args:
            indicators: one code or a list (``"NY.GDP.MKTP.KD.ZG"``). Lists are
                sent as ``a;b`` with ``source=`` and split into chunks of at
                most 60 codes.
            countries: ``"all"`` (economies and aggregates) or ISO2/ISO3/World
                Bank codes, aggregates included (``["KEN", "LMC", "SSA"]``).
            start, end: years (or ``"2013Q1"``/``"2012M01"`` for sub-annual
                sources). Both together are sent as ``date=start:end``; one
                alone is applied client side.
            source: API source id: 2 WDI (default), 3 WGI, 32 GFDD, ...
            version: WDI release ``"YYYYMM"`` from the WDI Database Archives
                (source 57). With ``source=2`` the request is routed to 57.
                Sets ``Provenance.vintage`` to ``"WDI archive <version>"``.
            mrv / mrnev: most recent (non-empty) values; exclusive with start/end.
            per_page: rows per page (pages are followed until ``pages``).
            resolve_aggregates: refresh aggregate codes from /v2/country first.
            refresh: bypass the cache (a pinned snapshot still wins).
        """
        inds = normalize_indicators(indicators)
        ctys = normalize_countries(countries)
        src, ver = resolve_source(source, version)
        if resolve_aggregates:
            self.fetch_countries(refresh=refresh)
        one_sided = (start is None) != (end is None)

        frames: list[pd.DataFrame] = []
        provs: list[Provenance] = []
        raw_flags: Counter = Counter()
        unknown: Counter = Counter()
        missing: list[str] = []
        versions: dict[str, str] = {}
        urls: list[str] = []
        lastupdated = ""
        dataset = dataset_for(src, ver)
        citation = citation_for(src)
        for ind_chunk, cty_chunk in plan_chunks(inds, ctys):
            page = 1
            while True:
                url, params = build_request(ind_chunk, cty_chunk, start, end, src, ver, mrv, mrnev=mrnev, per_page=per_page, page=page)
                _payload, retrieval, obj = self._get(url, params, refresh)
                parsed = parse_page(obj, source=src, version=ver, url=url)
                frames.append(parsed.frame)
                raw_flags.update(parsed.obs_status_raw)
                unknown.update(parsed.unknown_obs_status)
                missing.extend(parsed.missing_iso3)
                versions.update(parsed.version_labels)
                lastupdated = parsed.info.lastupdated or lastupdated
                urls.append(url)
                if ver is not None:
                    label = parsed.version_labels.get(ver, "")
                    vintage = f"WDI archive {ver}"
                    notes = [f"WDI Database Archives version {ver}" + (f" ({label})" if label else "")]
                else:
                    vintage = f"last updated {parsed.info.lastupdated}" if parsed.info.lastupdated else ""
                    notes = []
                notes.append(f"page {parsed.info.page or page} of {parsed.info.pages}")
                provs.append(self._prov(retrieval, dataset=dataset, vintage=vintage, citation=citation, tool="WorldBank.fetch_indicators", notes=notes))
                if page >= parsed.info.pages or page >= MAX_PAGES or parsed.frame.empty:
                    break
                page += 1

        non_empty = [f for f in frames if len(f)]
        df = pd.concat(non_empty, ignore_index=True) if non_empty else empty_frame()
        if one_sided:
            df = filter_periods(df, start, end)
        complete = (df["indicator"] != "") & (df["iso3"] != "") & (df["period"] != "")
        incomplete = int((~complete).sum())
        df = df[complete].reset_index(drop=True)
        key = ["source", "dataset", "indicator", "iso3", "period"]
        before = len(df)
        df = df.drop_duplicates(subset=key, keep="first")
        dropped = before - len(df)
        df = self.finalize(df) if len(df) else df
        validate_frame(df)

        aggs = self.aggregate_codes()
        present_aggs = sorted(set(df["iso3"]) & aggs) if len(df) else []
        caveats = build_caveats(
            df,
            source=src,
            version=ver,
            unknown_obs_status=dict(unknown),
            missing_iso3=missing,
            duplicates_dropped=dropped,
            incomplete_dropped=incomplete,
        )
        meta = {
            "aggregates": present_aggs,
            "source_id": src,
            "dataset": dataset,
            "version": ver,
            "version_label": versions.get(ver, "") if ver else "",
            "lastupdated": lastupdated,
            "requests": urls,
            "obs_status_raw": dict(raw_flags),
            "indicators": dict(sorted(dict(zip(df["indicator"], df["indicator_label"])).items())) if len(df) else {},
        }
        return Result(data=df, provenance=provs, caveats=caveats, meta=meta)

    # -- reference data -------------------------------------------------------
    def _paged(self, url: str, base_params: dict[str, str], refresh: bool, tool: str, parser) -> tuple[list[Any], list[Provenance]]:
        parts: list[Any] = []
        provs: list[Provenance] = []
        page = 1
        while True:
            params = {**base_params, "page": str(page)}
            _payload, retrieval, obj = self._get(url, params, refresh)
            parts.append(parser(obj))
            info = page_info(obj)
            vintage = f"last updated {info.lastupdated}" if info.lastupdated else ""
            provs.append(self._prov(retrieval, dataset="WB API reference", vintage=vintage, citation="World Bank, World Bank Open Data", tool=tool))
            if page >= info.pages or page >= MAX_PAGES:
                break
            page += 1
        return parts, provs

    def fetch_countries(self, refresh: bool = False) -> pd.DataFrame:
        """Economies and aggregates with ISO codes, region, income level,
        lending type and an ``is_aggregate`` flag. Classifications are the
        current fiscal year's (historical income groups are not in this
        endpoint). Provenance is in ``df.attrs["provenance"]``."""
        url = f"{BASE_URL}/country"
        parts, provs = self._paged(url, {"format": "json", "per_page": "1000"}, refresh, "WorldBank.fetch_countries", lambda o: parse_countries(o, url))
        df = pd.concat(parts, ignore_index=True) if parts else parse_countries([{}, []])
        df = df.drop_duplicates(subset=["iso3"]).sort_values("iso3").reset_index(drop=True)
        aggs = df.loc[df["is_aggregate"], ["iso3", "iso2"]]
        self._aggregates = frozenset(set(aggs["iso3"]) | set(aggs["iso2"])) - {""}
        df.attrs["provenance"] = [p.to_dict() for p in provs]
        return df

    def fetch_indicator_meta(self, code: str, source: int | None = None, refresh: bool = False) -> dict:
        """Indicator metadata: name, unit (derived per CONVENTIONS, PPP base
        included), source note, source organization, topics, provenance."""
        (code,) = normalize_indicators([code])
        url = f"{BASE_URL}/indicator/{code}"
        params = {"format": "json", "per_page": "50"}
        if source is not None:
            params["source"] = str(int(source))
        _payload, retrieval, obj = self._get(url, params, refresh)
        meta = parse_indicator_meta(obj, source=source, url=url)
        meta["provenance"] = self._prov(
            retrieval, dataset="WB API indicator metadata", vintage="", citation=citation_for(int(meta["source_id"] or source or 2)), tool="WorldBank.fetch_indicator_meta"
        ).to_dict()
        return meta

    def fetch_sources(self, refresh: bool = False) -> pd.DataFrame:
        """Databases available through the API (id, code, name, lastupdated, ...)."""
        url = f"{BASE_URL}/sources"
        parts, provs = self._paged(url, {"format": "json", "per_page": "1000"}, refresh, "WorldBank.fetch_sources", lambda o: parse_sources(o, url))
        df = pd.concat(parts, ignore_index=True).sort_values("id").reset_index(drop=True) if parts else parse_sources([{}, []])
        df.attrs["provenance"] = [p.to_dict() for p in provs]
        return df

    def fetch_archive_versions(self, refresh: bool = False) -> pd.DataFrame:
        """Versions (WDI releases) in the WDI Database Archives: ``id``
        (``YYYYMM``) and ``label`` (e.g. ``"2010 Apr"``).

        Uses ``/sources/57/version/all``, the dimension-member query wbgapi
        issues for ``source.features('version', db=57)`` (UNVERIFIED live)."""
        url = f"{BASE_URL}/sources/{ARCHIVE_SOURCE}/version/all"
        parts, provs = self._paged(url, {"format": "json", "per_page": "1000"}, refresh, "WorldBank.fetch_archive_versions", lambda o: parse_concept_variables(o, url))
        df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["id", "label"])
        df = df[df["id"] != ""].drop_duplicates("id").sort_values("id").reset_index(drop=True)
        df.attrs["provenance"] = [p.to_dict() for p in provs]
        return df


__all__ = [
    "KNOWN_AGGREGATES",
    "SOURCES",
    "WorldBank",
    "WorldBankAPIError",
    "WorldBankResponseError",
    "build_caveats",
    "build_request",
    "map_obs_status",
    "normalize_period",
    "parse",
    "parse_concept_variables",
    "parse_countries",
    "parse_indicator_meta",
    "parse_page",
    "parse_sources",
    "plan_chunks",
    "unit_from_name",
]
