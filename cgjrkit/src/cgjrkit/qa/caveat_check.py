"""Caveat check engine: rules over Results -> PASS / WARN / BLOCK plus chart footnotes.

:func:`run_caveat_check` takes the inputs of one output (a chart, a table or
an export) as a :class:`~cgjrkit.core.provenance.Result` or a list of them,
plus an optional ``context`` dict, runs every registered rule and returns a
:class:`CaveatReport`:

* ``status``: ``"BLOCK"`` if any caveat has severity ``block`` (the output
  must not be rendered or exported as is), ``"WARN"`` if any has ``warn``
  (footnote it), else ``"PASS"``;
* ``caveats``: the caveats raised by the rules, followed by the caveats the
  inputs already carried (connectors and methods attach their own; turn this
  off with ``context["inherit_caveats"] = False``);
* ``footnotes``: short, chart-ready strings (forecast years, ILO modelled
  estimates, series breaks, COVID-19 endpoints, ...).

Rules are small functions registered with :func:`register_rule`; each gets a
:class:`CheckInput` and returns a :class:`RuleOutput`. Results passed together
are treated as the inputs of one output, so cross-result problems (two PPP
bases, two vintages of one dataset, a modelled series continued by a survey
series) are caught. Every rule is documented in ``docs/caveat-rules.md``.

``context`` keys (all optional):

=====================  =========================================================
``mode``               ``"replication"`` turns unfrozen peer sets into BLOCK
                       (``replication=True`` is accepted too)
``redistribution``     ``True`` when data will be redistributed (exports,
                       client data packs): restricted licences BLOCK
                       (``purpose="redistribution"`` is accepted too)
``ppp_base``           PPP base year the output is pinned to (``"2017"``); any
                       other base BLOCKs
``min_n``              minimum members with data for a peer aggregate (default:
                       the ``min_n`` the method recorded, else 3)
``peer_sets``          peer sets used (``PeerSet`` objects or dicts, one or many);
                       ``peer_set`` is accepted too
``results_at``         ISO date the results were produced; a peer set frozen
                       after it is flagged
``periods``            extra averaging windows, e.g. ``{"2016-20": ("2016", "2020")}``
``covid_years``        years treated as COVID-19 endpoints (default 2020, 2021)
``inherit_caveats``    include the inputs' own caveats (default ``True``)
=====================  =========================================================
"""

from __future__ import annotations

import importlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from functools import cached_property
from typing import Any, Callable, Iterable, Literal, Mapping, Sequence

import pandas as pd

from cgjrkit.core.caveats import Caveat, Severity, worst
from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import COLUMNS, ppp_base

__all__ = [
    "CaveatReport",
    "CheckInput",
    "Rule",
    "RuleOutput",
    "RULES",
    "CONTEXT_KEYS",
    "register_rule",
    "list_rules",
    "run_caveat_check",
    "period_years",
    "window_years",
]

Status = Literal["PASS", "WARN", "BLOCK"]

#: Provider key of derived results (methods append a cgjrkit Provenance entry).
DERIVED_SOURCE = "cgjrkit"
#: Provider keys and dataset prefixes that identify ILO data.
ILO_SOURCES = frozenset({"ilostat", "ilo"})
#: ``obs_status`` values that count as "not modelled" when looking for splices.
#: ``forecast`` and ``estimate`` are neutral: ILO modelled projections and
#: provider-flagged estimates can sit inside a modelled series.
NON_MODELLED_STATUS = frozenset({"", "provisional", "break", "imputed"})
#: Optional dimension columns that, when present, are part of a series key.
SERIES_DIMENSIONS = ("sex", "age", "classif1", "classif2", "sector", "component", "stat", "group", "scenario", "window")
#: Default COVID-19 years for the endpoint rule.
COVID_YEARS = (2020, 2021)
#: Default minimum number of members with data for a peer aggregate.
DEFAULT_MIN_N = 3
#: Maximum number of items listed in evidence (counts are always given).
EVIDENCE_LIMIT = 25
#: Maximum number of series-break footnotes before they are summarised.
MAX_BREAK_FOOTNOTES = 4
#: Context keys the engine and rules read.
CONTEXT_KEYS = frozenset({
    "mode", "replication", "redistribution", "purpose", "ppp_base", "min_n", "peer_sets", "peer_set",
    "results_at", "periods", "covid_years", "inherit_caveats",
})

_NC_LICENCE = re.compile(r"non[-\s]?commercial|\bBY-NC\b|\bNC\b", re.IGNORECASE)
_ILO_LABEL = r"\bILO\b"
_PERIOD_RE = re.compile(r"^\s*(?:FY)?((?:19|20)\d{2})(?:/(\d{2}|\d{4}))?(?:Q[1-4]|M(?:0[1-9]|1[0-2])|-(?:0[1-9]|1[0-2])|S[12])?\s*$", re.IGNORECASE)
_WINDOW_RE = re.compile(
    r"^\s*(?P<a>(?:FY)?(?:19|20)\d{2}(?:/\d{2,4})?(?:Q[1-4]|M\d{2})?)"
    r"\s*(?:-|–|—|to)\s*"
    r"(?P<b>(?:FY)?(?:19|20)\d{2}(?:/\d{2,4})?(?:Q[1-4]|M\d{2})?|\d{2})\s*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# containers
# ---------------------------------------------------------------------------
@dataclass
class RuleOutput:
    """What one rule found: caveats (with ``evidence["rule"]``) and footnotes."""

    caveats: list[Caveat] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)

    def add(self, rule: str, code: str, severity: Severity, message: str, evidence: Mapping[str, Any] | None = None,
            footnote: str | None = None) -> None:
        self.caveats.append(Caveat(code, severity, message, {"rule": rule, **dict(evidence or {})}))
        if footnote:
            self.footnotes.append(footnote)


@dataclass(frozen=True)
class Rule:
    """A registered caveat rule."""

    name: str
    func: Callable[["CheckInput"], RuleOutput]
    description: str
    severities: tuple[str, ...]


@dataclass
class CaveatReport:
    """Outcome of :func:`run_caveat_check`."""

    status: Status
    caveats: list[Caveat] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)
    rules_run: list[str] = field(default_factory=list)
    inherited: list[Caveat] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return self.status == "BLOCK"

    def by_rule(self) -> dict[str, list[Caveat]]:
        """Rule caveats grouped by rule name (inherited caveats under ``"inherited"``)."""
        out: dict[str, list[Caveat]] = {}
        for c in self.caveats:
            key = "inherited" if c in self.inherited else str(c.evidence.get("rule", "engine"))
            out.setdefault(key, []).append(c)
        return out

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "caveats": [c.to_dict() for c in self.caveats],
            "footnotes": list(self.footnotes),
            "rules_run": list(self.rules_run),
            "inherited": [c.to_dict() for c in self.inherited],
            "errors": dict(self.errors),
        }


# ---------------------------------------------------------------------------
# check input
# ---------------------------------------------------------------------------
def _canonical(df: Any) -> pd.DataFrame:
    """Copy of ``df`` with every canonical column present (strings filled with "")."""
    out = df.copy() if isinstance(df, pd.DataFrame) else pd.DataFrame()
    for col in COLUMNS:
        if col not in out.columns:
            out[col] = float("nan") if col == "value" else ""
    for col in COLUMNS:
        if col != "value":
            out[col] = out[col].fillna("").astype(str)
    out["value"] = pd.to_numeric(out["value"], errors="coerce")
    return out


class CheckInput:
    """The inputs of one output, with helpers shared by the rules."""

    def __init__(self, results: Sequence[Result], context: Mapping[str, Any] | None = None):
        self.results = list(results)
        self.context = dict(context or {})

    @cached_property
    def frames(self) -> list[pd.DataFrame]:
        """Each result's data with canonical columns guaranteed."""
        return [_canonical(r.data) for r in self.results]

    @cached_property
    def data(self) -> pd.DataFrame:
        """All results' rows stacked, with ``_result`` (input index); extra columns filled with ""."""
        parts = [f.assign(_result=i) for i, f in enumerate(self.frames)]
        df = pd.concat(parts, ignore_index=True, sort=False) if parts else _canonical(None).assign(_result=0)
        for col in df.columns:
            if col not in ("value", "_result") and (df[col].dtype == object or pd.api.types.is_string_dtype(df[col])):
                df[col] = df[col].fillna("").astype(str)
        return df

    @property
    def replication(self) -> bool:
        return str(self.context.get("mode", "")).lower() == "replication" or self.context.get("replication") is True

    @property
    def redistribution(self) -> bool:
        return bool(self.context.get("redistribution")) or str(self.context.get("purpose", "")).lower() == "redistribution"

    def provenance(self, index: int | None = None, *, derived: bool = True) -> list[Provenance]:
        """Provenance entries of one result (or all, de-duplicated); ``derived=False`` drops cgjrkit steps."""
        results = self.results if index is None else [self.results[index]]
        out: list[Provenance] = []
        for r in results:
            for p in r.provenance or []:
                if not isinstance(p, Provenance):
                    continue
                if not derived and p.source == DERIVED_SOURCE:
                    continue
                if p not in out:
                    out.append(p)
        return out

    def cites_for(self, index: int, source: str, dataset: str) -> tuple[tuple[str, str], ...]:
        """``(label, vintage)`` pairs of the provenance entries behind rows of ``source``/``dataset``."""
        provs = self.provenance(index, derived=False) or self.provenance(None, derived=False)
        if source and source != DERIVED_SOURCE:
            same = [p for p in provs if p.source == source]
            exact = [p for p in same if p.dataset == dataset]
            partial = [p for p in same if dataset and dataset in p.dataset]
            provs = exact or partial or same or provs
        pairs = sorted({(p.citation or p.dataset or p.source, p.vintage) for p in provs})
        if not pairs and source:
            pairs = [(source if not dataset else f"{source} {dataset}", "")]
        return tuple(pairs)


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------
RULES: dict[str, Rule] = {}


def register_rule(name: str, description: str, severities: Sequence[str] = ("warn",), *,
                  replace: bool = False) -> Callable[[Callable[[CheckInput], RuleOutput]], Callable[[CheckInput], RuleOutput]]:
    """Decorator: register ``func(inp: CheckInput) -> RuleOutput`` under ``name``.

    Rules run in registration order. ``replace=True`` overrides an existing rule.
    """
    bad = [s for s in severities if s not in ("info", "warn", "block")]
    if bad:
        raise ValueError(f"unknown severities {bad}")

    def deco(func: Callable[[CheckInput], RuleOutput]) -> Callable[[CheckInput], RuleOutput]:
        if name in RULES and not replace:
            raise ValueError(f"caveat rule {name!r} is already registered")
        RULES[name] = Rule(name, func, description, tuple(severities))
        return func

    return deco


def list_rules() -> list[dict]:
    """Registered rules (name, description, severities) in run order."""
    return [{"name": r.name, "description": r.description, "severities": list(r.severities)} for r in RULES.values()]


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _limited(items: Sequence[Any], limit: int = EVIDENCE_LIMIT) -> list[Any]:
    return list(items)[:limit]


def _count(n: int, singular: str, plural: str | None = None) -> str:
    """``"1 observation"`` / ``"3 observations"``."""
    return f"{n} {singular if n == 1 else (plural or singular + 's')}"


def _join(items: Iterable[str], limit: int = 4) -> str:
    items = [str(i) for i in items]
    if len(items) <= limit:
        return ", ".join(items)
    return ", ".join(items[:limit]) + f" and {len(items) - limit} more"


def _cite_text(pairs: Iterable[tuple[str, str]], missing_vintage: str = "vintage not recorded") -> str:
    parts = [f"{label}, {vintage}" if vintage else f"{label}, {missing_vintage}" for label, vintage in pairs]
    return "; ".join(parts)


def _century(year: int, two_digits: str) -> int:
    return (year // 100) * 100 + int(two_digits)


def period_years(period: Any) -> tuple[int, int] | None:
    """First and last calendar year a single period touches.

    ``"2024"``, ``"2024Q1"``, ``"2024-03"``, ``"2024M03"`` -> ``(2024, 2024)``;
    ``"FY2019/20"`` -> ``(2019, 2020)``. Returns None for anything else
    (including windows such as ``"2011-2019"``; see :func:`window_years`).
    """
    m = _PERIOD_RE.match(str(period))
    if not m:
        return None
    first = int(m.group(1))
    tail = m.group(2)
    if not tail:
        return first, first
    last = int(tail) if len(tail) == 4 else _century(first, tail)
    return first, max(first, last)


def window_years(label: Any) -> tuple[int, int] | None:
    """Start and end year of a window label: ``"2011-2019"``, ``"2016-21"``, ``"2019Q1-2021Q4"``.

    ``"YYYY-MM"`` with ``MM`` in 01-12 is a month (canonical period), not a window.
    """
    text = str(label)
    m = _WINDOW_RE.match(text)
    if not m:
        return None
    a, b = m.group("a"), m.group("b")
    start = period_years(a)
    if start is None:
        return None
    if re.fullmatch(r"\d{2}", b):
        if re.fullmatch(r"(?:19|20)\d{2}", a) and 1 <= int(b) <= 12:
            return None  # "2024-03" is March 2024
        end_year = _century(start[0], b)
    else:
        end = period_years(b)
        if end is None:
            return None
        end_year = end[1]
    if end_year < start[0]:
        return None
    return start[0], end_year


def _windows_from(obj: Any, origin: str) -> list[dict]:
    """Averaging windows described by ``obj`` as ``{label, start, end, start_years, end_years, origin}``.

    Accepts ``{label: (start, end)}``, ``{label: "2011-2019"}``, lists of
    ``(start, end)`` pairs and lists of window labels. A bare list of periods
    is read as labels (charts store plotted periods that way), never as a pair.
    """
    out: list[dict] = []

    def from_pair(label: str, pair: Sequence[Any]) -> None:
        a, b = str(pair[0]), str(pair[1])
        ya, yb = period_years(a), period_years(b)
        if ya and yb and a != b:
            out.append({"label": label, "start": a, "end": b, "start_years": ya, "end_years": yb, "origin": origin})

    def from_label(label: str) -> None:
        w = window_years(label)
        if w and w[0] != w[1]:
            out.append({"label": label, "start": str(w[0]), "end": str(w[1]), "start_years": (w[0], w[0]),
                        "end_years": (w[1], w[1]), "origin": origin})

    def is_pair(x: Any) -> bool:
        return isinstance(x, (list, tuple)) and len(x) == 2 and all(isinstance(v, (str, int)) for v in x)

    if isinstance(obj, Mapping):
        for label, spec in obj.items():
            if is_pair(spec):
                from_pair(str(label), spec)
            elif isinstance(spec, str):
                from_label(spec)
            else:
                from_label(str(label))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            if is_pair(item):
                from_pair(f"{item[0]}-{item[1]}", item)
            elif isinstance(item, (str, int)):
                from_label(str(item))
    elif isinstance(obj, str):
        from_label(obj)
    return out


def _iso_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(str(value).strip()[:10]).isoformat()
    except ValueError:
        return None


def _series_keys(df: pd.DataFrame) -> list[str]:
    return ["indicator", "iso3"] + [c for c in SERIES_DIMENSIONS if c in df.columns]


def _is_ilo(df: pd.DataFrame) -> pd.Series:
    return (
        df["source"].str.lower().isin(ILO_SOURCES)
        | df["dataset"].str.upper().str.startswith("ILO")
        | df["indicator_label"].str.contains(_ILO_LABEL, case=False, regex=True)
    )


# ---------------------------------------------------------------------------
# rules
# ---------------------------------------------------------------------------
@register_rule("ppp_mixing", "BLOCK when the inputs carry more than one PPP base (schema.ppp_base of the unit "
               "labels), or a base other than context['ppp_base']; WARN when a PPP unit states no base year.",
               ("warn", "block"))
def rule_ppp_mixing(inp: CheckInput) -> RuleOutput:
    out = RuleOutput()
    units: set[str] = set()
    for df in inp.frames:
        cols = [c for c in df.columns if c == "unit" or str(c).endswith("_unit") or str(c).startswith("unit_")]
        for col in cols:
            units.update(str(u) for u in df[col].dropna().unique() if str(u))
    bases: dict[str, list[str]] = {}
    for u in sorted(units):
        b = ppp_base(u)
        if b:
            bases.setdefault(b, []).append(u)
    if not bases:
        return out
    evidence = {"ppp_bases": {b: _limited(us) for b, us in sorted(bases.items())}}
    if len(bases) > 1:
        out.add("ppp_mixing", "ppp_mixing", "block",
                f"Inputs mix PPP bases ({', '.join(sorted(bases))}); use one PPP base.", evidence)
    elif "unknown" in bases:
        out.add("ppp_mixing", "ppp_base_unstated", "warn",
                "A PPP-based unit does not state its base year; the PPP base cannot be checked.", evidence)
    expected = inp.context.get("ppp_base")
    if expected:
        expected = str(expected)
        other = sorted(b for b in bases if b != expected)
        if other:
            out.add("ppp_mixing", "ppp_base_mismatch", "block",
                    f"Output is pinned to PPP {expected} but inputs use PPP {', '.join(other)}.",
                    {**evidence, "expected": expected})
    return out


@register_rule("ilo_modelled", "WARN when ILO modelled estimates (obs_status 'modelled') are present; BLOCK when "
               "modelled and non-modelled observations of one indicator/country form one series (a splice).",
               ("info", "warn", "block"))
def rule_ilo_modelled(inp: CheckInput) -> RuleOutput:
    out = RuleOutput()
    df = inp.data
    if df.empty:
        return out
    has_value = df["value"].notna()
    modelled = (df["obs_status"] == "modelled") & has_value
    if not modelled.any():
        return out
    ilo = _is_ilo(df)

    # -- splices: one series holding both modelled and non-modelled observations
    keys = _series_keys(df)
    obs = df[has_value]
    splices = []
    for key, g in obs.groupby(keys, sort=True):
        st = set(g["obs_status"])
        if "modelled" in st and st & NON_MODELLED_STATUS:
            key = key if isinstance(key, tuple) else (key,)
            mod_periods = sorted(g.loc[g["obs_status"] == "modelled", "period"])
            other_periods = sorted(g.loc[g["obs_status"].isin(NON_MODELLED_STATUS), "period"])
            splices.append({"series": "/".join(str(k) for k in key if k != ""),
                            "modelled_periods": [mod_periods[0], mod_periods[-1]],
                            "non_modelled_periods": [other_periods[0], other_periods[-1]]})
    if splices:
        out.add("ilo_modelled", "ilo_modelled_splice", "block",
                f"{len(splices)} series combine modelled estimates with non-modelled (survey/national) observations; "
                "do not splice them.", {"series": _limited(splices), "n": len(splices)})

    # -- presence of modelled values
    mod_ilo = df[modelled & ilo]
    mod_other = df[modelled & ~ilo]
    if len(mod_ilo):
        groups: dict[tuple, list[str]] = {}
        for (i, src, ds), g in mod_ilo.groupby(["_result", "source", "dataset"], sort=True):
            cites = inp.cites_for(int(i), str(src), str(ds))
            groups.setdefault(cites, []).extend(g["indicator"].unique())
        indicators = sorted(set(mod_ilo["indicator"]))
        out.add("ilo_modelled", "ilo_modelled", "warn",
                f"{_count(len(indicators), 'indicator')} include{'s' if len(indicators) == 1 else ''} ILO modelled "
                f"estimates ({_count(len(mod_ilo), 'observation')}).",
                {"indicators": _limited(indicators), "iso3": _limited(sorted(set(mod_ilo["iso3"]))),
                 "rows": int(len(mod_ilo)), "vintages": sorted({v for c in groups for _, v in c if v})})
        all_modelled = bool((df.loc[has_value, "obs_status"] == "modelled").all())
        labels = mod_ilo.drop_duplicates("indicator").set_index("indicator")["indicator_label"].to_dict()
        for cites, inds in sorted(groups.items()):
            inds = sorted(set(inds))
            if all_modelled:
                what = "Values are"
            elif len(inds) <= 2:
                what = "Values for " + "; ".join(labels.get(k) or k for k in inds) + " are"
            else:
                what = f"Values for {len(inds)} series are"
            src_text = "; ".join(f"{lab}, {vin}" if vin else lab for lab, vin in cites)
            out.footnotes.append(f"{what} ILO modelled estimates ({src_text}).")
    if len(mod_other):
        out.add("ilo_modelled", "modelled_estimates", "info",
                f"{_count(len(mod_other), 'observation')} {'is a' if len(mod_other) == 1 else 'are'} model-based "
                f"estimate{'' if len(mod_other) == 1 else 's'} from non-ILO sources.",
                {"indicators": _limited(sorted(set(mod_other["indicator"]))),
                 "sources": sorted(set(mod_other["source"]))})
    return out


@register_rule("forecast_years", "WARN when observations are flagged obs_status 'forecast'; footnote "
               "'Values for YYYY onward are forecasts (source, vintage)'.", ("warn",))
def rule_forecast_years(inp: CheckInput) -> RuleOutput:
    out = RuleOutput()
    df = inp.data
    fc = df[(df["obs_status"] == "forecast")]
    if fc.empty:
        return out
    first: dict[tuple, dict] = {}   # cites -> {"years": {series: first year}, "windows": set}
    for (i, src, ds), g in fc.groupby(["_result", "source", "dataset"], sort=True):
        cites = inp.cites_for(int(i), str(src), str(ds))
        slot = first.setdefault(cites, {"years": {}, "windows": set()})
        for row in g.itertuples(index=False):
            yrs = period_years(row.period)
            if yrs is None:
                if window_years(row.period):
                    slot["windows"].add(str(row.period))
                continue
            key = (row.indicator, row.iso3)
            slot["years"][key] = min(slot["years"].get(key, yrs[0]), yrs[0])
    periods = sorted(set(fc["period"]))
    out.add("forecast_years", "forecast_years", "warn",
            f"{_count(len(fc), 'observation')} flagged as forecast (periods {_join(periods)}).",
            {"periods": _limited(periods), "rows": int(len(fc)), "indicators": _limited(sorted(set(fc["indicator"]))),
             "sources": [_cite_text(c) for c in sorted(first)]})
    for cites, slot in sorted(first.items()):
        src_text = _cite_text(cites)
        if slot["years"]:
            years = sorted(set(slot["years"].values()))
            text = f"Values for {years[0]} onward are forecasts ({src_text})"
            if len(years) > 1:
                text += f"; the first forecast year varies by series ({years[0]}-{years[-1]})"
            out.footnotes.append(text + ".")
        if slot["windows"]:
            out.footnotes.append(f"Averages for {_join(sorted(slot['windows']))} include forecast years ({src_text}).")
    return out


@register_rule("vintage_drift", "WARN when one dataset (same source and dataset name in provenance) appears with "
               "several vintages.", ("info", "warn"))
def rule_vintage_drift(inp: CheckInput) -> RuleOutput:
    out = RuleOutput()
    seen: dict[tuple[str, str], dict] = {}
    for p in inp.provenance(derived=False):
        slot = seen.setdefault((p.source, p.dataset), {"vintages": set(), "unstated": 0, "label": p.citation or p.dataset or p.source})
        if p.vintage:
            slot["vintages"].add(p.vintage)
        else:
            slot["unstated"] += 1
    for (src, ds), slot in sorted(seen.items()):
        vintages = sorted(slot["vintages"])
        name = f"{src}/{ds}" if ds else src
        if len(vintages) > 1:
            out.add("vintage_drift", "vintage_drift", "warn",
                    f"{name} appears with {len(vintages)} vintages ({', '.join(vintages)}); pin one vintage.",
                    {"source": src, "dataset": ds, "vintages": vintages},
                    footnote=f"{slot['label']} data combine vintages {' and '.join(vintages)}.")
        elif vintages and slot["unstated"]:
            out.add("vintage_drift", "vintage_unstated", "info",
                    f"{name} has {_count(slot['unstated'], 'provenance entry', 'provenance entries')} without a vintage "
                    f"next to vintage {vintages[0]}.",
                    {"source": src, "dataset": ds, "vintages": vintages, "unstated": slot["unstated"]})
    return out


@register_rule("missing_provenance", "BLOCK when a result has no provenance, no data source in its provenance, or no "
               "source line can be built; WARN when data sources in the frame are missing from provenance.",
               ("info", "warn", "block"))
def rule_missing_provenance(inp: CheckInput) -> RuleOutput:
    out = RuleOutput()
    for i, res in enumerate(inp.results):
        provs = list(res.provenance or [])
        if not provs:
            out.add("missing_provenance", "missing_provenance", "block", f"Input {i} has no provenance.", {"result": i})
            continue
        bad = [type(p).__name__ for p in provs if not isinstance(p, Provenance)]
        if bad:
            out.add("missing_provenance", "missing_provenance", "block",
                    f"Input {i} has provenance entries that are not Provenance records.", {"result": i, "types": sorted(set(bad))})
            continue
        try:
            line = res.source_line()
        except Exception as exc:  # malformed provenance fields
            out.add("missing_provenance", "missing_provenance", "block",
                    f"Input {i}: the source line could not be built ({exc.__class__.__name__}).", {"result": i})
            continue
        # Result.source_line() returns "Source: ." when every cite is empty, so check the cites directly
        if not line or not any(p.citation or p.dataset or p.source for p in provs):
            out.add("missing_provenance", "missing_provenance", "block",
                    f"Input {i}: no source line can be built (provenance has no citation, dataset or source).", {"result": i})
            continue
        data_provs = [p for p in provs if p.source != DERIVED_SOURCE and (p.citation or p.dataset or p.source)]
        if not data_provs:
            out.add("missing_provenance", "missing_provenance", "block",
                    f"Input {i}: provenance lists only cgjrkit processing steps, no data source.",
                    {"result": i, "tools": sorted({p.tool for p in provs if p.tool})})
            continue
        frame = inp.frames[i]
        in_data = {s for s in frame["source"].unique() if s and s != DERIVED_SOURCE}
        missing = sorted(in_data - {p.source for p in data_provs})
        if missing:
            out.add("missing_provenance", "provenance_incomplete", "warn",
                    f"Input {i}: data from {', '.join(missing)} has no provenance entry.", {"result": i, "sources": missing})
        unpinned = [p.source for p in data_provs if not p.payload_sha256]
        if unpinned:
            out.add("missing_provenance", "provenance_unpinned", "info",
                    f"Input {i}: {_count(len(unpinned), 'data-source provenance entry', 'data-source provenance entries')} "
                    "without a payload hash.",
                    {"result": i, "sources": sorted(set(unpinned))})
    return out


@register_rule("series_break", "WARN when observations are flagged obs_status 'break'.", ("warn",))
def rule_series_break(inp: CheckInput) -> RuleOutput:
    out = RuleOutput()
    df = inp.data
    br = df[df["obs_status"] == "break"]
    if br.empty:
        return out
    at = br[["indicator", "indicator_label", "iso3", "country", "period"]].drop_duplicates().sort_values(
        ["indicator", "iso3", "period"], kind="mergesort")
    out.add("series_break", "series_break", "warn",
            f"{_count(len(at), 'observation')} flagged as series break (periods {_join(sorted(set(at['period'])))}).",
            {"at": _limited([{"indicator": r.indicator, "iso3": r.iso3, "period": r.period} for r in at.itertuples()]),
             "n": int(len(at))})
    notes = []
    for (label, period), g in at.assign(_label=at["indicator_label"].where(at["indicator_label"] != "", at["indicator"])).groupby(
            ["_label", "period"], sort=True):
        who = sorted({(c or i) for c, i in zip(g["country"], g["iso3"])})
        notes.append(f"Series break in {period}: {label} ({_join(who)}).")
    if len(notes) > MAX_BREAK_FOOTNOTES:
        extra = len(notes) - MAX_BREAK_FOOTNOTES
        notes = notes[:MAX_BREAK_FOOTNOTES] + [f"Series breaks in {extra} further indicator-periods."]
    out.footnotes.extend(notes)
    return out


@register_rule("covid_endpoints", "WARN when an averaging window starts or ends in 2020 or 2021 (windows from "
               "Result.meta['periods'], cgjrkit provenance params, context['periods'] or window-shaped period labels).",
               ("warn",))
def rule_covid_endpoints(inp: CheckInput) -> RuleOutput:
    out = RuleOutput()
    covid = tuple(int(y) for y in inp.context.get("covid_years", COVID_YEARS))
    windows: list[dict] = []
    if "periods" in inp.context:
        windows += _windows_from(inp.context["periods"], "context")
    for i, res in enumerate(inp.results):
        meta = res.meta if isinstance(res.meta, Mapping) else {}
        for key in ("periods", "windows"):
            if key in meta:
                windows += _windows_from(meta[key], f"input {i} meta")
        for p in res.provenance or []:
            if isinstance(p, Provenance) and p.source == DERIVED_SOURCE and isinstance(p.params, Mapping) and "periods" in p.params:
                windows += _windows_from(p.params["periods"], f"input {i} {p.tool or 'cgjrkit'}")
    labels = sorted(set(inp.data["period"])) if not inp.data.empty else []
    windows += _windows_from([lab for lab in labels if window_years(lab)], "period labels")

    hits: dict[tuple[str, str, str], dict] = {}
    for w in windows:
        touches = [y for y in covid if y in w["start_years"] or y in w["end_years"]]
        if touches:
            hits.setdefault((w["label"], w["start"], w["end"]), {"label": w["label"], "start": w["start"], "end": w["end"],
                                                                  "origin": w["origin"]})
    if not hits:
        return out
    found = [hits[k] for k in sorted(hits)]
    names = sorted({h["label"] for h in found})
    span = "-".join(str(y) for y in (min(covid), max(covid))) if len(covid) > 1 else str(covid[0])
    out.add("covid_endpoints", "covid_endpoints", "warn",
            f"{_count(len(names), 'averaging window')} start{'s' if len(names) == 1 else ''} or end{'s' if len(names) == 1 else ''} "
            f"in {span}: {_join(names)}.",
            {"windows": _limited(found), "covid_years": list(covid)},
            footnote=f"Averages for {_join(names)} start or end in {span} (COVID-19 years).")
    return out


def _peer_entries(obj: Any) -> list[tuple[str, str | None]]:
    """``(name, frozen_at)`` for one or many peer sets (PeerSet objects or mappings)."""
    if obj is None:
        return []
    if isinstance(obj, (list, tuple, set)):
        return [e for item in obj for e in _peer_entries(item)]
    if isinstance(obj, Mapping):
        if {"members", "frozen_at", "name", "code"} & set(obj):
            name = str(obj.get("code") or obj.get("name") or "unnamed")
            return [(name, _iso_date(obj.get("frozen_at")))]
        return [e for item in obj.values() for e in _peer_entries(item)]
    if hasattr(obj, "frozen_at"):
        name = str(getattr(obj, "code", "") or getattr(obj, "name", "") or "unnamed")
        return [(name, _iso_date(getattr(obj, "frozen_at")))]
    return []


@register_rule("peers_not_frozen", "BLOCK in replication mode when a peer set has no frozen_at (WARN otherwise); "
               "also flags a peer set frozen after context['results_at'].", ("warn", "block"))
def rule_peers_not_frozen(inp: CheckInput) -> RuleOutput:
    out = RuleOutput()
    sets: dict[str, str | None] = {}

    def note(name: str, frozen: str | None) -> None:
        # conservative merge: any record without frozen_at wins; otherwise keep the latest date
        if name not in sets:
            sets[name] = frozen
        elif sets[name] is not None:
            sets[name] = None if frozen is None else max(sets[name], frozen)

    for name, frozen in _peer_entries(inp.context.get("peer_sets")) + _peer_entries(inp.context.get("peer_set")):
        note(name, frozen)
    for res in inp.results:
        meta = res.meta if isinstance(res.meta, Mapping) else {}
        for name, frozen in _peer_entries(meta.get("peer_sets")):
            note(name, frozen)
        # methods record the peer sets they used in their provenance params
        for p in res.provenance or []:
            if isinstance(p, Provenance) and p.source == DERIVED_SOURCE and isinstance(p.params, Mapping):
                for key in ("peerset", "peer_set", "peer_sets"):
                    for name, frozen in _peer_entries(p.params.get(key)):
                        note(name, frozen)
        for c in res.caveats or []:
            if getattr(c, "code", "") == "peer_set_not_frozen":
                note(str(c.evidence.get("peer_set") or "unnamed"), None)
    if not sets:
        return out
    severity: Severity = "block" if inp.replication else "warn"
    mode = "replication" if inp.replication else "analysis"
    unfrozen = sorted(n for n, f in sets.items() if f is None)
    if unfrozen:
        out.add("peers_not_frozen", "peers_not_frozen", severity,
                f"Peer set{'s' if len(unfrozen) > 1 else ''} {', '.join(unfrozen)} ha{'ve' if len(unfrozen) > 1 else 's'} "
                f"no frozen_at date" + ("; replication needs peers frozen before results." if inp.replication else "."),
                {"peer_sets": unfrozen, "mode": mode})
    results_at = _iso_date(inp.context.get("results_at"))
    if results_at:
        late = sorted(n for n, f in sets.items() if f is not None and f > results_at)
        if late:
            out.add("peers_not_frozen", "peers_frozen_late", severity,
                    f"Peer set{'s' if len(late) > 1 else ''} {', '.join(late)} frozen after the results ({results_at}).",
                    {"peer_sets": {n: sets[n] for n in late}, "results_at": results_at, "mode": mode})
    return out


def _min_n_for(inp: CheckInput, res: Result) -> int:
    if inp.context.get("min_n") is not None:
        return int(inp.context["min_n"])
    meta = res.meta if isinstance(res.meta, Mapping) else {}
    if meta.get("min_n") is not None:
        return int(meta["min_n"])
    for p in reversed(res.provenance or []):
        if isinstance(p, Provenance) and isinstance(p.params, Mapping) and p.params.get("min_n") is not None:
            try:
                return int(p.params["min_n"])
            except (TypeError, ValueError):
                continue
    return DEFAULT_MIN_N


@register_rule("small_n", "WARN when peer aggregates rest on fewer than min_n members with data ('n' column of the "
               "data, or 'n' in meta['cells'] / meta['peer_aggregates']).", ("warn",))
def rule_small_n(inp: CheckInput) -> RuleOutput:
    out = RuleOutput()
    small: list[dict] = []
    floors: set[int] = set()
    for i, (res, df) in enumerate(zip(inp.results, inp.frames)):
        min_n = _min_n_for(inp, res)
        if "n" in df.columns:
            n = pd.to_numeric(df["n"], errors="coerce")
            sub = df.assign(cc_n=n)[n.notna() & (n < min_n)]
            if len(sub):
                floors.add(min_n)
                name = sub["group"] if "group" in sub.columns else sub["iso3"]
                name = name.where(name.astype(str) != "", sub["iso3"]).astype(str)
                sub = sub.assign(cc_agg=name).drop_duplicates(["cc_agg", "indicator", "period", "cc_n"])
                for r in sub.sort_values(["cc_agg", "indicator", "period"], kind="mergesort").itertuples():
                    small.append({"result": i, "aggregate": r.cc_agg, "indicator": r.indicator, "period": r.period,
                                  "n": int(r.cc_n), "min_n": min_n})
        meta = res.meta if isinstance(res.meta, Mapping) else {}
        cells = meta.get("cells") if isinstance(meta.get("cells"), list) else []
        for cell in cells:
            if isinstance(cell, Mapping) and isinstance(cell.get("n"), (int, float)) and cell["n"] < min_n:
                floors.add(min_n)
                small.append({"result": i, "aggregate": str(cell.get("column", "")), "indicator": str(cell.get("row", "")),
                              "period": str(cell.get("period", "")), "n": int(cell["n"]), "min_n": min_n})
        aggs = meta.get("peer_aggregates") if isinstance(meta.get("peer_aggregates"), Mapping) else {}
        for code, entry in sorted(aggs.items()):
            if isinstance(entry, Mapping) and isinstance(entry.get("n"), (int, float)) and entry["n"] < min_n:
                floors.add(min_n)
                small.append({"result": i, "aggregate": str(code), "indicator": "", "period": "", "n": int(entry["n"]),
                              "min_n": min_n})
    if not small:
        return out
    floor = max(floors)
    lowest = min(s["n"] for s in small)
    out.add("small_n", "small_n", "warn",
            f"{_count(len(small), 'peer aggregate')} rest{'s' if len(small) == 1 else ''} on fewer than {floor} members "
            f"with data (lowest n = {lowest}).",
            {"aggregates": _limited(small), "n_aggregates": len(small), "min_n": sorted(floors)},
            footnote=f"Some peer aggregates are based on fewer than {floor} countries with data (lowest n = {lowest}).")
    return out


def _licence_registry() -> dict[str, dict] | None:
    """Licence entries from :mod:`cgjrkit.registries`, or None when the registry is unavailable."""
    try:
        module = importlib.import_module("cgjrkit.registries")
        loader = getattr(module, "load_licences")
        entries = loader()
    except Exception:  # registry absent, unreadable or older API: the rule degrades gracefully
        return None
    return entries if isinstance(entries, Mapping) else None


@register_rule("licence_restricted", "WARN for sources whose licence restricts reuse (licence registry: commercial "
               "use 'no'/'restricted', suggested caveat warn/block, unregistered sources, or a non-commercial "
               "provenance licence); BLOCK restricted sources when context['redistribution'] is true.",
               ("info", "warn", "block"))
def rule_licence_restricted(inp: CheckInput) -> RuleOutput:
    out = RuleOutput()
    rule = "licence_restricted"
    redistribution = inp.redistribution
    provs = inp.provenance(derived=False)
    sources = {p.source for p in provs if p.source}
    sources |= {s for s in inp.data["source"].unique() if s and s != DERIVED_SOURCE} if not inp.data.empty else set()
    registry = _licence_registry()
    if registry is None and sources:
        out.add(rule, "licence_registry_unavailable", "info",
                "Licence registry not available; only provenance licence fields were checked.", {"sources": sorted(sources)})
    for src in sorted(sources):
        entry = registry.get(src) if registry is not None else None
        if registry is not None and entry is None:
            out.add(rule, "licence_unregistered", "warn", f"Licence terms for {src!r} are not registered; check before use.",
                    {"source": src, "redistribution": redistribution})
        elif entry is not None:
            level = str(entry.get("caveat", "none"))
            use = str(entry.get("commercial_use", "unknown"))
            restricted = use in ("no", "restricted") or level == "block"
            severity: Severity | None
            if redistribution:
                severity = "block" if restricted else ("warn" if use == "unknown" or level == "warn" else
                                                       ("info" if level == "info" else None))
            else:
                severity = "warn" if restricted or level in ("warn", "block") else ("info" if level == "info" else None)
            if severity is not None:
                name = entry.get("name", src)
                msg = (f"{name}: {entry.get('licence', 'licence unknown')}; commercial use {use} "
                       f"({entry.get('commercial_use_status', 'U')}).")
                if severity == "block":
                    msg += " Not cleared for redistribution."
                out.add(rule, rule, severity, msg,
                        {"source": src, "licence": entry.get("licence", ""), "commercial_use": use,
                         "commercial_use_status": entry.get("commercial_use_status", ""), "registry_caveat": level,
                         "redistribution": redistribution, "notes": entry.get("notes", "")})
        # provenance licence text, e.g. per-indicator licences recorded by connectors
        nc = sorted({p.license for p in provs if p.source == src and p.license and _NC_LICENCE.search(p.license)})
        if nc:
            out.add(rule, "licence_noncommercial", "block" if redistribution else "warn",
                    f"Provenance licence for {src!r} is non-commercial: {'; '.join(nc)}.",
                    {"source": src, "licences": nc, "redistribution": redistribution})
    return out


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------
def _status(caveats: Sequence[Caveat]) -> Status:
    level = worst(list(caveats))
    return "BLOCK" if level == "block" else ("WARN" if level == "warn" else "PASS")


def _as_results(results: Result | pd.DataFrame | Sequence[Result | pd.DataFrame]) -> list[Result]:
    items = [results] if isinstance(results, (Result, pd.DataFrame)) else list(results)
    out: list[Result] = []
    for item in items:
        if isinstance(item, Result):
            out.append(item)
        elif isinstance(item, pd.DataFrame):
            out.append(Result(data=item))  # no provenance: missing_provenance will BLOCK
        else:
            raise TypeError(f"run_caveat_check expects Result objects, got {type(item).__name__}")
    if not out:
        raise ValueError("run_caveat_check needs at least one Result")
    return out


def run_caveat_check(
    results: Result | Sequence[Result],
    context: Mapping[str, Any] | None = None,
    *,
    rules: Sequence[str] | None = None,
    skip: Sequence[str] = (),
) -> CaveatReport:
    """Run the registered caveat rules over the inputs of one output.

    ``results`` is a Result or a list of Results (plain DataFrames are wrapped
    and will BLOCK for missing provenance). ``context`` is described in the
    module docstring. ``rules`` restricts the run to the named rules and
    ``skip`` leaves rules out. A rule that raises is reported as a ``warn``
    ``rule_error`` caveat (and in ``errors``) rather than aborting the check.
    """
    items = _as_results(results)
    ctx = dict(context or {})
    names = list(RULES) if rules is None else list(rules)
    unknown = sorted(set(names) - set(RULES)) + sorted(set(skip) - set(RULES))
    if unknown:
        raise ValueError(f"unknown caveat rules {unknown}; registered: {list(RULES)}")
    names = [n for n in names if n not in set(skip)]
    inp = CheckInput(items, ctx)

    caveats: list[Caveat] = []
    footnotes: list[str] = []
    errors: dict[str, str] = {}
    extra_keys = sorted(set(ctx) - CONTEXT_KEYS)
    for name in names:
        try:
            found = RULES[name].func(inp)
        except Exception as exc:
            errors[name] = f"{exc.__class__.__name__}: {exc}"
            caveats.append(Caveat("rule_error", "warn", f"Caveat rule {name} could not run ({exc.__class__.__name__}).",
                                  {"rule": name, "error": errors[name]}))
            continue
        for c in found.caveats:
            if c not in caveats:
                caveats.append(c)
        for note in found.footnotes:
            if note not in footnotes:
                footnotes.append(note)
    if extra_keys:
        caveats.append(Caveat("context_keys_unused", "info", f"Context keys not read by any rule: {', '.join(extra_keys)}.",
                              {"rule": "engine", "keys": extra_keys}))

    inherited: list[Caveat] = []
    if ctx.get("inherit_caveats", True):
        for res in items:
            for c in res.caveats or []:
                if isinstance(c, Caveat) and c not in inherited and c not in caveats:
                    inherited.append(c)
    everything = caveats + inherited
    return CaveatReport(status=_status(everything), caveats=everything, footnotes=footnotes, rules_run=names,
                        inherited=inherited, errors=errors)
