"""Cross-country benchmarking: income-conditional fits, ranks, densities, z-scores, frontiers.

These are the standard CGJR benchmarking exhibits (archetypes G03, S01, F05,
X05, R01, N01 in ``inventory/cross-report-inventory.md``):

=========================  ====================================================
``income_conditional_fit``  y against (log) GDP per capita across countries in
                            one period: linear, quadratic, cubic or lowess
                            curve, residuals by country, the focus country's
                            predicted vs actual value and an optional focus
                            trajectory (Kenya Figs 9, 14, 22; Indonesia 2.1)
``percentile_rank``         rank and percentile of every country, focus summary
                            (Kenya Fig 15: 48th percentile in 2001, 38th in 2023)
``kernel_density``          Gaussian KDE per period on a common grid, with the
                            focus country's value, density and percentile
                            (Kenya Fig 15)
``zscore``                  standardised values across countries (optionally
                            against a reference group) and z-score composites
``distance_to_frontier``    gap to, percent of, and 0-100 score relative to the
                            best performer or a named frontier country
=========================  ====================================================

Conventions
-----------
* Inputs are canonical frames (:mod:`cgjrkit.core.schema`) or
  :class:`~cgjrkit.core.provenance.Result` objects wrapping them. Outputs are
  new ``Result`` objects that carry forward every input's provenance, caveats
  and meta and append ``Provenance(source="cgjrkit", tool="benchmark.<name>")``
  with the exact parameters.
* Aggregates never enter a cross-country statistic by default
  (``aggregates="drop"``): codes listed in an input's ``meta["aggregates"]``,
  the World Bank aggregate codes, and any code that is not three capital
  letters (for example peer aggregates ``"STRUCT:median"``) are removed and
  listed in ``meta["dropped"]``.
* ``income_conditional_fit`` and ``kernel_density`` return *chart frames*: one
  tidy table whose ``role`` column separates country points, the fitted curve
  or density grid, and the focus country. ``percentile_rank``, ``zscore`` and
  ``distance_to_frontier`` transform values, so they return canonical frames
  (indicator ``"<code>|<suffix>"``) with the original value kept in ``level``.
* Statistics needing a single number per country use one observation per
  country, indicator and period; duplicates raise an error.
* PPP bases are never mixed silently (see :mod:`cgjrkit.methods.harmonize`):
  every function compares the PPP bases of the values it puts on one scale
  (within each indicator, over the periods used) and raises ``PPPMixError``
  (``on_ppp_mix="raise"``, the default) or attaches a ``ppp_base_mix`` block
  caveat (``"block"``). Several unit labels within one indicator add a
  ``mixed_units`` warning.
* Everything is deterministic: no random numbers, sorted outputs.
"""

from __future__ import annotations

import math
import re
from typing import Any, Iterable, Literal, Mapping, Sequence

import numpy as np
import pandas as pd
from scipy import stats

from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import COLUMNS
from cgjrkit.methods.harmonize import (
    Frame,
    PPPPolicy,
    _aggregate_codes,
    _as_result,
    _is_aggregate,
    _jsonable,
    _limited,
    _merge_meta,
    _mixed_units_caveat,
    _ppp_check_groups,
    combine_status,
)

__all__ = [
    "FIT_KINDS",
    "PERCENTILE_METHODS",
    "income_conditional_fit",
    "percentile_rank",
    "kernel_density",
    "zscore",
    "distance_to_frontier",
    "percentile_of",
    "aggregate_codes",
]

FitKind = Literal["linear", "quadratic", "cubic", "lowess"]
PercentileMethod = Literal["weak", "strict", "mean", "rank"]
AggregatePolicy = Literal["drop", "keep"]

#: Polynomial degree of each parametric fit kind (``lowess`` is non-parametric).
FIT_KINDS: dict[str, int | None] = {"linear": 1, "quadratic": 2, "cubic": 3, "lowess": None}

#: Percentile definitions, passed to :func:`scipy.stats.percentileofscore` as ``kind``.
#: ``weak``: % of countries with a value <= x (empirical CDF, the default);
#: ``strict``: % with a value < x; ``mean``: average of the two; ``rank``: average
#: percentage ranking (equals ``weak`` without ties).
PERCENTILE_METHODS: tuple[str, ...] = ("weak", "strict", "mean", "rank")

_ISO3_RE = re.compile(r"^[A-Z]{3}$")
_STATUS_CAVEATS: dict[str, tuple[str, str, str]] = {
    "forecast": ("forecast_values", "warn", "Some values used are forecasts/projections, not outturns."),
    "break": ("series_break", "warn", "Some values used are flagged as a series break."),
    "modelled": ("modelled_values", "info",
                 "Some values used are model-based estimates (e.g. ILO modelled estimates), not survey data."),
}


# ---------------------------------------------------------------------------
# shared helpers (also used by cgjrkit.methods.growth_accounting)
# ---------------------------------------------------------------------------
def _clean(value: Any) -> Any:
    """JSON-safe, deterministic version of ``value`` for meta and provenance (NaN -> None)."""
    value = _jsonable(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


def _num(value: Any) -> float | None:
    """Python float, or None for missing/non-finite values."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _finish(
    inputs: Sequence[Result],
    data: pd.DataFrame,
    tool: str,
    params: Mapping[str, Any],
    *,
    module: str = "benchmark",
    caveats: Iterable[Caveat] = (),
    meta: Mapping[str, Any] | None = None,
    notes: Iterable[str] = (),
) -> Result:
    """Output Result: inputs' provenance, caveats and meta, plus one cgjrkit Provenance entry."""
    provenance: list[Provenance] = []
    for res in inputs:
        for p in res.provenance:
            if p not in provenance:
                provenance.append(p)
    provenance.append(Provenance(source="cgjrkit", dataset=module, tool=f"{module}.{tool}",
                                 params=_clean(dict(params)), notes=list(notes)))
    out = Result(data=data.reset_index(drop=True), provenance=provenance)
    for res in inputs:
        _merge_meta(out.meta, res.meta)  # aggregate codes: union of the inputs' codes
        for c in res.caveats:
            out.add_caveat(c)
    for c in caveats:
        out.add_caveat(c)
    if meta:
        out.meta.update(_clean(dict(meta)))
    return out


def aggregate_codes(*results: Result) -> frozenset[str]:
    """Codes treated as aggregates: inputs' ``meta["aggregates"]`` plus World Bank aggregate codes."""
    return _aggregate_codes(*results)


def _drop_aggregates(df: pd.DataFrame, aggs: frozenset[str], policy: AggregatePolicy,
                     keep: Iterable[str] = ()) -> tuple[pd.DataFrame, list[str]]:
    """Remove aggregate codes (see :func:`aggregate_codes`) unless ``policy == "keep"``."""
    if policy not in ("drop", "keep"):
        raise ValueError("aggregates must be 'drop' or 'keep'")
    if policy == "keep" or df.empty:
        return df, []
    keep = {str(k).upper() for k in keep}
    iso = df["iso3"].astype(str)
    is_agg = _is_aggregate(iso, aggs) & ~iso.isin(keep)
    return df.loc[~is_agg].copy(), sorted(set(iso[is_agg]))


def _period_list(period: str | Sequence[str] | None, df: pd.DataFrame) -> list[str]:
    if period is None:
        return sorted(df["period"].unique())
    if isinstance(period, str):
        return [period]
    return [str(p) for p in period]


def _status_caveats(statuses: Iterable[str], context: str, evidence: Mapping[str, Any] | None = None) -> list[Caveat]:
    present = {str(s) for s in statuses if s}
    out = []
    for flag, (code, severity, message) in _STATUS_CAVEATS.items():
        if flag in present:
            out.append(Caveat(code, severity, f"{context}: {message}", dict(evidence or {})))
    return out


def _unique_per_country(df: pd.DataFrame, context: str) -> None:
    dup = df.duplicated(subset=["indicator", "iso3", "period"])
    if dup.any():
        row = df.loc[dup].iloc[0]
        raise ValueError(
            f"{context}: more than one observation for {row['indicator']}/{row['iso3']}/{row['period']} "
            "(several sources or datasets?); filter to a single series first"
        )


def _unit_guard(df: pd.DataFrame, context: str, on_ppp_mix: PPPPolicy) -> list[Caveat]:
    """PPP and unit checks for a cross-country statistic, run within each indicator over the periods used.

    Different PPP bases (e.g. a frame assembled from two WDI vintages, PPP 2017
    and PPP 2021) raise :class:`~cgjrkit.methods.harmonize.PPPMixError`
    (``on_ppp_mix="raise"``) or give a ``ppp_base_mix`` block caveat
    (``"block"``); several unit labels within one indicator give a
    ``mixed_units`` warn caveat.
    """
    caveats = _ppp_check_groups(df, ["indicator"], context, on_ppp_mix)
    caveats += _mixed_units_caveat(df, ["indicator"], context,
                                   f"{context}: countries of one indicator carry different unit labels; ranks, "
                                   "densities and scores compare them as if they were the same.")
    return caveats


def _single_indicator(df: pd.DataFrame, name: str) -> tuple[str, str, str]:
    """(indicator, label, unit) of a single-indicator frame; raises if several indicators."""
    inds = sorted(df["indicator"].unique())
    if len(inds) != 1:
        raise ValueError(f"{name} must hold exactly one indicator for the period, found {inds or 'none'}")
    sub = df[df["indicator"] == inds[0]]
    labels = [lab for lab in sub["indicator_label"].unique() if lab]
    units = [u for u in sub["unit"].unique() if u]
    return inds[0], (labels[0] if labels else inds[0]), (units[0] if len(units) == 1 else "; ".join(sorted(units)))


def _first_label(series: pd.Series) -> str:
    vals = [v for v in series if isinstance(v, str) and v]
    return vals[0] if vals else ""


def percentile_of(values: Sequence[float] | np.ndarray, score: float, method: PercentileMethod = "weak") -> float:
    """Percentile (0-100) of ``score`` within ``values`` (NaN ignored); see :data:`PERCENTILE_METHODS`.

    >>> percentile_of([1, 2, 3, 4], 3)            # 3 of 4 values are <= 3
    75.0
    >>> percentile_of([1, 2, 3, 4], 3, "strict")  # 2 of 4 values are < 3
    50.0
    """
    if method not in PERCENTILE_METHODS:
        raise ValueError(f"method must be one of {PERCENTILE_METHODS}")
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0 or not np.isfinite(score):
        return float("nan")
    return float(stats.percentileofscore(arr, float(score), kind=method))


def _prepare(frame: Frame, name: str, aggregates: AggregatePolicy, keep: Iterable[str] = ()):
    """Wrap, validate and strip aggregates; returns (Result, data, dropped aggregate codes)."""
    res = _as_result(frame, name)
    df, dropped = _drop_aggregates(res.data, aggregate_codes(res), aggregates, keep)
    return res, df, dropped


def _suffix_frame(df: pd.DataFrame, suffix: str, label_suffix: str) -> pd.DataFrame:
    df["indicator"] = df["indicator"] + "|" + suffix
    df["indicator_label"] = [f"{lab} ({label_suffix})" if lab else label_suffix for lab in df["indicator_label"]]
    return df


def _sorted_canonical(df: pd.DataFrame, extra: Sequence[str]) -> pd.DataFrame:
    cols = list(COLUMNS) + [c for c in extra if c not in COLUMNS]
    df = df[cols]
    if df.empty:
        return df.reset_index(drop=True)
    return df.sort_values(["indicator", "period", "iso3"], kind="mergesort").reset_index(drop=True)


# ---------------------------------------------------------------------------
# income_conditional_fit
# ---------------------------------------------------------------------------
def _design(x: np.ndarray, degree: int) -> np.ndarray:
    return np.column_stack([x ** k for k in range(degree + 1)])


def _equation(kind: str, degree: int | None, log_x: bool, log_y: bool) -> str:
    xs = "ln(x)" if log_x else "x"
    ys = "ln(y)" if log_y else "y"
    if degree is None:
        return f"{ys} = lowess({xs})"
    terms = ["b0"] + [f"b{k}*{xs}" + (f"^{k}" if k > 1 else "") for k in range(1, degree + 1)]
    return f"{ys} = " + " + ".join(terms)


def income_conditional_fit(
    x_frame: Frame,
    y_frame: Frame,
    period: str | tuple[str, str],
    kind: FitKind = "linear",
    log_x: bool = True,
    focus: str | None = None,
    exclude: Sequence[str] | None = None,
    *,
    log_y: bool = False,
    focus_path: Sequence[str] | None = None,
    y_bounds: tuple[float | None, float | None] = (None, None),
    frac: float = 2.0 / 3.0,
    lowess_it: int = 3,
    grid_points: int = 100,
    ci: float | None = 0.95,
    percentile_method: PercentileMethod = "weak",
    aggregates: AggregatePolicy = "drop",
    on_ppp_mix: PPPPolicy = "raise",
) -> Result:
    """Fit ``y`` on (log) ``x`` across countries in one period and score every country against the curve.

    Parameters
    ----------
    x_frame, y_frame
        Canonical frames (or Results), one indicator each in the chosen period:
        typically ``x`` = GDP per capita, PPP (``NY.GDP.PCAP.PP.KD``) and ``y``
        = the outcome (private credit % of GDP, exports % of GDP, ...).
    period
        ``"2023"`` for both axes, or ``(x_period, y_period)``.
    kind
        ``"linear"``, ``"quadratic"`` or ``"cubic"`` polynomial in the
        regressor (OLS), or ``"lowess"`` (statsmodels local linear regression,
        ``frac`` and ``lowess_it`` robustifying iterations).
    log_x, log_y
        Fit on natural logs (countries with non-positive values are dropped
        and listed). The curve is reported in levels on the original axes.
    focus
        ISO3 of the report country: its actual vs predicted value, residual,
        standardised residual and residual percentile go to ``meta["focus"]``.
    exclude
        ISO3 codes kept in the chart frame but left out of the fit
        (``in_fit=False``, ``exclusion_reason="excluded"``).
    focus_path
        Other periods for the focus country (Kenya Fig 22: 2011, 2015, 2024),
        returned as ``role="focus_path"`` rows scored against *this* period's curve.
    y_bounds
        ``(low, high)``: observations outside are left out of the fit (Kenya
        Fig 22 keeps exports below 100% of GDP), ``exclusion_reason="y_out_of_bounds"``.
    grid_points
        Number of curve points, evenly spaced on the fit scale (log x if
        ``log_x``) between the smallest and largest fitted ``x``.
    ci
        Confidence level of the band for the conditional mean of parametric
        fits (``ci_lower``/``ci_upper`` on the curve); None to skip. Lowess
        has no band.

    Returns
    -------
    Result
        ``data`` columns: ``role`` (``"country"``, ``"curve"``,
        ``"focus_path"``), ``iso3``, ``country``, ``period``, ``x``, ``y``,
        ``fitted``, ``residual`` (on the fit scale: log points if
        ``log_y``), ``ci_lower``, ``ci_upper``, ``in_fit``, ``is_focus``,
        ``exclusion_reason``, ``obs_status``. ``meta["fit"]`` has the
        equation, coefficients (``b0``, ``b1``, ... on the fit scale), R²
        (pseudo-R² for lowess), residual s.d., ``n`` and the axis indicators;
        ``meta["focus"]`` the focus summary; ``meta["dropped"]`` the
        countries removed and why.

    Example (Kenya Fig 14)::

        income_conditional_fit(gdppc_ppp, private_credit, "2023", kind="quadratic", focus="KEN")
    """
    if kind not in FIT_KINDS:
        raise ValueError(f"kind must be one of {sorted(FIT_KINDS)}")
    degree = FIT_KINDS[kind]
    if grid_points < 2:
        raise ValueError("grid_points must be at least 2")
    if ci is not None and not 0 < ci < 1:
        raise ValueError("ci must be between 0 and 1 (or None)")
    if kind == "lowess" and not 0 < frac <= 1:
        raise ValueError("frac must be in (0, 1]")
    x_period, y_period = (period if isinstance(period, (tuple, list)) else (period, period))
    x_period, y_period = str(x_period), str(y_period)
    focus = focus.upper() if focus else None
    excl = {str(e).upper() for e in (exclude or [])}

    keep = [focus] if focus else []
    rx, xdf_all, x_aggs = _prepare(x_frame, "x_frame", aggregates, keep)
    ry, ydf_all, y_aggs = _prepare(y_frame, "y_frame", aggregates, keep)
    xdf = xdf_all[xdf_all["period"] == x_period]
    ydf = ydf_all[ydf_all["period"] == y_period]
    if xdf.empty or ydf.empty:
        raise ValueError(f"no observations for x in {x_period!r} or y in {y_period!r}")
    x_ind, x_label, x_unit = _single_indicator(xdf, "x_frame")
    y_ind, y_label, y_unit = _single_indicator(ydf, "y_frame")
    _unique_per_country(xdf, "income_conditional_fit x_frame")
    _unique_per_country(ydf, "income_conditional_fit y_frame")

    merged = xdf[["iso3", "country", "value", "obs_status", "unit"]].merge(
        ydf[["iso3", "country", "value", "obs_status", "unit"]], on="iso3", how="inner", suffixes=("_x", "_y"))
    merged = merged.rename(columns={"value_x": "x", "value_y": "y"})
    merged["country"] = [cy or cx for cx, cy in zip(merged["country_x"], merged["country_y"])]
    merged["obs_status"] = [combine_status([a, b]) for a, b in zip(merged["obs_status_x"], merged["obs_status_y"])]

    dropped: dict[str, list[str]] = {"aggregates": sorted(set(x_aggs) | set(y_aggs))}
    only_x = sorted(set(xdf["iso3"]) - set(ydf["iso3"]))
    only_y = sorted(set(ydf["iso3"]) - set(xdf["iso3"]))
    dropped["missing_pair"] = sorted(set(only_x) | set(only_y))
    missing = merged["x"].isna() | merged["y"].isna()
    dropped["missing_value"] = sorted(merged.loc[missing, "iso3"])
    merged = merged.loc[~missing]
    if log_x:
        bad = merged["x"] <= 0
        dropped["x_nonpositive"] = sorted(merged.loc[bad, "iso3"])
        merged = merged.loc[~bad]
    if log_y:
        bad = merged["y"] <= 0
        dropped["y_nonpositive"] = sorted(merged.loc[bad, "iso3"])
        merged = merged.loc[~bad]
    merged = merged.sort_values("iso3", kind="mergesort").reset_index(drop=True)

    lo_b, hi_b = y_bounds
    reason = pd.Series("", index=merged.index, dtype=object)
    if lo_b is not None:
        reason[merged["y"] < lo_b] = "y_out_of_bounds"
    if hi_b is not None:
        reason[merged["y"] > hi_b] = "y_out_of_bounds"
    reason[merged["iso3"].isin(excl)] = "excluded"
    merged["exclusion_reason"] = reason
    merged["in_fit"] = reason == ""

    fx = (np.log if log_x else np.asarray)
    fy = (np.log if log_y else np.asarray)
    X_all = np.asarray(fx(merged["x"].to_numpy(float)), dtype=float)
    Y_all = np.asarray(fy(merged["y"].to_numpy(float)), dtype=float)
    mask = merged["in_fit"].to_numpy(bool)
    Xf, Yf = X_all[mask], Y_all[mask]
    n = int(mask.sum())
    min_n = (degree + 2) if degree is not None else 3
    if n < min_n:
        raise ValueError(f"income_conditional_fit: {n} countries in the fit sample; {kind} needs at least {min_n}")
    if np.ptp(Xf) == 0:
        raise ValueError("income_conditional_fit: all fitted x values are identical")

    grid = np.linspace(Xf.min(), Xf.max(), grid_points)
    coefficients: dict[str, float] = {}
    ci_lo = ci_hi = np.full(grid_points, np.nan)
    if degree is not None:
        D = _design(Xf, degree)
        beta, *_ = np.linalg.lstsq(D, Yf, rcond=None)
        fit_all = _design(X_all, degree) @ beta
        fit_grid = _design(grid, degree) @ beta
        resid_fit = Yf - D @ beta
        dof = n - (degree + 1)
        sigma = float(np.sqrt(resid_fit @ resid_fit / dof))
        coefficients = {f"b{k}": float(b) for k, b in enumerate(beta)}
        if ci is not None:
            cov = sigma ** 2 * np.linalg.pinv(D.T @ D)
            G = _design(grid, degree)
            se = np.sqrt(np.einsum("ij,jk,ik->i", G, cov, G))
            tcrit = float(stats.t.ppf(0.5 + ci / 2.0, dof))
            ci_lo, ci_hi = fit_grid - tcrit * se, fit_grid + tcrit * se
    else:
        from statsmodels.nonparametric.smoothers_lowess import lowess

        fit_all = lowess(Yf, Xf, frac=frac, it=lowess_it, xvals=X_all)
        fit_grid = lowess(Yf, Xf, frac=frac, it=lowess_it, xvals=grid)
        if not (np.isfinite(fit_all[mask]).all() and np.isfinite(fit_grid).all()):
            raise ValueError(
                f"income_conditional_fit: lowess returned NaN with frac={frac:g} on {n} countries "
                "(too few points per local regression); increase frac, set lowess_it=0, or use more countries")
        resid_fit = Yf - fit_all[mask]
        sigma = float(np.std(resid_fit, ddof=1))
    ssr = float(resid_fit @ resid_fit)
    sst = float(((Yf - Yf.mean()) ** 2).sum())
    r2 = 1.0 - ssr / sst if sst > 0 else float("nan")

    inv_y = (np.exp if log_y else np.asarray)
    merged["fitted"] = np.asarray(inv_y(fit_all), dtype=float)
    merged["residual"] = Y_all - fit_all
    merged["is_focus"] = merged["iso3"] == focus if focus else False
    merged["role"] = "country"
    merged["period"] = y_period if x_period == y_period else f"{x_period}|{y_period}"

    curve = pd.DataFrame({
        "role": "curve", "iso3": "", "country": "", "period": merged["period"].iloc[0] if len(merged) else y_period,
        "x": np.asarray(np.exp(grid) if log_x else grid, dtype=float), "y": np.nan,
        "fitted": np.asarray(inv_y(fit_grid), dtype=float), "residual": np.nan,
        "ci_lower": np.asarray(inv_y(ci_lo), dtype=float), "ci_upper": np.asarray(inv_y(ci_hi), dtype=float),
        "in_fit": False, "is_focus": False, "exclusion_reason": "", "obs_status": "",
    })

    def predict(xv: np.ndarray) -> np.ndarray:
        """Fitted values (fit scale) at transformed x, from the same curve."""
        if degree is not None:
            return _design(xv, degree) @ beta
        from statsmodels.nonparametric.smoothers_lowess import lowess

        return lowess(Yf, Xf, frac=frac, it=lowess_it, xvals=xv)

    caveats: list[Caveat] = []
    path_rows: list[dict] = []
    if focus_path:
        if not focus:
            raise ValueError("focus_path needs a focus country")
        xs = xdf_all[(xdf_all["iso3"] == focus) & (xdf_all["indicator"] == x_ind)].set_index("period")
        ys = ydf_all[(ydf_all["iso3"] == focus) & (ydf_all["indicator"] == y_ind)].set_index("period")
        missing_path = []
        for p in [str(p) for p in focus_path]:
            if p not in xs.index or p not in ys.index or pd.isna(xs.at[p, "value"]) or pd.isna(ys.at[p, "value"]):
                missing_path.append(p)
                continue
            xv, yv = float(xs.at[p, "value"]), float(ys.at[p, "value"])
            if (log_x and xv <= 0) or (log_y and yv <= 0):
                missing_path.append(p)
                continue
            xt, yt = float(fx(np.array([xv]))[0]), float(fy(np.array([yv]))[0])
            pred = float(predict(np.array([xt]))[0])
            path_rows.append({
                "role": "focus_path", "iso3": focus, "country": _first_label(ys["country"]) or _first_label(xs["country"]),
                "period": p, "x": xv, "y": yv, "fitted": float(inv_y(np.array([pred]))[0]), "residual": yt - pred,
                "ci_lower": np.nan, "ci_upper": np.nan, "in_fit": False, "is_focus": True, "exclusion_reason": "",
                "obs_status": combine_status([xs.at[p, "obs_status"], ys.at[p, "obs_status"]]),
            })
        if missing_path:
            caveats.append(Caveat("focus_path_missing", "warn",
                                  f"No x/y pair for {focus} in focus_path periods {missing_path}.",
                                  {"iso3": focus, "periods": missing_path}))

    cols = ["role", "iso3", "country", "period", "x", "y", "fitted", "residual", "ci_lower", "ci_upper",
            "in_fit", "is_focus", "exclusion_reason", "obs_status"]
    merged["ci_lower"] = np.nan
    merged["ci_upper"] = np.nan
    parts = [merged[cols], curve[cols]]
    if path_rows:
        parts.append(pd.DataFrame(path_rows)[cols].sort_values("period", kind="mergesort"))
    data = pd.concat(parts, ignore_index=True)
    data["in_fit"] = data["in_fit"].astype(bool)
    data["is_focus"] = data["is_focus"].astype(bool)

    # focus summary
    focus_meta: dict[str, Any] = {}
    if focus:
        row = merged[merged["iso3"] == focus]
        if row.empty:
            caveats.append(Caveat("focus_missing", "warn",
                                  f"Focus country {focus} has no x/y pair in {x_period}/{y_period}; it is not in the chart.",
                                  {"iso3": focus, "x_period": x_period, "y_period": y_period}))
        else:
            r = row.iloc[0]
            focus_meta = {
                "iso3": focus, "country": r["country"], "x_period": x_period, "y_period": y_period,
                "x": _num(r["x"]), "actual": _num(r["y"]), "predicted": _num(r["fitted"]),
                "residual": _num(r["residual"]),
                "residual_z": _num(r["residual"] / sigma) if sigma > 0 else None,
                "residual_percentile": _num(percentile_of(resid_fit, float(r["residual"]), percentile_method)),
                "percentile_method": percentile_method,
                "in_fit": bool(r["in_fit"]),
                "above_curve": bool(r["residual"] > 0),
            }

    used = merged.loc[merged["in_fit"]]
    plotted = set(merged["iso3"])  # every charted country, in the fit or not
    ppp_units = pd.DataFrame({"unit": list(xdf.loc[xdf["iso3"].isin(plotted), "unit"])
                              + list(ydf.loc[ydf["iso3"].isin(plotted), "unit"])})
    caveats = _ppp_check_groups(ppp_units, [], "income_conditional_fit", on_ppp_mix) + caveats
    caveats += _status_caveats(used["obs_status"], "income_conditional_fit",
                               {"countries": _limited(sorted(used.loc[used["obs_status"] != "", "iso3"]))})
    if n < 20:
        caveats.append(Caveat("small_sample", "warn" if n < 10 else "info",
                              f"The fit uses only {n} countries; the curve is imprecise.", {"n": n}))
    if len(set(xdf["unit"])) > 1 or len(set(ydf["unit"])) > 1:
        caveats.append(Caveat("mixed_units", "warn", "An axis mixes unit labels across countries.",
                              {"x_units": sorted(set(xdf["unit"])), "y_units": sorted(set(ydf["unit"]))}))
    nonpos = dropped.get("x_nonpositive", []) + dropped.get("y_nonpositive", [])
    if nonpos:
        caveats.append(Caveat("log_nonpositive_dropped", "info",
                              f"{len(nonpos)} countries with non-positive values cannot be logged and were dropped.",
                              {"countries": _limited(sorted(set(nonpos)))}))

    fit_meta = {
        "kind": kind, "degree": degree, "log_x": log_x, "log_y": log_y,
        "equation": _equation(kind, degree, log_x, log_y), "coefficients": coefficients,
        "r2": _num(r2), "r2_note": "pseudo-R² (1 - SSR/SST) of the lowess fit" if degree is None else "OLS R²",
        "residual_sd": _num(sigma), "n": n, "n_excluded": int((~merged["in_fit"]).sum()),
        "x_indicator": x_ind, "x_label": x_label, "x_unit": x_unit, "x_period": x_period,
        "y_indicator": y_ind, "y_label": y_label, "y_unit": y_unit, "y_period": y_period,
        "ci": ci if degree is not None else None, "frac": frac if degree is None else None,
        "lowess_it": lowess_it if degree is None else None,
        "residual_scale": "log points of y" if log_y else "units of y",
    }
    params = {
        "period": [x_period, y_period], "kind": kind, "log_x": log_x, "log_y": log_y, "focus": focus,
        "exclude": sorted(excl), "focus_path": list(focus_path or []), "y_bounds": list(y_bounds),
        "frac": frac, "lowess_it": lowess_it, "grid_points": grid_points, "ci": ci,
        "percentile_method": percentile_method, "aggregates": aggregates, "on_ppp_mix": on_ppp_mix,
    }
    return _finish([rx, ry], data, "income_conditional_fit", params, caveats=caveats,
                   meta={"fit": fit_meta, "focus": focus_meta, "dropped": dropped})


# ---------------------------------------------------------------------------
# percentile_rank
# ---------------------------------------------------------------------------
def _balanced(df: pd.DataFrame, periods: list[str]) -> tuple[pd.DataFrame, list[str]]:
    """Keep countries with a value in every period (per indicator)."""
    parts, removed = [], set()
    for _, g in df.groupby("indicator", sort=True):
        have = g.dropna(subset=["value"]).groupby("iso3")["period"].nunique()
        ok = set(have[have == len(periods)].index)
        removed |= set(g["iso3"]) - ok
        parts.append(g[g["iso3"].isin(ok)])
    return (pd.concat(parts, ignore_index=True) if parts else df.iloc[0:0]), sorted(removed)


def percentile_rank(
    frame: Frame,
    iso3: str | None,
    period: str | Sequence[str],
    *,
    method: PercentileMethod = "weak",
    descending: bool = True,
    balanced: bool = False,
    aggregates: AggregatePolicy = "drop",
    on_ppp_mix: PPPPolicy = "raise",
) -> Result:
    """Rank and percentile of every country, per indicator and period, with a focus summary.

    ``percentile`` is the share of countries (0-100) at or below the country's
    value with the default ``method="weak"`` (see :data:`PERCENTILE_METHODS`;
    the method CGJR teams used is not documented, so it is a parameter).
    ``rank`` is 1 for the highest value (``descending=True``; ties share the
    best rank). ``balanced=True`` keeps only countries observed in every
    requested period, so percentiles compare the same sample over time (Kenya
    Fig 15 uses 104 countries in both 2001 and 2023).

    Output: canonical frame with ``value`` = percentile, indicator
    ``"<code>|pctile"``, unit ``"percentile (0-100, <method>)"``, plus columns
    ``level`` (original value), ``level_unit``, ``rank``, ``n`` and
    ``is_focus``. ``meta["focus"]`` lists the focus country's level, rank, n
    and percentile per indicator and period.

    All values of an indicator (over the requested periods) must share one PPP
    base: a mix raises ``PPPMixError`` (``on_ppp_mix="raise"``) or attaches a
    ``ppp_base_mix`` block caveat (``"block"``); several unit labels give a
    ``mixed_units`` warning.
    """
    if method not in PERCENTILE_METHODS:
        raise ValueError(f"method must be one of {PERCENTILE_METHODS}")
    iso3 = iso3.upper() if iso3 else None
    res, df, dropped_aggs = _prepare(frame, "frame", aggregates, [iso3] if iso3 else [])
    periods = _period_list(period, df)
    df = df[df["period"].isin(periods)].dropna(subset=["value"])
    _unique_per_country(df, "percentile_rank")
    guard = _unit_guard(df, "percentile_rank", on_ppp_mix)
    removed: list[str] = []
    if balanced and len(periods) > 1:
        df, removed = _balanced(df, periods)
    if df.empty:
        raise ValueError(f"percentile_rank: no observations in periods {periods}")

    parts = []
    for (_, _), g in df.groupby(["indicator", "period"], sort=True):
        g = g.copy()
        vals = g["value"].to_numpy(float)
        g["level"] = g["value"]
        g["level_unit"] = g["unit"]
        g["value"] = [percentile_of(vals, v, method) for v in vals]
        g["rank"] = g["level"].rank(method="min", ascending=not descending).astype(int)
        g["n"] = len(g)
        parts.append(g)
    out = pd.concat(parts, ignore_index=True)
    out["is_focus"] = out["iso3"] == iso3 if iso3 else False
    out["unit"] = f"percentile (0-100, {method})"
    out = _suffix_frame(out, "pctile", "percentile rank")
    out = _sorted_canonical(out, ["level", "level_unit", "rank", "n", "is_focus"])

    caveats: list[Caveat] = guard + _status_caveats(df["obs_status"], "percentile_rank")
    focus_meta: list[dict] = []
    if iso3:
        for (ind, per), g in out.groupby(["indicator", "period"], sort=True):
            row = g[g["iso3"] == iso3]
            if not row.empty:
                r = row.iloc[0]
                focus_meta.append({"indicator": ind.rsplit("|", 1)[0], "period": per, "level": _num(r["level"]),
                                   "rank": int(r["rank"]), "n": int(r["n"]), "percentile": _num(r["value"])})
        found = {(f["indicator"], f["period"]) for f in focus_meta}
        expected = {(i.rsplit("|", 1)[0], p) for i, p in out[["indicator", "period"]].drop_duplicates().itertuples(index=False)}
        if expected - found:
            caveats.append(Caveat("focus_missing", "warn", f"{iso3} has no value for some indicator/periods.",
                                  {"iso3": iso3, "missing": [list(x) for x in sorted(expected - found)]}))
    if removed:
        caveats.append(Caveat("unbalanced_sample_dropped", "info",
                              f"{len(removed)} countries without a value in every period were dropped (balanced=True).",
                              {"countries": _limited(removed), "n": len(removed)}))
    params = {"iso3": iso3, "period": periods, "method": method, "descending": descending,
              "balanced": balanced, "aggregates": aggregates, "on_ppp_mix": on_ppp_mix}
    return _finish([res], out, "percentile_rank", params, caveats=caveats,
                   meta={"focus": focus_meta, "dropped": {"aggregates": dropped_aggs, "unbalanced": removed}})


# ---------------------------------------------------------------------------
# kernel_density
# ---------------------------------------------------------------------------
def kernel_density(
    frame: Frame,
    period: str | Sequence[str],
    grid_points: int = 200,
    *,
    focus: str | None = None,
    bw_method: str | float | None = None,
    cut: float = 3.0,
    balanced: bool = False,
    percentile_method: PercentileMethod = "weak",
    aggregates: AggregatePolicy = "drop",
    on_ppp_mix: PPPPolicy = "raise",
) -> Result:
    """Gaussian kernel density of one indicator across countries, per period, on a common grid.

    Uses :class:`scipy.stats.gaussian_kde` (``bw_method``: ``None`` = Scott's
    rule, ``"silverman"``, or a scalar factor). The bandwidth (kernel s.d.) is
    ``factor * sd(values, ddof=1)``. The grid spans, across all periods,
    ``[min - cut*bandwidth, max + cut*bandwidth]`` with ``grid_points``
    points, so densities for several years overlay on one axis (Kenya Fig 15:
    2001 and 2023).

    Output chart frame (``role`` column): ``"density"`` rows (``x``,
    ``density`` per period), ``"country"`` rows (each country's value and the
    density there, for rug marks) and ``"focus"`` rows (the focus country's
    value, density and empirical percentile, see :func:`percentile_of`).
    ``meta["kde"]`` records n, bandwidth and factor per period;
    ``meta["focus"]`` maps period -> value, percentile and n.

    The densities of every period share one axis, so all values must share
    one PPP base (``on_ppp_mix`` as in :func:`percentile_rank`); several unit
    labels give a ``mixed_units`` warning.
    """
    if grid_points < 2:
        raise ValueError("grid_points must be at least 2")
    focus = focus.upper() if focus else None
    res, df, dropped_aggs = _prepare(frame, "frame", aggregates, [focus] if focus else [])
    periods = _period_list(period, df)
    df = df[df["period"].isin(periods)].dropna(subset=["value"])
    if df.empty:
        raise ValueError(f"kernel_density: no observations in periods {periods}")
    ind, label, unit = _single_indicator(df, "frame")
    _unique_per_country(df, "kernel_density")
    guard = _unit_guard(df, "kernel_density", on_ppp_mix)
    removed: list[str] = []
    if balanced and len(periods) > 1:
        df, removed = _balanced(df, periods)

    kdes: dict[str, Any] = {}
    kde_meta: dict[str, dict] = {}
    lo, hi = math.inf, -math.inf
    for p in periods:
        vals = df.loc[df["period"] == p, "value"].to_numpy(float)
        if vals.size < 2 or np.ptp(vals) == 0:
            raise ValueError(f"kernel_density: period {p} needs at least 2 distinct values, has {vals.size}")
        kde = stats.gaussian_kde(vals, bw_method=bw_method)
        bandwidth = float(np.sqrt(kde.covariance[0, 0]))
        kdes[p] = kde
        kde_meta[p] = {"n": int(vals.size), "factor": float(kde.factor), "bandwidth": bandwidth,
                       "min": float(vals.min()), "max": float(vals.max())}
        lo, hi = min(lo, vals.min() - cut * bandwidth), max(hi, vals.max() + cut * bandwidth)
    grid = np.linspace(lo, hi, grid_points)

    rows: list[pd.DataFrame] = []
    focus_meta: dict[str, dict] = {}
    caveats: list[Caveat] = list(guard)
    for p in periods:
        kde = kdes[p]
        sub = df[df["period"] == p].sort_values("iso3", kind="mergesort")
        vals = sub["value"].to_numpy(float)
        n = int(vals.size)
        rows.append(pd.DataFrame({"role": "density", "period": p, "iso3": "", "country": "", "x": grid,
                                  "density": kde(grid), "percentile": np.nan, "n": n, "is_focus": False}))
        pct = [percentile_of(vals, v, percentile_method) for v in vals]
        rows.append(pd.DataFrame({"role": "country", "period": p, "iso3": sub["iso3"].to_numpy(),
                                  "country": sub["country"].to_numpy(), "x": vals, "density": kde(vals),
                                  "percentile": pct, "n": n, "is_focus": (sub["iso3"] == focus).to_numpy()}))
        if focus:
            f = sub[sub["iso3"] == focus]
            if f.empty:
                caveats.append(Caveat("focus_missing", "warn", f"{focus} has no value in {p}.",
                                      {"iso3": focus, "period": p}))
                continue
            v = float(f["value"].iloc[0])
            d = float(kde(np.array([v]))[0])
            pc = percentile_of(vals, v, percentile_method)
            rows.append(pd.DataFrame([{"role": "focus", "period": p, "iso3": focus, "country": f["country"].iloc[0],
                                       "x": v, "density": d, "percentile": pc, "n": n, "is_focus": True}]))
            focus_meta[p] = {"value": v, "density": d, "percentile": pc, "n": n,
                             "kde_cdf": float(kde.integrate_box_1d(-np.inf, v)) * 100.0}
    data = pd.concat(rows, ignore_index=True)
    data["is_focus"] = data["is_focus"].astype(bool)

    caveats += _status_caveats(df["obs_status"], "kernel_density")
    small = {p: m["n"] for p, m in kde_meta.items() if m["n"] < 20}
    if small:
        caveats.append(Caveat("small_sample", "warn", "Kernel densities on fewer than 20 countries are fragile.",
                              {"n_by_period": small}))
    if removed:
        caveats.append(Caveat("unbalanced_sample_dropped", "info",
                              f"{len(removed)} countries without a value in every period were dropped (balanced=True).",
                              {"countries": _limited(removed), "n": len(removed)}))
    params = {"period": periods, "grid_points": grid_points, "focus": focus, "bw_method": bw_method, "cut": cut,
              "balanced": balanced, "percentile_method": percentile_method, "aggregates": aggregates,
              "on_ppp_mix": on_ppp_mix}
    meta = {"kde": {"indicator": ind, "label": label, "unit": unit, "by_period": kde_meta,
                    "percentile_method": percentile_method},
            "focus": {"iso3": focus, "by_period": focus_meta} if focus else {},
            "dropped": {"aggregates": dropped_aggs, "unbalanced": removed}}
    return _finish([res], data, "kernel_density", params, caveats=caveats, meta=meta)


# ---------------------------------------------------------------------------
# zscore
# ---------------------------------------------------------------------------
def zscore(
    frame: Frame,
    period: str | Sequence[str] | None = None,
    *,
    reference: Sequence[str] | None = None,
    invert: Sequence[str] = (),
    ddof: int = 1,
    composite: bool = False,
    min_components: int = 1,
    focus: str | None = None,
    aggregates: AggregatePolicy = "drop",
    on_ppp_mix: PPPPolicy = "raise",
) -> Result:
    """Standardise each indicator across countries within each period: ``z = (x - mean) / sd``.

    ``reference``: ISO3 codes whose mean and s.d. define the scale (for example
    a frozen peer set or OECD members); every country is then scored on that
    scale. Default: all countries with data. ``ddof=1`` uses the sample s.d.
    ``invert``: indicator codes where lower is better (cost, time, risk); their
    z-scores are multiplied by -1 so that higher always means better.
    ``composite=True`` adds an unweighted mean of the available z-scores per
    country and period (indicator ``"zscore_composite"``, ``n_components``
    column), keeping countries with at least ``min_components`` components.

    Output: canonical frame, ``value`` = z, indicator ``"<code>|z"``, unit
    ``"z-score"``, extra columns ``level``, ``mean``, ``sd``, ``n_ref``,
    ``n_components``, ``is_focus``. Values of one indicator must share one
    PPP base (``on_ppp_mix`` as in :func:`percentile_rank`).
    """
    focus = focus.upper() if focus else None
    res, df, dropped_aggs = _prepare(frame, "frame", aggregates, [focus] if focus else [])
    periods = _period_list(period, df)
    df = df[df["period"].isin(periods)].dropna(subset=["value"])
    _unique_per_country(df, "zscore")
    if df.empty:
        raise ValueError(f"zscore: no observations in periods {periods}")
    guard = _unit_guard(df, "zscore", on_ppp_mix)
    ref = {str(r).upper() for r in reference} if reference else None
    inv = set(invert)
    unknown_inv = sorted(inv - set(df["indicator"]))

    parts, degenerate, ref_short = [], [], []
    for (ind, per), g in df.groupby(["indicator", "period"], sort=True):
        g = g.copy()
        base = g[g["iso3"].isin(ref)] if ref is not None else g
        vals = base["value"].to_numpy(float)
        mean = float(vals.mean()) if vals.size else float("nan")
        sd = float(vals.std(ddof=ddof)) if vals.size > ddof else float("nan")
        if ref is not None and len(base) < len(ref):
            ref_short.append({"indicator": ind, "period": per, "n_ref": int(len(base)), "n_reference": len(ref)})
        if not (math.isfinite(sd) and sd > 0):
            degenerate.append(f"{ind}/{per}")
        g["level"] = g["value"]
        z = (g["value"] - mean) / sd if (math.isfinite(sd) and sd > 0) else np.nan
        g["value"] = -z if ind in inv else z
        g["mean"], g["sd"], g["n_ref"], g["n_components"] = mean, sd, int(len(base)), 1
        parts.append(g)
    out = pd.concat(parts, ignore_index=True)
    out["unit"] = np.where(out["indicator"].isin(inv), "z-score (sign inverted: higher = better)", "z-score")
    out = _suffix_frame(out, "z", "z-score")

    caveats: list[Caveat] = guard + _status_caveats(df["obs_status"], "zscore")
    if composite:
        comp_rows = []
        n_ind = out["indicator"].nunique()
        for (iso, per), g in out.dropna(subset=["value"]).groupby(["iso3", "period"], sort=True):
            if len(g) < min_components:
                continue
            comp_rows.append({
                "source": g["source"].iloc[0] if g["source"].nunique() == 1 else "cgjrkit",
                "dataset": g["dataset"].iloc[0] if g["dataset"].nunique() == 1 else "zscore",
                "indicator": "zscore_composite",
                "indicator_label": f"Composite: mean of z-scores ({n_ind} indicators)",
                "iso3": iso, "country": _first_label(g["country"]), "period": per,
                "value": float(g["value"].mean()), "unit": "z-score (mean of components)",
                "obs_status": combine_status(g["obs_status"]), "level": np.nan, "mean": np.nan, "sd": np.nan,
                "n_ref": np.nan, "n_components": int(len(g)),
            })
        if comp_rows:
            comp = pd.DataFrame(comp_rows)
            partial = sorted(set(comp.loc[comp["n_components"] < n_ind, "iso3"]))
            if partial:
                caveats.append(Caveat("composite_partial", "warn",
                                      f"{len(partial)} countries' composites average fewer than {n_ind} indicators.",
                                      {"countries": _limited(partial), "n": len(partial)}))
            out = pd.concat([out, comp], ignore_index=True)
    out["is_focus"] = out["iso3"] == focus if focus else False
    out = _sorted_canonical(out, ["level", "mean", "sd", "n_ref", "n_components", "is_focus"])

    if degenerate:
        caveats.append(Caveat("zscore_zero_variance", "warn", "Some groups have zero or undefined spread; z is NaN.",
                              {"groups": _limited(degenerate)}))
    if ref_short:
        caveats.append(Caveat("reference_incomplete", "warn",
                              "Some reference countries have no value; the scale uses the members present.",
                              {"groups": _limited(ref_short)}))
    if unknown_inv:
        caveats.append(Caveat("invert_unknown_indicator", "info", "invert lists indicators not in the frame.",
                              {"indicators": unknown_inv}))
    focus_meta = {}
    if focus:
        f = out[out["iso3"] == focus]
        focus_meta = {"iso3": focus, "values": [{"indicator": r.indicator, "period": r.period, "z": _num(r.value)}
                                                for r in f.itertuples(index=False)]}
    params = {"period": periods, "reference": sorted(ref) if ref else None, "invert": sorted(inv), "ddof": ddof,
              "composite": composite, "min_components": min_components, "focus": focus, "aggregates": aggregates,
              "on_ppp_mix": on_ppp_mix}
    return _finish([res], out, "zscore", params, caveats=caveats,
                   meta={"focus": focus_meta, "dropped": {"aggregates": dropped_aggs}})


# ---------------------------------------------------------------------------
# distance_to_frontier
# ---------------------------------------------------------------------------
_DTF_SUFFIX = {"gap": ("dtf_gap", "gap to frontier"), "pct": ("pct_frontier", "% of frontier"),
               "score": ("dtf_score", "distance-to-frontier score, 0-100")}


def distance_to_frontier(
    frame: Frame,
    frontier: str = "max",
    period: str | Sequence[str] | None = None,
    *,
    measure: Literal["gap", "pct", "score"] = "gap",
    higher_is_better: bool = True,
    worst: float | None = None,
    focus: str | None = None,
    aggregates: AggregatePolicy = "drop",
    on_ppp_mix: PPPPolicy = "raise",
) -> Result:
    """Distance of every country to a frontier, per indicator and period.

    ``frontier``: ``"max"`` (best = highest value), ``"min"`` (best = lowest
    value, e.g. costs) or an ISO3 code (``"USA"``; direction from
    ``higher_is_better``). Three measures are computed and ``measure`` picks
    the one placed in ``value``:

    * ``gap``: ``frontier - x`` (``x - frontier`` when lower is better), so
      positive means behind the frontier;
    * ``pct``: ``100 * x / frontier``;
    * ``score``: ``100 * (x - worst) / (frontier - worst)`` (Doing Business
      style distance-to-frontier score: 100 = frontier, 0 = worst performer in
      the group, or the fixed ``worst`` value if given).

    Output: canonical frame with indicator ``"<code>|dtf_gap"`` (or
    ``|pct_frontier``, ``|dtf_score``) plus columns ``level``,
    ``frontier_value``, ``frontier_iso3``, ``gap``, ``pct_of_frontier``,
    ``score`` and ``is_focus``. Values of one indicator must share one PPP
    base (``on_ppp_mix`` as in :func:`percentile_rank`).
    """
    if measure not in _DTF_SUFFIX:
        raise ValueError(f"measure must be one of {sorted(_DTF_SUFFIX)}")
    fr = str(frontier)
    if fr.lower() in ("max", "min"):
        fr = fr.lower()
        higher_is_better = fr == "max"
    elif not _ISO3_RE.match(fr.upper()):
        raise ValueError("frontier must be 'max', 'min' or an ISO3 code")
    else:
        fr = fr.upper()
    focus = focus.upper() if focus else None
    keep = [c for c in (focus, fr if fr not in ("max", "min") else None) if c]
    res, df, dropped_aggs = _prepare(frame, "frame", aggregates, keep)
    periods = _period_list(period, df)
    df = df[df["period"].isin(periods)].dropna(subset=["value"])
    _unique_per_country(df, "distance_to_frontier")
    if df.empty:
        raise ValueError(f"distance_to_frontier: no observations in periods {periods}")
    guard = _unit_guard(df, "distance_to_frontier", on_ppp_mix)

    parts, no_frontier, degenerate = [], [], []
    for (ind, per), g in df.groupby(["indicator", "period"], sort=True):
        g = g.sort_values("iso3", kind="mergesort").copy()
        if fr in ("max", "min"):
            idx = g["value"].idxmax() if fr == "max" else g["value"].idxmin()
            f_val, f_iso = float(g.at[idx, "value"]), str(g.at[idx, "iso3"])
        else:
            hit = g[g["iso3"] == fr]
            if hit.empty:
                no_frontier.append(f"{ind}/{per}")
                continue
            f_val, f_iso = float(hit["value"].iloc[0]), fr
        w_val = worst if worst is not None else float(g["value"].min() if higher_is_better else g["value"].max())
        x = g["value"]
        g["level"] = x
        g["frontier_value"], g["frontier_iso3"] = f_val, f_iso
        g["gap"] = (f_val - x) if higher_is_better else (x - f_val)
        g["pct_of_frontier"] = 100.0 * x / f_val if f_val != 0 else np.nan
        if f_val == w_val:
            degenerate.append(f"{ind}/{per}")
            g["score"] = np.nan
        else:
            g["score"] = 100.0 * (x - w_val) / (f_val - w_val)
        g["value"] = g[{"gap": "gap", "pct": "pct_of_frontier", "score": "score"}[measure]]
        if measure == "pct":
            g["unit"] = f"% of frontier ({f_iso})"
        elif measure == "score":
            g["unit"] = "score (0-100, 100 = frontier)"
        parts.append(g)
    if not parts:
        raise ValueError(f"distance_to_frontier: frontier {fr} has no values in periods {periods}")
    out = pd.concat(parts, ignore_index=True)
    suffix, label_suffix = _DTF_SUFFIX[measure]
    out = _suffix_frame(out, suffix, label_suffix)
    out["is_focus"] = out["iso3"] == focus if focus else False
    out = _sorted_canonical(out, ["level", "frontier_value", "frontier_iso3", "gap", "pct_of_frontier", "score",
                                  "is_focus"])

    caveats: list[Caveat] = guard + _status_caveats(df["obs_status"], "distance_to_frontier")
    if no_frontier:
        caveats.append(Caveat("frontier_missing", "warn", f"Frontier {fr} has no value in some groups; dropped.",
                              {"groups": _limited(no_frontier), "n": len(no_frontier)}))
    if degenerate:
        caveats.append(Caveat("frontier_equals_worst", "info", "Frontier equals the worst value; score is NaN.",
                              {"groups": _limited(degenerate)}))
    focus_meta = {}
    if focus:
        f = out[out["iso3"] == focus]
        focus_meta = {"iso3": focus, "values": [
            {"indicator": r.indicator, "period": r.period, "level": _num(r.level), "gap": _num(r.gap),
             "pct_of_frontier": _num(r.pct_of_frontier), "score": _num(r.score), "frontier_iso3": r.frontier_iso3}
            for r in f.itertuples(index=False)]}
    params = {"frontier": fr, "period": periods, "measure": measure, "higher_is_better": higher_is_better,
              "worst": worst, "focus": focus, "aggregates": aggregates, "on_ppp_mix": on_ppp_mix}
    return _finish([res], out, "distance_to_frontier", params, caveats=caveats,
                   meta={"focus": focus_meta, "dropped": {"aggregates": dropped_aggs}})
