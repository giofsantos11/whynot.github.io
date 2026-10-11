"""Stacked contribution charts (archetypes G04, G08, S05, X01, F01).

``stacked_contributions`` draws the components of a total as bars stacked
above zero (positive contributions) and below zero (negative ones), one bar
per period, window or country, with the total as a dot, a short horizontal
tick or a line, optional segment value labels and total labels (Kenya Fig 2
"g = 3.8%"), small multiples by country or window (``facet``), and an
identity check flagging bars whose components do not add up to the total
(Kenya Fig 2: the 2020 stacks net to about +1.2 pp while the label says 0.5).

Inputs are long frames: the canonical frame plus a component column, as
returned by :func:`cgjrkit.methods.growth_accounting.growth_accounting`
(``component`` = ``capital``, ``labour``, ``human_capital``, ``tfp`` and the
total ``output``) or :func:`cgjrkit.methods.structural_change.sector_contributions`
(``sector`` per contribution and ``total_growth``). Stack positions are
computed in Python (positives upward from zero and negatives downward, in
component order), so the plotted CSV holds the exact geometry.
"""

from __future__ import annotations

from typing import Literal, Mapping, Sequence

import altair as alt
import numpy as np
import pandas as pd

from cgjrkit.charts import style
from cgjrkit.charts._common import (
    DARK,
    RULE,
    TEXT,
    FrameLike,
    add_footer_lines,
    as_frame,
    carries_provenance,
    js_lookup,
    merge_usermeta,
    require_columns,
    single_unit,
    sort_periods,
    title_kw,
)

__all__ = ["stacked_contributions", "TOTAL_NAMES", "DEFAULT_COMPONENT_LABELS"]

#: Component names recognised as the total when ``total="auto"`` (first match wins). ``gdp_per_capita``
#: comes before ``output_per_worker``: in a jobs_shapley result output per worker is a component of GDP
#: per capita growth, while in a per-worker growth accounting it is the total (and no GDP per capita row exists).
TOTAL_NAMES = ("total", "Total", "TOTAL", "gdp_per_capita", "output", "output_per_worker", "total_growth", "gdp", "GDP")

#: Display labels used when ``component_labels`` does not name a component.
DEFAULT_COMPONENT_LABELS: dict[str, str] = {
    "capital": "Capital",
    "labour": "Labour",
    "labor": "Labor",
    "human_capital": "Human capital",
    "tfp": "TFP",
    "capital_deepening": "Capital deepening",
    "output": "Output growth",
    "output_per_worker": "Output per worker growth",
    "gdp_per_capita": "GDP per capita growth",
    "employment_rate": "Employment rate",
    "working_age_share": "Working-age share",
    "total_growth": "Total growth",
    "within": "Within-sector",
    "between": "Between-sector (structural change)",
    "static_between": "Static reallocation",
    "dynamic_between": "Dynamic reallocation",
    "residual": "Residual",
}

TOTAL_COLOR = DARK
# band-scale padding of the category axis (fractions of one step); the total tick is sized from them
_PAD_INNER = 0.35
_PAD_OUTER = 0.2
_BAR_SYMBOL = "M-1,-0.18L1,-0.18L1,0.18L-1,0.18Z"


def _ink_for(hex_color: str) -> str:
    """White or dark text, whichever reads better on ``hex_color`` (WCAG relative luminance)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return TEXT
    rgb = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    lum = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return "white" if lum < 0.4 else TEXT


@carries_provenance
def stacked_contributions(
    frame: FrameLike,
    total: str | None = "auto",
    *,
    x: str = "period",
    component: str = "component",
    value: str = "value",
    facet: str | None = None,
    component_order: Sequence[str] | None = None,
    component_labels: Mapping[str, str] | None = None,
    total_label: str | None = None,
    x_order: Sequence[str] | None = None,
    period_labels: Mapping[str, str] | None = None,
    total_mark: Literal["dot", "tick", "line"] | None = "dot",
    value_labels: bool = False,
    label_min: float = 0.0,
    value_format: str = ".1f",
    total_labels: bool | str = False,
    colors: Mapping[str, str] | None = None,
    horizontal: bool = False,
    check_identity: bool = True,
    tolerance: float = 0.05,
    flag_gaps: bool = True,
    title: str = "",
    subtitle: str = "",
    y_title: str | None = None,
    width: int | None = None,
    height: int = style.HEIGHT,
) -> alt.LayerChart | alt.FacetChart:
    """Stacked positive/negative contributions with a total marker.

    Parameters
    ----------
    frame
        Long frame (or Result) with columns ``x``, ``component`` and ``value``
        (and ``facet`` if given); one row per bar and component.
    total
        ``"auto"``: the first component named in :data:`TOTAL_NAMES`, else the
        sum of the components; a component name; ``"sum"`` to compute the
        sum; ``None`` for no total marker.
    x
        Column naming the bars (``"period"`` windows such as ``"2011-2019"``,
        or ``"country"`` for peers).
    facet
        Column for small multiples (e.g. ``"country"`` with ``x="period"``,
        or ``"period"`` with ``x="country"``).
    component_order, component_labels, colors
        Stack order (bottom-up from zero), display labels and colours
        (default ``style.PALETTE`` in order; at most 8 components).
    x_order
        Bar order (default: period order when ``x="period"``, else first appearance).
    period_labels
        Axis labels per bar, ``"\\n"`` for line breaks
        (``{"2001-2010": "2001-10\\ng = 3.8%"}``).
    total_mark
        ``"dot"`` (point), ``"tick"`` (short horizontal line across the bar),
        ``"line"`` (line through the totals) or ``None``.
    value_labels, label_min, value_format
        Print each segment's value inside it when ``|value| >= label_min``.
    total_labels
        ``True`` prints the total above each bar with ``value_format``; a
        Python format string such as ``"g = {:.1f}%"`` controls the text.
    check_identity, tolerance, flag_gaps
        Compare an explicit total with the sum of the components; gaps above
        ``tolerance`` go to ``usermeta["cgjrkit_identity"]`` and, with
        ``flag_gaps``, to a footer note.
    horizontal
        Bars along the y axis (categories on y).
    """
    df, meta = as_frame(frame, "frame")
    need = [x, component, value] + ([facet] if facet else [])
    require_columns(df, need, "frame")
    df = df.copy()
    for col in [x, component] + ([facet] if facet else []):
        df[col] = df[col].astype(str)
    df[value] = pd.to_numeric(df[value], errors="coerce").astype(float)
    keys = ([facet] if facet else []) + [x, component]
    dup = df.duplicated(subset=keys, keep=False)
    if dup.any():
        sample = df.loc[dup, keys].drop_duplicates().head(4).to_dict("records")
        extra = " (several countries? pass facet='country' or filter)" if "iso3" in df.columns and df["iso3"].nunique() > 1 and facet is None else ""
        raise ValueError(f"stacked_contributions: several values per {tuple(keys)}, e.g. {sample}{extra}")

    comps_all = list(dict.fromkeys(df[component]))
    if total == "auto":
        total_comp = next((t for t in TOTAL_NAMES if t in comps_all), None)
        total_mode = "component" if total_comp else "sum"
    elif total is None:
        total_comp, total_mode = None, "none"
    elif total == "sum":
        total_comp, total_mode = None, "sum"
    else:
        if str(total) not in comps_all:
            raise ValueError(f"total component {total!r} is not in {comps_all}")
        total_comp, total_mode = str(total), "component"
    comps = [c for c in comps_all if c != total_comp]
    if component_order is not None:
        order = [str(c) for c in component_order if str(c) in comps]
        unknown = [str(c) for c in component_order if str(c) not in comps and str(c) != total_comp]
        if unknown:
            raise ValueError(f"component_order names unknown components {unknown}")
        comps = order + [c for c in comps if c not in order]
    if not comps:
        raise ValueError("no components to stack")
    if len(comps) > len(style.PALETTE) and not colors:
        raise ValueError(f"{len(comps)} components but {len(style.PALETTE)} palette colours; group small ones into 'Other'")
    labels_map = {c: (component_labels or {}).get(c) or DEFAULT_COMPONENT_LABELS.get(c) or c for c in comps}
    t_label = total_label or ((component_labels or {}).get(total_comp) if total_comp else None) \
        or (DEFAULT_COMPONENT_LABELS.get(total_comp) if total_comp else None) or "Total"
    color_map = {labels_map[c]: (colors or {}).get(c) or style.PALETTE[i] for i, c in enumerate(comps)}

    xs = list(dict.fromkeys(df[x]))
    if x_order is not None:
        xs = [str(v) for v in x_order if str(v) in xs] + [v for v in xs if v not in [str(o) for o in x_order]]
    elif x == "period":
        xs = sort_periods(xs)
    facets = list(dict.fromkeys(df[facet])) if facet else [""]
    if facet == "period":
        facets = sort_periods(facets)

    seg_rows: list[dict] = []
    tot_rows: list[dict] = []
    gaps: list[dict] = []
    for f in facets:
        sub_f = df[df[facet] == f] if facet else df
        for xv in xs:
            sub = sub_f[sub_f[x] == xv]
            if sub.empty:
                continue
            vals = sub.set_index(component)[value]
            pos = neg = 0.0
            s = 0.0
            top = 0.0
            n_valid = 0
            for c in comps:
                v = vals.get(c, np.nan)
                if not np.isfinite(v):
                    continue
                n_valid += 1
                if v >= 0:
                    y0, y1 = pos, pos + v
                    pos = y1
                else:
                    y0, y1 = neg, neg + v
                    neg = y1
                s += v
                top = max(top, y1)
                seg_rows.append({"layer": "segment", "facet": f, "x": xv, "component": c, "label": labels_map[c],
                                 "value": float(v), "y0": float(y0), "y1": float(y1), "ymid": float((y0 + y1) / 2),
                                 "text_color": _ink_for(color_map[labels_map[c]])})
            if total_mode == "none" or n_valid == 0:
                continue
            tv = float(vals.get(total_comp, np.nan)) if total_mode == "component" else float(s)
            if total_mode == "component" and check_identity and np.isfinite(tv) and abs(tv - s) > tolerance:
                gaps.append({"facet": f, "x": xv, "total": tv, "sum_components": float(s), "gap": float(tv - s)})
            top_y = max(top, tv if np.isfinite(tv) else top, 0.0)
            tot_rows.append({"layer": "total", "facet": f, "x": xv, "component": total_comp or "sum", "label": t_label,
                             "value": tv, "sum_components": float(s), "y_top": float(top_y)})
    if not seg_rows:
        raise ValueError("nothing to plot (all component values missing)")
    seg = pd.DataFrame(seg_rows)
    tot = pd.DataFrame(tot_rows, columns=["layer", "facet", "x", "component", "label", "value", "sum_components", "y_top"])
    if total_labels and len(tot):
        tmpl = total_labels if isinstance(total_labels, str) else "{:" + value_format + "}"
        tot["total_text"] = [tmpl.format(v) if np.isfinite(v) else "" for v in tot["value"]]
    ref = pd.DataFrame({"layer": "reference", "facet": facets, "value": 0.0})
    plot = pd.concat([seg, tot, ref], ignore_index=True, sort=False)
    if not facet:
        plot = plot.drop(columns=["facet"])

    unit = single_unit(df)
    if y_title is None:
        y_title = unit
    n_x = len(xs)
    if width is None:
        width = max(130, 66 * n_x) if facet else min(style.WIDTH, max(200, 80 * n_x))
    size_props = {"width": width, "height": height} if not horizontal else {"width": width or style.WIDTH, "height": max(120, 34 * n_x)}
    cat_ch, val_ch = ("y", "x") if horizontal else ("x", "y")
    Cat = alt.Y if horizontal else alt.X
    Val = alt.X if horizontal else alt.Y
    Val2 = alt.X2 if horizontal else alt.Y2
    label_expr = js_lookup(period_labels) if period_labels else alt.Undefined
    cat_enc = Cat("x:N", sort=xs, title=None,
                  axis=alt.Axis(labelAngle=0 if (horizontal or n_x <= 6 or period_labels) else -40, labelExpr=label_expr,
                                labelLimit=180, ticks=False, labelPadding=6),
                  scale=alt.Scale(paddingInner=_PAD_INNER, paddingOuter=_PAD_OUTER))
    val_axis = alt.Axis(format=value_format, title=y_title or None)
    comp_domain = [labels_map[c] for c in comps]
    color = alt.Color("label:N", scale=alt.Scale(domain=comp_domain, range=[color_map[c] for c in comp_domain]),
                      sort=comp_domain, legend=alt.Legend(title=None, orient="bottom", symbolType="square",
                                                          columns=min(len(comp_domain) + 1, 4), labelLimit=220))
    tooltip = [alt.Tooltip("x:N", title=x.capitalize()), alt.Tooltip("label:N", title="Component"),
               alt.Tooltip("value:Q", format=value_format, title=y_title or "Value")]
    if facet:
        tooltip.insert(0, alt.Tooltip("facet:N", title=facet.capitalize()))
    base = alt.Chart()
    layers: list[alt.Chart] = [
        base.mark_rule(color=RULE, strokeWidth=1).encode(**{val_ch: Val("value:Q")}).transform_filter("datum.layer === 'reference'"),
        base.mark_bar(stroke="white", strokeWidth=1).encode(**{
            cat_ch: cat_enc, val_ch: Val("y0:Q", axis=val_axis, title=y_title or None), f"{val_ch}2": Val2("y1:Q"),
            "color": color, "tooltip": tooltip}).transform_filter("datum.layer === 'segment'"),
    ]
    if value_labels:
        layers.append(base.mark_text(fontSize=9, baseline="middle", align="center").encode(**{
            cat_ch: cat_enc, val_ch: Val("ymid:Q"), "text": alt.Text("value:Q", format=value_format),
            "color": alt.Color("text_color:N", scale=None)}).transform_filter(
            f"datum.layer === 'segment' && abs(datum.value) >= {float(label_min)}"))
    if total_mode != "none" and len(tot):
        t_tooltip = [alt.Tooltip("x:N", title=x.capitalize()), alt.Tooltip("label:N", title="Total"),
                     alt.Tooltip("value:Q", format=value_format, title=t_label),
                     alt.Tooltip("sum_components:Q", format=value_format, title="Sum of components")]
        tfilter = "datum.layer === 'total' && isValid(datum.value)"
        if total_mark == "dot":
            legend_enc = {"shape": alt.Shape("label:N", scale=alt.Scale(domain=[t_label], range=["circle"]),
                                             legend=alt.Legend(title=None, orient="bottom", symbolFillColor=TOTAL_COLOR,
                                                               symbolStrokeColor="white", symbolSize=70))}
            layers.append(base.mark_point(filled=True, size=70, color=TOTAL_COLOR, stroke="white", strokeWidth=1,
                                          opacity=1).encode(**{cat_ch: cat_enc, val_ch: Val("value:Q"), "tooltip": t_tooltip,
                                                               **legend_enc}).transform_filter(tfilter))
        elif total_mark in ("tick", "line"):
            # a filled thin bar as the legend symbol works for both marks (tick marks are filled rects)
            legend_enc = {"strokeDash": alt.StrokeDash("label:N", scale=alt.Scale(domain=[t_label], range=[[1, 0]]),
                                                       legend=alt.Legend(title=None, orient="bottom", symbolType=_BAR_SYMBOL,
                                                                         symbolFillColor=TOTAL_COLOR, symbolStrokeColor=TOTAL_COLOR,
                                                                         symbolStrokeWidth=0.5, symbolSize=120))}
            if total_mark == "tick":
                # one bar's thickness on the drawn category axis (per facet panel): step x (1 - paddingInner)
                extent = size_props["height"] if horizontal else size_props["width"]
                step = extent / max(n_x - _PAD_INNER + 2 * _PAD_OUTER, 1.0)
                bar_px = step * (1 - _PAD_INNER)
                layers.append(base.mark_tick(color=TOTAL_COLOR, thickness=2.5, size=max(2.0, round(bar_px, 1)),
                                             orient="vertical" if horizontal else "horizontal").encode(**{
                    cat_ch: cat_enc, val_ch: Val("value:Q"), "tooltip": t_tooltip, **legend_enc}).transform_filter(tfilter))
            else:
                layers.append(base.mark_line(color=TOTAL_COLOR, strokeWidth=1.6,
                                             point=alt.OverlayMarkDef(filled=True, size=36, color=TOTAL_COLOR)).encode(**{
                    cat_ch: cat_enc, val_ch: Val("value:Q"), "tooltip": t_tooltip, **legend_enc}).transform_filter(tfilter))
        elif total_mark is not None:
            raise ValueError("total_mark must be 'dot', 'tick', 'line' or None")
        if total_labels:
            mk = dict(align="left", dx=6, baseline="middle") if horizontal else dict(baseline="bottom", dy=-6, align="center")
            layers.append(base.mark_text(fontSize=10, fontWeight="bold", color=TEXT, **mk).encode(**{
                cat_ch: cat_enc, val_ch: Val("y_top:Q"), "text": "total_text:N"}).transform_filter(tfilter))

    inner = alt.layer(*layers, data=plot) if not facet else alt.layer(*layers)
    if facet:
        chart = inner.properties(**size_props).facet(
            column=alt.Column("facet:N", sort=facets, title=None, header=alt.Header(labelFontWeight="bold", labelFontSize=11)),
            data=plot).properties(**title_kw(title, subtitle)).resolve_scale(x="independent" if not horizontal else "shared")
    else:
        chart = inner.properties(**size_props, **title_kw(title, subtitle))
    notes = []
    if gaps and flag_gaps:
        where = ", ".join(f"{g['facet'] + ' ' if g['facet'] else ''}{g['x']} ({g['gap']:+.1f})" for g in gaps[:6])
        notes.append(f"Note: Components do not add up to the total in {where}{'...' if len(gaps) > 6 else ''}.")
    if notes:
        chart = add_footer_lines(chart, notes)
    return merge_usermeta(chart, cgjrkit_chart={
        "kind": "stacked_contributions", "x": x, "component": component, "facet": facet,
        "components": comps, "labels": labels_map, "colors": color_map, "total": total_comp,
        "total_mode": total_mode, "total_mark": total_mark, "x_order": xs,
    }, cgjrkit_identity={"tolerance": tolerance, "gaps": gaps, "ok": not gaps} if total_mode == "component" else None)
