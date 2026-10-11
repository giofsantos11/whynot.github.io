# CGJR replication inventory

What it takes to replicate the charts in World Bank Country Growth and Jobs Reports (CGJRs) with a suite of deterministic tools: data connectors, analysis methods and chart generators.

Built from five reports in [`../CGJRs`](../CGJRs):

- Indonesia 2026
- Morocco 2026
- North Macedonia 2026
- Philippines 2025
- Kenya 2026, a background report that informs Kenya's CGJR

Each figure and table was extracted by an AI agent from the PDF text and page images. The counts are reproducible from `figures-5-reports.json`. The replicability ratings are the extractor's judgment and have **not been audited**.

## Files

| File | What it is |
|---|---|
| `cross-report-inventory.md` | Main inventory: overview, common report structure, 69 chart archetypes, indicator dictionary, data sources, methods, tool suite, coverage, first replication targets |
| `cross-report-review.md` | Independent review of the inventory: corrections, overclaims, missing archetypes, tool gaps |
| `figures-5-reports.json` | Every extracted item (536) with title, module, chart type, indicators, sources, method, data level, replicability |
| `cross-report-aggregates.json` | Counts by report, module, data level, source, method and chart type |
| `kenya-replication-inventory.md`, `kenya-figures.json` | Detailed Kenya pass with headline values to use as replication targets, plus a bibliography profile |
| `first-pass-inventory.md` | Inventory built from public information before the PDFs were available, with module IDs, the caveat list and the validation plan |

## Headline findings

- **536 items:** 479 charts, 49 tables, 7 diagrams and 1 map. About 18 are repeats of another figure in the same report.
- **Replicable from public data (unaudited):** 27.6% fully, and 60.4% fully or partly.
- **What blocks the rest:**
  - firm microdata: 146 items, 104 of them not replicable;
  - model outputs (MFMod, CGE, LTGM, CCDR): 60 items, 45 not replicable;
  - labor-survey microdata: 49 items.
- **Shared structure.** All 4 full CGJRs follow the same sequence: Overview → macro features → micro (firm productivity) → thematic chapters → policy roadmap with simulated reform impacts → standardized macro and employment tables.
- **Most common archetypes (4–5 of 5 reports):**
  - policy matrix
  - growth accounting
  - reform-simulation results
  - concentration and markups
  - sector productivity vs employment
  - skills
  - long-term growth scenarios
  - firm TFP
  - Enterprise Survey obstacles
  - export sophistication
  - productivity decomposition
  - trade openness
  - standardized macro tables
  - GDP growth vs peers
  - standardized employment table

## MVP tool suite (revised after the review)

| Layer | Tools |
|---|---|
| Connectors | `wb_data` (WDI, WGI, GFDD), `imf_data` (check the 2025 API changes), `ilo_data`, `pwt_data`, `reer_data`, `pop_data`, `trade_data`, `policy_index_data`, `es_aggregates`, `entrepreneurship_db`, `pip_poverty`, `national_table_ingest` |
| Registries and transforms | `peer_registry` + `peer_set`, `harmonize`, `vintage_pin`, `source_resolver` |
| Methods | `growth_accounting`, `structural_change`, `benchmark_fit` |
| Rendering | `chart_series`, `chart_decomposition`, `chart_benchmark`, `standard_tables`, `model_output_ingest` |
| QA and replication | `caveat_check`, `figure_digitizer`, `replicate_figure` |

Next: `lfs_kit` (labor-survey microdata), `firm_kit` (firm census, run on client data, aggregates only), `io_kit`, `regional_kit`, `ltgm`, `gravity`, `chart_coef`.

**Coverage estimates:**
- The original 20-tool MVP covers 113 of the 148 fully public items (76%).
- With the review's additions, it covers about 130 of 148 (~88%). That is about a quarter of all items.
- Most of the rest needs client-provided microdata or model outputs.

## First replication targets

1. Kenya Fig 1 (p.10)
2. North Macedonia Fig 1 (p.20)
3. Indonesia Fig 1.1 (p.23)
4. Philippines Fig 1 (p.16)
5. Kenya Fig 4 (p.15)
6. North Macedonia Fig 4 (p.21)
7. Kenya Fig 2 (p.14)
8. Kenya Fig 34 (p.33)
9. Kenya Fig 35 (p.33)
10. Indonesia Fig 2.1 (p.37)
11. Kenya Fig 14 (p.19)
12. Kenya Fig 22 (p.25)
13. North Macedonia Fig 37 (p.36)
14. North Macedonia Figs 23a–b (p.28)
15. Morocco Fig 4-1a (p.65)

Page numbers are PDF pages. Details are in `cross-report-inventory.md`, section 9.
