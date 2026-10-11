"""Bruegel real effective exchange rate database (Darvas) connector.

Bruegel publishes REER/NEER indices for 177 countries (monthly) and 178
countries (annual) plus the euro area as one zipped Excel workbook,
``REER_database_ver<DDMonYYYY>.zip`` holding ``REER_database_ver<DDMonYYYY>.xls``,
linked from the dataset page::

    https://www.bruegel.org/publications/datasets/real-effective-exchange-rates-for-178-countries-a-new-database

The file is replaced in place at irregular intervals (no archive), so the
payload SHA-256 recorded in provenance *is* the vintage; pin it with
``CGJRKIT_SNAPSHOT`` for replications.

Workbook layout (third-party evidence, see ``docs/apis/bruegel.md``):

* one sheet per measure, frequency and partner basket, named
  ``<REER|NEER>_<MONTHLY|ANNUAL>_<N>`` (``REER_MONTHLY_120``, ``REER_ANNUAL_65``,
  ``NEER_MONTHLY_38``, ...). Basket sizes change between releases, so sheets
  are discovered, never hard-coded;
* row 1 holds column headers ``<REER|NEER>_<N>_<code>`` with a two-letter
  country code (ISO 3166 alpha-2, except ``SQ`` = Serbia and ``EA`` = euro area);
* column A holds the period: ``1993M01`` for monthly sheets, a year for annual
  sheets;
* a ``READ ME`` sheet documents the series and maps codes to country names.

Download location, in order: the ``url`` argument, ``$CGJRKIT_BRUEGEL_URL``,
the newest REER zip linked from the dataset page, and :data:`FALLBACK_URL`.

``find_download_links``, ``build_request`` and ``parse`` are pure;
:class:`BruegelREER` wires them through :func:`cgjrkit.core.http.fetch`.
"""

from __future__ import annotations

import functools
import io
import math
import os
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd

from cgjrkit.connectors.base import Connector
from cgjrkit.core import http
from cgjrkit.core.cache import Cache, Retrieval
from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import empty_frame, make_frame, validate_frame

DATASET_PAGE = (
    "https://www.bruegel.org/publications/datasets/"
    "real-effective-exchange-rates-for-178-countries-a-new-database"
)
#: Last-resort download URL (seen as a fallback in third-party code; it goes stale
#: whenever Bruegel uploads a new release). UNVERIFIED as current.
FALLBACK_URL = "https://www.bruegel.org/sites/default/files/2026-02/REER_database_ver16Feb2026.zip"
URL_ENV = "CGJRKIT_BRUEGEL_URL"

DATASET = "REER"
#: Short form for chart source lines; the full references are in ``FULL_CITATION`` and ``Result.meta``.
CITATION = "Bruegel REER database (Darvas 2012, 2021)"
FULL_CITATION = (
    "Darvas, Z. (2012) 'Real effective exchange rates for 178 countries: a new database', Working Paper 2012/06, "
    "Bruegel; Darvas, Z. (2021) 'Timely measurement of real effective exchange rates', Working Paper 15/2021, Bruegel"
)
LICENSE = "Free for any purpose with attribution, no endorsement implied (Bruegel data policy; REER-specific terms UNVERIFIED)"

FREQUENCIES = {"MONTHLY": "monthly", "QUARTERLY": "quarterly", "ANNUAL": "annual"}
MEASURES = {"REER": "Real effective exchange rate (CPI-based)", "NEER": "Nominal effective exchange rate"}

SHEET_RE = re.compile(r"^\s*(REER|NEER)_(MONTHLY|QUARTERLY|ANNUAL)_(\d+)\s*$", re.I)
COLUMN_RE = re.compile(r"^\s*(REER|NEER)_(\d+)_([A-Za-z0-9]+)\s*$", re.I)
VERSION_RE = re.compile(r"ver_?(\d{1,2})_?([A-Za-z]{3})[a-z]*_?(\d{4})", re.I)
LINK_RE = re.compile(r"""(?:https?://(?:www\.)?bruegel\.org)?/sites/default/files/[^"'\s<>()]+?\.(?:zip|xlsx|xls)\b""", re.I)
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

#: Provider codes that are not ISO 3166 alpha-2 (or need a fixed mapping).
SPECIAL_CODES: dict[str, str] = {
    "SQ": "SRB",   # Serbia (Bruegel code; two third-party readers)
    "XK": "XKX",   # Kosovo (user-assigned code, World Bank convention)
    "UK": "GBR",   # defensive: EU-style codes
    "EL": "GRC",
    "AN": "ANT",   # former Netherlands Antilles (withdrawn ISO code)
}
#: Aggregates kept under the provider code.
AGGREGATES: dict[str, str] = {"EA": "Euro area"}

_ISO2_TO_ISO3_TEXT = """
AD:AND AE:ARE AF:AFG AG:ATG AI:AIA AL:ALB AM:ARM AO:AGO AQ:ATA AR:ARG AS:ASM AT:AUT AU:AUS
AW:ABW AX:ALA AZ:AZE BA:BIH BB:BRB BD:BGD BE:BEL BF:BFA BG:BGR BH:BHR BI:BDI BJ:BEN BL:BLM
BM:BMU BN:BRN BO:BOL BQ:BES BR:BRA BS:BHS BT:BTN BV:BVT BW:BWA BY:BLR BZ:BLZ CA:CAN CC:CCK
CD:COD CF:CAF CG:COG CH:CHE CI:CIV CK:COK CL:CHL CM:CMR CN:CHN CO:COL CR:CRI CU:CUB CV:CPV
CW:CUW CX:CXR CY:CYP CZ:CZE DE:DEU DJ:DJI DK:DNK DM:DMA DO:DOM DZ:DZA EC:ECU EE:EST EG:EGY
EH:ESH ER:ERI ES:ESP ET:ETH FI:FIN FJ:FJI FK:FLK FM:FSM FO:FRO FR:FRA GA:GAB GB:GBR GD:GRD
GE:GEO GF:GUF GG:GGY GH:GHA GI:GIB GL:GRL GM:GMB GN:GIN GP:GLP GQ:GNQ GR:GRC GS:SGS GT:GTM
GU:GUM GW:GNB GY:GUY HK:HKG HM:HMD HN:HND HR:HRV HT:HTI HU:HUN ID:IDN IE:IRL IL:ISR IM:IMN
IN:IND IO:IOT IQ:IRQ IR:IRN IS:ISL IT:ITA JE:JEY JM:JAM JO:JOR JP:JPN KE:KEN KG:KGZ KH:KHM
KI:KIR KM:COM KN:KNA KP:PRK KR:KOR KW:KWT KY:CYM KZ:KAZ LA:LAO LB:LBN LC:LCA LI:LIE LK:LKA
LR:LBR LS:LSO LT:LTU LU:LUX LV:LVA LY:LBY MA:MAR MC:MCO MD:MDA ME:MNE MF:MAF MG:MDG MH:MHL
MK:MKD ML:MLI MM:MMR MN:MNG MO:MAC MP:MNP MQ:MTQ MR:MRT MS:MSR MT:MLT MU:MUS MV:MDV MW:MWI
MX:MEX MY:MYS MZ:MOZ NA:NAM NC:NCL NE:NER NF:NFK NG:NGA NI:NIC NL:NLD NO:NOR NP:NPL NR:NRU
NU:NIU NZ:NZL OM:OMN PA:PAN PE:PER PF:PYF PG:PNG PH:PHL PK:PAK PL:POL PM:SPM PN:PCN PR:PRI
PS:PSE PT:PRT PW:PLW PY:PRY QA:QAT RE:REU RO:ROU RS:SRB RU:RUS RW:RWA SA:SAU SB:SLB SC:SYC
SD:SDN SE:SWE SG:SGP SH:SHN SI:SVN SJ:SJM SK:SVK SL:SLE SM:SMR SN:SEN SO:SOM SR:SUR SS:SSD
ST:STP SV:SLV SX:SXM SY:SYR SZ:SWZ TC:TCA TD:TCD TF:ATF TG:TGO TH:THA TJ:TJK TK:TKL TL:TLS
TM:TKM TN:TUN TO:TON TR:TUR TT:TTO TV:TUV TW:TWN TZ:TZA UA:UKR UG:UGA UM:UMI US:USA UY:URY
UZ:UZB VA:VAT VC:VCT VE:VEN VG:VGB VI:VIR VN:VNM VU:VUT WF:WLF WS:WSM YE:YEM YT:MYT ZA:ZAF
ZM:ZMB ZW:ZWE
"""
#: ISO 3166-1 alpha-2 -> alpha-3 (249 codes, generated from pycountry 26.2.16).
ISO2_TO_ISO3: dict[str, str] = dict(pair.split(":") for pair in _ISO2_TO_ISO3_TEXT.split())


class BruegelError(RuntimeError):
    """The Bruegel file could not be found or read."""


# ---------------------------------------------------------------------------
# small pure helpers
# ---------------------------------------------------------------------------
def code_to_iso3(code: str) -> tuple[str, bool]:
    """Map a Bruegel country code to ISO3. Returns ``(code_or_iso3, mapped)``;
    aggregates (``EA``) and unknown codes are returned unchanged with ``mapped=False``."""
    c = str(code).strip().upper()
    if c in AGGREGATES:
        return c, False
    if c in SPECIAL_CODES:
        return SPECIAL_CODES[c], True
    if c in ISO2_TO_ISO3:
        return ISO2_TO_ISO3[c], True
    if re.fullmatch(r"[A-Z]{3}", c):
        return c, True
    return c, False


def normalize_frequency(frequency: str | None) -> list[str]:
    """``"monthly"``/``"m"`` -> ``["MONTHLY"]``; ``"all"``/``None`` -> every frequency."""
    if frequency is None or str(frequency).strip().lower() in ("", "all", "*"):
        return list(FREQUENCIES)
    f = str(frequency).strip().lower()
    table = {"m": "MONTHLY", "month": "MONTHLY", "monthly": "MONTHLY", "q": "QUARTERLY", "quarter": "QUARTERLY",
             "quarterly": "QUARTERLY", "a": "ANNUAL", "y": "ANNUAL", "year": "ANNUAL", "yearly": "ANNUAL",
             "annual": "ANNUAL"}
    if f not in table:
        raise ValueError(f"unknown frequency {frequency!r}; use monthly, quarterly, annual or all")
    return [table[f]]


def normalize_measure(measure: str | None) -> list[str]:
    if measure is None or str(measure).strip().lower() in ("", "all", "*"):
        return list(MEASURES)
    m = str(measure).strip().upper()
    if m not in MEASURES:
        raise ValueError(f"unknown measure {measure!r}; use REER, NEER or all")
    return [m]


def normalize_baskets(baskets: int | str | Sequence[int | str] | None) -> list[int] | None:
    if baskets is None or (isinstance(baskets, str) and baskets.strip().lower() in ("", "all")):
        return None
    items = re.split(r"[;,\s]+", baskets) if isinstance(baskets, str) else (
        [baskets] if isinstance(baskets, int) else list(baskets))
    out = sorted({int(str(b).strip()) for b in items if str(b).strip()})
    return out or None


def normalize_countries(countries: Sequence[str] | str | None) -> list[str] | None:
    """Upper-case ISO3 / ISO2 / provider codes, or ``None`` for all."""
    if countries is None or (isinstance(countries, str) and countries.strip().lower() in ("", "all")):
        return None
    items = re.split(r"[;,\s]+", countries) if isinstance(countries, str) else list(countries)
    out = sorted({str(c).strip().upper() for c in items if str(c).strip()})
    for c in out:
        if not re.fullmatch(r"[A-Z0-9]{2,3}", c):
            raise ValueError(f"invalid country code {c!r}: use ISO3, ISO2 or Bruegel codes")
    return out or None


def parse_version(name: str) -> str | None:
    """``"REER_database_ver22Sep2026.zip"`` -> ``"2026-09-22"``; ``None`` when absent."""
    m = VERSION_RE.search(str(name))
    if not m:
        return None
    month = _MONTHS.get(m.group(2).lower())
    if not month:
        return None
    try:
        return date(int(m.group(3)), month, int(m.group(1))).isoformat()
    except ValueError:
        return None


def sheet_info(name: str) -> tuple[str, str, int] | None:
    """``"REER_MONTHLY_120"`` -> ``("REER", "MONTHLY", 120)``; ``None`` for other sheets."""
    m = SHEET_RE.match(str(name))
    return (m.group(1).upper(), m.group(2).upper(), int(m.group(3))) if m else None


def indicator_label(measure: str, frequency: str, basket: int) -> str:
    return (f"{MEASURES[measure]}, {FREQUENCIES[frequency]}, against {basket} trading partners "
            "(increase = appreciation)")


# ---------------------------------------------------------------------------
# download location (pure)
# ---------------------------------------------------------------------------
def find_download_links(html: bytes | str, base: str = "https://www.bruegel.org") -> list[str]:
    """REER workbook links (zip/xls/xlsx under ``/sites/default/files/``) in a page, in order."""
    text = html.decode("utf-8", "replace") if isinstance(html, (bytes, bytearray)) else str(html)
    out: list[str] = []
    for m in LINK_RE.finditer(text):
        link = m.group(0)
        if "reer" not in link.lower():
            continue
        if link.startswith("/"):
            link = base.rstrip("/") + link
        link = re.sub(r"^http://", "https://", link)
        if link not in out:
            out.append(link)
    return out


def pick_latest(links: Sequence[str]) -> str | None:
    """The link with the newest ``ver<DDMonYYYY>`` date (zip preferred on ties); else the first link."""
    if not links:
        return None
    dated = [(parse_version(link) or "", link.lower().endswith(".zip"), -i, link) for i, link in enumerate(links)]
    best = max(dated)
    return best[3] if best[0] else links[0]


def build_request(url: str | None = None) -> tuple[str, dict]:
    """``(url, params)`` for the workbook: explicit URL, ``$CGJRKIT_BRUEGEL_URL`` or the fallback."""
    return (url or os.environ.get(URL_ENV, "") or FALLBACK_URL), {}


def build_page_request() -> tuple[str, dict]:
    return DATASET_PAGE, {}


# ---------------------------------------------------------------------------
# workbook (pure)
# ---------------------------------------------------------------------------
@dataclass
class Workbook:
    sheets: list[str]
    member: str
    engine: str
    _payload: bytes = field(repr=False, default=b"")

    def parse(self, sheet: str) -> pd.DataFrame:
        return _parse_sheet_cached(self._payload, self.engine, sheet).copy()


def _excel_engine(payload: bytes) -> str:
    head = bytes(payload[:8])
    if head == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return "xlrd"
    if head.startswith(b"PK\x03\x04"):
        return "openpyxl"
    snippet = bytes(payload[:200]).lstrip()
    kind = "HTML" if snippet[:1] == b"<" else "an unknown format"
    raise BruegelError(f"the Bruegel download is {kind}, not an Excel workbook: {snippet[:120]!r}")


@functools.lru_cache(maxsize=4)
def _unpack(payload: bytes) -> tuple[bytes, str]:
    """Return ``(workbook_bytes, member_name)``; a zip archive is opened, a bare workbook passed through."""
    if payload[:4] == b"PK\x03\x04":
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                names = zf.namelist()
                if "[Content_Types].xml" in names:  # the payload is itself an xlsx
                    return payload, ""
                books = [n for n in names if re.search(r"\.(xls|xlsx)$", n, re.I) and not n.startswith("__MACOSX")
                         and not os.path.basename(n).startswith("~$")]
                if not books:
                    raise BruegelError(f"no .xls/.xlsx workbook in the Bruegel zip: {names[:20]}")
                reer = [n for n in books if "reer" in os.path.basename(n).lower()] or books
                member = sorted(reer, key=lambda n: (n.count("/"), n))[0]
                return zf.read(member), member
        except zipfile.BadZipFile as exc:
            raise BruegelError(f"corrupt Bruegel zip: {exc}") from exc
    return payload, ""


@functools.lru_cache(maxsize=2)
def _excel_file(book: bytes, engine: str) -> pd.ExcelFile:
    """Open a workbook once (xlrd loads every sheet on open, so re-opening per sheet is slow)."""
    try:
        return pd.ExcelFile(io.BytesIO(book), engine=engine)
    except Exception as exc:  # xlrd/openpyxl raise various exception types
        raise BruegelError(f"unreadable Bruegel workbook: {exc}") from exc


@functools.lru_cache(maxsize=64)
def _parse_sheet_cached(book: bytes, engine: str, sheet: str) -> pd.DataFrame:
    return _excel_file(book, engine).parse(sheet, header=None)


def _sheet_names(book: bytes, engine: str) -> tuple[str, ...]:
    return tuple(str(s) for s in _excel_file(book, engine).sheet_names)


def open_workbook(payload: bytes) -> Workbook:
    """Open the Bruegel workbook from a zip or a bare .xls/.xlsx payload."""
    book, member = _unpack(bytes(payload))
    engine = _excel_engine(book)
    return Workbook(sheets=list(_sheet_names(book, engine)), member=member, engine=engine, _payload=book)


def _period(cell: Any, frequency: str) -> str | None:
    """Canonical period string for a date cell, or ``None`` if the cell is not a period."""
    if cell is None or (isinstance(cell, float) and math.isnan(cell)):
        return None
    if isinstance(cell, (datetime, pd.Timestamp, date)):
        ts = pd.Timestamp(cell)
        if frequency == "ANNUAL":
            return f"{ts.year:04d}"
        if frequency == "QUARTERLY":
            return f"{ts.year:04d}Q{(ts.month - 1) // 3 + 1}"
        return f"{ts.year:04d}-{ts.month:02d}"
    if isinstance(cell, (int, float)):
        if float(cell).is_integer() and 1800 <= int(cell) <= 2200:
            return f"{int(cell):04d}" if frequency == "ANNUAL" else None
        return None
    s = str(cell).strip()
    m = re.fullmatch(r"(\d{4})\s*[Mm]\s*(\d{1,2})", s) or re.fullmatch(r"(\d{4})-(\d{1,2})(?:-\d{1,2})?", s)
    if m and 1 <= int(m.group(2)) <= 12:
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    m = re.fullmatch(r"(\d{4})\s*[Qq]\s*([1-4])", s)
    if m:
        return f"{m.group(1)}Q{m.group(2)}"
    m = re.fullmatch(r"(\d{4})(?:\.0)?", s)
    if m and frequency == "ANNUAL":
        return m.group(1)
    return None


def parse_readme(raw: pd.DataFrame) -> dict[str, str]:
    """Code -> country name from the ``READ ME`` sheet.

    Looks for a header row with a ``Country`` cell and a ``code`` cell
    (case-insensitive) and reads the rows below it until the table ends.
    """
    names: dict[str, str] = {}
    values = raw.astype(object).where(raw.notna(), None).values.tolist()
    for i, row in enumerate(values):
        cells = [str(c).strip().lower() if c is not None else "" for c in row]
        if "country" in cells and any(c in ("code", "iso code", "iso", "country code") for c in cells):
            ci = cells.index("country")
            ki = next(j for j, c in enumerate(cells) if c in ("code", "iso code", "iso", "country code"))
            blank = 0
            for row2 in values[i + 1:]:
                name, code = row2[ci], row2[ki]
                if name is None and code is None:
                    blank += 1
                    if blank >= 3:
                        break
                    continue
                blank = 0
                if isinstance(code, str) and re.fullmatch(r"[A-Za-z]{2,3}", code.strip()) and name is not None:
                    names.setdefault(code.strip().upper(), str(name).strip())
            break
    return names


def parse_sheet(raw: pd.DataFrame, sheet: str) -> tuple[pd.DataFrame, list[str]]:
    """Long frame ``code, period, value`` from one REER/NEER sheet, plus skipped headers."""
    info = sheet_info(sheet)
    if info is None:
        raise BruegelError(f"{sheet!r} is not a REER/NEER data sheet")
    measure, frequency, basket = info
    header_row = None
    for i in range(min(len(raw), 15)):
        hits = sum(1 for c in raw.iloc[i] if isinstance(c, str) and COLUMN_RE.match(c))
        if hits >= 1:
            header_row = i
            break
    if header_row is None:
        return pd.DataFrame(columns=["code", "period", "value"]), []
    header = raw.iloc[header_row].tolist()
    cols: dict[int, str] = {}
    skipped: list[str] = []
    for j, h in enumerate(header[1:], start=1):
        if h is None or (isinstance(h, float) and math.isnan(h)) or str(h).strip() == "":
            continue
        m = COLUMN_RE.match(str(h))
        if not m or m.group(1).upper() != measure or int(m.group(2)) != basket:
            skipped.append(str(h))
            continue
        cols[j] = m.group(3).upper()
    body = raw.iloc[header_row + 1:]
    periods = [_period(c, frequency) for c in body.iloc[:, 0].tolist()]
    keep = [p is not None for p in periods]
    body = body.loc[keep]
    periods = [p for p in periods if p is not None]
    if not cols or not len(body):
        return pd.DataFrame(columns=["code", "period", "value"]), skipped
    block = body.iloc[:, list(cols)].copy()
    block.columns = [cols[j] for j in cols]
    block.insert(0, "period", periods)
    long = block.melt(id_vars="period", var_name="code", value_name="value")
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    long = long.drop_duplicates(subset=["code", "period"], keep="first")  # repeated header or period rows
    return long[["code", "period", "value"]].reset_index(drop=True), skipped


def infer_base_year(long: pd.DataFrame, tol: float = 0.05) -> str | None:
    """Year whose average index is 100 for most series (``None`` if no clear base)."""
    if not len(long):
        return None
    df = long.dropna(subset=["value"]).copy()
    df["year"] = df["period"].str[:4]
    means = df.groupby(["code", "year"])["value"].mean().reset_index()
    near = means.assign(hit=(means["value"] - 100.0).abs() <= tol).groupby("year")["hit"].agg(["sum", "count"])
    if not len(near):
        return None
    near["share"] = near["sum"] / df["code"].nunique()
    ranked = near.sort_values(["share", "sum"], ascending=False)
    return str(ranked.index[0]) if ranked["share"].iloc[0] >= 0.6 else None


@dataclass
class ParsedREER:
    frame: pd.DataFrame
    member: str = ""
    version: str | None = None
    sheets_available: list[str] = field(default_factory=list)
    sheets_used: list[str] = field(default_factory=list)
    missing_baskets: list[int] = field(default_factory=list)
    base_years: dict[str, str | None] = field(default_factory=dict)
    unmapped_codes: list[str] = field(default_factory=list)
    skipped_columns: list[str] = field(default_factory=list)
    aggregates: dict[str, str] = field(default_factory=dict)
    missing_countries: list[str] = field(default_factory=list)
    country_names: dict[str, str] = field(default_factory=dict)
    other_sheets: list[str] = field(default_factory=list)


def select_sheets(sheets: Sequence[str], measures: Sequence[str], frequencies: Sequence[str],
                  baskets: Sequence[int] | None) -> tuple[list[str], list[int]]:
    """Data sheets matching the request, and requested baskets with no sheet."""
    chosen = []
    for s in sheets:
        info = sheet_info(s)
        if info and info[0] in measures and info[1] in frequencies and (baskets is None or info[2] in baskets):
            chosen.append(s)
    found = {sheet_info(s)[2] for s in chosen}  # type: ignore[index]
    missing = [b for b in (baskets or []) if b not in found]
    return sorted(chosen, key=lambda s: sheet_info(s)), missing  # type: ignore[arg-type,return-value]


def parse(
    payload: bytes,
    *,
    countries: Sequence[str] | str | None = None,
    frequency: str | None = "monthly",
    baskets: int | str | Sequence[int | str] | None = None,
    measure: str | None = "REER",
    start: str | int | None = None,
    end: str | int | None = None,
    dropna: bool = True,
    source_name: str = "",
) -> ParsedREER:
    """Parse the Bruegel workbook (zip, .xls or .xlsx) into the canonical frame.

    ``indicator`` is the sheet name, which encodes measure, frequency and basket
    (``REER_MONTHLY_120``). ``start``/``end`` compare as period strings
    (``"2005"``, ``"2005-01"``).
    """
    meas, freqs, bsk = normalize_measure(measure), normalize_frequency(frequency), normalize_baskets(baskets)
    wanted = normalize_countries(countries)
    wb = open_workbook(payload)
    chosen, missing_baskets = select_sheets(wb.sheets, meas, freqs, bsk)
    readme_name = next((s for s in wb.sheets if re.sub(r"[\s_]", "", s.lower()) == "readme"), None)
    names = parse_readme(wb.parse(readme_name)) if readme_name else {}
    out = ParsedREER(frame=empty_frame(), member=wb.member,
                     version=parse_version(wb.member) or parse_version(source_name),
                     sheets_available=[s for s in wb.sheets if sheet_info(s)],
                     other_sheets=[s for s in wb.sheets if not sheet_info(s)],
                     missing_baskets=missing_baskets, country_names=names)
    parts = []
    for sheet in chosen:
        long, skipped = parse_sheet(wb.parse(sheet), sheet)
        out.skipped_columns += skipped
        out.base_years[sheet.strip().upper()] = infer_base_year(long)
        long["indicator"] = sheet.strip().upper()
        parts.append(long)
    out.sheets_used = [s.strip().upper() for s in chosen]
    if not parts:
        return out
    df = pd.concat(parts, ignore_index=True)
    mapped = {c: code_to_iso3(c) for c in df["code"].unique()}
    df["iso3"] = df["code"].map(lambda c: mapped[c][0])
    out.unmapped_codes = sorted(c for c, (_, ok) in mapped.items() if not ok and c not in AGGREGATES)
    out.aggregates = {c: AGGREGATES[c] for c in mapped if c in AGGREGATES}
    if wanted is not None:
        keep = df["iso3"].isin(wanted) | df["code"].isin(wanted)
        have = set(df["iso3"]) | set(df["code"])
        out.missing_countries = [c for c in wanted if c not in have]
        df = df[keep]
    s0 = str(start) if start is not None and str(start).strip() else None
    s1 = str(end) if end is not None and str(end).strip() else None
    if s0:
        df = df[df["period"] >= s0]
    if s1:
        df = df[df["period"].str[:len(s1)] <= s1]
    if dropna:
        df = df[df["value"].notna()]
    if not len(df):
        return out
    labels, units = {}, {}
    for ind in df["indicator"].unique():
        m, f, b = sheet_info(ind)  # type: ignore[misc]
        labels[ind] = indicator_label(m, f, b)
        base = out.base_years.get(ind)
        units[ind] = f"index, {base}=100" if base else "index"
    records = pd.DataFrame({
        "indicator": df["indicator"],
        "indicator_label": df["indicator"].map(labels),
        "iso3": df["iso3"],
        "country": df["code"].map(lambda c: names.get(c) or AGGREGATES.get(c, "")),
        "period": df["period"],
        "value": df["value"],
        "unit": df["indicator"].map(units),
    })
    out.frame = make_frame(records, source="bruegel", dataset=DATASET, obs_status="")
    return out


def build_caveats(info: ParsedREER, *, sha256: str = "", history: Sequence[dict] = (), fallback_url: bool = False,
                  url: str = "") -> list[Caveat]:
    df = info.frame
    out: list[Caveat] = [Caveat(
        "bruegel_snapshot", "info",
        "Bruegel replaces the REER file in place and keeps no archive; this result is identified by the payload "
        "SHA-256. Set CGJRKIT_SNAPSHOT to pin it for a replication.",
        {"sha256": sha256, "version": info.version, "file": info.member or url})]
    shas = []
    for rec in history:
        if rec.get("sha256") and rec["sha256"] not in shas:
            shas.append(rec["sha256"])
    if len(shas) > 1:
        out.append(Caveat(
            "bruegel_file_changed", "warn",
            "The Bruegel file at this URL changed between retrievals (revised data or a new release). Earlier "
            "results may not match.",
            {"sha256_history": shas, "retrievals": [r.get("retrieved_at", "") for r in history]}))
    if fallback_url:
        out.append(Caveat(
            "bruegel_url_fallback", "warn",
            "The download link could not be read from the dataset page, so a hard-coded URL was used; it may be an "
            f"older release. Pass url= or set {URL_ENV}.", {"url": url}))
    if len(df) and any(i.startswith("REER_MONTHLY") for i in df["indicator"].unique()):
        out.append(Caveat(
            "bruegel_realtime_cpi", "warn",
            "The latest months of the monthly REERs combine actual exchange rates with forecast inflation (Darvas "
            "2021) and are revised when CPI data arrive.",
            {"last_period": str(df["period"].max())}))
    bases = {k: v for k, v in info.base_years.items() if k in set(df["indicator"])} if len(df) else {}
    if bases:
        out.append(Caveat(
            "bruegel_base", "info",
            "Index levels depend on the provider's base period and partner basket; rebase before comparing with a "
            "report chart (Kenya CGJR Fig 11 uses Dec 2007 = 100, 120 partners).", {"base_years": bases}))
    if info.missing_baskets:
        out.append(Caveat(
            "bruegel_basket_missing", "warn",
            "Requested partner baskets are not in this release; basket sizes change between releases.",
            {"missing": info.missing_baskets, "available": info.sheets_available}))
    if info.unmapped_codes:
        out.append(Caveat("bruegel_unmapped_codes", "warn",
                          "Some Bruegel country codes have no ISO3 mapping and are kept as provider codes.",
                          {"codes": info.unmapped_codes}))
    if info.aggregates and len(df) and set(info.aggregates) & set(df["iso3"]):
        out.append(Caveat("bruegel_aggregates", "info", "The euro area is returned under the provider code EA.",
                          {"aggregates": info.aggregates}))
    if info.missing_countries:
        out.append(Caveat("bruegel_missing_countries", "warn", "Some requested countries are not in the file.",
                          {"countries": info.missing_countries}))
    if not len(df):
        out.append(Caveat("bruegel_empty", "warn", "No observations matched the request.",
                          {"sheets_used": info.sheets_used, "sheets_available": info.sheets_available}))
    return out


# ---------------------------------------------------------------------------
# connector
# ---------------------------------------------------------------------------
class BruegelREER(Connector):
    """Bruegel REER/NEER database (zipped Excel workbook, overwritten in place)."""

    source = "bruegel"
    license = LICENSE
    citation = CITATION
    domains = ("www.bruegel.org",)

    def __init__(self, cache: Cache | None = None, url: str | None = None):
        super().__init__(cache)
        self.url = url

    def _get(self, url: str, params: dict, refresh: bool) -> tuple[bytes, Retrieval]:
        try:
            return http.fetch(self.source, url, params, cache=self.cache, refresh=refresh, timeout=600.0)
        except http.FetchError as exc:
            raise BruegelError(f"Bruegel download failed ({url}): {exc}") from exc

    def resolve_url(self, url: str | None = None, refresh: bool = False) -> tuple[str, bool, list[Provenance]]:
        """Workbook URL: argument, instance URL, ``$CGJRKIT_BRUEGEL_URL``, the dataset page, then the fallback.

        Returns ``(url, used_fallback, provenance_of_page_lookup)``.
        """
        explicit = url or self.url or os.environ.get(URL_ENV, "")
        if explicit:
            return explicit, False, []
        page_url, params = build_page_request()
        try:
            html, retrieval = self._get(page_url, params, refresh)
        except (http.NetworkBlocked, BruegelError):
            return FALLBACK_URL, True, []
        link = pick_latest(find_download_links(html))
        if not link:
            return FALLBACK_URL, True, []
        prov = self.provenance(retrieval, dataset=DATASET, tool="BruegelREER.resolve_url",
                               notes=[f"download link found on the dataset page: {link}"])
        return link, False, [prov]

    def fetch(
        self,
        countries: Sequence[str] | str | None = None,
        frequency: str | None = "monthly",
        baskets: int | str | Sequence[int | str] | None = None,
        measure: str | None = "REER",
        start: str | int | None = None,
        end: str | int | None = None,
        *,
        url: str | None = None,
        dropna: bool = True,
        refresh: bool = False,
    ) -> Result:
        """REER (or NEER) indices as a canonical frame.

        ``countries``: ISO3/ISO2 codes (``"KEN"``) or ``None`` for all.
        ``frequency``: ``"monthly"`` (default), ``"annual"``, ``"quarterly"`` or ``"all"``.
        ``baskets``: partner-basket sizes (``120``) or ``None`` for every basket in
        the file. ``measure``: ``"REER"`` (default), ``"NEER"`` or ``"all"``.
        ``refresh=True`` re-downloads (the file is overwritten in place upstream).
        """
        req_url, fallback, provs = self.resolve_url(url, refresh=refresh)
        req_url, params = build_request(req_url)
        payload, retrieval = self._get(req_url, params, refresh)
        info = parse(payload, countries=countries, frequency=frequency, baskets=baskets, measure=measure,
                     start=start, end=end, dropna=dropna, source_name=req_url)
        df = self.finalize(info.frame) if len(info.frame) else info.frame
        validate_frame(df)
        info.frame = df
        history = self.cache.history(retrieval.key)
        vintage = (f"Bruegel REER {info.version}" if info.version
                   else f"Bruegel REER snapshot sha256:{retrieval.sha256[:12]} ({retrieval.retrieved_at[:10]})")
        caveats = build_caveats(info, sha256=retrieval.sha256, history=history, fallback_url=fallback, url=req_url)
        notes = [f"workbook member {info.member}" if info.member else "bare workbook",
                 f"sheets {', '.join(info.sheets_used) or 'none'}"]
        provs.append(self.provenance(retrieval, dataset=DATASET, vintage=vintage, tool="BruegelREER.fetch",
                                     notes=notes))
        meta = {
            "url": req_url,
            "citation": FULL_CITATION,
            "version": info.version,
            "member": info.member,
            "payload_sha256": retrieval.sha256,
            "sheets_available": info.sheets_available,
            "sheets_used": info.sheets_used,
            "other_sheets": info.other_sheets,
            "base_years": info.base_years,
            "missing_baskets": info.missing_baskets,
            "unmapped_codes": info.unmapped_codes,
            "skipped_columns": info.skipped_columns[:50],
            "aggregates": sorted(info.aggregates),
            "aggregate_names": info.aggregates,
            "missing_countries": info.missing_countries,
            "used_fallback_url": fallback,
            "retrievals": len(history),
            "params": {"countries": countries, "frequency": frequency, "baskets": baskets, "measure": measure,
                       "start": start, "end": end, "dropna": dropna},
        }
        return Result(data=df, provenance=provs, caveats=caveats, meta=meta)

    def sheets(self, url: str | None = None, refresh: bool = False) -> pd.DataFrame:
        """Catalogue of data sheets: ``sheet, measure, frequency, basket`` (no values parsed)."""
        req_url, _, _ = self.resolve_url(url, refresh=refresh)
        payload, _ = self._get(*build_request(req_url), refresh)
        wb = open_workbook(payload)
        rows = [{"sheet": s.strip().upper(), "measure": i[0], "frequency": FREQUENCIES[i[1]], "basket": i[2]}
                for s in wb.sheets if (i := sheet_info(s))]
        return pd.DataFrame(rows, columns=["sheet", "measure", "frequency", "basket"])
