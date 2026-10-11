"""Shapley decomposition of growth in GDP per capita (jobs and growth).

GDP per capita is the product of three factors::

    GDP / P  =  (GDP / E)  x  (E / WAP)  x  (WAP / P)
    y        =  a           x  b          x  c
                output per    employment    working-age
                worker        rate          share

with ``E`` employment, ``WAP`` working-age population and ``P`` total
population. Between a start (``0``) and an end (``T``) period the change
``y_T - y_0`` is split exactly into one contribution per factor with the
Shapley value: each factor's contribution is its marginal effect averaged
over all ``3! = 6`` orders in which the factors can be switched from their
start to their end values. For three factors::

    phi_a = (a_T - a_0) * [ b_0 c_0 / 3 + (b_T c_0 + b_0 c_T) / 6 + b_T c_T / 3 ]

(and symmetrically for ``b`` and ``c``), so ``phi_a + phi_b + phi_c = y_T -
y_0`` exactly. :func:`shapley_multipliers` computes the bracket (the
"multiplier" of each factor) for any number of factors by enumerating the
orderings.

Sector splits (optional, from value added and employment by sector):

* output per worker: ``a = sum(theta_i * y_i)``; the shift-share terms of
  :func:`cgjrkit.methods.structural_change.decompose_productivity` (within and
  between, default Shapley mid-point weights) add up to the change in
  sector-based productivity, and each term ``x`` contributes ``m_a * x``. The
  difference between ``a`` (GDP / total employment) and the sector-based
  productivity (taxes on products, FISIM, sector coverage) is reported as an
  ``output_per_worker.residual`` row, so the sector rows always add up to
  the output-per-worker contribution.
* employment rate: ``b = sum(E_i / WAP)``; sector ``i`` contributes
  ``m_b * (E_iT / WAP_T - E_i0 / WAP_0)``, with an ``employment_rate.residual``
  row when sector employment does not sum to total employment.

Contributions are reported as percentage points (``annualize``, as in
:func:`cgjrkit.methods.structural_change.annualize_factor`: by default they
sum to the CAGR of GDP per capita), as percent of initial GDP per capita
(``pct_cumulative``) and as percent of the total change (``share_of_total``).
``method="log"`` instead reports each factor's own log change (``100 *
ln(x_T / x_0)``, per year unless ``annualize="none"``), which is also
additive but is not the Shapley split of the level change.

The World Bank Jobs Group's Job Structure Tool (JD JobStructure Tool, June
2023, World Bank data catalogue 0064624) describes a split of per capita
output growth into productivity, employment, labour-force and working-age
components plus sector contributions. This module is an independent
implementation of the three-factor identity above; agreement with that
tool's outputs is UNVERIFIED (its documentation could not be fetched from
this environment).
"""

from __future__ import annotations

import math
from itertools import permutations
from typing import Any, Literal, Mapping, Sequence

import numpy as np
import pandas as pd

from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Result
from cgjrkit.core.schema import COLUMNS
from cgjrkit.methods.harmonize import PeriodSpec, _limited, combine_status, format_period, parse_period
from cgjrkit.methods.structural_change import (
    TOTAL,
    Annualize,
    SectorMap,
    StructuralChangeError,
    Variant,
    _COMPONENTS,
    _ANNUALIZE_UNITS,
    _done,
    _endpoint_caveats,
    _modelled_caveat,
    _ordered_unique,
    _sector_values,
    _single_series,
    _years_between,
    annualize_factor,
    decompose_productivity,
    sector_panel,
)

__all__ = [
    "JobsShapleyError",
    "shapley_multipliers",
    "shapley_product",
    "jobs_shapley",
    "FACTORS",
]

Frame = Result | pd.DataFrame

#: Factor keys and labels, in identity order.
FACTORS: dict[str, str] = {
    "output_per_worker": "Output per worker (GDP / employment)",
    "employment_rate": "Employment rate (employment / working-age population)",
    "working_age_share": "Working-age share (working-age / total population)",
}

_MAX_FACTORS = 8
_IDENTITY_RTOL = 1e-9


class JobsShapleyError(StructuralChangeError):
    """Inputs cannot be decomposed (missing values, non-positive levels, ...)."""


# ---------------------------------------------------------------------------
# Shapley arithmetic
# ---------------------------------------------------------------------------
def shapley_multipliers(x0: Sequence[float], xT: Sequence[float]) -> list[float]:
    """Shapley multipliers for the product ``prod(x)`` moving from ``x0`` to ``xT``.

    ``m_k`` is the average over all orderings of the factors of the product
    of the *other* factors, each at its end value if it was switched before
    ``k`` in that ordering and at its start value otherwise. The Shapley
    contribution of factor ``k`` is ``m_k * (xT_k - x0_k)``; the
    contributions sum exactly to ``prod(xT) - prod(x0)``.
    """
    n = len(x0)
    if n != len(xT) or n == 0:
        raise ValueError("x0 and xT must be non-empty and of equal length")
    if n > _MAX_FACTORS:
        raise ValueError(f"at most {_MAX_FACTORS} factors (n! orderings)")
    totals = [0.0] * n
    count = 0
    for order in permutations(range(n)):
        count += 1
        switched: set[int] = set()
        for k in order:
            prod = 1.0
            for j in range(n):
                if j != k:
                    prod *= xT[j] if j in switched else x0[j]
            totals[k] += prod
            switched.add(k)
    return [t / count for t in totals]


def shapley_product(x0: Sequence[float], xT: Sequence[float]) -> list[float]:
    """Shapley contributions ``m_k * (xT_k - x0_k)`` to ``prod(xT) - prod(x0)``."""
    m = shapley_multipliers(x0, xT)
    return [mk * (b - a) for mk, a, b in zip(m, x0, xT)]


# ---------------------------------------------------------------------------
# jobs_shapley
# ---------------------------------------------------------------------------
def _endpoints(spec: PeriodSpec) -> tuple[str, str]:
    """``(start, end)`` level endpoints; a single period ``p`` means ``(p - 1, p)``."""
    if isinstance(spec, (tuple, list)):
        if len(spec) != 2:
            raise ValueError(f"a window must be (start, end), got {spec!r}")
        return str(spec[0]).strip(), str(spec[1]).strip()
    p = parse_period(str(spec).strip())
    return format_period(p.freq, p.ordinal - p.steps_per_year), p.text


def _value(series: pd.DataFrame, iso3: str, period: str, name: str, scale: float) -> tuple[float, str]:
    try:
        row = series.loc[(iso3, period)]
    except KeyError as exc:
        raise JobsShapleyError(f"{name}: no value for {iso3} in {period}") from exc
    v = float(row["value"]) * scale
    if not np.isfinite(v) or v <= 0:
        raise JobsShapleyError(f"{name}: value for {iso3} in {period} is missing or non-positive ({v})")
    return v, str(row["obs_status"])


def jobs_shapley(
    gdp: Frame,
    population: Frame,
    working_age: Frame,
    employment: Frame,
    periods: Mapping[str, PeriodSpec],
    *,
    va_by_sector: Frame | None = None,
    emp_by_sector: Frame | None = None,
    va_sectors: SectorMap | None = None,
    emp_sectors: SectorMap | None = None,
    sector_variant: Variant = "shapley",
    method: Literal["shapley", "log"] = "shapley",
    annualize: Annualize = "cagr",
    population_scale: float = 1.0,
    working_age_scale: float = 1.0,
    employment_scale: float = 1.0,
    sector_emp_scale: float | None = None,
    indicators: Mapping[str, str] | None = None,
    iso3: Sequence[str] | None = None,
) -> Result:
    """Decompose growth in GDP per capita into output per worker, employment rate and working-age share.

    Inputs are level series, one per country: ``gdp`` (constant prices),
    ``population``, ``working_age`` (e.g. ages 15-64) and ``employment``.
    The ``*_scale`` factors bring them to common units (ILOSTAT employment
    and WPP population are in thousands: ``employment_scale=1000``,
    ``population_scale=1000``, ...); a ratio above one (employment rate or
    working-age share) raises a ``jobs_ratio_above_one`` caveat. When a frame
    holds several indicators pick one with ``indicators={"gdp": code, ...}``.

    ``periods`` maps labels to ``(start, end)`` level endpoints (``"2020"``
    alone means ``("2019", "2020")``). Optional sector inputs
    (``va_by_sector`` + ``emp_by_sector`` with their sector mappings) split
    the output-per-worker contribution into within/between terms by sector
    (``sector_variant``) and the employment-rate contribution by sector.

    Output rows per country and window (indicator
    ``"jobs_shapley.<component>|<sector>"``):

    * ``gdp_per_capita|TOTAL`` (the total) and one row per factor
      (``output_per_worker``, ``employment_rate``, ``working_age_share``);
    * with sector inputs: ``output_per_worker.<term>|<sector>``,
      ``output_per_worker.residual|TOTAL``, ``employment_rate.sector|<sector>``
      and ``employment_rate.residual|TOTAL``.

    ``value`` is in percentage points per ``annualize``; extra columns:
    ``sector``, ``component``, ``window``, ``start``, ``end``,
    ``level_change``, ``pct_cumulative``, ``share_of_total``,
    ``factor_start``, ``factor_end``, ``method``. The factor rows sum
    exactly to the total (asserted), and sector rows sum exactly to their
    factor row.
    """
    if method not in ("shapley", "log"):
        raise ValueError("method must be 'shapley' or 'log'")
    if annualize not in _ANNUALIZE_UNITS:
        raise ValueError(f"annualize must be one of {sorted(_ANNUALIZE_UNITS)}")
    if sector_variant not in _COMPONENTS:
        raise ValueError(f"sector_variant must be one of {sorted(_COMPONENTS)}")
    if not periods:
        raise ValueError("periods must name at least one window")
    if (va_by_sector is None) != (emp_by_sector is None):
        raise ValueError("pass both va_by_sector and emp_by_sector, or neither")
    windows = {str(lab): _endpoints(spec) for lab, spec in periods.items()}
    for lo, hi in windows.values():
        _years_between(lo, hi)
    ind = dict(indicators or {})
    unknown = sorted(set(ind) - {"gdp", "population", "working_age", "employment"})
    if unknown:
        raise ValueError(f"unknown keys in indicators: {unknown}")
    r_gdp, s_gdp = _single_series(gdp, "gdp", ind.get("gdp"))
    r_pop, s_pop = _single_series(population, "population", ind.get("population"))
    r_wap, s_wap = _single_series(working_age, "working_age", ind.get("working_age"))
    r_emp, s_emp = _single_series(employment, "employment", ind.get("employment"))
    inputs = [r_gdp, r_pop, r_wap, r_emp]
    sector_mode = va_by_sector is not None
    va = emp_s = None
    order: list[str] = []
    sec_scale = employment_scale if sector_emp_scale is None else sector_emp_scale
    if sector_mode:
        r_va, va, va_order = sector_panel(va_by_sector, va_sectors, name="va_by_sector")
        r_es, emp_s, es_order = sector_panel(emp_by_sector, emp_sectors, name="emp_by_sector")
        emp_s = emp_s.assign(value=emp_s["value"] * sec_scale)
        inputs += [r_va, r_es]
        order = _ordered_unique(list(es_order) + list(va_order))
    rank = {s: i for i, s in enumerate(order)}

    countries = sorted(set.intersection(*(set(s.index.get_level_values(0)) for s in (s_gdp, s_pop, s_wap, s_emp))))
    if iso3 is not None:
        wanted = sorted({str(c).upper() for c in iso3})
        absent = sorted(set(wanted) - set(countries))
        if absent:
            raise JobsShapleyError(f"no complete inputs for {absent}")
        countries = wanted
    if not countries:
        raise JobsShapleyError("no country appears in all four inputs")

    caveats: list[Caveat] = _modelled_caveat(inputs, [r.data for r in inputs])
    above_one: list[dict] = []
    residuals: list[dict] = []
    log_undefined: list[dict] = []
    statuses: list[str] = []
    records: list[dict] = []
    meta_out: dict[str, Any] = {}
    for c in countries:
        country = next((str(x) for x in s_gdp.loc[c, "country"] if x), "") if c in s_gdp.index else ""
        for label, (lo, hi) in windows.items():
            years = _years_between(lo, hi)
            vals: dict[str, dict[str, float]] = {}
            st: list[str] = []
            for key, series, scale in (("gdp", s_gdp, 1.0), ("population", s_pop, population_scale),
                                       ("working_age", s_wap, working_age_scale),
                                       ("employment", s_emp, employment_scale)):
                v0, st0 = _value(series, c, lo, key, scale)
                vT, stT = _value(series, c, hi, key, scale)
                vals[key] = {"start": v0, "end": vT}
                st += [st0, stT]
            statuses += st
            status = combine_status(st)
            f0 = [vals["gdp"]["start"] / vals["employment"]["start"],
                  vals["employment"]["start"] / vals["working_age"]["start"],
                  vals["working_age"]["start"] / vals["population"]["start"]]
            fT = [vals["gdp"]["end"] / vals["employment"]["end"],
                  vals["employment"]["end"] / vals["working_age"]["end"],
                  vals["working_age"]["end"] / vals["population"]["end"]]
            for name, a0, aT in (("employment_rate", f0[1], fT[1]), ("working_age_share", f0[2], fT[2])):
                if a0 > 1 or aT > 1:
                    above_one.append({"iso3": c, "window": label, "factor": name, "start": a0, "end": aT})
            y0 = vals["gdp"]["start"] / vals["population"]["start"]
            yT = vals["gdp"]["end"] / vals["population"]["end"]
            dy = yT - y0
            m = shapley_multipliers(f0, fT)
            phi = [mk * (b - a) for mk, a, b in zip(m, f0, fT)]
            if abs(sum(phi) - dy) > _IDENTITY_RTOL * max(abs(y0), abs(yT)):
                raise JobsShapleyError(f"internal identity failed for {c} {label}")  # pragma: no cover
            k = annualize_factor(y0, yT, years, annualize) if method == "shapley" else float("nan")
            logs = [math.log(b / a) for a, b in zip(f0, fT)]
            log_total = math.log(yT / y0)
            log_scale = 100.0 / years if annualize != "none" else 100.0

            def contrib(level: float) -> tuple[float, float]:
                """(value, share_of_total) of a level contribution (method="shapley")."""
                return level * k, (100.0 * level / dy) if dy != 0 else float("nan")

            base = {"iso3": c, "country": country, "period": label, "window": label, "start": lo, "end": hi,
                    "obs_status": status}

            def add(component: str, sector: str, level: float, value: float, share: float,
                    fstart: float = float("nan"), fend: float = float("nan")) -> None:
                records.append({**base, "component": component, "sector": sector, "value": value,
                                "level_change": level if method == "shapley" else float("nan"),
                                "pct_cumulative": 100.0 * level / y0 if method == "shapley" else float("nan"),
                                "share_of_total": share, "factor_start": fstart, "factor_end": fend})

            # totals and factor rows
            if method == "shapley":
                add("gdp_per_capita", TOTAL, dy, dy * k, 100.0 if dy != 0 else float("nan"), y0, yT)
            else:
                add("gdp_per_capita", TOTAL, dy, log_total * log_scale, 100.0 if log_total != 0 else float("nan"),
                    y0, yT)
            factor_values: dict[str, float] = {}
            for (key, _lab), p_k, lg, a, b in zip(FACTORS.items(), phi, logs, f0, fT):
                if method == "shapley":
                    v, sh = contrib(p_k)
                else:
                    v = lg * log_scale
                    sh = (100.0 * lg / log_total) if log_total != 0 else float("nan")
                factor_values[key] = v
                add(key, TOTAL, p_k, v, sh, a, b)

            sector_meta: dict[str, Any] = {}
            if sector_mode:
                va0, vaT = _sector_values(va, c, lo), _sector_values(va, c, hi)
                e0, eT = _sector_values(emp_s, c, lo), _sector_values(emp_s, c, hi)
                try:
                    d = decompose_productivity(va0, vaT, e0, eT, sector_variant)
                except StructuralChangeError as exc:
                    raise JobsShapleyError(f"{c} {label}: {exc}") from exc
                sectors = sorted(d["sectors"], key=lambda s: rank.get(s, len(rank)))
                da = fT[0] - f0[0]

                def split(level_piece: float, factor: str, factor_delta: float, mult: float) -> tuple[float, float, float]:
                    """Contribution (level, value, share) of a piece of a factor's change."""
                    lev = mult * level_piece
                    if method == "shapley":
                        v, sh = contrib(lev)
                        return lev, v, sh
                    if factor_delta == 0:
                        log_undefined.append({"iso3": c, "window": label, "factor": factor})
                        return lev, float("nan"), float("nan")
                    v = factor_values[factor] * level_piece / factor_delta
                    sh = (100.0 * v / (log_total * log_scale)) if log_total != 0 else float("nan")
                    return lev, v, sh

                for comp in _COMPONENTS[sector_variant]:
                    for s in sectors:
                        piece = d["terms"][comp][s]
                        lev, v, sh = split(piece, "output_per_worker", da, m[0])
                        add(f"output_per_worker.{comp}", s, lev, v, sh)
                # aggregate minus sector-based productivity change (0 when GDP = sum(VA) and E = sum(E_i))
                resid_a = da - (d["YT"] - d["Y0"])
                if abs(resid_a) <= 1e-12 * max(abs(f0[0]), abs(fT[0])):
                    resid_a = 0.0
                lev, v, sh = split(resid_a, "output_per_worker", da, m[0])
                add("output_per_worker.residual", TOTAL, lev, v, sh)
                # employment rate by sector
                wap0, wapT = vals["working_age"]["start"], vals["working_age"]["end"]
                db = fT[1] - f0[1]
                for s in sectors:
                    piece = eT[s] / wapT - e0[s] / wap0
                    lev, v, sh = split(piece, "employment_rate", db, m[1])
                    add("employment_rate.sector", s, lev, v, sh, e0[s] / wap0, eT[s] / wapT)
                resid_b = db - (sum(eT.values()) / wapT - sum(e0.values()) / wap0)
                if abs(resid_b) <= 1e-12:
                    resid_b = 0.0
                lev, v, sh = split(resid_b, "employment_rate", db, m[1])
                add("employment_rate.residual", TOTAL, lev, v, sh)
                if resid_a != 0.0 or resid_b != 0.0:
                    residuals.append({
                        "iso3": c, "window": label,
                        "output_per_worker_residual_pct_of_change": (100.0 * resid_a / da) if da else None,
                        "employment_rate_residual_pct_of_change": (100.0 * resid_b / db) if db else None,
                        "va_coverage": [sum(va0.values()) / vals["gdp"]["start"], sum(vaT.values()) / vals["gdp"]["end"]],
                        "emp_coverage": [sum(e0.values()) / vals["employment"]["start"],
                                         sum(eT.values()) / vals["employment"]["end"]],
                    })
                sector_meta = {"variant": sector_variant, "sectors": sectors, "sector_productivity": [d["Y0"], d["YT"]],
                               "output_per_worker_residual": resid_a, "employment_rate_residual": resid_b}

            meta_out.setdefault(c, {})[label] = {
                "start": lo, "end": hi, "years": years, "method": method, "annualize": annualize,
                "gdp_per_capita": [y0, yT], "factors_start": dict(zip(FACTORS, f0)), "factors_end": dict(zip(FACTORS, fT)),
                "multipliers": dict(zip(FACTORS, m)), "contributions_level": dict(zip(FACTORS, phi)),
                "identity_abs_error": abs(sum(phi) - dy), "scale_k": k, **({"sectors": sector_meta} if sector_meta else {}),
            }

    if above_one:
        caveats.append(Caveat("jobs_ratio_above_one", "warn",
                              "An employment rate or working-age share exceeds one; check the *_scale arguments "
                              "(ILOSTAT and WPP report thousands) and the age bands.", {"at": _limited(above_one)}))
    if residuals:
        caveats.append(Caveat("jobs_sector_residual", "warn",
                              "Sector value added or employment does not add up to GDP or total employment; the gap is "
                              "reported in the *.residual rows (taxes on products, FISIM, sector coverage, units).",
                              {"at": _limited(residuals), "n": len(residuals)}))
    if log_undefined:
        caveats.append(Caveat("jobs_log_split_undefined", "info",
                              "A factor did not change, so its log contribution cannot be split by sector.",
                              {"at": _limited(log_undefined)}))
    caveats += _endpoint_caveats(list(windows.values()), statuses, "jobs_shapley")

    out = pd.DataFrame(records)
    out["source"] = "cgjrkit"
    out["dataset"] = "jobs_shapley"
    out["indicator"] = "jobs_shapley." + out["component"] + "|" + out["sector"]
    labels = {"gdp_per_capita": "GDP per capita", **FACTORS,
              "output_per_worker.residual": "Output per worker: aggregate minus sector-based (residual)",
              "employment_rate.residual": "Employment rate: total minus sum of sectors (residual)",
              "employment_rate.sector": "Employment rate, by sector"}

    def _label(comp: str, sec: str) -> str:
        text = labels[comp] if comp in labels else "Output per worker, " + comp.split(".", 1)[-1].replace("_", " ")
        return text if sec == TOTAL else f"{text}: {sec}"

    out["indicator_label"] = [_label(comp, sec) for comp, sec in zip(out["component"], out["sector"])]
    out["unit"] = (_ANNUALIZE_UNITS[annualize] if method == "shapley"
                   else ("log points x 100 per year" if annualize != "none" else "log points x 100"))
    out["method"] = method
    extra = ["sector", "component", "window", "start", "end", "level_change", "pct_cumulative", "share_of_total",
             "factor_start", "factor_end", "method"]
    out = out[COLUMNS + extra].reset_index(drop=True)
    for col in ("value", "level_change", "pct_cumulative", "share_of_total", "factor_start", "factor_end"):
        out[col] = out[col].astype("float64")
    params = {"periods": {k_: list(v) for k_, v in windows.items()}, "method": method, "annualize": annualize,
              "sector_variant": sector_variant if sector_mode else None,
              "va_sectors": dict(va_sectors) if va_sectors else None,
              "emp_sectors": dict(emp_sectors) if emp_sectors else None,
              "population_scale": population_scale, "working_age_scale": working_age_scale,
              "employment_scale": employment_scale, "sector_emp_scale": sec_scale if sector_mode else None,
              "indicators": ind or None, "iso3": countries}
    notes = ["GDP/P = (GDP/E) x (E/WAP) x (WAP/P); Shapley value over the 6 orderings of the three factors",
             f"method={method}, annualize={annualize}"]
    result = _done(inputs, out, "jobs_shapley", params, caveats, notes, module="jobs_shapley")
    result.meta.setdefault("jobs_shapley", {}).update(meta_out)
    return result
