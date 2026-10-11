# Caveat rules (`cgjrkit.qa.caveat_check`)

`run_caveat_check(results, context=None)` runs every registered rule over the
inputs of **one output** (a chart, a table or an export) and returns a
`CaveatReport`:

| Field | Meaning |
|---|---|
| `status` | `BLOCK` if any caveat has severity `block` (do not render or export as is), `WARN` if any has `warn` (footnote it), else `PASS`. `info` never changes the status. |
| `caveats` | Caveats raised by the rules (each has `evidence["rule"]`), then the caveats the inputs already carried (`inherited`). |
| `footnotes` | Short chart-ready strings, in rule order, de-duplicated. |
| `rules_run`, `inherited`, `errors` | Rules that ran; caveats taken from the inputs; rules that raised (reported as a `warn` `rule_error` caveat, never swallowed). |

Results passed together are treated as the inputs of one output, so the rules
see problems that only appear when results are combined (two PPP bases, two
vintages of one dataset, a modelled series continued by a survey series).
Plain DataFrames are accepted but carry no provenance, so they BLOCK.

```python
from cgjrkit.qa.caveat_check import run_caveat_check

report = run_caveat_check([weo_result, ilo_result], {"mode": "replication"})
report.status      # "PASS" | "WARN" | "BLOCK"
report.footnotes   # ["Values for 2026 onward are forecasts (IMF, World Economic Outlook database, WEO 2026-04)."]
report.to_dict()   # JSON-safe, for MCP responses
```

Options: `rules=[...]` runs only the named rules, `skip=[...]` leaves rules out
(unknown names raise `ValueError`). `list_rules()` lists the registry.

## Context keys

| Key | Used by | Effect |
|---|---|---|
| `mode="replication"` (or `replication=True`) | `peers_not_frozen` | Unfrozen or late-frozen peer sets BLOCK instead of WARN. |
| `redistribution=True` (or `purpose="redistribution"`) | `licence_restricted` | Restricted licences BLOCK instead of WARN. Use for exports and data packs that leave the team. |
| `ppp_base="2017"` | `ppp_mixing` | The output is pinned to this PPP base; any other base BLOCKs. |
| `min_n` | `small_n` | Minimum members with data; overrides the `min_n` the method recorded (default 3). |
| `peer_sets` / `peer_set` | `peers_not_frozen` | Peer sets used (`PeerSet` objects or dicts; one, a list, or a `{code: set}` mapping). |
| `results_at` | `peers_not_frozen` | ISO date the results were produced; a set frozen after it is flagged. |
| `periods` | `covid_endpoints` | Extra averaging windows, e.g. `{"2016-20": ("2016", "2020")}`. |
| `covid_years` | `covid_endpoints` | Years treated as COVID-19 endpoints (default `[2020, 2021]`). |
| `inherit_caveats` | engine | `False` leaves the inputs' own caveats out of the report (default `True`). |

Unknown keys are reported in an `info` caveat (`context_keys_unused`) so a
typo such as `replication_mode` does not silently disable a BLOCK.

## Rules

Rules run in the order below. Codes are the `Caveat.code` values a rule can
emit; the rule name is always in `evidence["rule"]`.

### 1. `ppp_mixing`

* **Checks:** `schema.ppp_base()` of every value in `unit` and in any
  `*_unit` / `unit_*` column (e.g. `level_unit`, `x_unit`) across all inputs.
* **BLOCK** `ppp_mixing`: more than one distinct base (for example 2017 and
  2021, or a stated base next to a PPP unit with no year, which parses as
  `"unknown"`, matching `harmonize.check_ppp`).
* **BLOCK** `ppp_base_mismatch`: `context["ppp_base"]` is set and an input
  uses another base (Kenya Fig 22 is pinned to 2017 PPP).
* **WARN** `ppp_base_unstated`: the only PPP units state no base year.
* **Footnote:** none (the output must be fixed, not footnoted).
* **Limits:** reads unit labels only; connectors must write the base into the
  unit (see CONVENTIONS). A growth rate computed from a PPP level no longer
  carries the base in its unit.

### 2. `ilo_modelled`

* **Checks:** observations with `obs_status == "modelled"` and a value.
  ILO origin = `source` `ilostat`/`ilo`, a dataset starting with `ILO`
  (`ILOEST`, `ILOSTAT`), or "ILO" in the indicator label (WDI "(modeled ILO
  estimate)").
* **BLOCK** `ilo_modelled_splice`: one series holds both modelled and
  non-modelled observations. A series is `indicator` + `iso3` plus any of
  `sex, age, classif1, classif2, sector, component, stat, group, scenario,
  window` that exist, across all inputs. Non-modelled means `obs_status` in
  `"", provisional, break, imputed`; `forecast` and `estimate` are neutral
  (ILO projections and provider-flagged estimates sit inside modelled series).
  Missing values are ignored. This catches `harmonize.splice` output in both
  directions (survey onto modelled and modelled onto survey).
* **WARN** `ilo_modelled`: ILO modelled estimates are present (evidence:
  indicators, countries, vintages).
* **INFO** `modelled_estimates`: modelled values from non-ILO sources.
* **Footnote:** `"Values are ILO modelled estimates (<citation>, <vintage>)."`
  when every observation is modelled, otherwise `"Values for <label> are ILO
  modelled estimates (...)."` (or `"Values for N series ..."` beyond two).

### 3. `forecast_years`

* **Checks:** observations with `obs_status == "forecast"`.
* **WARN** `forecast_years` (evidence: periods, rows, indicators, sources).
* **Footnote:** `"Values for YYYY onward are forecasts (<citation>, <vintage>)."`,
  one per source/vintage, where YYYY is the first forecast year. If the first
  forecast year differs across series, the footnote adds `"; the first
  forecast year varies by series (YYYY-YYYY)"`. Without a vintage it says
  `"vintage not recorded"`. Forecast rows whose period is a window label
  (`"2024-2026"`) give `"Averages for 2024-2026 include forecast years (...)."`
* Citation and vintage come from the provenance entries of the row's
  `source`/`dataset` in the same input (all data-source entries for derived rows).

### 4. `vintage_drift`

* **Checks:** data-source provenance entries (not `cgjrkit` steps) grouped by
  `(source, dataset)`.
* **WARN** `vintage_drift`: one group has several non-empty vintages (e.g.
  WEO 2025-10 and WEO 2026-04, or two ILOEST editions).
* **INFO** `vintage_unstated`: entries without a vintage next to a stated one.
* **Footnote:** `"<citation> data combine vintages <v1> and <v2>."`
* **Limits:** different dataset names are different datasets (`PWT10.01` vs
  `PWT11.0`; the PPP rule catches that pair).

### 5. `missing_provenance`

* **BLOCK** `missing_provenance`: an input has no provenance; has entries that
  are not `Provenance` records; its source line cannot be built (no citation,
  dataset or source in any entry; `Result.source_line()` alone would return
  `"Source: ."`); or it lists only `cgjrkit` processing steps and no data source.
* **WARN** `provenance_incomplete`: a `source` in the data has no provenance entry.
* **INFO** `provenance_unpinned`: data-source entries without `payload_sha256`.
* **Footnote:** none.

### 6. `series_break`

* **Checks:** observations with `obs_status == "break"` (connectors' break
  flags and the first new-series period of `harmonize.splice`).
* **WARN** `series_break` (evidence: indicator, country, period).
* **Footnote:** `"Series break in <period>: <indicator label> (<countries>)."`,
  at most four, then `"Series breaks in N further indicator-periods."`

### 7. `covid_endpoints`

* **Checks:** averaging windows that start or end in 2020 or 2021 (any year in
  the first or last period; `FY2019/20` touches 2020). Windows come from
  `Result.meta["periods"]` / `meta["windows"]`, the `periods` parameter that
  cgjrkit methods record in their provenance (`period_average`, `cagr`,
  growth accounting, structural change, jobs Shapley, standard tables),
  `context["periods"]`, and period labels shaped like windows (`"2016-2020"`,
  `"2016-21"`, `"2019Q1-2021Q4"`).
* Accepted shapes: `{label: (start, end)}`, `{label: "2011-2019"}`, lists of
  `(start, end)` pairs, lists of window labels. A bare list of periods is read
  as labels, never as a pair, because charts store plotted periods that way.
  `"YYYY-MM"` with MM 01-12 is a month, not a window.
* **WARN** `covid_endpoints` (evidence: windows with origin).
* **Footnote:** `"Averages for <labels> start or end in 2020-2021 (COVID-19 years)."`

### 8. `peers_not_frozen`

* **Checks:** peer sets from `context["peer_sets"]`/`["peer_set"]`,
  `Result.meta["peer_sets"]` (written by `peers.peer_aggregate`), the peer sets
  recorded in cgjrkit provenance params (`peerset`, `peer_sets`), and inherited
  `peer_set_not_frozen` caveats. Records of one set merge conservatively: any
  record without `frozen_at` makes the set unfrozen; otherwise the latest date wins.
* **BLOCK** (replication mode) / **WARN** (otherwise) `peers_not_frozen`: a set
  has no `frozen_at`.
* **BLOCK** / **WARN** `peers_frozen_late`: `context["results_at"]` is set and a
  set was frozen after it.
* **Footnote:** none.
* **Limits:** income-group aggregates built from a classification table have
  no peer set and are not checked.

### 9. `small_n`

* **Checks:** an `n` column in the data (members with data, as written by
  `peers.peer_aggregate`; also the ranking size of `benchmark.percentile_rank`),
  `n` in `Result.meta["cells"]` (standard tables) and in
  `meta["peer_aggregates"]` entries. `min_n` = `context["min_n"]`, else
  `meta["min_n"]`, else the latest `min_n` in provenance params, else 3.
* **WARN** `small_n` (evidence: aggregate, indicator, period, n, min_n; one
  entry per aggregate, not per statistic).
* **Footnote:** `"Some peer aggregates are based on fewer than <min_n> countries with data (lowest n = <n>)."`

### 10. `licence_restricted`

* **Checks:** every data source in provenance and in the data `source` column
  against `cgjrkit/registries/licences.yaml` (imported lazily; the rule works
  without it), plus licence text recorded in `Provenance.license`.
* Registry entries: `restricted` = `commercial_use` `no`/`restricted` or
  suggested caveat `block`.
  * Default context: **WARN** `licence_restricted` when restricted or the
    registry suggests `warn`/`block`; **INFO** when it suggests `info`;
    nothing for `none`.
  * `redistribution=True`: **BLOCK** when restricted (Comtrade, WITS, FRED,
    WB Documents API, IMF publications); **WARN** when commercial use is
    `unknown` or the registry suggests `warn` (IMF data, Atlas, UNCTAD, ...);
    **INFO** for `info`.
* **WARN** `licence_unregistered`: a source not in the registry.
* **WARN** / **BLOCK** (redistribution) `licence_noncommercial`: a provenance
  licence that reads non-commercial (`NC`, `BY-NC`, "non-commercial").
* **INFO** `licence_registry_unavailable`: the registry could not be loaded.
* **Footnote:** none.

## Engine behaviour

* Inherited caveats count toward the status: a connector's or method's `block`
  caveat (for example `harmonize` with `on_ppp_mix="caveat"`) BLOCKs the report.
* A rule that raises becomes a `warn` `rule_error` caveat and an entry in
  `errors`; the other rules still run.
* Output is deterministic: rules run in registry order, evidence lists are
  sorted and capped at 25 items (counts are always given).

## Adding a rule

```python
from cgjrkit.qa.caveat_check import RuleOutput, register_rule

@register_rule("my_rule", "WARN when ...", ("warn",))
def my_rule(inp):            # inp: CheckInput (results, context, frames, data, provenance(), cites_for())
    out = RuleOutput()
    if condition:
        out.add("my_rule", "my_code", "warn", "Short factual message.", {"evidence": 1},
                footnote="Chart-ready sentence.")
    return out
```

Each rule needs a seeded test in `tests/test_caveat_check.py` that must WARN
or BLOCK on a planted defect, and a clean counterpart that must not fire.

## Not covered yet (plan section 3.4)

Rules listed in the plan but not implemented here: undeclared definitions;
non-Bank forecast in a Bank chart; and the version comparability pack (survey
LPI vs LPI 2.0, PIP $2.15 vs $3.00 lines, PMR before/after 2018, B-READY vs
Doing Business, Atlas site vs Dataverse, old vs new WB STRI, Findex v01 vs
v02, WGI old estimate vs 0-100 score). The PIP pair is already caught by
`ppp_mixing` when both series are in one output, because PIP units state the
PPP base.
