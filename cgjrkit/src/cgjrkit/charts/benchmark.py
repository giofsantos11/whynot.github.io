"""Cross-country benchmarking charts (archetypes G03, S01, S04, F05, X05, X06, R01).

=====================  ========================================================
``scatter_vs_income``  countries against (log) GDP per capita with the fitted
                       curve and confidence band from
                       :func:`cgjrkit.methods.benchmark.income_conditional_fit`,
                       the focus country highlighted, peers coloured, labels,
                       a connected focus trajectory over periods and optional
                       bubble sizes (Kenya Figs 14 and 22; Indonesia Fig 2.1)
``kde_plot``           kernel densities per period with the focus country's
                       value and percentile marked (Kenya Fig 15)
``slope_rank``         slope chart of values, ranks or percentiles between two
                       or more periods, focus and peers highlighted
=====================  ========================================================

Colours follow :mod:`cgjrkit.charts.style`: focus ``style.FOCUS``, peers the
rest of ``style.PALETTE`` in fixed order, other countries ``style.GREY`` (or a
lighter context grey), the curve a dark neutral and its confidence band
``style.BAND``. The plotted data travel with the chart as one dataset with a
``layer`` column, so :func:`cgjrkit.charts.render.save_chart` writes them to CSV.
"""

from __future__ import annotations

import math
from typing import Any, Literal, Mapping, Sequence

import altair as alt
import numpy as np
import pandas as pd

from cgjrkit.charts import style
from cgjrkit.charts._common import (
    AGG_DASHES,
    AGG_GREYS,
    CONTEXT,
    DARK,
    PEER_COLORS,
    SOLID,
    TEXT,
    BlockedResultError,
    FrameLike,
    add_footer_lines,
    as_frame,
    assign_roles,
    carries_provenance,
    caveat_notes,
    check_unique,
    data_source_line,
    fmt_value,
    is_aggregate,
    known_aggregates,
    log_ticks,
    merge_usermeta,
    require_columns,
    single_label,
    single_unit,
    sort_periods,
    title_kw,
)

__all__ = ["scatter_vs_income", "kde_plot", "slope_rank", "ordinal"]

_CHART_COLS = ["role", "iso3", "country", "period", "x", "y", "fitted", "residual", "ci_lower", "ci_upper",
               "in_fit", "is_focus", "exclusion_reason", "obs_status"]


def ordinal(n: int) -> str:
    """``1 -> "1st"``, ``22 -> "22nd"``, ``13 -> "13th"``."""
    n = int(n)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


# ---------------------------------------------------------------------------
# scatter_vs_income
# ---------------------------------------------------------------------------
def _axis_title(meta_fit: Mapping[str, Any], which: str) -> str:
    lab = str(meta_fit.get(f"{which}_label") or "")
    unit = str(meta_fit.get(f"{which}_unit") or "")
    if lab and unit and unit.lower() not in lab.lower():
        return f"{lab} ({unit})"
    return lab or unit


def _plain_scatter(x_frame: FrameLike, y_frame: FrameLike, period: str | tuple[str, str] | None,
                   focus: str | None, trajectory: bool | Sequence[str]) -> tuple[pd.DataFrame, dict]:
    """Chart frame (same columns as ``income_conditional_fit``) for a scatter without a fit."""
    xdf, xm = as_frame(x_frame, "x_frame")
    ydf, ym = as_frame(y_frame, "y_frame")
    for name, df in (("x_frame", xdf), ("y_frame", ydf)):
        require_columns(df, ["iso3", "period", "value"], name)
        if "indicator" in df.columns and df["indicator"].astype(str).nunique() > 1:
            raise ValueError(f"{name} holds several indicators; filter to one")
        df["period"] = df["period"].astype(str)
        df["iso3"] = df["iso3"].astype(str)
    if period is None:
        xps, yps = sorted(set(xdf["period"])), sorted(set(ydf["period"]))
        if len(xps) != 1 or len(yps) != 1:
            raise ValueError("pass period= (the frames hold several periods)")
        xp, yp = xps[0], yps[0]
    else:
        xp, yp = (period if isinstance(period, (tuple, list)) else (period, period))
        xp, yp = str(xp), str(yp)
    known = known_aggregates(xm) | known_aggregates(ym)
    focus = focus.upper() if focus else None

    def pick(df: pd.DataFrame, p: str) -> pd.DataFrame:
        keep = np.array([(not is_aggregate(c, known)) or c == focus for c in df["iso3"]], dtype=bool)
        sub = df[(df["period"] == p).to_numpy() & keep]
        check_unique(sub, ["iso3"], "scatter_vs_income")
        cols = ["iso3", "value"] + [c for c in ("country", "obs_status") if c in sub.columns]
        return sub[cols]

    m = pick(xdf, xp).merge(pick(ydf, yp), on="iso3", suffixes=("_x", "_y"))
    m = m.dropna(subset=["value_x", "value_y"]).sort_values("iso3", kind="mergesort")
    country = m["country_y"] if "country_y" in m.columns else (m["country_x"] if "country_x" in m.columns else m["iso3"])
    per = yp if xp == yp else f"{xp}|{yp}"
    cf = pd.DataFrame({"role": "country", "iso3": m["iso3"].to_numpy(), "country": country.to_numpy(),
                       "period": per, "x": m["value_x"].to_numpy(float), "y": m["value_y"].to_numpy(float)})
    rows = [cf]
    if focus and isinstance(trajectory, (list, tuple)):
        path = []
        for p in [str(p) for p in trajectory]:
            xv = xdf[(xdf["iso3"] == focus) & (xdf["period"] == p)]["value"]
            yv = ydf[(ydf["iso3"] == focus) & (ydf["period"] == p)]["value"]
            if len(xv) == 1 and len(yv) == 1 and np.isfinite(float(xv.iloc[0])) and np.isfinite(float(yv.iloc[0])):
                path.append({"role": "focus_path", "iso3": focus, "period": p, "x": float(xv.iloc[0]), "y": float(yv.iloc[0]),
                             "country": cf.loc[cf["iso3"] == focus, "country"].iloc[0] if (cf["iso3"] == focus).any() else focus})
        if path:
            rows.append(pd.DataFrame(path))
    out = pd.concat(rows, ignore_index=True)
    for col in ("fitted", "residual", "ci_lower", "ci_upper"):
        out[col] = np.nan
    out["in_fit"] = False
    out["is_focus"] = out["iso3"] == focus if focus else False
    out["exclusion_reason"] = ""
    out["obs_status"] = ""
    meta_fit = {
        "x_label": single_label(xdf), "x_unit": single_unit(xdf), "x_period": xp,
        "y_label": single_label(ydf), "y_unit": single_unit(ydf), "y_period": yp,
    }
    return out[_CHART_COLS], {"fit": meta_fit, "aggregates": sorted(known & (set(xdf["iso3"]) | set(ydf["iso3"])))}


@carries_provenance
def scatter_vs_income(
    data: FrameLike | tuple[FrameLike, FrameLike],
    focus: str | None = None,
    label_countries: Sequence[str] | bool = (),
    trajectory: bool | Sequence[str] = True,
    *,
    peers: Sequence[str] = (),
    curve: bool = True,
    ci: bool = False,
    period: str | tuple[str, str] | None = None,
    fit_kind: str | None = None,
    log_x: bool | None = None,
    size: Mapping[str, float] | FrameLike | None = None,
    size_title: str = "",
    label_field: Literal["iso3", "country"] = "iso3",
    x_title: str | None = None,
    y_title: str | None = None,
    title: str = "",
    subtitle: str = "",
    x_format: str = ",.0f",
    y_format: str = ",.0f",
    fit_note: bool = True,
    width: int = style.WIDTH,
    height: int = style.HEIGHT,
) -> alt.LayerChart:
    """Scatter of an outcome against income with fitted curve, focus, peers, labels and trajectory.

    Parameters
    ----------
    data
        The Result (or its ``data``) returned by
        :func:`cgjrkit.methods.benchmark.income_conditional_fit` (chart frame
        with a ``role`` column: ``country``, ``curve``, ``focus_path``), or a
        pair ``(x_frame, y_frame)`` of canonical frames. With a pair,
        ``fit_kind`` (``"linear"``, ``"quadratic"``, ``"cubic"``, ``"lowess"``)
        runs the fit first (its source line and caveats go to the footer);
        without it the chart has no curve.
    focus
        ISO3 of the report country (``style.FOCUS``, larger marker, named label).
    label_countries
        ISO3 codes to label (``label_field``: code or name), or ``True`` to
        label every country with its ISO3 code as many CGJR scatters do.
    trajectory
        ``True`` draws the fit's ``focus_path`` rows connected to the focus
        point in period order with period labels (Kenya Fig 22: 2011, 2015,
        2024). With frames, a list of periods builds that path.
    peers
        ISO3 codes drawn in peer palette colours and labelled.
    curve, ci
        Draw the fitted curve and its confidence band (parametric fits only).
    log_x
        Log x axis with 1-2-5 ticks; defaults to the fit's ``log_x`` (else True).
    size
        Bubble sizes: ``{iso3: value}`` or a canonical frame (one value per country).
    fit_note
        Footnote the fit (kind, n, R²) and hollow markers: every country left
        out of the fit (``exclusion_reason`` set) is drawn hollow, the focus
        and peers in their own colour.
    """
    fit_res = None
    if isinstance(data, (tuple, list)):
        if len(data) != 2:
            raise ValueError("data as a sequence must be (x_frame, y_frame)")
        if fit_kind:
            from cgjrkit.methods.benchmark import income_conditional_fit

            if period is None:
                raise ValueError("period= is required to fit")
            path = [str(p) for p in trajectory] if isinstance(trajectory, (list, tuple)) else None
            fit_res = income_conditional_fit(data[0], data[1], period, kind=fit_kind,
                                             log_x=True if log_x is None else log_x, focus=focus, focus_path=path)
            cf, meta = fit_res.data.copy(), dict(fit_res.meta)
        else:
            cf, meta = _plain_scatter(data[0], data[1], period, focus, trajectory)
    else:
        cf, meta = as_frame(data, "data")
        if "role" not in cf.columns:
            raise ValueError("data must be an income_conditional_fit result/chart frame (with a 'role' column) "
                             "or a pair (x_frame, y_frame)")
    require_columns(cf, ["role", "iso3", "x", "y"], "scatter data")
    for col in _CHART_COLS:
        if col not in cf.columns:
            cf[col] = np.nan if col in ("fitted", "residual", "ci_lower", "ci_upper") else ""
    cf["iso3"] = cf["iso3"].fillna("").astype(str)
    cf["country"] = cf["country"].fillna("").astype(str)
    cf["period"] = cf["period"].fillna("").astype(str)
    cf["exclusion_reason"] = cf["exclusion_reason"].fillna("").astype(str)
    fit_meta = dict(meta.get("fit") or {})
    log_x = bool(fit_meta.get("log_x", True)) if log_x is None else bool(log_x)
    log_y = bool(fit_meta.get("log_y", False))
    focus = focus.upper() if focus else None
    if focus is None and "is_focus" in cf.columns:
        fc = cf.loc[(cf["role"] == "country") & (cf["is_focus"] == True), "iso3"]  # noqa: E712
        focus = str(fc.iloc[0]) if len(fc) else None
    peers = [str(p).upper() for p in peers]
    if len(peers) > len(PEER_COLORS):
        raise ValueError(f"at most {len(PEER_COLORS)} peers can take palette colours")
    countries = set(cf.loc[cf["role"] == "country", "iso3"])
    missing = [p for p in peers if p not in countries]
    if missing:
        raise ValueError(f"peers {missing} are not in the scatter (no x/y pair?)")

    def kind_of(row: pd.Series) -> str:
        if row["role"] == "curve":
            return "curve"
        if row["role"] == "focus_path":
            return "path"
        if row["iso3"] == focus:
            return "focus"
        if row["iso3"] in peers:
            return "peer"
        return "excluded" if row["exclusion_reason"] else "other"

    plot = cf[_CHART_COLS].copy()
    if trajectory is False:
        plot = plot[plot["role"] != "focus_path"].reset_index(drop=True)
    plot.insert(0, "layer", plot["role"])
    plot["kind"] = plot.apply(kind_of, axis=1)
    # countries left out of the fit are drawn hollow, whatever their kind (an excluded focus or peer keeps its colour)
    plot["hollow"] = ((plot["role"] == "country") & (plot["exclusion_reason"] != "")).astype(bool)
    if label_countries is True:
        to_label = countries
    else:
        to_label = {str(c).upper() for c in (label_countries or ())}
    to_label |= set(peers)

    def text_of(row: pd.Series) -> str:
        if row["kind"] == "focus":
            name = row["country"] or row["iso3"]
            show_period = trajectory is not False and (plot["kind"] == "path").any()
            return f"{name} {row['period']}" if show_period and row["period"] else name
        if row["kind"] == "path":
            return row["period"]
        if row["kind"] in ("peer", "other", "excluded") and row["iso3"] in to_label:
            return row["country"] if (label_field == "country" and row["country"]) else row["iso3"]
        return ""

    plot["label_text"] = plot.apply(text_of, axis=1)
    if size is not None:
        if isinstance(size, Mapping):
            smap = {str(k).upper(): float(v) for k, v in size.items()}
        else:
            sdf, _ = as_frame(size, "size")
            require_columns(sdf, ["iso3", "value"], "size")
            check_unique(sdf, ["iso3"], "size")
            smap = dict(zip(sdf["iso3"].astype(str), pd.to_numeric(sdf["value"], errors="coerce")))
        plot["size"] = [smap.get(c, np.nan) if r != "curve" else np.nan for c, r in zip(plot["iso3"], plot["role"])]
    peer_color = {p: PEER_COLORS[i] for i, p in enumerate(peers)}

    pts = plot[plot["role"] != "curve"]
    xs = [v for v in list(pts["x"]) + list(plot.loc[plot["role"] == "curve", "x"]) if np.isfinite(v)]
    if not xs:
        raise ValueError("scatter has no finite x values")
    lo, hi = min(xs), max(xs)
    if log_x:
        if lo <= 0:
            raise ValueError("log_x needs positive x values")
        dom = [lo / 1.15, hi * 1.15]
        x_scale = alt.Scale(type="log", domain=dom, nice=False)
        x_axis = alt.Axis(values=log_ticks(*dom), format=x_format, labelOverlap=True)
    else:
        pad = (hi - lo) * 0.05 or 1.0
        x_scale = alt.Scale(domain=[lo - pad, hi + pad], nice=False, zero=False)
        x_axis = alt.Axis(format=x_format, labelOverlap=True)
    if x_title is None:
        x_title = _axis_title(fit_meta, "x")
        if log_x and x_title:
            x_title += ", log scale"
    if y_title is None:
        y_title = _axis_title(fit_meta, "y")
    x_enc = alt.X("x:Q", scale=x_scale, axis=x_axis, title=x_title or None)
    y_scale = alt.Scale(zero=False, type="log" if log_y else "linear")

    def y_enc(field: str) -> alt.Y:
        return alt.Y(f"{field}:Q", scale=y_scale, axis=alt.Axis(format=y_format), title=y_title or None)

    tooltip = [alt.Tooltip("country:N", title="Country"), alt.Tooltip("iso3:N", title="ISO3"),
               alt.Tooltip("period:N", title="Period"), alt.Tooltip("x:Q", format=x_format, title=x_title or "x"),
               alt.Tooltip("y:Q", format=y_format, title=y_title or "y"),
               alt.Tooltip("fitted:Q", format=y_format, title="Fitted"), alt.Tooltip("residual:Q", format=".2f")]
    size_enc = (alt.Size("size:Q", scale=alt.Scale(range=[15, 900]),
                         legend=alt.Legend(title=size_title or None, orient="bottom", symbolType="circle"))
                if size is not None else alt.Undefined)
    base = alt.Chart()
    layers: list[alt.Chart] = []
    curve_rows = plot[plot["role"] == "curve"]
    if curve and ci and len(curve_rows) and curve_rows["ci_lower"].notna().any():
        layers.append(base.mark_area(color=style.BAND, opacity=0.9).encode(
            x=x_enc, y=y_enc("ci_lower"), y2="ci_upper:Q").transform_filter("datum.role === 'curve' && isValid(datum.ci_lower)"))
    if curve and len(curve_rows):
        layers.append(base.mark_line(color=DARK, strokeWidth=1.6).encode(
            x=x_enc, y=y_enc("fitted"), order="x:Q").transform_filter("datum.role === 'curve'"))
    layers.append(base.mark_circle(color=style.GREY, opacity=0.75, size=40).encode(
        x=x_enc, y=y_enc("y"), size=size_enc, tooltip=tooltip).transform_filter("datum.kind === 'other'"))
    if (plot["kind"] == "excluded").any():
        layers.append(base.mark_point(color=style.GREY, filled=False, shape="circle", size=40, strokeWidth=1.2).encode(
            x=x_enc, y=y_enc("y"), size=size_enc, tooltip=tooltip + [alt.Tooltip("exclusion_reason:N", title="Excluded")]
        ).transform_filter("datum.kind === 'excluded'"))
    excluded_tip = tooltip + [alt.Tooltip("exclusion_reason:N", title="Excluded")]
    if peers:
        dom = [p for p in peers]
        peer_enc = alt.Color("iso3:N", scale=alt.Scale(domain=dom, range=[peer_color[p] for p in dom]), legend=None)
        layers.append(base.mark_circle(opacity=0.95, size=70).encode(
            x=x_enc, y=y_enc("y"), size=size_enc, tooltip=tooltip, color=peer_enc,
        ).transform_filter("datum.kind === 'peer' && !datum.hollow"))
        if (plot["hollow"] & (plot["kind"] == "peer")).any():
            layers.append(base.mark_point(filled=False, shape="circle", size=70, strokeWidth=1.8).encode(
                x=x_enc, y=y_enc("y"), size=size_enc, tooltip=excluded_tip, color=peer_enc,
            ).transform_filter("datum.kind === 'peer' && datum.hollow"))
    has_path = trajectory is not False and (plot["kind"] == "path").any()
    if has_path:
        path_order = sort_periods(plot.loc[plot["kind"].isin(["path", "focus"]), "period"])
        rank = {p: i for i, p in enumerate(path_order)}
        plot["path_order"] = [rank.get(p, -1) if k in ("path", "focus") else np.nan for p, k in zip(plot["period"], plot["kind"])]
        layers.append(base.mark_line(color=style.FOCUS, strokeWidth=1.2, opacity=0.8).encode(
            x=x_enc, y=y_enc("y"), order="path_order:Q").transform_filter("datum.kind === 'path' || datum.kind === 'focus'"))
        layers.append(base.mark_circle(color=style.FOCUS, size=50, opacity=0.55).encode(
            x=x_enc, y=y_enc("y"), tooltip=tooltip).transform_filter("datum.kind === 'path'"))
    if focus and (plot["kind"] == "focus").any():
        layers.append(base.mark_circle(color=style.FOCUS, size=140, opacity=1, stroke="white", strokeWidth=1).encode(
            x=x_enc, y=y_enc("y"), size=size_enc, tooltip=tooltip).transform_filter("datum.kind === 'focus' && !datum.hollow"))
        if (plot["hollow"] & (plot["kind"] == "focus")).any():
            layers.append(base.mark_point(color=style.FOCUS, filled=False, shape="circle", size=140, strokeWidth=2.2,
                                          opacity=1).encode(x=x_enc, y=y_enc("y"), size=size_enc, tooltip=excluded_tip)
                          .transform_filter("datum.kind === 'focus' && datum.hollow"))
    if (plot["label_text"] != "").any():
        layers.append(base.mark_text(align="left", baseline="bottom", dx=5, dy=-3, fontSize=9, color=TEXT).encode(
            x=x_enc, y=y_enc("y"), text="label_text:N").transform_filter(
            "datum.label_text !== '' && datum.kind !== 'focus'"))
        layers.append(base.mark_text(align="left", baseline="bottom", dx=7, dy=-4, fontSize=11, fontWeight="bold",
                                     color=style.FOCUS).encode(x=x_enc, y=y_enc("y"), text="label_text:N")
                      .transform_filter("datum.kind === 'focus'"))
    chart = alt.layer(*layers, data=plot).properties(width=width, height=height, **title_kw(title, subtitle))

    notes: list[str] = []
    if fit_note and curve and fit_meta.get("kind") and len(curve_rows):
        r2 = fit_meta.get("r2")
        r2_txt = f", R² = {r2:.2f}" if isinstance(r2, (int, float)) and math.isfinite(r2) else ""
        on = "log x" if fit_meta.get("log_x") else "x"
        notes.append(f"Note: Line shows a {fit_meta['kind']} fit of y on {on} across {fit_meta.get('n', '?')} countries{r2_txt}"
                     + (f"; shaded band: {int(round(100 * fit_meta['ci']))}% confidence interval" if ci and fit_meta.get("ci") else "")
                     + ".")
    if fit_note and plot["hollow"].any():
        notes.append("Note: Hollow markers are left out of the fit.")
    source = ""
    if fit_res is not None:
        if fit_res.blocked:
            raise BlockedResultError("the fit result is BLOCKed: " + "; ".join(c.message for c in fit_res.caveats if c.severity == "block"))
        notes += caveat_notes(fit_res.caveats)
        source = data_source_line(fit_res)
    if notes or source:
        chart = add_footer_lines(chart, notes, source)
    return merge_usermeta(chart, cgjrkit_chart={
        "kind": "scatter_vs_income", "focus": focus, "peers": peers, "labelled": sorted(to_label),
        "colors": {**({focus: style.FOCUS} if focus else {}), **peer_color, "others": style.GREY, "curve": DARK},
        "log_x": log_x, "trajectory": bool(has_path), "fit": {k: fit_meta.get(k) for k in ("kind", "n", "r2", "equation") if k in fit_meta},
    })


# ---------------------------------------------------------------------------
# kde_plot
# ---------------------------------------------------------------------------
@carries_provenance
def kde_plot(
    density: FrameLike,
    focus: str | None = None,
    periods: Sequence[str] | None = None,
    *,
    rug: bool = False,
    focus_lines: bool = True,
    bw_method: str | float | None = None,
    x_title: str | None = None,
    y_title: str = "Density",
    title: str = "",
    subtitle: str = "",
    x_format: str = ",.0f",
    value_format: str = ",.1f",
    width: int = style.WIDTH,
    height: int = style.HEIGHT,
) -> alt.LayerChart:
    """Kernel densities across countries, one line per period, with the focus country marked.

    Parameters
    ----------
    density
        The Result (or data) of :func:`cgjrkit.methods.benchmark.kernel_density`
        (``role`` = ``density`` / ``country`` / ``focus``). A canonical frame
        without ``role`` is passed to ``kernel_density`` first (with
        ``periods``, ``focus`` and ``bw_method``).
    focus
        ISO3 whose value is drawn as a dashed vertical line per period,
        labelled with value and percentile (defaults to the ``focus`` rows).
    rug
        Tick marks at each country's value along the bottom.

    Periods are coloured with ``style.PALETTE`` in period order.
    """
    df, meta = as_frame(density, "density")
    computed = None
    if "role" not in df.columns:
        from cgjrkit.methods.benchmark import kernel_density

        require_columns(df, ["iso3", "period", "value"], "density")
        ps = list(periods) if periods else sort_periods(df["period"].astype(str))
        computed = kernel_density(density, ps, focus=focus, bw_method=bw_method)
        df, meta = computed.data.copy(), dict(computed.meta)
    require_columns(df, ["role", "period", "x", "density"], "density")
    df["period"] = df["period"].astype(str)
    df["iso3"] = df["iso3"].fillna("").astype(str) if "iso3" in df.columns else ""
    df["country"] = df["country"].fillna("").astype(str) if "country" in df.columns else ""
    ps = sort_periods(df.loc[df["role"] == "density", "period"])
    if periods:
        want = [str(p) for p in periods]
        missing = [p for p in want if p not in ps]
        if missing:
            raise ValueError(f"periods {missing} are not in the density frame")
        ps = sort_periods(want)
    if len(ps) > len(style.PALETTE):
        raise ValueError(f"at most {len(style.PALETTE)} periods")
    df = df[df["period"].isin(ps)].copy()
    focus = focus.upper() if focus else None
    if focus and not ((df["role"] == "focus") & (df["iso3"] == focus)).any():
        rows = df[(df["role"] == "country") & (df["iso3"] == focus)].copy()
        if rows.empty:
            raise ValueError(f"focus {focus} has no value in the density frame")
        df = pd.concat([df, rows.assign(role="focus")], ignore_index=True)
    if focus:
        df = df[(df["role"] != "focus") | (df["iso3"] == focus)]
    colors = dict(zip(ps, style.PALETTE))
    plot = df.copy()
    plot.insert(0, "layer", plot["role"])
    plot["label_text"] = ""
    plot["label_y"] = np.nan
    fmask = plot["role"] == "focus"
    for i, idx in enumerate(plot.index[fmask]):
        r = plot.loc[idx]
        pct = r.get("percentile")
        pct_txt = f" ({ordinal(round(float(pct)))} percentile)" if pct is not None and np.isfinite(pct) else ""
        name = r["country"] or r["iso3"]
        plot.loc[idx, "label_text"] = f"{name} {r['period']}: {fmt_value(r['x'], value_format)}{pct_txt}"
        plot.loc[idx, "label_y"] = 4 + 14 * i
    kde_meta = dict(meta.get("kde") or {})
    if x_title is None:
        lab, unit = str(kde_meta.get("label") or ""), str(kde_meta.get("unit") or "")
        x_title = f"{lab} ({unit})" if lab and unit and unit.lower() not in lab.lower() else (lab or unit)
    color = alt.Color("period:N", scale=alt.Scale(domain=ps, range=[colors[p] for p in ps]), sort=ps,
                      legend=alt.Legend(title=None, orient="bottom", symbolType="stroke", symbolStrokeWidth=2.5)
                      if len(ps) > 1 or focus else None)
    x_enc = alt.X("x:Q", title=x_title or None, axis=alt.Axis(format=x_format), scale=alt.Scale(zero=False, nice=False))
    base = alt.Chart()
    layers = [base.mark_line(strokeWidth=2).encode(
        x=x_enc, y=alt.Y("density:Q", title=y_title or None, axis=alt.Axis(format=".3~f")), color=color,
        order="x:Q", tooltip=[alt.Tooltip("period:N"), alt.Tooltip("x:Q", format=value_format), alt.Tooltip("density:Q", format=".4f")],
    ).transform_filter("datum.role === 'density'")]
    if rug:
        layers.append(base.mark_tick(thickness=1, size=8, opacity=0.6).encode(
            x=x_enc, y=alt.value(height - 4), color=color,
            tooltip=[alt.Tooltip("country:N"), alt.Tooltip("period:N"), alt.Tooltip("x:Q", format=value_format)],
        ).transform_filter("datum.role === 'country'"))
    if focus_lines and fmask.any():
        tt = [alt.Tooltip("country:N"), alt.Tooltip("period:N"), alt.Tooltip("x:Q", format=value_format, title="Value"),
              alt.Tooltip("percentile:Q", format=".0f")] if "percentile" in plot.columns else []
        layers.append(base.mark_rule(strokeDash=[4, 3], strokeWidth=1.6).encode(
            x=x_enc, color=color, tooltip=tt).transform_filter("datum.role === 'focus'"))
        layers.append(base.mark_text(align="left", baseline="top", dx=4, fontSize=10, color=TEXT).encode(
            x=x_enc, y=alt.Y("label_y:Q", scale=None), text="label_text:N").transform_filter("datum.role === 'focus'"))
    chart = alt.layer(*layers, data=plot).properties(width=width, height=height, **title_kw(title, subtitle))
    n_by = {p: int(v.get("n")) for p, v in (kde_meta.get("by_period") or {}).items() if isinstance(v, Mapping) and v.get("n") is not None}
    notes = []
    if n_by:
        ns = sorted(set(n_by.get(p) for p in ps if p in n_by))
        notes.append(f"Note: Gaussian kernel density across {ns[0] if len(ns) == 1 else ' / '.join(str(n_by[p]) for p in ps if p in n_by)} countries"
                     + ("; percentiles are the share of countries at or below the value." if fmask.any() else "."))
    source = ""
    if computed is not None:
        notes += caveat_notes(computed.caveats)
        source = data_source_line(computed)
    if notes or source:
        chart = add_footer_lines(chart, notes, source)
    return merge_usermeta(chart, cgjrkit_chart={"kind": "kde_plot", "focus": focus, "periods": ps, "colors": colors})


# ---------------------------------------------------------------------------
# slope_rank
# ---------------------------------------------------------------------------
@carries_provenance
def slope_rank(
    frame: FrameLike,
    periods: Sequence[str] | None = None,
    focus: str | None = None,
    peers: Sequence[str] | None = None,
    *,
    measure: Literal["value", "rank", "percentile"] = "value",
    descending: bool = True,
    balanced: bool = True,
    aggregates: Sequence[str] | None = None,
    show_others: bool = True,
    labels: bool = True,
    show_axis: bool = False,
    value_format: str | None = None,
    title: str = "",
    subtitle: str = "",
    y_title: str | None = None,
    width: int = 360,
    height: int = style.HEIGHT,
) -> alt.LayerChart:
    """Slope chart of values, ranks or percentiles between periods (two or more).

    Parameters
    ----------
    frame
        Canonical frame (or Result), one indicator, several countries.
    periods
        Periods to connect (default: the first and last in the frame).
    peers
        Highlighted countries in palette colours; ``None`` highlights none
        (every non-focus country is context). Other countries are drawn as
        thin light-grey lines when ``show_others``.
    measure
        ``"value"`` plots the values (aggregates drawn grey and dashed);
        ``"rank"`` ranks countries per period (1 = highest when
        ``descending``; ties share the best rank; axis reversed);
        ``"percentile"`` is the share of countries (0-100) with a value at
        or below the country's (the ``"weak"`` definition, as
        :func:`cgjrkit.methods.benchmark.percentile_rank`). Aggregates never
        enter ranks or percentiles.
    balanced
        Keep only countries with a value in every period, so ranks compare
        the same sample (Kenya Fig 15: 104 countries in 2001 and 2023).
    labels, show_axis
        End labels ("Kenya 48") carry the values, so the y axis is hidden
        unless ``show_axis``.
    """
    if measure not in ("value", "rank", "percentile"):
        raise ValueError("measure must be 'value', 'rank' or 'percentile'")
    df, meta = as_frame(frame, "frame")
    require_columns(df, ["iso3", "period", "value"], "frame")
    if "indicator" in df.columns and df["indicator"].astype(str).nunique() > 1:
        raise ValueError("slope_rank expects one indicator; filter the frame")
    df["iso3"] = df["iso3"].astype(str)
    df["period"] = df["period"].astype(str)
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    ps = [str(p) for p in periods] if periods else None
    allp = sort_periods(df["period"])
    if ps is None:
        if len(allp) < 2:
            raise ValueError("slope_rank needs at least two periods")
        ps = [allp[0], allp[-1]]
    if len(ps) < 2:
        raise ValueError("periods needs at least two entries")
    missing = [p for p in ps if p not in allp]
    if missing:
        raise ValueError(f"periods {missing} are not in the frame")
    ps = sort_periods(ps)
    df = df[df["period"].isin(ps)].dropna(subset=["value"])
    check_unique(df, ["iso3", "period"], "slope_rank")
    known = known_aggregates(meta)
    if aggregates is None:
        aggs = sorted({c for c in df["iso3"] if is_aggregate(c, known) and c != (focus or "").upper()})
    else:
        aggs = [str(a) for a in aggregates]
    countries = df[~df["iso3"].isin(aggs)]
    if balanced:
        have = countries.groupby("iso3")["period"].nunique()
        keep = set(have[have == len(ps)].index)
        dropped_unbalanced = sorted(set(countries["iso3"]) - keep)
        countries = countries[countries["iso3"].isin(keep)]
    else:
        dropped_unbalanced = []
    if measure == "value":
        agg_rows = df[df["iso3"].isin(aggs)]
        work = pd.concat([countries, agg_rows], ignore_index=True)
    else:
        work = countries.copy()
    if work.empty:
        raise ValueError("no countries left to plot")
    roles = assign_roles(work, "iso3", focus, peers if peers is not None else [], aggs if measure == "value" else [],
                         meta | {"aggregates": aggs}, None)
    if len(roles.peers) > len(PEER_COLORS):
        raise ValueError(f"at most {len(PEER_COLORS)} highlighted peers")
    roles.others = [c for c in sorted(set(work["iso3"])) if c not in roles.ordered] if show_others else []
    work = work[work["iso3"].isin(roles.ordered)].copy()

    n_by: dict[str, int] = {}
    work["y"] = work["value"]
    if measure != "value":
        parts = []
        for p, g in countries.groupby("period", sort=False):
            vals = g["value"].to_numpy(float)
            n_by[p] = int(len(vals))
            g = g.copy()
            if measure == "rank":
                g["y"] = g["value"].rank(ascending=not descending, method="min")
            else:
                g["y"] = [100.0 * float((vals <= v).sum()) / len(vals) for v in g["value"]]
            parts.append(g[["iso3", "period", "y"]])
        ranks = pd.concat(parts, ignore_index=True)
        work = work.drop(columns="y").merge(ranks, on=["iso3", "period"], how="left")
    fmt = value_format or {"value": ",.1f", "rank": "d", "percentile": ".0f"}[measure]
    order = {p: i for i, p in enumerate(ps)}
    rank_of = {c: i for i, c in enumerate(roles.ordered)}
    plot = pd.DataFrame({
        "layer": "slope", "iso3": work["iso3"].to_numpy(), "label": work["iso3"].map(roles.labels).to_numpy(),
        "role": [roles.role(c) for c in work["iso3"]], "period": work["period"].to_numpy(),
        "value": work["value"].to_numpy(float), "y": work["y"].to_numpy(float),
    })
    plot["n"] = plot["period"].map(n_by) if n_by else np.nan
    plot["_o"] = plot["iso3"].map(rank_of) * 1000 + plot["period"].map(order)
    plot = plot.sort_values("_o", kind="mergesort").drop(columns="_o").reset_index(drop=True)
    first, last = ps[0], ps[-1]
    plot["label_left"] = ""
    plot["label_right"] = ""
    hl = plot["role"].isin(["focus", "peer", "aggregate"])
    plot.loc[hl & (plot["period"] == first), "label_left"] = [
        f"{lab} {fmt_value(v, fmt)}" for lab, v in zip(plot.loc[hl & (plot["period"] == first), "label"], plot.loc[hl & (plot["period"] == first), "y"])]
    plot.loc[hl & (plot["period"] == last), "label_right"] = [
        f"{fmt_value(v, fmt)} {lab}" for lab, v in zip(plot.loc[hl & (plot["period"] == last), "label"], plot.loc[hl & (plot["period"] == last), "y"])]

    domain, colors, dashes = [], [], []
    if roles.focus:
        domain.append(roles.labels[roles.focus]); colors.append(style.FOCUS); dashes.append(SOLID)
    for i, p in enumerate(roles.peers):
        domain.append(roles.labels[p]); colors.append(PEER_COLORS[i]); dashes.append(SOLID)
    for i, a in enumerate(roles.aggregates):
        domain.append(roles.labels[a]); colors.append(AGG_GREYS[i % len(AGG_GREYS)]); dashes.append(AGG_DASHES[i % len(AGG_DASHES)])
    color = alt.Color("label:N", scale=alt.Scale(domain=domain or ["_"], range=colors or [CONTEXT]), legend=None)
    dash = alt.StrokeDash("label:N", scale=alt.Scale(domain=domain or ["_"], range=dashes or [SOLID]), legend=None)
    x_enc = alt.X("period:O", sort=ps, title=None, scale=alt.Scale(padding=0.15),
                  axis=alt.Axis(labelAngle=0, orient="top", domain=False, ticks=False, labelFontWeight="bold"))
    if measure == "rank":
        n_max = max(n_by.values()) if n_by else int(plot["y"].max())
        y_scale = alt.Scale(reverse=True, domain=[1, max(n_max, 2)], nice=False, zero=False)
        y_title = y_title if y_title is not None else ("Rank (1 = highest)" if descending else "Rank (1 = lowest)")
    elif measure == "percentile":
        y_scale = alt.Scale(domain=[0, 100], zero=True)
        y_title = y_title if y_title is not None else "Percentile"
    else:
        y_scale = alt.Scale(zero=False)
        y_title = y_title if y_title is not None else (single_unit(df) or single_label(df))
    y_axis = alt.Axis(format=fmt, grid=False, title=y_title or None) if (show_axis or not labels) else None
    y_enc = alt.Y("y:Q", scale=y_scale, axis=y_axis)
    tooltip = [alt.Tooltip("label:N", title="Country"), alt.Tooltip("period:N"),
               alt.Tooltip("value:Q", format=",.2f", title="Value"), alt.Tooltip("y:Q", format=fmt, title=measure.title())]
    base = alt.Chart()
    layers = []
    if roles.others:
        layers.append(base.mark_line(color=CONTEXT, strokeWidth=0.8, point=alt.OverlayMarkDef(size=10, color=CONTEXT)).encode(
            x=x_enc, y=y_enc, detail="iso3:N", tooltip=tooltip).transform_filter("datum.role === 'other'"))
    if roles.peers or roles.aggregates:
        layers.append(base.mark_line(strokeWidth=1.8, point=alt.OverlayMarkDef(size=30, filled=True)).encode(
            x=x_enc, y=y_enc, color=color, strokeDash=dash, detail="iso3:N", tooltip=tooltip
        ).transform_filter("datum.role === 'peer' || datum.role === 'aggregate'"))
    if roles.focus:
        layers.append(base.mark_line(strokeWidth=3, point=alt.OverlayMarkDef(size=60, filled=True)).encode(
            x=x_enc, y=y_enc, color=color, detail="iso3:N", tooltip=tooltip).transform_filter("datum.role === 'focus'"))
    if labels:
        layers.append(base.mark_text(align="right", dx=-8, fontSize=10, color=TEXT).encode(
            x=x_enc, y=y_enc, text="label_left:N").transform_filter("datum.label_left !== ''"))
        layers.append(base.mark_text(align="left", dx=8, fontSize=10, color=TEXT).encode(
            x=x_enc, y=y_enc, text="label_right:N").transform_filter("datum.label_right !== ''"))
    chart = alt.layer(*layers, data=plot).properties(width=width, height=height, **title_kw(title, subtitle))
    notes = []
    if measure != "value" and n_by:
        ns = [n_by[p] for p in ps if p in n_by]
        sample = (f"{ns[0]} countries" if len(set(ns)) == 1
                  else " and ".join(f"{n} ({p})" for p, n in zip(ps, ns)) + " countries")
        what = "Ranks" if measure == "rank" else "Percentiles"
        notes.append(f"Note: {what} among {sample}" + (" with data in every period shown." if balanced else " with data in each period."))
    if notes:
        chart = add_footer_lines(chart, notes)
    return merge_usermeta(chart, cgjrkit_chart={
        "kind": "slope_rank", "measure": measure, "periods": ps, "focus": roles.focus, "peers": roles.peers,
        "aggregates": roles.aggregates, "colors": dict(zip(domain, colors)), "n": n_by, "y_title": y_title,
        "dropped_unbalanced": dropped_unbalanced[:50],
    })
