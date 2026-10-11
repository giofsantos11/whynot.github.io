"""Structural change: value added per worker, shift-share, sector contributions, beta-convergence.

Every function is pure and deterministic. Inputs are canonical frames (or
:class:`~cgjrkit.core.provenance.Result` objects wrapping them, see
:mod:`cgjrkit.core.schema`); outputs are new ``Result`` objects that carry the
inputs' provenance, caveats and meta forward and append one
``Provenance(source="cgjrkit", tool="structural_change.<name>", params=...)``.

Functions
---------
=========================  ====================================================
``sector_panel``           canonical frame -> tidy ``iso3, period, sector``
                           panel (indicator->sector mapping, concordance sums)
``va_per_worker``          sector value added / sector employment (Kenya Fig 33)
``shift_share``            McMillan-Rodrik within/between decomposition of the
                           change in aggregate labour productivity, with the
                           three-term (within / static / dynamic) and Shapley
                           (mid-point) variants; exact identity check
                           (Kenya Fig 35)
``sector_contributions``   sector contributions to real value-added growth,
                           "average growth x average share" (Kenya Fig 2) or
                           the exact additive form; flags non-additivity
``beta_convergence``       absolute / conditional beta-convergence regression,
                           cross-section or pooled spells (SP1)
=========================  ====================================================

Sectors
-------
The canonical frame has no sector column: providers encode the sector in the
indicator code (WDI ``NV.AGR.TOTL.KD``; ILOSTAT
``EMP_2EMP_SEX_ECO_NB|SEX_T|ECO_SECTOR_AGR``). Every function therefore takes
an optional mapping ``{indicator code: sector label}``. Several codes may map
to one label (an ISIC -> report-sector concordance); their values are summed,
and the sum is missing unless every mapped code has a value. Without a
mapping, a ``sector`` column is used when present, else each indicator is a
sector.

Shift-share formulas
--------------------
With sectors ``i``, employment ``L_i``, value added ``VA_i``, employment
shares ``theta_i = L_i / sum(L)``, sector productivity ``y_i = VA_i / L_i``
and aggregate productivity ``Y = sum(VA) / sum(L) = sum(theta_i * y_i)``,
between a start period ``0`` and an end period ``T``:

* ``two_term`` (McMillan and Rodrik 2011, eq. 1):
  ``dY = sum(theta_i0 * dy_i)  [within]  +  sum(y_iT * dtheta_i)  [between]``
* ``three_term`` (de Vries, Timmer and de Vries 2015; Timmer and Szirmai):
  ``dY = sum(theta_i0 * dy_i) [within] + sum(y_i0 * dtheta_i) [static between]
  + sum(dy_i * dtheta_i) [dynamic between]``
* ``shapley`` (mid-point weights, the Shapley value of the two factors):
  ``dY = sum(mean(theta_i) * dy_i) [within] + sum(mean(y_i) * dtheta_i) [between]``

All three are exact identities over the sectors supplied. When an aggregate
value added or employment series is also given (GDP, total employment), the
aggregate change is compared with the sector-based one and a gap (taxes on
products, FISIM, sectors missing from the panel) raises
:class:`ShiftShareIdentityError` or attaches a caveat.

Annualisation (``annualize``) rescales every component by the same factor,
so the identity still holds after rescaling:

* ``"none"``: percent of initial aggregate productivity (cumulative change);
* ``"simple"``: cumulative percent divided by the number of years;
* ``"cagr"``: components proportional to their share of ``dY``, summing to
  the compound annual growth rate of ``Y`` (default);
* ``"log"``: same, summing to ``100 * ln(Y_T / Y_0) / years``.

References
----------
McMillan, M. and D. Rodrik (2011), "Globalization, Structural Change and
Productivity Growth", NBER WP 17143. de Vries, G., M. Timmer and K. de Vries
(2015), "Structural Transformation in Africa: Static Gains, Dynamic Losses",
Journal of Development Studies 51(6). Barro, R. and X. Sala-i-Martin (1992),
"Convergence", Journal of Political Economy 100(2).
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Iterable, Literal, Mapping, Sequence

import numpy as np
import pandas as pd

from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Result
from cgjrkit.core.schema import COLUMNS, ppp_base
from cgjrkit.methods.harmonize import (
    FrequencyMismatchError,
    PeriodSpec,
    PPPMixError,
    _finish,
    _limited,
    _price_basis,
    _window,
    combine_status,
    format_period,
    parse_period,
)

__all__ = [
    "StructuralChangeError",
    "ShiftShareIdentityError",
    "SectorMap",
    "sector_panel",
    "annualize_factor",
    "va_per_worker",
    "decompose_productivity",
    "shift_share",
    "sector_contributions",
    "beta_convergence",
    "TOTAL",
]

Frame = Result | pd.DataFrame
SectorMap = Mapping[str, str]
Annualize = Literal["none", "simple", "cagr", "log"]
Variant = Literal["two_term", "three_term", "shapley"]

#: Sector label used for aggregate rows.
TOTAL = "TOTAL"

#: Relative tolerance of the internal identity assertion (pure floating-point noise).
_IDENTITY_RTOL = 1e-9

_COMPONENTS: dict[str, tuple[str, ...]] = {
    "two_term": ("within", "between"),
    "three_term": ("within", "static_between", "dynamic_between"),
    "shapley": ("within", "between"),
}

_COMPONENT_LABELS = {
    "within": "Within-sector productivity change",
    "between": "Between-sector (structural) change",
    "static_between": "Static reallocation (between)",
    "dynamic_between": "Dynamic reallocation (cross term)",
    "total": "Change in aggregate labour productivity",
}

_ANNUALIZE_UNITS = {
    "none": "percent of initial level (cumulative)",
    "simple": "percentage points per year (cumulative % / years)",
    "cagr": "percentage points per year (shares of CAGR)",
    "log": "percentage points per year (shares of log growth x 100)",
}


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------
class StructuralChangeError(ValueError):
    """Inputs cannot be decomposed as requested (missing sectors, zero employment, ...)."""


class ShiftShareIdentityError(StructuralChangeError):
    """Sector components do not add up to the aggregate productivity change."""


# ---------------------------------------------------------------------------
# input handling (shared with jobs_shapley and standard_tables)
# ---------------------------------------------------------------------------
def _coerce(obj: Frame, name: str) -> Result:
    """Copy ``obj`` into a Result whose frame has every canonical column (extra columns kept).

    Unlike :func:`cgjrkit.methods.harmonize._as_result` this does not reject a
    frame whose rows differ only by an extra ``sector`` column.
    """
    if isinstance(obj, Result):
        df, prov, cav, meta = obj.data, list(obj.provenance), list(obj.caveats), dict(obj.meta)
    elif isinstance(obj, pd.DataFrame):
        df, prov, cav, meta = obj, [], [], {}
    else:
        raise TypeError(f"{name} must be a Result or a pandas DataFrame, not {type(obj).__name__}")
    missing = [c for c in ("iso3", "period", "value") if c not in df.columns]
    if missing:
        raise ValueError(f"{name} is missing columns {missing}")
    df = df.copy()
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df["value"] = pd.to_numeric(df["value"], errors="coerce").astype("float64")
    for col in COLUMNS:
        if col != "value":
            df[col] = df[col].fillna("").astype(str).str.strip()
    if "sector" in df.columns:
        df["sector"] = df["sector"].fillna("").astype(str).str.strip()
    if (df["iso3"] == "").any() or (df["period"] == "").any():
        raise ValueError(f"{name} has rows without iso3 or period")
    return Result(data=df.reset_index(drop=True), provenance=prov, caveats=cav, meta=meta)


def _ordered_unique(values: Iterable[str]) -> list[str]:
    seen: dict[str, None] = {}
    for v in values:
        seen.setdefault(v, None)
    return list(seen)


def _check_ppp(units: Iterable[str], context: str) -> None:
    bases = sorted({b for b in (ppp_base(u) for u in units if u) if b})
    if len(bases) > 1:
        raise PPPMixError(f"{context}: inputs carry different PPP bases {bases}")


def sector_panel(
    frame: Frame,
    sectors: SectorMap | None = None,
    *,
    name: str = "frame",
) -> tuple[Result, pd.DataFrame, list[str]]:
    """Tidy sector panel from a canonical frame.

    Returns ``(input_result, panel, sector_order)``. ``panel`` has one row per
    ``iso3, period, sector`` with columns ``iso3, country, period, sector,
    value, unit, obs_status``. ``sectors`` maps indicator codes to sector
    labels (several codes may share a label; their values are summed and the
    sum is NaN unless every mapped code is present and non-missing for that
    country-period). ``sector_order`` follows the mapping's order, else the
    order of first appearance.
    """
    res = _coerce(frame, name)
    df = res.data
    if sectors is not None:
        mapping = {str(k).strip(): str(v).strip() for k, v in sectors.items()}
        if not mapping:
            raise ValueError(f"{name}: the sector mapping is empty")
        df = df[df["indicator"].isin(mapping)].copy()
        if df.empty:
            raise StructuralChangeError(f"{name}: none of the mapped indicators {sorted(mapping)} is in the frame")
        df["sector"] = df["indicator"].map(mapping)
        expected: Counter | None = Counter(mapping.values())
        order = _ordered_unique(mapping.values())
    elif "sector" in df.columns and len(df) and (df["sector"] != "").all():
        df = df.copy()
        expected = None
        order = _ordered_unique(df["sector"])
    else:
        df = df.copy()
        df["sector"] = df["indicator"]
        expected = None
        order = _ordered_unique(df["sector"])
    if (df["sector"] == "").any():
        raise ValueError(f"{name}: some rows have an empty sector/indicator")
    dup = df.duplicated(subset=["iso3", "period", "sector", "indicator"])
    if dup.any():
        r = df.loc[dup].iloc[0]
        raise StructuralChangeError(
            f"{name}: several observations for {r['iso3']}/{r['period']}/{r['sector']} (indicator {r['indicator']!r}); "
            "filter the frame to one source/dataset first")
    _check_ppp(df["unit"], name)

    rows = []
    for (iso3, period, sector), g in df.groupby(["iso3", "period", "sector"], sort=True):
        vals = g["value"].to_numpy(dtype="float64")
        complete = bool(np.isfinite(vals).all())
        if expected is not None and g["indicator"].nunique() < expected[sector]:
            complete = False
        units = sorted({u for u in g["unit"] if u})
        countries = [c for c in g["country"] if c]
        rows.append({
            "iso3": iso3,
            "country": countries[0] if countries else "",
            "period": period,
            "sector": sector,
            "value": float(vals.sum()) if complete else float("nan"),
            "unit": units[0] if len(units) == 1 else "; ".join(units),
            "obs_status": combine_status(g["obs_status"]),
        })
    panel = pd.DataFrame(rows, columns=["iso3", "country", "period", "sector", "value", "unit", "obs_status"])
    panel["value"] = panel["value"].astype("float64")
    return res, panel, order


def _single_series(frame: Frame, name: str, indicator: str | None = None) -> tuple[Result, pd.DataFrame]:
    """A frame holding one series per country -> ``(result, frame indexed by iso3, period)``."""
    res = _coerce(frame, name)
    df = res.data
    if indicator is not None:
        df = df[df["indicator"] == indicator]
        if df.empty:
            raise StructuralChangeError(f"{name}: indicator {indicator!r} not in the frame")
    n_ind = df.groupby("iso3")["indicator"].nunique() if len(df) else pd.Series(dtype=int)
    if (n_ind > 1).any():
        raise StructuralChangeError(
            f"{name}: several indicators for {n_ind[n_ind > 1].index[0]}; pass indicator= or filter the frame")
    dup = df.duplicated(subset=["iso3", "period"])
    if dup.any():
        r = df.loc[dup].iloc[0]
        raise StructuralChangeError(f"{name}: several observations for {r['iso3']}/{r['period']}")
    _check_ppp(df["unit"], name)
    return res, df.set_index(["iso3", "period"]).sort_index()


def _units(panel: pd.DataFrame) -> list[str]:
    return sorted({u for u in panel["unit"] if u})


def _unit_caveats(panel: pd.DataFrame, name: str) -> list[Caveat]:
    units = _units(panel)
    if len(units) > 1:
        return [Caveat("mixed_units", "warn", f"{name}: sectors carry different unit labels; check they are comparable.",
                       {"input": name, "units": units})]
    return []


def _modelled_caveat(results: Sequence[Result], panels: Sequence[pd.DataFrame]) -> list[Caveat]:
    """WARN when inputs contain modelled values and no connector caveat already says so."""
    if any("modelled" in c.code for r in results for c in r.caveats):
        return []
    n = int(sum((p["obs_status"] == "modelled").sum() for p in panels))
    if n:
        return [Caveat("modelled_inputs", "warn",
                       "Some inputs are model-based estimates (e.g. ILO modelled employment); footnote them and do not "
                       "splice them with survey series.", {"rows": n})]
    return []


def _endpoint_caveats(starts_ends: Iterable[tuple[str, str]], statuses: Iterable[str], context: str) -> list[Caveat]:
    out: list[Caveat] = []
    covid = sorted({p for pair in starts_ends for p in pair if (parse_period(p).year in (2020, 2021))})
    if covid:
        out.append(Caveat("covid_endpoint", "info",
                          f"{context}: a window starts or ends in 2020-21; COVID-19 swings dominate such comparisons.",
                          {"periods": covid}))
    st = set(statuses)
    if "forecast" in st:
        out.append(Caveat("forecast_in_window", "warn", f"{context}: some values used are forecasts.", {}))
    if "break" in st:
        out.append(Caveat("series_break_in_window", "warn", f"{context}: a series break falls on a value used.", {}))
    return out


def _done(inputs: Sequence[Result], data: pd.DataFrame, tool: str, params: Mapping[str, Any],
          caveats: Iterable[Caveat] = (), notes: Iterable[str] = (), module: str = "structural_change") -> Result:
    out = _finish(inputs, data, tool, params, list(caveats), list(notes))
    out.provenance[-1].dataset = module
    out.provenance[-1].tool = f"{module}.{tool}"
    return out


def _years_between(lo: str, hi: str) -> float:
    a, b = parse_period(lo), parse_period(hi)
    if a.freq != b.freq:
        raise FrequencyMismatchError(f"periods {lo!r} and {hi!r} have different frequencies")
    years = (b.ordinal - a.ordinal) / a.steps_per_year
    if years <= 0:
        raise ValueError(f"end period {hi!r} must come after start period {lo!r}")
    return years


def annualize_factor(y0: float, yT: float, years: float, annualize: Annualize) -> float:
    """Factor ``k`` turning a level change component into percentage points.

    Every component is multiplied by the same ``k`` so additive identities
    survive the rescaling: ``"none"`` gives ``100 / y0`` (cumulative percent),
    ``"simple"`` ``100 / (y0 * years)``, ``"cagr"`` ``CAGR / (yT - y0)`` and
    ``"log"`` ``100 * ln(yT / y0) / years / (yT - y0)``. When ``yT == y0`` the
    CAGR and log forms fall back to ``"simple"`` (all three are zero then).
    """
    if annualize not in _ANNUALIZE_UNITS:
        raise ValueError(f"annualize must be one of {sorted(_ANNUALIZE_UNITS)}")
    if not (np.isfinite(y0) and y0 != 0):
        raise StructuralChangeError("the initial aggregate level is zero or missing")
    if annualize == "none":
        return 100.0 / y0
    if annualize == "simple" or yT == y0:
        return 100.0 / (y0 * years)
    if y0 <= 0 or yT <= 0:
        raise StructuralChangeError(f"annualize={annualize!r} needs positive levels (got {y0}, {yT})")
    if annualize == "cagr":
        total = 100.0 * ((yT / y0) ** (1.0 / years) - 1.0)
    else:
        total = 100.0 * math.log(yT / y0) / years
    return total / (yT - y0)


# ---------------------------------------------------------------------------
# va_per_worker
# ---------------------------------------------------------------------------
def va_per_worker(
    va_frame: Frame,
    emp_frame: Frame,
    *,
    va_sectors: SectorMap | None = None,
    emp_sectors: SectorMap | None = None,
    va_scale: float = 1.0,
    emp_scale: float = 1.0,
    total: str | None = TOTAL,
    unit: str | None = None,
) -> Result:
    """Value added per worker by sector: ``VA_i * va_scale / (L_i * emp_scale)``.

    ``va_sectors`` / ``emp_sectors`` map each frame's indicator codes to common
    sector labels (see :func:`sector_panel`). ``emp_scale`` converts employment
    to persons (ILOSTAT reports thousands: ``emp_scale=1000``). With ``total``
    set, an aggregate row ``sum(VA) / sum(L)`` is added for every
    country-period where every sector seen for that country has both values.

    Output: indicator ``"va_per_worker|<sector>"``, extra columns ``sector``,
    ``va`` and ``employment`` (both scaled). Unit: ``unit`` or
    ``"<VA unit> per worker"``. Sectors present in only one input are listed
    in a ``va_per_worker_unmatched`` caveat; non-positive employment gives NaN.
    """
    va_res, va, va_order = sector_panel(va_frame, va_sectors, name="va_frame")
    emp_res, emp, emp_order = sector_panel(emp_frame, emp_sectors, name="emp_frame")
    m = va.merge(emp, on=["iso3", "period", "sector"], how="outer", suffixes=("_va", "_emp"), indicator="match")
    caveats: list[Caveat] = _unit_caveats(va, "va_frame") + _unit_caveats(emp, "emp_frame")
    caveats += _modelled_caveat([va_res, emp_res], [va, emp])
    unmatched = m[m["match"] != "both"]
    if len(unmatched):
        items = [f"{r.sector}@{r.iso3}@{r.period} ({'VA only' if r.match == 'left_only' else 'employment only'})"
                 for r in unmatched.itertuples()]
        caveats.append(Caveat("va_per_worker_unmatched", "info",
                              f"{len(items)} sector-periods have value added or employment but not both.",
                              {"at": _limited(items), "n": len(items)}))
    both = m[m["match"] == "both"].copy()
    both["va"] = both["value_va"] * va_scale
    both["employment"] = both["value_emp"] * emp_scale
    nonpos = both["employment"].notna() & (both["employment"] <= 0)
    if nonpos.any():
        caveats.append(Caveat("nonpositive_employment", "warn", "Some sector-periods have zero or negative employment; "
                              "value added per worker is left missing.",
                              {"at": _limited([f"{r.sector}@{r.iso3}@{r.period}" for r in both[nonpos].itertuples()])}))
    with np.errstate(divide="ignore", invalid="ignore"):
        both["ratio"] = np.where(both["employment"] > 0, both["va"] / both["employment"], np.nan)

    va_units = _units(va)
    out_unit = unit or (f"{va_units[0]} per worker" if len(va_units) == 1 else "value added per worker")
    basis = {_price_basis(u) for u in va_units}
    if "current" in basis:
        caveats.append(Caveat("va_current_prices", "warn",
                              "Value added is in current prices; productivity growth needs constant-price value added.",
                              {"units": va_units}))

    order = _ordered_unique(list(va_order) + list(emp_order))
    rank = {s: i for i, s in enumerate(order)}
    records = []
    for r in both.itertuples():
        records.append({
            "iso3": r.iso3, "country": r.country_va or r.country_emp, "period": r.period, "sector": r.sector,
            "value": r.ratio, "va": r.va, "employment": r.employment,
            "obs_status": combine_status([r.obs_status_va, r.obs_status_emp]),
        })
    if total:
        sectors_by_iso = {iso: set(g["sector"]) for iso, g in m.groupby("iso3")}
        for (iso3, period), g in both.groupby(["iso3", "period"], sort=True):
            if total in set(g["sector"]):
                continue  # the mapping already provides an aggregate sector
            ok = (set(g["sector"]) >= sectors_by_iso[iso3]) and g["va"].notna().all() and (g["employment"] > 0).all()
            if not ok:
                continue
            records.append({
                "iso3": iso3, "country": next((c for c in g["country_va"] if c), ""), "period": period, "sector": total,
                "value": float(g["va"].sum() / g["employment"].sum()), "va": float(g["va"].sum()),
                "employment": float(g["employment"].sum()),
                "obs_status": combine_status(list(g["obs_status_va"]) + list(g["obs_status_emp"])),
            })
    out = pd.DataFrame(records, columns=["iso3", "country", "period", "sector", "value", "va", "employment", "obs_status"])
    out["source"] = "cgjrkit"
    out["dataset"] = "structural_change"
    out["indicator"] = "va_per_worker|" + out["sector"]
    out["indicator_label"] = "Value added per worker: " + out["sector"]
    out["unit"] = out_unit
    out["_rank"] = out["sector"].map(lambda s: rank.get(s, len(rank)))
    out = out.sort_values(["iso3", "period", "_rank"], kind="mergesort").drop(columns="_rank")
    out = out[COLUMNS + ["sector", "va", "employment"]].reset_index(drop=True)
    for col in ("value", "va", "employment"):
        out[col] = out[col].astype("float64")
    params = {"va_sectors": dict(va_sectors) if va_sectors else None,
              "emp_sectors": dict(emp_sectors) if emp_sectors else None,
              "va_scale": va_scale, "emp_scale": emp_scale, "total": total, "unit": unit}
    return _done([va_res, emp_res], out, "va_per_worker", params, caveats,
                 ["value added per worker = VA_i * va_scale / (L_i * emp_scale); total = sum(VA) / sum(L)"])


# ---------------------------------------------------------------------------
# shift_share
# ---------------------------------------------------------------------------
def _sector_values(panel: pd.DataFrame, iso3: str, period: str) -> dict[str, float]:
    sub = panel[(panel["iso3"] == iso3) & (panel["period"] == period)]
    return dict(zip(sub["sector"], sub["value"].astype(float)))


def _shift_share_terms(theta0: np.ndarray, thetaT: np.ndarray, y0: np.ndarray, yT: np.ndarray,
                       variant: str) -> dict[str, np.ndarray]:
    dy, dth = yT - y0, thetaT - theta0
    if variant == "two_term":
        return {"within": theta0 * dy, "between": yT * dth}
    if variant == "three_term":
        return {"within": theta0 * dy, "static_between": y0 * dth, "dynamic_between": dy * dth}
    if variant == "shapley":
        return {"within": 0.5 * (theta0 + thetaT) * dy, "between": 0.5 * (y0 + yT) * dth}
    raise ValueError(f"variant must be one of {sorted(_COMPONENTS)}")


def decompose_productivity(
    va0: Mapping[str, float], vaT: Mapping[str, float], emp0: Mapping[str, float], empT: Mapping[str, float],
    variant: Variant = "two_term",
) -> dict[str, Any]:
    """Shift-share terms for one country from sector dicts (pure arithmetic, no frames).

    Returns ``{"sectors", "theta0", "thetaT", "y0", "yT", "Y0", "YT", "terms":
    {component: {sector: level}}, "sum_components", "delta_y"}``. Raises
    :class:`StructuralChangeError` when a sector lacks a value or has
    non-positive employment, and asserts the identity to floating precision.
    """
    sectors = _ordered_unique(list(emp0) + list(empT) + list(va0) + list(vaT))
    parts = (("value added, start", va0), ("value added, end", vaT), ("employment, start", emp0),
             ("employment, end", empT))
    missing = [f"{s} ({which})" for s in sectors for which, d in parts
               if s not in d or not np.isfinite(d[s])]
    if missing:
        raise StructuralChangeError(f"shift-share needs every sector at both periods; missing: {missing}")
    L0 = np.array([emp0[s] for s in sectors], dtype="float64")
    LT = np.array([empT[s] for s in sectors], dtype="float64")
    V0 = np.array([va0[s] for s in sectors], dtype="float64")
    VT = np.array([vaT[s] for s in sectors], dtype="float64")
    bad = [s for s, a, b in zip(sectors, L0, LT) if a <= 0 or b <= 0]
    if bad:
        raise StructuralChangeError(f"non-positive employment in sectors {bad}; merge them into another sector")
    theta0, thetaT = L0 / L0.sum(), LT / LT.sum()
    y0, yT = V0 / L0, VT / LT
    Y0, YT = V0.sum() / L0.sum(), VT.sum() / LT.sum()
    terms = _shift_share_terms(theta0, thetaT, y0, yT, variant)
    total = float(sum(t.sum() for t in terms.values()))
    dY = float(YT - Y0)
    if abs(total - dY) > _IDENTITY_RTOL * max(1.0, abs(Y0), abs(YT)):
        raise ShiftShareIdentityError(f"internal identity failed: components {total} vs dY {dY}")  # pragma: no cover
    return {
        "sectors": sectors,
        "theta0": dict(zip(sectors, theta0)), "thetaT": dict(zip(sectors, thetaT)),
        "y0": dict(zip(sectors, y0)), "yT": dict(zip(sectors, yT)),
        "Y0": float(Y0), "YT": float(YT),
        "terms": {c: dict(zip(sectors, v.astype(float))) for c, v in terms.items()},
        "sum_components": total, "delta_y": dY,
    }


def shift_share(
    va_by_sector: Frame,
    emp_by_sector: Frame,
    start: str,
    end: str,
    variant: Variant = "two_term",
    *,
    va_sectors: SectorMap | None = None,
    emp_sectors: SectorMap | None = None,
    aggregate_va: Frame | None = None,
    aggregate_emp: Frame | None = None,
    annualize: Annualize = "cagr",
    on_gap: Literal["raise", "caveat"] = "raise",
    gap_tolerance: float = 1e-6,
    emp_scale: float = 1.0,
    iso3: Sequence[str] | None = None,
) -> Result:
    """Decompose the change in aggregate labour productivity between ``start`` and ``end``.

    ``variant`` is ``"two_term"`` (McMillan-Rodrik: within + between),
    ``"three_term"`` (within + static between + dynamic cross term) or
    ``"shapley"`` (mid-point weights). Every country present in both inputs
    is decomposed (or only ``iso3``). Each sector must have value added and
    positive employment at both periods.

    Identity: components sum exactly to ``Y_T - Y_0`` with ``Y = sum(VA) /
    sum(L)`` over the sectors given (asserted to floating precision). If
    ``aggregate_va`` and/or ``aggregate_emp`` are given (e.g. GDP and total
    employment), the aggregate change ``VA_agg/L_agg`` is compared with the
    sector-based one; a relative gap above ``gap_tolerance`` (relative to
    initial aggregate productivity) raises :class:`ShiftShareIdentityError`
    (``on_gap="raise"``) or attaches a ``shift_share_identity_gap`` caveat.

    Output rows per country: ``"shift_share.<component>|<sector>"`` for each
    sector, ``"shift_share.<component>|TOTAL"`` per component and
    ``"shift_share.total|TOTAL"``. ``value`` is in percentage points per
    ``annualize`` (default ``"cagr"``: components sum to the CAGR of
    aggregate productivity). Extra columns: ``sector``, ``component``,
    ``window``, ``start``, ``end``, ``level_change`` (currency per worker),
    ``pct_cumulative`` (percent of initial productivity), ``share_of_total``
    (percent of ``dY``) and ``variant``.
    """
    if variant not in _COMPONENTS:
        raise ValueError(f"variant must be one of {sorted(_COMPONENTS)}")
    if on_gap not in ("raise", "caveat"):
        raise ValueError("on_gap must be 'raise' or 'caveat'")
    start, end = str(start).strip(), str(end).strip()
    years = _years_between(start, end)
    va_res, va, va_order = sector_panel(va_by_sector, va_sectors, name="va_by_sector")
    emp_res, emp, emp_order = sector_panel(emp_by_sector, emp_sectors, name="emp_by_sector")
    if emp_scale != 1.0:
        emp = emp.assign(value=emp["value"] * emp_scale)
    inputs = [va_res, emp_res]
    agg_va = agg_emp = None
    if aggregate_va is not None:
        r, agg_va = _single_series(aggregate_va, "aggregate_va")
        inputs.append(r)
    if aggregate_emp is not None:
        r, agg_emp = _single_series(aggregate_emp, "aggregate_emp")
        inputs.append(r)
        agg_emp = agg_emp.assign(value=agg_emp["value"] * emp_scale)

    countries = sorted(set(va["iso3"]) & set(emp["iso3"]))
    if iso3 is not None:
        wanted = [str(c).upper() for c in iso3]
        absent = sorted(set(wanted) - set(countries))
        if absent:
            raise StructuralChangeError(f"no sector data for {absent} in both inputs")
        countries = sorted(set(wanted))
    if not countries:
        raise StructuralChangeError("no country has both value added and employment by sector")

    order = _ordered_unique(list(emp_order) + list(va_order))
    rank = {s: i for i, s in enumerate(order)}
    window = f"{start}-{end}"
    caveats: list[Caveat] = _unit_caveats(va, "va_by_sector") + _unit_caveats(emp, "emp_by_sector")
    caveats += _modelled_caveat([va_res, emp_res], [va, emp])
    statuses: list[str] = []
    gaps: list[dict] = []
    meta_out: dict[str, Any] = {}
    records: list[dict] = []
    for c in countries:
        va0, vaT = _sector_values(va, c, start), _sector_values(va, c, end)
        emp0, empT = _sector_values(emp, c, start), _sector_values(emp, c, end)
        if not emp0:
            raise StructuralChangeError(f"{c}: no employment by sector in {start}")
        try:
            d = decompose_productivity(va0, vaT, emp0, empT, variant)
        except StructuralChangeError as exc:
            raise StructuralChangeError(f"{c} {window}: {exc}") from exc
        sub_status = pd.concat([
            va[(va["iso3"] == c) & va["period"].isin([start, end])]["obs_status"],
            emp[(emp["iso3"] == c) & emp["period"].isin([start, end])]["obs_status"],
        ])
        statuses += list(sub_status)
        status = combine_status(sub_status)
        Y0, YT, dY = d["Y0"], d["YT"], d["delta_y"]
        k = annualize_factor(Y0, YT, years, annualize)

        # -- identity against the aggregate series ---------------------------------
        agg_check: dict[str, Any] = {"aggregate_given": agg_va is not None or agg_emp is not None}
        if agg_check["aggregate_given"]:
            def _agg(series: pd.DataFrame | None, sector_sum: float, period: str, label: str) -> float:
                if series is None:
                    return sector_sum
                try:
                    v = float(series.loc[(c, period), "value"])
                except KeyError as exc:
                    raise StructuralChangeError(f"{c}: {label} has no value for {period}") from exc
                return v
            sva0, svaT = sum(va0.values()), sum(vaT.values())
            sl0, slT = sum(emp0.values()), sum(empT.values())
            A0 = _agg(agg_va, sva0, start, "aggregate_va") / _agg(agg_emp, sl0, start, "aggregate_emp")
            AT = _agg(agg_va, svaT, end, "aggregate_va") / _agg(agg_emp, slT, end, "aggregate_emp")
            gap = (AT - A0) - dY
            rel = abs(gap) / abs(A0) if A0 else float("inf")
            agg_check.update({
                "aggregate_Y0": A0, "aggregate_YT": AT, "aggregate_delta_y": AT - A0, "sector_delta_y": dY,
                "gap": gap, "relative_gap": rel,
                "va_coverage": [sva0 / _agg(agg_va, sva0, start, "aggregate_va"),
                                svaT / _agg(agg_va, svaT, end, "aggregate_va")],
                "emp_coverage": [sl0 / _agg(agg_emp, sl0, start, "aggregate_emp"),
                                 slT / _agg(agg_emp, slT, end, "aggregate_emp")],
                "exact": rel <= gap_tolerance,
            })
            if rel > gap_tolerance:
                gaps.append({"iso3": c, "window": window, "aggregate_delta_y": AT - A0, "sector_delta_y": dY,
                             "gap": gap, "gap_pct_of_aggregate_change": (100.0 * gap / (AT - A0)) if AT != A0 else None,
                             "va_coverage": agg_check["va_coverage"], "emp_coverage": agg_check["emp_coverage"]})

        country = next((x for x in va.loc[va["iso3"] == c, "country"] if x), "")

        def row(component: str, sector: str, level: float) -> dict:
            return {
                "iso3": c, "country": country, "period": window, "sector": sector, "component": component,
                "value": level * k, "level_change": level, "pct_cumulative": 100.0 * level / Y0,
                "share_of_total": (100.0 * level / dY) if dY != 0 else float("nan"),
                "obs_status": status,
            }

        for comp in _COMPONENTS[variant]:
            for s in sorted(d["sectors"], key=lambda s: rank.get(s, len(rank))):
                records.append(row(comp, s, d["terms"][comp][s]))
        for comp in _COMPONENTS[variant]:
            records.append(row(comp, TOTAL, float(sum(d["terms"][comp].values()))))
        records.append(row("total", TOTAL, dY))
        meta_out[c] = {
            "variant": variant, "start": start, "end": end, "years": years, "Y0": Y0, "YT": YT, "delta_y": dY,
            "sum_components": d["sum_components"], "identity_abs_error": abs(d["sum_components"] - dY),
            "annualize": annualize, "total_pp": dY * k, "sectors": d["sectors"],
            "employment_shares": {"start": d["theta0"], "end": d["thetaT"]},
            "productivity": {"start": d["y0"], "end": d["yT"]},
            "aggregate_check": agg_check,
        }

    if gaps:
        msg = (f"Sector components do not add up to the aggregate productivity change for {len(gaps)} country-window(s) "
               "(sector coverage, taxes on products/FISIM, or employment totals differ); the decomposition is exact "
               "only over the sectors supplied.")
        if on_gap == "raise":
            raise ShiftShareIdentityError(msg + f" Evidence: {gaps[:3]}. Pass on_gap='caveat' to proceed.")
        caveats.append(Caveat("shift_share_identity_gap", "warn", msg, {"gaps": _limited(gaps), "n": len(gaps),
                                                                        "tolerance": gap_tolerance}))
    caveats += _endpoint_caveats([(start, end)], statuses, "shift_share")

    out = pd.DataFrame(records)
    out["source"] = "cgjrkit"
    out["dataset"] = "structural_change"
    out["indicator"] = "shift_share." + out["component"] + "|" + out["sector"]
    out["indicator_label"] = [f"{_COMPONENT_LABELS[comp]}: {sec}" if sec != TOTAL else _COMPONENT_LABELS[comp]
                              for comp, sec in zip(out["component"], out["sector"])]
    out["unit"] = _ANNUALIZE_UNITS[annualize]
    out["window"], out["start"], out["end"], out["variant"] = window, start, end, variant
    extra = ["sector", "component", "window", "start", "end", "level_change", "pct_cumulative", "share_of_total", "variant"]
    out = out[COLUMNS + extra].reset_index(drop=True)
    for col in ("value", "level_change", "pct_cumulative", "share_of_total"):
        out[col] = out[col].astype("float64")
    params = {"start": start, "end": end, "variant": variant, "va_sectors": dict(va_sectors) if va_sectors else None,
              "emp_sectors": dict(emp_sectors) if emp_sectors else None, "aggregate_va": aggregate_va is not None,
              "aggregate_emp": aggregate_emp is not None, "annualize": annualize, "on_gap": on_gap,
              "gap_tolerance": gap_tolerance, "emp_scale": emp_scale, "iso3": countries}
    formula = {
        "two_term": "dY = sum(theta_i0*dy_i) [within] + sum(y_iT*dtheta_i) [between] (McMillan & Rodrik 2011)",
        "three_term": "dY = sum(theta_i0*dy_i) + sum(y_i0*dtheta_i) + sum(dy_i*dtheta_i) (de Vries, Timmer & de Vries 2015)",
        "shapley": "dY = sum(mean(theta_i)*dy_i) [within] + sum(mean(y_i)*dtheta_i) [between] (Shapley mid-point)",
    }[variant]
    result = _done(inputs, out, "shift_share", params, caveats,
                   [formula, f"annualize={annualize}: every component scaled by the same factor"])
    result.meta.setdefault("shift_share", {}).update(meta_out)
    return result


# ---------------------------------------------------------------------------
# sector_contributions
# ---------------------------------------------------------------------------
_FREQ_TEXT = {"A": "calendar years ('2011')", "FY": "fiscal years ('FY2011/12')"}
_FY_HINT = "convert first with harmonize.fiscal_to_calendar() or harmonize.calendar_to_fiscal()"


def _year_index(periods: Iterable[str]) -> dict[str, int]:
    out = {}
    for p in periods:
        pp = parse_period(p)
        if pp.freq not in ("A", "FY"):
            raise FrequencyMismatchError(f"sector_contributions expects annual or fiscal-year periods, got {p!r}")
        out[p] = pp.ordinal
    return out


def _annual_frequency(periods: Iterable[str], name: str) -> str | None:
    """The single frequency (``"A"`` or ``"FY"``) of one input's periods; mixing them raises.

    Calendar year ``2011`` and fiscal year ``FY2011/12`` share the ordinal
    2011, so they must never be aligned silently.
    """
    freqs = {parse_period(p).freq for p in pd.unique(pd.Series(list(periods), dtype=object))}
    bad = sorted(freqs - {"A", "FY"})
    if bad:
        raise FrequencyMismatchError(f"sector_contributions: {name} has {bad} periods; it expects annual or "
                                     "fiscal-year periods")
    if len(freqs) > 1:
        raise FrequencyMismatchError(f"sector_contributions: {name} mixes calendar-year ('2011') and fiscal-year "
                                     f"('FY2011/12') periods; {_FY_HINT}")
    return freqs.pop() if freqs else None


def sector_contributions(
    va_by_sector: Frame,
    periods: Mapping[str, PeriodSpec],
    *,
    sectors: SectorMap | None = None,
    total: Frame | str | None = None,
    method: Literal["avg_growth_x_avg_share", "exact"] = "avg_growth_x_avg_share",
    share_timing: Literal["current", "lagged"] = "current",
    share_frame: Frame | None = None,
    share_sectors: SectorMap | None = None,
    tolerance_pp: float = 0.05,
    iso3: Sequence[str] | None = None,
) -> Result:
    """Sector contributions to real value-added growth over named windows (Kenya Fig 2).

    ``periods`` maps labels to inclusive windows of *growth years*, e.g.
    ``{"2001-10": ("2001", "2010"), "2020": "2020"}``; growth in year ``t``
    is ``100 * (X_t / X_{t-1} - 1)``, so the level of the year before the
    window is needed.

    * ``method="avg_growth_x_avg_share"`` (the Kenya Fig 2 note): contribution
      of sector ``i`` = mean annual growth of ``i`` x mean share of ``i`` in
      the total (shares of the window years, or of the years before them with
      ``share_timing="lagged"``). Not additive in general.
    * ``method="exact"``: mean over the window of ``s_{i,t-1} * g_{i,t}``;
      sums exactly to mean total growth when the total is the sum of sectors.

    ``total``: ``None`` (sum of the sectors), the label of one of the sectors
    that is the aggregate (it is then excluded from the contributing sectors),
    or a separate frame (e.g. GDP at constant prices). Shares use
    ``share_frame`` (e.g. current-price value added, mapped with
    ``share_sectors``) when given, else the same values as the growth rates.

    Output rows per country and window: one per sector (``component
    "contribution"``), ``TOTAL`` (``"total_growth"``, mean annual growth of the
    total) and ``RESIDUAL`` (``"residual"`` = total growth minus the sum of
    contributions). Extra columns: ``sector``, ``component``, ``window``,
    ``start``, ``end``, ``avg_growth``, ``avg_share`` (percent), ``n_years``.
    When ``|residual| > tolerance_pp`` a ``contributions_do_not_sum`` caveat
    lists the windows (the Kenya Fig 2 2020 stacks net to ~+1.2 pp against a
    printed 0.5%).

    Frequencies: ``va_by_sector``, ``total``, ``share_frame`` and the windows
    must all be calendar years or all fiscal years; anything else raises
    :class:`~cgjrkit.methods.harmonize.FrequencyMismatchError` (convert with
    :func:`~cgjrkit.methods.harmonize.fiscal_to_calendar` or
    :func:`~cgjrkit.methods.harmonize.calendar_to_fiscal`).
    """
    if method not in ("avg_growth_x_avg_share", "exact"):
        raise ValueError("method must be 'avg_growth_x_avg_share' or 'exact'")
    if share_timing not in ("current", "lagged"):
        raise ValueError("share_timing must be 'current' or 'lagged'")
    if not periods:
        raise ValueError("periods must name at least one window")
    windows = {str(lab): _window(spec) for lab, spec in periods.items()}
    res, panel, order = sector_panel(va_by_sector, sectors, name="va_by_sector")
    inputs = [res]
    total_label: str | None = None
    total_series: pd.DataFrame | None = None
    if isinstance(total, str):
        if total not in order:
            raise StructuralChangeError(f"total={total!r} is not one of the sectors {order}")
        total_label = total
    elif total is not None:
        r, total_series = _single_series(total, "total")
        inputs.append(r)
    share_panel = None
    if share_frame is not None:
        r, share_panel, _ = sector_panel(share_frame, share_sectors, name="share_frame")
        inputs.append(r)
    # Calendar 2011 and FY2011/12 both map to 2011 below: refuse to align different frequencies.
    freqs = {"va_by_sector": _annual_frequency(panel["period"], "va_by_sector")}
    if total_series is not None:
        freqs["total"] = _annual_frequency(total_series.index.get_level_values("period"), "total")
    if share_panel is not None:
        freqs["share_frame"] = _annual_frequency(share_panel["period"], "share_frame")
    present = {k: v for k, v in freqs.items() if v}
    if len(set(present.values())) > 1:
        raise FrequencyMismatchError(
            "sector_contributions: inputs mix frequencies ("
            + ", ".join(f"{k}: {_FREQ_TEXT[v]}" for k, v in present.items()) + f"); {_FY_HINT}")
    data_freq = present.get("va_by_sector")
    for label, (lo, _hi) in windows.items():
        if data_freq and lo.freq != data_freq:
            raise FrequencyMismatchError(
                f"sector_contributions: window {label!r} is in {_FREQ_TEXT.get(lo.freq, lo.freq)} but the data are in "
                f"{_FREQ_TEXT[data_freq]}; give windows in the data's frequency or {_FY_HINT}")
    contributing = [s for s in order if s != total_label]

    countries = sorted(set(panel["iso3"]))
    if iso3 is not None:
        countries = sorted({str(c).upper() for c in iso3})
        absent = sorted(set(countries) - set(panel["iso3"]))
        if absent:
            raise StructuralChangeError(f"no sector data for {absent}")
    caveats: list[Caveat] = _unit_caveats(panel, "va_by_sector") + _modelled_caveat([res], [panel])
    statuses: list[str] = []
    incomplete: list[dict] = []
    flagged: list[dict] = []
    records: list[dict] = []
    for c in countries:
        sub = panel[panel["iso3"] == c]
        wide = sub.pivot(index="period", columns="sector", values="value")
        idx = _year_index(wide.index)
        wide.index = [idx[p] for p in wide.index]
        wide = wide.sort_index()
        X = wide.reindex(columns=contributing)
        if total_label is not None:
            T = wide[total_label]
        elif total_series is not None:
            if c not in total_series.index.get_level_values(0):
                raise StructuralChangeError(f"total has no data for {c}")
            ts = total_series.loc[c, "value"]
            tidx = _year_index(ts.index)
            T = pd.Series(ts.to_numpy(dtype=float), index=[tidx[p] for p in ts.index]).sort_index()
        else:
            T = X.sum(axis=1, min_count=len(contributing)).where(X.notna().all(axis=1))
        if share_panel is not None:
            ssub = share_panel[share_panel["iso3"] == c].pivot(index="period", columns="sector", values="value")
            sidx = _year_index(ssub.index)
            ssub.index = [sidx[p] for p in ssub.index]
            ssub = ssub.sort_index()
            S_num = ssub.reindex(columns=contributing)
            S_den = ssub[total_label] if total_label is not None and total_label in ssub else \
                S_num.sum(axis=1, min_count=len(contributing)).where(S_num.notna().all(axis=1))
        else:
            S_num, S_den = X, T
        shares = S_num.div(S_den, axis=0) * 100.0
        years_all = sorted(set(X.index) | set(T.index))
        full = pd.RangeIndex(min(years_all), max(years_all) + 1) if years_all else pd.RangeIndex(0)
        Xf, Tf, Sf = X.reindex(full), T.reindex(full), shares.reindex(full)
        g = (Xf / Xf.shift(1) - 1.0) * 100.0
        gT = (Tf / Tf.shift(1) - 1.0) * 100.0
        country = next((x for x in sub["country"] if x), "")
        for label, (lo, hi) in windows.items():
            yrs = list(range(lo.ordinal, hi.ordinal + 1))
            ok = [t for t in yrs if t in g.index and np.isfinite(g.loc[t].to_numpy(dtype=float)).all()
                  and t in gT.index and np.isfinite(gT.loc[t])]
            if len(ok) < len(yrs):
                incomplete.append({"iso3": c, "window": label, "years_used": len(ok), "years": len(yrs)})
            if not ok:
                continue
            G = float(gT.loc[ok].mean())
            avg_g = g.loc[ok].mean()
            share_years = ok if share_timing == "current" else [t - 1 for t in ok]
            avg_s = Sf.reindex(share_years).mean()
            if method == "avg_growth_x_avg_share":
                contrib = avg_g * avg_s / 100.0
            else:
                lag = Sf.shift(1).loc[ok]
                contrib = (lag * g.loc[ok]).mean() / 100.0
            if contrib.isna().any():
                incomplete.append({"iso3": c, "window": label, "reason": "shares missing",
                                   "sectors": sorted(contrib.index[contrib.isna()])})
                continue
            csum = float(contrib.sum())
            resid = G - csum
            if abs(resid) > tolerance_pp:
                flagged.append({"iso3": c, "window": label, "total_growth": G, "sum_of_contributions": csum,
                                "gap_pp": resid})
            used_years = set(ok) | {t - 1 for t in ok}
            st = list(sub.loc[sub["period"].map(lambda p: idx.get(p) in used_years), "obs_status"])
            statuses += st
            status = combine_status(st)
            base = {"iso3": c, "country": country, "period": label, "window": label, "start": lo.text,
                    "end": hi.text, "n_years": len(ok), "obs_status": status}
            for s in contributing:
                records.append({**base, "sector": s, "component": "contribution", "value": float(contrib[s]),
                                "avg_growth": float(avg_g[s]), "avg_share": float(avg_s[s])})
            records.append({**base, "sector": TOTAL, "component": "total_growth", "value": G,
                            "avg_growth": G, "avg_share": 100.0})
            records.append({**base, "sector": "RESIDUAL", "component": "residual", "value": resid,
                            "avg_growth": float("nan"), "avg_share": float("nan")})

    if incomplete:
        caveats.append(Caveat("contribution_window_incomplete", "warn",
                              f"{len(incomplete)} country-windows lack growth or share data for some years; "
                              "averages use the years available.", {"windows": _limited(incomplete)}))
    if flagged:
        caveats.append(Caveat(
            "contributions_do_not_sum", "warn",
            f"Sector contributions differ from total growth by more than {tolerance_pp} pp in {len(flagged)} "
            "country-window(s). 'Average growth x average share' is not additive, and sectors may not add up to the "
            "total (taxes on products, FISIM, chain-linking); show the residual or use method='exact'.",
            {"windows": _limited(flagged), "n": len(flagged), "tolerance_pp": tolerance_pp}))
    caveats += _endpoint_caveats([(lo.text, hi.text) for lo, hi in windows.values()], statuses, "sector_contributions")

    cols = ["iso3", "country", "period", "sector", "component", "value", "obs_status", "window", "start", "end",
            "avg_growth", "avg_share", "n_years"]
    out = pd.DataFrame(records, columns=cols)
    out["source"] = "cgjrkit"
    out["dataset"] = "structural_change"
    out["indicator"] = "sector_contribution|" + out["sector"]
    out["indicator_label"] = [
        "Total growth" if s == TOTAL else ("Residual (total minus sum of contributions)" if s == "RESIDUAL"
                                           else f"Contribution to growth: {s}") for s in out["sector"]]
    out["unit"] = ["% per year" if comp == "total_growth" else "percentage points" for comp in out["component"]]
    out = out[COLUMNS + ["sector", "component", "window", "start", "end", "avg_growth", "avg_share", "n_years"]]
    for col in ("value", "avg_growth", "avg_share"):
        out[col] = out[col].astype("float64")
    out["n_years"] = out["n_years"].astype("int64")
    params = {"periods": {k: [lo.text, hi.text] for k, (lo, hi) in windows.items()}, "method": method,
              "share_timing": share_timing, "sectors": dict(sectors) if sectors else None,
              "total": total if isinstance(total, str) else ("frame" if total is not None else "sum of sectors"),
              "share_frame": share_frame is not None, "tolerance_pp": tolerance_pp, "iso3": countries}
    note = ("contribution_i = mean_t(g_i,t) * mean_t(s_i,t) / 100" if method == "avg_growth_x_avg_share"
            else "contribution_i = mean_t(s_i,t-1 * g_i,t) / 100 (additive)")
    return _done(inputs, out.reset_index(drop=True), "sector_contributions", params, caveats,
                 [note, f"shares: {share_timing}; growth g_t = 100*(X_t/X_t-1 - 1)"])


# ---------------------------------------------------------------------------
# beta_convergence
# ---------------------------------------------------------------------------
def _ols(y: np.ndarray, X: np.ndarray, names: list[str], robust: bool) -> dict[str, Any]:
    from scipy import stats

    n, k = X.shape
    if n <= k:
        raise StructuralChangeError(f"regression needs more observations ({n}) than parameters ({k})")
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    fitted = X @ coef
    resid = y - fitted
    rss = float(resid @ resid)
    tss = float(((y - y.mean()) ** 2).sum())
    xtx_inv = np.linalg.pinv(X.T @ X)
    dof = n - k
    if robust:
        meat = X.T @ (X * (resid ** 2)[:, None])
        cov = xtx_inv @ meat @ xtx_inv * (n / dof)
    else:
        cov = xtx_inv * (rss / dof)
    se = np.sqrt(np.clip(np.diag(cov), 0.0, None))
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(se > 0, coef / se, np.inf * np.sign(coef))
    p = 2.0 * stats.t.sf(np.abs(t), dof)
    def as_dict(arr: np.ndarray) -> dict[str, float]:
        return {name: float(v) for name, v in zip(names, arr)}

    return {
        "coef": as_dict(coef), "se": as_dict(se), "t": as_dict(t), "p": as_dict(p),
        "n": int(n), "k": int(k), "dof": int(dof), "r2": float(1.0 - rss / tss) if tss > 0 else float("nan"),
        "rss": rss, "se_type": "HC1" if robust else "classical", "fitted": fitted, "resid": resid,
    }


def beta_convergence(
    panel: Frame,
    start: str,
    end: str,
    *,
    indicator: str | None = None,
    iso3: Sequence[str] | None = None,
    growth: Literal["log", "cagr"] = "log",
    spell: int | None = None,
    period_effects: bool = True,
    controls: pd.DataFrame | None = None,
    robust: bool = False,
) -> Result:
    """Beta-convergence regression ``g_i = alpha + beta * ln(y_i0) [+ controls] + e_i``.

    ``panel`` holds one level series per unit (GDP per capita, value added
    per worker, regional GDP per capita; units may be regions). ``growth`` is
    the average annual growth in percent: ``"log"`` = ``100 * ln(y_T / y_0) /
    T`` (Barro and Sala-i-Martin) or ``"cagr"``. ``beta < 0`` means
    convergence.

    * Cross-section (default): one observation per unit between ``start`` and
      ``end``; ``controls`` (DataFrame indexed by iso3, numeric columns) give
      conditional convergence.
    * Pooled spells (``spell=k``): non-overlapping ``k``-year spells from
      ``start`` up to ``end``; with ``period_effects`` the regression includes
      spell dummies.

    Implied speed of convergence (log growth): ``lambda = -ln(1 + b*T) / T``
    with ``b = beta / 100`` and ``T`` the window (or spell) length;
    half-life ``ln(2) / lambda``. Both are ``None`` when ``beta >= 0``.

    Output data: one row per unit-observation (indicator
    ``"beta_convergence.growth"``, value = growth in % per year) with extra
    columns ``initial``, ``final``, ``log_initial``, ``fitted``, ``residual``,
    ``spell_start``, ``spell_end``; the regression is in
    ``meta["beta_convergence"]``.
    """
    if growth not in ("log", "cagr"):
        raise ValueError("growth must be 'log' or 'cagr'")
    res, series = _single_series(panel, "panel", indicator)
    start, end = str(start).strip(), str(end).strip()
    total_years = _years_between(start, end)
    a, b = parse_period(start), parse_period(end)
    steps = a.steps_per_year
    if spell is None:
        spells = [(start, end)]
    else:
        if spell <= 0:
            raise ValueError("spell must be a positive number of years")
        spells = []
        o = a.ordinal
        while o + spell * steps <= b.ordinal:
            spells.append((format_period(a.freq, o), format_period(a.freq, o + spell * steps)))
            o += spell * steps
        if not spells:
            raise ValueError(f"no complete {spell}-year spell between {start} and {end}")
    units = sorted(set(series.index.get_level_values(0)))
    if iso3 is not None:
        units = sorted({str(c).upper() for c in iso3} & set(units))
    obs: list[dict] = []
    skipped: list[str] = []
    for u in units:
        for lo, hi in spells:
            try:
                y0 = float(series.loc[(u, lo), "value"])
                yT = float(series.loc[(u, hi), "value"])
            except KeyError:
                skipped.append(f"{u}@{lo}-{hi} (missing)")
                continue
            if not (np.isfinite(y0) and np.isfinite(yT)) or y0 <= 0 or yT <= 0:
                skipped.append(f"{u}@{lo}-{hi} (non-positive or missing)")
                continue
            T = _years_between(lo, hi)
            g = 100.0 * math.log(yT / y0) / T if growth == "log" else 100.0 * ((yT / y0) ** (1.0 / T) - 1.0)
            obs.append({"iso3": u, "spell_start": lo, "spell_end": hi, "initial": y0, "final": yT,
                        "log_initial": math.log(y0), "growth": g, "years": T,
                        "country": str(series.loc[(u, lo), "country"]),
                        "obs_status": combine_status([series.loc[(u, lo), "obs_status"], series.loc[(u, hi), "obs_status"]])})
    if not obs:
        raise StructuralChangeError("no unit has positive levels at both ends of any spell")
    df = pd.DataFrame(obs)
    names = ["const", "log_initial"]
    cols = [np.ones(len(df)), df["log_initial"].to_numpy()]
    if controls is not None:
        if spell is not None:
            raise ValueError("controls are supported for the cross-section only")
        ctrl = controls.copy()
        if "iso3" in ctrl.columns:
            ctrl = ctrl.set_index("iso3")
        ctrl.index = ctrl.index.astype(str).str.upper()
        missing = sorted(set(df["iso3"]) - set(ctrl.index))
        if missing:
            raise StructuralChangeError(f"controls missing for {missing}")
        for col in ctrl.columns:
            names.append(str(col))
            cols.append(pd.to_numeric(ctrl.loc[df["iso3"], col], errors="raise").to_numpy(dtype=float))
    if spell is not None and period_effects and len(spells) > 1:
        for lo, _hi in spells[1:]:
            names.append(f"spell_{lo}")
            cols.append((df["spell_start"] == lo).to_numpy(dtype=float))
    X = np.column_stack(cols)
    reg = _ols(df["growth"].to_numpy(dtype=float), X, names, robust)
    beta = float(reg["coef"]["log_initial"])
    T_len = float(spell) if spell is not None else total_years
    lam = half = None
    if growth == "log" and beta < 0 and 1.0 + (beta / 100.0) * T_len > 0:
        lam = -math.log(1.0 + (beta / 100.0) * T_len) / T_len
        half = math.log(2.0) / lam
    df["fitted"] = reg.pop("fitted")
    df["residual"] = reg.pop("resid")

    label = f"{start}-{end}"
    out = pd.DataFrame({
        "source": "cgjrkit", "dataset": "structural_change", "indicator": "beta_convergence.growth",
        "indicator_label": f"Average annual growth ({growth}), {label}", "iso3": df["iso3"],
        "country": df["country"], "period": df["spell_start"] + "-" + df["spell_end"],
        "value": df["growth"].astype("float64"), "unit": "% per year", "obs_status": df["obs_status"],
        "initial": df["initial"], "final": df["final"], "log_initial": df["log_initial"], "fitted": df["fitted"],
        "residual": df["residual"], "spell_start": df["spell_start"], "spell_end": df["spell_end"],
    })
    caveats: list[Caveat] = []
    if skipped:
        caveats.append(Caveat("convergence_units_skipped", "info", f"{len(skipped)} unit-spells lack usable levels.",
                              {"at": _limited(skipped), "n": len(skipped)}))
    if reg["n"] < 10:
        caveats.append(Caveat("convergence_small_sample", "warn",
                              f"Only {reg['n']} observations; beta is imprecisely estimated.", {"n": reg["n"]}))
    caveats += _endpoint_caveats(spells, list(df["obs_status"]), "beta_convergence")
    params = {"start": start, "end": end, "indicator": indicator, "growth": growth, "spell": spell,
              "period_effects": period_effects, "controls": list(controls.columns) if controls is not None else None,
              "robust": robust, "iso3": list(iso3) if iso3 is not None else None}
    result = _done([res], out, "beta_convergence", params, caveats,
                   ["OLS: growth (% per year) on ln(initial level); lambda = -ln(1 + beta/100*T)/T"])
    result.meta["beta_convergence"] = {**reg, "beta": beta, "speed_lambda": lam, "half_life_years": half,
                                       "spells": [list(s) for s in spells], "years": T_len, "growth": growth,
                                       "converging": bool(beta < 0)}
    return result
