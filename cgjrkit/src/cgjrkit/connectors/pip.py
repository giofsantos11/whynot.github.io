"""World Bank Poverty and Inequality Platform (PIP) API connector.

Base URL ``https://api.worldbank.org/pip/v1`` (``prod_url`` + ``api_version``
in the World Bank's ``pipr`` client; same base in the CRAN ``worldbank``
client and in Our World in Data's production ETL). Endpoints used:

* ``/pip`` -- country survey-year (or gap-filled) poverty and inequality
  statistics;
* ``/pip-grp`` with ``group_by=wb`` -- World Bank regional and global
  aggregates;
* ``/versions`` -- available data versions (``ppp_version``,
  ``release_version``, ``version``), used to pin the release.

Responses are requested as CSV (``format=csv``), the format OWID's ETL and the
``worldbank`` R client parse. The 44 columns of ``/pip`` (``region_name`` ...
``estimate_type``) are listed in ``docs/apis/pip.md`` with their sources.

Units: PIP returns ``headcount``, ``poverty_gap``, ``poverty_severity``,
``spr``, ``gini`` and decile shares as fractions (0-1). This connector reports
them in percent (Gini as a 0-100 index, the WDI convention) and records the
scaling in ``Result.meta["scaled_x100"]``. Every unit that depends on the PPP
round states it (``"... (PPP 2021)"``) and line-dependent measures state the
poverty line, so :func:`cgjrkit.core.schema.ppp_base` and the mixing check
below can block 2017/2021 PPP mixes.

Release pinning: ``release_version=None`` resolves the latest release for the
requested ``ppp_version`` through ``/versions`` and then sends
``ppp_version``, ``release_version`` and ``version`` explicitly (as OWID's ETL
does), so the provenance vintage is always an exact ``YYYYMMDD`` release.
"""

from __future__ import annotations

import io
import json
import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from cgjrkit.connectors.base import Connector
from cgjrkit.core.cache import Cache
from cgjrkit.core.caveats import Caveat
from cgjrkit.core.http import FetchError, NetworkBlocked
from cgjrkit.core.http import fetch as http_fetch
from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import empty_frame, make_frame, ppp_base, validate_frame

BASE_URL = "https://api.worldbank.org/pip/v1"
PIP_URL = f"{BASE_URL}/pip"
GRP_URL = f"{BASE_URL}/pip-grp"
VERSIONS_URL = f"{BASE_URL}/versions"
AUX_URL = f"{BASE_URL}/aux"
CITATION_URL = f"{BASE_URL}/citation"
HEALTH_URL = f"{BASE_URL}/health-check"

#: Standard lines per PPP round: (international poverty line, LMIC line, UMIC line), $/day.
#: 2017 and 2021 from OWID's ETL (POVLINES_DICT) and the June 2025 World Bank factsheet;
#: 2011 is the PovcalNet-era set.
STANDARD_LINES: dict[int, tuple[float, float, float]] = {
    2011: (1.90, 3.20, 5.50),
    2017: (2.15, 3.65, 6.85),
    2021: (3.00, 4.20, 8.30),
}


@dataclass(frozen=True)
class Measure:
    label: str
    unit: str  # template: {line} poverty line, {ppp} PPP year
    scale: float  # multiply PIP value by this (fractions -> percent)
    line_dependent: bool
    fraction: bool  # True when PIP reports it as a 0-1 fraction


_PPP_DAY = "{ppp} international $ per person per day (PPP {ppp})"
_SHARE = "% of total welfare"

#: Measures read from PIP responses (column -> definition). Labels follow PIP/PovcalNet definitions.
MEASURES: dict[str, Measure] = {
    "headcount": Measure("Poverty headcount ratio at ${line}/day", "% of population below ${line}/day (PPP {ppp})", 100.0, True, True),
    "poverty_gap": Measure("Poverty gap index at ${line}/day", "% of poverty line, population average (${line}/day, PPP {ppp})", 100.0, True, True),
    "poverty_severity": Measure("Poverty severity (squared gap) at ${line}/day", "% (squared gap index, ${line}/day, PPP {ppp})", 100.0, True, True),
    "watts": Measure("Watts index at ${line}/day", "index (${line}/day, PPP {ppp})", 1.0, True, False),
    "mean": Measure("Mean income or consumption per capita", _PPP_DAY, 1.0, False, False),
    "median": Measure("Median income or consumption per capita", _PPP_DAY, 1.0, False, False),
    "gini": Measure("Gini index", "index (0-100)", 100.0, False, True),
    "mld": Measure("Mean log deviation", "index", 1.0, False, False),
    "polarization": Measure("Polarization index", "index", 1.0, False, False),
    **{
        f"decile{k}": Measure(f"Welfare share held by decile {k}", _SHARE, 100.0, False, True)
        for k in range(1, 11)
    },
    "spl": Measure("Societal poverty line", _PPP_DAY, 1.0, False, False),
    "spr": Measure("Societal poverty rate", "% of population below the societal poverty line (PPP {ppp})", 100.0, False, True),
    "pg": Measure("Prosperity gap", "ratio (PPP {ppp})", 1.0, False, False),
}

DEFAULT_MEASURES: tuple[str, ...] = tuple(MEASURES)

#: Per-observation context columns kept in ``meta["survey_info"]``.
INFO_COLUMNS = (
    "welfare_type",
    "reporting_level",
    "survey_acronym",
    "survey_coverage",
    "survey_year",
    "survey_comparability",
    "comparable_spell",
    "distribution_type",
    "estimation_type",
    "estimate_type",
    "is_interpolated",
    "cpi",
    "ppp",
    "reporting_pop",
    "reporting_gdp",
    "reporting_pce",
)

_ISO3 = re.compile(r"^[A-Z]{3}$")
_REGION = re.compile(r"^[A-Z0-9_]{2,8}$")


# =============================================================================
# Validation helpers (mirroring pipr 1.5 local checks)
# =============================================================================


def _validate_ppp(ppp_version: int | str) -> int:
    s = str(ppp_version)
    if not re.fullmatch(r"\d{4}", s):
        raise ValueError(f"ppp_version must be a 4-digit PPP year (2011, 2017, 2021), got {ppp_version!r}")
    return int(s)


def _validate_release(release_version: str | int | None) -> str | None:
    if release_version is None:
        return None
    s = str(release_version)
    if not re.fullmatch(r"\d{8}", s):
        raise ValueError(f"release_version must be a date in YYYYMMDD format, got {release_version!r}")
    try:
        datetime.strptime(s, "%Y%m%d")
    except ValueError as exc:
        raise ValueError(f"release_version {s!r} is not a valid date") from exc
    return s


def _validate_povline(povline: float) -> float:
    if isinstance(povline, bool) or not isinstance(povline, (int, float)) or not math.isfinite(povline):
        raise ValueError(f"povline must be a finite number, got {povline!r}")
    if not 0 <= povline <= 2700:
        raise ValueError("povline must be between 0 and 2700 $/day (PIP API range)")
    if round(povline, 3) != povline:
        raise ValueError("PIP accepts poverty lines with at most 3 decimals")
    return float(povline)


def _countries_param(countries: str | Iterable[str], pattern: re.Pattern = _ISO3) -> str:
    if isinstance(countries, str):
        if countries.lower() == "all":
            return "all"
        countries = countries.split(",")
    codes = sorted({c.strip().upper() for c in countries if c and c.strip()})
    if not codes:
        raise ValueError("pass at least one country code or 'all'")
    bad = [c for c in codes if not pattern.match(c)]
    if bad:
        raise ValueError(f"invalid codes {bad}; use uppercase ISO3 codes (or 'all')")
    return ",".join(codes)


def _years_param(years: str | int | Iterable[int]) -> str:
    if isinstance(years, str):
        if years.lower() in ("all", "mrv"):
            return "all" if years.lower() == "all" else "MRV"
        if re.fullmatch(r"\d{4}", years):
            return years
        raise ValueError(f"years must be 'all', 'MRV' or integer years, got {years!r}")
    if isinstance(years, int):
        return str(years)
    vals = sorted({int(y) for y in years})
    if not vals or any(y < 1900 or y > 2100 for y in vals):
        raise ValueError("years must be four-digit integers")
    return ",".join(str(y) for y in vals)


def line_code(povline: float) -> str:
    """Poverty line as used in indicator codes and units: ``3.0 -> "3.00"``, ``2.155 -> "2.155"``."""
    return f"{povline:.2f}" if round(povline, 2) == povline else f"{povline:.3f}"


# =============================================================================
# Pure request builders
# =============================================================================


def build_request(
    countries: str | Iterable[str] = "all",
    years: str | int | Iterable[int] = "all",
    povline: float = 3.0,
    ppp_version: int | str = 2021,
    release_version: str | int | None = None,
    version: str | None = None,
    fill_gaps: bool = False,
    *,
    welfare_type: str = "all",
    reporting_level: str = "national",
    nowcast: bool = False,
) -> tuple[str, dict]:
    """Return ``(url, params)`` for ``GET /pip``.

    ``country`` and ``year`` are comma lists (pipr: ``.multi = "comma"``);
    booleans are sent as ``true``/``false`` (OWID ETL); ``nowcast`` is sent
    only when requested and requires ``fill_gaps`` (as in the R clients).
    """
    if welfare_type not in ("all", "income", "consumption"):
        raise ValueError("welfare_type must be 'all', 'income' or 'consumption'")
    if reporting_level not in ("all", "national", "urban", "rural"):
        raise ValueError("reporting_level must be 'all', 'national', 'urban' or 'rural'")
    if nowcast and not fill_gaps:
        raise ValueError("nowcast=True requires fill_gaps=True")
    params: dict[str, str | int | float] = {
        "country": _countries_param(countries),
        "year": _years_param(years),
        "povline": _validate_povline(povline),
        "fill_gaps": "true" if fill_gaps else "false",
        "welfare_type": welfare_type,
        "reporting_level": reporting_level,
        "ppp_version": _validate_ppp(ppp_version),
    }
    rel = _validate_release(release_version)
    if rel:
        params["release_version"] = rel
    if version:
        params["version"] = _validate_version(version)
    if nowcast:
        params["nowcast"] = "true"
    params["format"] = "csv"
    return PIP_URL, params


def build_group_request(
    regions: str | Iterable[str] = "all",
    years: str | int | Iterable[int] = "all",
    povline: float = 3.0,
    ppp_version: int | str = 2021,
    release_version: str | int | None = None,
    version: str | None = None,
) -> tuple[str, dict]:
    """Return ``(url, params)`` for ``GET /pip-grp?group_by=wb`` (WB regions and World)."""
    params: dict[str, str | int | float] = {
        "country": _countries_param(regions, _REGION),
        "year": _years_param(years),
        "povline": _validate_povline(povline),
        "group_by": "wb",
        "ppp_version": _validate_ppp(ppp_version),
    }
    rel = _validate_release(release_version)
    if rel:
        params["release_version"] = rel
    if version:
        params["version"] = _validate_version(version)
    params["format"] = "csv"
    return GRP_URL, params


def build_versions_request() -> tuple[str, dict]:
    """Return ``(url, params)`` for ``GET /versions`` (CSV)."""
    return VERSIONS_URL, {"format": "csv"}


def _validate_version(version: str) -> str:
    # pipr: ^[0-9]{8}_[0-9]{4}_[0-9]{2}_[0-9]{2}_[A-Z]+$, e.g. 20260324_2021_01_02_PROD
    if not re.fullmatch(r"\d{8}_\d{4}_\d{2}_\d{2}_[A-Z]+", str(version)):
        raise ValueError(f"version must look like YYYYMMDD_PPP_XX_YY_IDENTITY, got {version!r}")
    return str(version)


# =============================================================================
# Pure parsers
# =============================================================================


def _read_table(payload: bytes | str) -> pd.DataFrame:
    """Read a PIP CSV payload (or a JSON array of records) into a DataFrame."""
    raw = payload.encode() if isinstance(payload, str) else bytes(payload)
    head = raw.lstrip()[:1]
    if head in (b"[", b"{"):
        obj = json.loads(raw.decode("utf-8"))
        if isinstance(obj, dict):
            if "error" in obj:
                raise ValueError(f"PIP API error: {obj.get('error')} {obj.get('details', '')}")
            obj = obj.get("value", [obj])
        return pd.DataFrame(obj)
    if not raw.strip():
        return pd.DataFrame()
    df = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False)
    return df.apply(lambda s: s.str.strip()) if not df.empty else df


def parse_versions(payload: bytes | str) -> list[dict]:
    """Return ``[{"version", "release_version", "ppp_version"}]`` from ``/versions``, newest first."""
    df = _read_table(payload)
    needed = {"version", "release_version", "ppp_version"}
    if df.empty or not needed <= set(df.columns):
        raise ValueError(f"unexpected PIP versions table; expected columns {sorted(needed)}, got {list(df.columns)}")
    out = []
    for rec in df.to_dict("records"):
        rel = str(rec["release_version"]).split(".")[0]
        ppp = str(rec["ppp_version"]).split(".")[0]
        if re.fullmatch(r"\d{8}", rel) and re.fullmatch(r"\d{4}", ppp):
            out.append({"version": str(rec["version"]), "release_version": rel, "ppp_version": int(ppp)})
    return sorted(out, key=lambda r: (r["release_version"], r["ppp_version"], r["version"]), reverse=True)


def resolve_version(versions: Sequence[dict], ppp_version: int | str, release_version: str | int | None = None) -> dict:
    """Pick the version record for a PPP round: the given release, or the latest one."""
    ppp = _validate_ppp(ppp_version)
    rel = _validate_release(release_version)
    candidates = [v for v in versions if v["ppp_version"] == ppp]
    if not candidates:
        raise ValueError(f"PIP has no data version for ppp_version={ppp}; available: {sorted({v['ppp_version'] for v in versions})}")
    if rel:
        match = [v for v in candidates if v["release_version"] == rel]
        if not match:
            raise ValueError(f"release_version {rel} not available for PPP {ppp}; available: {sorted({v['release_version'] for v in candidates}, reverse=True)}")
        return sorted(match, key=lambda v: v["version"])[-1]
    latest = max(v["release_version"] for v in candidates)
    return sorted([v for v in candidates if v["release_version"] == latest], key=lambda v: v["version"])[-1]


def _bool(value) -> bool:
    return str(value).strip().lower() in ("true", "1", "t", "yes")


def _float(value) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return f


def _json_safe(value):
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _obs_status(row: Mapping) -> str:
    est = str(row.get("estimate_type", "")).lower()
    if "projection" in est:
        return "forecast"
    if "nowcast" in est:
        return "modelled"
    if _bool(row.get("is_interpolated", "")):
        return "imputed"
    if str(row.get("estimation_type", "")).lower() in ("interpolation", "extrapolation"):
        return "imputed"
    return ""


@dataclass
class ParsedPoverty:
    """Output of :func:`parse_rows`: canonical frame plus per-observation survey context."""

    frame: pd.DataFrame
    survey_info: list[dict] = field(default_factory=list)
    aggregates: list[str] = field(default_factory=list)
    welfare_duplicates: list[dict] = field(default_factory=list)
    dropped_nowcast_rows: int = 0
    scaled_x100: bool = True
    measures: list[str] = field(default_factory=list)


def parse_rows(
    table: pd.DataFrame,
    *,
    ppp_version: int,
    povline: float | None = None,
    measures: Sequence[str] | None = None,
    drop_nowcast: bool = False,
    aggregate: bool = False,
) -> ParsedPoverty:
    """Map a PIP table onto the canonical frame (pure).

    * ``indicator``: measure name, with ``@<line>`` for line-dependent
      measures (``headcount@3.00``); ``|urban``/``|rural`` for sub-national
      reporting levels; ``|income``/``|consumption`` only where a country-year
      has both welfare types (listed in ``welfare_duplicates``).
    * ``dataset``: ``PIP_PPP<year>`` so 2017- and 2021-PPP results never
      collide on the canonical key.
    * ``obs_status``: ``imputed`` for gap-filled years, ``modelled`` for
      nowcasts, ``forecast`` for projections.
    """
    ppp = _validate_ppp(ppp_version)
    wanted = [m for m in (measures or DEFAULT_MEASURES)]
    unknown = [m for m in wanted if m not in MEASURES]
    if unknown:
        raise ValueError(f"unknown PIP measures {unknown}; known: {list(MEASURES)}")
    dataset = f"PIP_PPP{ppp}"
    out = ParsedPoverty(frame=empty_frame(), measures=[m for m in wanted if m in table.columns])
    if table.empty:
        return out

    df = table.copy()
    code_col = "country_code" if "country_code" in df.columns and not aggregate else "region_code"
    name_col = "country_name" if code_col == "country_code" else "region_name"
    year_col = "reporting_year" if "reporting_year" in df.columns else "year"
    if code_col not in df.columns or year_col not in df.columns:
        raise ValueError(f"PIP table lacks {code_col!r}/{year_col!r} columns; got {list(df.columns)}")

    if drop_nowcast and "estimate_type" in df.columns:
        mask = df["estimate_type"].astype(str).str.contains("nowcast", case=False, na=False)
        out.dropped_nowcast_rows = int(mask.sum())
        df = df[~mask]

    # Fractions -> percent, unless the payload is already in percent (guard).
    if "headcount" in df.columns:
        hc = pd.to_numeric(df["headcount"], errors="coerce")
        out.scaled_x100 = not bool((hc > 1.0 + 1e-9).any())

    df["_iso"] = df[code_col].astype(str).str.upper()
    df["_period"] = df[year_col].astype(str).str.split(".").str[0]
    df["_level"] = df["reporting_level"].astype(str).str.lower() if "reporting_level" in df.columns else "national"
    df["_welfare"] = df["welfare_type"].astype(str).str.lower() if "welfare_type" in df.columns else ""
    df["_suffix"] = df["_level"].map(lambda lv: "" if lv in ("national", "", "nan") else f"|{lv}")
    if "welfare_type" in df.columns:
        n_welfare = df.groupby(["_iso", "_period", "_level"])["_welfare"].transform("nunique")
        dup = n_welfare > 1
        df.loc[dup, "_suffix"] = df.loc[dup, "_suffix"] + "|" + df.loc[dup, "_welfare"]
        out.welfare_duplicates = (
            df.loc[dup, ["_iso", "_period", "_level"]].drop_duplicates()
            .rename(columns={"_iso": "iso3", "_period": "period", "_level": "reporting_level"})
            .sort_values(["iso3", "period"]).to_dict("records")
        )

    records: list[dict] = []
    info: list[dict] = []
    aggregates: set[str] = set()
    for _, row in df.iterrows():
        iso, period = row["_iso"], row["_period"]
        if not iso or not period:
            continue
        line = _float(row.get("poverty_line", povline if povline is not None else float("nan")))
        if math.isnan(line) and povline is not None:
            line = povline
        lc = line_code(line) if not math.isnan(line) else "NA"
        status = _obs_status(row)
        if aggregate or not _ISO3.match(iso) or code_col == "region_code":
            aggregates.add(iso)
        for m in out.measures:
            spec = MEASURES[m]
            value = _float(row.get(m))
            if spec.fraction and out.scaled_x100:
                value = value * spec.scale
            code = (f"{m}@{lc}" if spec.line_dependent else m) + row["_suffix"]
            records.append(
                {
                    "indicator": code,
                    "indicator_label": spec.label.replace("{line}", lc),
                    "iso3": iso,
                    "country": str(row.get(name_col, "")),
                    "period": period,
                    "value": value,
                    "unit": spec.unit.replace("{line}", lc).replace("{ppp}", str(ppp)),
                    "obs_status": status,
                }
            )
        rec = {"iso3": iso, "period": period, "series_suffix": row["_suffix"], "poverty_line": _json_safe(line)}
        for col in INFO_COLUMNS:
            if col in df.columns:
                val = row.get(col)
                if col == "is_interpolated":
                    rec[col] = _bool(val)
                elif col in ("cpi", "ppp", "reporting_pop", "reporting_gdp", "reporting_pce", "survey_year"):
                    rec[col] = _json_safe(_float(val))
                else:
                    rec[col] = "" if val is None or (isinstance(val, float) and math.isnan(val)) else str(val)
        info.append(rec)

    if records:
        frame = make_frame(records, source=PIP.source, dataset=dataset)
        out.frame = Connector.finalize(frame)
    out.survey_info = sorted(info, key=lambda r: (r["iso3"], r["period"], r["series_suffix"], str(r.get("poverty_line"))))
    out.aggregates = sorted(aggregates)
    return out


def parse(payload: bytes | str, *, ppp_version: int, povline: float | None = None, measures: Sequence[str] | None = None, drop_nowcast: bool = False, aggregate: bool = False) -> pd.DataFrame:
    """Parse one ``/pip`` (or ``/pip-grp`` with ``aggregate=True``) payload into the canonical frame."""
    return parse_rows(_read_table(payload), ppp_version=ppp_version, povline=povline, measures=measures, drop_nowcast=drop_nowcast, aggregate=aggregate).frame


# =============================================================================
# Caveat checks (pure)
# =============================================================================


def _line_of(indicator: str) -> str | None:
    m = re.search(r"@([0-9.]+)", indicator)
    return m.group(1) if m else None


def poverty_mix_caveats(data: pd.DataFrame | Result | Iterable[Result]) -> list[Caveat]:
    """Caveats for frames that combine PIP poverty lines or PPP rounds.

    * more than one PPP base among PIP rows -> ``block`` (``pip_mixed_ppp``);
    * more than one poverty line -> ``warn`` (``pip_mixed_lines``).

    Works on a canonical frame, a Result, or several Results (e.g. before
    merging a 2017-PPP $2.15 series with a 2021-PPP $3.00 series).
    """
    if isinstance(data, pd.DataFrame):
        frames = [data]
    elif isinstance(data, Result):
        frames = [data.data]
    else:
        frames = [r.data for r in data]
    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if df.empty:
        return []
    df = df[df["source"] == PIP.source]
    cavs: list[Caveat] = []
    bases = sorted({b for b in (ppp_base(u) for u in df["unit"].unique()) if b})
    if len(bases) > 1:
        cavs.append(
            Caveat(
                "pip_mixed_ppp",
                "block",
                "PIP values on different PPP rounds are combined; poverty lines and levels are not comparable across "
                "PPP rounds (e.g. $2.15 at 2017 PPPs vs $3.00 at 2021 PPPs). Chart them as separate, labelled series.",
                {"ppp_bases": bases, "datasets": sorted(df["dataset"].unique().tolist())},
            )
        )
    lines = sorted({ln for ln in (_line_of(i) for i in df["indicator"].unique()) if ln}, key=float)
    if len(lines) > 1:
        cavs.append(
            Caveat(
                "pip_mixed_lines",
                "warn",
                "Several poverty lines are combined; label each series with its line and PPP round.",
                {"poverty_lines": lines},
            )
        )
    return cavs


# =============================================================================
# Connector
# =============================================================================


class PIP(Connector):
    """World Bank Poverty and Inequality Platform API (no key)."""

    source = "pip"
    license = "CC BY 4.0 (World Bank dataset terms; PIP-specific terms to confirm)"
    citation = "World Bank, Poverty and Inequality Platform (PIP)"
    domains = ("api.worldbank.org",)

    build_request = staticmethod(build_request)
    parse = staticmethod(parse)
    parse_rows = staticmethod(parse_rows)

    def __init__(self, cache: Cache | None = None):
        super().__init__(cache)

    # -- versions --------------------------------------------------------------
    def versions(self, *, refresh: bool = False) -> tuple[list[dict], Provenance]:
        """Available PIP data versions (newest first) and the provenance of the lookup."""
        url, params = build_versions_request()
        payload, retrieval = http_fetch(self.source, url, params, cache=self.cache, refresh=refresh)
        return parse_versions(payload), self.provenance(retrieval, dataset="PIP versions", tool="PIP.versions")

    def _resolve(self, ppp_version: int, release_version: str | None, refresh: bool) -> tuple[dict, list[Provenance], list[str]]:
        notes: list[str] = []
        try:
            versions, prov = self.versions(refresh=refresh)
        except (NetworkBlocked, FetchError, ValueError) as exc:
            if release_version is None:
                raise RuntimeError(
                    f"could not resolve the latest PIP release for PPP {ppp_version} ({exc.__class__.__name__}: {exc}); "
                    "pass release_version='YYYYMMDD' explicitly"
                ) from exc
            notes.append(f"versions lookup failed ({exc.__class__.__name__}); sent ppp_version + release_version only")
            return {"version": "", "release_version": release_version, "ppp_version": ppp_version}, [], notes
        return resolve_version(versions, ppp_version, release_version), [prov], notes

    # -- data ------------------------------------------------------------------
    def fetch(self, *args, **kwargs) -> Result:
        """Alias of :meth:`fetch_poverty`."""
        return self.fetch_poverty(*args, **kwargs)

    def fetch_poverty(
        self,
        countries: str | Iterable[str],
        years: str | int | Iterable[int] = "all",
        povline: float | Sequence[float] | None = 3.0,
        ppp_version: int = 2021,
        release_version: str | int | None = None,
        fill_gaps: bool = False,
        *,
        welfare_type: str = "all",
        reporting_level: str = "national",
        measures: Sequence[str] | None = None,
        nowcast: bool = False,
        refresh: bool = False,
    ) -> Result:
        """Poverty and inequality statistics for countries (PIP ``/pip``).

        ``povline=None`` uses the international poverty line of the PPP round
        ($3.00 at 2021 PPPs, $2.15 at 2017 PPPs). Several lines can be passed;
        each is one request. The provenance vintage is the release_version.
        """
        ppp = _validate_ppp(ppp_version)
        rel = _validate_release(release_version)
        lines = self._lines(povline, ppp)
        resolved, provs, notes = self._resolve(ppp, rel, refresh)
        frames, info, dups, aggregates = [], [], [], set()
        dropped = 0
        scaled = True
        for line in lines:
            url, params = build_request(
                countries, years, line, ppp, resolved["release_version"], resolved["version"] or None, fill_gaps,
                welfare_type=welfare_type, reporting_level=reporting_level, nowcast=nowcast,
            )
            payload, retrieval = http_fetch(self.source, url, params, cache=self.cache, refresh=refresh)
            parsed = parse_rows(_read_table(payload), ppp_version=ppp, povline=line, measures=measures, drop_nowcast=fill_gaps and not nowcast)
            frames.append(parsed.frame)
            info.extend(parsed.survey_info)
            dups.extend(parsed.welfare_duplicates)
            aggregates.update(parsed.aggregates)
            dropped += parsed.dropped_nowcast_rows
            scaled = scaled and parsed.scaled_x100
            provs.append(self._data_provenance(retrieval, resolved, ppp, notes, "PIP.fetch_poverty"))
        return self._result(frames, provs, info, dups, sorted(aggregates), dropped, scaled, resolved, ppp, lines, fill_gaps, measures)

    def fetch_aggregates(
        self,
        regions: str | Iterable[str] = "all",
        years: str | int | Iterable[int] = "all",
        povline: float | Sequence[float] | None = 3.0,
        ppp_version: int = 2021,
        release_version: str | int | None = None,
        *,
        measures: Sequence[str] | None = None,
        refresh: bool = False,
    ) -> Result:
        """World Bank regional and global aggregates (PIP ``/pip-grp?group_by=wb``)."""
        ppp = _validate_ppp(ppp_version)
        rel = _validate_release(release_version)
        lines = self._lines(povline, ppp)
        resolved, provs, notes = self._resolve(ppp, rel, refresh)
        frames, info = [], []
        aggregates: set[str] = set()
        scaled = True
        for line in lines:
            url, params = build_group_request(regions, years, line, ppp, resolved["release_version"], resolved["version"] or None)
            payload, retrieval = http_fetch(self.source, url, params, cache=self.cache, refresh=refresh)
            parsed = parse_rows(_read_table(payload), ppp_version=ppp, povline=line, measures=measures, aggregate=True)
            frames.append(parsed.frame)
            info.extend(parsed.survey_info)
            aggregates.update(parsed.aggregates)
            scaled = scaled and parsed.scaled_x100
            provs.append(self._data_provenance(retrieval, resolved, ppp, notes, "PIP.fetch_aggregates"))
        return self._result(frames, provs, info, [], sorted(aggregates), 0, scaled, resolved, ppp, lines, False, measures)

    # -- helpers ---------------------------------------------------------------
    @staticmethod
    def _lines(povline: float | Sequence[float] | None, ppp: int) -> list[float]:
        if povline is None:
            if ppp not in STANDARD_LINES:
                raise ValueError(f"no default poverty line for PPP {ppp}; pass povline")
            return [STANDARD_LINES[ppp][0]]
        if isinstance(povline, bool):
            raise ValueError("povline must be a number or a list of numbers")
        raw = [povline] if isinstance(povline, (int, float)) else list(povline)
        lines = sorted({_validate_povline(float(x)) for x in raw})
        if not lines:
            raise ValueError("pass at least one poverty line")
        return lines

    def _data_provenance(self, retrieval, resolved: dict, ppp: int, notes: list[str], tool: str) -> Provenance:
        prov = self.provenance(retrieval, dataset=f"PIP_PPP{ppp}", vintage=resolved["release_version"], tool=tool, notes=list(notes))
        if resolved.get("version"):
            prov.notes.append(f"PIP data version {resolved['version']}")
            prov.citation = f"World Bank, Poverty and Inequality Platform (version {resolved['version']})"
        return prov

    def _result(self, frames, provs, info, dups, aggregates, dropped, scaled, resolved, ppp, lines, fill_gaps, measures) -> Result:
        data = pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(not f.empty for f in frames) else empty_frame()
        conflicts = 0
        if not data.empty:
            # Line-independent measures (gini, mean, deciles, ...) come back once per poverty line.
            key = ["source", "dataset", "indicator", "iso3", "period"]
            dup = data.duplicated(subset=key, keep=False)
            if dup.any():
                spread = data[dup].groupby(key)["value"].agg(lambda v: v.max() - v.min() if v.notna().all() else 0.0)
                conflicts = int((spread.abs() > 1e-9).sum())
                data = data.drop_duplicates(subset=key, keep="first")
            data = Connector.finalize(data)
        validate_frame(data)
        dups = [dict(t) for t in sorted({tuple(sorted(d.items())) for d in dups})]
        result = Result(
            data=data,
            provenance=provs,
            meta={
                "ppp_version": ppp,
                "release_version": resolved["release_version"],
                "version": resolved.get("version", ""),
                "poverty_lines": [line_code(x) for x in lines],
                "standard_lines": {"ipl": STANDARD_LINES.get(ppp, (None,))[0], "all": list(STANDARD_LINES.get(ppp, ()))},
                "aggregates": aggregates,
                "survey_info": info,
                "welfare_duplicates": dups,
                "dropped_nowcast_rows": dropped,
                "scaled_x100": scaled,
                "measures": list(measures or DEFAULT_MEASURES),
            },
        )
        for cav in self._caveats(result, ppp, lines, fill_gaps, scaled, dups, info):
            result.add_caveat(cav)
        if conflicts:
            result.add_caveat(Caveat("pip_inconsistent_repeats", "warn", "A line-independent measure differed between poverty-line requests; the first value was kept.", {"n_series": conflicts}))
        return result

    @staticmethod
    def _caveats(result: Result, ppp: int, lines: list[float], fill_gaps: bool, scaled: bool, dups: list[dict], info: list[dict]) -> list[Caveat]:
        cavs: list[Caveat] = [
            Caveat(
                "pip_ppp_base",
                "info",
                f"PIP values are in {ppp} PPP dollars; they are not comparable with estimates on other PPP rounds "
                "(earlier reports used $1.90 at 2011 PPPs or $2.15 at 2017 PPPs).",
                {"ppp_version": ppp, "release_version": result.meta["release_version"]},
            )
        ]
        standard = STANDARD_LINES.get(ppp, ())
        off = [line_code(x) for x in lines if standard and x not in standard]
        if off:
            belongs = {line_code(x): [p for p, s in STANDARD_LINES.items() if x in s] for x in lines if x not in standard}
            cavs.append(
                Caveat(
                    "pip_line_ppp_mismatch",
                    "warn",
                    f"Poverty line(s) {off} are not standard lines for {ppp} PPPs (standard: {[line_code(x) for x in standard]}). "
                    "A line defined on another PPP round must not be applied to this one.",
                    {"lines": off, "standard": [line_code(x) for x in standard], "standard_for_other_ppp": belongs},
                )
            )
        cavs.extend(poverty_mix_caveats(result.data))
        df = result.data
        if not df.empty and (df["obs_status"] == "imputed").any():
            cavs.append(
                Caveat(
                    "pip_interpolated",
                    "warn" if fill_gaps else "info",
                    "Some values are interpolated/extrapolated by PIP for years without a survey (obs_status 'imputed').",
                    {"n_imputed": int((df["obs_status"] == "imputed").sum())},
                )
            )
        if not df.empty and (df["obs_status"].isin(["modelled", "forecast"])).any():
            cavs.append(Caveat("pip_nowcast", "warn", "Some values are PIP nowcasts/projections, not survey estimates.", {"n": int(df["obs_status"].isin(["modelled", "forecast"]).sum())}))
        if dups:
            cavs.append(
                Caveat(
                    "pip_welfare_duplicates",
                    "warn",
                    "Some country-years have both income- and consumption-based estimates; they are kept as separate "
                    "series (indicator suffix |income / |consumption). Pass welfare_type to get one series.",
                    {"country_years": dups[:20], "n": len(dups)},
                )
            )
        mixed = sorted({r["iso3"] for r in info if r.get("welfare_type")} & {
            iso for iso in {r["iso3"] for r in info}
            if len({r.get("welfare_type") for r in info if r["iso3"] == iso and r.get("welfare_type")}) > 1
        })
        if mixed:
            cavs.append(Caveat("pip_welfare_mix", "info", "Series mix income- and consumption-based surveys over time; levels are not strictly comparable.", {"countries": mixed}))
        breaks = sorted({
            iso for iso in {r["iso3"] for r in info}
            if len({r.get("survey_comparability") for r in info if r["iso3"] == iso and r.get("survey_comparability") not in (None, "")}) > 1
        })
        if breaks:
            cavs.append(Caveat("pip_comparability_break", "info", "Survey comparability changes within these series (see survey_comparability / comparable_spell in meta['survey_info']).", {"countries": breaks}))
        dist = sorted({str(r["distribution_type"]) for r in info if r.get("distribution_type") not in (None, "", "micro")})
        if dist:
            cavs.append(Caveat("pip_distribution_type", "info", "Some estimates come from grouped, aggregated or imputed distributions rather than microdata.", {"distribution_types": dist}))
        if not scaled:
            cavs.append(Caveat("pip_scale_assumption", "warn", "headcount values above 1 were found, so PIP fractions were assumed to be in percent already and not rescaled.", {}))
        if df.empty:
            cavs.append(Caveat("pip_empty", "warn", "PIP returned no rows (no survey in the requested years? try fill_gaps=True).", {}))
        return cavs
