"""Harmonization transforms on canonical frames.

Every function here is pure: it takes canonical frames (or :class:`Result`
objects wrapping them, see :mod:`cgjrkit.core.schema`) and returns a new
:class:`~cgjrkit.core.provenance.Result` that

* carries forward the provenance, caveats and meta of every input, and
* appends one ``Provenance(source="cgjrkit", tool="harmonize.<name>",
  params={...})`` entry recording the exact parameters used.

Functions
---------
=====================  =====================================================
``rebase_index``       index a series to a base period (or base window) = 100
``growth_rate``        period-on-period growth, percent or log (x100)
``period_average``     averages (or CAGRs) over named windows such as
                       ``{"2011-19": ("2011", "2019")}``
``cagr``               compound annual growth rate between two periods
``pct_of_frontier``    value as a percent of a frontier country (default USA)
``deflate``            nominal -> real using a deflator, optionally rebased
``ratio``              numerator / denominator x scale (e.g. % of GDP);
                       aligns periods and refuses mismatched frequencies
``fiscal_to_calendar`` fiscal-year values ("FY2024/25") -> calendar years
``calendar_to_fiscal`` calendar years -> fiscal years (same weighting)
``quarterly_to_fiscal`` exact fiscal-year totals/means from quarterly data
``splice``             join an old and a new series at an overlap period
=====================  =====================================================

Conventions
-----------
* Periods are canonical strings: ``"2024"`` (annual), ``"2024Q1"``
  (quarterly), ``"2024-03"`` (monthly) and ``"FY2024/25"`` (fiscal year that
  *starts* in 2024). A computation group never mixes frequencies; functions
  that combine two frames (``ratio``, ``deflate``, ``splice``) raise
  :class:`FrequencyMismatchError` when the frequencies differ.
* Transformed series get a new indicator code, ``"<code>|<suffix>"`` (for
  example ``"NY.GDP.MKTP.KD|idx2011"``), so they never collide with the input
  series when frames are concatenated. Pass ``suffix=""`` to keep the code.
* PPP bases are never mixed silently. Units that encode a PPP base (see
  :func:`cgjrkit.core.schema.ppp_base`) are compared within every computation
  group; different bases raise :class:`PPPMixError` (``on_ppp_mix="raise"``,
  the default) or attach a ``block`` caveat (``on_ppp_mix="block"``). A unit
  whose PPP base cannot be parsed counts as base ``"unknown"`` and therefore
  also conflicts with any explicit base. The one deliberate bridge is a ratio
  :func:`splice`, which checks old and new separately and records the
  vintage change in its ``series_spliced`` caveat.
* Aggregate codes are tracked in ``Result.meta["aggregates"]``; when a method
  combines two inputs, the output keeps the union of their codes.
* ``obs_status`` of a derived value combines the statuses of the inputs used,
  keeping the most important flag in this order: break, forecast,
  provisional, modelled, estimate, imputed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable, Literal, Mapping, Sequence

import numpy as np
import pandas as pd

from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import COLUMNS, make_frame, ppp_base, validate_frame

__all__ = [
    "HarmonizeError",
    "FrequencyMismatchError",
    "PPPMixError",
    "Period",
    "parse_period",
    "period_frequency",
    "format_period",
    "fiscal_label",
    "combine_status",
    "cagr_value",
    "check_ppp",
    "rebase_index",
    "growth_rate",
    "period_average",
    "cagr",
    "pct_of_frontier",
    "deflate",
    "ratio",
    "fiscal_to_calendar",
    "calendar_to_fiscal",
    "quarterly_to_fiscal",
    "splice",
]

Frame = Result | pd.DataFrame
PPPPolicy = Literal["raise", "block"]
PeriodSpec = str | tuple[str, str] | list[str]

#: Order in which ``obs_status`` flags win when values are combined.
STATUS_PRIORITY: tuple[str, ...] = ("break", "forecast", "provisional", "modelled", "estimate", "imputed")

#: Maximum number of items listed in a caveat's evidence (the count is always given).
_EVIDENCE_LIMIT = 25


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------
class HarmonizeError(ValueError):
    """Inputs cannot be combined or transformed as requested."""


class FrequencyMismatchError(HarmonizeError):
    """Inputs (or a computation group) mix period frequencies."""


class PPPMixError(HarmonizeError):
    """Inputs carry different PPP bases (e.g. PPP 2017 and PPP 2021)."""


# ---------------------------------------------------------------------------
# periods
# ---------------------------------------------------------------------------
_ANNUAL_RE = re.compile(r"^(\d{4})$")
_QUARTER_RE = re.compile(r"^(\d{4})-?Q([1-4])$", re.I)
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_FISCAL_RE = re.compile(r"^FY\s?(\d{4})\s?/\s?(\d{2}|\d{4})$", re.I)

_STEPS_PER_YEAR = {"A": 1, "FY": 1, "Q": 4, "M": 12}


@dataclass(frozen=True)
class Period:
    """A parsed canonical period string.

    ``freq`` is ``"A"`` (calendar year), ``"Q"``, ``"M"`` or ``"FY"`` (fiscal
    year labelled by its start year: ``"FY2024/25"`` -> ``year=2024``).
    ``sub`` is the quarter (1-4) or month (1-12), 0 otherwise.
    """

    text: str
    freq: str
    year: int
    sub: int = 0

    @property
    def ordinal(self) -> int:
        """Integer position on the frequency's own time line (consecutive periods differ by 1)."""
        if self.freq == "Q":
            return self.year * 4 + self.sub - 1
        if self.freq == "M":
            return self.year * 12 + self.sub - 1
        return self.year

    @property
    def steps_per_year(self) -> int:
        return _STEPS_PER_YEAR[self.freq]


@lru_cache(maxsize=65536)
def parse_period(text: str) -> Period:
    """Parse ``"2024"``, ``"2024Q1"`` (or ``"2024-Q1"``), ``"2024-03"`` or ``"FY2024/25"``.

    Raises :class:`ValueError` for anything else (period labels produced by
    :func:`period_average`, such as ``"2011-2019"``, are not parseable).
    """
    s = str(text).strip()
    if m := _ANNUAL_RE.match(s):
        return Period(s, "A", int(m.group(1)))
    if m := _QUARTER_RE.match(s):
        return Period(s, "Q", int(m.group(1)), int(m.group(2)))
    if m := _MONTH_RE.match(s):
        month = int(m.group(2))
        if not 1 <= month <= 12:
            raise ValueError(f"invalid month in period {text!r}")
        return Period(s, "M", int(m.group(1)), month)
    if m := _FISCAL_RE.match(s):
        start = int(m.group(1))
        end_txt = m.group(2)
        end = int(end_txt) if len(end_txt) == 4 else (start // 100) * 100 + int(end_txt)
        if len(end_txt) == 2 and end < start:  # e.g. FY1999/00
            end += 100
        if end != start + 1:
            raise ValueError(f"fiscal period {text!r} must span two consecutive years")
        return Period(s, "FY", start)
    raise ValueError(f"unrecognised period {text!r}; expected '2024', '2024Q1', '2024-03' or 'FY2024/25'")


def period_frequency(text: str) -> str:
    """Frequency code of a period string: ``"A"``, ``"Q"``, ``"M"`` or ``"FY"``."""
    return parse_period(text).freq


def fiscal_label(start_year: int) -> str:
    """Canonical fiscal-year label: ``fiscal_label(2024) == "FY2024/25"``."""
    return f"FY{start_year}/{(start_year + 1) % 100:02d}"


def format_period(freq: str, ordinal: int) -> str:
    """Inverse of :attr:`Period.ordinal`."""
    if freq == "A":
        return f"{ordinal}"
    if freq == "FY":
        return fiscal_label(ordinal)
    if freq == "Q":
        return f"{ordinal // 4}Q{ordinal % 4 + 1}"
    if freq == "M":
        return f"{ordinal // 12}-{ordinal % 12 + 1:02d}"
    raise ValueError(f"unknown frequency {freq!r}")


def _window(spec: PeriodSpec) -> tuple[Period, Period]:
    """A single period or a ``(start, end)`` pair -> two parsed periods (inclusive)."""
    if isinstance(spec, (tuple, list)):
        if len(spec) != 2:
            raise ValueError(f"a period window must be (start, end), got {spec!r}")
        lo, hi = parse_period(str(spec[0])), parse_period(str(spec[1]))
    else:
        lo = hi = parse_period(str(spec))
    if lo.freq != hi.freq:
        raise FrequencyMismatchError(f"window {spec!r} mixes frequencies {lo.freq} and {hi.freq}")
    if hi.ordinal < lo.ordinal:
        raise ValueError(f"window {spec!r} ends before it starts")
    return lo, hi


def _window_label(lo: Period, hi: Period) -> str:
    return lo.text if lo == hi else f"{lo.text}-{hi.text}"


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def combine_status(statuses: Iterable[Any]) -> str:
    """Combine ``obs_status`` flags, keeping the most important one (see :data:`STATUS_PRIORITY`)."""
    present = {str(s) for s in statuses if isinstance(s, str) and s and s != "nan"}
    for flag in STATUS_PRIORITY:
        if flag in present:
            return flag
    return sorted(present)[0] if present else ""


def cagr_value(first: float, last: float, years: float) -> float:
    """Compound annual growth rate in percent: ``100 * ((last / first) ** (1 / years) - 1)``.

    Returns NaN when either value is missing or non-positive, or ``years <= 0``.
    """
    if years <= 0 or not (np.isfinite(first) and np.isfinite(last)) or first <= 0 or last <= 0:
        return float("nan")
    return 100.0 * ((last / first) ** (1.0 / years) - 1.0)


def _jsonable(value: Any) -> Any:
    """Make parameters JSON-safe and deterministic for provenance."""
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_jsonable(v) for v in value]
        return sorted(items, key=str) if isinstance(value, (set, frozenset)) else items
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _as_result(obj: Frame, name: str = "frame") -> Result:
    """Wrap a frame in a Result (copying), normalise dtypes and validate the schema."""
    if isinstance(obj, Result):
        df, prov, cav, meta = obj.data, list(obj.provenance), list(obj.caveats), dict(obj.meta)
    elif isinstance(obj, pd.DataFrame):
        df, prov, cav, meta = obj, [], [], {}
    else:
        raise TypeError(f"{name} must be a Result or a pandas DataFrame, not {type(obj).__name__}")
    if any(c not in df.columns for c in COLUMNS):
        df = make_frame(df)
    else:
        df = df.copy()
        df["value"] = pd.to_numeric(df["value"], errors="coerce").astype("float64")
        for col in COLUMNS:
            if col != "value":
                df[col] = df[col].fillna("").astype(str)
    validate_frame(df)
    return Result(data=df.reset_index(drop=True), provenance=prov, caveats=cav, meta=meta)


def _finish(
    inputs: Sequence[Result],
    data: pd.DataFrame,
    tool: str,
    params: Mapping[str, Any],
    caveats: Iterable[Caveat] = (),
    notes: Iterable[str] = (),
) -> Result:
    """Build the output Result: inputs' provenance + caveats + meta, plus a cgjrkit Provenance entry."""
    provenance: list[Provenance] = []
    for res in inputs:
        for p in res.provenance:
            if p not in provenance:
                provenance.append(p)
    provenance.append(
        Provenance(
            source="cgjrkit",
            dataset="harmonize",
            tool=f"harmonize.{tool}",
            params=_jsonable(dict(params)),
            notes=list(notes),
        )
    )
    out = Result(data=data, provenance=provenance)
    for res in inputs:
        _merge_meta(out.meta, res.meta)
        for c in res.caveats:
            out.add_caveat(c)
    for c in caveats:
        out.add_caveat(c)
    return out


def _aggregate_list(value: Any) -> list[str]:
    """Aggregate codes held in a ``meta["aggregates"]`` value (list, set, ``{code: name}`` dict or one code)."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Mapping):
        return [str(k) for k in value]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(v) for v in value]
    return [str(value)]


def _merge_aggregates(a: Any, b: Any) -> list[str] | dict[str, Any]:
    """Union of two ``meta["aggregates"]`` values.

    Two ``{code: name}`` dicts merge into one dict (sorted by code); any other
    combination becomes the sorted list of codes, the connectors' convention.
    """
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        merged = {str(k): v for k, v in a.items()}
        merged.update({str(k): v for k, v in b.items()})
        return dict(sorted(merged.items()))
    return sorted(set(_aggregate_list(a)) | set(_aggregate_list(b)))


def _merge_meta(target: dict, source: Mapping[str, Any]) -> dict:
    """Merge input meta into ``target`` in place (and return it).

    Keys follow ``dict.update`` except ``"aggregates"``, which becomes the
    union of both inputs' aggregate codes so that methods combining two inputs
    (``ratio``, ``deflate``, ``splice``, ``income_conditional_fit``) keep every
    code that :func:`cgjrkit.methods.benchmark.aggregate_codes` must drop.
    """
    for key, value in (source or {}).items():
        if key == "aggregates" and "aggregates" in target:
            target[key] = _merge_aggregates(target[key], value)
        elif key == "aggregates":
            target[key] = (dict(sorted((str(k), v) for k, v in value.items())) if isinstance(value, Mapping)
                           else sorted(set(_aggregate_list(value))))
        else:
            target[key] = value
    return target


_ISO3_CODE_RE = re.compile(r"^[A-Z]{3}$")


def _aggregate_codes(*results: Result) -> frozenset[str]:
    """Codes treated as aggregates: inputs' ``meta["aggregates"]`` plus the World Bank aggregate codes."""
    codes: set[str] = set()
    for res in results:
        if isinstance(res.meta, Mapping):
            codes.update(_aggregate_list(res.meta.get("aggregates")))
    try:  # static snapshot of World Bank aggregate codes (pure constant, no network)
        from cgjrkit.connectors.worldbank import KNOWN_AGGREGATES
    except Exception:  # pragma: no cover - connector unavailable
        KNOWN_AGGREGATES = frozenset()
    return frozenset(codes | set(KNOWN_AGGREGATES))


def _is_aggregate(iso3: pd.Series, aggs: frozenset[str]) -> pd.Series:
    """Boolean mask: the code is a known aggregate or not three capital letters (e.g. ``"STRUCT:median"``)."""
    iso = iso3.astype(str)
    return (~iso.str.fullmatch(_ISO3_CODE_RE.pattern)) | iso.isin(aggs)


def _mixed_units_caveat(df: pd.DataFrame, by: Sequence[str], context: str, message: str | None = None) -> list[Caveat]:
    """``mixed_units`` warn caveat when one ``by`` group carries more than one (non-empty) unit label."""
    mixed = []
    if len(df):
        for key, g in _groups(df, by):
            units = sorted({u for u in g["unit"] if u})
            if len(units) > 1:
                mixed.append({"group": _key_text(key), "units": units})
    if not mixed:
        return []
    msg = message or (f"{context}: one series carries several unit labels (different editions, base years or "
                      "units?); check they are comparable before combining.")
    return [Caveat("mixed_units", "warn", msg, {"groups": _limited(mixed), "n_groups": len(mixed), "context": context})]


def _empty_like(df: pd.DataFrame) -> pd.DataFrame:
    return df.iloc[0:0].copy()


def _sort(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.reset_index(drop=True)
    return df.sort_values(["indicator", "iso3", "period"], kind="mergesort").reset_index(drop=True)


def _key_text(key: Any) -> str:
    return "/".join(str(k) for k in (key if isinstance(key, tuple) else (key,)))


def _groups(df: pd.DataFrame, by: Sequence[str]):
    """Iterate ``(key_tuple, group)`` in sorted order; ``by=[]`` yields the whole frame."""
    by = list(by)
    if not by:
        yield (), df
        return
    for key, g in df.groupby(by, sort=True, dropna=False):
        yield (key if isinstance(key, tuple) else (key,)), g


def _check_by(df: pd.DataFrame, by: Sequence[str]) -> list[str]:
    by = list(by)
    missing = [c for c in by if c not in df.columns]
    if missing:
        raise ValueError(f"'by' columns not in frame: {missing}")
    if "period" in by or "value" in by:
        raise ValueError("'by' must not contain 'period' or 'value'")
    return by


def _unique_periods(g: pd.DataFrame, context: str) -> None:
    dup = g["period"].duplicated()
    if dup.any():
        raise HarmonizeError(
            f"{context}: more than one observation per period (e.g. {g.loc[dup, 'period'].iloc[0]!r}); "
            "add columns to 'by' so that each group is a single series"
        )


def _with_ordinals(df: pd.DataFrame, by: list[str], context: str) -> pd.DataFrame:
    """Copy of ``df`` with ``_ord``/``_freq`` columns; checks one frequency and unique periods per group."""
    work = df.copy()
    parsed = [parse_period(p) for p in work["period"]]
    work["_ord"] = pd.Series([p.ordinal for p in parsed], index=work.index, dtype="int64")
    work["_freq"] = pd.Series([p.freq for p in parsed], index=work.index, dtype=object)
    if len(work):
        nfreq = work.groupby(by)["_freq"].nunique() if by else pd.Series([work["_freq"].nunique()])
        if (nfreq > 1).any():
            bad = nfreq[nfreq > 1].index[0]
            raise FrequencyMismatchError(f"{context}: group {_key_text(bad)} mixes period frequencies")
        dup = work.duplicated(subset=by + ["period"])
        if dup.any():
            raise HarmonizeError(
                f"{context}: more than one observation per period (e.g. {work.loc[dup, 'period'].iloc[0]!r}); "
                "add columns to 'by' so that each group is a single series"
            )
    return work


def _with_suffix(df: pd.DataFrame, suffix: str, label_suffix: str) -> pd.DataFrame:
    if suffix:
        df["indicator"] = df["indicator"] + "|" + suffix
    if label_suffix:
        df["indicator_label"] = [f"{lab} ({label_suffix})" if lab else label_suffix for lab in df["indicator_label"]]
    return df


def _limited(items: list[Any]) -> list[Any]:
    return items[:_EVIDENCE_LIMIT]


# ---------------------------------------------------------------------------
# PPP guard
# ---------------------------------------------------------------------------
def _ppp_guard(mixed: list[dict], context: str, on_ppp_mix: PPPPolicy) -> list[Caveat]:
    """Raise or return a block caveat when ``mixed`` (groups with >1 PPP base) is non-empty."""
    if on_ppp_mix not in ("raise", "block"):
        raise ValueError("on_ppp_mix must be 'raise' or 'block'")
    if not mixed:
        return []
    bases = sorted({b for m in mixed for b in m["ppp_bases"]})
    msg = (
        f"{context}: inputs carry different PPP bases {bases}. PPP vintages (e.g. ICP 2017 vs 2021) are not "
        "comparable; use a single vintage (pin the source edition) before combining."
    )
    if on_ppp_mix == "raise":
        raise PPPMixError(msg + f" Groups: {_limited(mixed)}")
    return [Caveat("ppp_base_mix", "block", msg, {"groups": _limited(mixed), "n_groups": len(mixed), "context": context})]


def _ppp_check_groups(df: pd.DataFrame, by: Sequence[str], context: str, on_ppp_mix: PPPPolicy,
                      unit_cols: Sequence[str] = ("unit",)) -> list[Caveat]:
    """PPP bases found in ``unit_cols`` must agree within each ``by`` group (all rows when ``by`` is empty)."""
    by = list(by)
    if df.empty:
        return _ppp_guard([], context, on_ppp_mix)
    long = pd.concat([df[by + [col]].rename(columns={col: "_unit"}) for col in unit_cols], ignore_index=True)
    long["_unit"] = long["_unit"].fillna("").astype(str)
    base_of = {u: ppp_base(u) for u in pd.unique(long["_unit"])}
    long["_ppp"] = [base_of[u] for u in long["_unit"]]
    long = long[long["_ppp"].notna()]
    mixed = []
    if len(long):
        gb = by or ["_all"]
        if not by:
            long["_all"] = "all"
        found = long.groupby(gb, sort=True)["_ppp"].agg(lambda s: tuple(sorted(set(s))))
        mixed = [{"group": _key_text(k), "ppp_bases": list(v)} for k, v in found.items() if len(v) > 1]
    return _ppp_guard(mixed, context, on_ppp_mix)


def check_ppp(*frames: Frame, by: Sequence[str] = ()) -> list[Caveat]:
    """Return a ``block`` caveat if the frames (together, or within each ``by`` group) mix PPP bases.

    ``check_ppp(a, b)`` compares every unit in ``a`` and ``b``;
    ``check_ppp(a, by=["indicator"])`` compares units within each indicator.
    Never raises; use it to screen inputs before charting.
    """
    data = pd.concat([_as_result(f).data for f in frames], ignore_index=True) if frames else pd.DataFrame()
    if data.empty:
        return []
    return _ppp_check_groups(data, list(by), "check_ppp", "block")


def _price_basis(unit: str) -> str | None:
    """"current" or "constant" when a unit label states its price basis, else None."""
    u = str(unit).lower()
    if re.search(r"\b(current|nominal)\b", u):
        return "current"
    if re.search(r"\b(constant|chained|chain-linked|real)\b", u):
        return "constant"
    return None


# ---------------------------------------------------------------------------
# rebase_index
# ---------------------------------------------------------------------------
def _base_table(df: pd.DataFrame, by: list[str], lo: Period, hi: Period, context: str
                ) -> tuple[pd.DataFrame, list[dict], list[str]]:
    """Mean value over the base window per group.

    Returns ``(table, problems, forecast_groups)``: ``table`` has the group
    columns (``_all`` when ``by`` is empty) plus ``_base``; groups without a
    value for every base period, or with a zero base, are listed in
    ``problems`` instead.
    """
    work = _with_ordinals(df, by, context)
    gb = by or ["_all"]
    if not by:
        work["_all"] = ""
    wrong = work[work["_freq"] != lo.freq]
    if len(wrong):
        key = tuple(wrong.iloc[0][gb])
        raise FrequencyMismatchError(
            f"{context}: base period {lo.text!r} ({lo.freq}) does not match the frequency of group "
            f"{_key_text(key)} ({wrong.iloc[0]['_freq']})"
        )
    expected = hi.ordinal - lo.ordinal + 1
    inwin = work[(work["_ord"] >= lo.ordinal) & (work["_ord"] <= hi.ordinal)]
    groups = work.groupby(gb, sort=True).size().index
    stats = inwin[inwin["value"].notna()].groupby(gb, sort=True)["value"].agg(["mean", "count"]).reindex(groups)
    stats["count"] = stats["count"].fillna(0)
    incomplete = stats["count"] < expected
    zero = ~incomplete & ~(np.isfinite(stats["mean"]) & (stats["mean"] != 0))
    problems = [{"group": _key_text(k), "reason": f"{int(c)} of {expected} base periods available"}
                for k, c in stats.loc[incomplete, "count"].items()]
    problems += [{"group": _key_text(k), "reason": "base value is zero"} for k in stats.index[zero]]
    good = stats[~incomplete & ~zero]
    fc = inwin[inwin["obs_status"] == "forecast"].groupby(gb, sort=True).size()
    forecast = [_key_text(k) for k in fc.index if k in good.index]
    table = good[["mean"]].rename(columns={"mean": "_base"}).reset_index()
    return table, problems, forecast


def _base_values(df: pd.DataFrame, by: list[str], lo: Period, hi: Period, context: str) -> tuple[dict, list[dict]]:
    """``{group key tuple: base}`` (key ``()`` when ``by`` is empty) and the problem list."""
    table, problems, _ = _base_table(df, by, lo, hi, context)
    if not by:
        return ({(): float(table["_base"].iloc[0])} if len(table) else {}), problems
    return {tuple(r[:-1]): float(r[-1]) for r in table[by + ["_base"]].itertuples(index=False, name=None)}, problems


def rebase_index(
    frame: Frame,
    base_period: PeriodSpec,
    by: Sequence[str] = ("indicator", "iso3"),
    *,
    base_value: float = 100.0,
    suffix: str | None = None,
    on_ppp_mix: PPPPolicy = "raise",
) -> Result:
    """Index each series to ``base_period`` = ``base_value`` (default 100).

    ``base_period`` is one period (``"2011"``, ``"2007-12"``) or an inclusive
    window ``("2014", "2016")`` whose mean becomes the base. Each ``by`` group
    must be a single series (one observation per period) of one frequency.
    Groups without a value for every base period are dropped and listed in a
    ``rebase_base_missing`` caveat; a negative base keeps the sign convention
    of the ratio and raises a ``rebase_negative_base`` caveat.

    Output unit: ``"index (2011=100)"``; indicator code ``"<code>|idx2011"``.

    Example (Kenya Fig 3, 2011=100)::

        rebase_index(gdp_components, "2011")
    """
    res = _as_result(frame)
    df = res.data
    by = _check_by(df, by)
    lo, hi = _window(base_period)
    label = _window_label(lo, hi)
    caveats = _ppp_check_groups(df, by, "rebase_index", on_ppp_mix)
    table, problems, forecast_base = _base_table(df, by, lo, hi, "rebase_index")

    gb = by or ["_all"]
    out = (df if by else df.assign(_all="")).merge(table, on=gb, how="inner", validate="many_to_one")
    negative = [_key_text(k) for k in table.loc[table["_base"] < 0, gb].itertuples(index=False, name=None)]
    out["value"] = out["value"] / out["_base"] * base_value
    out["unit"] = f"index ({label}={base_value:g})"
    out = out[list(df.columns)]
    out = _with_suffix(out, f"idx{label}" if suffix is None else suffix, f"index, {label}={base_value:g}")

    if problems:
        caveats.append(Caveat("rebase_base_missing", "warn",
                              f"{len(problems)} series dropped: no complete value for base period {label}.",
                              {"groups": _limited(problems), "n_groups": len(problems)}))
    if negative:
        caveats.append(Caveat("rebase_negative_base", "warn",
                              f"Base value for {label} is negative; index values flip sign.", {"groups": _limited(negative)}))
    if forecast_base:
        caveats.append(Caveat("rebase_forecast_base", "warn",
                              f"The base period {label} includes forecast values.", {"groups": _limited(forecast_base)}))
    params = {"base_period": label, "by": by, "base_value": base_value, "suffix": suffix, "on_ppp_mix": on_ppp_mix}
    return _finish([res], _sort(out), "rebase_index", params, caveats)


# ---------------------------------------------------------------------------
# growth_rate
# ---------------------------------------------------------------------------
def growth_rate(
    frame: Frame,
    kind: Literal["pct", "log"] = "pct",
    by: Sequence[str] = ("indicator", "iso3"),
    *,
    lag: int = 1,
    scale: float = 100.0,
    suffix: str | None = None,
    on_ppp_mix: PPPPolicy = "raise",
) -> Result:
    """Growth over ``lag`` periods of the series' own frequency.

    * ``kind="pct"``: ``scale * (x_t / x_{t-lag} - 1)`` (needs a positive base
      ``x_{t-lag}``)
    * ``kind="log"``: ``scale * ln(x_t / x_{t-lag})`` (needs positive values)

    ``lag=4`` on quarterly data gives year-on-year growth. The comparison
    period must be exactly ``lag`` steps earlier: gaps give no value (never a
    silent multi-period growth rate). Periods without a comparison value are
    dropped. A zero or negative base makes a percent change meaningless (from
    -10 to -5 would read as -50%), so those periods are dropped and listed in
    a ``growth_nonpositive_base`` warn caveat: for balances (current account,
    fiscal balance, net FDI) chart the change in levels or in % of GDP
    instead. A value flagged ``break`` makes the growth rate cross a series
    break: the output keeps ``obs_status="break"`` and a
    ``growth_across_break`` caveat is attached.
    """
    if kind not in ("pct", "log"):
        raise ValueError("kind must be 'pct' or 'log'")
    if lag < 1:
        raise ValueError("lag must be a positive integer")
    res = _as_result(frame)
    df = res.data
    by = _check_by(df, by)
    caveats = _ppp_check_groups(df, by, "growth_rate", on_ppp_mix)

    # Vectorised: pair each observation with the one exactly `lag` steps earlier.
    work = _with_ordinals(df, by, "growth_rate")
    prev = work[by + ["_freq", "_ord", "value", "obs_status"]].rename(columns={"value": "_v0", "obs_status": "_s0"})
    prev["_ord"] = prev["_ord"] + lag
    m = work.merge(prev, on=by + ["_freq", "_ord"], how="left", validate="one_to_one", indicator=True)
    has_prev = (m["_merge"] == "both").to_numpy()
    first = (m.groupby(by)["_ord"].transform("min") if by else pd.Series(m["_ord"].min(), index=m.index)).to_numpy()
    v0, v1 = m["_v0"].to_numpy(dtype="float64"), m["value"].to_numpy(dtype="float64")
    finite = has_prev & np.isfinite(v0) & np.isfinite(v1)
    with np.errstate(divide="ignore", invalid="ignore"):
        if kind == "pct":
            # A non-positive base flips (or breaks) the sign of a percent change: drop and report it.
            ok = finite & (v0 > 0)
            growth = scale * (v1 / v0 - 1.0)
            bad_log = np.zeros(len(m), dtype=bool)
            bad_base = finite & ~ok
        else:
            ok = finite & (v0 > 0) & (v1 > 0)
            growth = scale * np.log(v1 / v0)
            bad_log = finite & ~ok
            bad_base = np.zeros(len(m), dtype=bool)
    is_break = (m["obs_status"] == "break").to_numpy()

    def labels(mask: np.ndarray) -> list[str]:
        sub = m.loc[mask, by + ["period"]]
        return ["/".join(map(str, r[:-1])) + "@" + str(r[-1]) for r in sub.itertuples(index=False, name=None)]

    gaps = labels(~has_prev & (m["_ord"].to_numpy() - lag >= first))
    nonpositive = labels(bad_log)
    nonpositive_base = labels(bad_base)
    breaks = labels(ok & is_break)

    out = m.loc[ok, list(df.columns)].copy()
    out["value"] = growth[ok]
    out["obs_status"] = [
        "break" if s1 == "break" else combine_status([s1, s0 if s0 != "break" else ""])
        for s1, s0 in zip(m.loc[ok, "obs_status"], m.loc[ok, "_s0"])
    ]
    out["value"] = out["value"].astype("float64")
    tag = "pct" if kind == "pct" else "log"
    if scale == 100:
        unit = "% change" if kind == "pct" else "log change (x100)"
    else:
        unit = f"change (x{scale:g})" if kind == "pct" else f"log change (x{scale:g})"
    if lag != 1:
        unit += f" over {lag} periods"
    out["unit"] = unit
    default_suffix = f"gr_{tag}" + (f"_lag{lag}" if lag != 1 else "")
    out = _with_suffix(out, default_suffix if suffix is None else suffix,
                       ("growth, %" if kind == "pct" else "log growth") + (f", lag {lag}" if lag != 1 else ""))

    if gaps:
        caveats.append(Caveat("growth_gap", "info", f"{len(gaps)} growth rates not computed because the comparison period is missing.",
                              {"at": _limited(gaps), "n": len(gaps)}))
    if nonpositive:
        caveats.append(Caveat("growth_log_nonpositive", "warn", "Log growth undefined for non-positive values; those periods are dropped.",
                              {"at": _limited(nonpositive), "n": len(nonpositive)}))
    if nonpositive_base:
        caveats.append(Caveat("growth_nonpositive_base", "warn",
                              f"{len(nonpositive_base)} percent changes dropped: the base value is zero or negative, "
                              "so the sign of the change would be wrong (-10 -> -5 would read as -50%). For balances "
                              "use the change in levels or in % of GDP.",
                              {"at": _limited(nonpositive_base), "n": len(nonpositive_base)}))
    if breaks:
        caveats.append(Caveat("growth_across_break", "warn",
                              "Some growth rates compare observations across a series break.", {"at": _limited(breaks), "n": len(breaks)}))
    params = {"kind": kind, "by": by, "lag": lag, "scale": scale, "suffix": suffix, "on_ppp_mix": on_ppp_mix}
    return _finish([res], _sort(out), "growth_rate", params, caveats)


# ---------------------------------------------------------------------------
# period_average / cagr
# ---------------------------------------------------------------------------
def _period_stats(
    res: Result,
    periods: Mapping[str, PeriodSpec],
    method: str,
    by: list[str],
    require_complete: bool,
    suffix: str | None,
    on_ppp_mix: PPPPolicy,
    tool: str,
) -> Result:
    if method not in ("mean", "cagr"):
        raise ValueError("method must be 'mean' or 'cagr'")
    if not periods:
        raise ValueError("periods must name at least one window, e.g. {'2011-19': ('2011', '2019')}")
    df = res.data
    windows = {str(label): _window(spec) for label, spec in periods.items()}
    caveats = _ppp_check_groups(df, by, tool, on_ppp_mix)

    work = _with_ordinals(df, by, tool)
    gb = by or ["_all"]
    if not by:
        work["_all"] = ""
    # one template row per series carries the metadata (labels, unit, country)
    templates = work.drop_duplicates(subset=gb, keep="first").set_index(gb).sort_index()
    group_freq = templates["_freq"]
    steps = group_freq.map(_STEPS_PER_YEAR).astype("float64")

    parts, incomplete, missing, forecast = [], [], [], []
    for label, (lo, hi) in windows.items():
        wrong = group_freq[group_freq != lo.freq]
        if len(wrong):
            raise FrequencyMismatchError(
                f"{tool}: window {label!r} is {lo.freq} but group {_key_text(wrong.index[0])} is {wrong.iloc[0]}")
        inside = work[(work["_ord"] >= lo.ordinal) & (work["_ord"] <= hi.ordinal) & work["value"].notna()]
        expected = hi.ordinal - lo.ordinal + 1
        if method == "mean":
            agg = inside.groupby(gb, sort=True)["value"].agg(["mean", "size"]).reindex(templates.index)
            absent = agg["mean"].isna()
            short = ~absent & (agg["size"] < expected)
            missing += [f"{_key_text(k)}@{label}" for k in agg.index[absent]]
            incomplete += [{"group": _key_text(k), "window": label, "n": int(n), "expected": expected}
                           for k, n in agg.loc[short, "size"].items()]
            keep = ~absent & ~short if require_complete else ~absent
            values = agg.loc[keep, "mean"]
            used = inside
        else:
            if hi.ordinal <= lo.ordinal:
                raise ValueError(f"{tool}: window {label!r} must span more than one period for a CAGR")
            ends = pd.DataFrame({
                "lo": inside[inside["_ord"] == lo.ordinal].set_index(gb)["value"],
                "hi": inside[inside["_ord"] == hi.ordinal].set_index(gb)["value"],
            }).reindex(templates.index)
            years = (hi.ordinal - lo.ordinal) / steps
            with np.errstate(divide="ignore", invalid="ignore"):
                growth = 100.0 * ((ends["hi"] / ends["lo"]) ** (1.0 / years) - 1.0)
            present = ends["lo"].notna() & ends["hi"].notna()
            positive = present & (ends["lo"] > 0) & (ends["hi"] > 0)
            missing += [f"{_key_text(k)}@{label}" for k in ends.index[~present]]
            missing += [f"{_key_text(k)}@{label} (non-positive level)" for k in ends.index[present & ~positive]]
            values = growth[positive]
            used = inside[inside["_ord"].isin([lo.ordinal, hi.ordinal])]
        status = used.groupby(gb, sort=True)["obs_status"].agg(combine_status)
        fc = used[used["obs_status"] == "forecast"].groupby(gb, sort=True).size()
        forecast += [f"{_key_text(k)}@{label}" for k in fc.index if k in values.index]
        rows = templates.loc[values.index].copy()
        rows["value"] = values.astype("float64")
        rows["period"] = label
        rows["obs_status"] = status.reindex(values.index).fillna("").astype(str)
        if method == "cagr":
            rows["unit"] = "% per year"
        parts.append(rows.reset_index())

    out = pd.concat(parts, ignore_index=True)[list(df.columns)] if parts else _empty_like(df)
    out["value"] = out["value"].astype("float64")
    default_suffix = "avg" if method == "mean" else "cagr"
    out = _with_suffix(out, default_suffix if suffix is None else suffix,
                       "period average" if method == "mean" else "CAGR, % per year")

    if incomplete:
        caveats.append(Caveat("period_average_incomplete", "warn",
                              f"{len(incomplete)} window averages use fewer observations than the window length"
                              + (" and were dropped." if require_complete else "."),
                              {"windows": _limited(incomplete), "n": len(incomplete)}))
    if missing:
        caveats.append(Caveat("period_window_missing", "info", f"{len(missing)} windows had no usable data.",
                              {"at": _limited(missing), "n": len(missing)}))
    if forecast:
        caveats.append(Caveat("period_includes_forecast", "warn", "Some windows include forecast values.",
                              {"at": _limited(forecast), "n": len(forecast)}))
    params = {
        "periods": {k: [lo.text, hi.text] for k, (lo, hi) in windows.items()},
        "method": method,
        "by": by,
        "require_complete": require_complete,
        "suffix": suffix,
        "on_ppp_mix": on_ppp_mix,
    }
    notes = []
    if method == "cagr":
        notes.append("CAGR uses the levels at the window's first and last periods: 100*((x_end/x_start)^(1/years)-1)")
    else:
        notes.append("Arithmetic mean of the observations whose periods fall inside the inclusive window")
    return _finish([res], _sort(out), tool, params, caveats, notes)


def period_average(
    frame: Frame,
    periods: Mapping[str, PeriodSpec],
    method: Literal["mean", "cagr"] = "mean",
    by: Sequence[str] = ("indicator", "iso3"),
    *,
    require_complete: bool = False,
    suffix: str | None = None,
    on_ppp_mix: PPPPolicy = "raise",
) -> Result:
    """Average (or CAGR) of each series over named, inclusive windows.

    ``periods`` maps an output label to ``(start, end)``, e.g.
    ``{"2011-2019": ("2011", "2019"), "2021-2023": ("2021", "2023")}`` (Kenya
    Fig 23). The output ``period`` is the label.

    * ``method="mean"``: arithmetic mean of the observations inside the
      window (use it on growth rates or ratios). Windows with fewer
      observations than periods get a ``period_average_incomplete`` caveat
      (and are dropped if ``require_complete=True``).
    * ``method="cagr"``: compound annual growth between the *levels* at the
      first and last period, ``100*((x_end/x_start)^(1/years)-1)``, in
      ``"% per year"``. To match an "average growth 2011-2019" computed from
      annual growth rates, the CAGR window starts one year earlier
      (``("2010", "2019")``).
    """
    res = _as_result(frame)
    by = _check_by(res.data, by)
    return _period_stats(res, periods, method, by, require_complete, suffix, on_ppp_mix, "period_average")


def cagr(
    frame: Frame,
    start: str,
    end: str,
    by: Sequence[str] = ("indicator", "iso3"),
    *,
    label: str | None = None,
    suffix: str | None = None,
    on_ppp_mix: PPPPolicy = "raise",
) -> Result:
    """Compound annual growth rate (percent per year) between the levels at ``start`` and ``end``.

    ``years`` is measured on the series' frequency: ``("2010Q4", "2019Q4")``
    spans 9 years. Output period is ``label`` (default ``"<start>-<end>"``).
    """
    res = _as_result(frame)
    by = _check_by(res.data, by)
    lab = label or f"{start}-{end}"
    return _period_stats(res, {lab: (start, end)}, "cagr", by, False, suffix, on_ppp_mix, "cagr")


# ---------------------------------------------------------------------------
# pct_of_frontier
# ---------------------------------------------------------------------------
def pct_of_frontier(
    frame: Frame,
    frontier_iso3: str = "USA",
    by: Sequence[str] = ("indicator",),
    *,
    scale: float = 100.0,
    keep_frontier: bool = True,
    max_over_aggregates: bool = False,
    suffix: str | None = None,
    on_ppp_mix: PPPPolicy = "raise",
) -> Result:
    """Each value as a percent of the frontier's value in the same period.

    ``frontier_iso3`` is a country code (default ``"USA"``, as in Indonesia
    Fig 1.1, real GDP per capita as % of the US) or ``"max"`` for the best
    performing *country* among the rows of that indicator-period. With
    ``"max"``, aggregate codes (the input's ``meta["aggregates"]``, the World
    Bank aggregates such as ``HIC`` or ``WLD``, and any code that is not three
    capital letters, e.g. peer aggregates) are not frontier candidates, as in
    :func:`cgjrkit.methods.benchmark.distance_to_frontier`; they stay in the
    output, expressed as % of that frontier (``max_over_aggregates=True``
    restores the plain maximum over every row). Indicator-periods without a
    positive frontier value are dropped (``frontier_missing`` caveat). All
    countries of an indicator must share one PPP base.
    """
    res = _as_result(frame)
    df = res.data
    by = _check_by(df, by)
    caveats = _ppp_check_groups(df, by, "pct_of_frontier", on_ppp_mix)
    caveats += _mixed_units_caveat(df, by, "pct_of_frontier", "Countries of one indicator carry different unit labels.")
    is_agg = _is_aggregate(df["iso3"], _aggregate_codes(res)) if frontier_iso3 == "max" and not max_over_aggregates \
        else pd.Series(False, index=df.index)
    excluded_aggs = sorted(set(df.loc[is_agg, "iso3"]))

    parts, missing = [], []
    for key, g in _groups(df, by + ["period"]):
        if frontier_iso3 == "max":
            ref = g.loc[~is_agg[g.index], "value"].max()
        else:
            hit = g.loc[g["iso3"] == frontier_iso3, "value"]
            if len(hit) > 1:
                raise HarmonizeError(f"pct_of_frontier: frontier {frontier_iso3} appears twice in group {_key_text(key)}")
            ref = hit.iloc[0] if len(hit) else float("nan")
        if not (np.isfinite(ref) and ref > 0):
            missing.append(_key_text(key))
            continue
        g = g.copy()
        g["value"] = g["value"] / ref * scale
        if not keep_frontier and frontier_iso3 != "max":
            g = g[g["iso3"] != frontier_iso3]
        parts.append(g)
    out = pd.concat(parts, ignore_index=True) if parts else _empty_like(df)
    name = "frontier (max)" if frontier_iso3 == "max" else frontier_iso3
    out["unit"] = f"% of {name}" if scale == 100 else f"ratio to {name} (x{scale:g})"
    out = _with_suffix(out, f"pct_{frontier_iso3}" if suffix is None else suffix, f"% of {name}")

    if missing:
        caveats.append(Caveat("frontier_missing", "warn", f"No positive {name} value for {len(missing)} indicator-periods; dropped.",
                              {"groups": _limited(missing), "n": len(missing)}))
    if excluded_aggs:
        caveats.append(Caveat("frontier_excludes_aggregates", "info",
                              "Aggregate rows were not candidates for the 'max' frontier (they are kept in the output "
                              "as % of the best country).", {"aggregates": _limited(excluded_aggs),
                                                             "n": len(excluded_aggs)}))
    params = {"frontier_iso3": frontier_iso3, "by": by, "scale": scale, "keep_frontier": keep_frontier,
              "max_over_aggregates": max_over_aggregates, "suffix": suffix, "on_ppp_mix": on_ppp_mix}
    return _finish([res], _sort(out), "pct_of_frontier", params, caveats)


# ---------------------------------------------------------------------------
# alignment helpers for two-frame operations
# ---------------------------------------------------------------------------
def _frequency_guard(a: pd.DataFrame, b: pd.DataFrame, context: str, names: tuple[str, str]) -> str:
    fa = {period_frequency(p) for p in a["period"]}
    fb = {period_frequency(p) for p in b["period"]}
    if len(fa) > 1 or len(fb) > 1 or (fa and fb and fa != fb):
        hint = ""
        if {"FY", "A"} <= (fa | fb):
            hint = " Convert first with fiscal_to_calendar() or calendar_to_fiscal()."
        raise FrequencyMismatchError(
            f"{context}: {names[0]} frequencies {sorted(fa)} vs {names[1]} frequencies {sorted(fb)}; "
            f"both must share one frequency.{hint}"
        )
    return fa.pop() if fa else (fb.pop() if fb else "")


def _single_series_per_key(df: pd.DataFrame, keys: list[str], context: str) -> None:
    """Each match key (excluding period) must map to one series."""
    series_cols = ["source", "dataset", "indicator"]
    if keys:
        counts = df.groupby(keys)[series_cols].apply(lambda g: len(g.drop_duplicates()))
        bad = counts[counts > 1]
        if len(bad):
            raise HarmonizeError(f"{context}: more than one series per {keys} (e.g. {bad.index[0]!r}); filter to one indicator first")
    elif len(df[series_cols].drop_duplicates()) > 1:
        raise HarmonizeError(f"{context}: must contain a single series when matching on period only")
    dup = df.duplicated(subset=keys + ["period"])
    if dup.any():
        raise HarmonizeError(f"{context}: duplicate observations for the match keys {keys + ['period']}")


def _match_keys(match_on: Sequence[str]) -> list[str]:
    keys = list(match_on)
    if "period" not in keys:
        raise ValueError("match_on must include 'period'")
    return [k for k in keys if k != "period"]


# ---------------------------------------------------------------------------
# deflate
# ---------------------------------------------------------------------------
def _constant_unit(unit: str, label: str) -> str:
    if re.search(r"\bcurrent\b", unit, flags=re.I):
        return re.sub(r"\bcurrent\b", f"constant {label}", unit, count=1, flags=re.I)
    return f"{unit}, constant {label} prices" if unit else f"constant {label} prices"


def deflate(
    nominal: Frame,
    deflator: Frame,
    base_period: PeriodSpec | None = None,
    *,
    match_on: Sequence[str] = ("iso3", "period"),
    suffix: str | None = None,
    on_ppp_mix: PPPPolicy = "raise",
) -> Result:
    """Convert nominal values to real terms: ``real_t = nominal_t / (deflator_t / deflator_base)``.

    * With ``base_period`` (a period or a ``(start, end)`` window), each
      deflator series is first rebased so the window's mean is 1; the result
      is in constant ``base_period`` prices.
    * Without it the deflator is taken as an index equal to 100 in its own
      base year (``real = nominal * 100 / deflator``) and an info caveat says
      so (WDI deflators have country-specific base years: prefer passing
      ``base_period``).

    ``match_on=("iso3", "period")`` uses each country's own deflator; use
    ``match_on=("period",)`` with a single deflator series (e.g. US CPI for
    US$ values). Frequencies must match. Unmatched nominal rows are dropped
    (``deflate_unmatched`` caveat). Units containing "current" are relabelled
    ``"constant <base>"``.
    """
    nres, dres = _as_result(nominal, "nominal"), _as_result(deflator, "deflator")
    n, d = nres.data, dres.data
    keys = _match_keys(match_on)
    _frequency_guard(n, d, "deflate", ("nominal", "deflator"))
    _single_series_per_key(d, keys, "deflate: deflator")
    caveats = _ppp_check_groups(n, ["indicator", "iso3"], "deflate", on_ppp_mix)

    d = d.copy()
    dropped: list[dict] = []
    if base_period is not None:
        lo, hi = _window(base_period)
        label = _window_label(lo, hi)
        series_by = keys if keys else []
        bases, dropped = _base_values(d, series_by, lo, hi, "deflate")
        factors = []
        for key, g in _groups(d, series_by):
            if key in bases:
                g = g.copy()
                g["factor"] = g["value"] / bases[key]
                factors.append(g)
        d = pd.concat(factors, ignore_index=True) if factors else d.iloc[0:0].assign(factor=pd.Series(dtype="float64"))
    else:
        label = "deflator base"
        d["factor"] = d["value"] / 100.0
        caveats.append(Caveat("deflator_base_implicit", "info",
                              "Deflator used as an index with base 100 in its own base year; real values are in that year's prices.",
                              {"deflator_indicators": sorted(set(d["indicator"]))}))

    dsub = d[keys + ["period", "factor", "obs_status"]].rename(columns={"obs_status": "obs_status_d"})
    merged = n.merge(dsub, on=keys + ["period"], how="left", validate="many_to_one")
    unmatched = merged["factor"].isna() | ~(merged["factor"] > 0)
    out = merged[~unmatched].copy()
    out["value"] = out["value"] / out["factor"]
    out["obs_status"] = [combine_status([a, b]) for a, b in zip(out["obs_status"], out["obs_status_d"])]
    out["unit"] = [_constant_unit(u, label) for u in out["unit"]]
    out = out[list(n.columns)]
    out = _with_suffix(out, (f"real{label}" if base_period is not None else "real") if suffix is None else suffix,
                       f"constant {label} prices")

    if unmatched.any():
        caveats.append(Caveat("deflate_unmatched", "info", f"{int(unmatched.sum())} nominal observations had no usable deflator; dropped.",
                              {"at": _limited([f"{i}@{p}" for i, p in zip(merged.loc[unmatched, 'iso3'], merged.loc[unmatched, 'period'])]),
                               "n": int(unmatched.sum())}))
    if dropped:
        caveats.append(Caveat("rebase_base_missing", "warn", "Some deflator series lack the base period; their countries are dropped.",
                              {"groups": _limited(dropped)}))
    params = {"base_period": None if base_period is None else label, "match_on": list(match_on), "suffix": suffix,
              "on_ppp_mix": on_ppp_mix}
    return _finish([nres, dres], _sort(out), "deflate", params, caveats)


# ---------------------------------------------------------------------------
# ratio
# ---------------------------------------------------------------------------
def ratio(
    numerator: Frame,
    denominator: Frame,
    scale: float = 100.0,
    *,
    match_on: Sequence[str] = ("iso3", "period"),
    indicator: str | None = None,
    indicator_label: str | None = None,
    unit: str | None = None,
    on_ppp_mix: PPPPolicy = "raise",
) -> Result:
    """``scale * numerator / denominator`` aligned on ``match_on`` (default iso3 and period).

    Built for ratios to GDP: ``ratio(revenue, gdp)`` gives ``"% of <GDP label>"``.

    * Frequencies must match: annual with annual, ``FY2024/25`` with
      ``FY2024/25``. Otherwise :class:`FrequencyMismatchError` is raised
      (convert with :func:`calendar_to_fiscal` / :func:`fiscal_to_calendar`).
    * The denominator must hold one series per country (one per period when
      ``match_on=("period",)``); the numerator may hold several indicators
      (e.g. revenue components), each divided by the same denominator.
    * Rows without a matching, non-zero denominator are dropped and counted
      in a ``ratio_unmatched`` caveat.
    * Different PPP bases raise :class:`PPPMixError` (or ``block``); a current
      vs constant price mismatch between the units gets a ``warn`` caveat.

    Output indicator: ``indicator`` or ``"<num>/<den>"``; source is kept when
    both sides share it, otherwise ``"cgjrkit"`` with dataset ``"derived"``.
    """
    nres, dres = _as_result(numerator, "numerator"), _as_result(denominator, "denominator")
    n, d = nres.data, dres.data
    keys = _match_keys(match_on)
    _frequency_guard(n, d, "ratio", ("numerator", "denominator"))
    _single_series_per_key(d, keys, "ratio: denominator")
    if indicator is not None and n["indicator"].nunique() > 1:
        raise HarmonizeError("ratio: 'indicator' can only be set when the numerator holds a single indicator")

    dcols = keys + ["period", "source", "dataset", "indicator", "indicator_label", "value", "unit", "obs_status"]
    dsub = d[dcols].rename(columns={c: f"{c}_den" for c in dcols if c not in keys + ["period"]})
    merged = n.merge(dsub, on=keys + ["period"], how="left", validate="many_to_one")

    # PPP: compare numerator and denominator units within each output series.
    caveats = _ppp_check_groups(merged.dropna(subset=["indicator_den"]), ["indicator", "iso3"], "ratio", on_ppp_mix,
                                unit_cols=("unit", "unit_den"))
    basis_mismatch = sorted({
        f"{u} vs {ud}" for u, ud in zip(merged["unit"], merged["unit_den"].fillna(""))
        if _price_basis(u) and _price_basis(ud) and _price_basis(u) != _price_basis(ud)
    })
    if basis_mismatch:
        caveats.append(Caveat("ratio_price_basis_mismatch", "warn",
                              "Numerator and denominator mix current and constant prices.", {"units": _limited(basis_mismatch)}))

    ok = merged["value_den"].notna() & (merged["value_den"] != 0)
    out = merged[ok].copy()
    out["value"] = scale * out["value"] / out["value_den"]
    out["obs_status"] = [combine_status([a, b]) for a, b in zip(out["obs_status"], out["obs_status_den"])]
    same_source = out["source"] == out["source_den"]
    same_dataset = same_source & (out["dataset"] == out["dataset_den"])
    out["source"] = out["source"].where(same_source, "cgjrkit")
    out["dataset"] = out["dataset"].where(same_dataset, "derived")
    den_name = [lab or code for lab, code in zip(out["indicator_label_den"], out["indicator_den"])]
    if unit is not None:
        out["unit"] = unit
    else:
        out["unit"] = [f"% of {nm}" if scale == 100 else (f"ratio to {nm}" if scale == 1 else f"ratio to {nm} (x{scale:g})")
                       for nm in den_name]
    num_name = [lab or code for lab, code in zip(out["indicator_label"], out["indicator"])]
    out["indicator"] = indicator if indicator is not None else [f"{a}/{b}" for a, b in zip(out["indicator"], out["indicator_den"])]
    out["indicator_label"] = indicator_label if indicator_label is not None else [
        f"{a} ({u})" for a, u in zip(num_name, out["unit"])]
    unmatched = merged[~ok]
    out = out[list(n.columns)]

    if len(unmatched):
        caveats.append(Caveat("ratio_unmatched", "info",
                              f"{len(unmatched)} numerator observations had no matching non-zero denominator; dropped.",
                              {"at": _limited([f"{i}@{p}" for i, p in zip(unmatched["iso3"], unmatched["period"])]),
                               "n": int(len(unmatched))}))
    params = {"scale": scale, "match_on": list(match_on), "indicator": indicator, "unit": unit, "on_ppp_mix": on_ppp_mix}
    out_res = _finish([nres, dres], _sort(out), "ratio", params, caveats)
    dupes = out_res.data.duplicated(subset=["source", "dataset", "indicator", "iso3", "period"])
    if dupes.any():
        raise HarmonizeError("ratio: output has duplicate observations; filter the numerator to one series per indicator")
    return out_res


# ---------------------------------------------------------------------------
# fiscal <-> calendar years
# ---------------------------------------------------------------------------
def _fy_start_month(iso3: str, fy_start_month: int | Mapping[str, int] | None) -> int:
    if isinstance(fy_start_month, Mapping):
        if iso3 not in fy_start_month:
            raise HarmonizeError(f"no fiscal-year start month given for {iso3}")
        month = int(fy_start_month[iso3])
    elif fy_start_month is None:
        from cgjrkit.registries import fiscal_year_start_month  # local import keeps harmonize importable alone

        try:
            month = fiscal_year_start_month(iso3)
        except KeyError as exc:
            raise HarmonizeError(f"{iso3}: fiscal-year start month not in the country registry; pass fy_start_month") from exc
    else:
        month = int(fy_start_month)
    if not 1 <= month <= 12:
        raise ValueError(f"fiscal-year start month must be 1-12, got {month}")
    if month == 1:
        raise HarmonizeError(f"{iso3}: the fiscal year starts in January, so it equals the calendar year; no conversion needed")
    return month


def _fiscal_weights(month: int) -> tuple[float, float]:
    """(weight on the earlier year, weight on the later year) for a fiscal year starting in ``month``."""
    return (month - 1) / 12.0, (13 - month) / 12.0


_FY_NOTE = (
    "A fiscal year starting in month m of year Y covers 13-m months of calendar year Y and m-1 months of Y+1. "
    "CY(Y) = (m-1)/12 * FY(Y-1) + (13-m)/12 * FY(Y); FY(Y) = (13-m)/12 * CY(Y) + (m-1)/12 * CY(Y+1). "
    "Flows assume an even spread within each year; averages assume a flat monthly profile; end-of-period stocks "
    "are linearly interpolated between year ends, which gives the same weights. Kenya (m=7): equal weights of 0.5."
)


def fiscal_to_calendar(
    frame: Frame,
    fy_start_month: int | Mapping[str, int] | None = None,
    *,
    kind: Literal["flow", "average", "stock"] = "flow",
    by: Sequence[str] = ("indicator", "iso3"),
    suffix: str | None = None,
    on_ppp_mix: PPPPolicy = "raise",
) -> Result:
    """Convert fiscal-year values (``"FY2024/25"``) to calendar years (``"2024"``).

    Weighting (fiscal year starting in month ``m``; FY labelled by its start
    year)::

        CY(Y) = (m-1)/12 * FY(Y-1) + (13-m)/12 * FY(Y)

    Kenya's July-June year (``m=7``) gives ``CY2024 = 0.5*FY2023/24 +
    0.5*FY2024/25``. The same weights hold for ``kind="flow"`` (each fiscal
    year's flow spread evenly over its 12 months), ``"average"`` (flat
    monthly profile) and ``"stock"`` (end-of-period stocks linearly
    interpolated between fiscal year ends); ``kind`` is recorded in the
    provenance. Calendar years at the edges (one fiscal year missing) are
    dropped.

    ``fy_start_month`` may be an int, a ``{iso3: month}`` mapping, or None to
    read each country's month from ``cgjrkit.registries``. Use
    :func:`quarterly_to_fiscal` when quarterly data allow an exact result.
    """
    return _convert_years(frame, fy_start_month, kind, by, suffix, on_ppp_mix, to="calendar")


def calendar_to_fiscal(
    frame: Frame,
    fy_start_month: int | Mapping[str, int] | None = None,
    *,
    kind: Literal["flow", "average", "stock"] = "flow",
    by: Sequence[str] = ("indicator", "iso3"),
    suffix: str | None = None,
    on_ppp_mix: PPPPolicy = "raise",
) -> Result:
    """Convert calendar-year values to fiscal years (``"FY2024/25"``).

    Weighting (fiscal year starting in month ``m`` of year ``Y``)::

        FY(Y) = (13-m)/12 * CY(Y) + (m-1)/12 * CY(Y+1)

    Typical use: fiscal-year GDP as the denominator of National Treasury
    ratios (Kenya Figs 8, 18, 19) when quarterly GDP is not available.
    Same ``kind`` semantics as :func:`fiscal_to_calendar`.
    """
    return _convert_years(frame, fy_start_month, kind, by, suffix, on_ppp_mix, to="fiscal")


def _convert_years(frame, fy_start_month, kind, by, suffix, on_ppp_mix, *, to: str) -> Result:
    if kind not in ("flow", "average", "stock"):
        raise ValueError("kind must be 'flow', 'average' or 'stock'")
    res = _as_result(frame)
    df = res.data
    by = _check_by(df, by)
    if "iso3" not in by:
        raise ValueError("by must include 'iso3' (the fiscal year is country-specific)")
    want = "FY" if to == "calendar" else "A"
    freqs = {period_frequency(p) for p in df["period"]}
    if freqs and freqs != {want}:
        raise FrequencyMismatchError(
            f"{'fiscal_to_calendar' if to == 'calendar' else 'calendar_to_fiscal'} expects only "
            f"{'fiscal-year (FY2024/25)' if want == 'FY' else 'calendar-year (2024)'} periods, got {sorted(freqs)}"
        )
    tool = "fiscal_to_calendar" if to == "calendar" else "calendar_to_fiscal"
    caveats = _ppp_check_groups(df, by, tool, on_ppp_mix)

    rows, edges, months_used = [], [], {}
    for key, g in _groups(df, by):
        _unique_periods(g, f"{tool} group {_key_text(key)}")
        iso3 = str(g["iso3"].iloc[0])
        m = _fy_start_month(iso3, fy_start_month)
        months_used[iso3] = m
        w_early, w_late = _fiscal_weights(m)
        vals = {parse_period(p).year: (v, s) for p, v, s in zip(g["period"], g["value"], g["obs_status"]) if np.isfinite(v)}
        template = g.iloc[0]
        years = sorted(vals)
        if to == "calendar":
            # CY(Y) needs FY(Y-1) and FY(Y)
            targets = sorted({y for y in years} | {y + 1 for y in years})
            for y in targets:
                if (y - 1) in vals and y in vals:
                    (v0, s0), (v1, s1) = vals[y - 1], vals[y]
                    new = template.copy()
                    new["period"] = str(y)
                    new["value"] = w_early * v0 + w_late * v1
                    new["obs_status"] = combine_status([s0, s1])
                    rows.append(new)
                else:
                    edges.append(f"{_key_text(key)}@{y}")
        else:
            # FY(Y) (starting in Y) needs CY(Y) and CY(Y+1)
            for y in years:
                if (y + 1) in vals:
                    (v0, s0), (v1, s1) = vals[y], vals[y + 1]
                    new = template.copy()
                    new["period"] = fiscal_label(y)
                    new["value"] = w_late * v0 + w_early * v1
                    new["obs_status"] = combine_status([s0, s1])
                    rows.append(new)
                else:
                    edges.append(f"{_key_text(key)}@{fiscal_label(y)}")

    out = pd.DataFrame(rows, columns=df.columns) if rows else _empty_like(df)
    out["value"] = out["value"].astype("float64")
    default_suffix = "cy" if to == "calendar" else "fy"
    out = _with_suffix(out, default_suffix if suffix is None else suffix,
                       "calendar-year, converted from fiscal years" if to == "calendar" else "fiscal-year, converted from calendar years")
    caveats.append(Caveat(f"{tool}", "info",
                          "Values converted between fiscal and calendar years by month weighting (approximation).",
                          {"fy_start_month": months_used, "kind": kind}))
    if edges:
        caveats.append(Caveat("fiscal_conversion_edges", "info",
                              f"{len(edges)} periods dropped because an adjacent year is missing.", {"at": _limited(edges), "n": len(edges)}))
    params = {"fy_start_month": months_used, "kind": kind, "by": by, "suffix": suffix, "on_ppp_mix": on_ppp_mix}
    return _finish([res], _sort(out), tool, params, caveats, [_FY_NOTE])


def quarterly_to_fiscal(
    frame: Frame,
    fy_start_month: int | Mapping[str, int] | None = None,
    *,
    how: Literal["sum", "mean", "last"] = "sum",
    by: Sequence[str] = ("indicator", "iso3"),
    suffix: str | None = None,
    on_ppp_mix: PPPPolicy = "raise",
) -> Result:
    """Exact fiscal-year values from quarterly data (``"2024Q3"`` ... ``"2025Q2"`` -> ``"FY2024/25"``).

    ``how="sum"`` for flows (GDP, revenue), ``"mean"`` for averages, ``"last"``
    for end-of-period stocks. The fiscal year must start at a quarter
    boundary (month 4, 7 or 10); only fiscal years with all four quarters are
    returned.
    """
    if how not in ("sum", "mean", "last"):
        raise ValueError("how must be 'sum', 'mean' or 'last'")
    res = _as_result(frame)
    df = res.data
    by = _check_by(df, by)
    if "iso3" not in by:
        raise ValueError("by must include 'iso3' (the fiscal year is country-specific)")
    freqs = {period_frequency(p) for p in df["period"]}
    if freqs and freqs != {"Q"}:
        raise FrequencyMismatchError(f"quarterly_to_fiscal expects quarterly periods, got {sorted(freqs)}")
    caveats = _ppp_check_groups(df, by, "quarterly_to_fiscal", on_ppp_mix)

    rows, incomplete, months_used = [], [], {}
    for key, g in _groups(df, by):
        _unique_periods(g, f"quarterly_to_fiscal group {_key_text(key)}")
        iso3 = str(g["iso3"].iloc[0])
        m = _fy_start_month(iso3, fy_start_month)
        if m not in (4, 7, 10):
            raise HarmonizeError(f"{iso3}: fiscal year starting in month {m} does not align with quarters")
        months_used[iso3] = m
        first_q = (m - 1) // 3 + 1
        vals = {parse_period(p).ordinal: (v, s) for p, v, s in zip(g["period"], g["value"], g["obs_status"]) if np.isfinite(v)}
        start_years = sorted({o // 4 - (1 if (o % 4 + 1) < first_q else 0) for o in vals})
        template = g.iloc[0]
        for y in start_years:
            start = y * 4 + first_q - 1
            quarters = [vals.get(start + i) for i in range(4)]
            if any(q is None for q in quarters):
                incomplete.append(f"{_key_text(key)}@{fiscal_label(y)}")
                continue
            values = [q[0] for q in quarters]
            value = sum(values) if how == "sum" else (sum(values) / 4.0 if how == "mean" else values[-1])
            new = template.copy()
            new["period"] = fiscal_label(y)
            new["value"] = value
            new["obs_status"] = combine_status(q[1] for q in quarters)
            rows.append(new)

    out = pd.DataFrame(rows, columns=df.columns) if rows else _empty_like(df)
    out["value"] = out["value"].astype("float64")
    out = _with_suffix(out, "fy" if suffix is None else suffix, f"fiscal-year {how} of quarters")
    if incomplete:
        caveats.append(Caveat("fiscal_year_incomplete", "info", f"{len(incomplete)} fiscal years lack a quarter; dropped.",
                              {"at": _limited(incomplete), "n": len(incomplete)}))
    params = {"fy_start_month": months_used, "how": how, "by": by, "suffix": suffix, "on_ppp_mix": on_ppp_mix}
    return _finish([res], _sort(out), "quarterly_to_fiscal", params, caveats)


# ---------------------------------------------------------------------------
# splice
# ---------------------------------------------------------------------------
def splice(
    old: Frame,
    new: Frame,
    overlap_period: PeriodSpec,
    method: Literal["ratio", "level"] = "ratio",
    *,
    by: Sequence[str] = ("iso3",),
    indicator: str | None = None,
    on_ppp_mix: PPPPolicy = "raise",
) -> Result:
    """Join an ``old`` series onto a ``new`` one at ``overlap_period``.

    ``overlap_period`` is a period present in both series, or a ``(start,
    end)`` window whose means are compared. For each ``by`` group (default
    one series per country):

    * ``method="ratio"``: ``old_adj = old * mean(new_overlap) / mean(old_overlap)``
      (keeps old growth rates; use for levels such as GDP).
    * ``method="level"``: ``old_adj = old + mean(new_overlap) - mean(old_overlap)``
      (keeps old changes; use for rates and shares).

    Output: adjusted old values before the first overlap period, new values
    from it onward. The first overlap period is flagged
    ``obs_status="break"`` and a ``series_spliced`` caveat records the
    factor. Metadata (indicator, unit, labels) come from ``new`` unless
    ``indicator`` is given. Groups present in only one input, or without
    complete overlap data, are dropped (``splice_skipped`` caveat).

    PPP bases: each input must be internally consistent (one PPP base per
    group). A ratio splice is the standard bridge between PPP vintages (e.g.
    PWT 10.01 at PPP 2017 onto PWT 11.0 at PPP 2021): it keeps the old growth
    rates at the new series' level and labels every output row with the new
    unit, so old and new may carry different bases; the ``series_spliced``
    caveat then records ``ppp_bases`` (old -> new) for each group. A level
    splice adds a shift in the old units, so different bases between old and
    new still raise :class:`PPPMixError` (or ``block``).
    """
    if method not in ("ratio", "level"):
        raise ValueError("method must be 'ratio' or 'level'")
    ores, nres = _as_result(old, "old"), _as_result(new, "new")
    o, nw = ores.data, nres.data
    by = _check_by(o, by)
    _check_by(nw, by)
    _frequency_guard(o, nw, "splice", ("old", "new"))
    lo, hi = _window(overlap_period)
    label = _window_label(lo, hi)
    expected = hi.ordinal - lo.ordinal + 1

    if method == "ratio":
        # A ratio splice rescales the old series to the new level (and unit): check each input on its own.
        caveats = (_ppp_check_groups(o, by, "splice (old series)", on_ppp_mix)
                   + _ppp_check_groups(nw, by, "splice (new series)", on_ppp_mix))
    else:
        both = pd.concat([o.assign(_side="old"), nw.assign(_side="new")], ignore_index=True)
        caveats = _ppp_check_groups(both, by, "splice", on_ppp_mix)

    def bases(g: pd.DataFrame) -> list[str]:
        return sorted({b for b in (ppp_base(u) for u in pd.unique(g["unit"])) if b})

    old_groups = dict(_groups(o, by))
    new_groups = dict(_groups(nw, by))
    parts, skipped, evidence, bridged = [], [], [], []
    for key in sorted(set(old_groups) | set(new_groups), key=str):
        if key not in old_groups or key not in new_groups:
            skipped.append({"group": _key_text(key), "reason": "present in only one input"})
            continue
        go, gn = old_groups[key], new_groups[key]
        for side, g in (("old", go), ("new", gn)):
            _unique_periods(g, f"splice {side} group {_key_text(key)}")
            if g["indicator"].nunique() > 1:
                raise HarmonizeError(f"splice: {side} group {_key_text(key)} holds several indicators; add 'indicator' to by or filter")
        o_ord = go["period"].map(lambda p: parse_period(p).ordinal)
        n_ord = gn["period"].map(lambda p: parse_period(p).ordinal)
        if parse_period(go["period"].iloc[0]).freq != lo.freq:
            raise FrequencyMismatchError(f"splice: overlap {label!r} does not match the series frequency")
        ow = go.loc[(o_ord >= lo.ordinal) & (o_ord <= hi.ordinal), "value"].dropna()
        nw_vals = gn.loc[(n_ord >= lo.ordinal) & (n_ord <= hi.ordinal), "value"].dropna()
        if len(ow) < expected or len(nw_vals) < expected:
            skipped.append({"group": _key_text(key), "reason": f"overlap {label} incomplete"})
            continue
        mo, mn = float(ow.mean()), float(nw_vals.mean())
        if method == "ratio":
            if mo == 0:
                skipped.append({"group": _key_text(key), "reason": "old overlap value is zero"})
                continue
            factor = mn / mo
            adj = go.loc[o_ord < lo.ordinal].copy()
            adj["value"] = adj["value"] * factor
        else:
            factor = mn - mo
            adj = go.loc[o_ord < lo.ordinal].copy()
            adj["value"] = adj["value"] + factor
        tail = gn.loc[n_ord >= lo.ordinal].copy()
        first_new = n_ord[n_ord >= lo.ordinal].min()
        tail.loc[n_ord[tail.index] == first_new, "obs_status"] = "break"
        ref = gn.iloc[0]
        for col in ("indicator", "indicator_label", "unit", "country"):
            adj[col] = ref[col]
        if go["source"].iloc[0] != ref["source"]:
            # one spliced series from two providers: provenance keeps both sources
            adj["source"] = tail["source"] = "cgjrkit"
            adj["dataset"] = tail["dataset"] = "spliced"
        elif go["dataset"].iloc[0] != ref["dataset"]:
            adj["dataset"] = tail["dataset"] = "spliced"
        part = pd.concat([adj, tail], ignore_index=True)
        if indicator is not None:
            part["indicator"] = indicator
        parts.append(part)
        ev = {
            "group": _key_text(key), "method": method, "overlap": label,
            "factor" if method == "ratio" else "shift": factor,
            "old_indicator": str(go["indicator"].iloc[0]), "new_indicator": str(ref["indicator"]),
            "break_at": format_period(lo.freq, int(first_new)),
        }
        ob, nb = bases(go), bases(gn)
        if ob or nb:
            ev["ppp_bases"] = {"old": ob, "new": nb}
            if ob != nb:
                bridged.append(f"{_key_text(key)}: {'/'.join(ob) or 'none'} -> {'/'.join(nb) or 'none'}")
        evidence.append(ev)

    out = pd.concat(parts, ignore_index=True) if parts else _empty_like(nw)
    if evidence:
        msg = f"Series spliced at {label} ({method} method); values before the break are rescaled old-series values."
        if bridged:
            msg += (f" The splice bridges PPP vintages ({'; '.join(_limited(bridged))}): old growth rates are kept at "
                    "the new series' PPP level and every value is labelled in the new series' unit.")
        caveats.append(Caveat("series_spliced", "warn", msg,
                              {"splices": _limited(evidence), "n": len(evidence),
                               "ppp_vintage_bridge": _limited(bridged)}))
    if skipped:
        caveats.append(Caveat("splice_skipped", "warn" if not parts else "info",
                              f"{len(skipped)} groups were not spliced.", {"groups": _limited(skipped)}))
    params = {"overlap_period": label, "method": method, "by": by, "indicator": indicator, "on_ppp_mix": on_ppp_mix}
    return _finish([ores, nres], _sort(out), "splice", params, caveats)
