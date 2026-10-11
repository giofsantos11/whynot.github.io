"""Time-series charts against peers (archetypes G01, G02, G06, L01, X01-X04).

=================  ===========================================================
``line_vs_peers``  lines for the focus country, peers and aggregates, with an
                   optional peer-range band (IQR), shaded episodes, index
                   rebasing and dashed forecast segments (Kenya Fig 1, Fig 4;
                   North Macedonia Fig 37 IQR template)
``bar_vs_peers``   bars by country for one period, or grouped bars for several
                   periods/windows (North Macedonia Fig 1, Figs 23a-b)
``dual_axis``      two series with independent left and right y axes
=================  ===========================================================

Colour rules (from :mod:`cgjrkit.charts.style`): the focus series is always
``style.FOCUS`` and drawn on top with a thicker line; peers take the rest of
``style.PALETTE`` in a fixed order (colour follows the entity, never its
rank); aggregates (World Bank codes such as ``LMC``/``UMC``/``SSF``, codes in
``Result.meta["aggregates"]`` and non-ISO codes such as ``"STRUCT:median"``)
are grey with distinct dash patterns; the band is ``style.BAND`` and shaded
episodes ``style.EVENT``.

Every builder returns an Altair chart whose data is the plotted data (one
``layer`` column tells the parts apart, so the CSV written by
:func:`cgjrkit.charts.render.save_chart` is self-describing) and whose
``usermeta["cgjrkit_chart"]`` records the chart kind, roles and colours.
Charts never fetch data and are deterministic. A Result passed as data (or as
``band``) must not be BLOCKed; its ``warn`` caveats and data source line go
to the chart footer (:func:`cgjrkit.charts._common.carries_provenance`).
"""

from __future__ import annotations

from typing import Any, Literal, Sequence

import altair as alt
import numpy as np
import pandas as pd

from cgjrkit.charts import style
from cgjrkit.charts._common import (
    AGG_DASHES,
    AGG_GREYS,
    CONTEXT,
    MUTED,
    PEER_COLORS,
    RULE,
    SOLID,
    TEXT,
    FrameLike,
    PeriodAxis,
    Roles,
    add_footer_lines,
    as_frame,
    assign_roles,
    bold_label_condition,
    carries_provenance,
    check_unique,
    merge_usermeta,
    require_columns,
    single_label,
    single_unit,
    sort_periods,
    title_kw,
)

__all__ = ["line_vs_peers", "bar_vs_peers", "dual_axis", "Event", "BandInput"]

#: A shaded episode: ``(start period, end period, label)``, both ends inclusive.
Event = tuple[str, str, str]
#: Band input: ``(low_frame, high_frame)``, a frame with ``period, low, high``,
#: or a :func:`cgjrkit.methods.peers.peer_aggregate` frame with ``stat`` rows.
BandInput = Any

FOCUS_WIDTH = 3.0
LINE_WIDTH = 1.8
CONTEXT_WIDTH = 1.0
FORECAST_DASH = [4, 3]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _color_scales(roles: Roles, extra: Sequence[tuple[str, str, list[float]]] = ()) -> tuple[list[str], list[str], list[list[float]], dict[str, str]]:
    """Legend domain, colours and dash patterns for focus, peers, aggregates (+ extra entries)."""
    domain: list[str] = []
    colors: list[str] = []
    dashes: list[list[float]] = []
    if roles.focus:
        domain.append(roles.labels[roles.focus]); colors.append(style.FOCUS); dashes.append(SOLID)
    for i, p in enumerate(roles.peers):
        domain.append(roles.labels[p]); colors.append(PEER_COLORS[i]); dashes.append(SOLID)
    for i, a in enumerate(roles.aggregates):
        domain.append(roles.labels[a]); colors.append(AGG_GREYS[i % len(AGG_GREYS)])
        dashes.append(AGG_DASHES[(i + i // len(AGG_GREYS)) % len(AGG_DASHES)])
    for lab, col, dash in extra:
        domain.append(lab); colors.append(col); dashes.append(dash)
    return domain, colors, dashes, dict(zip(domain, colors))


def _legend(n_entries: int, symbol: str = "stroke") -> alt.Legend | None:
    if n_entries < 2:
        return None
    return alt.Legend(title=None, orient="bottom", symbolType=symbol, columns=min(n_entries, 4),
                      labelLimit=220, symbolStrokeWidth=2.5)


def _rebase(df: pd.DataFrame, series: str, base: str) -> pd.DataFrame:
    base_rows = df[df["period"] == base].set_index(series)["value"]
    codes = sorted(set(df[series]))
    missing = [c for c in codes if c not in base_rows.index or not np.isfinite(base_rows.get(c, np.nan))
               or base_rows.get(c) == 0]
    if missing:
        raise ValueError(f"index_base={base!r}: no non-zero value in {base} for {missing}; "
                         "choose another base period or drop these series")
    out = df.copy()
    out["value"] = out["value"] / out[series].map(base_rows) * 100.0
    return out


def _one_series(obj: FrameLike, name: str) -> pd.DataFrame:
    df, _ = as_frame(obj, name)
    require_columns(df, ["period", "value"], name)
    df = df.assign(period=df["period"].astype(str))
    if df["period"].duplicated().any():
        raise ValueError(f"{name}: band frames need one value per period (filter to one series)")
    return df[["period", "value"]]


def _band_frame(band: BandInput, stats: tuple[str, str], group: str | None) -> pd.DataFrame:
    """Normalise a band input to columns ``period, low, high``."""
    if isinstance(band, (tuple, list)):
        if len(band) != 2:
            raise ValueError("band as a sequence must be (low_frame, high_frame)")
        lo, hi = _one_series(band[0], "band low"), _one_series(band[1], "band high")
        out = lo.rename(columns={"value": "low"}).merge(hi.rename(columns={"value": "high"}), on="period", how="outer")
    else:
        df, _ = as_frame(band, "band")
        if {"period", "low", "high"} <= set(df.columns):
            out = df[["period", "low", "high"]].copy()
        else:
            require_columns(df, ["period", "value"], "band")
            df = df.copy()
            if "stat" not in df.columns:
                if "iso3" not in df.columns:
                    raise ValueError("band frame needs 'low'/'high' columns, a 'stat' column, or iso3 codes like 'GRP:p25'")
                df["stat"] = df["iso3"].astype(str).str.split(":").str[-1]
                df["group"] = df["iso3"].astype(str).str.split(":").str[0]
            if group is not None:
                gcol = "group" if "group" in df.columns else "iso3"
                df = df[df[gcol].astype(str).str.split(":").str[0] == group]
            elif "group" in df.columns and df["group"].nunique() > 1:
                raise ValueError(f"band frame has several groups {sorted(df['group'].unique())}; pass band_group=")
            if "indicator" in df.columns and df["indicator"].nunique() > 1:
                raise ValueError("band frame mixes indicators; filter to one")
            parts = []
            for col, stat in zip(("low", "high"), stats):
                sub = df[df["stat"].astype(str) == stat][["period", "value"]]
                if sub.empty:
                    raise ValueError(f"band frame has no {stat!r} rows (stats present: {sorted(df['stat'].astype(str).unique())})")
                if sub["period"].astype(str).duplicated().any():
                    raise ValueError(f"band frame has several {stat!r} values per period")
                parts.append(sub.rename(columns={"value": col}).assign(period=sub["period"].astype(str)))
            out = parts[0].merge(parts[1], on="period", how="outer")
    out["period"] = out["period"].astype(str)
    out["low"] = pd.to_numeric(out["low"], errors="coerce")
    out["high"] = pd.to_numeric(out["high"], errors="coerce")
    return out


def _events_frame(events: Sequence[Event], axis: PeriodAxis) -> pd.DataFrame:
    rows = []
    for ev in events:
        if len(ev) != 3:
            raise ValueError(f"each event must be (start, end, label), got {ev!r}")
        s, e, lab = (str(x) for x in ev)
        x0, x1, xm = axis.span(s, e)
        rows.append({"layer": "event", "label": lab, "start": s, "end": e, "x0": x0, "x1": x1, "xm": xm})
    return pd.DataFrame(rows, columns=["layer", "label", "start", "end", "x0", "x1", "xm"])


def _event_layers(ev: pd.DataFrame, axis: PeriodAxis, x_title: str | None = None) -> list[alt.Chart]:
    """Shaded rectangles and labels for episodes, with the same x encoding as the data layers."""
    if ev.empty:
        return []
    if axis.kind == "band":
        band_x = axis.x("x0", title=x_title)
        x0 = alt.X("x0:O", sort=list(axis.order), bandPosition=0, title=x_title, scale=band_x["scale"], axis=band_x["axis"])
        x1 = alt.X2("x1", bandPosition=1)
        xm = axis.x("xm", title=x_title)
    else:
        x0, x1, xm = axis.x("x0", title=x_title), alt.X2("x1"), axis.x("xm", title=x_title)
    rect = alt.Chart(ev).mark_rect(color=style.EVENT, opacity=1).encode(
        x=x0, x2=x1,
        tooltip=[alt.Tooltip("label:N", title="Episode"), alt.Tooltip("start:N"), alt.Tooltip("end:N")],
    )
    text = alt.Chart(ev).mark_text(baseline="top", fontSize=10, color=MUTED).encode(
        x=xm, y=alt.value(2), text="label:N")
    return [rect, text]


def _reference_layer(value: float) -> alt.Chart:
    return alt.Chart(pd.DataFrame({"layer": ["reference"], "y": [float(value)]})).mark_rule(
        color=RULE, strokeWidth=1).encode(y="y:Q")


def _prepare_series_frame(frame: FrameLike, series: str, name: str) -> tuple[pd.DataFrame, dict]:
    df, meta = as_frame(frame, name)
    require_columns(df, [series, "period", "value"], name)
    if series == "iso3" and "indicator" in df.columns and df["indicator"].astype(str).nunique() > 1:
        raise ValueError(f"{name} holds several indicators {sorted(df['indicator'].astype(str).unique())[:6]}; "
                         "filter to one, or pass series='indicator' to draw one line per indicator")
    df[series] = df[series].astype(str)
    df["period"] = df["period"].astype(str)
    df["value"] = pd.to_numeric(df["value"], errors="coerce").astype(float)
    if "obs_status" not in df.columns:
        df["obs_status"] = ""
    df["obs_status"] = df["obs_status"].fillna("").astype(str)
    return df, meta


# ---------------------------------------------------------------------------
# line_vs_peers
# ---------------------------------------------------------------------------
@carries_provenance
def line_vs_peers(
    frame: FrameLike,
    focus: str | None = None,
    peers: Sequence[str] | None = None,
    band: BandInput | None = None,
    events: Sequence[Event] = (),
    index_base: str | None = None,
    title: str = "",
    subtitle: str = "",
    y_title: str | None = None,
    *,
    aggregates: Sequence[str] | None = None,
    series: str = "iso3",
    label: str | None = None,
    band_label: str = "Interquartile range",
    band_stats: tuple[str, str] = ("p25", "p75"),
    band_group: str | None = None,
    peer_style: Literal["palette", "grey"] = "palette",
    mark_forecasts: bool = True,
    end_labels: bool = False,
    points: bool = False,
    reference_line: float | bool | None = None,
    x_title: str | None = None,
    y_format: str = ",.1f",
    width: int = style.WIDTH,
    height: int = style.HEIGHT,
) -> alt.LayerChart:
    """Line chart of one indicator for the focus country against peers and aggregates.

    Parameters
    ----------
    frame
        Canonical frame (or Result) with one indicator (``series="iso3"``),
        or several indicators for one country with ``series="indicator"``
        (Kenya Fig 4: TFP, capital per worker, GDP per worker).
    focus
        Code of the highlighted series (ISO3 by default), drawn in
        ``style.FOCUS`` on top of the others.
    peers
        Codes to draw as peers, in legend order. ``None`` draws every
        non-aggregate series in the frame (sorted by label). Unknown codes
        raise ``ValueError`` rather than silently vanishing.
    band
        Peer range drawn as a ``style.BAND`` area: ``(low_frame, high_frame)``,
        a frame with ``period, low, high``, or a ``peer_aggregate`` frame with
        ``band_stats`` rows (default p25/p75; pick a group with ``band_group``).
    events
        Shaded episodes ``[(start, end, label), ...]`` in ``style.EVENT``
        (e.g. ``[("2008", "2009", "GFC"), ("2020", "2021", "COVID-19")]``);
        on annual axes a band runs from ``start - 0.5`` to ``end + 0.5``.
    index_base
        Rebase every series to 100 in this period (e.g. ``"2001"``); a series
        without a non-zero base value raises. Cannot be combined with ``band``
        (compute the band on indexed data instead).
    aggregates
        Codes drawn grey and dashed. ``None`` detects World Bank aggregate
        codes, ``Result.meta["aggregates"]`` and non-ISO codes.
    peer_style
        ``"palette"`` (one palette colour per peer, at most 7) or ``"grey"``
        (thin light-grey context lines without a legend, for many peers).
    mark_forecasts
        Draw observations flagged ``obs_status == "forecast"`` as dashed
        segments joined to the last actual value, and add a footer note.
    end_labels
        Write each series' label at its last value (secondary encoding for
        colour-blind readers).
    reference_line
        Horizontal rule: ``None`` draws one at 100 for indexed charts and at 0
        when values cross zero; a number draws it there; ``False`` none.

    Returns
    -------
    alt.LayerChart
        ``usermeta["cgjrkit_chart"]`` holds ``kind``, ``focus``, ``peers``,
        ``aggregates``, ``colors`` (label -> hex) and ``index_base``.
    """
    df, meta = _prepare_series_frame(frame, series, "frame")
    roles = assign_roles(df, series, focus, peers, aggregates, meta, label)
    if peer_style not in ("palette", "grey"):
        raise ValueError("peer_style must be 'palette' or 'grey'")
    if peer_style == "palette" and len(roles.peers) > len(PEER_COLORS):
        raise ValueError(f"{len(roles.peers)} peers but only {len(PEER_COLORS)} peer colours; pass fewer peers, "
                         "use peer_style='grey', or show a peer band instead")
    if peer_style == "grey":
        roles.others, roles.peers = roles.peers, []
    df = df[df[series].isin(roles.ordered)].copy()
    check_unique(df, [series, "period"], "line_vs_peers")
    if index_base is not None:
        if band is not None:
            raise ValueError("index_base cannot be combined with band: rebase the data, then compute the band")
        df = _rebase(df, series, str(index_base))

    band_df = _band_frame(band, band_stats, band_group) if band is not None else None
    periods = list(df["period"]) + (list(band_df["period"]) if band_df is not None else [])
    axis = PeriodAxis.from_periods(periods)
    order = {p: i for i, p in enumerate(axis.order)}
    rank = {c: i for i, c in enumerate(roles.ordered)}

    plot = pd.DataFrame({
        "layer": "series",
        "series": df[series].to_numpy(),
        "label": df[series].map(roles.labels).to_numpy(),
        "role": [roles.role(c) for c in df[series]],
        "period": df["period"].to_numpy(),
        "t": [axis.position(p) for p in df["period"]],
        "value": df["value"].to_numpy(float),
        "obs_status": df["obs_status"].to_numpy(),
    })
    plot["_o"] = plot["series"].map(rank) * 100000 + plot["period"].map(order)
    plot = plot.sort_values("_o", kind="mergesort").drop(columns="_o").reset_index(drop=True)
    plot["segment"] = "actual"
    has_forecast = bool(mark_forecasts and (plot["obs_status"] == "forecast").any())
    if has_forecast:
        fc = plot["obs_status"] == "forecast"
        bridges = []
        for _, g in plot.groupby("series", sort=False):
            gf = g[fc.loc[g.index]]
            if gf.empty:
                continue
            first = gf.index.min()
            prior = g[(g.index < first) & ~fc.loc[g.index] & g["value"].notna()]
            if not prior.empty:
                bridges.append(prior.iloc[[-1]].assign(segment="forecast_bridge"))
        plot.loc[fc, "segment"] = "forecast"
        if bridges:
            plot = pd.concat([plot] + bridges, ignore_index=True)

    extra = [(band_label, style.BAND, SOLID)] if band_df is not None else []
    domain, colors, dashes, color_map = _color_scales(roles, extra)
    legend = _legend(len(domain))
    color = alt.Color("label:N", scale=alt.Scale(domain=domain, range=colors), sort=domain, legend=legend)
    dash = alt.StrokeDash("label:N", scale=alt.Scale(domain=domain, range=dashes), sort=domain, legend=legend)
    unit = single_unit(df)
    if y_title is None:
        y_title = f"Index ({index_base} = 100)" if index_base is not None else (unit or single_label(df))
    x_enc = axis.x("t", title=x_title)
    y_enc = alt.Y("value:Q", title=y_title or None, scale=alt.Scale(zero=False), axis=alt.Axis(format=y_format))
    tooltip = [alt.Tooltip("label:N", title="Series"), alt.Tooltip("period:N", title="Period"),
               alt.Tooltip("value:Q", title=y_title or "Value", format=y_format),
               alt.Tooltip("obs_status:N", title="Status")]
    base = alt.Chart(plot)
    point = alt.OverlayMarkDef(filled=True, size=18) if points else alt.Undefined

    def lines(test: str, width_: float, forecast: bool, ctx: bool = False) -> alt.Chart:
        seg = "(datum.segment === 'forecast' || datum.segment === 'forecast_bridge')" if forecast else "datum.segment === 'actual'"
        mark: dict[str, Any] = {"strokeWidth": width_, "invalid": "break-paths-filter-domains", "point": point}
        enc: dict[str, Any] = {"x": x_enc, "y": y_enc, "detail": "series:N", "tooltip": tooltip}
        if forecast:
            mark["strokeDash"] = FORECAST_DASH  # a fixed dash, so no strokeDash encoding on this layer
        elif not ctx:
            enc["strokeDash"] = dash
        if ctx:
            mark["color"] = CONTEXT
        else:
            enc["color"] = color
        return base.mark_line(**mark).encode(**enc).transform_filter(f"({test}) && {seg}")

    layers: list[alt.Chart] = []
    ev = _events_frame(events, axis)
    layers += _event_layers(ev, axis, x_title)
    if band_df is not None:
        b = band_df.assign(layer="band", label=band_label, t=[axis.position(p) for p in band_df["period"]])
        b = b[["layer", "label", "period", "t", "low", "high"]].dropna(subset=["low", "high"])
        layers.append(alt.Chart(b).mark_area(opacity=1).encode(
            x=x_enc, y=alt.Y("low:Q"), y2="high:Q", color=color,
            tooltip=[alt.Tooltip("label:N", title="Band"), alt.Tooltip("period:N", title="Period"),
                     alt.Tooltip("low:Q", format=y_format), alt.Tooltip("high:Q", format=y_format)]))
    ref = reference_line
    if ref is None:
        if index_base is not None:
            ref = 100.0
        else:
            vals = plot["value"].dropna()
            ref = 0.0 if (len(vals) and vals.min() < 0 < vals.max()) else False
    elif ref is True:
        ref = 0.0
    if ref is not False:
        layers.append(_reference_layer(float(ref)))
    # drawing order: context lines, then peers and aggregates, then the focus on top
    segments = (False, True) if has_forecast else (False,)
    for fcast in segments:
        if roles.others:
            layers.append(lines("datum.role === 'other'", CONTEXT_WIDTH, fcast, ctx=True))
    for fcast in segments:
        if roles.peers or roles.aggregates:
            layers.append(lines("datum.role === 'peer' || datum.role === 'aggregate'", LINE_WIDTH, fcast))
    for fcast in segments:
        if roles.focus:
            layers.append(lines("datum.role === 'focus'", FOCUS_WIDTH, fcast))
    if end_labels:
        last = (plot[(plot["role"] != "other") & plot["value"].notna() & (plot["segment"] != "forecast_bridge")]
                .groupby("series", sort=False).tail(1))
        lab = last.assign(layer="end_label")[["layer", "series", "label", "period", "t", "value"]]
        layers.append(alt.Chart(lab).mark_text(align="left", dx=5, fontSize=10, color=TEXT).encode(
            x=x_enc, y=alt.Y("value:Q"), text="label:N"))

    chart = alt.layer(*layers).properties(width=width, height=height, **title_kw(title, subtitle))
    chart = merge_usermeta(chart, cgjrkit_chart={
        "kind": "line_vs_peers", "series_field": series, "focus": roles.focus, "peers": roles.peers,
        "context": roles.others, "aggregates": roles.aggregates, "colors": color_map,
        "index_base": index_base, "band": band_label if band_df is not None else None,
        "events": [list(map(str, e)) for e in events], "axis": axis.kind, "unit": unit,
    })
    if has_forecast:
        chart = add_footer_lines(chart, ["Note: Dashed segments are forecasts."])
    return chart


# ---------------------------------------------------------------------------
# bar_vs_peers
# ---------------------------------------------------------------------------
@carries_provenance
def bar_vs_peers(
    frame: FrameLike,
    focus: str | None = None,
    peers: Sequence[str] | None = None,
    period: str | Sequence[str] | None = None,
    title: str = "",
    subtitle: str = "",
    y_title: str | None = None,
    *,
    aggregates: Sequence[str] | None = None,
    series: str = "iso3",
    label: str | None = None,
    color_by: Literal["role", "series", "period"] | None = None,
    peer_color: str | None = None,
    horizontal: bool = False,
    sort: Literal["descending", "ascending", "none"] | Sequence[str] = "descending",
    value_labels: bool = False,
    value_format: str = ",.1f",
    period_labels: dict[str, str] | None = None,
    width: int | None = None,
    height: int | None = None,
) -> alt.LayerChart:
    """Bars by country for one period, or grouped bars (one per period) by country.

    Parameters
    ----------
    period
        One period (``"2023"`` or a window label such as ``"2011-19"``), a list
        of periods (grouped bars, in period order), or ``None`` for every
        period in the frame.
    color_by
        ``"role"`` (default for one period): focus ``style.FOCUS``, peers
        ``peer_color`` (default the first peer colour), aggregates
        ``style.GREY``. ``"series"``: the same colours as ``line_vs_peers``
        (each peer its own palette colour). ``"period"`` (default for several
        periods): one palette colour per period; the focus label is bold.
    sort
        ``"descending"``/``"ascending"`` by value (the last period when
        grouped), ``"none"`` (focus, peers, aggregates), or a list of codes.
    horizontal
        Horizontal bars (countries on the y axis), as in WGI charts.
    period_labels
        Display labels for periods in the legend (``{"2011-19": "2011-2019"}``).
    """
    df, meta = _prepare_series_frame(frame, series, "frame")
    roles = assign_roles(df, series, focus, peers, aggregates, meta, label)
    df = df[df[series].isin(roles.ordered)].copy()
    if period is None:
        periods = sort_periods(df["period"])
    else:
        periods = [str(period)] if isinstance(period, str) else [str(p) for p in period]
        missing = [p for p in periods if p not in set(df["period"])]
        if missing:
            raise ValueError(f"periods {missing} are not in the frame")
    df = df[df["period"].isin(periods)]
    check_unique(df, [series, "period"], "bar_vs_peers")
    grouped = len(periods) > 1
    color_by = color_by or ("period" if grouped else "role")
    if color_by not in ("role", "series", "period"):
        raise ValueError("color_by must be 'role', 'series' or 'period'")
    if color_by == "series" and len(roles.peers) > len(PEER_COLORS):
        raise ValueError(f"{len(roles.peers)} peers but only {len(PEER_COLORS)} peer colours; use color_by='role'")
    if color_by == "period" and len(periods) > len(style.PALETTE):
        raise ValueError(f"{len(periods)} periods but only {len(style.PALETTE)} palette colours")

    plabels = {p: (period_labels or {}).get(p, p) for p in periods}
    plot = pd.DataFrame({
        "layer": "bar",
        "series": df[series].to_numpy(),
        "label": df[series].map(roles.labels).to_numpy(),
        "role": [roles.role(c) for c in df[series]],
        "period": df["period"].to_numpy(),
        "period_label": df["period"].map(plabels).to_numpy(),
        "value": df["value"].to_numpy(float),
        "obs_status": df["obs_status"].to_numpy(),
    })
    # category order
    if isinstance(sort, str) and sort in ("descending", "ascending"):
        key_period = periods[-1]
        vals = plot[plot["period"] == key_period].set_index("series")["value"]
        codes = sorted(roles.ordered, key=lambda c: (np.isnan(vals.get(c, np.nan)),
                                                     (-1 if sort == "descending" else 1) * (vals.get(c, 0.0) if np.isfinite(vals.get(c, np.nan)) else 0.0),
                                                     roles.ordered.index(c)))
    elif isinstance(sort, str) and sort == "none":
        codes = roles.ordered
    elif isinstance(sort, str):
        raise ValueError("sort must be 'descending', 'ascending', 'none' or a list of codes")
    else:
        codes = [str(c) for c in sort if str(c) in roles.ordered]
        codes += [c for c in roles.ordered if c not in codes]
    cat_order = [roles.labels[c] for c in codes]
    plot["_o"] = plot["series"].map({c: i for i, c in enumerate(codes)}) * 1000 + plot["period"].map({p: i for i, p in enumerate(periods)})
    plot = plot.sort_values("_o", kind="mergesort").drop(columns="_o").reset_index(drop=True)

    if color_by == "role":
        plot["color_key"] = plot["role"]
        domain = [r for r in ("focus", "peer", "aggregate") if r in set(plot["role"])]
        rng = {"focus": style.FOCUS, "peer": peer_color or PEER_COLORS[0], "aggregate": style.GREY}
        color = alt.Color("color_key:N", scale=alt.Scale(domain=domain, range=[rng[d] for d in domain]), legend=None)
        color_map = {roles.labels[c]: rng[roles.role(c)] for c in codes}
    elif color_by == "series":
        plot["color_key"] = plot["label"]
        domain, colors, _, color_map = _color_scales(roles)
        color = alt.Color("color_key:N", scale=alt.Scale(domain=domain, range=colors), legend=None)
    else:
        plot["color_key"] = plot["period_label"]
        domain = [plabels[p] for p in periods]
        color = alt.Color("color_key:N", scale=alt.Scale(domain=domain, range=style.PALETTE[:len(domain)]),
                          sort=domain, legend=_legend(len(domain), symbol="square"))
        color_map = dict(zip(domain, style.PALETTE))

    unit = single_unit(df)
    if y_title is None:
        y_title = unit or single_label(df)
    n_cat = len(cat_order)
    width = width or (style.WIDTH if not horizontal else 420)
    height = height or (style.HEIGHT if not horizontal else max(160, 22 * n_cat * (len(periods) if grouped else 1) ** 0.5 + 40))
    focus_label = roles.labels.get(roles.focus) if roles.focus else None
    cat_axis = alt.Axis(labelFontWeight=bold_label_condition(focus_label), labelLimit=160,
                        labelAngle=0 if horizontal or n_cat <= 8 else -40, title=None)
    val_axis = alt.Axis(format=value_format, title=y_title or None)
    cat = alt.X if not horizontal else alt.Y
    val = alt.Y if not horizontal else alt.X
    off = alt.XOffset if not horizontal else alt.YOffset
    enc: dict[str, Any] = {
        ("x" if not horizontal else "y"): cat("label:N", sort=cat_order, axis=cat_axis,
                                              scale=alt.Scale(paddingInner=0.25 if grouped else 0.35, paddingOuter=0.2)),
        ("y" if not horizontal else "x"): val("value:Q", axis=val_axis, scale=alt.Scale(zero=True)),
        "color": color,
        "tooltip": [alt.Tooltip("label:N", title="Series"), alt.Tooltip("period_label:N", title="Period"),
                    alt.Tooltip("value:Q", title=y_title or "Value", format=value_format),
                    alt.Tooltip("obs_status:N", title="Status")],
    }
    if grouped:
        enc["xOffset" if not horizontal else "yOffset"] = off("period_label:N", sort=[plabels[p] for p in periods])
    base = alt.Chart(plot)
    layers: list[alt.Chart] = [base.mark_bar(stroke="white", strokeWidth=1).encode(**enc)]
    vals = plot["value"].dropna()
    if len(vals) and vals.min() < 0 < vals.max():
        rule = alt.Chart(pd.DataFrame({"layer": ["reference"], "v": [0.0]})).mark_rule(color=RULE, strokeWidth=1)
        layers.append(rule.encode(**({"y": "v:Q"} if not horizontal else {"x": "v:Q"})))
    if value_labels:
        for sign, test in (("pos", "datum.value >= 0"), ("neg", "datum.value < 0")):
            if horizontal:
                mk = dict(align="left" if sign == "pos" else "right", dx=3 if sign == "pos" else -3, baseline="middle")
            else:
                mk = dict(baseline="bottom" if sign == "pos" else "top", dy=-2 if sign == "pos" else 2, align="center")
            tenc = {k: v for k, v in enc.items() if k not in ("color", "tooltip")}
            tenc["text"] = alt.Text("value:Q", format=value_format)
            layers.append(base.mark_text(fontSize=10, color=TEXT, **mk).encode(**tenc).transform_filter(
                f"isValid(datum.value) && {test}"))
    chart = alt.layer(*layers).properties(width=width, height=height, **title_kw(title, subtitle))
    return merge_usermeta(chart, cgjrkit_chart={
        "kind": "bar_vs_peers", "series_field": series, "focus": roles.focus, "peers": roles.peers,
        "aggregates": roles.aggregates, "periods": periods, "color_by": color_by, "colors": color_map,
        "order": codes, "horizontal": horizontal, "unit": unit,
    })


# ---------------------------------------------------------------------------
# dual_axis
# ---------------------------------------------------------------------------
@carries_provenance
def dual_axis(
    left_frame: FrameLike,
    right_frame: FrameLike,
    title: str = "",
    subtitle: str = "",
    left_title: str | None = None,
    right_title: str | None = None,
    *,
    left_mark: Literal["line", "bar"] = "line",
    right_mark: Literal["line", "bar"] = "line",
    series: str = "iso3",
    label: str | None = None,
    right_suffix: str = " (right axis)",
    events: Sequence[Event] = (),
    left_format: str = ",.1f",
    right_format: str = ",.1f",
    width: int = style.WIDTH,
    height: int = style.HEIGHT,
) -> alt.LayerChart:
    """Two measures on independent left and right y axes (e.g. REER level and inflation).

    Dual axes make the alignment of the two scales arbitrary; prefer
    ``line_vs_peers(index_base=...)`` or two panels when the report does not
    require this form. To keep the reading unambiguous, right-axis series get
    ``right_suffix`` in the legend, axis titles take the colour of their
    series when each side has one series, and right-axis lines are dashed.

    Each frame may hold one or more series (split by ``series``). When a side
    holds a single series, its legend label is the indicator label.
    Colours run through ``style.PALETTE`` (left side first). If either side is
    drawn as bars the period axis becomes ordinal so bars and lines align.
    """
    sides = []
    for name, obj, mark in (("left_frame", left_frame, left_mark), ("right_frame", right_frame, right_mark)):
        if mark not in ("line", "bar"):
            raise ValueError(f"{name}: mark must be 'line' or 'bar'")
        df, meta = _prepare_series_frame(obj, series, name)
        check_unique(df, [series, "period"], f"dual_axis {name}")
        roles = assign_roles(df, series, None, None, [], meta, label, sort_peers=True)
        codes = roles.peers
        labels = dict(roles.labels)
        ind_label = single_label(df)
        if len(codes) == 1 and ind_label:
            labels = {codes[0]: ind_label}
        sides.append((name, df, mark, codes, labels))
    any_bar = left_mark == "bar" or right_mark == "bar"
    all_periods = list(sides[0][1]["period"]) + list(sides[1][1]["period"])
    axis = PeriodAxis.from_periods(all_periods)
    if any_bar:
        axis = PeriodAxis("band", "other", axis.order)

    frames, domain, colors, label_side = [], [], [], {}
    for i, (_, df, _, codes, labels) in enumerate(sides):
        side = "left" if i == 0 else "right"
        for c in codes:
            lab = labels[c] + (right_suffix if side == "right" else "")
            if lab in domain:
                lab = f"{lab} [{side}]"
            domain.append(lab)
            label_side[lab] = side
            colors.append(style.PALETTE[len(colors) % len(style.PALETTE)])
            sub = df[df[series] == c]
            frames.append(pd.DataFrame({
                "layer": side, "side": side, "series": c, "label": lab, "period": sub["period"].to_numpy(),
                "t": [axis.position(p) for p in sub["period"]], "value": sub["value"].to_numpy(float),
                "obs_status": sub["obs_status"].to_numpy()}))
    if len(domain) > len(style.PALETTE):
        raise ValueError("too many series for one dual-axis chart")
    plot = pd.concat(frames, ignore_index=True)
    order = {p: k for k, p in enumerate(axis.order)}
    plot = plot.assign(_o=plot["period"].map(order)).sort_values(["side", "series", "_o"], kind="mergesort").drop(columns="_o")
    color_map = dict(zip(domain, colors))
    color = alt.Color("label:N", scale=alt.Scale(domain=domain, range=colors), sort=domain,
                      legend=_legend(len(domain)))
    x_enc = axis.x("t", title=None)
    base = alt.Chart(plot)
    side_layers = []
    for i, (_, df, mark, codes, _) in enumerate(sides):
        side = "left" if i == 0 else "right"
        fmt = left_format if side == "left" else right_format
        ttl = left_title if side == "left" else right_title
        if ttl is None:
            ttl = single_unit(df) or single_label(df)
        side_domain = [d for d in domain if label_side[d] == side]
        title_color = color_map[side_domain[0]] if len(side_domain) == 1 else TEXT
        y = alt.Y("value:Q", title=ttl or None, scale=alt.Scale(zero=mark == "bar"),
                  axis=alt.Axis(orient=side, format=fmt, titleColor=title_color, grid=side == "left"))
        tooltip = [alt.Tooltip("label:N", title="Series"), alt.Tooltip("period:N", title="Period"),
                   alt.Tooltip("value:Q", format=fmt, title=ttl or "Value")]
        if mark == "bar":
            m = base.mark_bar(opacity=0.85, stroke="white", strokeWidth=1)
            enc = {"x": x_enc, "y": y, "color": color, "tooltip": tooltip}
            if len(codes) > 1:
                enc["xOffset"] = alt.XOffset("label:N", sort=side_domain)
        else:
            m = base.mark_line(strokeWidth=LINE_WIDTH + (0.4 if side == "left" else 0),
                               strokeDash=FORECAST_DASH if side == "right" else alt.Undefined,
                               invalid="break-paths-filter-domains")
            enc = {"x": x_enc, "y": y, "color": color, "detail": "series:N", "tooltip": tooltip}
        side_layers.append(m.encode(**enc).transform_filter(f"datum.side === '{side}'"))
    event_layers = _event_layers(_events_frame(events, axis), axis)  # no y channel: they span the plot
    chart = alt.layer(*event_layers, *side_layers).resolve_scale(y="independent")
    chart = chart.properties(width=width, height=height, **title_kw(title, subtitle))
    return merge_usermeta(chart, cgjrkit_chart={
        "kind": "dual_axis", "series_field": series, "left": [d for d in domain if label_side[d] == "left"],
        "right": [d for d in domain if label_side[d] == "right"], "colors": color_map,
        "marks": {"left": left_mark, "right": right_mark}, "axis": axis.kind,
    })

