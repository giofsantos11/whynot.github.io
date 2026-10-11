"""IMF connector: WEO and other dataflows from the IMF SDMX 3.0 API, with the
IMF DataMapper API as a fallback for the latest WEO.

Endpoints used (all ``GET``, keyless for public dataflows)::

    https://api.imf.org/external/sdmx/3.0/data/dataflow/{agency}/{flow}/{version}/{key}
    https://api.imf.org/external/sdmx/3.0/structure/dataflow/all/*/+          (catalogue)
    https://api.imf.org/external/sdmx/3.0/structure/dataflow/IMF.RES/*/*?detail=allstubs
    https://api.imf.org/external/sdmx/3.0/structure/codelist/{agency}/{id}/+
    https://www.imf.org/external/datamapper/api/v1/{indicator}/{c1}/{c2}...  (fallback)
    https://www.imf.org/external/datamapper/api/v1/indicators
    https://www.imf.org/external/datamapper/api/v1/regions | /groups          (aggregate codes)

Key facts (sources and what is UNVERIFIED are in ``docs/apis/imf.md``):

* WEO is the ``IMF.RES`` dataflow ``WEO`` (DSD_WEO, key
  ``COUNTRY.INDICATOR.FREQUENCY``, ISO3 country codes, ``G``-prefixed
  aggregates such as ``G001``). The API serves April 2025 onward. A named
  release is served either by an archival dataflow ``WEO_YYYY_MON_VINTAGE``
  (e.g. ``WEO_2025_OCT_VINTAGE``) or by an *older version of the bare* ``WEO``
  flow, which stays live (April 2025 is ``WEO`` 6.0.0). Releases are resolved
  as imf-reader does: list every IMF.RES flow version (``detail=allstubs``),
  probe each WEO (flow, version) for PUBLICATION_DATE, prefer bare flows, then
  request that explicit version (:meth:`IMF.resolve_weo_vintage`).
* The WEO "Estimates Start After" field is the series attribute
  ``LATEST_ACTUAL_ANNUAL_DATA`` (``"2024"`` or ``"FY2023/24"``). It is not in the
  default attribute set of every WEO version, so data requests ask for
  ``attributes=series`` explicitly.
* The release a flow carries is the dataset attribute ``PUBLICATION_DATE``
  (``attributes=dataset``). The ``lastUpdatedAt`` annotation is not reliable.
* ``OBS_VALUE`` is already in base units; ``SCALE`` is informational only.
* The balance of payments (successor of the IFS BOP tables) is ``IMF.STA``
  ``BOP`` with key ``COUNTRY.BOP_ACCOUNTING_ENTRY.INDICATOR.UNIT.FREQUENCY``.
* Responses are SDMX-CSV (``Accept: text/csv``, the default here) or SDMX-JSON
  2.0 (``Accept: application/json``). Both parsers return the same raw table.

IMF terms: attribution is required, and systematic downloading or commercial
reuse needs written permission (copyright@imf.org). Every result carries an
``IMF_TERMS`` caveat and a provenance note; keep extracts small and cached.

Design: ``build_*`` request builders and ``read_*``/``parse_*`` functions are
pure; :class:`IMF` wires them through :func:`cgjrkit.core.http.fetch`.
"""

from __future__ import annotations

import io
import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

import pandas as pd

from cgjrkit.connectors.base import Connector
from cgjrkit.core import http
from cgjrkit.core.cache import Cache, Retrieval
from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import COLUMNS, empty_frame, make_frame, validate_frame, year_of

SDMX_BASE_URL = "https://api.imf.org/external/sdmx/3.0"
DATAMAPPER_BASE_URL = "https://www.imf.org/external/datamapper/api/v1"

WEO_AGENCY = "IMF.RES"
WEO_FLOW = "WEO"
#: single-series key used to read the dataset-level PUBLICATION_DATE of a WEO
#: flow (the probe imf-reader uses; every WEO release carries this series)
WEO_PROBE_KEY = "USA.NGDP_RPCH.A"

CSV_ACCEPT = "text/csv"
JSON_ACCEPT = "application/json"
#: query parameters imfp 2.x sends with SDMX-JSON data requests (verified
#: against its recorded live responses). JSON requests always carry
#: ``dimensionAtObservation``; CSV requests never do. This keeps the cache keys
#: of the two formats apart, because ``http.fetch`` does not key on headers.
JSON_DEFAULT_PARAMS = {"dimensionAtObservation": "TIME_PERIOD", "attributes": "dsd", "measures": "all"}

IMF_TERMS_URL = "https://www.imf.org/en/About/copyright-and-terms"
IMF_TERMS_NOTE = (
    "IMF data are subject to the IMF Copyright and Usage terms (" + IMF_TERMS_URL + "): attribution is "
    "required; systematic or bulk downloading and commercial reuse need prior written permission "
    "(copyright@imf.org). Keep extracts small and cached."
)

#: Agencies of common dataflows, from the live catalogue (structure/dataflow/all/*/+,
#: recorded 2025-11-10 in imfp's test responses). Unknown flows are looked up in
#: the catalogue at run time.
KNOWN_AGENCIES: dict[str, str] = {
    "WEO": "IMF.RES", "PCPS": "IMF.RES", "CTOT": "IMF.RES", "ITS": "IMF.RES", "ED": "IMF.RES",
    "EQ": "IMF.RES", "SRD": "IMF.RES", "MPFT": "IMF.RES", "TAXFIT": "IMF.RES",
    "BOP": "IMF.STA", "BOP_AGG": "IMF.STA", "IIP": "IMF.STA", "IIPCC": "IMF.STA", "SPE": "IMF.STA",
    "IRFCL": "IMF.STA", "IL": "IMF.STA", "ER": "IMF.STA", "EER": "IMF.STA", "CPI": "IMF.STA",
    "CPI_WCA": "IMF.STA", "PPI": "IMF.STA", "ITG": "IMF.STA", "ITG_WCA": "IMF.STA", "IMTS": "IMF.STA",
    "ANEA": "IMF.STA", "QNEA": "IMF.STA", "QGDP_WCA": "IMF.STA", "GFS_SOO": "IMF.STA",
    "GFS_BS": "IMF.STA", "GFS_COFOG": "IMF.STA", "GFS_SSUC": "IMF.STA", "GFS_SFCP": "IMF.STA",
    "GFS_SOEF": "IMF.STA", "QGFS": "IMF.STA", "FD": "IMF.STA", "DIP": "IMF.STA", "PIP": "IMF.STA",
    "COFER": "IMF.STA", "FAS": "IMF.STA", "FSIC": "IMF.STA", "FSIBSIS": "IMF.STA", "MFS_CBS": "IMF.STA",
    "MFS_DC": "IMF.STA", "MFS_MA": "IMF.STA", "MFS_IR": "IMF.STA", "NSDP": "IMF.STA", "LS": "IMF.STA",
    "FM": "IMF.FAD", "GDD": "IMF.FAD", "HPD": "IMF.FAD", "ICSD": "IMF.FAD", "PSBS": "IMF.FAD",
    "AFRREO": "IMF.AFR", "APDREO": "IMF.APD", "WHDREO": "IMF.WHD", "MCDREO": "IMF.MCD",
    "FDI": "IMF.MCM",
}

#: Citations for chart source lines.
FLOW_CITATIONS: dict[str, str] = {
    "WEO": "IMF, World Economic Outlook database",
    "BOP": "IMF, Balance of Payments Statistics",
    "IIP": "IMF, International Investment Position Statistics",
    "IRFCL": "IMF, International Reserves and Foreign Currency Liquidity",
    "FM": "IMF, Fiscal Monitor",
    "GDD": "IMF, Global Debt Database",
    "HPD": "IMF, Historical Public Debt Database",
    "ICSD": "IMF, Investment and Capital Stock Dataset",
    "AFRREO": "IMF, Regional Economic Outlook: Sub-Saharan Africa",
    "GFS_SOO": "IMF, Government Finance Statistics",
    "CPI": "IMF, Consumer Price Index",
    "ER": "IMF, Exchange Rates",
    "EER": "IMF, Effective Exchange Rates",
}


@dataclass(frozen=True)
class IndicatorInfo:
    label: str
    unit: str


#: WEO indicators with human-readable labels and units. Units describe values
#: as the API returns them (base units: ``NGDPD`` is in US dollars, not
#: billions). The first six are the CGJR fiscal/macro set.
WEO_INDICATORS: dict[str, IndicatorInfo] = {
    "NGDP_RPCH": IndicatorInfo("Real GDP growth", "annual % change"),
    "GGXWDG_NGDP": IndicatorInfo("General government gross debt", "% of GDP"),
    "GGXCNL_NGDP": IndicatorInfo("General government net lending/borrowing", "% of GDP"),
    "GGR_NGDP": IndicatorInfo("General government revenue", "% of GDP"),
    "GGX_NGDP": IndicatorInfo("General government total expenditure", "% of GDP"),
    "BCA_NGDPD": IndicatorInfo("Current account balance", "% of GDP"),
    "GGXONLB_NGDP": IndicatorInfo("General government primary net lending/borrowing", "% of GDP"),
    "GGXWDN_NGDP": IndicatorInfo("General government net debt", "% of GDP"),
    "GGSB_NPGDP": IndicatorInfo("General government structural balance", "% of potential GDP"),
    "NID_NGDP": IndicatorInfo("Total investment", "% of GDP"),
    "NGSD_NGDP": IndicatorInfo("Gross national savings", "% of GDP"),
    "PCPIPCH": IndicatorInfo("Inflation, average consumer prices", "annual % change"),
    "PCPIEPCH": IndicatorInfo("Inflation, end of period consumer prices", "annual % change"),
    "LUR": IndicatorInfo("Unemployment rate", "% of labor force"),
    "LP": IndicatorInfo("Population", "persons"),
    "NGDPD": IndicatorInfo("GDP, current prices", "current US$"),
    "NGDPDPC": IndicatorInfo("GDP per capita, current prices", "current US$"),
    "NGDP": IndicatorInfo("GDP, current prices", "current national currency"),
    "BCA": IndicatorInfo("Current account balance", "current US$"),
    "TX_RPCH": IndicatorInfo("Volume of exports of goods and services", "annual % change"),
    "TM_RPCH": IndicatorInfo("Volume of imports of goods and services", "annual % change"),
    # PPP concepts: UNIT_CODE is null on every release (imf-reader docs/weo-coverage.md), so
    # the unit must come from here. Only the dollar levels are PPP-dollar units; the
    # share and the conversion rate are not (and must not trip PPP-mixing checks).
    "PPPGDP": IndicatorInfo("GDP, current prices (PPP)", "current international $ (PPP base year unknown)"),
    "PPPPC": IndicatorInfo("GDP per capita, current prices (PPP)", "current international $ (PPP base year unknown)"),
    "NGDPRPPPPC": IndicatorInfo("GDP per capita, constant prices (PPP)", "constant international $ (PPP base year unknown)"),
    "PPPSH": IndicatorInfo("GDP based on PPP, share of world total", "% of world GDP (valued at purchasing power parity)"),
    "PPPEX": IndicatorInfo("Implied PPP conversion rate", "national currency per international $"),
}

#: Friendly names accepted by :func:`resolve_indicator` (case-insensitive).
WEO_ALIASES: dict[str, str] = {
    "real_gdp_growth": "NGDP_RPCH",
    "gdp_growth": "NGDP_RPCH",
    "gross_debt": "GGXWDG_NGDP",
    "government_debt": "GGXWDG_NGDP",
    "net_debt": "GGXWDN_NGDP",
    "net_lending": "GGXCNL_NGDP",
    "fiscal_balance": "GGXCNL_NGDP",
    "overall_balance": "GGXCNL_NGDP",
    "primary_balance": "GGXONLB_NGDP",
    "revenue": "GGR_NGDP",
    "government_revenue": "GGR_NGDP",
    "expenditure": "GGX_NGDP",
    "government_expenditure": "GGX_NGDP",
    "current_account": "BCA_NGDPD",
    "inflation": "PCPIPCH",
    "unemployment": "LUR",
}

#: Balance-of-payments items in the IMF.STA BOP dataflow:
#: name -> (BOP_ACCOUNTING_ENTRY, INDICATOR, label). The code pairs were read
#: from a recorded live BOP response (Kenya, 2016-2018) and the CL_BOP_INDICATOR
#: / CL_BOP_ACCOUNTING_ENTRY codelists. ``reserve_assets`` (A_T.R_F) is
#: UNVERIFIED as to the meaning of the A_T entry; a live test checks it.
BOP_ITEMS: dict[str, tuple[str, str, str]] = {
    "current_account": ("NETCD_T", "CAB", "Current account balance (credit less debit)"),
    "goods_services": ("NETCD_T", "GS", "Goods and services, net"),
    "goods": ("NETCD_T", "G", "Goods, net"),
    "services": ("NETCD_T", "S", "Services, net"),
    "primary_income": ("NETCD_T", "IN1", "Primary income, net"),
    "secondary_income": ("NETCD_T", "IN2", "Secondary income, net"),
    "capital_account": ("NETCD_T", "KAB", "Capital account balance (credit less debit)"),
    "errors_omissions": ("NETCD_T", "EO", "Net errors and omissions"),
    "financial_account": ("NNAFANIL_T", "FAB", "Financial account balance (assets less liabilities)"),
    "direct_investment": ("NNAFANIL_T", "D_F", "Direct investment, net (assets less liabilities)"),
    "portfolio_investment": ("NNAFANIL_T", "P_F", "Portfolio investment, net (assets less liabilities)"),
    "other_investment": ("NNAFANIL_T", "O_F", "Other investment, net (assets less liabilities)"),
    "reserve_assets": ("A_T", "R_F", "Reserve assets"),
}

#: Unit codes (IMF CL_UNIT) -> labels for values in base units.
UNIT_LABELS: dict[str, str] = {
    "PT": "percent",
    "USD": "current US$",
    "EUR": "current euros",
    "XDC": "current national currency",
    "IX": "index",
    "PE": "persons",
}

#: SDMX CL_OBS_STATUS letters -> canonical obs_status. Their use by the IMF
#: ``STATUS``/``OBS_STATUS`` attributes is UNVERIFIED (the IMF ships STATUS as
#: free literals such as ``NA``). Unknown values map to "" and are reported.
OBS_STATUS_MAP: dict[str, str] = {
    "": "", "A": "", "NA": "", "N/A": "", "M": "",
    "B": "break", "E": "estimate", "F": "forecast", "I": "imputed", "P": "provisional",
}

_ENVELOPE_COLUMNS = ("STRUCTURE", "STRUCTURE_ID", "STRUCTURE_NAME", "ACTION", "DATAFLOW")
_COUNTRY_DIMS = ("COUNTRY", "REF_AREA", "JURISDICTION", "ECONOMY")
_FREQ_DIMS = ("FREQUENCY", "FREQ")
_STATUS_ATTRS = ("OBS_STATUS", "STATUS")
_CODE_RE = re.compile(r"^[A-Za-z0-9_\-@]+$")
_COUNTRY_RE = re.compile(r"^[A-Z0-9_]{2,10}$")
_ISO3_RE = re.compile(r"^[A-Z]{3}$")
_MULTI_VALUE_SUFFIX_RE = re.compile(r"\[[^\]]*\]$")
_VINTAGE_FLOW_RE = re.compile(r"^(?P<base>[A-Z0-9_]+?)_(?P<year>\d{4})_(?P<mon>[A-Z]{3})_VINTAGE$")
#: archival WEO vintage dataflows (imf-reader's ``_VINTAGE_ID_RE``)
_WEO_VINTAGE_FLOW_RE = re.compile(r"^WEO_(?P<year>\d{4})_(?P<mon>[A-Z]{3})_VINTAGE$")
_STRUCTURE_ID_RE = re.compile(r"^(?P<agency>[^:]+):(?P<id>[^(]+)\((?P<version>[^)]+)\)$")
_LAST_ACTUAL_RE = re.compile(r"^(?:FY(?P<fy>\d{4})/\d{2,4}|(?P<year>\d{4}))$")
_MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
_MONTH_ABBR = {v: k for k, v in _MONTHS.items()}
#: caps on evidence lists inside caveats
_EVIDENCE_CAP = 40


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------
class IMFAPIError(RuntimeError):
    """The IMF API answered with an HTTP error (``status``), optionally with its
    JSON error envelope (``message``, ``code``, ``correlationId``)."""

    def __init__(self, status: int, message: str, url: str = "", code: str = "", correlation_id: str = ""):
        self.status = int(status)
        self.message = message
        self.url = url
        self.code = code
        self.correlation_id = correlation_id
        text = f"IMF API error {self.status}: {message or 'no message'}"
        if code:
            text += f" (code {code})"
        if correlation_id:
            text += f" [correlationId {correlation_id}]"
        if url:
            text += f" [{url}]"
        super().__init__(text)

    @classmethod
    def from_fetch_error(cls, exc: Exception, url: str = "") -> "IMFAPIError":
        """Parse ``http.FetchError`` text (``"<status> from <url>: <body>"``)."""
        text = str(exc)
        m = re.match(r"^(\d{3}) from (\S+?)(?::\s(.*))?$", text, flags=re.S)
        if not m:
            return cls(0, text, url)
        status, url2, body = int(m.group(1)), m.group(2), (m.group(3) or "").strip()
        message, code, corr = body, "", ""
        try:
            obj = json.loads(body)
            if isinstance(obj, dict):
                message = str(obj.get("message") or obj.get("detail") or body)
                code = str(obj.get("code") or "")
                corr = str(obj.get("correlationId") or "")
        except ValueError:
            mm = re.search(r'"message"\s*:\s*"([^"]*)"', body)
            if mm:
                message = mm.group(1)
        return cls(status, message, url2 or url, code, corr)


class IMFResponseError(RuntimeError):
    """The payload is not a format this connector understands (HTML page,
    wrong format, missing TIME_PERIOD/OBS_VALUE, truncated JSON)."""


# ---------------------------------------------------------------------------
# small pure helpers
# ---------------------------------------------------------------------------
def _as_list(values: Sequence[str] | str | None, seps: str = r"[;,+\s]+") -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = re.split(seps, values)
    out: list[str] = []
    for v in values:
        v = str(v).strip()
        if v and v not in out:
            out.append(v)
    return out


def resolve_indicator(code: str) -> str:
    """Map a friendly name (``"gross_debt"``) to a WEO code; codes pass through."""
    key = str(code).strip()
    return WEO_ALIASES.get(key.lower(), key)


def normalize_indicators(indicators: Sequence[str] | str) -> list[str]:
    """Indicator codes (aliases resolved), validated for use in an SDMX key."""
    codes = [resolve_indicator(c) for c in _as_list(indicators)]
    codes = list(dict.fromkeys(codes))
    if not codes:
        raise ValueError("at least one indicator code is required")
    bad = [c for c in codes if not _CODE_RE.match(c)]
    if bad:
        raise ValueError(f"invalid indicator code(s): {bad} (no '.', '+' or '/' allowed)")
    return codes


def normalize_countries(countries: Sequence[str] | str | None) -> list[str] | None:
    """Upper-cased country/aggregate codes, or None for all (``None``, ``"all"``, ``"*"``)."""
    if countries is None or (isinstance(countries, str) and countries.strip().lower() in ("", "all", "*")):
        return None
    codes = [c.upper() for c in _as_list(countries)]
    bad = [c for c in codes if not _COUNTRY_RE.match(c)]
    if bad:
        raise ValueError(f"invalid country code(s): {bad}")
    return codes or None


def build_key(*parts: Sequence[str] | str | None) -> str:
    """Positional SDMX key: one slot per dimension, ``+`` within a slot, ``*``
    for a wildcard (``None``, ``""`` or ``"*"``)."""
    slots = []
    for part in parts:
        codes = _as_list(part, seps=r"[;,+]+") if part is not None else []
        codes = [c for c in codes if c != "*"]
        slots.append("+".join(codes) if codes else "*")
    return ".".join(slots)


#: Three-letter DataMapper region codes (``/api/v1/regions``: SSQ "Sub-Saharan
#: Africa (Region)", AFQ, APQ, EUQ, MEQ, NAQ, WHQ). None is an ISO 3166 alpha-3
#: code. Other DataMapper groups use longer codes (``ADVEC``, ``OEMDC``), which
#: the non-ISO rule already catches; :meth:`IMF.fetch_datamapper_groups` reads
#: the live lists when metadata is enabled.
DATAMAPPER_REGION_CODES = frozenset({"AFQ", "APQ", "EUQ", "MEQ", "NAQ", "SSQ", "WHQ"})


def is_aggregate(code: str, extra: frozenset[str] | set[str] = frozenset()) -> bool:
    """IMF aggregates: SDMX G-codes and other non-ISO codes (``G001`` World,
    ``G603``, ``GX1213``, DataMapper ``ADVEC``), the three-letter DataMapper
    regions (:data:`DATAMAPPER_REGION_CODES`) and any code in ``extra``."""
    c = str(code)
    return not _ISO3_RE.match(c) or c in DATAMAPPER_REGION_CODES or c in extra


def normalize_period(raw: Any) -> str:
    """SDMX period -> canonical period: ``2024``, ``2024-A1`` -> ``2024``;
    ``2024-Q1`` -> ``2024Q1``; ``2024-M03``/``2024-03`` -> ``2024-03``."""
    s = str(raw).strip()
    if m := re.fullmatch(r"(\d{4})(?:-A1?)?", s):
        return m.group(1)
    if m := re.fullmatch(r"(\d{4})-?Q([1-4])", s):
        return f"{m.group(1)}Q{m.group(2)}"
    if m := re.fullmatch(r"(\d{4})-M?(\d{2})", s):
        return f"{m.group(1)}-{m.group(2)}"
    if m := re.fullmatch(r"(\d{4})-S([12])", s):
        return f"{m.group(1)}S{m.group(2)}"
    return s


def parse_last_actual(raw: Any) -> tuple[int | None, bool]:
    """``LATEST_ACTUAL_ANNUAL_DATA`` -> (year, is_fiscal_year).

    ``"2024"`` -> (2024, False). ``"FY2023/24"`` -> (2023, True): the leading
    year, as imf-reader does. Whether WEO labels FY2023/24 as 2023 or 2024
    varies by country; the leading year is the conservative choice (it never
    labels a projection as actual). Anything else -> (None, False).
    """
    s = "" if raw is None else str(raw).strip()
    m = _LAST_ACTUAL_RE.match(s)
    if not m:
        return None, False
    if m.group("fy"):
        return int(m.group("fy")), True
    return int(m.group("year")), False


def parse_date(raw: Any) -> str:
    """ISO date (``YYYY-MM-DD``) from ``2026-04-14``, ``2026-04-14T..`` or US
    ``4/14/2026``; ``""`` when unparseable."""
    s = "" if raw is None else str(raw).strip()
    if not s:
        return ""
    candidates = [(s[:10], "%Y-%m-%d")] if re.match(r"^\d{4}-\d{2}-\d{2}", s) else []
    candidates += [(s, "%m/%d/%Y"), (s, "%d %B %Y"), (s, "%B %d, %Y")]
    for text, fmt in candidates:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


# -- WEO vintages -------------------------------------------------------------
def parse_vintage(vintage: Any) -> tuple[int, int] | None:
    """Normalise a WEO vintage to ``(year, month)`` with month 4 or 10.

    Accepts ``None``/``"latest"`` (-> None), ``"2025-10"``, ``"2025-OCT"``,
    ``"Oct 2025"``, ``"October 2025"``, ``"WEO 2025-10"``,
    ``"WEO_2025_OCT_VINTAGE"``, ``("October", 2025)`` and ``(2025, 10)``.
    """
    if vintage is None:
        return None
    if isinstance(vintage, (tuple, list)) and len(vintage) == 2:
        a, b = vintage
        if isinstance(a, int) and not isinstance(a, bool):
            year, month = int(a), _month_number(b)
        else:
            year, month = int(b), _month_number(a)
        return _check_vintage(year, month, vintage)
    s = str(vintage).strip().upper()
    if s in ("", "LATEST", "CURRENT"):
        return None
    if m := _VINTAGE_FLOW_RE.match(s):
        return _check_vintage(int(m.group("year")), _month_number(m.group("mon")), vintage)
    s = re.sub(r"^WEO[\s_-]*", "", s)
    if m := re.fullmatch(r"(\d{4})[\s_-]*(\d{1,2})", s):
        return _check_vintage(int(m.group(1)), int(m.group(2)), vintage)
    if m := re.fullmatch(r"(\d{4})[\s_-]*([A-Z]{3,9})", s):
        return _check_vintage(int(m.group(1)), _month_number(m.group(2)), vintage)
    if m := re.fullmatch(r"([A-Z]{3,9})\.?[\s_-]*(\d{4})", s):
        return _check_vintage(int(m.group(2)), _month_number(m.group(1)), vintage)
    raise ValueError(f"unrecognised WEO vintage {vintage!r}; use 'latest' or e.g. '2025-10'")


def _month_number(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    s = str(value).strip().upper()
    if s.isdigit():
        return int(s)
    if s[:3] in _MONTHS:
        return _MONTHS[s[:3]]
    raise ValueError(f"unrecognised month {value!r}")


def _check_vintage(year: int, month: int, raw: Any) -> tuple[int, int]:
    if month not in (4, 10):
        raise ValueError(f"WEO databases are released in April and October; got month {month} in {raw!r}")
    if not 1980 <= year <= 2100:
        raise ValueError(f"implausible WEO vintage year {year} in {raw!r}")
    return year, month


def weo_flow_for(vintage: tuple[int, int] | None) -> str:
    """``None`` -> ``"WEO"`` (latest); ``(2025, 10)`` -> ``"WEO_2025_OCT_VINTAGE"``."""
    if vintage is None:
        return WEO_FLOW
    year, month = vintage
    return f"WEO_{year}_{_MONTH_ABBR[month]}_VINTAGE"


def vintage_from_flow(flow: str) -> tuple[int, int] | None:
    m = _VINTAGE_FLOW_RE.match(str(flow).upper())
    if not m:
        return None
    try:
        return _check_vintage(int(m.group("year")), _month_number(m.group("mon")), flow)
    except ValueError:
        return None


def vintage_from_publication_date(date: str) -> tuple[int, int] | None:
    """Release from a PUBLICATION_DATE: month < 7 -> April, else October."""
    iso = parse_date(date)
    if not iso:
        return None
    year, month = int(iso[:4]), int(iso[5:7])
    return year, (4 if month < 7 else 10)


def vintage_label(vintage: tuple[int, int] | None) -> str:
    """``(2026, 4)`` -> ``"WEO 2026-04"`` (the Provenance.vintage convention)."""
    return f"WEO {vintage[0]}-{vintage[1]:02d}" if vintage else ""


def infer_release_from_date(date: Any) -> tuple[int, int] | None:
    """The WEO release the DataMapper most likely served on ``date``.

    WEO databases come out in mid-April and mid-October (14 April 2026, 22 April
    2025, 16 April 2024), so a day-level cut-off would be wrong in some years.
    To never label a projection as an actual, a release is assumed out only
    after its month ends: January-April -> the previous October; May-October ->
    April; November-December -> October. The publication year is thus the
    previous year until the end of April (an April retrieval after the release
    flags one extra year as a projection). ``None`` if ``date`` is unparseable.
    """
    iso = parse_date(date)
    if not iso:
        return None
    year, month = int(iso[:4]), int(iso[5:7])
    if month <= 4:
        return year - 1, 10
    return (year, 4) if month <= 10 else (year, 10)


def infer_vintage_from_text(text: str) -> tuple[int, int] | None:
    """``"World Economic Outlook (April 2026)"`` -> ``(2026, 4)``."""
    m = re.search(r"\b(April|October|Apr|Oct)\.?\s+(\d{4})\b", str(text or ""), flags=re.I)
    if not m:
        return None
    return int(m.group(2)), (4 if m.group(1).lower().startswith("apr") else 10)


# -- request builders (pure) ---------------------------------------------------
def build_data_request(
    agency: str,
    flow: str,
    key: str,
    *,
    version: str = "+",
    params: dict | None = None,
    fmt: str = "csv",
) -> tuple[str, dict[str, str], dict[str, str]]:
    """``(url, params, headers)`` for an SDMX 3.0 data query."""
    if fmt not in ("csv", "json"):
        raise ValueError("fmt must be 'csv' or 'json'")
    for name, value in (("agency", agency), ("flow", flow), ("version", version)):
        if not value or "/" in str(value):
            raise ValueError(f"invalid {name}: {value!r}")
    url = f"{SDMX_BASE_URL}/data/dataflow/{agency}/{flow}/{version}/{key or '*'}"
    q = {str(k): str(v) for k, v in (params or {}).items() if v is not None}
    if fmt == "json":
        q = {**JSON_DEFAULT_PARAMS, **q}
        q["dimensionAtObservation"] = q.get("dimensionAtObservation") or "TIME_PERIOD"
        headers = {"Accept": JSON_ACCEPT}
    else:
        q.pop("dimensionAtObservation", None)
        headers = {"Accept": CSV_ACCEPT}
    return url, q, headers


def build_weo_request(
    indicators: Sequence[str] | str,
    countries: Sequence[str] | str | None = None,
    vintage: tuple[int, int] | None = None,
    *,
    fmt: str = "csv",
    flow: str | None = None,
    version: str = "+",
) -> tuple[str, dict[str, str], dict[str, str]]:
    """WEO data request: key ``COUNTRY.INDICATOR.A``, asking for series
    attributes so that ``LATEST_ACTUAL_ANNUAL_DATA`` is included.

    The dataflow is ``flow``/``version`` when given (what
    :meth:`IMF.fetch_weo` uses for a past release, resolved through
    :func:`weo_flow_mapping`), else ``IMF.RES/WEO`` (latest) or the
    conventionally named ``IMF.RES/WEO_YYYY_MON_VINTAGE`` for ``vintage``."""
    inds = normalize_indicators(indicators)
    ctys = normalize_countries(countries)
    key = build_key(ctys, inds, "A")
    params = {"attributes": "series"} if fmt == "csv" else {"attributes": "all"}
    return build_data_request(WEO_AGENCY, flow or weo_flow_for(vintage), key, version=version, params=params, fmt=fmt)


def build_publication_probe(flow: str = WEO_FLOW, version: str = "+") -> tuple[str, dict[str, str], dict[str, str]]:
    """One-observation request that returns the dataset attribute PUBLICATION_DATE
    of ``flow`` at ``version`` (imf-reader's probe: ``USA.NGDP_RPCH.A``)."""
    return build_data_request(
        WEO_AGENCY, flow, WEO_PROBE_KEY, version=version, params={"attributes": "dataset", "lastNObservations": "1"}
    )


def time_filter_param(start: int | str | None, end: int | str | None, freq: str | None = "A") -> dict[str, str]:
    """SDMX 3.0 time filter ``c[TIME_PERIOD]=ge:2016-A1+le:2018-A1``.

    The annual form is verified (imfp request against IMF.STA/BOP). imfp notes
    that only ``IMF.STA`` flows honour it; quarterly/monthly suffixes follow
    imfp's mapping and are UNVERIFIED.
    """
    starts = {"A": "-A1", "Q": "-Q1", "M": "-M01"}
    ends = {"A": "-A1", "Q": "-Q4", "M": "-M12"}
    f = (freq or "").upper()
    parts = []
    for bound, value, table, default in (("ge", start, starts, "-A1"), ("le", end, ends, "-W99")):
        if value is None or str(value).strip() == "":
            continue
        s = str(value).strip()
        if re.fullmatch(r"\d{4}", s):
            s = s + table.get(f, default)
        parts.append(f"{bound}:{s}")
    return {"c[TIME_PERIOD]": "+".join(parts)} if parts else {}


def build_dataflows_request(agency: str = "all") -> tuple[str, dict[str, str], dict[str, str]]:
    """Catalogue of dataflows (latest version of each)."""
    return f"{SDMX_BASE_URL}/structure/dataflow/{agency}/*/+", {}, {"Accept": JSON_ACCEPT}


def build_weo_vintages_request() -> tuple[str, dict[str, str], dict[str, str]]:
    """Every version of every IMF.RES dataflow, as stubs (imf-reader's discovery query)."""
    return f"{SDMX_BASE_URL}/structure/dataflow/{WEO_AGENCY}/*/*", {"detail": "allstubs"}, {"Accept": JSON_ACCEPT}


def build_codelist_request(codelist_id: str, agency: str = "IMF") -> tuple[str, dict[str, str], dict[str, str]]:
    if not _CODE_RE.match(codelist_id) or not re.fullmatch(r"[A-Za-z0-9_.\-]+", agency):
        raise ValueError(f"invalid codelist reference {agency}/{codelist_id}")
    return f"{SDMX_BASE_URL}/structure/codelist/{agency}/{codelist_id}/+", {}, {"Accept": JSON_ACCEPT}


def build_datamapper_request(indicator: str, countries: Sequence[str] | str | None = None) -> tuple[str, dict[str, str]]:
    """DataMapper series URL: ``/api/v1/{indicator}`` plus ``/{c1}/{c2}...``.

    A single country path is verified (many clients); several countries joined
    by ``/`` follow the API help text and are UNVERIFIED (live test). No
    ``periods`` filter is sent: years are filtered client side so that the
    cache key does not depend on the year range.
    """
    code = str(indicator).strip()
    if not _CODE_RE.match(code):
        raise ValueError(f"invalid DataMapper indicator {indicator!r}")
    ctys = normalize_countries(countries)
    url = f"{DATAMAPPER_BASE_URL}/{code}" + ("/" + "/".join(ctys) if ctys else "")
    return url, {}


def build_datamapper_indicators_request() -> tuple[str, dict[str, str]]:
    return f"{DATAMAPPER_BASE_URL}/indicators", {}


def build_datamapper_groups_request(kind: str = "regions") -> tuple[str, dict[str, str]]:
    """``/api/v1/regions`` or ``/api/v1/groups`` (aggregate codes and labels)."""
    if kind not in ("regions", "groups"):
        raise ValueError("kind must be 'regions' or 'groups'")
    return f"{DATAMAPPER_BASE_URL}/{kind}", {}


# ---------------------------------------------------------------------------
# readers: payload -> raw table (dimension columns, TIME_PERIOD, OBS_VALUE, attributes)
# ---------------------------------------------------------------------------
def _empty_raw() -> pd.DataFrame:
    raw = pd.DataFrame(columns=["TIME_PERIOD", "OBS_VALUE"])
    raw.attrs.update({"dimensions": [], "attributes": [], "structure_id": ""})
    return raw


def _sniff_error(text: str, url: str = "", expect: str = "json") -> None:
    """Raise on payloads that are not data: HTML/XML pages, the API's JSON error
    envelope (``message``/``code``/``correlationId``), or JSON where CSV was expected."""
    head = text.lstrip()[:1]
    where = f" from {url}" if url else ""
    if head == "<":
        raise IMFResponseError(f"expected SDMX data, got an HTML/XML page{where}: {text.strip()[:150]!r}")
    if head in ("{", "["):
        try:
            obj = json.loads(text)
        except ValueError:
            obj = None
        if isinstance(obj, dict) and "data" not in obj and ("message" in obj or "errors" in obj):
            status = obj.get("status") or obj.get("statusCode") or 0
            raise IMFAPIError(
                int(status) if str(status).isdigit() else 0,
                str(obj.get("message") or obj.get("errors") or ""),
                url,
                str(obj.get("code") or ""),
                str(obj.get("correlationId") or ""),
            )
        if expect == "csv":
            raise IMFResponseError(f"expected SDMX-CSV, got JSON{where}: {text.strip()[:150]!r}")


def read_sdmx_csv(payload: bytes | str, url: str = "") -> pd.DataFrame:
    """Parse an SDMX-CSV data message into the raw table.

    Columns are read as strings; empty cells become ``""``. Header suffixes
    naming a multi-value delimiter (``STRUCTURE[;]``, ``TOPIC[]``) are
    stripped. Dimension columns are those between the envelope columns
    (``STRUCTURE``, ``STRUCTURE_ID``, ``ACTION``) and ``TIME_PERIOD``;
    attribute columns follow ``OBS_VALUE``. ``raw.attrs`` holds
    ``dimensions``, ``attributes`` and ``structure_id``.
    """
    text = payload.decode("utf-8-sig", errors="replace") if isinstance(payload, (bytes, bytearray)) else str(payload)
    if not text.strip():
        return _empty_raw()
    _sniff_error(text, url, expect="csv")
    try:
        df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, na_values=[], low_memory=False)
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise IMFResponseError(f"unreadable SDMX-CSV payload{(' from ' + url) if url else ''}: {exc}") from exc
    df.columns = [_MULTI_VALUE_SUFFIX_RE.sub("", str(c).strip().lstrip("﻿")) for c in df.columns]
    if "TIME_PERIOD" not in df.columns or "OBS_VALUE" not in df.columns:
        raise IMFResponseError(f"SDMX-CSV payload lacks TIME_PERIOD/OBS_VALUE columns: {list(df.columns)[:12]}")
    cols = list(df.columns)
    t, v = cols.index("TIME_PERIOD"), cols.index("OBS_VALUE")
    env_idx = [i for i, c in enumerate(cols[:t]) if c in _ENVELOPE_COLUMNS]
    first_dim = (max(env_idx) + 1) if env_idx else 0
    dims = [c for c in cols[first_dim:t] if c not in _ENVELOPE_COLUMNS]
    attrs = [c for c in cols[v + 1:] if c not in _ENVELOPE_COLUMNS]
    structure_id = ""
    if "STRUCTURE_ID" in df.columns and len(df):
        structure_id = str(df["STRUCTURE_ID"].iloc[0])
    raw = df[dims + ["TIME_PERIOD", "OBS_VALUE"] + attrs].copy()
    raw.attrs.update({"dimensions": dims, "attributes": attrs, "structure_id": structure_id})
    return raw


def _decode_json(payload: bytes | str | Any, url: str = "") -> Any:
    if isinstance(payload, (bytes, bytearray, str)):
        text = payload.decode("utf-8-sig", errors="replace") if isinstance(payload, (bytes, bytearray)) else payload
        if not text.strip():
            return {}
        _sniff_error(text, url)
        try:
            return json.loads(text)
        except ValueError as exc:
            raise IMFResponseError(f"invalid JSON{(' from ' + url) if url else ''}: {exc}") from exc
    return payload


def _attr_value(defn: dict, raw: Any) -> str | None:
    """Coded attribute slots hold an integer index into ``values``; uncoded
    ones (e.g. COUNTRY_UPDATE_DATE, STATUS) carry a string literal. An index
    into an empty or short ``values`` list gives None: the portal ships
    dimension-group definitions with no values for ``+`` keys (imf-mcp-server
    issue #34), and reading the index as the value would be wrong."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        values = defn.get("values") or []
        if not 0 <= raw < len(values):
            return None
        entry = values[raw]
        val = entry.get("id", entry.get("value"))
        if isinstance(val, dict):  # localised value
            val = val.get("en") or next(iter(val.values()), None)
        return None if val is None else str(val)
    return str(raw)


def read_sdmx_json(payload: bytes | str | Any, url: str = "") -> pd.DataFrame:
    """Parse an SDMX-JSON 2.0 data message (series-keyed, ``dimensionAtObservation=TIME_PERIOD``)
    into the same raw table as :func:`read_sdmx_csv`.

    Series keys (``"0:3:1"``) index each series dimension's ``values``; an
    observation key indexes the time dimension. Attributes are decoded at the
    dimension-group (partial key, one colon slot per series dimension plus one
    for time), series and observation levels. Observation arrays hold the
    measures first, then the observation attributes. Values arrive as strings.
    """
    obj = _decode_json(payload, url)
    data = obj.get("data") if isinstance(obj, dict) else None
    if not isinstance(data, dict):
        if obj in ({}, None):
            return _empty_raw()
        raise IMFResponseError("SDMX-JSON message has no 'data' object")
    datasets, structures = data.get("dataSets") or [], data.get("structures") or []
    if not datasets or not structures:
        return _empty_raw()
    ds, st = datasets[0], structures[0]
    sdims = (st.get("dimensions") or {}).get("series") or []
    odims = (st.get("dimensions") or {}).get("observation") or []
    dim_ids = [str(d.get("id")) for d in sdims]
    attrs = st.get("attributes") or {}
    g_defs, s_defs, o_defs = attrs.get("dimensionGroup") or [], attrs.get("series") or [], attrs.get("observation") or []
    n_measures = len((st.get("measures") or {}).get("observation") or []) or 1
    n = len(dim_ids)
    groups = []
    for gkey, gvals in (ds.get("dimensionGroupAttributes") or {}).items():
        parts = str(gkey).split(":")
        pins = {i: p for i, p in enumerate(parts) if p != ""}
        groups.append((pins, gvals))

    def code_at(defn: dict | None, idx: Any, keys: tuple[str, ...]) -> str:
        try:
            entry = (defn or {}).get("values", [])[int(idx)]
        except (ValueError, TypeError, IndexError):
            return ""
        for k in keys:
            if entry.get(k) is not None:
                return str(entry[k])
        return ""

    def apply(row: dict, defs: list, raw_vals: list | None) -> None:
        for defn, raw in zip(defs, raw_vals or []):
            val = _attr_value(defn, raw)
            name = str(defn.get("id"))
            if val is not None and name not in row:
                row[name] = val

    links = [l.get("urn", "") for l in st.get("links") or [] if l.get("title") == "Dataflow" or "Dataflow=" in str(l.get("urn", ""))]
    structure_id = ""
    if links and (m := re.search(r"Dataflow=([^:]+:[^(]+\([^)]+\))", links[0])):
        structure_id = m.group(1)

    rows: list[dict] = []
    for skey, series in (ds.get("series") or {}).items():
        idx = str(skey).split(":")
        base = {dim_ids[i]: code_at(sdims[i], idx[i], ("id", "value")) for i in range(min(n, len(idx)))}
        sattr: dict = {}
        apply(sattr, s_defs, series.get("attributes"))
        for pins, gvals in groups:
            if all(i < n and i < len(idx) and idx[i] == p for i, p in pins.items()):
                apply(sattr, g_defs, gvals)
        for okey, obs in (series.get("observations") or {}).items():
            row = dict(base)
            row["TIME_PERIOD"] = code_at(odims[0] if odims else None, okey, ("value", "id"))
            raw_value = obs[0] if obs else None
            row["OBS_VALUE"] = "" if raw_value is None else str(raw_value)
            apply(row, o_defs, list(obs[n_measures:]) if obs else [])
            for pins, gvals in groups:
                if n in pins and pins[n] == str(okey) and all(
                    i < len(idx) and idx[i] == p for i, p in pins.items() if i < n
                ):
                    apply(row, g_defs, gvals)
            for k, val in sattr.items():
                row.setdefault(k, val)
            rows.append(row)
    attr_names = [str(d.get("id")) for d in [*g_defs, *s_defs, *o_defs]]
    if not rows:
        raw = _empty_raw()
        raw.attrs.update({"dimensions": dim_ids, "structure_id": structure_id})
        return raw
    raw = pd.DataFrame(rows)
    present_attrs = [a for a in dict.fromkeys(attr_names) if a in raw.columns and a not in dim_ids]
    raw = raw[dim_ids + ["TIME_PERIOD", "OBS_VALUE"] + present_attrs].fillna("").astype(str)
    raw.attrs.update({"dimensions": dim_ids, "attributes": present_attrs, "structure_id": structure_id})
    return raw


def read_sdmx(payload: bytes | str, fmt: str = "csv", url: str = "") -> pd.DataFrame:
    return read_sdmx_csv(payload, url) if fmt == "csv" else read_sdmx_json(payload, url)


# ---------------------------------------------------------------------------
# raw table -> canonical frame
# ---------------------------------------------------------------------------
@dataclass
class ParsedSDMX:
    """Canonical frame plus series-level metadata from one SDMX data message."""

    frame: pd.DataFrame
    series: pd.DataFrame
    dimensions: list[str] = field(default_factory=list)
    structure_id: str = ""
    obs_status_raw: dict[str, int] = field(default_factory=dict)
    unknown_obs_status: dict[str, int] = field(default_factory=dict)
    dropped_missing: int = 0
    duplicates_dropped: int = 0
    publication_date: str = ""
    forecast_rules: dict[str, str] = field(default_factory=dict)  # "ISO/IND" -> rule


SERIES_COLUMNS = [
    "iso3", "indicator", "unit_code", "scale", "last_actual", "last_actual_raw", "fiscal_year",
    "reporting_months", "country_update_date", "notes",
]


def unit_for(indicator: str, unit_code: str = "", units: dict[str, str] | None = None) -> str:
    """Unit label: explicit table, WEO table, code-suffix rule, then CL_UNIT code."""
    if units and indicator in units:
        return units[indicator]
    if indicator in WEO_INDICATORS:
        return WEO_INDICATORS[indicator].unit
    code = str(unit_code or "").strip()
    if code in ("PT", "") and re.search(r"_NGDPD?$", indicator):
        return "% of GDP"
    if code in ("PT", "") and re.search(r"_R?PCH$", indicator):
        return "annual % change"
    # Generic PPP rule for level series only: shares (..SH) and conversion rates (..EX) are not PPP dollars.
    if "PPP" in indicator.upper() and not re.search(r"(SH|EX)$", indicator.upper()):
        return "international $ (PPP base year unknown)"
    return UNIT_LABELS.get(code, code)


def apply_forecast_flags(
    frame: pd.DataFrame,
    last_actual: dict[tuple[str, str], int | None],
    *,
    publication_year: int | None = None,
    distinguish_estimates: bool = False,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Set ``obs_status`` for WEO-style series.

    * Series with a last actual year ``L`` ("Estimates Start After"): periods
      whose year is after ``L`` become ``"forecast"``. With
      ``distinguish_estimates=True`` and a known ``publication_year``, years
      after ``L`` but before the publication year become ``"estimate"``.
    * Series without one, when ``publication_year`` is known: years from the
      publication year on become ``"forecast"`` (a WEO never holds actuals for
      its own publication year). This is a lower bound: earlier years may also
      be staff estimates.

    Returns the new frame and the rule used per ``"ISO3/INDICATOR"``.
    """
    out = frame.copy()
    rules: dict[str, str] = {}
    statuses = list(out["obs_status"]) if len(out) else []
    for i, (iso3, ind, period) in enumerate(zip(out["iso3"], out["indicator"], out["period"])):
        y = year_of(period)
        la = last_actual.get((iso3, ind))
        sid = f"{iso3}/{ind}"
        if la is not None:
            rules[sid] = "last_actual"
            if y is not None and y > la:
                if distinguish_estimates and publication_year is not None and y < publication_year:
                    statuses[i] = "estimate"
                else:
                    statuses[i] = "forecast"
        elif publication_year is not None:
            rules[sid] = "publication_year"
            if y is not None and y >= publication_year:
                statuses[i] = "forecast"
        else:
            rules.setdefault(sid, "none")
    if len(out):
        out["obs_status"] = statuses
    return out, rules


def raw_to_canonical(
    raw: pd.DataFrame,
    *,
    dataset: str,
    country_dim: str | None = None,
    indicator_dims: Sequence[str] | None = None,
    labels: dict[str, str] | None = None,
    units: dict[str, str] | None = None,
    country_names: dict[str, str] | None = None,
    publication_year: int | None = None,
    distinguish_estimates: bool = False,
    flag_forecasts: bool = True,
    weo_rules: bool = False,
) -> ParsedSDMX:
    """Map the raw table to the canonical frame.

    ``iso3`` comes from the country dimension (``COUNTRY``/``REF_AREA``; ``_Z``
    when the flow has none). ``indicator`` joins the remaining non-frequency
    dimensions with ``.`` (WEO: the INDICATOR code; BOP:
    ``NETCD_T.CAB.USD``). Rows without a numeric value are dropped and counted.
    Forecast rules are computed when the data look like WEO data (a last-actual
    column, a publication year) or always with ``weo_rules=True``, so that every
    WEO series gets a rule, ``"none"`` included.
    """
    dims = list(raw.attrs.get("dimensions") or [])
    structure_id = raw.attrs.get("structure_id", "")
    cdim = country_dim or next((d for d in _COUNTRY_DIMS if d in dims), None)
    fdims = [d for d in dims if d in _FREQ_DIMS]
    ind_dims = list(indicator_dims) if indicator_dims else [d for d in dims if d != cdim and d not in fdims]
    empty = ParsedSDMX(frame=empty_frame(), series=pd.DataFrame(columns=SERIES_COLUMNS), dimensions=dims, structure_id=structure_id)
    if raw.empty:
        return empty
    missing_dims = [d for d in ([cdim] if cdim else []) + ind_dims if d not in raw.columns]
    if missing_dims:
        raise IMFResponseError(f"payload lacks dimension column(s) {missing_dims}; present: {list(raw.columns)[:15]}")

    def col(name: str) -> pd.Series:
        return raw[name].astype(str) if name in raw.columns else pd.Series([""] * len(raw), index=raw.index)

    iso3 = col(cdim).str.strip() if cdim else pd.Series(["_Z"] * len(raw), index=raw.index)
    if ind_dims:
        indicator = raw[ind_dims].astype(str).agg(".".join, axis=1)
    else:
        indicator = pd.Series([dataset or "value"] * len(raw), index=raw.index)
    value = pd.to_numeric(col("OBS_VALUE").str.strip().replace({"NA": "", "n/a": "", "--": ""}), errors="coerce")
    unit_code = col("UNIT")
    status_col = next((a for a in _STATUS_ATTRS if a in raw.columns), None)
    raw_status = col(status_col).str.strip() if status_col else pd.Series([""] * len(raw), index=raw.index)
    publication_date = ""
    if "PUBLICATION_DATE" in raw.columns:
        vals = [v for v in raw["PUBLICATION_DATE"].astype(str) if v.strip()]
        publication_date = parse_date(vals[0]) if vals else ""

    work = pd.DataFrame(
        {
            "iso3": iso3,
            "indicator": indicator,
            "period": col("TIME_PERIOD").map(normalize_period),
            "value": value,
            "unit_code": unit_code,
            "raw_status": raw_status,
            "last_actual_raw": col("LATEST_ACTUAL_ANNUAL_DATA").str.strip(),
            "reporting_months": col("START_END_MONTHS_OF_REPORTING_YEAR").str.strip(),
            "country_update_date": col("COUNTRY_UPDATE_DATE").str.strip(),
            "scale": col("SCALE").str.strip(),
            "notes": col("METHODOLOGY_NOTES").str.strip(),
        }
    )
    dropped_missing = int(work["value"].isna().sum())

    # series metadata (first non-empty value per series)
    def first_nonempty(s: pd.Series) -> str:
        for v in s:
            if v and v.upper() not in ("N/A", "NA"):
                return v
        return ""

    meta_cols = ["unit_code", "scale", "last_actual_raw", "reporting_months", "country_update_date", "notes"]
    series = work.groupby(["iso3", "indicator"], sort=True)[meta_cols].agg(first_nonempty).reset_index()
    parsed_la = series["last_actual_raw"].map(parse_last_actual)
    # object dtype keeps None (a float column would turn it into NaN)
    series["last_actual"] = pd.Series([p[0] for p in parsed_la], index=series.index, dtype="object")
    series["fiscal_year"] = pd.Series([p[1] for p in parsed_la], index=series.index, dtype="bool")
    series["country_update_date"] = series["country_update_date"].map(lambda s: parse_date(s) or s)
    series = series[SERIES_COLUMNS]

    obs_raw = Counter(v for v in work["raw_status"] if v)
    unknown = Counter({k: c for k, c in obs_raw.items() if k.upper() not in OBS_STATUS_MAP})
    work = work[work["value"].notna()].copy()
    work["obs_status"] = work["raw_status"].map(lambda s: OBS_STATUS_MAP.get(s.upper(), ""))
    series_unit = {(r.iso3, r.indicator): r.unit_code for r in series.itertuples()}
    work["unit"] = [unit_for(ind, series_unit.get((c, ind), ""), units) for c, ind in zip(work["iso3"], work["indicator"])]
    work["indicator_label"] = work["indicator"].map(lambda i: (labels or {}).get(i) or (WEO_INDICATORS[i].label if i in WEO_INDICATORS else ""))
    work["country"] = work["iso3"].map(lambda c: (country_names or {}).get(c, ""))
    frame = make_frame(work[[c for c in COLUMNS if c in work.columns]], source="imf", dataset=dataset)
    before = len(frame)
    frame = frame.drop_duplicates(subset=["source", "dataset", "indicator", "iso3", "period"], keep="first")
    dups = before - len(frame)

    rules: dict[str, str] = {}
    if flag_forecasts and len(frame):
        la_map = {
            (r.iso3, r.indicator): int(r.last_actual)
            for r in series.itertuples()
            if r.last_actual is not None and not pd.isna(r.last_actual)
        }
        has_la_column = "LATEST_ACTUAL_ANNUAL_DATA" in raw.columns
        if la_map or publication_year is not None or has_la_column or weo_rules:
            frame, rules = apply_forecast_flags(
                frame, la_map, publication_year=publication_year, distinguish_estimates=distinguish_estimates
            )
    frame = frame.sort_values(["indicator", "iso3", "period"]).reset_index(drop=True)
    return ParsedSDMX(
        frame=frame,
        series=series,
        dimensions=dims,
        structure_id=structure_id,
        obs_status_raw=dict(obs_raw),
        unknown_obs_status=dict(unknown),
        dropped_missing=dropped_missing,
        duplicates_dropped=dups,
        publication_date=publication_date,
        forecast_rules=rules,
    )


def parse_sdmx(payload: bytes | str, *, fmt: str = "csv", dataset: str = "", url: str = "", **kwargs: Any) -> ParsedSDMX:
    """Pure: SDMX-CSV/JSON payload -> :class:`ParsedSDMX` (see :func:`raw_to_canonical`)."""
    return raw_to_canonical(read_sdmx(payload, fmt, url), dataset=dataset, **kwargs)


def parse_weo(payload: bytes | str, *, fmt: str = "csv", publication_year: int | None = None, distinguish_estimates: bool = False, country_names: dict[str, str] | None = None, url: str = "") -> ParsedSDMX:
    """Pure: WEO data message -> canonical frame with forecast flags."""
    return parse_sdmx(
        payload,
        fmt=fmt,
        dataset="WEO",
        url=url,
        country_dim="COUNTRY",
        indicator_dims=["INDICATOR"],
        publication_year=publication_year,
        distinguish_estimates=distinguish_estimates,
        country_names=country_names,
        weo_rules=True,
    )


def parse_publication_date(payload: bytes | str, fmt: str = "csv") -> str:
    """ISO PUBLICATION_DATE from an ``attributes=dataset`` probe, or ``""``."""
    raw = read_sdmx(payload, fmt)
    if "PUBLICATION_DATE" not in raw.columns:
        return ""
    for v in raw["PUBLICATION_DATE"].astype(str):
        if parse_date(v):
            return parse_date(v)
    return ""


def structure_version(structure_id: str) -> str:
    """``"IMF.RES:WEO(9.0.0)"`` -> ``"9.0.0"``; ``""`` otherwise."""
    m = _STRUCTURE_ID_RE.match(str(structure_id or "").strip())
    return m.group("version") if m else ""


# -- structure messages -------------------------------------------------------
def _text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("en") or next(iter(value.values()), ""))
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return "" if value is None else str(value)


def parse_dataflows(payload: bytes | str | Any) -> pd.DataFrame:
    """Dataflow catalogue (``data.dataflows``) -> id, agency, version, name,
    structure, last_updated (the ``lastUpdatedAt`` annotation; not reliable
    for WEO release dating)."""
    obj = _decode_json(payload)
    flows = ((obj or {}).get("data") or {}).get("dataflows") or []
    rows = []
    for f in flows:
        ann = {a.get("id"): a.get("value") for a in f.get("annotations") or [] if isinstance(a, dict)}
        rows.append(
            {
                "id": _text(f.get("id")),
                "agency": _text(f.get("agencyID")),
                "version": _text(f.get("version")),
                "name": _text(f.get("name") or f.get("names")),
                "structure": _text(f.get("structure")),
                "last_updated": str(ann.get("lastUpdatedAt") or ""),
            }
        )
    cols = ["id", "agency", "version", "name", "structure", "last_updated"]
    return pd.DataFrame(rows, columns=cols).sort_values(["agency", "id", "version"]).reset_index(drop=True)


def _version_key(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(p) for p in str(v).split("."))
    except ValueError:
        return (0,)


def is_weo_flow(flow_id: str) -> bool:
    """The bare ``WEO`` dataflow or an archival ``WEO_YYYY_MON_VINTAGE`` one."""
    return flow_id == WEO_FLOW or _WEO_VINTAGE_FLOW_RE.match(str(flow_id)) is not None


WEO_VINTAGE_COLUMNS = ["dataflow", "version", "vintage", "label", "publication_date", "label_source", "selected", "latest"]


def weo_vintages_from_flows(flows: pd.DataFrame, publication_dates: dict[tuple[str, str], str] | None = None) -> pd.DataFrame:
    """Every version of the WEO dataflows, labelled by the release it serves.

    ``flows`` is :func:`parse_dataflows` output of the ``detail=allstubs``
    catalogue; ``publication_dates`` maps ``(dataflow, version)`` to the
    PUBLICATION_DATE its probe returned. Labelling follows imf-reader 2.2.0
    (``_fetch_flow_mapping``), because old versions of the bare ``WEO`` flow
    stay live and keep serving their release (April 2025 is ``WEO`` 6.0.0):

    * a probed (flow, version) gets the release of its PUBLICATION_DATE
      (``label_source = "PUBLICATION_DATE"``);
    * an unprobed vintage flow gets the release in its id (``"flow id"``);
    * an unprobed bare ``WEO`` version stays unlabelled (``vintage = ""``).

    ``selected`` marks the (flow, version) that serves each release: a bare
    ``WEO`` flow wins over a vintage flow, then the higher version wins.
    ``latest`` marks the selected row of the newest release. Pure.
    """
    dates = publication_dates or {}
    rows = []
    for r in flows.itertuples():
        if not is_weo_flow(r.id):
            continue
        date = dates.get((r.id, r.version), "")
        v, source = vintage_from_publication_date(date), "PUBLICATION_DATE"
        if v is None:
            date, source = "", ""
            if r.id != WEO_FLOW and (v := vintage_from_flow(r.id)) is not None:
                source = "flow id"
        rows.append(
            {
                "dataflow": r.id,
                "version": r.version,
                "vintage": f"{v[0]}-{v[1]:02d}" if v else "",
                "label": vintage_label(v) if v else "WEO (release unknown)",
                "publication_date": date,
                "label_source": source,
                "selected": False,
                "latest": False,
            }
        )
    df = pd.DataFrame(rows, columns=WEO_VINTAGE_COLUMNS)
    if df.empty:
        return df
    df["_bare"] = df["dataflow"] == WEO_FLOW
    df["_v"] = df["version"].map(_version_key)
    df["_known"] = df["vintage"] != ""
    df = df.sort_values(["_known", "vintage", "_bare", "_v"], ascending=[False, False, False, False]).reset_index(drop=True)
    first = df[df["_known"]].drop_duplicates("vintage", keep="first").index
    df.loc[first, "selected"] = True
    if len(first):
        df.loc[first[0], "latest"] = True
    return df.drop(columns=["_bare", "_v", "_known"])


def weo_flow_mapping(vintages: pd.DataFrame) -> dict[tuple[int, int], tuple[str, str]]:
    """``(year, month) -> (dataflow, version)`` from the ``selected`` rows of
    :func:`weo_vintages_from_flows`. Pure."""
    out: dict[tuple[int, int], tuple[str, str]] = {}
    for r in vintages[vintages["selected"].astype(bool)].itertuples() if len(vintages) else []:
        year, month = (int(x) for x in r.vintage.split("-"))
        out[(year, month)] = (r.dataflow, r.version)
    return out


def parse_codelist(payload: bytes | str | Any) -> pd.DataFrame:
    """Codelist message -> ``code``, ``name`` (English)."""
    obj = _decode_json(payload)
    lists = ((obj or {}).get("data") or {}).get("codelists") or []
    if not lists:
        return pd.DataFrame(columns=["code", "name"])
    codes = lists[-1].get("codes") or []
    rows = [{"code": _text(c.get("id")), "name": _text(c.get("name") or c.get("names"))} for c in codes if c.get("id") is not None]
    return pd.DataFrame(rows, columns=["code", "name"])


# -- DataMapper -----------------------------------------------------------------
_DM_UNITS = {
    "percent of gdp": "% of GDP",
    "annual percent change": "annual % change",
    "percent change": "annual % change",
    "% of gdp": "% of GDP",
}


def parse_datamapper_indicators(payload: bytes | str | Any) -> pd.DataFrame:
    """``/api/v1/indicators`` -> code, label, description, source, unit, dataset."""
    obj = _decode_json(payload)
    inds = (obj or {}).get("indicators") or {}
    rows = []
    for code, meta in inds.items():
        if not isinstance(meta, dict):
            continue
        rows.append({k: str(meta.get(k) or "") for k in ("label", "description", "source", "unit", "dataset")} | {"code": str(code)})
    cols = ["code", "label", "description", "source", "unit", "dataset"]
    return pd.DataFrame(rows, columns=cols).sort_values("code").reset_index(drop=True) if rows else pd.DataFrame(columns=cols)


def parse_datamapper_groups(payload: bytes | str | Any, kind: str = "regions") -> dict[str, str]:
    """``/api/v1/regions`` or ``/groups`` -> ``{code: label}``.

    Assumed envelope ``{"regions": {"SSQ": {"label": "Sub-Saharan Africa (Region)"}, ...}}``,
    by analogy with ``/indicators`` and ``/countries`` (the API help says the keys are
    the ids to use in series requests). UNVERIFIED: live test
    ``test_datamapper_regions_and_groups``."""
    obj = _decode_json(payload)
    if not isinstance(obj, dict):
        return {}
    body = obj.get(kind)
    if not isinstance(body, dict):
        body = next((v for k, v in obj.items() if k != "api" and isinstance(v, dict)), {})
    out: dict[str, str] = {}
    for code, meta in body.items():
        label = meta.get("label") if isinstance(meta, dict) else meta
        out[str(code).upper()] = "" if label is None else str(label)
    return dict(sorted(out.items()))


def parse_datamapper(
    payload: bytes | str | Any,
    *,
    dataset: str = "WEO (DataMapper)",
    labels: dict[str, str] | None = None,
    units: dict[str, str] | None = None,
    countries: Sequence[str] | None = None,
) -> pd.DataFrame:
    """DataMapper series JSON ``{"values": {IND: {ISO: {"YYYY": value}}}}`` ->
    canonical frame (obs_status left empty; see :func:`apply_forecast_flags`)."""
    obj = _decode_json(payload)
    values = (obj or {}).get("values") or {}
    keep = {c.upper() for c in countries} if countries else None
    records = []
    for ind, by_country in values.items():
        if not isinstance(by_country, dict):
            continue
        for iso, by_year in by_country.items():
            if not isinstance(by_year, dict) or (keep is not None and str(iso).upper() not in keep):
                continue
            for year, val in by_year.items():
                try:
                    num = float(val)
                except (TypeError, ValueError):
                    continue
                records.append(
                    {
                        "indicator": str(ind),
                        "indicator_label": (labels or {}).get(ind) or (WEO_INDICATORS[ind].label if ind in WEO_INDICATORS else ""),
                        "iso3": str(iso).upper(),
                        "period": normalize_period(year),
                        "value": num,
                        "unit": (units or {}).get(ind) or unit_for(str(ind)),
                        "obs_status": "",
                    }
                )
    if not records:
        return empty_frame()
    frame = make_frame(records, source="imf", dataset=dataset)
    frame = frame.drop_duplicates(subset=["source", "dataset", "indicator", "iso3", "period"])
    return frame.sort_values(["indicator", "iso3", "period"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# caveats
# ---------------------------------------------------------------------------
def terms_caveat() -> Caveat:
    return Caveat("IMF_TERMS", "info", IMF_TERMS_NOTE, {"terms": IMF_TERMS_URL})


def forecast_caveats(frame: pd.DataFrame, series: pd.DataFrame, rules: dict[str, str], publication_year: int | None) -> list[Caveat]:
    """WEO_FORECAST_YEARS / WEO_ESTIMATE_YEARS / WEO_FORECAST_BOUNDARY_APPROX /
    WEO_FORECAST_BOUNDARY_UNKNOWN / WEO_FISCAL_YEAR."""
    out: list[Caveat] = []
    if frame.empty:
        return out
    for status, code, severity, text in (
        ("forecast", "WEO_FORECAST_YEARS", "warn", "Contains IMF staff projections (years after the last actual year, 'Estimates Start After'). Mark them as projections in charts."),
        ("estimate", "WEO_ESTIMATE_YEARS", "info", "Contains IMF staff estimates for past years after the last actual year."),
    ):
        sel = frame[frame["obs_status"] == status]
        if len(sel):
            first = sel.groupby(["iso3", "indicator"])["period"].min()
            evidence = {f"{i}/{k}": p for (i, k), p in list(first.items())[:_EVIDENCE_CAP]}
            out.append(Caveat(code, severity, text, {"first_period": evidence, "rows": int(len(sel)), "publication_year": publication_year}))
    approx = sorted(k for k, r in rules.items() if r == "publication_year")
    if approx:
        out.append(
            Caveat(
                "WEO_FORECAST_BOUNDARY_APPROX",
                "warn",
                "No 'Estimates Start After' year for some series; years from the publication year on are flagged as "
                "forecasts, but earlier years may also be staff estimates.",
                {"series": approx[:_EVIDENCE_CAP], "publication_year": publication_year},
            )
        )
    unknown = sorted(k for k, r in rules.items() if r == "none")
    if unknown:
        out.append(
            Caveat(
                "WEO_FORECAST_BOUNDARY_UNKNOWN",
                "warn",
                "Some series have neither an 'Estimates Start After' year nor a known publication year, so none of "
                "their years could be flagged as projections (obs_status left empty). Treat their recent years as "
                "possible projections; WEO aggregates carry no 'Estimates Start After'.",
                {"series": unknown[:_EVIDENCE_CAP], "count": len(unknown)},
            )
        )
    if "fiscal_year" in series.columns and series["fiscal_year"].any():
        fy = series[series["fiscal_year"]]
        out.append(
            Caveat(
                "WEO_FISCAL_YEAR",
                "info",
                "Some series report on a fiscal-year basis ('FY' last actual); the leading year is used as the "
                "last actual year. Check START_END_MONTHS_OF_REPORTING_YEAR before comparing with calendar-year data.",
                {f"{r.iso3}/{r.indicator}": {"last_actual_raw": r.last_actual_raw, "reporting_months": r.reporting_months} for r in fy.head(_EVIDENCE_CAP).itertuples()},
            )
        )
    return out


def missing_series(frame: pd.DataFrame, countries: Sequence[str] | None, indicators: Sequence[str]) -> list[str]:
    if not countries:
        return [i for i in indicators if frame.empty or i not in set(frame["indicator"])]
    present = set(zip(frame["iso3"], frame["indicator"])) if len(frame) else set()
    return [f"{c}/{i}" for c in countries for i in indicators if (c, i) not in present]


def filter_years(df: pd.DataFrame, start: int | str | None, end: int | str | None) -> pd.DataFrame:
    if df.empty or (start is None and end is None):
        return df
    years = df["period"].map(year_of)
    keep = pd.Series(True, index=df.index)
    if start is not None:
        keep &= years.map(lambda y: y is not None and y >= int(str(start)[:4]))
    if end is not None:
        keep &= years.map(lambda y: y is not None and y <= int(str(end)[:4]))
    return df[keep].reset_index(drop=True)


def _scale_meta(series: pd.DataFrame) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in series.itertuples():
        if r.scale and r.indicator not in out:
            out[r.indicator] = r.scale
    return dict(sorted(out.items()))


#: max series-metadata records kept in Result.meta (keeps MCP summaries small)
SERIES_META_CAP = 250


def _series_records(series: pd.DataFrame, cap: int = SERIES_META_CAP) -> list[dict]:
    recs = []
    for r in series.head(cap).itertuples():
        recs.append(
            {
                "iso3": r.iso3,
                "indicator": r.indicator,
                "last_actual": None if r.last_actual is None or pd.isna(r.last_actual) else int(r.last_actual),
                "last_actual_raw": r.last_actual_raw,
                "fiscal_year": bool(r.fiscal_year),
                "reporting_months": r.reporting_months,
                "country_update_date": r.country_update_date,
                "unit_code": r.unit_code,
                "scale": r.scale,
            }
        )
    return recs


# ---------------------------------------------------------------------------
# connector
# ---------------------------------------------------------------------------
class IMF(Connector):
    """IMF data: WEO (latest and vintage dataflows), BOP and any SDMX 3.0
    dataflow on api.imf.org; DataMapper fallback for the latest WEO."""

    source = "imf"
    license = (
        "IMF Copyright and Usage terms (attribution required; systematic downloading or commercial reuse "
        "needs written permission from copyright@imf.org)"
    )
    citation = "IMF, World Economic Outlook database"
    domains = ("api.imf.org", "www.imf.org")

    def __init__(self, cache: Cache | None = None):
        super().__init__(cache)
        self._agencies: dict[str, str] = {}

    # -- plumbing -----------------------------------------------------------------
    def _get(self, url: str, params: dict | None = None, headers: dict | None = None, refresh: bool = False) -> tuple[bytes, Retrieval]:
        try:
            return http.fetch(self.source, url, params or {}, headers=headers, cache=self.cache, refresh=refresh)
        except http.FetchError as exc:
            raise IMFAPIError.from_fetch_error(exc, url) from exc

    def _prov(self, retrieval: Retrieval, *, dataset: str, vintage: str, citation: str, tool: str, notes: list[str] | None = None) -> Provenance:
        prov = self.provenance(retrieval, dataset=dataset, vintage=vintage, tool=tool, notes=[*(notes or []), IMF_TERMS_NOTE])
        prov.citation = citation
        return prov

    # -- catalogue and codelists -----------------------------------------------------
    def list_dataflows(self, agency: str = "all", refresh: bool = False) -> pd.DataFrame:
        """Dataflow catalogue (latest version of each flow)."""
        url, params, headers = build_dataflows_request(agency)
        payload, retrieval = self._get(url, params, headers, refresh)
        df = parse_dataflows(payload)
        df.attrs["provenance"] = [self._prov(retrieval, dataset="IMF catalogue", vintage="", citation="IMF Data API", tool="IMF.list_dataflows").to_dict()]
        return df

    def list_weo_vintages(self, refresh: bool = False) -> pd.DataFrame:
        """Every version of the WEO dataflows (bare ``WEO`` and
        ``WEO_YYYY_MON_VINTAGE``) with the release each serves, read from the
        ``detail=allstubs`` catalogue of IMF.RES and a PUBLICATION_DATE probe
        per (flow, version); see :func:`weo_vintages_from_flows`.

        ``attrs["provenance"]`` holds the catalogue and probe records and
        ``attrs["probe_failures"]`` the (flow, version) probes that failed."""
        table, cat_prov, probe_provs, failures = self._weo_vintage_table(refresh)
        table.attrs["provenance"] = [cat_prov.to_dict()] + [p.to_dict() for p in probe_provs.values()]
        table.attrs["probe_failures"] = {f"{f}/{v}": str(e)[:200] for (f, v), e in failures.items()}
        return table

    def _weo_vintage_table(
        self, refresh: bool = False
    ) -> tuple[pd.DataFrame, Provenance, dict[tuple[str, str], Provenance], dict[tuple[str, str], Exception]]:
        """Catalogue of WEO flows labelled by PUBLICATION_DATE probes (all cached)."""
        url, params, headers = build_weo_vintages_request()
        payload, retrieval = self._get(url, params, headers, refresh)
        flows = parse_dataflows(payload)
        cat_prov = self._prov(retrieval, dataset="IMF catalogue", vintage="", citation="IMF Data API", tool="IMF.list_weo_vintages")
        dates: dict[tuple[str, str], str] = {}
        probe_provs: dict[tuple[str, str], Provenance] = {}
        failures: dict[tuple[str, str], Exception] = {}
        for r in flows.itertuples():
            if not is_weo_flow(r.id) or not r.version:
                continue
            ref = (r.id, r.version)
            try:
                date, probe_ret = self.weo_publication_date(r.id, refresh, version=r.version)
            except (http.NetworkBlocked, IMFAPIError, IMFResponseError) as exc:
                failures[ref] = exc
                continue
            if not date:
                failures[ref] = IMFResponseError(f"no PUBLICATION_DATE in the probe of {r.id} {r.version}")
                continue
            dates[ref] = date
            probe_provs[ref] = self._prov(
                probe_ret, dataset="WEO", vintage=vintage_label(vintage_from_publication_date(date)),
                citation=FLOW_CITATIONS["WEO"], tool="IMF.weo_publication_date", notes=[f"dataflow {WEO_AGENCY}:{r.id} version {r.version}"],
            )
        return weo_vintages_from_flows(flows, dates), cat_prov, probe_provs, failures

    def fetch_codelist(self, codelist_id: str, agency: str = "IMF", refresh: bool = False) -> pd.DataFrame:
        """Codes and English names, e.g. ``("CL_WEO_COUNTRY", "IMF.RES")``."""
        url, params, headers = build_codelist_request(codelist_id, agency)
        payload, retrieval = self._get(url, params, headers, refresh)
        df = parse_codelist(payload)
        df.attrs["provenance"] = [self._prov(retrieval, dataset=f"codelist {agency}:{codelist_id}", vintage="", citation="IMF Data API", tool="IMF.fetch_codelist").to_dict()]
        return df

    def resolve_agency(self, flow: str, refresh: bool = False) -> str:
        """Agency of a dataflow: static table, vintage-flow base, then the catalogue."""
        flow = str(flow).strip()
        if flow in KNOWN_AGENCIES:
            return KNOWN_AGENCIES[flow]
        m = _VINTAGE_FLOW_RE.match(flow)
        if m and m.group("base") in KNOWN_AGENCIES:
            return KNOWN_AGENCIES[m.group("base")]
        if flow in self._agencies:
            return self._agencies[flow]
        cat = self.list_dataflows(refresh=refresh)
        self._agencies.update(dict(zip(cat["id"], cat["agency"])))
        if flow not in self._agencies:
            raise ValueError(f"unknown IMF dataflow {flow!r}; see IMF.list_dataflows() or pass agency=")
        return self._agencies[flow]

    def weo_publication_date(self, flow: str = WEO_FLOW, refresh: bool = False, *, version: str = "+") -> tuple[str, Retrieval]:
        """PUBLICATION_DATE of a WEO flow at ``version`` (ISO date, ``""`` when absent)."""
        url, params, headers = build_publication_probe(flow, version)
        payload, retrieval = self._get(url, params, headers, refresh)
        return parse_publication_date(payload), retrieval

    # -- WEO ---------------------------------------------------------------------------
    def fetch(self, indicators: Sequence[str] | str, countries: Sequence[str] | str | None = None, **kwargs: Any) -> Result:
        """Alias of :meth:`fetch_weo`."""
        return self.fetch_weo(indicators, countries, **kwargs)

    def fetch_weo(
        self,
        indicators: Sequence[str] | str,
        countries: Sequence[str] | str | None = None,
        vintage: Any = None,
        start: int | str | None = None,
        end: int | str | None = None,
        *,
        fallback: bool = True,
        distinguish_estimates: bool = False,
        labels: bool = False,
        refresh: bool = False,
    ) -> Result:
        """World Economic Outlook series as a canonical :class:`Result`.

        Args:
            indicators: WEO codes (``"GGXWDG_NGDP"``) or aliases (``"gross_debt"``).
            countries: ISO3 codes and IMF aggregate codes (``"G001"``); ``None``
                for every economy.
            vintage: ``None``/``"latest"`` or a release such as ``"2025-10"``,
                ``"Oct 2025"``, ``("October", 2025)``. A named release is read
                from the explicit (dataflow, version) that serves it, found
                with :meth:`resolve_weo_vintage` (catalogue + PUBLICATION_DATE
                probes): an archival ``WEO_YYYY_MON_VINTAGE`` flow, or an older
                version of the bare ``WEO`` flow (April 2025 is ``WEO`` 6.0.0).
            start, end: years, applied client side.
            fallback: when the SDMX API cannot be used and ``vintage`` is None,
                read the DataMapper API instead (vintage unpinned; caveat).
            distinguish_estimates: years after the last actual but before the
                publication year get ``obs_status="estimate"`` instead of
                ``"forecast"``.
            labels: fetch CL_WEO_COUNTRY / CL_WEO_INDICATOR for names.
            refresh: bypass the cache (a pinned snapshot still wins).
        """
        inds = normalize_indicators(indicators)
        ctys = normalize_countries(countries)
        req_vintage = parse_vintage(vintage)
        try:
            return self._fetch_weo_sdmx(inds, ctys, req_vintage, start, end, distinguish_estimates, labels, refresh)
        except (http.NetworkBlocked, IMFAPIError, IMFResponseError) as exc:
            # The DataMapper serves only the current release, so it never
            # substitutes for an explicitly requested vintage.
            if not fallback or req_vintage is not None:
                raise
            # IMF SDMX aggregates (G001, GX1213, ...) are not DataMapper codes; the
            # DataMapper has its own regions and groups (SSQ, ADVEC, ...).
            sdmx_aggs = [c for c in ctys if not _ISO3_RE.match(c)] if ctys else []
            dm_ctys = [c for c in ctys if c not in sdmx_aggs] if ctys else None
            if ctys and not dm_ctys:
                raise
            try:
                res = self.fetch_datamapper(inds, dm_ctys, start, end, refresh=refresh)
            except Exception as dm_exc:
                raise dm_exc from exc
            if sdmx_aggs:
                res.meta["missing"] = res.meta["missing"] + [f"{c}/{i}" for c in sdmx_aggs for i in inds]
                res.add_caveat(
                    Caveat(
                        "IMF_FALLBACK_AGGREGATES_DROPPED",
                        "warn",
                        "IMF SDMX aggregate codes (G-codes such as G001 World) do not exist in the DataMapper, which uses "
                        "its own region and group codes (e.g. SSQ, ADVEC); these aggregates are not in this result.",
                        {"dropped": sdmx_aggs},
                    )
                )
            res.add_caveat(
                Caveat(
                    "IMF_FALLBACK_DATAMAPPER",
                    "warn",
                    "The IMF SDMX API could not be used; values come from the DataMapper API (latest WEO only, "
                    "no 'Estimates Start After' flags, vintage not pinned by the provider).",
                    {"reason": f"{exc.__class__.__name__}: {str(exc)[:300]}"},
                )
            )
            res.meta["fallback_reason"] = f"{exc.__class__.__name__}: {str(exc)[:300]}"
            return res

    def resolve_weo_vintage(self, vintage: Any, refresh: bool = False) -> dict[str, Any]:
        """The (dataflow, version) that serves a WEO release, the way imf-reader resolves it.

        Reads the ``detail=allstubs`` catalogue, probes PUBLICATION_DATE of each
        WEO (flow, version) and picks the selected row for the release (bare
        ``WEO`` over vintage flows, then the higher version). Returns
        ``{"vintage", "dataflow", "version", "publication_date", "label_source",
        "provenance": [Provenance...], "probe_failures": {...}, "available": [...]}``.
        Raises :class:`IMFAPIError` (404) when no flow serves the release, or
        ``http.NetworkBlocked`` when it could not be found because probes could
        not reach the API.
        """
        req = parse_vintage(vintage)
        if req is None:
            raise ValueError("resolve_weo_vintage needs an explicit release such as '2025-04'")
        table, cat_prov, probe_provs, failures = self._weo_vintage_table(refresh)
        mapping = weo_flow_mapping(table)
        failed = {f"{f}/{v}": f"{e.__class__.__name__}: {str(e)[:150]}" for (f, v), e in failures.items()}
        available = [vintage_label(v) for v in sorted(mapping, reverse=True)]
        if req not in mapping:
            msg = (
                f"WEO vintage {vintage_label(req)} is not served by any WEO dataflow in the IMF.RES catalogue "
                f"(available: {', '.join(available) or 'none'}; the API serves April 2025 onward)"
            )
            if failed:
                msg += f"; PUBLICATION_DATE probes failed for {sorted(failed)}, so their releases are unknown"
            if any(isinstance(e, http.NetworkBlocked) for e in failures.values()):
                raise http.NetworkBlocked(msg)
            url, _, _ = build_weo_vintages_request()
            raise IMFAPIError(404, msg, url)
        flow, version = mapping[req]
        row = table[(table["dataflow"] == flow) & (table["version"] == version)].iloc[0]
        # In a data result the catalogue record documents how the release was found;
        # it carries the WEO citation so that chart source lines name the release only.
        cat_prov = replace(
            cat_prov,
            dataset="WEO",
            vintage=vintage_label(req),
            citation=FLOW_CITATIONS["WEO"],
            notes=[f"dataflow catalogue used to resolve {vintage_label(req)} to {WEO_AGENCY}:{flow} version {version}", *cat_prov.notes],
        )
        provs = [cat_prov] + ([probe_provs[(flow, version)]] if (flow, version) in probe_provs else [])
        return {
            "vintage": req,
            "dataflow": flow,
            "version": version,
            "publication_date": str(row["publication_date"]),
            "label_source": str(row["label_source"]),
            "provenance": provs,
            "probe_failures": failed,
            "available": available,
        }

    def _country_names(self, refresh: bool) -> tuple[dict[str, str], dict[str, str], list[Caveat]]:
        try:
            countries = self.fetch_codelist("CL_WEO_COUNTRY", WEO_AGENCY, refresh=refresh)
            indicators = self.fetch_codelist("CL_WEO_INDICATOR", WEO_AGENCY, refresh=refresh)
            return dict(zip(countries["code"], countries["name"])), dict(zip(indicators["code"], indicators["name"])), []
        except (http.NetworkBlocked, IMFAPIError, IMFResponseError) as exc:
            return {}, {}, [Caveat("IMF_LABELS_UNAVAILABLE", "info", "WEO codelists could not be read; names left empty.", {"reason": str(exc)[:200]})]

    def _fetch_weo_sdmx(
        self,
        inds: list[str],
        ctys: list[str] | None,
        req_vintage: tuple[int, int] | None,
        start: int | str | None,
        end: int | str | None,
        distinguish_estimates: bool,
        labels: bool,
        refresh: bool,
    ) -> Result:
        provs: list[Provenance] = []
        caveats: list[Caveat] = [terms_caveat()]
        urls: list[str] = []
        flow, version = WEO_FLOW, "+"
        pub_date = ""
        resolved: tuple[int, int] | None = req_vintage
        resolution: dict[str, Any] = {}

        if req_vintage is not None:
            # A past (or the current) release: find the (dataflow, version) that
            # serves it. Old versions of the bare WEO flow keep serving their
            # release, so the conventional WEO_YYYY_MON_VINTAGE name is not enough.
            info = self.resolve_weo_vintage(req_vintage, refresh)
            flow, version, pub_date = info["dataflow"], info["version"], info["publication_date"]
            provs.extend(info["provenance"])
            resolution = {k: info[k] for k in ("dataflow", "version", "publication_date", "label_source", "probe_failures", "available")}
        url, params, headers = build_weo_request(inds, ctys, flow=flow, version=version)
        payload, retrieval = self._get(url, params, headers, refresh)
        urls.append(url)
        if req_vintage is None:
            try:
                pub_date, probe_ret = self.weo_publication_date(WEO_FLOW, refresh)
                resolved = vintage_from_publication_date(pub_date)
                provs.append(self._prov(probe_ret, dataset="WEO", vintage=vintage_label(resolved), citation=FLOW_CITATIONS["WEO"], tool="IMF.weo_publication_date"))
            except (http.NetworkBlocked, IMFAPIError, IMFResponseError) as exc:
                caveats.append(Caveat("IMF_VINTAGE_UNRESOLVED", "warn", "The release held by the latest WEO flow could not be determined (PUBLICATION_DATE probe failed); vintage left empty.", {"reason": str(exc)[:200]}))
                resolved = None
        names, ind_names, label_caveats = self._country_names(refresh) if labels else ({}, {}, [])
        caveats.extend(label_caveats)
        pub_year = resolved[0] if resolved else None
        parsed = parse_weo(payload, publication_year=pub_year, distinguish_estimates=distinguish_estimates, country_names=names, url=url)
        if not pub_date and parsed.publication_date:
            pub_date = parsed.publication_date
        frame = filter_years(parsed.frame, start, end)
        if ind_names and len(frame):
            frame["indicator_label"] = [l or ind_names.get(i, "") for l, i in zip(frame["indicator_label"], frame["indicator"])]
        validate_frame(frame)

        label = vintage_label(resolved)
        sv = structure_version(parsed.structure_id) or ("" if version == "+" else version)
        notes = [f"dataflow {WEO_AGENCY}:{flow}" + (f" version {sv}" if sv else "")]
        if pub_date:
            notes.append(f"PUBLICATION_DATE {pub_date}")
        if resolution:
            notes.append(f"release resolved from the IMF.RES dataflow catalogue ({resolution['label_source'] or 'unlabelled'})")
        provs.insert(0, self._prov(retrieval, dataset="WEO", vintage=label, citation=FLOW_CITATIONS["WEO"], tool="IMF.fetch_weo", notes=notes))

        if req_vintage is None and label:
            caveats.append(
                Caveat(
                    "IMF_VINTAGE_LATEST",
                    "info",
                    f"Requested the latest WEO; resolved to {label} from PUBLICATION_DATE. Pin it with vintage='{resolved[0]}-{resolved[1]:02d}' "
                    "(resolved through the dataflow catalogue to an explicit dataflow version) or with CGJRKIT_SNAPSHOT.",
                    {"publication_date": pub_date, "dataflow": flow},
                )
            )
        caveats.extend(forecast_caveats(frame, parsed.series, parsed.forecast_rules, pub_year))
        caveats.extend(self._common_caveats(frame, parsed, ctys, inds))
        meta = self._meta(frame, parsed, urls, flow=flow, agency=WEO_AGENCY, vintage=label, publication_date=pub_date, missing=missing_series(frame, ctys, inds))
        meta["flow_version"] = version
        if resolution:
            meta["vintage_resolution"] = resolution
        return Result(data=frame, provenance=provs, caveats=caveats, meta=meta)

    # -- generic SDMX --------------------------------------------------------------------
    def fetch_sdmx(
        self,
        dataflow: str,
        key: str | Sequence[Sequence[str] | str | None] = "*",
        params: dict | None = None,
        *,
        agency: str | None = None,
        version: str = "+",
        start: int | str | None = None,
        end: int | str | None = None,
        fmt: str = "csv",
        country_dim: str | None = None,
        indicator_dims: Sequence[str] | None = None,
        labels: dict[str, str] | None = None,
        units: dict[str, str] | None = None,
        server_time_filter: bool = True,
        refresh: bool = False,
    ) -> Result:
        """Any SDMX 3.0 dataflow as a canonical :class:`Result`.

        Args:
            dataflow: e.g. ``"BOP"``, ``"WEO"``, ``"WEO_2025_OCT_VINTAGE"``, ``"FM"``.
            key: dot-separated key in DSD order (``"KEN.NETCD_T.CAB.USD.A"``;
                ``+`` for several codes, ``*`` for all), or a list of slots.
            params: extra query parameters (passed through).
            agency: maintenance agency (``IMF.STA``...); looked up when omitted.
            version: dataflow version (``+`` = latest).
            start, end: years. Sent as ``c[TIME_PERIOD]`` for ``IMF.STA`` flows
                (when ``server_time_filter``) and always applied client side.
            fmt: ``"csv"`` (SDMX-CSV, default) or ``"json"`` (SDMX-JSON 2.0).
            country_dim / indicator_dims: override the dimension mapping.
            labels / units: indicator -> label/unit overrides.
        """
        flow = str(dataflow).strip()
        key_str = key if isinstance(key, str) else build_key(*key)
        if not re.fullmatch(r"[A-Za-z0-9_@*+.\-]*", key_str or ""):
            raise ValueError(f"invalid SDMX key {key_str!r}")
        ag = agency or self.resolve_agency(flow, refresh)
        q = dict(params or {})
        if server_time_filter and ag == "IMF.STA" and (start is not None or end is not None) and "c[TIME_PERIOD]" not in q:
            last = (key_str or "").split(".")[-1]
            q.update(time_filter_param(start, end, last if last in ("A", "Q", "M") else None))
        url, qp, headers = build_data_request(ag, flow, key_str or "*", version=version, params=q, fmt=fmt)
        payload, retrieval = self._get(url, qp, headers, refresh)
        is_weo = flow == WEO_FLOW or flow.startswith("WEO_")
        fv = vintage_from_flow(flow) if is_weo else None
        raw = read_sdmx(payload, fmt, url)
        pub_year = fv[0] if fv else None
        if is_weo and pub_year is None and "PUBLICATION_DATE" in raw.columns:
            pv = vintage_from_publication_date(next((v for v in raw["PUBLICATION_DATE"] if parse_date(v)), ""))
            pub_year = pv[0] if pv else None
        parsed = raw_to_canonical(
            raw,
            dataset="WEO" if is_weo else flow,
            country_dim=country_dim,
            indicator_dims=indicator_dims or (["INDICATOR"] if is_weo else None),
            labels=labels,
            units=units,
            publication_year=pub_year,
            weo_rules=is_weo,
        )
        frame = filter_years(parsed.frame, start, end)
        validate_frame(frame)
        base = _VINTAGE_FLOW_RE.match(flow).group("base") if _VINTAGE_FLOW_RE.match(flow) else flow
        citation = FLOW_CITATIONS.get(base, f"IMF, {flow} dataset")
        sv = structure_version(parsed.structure_id) or ("" if version == "+" else version)
        # The dataflow version is a structure version, not a data release (data
        # are revised in place within a version), so it goes in the notes only.
        if is_weo and fv:
            vint = vintage_label(fv)
        elif is_weo and parsed.publication_date:
            vint = vintage_label(vintage_from_publication_date(parsed.publication_date))
        elif parsed.publication_date:
            vint = f"{flow} published {parsed.publication_date}"
        else:
            vint = ""
        notes = [f"dataflow {ag}:{flow}" + (f" version {sv}" if sv else ""), f"key {key_str}"]
        prov = self._prov(retrieval, dataset="WEO" if is_weo else flow, vintage=vint, citation=citation, tool="IMF.fetch_sdmx", notes=notes)
        caveats = [terms_caveat()]
        if version == "+" and not fv:
            caveats.append(
                Caveat(
                    "IMF_VINTAGE_LATEST",
                    "info",
                    "Latest version of the dataflow; the provider revises data in place. Pin results with CGJRKIT_SNAPSHOT.",
                    {"dataflow": flow, "structure_version": sv},
                )
            )
        caveats.extend(forecast_caveats(frame, parsed.series, parsed.forecast_rules, pub_year))
        caveats.extend(self._common_caveats(frame, parsed, None, []))
        meta = self._meta(frame, parsed, [url], flow=flow, agency=ag, vintage=vint, publication_date=parsed.publication_date, missing=[] if len(frame) else [key_str])
        if frame.empty:
            caveats.append(Caveat("IMF_NO_DATA", "warn", "The query matched no observations (unknown codes return an empty message, not an error).", {"dataflow": flow, "key": key_str}))
        return Result(data=frame, provenance=[prov], caveats=caveats, meta=meta)

    def fetch_bop(
        self,
        items: Sequence[str] | str,
        countries: Sequence[str] | str,
        start: int | str | None = None,
        end: int | str | None = None,
        *,
        unit: str = "USD",
        freq: str = "A",
        refresh: bool = False,
    ) -> Result:
        """Balance-of-payments items (``BOP_ITEMS`` names, or ``"ENTRY.INDICATOR"``
        pairs) from IMF.STA ``BOP``, in base units of ``unit`` (default US$).

        One request covers all items and countries; series outside the requested
        (entry, indicator) pairs are dropped. Canonical ``indicator`` is
        ``ENTRY.INDICATOR.UNIT`` (e.g. ``NETCD_T.CAB.USD``).
        """
        pairs: list[tuple[str, str, str]] = []
        for item in _as_list(items, seps=r"[;,\s]+"):
            if item in BOP_ITEMS:
                pairs.append(BOP_ITEMS[item])
            elif re.fullmatch(r"[A-Z0-9_]+\.[A-Z0-9_]+", item):
                entry, ind = item.split(".")
                pairs.append((entry, ind, ""))
            else:
                raise ValueError(f"unknown BOP item {item!r}; use one of {sorted(BOP_ITEMS)} or 'ENTRY.INDICATOR'")
        ctys = normalize_countries(countries)
        if not pairs:
            raise ValueError("at least one BOP item is required")
        entries = list(dict.fromkeys(p[0] for p in pairs))
        inds = list(dict.fromkeys(p[1] for p in pairs))
        key = build_key(ctys, entries, inds, unit, freq)
        wanted = {f"{e}.{i}.{unit}": (lbl or f"{e}.{i}") for e, i, lbl in pairs}
        res = self.fetch_sdmx(
            "BOP", key, agency="IMF.STA", start=start, end=end, labels=wanted,
            units={k: UNIT_LABELS.get(unit, unit) for k in wanted}, refresh=refresh,
        )
        df = res.data[res.data["indicator"].isin(wanted)].reset_index(drop=True)
        res.data = df
        res.meta["indicators"] = dict(sorted({k: v for k, v in wanted.items()}.items()))
        res.meta["missing"] = missing_series(df, ctys, list(wanted))
        if res.meta["missing"]:
            res.add_caveat(Caveat("IMF_MISSING_SERIES", "warn", "Some requested BOP series returned no data.", {"missing": res.meta["missing"][:_EVIDENCE_CAP]}))
        if any(e == "NNAFANIL_T" for e, _, _ in pairs):
            res.add_caveat(
                Caveat(
                    "BOP_BPM6_SIGN",
                    "info",
                    "Financial-account items are BPM6 net acquisition of assets less net incurrence of liabilities: a "
                    "positive value is a net outflow. Flip the sign to chart net inflows (e.g. net FDI).",
                    {"entries": ["NNAFANIL_T"]},
                )
            )
        res.add_caveat(
            Caveat(
                "BOP_BASE_UNITS",
                "info",
                f"Values are in base units of {unit} (the SCALE attribute is informational). Divide by GDP in the same "
                "currency (e.g. WEO NGDPD) for ratios.",
                {"scale": res.meta.get("scale", {})},
            )
        )
        res.provenance[0].tool = "IMF.fetch_bop"
        return res

    # -- DataMapper ----------------------------------------------------------------------
    def fetch_datamapper_indicators(self, refresh: bool = False) -> pd.DataFrame:
        url, params = build_datamapper_indicators_request()
        payload, retrieval = self._get(url, params, {"Accept": JSON_ACCEPT}, refresh)
        df = parse_datamapper_indicators(payload)
        df.attrs["provenance"] = [self._prov(retrieval, dataset="DataMapper indicators", vintage="", citation="IMF DataMapper", tool="IMF.fetch_datamapper_indicators").to_dict()]
        return df

    def fetch_datamapper_groups(self, refresh: bool = False) -> dict[str, str]:
        """DataMapper aggregate codes and labels from ``/regions`` and ``/groups`` (cached)."""
        out: dict[str, str] = {}
        for kind in ("regions", "groups"):
            url, params = build_datamapper_groups_request(kind)
            payload, _ = self._get(url, params, {"Accept": JSON_ACCEPT}, refresh)
            out.update(parse_datamapper_groups(payload, kind))
        return out

    def fetch_datamapper(
        self,
        indicators: Sequence[str] | str,
        countries: Sequence[str] | str | None = None,
        start: int | str | None = None,
        end: int | str | None = None,
        *,
        with_metadata: bool = True,
        refresh: bool = False,
    ) -> Result:
        """Latest-WEO values from the DataMapper API (one request per indicator).

        The DataMapper serves only the current release and exposes no
        "Estimates Start After" field, so forecasts are flagged from the
        publication year: from the indicator metadata ``source`` when it names
        a release, else inferred from the retrieval date with
        :func:`infer_release_from_date` (conservative). A vintage caveat is
        always attached. Aggregates (``meta["aggregates"]``) are non-ISO codes,
        the DataMapper regions, and, with ``with_metadata``, every code in the
        live ``/regions`` and ``/groups`` lists.
        """
        inds = normalize_indicators(indicators)
        ctys = normalize_countries(countries)
        provs: list[Provenance] = []
        caveats: list[Caveat] = [terms_caveat()]
        meta_df = pd.DataFrame(columns=["code", "label", "description", "source", "unit", "dataset"])
        if with_metadata:
            try:
                meta_df = self.fetch_datamapper_indicators(refresh)
            except (http.NetworkBlocked, IMFAPIError, IMFResponseError) as exc:
                caveats.append(Caveat("IMF_LABELS_UNAVAILABLE", "info", "DataMapper indicator metadata could not be read.", {"reason": str(exc)[:200]}))
        meta_by = {r.code: r for r in meta_df.itertuples()}
        dm_groups: dict[str, str] = {}
        if with_metadata:
            try:
                dm_groups = self.fetch_datamapper_groups(refresh)
            except (http.NetworkBlocked, IMFAPIError, IMFResponseError) as exc:
                caveats.append(Caveat("IMF_DM_GROUPS_UNAVAILABLE", "info", "DataMapper region/group lists could not be read; aggregates are classified from a static list.", {"reason": str(exc)[:200]}))
        frames, urls, retrieved = [], [], []
        inferred: tuple[int, int] | None = None
        for ind in inds:
            url, params = build_datamapper_request(ind, ctys)
            payload, retrieval = self._get(url, params, {"Accept": JSON_ACCEPT}, refresh)
            m = meta_by.get(ind)
            ds = f"{m.dataset} (DataMapper)" if m is not None and m.dataset else "WEO (DataMapper)"
            unit = _DM_UNITS.get(str(m.unit).strip().lower(), str(m.unit)) if m is not None and m.unit else unit_for(ind)
            if ind in WEO_INDICATORS:
                unit = WEO_INDICATORS[ind].unit
            label = (WEO_INDICATORS[ind].label if ind in WEO_INDICATORS else "") or (m.label if m is not None else "")
            frames.append(parse_datamapper(payload, dataset=ds, labels={ind: label}, units={ind: unit}, countries=ctys))
            if m is not None and inferred is None:
                inferred = infer_vintage_from_text(m.source)
            urls.append(url)
            retrieved.append(retrieval)
        non_empty = [f for f in frames if len(f)]
        frame = pd.concat(non_empty, ignore_index=True) if non_empty else empty_frame()
        from_date = None if inferred or not retrieved else infer_release_from_date(retrieved[0].retrieved_at)
        pub_year = inferred[0] if inferred else (from_date[0] if from_date else None)
        frame, rules = apply_forecast_flags(frame, {}, publication_year=pub_year) if len(frame) else (frame, {})
        frame = filter_years(frame, start, end)
        frame = self.finalize(frame) if len(frame) else frame
        validate_frame(frame)
        vint = (vintage_label(inferred) + " (DataMapper, unpinned)") if inferred else ""
        for retrieval in retrieved:
            provs.append(self._prov(retrieval, dataset="WEO (DataMapper)", vintage=vint, citation="IMF, World Economic Outlook (via IMF DataMapper)", tool="IMF.fetch_datamapper", notes=["DataMapper serves the latest WEO only; release not pinned by the provider"]))
        if inferred:
            how = f" (metadata names {vintage_label(inferred)})"
        elif from_date:
            how = (
                f" and the metadata does not name it; the forecast boundary uses publication year {pub_year}, inferred "
                f"from the retrieval date ({retrieved[0].retrieved_at[:10]}): a release is assumed out only after its "
                f"month ends, so {vintage_label(from_date)} is assumed"
            )
        else:
            how = " and could not be identified"
        caveats.append(
            Caveat(
                "IMF_VINTAGE_UNPINNED",
                "warn",
                "DataMapper values are the latest WEO release, which changes in place twice a year; the vintage is not "
                "pinned by the provider" + how + ". Pin with CGJRKIT_SNAPSHOT.",
                {
                    "inferred_vintage": vintage_label(inferred),
                    "publication_year_used": pub_year,
                    "release_assumed_from_retrieval_date": vintage_label(from_date),
                },
            )
        )
        series = pd.DataFrame(columns=SERIES_COLUMNS)
        caveats.extend(forecast_caveats(frame, series, rules, pub_year))
        missing = missing_series(frame, ctys, inds)
        if missing:
            caveats.append(Caveat("IMF_MISSING_SERIES", "warn", "Some requested series returned no data.", {"missing": missing[:_EVIDENCE_CAP]}))
        aggs = sorted({c for c in frame["iso3"] if is_aggregate(c, frozenset(dm_groups))}) if len(frame) else []
        if len(frame) and dm_groups:
            frame["country"] = [n or dm_groups.get(c, "") for n, c in zip(frame["country"], frame["iso3"])]
        meta = {
            "aggregates": aggs,
            "dataflow": "DataMapper",
            "vintage": vint,
            "requests": urls,
            "missing": missing,
            "forecast_rule": "publication_year",
            "indicators": dict(sorted(dict(zip(frame["indicator"], frame["indicator_label"])).items())) if len(frame) else {},
        }
        return Result(data=frame, provenance=provs, caveats=caveats, meta=meta)

    # -- shared result helpers ---------------------------------------------------------------
    @staticmethod
    def _common_caveats(frame: pd.DataFrame, parsed: ParsedSDMX, ctys: list[str] | None, inds: list[str]) -> list[Caveat]:
        out: list[Caveat] = []
        if inds:
            miss = missing_series(frame, ctys, inds)
            if miss:
                wb_like = {"SSA", "SSF", "LMC", "UMC", "LIC", "HIC", "WLD", "EAP", "ECA", "LAC", "MNA", "SAS"}
                hint = (
                    " IMF aggregates use G-codes (e.g. G001 World), not World Bank codes."
                    if any(m.split("/")[0] in wb_like for m in miss)
                    else ""
                )
                out.append(Caveat("IMF_MISSING_SERIES", "warn", "Some requested series returned no data (unknown codes return an empty message)." + hint, {"missing": miss[:_EVIDENCE_CAP]}))
        if parsed.unknown_obs_status:
            out.append(Caveat("IMF_UNMAPPED_OBS_STATUS", "info", "Observation status values without a canonical mapping were left blank.", {"values": parsed.unknown_obs_status}))
        if len(frame) and frame["unit"].map(lambda u: "ppp" in u.lower()).any():
            out.append(Caveat("IMF_PPP_BASE_UNKNOWN", "warn", "PPP-based series: the API does not state the ICP base year; do not mix with other PPP vintages without checking.", {}))
        scales = {r.indicator: r.scale for r in parsed.series.itertuples() if r.scale and r.scale not in ("0", "")}
        if scales:
            out.append(Caveat("IMF_SCALE_INFORMATIONAL", "info", "Values are in base units; the SCALE attribute (power of ten) is only a display hint.", {"scale": dict(sorted(scales.items()))}))
        return out

    @staticmethod
    def _meta(frame: pd.DataFrame, parsed: ParsedSDMX, urls: list[str], *, flow: str, agency: str, vintage: str, publication_date: str, missing: list[str]) -> dict:
        aggs = sorted({c for c in frame["iso3"] if is_aggregate(c)}) if len(frame) else []
        return {
            "aggregates": aggs,
            "dataflow": flow,
            "agency": agency,
            "structure_id": parsed.structure_id,
            "structure_version": structure_version(parsed.structure_id),
            "vintage": vintage,
            "publication_date": publication_date,
            "requests": urls,
            "dimensions": parsed.dimensions,
            "series": _series_records(parsed.series),
            "series_count": int(len(parsed.series)),
            "series_truncated": bool(len(parsed.series) > SERIES_META_CAP),
            "forecast_rules": dict(sorted(parsed.forecast_rules.items())[:SERIES_META_CAP]),
            "obs_status_raw": parsed.obs_status_raw,
            "scale": _scale_meta(parsed.series),
            "dropped_missing_values": parsed.dropped_missing,
            "duplicates_dropped": parsed.duplicates_dropped,
            "missing": missing,
            "indicators": dict(sorted(dict(zip(frame["indicator"], frame["indicator_label"])).items())) if len(frame) else {},
        }


__all__ = [
    "BOP_ITEMS",
    "IMF",
    "IMFAPIError",
    "IMFResponseError",
    "KNOWN_AGENCIES",
    "ParsedSDMX",
    "WEO_ALIASES",
    "WEO_INDICATORS",
    "apply_forecast_flags",
    "build_data_request",
    "build_datamapper_request",
    "build_key",
    "build_publication_probe",
    "build_weo_request",
    "infer_release_from_date",
    "infer_vintage_from_text",
    "is_aggregate",
    "is_weo_flow",
    "normalize_period",
    "parse_codelist",
    "parse_dataflows",
    "parse_datamapper",
    "parse_datamapper_groups",
    "parse_datamapper_indicators",
    "parse_last_actual",
    "parse_publication_date",
    "parse_sdmx",
    "parse_vintage",
    "parse_weo",
    "read_sdmx_csv",
    "read_sdmx_json",
    "time_filter_param",
    "vintage_label",
    "weo_flow_for",
    "weo_flow_mapping",
    "weo_vintages_from_flows",
]
