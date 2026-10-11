"""Penn World Table connector (PWT 10.01 and 11.0; PWT 10.0 as a legacy option).

Each PWT release is a fixed file published under its own DOI on DataverseNL:

* PWT 10.01: DOI ``10.34894/QT5BCC``, 183 countries, 1950-2019, 2017 PPPs;
* PWT 11.0: DOI ``10.34894/FABVLR``, 185 countries, 1950-2023, 2021 PPPs;
* PWT 10.0 (legacy): ``https://www.rug.nl/ggdc/docs/pwt100.xlsx`` / ``.dta``.

Files are downloaded with the Dataverse Data Access API,
``https://dataverse.nl/api/access/datafile/<id>``. The numeric file ids are
pinned in :data:`VERSIONS` (third-party evidence, see ``docs/apis/pwt.md``);
``PWT.fetch(..., resolve=True)`` instead looks the file up in the dataset
record (``/api/datasets/:persistentId/?persistentId=doi:...``) and records the
Dataverse dataset version. Direct ``www.rug.nl/ggdc/docs/pwt1001.xlsx`` and
``pwt110.xlsx`` links return 404 (third-party observation), so rug.nl is used
only for PWT 10.0.

File layouts (both read, sniffed from the payload bytes):

* ``.xlsx``: sheets ``Info``, ``Legend`` (``Variable name``, ``Variable
  definition``, with one-cell group header rows) and ``Data`` (one row per
  country-year: ``countrycode, country, currency_unit, year, rgdpe, ...``);
* ``.dta``: the same wide table with Stata variable labels;
* tab-separated text, the Dataverse "archival" form of an ingested ``.dta``.

The canonical frame has one row per country-year-variable: ``indicator`` is
the PWT variable name (``rgdpna``, ``rnna``, ``emp``, ...), ``dataset`` is
``"PWT10.01"``/``"PWT11.0"`` so versions never collide, and PPP-based units
state the PPP base year (``"(PPP 2017)"``) for :func:`cgjrkit.core.schema.ppp_base`.
The reference year in units (2017 or 2021) is inferred from the data
(``rkna``/``rtfpna`` equal 1 in the reference year) and checked against the
version table.

``build_request``/``build_dataset_request`` and ``parse``/``parse_dataset_files``
are pure; :class:`PWT` wires them through :func:`cgjrkit.core.http.fetch`.
Licence CC BY 4.0; cite Feenstra, Inklaar and Timmer (2015).
"""

from __future__ import annotations

import functools
import hashlib
import io
import json
import re
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from cgjrkit.connectors.base import Connector
from cgjrkit.core import http
from cgjrkit.core.cache import Cache, Retrieval
from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import empty_frame, make_frame, ppp_base, validate_frame

DATAVERSE = "https://dataverse.nl"
ACCESS_URL = DATAVERSE + "/api/access/datafile/{file_id}"
DATASET_URL = DATAVERSE + "/api/datasets/:persistentId/"
RUG_DOCS = "https://www.rug.nl/ggdc/docs/"

#: Short form for chart source lines; the full reference is in ``FULL_CITATION`` and ``Result.meta``.
CITATION = "Penn World Table (Feenstra, Inklaar and Timmer 2015)"
FULL_CITATION = (
    "Feenstra, Robert C., Robert Inklaar and Marcel P. Timmer (2015), \"The Next Generation of the "
    "Penn World Table\", American Economic Review, 105(10), 3150-3182, available for download at www.ggdc.net/pwt"
)

DEFAULT_VERSION = "10.01"
FILE_FORMATS = ("xlsx", "dta")

#: Identifier columns of the PWT data table (not variables).
ID_COLUMNS = ("countrycode", "country", "currency_unit", "year")
#: Categorical flag columns: used for caveats, never returned as values.
FLAG_COLUMNS = ("i_cig", "i_xm", "i_xr", "i_outlier", "i_irr")


# ---------------------------------------------------------------------------
# versions and files
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PWTFile:
    """One downloadable data file of a PWT version."""

    filename: str
    file_id: int | None = None   # DataverseNL database id (``/api/access/datafile/<id>``)
    url: str = ""                # direct URL when the file is not on Dataverse


@dataclass(frozen=True)
class PWTVersion:
    version: str
    doi: str
    reference_year: int          # PPP base and constant-price reference year
    first_year: int
    last_year: int
    countries: int
    released: str
    files: dict[str, PWTFile] = field(default_factory=dict)

    @property
    def dataset(self) -> str:
        return f"PWT{self.version}"

    @property
    def vintage(self) -> str:
        return f"PWT {self.version}"

    @property
    def on_dataverse(self) -> bool:
        return any(f.file_id is not None for f in self.files.values())


VERSIONS: dict[str, PWTVersion] = {
    "10.01": PWTVersion(
        "10.01", "10.34894/QT5BCC", 2017, 1950, 2019, 183, "2023-01-23",
        {"xlsx": PWTFile("pwt1001.xlsx", 354095), "dta": PWTFile("pwt1001.dta", 354098)},
    ),
    "11.0": PWTVersion(
        "11.0", "10.34894/FABVLR", 2021, 1950, 2023, 185, "2025-10-07",
        # File names pwt110.* are third-party evidence (UNVERIFIED); ids are pinned from several clients.
        {"xlsx": PWTFile("pwt110.xlsx", 554105), "dta": PWTFile("pwt110.dta", 554030)},
    ),
    "10.0": PWTVersion(
        "10.0", "10.15141/S50T0R", 2017, 1950, 2019, 183, "",
        {"xlsx": PWTFile("pwt100.xlsx", url=RUG_DOCS + "pwt100.xlsx"),
         "dta": PWTFile("pwt100.dta", url=RUG_DOCS + "pwt100.dta")},
    ),
}

#: Variables read for a default fetch (growth accounting and benchmarking).
DEFAULT_VARIABLES = ("rgdpna", "rnna", "rkna", "rtfpna", "emp", "hc", "labsh", "pop")

_MONEY_CHAINED = "million {y} US$ at chained PPPs (PPP {y})"
_MONEY_CURRENT = "million {y} US$ at current PPPs (PPP {y})"
_MONEY_NA = "million {y} US$ at constant {y} national prices (PPP {y})"
_REL_USA = "index, USA=1 (current PPPs, PPP {y})"
_PRICE = "price level, USA GDPo in {y}=1 (PPP {y})"
_SHARE = "share at current PPPs (PPP {y})"

#: variable -> (label template, unit template). ``{y}`` is the reference year.
#: Labels follow the PWT Legend sheet (10.01 via the CRAN pwt10 docs, 11.0 via a
#: printout of pwt110.xlsx); units are written by cgjrkit from those definitions.
VARIABLES: dict[str, tuple[str, str]] = {
    "rgdpe": ("Expenditure-side real GDP at chained PPPs", _MONEY_CHAINED),
    "rgdpo": ("Output-side real GDP at chained PPPs", _MONEY_CHAINED),
    "pop": ("Population", "millions"),
    "emp": ("Number of persons engaged", "millions"),
    "avh": ("Average annual hours worked by persons engaged", "hours per person engaged per year"),
    "hc": ("Human capital index, based on years of schooling and returns to education", "index"),
    "ccon": ("Real consumption of households and government, at current PPPs", _MONEY_CURRENT),
    "cda": ("Real domestic absorption (real consumption plus investment), at current PPPs", _MONEY_CURRENT),
    "cgdpe": ("Expenditure-side real GDP at current PPPs", _MONEY_CURRENT),
    "cgdpo": ("Output-side real GDP at current PPPs", _MONEY_CURRENT),
    "cn": ("Capital stock at current PPPs", _MONEY_CURRENT),
    "ck": ("Capital services levels at current PPPs", _REL_USA),
    "ctfp": ("TFP level at current PPPs", _REL_USA),
    "cwtfp": ("Welfare-relevant TFP levels at current PPPs", _REL_USA),
    "rgdpna": ("Real GDP at constant {y} national prices", _MONEY_NA),
    "rconna": ("Real consumption at constant {y} national prices", _MONEY_NA),
    "rdana": ("Real domestic absorption at constant {y} national prices", _MONEY_NA),
    "rnna": ("Capital stock at constant {y} national prices", _MONEY_NA),
    "rkna": ("Capital services at constant {y} national prices", "index, {y}=1"),
    "rtfpna": ("TFP at constant national prices", "index, {y}=1"),
    "rwtfpna": ("Welfare-relevant TFP at constant national prices", "index, {y}=1"),
    "labsh": ("Share of labour compensation in GDP at current national prices", "share of GDP (0-1)"),
    "irr": ("Real internal rate of return", "rate (0-1)"),
    "delta": ("Average depreciation rate of the capital stock", "rate (0-1)"),
    "xr": ("Exchange rate, national currency/USD (market and estimated)", "national currency per US$"),
    "pl_con": ("Price level of CCON (PPP/XR)", _PRICE),
    "pl_da": ("Price level of CDA (PPP/XR)", _PRICE),
    "pl_gdpo": ("Price level of CGDPo (PPP/XR)", _PRICE),
    "cor_exp": ("Correlation between expenditure shares of the country and the US (benchmark observations only)",
                "correlation coefficient"),
    "statcap": ("Statistical capacity indicator (World Bank, developing countries only)", "index"),
    "csh_c": ("Share of household consumption at current PPPs", _SHARE),
    "csh_i": ("Share of gross capital formation at current PPPs", _SHARE),
    "csh_g": ("Share of government consumption at current PPPs", _SHARE),
    "csh_x": ("Share of merchandise exports at current PPPs", _SHARE),
    "csh_m": ("Share of merchandise imports at current PPPs", _SHARE),
    "csh_r": ("Share of residual trade and GDP statistical discrepancy at current PPPs", _SHARE),
    "pl_c": ("Price level of household consumption", _PRICE),
    "pl_i": ("Price level of capital formation", _PRICE),
    "pl_g": ("Price level of government consumption", _PRICE),
    "pl_x": ("Price level of exports", _PRICE),
    "pl_m": ("Price level of imports", _PRICE),
    "pl_n": ("Price level of the capital stock", "price level, USA in {y}=1 (PPP {y})"),
    "pl_k": ("Price level of the capital services", "price level, USA=1 (PPP {y})"),
}

#: Variables whose PPP-level comparisons are affected by ``i_outlier``.
_OUTLIER_SENSITIVE = {"rgdpe", "rgdpo", "cgdpe", "cgdpo", "ccon", "cda", "pl_con", "pl_da", "pl_gdpo"}

_VAR_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_ISO3_RE = re.compile(r"^[A-Z]{3}$")


class PWTError(RuntimeError):
    """A PWT payload could not be read or the Dataverse record is unusable."""


# ---------------------------------------------------------------------------
# small pure helpers
# ---------------------------------------------------------------------------
def normalize_version(version: str | float | int | None) -> str:
    """``"10.01"``, ``"1001"``, ``"pwt1001"``, ``"PWT 10.01"`` -> ``"10.01"``; ``"11"``/``"110"`` -> ``"11.0"``."""
    if version is None or str(version).strip() == "":
        return DEFAULT_VERSION
    s = re.sub(r"^pwt", "", str(version).strip().lower().replace(" ", ""))
    aliases = {"1001": "10.01", "10.01": "10.01", "11": "11.0", "11.0": "11.0", "110": "11.0",
               "11.00": "11.0", "10": "10.0", "10.0": "10.0", "100": "10.0"}
    if s in aliases:
        return aliases[s]
    raise ValueError(f"unknown PWT version {version!r}; supported: {sorted(VERSIONS)}")


def get_version(version: str | float | int | None) -> PWTVersion:
    return VERSIONS[normalize_version(version)]


def normalize_variables(variables: Sequence[str] | str | None) -> list[str]:
    """Lower-cased, de-duplicated variable names (order kept). Flags are rejected."""
    if variables is None:
        return list(DEFAULT_VARIABLES)
    items = re.split(r"[;,\s]+", variables) if isinstance(variables, str) else list(variables)
    out: list[str] = []
    for item in items:
        v = str(item).strip().lower()
        if not v:
            continue
        if v in ID_COLUMNS:
            continue
        if v in FLAG_COLUMNS:
            raise ValueError(f"{v!r} is a PWT flag variable; flags are used for caveats, not returned as values")
        if not _VAR_RE.match(v):
            raise ValueError(f"invalid PWT variable name {item!r}")
        if v not in out:
            out.append(v)
    if not out:
        raise ValueError("no PWT variables requested")
    return out


def normalize_countries(countries: Sequence[str] | str | None) -> list[str] | None:
    """Upper-case ISO3 codes, or ``None`` for all countries."""
    if countries is None or (isinstance(countries, str) and countries.strip().lower() in ("", "all")):
        return None
    items = re.split(r"[;,\s]+", countries) if isinstance(countries, str) else list(countries)
    out = []
    for item in items:
        s = str(item).strip().upper()
        if not s:
            continue
        if not _ISO3_RE.match(s):
            raise ValueError(f"invalid country code {item!r}: PWT uses ISO 3166-1 alpha-3 codes")
        if s not in out:
            out.append(s)
    return sorted(out)


def _year(v: int | str | None) -> int | None:
    if v is None or str(v).strip() == "":
        return None
    s = str(v).strip()
    if not re.fullmatch(r"\d{4}", s):
        raise ValueError(f"start/end must be 4-digit years, got {v!r}")
    return int(s)


def variable_meta(variable: str, reference_year: int) -> tuple[str, str]:
    """``(label, unit)`` for a variable at a reference year; unknown variables -> ``(variable, "")``."""
    label, unit = VARIABLES.get(variable, (variable, ""))
    return label.format(y=reference_year), unit.format(y=reference_year)


def is_ppp_based(variable: str) -> bool:
    _, unit = VARIABLES.get(variable, ("", ""))
    return "PPP {y}" in unit


# ---------------------------------------------------------------------------
# request builders (pure)
# ---------------------------------------------------------------------------
def build_request(version: str = DEFAULT_VERSION, file_format: str = "xlsx") -> tuple[str, dict]:
    """``(url, params)`` for the pinned data file of a version."""
    spec = get_version(version)
    fmt = file_format.lower().lstrip(".")
    if fmt not in spec.files:
        raise ValueError(f"unknown PWT file format {file_format!r}; use one of {sorted(spec.files)}")
    f = spec.files[fmt]
    if f.file_id is not None:
        return ACCESS_URL.format(file_id=f.file_id), {}
    return f.url, {}


def build_file_request(file_id: int, *, original: bool = False) -> tuple[str, dict]:
    """Dataverse access request for a file id; ``original=True`` asks for the saved
    original of an ingested tabular file (``format=original``)."""
    return ACCESS_URL.format(file_id=int(file_id)), ({"format": "original"} if original else {})


def build_dataset_request(version: str = DEFAULT_VERSION) -> tuple[str, dict]:
    """Dataverse native API request for the dataset record (latest published version)."""
    spec = get_version(version)
    if not spec.on_dataverse:
        raise ValueError(f"PWT {spec.version} is not hosted on DataverseNL in this connector")
    return DATASET_URL, {"persistentId": f"doi:{spec.doi}"}


# ---------------------------------------------------------------------------
# Dataverse dataset record (pure)
# ---------------------------------------------------------------------------
def parse_dataset_files(payload: bytes | str) -> tuple[list[dict], str]:
    """Return ``(files, dataset_version)`` from a Dataverse dataset JSON record.

    Each file is ``{id, filename, label, directory, content_type, size,
    checksum_type, checksum, original_format, original_name, tabular}``.
    """
    try:
        doc = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise PWTError(f"Dataverse did not return JSON: {str(payload)[:200]!r}") from exc
    if not isinstance(doc, dict) or doc.get("status") != "OK":
        msg = doc.get("message") if isinstance(doc, dict) else doc
        raise PWTError(f"Dataverse error: {msg}")
    data = doc.get("data") or {}
    latest = data.get("latestVersion") or data
    major, minor = latest.get("versionNumber"), latest.get("versionMinorNumber")
    ds_version = f"{major}.{minor}" if major is not None else ""
    files = []
    for entry in latest.get("files") or []:
        df = entry.get("dataFile") or {}
        if "id" not in df:
            continue
        checksum = df.get("checksum") or {}
        files.append({
            "id": int(df["id"]),
            "filename": str(df.get("filename") or entry.get("label") or ""),
            "label": str(entry.get("label") or ""),
            "directory": str(entry.get("directoryLabel") or ""),
            "content_type": str(df.get("contentType") or ""),
            "size": df.get("filesize"),
            "checksum_type": str(checksum.get("type") or ("MD5" if df.get("md5") else "")),
            "checksum": str(checksum.get("value") or df.get("md5") or ""),
            "original_format": str(df.get("originalFileFormat") or ""),
            "original_name": str(df.get("originalFileName") or ""),
            "tabular": bool(df.get("originalFileFormat")) or str(df.get("contentType", "")).startswith("text/tab-separated"),
        })
    return files, ds_version


def select_file(files: list[dict], version: str, file_format: str = "xlsx") -> dict:
    """Pick the main PWT data file of a version from a Dataverse file list."""
    spec = get_version(version)
    fmt = file_format.lower().lstrip(".")
    expected = spec.files[fmt].filename if fmt in spec.files else ""
    top = [f for f in files if not f["directory"]]
    for f in top:
        if expected and expected in (f["filename"], f["label"], f["original_name"]):
            return f
    pattern = re.compile(rf"^pwt\d+\.{re.escape(fmt)}$", re.I)
    for f in top:
        if any(pattern.match(n) for n in (f["filename"], f["label"], f["original_name"]) if n):
            return f
    names = sorted(f["filename"] for f in files)
    raise PWTError(f"no main PWT .{fmt} file in the Dataverse record of PWT {spec.version}; files: {names[:30]}")


def checksum_ok(payload: bytes, checksum_type: str, value: str) -> bool | None:
    """Compare a payload with a Dataverse checksum; ``None`` when it cannot be checked."""
    algo = {"MD5": "md5", "SHA-1": "sha1", "SHA1": "sha1", "SHA-256": "sha256", "SHA-512": "sha512"}.get(
        checksum_type.upper(), "")
    if not algo or not value:
        return None
    return hashlib.new(algo, payload).hexdigest().lower() == value.strip().lower()


# ---------------------------------------------------------------------------
# data file -> wide table (pure)
# ---------------------------------------------------------------------------
@dataclass
class PWTTable:
    """The PWT data table as read from a payload."""

    data: pd.DataFrame
    labels: dict[str, str] = field(default_factory=dict)
    file_format: str = ""
    sheet: str = ""
    member: str = ""


def sniff_format(payload: bytes) -> str:
    """``"xlsx"``, ``"zip"``, ``"dta"``, ``"tsv"``, ``"csv"``, ``"html"`` or ``"json"``."""
    head = bytes(payload[:16])
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                names = zf.namelist()
        except zipfile.BadZipFile as exc:
            raise PWTError(f"corrupt zip/xlsx payload: {exc}") from exc
        return "xlsx" if "[Content_Types].xml" in names else "zip"
    if head.startswith(b"<stata_dta>") or (len(head) > 2 and 104 <= head[0] <= 115 and head[1] in (1, 2) and head[2] == 1):
        return "dta"
    stripped = head.lstrip()
    if stripped[:1] == b"<":
        return "html"
    if stripped[:1] in (b"{", b"["):
        return "json"
    first_line = bytes(payload[:4096]).split(b"\n", 1)[0]
    return "tsv" if b"\t" in first_line else "csv"


def _legend_labels(raw: pd.DataFrame) -> dict[str, str]:
    """Variable definitions from the ``Legend`` sheet (two-cell rows ``name, definition``)."""
    labels: dict[str, str] = {}
    for row in raw.itertuples(index=False):
        cells = [c for c in row if isinstance(c, str) and c.strip()]
        if len(cells) >= 2 and _VAR_RE.match(cells[0].strip()):
            labels[cells[0].strip()] = cells[1].strip()
    return labels


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    if "countrycode" not in df.columns and "isocode" in df.columns:  # CRAN pwt10 naming
        df = df.rename(columns={"isocode": "countrycode"})
    return df


def _read_xlsx(payload: bytes) -> PWTTable:
    try:
        xl = pd.ExcelFile(io.BytesIO(payload), engine="openpyxl")
    except Exception as exc:  # openpyxl raises several exception types for broken files
        raise PWTError(f"unreadable PWT workbook: {exc}") from exc
    sheet = "Data" if "Data" in xl.sheet_names else ""
    if not sheet:
        for name in xl.sheet_names:
            head = xl.parse(name, nrows=0)
            if {"countrycode", "year"} <= {str(c).strip().lower() for c in head.columns}:
                sheet = name
                break
    if not sheet:
        raise PWTError(f"no PWT data sheet (with countrycode and year columns) in workbook sheets {xl.sheet_names}")
    data = _normalize_columns(xl.parse(sheet))
    labels = _legend_labels(xl.parse("Legend", header=None)) if "Legend" in xl.sheet_names else {}
    return PWTTable(data=data, labels=labels, file_format="xlsx", sheet=sheet)


def _read_dta(payload: bytes) -> PWTTable:
    def read(convert: bool) -> tuple[pd.DataFrame, dict[str, str]]:
        with pd.read_stata(io.BytesIO(payload), iterator=True, convert_categoricals=convert) as reader:
            frame = reader.read()
            return frame, dict(reader.variable_labels())

    try:
        try:
            data, labels = read(True)
        except ValueError:  # non-unique value labels
            data, labels = read(False)
    except Exception as exc:
        raise PWTError(f"unreadable PWT Stata file: {exc}") from exc
    labels = {str(k).strip().lower(): str(v).strip() for k, v in labels.items() if str(v).strip()}
    return PWTTable(data=_normalize_columns(data), labels=labels, file_format="dta")


def _read_text(payload: bytes, sep: str) -> PWTTable:
    try:
        data = pd.read_csv(io.BytesIO(payload), sep=sep, encoding="utf-8-sig", low_memory=False)
    except (pd.errors.ParserError, UnicodeDecodeError, pd.errors.EmptyDataError) as exc:
        raise PWTError(f"unreadable PWT text table: {exc}") from exc
    return PWTTable(data=_normalize_columns(data), file_format="tsv" if sep == "\t" else "csv")


@functools.lru_cache(maxsize=4)
def _read_table_cached(payload: bytes) -> PWTTable:
    fmt = sniff_format(payload)
    if fmt == "xlsx":
        table = _read_xlsx(payload)
    elif fmt == "dta":
        table = _read_dta(payload)
    elif fmt in ("tsv", "csv"):
        table = _read_text(payload, "\t" if fmt == "tsv" else ",")
    elif fmt == "zip":
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            members = [n for n in zf.namelist() if re.search(r"(^|/)pwt\d+\.(xlsx|dta)$", n, re.I)]
            if not members:
                raise PWTError(f"no pwt*.xlsx/.dta file in zip archive: {zf.namelist()[:20]}")
            member = sorted(members, key=lambda n: (n.count("/"), not n.lower().endswith(".xlsx"), n))[0]
            inner = zf.read(member)
        table = _read_table_cached(inner)
        table = PWTTable(table.data, table.labels, table.file_format, table.sheet, member)
    elif fmt == "json":
        try:
            doc = json.loads(payload)
            msg = doc.get("message", doc) if isinstance(doc, dict) else doc
        except ValueError:
            msg = bytes(payload[:200])
        raise PWTError(f"the server returned JSON instead of a PWT file: {msg}")
    else:
        raise PWTError(f"the server returned HTML instead of a PWT file: {bytes(payload[:200])!r}")
    missing = [c for c in ("countrycode", "year") if c not in table.data.columns]
    if missing:
        raise PWTError(f"PWT table lacks identifier columns {missing}; columns: {list(table.data.columns)[:20]}")
    return table


def read_table(payload: bytes) -> PWTTable:
    """Read a PWT file (xlsx, dta, tab/csv text or a zip holding one) into a wide table.

    Results are memoised per payload (files are 3-7 MB and take seconds to parse);
    the returned frame is a copy, so callers may modify it.
    """
    table = _read_table_cached(bytes(payload))
    return PWTTable(table.data.copy(), dict(table.labels), table.file_format, table.sheet, table.member)


def infer_reference_year(wide: pd.DataFrame) -> int | None:
    """Year in which ``rkna`` (or ``rtfpna``/``rwtfpna``) equals 1 for most countries."""
    for col in ("rkna", "rtfpna", "rwtfpna"):
        if col not in wide.columns:
            continue
        v = pd.to_numeric(wide[col], errors="coerce")
        countries = wide.loc[v.notna(), "countrycode"].nunique()
        hits = wide.loc[(v - 1.0).abs() < 1e-6, "year"]
        if countries and len(hits):
            counts = pd.to_numeric(hits, errors="coerce").dropna().astype(int).value_counts()
            if len(counts) and counts.iloc[0] >= max(1, 0.5 * countries):
                top = counts[counts == counts.iloc[0]].index.min()
                return int(top)
    return None


def _flag_true(value: Any, truthy: Iterable[str]) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    s = str(value).strip().lower()
    if s.endswith(".0"):
        s = s[:-2]
    return s in set(truthy)


def _flagged(sel: pd.DataFrame, column: str, truthy: Iterable[str]) -> list[dict]:
    """``[{iso3, year}]`` for rows whose flag column is set."""
    if column not in sel.columns or not len(sel):
        return []
    mask = sel[column].map(lambda v: _flag_true(v, truthy)).astype(bool)
    return [{"iso3": str(c), "year": int(y)} for c, y in zip(sel.loc[mask, "countrycode"], sel.loc[mask, "year"], strict=True)]


# ---------------------------------------------------------------------------
# wide table -> canonical frame (pure)
# ---------------------------------------------------------------------------
@dataclass
class ParsedPWT:
    frame: pd.DataFrame
    version: str
    reference_year: int
    inferred_reference_year: int | None = None
    variables: list[str] = field(default_factory=list)
    missing_variables: list[str] = field(default_factory=list)
    missing_countries: list[str] = field(default_factory=list)
    outliers: list[dict] = field(default_factory=list)
    estimated_xr: list[dict] = field(default_factory=list)
    file_labels: dict[str, str] = field(default_factory=dict)
    file_format: str = ""
    file_years: tuple[int, int] | None = None
    file_countries: int = 0


def parse(
    payload: bytes,
    *,
    version: str = DEFAULT_VERSION,
    variables: Sequence[str] | str | None = None,
    countries: Sequence[str] | str | None = None,
    start: int | str | None = None,
    end: int | str | None = None,
    dropna: bool = True,
) -> ParsedPWT:
    """Parse a PWT data file into the canonical long frame.

    ``indicator`` is the PWT variable name and ``period`` the year. Rows with
    missing values are dropped unless ``dropna=False``.
    """
    spec = get_version(version)
    wanted = normalize_variables(variables)
    iso = normalize_countries(countries)
    y0, y1 = _year(start), _year(end)
    if y0 is not None and y1 is not None and y0 > y1:
        raise ValueError(f"start ({y0}) is after end ({y1})")

    table = read_table(payload)
    wide = table.data
    years_all = pd.to_numeric(wide["year"], errors="coerce")
    wide = wide.loc[years_all.notna()].copy()
    wide["year"] = years_all[years_all.notna()].astype(int)
    wide["countrycode"] = wide["countrycode"].astype(str).str.strip().str.upper()
    if "country" not in wide.columns:
        wide["country"] = ""

    inferred = infer_reference_year(wide)
    ref_year = inferred if inferred is not None else spec.reference_year
    present = [v for v in wanted if v in wide.columns]
    missing_vars = [v for v in wanted if v not in wide.columns]

    mask = pd.Series(True, index=wide.index)
    missing_countries: list[str] = []
    if iso is not None:
        mask &= wide["countrycode"].isin(iso)
        have = set(wide["countrycode"])
        missing_countries = [c for c in iso if c not in have]
    if y0 is not None:
        mask &= wide["year"] >= y0
    if y1 is not None:
        mask &= wide["year"] <= y1
    sel = wide.loc[mask]

    outliers = _flagged(sel, "i_outlier", ("1", "outlier", "yes", "true"))
    estimated_xr = _flagged(sel, "i_xr", ("1", "estimated", "yes")) if "xr" in present else []

    if present and len(sel):
        long = sel[["countrycode", "country", "year", *present]].melt(
            id_vars=["countrycode", "country", "year"], var_name="indicator", value_name="value")
        long["value"] = pd.to_numeric(long["value"], errors="coerce")
        if dropna:
            long = long[long["value"].notna()]
        meta = {v: variable_meta(v, ref_year) for v in present}
        for v in present:  # variables outside the catalogue keep the file's own definition
            if v not in VARIABLES and v in table.labels:
                meta[v] = (table.labels[v], "")
        records = pd.DataFrame({
            "indicator": long["indicator"].astype(str),
            "indicator_label": long["indicator"].map(lambda v: meta[v][0]),
            "iso3": long["countrycode"],
            "country": long["country"].astype(str),
            "period": long["year"].astype(int).astype(str),
            "value": long["value"],
            "unit": long["indicator"].map(lambda v: meta[v][1]),
        })
        frame = make_frame(records, source="pwt", dataset=spec.dataset, obs_status="")
    else:
        frame = empty_frame()
    yrs = (int(wide["year"].min()), int(wide["year"].max())) if len(wide) else None
    return ParsedPWT(
        frame=frame, version=spec.version, reference_year=ref_year, inferred_reference_year=inferred,
        variables=present, missing_variables=missing_vars, missing_countries=missing_countries,
        outliers=outliers, estimated_xr=estimated_xr,
        file_labels={v: table.labels[v] for v in present if v in table.labels},
        file_format=table.file_format, file_years=yrs, file_countries=int(wide["countrycode"].nunique()),
    )


# ---------------------------------------------------------------------------
# caveats (pure)
# ---------------------------------------------------------------------------
def build_caveats(info: ParsedPWT) -> list[Caveat]:
    spec = get_version(info.version)
    df = info.frame
    out: list[Caveat] = []
    if any(is_ppp_based(v) for v in info.variables):
        out.append(Caveat(
            "pwt_ppp_base", "info",
            f"PWT {spec.version} PPP-based variables are in {info.reference_year} PPPs. PWT 10.x uses 2017 PPPs and "
            "PWT 11.0 uses 2021 PPPs: never combine levels from different versions.",
            {"version": spec.version, "ppp_base": str(info.reference_year)}))
    if info.inferred_reference_year is not None and info.inferred_reference_year != spec.reference_year:
        out.append(Caveat(
            "pwt_reference_year_mismatch", "warn",
            f"The file's reference year ({info.inferred_reference_year}) differs from the expected "
            f"{spec.reference_year} for PWT {spec.version}; units use the file's year. Check the version.",
            {"expected": spec.reference_year, "inferred": info.inferred_reference_year}))
    if len(df):
        out.append(Caveat(
            "pwt_coverage", "info",
            f"PWT {spec.version} ends in {spec.last_year}; later years need another source (and a disclosed splice).",
            {"version": spec.version, "last_year": spec.last_year,
             "first_period": str(df["period"].min()), "last_period": str(df["period"].max())}))
    sensitive = sorted(set(info.variables) & _OUTLIER_SENSITIVE)
    if info.outliers and sensitive:
        out.append(Caveat(
            "pwt_outlier", "warn",
            "PWT flags some country-years as outliers in their PPP price levels (i_outlier); PPP-level comparisons "
            "for those observations are unreliable.",
            {"variables": sensitive, "observations": info.outliers[:50], "count": len(info.outliers)}))
    if info.estimated_xr:
        out.append(Caveat(
            "pwt_estimated_xr", "info", "Some exchange rates (xr) are estimated rather than market rates (i_xr).",
            {"observations": info.estimated_xr[:50], "count": len(info.estimated_xr)}))
    if spec.version == "11.0" and len(df) and "CHN" in set(df["iso3"]):
        out.append(Caveat(
            "pwt_china_series", "info",
            "PWT 11.0 uses China's official national accounts growth rates instead of the earlier adjusted series; "
            "China's growth and TFP differ from PWT 10.x.", {"iso3": "CHN"}))
    if info.missing_variables:
        out.append(Caveat("pwt_missing_variables", "warn", "Some requested variables are not in this PWT file.",
                          {"variables": info.missing_variables, "version": spec.version}))
    if info.missing_countries:
        out.append(Caveat("pwt_missing_countries", "warn", "Some requested countries are not in this PWT file.",
                          {"countries": info.missing_countries, "version": spec.version}))
    if len(df) == 0:
        out.append(Caveat("pwt_empty", "warn", "No observations matched the request.",
                          {"version": spec.version, "variables": info.variables}))
    return out


def _frames(objs: Any) -> list[pd.DataFrame]:
    if isinstance(objs, (pd.DataFrame, Result)):
        objs = [objs]
    return [o.data if isinstance(o, Result) else o for o in objs]


def pwt_mix_caveats(objs: pd.DataFrame | Result | Sequence[pd.DataFrame | Result]) -> list[Caveat]:
    """Block combining PWT rows on different PPP bases; warn on mixed PWT versions."""
    frames = [f[f["source"] == "pwt"] for f in _frames(objs) if len(f)]
    rows = pd.concat(frames, ignore_index=True) if frames else empty_frame()
    out: list[Caveat] = []
    if not len(rows):
        return out
    bases = sorted({b for b in (ppp_base(u) for u in rows["unit"].unique()) if b})
    datasets = sorted(rows["dataset"].unique())
    if len(bases) > 1:
        out.append(Caveat("pwt_mixed_ppp", "block",
                          "PWT series on different PPP bases are combined (e.g. PWT 10.01 at 2017 PPPs with PWT 11.0 "
                          "at 2021 PPPs). Use one PWT version.", {"ppp_bases": bases, "datasets": datasets}))
    elif len(datasets) > 1:
        out.append(Caveat("pwt_mixed_versions", "warn",
                          "Series from different PWT versions are combined; revisions make them inconsistent.",
                          {"datasets": datasets}))
    return out


# ---------------------------------------------------------------------------
# connector
# ---------------------------------------------------------------------------
class PWT(Connector):
    """Penn World Table 10.01 / 11.0 (DataverseNL, DOI-pinned) and 10.0 (rug.nl)."""

    source = "pwt"
    license = "CC BY 4.0"
    citation = CITATION
    domains = ("dataverse.nl", "www.rug.nl")

    def __init__(self, cache: Cache | None = None):
        super().__init__(cache)

    def _get(self, url: str, params: dict, refresh: bool) -> tuple[bytes, Retrieval]:
        try:
            return http.fetch(self.source, url, params, cache=self.cache, refresh=refresh, timeout=600.0)
        except http.FetchError as exc:
            raise PWTError(f"PWT download failed ({url}): {exc}") from exc

    def dataset_files(self, version: str = DEFAULT_VERSION, refresh: bool = False) -> tuple[list[dict], str, Provenance]:
        """List the files of a version's Dataverse record: ``(files, dataset_version, provenance)``."""
        spec = get_version(version)
        url, params = build_dataset_request(spec.version)
        payload, retrieval = self._get(url, params, refresh)
        files, ds_version = parse_dataset_files(payload)
        prov = self.provenance(retrieval, dataset=spec.dataset, vintage=spec.vintage, tool="PWT.dataset_files",
                               notes=[f"DOI {spec.doi}", f"Dataverse dataset version {ds_version}"])
        return files, ds_version, prov

    def fetch(
        self,
        version: str = DEFAULT_VERSION,
        variables: Sequence[str] | str | None = DEFAULT_VARIABLES,
        countries: Sequence[str] | str | None = None,
        start: int | str | None = None,
        end: int | str | None = None,
        *,
        file_format: str = "xlsx",
        resolve: bool = False,
        url: str | None = None,
        dropna: bool = True,
        refresh: bool = False,
    ) -> Result:
        """PWT variables as a canonical frame.

        ``version``: ``"10.01"`` (default, 2017 PPPs, to 2019), ``"11.0"`` (2021 PPPs,
        to 2023) or ``"10.0"``. ``variables``: PWT names such as ``rgdpna``, ``rnna``,
        ``rkna``, ``emp``, ``hc``, ``rtfpna``, ``labsh``, ``pop``, ``rgdpo``, ``cgdpo``,
        ``avh``, ``delta``, ``csh_i``. ``countries``: ISO3 codes or ``None`` for all.
        ``file_format``: ``"xlsx"`` or ``"dta"``. ``resolve=True`` looks the file up in
        the Dataverse dataset record instead of using the pinned file id. ``url``
        overrides the download location (a mirror or local copy served over HTTP).
        """
        spec = get_version(version)
        fmt = file_format.lower().lstrip(".")
        if fmt not in FILE_FORMATS:
            raise ValueError(f"file_format must be one of {FILE_FORMATS}, got {file_format!r}")
        wanted = normalize_variables(variables)
        provs: list[Provenance] = []
        notes = [f"DOI {spec.doi}"]
        file_meta: dict[str, Any] = {"format": fmt}
        expected_checksum: tuple[str, str] | None = None

        if url:
            req_url, params = url, {}
            notes.append("custom download URL")
            file_meta["url"] = url
        elif resolve and spec.on_dataverse:
            files, ds_version, meta_prov = self.dataset_files(spec.version, refresh=refresh)
            provs.append(meta_prov)
            chosen = select_file(files, spec.version, fmt)
            req_url, params = build_file_request(chosen["id"], original=chosen["tabular"])
            expected_checksum = (chosen["checksum_type"], chosen["checksum"])
            file_meta.update({"file_id": chosen["id"], "filename": chosen["original_name"] or chosen["filename"],
                              "dataset_version": ds_version, "checksum": chosen["checksum"],
                              "checksum_type": chosen["checksum_type"]})
            notes += [f"file {file_meta['filename']} (Dataverse id {chosen['id']})", f"Dataverse dataset version {ds_version}"]
        else:
            req_url, params = build_request(spec.version, fmt)
            pinned = spec.files[fmt]
            file_meta.update({"file_id": pinned.file_id, "filename": pinned.filename})
            notes.append(f"file {pinned.filename}" + (f" (Dataverse id {pinned.file_id})" if pinned.file_id else ""))

        payload, retrieval = self._get(req_url, params, refresh)
        info = parse(payload, version=spec.version, variables=wanted, countries=countries, start=start, end=end,
                     dropna=dropna)
        df = self.finalize(info.frame) if len(info.frame) else info.frame
        validate_frame(df)
        info.frame = df
        caveats = build_caveats(info)
        if expected_checksum is not None:
            ok = checksum_ok(payload, *expected_checksum)
            file_meta["checksum_ok"] = ok
            if ok is False:
                caveats.append(Caveat("pwt_checksum_mismatch", "warn",
                                      "The downloaded file does not match the Dataverse checksum (the server may have "
                                      "returned a converted copy).", {"expected": expected_checksum[1],
                                                                       "type": expected_checksum[0]}))
        notes += [f"reference year / PPP base {info.reference_year}", f"read as {info.file_format}"]
        provs.append(self.provenance(retrieval, dataset=spec.dataset, vintage=spec.vintage, tool="PWT.fetch",
                                     notes=notes))
        meta = {
            "version": spec.version,
            "doi": spec.doi,
            "citation": FULL_CITATION,
            "reference_year": info.reference_year,
            "ppp_base": str(info.reference_year),
            "inferred_reference_year": info.inferred_reference_year,
            "last_year": spec.last_year,
            "file": file_meta,
            "file_format_read": info.file_format,
            "file_years": list(info.file_years) if info.file_years else None,
            "file_countries": info.file_countries,
            "variables": info.variables,
            "missing_variables": info.missing_variables,
            "missing_countries": info.missing_countries,
            "file_labels": info.file_labels,
            "aggregates": [],
            "params": {"version": spec.version, "variables": wanted, "countries": countries, "start": start,
                       "end": end, "file_format": fmt, "resolve": resolve, "dropna": dropna},
        }
        return Result(data=df, provenance=provs, caveats=caveats, meta=meta)
