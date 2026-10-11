"""Growth accounting, perpetual-inventory capital and an HP-filter potential output approximation.

Production function (Cobb-Douglas, constant returns)::

    Y = A * K**alpha * (hc * L)**(1 - alpha)

so that, in log differences,

    dln Y   = alpha*dln K + (1-alpha)*dln L + (1-alpha)*dln hc + dln A           (aggregate)
    dln Y/L = alpha*dln(K/L) + (1-alpha)*dln hc + dln A                          (per worker)

TFP (``dln A``) is the residual, so the contributions add up to output growth
*exactly* (to floating-point precision); ``meta["growth_accounting"]
["identity_max_abs_error"]`` reports the check.

Functions
---------
=======================  ======================================================
``growth_accounting``    average annual contributions of capital, labour,
                         human capital and TFP for named periods, aggregate or
                         per worker, with an alpha sensitivity band
                         (archetypes G04/G06; Kenya Fig 5, North Macedonia Fig 4)
``perpetual_inventory``  capital stock from investment:
                         ``K_t = (1 - delta) K_{t-1} + I_t`` with a steady-state
                         or given initial stock
``potential_output``     production-function potential output from
                         HP-filtered TFP and employment; an *approximation*,
                         not the World Bank MFMod estimate
=======================  ======================================================

Inputs are canonical frames (or Results, or a list of them) holding the
variables as indicators, by default the Penn World Table names: ``rgdpna``
(real GDP at constant national prices), ``rkna`` (capital services at
constant national prices), ``emp`` (persons engaged), ``hc`` (human capital
index) and ``labsh`` (labour share of GDP). Any other series can be mapped
with ``output=``, ``capital=``, ``labour=``, ``human_capital=`` and
``labour_share=`` (e.g. WDI ``NY.GDP.MKTP.KN`` and a capital stock from
:func:`perpetual_inventory`). Periods must be annual (``"2019"``).

Capital measure: ``rkna`` (capital services) is the input PWT itself uses for
``rtfpna``; ``rnna`` (capital stock) is the textbook alternative. PWT computes
``rtfpna`` with Tornqvist weights (labour share averaged over t-1 and t) on
labour input ``emp * hc`` (Feenstra, Inklaar and Timmer 2015; UNVERIFIED in
this environment, see ``tests/live/test_growth_accounting_live.py``);
``weights="tornqvist"`` reproduces that scheme.

Units and PPP bases: growth accounting takes log differences of each series,
so a series must keep one unit (one PWT edition, one national-accounts base
year) over the sample. Within each indicator and country, different PPP bases
(e.g. PWT 10.01 at PPP 2017 up to 2005 and PWT 11.0 at PPP 2021 after) raise
:class:`~cgjrkit.methods.harmonize.PPPMixError` (``on_ppp_mix="raise"``, the
default) or attach a ``ppp_base_mix`` block caveat (``"block"``); any other
change of unit label within a series (constant 2009 vs 2016 LCU) attaches a
``mixed_units`` warning. Different labels *across* countries are fine (growth
rates are unit-free). Bridge editions first with
:func:`cgjrkit.methods.harmonize.splice` (``method="ratio"``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Sequence

import numpy as np
import pandas as pd

from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Result
from cgjrkit.core.schema import COLUMNS
from cgjrkit.methods.benchmark import _clean, _finish, _status_caveats
from cgjrkit.methods.harmonize import (
    Frame,
    PPPPolicy,
    _as_result,
    _limited,
    _merge_meta,
    _mixed_units_caveat,
    _ppp_check_groups,
    combine_status,
    parse_period,
)

__all__ = [
    "DEFAULT_ALPHA",
    "DEFAULT_SENSITIVITY",
    "COMPONENT_LABELS",
    "growth_accounting",
    "perpetual_inventory",
    "potential_output",
]

#: Capital share used only when ``alpha=None`` and no labour share is available (with a warning).
DEFAULT_ALPHA = 1.0 / 3.0
#: Default alpha sensitivity band.
DEFAULT_SENSITIVITY: tuple[float, ...] = (0.3, 0.35, 0.4)

COMPONENT_LABELS: dict[str, str] = {
    "output": "Real output growth",
    "capital": "Contribution of capital",
    "labour": "Contribution of labour (employment)",
    "human_capital": "Contribution of human capital",
    "tfp": "Contribution of TFP (residual)",
    "output_per_worker": "Real output per worker growth",
    "capital_deepening": "Contribution of capital deepening (capital per worker)",
}

_UNITS = {
    "log": "log points per year (100 x annual change in ln)",
    "percent": "percent per year (log contributions rescaled to the compound annual growth rate)",
}

Weights = Literal["period_mean", "tornqvist"]
Units = Literal["log", "percent"]
_ROLES = ("Y", "K", "L", "H", "S")  # output, capital, labour, human capital, labour share


# ---------------------------------------------------------------------------
# panel loading
# ---------------------------------------------------------------------------
@dataclass
class _Panel:
    result: Result                                    # combined inputs (provenance, caveats, meta)
    values: dict[str, pd.DataFrame]                   # iso3 -> wide frame (index year, columns roles)
    status: dict[str, pd.DataFrame]                   # iso3 -> obs_status, same shape
    country: dict[str, str] = field(default_factory=dict)
    source: dict[str, tuple[str, str]] = field(default_factory=dict)  # iso3 -> (source, dataset)
    units: dict[str, str] = field(default_factory=dict)              # role -> unit label(s), "; "-joined
    absent: list[str] = field(default_factory=list)                  # requested iso3 without data
    caveats: list[Caveat] = field(default_factory=list)              # PPP / unit-label checks
    country_units: dict[str, dict[str, str]] = field(default_factory=dict)  # iso3 -> role -> unit label


def _combine(panel: Frame | Sequence[Frame]) -> Result:
    """One Result from a frame, a Result, or a list of them (provenance and caveats merged)."""
    if isinstance(panel, (Result, pd.DataFrame)):
        return _as_result(panel, "panel")
    items = [_as_result(p, f"panel[{i}]") for i, p in enumerate(panel)]
    if not items:
        raise ValueError("panel is empty")
    out = items[0]
    for other in items[1:]:
        out = out.merged_with(other)
    out.data = out.data.reset_index(drop=True)
    meta: dict = {}
    for item in items:  # Result.merged_with keeps only the last meta["aggregates"]; keep the union
        _merge_meta(meta, item.meta)
    out.meta = meta
    return out


def _unit_checks(df: pd.DataFrame, codes: Mapping[str, str], context: str, on_ppp_mix: PPPPolicy) -> list[Caveat]:
    """PPP bases must agree within each indicator and country; other unit-label changes get a warning."""
    caveats = _ppp_check_groups(df, ["indicator", "iso3"], context, on_ppp_mix)
    mixed = _mixed_units_caveat(
        df, ["indicator", "iso3"], context,
        f"{context}: a series changes unit label within the sample (two editions or national-accounts base years, "
        "e.g. constant 2009 vs 2016 LCU); log growth across the switch mixes levels measured on different bases. "
        "Use one edition, or splice them first (harmonize.splice, method='ratio').")
    for c in mixed:  # name the role of each series in the evidence
        groups = [dict(g, role=codes.get(g["group"].rsplit("/", 1)[0], "")) for g in c.evidence["groups"]]
        caveats.append(Caveat(c.code, c.severity, c.message, {**c.evidence, "groups": groups}))
    return caveats


def _load_panel(panel: Frame | Sequence[Frame], roles: Mapping[str, str | None],
                iso3: str | Sequence[str] | None, context: str = "growth_accounting",
                on_ppp_mix: PPPPolicy = "raise") -> _Panel:
    res = _combine(panel)
    codes = {code: role for role, code in roles.items() if code}
    if len(codes) != len([c for c in roles.values() if c]):
        raise ValueError(f"each role needs its own indicator, got {dict(roles)}")
    df = res.data[res.data["indicator"].isin(codes)].copy()
    absent: list[str] = []
    if iso3 is not None:
        wanted = [iso3.upper()] if isinstance(iso3, str) else [str(i).upper() for i in iso3]
        df = df[df["iso3"].isin(wanted)]
        absent = sorted(set(wanted) - set(df["iso3"]))
        if absent and len(absent) == len(wanted):
            raise ValueError(f"no observations for {absent} in the panel")
    for role in ("Y", "K", "L"):
        if roles[role] not in set(df["indicator"]):
            raise ValueError(f"panel has no {roles[role]!r} observations (role {role})")
    dup = df.duplicated(subset=["indicator", "iso3", "period"], keep=False)
    if dup.any():
        sample = df.loc[dup, ["indicator", "iso3", "period", "dataset"]].head(4).to_dict("records")
        raise ValueError(f"panel has several observations per indicator/country/year (several datasets or "
                         f"vintages?); filter to one, e.g. {sample}")
    freqs = {parse_period(p).freq for p in df["period"].unique()}
    if freqs - {"A"}:
        raise ValueError(f"growth accounting needs annual periods ('2019'), found frequencies {sorted(freqs)}")
    checks = _unit_checks(df, codes, context, on_ppp_mix)
    df["year"] = df["period"].astype(int)
    df["role"] = df["indicator"].map(codes)

    values: dict[str, pd.DataFrame] = {}
    status: dict[str, pd.DataFrame] = {}
    country: dict[str, str] = {}
    source: dict[str, tuple[str, str]] = {}
    country_units: dict[str, dict[str, str]] = {}
    for iso, g in df.groupby("iso3", sort=True):
        values[iso] = g.pivot(index="year", columns="role", values="value").reindex(columns=list(_ROLES)).sort_index()
        status[iso] = g.pivot(index="year", columns="role", values="obs_status").reindex(columns=list(_ROLES)).sort_index()
        labels = [c for c in g.loc[g["role"] == "Y", "country"] if c] or [c for c in g["country"] if c]
        country[iso] = labels[0] if labels else ""
        srcs, dsets = sorted(set(g["source"])), sorted(set(g["dataset"]))
        source[iso] = (srcs[0] if len(srcs) == 1 else "cgjrkit", dsets[0] if len(dsets) == 1 else "growth_accounting")
        country_units[iso] = {role: "; ".join(sorted(set(r["unit"]) - {""})) for role, r in g.groupby("role")}
    units = {}
    for role, code in roles.items():
        if code:
            u = sorted(set(df.loc[df["indicator"] == code, "unit"]) - {""})
            units[role] = "; ".join(u)
    s = df.loc[df["role"] == "S", "value"].dropna()
    if len(s) and ((s <= 0) | (s >= 1)).any():
        raise ValueError(f"labour share {roles['S']!r} must be a fraction in (0, 1); "
                         f"found values in [{s.min():g}, {s.max():g}] (percent? divide by 100)")
    return _Panel(res, values, status, country, source, units, absent, checks, country_units)


def _periods(periods: Mapping[str, Sequence[str]] | Sequence[Sequence[str]]) -> list[tuple[str, int, int]]:
    """``{"2001-10": ("2000", "2010")}`` or ``[("2000", "2010")]`` -> [(label, start, end)]."""
    items = list(periods.items()) if isinstance(periods, Mapping) else [(None, p) for p in periods]
    if not items:
        raise ValueError("periods is empty")
    out = []
    for label, win in items:
        if isinstance(win, str) or len(win) != 2:
            raise ValueError(f"each period must be a (start, end) pair of years, got {win!r}")
        lo, hi = parse_period(str(win[0])), parse_period(str(win[1]))
        if lo.freq != "A" or hi.freq != "A":
            raise ValueError(f"period endpoints must be calendar years, got {win!r}")
        if hi.year <= lo.year:
            raise ValueError(f"period {win!r} must end after it starts")
        out.append((str(label) if label is not None else f"{lo.year}-{hi.year}", lo.year, hi.year))
    labels = [o[0] for o in out]
    if len(set(labels)) != len(labels):
        raise ValueError(f"period labels must be unique: {labels}")
    return out


# ---------------------------------------------------------------------------
# contribution arithmetic (pure numpy)
# ---------------------------------------------------------------------------
def _contributions(d: Mapping[str, np.ndarray], alpha: np.ndarray, years: int, per_worker: bool,
                   use_h: bool) -> dict[str, float]:
    """Average annual contributions in log points (x100) from log differences.

    ``d[role]`` holds log differences (one element per sub-interval; a single
    element for start-to-end differences) and ``alpha`` the capital share of
    each sub-interval.
    """
    a = np.asarray(alpha, dtype=float)
    dY, dK, dL = (np.asarray(d[r], dtype=float) for r in ("Y", "K", "L"))
    dH = np.asarray(d["H"], dtype=float) if use_h else np.zeros_like(dY)
    scale = 100.0 / years
    out: dict[str, float] = {}
    if per_worker:
        out["output_per_worker"] = scale * float(np.sum(dY - dL))
        out["capital_deepening"] = scale * float(np.sum(a * (dK - dL)))
        if use_h:
            out["human_capital"] = scale * float(np.sum((1 - a) * dH))
        out["tfp"] = out["output_per_worker"] - sum(v for k, v in out.items() if k != "output_per_worker")
    else:
        out["output"] = scale * float(np.sum(dY))
        out["capital"] = scale * float(np.sum(a * dK))
        out["labour"] = scale * float(np.sum((1 - a) * dL))
        if use_h:
            out["human_capital"] = scale * float(np.sum((1 - a) * dH))
        out["tfp"] = out["output"] - sum(v for k, v in out.items() if k != "output")
    return out


def _to_percent(contrib: dict[str, float], total_key: str) -> dict[str, float]:
    """Rescale log contributions so they add up to the compound annual growth rate."""
    g = contrib[total_key]
    factor = 100.0 * math.expm1(g / 100.0) / g if g != 0 else 1.0
    return {k: v * factor for k, v in contrib.items()}


# ---------------------------------------------------------------------------
# growth_accounting
# ---------------------------------------------------------------------------
def growth_accounting(
    panel: Frame | Sequence[Frame],
    periods: Mapping[str, Sequence[str]] | Sequence[Sequence[str]],
    alpha: float | None = None,
    *,
    iso3: str | Sequence[str] | None = None,
    output: str = "rgdpna",
    capital: str = "rkna",
    labour: str = "emp",
    human_capital: str | None = "hc",
    labour_share: str | None = "labsh",
    weights: Weights = "period_mean",
    per_worker: bool = False,
    units: Units = "log",
    sensitivity: Sequence[float] | None = DEFAULT_SENSITIVITY,
    on_ppp_mix: PPPPolicy = "raise",
) -> Result:
    """Average annual contributions of capital, labour, human capital and TFP to growth.

    Parameters
    ----------
    panel
        Canonical frame(s) with the variables as indicators (PWT names by
        default; see the module docstring). Annual periods only.
    periods
        ``{"2000-09": ("1999", "2009"), ...}``: each window is measured from
        its *base* year to its end year, so average growth over 2000-09
        (10 annual changes) is ``("1999", "2009")``. A list of pairs gets
        labels ``"<start>-<end>"``.
    alpha
        Capital share. ``None`` (default): ``1 - labour_share``, averaged over
        the window's years (``weights="period_mean"``) or per year
        (``weights="tornqvist"``: ``alpha_t = 1 - (labsh_t + labsh_{t-1})/2``,
        needs every year). Countries with no labour share fall back to
        :data:`DEFAULT_ALPHA` (1/3) with an ``alpha_default`` warning. A float
        applies one share to every country and window.
    human_capital
        Indicator of the human capital index, or ``None`` to leave it out
        (labour is then raw employment). When set, windows lacking it are skipped.
    per_worker
        Decompose output per worker:
        ``dln(Y/L) = alpha*dln(K/L) + (1-alpha)*dln hc + dln TFP`` (same TFP).
    units
        ``"log"`` (default): 100 x average annual log change, contributions
        add up exactly. ``"percent"``: every contribution is multiplied by
        ``CAGR / log growth`` so that they add up exactly to the compound
        annual growth rate (a presentational rescaling).
    sensitivity
        Constant alphas for the sensitivity band (default 0.3, 0.35, 0.4);
        ``None`` or ``()`` to skip. ``band_min``/``band_max`` give the range of
        each component across these alphas and ``meta["alpha_sensitivity"]``
        the full table.
    on_ppp_mix
        A series (indicator x country) whose unit labels carry different PPP
        bases raises ``PPPMixError`` (``"raise"``) or returns a result with a
        ``ppp_base_mix`` block caveat (``"block"``); see the module docstring.

    Returns
    -------
    Result
        Canonical frame, one row per country x window x component, with
        indicator ``"ga.<component>"`` (``"ga_pw.<component>"`` per worker),
        ``period`` = window label, plus columns ``component``, ``alpha``
        (effective capital share), ``start``, ``end``, ``years``,
        ``share_of_growth`` (% of output growth), ``band_min``, ``band_max``.
        Components: ``output``, ``capital``, ``labour``, ``human_capital``,
        ``tfp`` (aggregate) or ``output_per_worker``, ``capital_deepening``,
        ``human_capital``, ``tfp`` (per worker).
    """
    if weights not in ("period_mean", "tornqvist"):
        raise ValueError("weights must be 'period_mean' or 'tornqvist'")
    if units not in _UNITS:
        raise ValueError(f"units must be one of {sorted(_UNITS)}")
    if alpha is not None and not 0 < float(alpha) < 1:
        raise ValueError("alpha must be between 0 and 1")
    if weights == "tornqvist" and alpha is not None:
        raise ValueError("weights='tornqvist' uses year-specific labour shares; pass alpha=None")
    sens = [float(a) for a in (sensitivity or [])]
    if any(not 0 < a < 1 for a in sens):
        raise ValueError("sensitivity alphas must be between 0 and 1")
    roles = {"Y": output, "K": capital, "L": labour, "H": human_capital,
             "S": labour_share if alpha is None else None}
    pan = _load_panel(panel, roles, iso3, "growth_accounting", on_ppp_mix)
    windows = _periods(periods)
    use_h = human_capital is not None
    total_key = "output_per_worker" if per_worker else "output"
    prefix = "ga_pw" if per_worker else "ga"
    needed = ["Y", "K", "L"] + (["H"] if use_h else [])

    rows: list[dict] = []
    sens_records: list[dict] = []
    skipped: list[dict] = []
    defaulted: list[str] = []
    alpha_used: dict[str, dict[str, float]] = {}
    identity_err = 0.0
    for iso, w in pan.values.items():
        st = pan.status[iso]
        for label, y0, y1 in windows:
            years = y1 - y0
            ends = w.reindex([y0, y1])
            bad = [r for r in needed if not (ends[r].notna().all() and (ends[r] > 0).all())]
            if bad:
                skipped.append({"iso3": iso, "period": label, "reason": f"missing or non-positive {bad} at {y0}/{y1}"})
                continue
            span = w.reindex(range(y0, y1 + 1))
            if weights == "tornqvist":
                cols = needed + ["S"]
                gaps = [r for r in cols if span[r].isna().any()]
                if gaps:
                    skipped.append({"iso3": iso, "period": label,
                                    "reason": f"weights='tornqvist' needs every year {y0}-{y1}; missing {gaps}"})
                    continue
                if (span[needed] <= 0).any().any():
                    skipped.append({"iso3": iso, "period": label, "reason": "non-positive values"})
                    continue
                logs = np.log(span[needed].to_numpy(float))
                diffs = {r: np.diff(logs[:, i]) for i, r in enumerate(needed)}
                s = span["S"].to_numpy(float)
                a_vec = 1.0 - 0.5 * (s[1:] + s[:-1])
                a_eff = float(a_vec.mean())
            else:
                diffs = {r: np.array([math.log(ends[r].iloc[1] / ends[r].iloc[0])]) for r in needed}
                if alpha is not None:
                    a_eff = float(alpha)
                else:
                    s = span["S"].dropna()
                    if s.empty:
                        a_eff = DEFAULT_ALPHA
                        defaulted.append(f"{iso}/{label}")
                    else:
                        a_eff = float(1.0 - s.mean())
                a_vec = np.array([a_eff])
            contrib = _contributions(diffs, a_vec, years, per_worker, use_h)
            identity_err = max(identity_err, abs(sum(v for k, v in contrib.items() if k != total_key) - contrib[total_key]))
            if units == "percent":
                contrib = _to_percent(contrib, total_key)
            alpha_used.setdefault(iso, {})[label] = a_eff

            # alpha sensitivity (constant alpha, start-to-end differences)
            end_diffs = {r: np.array([math.log(ends[r].iloc[1] / ends[r].iloc[0])]) for r in needed}
            band: dict[str, list[float]] = {}
            for a in sens:
                c = _contributions(end_diffs, np.array([a]), years, per_worker, use_h)
                if units == "percent":
                    c = _to_percent(c, total_key)
                for comp, v in c.items():
                    band.setdefault(comp, []).append(v)
                    sens_records.append({"iso3": iso, "period": label, "alpha": a, "component": comp, "value": v})

            status = combine_status(st.reindex(range(y0, y1 + 1))[needed].to_numpy().ravel().tolist())
            src, dset = pan.source[iso]
            total = contrib[total_key]
            for comp, v in contrib.items():
                rows.append({
                    "source": src, "dataset": dset, "indicator": f"{prefix}.{comp}",
                    "indicator_label": COMPONENT_LABELS[comp], "iso3": iso, "country": pan.country[iso],
                    "period": label, "value": v, "unit": _UNITS[units], "obs_status": status,
                    "component": comp, "alpha": a_eff, "start": str(y0), "end": str(y1), "years": years,
                    "share_of_growth": 100.0 * v / total if total != 0 else np.nan,
                    "band_min": min(band[comp]) if comp in band else np.nan,
                    "band_max": max(band[comp]) if comp in band else np.nan,
                })
    extra = ["component", "alpha", "start", "end", "years", "share_of_growth", "band_min", "band_max"]
    data = pd.DataFrame(rows, columns=list(COLUMNS) + extra)
    if data.empty:
        raise ValueError(f"growth_accounting: no country/period had the data needed; skipped: {_limited(skipped)}")
    order = {c: i for i, c in enumerate(["output", "output_per_worker", "capital", "capital_deepening", "labour",
                                          "human_capital", "tfp"])}
    win_order = {lab: i for i, (lab, _, _) in enumerate(windows)}
    data = data.assign(_w=data["period"].map(win_order), _c=data["component"].map(order))
    data = data.sort_values(["iso3", "_w", "_c"], kind="mergesort").drop(columns=["_w", "_c"]).reset_index(drop=True)
    data["value"] = data["value"].astype(float)

    caveats: list[Caveat] = [Caveat(
        "growth_accounting_residual", "info",
        "TFP is the residual: it absorbs measurement error, capacity utilisation, factor quality not captured by "
        "hc, and any error in the assumed factor shares (see the alpha band).",
        {"capital": capital, "labour": labour, "human_capital": human_capital})]
    caveats += pan.caveats
    if alpha is None and labour_share:
        caveats.append(Caveat(
            "alpha_from_labour_share", "info",
            f"alpha = 1 - {labour_share}. PWT labour shares are partly imputed where national accounts lack labour "
            "compensation data (Feenstra, Inklaar and Timmer 2015); compare with the alpha band.",
            {"labour_share": labour_share, "weights": weights}))
    if defaulted:
        caveats.append(Caveat("alpha_default", "warn",
                              f"No labour share for {len(defaulted)} country-periods; alpha = 1/3 was used.",
                              {"groups": _limited(defaulted), "n": len(defaulted), "alpha": DEFAULT_ALPHA}))
    if skipped:
        caveats.append(Caveat("growth_accounting_skipped", "warn",
                              f"{len(skipped)} country-periods were skipped for missing data.",
                              {"skipped": _limited(skipped), "n": len(skipped)}))
    if pan.absent:
        caveats.append(Caveat("countries_missing", "warn", f"No data in the panel for {pan.absent}.",
                              {"iso3": pan.absent}))
    if capital == "rnna":
        caveats.append(Caveat("capital_stock_not_services", "info",
                              "Capital is the PWT capital stock (rnna); PWT's own TFP (rtfpna) uses capital services "
                              "(rkna), so TFP will differ from rtfpna.", {}))
    all_status = [s for iso, st in pan.status.items() for s in st[needed].to_numpy().ravel().tolist()
                  if isinstance(s, str)]
    caveats += _status_caveats(all_status, "growth_accounting")

    params = {
        "periods": {lab: [str(a), str(b)] for lab, a, b in windows}, "alpha": alpha, "iso3": iso3,
        "output": output, "capital": capital, "labour": labour, "human_capital": human_capital,
        "labour_share": labour_share if alpha is None else None, "weights": weights, "per_worker": per_worker,
        "units": units, "sensitivity": sens, "on_ppp_mix": on_ppp_mix,
    }
    meta = {"growth_accounting": {
        "scheme": "per_worker" if per_worker else "aggregate",
        "equation": ("dln(Y/L) = alpha*dln(K/L) + (1-alpha)*dln(hc) + dln(TFP)" if per_worker else
                     "dln(Y) = alpha*dln(K) + (1-alpha)*dln(L) + (1-alpha)*dln(hc) + dln(TFP)")
        if use_h else ("dln(Y/L) = alpha*dln(K/L) + dln(TFP)" if per_worker else
                       "dln(Y) = alpha*dln(K) + (1-alpha)*dln(L) + dln(TFP)"),
        "units": _UNITS[units], "weights": weights if alpha is None else "constant",
        "alpha": alpha_used, "variables": {k: v for k, v in roles.items() if v},
        "input_units": pan.units, "identity_max_abs_error": identity_err, "skipped": skipped,
    }, "alpha_sensitivity": sens_records}
    return _finish([pan.result], data, "growth_accounting", params, module="growth_accounting",
                   caveats=caveats, meta=meta)


# ---------------------------------------------------------------------------
# perpetual_inventory
# ---------------------------------------------------------------------------
def _per_country(value: Any, iso: str, name: str) -> Any:
    if isinstance(value, Mapping):
        if iso not in value:
            raise ValueError(f"{name} has no entry for {iso}")
        return value[iso]
    return value


def _delta_path(delta: float | Mapping[str, float] | Frame, iso: str, years: list[int]) -> np.ndarray:
    if isinstance(delta, (Result, pd.DataFrame)):
        d = _as_result(delta, "delta").data
        d = d[d["iso3"] == iso].copy()
        if d["indicator"].nunique() > 1:
            raise ValueError("delta frame must hold a single indicator")
        s = pd.Series(d["value"].to_numpy(float), index=d["period"].astype(int)).reindex(years)
        if s.isna().any():
            raise ValueError(f"delta frame lacks values for {iso} in {[y for y, v in s.items() if pd.isna(v)]}")
        out = s.to_numpy(float)
    else:
        out = np.full(len(years), float(_per_country(delta, iso, "delta")))
    if ((out < 0) | (out >= 1)).any():
        raise ValueError(f"depreciation rates must be in [0, 1); got {out.min():g}..{out.max():g} for {iso}")
    return out


def perpetual_inventory(
    investment: Frame,
    delta: float | Mapping[str, float] | Frame,
    initial: Literal["steady_state"] | float | Mapping[str, float] = "steady_state",
    g: float | Mapping[str, float] | None = None,
    *,
    g_years: int = 10,
    timing: Literal["current", "lagged"] = "current",
    indicator: str | None = None,
    on_ppp_mix: PPPPolicy = "raise",
) -> Result:
    """Capital stock by the perpetual inventory method.

    ``timing="current"`` (PWT convention): ``K_t = (1 - delta_t) K_{t-1} + I_t``;
    ``timing="lagged"``: ``K_t = (1 - delta_t) K_{t-1} + I_{t-1}``.

    Initial stock in the first year ``t0`` of each country's series:

    * ``"steady_state"``: the stock that grows at rate ``g`` with constant
      investment growth, ``K_t0 = I_t0 (1 + g) / (g + delta)`` (current timing)
      or ``I_t0 / (g + delta)`` (lagged). ``g=None`` uses the geometric average
      growth of investment over the first ``g_years`` years (Harberger-style);
    * a number (or ``{iso3: number}``): ``K_t0`` itself.

    ``investment`` is one indicator of real (constant-price) investment, e.g.
    WDI ``NE.GDI.FTOT.KN``, with consecutive annual observations (leading and
    trailing gaps are trimmed; interior gaps raise). ``delta`` is a rate in
    [0, 1): one number, ``{iso3: rate}``, or a frame of rates by country-year
    (PWT ``delta``). Output: canonical frame, indicator ``"<code>|pim"`` (or
    ``indicator``), in the investment's unit; ``meta["pim"]`` records K0, g and
    delta per country.

    The stock accumulates investment levels, so each country's investment
    series must keep one unit: different PPP bases raise ``PPPMixError``
    (``on_ppp_mix="raise"``) or attach a ``ppp_base_mix`` block caveat
    (``"block"``), and any other change of unit label attaches a
    ``mixed_units`` warning (the stock is then labelled with every label, joined
    by ``"; "``).
    """
    if timing not in ("current", "lagged"):
        raise ValueError("timing must be 'current' or 'lagged'")
    if g_years < 1:
        raise ValueError("g_years must be >= 1")
    res = _as_result(investment, "investment")
    df = res.data.dropna(subset=["value"])
    if df["indicator"].nunique() != 1:
        raise ValueError(f"investment must hold exactly one indicator, found {sorted(df['indicator'].unique())}")
    if df.duplicated(subset=["iso3", "period"]).any():
        raise ValueError("investment has several observations per country-year; filter to one series")
    if {parse_period(p).freq for p in df["period"].unique()} - {"A"}:
        raise ValueError("perpetual_inventory needs annual periods")
    unit_caveats = _ppp_check_groups(df, ["iso3"], "perpetual_inventory", on_ppp_mix)
    unit_caveats += _mixed_units_caveat(
        df, ["iso3"], "perpetual_inventory",
        "perpetual_inventory: a country's investment series changes unit label (editions or base years); the stock "
        "adds up levels measured on different bases. Use one series, or splice them first (harmonize.splice).")
    inv_code = df["indicator"].iloc[0]
    inv_label = next((lab for lab in df["indicator_label"] if lab), inv_code)
    out_code = indicator or f"{inv_code}|pim"

    rows, pim_meta, caveats = [], {}, list(unit_caveats)
    for iso, s in df.groupby("iso3", sort=True):
        s = s.assign(year=s["period"].astype(int)).sort_values("year")
        years = s["year"].tolist()
        if years != list(range(years[0], years[-1] + 1)):
            missing = sorted(set(range(years[0], years[-1] + 1)) - set(years))
            raise ValueError(f"investment for {iso} has interior gaps {missing}; fill or trim the series first")
        inv = s["value"].to_numpy(float)
        stat = s["obs_status"].tolist()
        d = _delta_path(delta, iso, years)
        n = len(inv)
        if isinstance(initial, str):
            if initial != "steady_state":
                raise ValueError("initial must be 'steady_state', a number or {iso3: number}")
            g_i = _per_country(g, iso, "g") if g is not None else None
            if g_i is None:
                m = min(g_years, n - 1)
                if m == 0:
                    g_i = 0.0
                    caveats.append(Caveat("pim_growth_assumed_zero", "warn",
                                          f"{iso}: a single investment year; g = 0 assumed.", {"iso3": iso}))
                else:
                    if inv[0] <= 0 or inv[m] <= 0:
                        raise ValueError(f"{iso}: investment must be positive to estimate g")
                    g_i = (inv[m] / inv[0]) ** (1.0 / m) - 1.0
            g_i = float(g_i)
            if g_i + d[0] <= 0:
                raise ValueError(f"{iso}: g + delta must be positive for a steady-state initial stock")
            k0 = inv[0] * (1.0 + g_i) / (g_i + d[0]) if timing == "current" else inv[0] / (g_i + d[0])
            init_kind = "steady_state"
        else:
            k0 = float(_per_country(initial, iso, "initial"))
            g_i = None
            init_kind = "value"
        k = np.empty(n)
        k[0] = k0
        status = [stat[0]]
        for t in range(1, n):
            add = inv[t] if timing == "current" else inv[t - 1]
            k[t] = (1.0 - d[t]) * k[t - 1] + add
            status.append(combine_status([status[-1], stat[t] if timing == "current" else stat[t - 1]]))
        country = next((c for c in s["country"] if c), "")
        unit = "; ".join(sorted(set(s["unit"]) - {""}))  # one label unless flagged by mixed_units
        for i, y in enumerate(years):
            rows.append({"source": s["source"].iloc[0], "dataset": s["dataset"].iloc[0], "indicator": out_code,
                         "indicator_label": f"Capital stock, perpetual inventory ({inv_label})", "iso3": iso,
                         "country": country, "period": str(y), "value": float(k[i]), "unit": unit,
                         "obs_status": status[i]})
        half_life = math.log(0.5) / math.log(1.0 - float(d.mean())) if d.mean() > 0 else math.inf
        pim_meta[iso] = {"K0": float(k0), "first_year": years[0], "initial": init_kind, "g": g_i,
                         "delta_first": float(d[0]), "delta_mean": float(d.mean()), "n_years": n,
                         "initial_weight_half_life_years": half_life if math.isfinite(half_life) else None}
    data = pd.DataFrame(rows, columns=list(COLUMNS))
    data["value"] = data["value"].astype(float)

    caveats.append(Caveat(
        "pim_initial_condition", "info",
        "The initial capital stock is an assumption; its weight in K_t decays by (1 - delta) a year, so early years "
        "are sensitive to it (half-life in meta['pim']).",
        {"initial": initial if not isinstance(initial, Mapping) else "per-country values",
         "timing": timing, "g_years": g_years}))
    if any("current" in str(u).lower() for u in df["unit"].unique()):
        caveats.append(Caveat("pim_current_prices", "warn",
                              "Investment looks like a current-price series; use constant prices for a real stock.",
                              {"units": sorted(df["unit"].unique())}))
    caveats += _status_caveats(df["obs_status"], "perpetual_inventory")
    params = {"delta": delta if not isinstance(delta, (Result, pd.DataFrame)) else "frame",
              "initial": initial, "g": g, "g_years": g_years, "timing": timing, "indicator": out_code,
              "on_ppp_mix": on_ppp_mix}
    return _finish([res] + ([_as_result(delta, "delta")] if isinstance(delta, (Result, pd.DataFrame)) else []),
                   data, "perpetual_inventory", params, module="growth_accounting", caveats=caveats,
                   meta={"pim": pim_meta})


# ---------------------------------------------------------------------------
# potential_output
# ---------------------------------------------------------------------------
_POTENTIAL = {
    "potential_output": ("potential.output", "Potential output (HP-filter production function approximation)"),
    "potential_growth": ("potential.growth", "Potential output growth"),
    "output_gap": ("potential.output_gap", "Output gap (% of potential, log approximation)"),
    "actual_growth": ("potential.actual_growth", "Actual output growth"),
    "capital": ("potential.capital", "Contribution of capital to potential growth"),
    "labour": ("potential.labour", "Contribution of trend employment to potential growth"),
    "human_capital": ("potential.human_capital", "Contribution of human capital to potential growth"),
    "tfp": ("potential.tfp", "Contribution of trend TFP to potential growth"),
}


def potential_output(
    panel: Frame | Sequence[Frame],
    alpha: float | None = None,
    *,
    iso3: str | Sequence[str] | None = None,
    output: str = "rgdpna",
    capital: str = "rkna",
    labour: str = "emp",
    human_capital: str | None = "hc",
    labour_share: str | None = "labsh",
    lamb: float = 100.0,
    smooth_human_capital: bool = False,
    start: str | None = None,
    end: str | None = None,
    min_years: int = 8,
    on_ppp_mix: PPPPolicy = "raise",
) -> Result:
    """Production-function potential output from HP-filtered TFP and employment (an APPROXIMATION).

    With a constant capital share ``alpha`` (``None``: ``1 - mean(labour
    share)`` over the sample, else 1/3 with a warning)::

        ln A_t  = ln Y_t - alpha ln K_t - (1-alpha)(ln L_t + ln hc_t)
        ln Y*_t = HP(ln A)_t + alpha ln K_t + (1-alpha)(HP(ln L)_t + ln hc*_t)

    where ``HP`` is the Hodrick-Prescott trend
    (:func:`statsmodels.tsa.filters.hp_filter.hpfilter`, ``lamb`` = 100 by
    default for annual data; Ravn-Uhlig suggest 6.25), capital enters at its
    actual level and ``hc*`` is actual ``hc`` (or its HP trend with
    ``smooth_human_capital=True``).

    This is **not** the World Bank MFMod potential output (which uses its own
    trend employment and TFP assumptions); HP trends are unreliable at the
    sample end-points. Each country needs ``min_years`` consecutive years with
    every input; countries without them are skipped with a caveat.

    Output (canonical frame, ``component`` column, one row per country-year):
    ``potential.output`` (level, output's unit), ``potential.growth``,
    ``potential.actual_growth`` (log points), ``potential.output_gap`` (100 x
    (ln Y - ln Y*)), and contributions to potential growth
    ``potential.capital``, ``potential.labour``, ``potential.human_capital``,
    ``potential.tfp`` that add up exactly to ``potential.growth``. Average them
    over windows with :func:`cgjrkit.methods.harmonize.period_average` for a
    Kenya Fig 5-style chart.

    Each input series must keep one unit over the sample: PPP bases and unit
    labels are checked as in :func:`growth_accounting` (``on_ppp_mix``).
    """
    from statsmodels.tsa.filters.hp_filter import hpfilter

    if alpha is not None and not 0 < float(alpha) < 1:
        raise ValueError("alpha must be between 0 and 1")
    if lamb <= 0:
        raise ValueError("lamb must be positive")
    if min_years < 3:
        raise ValueError("min_years must be at least 3")
    roles = {"Y": output, "K": capital, "L": labour, "H": human_capital,
             "S": labour_share if alpha is None else None}
    pan = _load_panel(panel, roles, iso3, "potential_output", on_ppp_mix)
    use_h = human_capital is not None
    needed = ["Y", "K", "L"] + (["H"] if use_h else [])
    y_lo = parse_period(start).year if start else None
    y_hi = parse_period(end).year if end else None

    rows, skipped, defaulted, alphas = [], [], [], {}
    for iso, w in pan.values.items():
        w = w.loc[(w.index >= (y_lo if y_lo is not None else w.index.min()))
                  & (w.index <= (y_hi if y_hi is not None else w.index.max()))]
        ok = w[needed].notna().all(axis=1) & (w[needed] > 0).all(axis=1)
        sample = w.loc[ok]
        yrs = sample.index.tolist()
        if len(yrs) < min_years:
            skipped.append({"iso3": iso, "reason": f"{len(yrs)} usable years (< {min_years})"})
            continue
        if yrs != list(range(yrs[0], yrs[-1] + 1)):
            skipped.append({"iso3": iso, "reason": "gaps inside the sample; set start/end to a gap-free span"})
            continue
        if alpha is not None:
            a = float(alpha)
        else:
            s = sample["S"].dropna()
            if s.empty:
                a = DEFAULT_ALPHA
                defaulted.append(iso)
            else:
                a = float(1.0 - s.mean())
        alphas[iso] = a
        lnY, lnK, lnL = (np.log(sample[r].to_numpy(float)) for r in ("Y", "K", "L"))
        lnH = np.log(sample["H"].to_numpy(float)) if use_h else np.zeros_like(lnY)
        lnA = lnY - a * lnK - (1 - a) * (lnL + lnH)
        lnA_t = np.asarray(hpfilter(lnA, lamb=lamb)[1], dtype=float)
        lnL_t = np.asarray(hpfilter(lnL, lamb=lamb)[1], dtype=float)
        lnH_t = np.asarray(hpfilter(lnH, lamb=lamb)[1], dtype=float) if (use_h and smooth_human_capital) else lnH
        lnYp = lnA_t + a * lnK + (1 - a) * (lnL_t + lnH_t)
        series: dict[str, np.ndarray] = {
            "potential_output": np.exp(lnYp),
            "output_gap": 100.0 * (lnY - lnYp),
            "potential_growth": np.r_[np.nan, 100.0 * np.diff(lnYp)],
            "actual_growth": np.r_[np.nan, 100.0 * np.diff(lnY)],
            "capital": np.r_[np.nan, 100.0 * a * np.diff(lnK)],
            "labour": np.r_[np.nan, 100.0 * (1 - a) * np.diff(lnL_t)],
            "tfp": np.r_[np.nan, 100.0 * np.diff(lnA_t)],
        }
        if use_h:
            series["human_capital"] = np.r_[np.nan, 100.0 * (1 - a) * np.diff(lnH_t)]
        st = pan.status[iso].reindex(yrs)
        src, dset = pan.source[iso]
        for comp in _POTENTIAL:
            if comp not in series:
                continue
            code, label = _POTENTIAL[comp]
            unit = (pan.country_units.get(iso, {}).get("Y", "") if comp == "potential_output" else
                    "% of potential output" if comp == "output_gap" else "log points (100 x change in ln)")
            for i, y in enumerate(yrs):
                v = series[comp][i]
                if not np.isfinite(v):
                    continue
                stat_years = [y] if i == 0 or comp in ("potential_output", "output_gap") else [y - 1, y]
                rows.append({"source": src, "dataset": dset, "indicator": code, "indicator_label": label,
                             "iso3": iso, "country": pan.country[iso], "period": str(y), "value": float(v),
                             "unit": unit,
                             "obs_status": combine_status(st.reindex(stat_years)[needed].to_numpy().ravel().tolist()),
                             "component": comp, "alpha": a})
    data = pd.DataFrame(rows, columns=list(COLUMNS) + ["component", "alpha"])
    if data.empty:
        raise ValueError(f"potential_output: no country had enough data; skipped: {_limited(skipped)}")
    data = data.sort_values(["iso3", "indicator", "period"], kind="mergesort").reset_index(drop=True)

    caveats = [Caveat(
        "potential_output_approximation", "warn",
        f"Approximation: Cobb-Douglas potential output with HP-filtered (lambda={lamb:g}) TFP and employment and "
        "actual capital. Not the World Bank MFMod potential output; HP trends are biased at the sample end-points "
        "(last 3-5 years), so recent potential growth is fragile.",
        {"lamb": lamb, "alpha": alphas, "smooth_human_capital": smooth_human_capital})]
    caveats += pan.caveats
    if defaulted:
        caveats.append(Caveat("alpha_default", "warn", f"No labour share for {defaulted}; alpha = 1/3 was used.",
                              {"countries": defaulted, "alpha": DEFAULT_ALPHA}))
    if pan.absent:
        caveats.append(Caveat("countries_missing", "warn", f"No data in the panel for {pan.absent}.",
                              {"iso3": pan.absent}))
    if skipped:
        caveats.append(Caveat("potential_output_skipped", "warn", f"{len(skipped)} countries were skipped.",
                              {"skipped": _limited(skipped)}))
    all_status = [s for st in pan.status.values() for s in st[needed].to_numpy().ravel().tolist() if isinstance(s, str)]
    caveats += _status_caveats(all_status, "potential_output")
    params = {"alpha": alpha, "iso3": iso3, "output": output, "capital": capital, "labour": labour,
              "human_capital": human_capital, "labour_share": labour_share if alpha is None else None,
              "lamb": lamb, "smooth_human_capital": smooth_human_capital, "start": start, "end": end,
              "min_years": min_years, "on_ppp_mix": on_ppp_mix}
    meta = {"potential_output": {"alpha": alphas, "lamb": lamb, "skipped": skipped,
                                 "method": "HP-filter production function (approximation, not MFMod)"}}
    return _finish([pan.result], data, "potential_output", params, module="growth_accounting",
                   caveats=caveats, meta=_clean(meta))
