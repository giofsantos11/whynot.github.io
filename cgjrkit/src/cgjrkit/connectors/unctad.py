"""UNCTADstat connector: foreign direct investment flows and stocks (``US.FdiFlowsStock``).

The UNCTADstat Data Centre (launched 2024) serves three routes; all are
described in ``docs/apis/unctad.md`` with their evidence:

* **Report metadata** (keyless ``GET``, JSON)::

      https://unctadstat-api.unctad.org/api/reportMetadata/{report}/en

  gives the release ``version`` (an integer, e.g. ``1912``), ``lastUpdated``,
  the dimensions (``Economy``, ``Year``, ``Direction``, ``Flow``) and the
  measures (``0100`` US$ at current prices, ``5011`` percentage of world, ...).
  It is the vintage of every result.

* **Bulk download** (keyless ``GET``, default route)::

      https://unctadstat-api.unctad.org/bulkdownload/{report}/{report with "_"}
      https://unctadstat-api.unctad.org/bulkdownload/{report}/{version}/{file}   (pinned release, UNVERIFIED)

  returns a 7z archive holding one CSV (``Year, Economy, Economy Label,
  Direction, Direction Label, Flow, Flow Label`` and one column per measure,
  e.g. ``US$ at current prices in millions`` plus ``... Footnote`` and
  ``... Missing value``). Reading 7z needs ``py7zr``; zip, gzip and plain CSV
  payloads are read with the standard library.

* **Data API** (keyed ``POST``, CSV)::

      https://unctadstat-user-api.unctad.org/{report}/cur/Facts
      headers ClientId / ClientSecret; fields $select, $filter, $format=csv

  needs free Data Centre credentials: ``CGJRKIT_UNCTAD_CLIENT_ID`` and
  ``CGJRKIT_UNCTAD_KEY`` (client secret). Its CSV has ``Economy_Code,
  Economy_Label, Year, Direction_Code, Direction_Label, Flow_Code, Flow_Label,
  M0100_Value, M0100_Footnote_Code, M0100_Footnote_Text,
  M0100_MissingValue_Code, ...``. The provider expects the OData fields as a
  form body; ``core.http.fetch`` can only send query parameters or JSON, so
  they are sent as query parameters (UNVERIFIED; see the core change request
  in ``docs/apis/unctad.md``).

Codes: ``Direction`` 1 = inward, 2 = outward; ``Flow`` ``08`` = flow, ``09`` =
stock. Economies are UN M49 codes (3 digits, mapped to ISO3 here); groups have
4 digits (``0000`` = World) and keep their provider code, listed in
``Result.meta["aggregates"]``. Values are reported in **millions of current
US$** (the API's ``M0100`` is in US$ and is divided by 10^6). Percent of GDP
(``5025``) is requested when available; the 2025 release metadata lists only
``0100`` and ``5011``, so a missing measure raises a caveat (compute % of GDP
from WDI GDP instead).

Licence: CC BY 3.0 IGO (UNCTADstat FAQ); cite UNCTADstat as the source.

Design: ``build_*`` and ``parse_*``/``read_*`` functions are pure;
:class:`UNCTAD` wires them through :func:`cgjrkit.core.http.fetch`.
"""

from __future__ import annotations

import gzip
import io
import json
import os
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from cgjrkit.connectors.base import Connector
from cgjrkit.core import http
from cgjrkit.core.cache import Cache, Retrieval
from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import empty_frame, make_frame, validate_frame

API_HOST = "https://unctadstat-api.unctad.org"
USER_API_HOST = "https://unctadstat-user-api.unctad.org"
METADATA_URL = API_HOST + "/api/reportMetadata/{report}/en"
BULK_URL = API_HOST + "/bulkdownload/{report}/{file}"
BULK_URL_VERSIONED = API_HOST + "/bulkdownload/{report}/{version}/{file}"
FACTS_URL = USER_API_HOST + "/{report}/cur/Facts"
KEY_ENV = "CGJRKIT_UNCTAD_KEY"
CLIENT_ID_ENV = "CGJRKIT_UNCTAD_CLIENT_ID"

FDI_REPORT = "US.FdiFlowsStock"
FDI_TITLE = "Foreign direct investment: Inward and outward flows and stock, annual"
LICENSE = "CC BY 3.0 IGO (UNCTADstat terms: reuse with attribution to UNCTADstat)"
CITATION = "UNCTADstat, Foreign direct investment: inward and outward flows and stock (US.FdiFlowsStock)"

DIRECTIONS = {"1": "inward", "2": "outward"}
MODES = {"08": "flow", "09": "stock"}

#: measure code -> (key, unit, divisor applied to reach the unit)
MEASURES: dict[str, tuple[str, str, float]] = {
    "0100": ("usd", "current US$ millions", 1e6),
    "0106": ("usd", "current US$ millions", 1.0),
    "5011": ("pct_world", "% of world total", 1.0),
    "5025": ("pct_gdp", "% of GDP", 1.0),
    "5026": ("pct_gfcf", "% of gross fixed capital formation", 1.0),
    "0110": ("usd_per_capita", "current US$ per capita", 1.0),
}
MEASURE_KEYS = ("usd", "pct_world", "pct_gdp", "pct_gfcf", "usd_per_capita")
_SUFFIX = {"usd": "USD_MN", "pct_world": "PCT_WORLD", "pct_gdp": "PCT_GDP", "pct_gfcf": "PCT_GFCF",
           "usd_per_capita": "USD_PC"}
#: series names: (direction, mode) -> (indicator stem, label)
SERIES: dict[tuple[str, str], tuple[str, str]] = {
    ("inward", "flow"): ("FDI_INFLOWS", "FDI inflows"),
    ("outward", "flow"): ("FDI_OUTFLOWS", "FDI outflows"),
    ("inward", "stock"): ("FDI_INWARD_STOCK", "FDI inward stock"),
    ("outward", "stock"): ("FDI_OUTWARD_STOCK", "FDI outward stock"),
}
#: order of preference when two measure codes give the same key (keep the first present)
_MEASURE_PRIORITY = ("0106", "0100", "5011", "5025", "5026", "0110")

#: UN M49 (= ISO 3166-1 numeric for countries) -> ISO3, generated from the
#: ISO 3166-1 table shipped with pycountry 24.6, plus the historical and
#: special codes below. Codes not in the table keep the provider code.
M49_TO_ISO3: dict[str, str] = {
    "004": "AFG", "008": "ALB", "010": "ATA", "012": "DZA", "016": "ASM", "020": "AND", "024": "AGO", "028": "ATG",
    "031": "AZE", "032": "ARG", "036": "AUS", "040": "AUT", "044": "BHS", "048": "BHR", "050": "BGD", "051": "ARM",
    "052": "BRB", "056": "BEL", "060": "BMU", "064": "BTN", "068": "BOL", "070": "BIH", "072": "BWA", "074": "BVT",
    "076": "BRA", "084": "BLZ", "086": "IOT", "090": "SLB", "092": "VGB", "096": "BRN", "100": "BGR", "104": "MMR",
    "108": "BDI", "112": "BLR", "116": "KHM", "120": "CMR", "124": "CAN", "132": "CPV", "136": "CYM", "140": "CAF",
    "144": "LKA", "148": "TCD", "152": "CHL", "156": "CHN", "158": "TWN", "162": "CXR", "166": "CCK", "170": "COL",
    "174": "COM", "175": "MYT", "178": "COG", "180": "COD", "184": "COK", "188": "CRI", "191": "HRV", "192": "CUB",
    "196": "CYP", "203": "CZE", "204": "BEN", "208": "DNK", "212": "DMA", "214": "DOM", "218": "ECU", "222": "SLV",
    "226": "GNQ", "231": "ETH", "232": "ERI", "233": "EST", "234": "FRO", "238": "FLK", "239": "SGS", "242": "FJI",
    "246": "FIN", "248": "ALA", "250": "FRA", "254": "GUF", "258": "PYF", "260": "ATF", "262": "DJI", "266": "GAB",
    "268": "GEO", "270": "GMB", "275": "PSE", "276": "DEU", "288": "GHA", "292": "GIB", "296": "KIR", "300": "GRC",
    "304": "GRL", "308": "GRD", "312": "GLP", "316": "GUM", "320": "GTM", "324": "GIN", "328": "GUY", "332": "HTI",
    "334": "HMD", "336": "VAT", "340": "HND", "344": "HKG", "348": "HUN", "352": "ISL", "356": "IND", "360": "IDN",
    "364": "IRN", "368": "IRQ", "372": "IRL", "376": "ISR", "380": "ITA", "384": "CIV", "388": "JAM", "392": "JPN",
    "398": "KAZ", "400": "JOR", "404": "KEN", "408": "PRK", "410": "KOR", "414": "KWT", "417": "KGZ", "418": "LAO",
    "422": "LBN", "426": "LSO", "428": "LVA", "430": "LBR", "434": "LBY", "438": "LIE", "440": "LTU", "442": "LUX",
    "446": "MAC", "450": "MDG", "454": "MWI", "458": "MYS", "462": "MDV", "466": "MLI", "470": "MLT", "474": "MTQ",
    "478": "MRT", "480": "MUS", "484": "MEX", "492": "MCO", "496": "MNG", "498": "MDA", "499": "MNE", "500": "MSR",
    "504": "MAR", "508": "MOZ", "512": "OMN", "516": "NAM", "520": "NRU", "524": "NPL", "528": "NLD", "531": "CUW",
    "533": "ABW", "534": "SXM", "535": "BES", "540": "NCL", "548": "VUT", "554": "NZL", "558": "NIC", "562": "NER",
    "566": "NGA", "570": "NIU", "574": "NFK", "578": "NOR", "580": "MNP", "581": "UMI", "583": "FSM", "584": "MHL",
    "585": "PLW", "586": "PAK", "591": "PAN", "598": "PNG", "600": "PRY", "604": "PER", "608": "PHL", "612": "PCN",
    "616": "POL", "620": "PRT", "624": "GNB", "626": "TLS", "630": "PRI", "634": "QAT", "638": "REU", "642": "ROU",
    "643": "RUS", "646": "RWA", "652": "BLM", "654": "SHN", "659": "KNA", "660": "AIA", "662": "LCA", "663": "MAF",
    "666": "SPM", "670": "VCT", "674": "SMR", "678": "STP", "682": "SAU", "686": "SEN", "688": "SRB", "690": "SYC",
    "694": "SLE", "702": "SGP", "703": "SVK", "704": "VNM", "705": "SVN", "706": "SOM", "710": "ZAF", "716": "ZWE",
    "724": "ESP", "728": "SSD", "729": "SDN", "732": "ESH", "740": "SUR", "744": "SJM", "748": "SWZ", "752": "SWE",
    "756": "CHE", "760": "SYR", "762": "TJK", "764": "THA", "768": "TGO", "772": "TKL", "776": "TON", "780": "TTO",
    "784": "ARE", "788": "TUN", "792": "TUR", "795": "TKM", "796": "TCA", "798": "TUV", "800": "UGA", "804": "UKR",
    "807": "MKD", "818": "EGY", "826": "GBR", "831": "GGY", "832": "JEY", "833": "IMN", "834": "TZA", "840": "USA",
    "850": "VIR", "854": "BFA", "858": "URY", "860": "UZB", "862": "VEN", "876": "WLF", "882": "WSM", "887": "YEM",
    "894": "ZMB",
    # historical / special (ISO 3166-3 and UN M49 practice)
    "200": "CSK", "278": "DDR", "530": "ANT", "720": "YMD", "810": "SUN", "890": "YUG", "891": "SCG", "412": "XKX",
}
ISO3_TO_M49: dict[str, str] = {v: k for k, v in M49_TO_ISO3.items()}

_MAGIC_7Z = b"7z\xbc\xaf\x27\x1c"
_ECONOMY_RE = re.compile(r"^\d{3}$")
_STAMP_RE = re.compile(r"_ST(\d{6})")


class UNCTADError(RuntimeError):
    """A payload could not be read, a credential is missing, or a request is invalid."""


# ---------------------------------------------------------------------------
# pure request builders
# ---------------------------------------------------------------------------
def bulk_file_name(report: str) -> str:
    """Bulk file stem: ``US.FdiFlowsStock`` -> ``US_FdiFlowsStock``."""
    return report.replace(".", "_")


def build_metadata_request(report: str = FDI_REPORT) -> tuple[str, dict]:
    return METADATA_URL.format(report=report), {}


def build_bulk_request(report: str = FDI_REPORT, version: int | str | None = None) -> tuple[str, dict]:
    """Bulk 7z URL; ``version`` pins a release (path form UNVERIFIED for this report)."""
    file = bulk_file_name(report)
    if version is None or str(version).strip() == "":
        return BULK_URL.format(report=report, file=file), {}
    return BULK_URL_VERSIONED.format(report=report, version=str(version).strip(), file=file), {}


def pinned_vintage(report: str, version: str, member: str = "") -> str:
    """Vintage label of a pinned bulk release: ``"UNCTADstat US.FdiFlowsStock v1912 (pinned bulk release)"``,
    with the ``_ST<yyyymm>`` stamp of the bulk member name when there is one."""
    stamp = _STAMP_RE.search(member or "")
    extra = f", ST{stamp.group(1)}" if stamp else ""
    return f"UNCTADstat {report} v{version} (pinned bulk release{extra})"


def to_m49(code: str) -> str:
    """ISO3 (``KEN``) or M49 (``404``/``0000``) -> UNCTAD economy code."""
    c = str(code).strip().upper()
    if c.isdigit():
        return c.zfill(3) if len(c) <= 3 else c
    if c in ISO3_TO_M49:
        return ISO3_TO_M49[c]
    raise ValueError(f"unknown economy {code!r}: use ISO3 codes or UNCTAD M49/group codes")


def economy_codes(countries: Sequence[str] | str | None) -> set[str]:
    """UNCTAD economy codes for ISO3/M49 inputs (``None`` or empty = no filter)."""
    if countries is None:
        return set()
    items = [countries] if isinstance(countries, str) else list(countries)
    return {to_m49(part) for item in items for part in str(item).split(",") if part.strip()}


def build_api_request(
    report: str = FDI_REPORT,
    measures: Sequence[str] = ("0100", "5011"),
    economies: Sequence[str] | None = None,
    years: Sequence[int] | None = None,
) -> tuple[str, dict, tuple[str, str]]:
    """Keyed Facts query: ``(url, fields, (client id header, client secret header))``.

    ``fields`` are the OData options the API documents as a form body:
    ``$select`` (bare field names return code, label, footnote and missing-value
    sub-columns), ``$filter`` and ``$format=csv``.
    """
    select = ["Economy", "Year", "Direction", "Flow"] + [f"M{str(m).lstrip('M')}" for m in measures]
    filters = []
    if economies:
        codes = sorted(economy_codes(economies))
        filters.append("Economy/Code in (" + ",".join(f"'{c}'" for c in codes) + ")")
    if years:
        filters.append("Year in (" + ",".join(str(int(y)) for y in sorted(set(years))) + ")")
    fields = {"$select": ",".join(select), "$format": "csv", "culture": "en"}
    if filters:
        fields["$filter"] = " and ".join(filters)
    return FACTS_URL.format(report=report), fields, ("ClientId", "ClientSecret")


def api_credentials() -> tuple[str, str]:
    """``(client id, client secret)`` from the environment, or a clear error."""
    cid = os.environ.get(CLIENT_ID_ENV, "").strip()
    key = os.environ.get(KEY_ENV, "").strip()
    if not (cid and key):
        raise UNCTADError(
            f"the UNCTADstat data API needs {CLIENT_ID_ENV} and {KEY_ENV} (client id and secret from a free "
            "UNCTADstat Data Centre account); or use route='bulk', which is keyless")
    return cid, key


# ---------------------------------------------------------------------------
# pure parsing
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReportMetadata:
    name: str
    version: str
    last_updated: str
    measures: dict[str, str]            # code -> label (all magnitude variants)
    dimensions: list[str]               # field names in axis order (row, col, page)

    @property
    def vintage(self) -> str:
        date = (self.last_updated or "")[:10]
        return f"UNCTADstat {self.name} v{self.version}" + (f" ({date})" if date else "")


def parse_metadata(payload: bytes | str) -> ReportMetadata:
    """Read ``/api/reportMetadata/{report}/en``."""
    try:
        doc = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise UNCTADError(f"UNCTAD metadata is not JSON: {str(payload)[:120]!r}") from exc
    defaults = doc.get("defaults") or {}
    measures: dict[str, str] = {}
    for group in defaults.get("observations") or []:
        for m in group.get("measures") or [{"code": group.get("code"), "label": group.get("label")}]:
            if m.get("code"):
                measures[str(m["code"])] = str(m.get("label", ""))
    dims = [str(d.get("field")) for axe in ("rowAxe", "colAxe", "pageAxe") for d in (defaults.get(axe) or [])
            if d.get("field")]
    return ReportMetadata(str(doc.get("name", "")), str(doc.get("version", "")), str(doc.get("lastUpdated", "")),
                          measures, dims)


def read_archive(payload: bytes) -> tuple[str, str]:
    """``(member name, CSV text)`` from a 7z, zip, gzip or plain-CSV payload."""
    if payload[:6] == _MAGIC_7Z:
        try:
            import py7zr
            from py7zr.io import BytesIOFactory
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise UNCTADError("the UNCTADstat bulk file is a 7z archive: install py7zr "
                              "(uv pip install py7zr) or use route='api'") from exc
        factory = BytesIOFactory(limit=2**34)
        with py7zr.SevenZipFile(io.BytesIO(payload), mode="r") as z:
            names = [n for n in z.getnames() if n.lower().endswith(".csv")] or z.getnames()
            z.extractall(factory=factory)
        if not names:
            raise UNCTADError("empty 7z archive")
        product = factory.products[names[0]]
        product.seek(0)
        return names[0], product.read().decode("utf-8-sig")
    if payload[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".csv")] or z.namelist()
            if not names:
                raise UNCTADError("empty zip archive")
            return names[0], z.read(names[0]).decode("utf-8-sig")
    if payload[:2] == b"\x1f\x8b":
        return "", gzip.decompress(payload).decode("utf-8-sig")
    text = payload.decode("utf-8-sig")
    if text.lstrip()[:1] in ("<", "{"):
        raise UNCTADError(f"UNCTAD returned a non-CSV payload: {text.lstrip()[:120]!r}")
    return "", text


def measure_code_for_label(label: str) -> str | None:
    """Measure code of a bulk-CSV value column label, or ``None``."""
    low = label.lower().strip()
    if "percentage of total world" in low or "percentage of world" in low:
        return "5011"
    if "gross domestic product" in low or low.endswith("% of gdp"):
        return "5025"
    if "gross fixed capital formation" in low:
        return "5026"
    if "per capita" in low:
        return "0110"
    if "us$" in low or "us dollars" in low:
        return "0106" if "million" in low else "0100"
    return None


_RAW_COLUMNS = ["economy", "economy_label", "year", "direction", "mode", "measure", "value", "footnote", "missing"]


def _directions(code: pd.Series, label: pd.Series) -> pd.Series:
    by_code = code.astype(str).str.strip().map(DIRECTIONS)
    by_label = label.astype(str).str.strip().str.lower().map({"inward": "inward", "outward": "outward"})
    return by_code.fillna(by_label).fillna("")


def _modes(code: pd.Series, label: pd.Series) -> pd.Series:
    by_code = code.astype(str).str.strip().str.zfill(2).map(MODES)
    by_label = label.astype(str).str.strip().str.lower().map({"flow": "flow", "flows": "flow", "stock": "stock",
                                                              "stocks": "stock"})
    return by_code.fillna(by_label).fillna("")


def parse_bulk_csv(text: str) -> pd.DataFrame:
    """Long raw table from the bulk CSV (``Year, Economy, Economy Label, Direction(
    Label), Flow( Label)`` + one column per measure)."""
    df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    need = ["Year", "Economy"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise UNCTADError(f"bulk CSV lacks {missing}; columns: {list(df.columns)[:12]}")
    value_cols = {c: measure_code_for_label(c) for c in df.columns
                  if not c.endswith(" Footnote") and not c.endswith(" Missing value")
                  and c not in ("Year", "Economy", "Economy Label", "Direction", "Direction Label", "Flow", "Flow Label")}
    value_cols = {c: m for c, m in value_cols.items() if m}
    if not value_cols:
        raise UNCTADError(f"no measure columns recognised in the bulk CSV: {list(df.columns)}")
    base = pd.DataFrame({
        "economy": df["Economy"].str.strip(),
        "economy_label": df.get("Economy Label", df["Economy"]).str.strip(),
        "year": df["Year"].str.strip(),
        "direction": _directions(df.get("Direction", pd.Series("", index=df.index)),
                                 df.get("Direction Label", pd.Series("", index=df.index))),
        "mode": _modes(df.get("Flow", pd.Series("", index=df.index)),
                       df.get("Flow Label", pd.Series("", index=df.index))),
    })
    parts = []
    for col, code in value_cols.items():
        part = base.copy()
        part["measure"] = code
        part["value"] = df[col]
        part["footnote"] = df.get(col + " Footnote", pd.Series("", index=df.index))
        part["missing"] = df.get(col + " Missing value", pd.Series("", index=df.index))
        parts.append(part)
    return pd.concat(parts, ignore_index=True)[_RAW_COLUMNS]


def parse_facts_csv(text: str) -> pd.DataFrame:
    """Long raw table from the Facts CSV (``Economy_Code, ..., M0100_Value, M0100_Footnote_Text, ...``)."""
    df = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    if "Economy_Code" not in df.columns or "Year" not in df.columns:
        raise UNCTADError(f"Facts CSV lacks Economy_Code/Year; columns: {list(df.columns)[:12]}")
    blank = pd.Series("", index=df.index)
    base = pd.DataFrame({
        "economy": df["Economy_Code"].str.strip(),
        "economy_label": df.get("Economy_Label", df["Economy_Code"]).str.strip(),
        "year": df["Year"].str.strip(),
        "direction": _directions(df.get("Direction_Code", blank), df.get("Direction_Label", blank)),
        "mode": _modes(df.get("Flow_Code", blank), df.get("Flow_Label", blank)),
    })
    parts = []
    for col in df.columns:
        m = re.fullmatch(r"M(\d{4})_Value", col)
        if not m:
            continue
        code = m.group(1)
        part = base.copy()
        part["measure"] = code
        part["value"] = df[col]
        part["footnote"] = df.get(f"M{code}_Footnote_Text", blank)
        part["missing"] = df.get(f"M{code}_MissingValue_Label", df.get(f"M{code}_MissingValue_Code", blank))
        parts.append(part)
    if not parts:
        raise UNCTADError("no M<code>_Value columns in the Facts CSV")
    return pd.concat(parts, ignore_index=True)[_RAW_COLUMNS]


@dataclass
class ParsedFDI:
    frame: pd.DataFrame
    meta: dict = field(default_factory=dict)
    caveats: list[Caveat] = field(default_factory=list)


def _keys(measures: Sequence[str] | str | None) -> list[str]:
    if measures is None:
        return list(MEASURE_KEYS)
    items = [measures] if isinstance(measures, str) else list(measures)
    out = []
    for m in items:
        k = str(m).strip().lower()
        if k in MEASURES:
            k = MEASURES[k][0]
        if k not in MEASURE_KEYS:
            raise ValueError(f"unknown FDI measure {m!r}; use {MEASURE_KEYS} or codes {sorted(MEASURES)}")
        out.append(k)
    return list(dict.fromkeys(out))


def to_canonical(
    raw: pd.DataFrame,
    countries: Sequence[str] | str | None = None,
    start: int | None = None,
    end: int | None = None,
    measures: Sequence[str] | str | None = ("usd", "pct_world", "pct_gdp"),
    series: Sequence[str] | None = None,
) -> ParsedFDI:
    """Canonical frame from a raw long table (bulk or Facts).

    ``countries``: ISO3 or UNCTAD codes (``None`` = all). ``measures``: keys
    from :data:`MEASURE_KEYS`. ``series``: indicator stems (``FDI_INFLOWS``,
    ``FDI_OUTFLOWS``, ``FDI_INWARD_STOCK``, ``FDI_OUTWARD_STOCK``; ``None`` = all).
    """
    keys = _keys(measures)
    df = raw.copy()
    df["key"] = df["measure"].map(lambda c: MEASURES.get(c, ("",))[0])
    available = sorted(set(df.loc[df["key"] != "", "key"]))
    mask = df["key"].isin(keys) & df["direction"].isin(["inward", "outward"]) & df["mode"].isin(["flow", "stock"])
    wanted = economy_codes(countries)
    if wanted:
        mask &= df["economy"].isin(wanted)
    years = pd.to_numeric(df["year"], errors="coerce")
    if start is not None:
        mask &= years >= int(start)
    if end is not None:
        mask &= years <= int(end)
    df = df[mask].copy()
    df["stem"] = [SERIES[(d, m)][0] for d, m in zip(df["direction"], df["mode"])]
    if series:
        df = df[df["stem"].isin([s.upper() for s in series])]

    # keep one measure code per (key): the first present in _MEASURE_PRIORITY
    chosen: dict[str, str] = {}
    for code in _MEASURE_PRIORITY:
        k = MEASURES[code][0]
        if k not in chosen and (df["measure"] == code).any():
            chosen[k] = code
    df = df[df["measure"].isin(set(chosen.values()))]
    values = pd.to_numeric(df["value"].str.replace(",", "", regex=False), errors="coerce")
    divisor = df["measure"].map(lambda c: MEASURES[c][2])
    df = df.assign(num=values / divisor)
    n_missing = int(df["num"].isna().sum())
    df = df[df["num"].notna()]

    is_economy = df["economy"].str.match(_ECONOMY_RE)
    iso3 = df["economy"].map(M49_TO_ISO3)
    iso3 = iso3.where(is_economy & iso3.notna(), df["economy"])
    foot = df["footnote"].fillna("").astype(str)
    status = foot.str.contains("estimat", case=False).map({True: "estimate", False: ""})
    out = pd.DataFrame({
        "indicator": df["stem"] + "_" + df["key"].map(_SUFFIX),
        "indicator_label": df.apply(lambda r: f"{SERIES[(r['direction'], r['mode'])][1]}, "
                                              f"{MEASURES[r['measure']][1]}", axis=1) if len(df) else pd.Series(dtype=str),
        "iso3": iso3,
        "country": df["economy_label"],
        "period": pd.to_numeric(df["year"], errors="coerce").astype("Int64").astype(str),
        "value": df["num"],
        "unit": df["measure"].map(lambda c: MEASURES[c][1]),
        "obs_status": status,
    })
    frame = make_frame(out, source="unctad", dataset="UNCTADstat FDI") if len(out) else empty_frame()
    frame = Connector.finalize(frame)
    aggregates = sorted(set(df.loc[~is_economy, "economy"]))
    unmapped = sorted(set(df.loc[is_economy & df["economy"].map(M49_TO_ISO3).isna(), "economy"]))
    footnotes = (pd.DataFrame({"iso3": iso3, "period": out["period"], "indicator": out["indicator"], "text": foot})
                 .loc[lambda x: x["text"].str.strip().ne("") & x["text"].ne("0")].head(200).to_dict("records")) if len(df) else []
    meta = {
        "measures_available": available,
        "measures_used": chosen,
        "aggregates": aggregates,
        "aggregate_labels": dict(sorted(set(zip(df.loc[~is_economy, "economy"], df.loc[~is_economy, "economy_label"])))),
        "unmapped_economies": unmapped,
        "dropped_missing_values": n_missing,
        "footnotes": footnotes,
    }
    caveats = _caveats(frame, keys, available, meta)
    return ParsedFDI(frame, meta, caveats)


def _caveats(df: pd.DataFrame, keys: list[str], available: list[str], meta: dict) -> list[Caveat]:
    out = [
        Caveat("unctad_directional", "info",
               "UNCTAD FDI data follow the directional principle unless otherwise indicated; they can differ from "
               "IMF BPM6 asset/liability data and WDI net inflows.", {}),
        Caveat("unctad_revisions", "info",
               "Each World Investment Report revises earlier years; pin the payload (snapshot) and cite the release "
               "version.", {}),
    ]
    missing = [k for k in keys if k not in available]
    if missing:
        out.append(Caveat("unctad_measure_missing", "warn",
                          f"Measure(s) {missing} are not in this release of US.FdiFlowsStock. For FDI as % of GDP, "
                          "divide the US$ series by WDI GDP (current US$) and footnote the method.",
                          {"missing": missing, "available": available}))
    if len(df) and (df["value"] < 0).any() and df["indicator"].str.contains("FLOWS").any():
        out.append(Caveat("unctad_negative_flows", "info",
                          "Negative FDI flows (disinvestment or negative reinvested earnings/intra-company loans) "
                          "are present.", {"rows": int((df["value"] < 0).sum())}))
    if meta["unmapped_economies"]:
        out.append(Caveat("unctad_unmapped_economies", "warn",
                          "Some UNCTAD economy codes have no ISO3 mapping and keep the provider code.",
                          {"codes": meta["unmapped_economies"]}))
    if not len(df):
        out.append(Caveat("unctad_empty", "warn", "No UNCTAD FDI observations matched the request.", {}))
    return out


# ---------------------------------------------------------------------------
# connector
# ---------------------------------------------------------------------------
class UNCTAD(Connector):
    """UNCTADstat FDI flows and stocks (bulk 7z by default; keyed data API optional)."""

    source = "unctad"
    license = LICENSE
    citation = CITATION
    domains = ("unctadstat-api.unctad.org", "unctadstat-user-api.unctad.org")

    def __init__(self, cache: Cache | None = None):
        super().__init__(cache)

    def _get(self, url: str, params: dict | None, refresh: bool, **kw: Any) -> tuple[bytes, Retrieval]:
        try:
            return http.fetch(self.source, url, params, cache=self.cache, refresh=refresh, timeout=600.0, **kw)
        except http.FetchError as exc:
            raise UNCTADError(f"UNCTADstat request failed ({url}): {exc}") from exc

    def metadata(self, report: str = FDI_REPORT, refresh: bool = False) -> tuple[ReportMetadata, Provenance]:
        """Release version, last update and measures of a report (keyless)."""
        url, params = build_metadata_request(report)
        payload, retrieval = self._get(url, params, refresh)
        meta = parse_metadata(payload)
        return meta, self.provenance(retrieval, dataset=report, vintage=meta.vintage, tool="UNCTAD.metadata")

    def fetch(self, *args: Any, **kwargs: Any) -> Result:
        """Alias of :meth:`fdi`."""
        return self.fdi(*args, **kwargs)

    def fdi(
        self,
        countries: Sequence[str] | str | None = None,
        start: int | None = None,
        end: int | None = None,
        measures: Sequence[str] | str | None = ("usd", "pct_world", "pct_gdp"),
        series: Sequence[str] | None = None,
        *,
        route: str = "bulk",
        version: int | str | None = None,
        with_metadata: bool = True,
        refresh: bool = False,
    ) -> Result:
        """FDI inflows, outflows and stocks as a canonical frame.

        ``route="bulk"`` (default) downloads the keyless 7z bulk file once (all
        economies, all years) and filters locally; ``route="api"`` queries the
        keyed data API for the requested economies and years only. ``version``
        pins a bulk release. ``with_metadata`` also reads the report metadata
        (keyless) for the release version; if it cannot be read the result
        carries ``unctad_vintage_unknown``.
        """
        provs: list[Provenance] = []
        caveats: list[Caveat] = []
        meta_info: ReportMetadata | None = None
        meta_prov: Provenance | None = None
        if with_metadata:
            try:
                meta_info, meta_prov = self.metadata(FDI_REPORT, refresh=refresh)
            except (UNCTADError, http.NetworkBlocked) as exc:
                caveats.append(Caveat("unctad_vintage_unknown", "warn",
                                      "The report metadata (release version) could not be read; cite the "
                                      "retrieval date and payload hash.", {"error": str(exc)[:200]}))
        member = ""
        route = route.lower()
        if route == "bulk":
            url, params = build_bulk_request(FDI_REPORT, version)
            payload, retrieval = self._get(url, params, refresh)
            member, text = read_archive(payload)
            raw = parse_bulk_csv(text)
        elif route == "api":
            if not countries:
                raise ValueError("route='api' needs countries (the API refuses queries above 62,500 cells); "
                                 "use route='bulk' for all economies")
            cid, key = api_credentials()
            economies = [countries] if isinstance(countries, str) else list(countries)
            years = list(range(int(start), int(end) + 1)) if start is not None and end is not None else None
            measure_codes = [c for c in ("0100", "5011", "5025", "5026", "0110") if MEASURES[c][0] in _keys(measures)]
            if meta_info and meta_info.measures:
                measure_codes = [c for c in measure_codes if c in meta_info.measures] or ["0100"]
            url, fields, (h_id, h_secret) = build_api_request(FDI_REPORT, measure_codes, economies, years)
            # UNVERIFIED: the API documents a form body; core.http.fetch sends these as query parameters.
            payload, retrieval = self._get(url, fields, refresh, method="POST", headers={h_id: cid, h_secret: key})
            raw = parse_facts_csv(payload.decode("utf-8-sig"))
        else:
            raise ValueError("route must be 'bulk' or 'api'")
        parsed = to_canonical(raw, countries, start, end, measures, series)
        df = parsed.frame
        validate_frame(df)
        stamp = _STAMP_RE.search(member or "")
        pinned = str(version).strip() if (route == "bulk" and version is not None and str(version).strip()) else ""
        vintage = pinned_vintage(FDI_REPORT, pinned, member) if pinned else (
            meta_info.vintage if meta_info else (f"UNCTADstat {FDI_REPORT} ST{stamp.group(1)}" if stamp else ""))
        notes = [f"route {route}", "values in millions of current US$ (US$ series)"]
        if member:
            notes.append(f"bulk member {member}")
        if version is not None:
            notes.append(f"requested bulk version {version}")
        if pinned and meta_info:
            notes.append(f"current metadata release: {meta_info.vintage}")
        prov = self.provenance(retrieval, dataset=FDI_REPORT, vintage=vintage, tool=f"UNCTAD.fdi[{route}]", notes=notes)
        # The metadata record describes the *current* release. With a pinned bulk release it only feeds the
        # mismatch check, so it is kept in meta (not in provenance) and source lines cite the pinned release only.
        if meta_prov is not None and not pinned:
            provs.append(meta_prov)
        provs.append(prov)
        if meta_info and pinned and pinned != meta_info.version:
            caveats.append(Caveat("unctad_version_mismatch", "warn",
                                  "The pinned bulk version differs from the current metadata version. The data "
                                  "provenance vintage names the pinned release; the metadata record (and "
                                  "meta['release']) describe the current one.",
                                  {"requested": pinned, "current": meta_info.version}))
        res = Result(data=df, provenance=provs, caveats=[], meta={
            **parsed.meta, "route": route, "bulk_member": member,
            "release": {"version": meta_info.version, "last_updated": meta_info.last_updated,
                        "measures": meta_info.measures} if meta_info else {},
            "pinned_version": pinned,
            **({"current_release_provenance": meta_prov.to_dict()} if (pinned and meta_prov is not None) else {}),
        })
        for c in parsed.caveats + caveats:
            res.add_caveat(c)
        return res
