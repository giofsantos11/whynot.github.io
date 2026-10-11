"""Private helpers shared by the chart builders.

Nothing here touches the network or global Altair state. The public chart
functions live in :mod:`cgjrkit.charts.series`, :mod:`cgjrkit.charts.benchmark`,
:mod:`cgjrkit.charts.decomposition` and :mod:`cgjrkit.charts.render`.
"""

from __future__ import annotations

import functools
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence, TypeVar, Union

import altair as alt
import numpy as np
import pandas as pd

from cgjrkit.charts import style
from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Result

#: Anything a chart builder accepts as data: a canonical (or chart) frame, or a Result wrapping one.
FrameLike = Union[pd.DataFrame, Result]

_F = TypeVar("_F", bound=Callable[..., Any])

# -- colours beyond style.py (neutral inks and greys only) --------------------
#: Text ink for direct labels (labels never wear the series colour).
TEXT = "#333333"
#: Muted ink for footers, event labels and notes (same as the style subtitle colour).
MUTED = "#555555"
#: Light grey for context marks (non-highlighted countries).
CONTEXT = "#C9D1D9"
#: Neutral rule colour (zero line, reference lines); same as style axis domain colour.
RULE = "#888888"
#: Dark neutral for fitted curves and totals, distinct from every palette hue.
DARK = "#2B2B2B"
#: Grey tones for aggregates; the first is ``style.GREY``. Dash patterns separate them.
AGG_GREYS = [style.GREY, "#5F6B78", "#B8C1CA", "#7D8794"]
AGG_DASHES: list[list[float]] = [[6, 3], [2, 2], [8, 3, 2, 3], [4, 2]]
SOLID: list[float] = [1, 0]
#: Peer colours: the house palette without the focus colour, in fixed order.
PEER_COLORS = [c for c in style.PALETTE if c != style.FOCUS]

_ISO3_RE = re.compile(r"^[A-Z]{3}$")
_YEAR_RE = re.compile(r"^(\d{4})$")
_QUARTER_RE = re.compile(r"^(\d{4})Q([1-4])$")
_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_FIRST_YEAR_RE = re.compile(r"(\d{4})")

# Fallback when the World Bank connector cannot be imported (pure constant).
_FALLBACK_AGGREGATES = frozenset({
    "AFE", "AFW", "ARB", "EAP", "EAS", "ECA", "ECS", "EMU", "EUU", "FCS", "HIC", "LAC", "LCN", "LDC",
    "LIC", "LMC", "LMY", "MEA", "MIC", "MNA", "NAC", "OED", "SAS", "SSA", "SSF", "UMC", "WLD",
})


# ---------------------------------------------------------------------------
# provenance: Results given to a chart
# ---------------------------------------------------------------------------
class BlockedResultError(ValueError):
    """Raised when charting a Result that carries a ``block`` caveat."""


def caveat_notes(caveats: Sequence[Caveat], include_info: bool = False) -> list[str]:
    """Footnote lines for caveats: every ``warn`` (and ``info`` if asked), de-duplicated, in order."""
    seen: list[str] = []
    for c in caveats:
        if c.severity == "warn" or (include_info and c.severity == "info"):
            msg = c.message.strip()
            if msg and msg not in seen:
                seen.append(msg)
    return [f"Note: {m}" for m in seen]


def data_source_line(result: Result) -> str:
    """``result.source_line()`` restricted to data providers.

    Provenance entries appended by cgjrkit methods (``source="cgjrkit"``)
    describe computations, not sources, so they are left out of the chart
    source line (they stay in the provenance record). Falls back to
    ``result.source_line()`` when only cgjrkit entries exist.
    """
    data_prov = [p for p in result.provenance if p.source != "cgjrkit"]
    if not data_prov:
        return result.source_line()
    return Result(data=result.data.iloc[0:0], provenance=data_prov).source_line()


def results_in(values: Iterable[Any]) -> list[Result]:
    """The Results among ``values``, looking one level into tuples and lists (``band=(lo, hi)``, ``(x, y)``)."""
    out: list[Result] = []
    for v in values:
        items = v if isinstance(v, (tuple, list)) else (v,)
        for item in items:
            if isinstance(item, Result) and not any(item is r for r in out):
                out.append(item)
    return out


def check_not_blocked(results: Iterable[Result]) -> None:
    """Raise :class:`BlockedResultError` if any Result carries a ``block`` caveat."""
    blocking = [c for r in results for c in r.caveats if c.severity == "block"]
    if blocking:
        raise BlockedResultError("cannot chart a BLOCKed result: "
                                 + "; ".join(f"[{c.code}] {c.message}" for c in blocking))


def combine_results(results: Sequence[Result]) -> Result | None:
    """One Result carrying the provenance and caveats of all ``results`` (data of the first), or None."""
    if not results:
        return None
    combined = results[0]
    for other in results[1:]:
        combined = combined.merged_with(other, data=combined.data)
    return combined


def attach_provenance(chart: Any, combined: Result, notes: Sequence[str] = (), source_line: str | None = None,
                      include_info: bool = False, **extra: Any) -> Any:
    """Footer (``notes``, caveat notes, source line) and ``usermeta["cgjrkit_provenance"]`` from ``combined``."""
    lines = [n for n in notes if n] + caveat_notes(combined.caveats, include_info)
    src = data_source_line(combined) if source_line is None else source_line
    chart = add_footer_lines(chart, lines, src)
    return merge_usermeta(chart, cgjrkit_provenance={
        "data_sha256": combined.data_sha256,
        "caveats": [{"code": c.code, "severity": c.severity} for c in combined.caveats],
        "tools": [p.tool for p in combined.provenance if p.tool],
        **{k: v for k, v in extra.items() if v is not None},
    })


def carries_provenance(fn: _F) -> _F:
    """Decorator for public chart builders: Results given as arguments keep their provenance.

    Before building, any Result argument (also inside a ``(lo, hi)`` or
    ``(x, y)`` pair) with a ``block`` caveat raises :class:`BlockedResultError`.
    After building, the Results' ``warn`` caveats become footnotes and their
    data source line becomes the chart's source line. Frame inputs carry no
    provenance, so a chart built only from frames has no source line.
    :func:`cgjrkit.charts.render.chart_from_result` calls the undecorated
    builder (``fn.__wrapped__``) and composes the footer itself.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        results = results_in(list(args) + list(kwargs.values()))
        check_not_blocked(results)
        chart = fn(*args, **kwargs)
        combined = combine_results(results)
        return chart if combined is None else attach_provenance(chart, combined)

    return wrapper  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------
def as_frame(obj: FrameLike, name: str = "frame") -> tuple[pd.DataFrame, dict]:
    """Return ``(copy of the data, meta)`` from a DataFrame or a Result.

    A Result with a ``block`` caveat raises :class:`BlockedResultError`:
    blocked outputs cannot be charted, whichever argument carries them.
    """
    if isinstance(obj, Result):
        check_not_blocked([obj])
        return obj.data.copy(), dict(obj.meta or {})
    if isinstance(obj, pd.DataFrame):
        return obj.copy(), {}
    raise TypeError(f"{name} must be a pandas DataFrame or a cgjrkit Result, got {type(obj).__name__}")


def require_columns(df: pd.DataFrame, cols: Iterable[str], name: str = "frame") -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(f"{name} is missing columns {missing}; it has {list(df.columns)}")


def known_aggregates(meta: Mapping[str, Any] | None = None) -> frozenset[str]:
    """Aggregate codes: World Bank aggregates plus any listed in ``meta["aggregates"]``."""
    try:
        from cgjrkit.connectors.worldbank import KNOWN_AGGREGATES as wb
    except Exception:  # pragma: no cover - connector unavailable
        wb = _FALLBACK_AGGREGATES
    codes = set(wb)
    aggs = (meta or {}).get("aggregates")
    if isinstance(aggs, Mapping):
        codes.update(str(a) for a in aggs)
    elif isinstance(aggs, (list, tuple, set, frozenset)):
        codes.update(str(a) for a in aggs)
    return frozenset(codes)


def is_aggregate(code: str, known: frozenset[str]) -> bool:
    """True for provider aggregates and anything that is not a 3-letter ISO code (e.g. ``"STRUCT:median"``)."""
    code = str(code)
    return code in known or not _ISO3_RE.match(code)


def series_labels(df: pd.DataFrame, series: str, label: str | None) -> dict[str, str]:
    """Map each series code to a unique display label.

    ``label`` defaults to ``"country"`` when splitting by ``iso3`` and to
    ``"indicator_label"`` when splitting by ``indicator``; empty labels fall
    back to the code, and duplicated labels get the code appended.
    """
    if label is None:
        label = {"iso3": "country", "indicator": "indicator_label"}.get(series, series)
    out: dict[str, str] = {}
    for code, g in df.groupby(series, sort=True):
        lab = ""
        if label in g.columns:
            vals = [str(v) for v in g[label] if isinstance(v, str) and v.strip()]
            lab = vals[0] if vals else ""
        out[str(code)] = lab or str(code)
    counts: dict[str, int] = {}
    for lab in out.values():
        counts[lab] = counts.get(lab, 0) + 1
    return {c: (f"{lab} ({c})" if counts[lab] > 1 and lab != c else lab) for c, lab in out.items()}


def single_unit(df: pd.DataFrame) -> str:
    """The unit label if the frame has exactly one non-empty unit, else ``""``."""
    if "unit" not in df.columns:
        return ""
    units = sorted({str(u) for u in df["unit"] if isinstance(u, str) and u})
    return units[0] if len(units) == 1 else ""


def single_label(df: pd.DataFrame, col: str = "indicator_label") -> str:
    if col not in df.columns:
        return ""
    vals = sorted({str(u) for u in df[col] if isinstance(u, str) and u})
    return vals[0] if len(vals) == 1 else ""


def check_unique(df: pd.DataFrame, keys: Sequence[str], what: str) -> None:
    dup = df.duplicated(subset=list(keys), keep=False)
    if dup.any():
        sample = df.loc[dup, list(keys)].drop_duplicates().head(5).to_dict("records")
        raise ValueError(f"{what}: more than one value per {tuple(keys)}, e.g. {sample}; filter the frame first")


@dataclass
class Roles:
    """Which series is the focus, which are peers and which are aggregates (in plotting order)."""

    focus: str | None
    peers: list[str]
    aggregates: list[str]
    labels: dict[str, str]
    others: list[str] = field(default_factory=list)  # context series (drawn light grey, no legend)

    @property
    def ordered(self) -> list[str]:
        return ([self.focus] if self.focus else []) + self.peers + self.aggregates + self.others

    def role(self, code: str) -> str:
        if code == self.focus:
            return "focus"
        if code in self.aggregates:
            return "aggregate"
        if code in self.peers:
            return "peer"
        return "other"


def assign_roles(
    df: pd.DataFrame,
    series: str,
    focus: str | None,
    peers: Sequence[str] | None,
    aggregates: Sequence[str] | None,
    meta: Mapping[str, Any],
    label: str | None = None,
    sort_peers: bool = True,
) -> Roles:
    """Classify series codes into focus / peers / aggregates, failing loudly on unknown codes."""
    codes = sorted({str(c) for c in df[series]})
    if not codes:
        raise ValueError("frame has no rows to plot")
    labels = series_labels(df, series, label)

    def norm(code: str) -> str:
        code = str(code)
        if code in codes:
            return code
        up = code.upper()
        return up if up in codes else code

    focus_c = norm(focus) if focus else None
    if focus_c is not None and focus_c not in codes:
        raise ValueError(f"focus {focus!r} is not in the frame's {series!r} column (have {codes[:12]}...)")
    known = known_aggregates(meta)
    if aggregates is None:
        aggs = [c for c in codes if c != focus_c and is_aggregate(c, known)] if series == "iso3" else []
    else:
        aggs = [norm(a) for a in aggregates]
        missing = [a for a in aggs if a not in codes]
        if missing:
            raise ValueError(f"aggregates {missing} are not in the frame")
    if peers is None:
        prs = [c for c in codes if c != focus_c and c not in aggs]
        if sort_peers:
            prs = sorted(prs, key=lambda c: (labels[c], c))
    else:
        prs = [norm(p) for p in peers]
        missing = [p for p in prs if p not in codes]
        if missing:
            raise ValueError(f"peers {missing} are not in the frame (no data?); drop them or fetch them first")
        prs = [p for p in dict.fromkeys(prs) if p != focus_c and p not in aggs]
    aggs = [a for a in dict.fromkeys(aggs) if a != focus_c]
    return Roles(focus=focus_c, peers=prs, aggregates=aggs, labels=labels)


# ---------------------------------------------------------------------------
# periods
# ---------------------------------------------------------------------------
def period_sort_key(period: str) -> tuple:
    p = str(period)
    m = _QUARTER_RE.match(p)
    if m:
        return (int(m.group(1)), int(m.group(2)) * 3 - 2, p)
    m = _MONTH_RE.match(p)
    if m:
        return (int(m.group(1)), int(m.group(2)), p)
    m = _FIRST_YEAR_RE.search(p)
    return (int(m.group(1)) if m else 99999, 0, p)


def sort_periods(periods: Iterable[str]) -> list[str]:
    return sorted({str(p) for p in periods}, key=period_sort_key)


def _next_start(period: str, kind: str) -> str:
    if kind == "quarter":
        m = _QUARTER_RE.match(period)
        y, q = int(m.group(1)), int(m.group(2))
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
        return f"{y}-{3 * q - 2:02d}-01"
    m = _MONTH_RE.match(period)
    y, mo = int(m.group(1)), int(m.group(2))
    y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    return f"{y}-{mo:02d}-01"


@dataclass(frozen=True)
class PeriodAxis:
    """How period strings map to an x position.

    * ``year``: all periods are ``"YYYY"`` -> quantitative year axis.
    * ``temporal``: all periods are ``"YYYYQn"`` or ``"YYYY-MM"`` -> UTC time
      axis; an episode runs from the start of ``start`` to the end of ``end``.
    * ``ordinal``: anything else (fiscal years, window labels such as
      ``"2011-19"``) -> evenly spaced positions ``0, 1, 2, ...`` in
      :func:`period_sort_key` order, labelled with the period strings.
    * ``band``: like ``ordinal`` but a band scale, for bars (``dual_axis``).

    On ``year`` and ``ordinal`` axes an episode ``(start, end)`` is shaded
    from half a step before ``start`` to half a step after ``end``, so a
    single-period episode is visible.
    """

    kind: Literal["year", "temporal", "ordinal", "band"]
    sub: Literal["year", "quarter", "month", "other"]
    order: tuple[str, ...]

    @classmethod
    def from_periods(cls, periods: Iterable[str]) -> "PeriodAxis":
        ps = sort_periods(periods)
        if ps and all(_YEAR_RE.match(p) for p in ps):
            return cls("year", "year", tuple(ps))
        if ps and all(_QUARTER_RE.match(p) for p in ps):
            return cls("temporal", "quarter", tuple(ps))
        if ps and all(_MONTH_RE.match(p) for p in ps):
            return cls("temporal", "month", tuple(ps))
        return cls("ordinal", "other", tuple(ps))

    @property
    def vl_type(self) -> str:
        return {"year": "quantitative", "temporal": "temporal", "ordinal": "quantitative", "band": "ordinal"}[self.kind]

    def _index(self, p: str) -> int:
        try:
            return self.order.index(p)
        except ValueError:
            raise ValueError(f"period {p!r} is not on this axis (periods: {list(self.order)[:8]}...)") from None

    def position(self, period: str) -> Any:
        p = str(period)
        if self.kind == "year":
            if not _YEAR_RE.match(p):
                raise ValueError(f"period {p!r} is not a year on this annual axis")
            return int(p)
        if self.kind == "temporal":
            m = _QUARTER_RE.match(p)
            if m:
                return f"{m.group(1)}-{int(m.group(2)) * 3 - 2:02d}-01"
            m = _MONTH_RE.match(p)
            if m:
                return f"{p}-01"
            raise ValueError(f"period {p!r} does not fit this {self.sub}ly axis")
        if self.kind == "ordinal":
            return self._index(p)
        self._index(p)
        return p

    def span(self, start: str, end: str) -> tuple[Any, Any, Any]:
        """``(x0, x1, x_mid)`` covering periods ``start`` .. ``end`` inclusive."""
        s, e = str(start), str(end)
        if self.kind in ("year", "ordinal"):
            x0, x1 = self.position(s) - 0.5, self.position(e) + 0.5
            if x1 <= x0:
                raise ValueError(f"episode ({s}, {e}) ends before it starts")
            return x0, x1, (x0 + x1) / 2
        if self.kind == "temporal":
            x0 = self.position(s)
            self.position(e)  # validates
            x1 = _next_start(e, "quarter" if _QUARTER_RE.match(e) else "month")
            if x1 <= x0:
                raise ValueError(f"episode ({s}, {e}) ends before it starts")
            mid = (pd.Timestamp(x0) + (pd.Timestamp(x1) - pd.Timestamp(x0)) / 2).strftime("%Y-%m-%d")
            return x0, x1, mid
        i0, i1 = self._index(s), self._index(e)
        if i1 < i0:
            raise ValueError(f"episode ({s}, {e}) ends before it starts")
        mid = self.order[(i0 + i1) // 2]
        return s, e, mid

    def x(self, field: str = "t", title: str | None = None, **axis_kw: Any) -> alt.X:
        """The x encoding for this axis (every layer must use the same one so axes merge cleanly)."""
        if self.kind == "year":
            return alt.X(f"{field}:Q", title=title, scale=alt.Scale(zero=False, nice=False),
                         axis=alt.Axis(format="d", tickMinStep=1, labelOverlap=True, **axis_kw))
        if self.kind == "temporal":
            # explicit tick intervals: the d3 default puts ticks at quarters/months and "%Y" would repeat
            # labels; (formatType="utc" is ignored by the bundled Vega, the utc scale formats in UTC already)
            years = period_sort_key(self.order[-1])[0] - period_sort_key(self.order[0])[0] + 1
            if years <= 3:
                fmt, tick = "%b %Y", alt.TimeIntervalStep(interval="month", step=3 if self.sub == "quarter" or years > 1 else 1)
            else:
                fmt, tick = "%Y", alt.TimeIntervalStep(interval="year", step=max(1, math.ceil(years / 12)))
            return alt.X(f"{field}:T", title=title, scale=alt.Scale(type="utc"),
                         axis=alt.Axis(format=fmt, tickCount=tick, labelOverlap=True, **axis_kw))
        if self.kind == "ordinal":
            n = len(self.order)
            expr = js_lookup({str(i): p for i, p in enumerate(self.order)}, fallback="''")
            return alt.X(f"{field}:Q", title=title, scale=alt.Scale(domain=[-0.5, n - 0.5], nice=False, zero=False),
                         axis=alt.Axis(values=list(range(n)), labelExpr=expr, grid=False, labelAngle=0,
                                       labelOverlap=True, **axis_kw))
        return alt.X(f"{field}:O", title=title, sort=list(self.order), scale=alt.Scale(paddingInner=0.2, paddingOuter=0.1),
                     axis=alt.Axis(labelAngle=0, labelOverlap=True, ticks=False, **axis_kw))


# ---------------------------------------------------------------------------
# chart helpers
# ---------------------------------------------------------------------------
def title_kw(title: str, subtitle: str = "") -> dict[str, Any]:
    """``{"title": ...}`` for ``.properties(**...)``, or ``{}`` when there is no title."""
    if not title and not subtitle:
        return {}
    if subtitle:
        return {"title": alt.Title(title or " ", subtitle=subtitle)}
    return {"title": alt.Title(title)}


def get_usermeta(chart: Any) -> dict:
    um = getattr(chart, "usermeta", alt.Undefined)
    if um is alt.Undefined or um is None:
        return {}
    if hasattr(um, "to_dict"):
        um = um.to_dict()
    return dict(um)


def merge_usermeta(chart: Any, **entries: Any) -> Any:
    """Return ``chart`` with ``entries`` merged into its ``usermeta`` (unlike ``style.with_source``, which replaces it)."""
    um = get_usermeta(chart)
    um.update({k: v for k, v in entries.items() if v is not None})
    return chart.properties(usermeta=jsonable(um))


def add_footer_lines(chart: Any, notes: Sequence[str] = (), source_line: str = "") -> Any:
    """Merge notes and a source line into ``usermeta["cgjrkit_footer"]``.

    Order: existing non-source lines, new notes, then one source line (a new
    ``source_line`` replaces an existing ``"Source:"`` line). Duplicates are dropped.
    """
    um = get_usermeta(chart)
    lines = [str(x) for x in um.get("cgjrkit_footer", []) if x]
    sources = [x for x in lines if x.startswith("Source")]
    body = [x for x in lines if not x.startswith("Source")]
    for n in notes:
        if n and n not in body:
            body.append(str(n))
    src = source_line or (sources[-1] if sources else "")
    um["cgjrkit_footer"] = body + ([src] if src else [])
    return chart.properties(usermeta=jsonable(um))


def jsonable(value: Any) -> Any:
    """JSON-safe copy (numpy scalars -> Python, NaN -> None, tuples -> lists)."""
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        seq = sorted(value, key=str) if isinstance(value, (set, frozenset)) else value
        return [jsonable(v) for v in seq]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def js_lookup(mapping: Mapping[str, Any], fallback: str = "datum.value") -> str:
    """A Vega expression looking ``datum.value`` up in ``mapping`` (multi-line labels: ``"a\\nb"`` -> array)."""
    obj = {}
    for k, v in mapping.items():
        v = str(v)
        obj[str(k)] = v.split("\n") if "\n" in v else v
    return f"({json.dumps(obj, ensure_ascii=False)})[datum.value] || {fallback}"


def bold_label_condition(label: str | None) -> Any:
    """Axis ``labelFontWeight`` making ``label`` bold (used to emphasise the focus country)."""
    if not label:
        return alt.Undefined
    return alt.condition(f"datum.value === {json.dumps(label, ensure_ascii=False)}", alt.value("bold"), alt.value("normal"))


def num_range(values: Iterable[float]) -> tuple[float, float] | None:
    arr = np.asarray([v for v in values if v is not None], dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return None
    return float(arr.min()), float(arr.max())


def log_ticks(lo: float, hi: float) -> list[float]:
    """1-2-5 tick values covering ``[lo, hi]`` on a log axis."""
    if not (lo > 0 and hi > 0):
        return []
    out: list[float] = []
    for k in range(math.floor(math.log10(lo)) - 1, math.ceil(math.log10(hi)) + 1):
        for m in (1, 2, 5):
            v = m * 10.0 ** k
            if lo / 1.0001 <= v <= hi * 1.0001:
                out.append(float(round(v, 10)))
    if len(out) > 12:  # thin to powers of ten when the range is wide
        out = [v for v in out if math.isclose(math.log10(v) % 1, 0, abs_tol=1e-9)] or out
    return out


def fmt_value(v: Any, fmt: str) -> str:
    """Format a number with a d3-like format string subset (``",.1f"``, ``".0f"``, ``",d"``)."""
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return ""
    f = fmt.replace("d", ".0f") if fmt.endswith("d") else fmt
    try:
        return format(float(v), f)
    except ValueError:
        return str(v)
