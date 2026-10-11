"""Render charts to files offline, with the footer drawn under the chart.

``save_chart`` turns an Altair chart into a Vega-Lite spec with the house
theme (:func:`cgjrkit.charts.style.theme`), draws the footer lines stored in
``usermeta["cgjrkit_footer"]`` (notes, caveats, source line) under the plot,
and writes ``<stem>.vl.json``, ``<stem>.svg``, ``<stem>.png`` (and optionally
``.pdf``/``.html``) plus the plotted data as ``<stem>.csv``.

Rendering uses ``vl-convert`` (Vega and Vega-Lite bundled in the wheel): no
browser, no CDN, and external data URLs are refused (``allowed_base_urls=[]``),
so it works offline and cannot leak requests. HTML output bundles the
JavaScript (``bundle=True``) for the same reason.

``chart_from_result`` builds a chart straight from a
:class:`~cgjrkit.core.provenance.Result`, attaching its source line
(``result.source_line()`` over the data providers, see
:func:`data_source_line`) and footnotes for its ``warn`` caveats; BLOCKed
results cannot be charted (:class:`BlockedResultError`). The chart builders
do the same when they are given Results (see
:func:`cgjrkit.charts._common.carries_provenance`); charts built from plain
frames carry no source line.
"""

from __future__ import annotations

import copy
import json
import re
import textwrap
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import altair as alt
import pandas as pd

from cgjrkit.charts import style
from cgjrkit.charts._common import (
    MUTED,
    BlockedResultError,
    add_footer_lines,
    attach_provenance,
    caveat_notes,
    check_not_blocked,
    combine_results,
    data_source_line,
    get_usermeta,
    jsonable,
    results_in,
)
from cgjrkit.core.provenance import Result

__all__ = [
    "FORMATS",
    "BlockedResultError",
    "to_spec",
    "footer_lines",
    "add_footer",
    "with_footer",
    "plotted_data",
    "render",
    "save_chart",
    "chart_paths",
    "chart_from_result",
    "method_chart_defaults",
    "filter_rows",
    "caveat_notes",
    "data_source_line",
    "CHART_KINDS",
]

#: Output formats ``save_chart`` understands (``csv`` is controlled by ``data_csv``).
FORMATS = ("vl.json", "svg", "png", "pdf", "html")
THEME_NAME = "cgjrkit"
FOOTER_FONT_SIZE = 10
FOOTER_LINE_HEIGHT = 13
_LEGEND_CHANNELS = ("color", "fill", "stroke", "opacity", "fillOpacity", "strokeOpacity", "shape", "size",
                    "strokeWidth", "strokeDash", "angle")
# Keys that may only appear at the top level of a Vega-Lite spec.
_TOP_LEVEL_KEYS = ("$schema", "config", "usermeta", "datasets", "background", "padding", "autosize", "params")


# ---------------------------------------------------------------------------
# specs
# ---------------------------------------------------------------------------
def _ensure_theme() -> None:
    if THEME_NAME not in alt.theme.names():
        alt.theme.register(THEME_NAME, enable=False)(style.theme)


def to_spec(chart: alt.TopLevelMixin | Mapping[str, Any]) -> dict:
    """Vega-Lite spec dict of ``chart`` with the house theme and no row limit.

    The global Altair theme and data transformer are restored afterwards, so
    the output does not depend on what the caller enabled. A dict is
    returned as a deep copy (assumed to be a finished spec).
    """
    if isinstance(chart, Mapping):
        return copy.deepcopy(dict(chart))
    _ensure_theme()
    with alt.theme.enable(THEME_NAME), alt.data_transformers.disable_max_rows():
        spec = chart.to_dict()
    return spec


def footer_lines(chart_or_spec: alt.TopLevelMixin | Mapping[str, Any]) -> list[str]:
    """The footer lines stored in ``usermeta["cgjrkit_footer"]`` (empty list if none)."""
    if isinstance(chart_or_spec, Mapping):
        um = chart_or_spec.get("usermeta") or {}
    else:
        um = get_usermeta(chart_or_spec)
    lines = um.get("cgjrkit_footer") or []
    return [str(x) for x in lines if x]


def add_footer(chart: alt.TopLevelMixin, source_line: str = "", notes: Sequence[str] | str = ()) -> alt.TopLevelMixin:
    """Merge notes and a source line into the chart's footer, keeping other ``usermeta``.

    Use this instead of :func:`cgjrkit.charts.style.with_source` when the chart
    already carries metadata (``with_source`` replaces the whole ``usermeta``).
    """
    if isinstance(notes, str):
        notes = [notes] if notes else []
    return add_footer_lines(chart, list(notes), source_line)


def _chart_width(spec: Mapping[str, Any]) -> int:
    w = spec.get("width")
    if isinstance(w, (int, float)):
        return int(w)
    inner = spec.get("spec")  # facet
    if isinstance(inner, Mapping) and isinstance(inner.get("width"), (int, float)):
        cols = spec.get("columns")
        n = int(cols) if isinstance(cols, int) and cols > 0 else _facet_columns(spec)
        return int(inner["width"]) * n + 20 * (n - 1)
    for key in ("hconcat", "concat"):
        if isinstance(spec.get(key), list):
            return sum(_chart_width(s) for s in spec[key]) or style.WIDTH
    if isinstance(spec.get("vconcat"), list):
        return max((_chart_width(s) for s in spec["vconcat"]), default=style.WIDTH)
    if isinstance(spec.get("layer"), list):
        return max((_chart_width(s) for s in spec["layer"] if isinstance(s.get("width"), (int, float))),
                   default=style.WIDTH)
    return style.WIDTH


def _facet_columns(spec: Mapping[str, Any]) -> int:
    """Number of facet columns, counted from the embedded data (1 if unknown)."""
    facet = spec.get("facet") or {}
    col = facet.get("column") if isinstance(facet, Mapping) else None
    field = col.get("field") if isinstance(col, Mapping) else None
    data = spec.get("data") or {}
    values = data.get("values") if isinstance(data, Mapping) else None
    if values is None and isinstance(data, Mapping) and data.get("name"):
        values = (spec.get("datasets") or {}).get(data["name"])
    if not field or not isinstance(values, list):
        return 1
    return max(1, len({str(v.get(field)) for v in values if isinstance(v, Mapping) and v.get(field) is not None}))


def _wrap(lines: Sequence[str], width_px: int, font_size: int) -> list[str]:
    chars = max(40, int(width_px / (font_size * 0.53)))
    out: list[str] = []
    for line in lines:
        out += textwrap.wrap(line, width=chars, break_long_words=False, break_on_hyphens=False) or [""]
    return out


def with_footer(
    spec: Mapping[str, Any],
    lines: Sequence[str] | None = None,
    *,
    font_size: int = FOOTER_FONT_SIZE,
    line_height: int = FOOTER_LINE_HEIGHT,
    color: str = MUTED,
) -> dict:
    """Return a spec that draws ``lines`` (default: the spec's footer) under the chart.

    The chart becomes the first panel of a ``vconcat`` whose second panel is a
    text-only view; long lines are wrapped to the chart width. Top-level-only
    keys (``$schema``, ``config``, ``datasets``, ``usermeta``, ...) move to the
    new top level and ``usermeta["cgjrkit_footer_drawn"]`` is set so viewers
    that also show the footer from metadata can skip it.
    """
    spec = copy.deepcopy(dict(spec))
    lines = footer_lines(spec) if lines is None else [str(x) for x in lines if x]
    if not lines:
        return spec
    width = _chart_width(spec)  # before moving datasets (facet widths count the facet values)
    top = {k: spec.pop(k) for k in _TOP_LEVEL_KEYS if k in spec}
    wrapped = _wrap(lines, width, font_size)
    footer = {
        "data": {"values": [{}]},
        "width": width,
        "height": len(wrapped) * line_height,
        "view": {"stroke": None},
        "layer": [
            {"mark": {"type": "text", "text": text, "x": 0, "y": i * line_height, "align": "left",
                      "baseline": "top", "fontSize": font_size, "color": color, "font": style.FONT,
                      "tooltip": None}}
            for i, text in enumerate(wrapped)
        ],
    }
    usermeta = dict(top.pop("usermeta", {}) or {})
    usermeta["cgjrkit_footer_drawn"] = True
    out: dict[str, Any] = {}
    if "$schema" in top:
        out["$schema"] = top.pop("$schema")
    out.update(top)
    out["usermeta"] = usermeta
    out["vconcat"] = [spec, footer]
    out["spacing"] = 10
    # keep the chart's legends with the chart (above the footer), not at the bottom of the concat
    out["resolve"] = {"legend": {ch: "independent" for ch in _LEGEND_CHANNELS}}
    return out


def plotted_data(spec: Mapping[str, Any]) -> pd.DataFrame:
    """The data a spec plots, as one frame (datasets stacked; a ``dataset`` column if no ``layer`` column)."""
    frames = []
    datasets = spec.get("datasets") or {}
    for name, values in datasets.items():
        df = pd.DataFrame(values)
        if df.empty:
            continue
        if len(datasets) > 1 and "layer" not in df.columns:
            df.insert(0, "dataset", name)
        frames.append(df)
    if not frames:
        def walk(node: Any) -> None:
            if isinstance(node, Mapping):
                data = node.get("data")
                if isinstance(data, Mapping) and isinstance(data.get("values"), list) and data["values"] != [{}]:
                    frames.append(pd.DataFrame(data["values"]))
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)
        walk(spec)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def _vl_version(spec: Mapping[str, Any]) -> str | None:
    import vl_convert as vlc

    m = re.search(r"/v(\d+)\.(\d+)", str(spec.get("$schema", "")))
    if not m:
        return None
    want = f"{m.group(1)}.{m.group(2)}"
    have = set(vlc.get_vegalite_versions())
    return want if want in have else None


def render(chart: alt.TopLevelMixin | Mapping[str, Any], fmt: str = "svg", *, footer: bool = True,
           scale: float = 2.0) -> str | bytes:
    """Render to ``"svg"`` (str), ``"png"``/``"pdf"`` (bytes), ``"html"`` (str) or ``"vl.json"`` (str), offline."""
    import vl_convert as vlc

    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {FORMATS}")
    spec = to_spec(chart)
    if footer and not (spec.get("usermeta") or {}).get("cgjrkit_footer_drawn"):
        spec = with_footer(spec)
    if fmt == "vl.json":
        return json.dumps(jsonable(spec), indent=2, ensure_ascii=False)
    ver = _vl_version(spec)
    kw: dict[str, Any] = {"vl_version": ver} if ver else {}
    if fmt == "svg":
        return vlc.vegalite_to_svg(spec, allowed_base_urls=[], **kw)
    if fmt == "png":
        return vlc.vegalite_to_png(spec, scale=scale, allowed_base_urls=[], **kw)
    if fmt == "pdf":
        return vlc.vegalite_to_pdf(spec, scale=1.0, allowed_base_urls=[], **kw)
    return vlc.vegalite_to_html(spec, bundle=True, **kw)


def chart_paths(path_stem: str | Path, formats: Sequence[str] = ("vl.json", "svg", "png"),
                data_csv: bool = True) -> dict[str, Path]:
    """The files :func:`save_chart` writes for these arguments, ``{format: path}`` (``csv`` last)."""
    bad = [f for f in formats if f not in FORMATS]
    if bad:
        raise ValueError(f"unknown formats {bad}; choose from {FORMATS}")
    stem = Path(path_stem)
    out = {fmt: Path(f"{stem}.{fmt}") for fmt in dict.fromkeys(formats)}
    if data_csv:
        out["csv"] = Path(f"{stem}.csv")
    return out


def save_chart(
    chart: alt.TopLevelMixin | Mapping[str, Any],
    path_stem: str | Path,
    data_frame: pd.DataFrame | None = None,
    formats: Sequence[str] = ("vl.json", "svg", "png"),
    *,
    footer: bool = True,
    scale: float = 2.0,
    data_csv: bool = True,
    overwrite: bool = True,
) -> dict[str, Path]:
    """Write the chart in ``formats`` and the plotted data as CSV; return ``{format: path}``.

    Every output is rendered in memory first, so a rendering error writes nothing.

    Parameters
    ----------
    chart
        An Altair chart (or a finished Vega-Lite spec dict).
    path_stem
        Output path without extension: ``out/kenya_fig1`` writes
        ``out/kenya_fig1.vl.json``, ``.svg``, ``.png`` and ``.csv``.
        Parent directories are created.
    data_frame
        Data to write as the CSV; by default the data embedded in the spec
        (every layer, with its ``layer`` column).
    formats
        Any of :data:`FORMATS`. The ``vl.json`` is exactly the spec that was
        rendered (footer included), so re-rendering it reproduces the image.
    footer
        Draw ``usermeta["cgjrkit_footer"]`` lines under the chart.
    scale
        PNG scale factor (2 = retina).
    overwrite
        ``False`` raises :class:`FileExistsError` (before writing anything) if
        any target file exists, and creates files exclusively.
    """
    import vl_convert as vlc

    paths = chart_paths(path_stem, formats, data_csv)
    if not overwrite:
        existing = [str(p) for p in paths.values() if p.exists()]
        if existing:
            raise FileExistsError(f"refusing to overwrite existing files: {existing}")
    spec = to_spec(chart)
    data = data_frame if data_frame is not None else plotted_data(spec)
    if footer and not (spec.get("usermeta") or {}).get("cgjrkit_footer_drawn"):
        spec = with_footer(spec)
    ver = _vl_version(spec)
    kw: dict[str, Any] = {"vl_version": ver} if ver else {}
    content: dict[str, bytes] = {}
    for fmt in paths:
        if fmt == "vl.json":
            content[fmt] = (json.dumps(jsonable(spec), indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        elif fmt == "svg":
            content[fmt] = vlc.vegalite_to_svg(spec, allowed_base_urls=[], **kw).encode("utf-8")
        elif fmt == "png":
            content[fmt] = bytes(vlc.vegalite_to_png(spec, scale=scale, allowed_base_urls=[], **kw))
        elif fmt == "pdf":
            content[fmt] = bytes(vlc.vegalite_to_pdf(spec, scale=1.0, allowed_base_urls=[], **kw))
        elif fmt == "html":
            content[fmt] = vlc.vegalite_to_html(spec, bundle=True, **kw).encode("utf-8")
        elif fmt == "csv":
            content[fmt] = data.to_csv(index=False).encode("utf-8")
    Path(path_stem).parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if overwrite else "xb"
    for fmt, path in paths.items():
        with open(path, mode) as fh:
            fh.write(content[fmt])
    return paths


# ---------------------------------------------------------------------------
# Result -> chart
# ---------------------------------------------------------------------------
def _builders() -> dict[str, Callable[..., alt.TopLevelMixin]]:
    from cgjrkit.charts import benchmark, decomposition, series

    return {
        "line": series.line_vs_peers,
        "line_vs_peers": series.line_vs_peers,
        "bar": series.bar_vs_peers,
        "bar_vs_peers": series.bar_vs_peers,
        "dual_axis": series.dual_axis,
        "scatter": benchmark.scatter_vs_income,
        "scatter_vs_income": benchmark.scatter_vs_income,
        "kde": benchmark.kde_plot,
        "kde_plot": benchmark.kde_plot,
        "slope": benchmark.slope_rank,
        "slope_rank": benchmark.slope_rank,
        "stacked": decomposition.stacked_contributions,
        "stacked_contributions": decomposition.stacked_contributions,
        "decomposition": decomposition.stacked_contributions,
    }


#: Chart kinds accepted by :func:`chart_from_result`.
CHART_KINDS = ("line", "bar", "dual_axis", "scatter", "kde", "slope", "stacked")


_STACKED_KINDS = frozenset({"stacked", "stacked_contributions", "decomposition"})
#: The total and factor rows of a jobs_shapley result (sector splits and residuals are sub-rows of a factor).
_JOBS_SHAPLEY_FACTORS = ("gdp_per_capita", "output_per_worker", "employment_rate", "working_age_share")
#: Scalar types allowed as ``where`` values.
_WHERE_SCALARS = (str, int, float, bool)


def _last_method(result: Result) -> str:
    """Tool name of the last cgjrkit method that produced ``result`` (``""`` if none)."""
    tools = [p.tool for p in result.provenance if p.source == "cgjrkit" and p.tool]
    return tools[-1] if tools else ""


def method_chart_defaults(result: Result, kind: str) -> dict[str, Any]:
    """Default chart options for a method's output (what the standard report chart shows).

    Only ``kind="stacked"`` has defaults, keyed on the last cgjrkit method in
    the provenance:

    * ``jobs_shapley``: total ``gdp_per_capita`` (``output_per_worker`` is a
      component); with sector rows, only the total and the three factor rows
      (``where={"component": [...]}``; sector splits and residuals are parts
      of a factor and would be counted twice);
    * ``shift_share``: total ``total``, the economy-wide terms only
      (``where={"sector": "TOTAL"}``);
    * ``growth_accounting``: total ``output`` (``output_per_worker`` with
      ``per_worker=True``);
    * ``sector_contributions``: one segment per sector (``component="sector"``),
      total ``TOTAL``.

    Explicit arguments to :func:`chart_from_result` override these.
    """
    if kind not in _STACKED_KINDS:
        return {}
    tool = _last_method(result)
    cols = set(result.data.columns)
    comps = set(result.data["component"].astype(str)) if "component" in cols else set()
    out: dict[str, Any] = {}
    if tool == "jobs_shapley.jobs_shapley":
        out["total"] = "gdp_per_capita"
        factors = [c for c in _JOBS_SHAPLEY_FACTORS if c in comps]
        if factors and comps - set(factors):  # sector rows present: keep the total and the three factor rows
            out["where"] = {"component": factors}
    elif tool == "structural_change.shift_share":
        out["total"] = "total"
        if "sector" in cols:
            out["where"] = {"sector": "TOTAL"}
    elif tool == "growth_accounting.growth_accounting":
        out["total"] = "output_per_worker" if "output_per_worker" in comps else "output"
    elif tool == "structural_change.sector_contributions" and "sector" in cols:
        out["component"] = "sector"
        sectors = set(result.data["sector"].astype(str))
        if "TOTAL" in sectors:
            out["total"] = "TOTAL"
        return out
    if "total" in out and out["total"] not in comps:
        del out["total"]
    return out


def filter_rows(result: Result, where: Mapping[str, Any]) -> Result:
    """Rows of ``result`` where each column equals the given value (or one of a list of values).

    Values are compared as strings. Unknown columns, non-scalar values and a
    filter that keeps no rows raise ``ValueError``. Provenance, caveats and
    meta are kept.
    """
    if not isinstance(where, Mapping):
        raise ValueError("where must map column names to a value or a list of values")
    df = result.data
    keep = pd.Series(True, index=df.index)
    for col, want in where.items():
        if col not in df.columns:
            raise ValueError(f"where: no column {col!r}; columns are {list(df.columns)}")
        values = list(want) if isinstance(want, (list, tuple, set, frozenset)) else [want]
        if not values or not all(isinstance(v, _WHERE_SCALARS) for v in values):
            raise ValueError(f"where[{col!r}] must be a value or a non-empty list of values (str, number, bool)")
        keep &= df[col].astype(str).isin([str(v) for v in values])
    if not keep.any():
        raise ValueError(f"where={dict(where)!r} keeps no rows")
    return Result(data=df[keep].reset_index(drop=True), provenance=list(result.provenance),
                  caveats=list(result.caveats), meta=dict(result.meta or {}))


def chart_from_result(
    result: Result,
    kind: str,
    *,
    note: str | Sequence[str] = "",
    source_line: str | None = None,
    include_info: bool = False,
    right: Result | None = None,
    where: Mapping[str, Any] | None = None,
    **kw: Any,
) -> alt.TopLevelMixin:
    """Build a chart of ``kind`` from a Result and attach its source line and caveat footnotes.

    Parameters
    ----------
    kind
        ``"line"``, ``"bar"``, ``"dual_axis"`` (needs ``right=``, the
        right-axis Result), ``"scatter"``, ``"kde"``, ``"slope"`` or
        ``"stacked"`` (long names such as ``"line_vs_peers"`` also work).
    note
        Extra note line(s) printed above the caveat notes.
    source_line
        Overrides the default, :func:`data_source_line` (``result.source_line()``
        without the cgjrkit method entries, over every Result used: ``result``,
        ``right`` and Result-valued builder options such as ``band``).
        Python API only: the MCP server refuses it.
    include_info
        Also footnote ``info`` caveats (``warn`` caveats always are).
    where
        Row filter applied to ``result`` before charting, ``{column: value or
        [values]}`` (see :func:`filter_rows`). ``None`` uses the method default
        (:func:`method_chart_defaults`); ``{}`` charts every row.
    **kw
        Passed to the chart builder (``focus=``, ``peers=``, ``title=``,
        ``band=``, ...). For ``kind="stacked"``, :func:`method_chart_defaults`
        fills ``total`` (and ``component``) unless given.

    Raises
    ------
    BlockedResultError
        If the Result, ``right`` or a Result-valued option has a ``block``
        caveat: blocked outputs cannot be rendered or exported.
    """
    builders = _builders()
    if kind not in builders:
        raise ValueError(f"unknown chart kind {kind!r}; choose from {CHART_KINDS}")
    if not isinstance(result, Result):
        raise TypeError("chart_from_result needs a cgjrkit Result")
    if right is not None and not isinstance(right, Result):
        raise TypeError("right= must be a cgjrkit Result")
    inputs = results_in([result, right, *kw.values()])
    check_not_blocked(inputs)
    combined = combine_results(inputs)
    assert combined is not None
    defaults = method_chart_defaults(result, kind)
    default_where = defaults.pop("where", None)
    applied = {k: v for k, v in defaults.items() if k not in kw}
    kw = {**applied, **kw}
    row_filter = default_where if where is None else where
    if row_filter:
        result = filter_rows(result, row_filter)
        if where is None:
            applied["where"] = dict(row_filter)
    fn = builders[kind]
    raw = getattr(fn, "__wrapped__", fn)  # the footer is composed here, not by the builder's decorator
    if fn.__name__ == "dual_axis":
        if right is None:
            raise ValueError("kind='dual_axis' needs right=<Result for the right axis>")
        chart = raw(result, right, **kw)
    else:
        if right is not None:
            raise ValueError("right= is only used with kind='dual_axis'")
        chart = raw(result, **kw)
    notes = [note] if isinstance(note, str) else list(note)
    return attach_provenance(chart, combined, notes, source_line, include_info,
                             where=dict(row_filter) if row_filter else None, defaults=applied or None)
