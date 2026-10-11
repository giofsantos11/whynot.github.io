"""ILOSTAT connector (International Labour Organization labour statistics).

Two access paths, both keyless (see ``docs/apis/ilostat.md`` for what is
verified and from where):

* **Bulk download service** (default, ``via="bulk"``), the service behind
  ilostat.ilo.org/data/bulk and the Rilostat R package::

      https://rplumber.ilo.org/data/indicator/?id=<ID>&ref_area=KEN+UGA&sex=SEX_T
          &classif1=ECO_AGGREGATE_AGR&timefrom=2000&timeto=2024&type=code&format=.csv
      https://rplumber.ilo.org/metadata/toc/indicator?lang=en&format=.csv   (table of contents)
      https://rplumber.ilo.org/metadata/dic/?var=classif1&lang=en&format=.csv (code lists)

  ``<ID>`` is the indicator code plus the frequency, e.g.
  ``EMP_2EMP_SEX_ECO_NB_A``. Multiple values are joined with a literal ``+``
  (as in every published example), so the query string is composed here
  rather than by httpx, which would send ``%2B``.

  The CSV has one row per observation with the columns ``ref_area, source,
  indicator, sex, classif1, classif2, time, obs_value, obs_status,
  note_classif, note_indicator, note_source`` (classification and note
  columns appear only when the indicator has them; ``type=both`` adds
  ``<col>.label`` columns). ``time`` is ``2024``, ``2024Q1`` or ``2024M01``.

* **SDMX REST** (``via="sdmx"``)::

      https://sdmx.ilo.org/rest/data/ILO,DF_<INDICATOR>,1.0/<REF_AREA>.<FREQ>.<MEASURE>.<classif...>
          ?startPeriod=2000&endPeriod=2024&format=csv

  The key layout (REF_AREA, FREQ, MEASURE, then one dimension per
  classification in the indicator code) is inferred from two published
  examples and is UNVERIFIED; so are the SDMX-CSV column names. A live test
  checks both.

ILO modelled estimates (ILOEST) are identified by the source code prefix
``XA:`` ("ILO - Modelled Estimates"), by a source label or indicator label
containing "modelled estimates", or by the table of contents (database
``ILOEST``). Their rows get ``obs_status="modelled"`` and a ``warn`` caveat:
they are harmonised, model-based values (imputed where no national data
exist), revised with every edition, and must never be spliced with national
survey series. The edition ("Nov. 2025") is read from the indicator label and
recorded as ``Provenance.vintage = "ILOEST Nov. 2025"``. ILOSTAT keeps no
archive of past editions, so replications must pin snapshots
(``CGJRKIT_SNAPSHOT``).

Design: :func:`build_request`, :func:`request_url`, :func:`build_sdmx_request`,
:func:`parse`, :func:`parse_sdmx`, :func:`parse_toc`, :func:`parse_dictionary`
and :func:`build_caveats` are pure; :class:`ILOSTAT` wires them through
:func:`cgjrkit.core.http.fetch`.
"""

from __future__ import annotations

import gzip
import io
import json
import re
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode

import numpy as np
import pandas as pd

from cgjrkit.connectors.base import Connector
from cgjrkit.core import http
from cgjrkit.core.cache import Cache
from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import empty_frame, make_frame, ppp_base, validate_frame, year_of

BULK_BASE = "https://rplumber.ilo.org"
DATA_URL = f"{BULK_BASE}/data/indicator/"
TOC_URL = f"{BULK_BASE}/metadata/toc/indicator"
DIC_URL = f"{BULK_BASE}/metadata/dic/"
SDMX_BASE = "https://sdmx.ilo.org/rest"

#: Frequencies used in ILOSTAT ids (annual, quarterly, monthly).
FREQUENCIES = ("A", "Q", "M")
#: Source-code prefix of ILO modelled estimates ("XA:2198", "XA:2070", ...).
MODELLED_SOURCE_PREFIX = "XA:"
#: SDMX queries return at most this many records (Rilostat documentation; UNVERIFIED for the current service).
SDMX_MAX_RECORDS = 300_000

#: ILOSTAT ``obs_status`` codes -> ``schema.OBS_STATUS`` for non-modelled rows.
#: A, B, I, M, R and U are in the ILOSTAT code list (third-party read of
#: ``metadata/dic?var=obs_status``, 2026-10-06); E, F and P follow SDMX
#: CL_OBS_STATUS and are UNVERIFIED for ILOSTAT. Unknown codes map to "" and
#: raise an ``info`` caveat. Modelled rows are always ``"modelled"``; their
#: raw flag (``R`` = based on a real national value) is kept in meta.
OBS_STATUS_MAP: dict[str, str] = {
    "": "",
    "R": "",            # real value
    "A": "estimate",    # adjusted
    "B": "break",       # break in series
    "I": "imputed",     # imputation
    "M": "modelled",    # model-based extrapolation
    "U": "",            # unreliable -> caveat
    "E": "estimate",
    "F": "forecast",
    "P": "provisional",
}
OBS_STATUS_LABELS: dict[str, str] = {
    "A": "Adjusted", "B": "Break in series", "I": "Imputation", "M": "Model-based extrapolation",
    "R": "Real value", "U": "Unreliable",
}

#: Labels for common classification codes, from the ILOSTAT ``classif1`` and
#: ``sex`` code lists (as mirrored by open-numbers/ddf--ilo--ilostat, Feb 2026).
#: Combined ISIC codes used by ILOEST (``ECO_ISIC4_DE`` ...) are not in that
#: mirror; :func:`classif_label` builds a label from the sections and marks it.
CLASSIF_LABELS: dict[str, str] = {
    "SEX_T": "Total", "SEX_M": "Male", "SEX_F": "Female", "SEX_O": "Other",
    "AGE_YTHADULT_YGE15": "Age (Youth, adults): 15+",
    "AGE_YTHADULT_Y15-64": "Age (Youth, adults): 15-64",
    "AGE_YTHADULT_Y15-24": "Age (Youth, adults): 15-24",
    "AGE_YTHADULT_YGE25": "Age (Youth, adults): 25+",
    "ECO_SECTOR_TOTAL": "Economic activity (Broad sector): Total",
    "ECO_SECTOR_AGR": "Economic activity (Broad sector): Agriculture",
    "ECO_SECTOR_NAG": "Economic activity (Broad sector): Non-agriculture",
    "ECO_SECTOR_IND": "Economic activity (Broad sector): Industry",
    "ECO_SECTOR_SER": "Economic activity (Broad sector): Services",
    "ECO_SECTOR_X": "Economic activity (Broad sector): Not classified",
    "ECO_AGGREGATE_TOTAL": "Economic activity (Aggregate): Total",
    "ECO_AGGREGATE_AGR": "Economic activity (Aggregate): Agriculture",
    "ECO_AGGREGATE_MAN": "Economic activity (Aggregate): Manufacturing",
    "ECO_AGGREGATE_CON": "Economic activity (Aggregate): Construction",
    "ECO_AGGREGATE_MEL": "Economic activity (Aggregate): Mining and quarrying; Electricity, gas and water supply",
    "ECO_AGGREGATE_MKT": (
        "Economic activity (Aggregate): Trade, Transportation, Accommodation and Food, "
        "and Business and Administrative Services"
    ),
    "ECO_AGGREGATE_PUB": (
        "Economic activity (Aggregate): Public Administration, Community, Social and other Services and Activities"
    ),
    "ECO_AGGREGATE_X": "Economic activity (Aggregate): Not classified",
    "ECO_ISIC4_TOTAL": "Economic activity (ISIC-Rev.4): Total",
}
ISIC4_SECTIONS: dict[str, str] = {
    "A": "Agriculture; forestry and fishing", "B": "Mining and quarrying", "C": "Manufacturing",
    "D": "Electricity; gas, steam and air conditioning supply",
    "E": "Water supply; sewerage, waste management and remediation activities", "F": "Construction",
    "G": "Wholesale and retail trade; repair of motor vehicles and motorcycles", "H": "Transportation and storage",
    "I": "Accommodation and food service activities", "J": "Information and communication",
    "K": "Financial and insurance activities", "L": "Real estate activities",
    "M": "Professional, scientific and technical activities", "N": "Administrative and support service activities",
    "O": "Public administration and defence; compulsory social security", "P": "Education",
    "Q": "Human health and social work activities", "R": "Arts, entertainment and recreation",
    "S": "Other service activities",
    "T": "Activities of households as employers; undifferentiated goods- and services-producing activities of households for own use",
    "U": "Activities of extraterritorial organizations and bodies", "X": "Not elsewhere classified",
}


@dataclass(frozen=True)
class IndicatorSpec:
    """A named concept with its ILO modelled and national (survey) indicator codes."""

    label: str
    modelled: str | None
    survey: str | None = None
    classif1_prefix: str = ""
    notes: str = ""


#: Indicators the CGJR charts use. Codes verified against the ILOSTAT table of
#: contents (open-numbers mirror, Feb 2026, and community scripts; see docs).
CATALOG: dict[str, IndicatorSpec] = {
    "employment_by_sector": IndicatorSpec(
        "Employment by sex and economic activity (thousands)", "EMP_2EMP_SEX_ECO_NB", "EMP_TEMP_SEX_ECO_NB", "ECO_",
        "classif1 levels: ECO_SECTOR_* (3 broad sectors), ECO_AGGREGATE_* (ISIC aggregates), ECO_ISIC4_* (sections)",
    ),
    "employment": IndicatorSpec("Employment by sex and age (thousands)", "EMP_2EMP_SEX_AGE_NB", "EMP_TEMP_SEX_AGE_NB", "AGE_"),
    "labour_force_participation_rate": IndicatorSpec(
        "Labour force participation rate by sex and age (%)", "EAP_2WAP_SEX_AGE_RT", "EAP_DWAP_SEX_AGE_RT", "AGE_"
    ),
    "unemployment_rate": IndicatorSpec("Unemployment rate by sex and age (%)", "UNE_2EAP_SEX_AGE_RT", "UNE_DEAP_SEX_AGE_RT", "AGE_"),
    "employment_to_population_ratio": IndicatorSpec(
        "Employment-to-population ratio by sex and age (%)", "EMP_2WAP_SEX_AGE_RT", "EMP_DWAP_SEX_AGE_RT", "AGE_"
    ),
    "output_per_worker": IndicatorSpec("Output per worker (GDP constant 2015 US $)", "GDP_205U_NOC_NB"),
    "output_per_worker_ppp": IndicatorSpec(
        "Output per worker (GDP constant international $ at PPP)", "GDP_211P_NOC_NB", None, "",
        "the code still says 211P but the PPP base follows the edition (2021 PPP in the Nov. 2025 edition)",
    ),
    "output_per_hour_ppp": IndicatorSpec("Output per hour worked (GDP constant international $ at PPP)", "GDP_2HRW_NOC_NB"),
}
ALIASES: dict[str, str] = {
    "lfpr": "labour_force_participation_rate",
    "epr": "employment_to_population_ratio",
    "unemployment": "unemployment_rate",
    "productivity": "output_per_worker",
    "sectoral_employment": "employment_by_sector",
}

_CODE_RE = re.compile(r"^[A-Z0-9]+(?:_[A-Z0-9]+)+$")
_ID_RE = re.compile(r"^(?P<code>[A-Z0-9]+(?:_[A-Z0-9]+)+)_(?P<freq>[AQM])$")
_AREA_RE = re.compile(r"^[A-Z0-9]{3}$")
_CLASSIF_CODE_RE = re.compile(r"^[A-Za-z0-9_\-.]+\*?$")
_EDITION_RE = re.compile(
    r"ILO\s+modell?ed\s+estimates,?\s*(?P<month>[A-Za-z]+)\.?\s*(?P<year>(?:19|20)\d{2})", re.IGNORECASE
)
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
_MONTH_ABBR = {v: k.capitalize() for k, v in _MONTHS.items()}


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------
class ILOSTATError(RuntimeError):
    """The service answered with an error, or with a payload this connector cannot read."""


# ---------------------------------------------------------------------------
# small pure helpers
# ---------------------------------------------------------------------------
def _as_list(values: Sequence[str] | str | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = re.split(r"[;,+\s]+", values)
    out: list[str] = []
    for v in values:
        v = str(v).strip()
        if v and v not in out:
            out.append(v)
    return out


def resolve_indicator(indicator: str, freq: str = "A", modelled: bool = True) -> tuple[str, str]:
    """Return ``(code, freq)`` for an ILOSTAT id, an indicator code or a catalog name.

    ``"EMP_2EMP_SEX_ECO_NB_A"`` -> ``("EMP_2EMP_SEX_ECO_NB", "A")``;
    ``"unemployment_rate"`` -> the ILO modelled code (or the national survey
    code with ``modelled=False``).
    """
    name = str(indicator or "").strip()
    key = ALIASES.get(name.lower(), name.lower())
    if key in CATALOG:
        spec = CATALOG[key]
        code = spec.modelled if modelled else spec.survey
        if not code:
            raise ValueError(f"{key!r} has no {'ILO modelled' if modelled else 'national survey'} series in the catalog")
        return code, _check_freq(freq)
    up = name.upper()
    m = _ID_RE.match(up)
    if m and m.group("code").count("_") >= 2:
        return m.group("code"), m.group("freq")
    if not _CODE_RE.match(up):
        raise ValueError(f"invalid ILOSTAT indicator {indicator!r}: use an id such as 'EMP_2EMP_SEX_ECO_NB_A', a code, or one of {sorted(CATALOG)}")
    return up, _check_freq(freq)


def _check_freq(freq: str) -> str:
    f = str(freq or "A").upper()
    if f not in FREQUENCIES:
        raise ValueError(f"freq must be one of {FREQUENCIES}, got {freq!r}")
    return f


def dataset_id(code: str, freq: str = "A") -> str:
    """Bulk-file id: indicator code plus frequency (``EMP_2EMP_SEX_ECO_NB_A``)."""
    return f"{code}_{_check_freq(freq)}"


def classification_dims(code: str) -> list[str]:
    """Classification segments of an indicator code: ``EMP_2EMP_SEX_ECO_NB`` -> ``["SEX", "ECO"]``;
    ``NOC`` (no classification) -> ``[]``."""
    parts = code.split("_")
    return [p for p in parts[2:-1] if p != "NOC"]


def normalize_countries(countries: Sequence[str] | str | None) -> list[str] | None:
    """``None``/``"all"`` -> None (every reference area); otherwise upper-case 3-character
    codes (ISO3, ILO aggregates such as ``X01``, ``KOS``)."""
    if countries is None or (isinstance(countries, str) and countries.strip().lower() in ("", "all")):
        return None
    codes = [c.upper() for c in _as_list(countries)]
    bad = [c for c in codes if not _AREA_RE.match(c)]
    if bad:
        raise ValueError(f"invalid ILOSTAT ref_area code(s): {bad} (use ISO3 codes or ILO aggregate codes such as X01)")
    return sorted(codes)


def _split_codes(values: Sequence[str] | str | None) -> tuple[list[str], list[str]]:
    """Split classification filters into exact codes and wildcard prefixes (``"ECO_AGGREGATE_*"``)."""
    exact: list[str] = []
    prefixes: list[str] = []
    for v in _as_list(values):
        if not _CLASSIF_CODE_RE.match(v):
            raise ValueError(f"invalid classification code {v!r}")
        if v.endswith("*"):
            prefixes.append(v[:-1].upper())
        else:
            exact.append(v.upper())
    return sorted(exact), sorted(prefixes)


def _year(v: int | str | None) -> int | None:
    if v is None or str(v).strip() == "":
        return None
    y = year_of(str(v).strip())
    if y is None:
        raise ValueError(f"start/end must be years, got {v!r}")
    return y


def normalize_period(time: Any) -> str:
    """ILOSTAT time codes to canonical periods: ``2024``; ``2024Q1``/``2024q1`` -> ``2024Q1``;
    ``2024M03``/``2024m03`` -> ``2024-03``."""
    s = str(time if time is not None else "").strip()
    m = re.fullmatch(r"(\d{4})[Qq]([1-4])", s)
    if m:
        return f"{m.group(1)}Q{m.group(2)}"
    m = re.fullmatch(r"(\d{4})[Mm](\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.fullmatch(r"(\d{4})-(\d{2})", s)
    if m:
        return s
    m = re.fullmatch(r"(\d{4})(?:\.0)?", s)
    return m.group(1) if m else s


def edition_from_label(label: str) -> str:
    """Edition of an ILO modelled series from its label, normalised to ``"Nov. 2025"``.

    ``"... -- ILO modelled estimates, Nov. 2025 (thousands)"`` -> ``"Nov. 2025"``;
    ``"... ILO modelled estimates, November 2018 (%)"`` -> ``"Nov. 2018"``. Returns
    "" when the label has no edition."""
    m = _EDITION_RE.search(label or "")
    if not m:
        return ""
    month = _MONTHS.get(m.group("month")[:3].lower())
    if not month:
        return ""
    return f"{_MONTH_ABBR[month]}. {m.group('year')}"


def normalize_edition(edition: str) -> str:
    """Accept ``"Nov 2023"``, ``"November 2023"``, ``"Nov. 2023"``, ``"2023-11"`` -> ``"Nov. 2023"``."""
    s = str(edition or "").strip()
    m = re.fullmatch(r"((?:19|20)\d{2})-(\d{1,2})", s)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{_MONTH_ABBR[int(m.group(2))]}. {m.group(1)}"
    m = re.fullmatch(r"([A-Za-z]+)\.?\s*((?:19|20)\d{2})", s)
    if m and m.group(1)[:3].lower() in _MONTHS:
        return f"{_MONTH_ABBR[_MONTHS[m.group(1)[:3].lower()]]}. {m.group(2)}"
    raise ValueError(f"cannot read edition {edition!r}; use e.g. 'Nov. 2023'")


def is_modelled_label(text: str) -> bool:
    return bool(re.search(r"modell?ed\s+estimates", text or "", re.IGNORECASE))


def classif_label(code: str) -> str:
    """Human label for a classification code (built-in table; combined ISIC sections are
    composed and suffixed with "[label composed by cgjrkit]"); falls back to the code."""
    if not code:
        return ""
    if code in CLASSIF_LABELS:
        return CLASSIF_LABELS[code]
    m = re.fullmatch(r"ECO_ISIC4_([A-UX]+)", code)
    if m:
        letters = m.group(1)
        if len(letters) == 1 and letters in ISIC4_SECTIONS:
            return f"Economic activity (ISIC-Rev.4): {letters}. {ISIC4_SECTIONS[letters]}"
        if all(ch in ISIC4_SECTIONS for ch in letters):
            parts = "; ".join(f"{ch}. {ISIC4_SECTIONS[ch]}" for ch in letters)
            return f"Economic activity (ISIC-Rev.4): {'+'.join(letters)} ({parts}) [label composed by cgjrkit]"
    return code


_PPP_RE = re.compile(r"constant\s+((?:19|20)\d{2})\s+international\s+\$\s*(?:at\s+)?PPP", re.IGNORECASE)
_USD_RE = re.compile(r"constant\s+((?:19|20)\d{2})\s+US\s*\$", re.IGNORECASE)
_PPP_NOYEAR_RE = re.compile(r"international\s+\$\s*(?:at\s+)?PPP", re.IGNORECASE)

#: Units for price-based indicators when no label is available (``type="code"``
#: payload and no table-of-contents entry). The codes are reused across base
#: years (GDP_211P carried 2011, 2017 and now 2021 PPP), so the base year is
#: "unknown": ``schema.ppp_base()`` then returns ``"unknown"`` and PPP-mixing
#: checks still see a PPP series, and :func:`build_caveats` raises
#: ``ilostat_unit_base_unknown``.
CODE_UNITS: dict[str, str] = {
    "GDP_211P": "constant international $ per worker (PPP unknown)",
    "GDP_2HRW": "constant international $ per hour worked (PPP unknown)",
    "GDP_205U": "constant US$ per worker (base year unknown)",
}


def unit_from_label(label: str, code: str = "") -> str:
    """Unit label from an ILOSTAT indicator label (trailing parenthetical), with PPP units
    carrying their base year as CONVENTIONS require.

    ``"... Nov. 2025 (thousands)"`` -> ``"thousands"``; ``"... (%)"`` -> ``"%"``;
    ``"Output per worker (GDP constant 2015 US $) -- ..."`` -> ``"constant 2015 US$ per worker"``;
    ``"Output per worker (GDP constant 2021 international $ at PPP) -- ..."`` ->
    ``"constant 2021 international $ per worker (PPP 2021)"``. Without a label the
    code is used: ``_RT`` -> ``"%"``, and the GDP-based codes in :data:`CODE_UNITS`
    get a unit whose base year is ``unknown`` (``"... (PPP unknown)"``) rather than ``""``."""
    text = (label or "").strip()
    per = ""
    if re.search(r"per\s+hour\s+worked", text, re.IGNORECASE):
        per = " per hour worked"
    elif re.search(r"per\s+worker", text, re.IGNORECASE):
        per = " per worker"
    m = _PPP_RE.search(text)
    if m:
        unit = f"constant {m.group(1)} international ${per} (PPP {m.group(1)})"
        if re.search(r"millions", text, re.IGNORECASE):
            unit = f"millions of {unit}"
        return unit
    m = _USD_RE.search(text)
    if m:
        return f"constant {m.group(1)} US${per}"
    if _PPP_NOYEAR_RE.search(text):
        unit = f"constant international ${per} (PPP unknown)"
        return f"millions of {unit}" if re.search(r"millions", text, re.IGNORECASE) else unit
    tail = re.search(r"\(([^()]*)\)\s*$", text)
    if tail and not re.search(r"\bGDP\b", tail.group(1)):
        return tail.group(1).strip()
    for prefix, unit in CODE_UNITS.items():
        if code.upper().startswith(prefix):
            return unit
    if code.endswith("_RT"):
        return "%"
    return ""


def is_aggregate(code: str) -> bool:
    """ILO regional/income aggregates use codes starting with ``X`` (``X01`` = World)."""
    return bool(code) and code.startswith("X") and code != "XKX"


def request_url(url: str, params: dict[str, Any]) -> str:
    """Compose ``url?query`` keeping ``+`` literal between multiple values, as the
    published ILOSTAT examples do (httpx would encode it as ``%2B``)."""
    if not params:
        return url
    query = urlencode([(k, str(v)) for k, v in params.items()], safe="+", quote_via=quote)
    return f"{url}?{query}"


# ---------------------------------------------------------------------------
# request builders (pure)
# ---------------------------------------------------------------------------
def build_request(
    indicator: str,
    countries: Sequence[str] | str | None = None,
    start: int | str | None = None,
    end: int | str | None = None,
    *,
    freq: str = "A",
    sex: Sequence[str] | str | None = None,
    classif1: Sequence[str] | str | None = None,
    classif2: Sequence[str] | str | None = None,
    type: str = "code",
    lang: str = "en",
) -> tuple[str, dict[str, str]]:
    """Bulk data request ``(url, params)`` for one indicator file.

    Exact classification codes are sent as server-side filters; wildcard
    prefixes (``"ECO_AGGREGATE_*"``) are applied client side by :func:`parse`.
    Parameters are emitted in a fixed order so cache keys are stable.
    """
    code, f = resolve_indicator(indicator, freq)
    if type not in ("code", "both", "label"):
        raise ValueError("type must be 'code', 'both' or 'label'")
    if lang not in ("en", "fr", "es"):
        raise ValueError("lang must be 'en', 'fr' or 'es'")
    params: dict[str, str] = {"id": dataset_id(code, f)}
    areas = normalize_countries(countries)
    if areas:
        params["ref_area"] = "+".join(areas)
    for name, values in (("sex", sex), ("classif1", classif1), ("classif2", classif2)):
        exact, _prefixes = _split_codes(values)
        if exact:
            params[name] = "+".join(exact)
    y0, y1 = _year(start), _year(end)
    if y0 is not None and y1 is not None and y0 > y1:
        raise ValueError(f"start ({y0}) is after end ({y1})")
    if y0 is not None:
        params["timefrom"] = str(y0)
    if y1 is not None:
        params["timeto"] = str(y1)
    params["type"] = type
    params["lang"] = lang
    params["format"] = ".csv"
    return DATA_URL, params


def build_toc_request(lang: str = "en") -> tuple[str, dict[str, str]]:
    return TOC_URL, {"lang": lang, "format": ".csv"}


def build_dictionary_request(var: str, lang: str = "en") -> tuple[str, dict[str, str]]:
    if not re.fullmatch(r"[a-z0-9_]+", str(var).lower()):
        raise ValueError(f"invalid dictionary name {var!r}")
    return DIC_URL, {"var": str(var).lower(), "lang": lang, "format": ".csv"}


def build_sdmx_request(
    indicator: str,
    countries: Sequence[str] | str | None = None,
    start: int | str | None = None,
    end: int | str | None = None,
    *,
    freq: str = "A",
    sex: Sequence[str] | str | None = None,
    classif1: Sequence[str] | str | None = None,
    classif2: Sequence[str] | str | None = None,
) -> tuple[str, dict[str, str]]:
    """SDMX REST data request. Key = REF_AREA.FREQ.MEASURE.<one dimension per
    classification segment of the code> (UNVERIFIED layout, see module docstring)."""
    code, f = resolve_indicator(indicator, freq)
    areas = normalize_countries(countries) or []
    dims = classification_dims(code)
    values: list[list[str]] = []
    filters = {"sex": sex, "classif1": classif1, "classif2": classif2}
    order = ["sex"] if dims[:1] == ["SEX"] else []
    order += ["classif1", "classif2"][: len(dims) - len(order)]
    for name in order:
        exact, _ = _split_codes(filters[name])
        values.append(exact)
    key = ".".join(["+".join(areas), f, ""] + ["+".join(v) for v in values])
    params: dict[str, str] = {}
    y0, y1 = _year(start), _year(end)
    if y0 is not None:
        params["startPeriod"] = str(y0)
    if y1 is not None:
        params["endPeriod"] = str(y1)
    params["format"] = "csv"
    return f"{SDMX_BASE}/data/ILO,DF_{code},1.0/{key}", params


# ---------------------------------------------------------------------------
# payload decoding (pure)
# ---------------------------------------------------------------------------
def read_table(payload: bytes | str, url: str = "") -> pd.DataFrame:
    """Decode a CSV payload (gzip or plain, optional BOM) to an all-string frame.

    Raises :class:`ILOSTATError` for HTML/XML/JSON error bodies."""
    data = payload.encode() if isinstance(payload, str) else bytes(payload)
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except (OSError, EOFError) as exc:
            raise ILOSTATError(f"truncated gzip payload from {url or 'ILOSTAT'}: {exc}") from exc
    text = data.decode("utf-8-sig", errors="replace")
    stripped = text.strip()
    if not stripped:
        return pd.DataFrame()
    if stripped[0] in "{[":
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            obj = None
        msg = ""
        if isinstance(obj, dict):
            msg = str(obj.get("error") or obj.get("message") or obj)
        elif isinstance(obj, list) and obj:
            msg = str(obj[0])
        raise ILOSTATError(f"ILOSTAT returned JSON instead of CSV from {url or 'ILOSTAT'}: {msg[:300]}")
    if stripped.startswith("<"):
        raise ILOSTATError(f"ILOSTAT returned HTML/XML instead of CSV from {url or 'ILOSTAT'}: {stripped[:200]!r}")
    try:
        return pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    except pd.errors.ParserError as exc:
        raise ILOSTATError(f"unreadable CSV from {url or 'ILOSTAT'}: {exc}") from exc


@dataclass(frozen=True)
class TocEntry:
    """One row of the ILOSTAT table of contents (by indicator)."""

    id: str
    indicator: str
    label: str
    freq: str
    last_update: str = ""   # ISO date (YYYY-MM-DD) of the file's last update
    database: str = ""
    modelled: bool = False

    @property
    def edition(self) -> str:
        return edition_from_label(self.label) if self.modelled else ""


def _norm_col(name: str) -> str:
    return str(name).strip().lower().replace("_", ".")


def _iso_date(text: str) -> str:
    """``"02/12/2025 13:02:16"`` (day/month/year) -> ``"2025-12-02"``; ISO input passes through."""
    s = str(text or "").strip()
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return m.group(0)
    m = re.match(r"(\d{4})(\d{2})(\d{2})T", s)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""


def parse_toc(payload: bytes | str, url: str = "") -> pd.DataFrame:
    """Table of contents -> frame with ``id, indicator, label, freq, last_update,
    database, modelled, edition`` plus the remaining provider columns (names
    normalised to dotted lower case: ``data.start``, ``n.records``, ...)."""
    raw = read_table(payload, url)
    if raw.empty:
        return pd.DataFrame(columns=["id", "indicator", "label", "freq", "last_update", "database", "modelled", "edition"])
    df = raw.rename(columns={c: _norm_col(c) for c in raw.columns})
    if "id" not in df.columns:
        raise ILOSTATError(f"table of contents without an 'id' column from {url or 'ILOSTAT'}: {list(raw.columns)[:8]}")
    out = pd.DataFrame({"id": df["id"].str.strip()})
    out["indicator"] = df.get("indicator", out["id"].str.replace(r"_[AQM]$", "", regex=True))
    out["label"] = df.get("indicator.label", "")
    out["freq"] = df.get("freq", out["id"].str[-1:])
    out["last_update"] = df.get("last.update", pd.Series("", index=df.index)).map(_iso_date)
    db = df.get("database", pd.Series("", index=df.index)).fillna("")
    coll = df.get("collection", pd.Series("", index=df.index)).fillna("")
    out["database"] = db
    out["modelled"] = (db.str.upper() == "ILOEST") | (coll.str.upper() == "ILOEST") | out["label"].map(is_modelled_label)
    out["edition"] = [edition_from_label(lbl) if mod else "" for lbl, mod in zip(out["label"], out["modelled"])]
    extra = [c for c in df.columns if c not in ("id", "indicator", "indicator.label", "freq", "last.update", "database")]
    for c in extra:
        out[c] = df[c]
    return out.drop_duplicates("id").sort_values("id").reset_index(drop=True)


def toc_entry(toc: pd.DataFrame, id_: str) -> TocEntry | None:
    rows = toc[toc["id"] == id_] if len(toc) else toc
    if rows is None or not len(rows):
        return None
    r = rows.iloc[0]
    return TocEntry(id=r["id"], indicator=r["indicator"], label=r["label"], freq=r["freq"],
                    last_update=r["last_update"], database=r["database"], modelled=bool(r["modelled"]))


def parse_dictionary(payload: bytes | str, url: str = "") -> pd.DataFrame:
    """Code list -> frame ``code, label`` (first two columns of the provider file;
    the rest is kept)."""
    raw = read_table(payload, url)
    if raw.empty or raw.shape[1] < 2:
        return pd.DataFrame(columns=["code", "label"])
    cols = list(raw.columns)
    out = raw.rename(columns={cols[0]: "code", cols[1]: "label"})
    return out.reset_index(drop=True)


# ---------------------------------------------------------------------------
# observations -> canonical frame (pure)
# ---------------------------------------------------------------------------
@dataclass
class ParsedILO:
    """Canonical frame plus what the caveat builder and provenance need."""

    frame: pd.DataFrame
    indicator: str = ""
    indicator_label: str = ""
    edition: str = ""
    modelled_rows: int = 0
    survey_rows: int = 0
    obs_status_raw: Counter = field(default_factory=Counter)
    unknown_obs_status: Counter = field(default_factory=Counter)
    unreliable: int = 0
    sources: dict[str, str] = field(default_factory=dict)
    series: dict[str, dict] = field(default_factory=dict)
    disambiguated: list[str] = field(default_factory=list)
    best_source_dropped: int = 0
    duplicates_dropped: int = 0
    missing_values: int = 0
    real_value_periods: dict[str, str] = field(default_factory=dict)
    #: latest ILOEST period flagged ``R`` over all countries (kept for reference; caveats use the per-country values)
    last_real_period: str = ""
    #: latest ILOEST period flagged ``R`` per country (max over the country's series)
    last_real_by_area: dict[str, str] = field(default_factory=dict)
    #: ILOEST periods after the last ``R`` period of their own series and country, by country
    projected_periods: dict[str, list[str]] = field(default_factory=dict)
    #: ILOEST periods of series with no ``R`` period at all in the result, by country (only
    #: filled when the payload carries ``R`` flags, so a missing obs_status column is not read as "no data")
    no_real_periods: dict[str, list[str]] = field(default_factory=dict)
    notes: dict[str, list[str]] = field(default_factory=dict)


def _period_key(p: str) -> tuple[int, str]:
    return (year_of(p) or 0, p)


def _modelled_projections(t: pd.DataFrame) -> tuple[dict[str, str], dict[str, list[str]], dict[str, list[str]]]:
    """Per-country view of the ILOEST ``R`` ("Real value") flags.

    The last ``R`` period of each series and country is its last year based on
    national data; later ILOEST values are nowcasts/projections (OWID etl
    ``ilostat`` reads the flag the same way). Returns ``(last real period by
    country, projected periods by country, periods of series without any R
    flag by country)``. The last mapping is filled only when some modelled row
    carries an ``R`` flag, so a payload without obs_status is not read as
    "no national data", and leaves out ILO aggregates (``X..``), which carry no
    ``R`` flags."""
    mod = t[t["modelled"]]
    if mod.empty:
        return {}, {}, {}
    real = mod[mod["obs_status"] == "R"]
    last_by_series: dict[tuple[str, str], tuple[int, str]] = {}
    for key, grp in real.groupby(["series", "ref_area"], sort=True):
        last_by_series[key] = max((_period_key(p) for p in grp["period"].unique()))
    last_by_area: dict[str, str] = {}
    for (_, area), k in last_by_series.items():
        if area not in last_by_area or k > _period_key(last_by_area[area]):
            last_by_area[area] = k[1]
    projected: dict[str, set[str]] = {}
    no_real: dict[str, set[str]] = {}
    flags_present = bool(len(real))
    for series, area, period in zip(mod["series"], mod["ref_area"], mod["period"]):
        last = last_by_series.get((series, area))
        if last is None:
            if flags_present and not is_aggregate(area):  # regional aggregates carry no R flags
                no_real.setdefault(area, set()).add(period)
        elif _period_key(period) > last:
            projected.setdefault(area, set()).add(period)
    order = lambda d: {a: sorted(ps, key=_period_key) for a, ps in sorted(d.items())}  # noqa: E731
    return dict(sorted(last_by_area.items())), order(projected), order(no_real)


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    for c in df.columns:
        if c.strip().lower() == name:
            return df[c].fillna("").astype(str).str.strip()
    return pd.Series("", index=df.index, dtype=object)


def _ranges(periods: Iterable[str]) -> str:
    """Compact year list: ``["2005","2006","2007","2010"]`` -> ``"2005-2007,2010"``."""
    years = sorted({y for y in (year_of(p) for p in periods) if y is not None})
    if not years:
        return ""
    out: list[str] = []
    lo = prev = years[0]
    for y in years[1:] + [None]:  # type: ignore[list-item]
        if y is not None and y == prev + 1:
            prev = y
            continue
        out.append(str(lo) if lo == prev else f"{lo}-{prev}")
        if y is not None:
            lo = prev = y
    return ",".join(out)


def _map_unique(series: pd.Series, func: Any, dtype: Any = object) -> pd.Series:
    """Apply ``func`` once per distinct value (fast on large bulk files)."""
    codes, uniques = pd.factorize(series)
    values = np.array([func(u) for u in uniques], dtype=dtype)
    if not len(values):
        return pd.Series([], index=series.index, dtype=dtype)
    return pd.Series(values[codes], index=series.index)


def _apply_code_filter(df: pd.DataFrame, col: str, values: Sequence[str] | str | None) -> pd.DataFrame:
    exact, prefixes = _split_codes(values)
    if not exact and not prefixes:
        return df
    s = df[col].str.upper()
    keep = s.isin(exact)
    for p in prefixes:
        keep |= s.str.startswith(p)
    return df[keep]


def _source_matches(source: pd.Series, wanted: list[str]) -> pd.Series:
    keep = pd.Series(False, index=source.index)
    up = source.str.upper()
    for w in wanted:
        w = w.upper()
        if ":" in w.rstrip(":"):
            keep |= up == w  # exact source code, e.g. "BA:259"
        else:
            keep |= up.str.startswith(w.rstrip(":") + ":")  # source type prefix, e.g. "BA" (LFS), "XA"
    return keep


def standardize(
    table: pd.DataFrame,
    *,
    indicator: str = "",
    countries: Sequence[str] | str | None = None,
    start: int | str | None = None,
    end: int | str | None = None,
    sex: Sequence[str] | str | None = None,
    classif1: Sequence[str] | str | None = None,
    classif2: Sequence[str] | str | None = None,
    sources: Sequence[str] | str | None = None,
    best_source: bool = True,
    toc: TocEntry | None = None,
    dataset: str | None = None,
) -> ParsedILO:
    """Turn an ILOSTAT observation table (bulk CSV columns) into the canonical frame.

    Client-side filters repeat the server-side ones (in case the service
    ignores a parameter) and apply wildcard prefixes. When several sources
    report the same series and period (national surveys and censuses do), a
    ``best_source`` column is honoured if present; remaining clashes are made
    unique by appending ``@<source>`` to the indicator of every affected
    series, listed in ``ParsedILO.disambiguated``.
    """
    if table.empty:
        return ParsedILO(frame=empty_frame(), indicator=indicator)
    t = pd.DataFrame({
        "ref_area": _col(table, "ref_area").str.upper(),
        "ref_area_label": _col(table, "ref_area.label"),
        "source": _col(table, "source"),
        "source_label": _col(table, "source.label"),
        "indicator": _col(table, "indicator").str.upper(),
        "indicator_label": _col(table, "indicator.label"),
        "sex": _col(table, "sex").str.upper(),
        "sex_label": _col(table, "sex.label"),
        "classif1": _col(table, "classif1").str.upper(),
        "classif1_label": _col(table, "classif1.label"),
        "classif2": _col(table, "classif2").str.upper(),
        "classif2_label": _col(table, "classif2.label"),
        "time": _col(table, "time"),
        "obs_value": _col(table, "obs_value"),
        "obs_status": _col(table, "obs_status").str.upper(),
        "best_source": _col(table, "best_source"),
        "note_classif": _col(table, "note_classif"),
        "note_indicator": _col(table, "note_indicator"),
        "note_source": _col(table, "note_source"),
    })
    if not (t["ref_area"] != "").any() or not (t["time"] != "").any():
        raise ILOSTATError(f"ILOSTAT table lacks ref_area/time codes (columns: {list(table.columns)[:12]}); request type='code' or 'both'")
    if indicator:
        t.loc[t["indicator"] == "", "indicator"] = indicator.upper()
    before = len(t)
    t = t.drop_duplicates()
    duplicates_dropped = before - len(t)

    areas = normalize_countries(countries)
    if areas:
        t = t[t["ref_area"].isin(areas)]
    t = _apply_code_filter(t, "sex", sex)
    t = _apply_code_filter(t, "classif1", classif1)
    t = _apply_code_filter(t, "classif2", classif2)
    t = t.assign(period=_map_unique(t["time"], normalize_period).astype(str))
    y0, y1 = _year(start), _year(end)
    if y0 is not None or y1 is not None:
        yrs = pd.to_numeric(_map_unique(t["period"], year_of), errors="coerce")
        keep = yrs.notna()
        if y0 is not None:
            keep &= yrs >= y0
        if y1 is not None:
            keep &= yrs <= y1
        t = t[keep]
    wanted_sources = _as_list(sources)
    if wanted_sources and not (len(wanted_sources) == 1 and wanted_sources[0].lower() == "all"):
        t = t[_source_matches(t["source"], wanted_sources)]
    best_dropped = 0
    if best_source and (t["best_source"] != "").any():
        mask = t["best_source"].isin(["0", "0.0", "FALSE", "false"])
        best_dropped = int(mask.sum())
        t = t[~mask]

    toc_modelled = bool(toc and toc.modelled)
    modelled = (
        t["source"].str.upper().str.startswith(MODELLED_SOURCE_PREFIX)
        | _map_unique(t["source_label"], is_modelled_label, bool)
        | _map_unique(t["indicator_label"], is_modelled_label, bool)
        | toc_modelled
    )
    t = t.assign(modelled=modelled)

    # labels
    toc_label = toc.label if toc else ""
    labelled = t.loc[t["indicator_label"] != "", "indicator_label"]
    base_label = (labelled.iloc[0] if len(labelled) else "") or toc_label
    edition = edition_from_label(base_label) if (modelled.any() or toc_modelled) else ""

    # series keys: indicator plus the classification codes present (vectorised)
    series = t["indicator"].astype(str)
    for c in ("sex", "classif1", "classif2"):
        series = series.where(t[c] == "", series + "|" + t[c].astype(str))
    t = t.assign(series=series)
    disambiguated: list[str] = []
    if len(t):
        n_src = t.groupby(["series", "ref_area", "period"])["source"].transform("nunique")
        clash = t.loc[n_src > 1, ["series", "ref_area"]].drop_duplicates()
        if len(clash):
            pairs = set(map(tuple, clash.to_numpy()))
            hit = [(s, a) in pairs for s, a in zip(t["series"], t["ref_area"])]
            t = t.assign(series=[f"{s}@{src}" if h else s for s, src, h in zip(t["series"], t["source"], hit)])
            disambiguated = sorted(f"{s}|{a}" for s, a in pairs)

    raw_flags: Counter = Counter({k: int(v) for k, v in t["obs_status"].value_counts().items()})
    survey_flags = t.loc[~t["modelled"], "obs_status"].value_counts()
    unknown: Counter = Counter({k: int(v) for k, v in survey_flags.items() if k not in OBS_STATUS_MAP})
    mapped = _map_unique(t["obs_status"], lambda f: OBS_STATUS_MAP.get(f, ""))
    status = np.where(t["modelled"].to_numpy(dtype=bool), "modelled", mapped.to_numpy(dtype=object))
    unreliable = int(((t["obs_status"] == "U") & ~t["modelled"]).sum())

    labels = t["indicator_label"].where(t["indicator_label"] != "", toc_label or "").astype(str)
    labels = labels.where(labels != "", t["indicator"].astype(str))
    for c in ("sex", "classif1", "classif2"):
        fallback = _map_unique(t[c], classif_label).astype(str)
        part = t[f"{c}_label"].where(t[f"{c}_label"] != "", fallback).astype(str)
        labels = labels.where(t[c] == "", labels + " | " + part)
    first_label = t.drop_duplicates("indicator").set_index("indicator")["indicator_label"].to_dict()
    units = _map_unique(t["indicator"], lambda code: unit_from_label(first_label.get(code, "") or toc_label, code))

    ds_modelled = dataset or "ILOEST"
    ds_survey = dataset or "ILOSTAT"
    records = pd.DataFrame({
        "source": "ilostat",
        "dataset": np.where(t["modelled"].to_numpy(dtype=bool), ds_modelled, ds_survey),
        "indicator": t["series"],
        "indicator_label": labels,
        "iso3": t["ref_area"],
        "country": t["ref_area_label"],
        "period": t["period"],
        "value": t["obs_value"],
        "unit": units,
        "obs_status": status,
    })
    frame = make_frame(records) if len(records) else empty_frame()
    missing_values = int(frame["value"].isna().sum())

    series_meta: dict[str, dict] = {}
    for key, grp in t.groupby("series", sort=True):
        r = grp.iloc[0]
        series_meta[key] = {
            "indicator": r["indicator"], "sex": r["sex"], "classif1": r["classif1"], "classif2": r["classif2"],
            "sex_label": r["sex_label"] or classif_label(r["sex"]),
            "classif1_label": r["classif1_label"] or classif_label(r["classif1"]),
            "classif2_label": r["classif2_label"] or classif_label(r["classif2"]),
            "modelled": bool(grp["modelled"].any()),
        }
    src_pairs = t[["source", "source_label"]].drop_duplicates()
    sources_meta = {s: lbl for s, lbl in sorted(zip(src_pairs["source"], src_pairs["source_label"])) if s}
    real: dict[str, str] = {}
    mod = t[t["modelled"] & (t["obs_status"] == "R")]
    for area, grp in mod.groupby("ref_area", sort=True):
        real[area] = _ranges(grp["period"].unique())
    last_real = max(mod["period"].unique(), key=_period_key) if len(mod) else ""
    last_real_by_area, projected, no_real = _modelled_projections(t)
    notes: dict[str, list[str]] = {}
    noted = t[(t["note_classif"] != "") | (t["note_indicator"] != "") | (t["note_source"] != "")]
    for (key, area), grp in noted.groupby(["series", "ref_area"], sort=True):
        vals = sorted({v for c in ("note_classif", "note_indicator", "note_source") for v in grp[c] if v})
        notes[f"{key}|{area}"] = vals

    return ParsedILO(
        frame=frame,
        indicator=(t["indicator"].iloc[0] if len(t) else indicator),
        indicator_label=base_label,
        edition=edition,
        modelled_rows=int(t["modelled"].sum()),
        survey_rows=int((~t["modelled"]).sum()),
        obs_status_raw=raw_flags,
        unknown_obs_status=unknown,
        unreliable=unreliable,
        sources=sources_meta,
        series=series_meta,
        disambiguated=disambiguated,
        best_source_dropped=best_dropped,
        duplicates_dropped=duplicates_dropped,
        missing_values=missing_values,
        real_value_periods=real,
        last_real_period=last_real,
        last_real_by_area=last_real_by_area,
        projected_periods=projected,
        no_real_periods=no_real,
        notes=notes,
    )


def parse(payload: bytes | str, *, url: str = "", **filters: Any) -> ParsedILO:
    """Parse a bulk CSV payload (see :func:`standardize` for ``filters``)."""
    return standardize(read_table(payload, url), **filters)


_SDMX_RESERVED = {"DATAFLOW", "STRUCTURE", "STRUCTURE_ID", "STRUCTURE_NAME", "ACTION", "REF_AREA", "FREQ", "MEASURE"}


def sdmx_to_table(raw: pd.DataFrame, indicator: str) -> pd.DataFrame:
    """Map an SDMX-CSV frame to the bulk-table columns (UNVERIFIED column names).

    Dimensions are the columns before ``TIME_PERIOD`` other than DATAFLOW,
    REF_AREA, FREQ and MEASURE; a ``SEX`` dimension maps to ``sex`` and the
    others, in order, to ``classif1`` and ``classif2``. ``SOURCE`` (a source
    name in SDMX output) maps to ``source``."""
    if raw.empty:
        return raw
    cols = {c: str(c).split(":")[0].strip().upper() for c in raw.columns}
    df = raw.rename(columns=cols)
    if "TIME_PERIOD" not in df.columns or "OBS_VALUE" not in df.columns or "REF_AREA" not in df.columns:
        raise ILOSTATError(f"SDMX-CSV without REF_AREA/TIME_PERIOD/OBS_VALUE columns: {list(df.columns)[:12]}")
    names = list(df.columns)
    dims = [c for c in names[: names.index("TIME_PERIOD")] if c not in _SDMX_RESERVED]
    out = pd.DataFrame({
        "ref_area": df["REF_AREA"], "indicator": indicator, "time": df["TIME_PERIOD"], "obs_value": df["OBS_VALUE"],
        "obs_status": df["OBS_STATUS"] if "OBS_STATUS" in df.columns else "",
        "source": df["SOURCE"] if "SOURCE" in df.columns else "",
    })
    targets = ["classif1", "classif2"]
    for d in dims:
        if d == "SEX":
            out["sex"] = df[d]
        elif targets:
            out[targets.pop(0)] = df[d]
    for c in ("source",):
        lbl = out[c].astype(str)
        out[f"{c}.label"] = lbl
    return out


def parse_sdmx(payload: bytes | str, *, indicator: str, url: str = "", **filters: Any) -> ParsedILO:
    """Parse an SDMX-CSV payload into the same structure as :func:`parse`."""
    table = sdmx_to_table(read_table(payload, url), indicator)
    return standardize(table, indicator=indicator, **filters)


# ---------------------------------------------------------------------------
# caveats (pure)
# ---------------------------------------------------------------------------
def vintage_for(parsed: ParsedILO, toc: TocEntry | None) -> str:
    """``"ILOEST Nov. 2025"`` for modelled series with a known edition; otherwise
    ``"ILOSTAT update YYYY-MM-DD"`` from the table of contents, or ""."""
    if parsed.modelled_rows and parsed.edition:
        return f"ILOEST {parsed.edition}"
    if toc and toc.last_update:
        return f"ILOSTAT update {toc.last_update}"
    return ""


def build_caveats(parsed: ParsedILO, *, expect_edition: str | None = None) -> list[Caveat]:
    """Caveats for a parsed ILOSTAT result (modelled data, editions, sources, flags)."""
    out: list[Caveat] = []
    df = parsed.frame
    if parsed.modelled_rows:
        out.append(Caveat(
            "ilo_modelled", "warn",
            "ILO modelled estimates (ILOEST"
            + (f", {parsed.edition}" if parsed.edition else "")
            + ") are harmonised model-based values; observations without national data are imputed and "
            "should not be used for country rankings. Every edition revises the whole history: never splice them "
            "with national survey (LFS) series or with another ILOEST edition.",
            {"indicator": parsed.indicator, "edition": parsed.edition, "rows": parsed.modelled_rows},
        ))
        if not parsed.edition:
            out.append(Caveat("ilostat_edition_unknown", "info",
                              "The ILOEST edition could not be read from the indicator label; record it from ILOSTAT before citing.",
                              {"indicator": parsed.indicator}))
        if parsed.projected_periods and len(df):
            by_country = parsed.projected_periods
            proj = sorted({p for ps in by_country.values() for p in ps}, key=_period_key)
            last = {a: parsed.last_real_by_area.get(a, "") for a in by_country}
            detail = "; ".join(f"{a} after {last[a]}" for a in sorted(by_country)[:10])
            more = f" (and {len(by_country) - 10} more)" if len(by_country) > 10 else ""
            out.append(Caveat(
                "ilostat_modelled_projection", "warn",
                "ILOEST values after each country's last year flagged 'Real value' (based on national data) are "
                f"nowcasts/projections: {detail}{more}.",
                {"periods": proj, "by_country": by_country, "last_real_by_country": last},
            ))
        if parsed.no_real_periods and len(df):
            out.append(Caveat(
                "ilostat_modelled_no_real_value", "warn",
                "Some ILOEST series of these countries have no value flagged 'Real value' in the result: their values "
                "are imputed or projected by the ILO model (no national data in the requested window).",
                {"by_country": {a: _ranges(ps) for a, ps in parsed.no_real_periods.items()}},
            ))
    if parsed.modelled_rows and parsed.survey_rows:
        out.append(Caveat("ilo_modelled_and_survey_mixed", "warn",
                          "The result mixes ILO modelled estimates and national survey data; keep them as separate series.",
                          {"modelled_rows": parsed.modelled_rows, "survey_rows": parsed.survey_rows}))
    if expect_edition and parsed.modelled_rows:
        want = normalize_edition(expect_edition)
        if parsed.edition and parsed.edition != want:
            out.append(Caveat(
                "ilostat_edition_mismatch", "warn",
                f"Requested ILOEST {want} but the service returned {parsed.edition}; ILOSTAT keeps no archive of past "
                "editions, so use a pinned snapshot (CGJRKIT_SNAPSHOT) or the WDI archive for the older edition.",
                {"expected": want, "returned": parsed.edition},
            ))
    if parsed.disambiguated:
        out.append(Caveat(
            "ilostat_multiple_sources", "warn",
            "Several sources report the same series and period; affected series carry '@<source>' in the indicator. "
            "Choose one with sources=[...] before charting.",
            {"series": parsed.disambiguated[:50], "n": len(parsed.disambiguated)},
        ))
    if parsed.unreliable:
        out.append(Caveat("ilostat_unreliable", "warn", "Some observations are flagged 'Unreliable' (obs_status U) by ILOSTAT.",
                          {"rows": parsed.unreliable}))
    if parsed.unknown_obs_status:
        out.append(Caveat("ilostat_unknown_obs_status", "info", "Unrecognised obs_status codes were mapped to ''.",
                          {"codes": dict(parsed.unknown_obs_status)}))
    if len(df) and "break" in set(df["obs_status"]):
        out.append(Caveat("ilostat_series_break", "warn", "Series breaks are flagged (obs_status B); do not compute growth across them.",
                          {"rows": int((df["obs_status"] == "break").sum())}))
    if parsed.indicator.startswith("GDP_211P") and len(df):
        out.append(Caveat("ilostat_ppp_code_reuse", "info",
                          "GDP_211P keeps its code across PPP rounds (2011, 2017, 2021); the base year is read from the label into 'unit'.",
                          {"unit": df["unit"].iloc[0]}))
    if len(df):
        unknown_base = sorted(u for u in set(df["unit"]) if ppp_base(u) == "unknown" or "base year unknown" in u)
        if unknown_base:
            out.append(Caveat(
                "ilostat_unit_base_unknown", "warn",
                "The PPP or price base year could not be read: the payload has no indicator label (type='code') and "
                "the table of contents gave none. These codes are reused across base years, so do not combine the "
                "values with other PPP or constant-price series until the base is known; re-fetch with type='both' "
                "or with the table of contents available.",
                {"indicator": parsed.indicator, "units": unknown_base},
            ))
    if parsed.missing_values:
        out.append(Caveat("ilostat_missing_values", "info", "Some observations have no value (kept as NaN).",
                          {"rows": parsed.missing_values}))
    return out


# ---------------------------------------------------------------------------
# connector
# ---------------------------------------------------------------------------
class ILOSTAT(Connector):
    """ILOSTAT bulk download service and SDMX API.

    ``fetch`` returns a canonical :class:`Result`; classification codes are kept
    in the indicator (``EMP_2EMP_SEX_ECO_NB|SEX_T|ECO_AGGREGATE_AGR``) and
    described in ``meta["series"]``.
    """

    source = "ilostat"
    license = "CC BY 4.0"
    citation = "ILOSTAT, International Labour Organization (ILO)"
    domains = ("rplumber.ilo.org", "sdmx.ilo.org")

    def __init__(self, cache: Cache | None = None):
        super().__init__(cache)
        self._toc: pd.DataFrame | None = None
        self._toc_prov: Provenance | None = None

    # -- plumbing -------------------------------------------------------------
    def _get(self, url: str, params: dict[str, str], refresh: bool, *, compose: bool = True) -> tuple[bytes, Any]:
        full = request_url(url, params) if compose else url
        try:
            return http.fetch(self.source, full, None if compose else params, cache=self.cache, refresh=refresh)
        except http.FetchError as exc:
            raise ILOSTATError(f"ILOSTAT request failed: {exc}") from exc

    def _prov(self, retrieval: Any, *, dataset: str, vintage: str, params: dict, tool: str, notes: list[str] | None = None) -> Provenance:
        prov = self.provenance(retrieval, dataset=dataset, vintage=vintage, tool=tool, notes=notes)
        prov.params = dict(params)
        return prov

    # -- reference data -------------------------------------------------------
    def fetch_toc(self, lang: str = "en", refresh: bool = False) -> pd.DataFrame:
        """Table of contents by indicator (one row per bulk file). Provenance in ``attrs``."""
        url, params = build_toc_request(lang)
        payload, retrieval = self._get(url, params, refresh)
        toc = parse_toc(payload, url)
        prov = self._prov(retrieval, dataset="ILOSTAT table of contents", vintage="", params=params, tool="ILOSTAT.fetch_toc")
        toc.attrs["provenance"] = [prov.to_dict()]
        if lang == "en":
            self._toc, self._toc_prov = toc, prov
        return toc

    def fetch_dictionary(self, var: str, lang: str = "en", refresh: bool = False) -> pd.DataFrame:
        """Code list (``classif1``, ``source``, ``obs_status``, ``ref_area``, ...) as ``code, label``."""
        url, params = build_dictionary_request(var, lang)
        payload, retrieval = self._get(url, params, refresh)
        df = parse_dictionary(payload, url)
        df.attrs["provenance"] = [self._prov(retrieval, dataset="ILOSTAT code list", vintage="", params=params,
                                             tool="ILOSTAT.fetch_dictionary").to_dict()]
        return df

    def _toc_lookup(self, id_: str, refresh: bool) -> tuple[TocEntry | None, str]:
        """Fail-soft lookup of one id in the table of contents."""
        try:
            toc = self._toc if (self._toc is not None and not refresh) else self.fetch_toc(refresh=refresh)
        except (http.NetworkBlocked, ILOSTATError) as exc:
            return None, f"table of contents unavailable ({exc.__class__.__name__}); vintage from labels only"
        entry = toc_entry(toc, id_)
        return entry, "" if entry else f"{id_} not listed in the table of contents"

    # -- data -----------------------------------------------------------------
    def fetch(
        self,
        indicator: str,
        countries: Sequence[str] | str | None = None,
        start: int | str | None = None,
        end: int | str | None = None,
        *,
        freq: str = "A",
        sex: Sequence[str] | str | None = None,
        classif1: Sequence[str] | str | None = None,
        classif2: Sequence[str] | str | None = None,
        sources: Sequence[str] | str | None = None,
        best_source: bool = True,
        modelled: bool = True,
        via: str = "bulk",
        type: str = "code",
        use_toc: bool = True,
        expect_edition: str | None = None,
        refresh: bool = False,
    ) -> Result:
        """Fetch one ILOSTAT indicator as a canonical :class:`Result`.

        Args:
            indicator: bulk id (``"UNE_2EAP_SEX_AGE_RT_A"``), indicator code
                (``"UNE_2EAP_SEX_AGE_RT"`` + ``freq``) or catalog name
                (``"unemployment_rate"``; ``modelled=False`` picks the national
                survey series).
            countries: ISO3 / ILO aggregate codes (``X01`` World); None = all.
            start, end: years (sent as ``timefrom``/``timeto`` and re-applied).
            sex, classif1, classif2: classification codes, e.g. ``sex="SEX_T"``,
                ``classif1="AGE_YTHADULT_YGE15"``; a trailing ``*`` is a prefix
                filter applied client side (``classif1="ECO_AGGREGATE_*"``).
            sources: keep only these source codes or prefixes (``"BA"`` = LFS).
            best_source: drop rows with ``best_source == 0`` when the column exists.
            via: ``"bulk"`` (rplumber.ilo.org) or ``"sdmx"`` (sdmx.ilo.org, UNVERIFIED key layout).
            type: bulk ``type`` parameter: ``"code"`` (compact) or ``"both"`` (adds labels).
            use_toc: read the table of contents for the label, edition and last update.
            expect_edition: e.g. ``"Nov. 2023"``; a different edition raises
                the ``ilostat_edition_mismatch`` caveat.
            refresh: bypass the cache (a pinned snapshot still wins).
        """
        code, f = resolve_indicator(indicator, freq, modelled=modelled)
        id_ = dataset_id(code, f)
        notes: list[str] = []
        entry: TocEntry | None = None
        if use_toc:
            entry, note = self._toc_lookup(id_, refresh)
            if note:
                notes.append(note)
        filters = dict(countries=countries, start=start, end=end, sex=sex, classif1=classif1, classif2=classif2,
                       sources=sources, best_source=best_source, toc=entry)
        if via == "bulk":
            url, params = build_request(code, countries, start, end, freq=f, sex=sex, classif1=classif1, classif2=classif2, type=type)
            payload, retrieval = self._get(url, params, refresh)
            parsed = parse(payload, url=url, indicator=code, **filters)
            dataset_label = "ILOSTAT bulk download"
        elif via == "sdmx":
            url, params = build_sdmx_request(code, countries, start, end, freq=f, sex=sex, classif1=classif1, classif2=classif2)
            payload, retrieval = self._get(url, params, refresh, compose=False)
            parsed = parse_sdmx(payload, indicator=code, url=url, **filters)
            dataset_label = "ILOSTAT SDMX"
            notes.append("SDMX key layout and SDMX-CSV columns are UNVERIFIED (see docs/apis/ilostat.md)")
        else:
            raise ValueError("via must be 'bulk' or 'sdmx'")

        df = parsed.frame
        df = self.finalize(df) if len(df) else df
        validate_frame(df)
        parsed.frame = df
        vintage = vintage_for(parsed, entry)
        if entry and entry.last_update:
            notes.append(f"table of contents last update {entry.last_update}")
        datasets = sorted(set(df["dataset"])) if len(df) else ["ILOEST" if parsed.modelled_rows else "ILOSTAT"]
        provs = [self._prov(retrieval, dataset=" + ".join(datasets), vintage=vintage, params=params, tool="ILOSTAT.fetch",
                            notes=[f"{dataset_label} {id_}"] + notes)]
        if self._toc_prov is not None and entry is not None:
            provs.append(self._toc_prov)
        caveats = build_caveats(parsed, expect_edition=expect_edition)
        meta = {
            "id": id_,
            "indicator": code,
            "indicator_label": parsed.indicator_label or (entry.label if entry else ""),
            "edition": parsed.edition,
            "last_update": entry.last_update if entry else "",
            "via": via,
            "modelled_rows": parsed.modelled_rows,
            "survey_rows": parsed.survey_rows,
            "aggregates": sorted({a for a in df["iso3"] if is_aggregate(a)}) if len(df) else [],
            "series": parsed.series,
            "sources": parsed.sources,
            "obs_status_raw": dict(parsed.obs_status_raw),
            "real_value_periods": parsed.real_value_periods,
            "last_real_period": parsed.last_real_period,
            "last_real_by_country": parsed.last_real_by_area,
            "disambiguated": parsed.disambiguated,
            "best_source_dropped": parsed.best_source_dropped,
            "duplicates_dropped": parsed.duplicates_dropped,
            "notes": parsed.notes,
            "request": request_url(url, params) if via == "bulk" else url,
        }
        return Result(data=df, provenance=provs, caveats=caveats, meta=meta)

    # -- convenience wrappers for the CGJR charts ------------------------------
    def employment_by_sector(self, countries: Sequence[str] | str | None = None, start: int | str | None = None,
                             end: int | str | None = None, *, level: str = "aggregate", sex: str = "SEX_T",
                             modelled: bool = True, **kw: Any) -> Result:
        """Employment by economic activity (thousands). ``level``: ``"sector"`` (agriculture,
        industry, services), ``"aggregate"`` (ISIC aggregates: AGR, MAN, CON, MEL, MKT, PUB)
        or ``"isic4"`` (ISIC Rev.4 sections, as grouped by ILOEST)."""
        prefix = {"sector": "ECO_SECTOR_*", "aggregate": "ECO_AGGREGATE_*", "isic4": "ECO_ISIC4_*"}.get(level)
        if prefix is None:
            raise ValueError("level must be 'sector', 'aggregate' or 'isic4'")
        return self.fetch("employment_by_sector", countries, start, end, sex=sex, classif1=prefix, modelled=modelled, **kw)

    def _by_age(self, name: str, countries, start, end, sex: str, age: str, modelled: bool, **kw: Any) -> Result:
        return self.fetch(name, countries, start, end, sex=sex, classif1=age, modelled=modelled, **kw)

    def labour_force_participation_rate(self, countries=None, start=None, end=None, *, sex: str = "SEX_T",
                                        age: str = "AGE_YTHADULT_YGE15", modelled: bool = True, **kw: Any) -> Result:
        """Labour force participation rate (%), ILOEST by default."""
        return self._by_age("labour_force_participation_rate", countries, start, end, sex, age, modelled, **kw)

    def unemployment_rate(self, countries=None, start=None, end=None, *, sex: str = "SEX_T",
                          age: str = "AGE_YTHADULT_YGE15", modelled: bool = True, **kw: Any) -> Result:
        """Unemployment rate (%), ILOEST by default."""
        return self._by_age("unemployment_rate", countries, start, end, sex, age, modelled, **kw)

    def employment_to_population_ratio(self, countries=None, start=None, end=None, *, sex: str = "SEX_T",
                                       age: str = "AGE_YTHADULT_YGE15", modelled: bool = True, **kw: Any) -> Result:
        """Employment-to-population ratio (%), ILOEST by default."""
        return self._by_age("employment_to_population_ratio", countries, start, end, sex, age, modelled, **kw)

    def output_per_worker(self, countries=None, start=None, end=None, *, basis: str = "2015usd", **kw: Any) -> Result:
        """Output per worker (ILOEST): ``basis="2015usd"`` (GDP constant 2015 US$) or
        ``"ppp"`` (constant international $ at PPP; base year in ``unit``)."""
        name = {"2015usd": "output_per_worker", "usd": "output_per_worker", "ppp": "output_per_worker_ppp"}.get(basis)
        if name is None:
            raise ValueError("basis must be '2015usd' or 'ppp'")
        return self.fetch(name, countries, start, end, **kw)


__all__ = [
    "CATALOG",
    "ILOSTAT",
    "ILOSTATError",
    "IndicatorSpec",
    "ParsedILO",
    "TocEntry",
    "build_caveats",
    "build_dictionary_request",
    "build_request",
    "build_sdmx_request",
    "build_toc_request",
    "classif_label",
    "classification_dims",
    "edition_from_label",
    "normalize_edition",
    "normalize_period",
    "parse",
    "parse_dictionary",
    "parse_sdmx",
    "parse_toc",
    "read_table",
    "request_url",
    "resolve_indicator",
    "standardize",
    "unit_from_label",
    "vintage_for",
]
