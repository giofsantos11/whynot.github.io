"""UN World Population Prospects 2024 connector (bulk CSV; Data Portal API optional).

Bulk files (keyless, gzip-compressed CSV, UTF-8 with BOM) live under::

    https://population.un.org/wpp/assets/Excel%20Files/1_Indicator%20(Standard)/CSV_FILES/<file>

and are listed in the catalogue ``https://population.un.org/wpp/assets/downloads.json``.
Every file starts with the location and variant columns ``SortOrder, LocID,
Notes, ISO3_code, ISO2_code, SDMX_code, LocTypeID, LocTypeName, ParentID,
Location, VarID, Variant, Time``; age tables add ``MidPeriod, AgeGrp,
AgeGrpStart, AgeGrpSpan`` and the population tables carry ``PopMale,
PopFemale, PopTotal`` (thousands). Aggregates (regions, income groups, SDG
regions, special groupings) have an empty ``ISO3_code``; they are returned
with ``iso3 = "LOC<LocID>"`` (e.g. ``LOC900`` = World) and listed in
``Result.meta["aggregates"]``.

Files used (see ``docs/apis/wpp.md`` for what is verified):

* ``WPP2024_Demographic_Indicators_Medium.csv.gz``: 54 indicators, Medium variant
  (``WPP2024_Demographic_Indicators_OtherVariants.csv.gz`` for other variants,
  UNVERIFIED name);
* ``WPP2024_TotalPopulationBySex.csv.gz``: total population by sex, all 18 variants;
* ``WPP2024_PopulationByAge5GroupSex_Medium.csv.gz``: 5-year age groups, 1 July
  (one third-party note says this file is missing from the portal, so the
  connector falls back to the 1 January file when it is not found);
* ``WPP2024_Population1JanuaryByAge5GroupSex_Medium.csv.gz`` and ``..._OtherVariants``.

Estimates cover 1950-2023 and projections 2024-2100: rows from 2024 get
``obs_status="forecast"``. Vintage is ``"WPP 2024"``; the January 2026
interim update (Togo only) is not in the bulk CSV and raises a caveat when
Togo is in the result.

The Data Portal API (``https://population.un.org/dataportalapi/api/v1``) needs
a token sent as ``Authorization: Bearer <token>``; set ``CGJRKIT_WPP_KEY`` to
use :meth:`WPP.fetch_api`. Its JSON field names are third-party evidence
(UNVERIFIED live).

Design: :func:`build_request`, :func:`parse_bulk`, :func:`aggregate_ages`,
:func:`pyramid_table`, :func:`build_api_request` and :func:`parse_api` are
pure; :class:`WPP` wires them through :func:`cgjrkit.core.http.fetch`.
"""

from __future__ import annotations

import gzip
import io
import json
import math
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from cgjrkit.connectors.base import Connector
from cgjrkit.core import http
from cgjrkit.core.cache import Cache
from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import empty_frame, make_frame, validate_frame

BULK_BASE = "https://population.un.org/wpp/assets/Excel%20Files/1_Indicator%20(Standard)/CSV_FILES/"
CATALOG_URL = "https://population.un.org/wpp/assets/downloads.json"
API_BASE = "https://population.un.org/dataportalapi/api/v1"
KEY_ENV = "CGJRKIT_WPP_KEY"

EDITION = "WPP 2024"
DATASET = "WPP2024"
FIRST_PROJECTION_YEAR = 2024
LAST_YEAR = 2100
DEFAULT_VARIANT = "Medium"
CITATION = (
    "United Nations, Department of Economic and Social Affairs, Population Division (2024). "
    "World Population Prospects 2024, Online Edition"
)

#: Bulk files by key. ``variants``: "medium" (Medium only), "all" (every
#: variant in one file) or "other" (every variant except Medium).
@dataclass(frozen=True)
class BulkFile:
    name: str
    variants: str
    reference: str = ""          # "1 July", "1 January" or "" (period indicators)
    verified: bool = True        # file name seen in third-party download scripts / catalogue notes


FILES: dict[str, BulkFile] = {
    "demographic_indicators": BulkFile("WPP2024_Demographic_Indicators_Medium.csv.gz", "medium"),
    "demographic_indicators_other": BulkFile("WPP2024_Demographic_Indicators_OtherVariants.csv.gz", "other", verified=False),
    "total_population": BulkFile("WPP2024_TotalPopulationBySex.csv.gz", "all", "1 July"),
    "population_age5": BulkFile("WPP2024_PopulationByAge5GroupSex_Medium.csv.gz", "medium", "1 July", verified=False),
    "population_age5_jan1": BulkFile("WPP2024_Population1JanuaryByAge5GroupSex_Medium.csv.gz", "medium", "1 January"),
    "population_age5_jan1_other": BulkFile("WPP2024_Population1JanuaryByAge5GroupSex_OtherVariants.csv.gz", "other", "1 January"),
}

#: Projection variants and their ``VarID`` (WPP 2024 TotalPopulationBySex; the
#: misspelling of 19 is the provider's).
VARIANTS: dict[str, int] = {
    "Medium": 2, "High": 3, "Low": 4, "Constant fertility": 5, "Instant replacement": 6, "Zero migration": 7,
    "Constant mortality": 8, "No change": 9, "Momentum": 10, "Instant replacement zero migration": 16,
    "No fertility below age 18": 17, "Accelerated ABR decline": 18, "Accelarated ABR decline w/rec.": 19,
    "Median PI": 202, "Upper 80 PI": 203, "Lower 80 PI": 204, "Upper 95 PI": 206, "Lower 95 PI": 207,
}

LOCATION_COLUMNS = ["LocID", "ISO3_code", "LocTypeName", "Location", "VarID", "Variant", "Time"]
POP_COLUMNS = {"PopTotal": "both sexes", "PopMale": "male", "PopFemale": "female"}
SEX_ALIASES = {"total": "PopTotal", "both": "PopTotal", "male": "PopMale", "female": "PopFemale",
               "poptotal": "PopTotal", "popmale": "PopMale", "popfemale": "PopFemale"}

#: Demographic Indicators columns -> (label, unit). Labels written by cgjrkit
#: after the WPP 2024 metadata file (WPP2024_F02_Metadata.xlsx, not read here);
#: units for population, births, deaths and migration (thousands), rates per
#: 1,000, growth (%) and life expectancy (years) are confirmed by the
#: third-party description in docs/apis/wpp.md; the others are UNVERIFIED wording.
INDICATORS: dict[str, tuple[str, str]] = {
    "TPopulation1Jan": ("Total population, as of 1 January", "thousands"),
    "TPopulation1July": ("Total population, as of 1 July", "thousands"),
    "TPopulationMale1July": ("Male population, as of 1 July", "thousands"),
    "TPopulationFemale1July": ("Female population, as of 1 July", "thousands"),
    "PopDensity": ("Population density, as of 1 July", "persons per square km"),
    "PopSexRatio": ("Population sex ratio, as of 1 July", "males per 100 females"),
    "MedianAgePop": ("Median age, as of 1 July", "years"),
    "NatChange": ("Natural change, births minus deaths", "thousands"),
    "NatChangeRT": ("Rate of natural change", "per 1,000 population"),
    "PopChange": ("Population change", "thousands"),
    "PopGrowthRate": ("Population growth rate", "%"),
    "DoublingTime": ("Population annual doubling time", "years"),
    "Births": ("Births", "thousands"),
    "Births1519": ("Births by women aged 15 to 19", "thousands"),
    "CBR": ("Crude birth rate", "births per 1,000 population"),
    "TFR": ("Total fertility rate", "live births per woman"),
    "NRR": ("Net reproduction rate", "surviving daughters per woman"),
    "MAC": ("Mean age at childbearing", "years"),
    "SRB": ("Sex ratio at birth", "males per 100 female births"),
    "Deaths": ("Total deaths", "thousands"),
    "DeathsMale": ("Male deaths", "thousands"),
    "DeathsFemale": ("Female deaths", "thousands"),
    "CDR": ("Crude death rate", "deaths per 1,000 population"),
    "LEx": ("Life expectancy at birth, both sexes", "years"),
    "LExMale": ("Male life expectancy at birth", "years"),
    "LExFemale": ("Female life expectancy at birth", "years"),
    "LE15": ("Life expectancy at age 15, both sexes", "years"),
    "LE15Male": ("Male life expectancy at age 15", "years"),
    "LE15Female": ("Female life expectancy at age 15", "years"),
    "LE65": ("Life expectancy at age 65, both sexes", "years"),
    "LE65Male": ("Male life expectancy at age 65", "years"),
    "LE65Female": ("Female life expectancy at age 65", "years"),
    "LE80": ("Life expectancy at age 80, both sexes", "years"),
    "LE80Male": ("Male life expectancy at age 80", "years"),
    "LE80Female": ("Female life expectancy at age 80", "years"),
    "InfantDeaths": ("Infant deaths, under age 1", "thousands"),
    "IMR": ("Infant mortality rate", "infant deaths per 1,000 live births"),
    "LBsurvivingAge1": ("Live births surviving to age 1", "thousands"),
    "Under5Deaths": ("Deaths under age 5", "thousands"),
    "Q5": ("Under-five mortality", "deaths under age 5 per 1,000 live births"),
    "Q0040": ("Mortality before age 40, both sexes", "deaths under age 40 per 1,000 live births"),
    "Q0040Male": ("Male mortality before age 40", "deaths under age 40 per 1,000 male live births"),
    "Q0040Female": ("Female mortality before age 40", "deaths under age 40 per 1,000 female live births"),
    "Q0060": ("Mortality before age 60, both sexes", "deaths under age 60 per 1,000 live births"),
    "Q0060Male": ("Male mortality before age 60", "deaths under age 60 per 1,000 male live births"),
    "Q0060Female": ("Female mortality before age 60", "deaths under age 60 per 1,000 female live births"),
    "Q1550": ("Mortality between age 15 and 50, both sexes", "deaths under age 50 per 1,000 alive at age 15"),
    "Q1550Male": ("Male mortality between age 15 and 50", "deaths under age 50 per 1,000 males alive at age 15"),
    "Q1550Female": ("Female mortality between age 15 and 50", "deaths under age 50 per 1,000 females alive at age 15"),
    "Q1560": ("Mortality between age 15 and 60, both sexes", "deaths under age 60 per 1,000 alive at age 15"),
    "Q1560Male": ("Male mortality between age 15 and 60", "deaths under age 60 per 1,000 males alive at age 15"),
    "Q1560Female": ("Female mortality between age 15 and 60", "deaths under age 60 per 1,000 females alive at age 15"),
    "NetMigrations": ("Net number of migrants", "thousands"),
    "CNMR": ("Net migration rate", "per 1,000 population"),
}

#: Data Portal indicator ids seen in third-party code (UNVERIFIED labels).
API_INDICATORS: dict[int, str] = {49: "Total population by sex", 47: "Population by broad age groups and sex (UNVERIFIED)"}

_ISO3_RE = re.compile(r"^[A-Z]{3}$")
_AGE_RE = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+)|\+)\s*$")


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------
class WPPError(RuntimeError):
    """A WPP payload could not be read, or a required token is missing."""


# ---------------------------------------------------------------------------
# small pure helpers
# ---------------------------------------------------------------------------
def normalize_variant(variant: str | int | None) -> str:
    """Canonical variant name (``"medium"`` -> ``"Medium"``, ``3`` -> ``"High"``)."""
    if variant is None or str(variant).strip() == "":
        return DEFAULT_VARIANT
    if isinstance(variant, int) or str(variant).strip().isdigit():
        vid = int(variant)
        for name, i in VARIANTS.items():
            if i == vid:
                return name
        raise ValueError(f"unknown WPP VarID {variant!r}; known: {sorted(VARIANTS.values())}")
    key = str(variant).strip().lower()
    for name in VARIANTS:
        if name.lower() == key:
            return name
    raise ValueError(f"unknown WPP variant {variant!r}; known: {list(VARIANTS)}")


def dataset_for(variant: str) -> str:
    """``"WPP2024"`` for the Medium variant, ``"WPP2024 High"`` etc. otherwise, so
    variants never collide on the canonical key."""
    v = normalize_variant(variant)
    return DATASET if v == DEFAULT_VARIANT else f"{DATASET} {v}"


def normalize_countries(countries: Sequence[str | int] | str | None) -> tuple[list[str] | None, list[str]]:
    """Return ``(iso3_codes, loc_ids)``; ``None``/``"all"`` -> ``(None, [])``.

    ISO3 codes are upper-cased; numeric codes and ``LOC<id>`` are UN location
    ids (M49 for countries, e.g. ``404`` Kenya, ``900`` World)."""
    if countries is None or (isinstance(countries, str) and countries.strip().lower() in ("", "all")):
        return None, []
    items = re.split(r"[;,\s]+", countries) if isinstance(countries, str) else list(countries)
    iso: list[str] = []
    loc: list[str] = []
    for item in items:
        s = str(item).strip().upper()
        if not s:
            continue
        if s.startswith("LOC") and s[3:].isdigit():
            s = s[3:]
        if s.isdigit():
            loc.append(str(int(s)))
        elif _ISO3_RE.match(s):
            iso.append(s)
        else:
            raise ValueError(f"invalid WPP location {item!r}: use ISO3 codes or numeric UN location ids")
    return sorted(set(iso)), sorted(set(loc), key=int)


def _year(v: int | str | None) -> int | None:
    if v is None or str(v).strip() == "":
        return None
    s = str(v).strip()
    if not re.fullmatch(r"\d{4}", s):
        raise ValueError(f"start/end must be 4-digit years, got {v!r}")
    return int(s)


def parse_age_group(label: str) -> tuple[int, float]:
    """``"15-19"`` -> ``(15, 19)``; ``"100+"`` -> ``(100, inf)``."""
    m = _AGE_RE.match(str(label))
    if not m:
        raise ValueError(f"cannot read age group {label!r}")
    lo = int(m.group(1))
    return lo, (float(m.group(2)) if m.group(2) else math.inf)


def file_url(key: str) -> str:
    if key not in FILES:
        raise ValueError(f"unknown WPP file key {key!r}; known: {sorted(FILES)}")
    return BULK_BASE + FILES[key].name


def build_request(key: str) -> tuple[str, dict]:
    """Bulk file request ``(url, params)``; bulk files take no parameters."""
    return file_url(key), {}


def indicator_meta(column: str) -> tuple[str, str]:
    return INDICATORS.get(column, (column, ""))


# ---------------------------------------------------------------------------
# bulk payload -> canonical frame (pure)
# ---------------------------------------------------------------------------
@dataclass
class ParsedWPP:
    frame: pd.DataFrame
    variant: str = DEFAULT_VARIANT
    file: str = ""
    reference: str = ""
    aggregates: dict[str, str] = field(default_factory=dict)
    missing_locations: list[str] = field(default_factory=list)
    missing_columns: list[str] = field(default_factory=list)
    age_groups: list[str] = field(default_factory=list)
    rows_read: int = 0


def _reader(payload: bytes) -> io.BufferedIOBase:
    data = bytes(payload)
    if data[:2] == b"\x1f\x8b":
        return gzip.GzipFile(fileobj=io.BytesIO(data))  # type: ignore[return-value]
    return io.BytesIO(data)


def read_bulk(
    payload: bytes,
    columns: Sequence[str],
    row_filter: Callable[[pd.DataFrame], pd.Series] | None = None,
    *,
    chunksize: int = 250_000,
    url: str = "",
) -> tuple[pd.DataFrame, list[str], int]:
    """Read selected columns of a WPP CSV (gzip or plain, BOM-tolerant) in chunks,
    keeping only rows that pass ``row_filter``. Returns ``(frame, missing_columns, rows_read)``."""
    head = bytes(payload[:64])
    if head.lstrip().startswith((b"<", b"{")):
        raise WPPError(f"WPP returned HTML/JSON instead of a CSV file from {url or 'population.un.org'}: {head!r}")
    wanted = set(columns)
    seen: list[str] = []

    def use(col: str) -> bool:
        c = col.lstrip("﻿")
        if c not in seen:
            seen.append(c)
        return c in wanted

    parts: list[pd.DataFrame] = []
    rows = 0
    try:
        reader = pd.read_csv(_reader(payload), dtype=str, keep_default_na=False, encoding="utf-8-sig",
                             usecols=use, chunksize=chunksize)
        for chunk in reader:
            chunk.columns = [c.lstrip("﻿") for c in chunk.columns]
            rows += len(chunk)
            if row_filter is not None and len(chunk):
                chunk = chunk[row_filter(chunk)]
            if len(chunk):
                parts.append(chunk)
    except (OSError, EOFError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        raise WPPError(f"unreadable WPP CSV from {url or 'population.un.org'}: {exc}") from exc
    except pd.errors.EmptyDataError:
        return pd.DataFrame(columns=list(columns)), list(columns), 0
    missing = [c for c in columns if c not in seen]
    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=[c for c in columns if c in seen])
    return df, missing, rows


def _row_filter(iso: list[str] | None, loc: list[str], start: int | None, end: int | None,
                variant: str | None, include_aggregates: bool) -> Callable[[pd.DataFrame], pd.Series]:
    vid = str(VARIANTS[variant]) if variant else None

    def keep(df: pd.DataFrame) -> pd.Series:
        mask = pd.Series(True, index=df.index)
        if iso is not None:
            loc_mask = df["ISO3_code"].isin(iso) | df["LocID"].isin(loc)
            mask &= loc_mask
        elif not include_aggregates:
            mask &= (df["ISO3_code"] != "") | df["LocID"].isin(loc)
        if start is not None or end is not None:
            t = pd.to_numeric(df["Time"], errors="coerce")
            if start is not None:
                mask &= t >= start
            if end is not None:
                mask &= t <= end
        if vid is not None and "VarID" in df.columns:
            by_id = df["VarID"].str.replace(r"\.0$", "", regex=True) == vid
            by_name = df["Variant"].str.lower() == str(variant).lower()
            mask &= by_id | by_name
        return mask

    return keep


def _iso_codes(df: pd.DataFrame) -> pd.Series:
    loc = df["LocID"].str.replace(r"\.0$", "", regex=True)
    return df["ISO3_code"].where(df["ISO3_code"] != "", "LOC" + loc)


def parse_bulk(
    payload: bytes,
    value_columns: Sequence[str],
    *,
    countries: Sequence[str | int] | str | None = "all",
    start: int | str | None = None,
    end: int | str | None = None,
    variant: str | int | None = DEFAULT_VARIANT,
    include_aggregates: bool = False,
    age: bool = False,
    reference: str = "",
    file: str = "",
    url: str = "",
) -> ParsedWPP:
    """Parse a WPP bulk CSV into the canonical long frame.

    ``value_columns`` are the provider columns to keep (``TPopulation1July``,
    ``PopTotal``, ...). With ``age=True`` the indicator is ``<column>|<AgeGrp>``
    (``PopMale|15-19``). Rows are filtered by location, years and variant
    while reading; the variant must be present in the file (Medium files hold
    only the Medium variant).
    """
    var = normalize_variant(variant)
    iso, loc = normalize_countries(countries)
    y0, y1 = _year(start), _year(end)
    if y0 is not None and y1 is not None and y0 > y1:
        raise ValueError(f"start ({y0}) is after end ({y1})")
    cols = LOCATION_COLUMNS + (["AgeGrp", "AgeGrpStart"] if age else []) + list(value_columns)
    raw, missing, rows = read_bulk(payload, cols, _row_filter(iso, loc, y0, y1, var, include_aggregates), url=url)
    missing_values = [c for c in value_columns if c in missing]
    if any(c in missing for c in ("LocID", "ISO3_code", "Time")):
        raise WPPError(f"not a WPP bulk file (missing location/time columns) from {url or file}")
    if age and "AgeGrp" in missing:
        raise WPPError(f"{file or url} has no AgeGrp column; not an age-structure file")
    present = [c for c in value_columns if c not in missing]
    info = ParsedWPP(frame=empty_frame(), variant=var, file=file, reference=reference, missing_columns=missing_values, rows_read=rows)
    if iso is not None:
        found = set(raw["ISO3_code"]) | set(raw["LocID"].str.replace(r"\.0$", "", regex=True)) if len(raw) else set()
        info.missing_locations = [c for c in iso + loc if c not in found]
    if raw.empty or not present:
        return info
    raw = raw.assign(iso3=_iso_codes(raw))
    ids = ["iso3", "Location", "Time", "LocTypeName", "ISO3_code"] + (["AgeGrp"] if age else [])
    long = raw.melt(id_vars=ids, value_vars=present, var_name="column", value_name="value")
    if age:
        long["indicator"] = long["column"] + "|" + long["AgeGrp"].str.strip()
        sex_word = long["column"].map(POP_COLUMNS).fillna("")
        ref = f", {reference}" if reference else ""
        long["indicator_label"] = "Population aged " + long["AgeGrp"].str.strip() + ", " + sex_word + ref
        long["unit"] = "thousands"
        groups = sorted(set(long["AgeGrp"].str.strip()), key=lambda g: parse_age_group(g)[0])
        info.age_groups = groups
    else:
        long["indicator"] = long["column"]
        long["indicator_label"] = long["column"].map(lambda c: indicator_meta(c)[0])
        long["unit"] = long["column"].map(lambda c: indicator_meta(c)[1])
    years = pd.to_numeric(long["Time"], errors="coerce")
    long["obs_status"] = ["forecast" if (y == y and y >= FIRST_PROJECTION_YEAR) else "" for y in years]
    frame = make_frame(pd.DataFrame({
        "source": "wpp",
        "dataset": dataset_for(var),
        "indicator": long["indicator"],
        "indicator_label": long["indicator_label"],
        "iso3": long["iso3"],
        "country": long["Location"],
        "period": long["Time"].str.replace(r"\.0$", "", regex=True),
        "value": long["value"],
        "unit": long["unit"],
        "obs_status": long["obs_status"],
    }))
    aggs = raw.loc[raw["ISO3_code"] == "", ["iso3", "Location"]].drop_duplicates()
    info.aggregates = dict(sorted(zip(aggs["iso3"], aggs["Location"])))
    info.frame = frame
    return info


# ---------------------------------------------------------------------------
# derived age structures (pure)
# ---------------------------------------------------------------------------
def parse_band(band: str) -> tuple[int, float]:
    """``"15-64"`` -> ``(15, 64)``; ``"15+"`` -> ``(15, inf)``."""
    return parse_age_group(band)


def aggregate_ages(age_frame: pd.DataFrame, bands: Sequence[str] = ("15-64", "15+"), *, dependency: bool = True,
                   shares: bool = True) -> pd.DataFrame:
    """Sum 5-year (or single-year) age groups into bands, per location, period and sex.

    Input: the canonical frame from an age-structure file (indicators
    ``PopTotal|15-19`` ...). Output (canonical): ``<sex column>|<band>`` in
    thousands; with ``shares`` also ``share|<sex column>|<band>`` (% of the
    same sex, all ages); with ``dependency`` the total, young (0-14) and old
    (65+) dependency ratios per 100 persons aged 15-64 (both sexes). A band
    that cuts through an age group raises ``ValueError``; a band missing groups
    (incomplete data) yields NaN.
    """
    if age_frame.empty:
        return empty_frame()
    parts = age_frame["indicator"].str.split("|", n=1, expand=True)
    if parts.shape[1] < 2:
        raise ValueError("aggregate_ages expects an age-structure frame (indicators like 'PopTotal|15-19')")
    df = age_frame.assign(column=parts[0], group=parts[1])
    df = df[df["group"].notna()]
    bounds = {g: parse_age_group(g) for g in sorted(set(df["group"]))}
    keys = ["source", "dataset", "iso3", "country", "period", "obs_status"]

    def members(band: str) -> list[str]:
        lo, hi = parse_band(band)
        out = []
        for g, (glo, ghi) in bounds.items():
            inside = glo >= lo and ghi <= hi
            overlap = glo <= hi and ghi >= lo
            if inside:
                out.append(g)
            elif overlap:
                raise ValueError(f"band {band!r} cuts through age group {g!r}; use bands on group boundaries")
        return out

    def expected(band: str) -> int:
        lo, hi = parse_band(band)
        return sum(1 for g, (glo, ghi) in bounds.items() if glo >= lo and ghi <= hi)

    out: list[pd.DataFrame] = []
    totals = df.groupby(keys + ["column"], dropna=False)["value"].sum(min_count=1).rename("all_ages")
    wanted = list(dict.fromkeys(list(bands) + (["0-14", "15-64", "65+"] if dependency else [])))
    sums: dict[str, pd.Series] = {}
    for band in wanted:
        groups = members(band)
        if not groups:
            raise ValueError(f"band {band!r} contains no age group in the data")
        sub = df[df["group"].isin(groups)]
        g = sub.groupby(keys + ["column"], dropna=False)["value"]
        s = g.sum(min_count=1).where(g.count() == expected(band))
        sums[band] = s
        if band not in bands:
            continue
        band_df = s.rename("value").reset_index()
        band_df["indicator"] = band_df["column"] + "|" + band
        band_df["indicator_label"] = "Population aged " + band + ", " + band_df["column"].map(POP_COLUMNS).fillna("")
        band_df["unit"] = "thousands"
        out.append(band_df)
        if shares:
            share = (s / totals.reindex(s.index) * 100).rename("value").reset_index()
            share["indicator"] = "share|" + share["column"] + "|" + band
            share["indicator_label"] = "Share of population aged " + band + ", " + share["column"].map(POP_COLUMNS).fillna("")
            share["unit"] = "% of population (same sex, all ages)"
            out.append(share)
    if dependency:
        young, work, old = (sums[b].xs("PopTotal", level="column") if "PopTotal" in set(df["column"]) else None
                            for b in ("0-14", "15-64", "65+"))
        if work is not None:
            for name, num, label in (
                ("dependency_ratio", young + old, "Total dependency ratio (0-14 and 65+ per 100 aged 15-64)"),
                ("dependency_ratio_young", young, "Young-age dependency ratio (0-14 per 100 aged 15-64)"),
                ("dependency_ratio_old", old, "Old-age dependency ratio (65+ per 100 aged 15-64)"),
            ):
                dr = (num / work * 100).rename("value").reset_index()
                dr["indicator"] = name
                dr["indicator_label"] = label
                dr["unit"] = "per 100 persons aged 15-64"
                out.append(dr)
    if not out:
        return empty_frame()
    res = pd.concat(out, ignore_index=True)
    return make_frame(res.drop(columns=[c for c in ("column",) if c in res.columns]))


def pyramid_table(age_frame: pd.DataFrame, iso3: str, period: str | int) -> pd.DataFrame:
    """Population pyramid for one location and year: rows are age groups (youngest
    first), columns ``male``, ``female`` (thousands) and ``male_pct``,
    ``female_pct`` (% of the total population, both sexes, all ages)."""
    sub = age_frame[(age_frame["iso3"] == iso3) & (age_frame["period"] == str(period))]
    if sub.empty:
        raise ValueError(f"no age-structure rows for {iso3} {period}")
    parts = sub["indicator"].str.split("|", n=1, expand=True)
    sub = sub.assign(column=parts[0], group=parts[1])
    wide = sub.pivot_table(index="group", columns="column", values="value", aggfunc="first")
    wide = wide.reindex(sorted(wide.index, key=lambda g: parse_age_group(g)[0]))
    out = pd.DataFrame(index=wide.index)
    out.index.name = "age_group"
    out["male"] = wide.get("PopMale")
    out["female"] = wide.get("PopFemale")
    total = wide["PopTotal"].sum() if "PopTotal" in wide else (out["male"].sum() + out["female"].sum())
    out["male_pct"] = out["male"] / total * 100
    out["female_pct"] = out["female"] / total * 100
    return out


# ---------------------------------------------------------------------------
# Data Portal API (token) (pure parts)
# ---------------------------------------------------------------------------
def read_token(token: str | None = None) -> str:
    """Token from the argument or ``CGJRKIT_WPP_KEY``; a pasted ``"Bearer "`` prefix is removed."""
    tok = (token or os.environ.get(KEY_ENV, "")).strip()
    if tok.lower().startswith("bearer "):
        tok = tok[7:].strip()
    if not tok:
        raise WPPError(
            f"the UN Data Portal API needs a token: request one at https://population.un.org/dataportalapi/token/index.html "
            f"and set {KEY_ENV}; the bulk CSV methods need no token"
        )
    return tok


def build_api_request(indicator_ids: Sequence[int] | int, location_ids: Sequence[int | str] | int | str,
                      start: int, end: int, *, page: int = 1, page_size: int = 100) -> tuple[str, dict[str, str]]:
    """``/data/indicators/{ids}/locations/{ids}/start/{y0}/end/{y1}`` with JSON paging
    (``pagingInHeader=false``, ``pageNumber``, ``pageSize``; third-party usage)."""
    inds = [int(i) for i in ([indicator_ids] if isinstance(indicator_ids, (int, str)) else indicator_ids)]
    locs = [str(int(x)) for x in ([location_ids] if isinstance(location_ids, (int, str)) else location_ids)]
    if not inds or not locs:
        raise ValueError("indicator_ids and location_ids must not be empty")
    y0, y1 = _year(start), _year(end)
    if y0 is None or y1 is None or y0 > y1:
        raise ValueError("start and end years are required for the Data Portal API, with start <= end")
    url = f"{API_BASE}/data/indicators/{','.join(map(str, inds))}/locations/{','.join(locs)}/start/{y0}/end/{y1}"
    return url, {"pagingInHeader": "false", "format": "json", "pageNumber": str(page), "pageSize": str(page_size)}


def build_locations_request(page: int = 1, page_size: int = 100) -> tuple[str, dict[str, str]]:
    return f"{API_BASE}/locations", {"sort": "id", "pageNumber": str(page), "pageSize": str(page_size), "format": "json"}


def _api_json(payload: bytes | str, url: str = "") -> dict:
    text = payload.decode("utf-8-sig", errors="replace") if isinstance(payload, (bytes, bytearray)) else str(payload)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WPPError(f"Data Portal API returned non-JSON from {url or API_BASE}: {text[:200]!r}") from exc
    if not isinstance(obj, dict) or "data" not in obj:
        raise WPPError(f"unexpected Data Portal API payload from {url or API_BASE}: {str(obj)[:200]}")
    return obj


def parse_api(payload: bytes | str, url: str = "") -> tuple[pd.DataFrame, str | None]:
    """Data Portal JSON page -> ``(canonical frame, nextPage)``.

    Indicator = ``api:<indicatorId>`` plus ``|<sex>`` unless "Both sexes" and
    ``|<ageLabel>`` unless total; dataset = ``"WPP2024 API"`` (+ variant unless
    Median/Medium). Field names follow third-party clients (UNVERIFIED)."""
    obj = _api_json(payload, url)
    rows = obj.get("data") or []
    nxt = obj.get("nextPage") or None
    if not rows:
        return empty_frame(), nxt
    df = pd.DataFrame(rows)

    def col(name: str, default: Any = "") -> pd.Series:
        return df[name] if name in df.columns else pd.Series(default, index=df.index)

    sex = col("sex").fillna("").astype(str)
    age = col("ageLabel").fillna("").astype(str)
    variant = col("variant").fillna("").astype(str)
    ind = "api:" + col("indicatorId").astype(str)
    ind = ind + sex.map(lambda s: "" if s.lower() in ("", "both sexes", "both") else f"|{s}")
    ind = ind + age.map(lambda a: "" if a.lower() in ("", "total", "all ages", "all") else f"|{a}")
    iso3 = col("iso3").fillna("").astype(str)
    loc = col("locationId").astype(str)
    period = col("timeLabel").astype(str).str.replace(r"\.0$", "", regex=True)
    years = pd.to_numeric(period.str[:4], errors="coerce")
    frame = make_frame(pd.DataFrame({
        "source": "wpp",
        "dataset": ["WPP2024 API" if v.lower() in ("", "median", "medium") else f"WPP2024 API {v}" for v in variant],
        "indicator": ind,
        "indicator_label": col("indicatorDisplayName", "").fillna("").astype(str),
        "iso3": iso3.where(iso3 != "", "LOC" + loc),
        "country": col("location").fillna("").astype(str),
        "period": period,
        "value": col("value", None),
        "unit": "",
        "obs_status": ["forecast" if (y == y and y >= FIRST_PROJECTION_YEAR) else "" for y in years],
    }))
    return frame, nxt


def parse_locations(payload: bytes | str, url: str = "") -> tuple[pd.DataFrame, str | None]:
    obj = _api_json(payload, url)
    df = pd.DataFrame(obj.get("data") or [])
    for c in ("id", "name", "iso2", "iso3"):
        if c not in df.columns:
            df[c] = ""
    return df, obj.get("nextPage") or None


# ---------------------------------------------------------------------------
# caveats (pure)
# ---------------------------------------------------------------------------
def build_caveats(info: ParsedWPP, frame: pd.DataFrame | None = None, *, fallback_note: str = "") -> list[Caveat]:
    df = info.frame if frame is None else frame
    out: list[Caveat] = []
    if len(df) and (df["obs_status"] == "forecast").any():
        years = sorted({p for p, s in zip(df["period"], df["obs_status"]) if s == "forecast"})
        out.append(Caveat("wpp_projection", "warn",
                          f"WPP 2024 values from {FIRST_PROJECTION_YEAR} are projections ({info.variant} variant), not estimates.",
                          {"first": years[0], "last": years[-1], "variant": info.variant}))
    if len(df) and "TGO" in set(df["iso3"]):
        out.append(Caveat("wpp_togo_interim_update", "warn",
                          "The January 2026 WPP 2024 interim update revised Togo (census re-adjustment); the bulk CSV used here "
                          "predates it. Use WPP2024_CSV_files_update.zip for Togo; aggregates were not revised.",
                          {"iso3": "TGO"}))
    if len(df) == 0 and info.rows_read:
        out.append(Caveat("wpp_empty_selection", "warn",
                          "No rows matched the requested locations, years and variant in this file.",
                          {"file": info.file, "variant": info.variant, "rows_read": info.rows_read}))
    if info.missing_locations:
        out.append(Caveat("wpp_missing_locations", "warn", "Some requested locations are not in the WPP file.",
                          {"locations": info.missing_locations}))
    if info.missing_columns:
        out.append(Caveat("wpp_missing_columns", "warn", "Some requested columns are not in the WPP file.",
                          {"columns": info.missing_columns, "file": info.file}))
    if info.aggregates:
        out.append(Caveat("wpp_aggregates", "info",
                          "WPP aggregates are returned as LOC<LocID>; do not sum them with countries.",
                          {"aggregates": info.aggregates}))
    if fallback_note:
        out.append(Caveat("wpp_reference_date", "info", fallback_note, {"file": info.file, "reference": info.reference}))
    return out


# ---------------------------------------------------------------------------
# connector
# ---------------------------------------------------------------------------
class WPP(Connector):
    """UN World Population Prospects 2024 (bulk CSV; Data Portal API with a token)."""

    source = "wpp"
    license = "CC BY 3.0 IGO"
    citation = CITATION
    domains = ("population.un.org",)

    def __init__(self, cache: Cache | None = None):
        super().__init__(cache)

    # -- plumbing -------------------------------------------------------------
    def _get_file(self, key: str, refresh: bool) -> tuple[bytes, Any, str]:
        url, params = build_request(key)
        try:
            payload, retrieval = http.fetch(self.source, url, params, cache=self.cache, refresh=refresh, timeout=600.0)
        except http.FetchError as exc:
            raise WPPError(f"WPP download failed for {FILES[key].name}: {exc}") from exc
        return payload, retrieval, url

    def _prov(self, retrieval: Any, *, dataset: str, tool: str, notes: list[str] | None = None) -> Provenance:
        return self.provenance(retrieval, dataset=dataset, vintage=EDITION, tool=tool, notes=notes)

    def _result(self, info: ParsedWPP, retrieval: Any, tool: str, params: dict, *, notes: list[str] | None = None,
                fallback_note: str = "") -> Result:
        df = info.frame
        df = self.finalize(df) if len(df) else df
        validate_frame(df)
        info.frame = df
        prov = self._prov(retrieval, dataset=dataset_for(info.variant), tool=tool,
                          notes=[f"file {info.file}"] + ([f"reference date {info.reference}"] if info.reference else []) + (notes or []))
        meta = {
            "edition": EDITION,
            "variant": info.variant,
            "file": info.file,
            "reference": info.reference,
            "aggregates": sorted(info.aggregates),
            "aggregate_names": info.aggregates,
            "age_groups": info.age_groups,
            "missing_locations": info.missing_locations,
            "first_projection_year": FIRST_PROJECTION_YEAR,
            "params": params,
        }
        return Result(data=df, provenance=[prov], caveats=build_caveats(info, fallback_note=fallback_note), meta=meta)

    # -- bulk data --------------------------------------------------------------
    def fetch(
        self,
        indicators: Sequence[str] | str = ("TPopulation1July",),
        countries: Sequence[str | int] | str = "all",
        start: int | str | None = None,
        end: int | str | None = None,
        *,
        variant: str | int = DEFAULT_VARIANT,
        include_aggregates: bool = False,
        refresh: bool = False,
    ) -> Result:
        """Demographic indicators (``TPopulation1July``, ``TFR``, ``LEx``, ``MedianAgePop``, ...).

        The Medium variant comes from ``WPP2024_Demographic_Indicators_Medium.csv.gz``;
        other variants from the ``_OtherVariants`` file (UNVERIFIED name).
        """
        cols = [indicators] if isinstance(indicators, str) else list(indicators)
        unknown = [c for c in cols if c not in INDICATORS]
        if not cols or unknown:
            raise ValueError(f"unknown WPP demographic indicator(s) {unknown}; known: {sorted(INDICATORS)}")
        var = normalize_variant(variant)
        key = "demographic_indicators" if var == DEFAULT_VARIANT else "demographic_indicators_other"
        payload, retrieval, url = self._get_file(key, refresh)
        info = parse_bulk(payload, cols, countries=countries, start=start, end=end, variant=var,
                          include_aggregates=include_aggregates, file=FILES[key].name, url=url)
        params = {"indicators": cols, "countries": countries, "start": start, "end": end, "variant": var}
        return self._result(info, retrieval, "WPP.fetch", params)

    def total_population(
        self,
        countries: Sequence[str | int] | str = "all",
        start: int | str | None = None,
        end: int | str | None = None,
        *,
        variant: str | int = DEFAULT_VARIANT,
        sexes: Sequence[str] | str = ("total",),
        include_aggregates: bool = False,
        refresh: bool = False,
    ) -> Result:
        """Total population (thousands, 1 July) by sex from ``WPP2024_TotalPopulationBySex.csv.gz``
        (all variants). ``sexes``: any of ``"total"``, ``"male"``, ``"female"``."""
        cols = _sex_columns(sexes)
        var = normalize_variant(variant)
        payload, retrieval, url = self._get_file("total_population", refresh)
        info = parse_bulk(payload, cols, countries=countries, start=start, end=end, variant=var,
                          include_aggregates=include_aggregates, file=FILES["total_population"].name,
                          reference="1 July", url=url)
        labels = {"PopTotal": "Total population, as of 1 July", "PopMale": "Male population, as of 1 July",
                  "PopFemale": "Female population, as of 1 July"}
        if len(info.frame):
            info.frame["indicator_label"] = info.frame["indicator"].map(labels).fillna(info.frame["indicator_label"])
            info.frame["unit"] = "thousands"
        params = {"countries": countries, "start": start, "end": end, "variant": var, "sexes": cols}
        return self._result(info, retrieval, "WPP.total_population", params)

    def _age_file(self, variant: str, reference: str, refresh: bool) -> tuple[bytes, Any, str, str, str]:
        """Pick the 5-year age file; Medium + 1 July falls back to 1 January when missing."""
        if variant != DEFAULT_VARIANT:
            key = "population_age5_jan1_other"
            payload, retrieval, url = self._get_file(key, refresh)
            note = "" if reference == "jan1" else "Non-Medium variants by age are published for 1 January only."
            return payload, retrieval, url, key, note
        if reference == "jul1":
            try:
                payload, retrieval, url = self._get_file("population_age5", refresh)
                return payload, retrieval, url, "population_age5", ""
            except WPPError as exc:
                if "404" not in str(exc):
                    raise
                note = (f"{FILES['population_age5'].name} was not found (404); used the 1 January file instead. "
                        "Population on 1 January differs from mid-year population.")
        else:
            note = ""
        key = "population_age5_jan1"
        payload, retrieval, url = self._get_file(key, refresh)
        return payload, retrieval, url, key, note

    def age_structure(
        self,
        countries: Sequence[str | int] | str,
        start: int | str | None = None,
        end: int | str | None = None,
        *,
        variant: str | int = DEFAULT_VARIANT,
        sexes: Sequence[str] | str = ("male", "female", "total"),
        reference: str = "jul1",
        include_aggregates: bool = False,
        refresh: bool = False,
    ) -> Result:
        """Population by 5-year age group and sex (thousands), the input of population
        pyramids (:func:`pyramid_table`). Indicators look like ``PopFemale|20-24``.
        ``reference``: ``"jul1"`` (mid-year, default) or ``"jan1"``."""
        if reference not in ("jul1", "jan1"):
            raise ValueError("reference must be 'jul1' or 'jan1'")
        cols = _sex_columns(sexes)
        var = normalize_variant(variant)
        payload, retrieval, url, key, note = self._age_file(var, reference, refresh)
        info = parse_bulk(payload, cols, countries=countries, start=start, end=end, variant=var,
                          include_aggregates=include_aggregates, age=True, reference=FILES[key].reference,
                          file=FILES[key].name, url=url)
        params = {"countries": countries, "start": start, "end": end, "variant": var, "sexes": cols, "reference": reference}
        return self._result(info, retrieval, "WPP.age_structure", params, fallback_note=note)

    def working_age_population(
        self,
        countries: Sequence[str | int] | str,
        start: int | str | None = None,
        end: int | str | None = None,
        *,
        bands: Sequence[str] = ("15-64", "15+"),
        sexes: Sequence[str] | str = ("total",),
        variant: str | int = DEFAULT_VARIANT,
        reference: str = "jul1",
        dependency: bool = True,
        shares: bool = True,
        refresh: bool = False,
    ) -> Result:
        """Working-age population (``PopTotal|15-64``, ``PopTotal|15+`` in thousands),
        shares of the population, and dependency ratios, summed from 5-year age groups."""
        cols = _sex_columns(sexes)
        need = list(dict.fromkeys(cols + (["PopTotal"] if dependency else [])))
        base = self.age_structure(countries, start, end, variant=variant, sexes=need, reference=reference, refresh=refresh)
        derived = aggregate_ages(base.data, bands, dependency=dependency, shares=shares)
        if len(derived):
            keep_cols = set(cols)
            col_of = derived["indicator"].str.replace(r"^share\|", "", regex=True).str.split("|").str[0]
            derived = derived[col_of.isin(keep_cols) | derived["indicator"].str.startswith("dependency_ratio")]
        derived = self.finalize(derived) if len(derived) else derived
        validate_frame(derived)
        tool = Provenance(source="cgjrkit", tool="WPP.working_age_population",
                          params={"bands": list(bands), "sexes": cols, "dependency": dependency, "shares": shares,
                                  "method": "sum of 5-year age groups; NaN when a group is missing"})
        return Result(data=derived.reset_index(drop=True), provenance=base.provenance + [tool], caveats=list(base.caveats),
                      meta={**base.meta, "bands": list(bands)})

    # -- Data Portal API (token) --------------------------------------------------
    def api_locations(self, refresh: bool = False, max_pages: int = 100) -> pd.DataFrame:
        """Locations from the Data Portal (``id`` = UN location id, ``iso3``); no token needed (third-party report)."""
        frames = []
        for page in range(1, max_pages + 1):
            url, params = build_locations_request(page)
            payload, _ = http.fetch(self.source, url, params, cache=self.cache, refresh=refresh)
            df, nxt = parse_locations(payload, url)
            frames.append(df)
            if not nxt:
                break
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["id", "name", "iso2", "iso3"])

    def fetch_api(
        self,
        indicator_ids: Sequence[int] | int,
        locations: Sequence[str | int] | str | int,
        start: int,
        end: int,
        *,
        token: str | None = None,
        max_pages: int = 1000,
        refresh: bool = False,
    ) -> Result:
        """Data Portal API query (token required: ``CGJRKIT_WPP_KEY``). ``locations`` are UN
        location ids or ISO3 codes (resolved with :meth:`api_locations`)."""
        tok = read_token(token)
        iso, loc = normalize_countries([locations] if isinstance(locations, (int, str)) else list(locations))
        if iso:
            locs = self.api_locations(refresh=refresh)
            mapping = {str(r["iso3"]).upper(): str(r["id"]) for _, r in locs.iterrows() if r.get("iso3")}
            missing = [c for c in iso if c not in mapping]
            if missing:
                raise WPPError(f"ISO3 code(s) not found in Data Portal locations: {missing}")
            loc = sorted(set(loc) | {mapping[c] for c in iso}, key=int)
        headers = {"Authorization": f"Bearer {tok}", "Accept": "application/json"}
        frames: list[pd.DataFrame] = []
        provs: list[Provenance] = []
        for page in range(1, max_pages + 1):
            url, params = build_api_request(indicator_ids, loc, start, end, page=page)
            try:
                payload, retrieval = http.fetch(self.source, url, params, headers=headers, cache=self.cache, refresh=refresh)
            except http.FetchError as exc:
                raise WPPError(f"Data Portal API request failed (check {KEY_ENV}): {exc}") from exc
            df, nxt = parse_api(payload, url)
            frames.append(df)
            provs.append(self.provenance(retrieval, dataset="WPP2024 API", vintage=EDITION, tool="WPP.fetch_api",
                                         notes=[f"page {page}", "field names UNVERIFIED (third-party clients)"]))
            if not nxt or df.empty:
                break
        non_empty = [f for f in frames if len(f)]
        data = pd.concat(non_empty, ignore_index=True) if non_empty else empty_frame()
        data = data.drop_duplicates(subset=["source", "dataset", "indicator", "iso3", "period"], keep="first")
        data = self.finalize(data) if len(data) else data
        validate_frame(data)
        info = ParsedWPP(frame=data, variant="Median/Medium", file="Data Portal API")
        caveats = build_caveats(info)
        return Result(data=data, provenance=provs, caveats=caveats,
                      meta={"edition": EDITION, "indicator_ids": indicator_ids, "locations": loc})


def _sex_columns(sexes: Sequence[str] | str) -> list[str]:
    items = [sexes] if isinstance(sexes, str) else list(sexes)
    cols: list[str] = []
    for s in items:
        key = str(s).strip().lower()
        if key not in SEX_ALIASES:
            raise ValueError(f"unknown sex {s!r}; use 'total', 'male' or 'female'")
        col = SEX_ALIASES[key]
        if col not in cols:
            cols.append(col)
    if not cols:
        raise ValueError("at least one sex is required")
    return cols


__all__ = [
    "FILES",
    "INDICATORS",
    "VARIANTS",
    "WPP",
    "WPPError",
    "aggregate_ages",
    "build_api_request",
    "build_caveats",
    "build_locations_request",
    "build_request",
    "dataset_for",
    "normalize_countries",
    "normalize_variant",
    "parse_age_group",
    "parse_api",
    "parse_bulk",
    "parse_locations",
    "pyramid_table",
    "read_bulk",
    "read_token",
]
