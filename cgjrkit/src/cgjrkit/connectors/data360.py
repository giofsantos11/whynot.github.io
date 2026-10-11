"""World Bank Data360 API connector (``data360api.worldbank.org``).

Data360 serves many World Bank and partner databases through one SDMX-like
REST API. This connector covers the generic ``/data360/data`` endpoint plus
indicator listing, metadata, search and codelists, with two convenience
wrappers used by the CGJR charts:

* :meth:`Data360.fetch_enterprise_surveys` -- Enterprise Surveys aggregates
  (database ``WB_ES``), economy-wide totals by default, standard errors and
  sample sizes kept in ``Result.meta["extras"]`` when the payload carries them;
* :meth:`Data360.fetch_lpi20` -- Logistics Performance Indicators 2.0
  (database ``WB_LPI_20``), one series per indicator x statistic (mean,
  median, interquartile range, TEU-weighted).

What was verified, and where, is recorded in ``docs/apis/data360.md``. In
short: endpoints, query parameters, the 24-field row schema and the 1000-row
page cap come from the official OpenAPI spec (``worldbank/open-api-specs``,
"Data360 Open_API.json") and the World Bank's ``data360-mcp`` client; the
``WB_LPI_20`` layout (statistic in ``COMP_BREAKDOWN_1``, observation-count band
in ``COMMENT_OBS``) comes from a public CSV export of that database; the
``WB_ES`` "economy-wide = ``_T`` in all breakdowns" rule comes from World Bank
team code. How ``WB_ES`` exposes standard errors and sample sizes through the
API is **UNVERIFIED**: the parser reads them only from ``COMMENT_OBS`` text,
companion indicator ids next to their estimate, or breakdown codes listed
exactly in :data:`ES_STATISTIC_CODES` (empty until the live test reports the
real codes); it never guesses from a code fragment.

Data360 has no release/vintage endpoint. Vintage is taken from the indicator
metadata ``version_statement.version_date`` when available; the cached payload
hash pins the exact snapshot in every case.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import pandas as pd

from cgjrkit.connectors.base import Connector
from cgjrkit.core.cache import Cache
from cgjrkit.core.caveats import Caveat
from cgjrkit.core.http import FetchError, NetworkBlocked
from cgjrkit.core.http import fetch as http_fetch
from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import empty_frame, make_frame, validate_frame

# -- endpoints (OpenAPI spec "Data360 Open_API.json", servers[0].url) ---------

BASE_URL = "https://data360api.worldbank.org"
DATA_URL = f"{BASE_URL}/data360/data"
METADATA_URL = f"{BASE_URL}/data360/metadata"
SEARCH_URL = f"{BASE_URL}/data360/searchv2"
INDICATORS_URL = f"{BASE_URL}/data360/indicators"
DISAGGREGATION_URL = f"{BASE_URL}/data360/disaggregation"
#: Not in the OpenAPI spec; used by the World Bank's data360-mcp client
#: (``providers.CodelistManager``). UNVERIFIED against the live API here.
CODELIST_URL = f"{BASE_URL}/data360/codelist"

#: "Each API call returns a maximum of 1000 records" (OpenAPI ``skip`` parameter).
PAGE_SIZE = 1000

ES_DATABASE = "WB_ES"
LPI20_DATABASE = "WB_LPI_20"

#: Dimension fields of the Data360 row schema that can split a series.
DIMENSIONS: tuple[str, ...] = (
    "SEX",
    "AGE",
    "URBANISATION",
    "COMP_BREAKDOWN_1",
    "COMP_BREAKDOWN_2",
    "COMP_BREAKDOWN_3",
)
#: SDMX "total" and "not applicable" codes: a dimension with these values does not split a series.
TRIVIAL_CODES = frozenset({"_T", "_Z", ""})

#: Query filters accepted by ``/data360/data`` besides DATABASE_ID/INDICATOR/REF_AREA (OpenAPI spec).
FILTER_PARAMS = frozenset(
    {
        "SEX",
        "AGE",
        "URBANISATION",
        "COMP_BREAKDOWN_1",
        "COMP_BREAKDOWN_2",
        "COMP_BREAKDOWN_3",
        "TIME_PERIOD",
        "FREQ",
        "UNIT_MEASURE",
        "UNIT_TYPE",
        "UNIT_MULT",
    }
)

#: SDMX CL_OBS_STATUS -> cgjrkit ``obs_status``. Codes not listed map to "" and
#: the raw code is kept in the extras table.
OBS_STATUS_MAP = {
    "A": "",  # normal value
    "B": "break",  # time series break
    "E": "estimate",  # estimated value
    "F": "forecast",  # forecast value
    "I": "imputed",  # imputed value
    "P": "provisional",  # provisional value
}

#: Unit labels for UNIT_MEASURE codes seen in public Data360 material (LPI 2.0
#: CSV export labels, HCI+ API notes, WB_ESG sample metadata). Used only when
#: neither indicator metadata nor the UNIT_MEASURE codelist gives a label.
BUILTIN_UNITS = {
    "PT": "percent",
    "PT_YR": "annual percentage",
    "D": "days",
    "PRTN": "partners",
    "ALLIANC": "alliances",
    "SERV": "services",
    "TRANS": "transshipments",
    "YR": "years",
    "SCORE": "score",
    "PS": "persons",
}

#: Labels of the WB_LPI_20 statistic codes (COMP_BREAKDOWN_1), from the
#: database's public CSV export (``COMP_BREAKDOWN_1_LABEL``).
LPI20_METRICS = {
    "WB_LPI_M": "Mean",
    "WB_LPI_MD": "Median",
    "WB_LPI_IQR_75_25": "Interquartile range (P75-P25)",
    "WB_LPI_IQR_80_20": "Interquartile range (P80-P20)",
    "WB_LPI_M_TEU": "TEU weighted mean",
    "WB_LPI_MD_TEU": "TEU weighted median",
}

#: World Bank aggregate codes that look like ISO3 codes (WB API v2 regions,
#: income groups, lending groups and other groupings).
WB_AGGREGATES = frozenset(
    """AFE AFR AFW ARB CEB CSS EAP EAR EAS ECA ECS EMU EUU FCS HIC HPC IBD IBT IDA IDB IDX
    INX LAC LCN LDC LIC LMC LMY LTE MEA MIC MNA NAC OED OSS PRE PSS PST SAS SSA SSF SST
    TEA TEC TLA TMN TSA TSS UMC WLD""".split()
)

_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
_ISO3_RE = re.compile(r"^[A-Z]{3}$")

#: Breakdown codes (``SEX``/``AGE``/``URBANISATION``/``COMP_BREAKDOWN_x`` values)
#: that carry a *statistic of the estimate* rather than a subgroup:
#: ``code -> "se" | "n"``. **Empty on purpose**: how ``WB_ES`` exposes standard
#: errors and sample sizes through the API is UNVERIFIED, and a regex on code
#: segments misreads subgroups (e.g. ``..._EXPORTER_N`` = "non-exporters") as
#: sample sizes. Fill it with the exact codes once
#: ``tests/live/test_data360_live.py::test_es_breakdown_codes_report`` shows
#: them, or pass ``statistic_codes=`` to :func:`parse_rows` / :meth:`Data360.fetch`.
ES_STATISTIC_CODES: dict[str, str] = {}

# Companion-indicator ids for WB_ES statistics (UNVERIFIED encodings, see module doc).
# Used only when the base indicator and its estimate row are in the same payload.
_ES_PREFIX_STAT = re.compile(r"^(?P<db>WB_ES)_(?P<stat>SE|N|NOBS|N_OBS)_(?P<rest>.+)$")
_SUFFIX_STAT = re.compile(r"^(?P<base>.+?)_(?P<stat>SE|STDERR|STD_ERR|NOBS|N_OBS|OBS_COUNT)$")

# COMMENT_OBS patterns. "Number of observation group: 1000-10000" is verified (WB_LPI_20 export).
_COMMENT_N = re.compile(r"^\s*(?:number|no\.?)\s+of\s+(?:observations?|obs\.?|firms)(?:\s+group)?\s*[:=]\s*(?P<v>.+?)\s*$", re.I)
_COMMENT_N_SHORT = re.compile(r"^\s*(?:n|nobs|sample size)\s*[:=]\s*(?P<v>[0-9][0-9,]*)\s*$", re.I)
_COMMENT_SE = re.compile(r"^\s*(?:standard\s+error|std\.?\s*err(?:or)?|s\.?e\.?)\s*[:=]\s*(?P<v>-?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*$", re.I)


# =============================================================================
# Pure request builders
# =============================================================================


def _check_id(value: str, what: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ValueError(f"invalid Data360 {what}: {value!r} (letters, digits, '_', '.', '-' only)")
    return value


def _as_list(values: str | Iterable[str] | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [v for v in values.split(",")]
    return sorted({v.strip() for v in values if v and v.strip()})


def build_data_request(
    database_id: str,
    indicators: str | Iterable[str] | None = None,
    countries: str | Iterable[str] | None = None,
    start_year: int | str | None = None,
    end_year: int | str | None = None,
    filters: Mapping[str, str] | None = None,
    skip: int = 0,
    top: int = PAGE_SIZE,
) -> tuple[str, dict]:
    """Return ``(url, params)`` for one page of ``GET /data360/data``.

    ``indicators`` and ``countries`` are sorted and comma-joined so the cache
    key does not depend on argument order (comma lists: INDICATOR verified in
    World Bank team code, REF_AREA in the data360-mcp client).
    """
    params: dict[str, str | int] = {"DATABASE_ID": _check_id(database_id, "database id")}
    inds = [_check_id(i, "indicator id") for i in _as_list(indicators)]
    if inds:
        params["INDICATOR"] = ",".join(inds)
    areas = [_check_id(c.upper(), "REF_AREA code") for c in _as_list(countries)]
    if areas:
        params["REF_AREA"] = ",".join(areas)
    for key, value in sorted((filters or {}).items()):
        if key not in FILTER_PARAMS:
            raise ValueError(f"unsupported Data360 filter {key!r}; allowed: {sorted(FILTER_PARAMS)}")
        params[key] = ",".join(_as_list(value)) if isinstance(value, (list, tuple, set)) else str(value)
    if start_year is not None:
        params["timePeriodFrom"] = str(start_year)
    if end_year is not None:
        params["timePeriodTo"] = str(end_year)
    if skip < 0 or top <= 0:
        raise ValueError("skip must be >= 0 and top > 0")
    params["skip"] = int(skip)
    # ``top`` is not in the OpenAPI spec (only ``skip``); data360-mcp sends it
    # ("API uses 'top' not '$top'"). UNVERIFIED against the spec; pagination
    # still works if the server ignores it, because paging follows ``count``.
    params["top"] = int(top)
    return DATA_URL, params


def build_metadata_request(
    indicator_id: str | None = None,
    database_id: str | None = None,
    select: Sequence[str] | None = None,
) -> tuple[str, dict]:
    """Return ``(url, json_body)`` for ``POST /data360/metadata``.

    The body follows the OpenAPI examples: ``{"query": "&$filter=...&$select=..."}``.
    (The data360-mcp client sends ``{"query": "<filter>", "select": ...}``
    instead; which form the server prefers is checked by the live test.)
    """
    if (indicator_id is None) == (database_id is None):
        raise ValueError("pass exactly one of indicator_id or database_id")
    if indicator_id is not None:
        query = f"&$filter=series_description/idno eq '{_check_id(indicator_id, 'indicator id')}'"
    else:
        query = f"&$filter=series_description/database_id eq '{_check_id(str(database_id), 'database id')}'"
    if select:
        fields = ",".join(f"series_description/{_check_id(f, 'metadata field')}" for f in select)
        query += f"&$select={fields}"
    return METADATA_URL, {"query": query}


def build_indicators_request(database_id: str) -> tuple[str, dict]:
    """Return ``(url, params)`` for ``GET /data360/indicators`` (indicator ids of a database)."""
    return INDICATORS_URL, {"datasetId": _check_id(database_id, "database id")}


def build_disaggregation_request(database_id: str, indicator_id: str) -> tuple[str, dict]:
    """Return ``(url, params)`` for ``GET /data360/disaggregation``."""
    return DISAGGREGATION_URL, {
        "datasetId": _check_id(database_id, "database id"),
        "indicatorId": _check_id(indicator_id, "indicator id"),
    }


def build_codelist_request(codelist_type: str) -> tuple[str, dict]:
    """Return ``(url, params)`` for ``GET /data360/codelist?type=...`` (REF_AREA, UNIT_MEASURE)."""
    return CODELIST_URL, {"type": _check_id(codelist_type.upper(), "codelist type")}


DEFAULT_SEARCH_SELECT = (
    "series_description/idno, series_description/name, series_description/database_id, "
    "series_description/measurement_unit, series_description/periodicity, series_description/time_periods"
)


def build_search_request(
    query: str,
    top: int = 10,
    skip: int = 0,
    filter: str | None = None,
    select: str = DEFAULT_SEARCH_SELECT,
) -> tuple[str, dict]:
    """Return ``(url, json_body)`` for ``POST /data360/searchv2`` (OpenAPI ``SearchQuerySchema``)."""
    body: dict = {"search": str(query), "top": int(top), "skip": int(skip), "count": True, "select": select}
    if filter:
        body["filter"] = filter
    return SEARCH_URL, body


# =============================================================================
# Pure parsers
# =============================================================================


def _decode_json(payload: bytes | str | dict | list):
    if isinstance(payload, (dict, list)):
        return payload
    text = payload.decode("utf-8") if isinstance(payload, (bytes, bytearray)) else payload
    return json.loads(text)


def parse_page(payload: bytes | str | dict) -> tuple[list[dict], int | None]:
    """Return ``(rows, total_count)`` from one ``/data360/data`` response.

    The documented envelope is ``{"count": <total>, "value": [rows]}``; older
    clients read ``@odata.count``, so both are accepted.
    """
    obj = _decode_json(payload)
    if isinstance(obj, list):
        return [r for r in obj if isinstance(r, dict)], None
    if not isinstance(obj, dict) or "value" not in obj:
        raise ValueError("unexpected Data360 data response: no 'value' array")
    rows = [r for r in obj.get("value") or [] if isinstance(r, dict)]
    count = obj.get("count", obj.get("@odata.count"))
    return rows, (int(count) if count is not None else None)


def _str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def _num(value) -> float:
    if value is None or value == "":
        return float("nan")
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _json_safe(value):
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def normalize_period(time_period, freq: str = "") -> str:
    """Map a Data360 ``TIME_PERIOD`` to the canonical period string.

    ``2024`` -> ``"2024"``; ``2024-Q1`` -> ``"2024Q1"``; ``2024-M03``/``2024-03`` ->
    ``"2024-03"``; full dates (``2024-01-01``) are cut to the frequency (A, Q, M).
    """
    tp = _str(time_period)
    if re.fullmatch(r"\d{4}", tp):
        return tp
    if re.fullmatch(r"\d{4}\.0", tp):
        return tp[:4]
    m = re.fullmatch(r"(\d{4})-?Q([1-4])", tp)
    if m:
        return f"{m.group(1)}Q{m.group(2)}"
    m = re.fullmatch(r"(\d{4})-M?(\d{2})", tp)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})(?:T.*)?", tp)
    if m:
        year, month = m.group(1), int(m.group(2))
        if freq == "M":
            return f"{year}-{month:02d}"
        if freq == "Q":
            return f"{year}Q{(month - 1) // 3 + 1}"
        return year
    return tp


def is_aggregate(code: str) -> bool:
    """True for World Bank aggregate codes and non-ISO3 groupings (e.g. ``G_LLD``)."""
    return code in WB_AGGREGATES or not _ISO3_RE.match(code)


def _statistic_from_indicator(indicator: str, known: set[str]) -> tuple[str, str]:
    """Return ``(statistic, base_indicator)`` for companion SE/N indicator ids.

    Only matches whose base indicator is itself in ``known`` (the payload)
    count; anything else is an ordinary indicator (``("", indicator)``).
    """
    m = _ES_PREFIX_STAT.match(indicator)
    if m:
        stat = "se" if m.group("stat") == "SE" else "n"
        for base in (f"{m.group('db')}_T_{m.group('rest')}", f"{m.group('db')}_{m.group('rest')}"):
            if base in known:
                return stat, base
        return "", indicator
    m = _SUFFIX_STAT.match(indicator)
    if m and m.group("base") in known:
        stat = "se" if m.group("stat") in ("SE", "STDERR", "STD_ERR") else "n"
        return stat, m.group("base")
    return "", indicator


def parse_comment_obs(text: str) -> list[tuple[str, object]]:
    """Split a ``COMMENT_OBS`` string into ``(field, value)`` pairs.

    Recognised: observation counts (numeric -> ``n``, bands such as
    ``"1000-10000"`` or ``"10000+"`` -> ``n_obs_group``) and standard errors
    (``se``). Anything else is kept verbatim as ``comment_obs``.
    """
    out: list[tuple[str, object]] = []
    for part in re.split(r"[;|]", _str(text)):
        part = part.strip()
        if not part:
            continue
        m = _COMMENT_N.match(part) or _COMMENT_N_SHORT.match(part)
        if m:
            raw = m.group("v").replace(",", "").strip()
            if re.fullmatch(r"\d+", raw):
                out.append(("n", float(raw)))
            else:
                out.append(("n_obs_group", raw))
            continue
        m = _COMMENT_SE.match(part)
        if m:
            out.append(("se", float(m.group("v"))))
            continue
        out.append(("comment_obs", part))
    return out


@dataclass
class ParsedData:
    """Output of :func:`parse_rows`: canonical frame, extras table and parse info."""

    frame: pd.DataFrame
    extras: list[dict] = field(default_factory=list)
    series: dict[str, dict] = field(default_factory=dict)
    aggregates: list[str] = field(default_factory=list)
    #: rows dropped by ``totals_only`` because a more aggregated row (a total) exists for them
    dropped_breakdown_rows: int = 0
    #: dropped rows per Data360 ``INDICATOR``
    dropped_by_indicator: dict[str, int] = field(default_factory=dict)
    #: breakdown codes of the dropped rows, per dimension (shows how a database encodes its breakdowns)
    dropped_codes: dict[str, list[str]] = field(default_factory=dict)
    #: series kept although split by a dimension outside ``keep_dims``, because no total exists for them
    #: (``INDICATOR -> [dimension, ...]``); their codes are appended to the indicator id
    untotalled: dict[str, list[str]] = field(default_factory=dict)
    #: usable rows received per Data360 ``INDICATOR`` (before any filtering)
    rows_by_indicator: dict[str, int] = field(default_factory=dict)
    comment_ts: dict[str, str] = field(default_factory=dict)
    obs_status_raw: dict[str, int] = field(default_factory=dict)

    def series_indicators(self) -> set[str]:
        """Data360 indicator ids present in the parsed series."""
        return {s["INDICATOR"] for s in self.series.values()}

    @property
    def rows_received(self) -> int:
        return sum(self.rows_by_indicator.values())


@dataclass
class _Row:
    raw: dict
    indicator: str
    area: str
    period: str
    dims: dict[str, str]  # non-trivial dimension codes
    base: str = ""  # estimate indicator this row belongs to
    statistic: str = ""  # "se" | "n" | ""


def _dims_key(dims: Mapping[str, str]) -> frozenset:
    return frozenset(dims.items())


def parse_rows(
    rows: Sequence[dict],
    *,
    database_id: str = "",
    totals_only: bool = True,
    keep_dims: Sequence[str] = (),
    detect_statistics: bool = False,
    statistic_codes: Mapping[str, str] | None = None,
    unit_labels: Mapping[str, str] | None = None,
    indicator_units: Mapping[str, str] | None = None,
    indicator_labels: Mapping[str, str] | None = None,
    dim_labels: Mapping[str, str] | None = None,
    country_names: Mapping[str, str] | None = None,
) -> ParsedData:
    """Map Data360 rows onto the canonical frame (pure).

    Rules:

    * ``indicator`` is the Data360 ``INDICATOR`` id, followed by ``|<code>``
      for every non-trivial dimension of the row (order of
      :data:`DIMENSIONS`), and by ``|<UNIT_MEASURE>`` when one indicator comes
      in several units. Examples: ``CT_DT_M|WB_LPI_MD``, ``WB_WDI_SE_PRM_CMPT_FE_ZS|F``.
    * ``totals_only`` drops a row only when a *more aggregated row exists*: a
      row for the same indicator, area, period and ``keep_dims`` codes whose
      other non-trivial codes are a strict subset of this row's (``_T`` where
      this row has a subgroup code). Rows with no such total (a female-only
      series, WEF_TTDI value/rank/score, ...) are kept as separate series and
      listed in ``untotalled``. Dropped rows are counted.
    * ``detect_statistics`` moves standard-error and sample-size rows into the
      extras table, keyed on the estimate they belong to. A row counts as a
      statistic only if (a) one of its breakdown codes is listed exactly in
      ``statistic_codes`` (default :data:`ES_STATISTIC_CODES`, empty until the
      encoding is verified), or (b) its id is a companion id
      (``WB_ES_SE_<x>``/``WB_ES_N_<x>``, ``<x>_SE``, ``<x>_NOBS``) of an indicator
      in the payload; and in both cases the matching estimate row exists. The
      statistic row then follows its estimate through the totals filter.
    * ``COMMENT_OBS`` contents and non-normal ``OBS_STATUS`` codes go to extras.
    """
    keep = set(keep_dims)
    stat_codes = dict(ES_STATISTIC_CODES if statistic_codes is None else statistic_codes)
    unit_labels = unit_labels or {}
    indicator_units = indicator_units or {}
    indicator_labels = indicator_labels or {}
    dim_labels = {**LPI20_METRICS, **(dim_labels or {})}
    country_names = country_names or {}
    out = ParsedData(frame=empty_frame())

    # Pass 1: normalise rows.
    items: list[_Row] = []
    for r in rows:
        indicator = _str(r.get("INDICATOR"))
        area = _str(r.get("REF_AREA")).upper()
        period = normalize_period(r.get("TIME_PERIOD"), _str(r.get("FREQ")))
        if not indicator or not area or not period:
            continue
        dims = {d: _str(r.get(d)) for d in DIMENSIONS if _str(r.get(d)) not in TRIVIAL_CODES}
        items.append(_Row(r, indicator, area, period, dims, base=indicator))
        out.rows_by_indicator[indicator] = out.rows_by_indicator.get(indicator, 0) + 1
    known_indicators = {it.indicator for it in items}
    row_keys = {(it.indicator, it.area, it.period, _dims_key(it.dims)) for it in items}

    # Pass 2: statistics (exact codes or companion ids, and only next to their estimate).
    if detect_statistics:
        for it in items:
            stat_dims = [d for d in DIMENSIONS if d in it.dims and it.dims[d] in stat_codes]
            if len(stat_dims) == 1:
                rest = {d: c for d, c in it.dims.items() if d != stat_dims[0]}
                if (it.indicator, it.area, it.period, _dims_key(rest)) in row_keys:
                    it.statistic, it.dims = stat_codes[it.dims[stat_dims[0]]], rest
                    continue
            stat, base = _statistic_from_indicator(it.indicator, known_indicators)
            if stat and (base, it.area, it.period, _dims_key(it.dims)) in row_keys:
                it.statistic, it.base = stat, base

    # Pass 3: totals filter. Rows are compared within (estimate indicator, area,
    # period, kept codes); an estimate row whose split codes are a strict subset
    # of another row's split codes is a total for it.
    def split_of(it: _Row) -> frozenset:
        return frozenset((d, c) for d, c in it.dims.items() if d not in keep)

    classified: list[_Row] = []
    if totals_only:
        groups: dict[tuple, list[frozenset]] = {}
        for it in items:
            if not it.statistic:
                kept = tuple((d, it.dims[d]) for d in DIMENSIONS if d in keep and d in it.dims)
                groups.setdefault((it.base, it.area, it.period, kept), []).append(split_of(it))
        untotalled: dict[str, set[str]] = {}
        dropped_codes: dict[str, set[str]] = {}
        for it in items:
            split = split_of(it)
            if split:
                kept = tuple((d, it.dims[d]) for d in DIMENSIONS if d in keep and d in it.dims)
                if any(other < split for other in groups.get((it.base, it.area, it.period, kept), [])):
                    out.dropped_breakdown_rows += 1
                    out.dropped_by_indicator[it.indicator] = out.dropped_by_indicator.get(it.indicator, 0) + 1
                    for d, c in split:
                        dropped_codes.setdefault(d, set()).add(c)
                    continue
                untotalled.setdefault(it.base, set()).update(d for d, _ in split)
            classified.append(it)
        out.untotalled = {k: [d for d in DIMENSIONS if d in v] for k, v in sorted(untotalled.items())}
        out.dropped_codes = {d: sorted(dropped_codes[d]) for d in DIMENSIONS if d in dropped_codes}
    else:
        classified = items

    # Units are folded into the code only when one estimate series comes in several units.
    units_per_base: dict[str, set[str]] = {}
    for it in classified:
        if not it.statistic:
            units_per_base.setdefault(it.base, set()).add(_str(it.raw.get("UNIT_MEASURE")))

    # Pass 4: build canonical records and the extras table.
    records: list[dict] = []
    extras: list[dict] = []
    aggregates: set[str] = set()
    for it in classified:
        r, area, period, base_indicator, dims = it.raw, it.area, it.period, it.base, it.dims
        unit_code = _str(r.get("UNIT_MEASURE"))
        code = base_indicator + "".join(f"|{dims[d]}" for d in DIMENSIONS if d in dims)
        base_units = units_per_base.get(base_indicator, set())
        if len(base_units) > 1 and unit_code in base_units:
            code += f"|{unit_code}"

        db = _str(r.get("DATABASE_ID")) or database_id
        if is_aggregate(area):
            aggregates.add(area)
        value = _num(r.get("OBS_VALUE"))
        key = {"indicator": code, "iso3": area, "period": period}

        if it.statistic in ("se", "n"):
            extras.append({**key, "field": it.statistic, "value": _json_safe(value)})
            continue

        raw_status = _str(r.get("OBS_STATUS"))
        if raw_status and raw_status != "A":
            out.obs_status_raw[raw_status] = out.obs_status_raw.get(raw_status, 0) + 1
            if raw_status not in OBS_STATUS_MAP:
                extras.append({**key, "field": "obs_status_raw", "value": raw_status})
        comment = _str(r.get("COMMENT_OBS"))
        if comment:
            for fld, val in parse_comment_obs(comment):
                extras.append({**key, "field": fld, "value": val})
        comment_ts = _str(r.get("COMMENT_TS"))
        if comment_ts and base_indicator not in out.comment_ts:
            out.comment_ts[base_indicator] = comment_ts

        unit = indicator_units.get(base_indicator) or unit_labels.get(unit_code) or BUILTIN_UNITS.get(unit_code) or unit_code
        mult = _str(r.get("UNIT_MULT"))
        if mult not in ("", "0"):
            unit = f"{unit} (x10^{mult})"
        label = indicator_labels.get(base_indicator) or comment_ts
        suffix = [dim_labels.get(dims[d], dims[d]) for d in DIMENSIONS if d in dims]
        if suffix:
            label = f"{label or base_indicator} ({', '.join(suffix)})"

        if code not in out.series:
            out.series[code] = {"INDICATOR": base_indicator, "UNIT_MEASURE": unit_code, **{d: dims[d] for d in DIMENSIONS if d in dims}}
        records.append(
            {
                **key,
                "dataset": db,
                "indicator_label": label,
                "country": country_names.get(area, ""),
                "value": value,
                "unit": unit,
                "obs_status": OBS_STATUS_MAP.get(raw_status, ""),
            }
        )

    if records:
        frame = make_frame(records, source=Data360.source, dataset=database_id)
        frame = frame.drop_duplicates().reset_index(drop=True)
        dupes = frame.duplicated(subset=["source", "dataset", "indicator", "iso3", "period"], keep=False)
        if dupes.any():
            sample = frame.loc[dupes, ["indicator", "iso3", "period"]].head(5).to_dict("records")
            raise ValueError(
                "Data360 payload has conflicting values for the same series/area/period "
                f"(e.g. {sample}); add a dimension to keep_dims or pass filters to narrow the query"
            )
        out.frame = Connector.finalize(frame)
    extras.sort(key=lambda e: (e["indicator"], e["iso3"], e["period"], e["field"], str(e["value"])))
    out.extras = extras
    out.aggregates = sorted(aggregates)
    return out


def parse(payload: bytes | str | dict, **options) -> pd.DataFrame:
    """Parse one ``/data360/data`` payload into the canonical frame (see :func:`parse_rows`)."""
    rows, _ = parse_page(payload)
    return parse_rows(rows, **options).frame


def parse_metadata(payload: bytes | str | dict) -> dict[str, dict]:
    """Return ``{indicator_id: {...}}`` from a ``/data360/metadata`` response.

    Fields kept: name, database_id, database_name, measurement_unit,
    periodicity, version_date (``version_statement.version_date``), license
    (every ``license[].name``, joined with ``"; "``), citation
    (``citation_requirement``), aliases (e.g. the WDI code) and time_periods.
    """
    obj = _decode_json(payload)
    values = obj.get("value", []) if isinstance(obj, dict) else obj
    out: dict[str, dict] = {}
    for item in values or []:
        sd = item.get("series_description", item) if isinstance(item, dict) else {}
        idno = _str(sd.get("idno"))
        if not idno:
            continue
        version = sd.get("version_statement") or {}
        if isinstance(version, list):
            version = version[0] if version else {}
        licenses = sd.get("license") or []
        if isinstance(licenses, dict):
            licenses = [licenses]
        aliases = [a.get("alias") for a in sd.get("aliases") or [] if isinstance(a, dict) and a.get("alias")]
        out[idno] = {
            "name": _str(sd.get("name")),
            "database_id": _str(sd.get("database_id")),
            "database_name": _str(sd.get("database_name")),
            "measurement_unit": _str(sd.get("measurement_unit")),
            "periodicity": _str(sd.get("periodicity")),
            "version_date": _str(version.get("version_date")) if isinstance(version, dict) else "",
            "license": "; ".join(
                dict.fromkeys(_str(lic.get("name")) for lic in licenses if isinstance(lic, dict) and _str(lic.get("name")))
            ),
            "citation": _str(sd.get("citation_requirement")),
            "aliases": aliases,
            "time_periods": sd.get("time_periods") or [],
        }
    return out


def parse_indicators(payload: bytes | str | dict | list) -> list[str]:
    """Return the indicator ids from ``/data360/indicators`` (a bare JSON list of ids)."""
    obj = _decode_json(payload)
    if isinstance(obj, dict):
        obj = obj.get("value", [])
    ids = []
    for item in obj or []:
        if isinstance(item, str):
            ids.append(item)
        elif isinstance(item, dict):
            ids.append(_str(item.get("idno") or item.get("id") or item.get("INDICATOR")))
    return sorted({i for i in ids if i})


def parse_codelist(payload: bytes | str | dict) -> dict[str, str]:
    """Return ``{code: name}`` from ``/data360/codelist`` (items ``{"Id": ..., "Name": ...}``)."""
    obj = _decode_json(payload)
    values = obj.get("value", []) if isinstance(obj, dict) else obj
    out = {}
    for item in values or []:
        if isinstance(item, dict):
            code = _str(item.get("Id", item.get("id")))
            if code:
                out[code] = _str(item.get("Name", item.get("name")))
    return out


def parse_disaggregation(payload: bytes | str | dict | list) -> dict[str, list[str]]:
    """Return ``{field_name: [values]}`` from ``/data360/disaggregation``."""
    obj = _decode_json(payload)
    if isinstance(obj, dict):
        obj = obj.get("value", obj.get("dimensions", []))
    out: dict[str, list[str]] = {}
    for item in obj or []:
        if isinstance(item, dict) and item.get("field_name"):
            out[_str(item["field_name"])] = [_str(v) for v in item.get("field_value") or []]
    return out


def parse_search(payload: bytes | str | dict) -> list[dict]:
    """Return the ``series_description`` records of a ``/data360/searchv2`` response."""
    obj = _decode_json(payload)
    out = []
    for item in obj.get("value", []) if isinstance(obj, dict) else []:
        sd = item.get("series_description") if isinstance(item, dict) else None
        if isinstance(sd, dict) and sd.get("idno"):
            out.append(sd)
    return out


def is_cc_by(license_name: str) -> bool:
    """True for plain CC BY licences; False for NC/ND variants and anything else."""
    u = re.sub(r"[-_]", " ", _str(license_name).upper())
    attribution = "CC BY" in u or ("CREATIVE COMMONS" in u and "ATTRIBUTION" in u)
    restricted = re.search(r"\bNC\b|NON ?COMMERCIAL|\bND\b|NO ?DERIV", u) is not None
    return attribution and not restricted


def extras_frame(result: Result) -> pd.DataFrame:
    """Pivot ``result.meta["extras"]`` to one row per observation (se, n, n_obs_group, ...)."""
    extras = result.meta.get("extras") or []
    if not extras:
        return pd.DataFrame(columns=["indicator", "iso3", "period"])
    df = pd.DataFrame(extras)
    wide = df.pivot_table(index=["indicator", "iso3", "period"], columns="field", values="value", aggfunc="first")
    return wide.reset_index().rename_axis(columns=None)


#: ``Provenance.license`` of Data360 data records when no indicator licence could be read.
LICENSE_UNVERIFIED = "unverified (Data360 per-indicator licence not read)"


def provenance_license(meta_info: Mapping[str, dict], indicators: Iterable[str]) -> tuple[str, list[str]]:
    """Licence string for the data provenance of ``indicators``, and the indicators without one.

    The metadata licences of the indicators are joined with ``"; "``; if some
    indicators have no licence in ``meta_info`` (metadata not read), the string
    says so. Pure.
    """
    inds = sorted(set(indicators))
    by_ind = {i: meta_info[i]["license"] for i in inds if (meta_info.get(i) or {}).get("license")}
    unread = [i for i in inds if i not in by_ind]
    licences = sorted(set(by_ind.values()))
    if not licences:
        return LICENSE_UNVERIFIED, unread
    text = "; ".join(licences)
    if unread:
        text += f"; unverified for {len(unread)} indicator(s)"
    return text, unread


# =============================================================================
# Connector
# =============================================================================


class Data360(Connector):
    """World Bank Data360 API (no key needed; no documented rate limit)."""

    source = "data360"
    #: Licence of the API itself (OpenAPI ``info.license``). Data records get the
    #: per-indicator licence from the metadata instead (see :func:`provenance_license`),
    #: because Data360 also hosts third-party databases under other terms.
    license = "CC BY 4.0"
    citation = "World Bank Data360"
    domains = ("data360api.worldbank.org",)

    # pure functions, exposed on the class for discoverability
    build_request = staticmethod(build_data_request)
    parse = staticmethod(parse)
    parse_rows = staticmethod(parse_rows)
    parse_metadata = staticmethod(parse_metadata)

    def __init__(self, cache: Cache | None = None):
        super().__init__(cache)

    # -- small endpoints -------------------------------------------------------
    def _get(self, url: str, params: dict | None, *, refresh: bool = False):
        return http_fetch(self.source, url, params, cache=self.cache, refresh=refresh)

    def _post(self, url: str, body: dict, *, refresh: bool = False):
        return http_fetch(self.source, url, None, cache=self.cache, refresh=refresh, method="POST", json_body=body)

    def list_indicators(self, database_id: str, *, refresh: bool = False) -> list[str]:
        """Indicator ids available in a Data360 database (e.g. ``WB_ES``, ``WB_LPI_20``)."""
        url, params = build_indicators_request(database_id)
        payload, _ = self._get(url, params, refresh=refresh)
        return parse_indicators(payload)

    def metadata(self, indicator_id: str | None = None, database_id: str | None = None, *, select: Sequence[str] | None = None, refresh: bool = False) -> dict[str, dict]:
        """Indicator metadata (name, unit, version date, licence, citation) keyed by indicator id."""
        url, body = build_metadata_request(indicator_id, database_id, select)
        payload, _ = self._post(url, body, refresh=refresh)
        return parse_metadata(payload)

    def disaggregation(self, database_id: str, indicator_id: str, *, refresh: bool = False) -> dict[str, list[str]]:
        """Available dimension values (TIME_PERIOD, REF_AREA, SEX, COMP_BREAKDOWN_x, ...)."""
        url, params = build_disaggregation_request(database_id, indicator_id)
        payload, _ = self._get(url, params, refresh=refresh)
        return parse_disaggregation(payload)

    def codelist(self, codelist_type: str, *, refresh: bool = False) -> dict[str, str]:
        """Global codelist ``{code: name}`` (REF_AREA or UNIT_MEASURE)."""
        url, params = build_codelist_request(codelist_type)
        payload, _ = self._get(url, params, refresh=refresh)
        return parse_codelist(payload)

    def search(self, query: str, *, top: int = 10, skip: int = 0, filter: str | None = None, refresh: bool = False) -> list[dict]:
        """Free-text indicator search (vector search on the metadata index)."""
        url, body = build_search_request(query, top=top, skip=skip, filter=filter)
        payload, _ = self._post(url, body, refresh=refresh)
        return parse_search(payload)

    # -- data ------------------------------------------------------------------
    def fetch(
        self,
        database_id: str,
        indicators: str | Iterable[str] | None = None,
        countries: str | Iterable[str] | None = None,
        start_year: int | str | None = None,
        end_year: int | str | None = None,
        *,
        filters: Mapping[str, str] | None = None,
        totals_only: bool = True,
        keep_dims: Sequence[str] = (),
        detect_statistics: bool = False,
        statistic_codes: Mapping[str, str] | None = None,
        with_metadata: bool = True,
        with_labels: bool = True,
        refresh: bool = False,
        page_size: int = PAGE_SIZE,
        max_pages: int = 500,
        tool: str = "Data360.fetch",
    ) -> Result:
        """Fetch observations from ``/data360/data`` (all pages) as a canonical Result.

        ``with_metadata`` adds indicator names, units, version dates and
        licences (one metadata call per requested indicator, or one per
        database when ``indicators`` is None). ``with_labels`` adds country
        names and unit labels from the global codelists. Both are optional
        enrichments: if they fail, the data is returned with a note. The
        provenance licence of the data records comes from the indicator
        metadata (``"unverified ..."`` when it could not be read).
        ``statistic_codes`` is passed to :func:`parse_rows`.
        """
        rows: list[dict] = []
        provs: list[Provenance] = []
        notes: list[str] = []
        skip = 0
        total: int | None = None
        for _page in range(max_pages):
            url, params = build_data_request(database_id, indicators, countries, start_year, end_year, filters, skip=skip, top=page_size)
            payload, retrieval = self._get(url, params, refresh=refresh)
            page_rows, count = parse_page(payload)
            provs.append(self.provenance(retrieval, dataset=database_id, tool=tool))
            rows.extend(page_rows)
            total = count if count is not None else total
            skip += len(page_rows)
            if not page_rows or (total is not None and skip >= total) or (total is None and len(page_rows) < page_size):
                break
        else:
            raise RuntimeError(f"Data360 pagination exceeded {max_pages} pages for {database_id}; narrow the query")

        requested = _as_list(indicators)
        meta_info: dict[str, dict] = {}
        if with_metadata:
            targets: list[str | None] = list(requested) or [None]
            for ind in targets:
                try:
                    url, body = build_metadata_request(indicator_id=ind) if ind else build_metadata_request(database_id=database_id)
                    payload, retrieval = self._post(url, body, refresh=refresh)
                    meta_info.update(parse_metadata(payload))
                    provs.append(self.provenance(retrieval, dataset=database_id, tool="Data360.metadata"))
                except (NetworkBlocked, FetchError, ValueError) as exc:
                    notes.append(f"metadata unavailable for {ind or database_id}: {exc.__class__.__name__}")
        names: dict[str, str] = {}
        unit_names: dict[str, str] = {}
        if with_labels:
            for ctype, target in (("REF_AREA", names), ("UNIT_MEASURE", unit_names)):
                try:
                    url, params = build_codelist_request(ctype)
                    payload, retrieval = self._get(url, params, refresh=refresh)
                    target.update(parse_codelist(payload))
                    provs.append(self.provenance(retrieval, dataset=database_id, tool=f"Data360.codelist[{ctype}]"))
                except (NetworkBlocked, FetchError, ValueError) as exc:
                    notes.append(f"{ctype} codelist unavailable: {exc.__class__.__name__}")

        parsed = parse_rows(
            rows,
            database_id=database_id,
            totals_only=totals_only,
            keep_dims=keep_dims,
            detect_statistics=detect_statistics,
            statistic_codes=statistic_codes,
            unit_labels=unit_names,
            indicator_units={k: v["measurement_unit"] for k, v in meta_info.items() if v.get("measurement_unit")},
            indicator_labels={k: v["name"] for k, v in meta_info.items() if v.get("name")},
            country_names=names,
        )
        validate_frame(parsed.frame)

        dates = sorted({v["version_date"] for v in meta_info.values() if v.get("version_date")})
        vintage = f"version_date {dates[-1]}" if dates else ""
        if not vintage:
            notes.append("Data360 exposes no release/vintage endpoint; the payload hash pins this snapshot")
        in_scope = parsed.series_indicators() or set(requested)
        data_license, unread = provenance_license(meta_info, in_scope)
        for p in provs:
            if p.tool == tool:
                p.vintage = vintage
                p.license = data_license
                p.notes = list(notes)

        result = Result(
            data=parsed.frame,
            provenance=provs,
            meta={
                "database_id": database_id,
                "aggregates": parsed.aggregates,
                "series": parsed.series,
                "extras": parsed.extras,
                "dropped_breakdown_rows": parsed.dropped_breakdown_rows,
                "dropped_breakdown_codes": parsed.dropped_codes,
                "untotalled_series": parsed.untotalled,
                "license": data_license,
                "rows_received": len(rows),
                "total_count": total,
                "metadata": {k: {kk: vv for kk, vv in v.items() if kk != "time_periods"} for k, v in sorted(meta_info.items())},
                "obs_status_raw": parsed.obs_status_raw,
            },
        )
        for cav in self._generic_caveats(parsed, meta_info, requested):
            result.add_caveat(cav)
        if unread:
            result.add_caveat(
                Caveat(
                    "data360_license_unverified",
                    "info",
                    "The per-indicator licence could not be read from the Data360 metadata; Data360 hosts databases "
                    "from other publishers under their own terms (the API itself is CC BY 4.0). Check the licence "
                    "before reuse.",
                    {"indicators": unread[:40], "provenance_license": data_license},
                )
            )
        return result

    @staticmethod
    def _generic_caveats(parsed: ParsedData, meta_info: Mapping[str, dict], requested: Sequence[str]) -> list[Caveat]:
        cavs: list[Caveat] = []
        df = parsed.frame
        if df.empty and parsed.dropped_breakdown_rows:
            cavs.append(
                Caveat(
                    "data360_all_rows_filtered",
                    "warn",
                    f"Data360 returned {parsed.rows_received} rows, but the breakdown filter (totals_only) dropped all "
                    "of them; pass keep_dims or totals_only=False to keep breakdown series.",
                    {"requested": list(requested), "dropped_breakdown_rows": parsed.dropped_breakdown_rows, "dropped_codes": parsed.dropped_codes},
                )
            )
        elif df.empty:
            cavs.append(Caveat("data360_empty", "warn", "Data360 returned no observations for this query.", {"requested": list(requested)}))
        missing = sorted(set(requested) - parsed.series_indicators())
        filtered = [i for i in missing if parsed.dropped_by_indicator.get(i)]
        absent = [i for i in missing if i not in filtered]
        if absent:
            cavs.append(Caveat("data360_missing_indicators", "warn", "Some requested indicators returned no observations.", {"indicators": absent}))
        if parsed.dropped_breakdown_rows:
            cavs.append(
                Caveat(
                    "data360_breakdowns_dropped",
                    "warn" if filtered else "info",
                    "Breakdown rows (subgroups of a total that is present) were dropped by totals_only"
                    + ("; some requested indicators have no rows left" if filtered else "")
                    + ". Pass keep_dims or totals_only=False to keep them.",
                    {
                        "dropped_breakdown_rows": parsed.dropped_breakdown_rows,
                        "by_indicator": dict(sorted(parsed.dropped_by_indicator.items())),
                        "indicators_with_no_rows_left": filtered,
                        "dropped_codes": parsed.dropped_codes,
                    },
                )
            )
        if parsed.untotalled:
            cavs.append(
                Caveat(
                    "data360_no_total",
                    "info",
                    "Some series have no total ('_T') for a breakdown dimension; they were kept as separate series with "
                    "the breakdown code appended to the indicator id (e.g. 'ID|F').",
                    {"series": dict(list(parsed.untotalled.items())[:40])},
                )
            )
        flagged = df[df["obs_status"] != ""] if not df.empty else df
        if len(flagged):
            counts = flagged["obs_status"].value_counts().to_dict()
            cavs.append(Caveat("data360_flagged_obs", "info", "Some observations carry provider status flags (see obs_status).", {"counts": {k: int(v) for k, v in counts.items()}}))
        other = sorted({f"{k}: {v['license']}" for k, v in meta_info.items() if v.get("license") and not is_cc_by(v["license"])})
        if other:
            cavs.append(Caveat("data360_license", "warn", "Some indicators are not under CC BY 4.0; check reuse terms before publishing.", {"licenses": other}))
        return cavs

    # -- wrappers used by CGJR charts ---------------------------------------------
    def fetch_enterprise_surveys(
        self,
        indicators: str | Iterable[str],
        countries: str | Iterable[str] | None = None,
        start_year: int | str | None = None,
        end_year: int | str | None = None,
        *,
        breakdowns: bool = False,
        **kwargs,
    ) -> Result:
        """Enterprise Surveys aggregates (Data360 database ``WB_ES``).

        By default only economy-wide values are kept (all breakdown
        dimensions ``_T``, as in World Bank team code); ``breakdowns=True``
        keeps subgroup series (firm size, sector, ...) with the breakdown codes
        appended to the indicator id. Standard errors and sample sizes, when
        the payload carries them in a recognised form (``COMMENT_OBS`` text,
        companion ``WB_ES_SE_*``/``WB_ES_N_*`` indicators next to their
        estimate, or breakdown codes listed in ``statistic_codes`` /
        :data:`ES_STATISTIC_CODES`), are moved to ``meta["extras"]`` (fields
        ``se`` and ``n``) instead of being mixed with the estimates. Other
        breakdown codes are treated as subgroups, never guessed to be statistics.
        """
        inds = _as_list(indicators)
        if not inds:
            raise ValueError("pass WB_ES indicator ids (see Data360().list_indicators('WB_ES')); the full database is too large to pull at once")
        res = self.fetch(
            ES_DATABASE,
            inds,
            countries,
            start_year,
            end_year,
            totals_only=not breakdowns,
            keep_dims=DIMENSIONS if breakdowns else (),
            detect_statistics=True,
            tool="Data360.fetch_enterprise_surveys",
            **kwargs,
        )
        fields = {e["field"] for e in res.meta["extras"]}
        res.meta["standard_errors_found"] = "se" in fields
        res.meta["sample_sizes_found"] = "n" in fields or "n_obs_group" in fields
        res.add_caveat(
            Caveat(
                "es_survey_waves",
                "info",
                "Enterprise Surveys values refer to survey waves (fieldwork year), not annual series; waves are "
                "irregular and countries are surveyed in different years, so peer comparisons mix years.",
                {"periods": sorted(res.data["period"].unique().tolist()) if not res.data.empty else []},
            )
        )
        if not (res.meta["standard_errors_found"] and res.meta["sample_sizes_found"]):
            res.add_caveat(
                Caveat(
                    "es_se_missing",
                    "warn",
                    "No standard errors and/or sample sizes were found in a recognised form for these ES indicators; "
                    "differences between economies or waves cannot be tested from this result (the indicator "
                    "downloads on enterprisesurveys.org include SE and n). Breakdown codes are not read as "
                    "statistics unless listed in statistic_codes (the WB_ES encoding is UNVERIFIED); the codes of "
                    "dropped breakdown rows are in meta['dropped_breakdown_codes'].",
                    {
                        "standard_errors_found": res.meta["standard_errors_found"],
                        "sample_sizes_found": res.meta["sample_sizes_found"],
                        "dropped_breakdown_codes": res.meta.get("dropped_breakdown_codes", {}),
                    },
                )
            )
        if breakdowns:
            res.add_caveat(
                Caveat(
                    "es_small_cells",
                    "info",
                    "Subgroup estimates rest on fewer firms; check n before charting (ES suppresses cells under 5 firms).",
                    {},
                )
            )
        return res

    def fetch_lpi20(
        self,
        indicators: str | Iterable[str] | None = None,
        countries: str | Iterable[str] | None = None,
        *,
        metrics: str | Iterable[str] | None = None,
        **kwargs,
    ) -> Result:
        """Logistics Performance Indicators 2.0 (Data360 database ``WB_LPI_20``).

        Each indicator comes with several statistics in ``COMP_BREAKDOWN_1``
        (mean, median, interquartile ranges, TEU-weighted mean/median); they
        are kept as separate series, e.g. ``CT_DT_M|WB_LPI_MD``. ``metrics``
        filters on those codes (see :data:`LPI20_METRICS`). Indicators without
        a statistic breakdown keep their plain id. ``indicators=None`` pulls the
        whole database (about 12,000 rows in the 2023-24 release).
        """
        res = self.fetch(
            LPI20_DATABASE,
            indicators,
            countries,
            totals_only=True,
            keep_dims=("COMP_BREAKDOWN_1",),
            tool="Data360.fetch_lpi20",
            **kwargs,
        )
        wanted = set(_as_list(metrics))
        if wanted:
            keep_codes = {code for code, s in res.meta["series"].items() if s.get("COMP_BREAKDOWN_1") in wanted}
            res.data = res.data[res.data["indicator"].isin(keep_codes)].reset_index(drop=True)
            res.meta["extras"] = [e for e in res.meta["extras"] if e["indicator"] in keep_codes]
            res.meta["series"] = {k: v for k, v in res.meta["series"].items() if k in keep_codes}
        res.add_caveat(
            Caveat(
                "lpi20_series_break",
                "warn",
                "LPI 2.0 (shipment-tracking data, 2023-24) is a different instrument from the survey-based LPI "
                "(2007-2023, WDI LP.LPI.*); do not chart them as one series or compare scores across the two.",
                {"database": LPI20_DATABASE},
            )
        )
        res.add_caveat(
            Caveat(
                "lpi20_statistics",
                "info",
                "Each LPI 2.0 indicator is published as several statistics (mean, median, IQR, TEU-weighted); pick one "
                "consistently. Observation-count bands are in meta['extras'] (field n_obs_group).",
                {"metrics": sorted({s.get("COMP_BREAKDOWN_1", "") for s in res.meta["series"].values()} - {""})},
            )
        )
        return res
