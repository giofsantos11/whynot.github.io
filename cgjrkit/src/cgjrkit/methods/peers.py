"""Peer sets and peer-group aggregates.

A :class:`PeerSet` records *which* countries a report benchmarks against and
*who decided*: name, ISO3 members, selection criteria, approver, the ISO date
the set was frozen, and notes. Sets are stored as YAML, one file per report
(several sets per file, e.g. structural, aspirational and one alternative)::

    report: KEN-2026
    peer_sets:
      - name: Structural peers
        code: STRUCT
        members: [GHA, TZA, UGA]
        criteria: "Lower-middle-income, population above 20 million, SSA"
        approver: "Task team leader"
        frozen_at: "2026-03-01"
        notes: ""

:func:`peer_aggregate` computes group statistics (mean, median, quartiles,
...) per indicator and period, with the number of members that had data
(``n``). Membership can also vary by year: pass an income classification
table (e.g. the World Bank's historical income groups) and the aggregate is
built from the countries in each group *in that year*.

Output frame
------------
Canonical columns plus ``stat`` (statistic name), ``n`` (members with data)
and ``group`` (group code). ``iso3`` is ``"<group code>:<stat>"`` (for
example ``"STRUCT:median"``) so the frame stays unique on the canonical key;
``country`` is ``"<name> (<stat>)"``. ``Result.meta["aggregates"]`` lists the
codes (as connectors do) and ``Result.meta["peer_aggregates"]`` describes each. Quantiles use linear interpolation between order statistics
(numpy/pandas default, Excel ``PERCENTILE.INC``, R type 7); Stata's default
centiles differ, so pass ``quantile_method`` to match another convention (the
median follows the same ``quantile_method`` as p10/p25/p75/p90).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd
import yaml

from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Result
from cgjrkit.methods.harmonize import (
    Frame,
    PPPPolicy,
    _as_result,
    _finish,
    _limited,
    _ppp_check_groups,
    combine_status,
    parse_period,
)

__all__ = [
    "PeerSet",
    "load_peersets",
    "save_peersets",
    "normalize_classification",
    "income_group",
    "members_by_year",
    "peer_aggregate",
    "STATS",
]

_ISO3_RE = re.compile(r"^[A-Z]{3}$")
_CODE_RE = re.compile(r"[^A-Z0-9]+")


def _iso_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ValueError(f"frozen_at must be an ISO date (YYYY-MM-DD), got {value!r}") from exc


# ---------------------------------------------------------------------------
# PeerSet
# ---------------------------------------------------------------------------
@dataclass
class PeerSet:
    """A named, documented set of peer countries.

    ``members`` are ISO 3166-1 alpha-3 codes (validated, de-duplicated input is
    refused). ``frozen_at`` is the ISO date on which the approver froze the
    set; an unfrozen set raises a ``peer_set_not_frozen`` caveat when used.
    ``code`` labels the aggregates (default: the name in upper case with
    non-alphanumerics replaced by ``_``); ``focus`` is the report country.
    """

    name: str
    members: list[str]
    criteria: str = ""
    approver: str = ""
    frozen_at: str | None = None
    notes: str = ""
    code: str = ""
    focus: str = ""

    def __post_init__(self) -> None:
        self.name = str(self.name or "").strip()
        if not self.name:
            raise ValueError("a peer set needs a name")
        if isinstance(self.members, str):
            raise TypeError("members must be a list of ISO3 codes, not a string")
        members = [str(m).strip().upper() for m in (self.members or [])]
        bad = [m for m in members if not _ISO3_RE.match(m)]
        if bad:
            raise ValueError(f"peer set {self.name!r}: not ISO3 codes: {bad}")
        dupes = sorted({m for m in members if members.count(m) > 1})
        if dupes:
            raise ValueError(f"peer set {self.name!r}: duplicate members {dupes}")
        if not members:
            raise ValueError(f"peer set {self.name!r} has no members")
        self.members = members
        self.frozen_at = _iso_date(self.frozen_at)
        self.code = _CODE_RE.sub("_", str(self.code or self.name).upper()).strip("_")
        self.focus = str(self.focus or "").strip().upper()
        if self.focus and not _ISO3_RE.match(self.focus):
            raise ValueError(f"peer set {self.name!r}: focus must be an ISO3 code, got {self.focus!r}")
        for attr in ("criteria", "approver", "notes"):
            setattr(self, attr, "" if getattr(self, attr) is None else str(getattr(self, attr)).strip())

    @property
    def is_frozen(self) -> bool:
        return self.frozen_at is not None

    def freeze(self, approver: str, on: str | date) -> "PeerSet":
        """Return a frozen copy approved by ``approver`` on ``on`` (ISO date)."""
        if not str(approver).strip():
            raise ValueError("freezing a peer set needs an approver")
        return replace(self, approver=str(approver), frozen_at=_iso_date(on))

    def to_dict(self) -> dict:
        d = asdict(self)
        order = ["name", "code", "members", "criteria", "approver", "frozen_at", "focus", "notes"]
        return {k: d[k] for k in order}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PeerSet":
        known = {"name", "members", "criteria", "approver", "frozen_at", "notes", "code", "focus"}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ValueError(f"unknown peer-set fields: {unknown}")
        return cls(**{k: data[k] for k in data if k in known})

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), sort_keys=False, allow_unicode=True, default_flow_style=None)

    def save(self, path: str | Path) -> Path:
        """Write this set alone to ``path`` (YAML)."""
        p = Path(path)
        p.write_text(self.to_yaml(), encoding="utf-8")
        return p

    @classmethod
    def load(cls, path: str | Path, name: str | None = None) -> "PeerSet":
        """Load one set from a YAML file (a single-set file, or pick ``name``/``code`` from a multi-set file)."""
        sets = load_peersets(path)
        if name is None:
            if len(sets) != 1:
                raise ValueError(f"{path} holds {len(sets)} peer sets; pass name= one of {sorted(sets)}")
            return next(iter(sets.values()))
        for ps in sets.values():
            if name in (ps.name, ps.code):
                return ps
        raise KeyError(f"no peer set named {name!r} in {path}")


def _yaml_source(path_or_text: str | Path) -> Any:
    if isinstance(path_or_text, Path) or (
        isinstance(path_or_text, str) and "\n" not in path_or_text
        and (Path(path_or_text).suffix in (".yaml", ".yml") or Path(path_or_text).is_file())
    ):
        return yaml.safe_load(Path(path_or_text).read_text(encoding="utf-8"))
    return yaml.safe_load(str(path_or_text))


def load_peersets(path_or_text: str | Path) -> dict[str, PeerSet]:
    """Load peer sets from a YAML file path or YAML text, keyed by ``code``.

    Accepts a single set (mapping with ``name`` and ``members``), a list of
    sets, or ``{report: ..., peer_sets: [...]}``.
    """
    data = _yaml_source(path_or_text)
    if isinstance(data, Mapping) and "peer_sets" in data:
        items = data["peer_sets"]
    elif isinstance(data, Mapping):
        items = [data]
    elif isinstance(data, list):
        items = data
    else:
        raise ValueError("peer-set YAML must be a mapping or a list of mappings")
    out: dict[str, PeerSet] = {}
    for item in items:
        ps = PeerSet.from_dict(item)
        if ps.code in out:
            raise ValueError(f"duplicate peer-set code {ps.code!r}")
        out[ps.code] = ps
    return out


def save_peersets(peersets: Iterable[PeerSet] | Mapping[str, PeerSet], path: str | Path, *, report: str = "") -> Path:
    """Write several sets to one YAML file (``{report, peer_sets: [...]}``)."""
    sets = list(peersets.values()) if isinstance(peersets, Mapping) else list(peersets)
    doc: dict[str, Any] = {}
    if report:
        doc["report"] = report
    doc["peer_sets"] = [ps.to_dict() for ps in sets]
    p = Path(path)
    p.write_text(yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, default_flow_style=None), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# income classification by year
# ---------------------------------------------------------------------------
_MISSING_GROUP = {"", "..", "nan", "none", "null"}


def normalize_classification(
    table: pd.DataFrame,
    *,
    iso3_col: str = "iso3",
    year_col: str = "year",
    group_col: str = "income_group",
) -> pd.DataFrame:
    """Return a long ``iso3, year, group`` table (one row per country-year).

    Accepts a long table (``iso3_col``, ``year_col``, ``group_col``) or a wide
    one (``iso3_col`` plus one column per year, as in the World Bank's
    historical classification file, whose layout must first be reduced to
    ISO3 + year columns by the caller). ``year`` is the calendar year whose
    data the classification applies to; the World Bank's FY classification
    released on 1 July of year t uses GNI per capita of year t-1, so map the
    years before calling if needed. Missing codes (``""``, ``".."``) are
    dropped.
    """
    if iso3_col not in table.columns:
        raise ValueError(f"classification table needs an {iso3_col!r} column")
    if year_col in table.columns and group_col in table.columns:
        long = table[[iso3_col, year_col, group_col]].copy()
    else:
        year_cols = [c for c in table.columns if c != iso3_col and re.fullmatch(r"\d{4}", str(c).strip())]
        if not year_cols:
            raise ValueError("classification table must be long (iso3, year, income_group) or wide (iso3 + year columns)")
        long = table.melt(id_vars=[iso3_col], value_vars=year_cols, var_name=year_col, value_name=group_col)
    long.columns = ["iso3", "year", "group"]
    long["iso3"] = long["iso3"].astype(str).str.strip().str.upper()
    long["year"] = pd.to_numeric(long["year"].astype(str).str.strip(), errors="coerce")
    long["group"] = long["group"].astype(str).str.strip()
    long = long[long["year"].notna() & ~long["group"].str.lower().isin(_MISSING_GROUP)]
    long["year"] = long["year"].astype(int)
    dup = long.duplicated(subset=["iso3", "year"], keep=False)
    if dup.any():
        bad = long.loc[dup, ["iso3", "year"]].drop_duplicates().head(5).to_dict("records")
        raise ValueError(f"classification has several groups for the same country-year, e.g. {bad}")
    return long.sort_values(["iso3", "year"]).reset_index(drop=True)


def income_group(classification: pd.DataFrame, iso3: str, year: int) -> str | None:
    """Group of ``iso3`` in ``year`` (None if not classified)."""
    cls = normalize_classification(classification) if "group" not in classification.columns else classification
    hit = cls[(cls["iso3"] == iso3.upper()) & (cls["year"] == int(year))]
    return str(hit["group"].iloc[0]) if len(hit) else None


def members_by_year(classification: pd.DataFrame, group: str) -> dict[int, list[str]]:
    """``{year: sorted ISO3 members}`` of ``group``."""
    cls = normalize_classification(classification) if "group" not in classification.columns else classification
    sub = cls[cls["group"] == group]
    return {int(y): sorted(g["iso3"]) for y, g in sub.groupby("year", sort=True)}


# ---------------------------------------------------------------------------
# peer_aggregate
# ---------------------------------------------------------------------------
STATS: dict[str, str] = {
    "mean": "arithmetic mean",
    "median": "median",
    "p10": "10th percentile",
    "p25": "25th percentile",
    "p75": "75th percentile",
    "p90": "90th percentile",
    "min": "minimum",
    "max": "maximum",
    "sd": "standard deviation (n-1)",
    "count": "number of members with data",
}


def _stat(values: np.ndarray, stat: str, quantile_method: str) -> float:
    if values.size == 0:
        return float("nan")
    if stat == "mean":
        return float(np.mean(values))
    if stat == "median":
        # same convention as the quantiles, so an IQR band and its centre line agree
        return float(np.percentile(values, 50, method=quantile_method))
    if stat in ("p10", "p25", "p75", "p90"):
        return float(np.percentile(values, int(stat[1:]), method=quantile_method))
    if stat == "min":
        return float(np.min(values))
    if stat == "max":
        return float(np.max(values))
    if stat == "sd":
        return float(np.std(values, ddof=1)) if values.size > 1 else float("nan")
    if stat == "count":
        return float(values.size)
    raise ValueError(f"unknown statistic {stat!r}; choose from {sorted(STATS)}")


def _period_year(period: str) -> int | None:
    try:
        return parse_period(period).year
    except ValueError:
        head = str(period)[:4]
        return int(head) if head.isdigit() else None


def peer_aggregate(
    frame: Frame,
    peerset: PeerSet | None = None,
    stats: Sequence[str] = ("mean", "median", "p25", "p75"),
    min_n: int = 3,
    *,
    by: Sequence[str] = ("indicator",),
    classification: pd.DataFrame | None = None,
    groups: Sequence[str] | None = None,
    group_prefix: str = "IG_",
    drop_below_min_n: bool = False,
    quantile_method: str = "linear",
    on_ppp_mix: PPPPolicy = "raise",
) -> Result:
    """Peer-group statistics per indicator and period, with member counts.

    * ``peerset`` alone: one aggregate over the set's members (code =
      ``peerset.code``).
    * ``classification`` (see :func:`normalize_classification`): one aggregate
      per income group, with membership read for each period's year; with a
      ``peerset`` too, membership is the intersection (peers that were in the
      group that year). Codes are ``group_prefix + group`` (``"IG_LM"``), or
      ``"<peerset code>_<group>"`` when a peer set is given. ``groups``
      restricts the groups computed.

    Only non-missing values count. ``n`` is the number of members with data;
    aggregates with ``n < min_n`` get a ``peer_small_n`` caveat (and are
    dropped when ``drop_below_min_n``). Members never seen in the frame are
    listed in a ``peer_members_missing`` caveat; an unfrozen peer set adds
    ``peer_set_not_frozen``. Members of one indicator must share a PPP base.
    """
    if peerset is None and classification is None:
        raise ValueError("peer_aggregate needs a peerset, a classification table, or both")
    stats = list(stats)
    unknown = [s for s in stats if s not in STATS]
    if unknown:
        raise ValueError(f"unknown statistics {unknown}; choose from {sorted(STATS)}")
    if min_n < 1:
        raise ValueError("min_n must be at least 1")
    res = _as_result(frame)
    df = res.data
    by = list(by)
    if "indicator" not in by:
        raise ValueError("by must include 'indicator'")
    missing_cols = [c for c in by if c not in df.columns]
    if missing_cols or "period" in by or "iso3" in by:
        raise ValueError(f"invalid 'by' {by}: columns must exist and exclude period/iso3")

    # -- membership ---------------------------------------------------------
    cls = normalize_classification(classification) if classification is not None else None
    group_defs: list[tuple[str, str, Any]] = []  # (code, name, members spec)
    if cls is None:
        group_defs.append((peerset.code, peerset.name, set(peerset.members)))
    else:
        wanted = list(groups) if groups is not None else sorted(cls["group"].unique())
        for g in wanted:
            code = f"{peerset.code}_{g}" if peerset is not None else f"{group_prefix}{g}"
            name = f"{peerset.name}, {g}" if peerset is not None else f"Income group {g}"
            yearly = {y: set(m) for y, m in members_by_year(cls, g).items()}
            if peerset is not None:
                yearly = {y: m & set(peerset.members) for y, m in yearly.items()}
            group_defs.append((code, name, yearly))
    candidates = set(peerset.members) if peerset is not None else set(cls["iso3"])
    data = df[df["iso3"].isin(candidates) & df["value"].notna()]

    caveats = _ppp_check_groups(data, by, "peer_aggregate", on_ppp_mix)

    rows: list[dict] = []
    small: list[dict] = []
    unclassified_years: set[int] = set()
    mixed_units: list[dict] = []
    for key, g in data.groupby(by + ["period"], sort=True):
        key = key if isinstance(key, tuple) else (key,)
        period = str(key[-1])
        year = _period_year(period)
        units = sorted({u for u in g["unit"] if u})
        if len(units) > 1:
            mixed_units.append({"group": "/".join(map(str, key)), "units": units})
        unit = units[0] if len(units) == 1 else "; ".join(units)
        sources = sorted(set(g["source"]))
        datasets = sorted(set(g["dataset"]))
        labels = [lab for lab in g["indicator_label"] if lab]
        for code, name, spec in group_defs:
            if isinstance(spec, set):
                members = spec
            else:
                if year not in spec:
                    unclassified_years.add(year if year is not None else -1)
                    continue
                members = spec[year]
            sub = g[g["iso3"].isin(members)]
            if sub["iso3"].duplicated().any():
                raise ValueError(f"peer_aggregate: several observations per country in {key}; add columns to 'by'")
            values = sub["value"].to_numpy(dtype="float64")
            n = int(values.size)
            if n == 0:
                continue
            if n < min_n:
                small.append({"group": code, "key": "/".join(map(str, key)), "n": n})
                if drop_below_min_n:
                    continue
            status = combine_status(sub["obs_status"])
            for stat in stats:
                row = {col: k for col, k in zip(by, key[:-1])}
                row.update({
                    "source": sources[0] if len(sources) == 1 else "cgjrkit",
                    "dataset": datasets[0] if len(datasets) == 1 else "derived",
                    "indicator_label": row.get("indicator_label", labels[0] if labels else ""),
                    "iso3": f"{code}:{stat}",
                    "country": f"{name} ({stat})",
                    "period": period,
                    "value": _stat(values, stat, quantile_method),
                    "unit": "count" if stat == "count" else unit,
                    "obs_status": status,
                    "stat": stat,
                    "n": n,
                    "group": code,
                })
                rows.append(row)

    columns = ["source", "dataset", "indicator", "indicator_label", "iso3", "country", "period", "value", "unit",
               "obs_status", "stat", "n", "group"]
    out = pd.DataFrame(rows)
    for col in columns:
        if col not in out.columns:
            out[col] = pd.Series(dtype="float64" if col == "value" else ("int64" if col == "n" else "object"))
    extra = [c for c in by if c not in columns]
    out = out[columns + extra]
    out["value"] = out["value"].astype("float64")
    out["n"] = out["n"].astype("int64")
    for col in columns:
        if col not in ("value", "n"):
            out[col] = out[col].fillna("").astype(str)
    out = out.sort_values(["indicator", "iso3", "period"], kind="mergesort").reset_index(drop=True)

    # -- caveats ----------------------------------------------------------------
    if peerset is not None and not peerset.is_frozen:
        caveats.append(Caveat("peer_set_not_frozen", "warn",
                              f"Peer set {peerset.name!r} has no frozen_at date/approver; record the approved set before publishing.",
                              {"peer_set": peerset.code}))
    if small:
        caveats.append(Caveat("peer_small_n", "warn",
                              f"{len(small)} aggregates rest on fewer than {min_n} members with data"
                              + (" and were dropped." if drop_below_min_n else "."),
                              {"aggregates": _limited(small), "n_aggregates": len(small), "min_n": min_n}))
    present = set(df["iso3"])
    absent = sorted(candidates - present) if peerset is not None else []
    if absent:
        caveats.append(Caveat("peer_members_missing", "info", f"{len(absent)} peer-set members have no rows in the input.",
                              {"members": absent}))
    if unclassified_years:
        caveats.append(Caveat("income_class_missing_years", "info",
                              "Some periods fall in years without an income classification; no group aggregate for them.",
                              {"years": sorted(unclassified_years)}))
    if mixed_units:
        caveats.append(Caveat("mixed_units", "warn", "Members of one indicator carry different unit labels.",
                              {"groups": _limited(mixed_units)}))

    aggregates = {}
    for code, name, spec in group_defs:
        for stat in stats:
            aggregates[f"{code}:{stat}"] = {"group": code, "name": name, "stat": STATS[stat],
                                            "members": sorted(spec) if isinstance(spec, set) else "by year (classification)"}
    params = {
        "peerset": peerset.to_dict() if peerset is not None else None,
        "stats": stats,
        "min_n": min_n,
        "by": by,
        "classification": None if cls is None else {"rows": int(len(cls)), "groups": sorted(cls["group"].unique()),
                                                     "years": [int(cls["year"].min()), int(cls["year"].max())]},
        "groups": list(groups) if groups is not None else None,
        "drop_below_min_n": drop_below_min_n,
        "quantile_method": quantile_method,
        "on_ppp_mix": on_ppp_mix,
    }
    result = _finish([res], out, "peer_aggregate", params, caveats,
                     [f"median and quantiles: numpy.percentile(method={quantile_method!r}); sd uses ddof=1"])
    result.provenance[-1].dataset = "peers"
    result.provenance[-1].tool = "peers.peer_aggregate"
    # Connectors keep meta["aggregates"] as a list of aggregate codes; follow that and
    # describe the peer aggregates separately.
    existing = result.meta.get("aggregates", [])
    codes = set(existing.keys()) if isinstance(existing, Mapping) else set(existing or [])
    result.meta["aggregates"] = sorted(codes | set(aggregates))
    result.meta.setdefault("peer_aggregates", {}).update(aggregates)
    if peerset is not None:
        result.meta.setdefault("peer_sets", {})[peerset.code] = peerset.to_dict()
    return result
