# CGJR Analysis Services: Overall Plan (tools, data access, roadmap)

**For:** Gio and Olzhas · **Date:** 11 Oct 2026 · **Horizon:** 12 weeks (12 Oct 2026 to 1 Jan 2027), with the product phase after that

**Labels.** **V** = verified on an official or primary page (linked). **I** = our inference. **U** = unknown, check before relying on it. Most official data pages could not be opened from the dev environment because of network blocks. Many V items therefore rest on official search snippets, GitHub or PyPI.

---

## 1. Objectives and principles

**Objectives (next 12 weeks)**

| # | Objective | Measure |
|---|---|---|
| O1 | Sell fixed-price analysis packs to World Bank CGJR teams | 1 paid pilot signed by week 10 and delivered by week 12; pipeline of $50k or more |
| O2 | Build a deterministic engine that replicates public-data CGJR charts with provenance | 15 first targets by week 6; 40–50 of the 148 fully public items by week 12 |
| O3 | Keep the path open to the in-Bank product | Tool contracts frozen as plain functions; an MCP wrapper starts only after the week-12 gate |

**Principles (already decided).**
1. The AI chooses and explains; deterministic tools compute.
2. Every number carries provenance: dataset, vintage, parameters, code hash and `result_id`.
3. Replication reads only pinned vintages.
4. Caveat checks block invalid output: ILO modelled data spliced with survey data, PPP 2017 mixed with 2021, series breaks, undeclared definitions, peers not frozen in advance.
5. Microdata rows never reach the model. Only aggregates leave, with small cells suppressed.
6. Draft-then-verify: every number in a draft binds to a computed result.
7. Custom code written by the AI runs in a sandbox and is labelled "custom".
8. Firm microdata runs in client-provided-data mode. Model outputs are ingested, not re-estimated.
9. **New from the review: deterministic-only mode.** Bank drafts and team tables are probably Official Use Only (OUO). They get no AI processing unless the contract allows it.

---

## 2. Architecture at a glance

```
 PUBLIC SOURCES (allowlisted hosts)          TEAM-SUPPLIED (per engagement, never committed)
 WDI/WGI · IMF SDMX · ILOSTAT · PWT ·        national tables · MPO/MFMod/CGE outputs ·
 WPP · OECD · BACI · ES · PIP · OKR ...      drafts · microdata (enclave: code only)
            │                                            │
 L1 CONNECTORS  httpx + retry + per-host rate limiter + host allowlist
            │  raw bytes, sha256, URL, retrieved_at
 L2 STORE       raw/ (content-addressed) → Parquet (long schema) → DuckDB · manifest.lock
            │
 L3 REGISTRIES  peers · countries (FY, rebasing, PPP) · sources · concordances · licences
    TRANSFORMS  harmonize (rebasing, CAGR, FY↔CY, splice with break flags)
            │
 L4 METHODS     pure functions → Result{table, params, provenance, result_id}
            │
 L5 QA          caveat_check  →  BLOCK / WARN / PASS   (BLOCKed results cannot render or export)
            │
 L6 RENDER      Vega-Lite + SVG/PNG + plotted-data CSV + auto source note · standard tables
            │
 L7 REPLICATE   FigureSpec YAML → L2–L6 → diff vs targets (tolerances.yaml) → grade R0–R3
            │
 L8 DELIVER     static HTML index · XLSX · do-files/Python · caveat memo · results ledger
            │                         · check_draft · prior-findings ledger
 L9 (LATER)     MCP server, stdio, fixed read-only tools, signed releases, no telemetry
            ⇅
   AI CLIENT ONLY: Claude Code (our services) → Bank's approved assistant via its gateway (product)
   reads questions · proposes archetypes/peers/periods · calls tools · drafts text · explains
```

| Step | AI role | Deterministic role |
|---|---|---|
| Scoping | Maps the team's questions to archetypes, peers and periods | `peer_registry` freezes peers before any results exist |
| Computing | None | Connectors, methods, caveats |
| Drafting | Drafts text and candidate messages, labelled "AI-drafted" | `check_draft` binds every number; unbound numbers fail |
| One-off analysis | Writes custom code (services work only) | Sandbox; output labelled custom |
| Bank drafts, team tables | **None by default** | Matcher, linter and caveats run without AI |

**MCP facts for the later server.**
- Sampling is deprecated in the 2026-07-28 revision, so the server makes no AI calls (V, [SDK](https://py.sdk.modelcontextprotocol.io/deprecated/)).
- In `mcp` 2.3.0 the server class is `MCPServer` (V, [PyPI](https://pypi.org/project/mcp/)).
- The Bank's own `data360-mcp` is a precedent for IT approval, but it runs over HTTP/SSE only (V, [GitHub](https://github.com/worldbank/data360-mcp)). We treat it as reference code, not a dependency.

---

## 3. Tool catalog (single source of truth)

**Phases.** **MVP** = shipped by week 12. **Next** = months 4–6. **Later** = after the product is validated.

**Licences.** **Open** = the MIT method library, `gjm` (placeholder name). **Prop.** = the proprietary engine, `gje` (placeholder name).

Archetype IDs come from `cross-report-inventory.md` §3. T1–T15 are the revised first-15 targets in §7.

### 3.1 L1 Connectors and ingest

| Tool | Purpose | Inputs → outputs | Phase (sprint) | Lic. | Archetypes / targets |
|---|---|---|---|---|---|
| `wb_data` | WDI, WGI, GFDD, LPI, income-class history; **source 57** for past WDI vintages | iso3s, series, years, vintage → `Observation` panel + manifest entry | MVP (S1) | Open | G01–G03, G07, F05, X04–X05, S01, R01, G14, G16 · T1–T4, T10, T12–T14 |
| `imf_data` | WEO vintage dataflows; the thematic flows that replaced IFS (BOP, reserves); ICSD | dataflow, key, vintage → panel | MVP (S4) | Open | F01, F02, X01, G06, G07 · Kenya Figs 7, 9, 10, 12, 13 |
| `ilo_data` | ILOSTAT bulk CSV; `obs_flag` = modelled or survey | indicator, iso3s, edition snapshot → panel | MVP (S3) | Open | S02, S03, S05, L01, G16 · T6, T8, T9, T15 |
| `pwt_data` | PWT **10.01 and 11.0**, both DOI-pinned | version → panel | MVP (S2) | Open | G04, G06 · T5 |
| `reer_data` | Bruegel REER (hashed snapshot) | file → panel | MVP (S4) | Open | X02 · Kenya Figs 11, 20, 28 |
| `pop_data` | UN WPP 2024 bulk CSV (token API optional) | edition → panel | MVP (S4) | Open | L08 |
| `trade_data` | UNCTAD FDI, BACI HS6 (HHI, RCA), WITS/TRAINS tariffs, Atlas (cross-check only) | → panel | MVP (S3–S4) | Open | X04, X06, X08 · T13, Kenya Fig 26 |
| `policy_index_data` | OECD SDMX (PMR, STRI, FDIRI), OECD-WBG PMR, WB-WTO STRI, B-READY, BTI | → panel | MVP (S3) | Open | R01, R03, X09, N01 · T11 |
| `es_aggregates` | Enterprise Surveys indicators (Data360 `WB_ES`), with SE and n | → panel | MVP (S4) | Open | B13 (+B01, F05) |
| `entrepreneurship_db` | New firm density, via WDI `IC.BUS.NDNS.ZS` and `IC.BUS.NREG` | → panel | MVP (S4) | Open | B07 (routed here, not to `firm_kit`) |
| `pip_poverty` | PIP REST; `ppp_version` and `release_version` are required parameters | → panel | MVP (S4) | Open | P01 · Kenya Fig 29 |
| `national_table_ingest` | NSO, ministry and central-bank tables (CSV/XLSX/PDF); a person checks every PDF extraction | file + schema → validated panel | MVP (S3) | Open | G08, F01, X01, S04, SP1 · T7, Kenya Figs 18–19 |
| `model_output_ingest` | Team-supplied MPO/MFMod/MANAGE/CGE/LTGM/CCDR outputs; **official forecast years** | file → scenario × variable × year × deviation type | MVP (S5) | Open | G11–G13, C01, G14 projection columns |
| `comtrade` | UN Comtrade, only after the licence answer | → panel | Next | Open | X06 (alternative to BACI) |
| `es_microdata` | Registered WBES files; weighted tables with SE; 5-firm rule | → aggregates only | Next (spike S6) | Open | B13, B01, B11, B02 (24 items) |
| `findex`, TED loader | Findex 2025 aggregates; Conference Board TED (terms U) | → panel | Next | Open | Financial inclusion; G04 (North Macedonia Fig 4) |

### 3.2 L2–L3 Store, provenance, registries and transforms

| Tool | Purpose | Inputs → outputs | Phase | Lic. | Serves |
|---|---|---|---|---|---|
| Connector base | Retry; **per-host rate limiter** (cautious defaults where the limit is U); host allowlist check; User-Agent | request → raw bytes + metadata | MVP (S1) | Open | All connectors |
| `vintage_pin` + store | Content-addressed raw store, `manifest.lock`, `resolve(dataset, as_of)`; refuses unpinned reads in replication mode | raw → Parquet + DuckDB catalog | MVP (S1) | Open | All |
| Provenance / results ledger | Pydantic `Provenance`; `result_id` = hash of dataset, vintage, sha256, tool version, parameters and git commit | Result → ledger row | MVP (S1) | Open | `check_draft`, exports |
| `peer_registry` + `peer_set` | YAML per report: peers, selection criteria, approver, date frozen, one alternative set. Computes group mean/median/IQR and income group by year | registry → aggregates | MVP (S1) | Registry data Prop.; function Open | All benchmark archetypes |
| `country_registry` | Fiscal year (Kenya: Jul–Jun), national-accounts rebasing dates, PPP base, NSO hosts | YAML → lookups for `harmonize` and caveats | MVP (S2) | Open | All |
| `harmonize` | Rebasing, period averages, CAGR, % of frontier, deflation, FY↔CY, PPP tag, splicing with break flags | panel → panel | MVP (S1 basic, S3 full) | Open | All |
| Concordances | ISIC → report sectors; HS revisions | YAML | MVP (S3) | Open | S02–S05, X06 |
| `source_resolver` | Maps "WB staff calculations" and bare "Other" source lines (37.6% of source tags) to dataset + series + vintage. **The MVP is a hand-curated YAML per replicated figure**; the knowledge base comes later | source line → candidates | MVP-lite (S1), KB Next | Prop. | All targets |
| `licence_register` | Licence and third-party flag per source and series, including the third-party flags in WDI metadata | registry → caveat input | MVP (S2) | Open | "Licensed data" caveat |

### 3.3 L4 Methods (all Open)

| Tool | Method | Inputs → outputs | Phase | Archetypes / targets |
|---|---|---|---|---|
| `growth_accounting` | Cobb-Douglas with α as a parameter (shown as a band); PWT `hc`; perpetual inventory; potential output | PWT/national panel → contributions | MVP (S2) | G04, G06 · T5 |
| `benchmark_fit` | Income-conditional fits (linear, quadratic, lowess); residuals; percentile ranks; KDE; z-scores; distance to frontier | panel → fit + ranks | MVP (S2) | G03, S01, F05, X05, R01, N01 · T4, T10, T12 |
| `structural_change` | VA per worker by sector; McMillan-Rodrik shift-share (exact identity check); sector contributions; β-convergence | sector panel → decomposition | MVP (S3) | G08, S02–S05, SP1 · T7–T9 |
| `lfs_tab` (minimal) | Client-mode LFS tabulator: LFPR, sector shares, wage employment, informality under a **declared** definition (13th vs 19th ICLS); do-file export | team microdata → aggregates (n ≥ threshold) | MVP-lite (S5) | L01, L03, G16 |
| `firm_kit` | TFP (OLS/FE, LP, ACF), Olley-Pakes, Melitz-Polanec, misallocation, CR4/HHI/markups, DHS job flows; disclosure log | client data in enclave → aggregates | Next (spike S6) | B02–B06, B08, X12, SP2 |
| `jobs_shapley` | Shapley split of GDP-per-capita growth (none of the 5 reports has one) | panel → contributions | Next | New; close to S05, L02 |
| Other Next tools | Full `lfs_kit` (NEET, mismatch, Mincer, transitions); `ltgm` port (licence U); `gravity` (PPML on CEPII + BACI); `io_kit`; `tax_wedge`; `regional_kit`; `policy_matrix` | — | Next | L01–L07, L13, G11, X05, X09, S06, X08, F03, SP1, SP3, Z02 |
| Later | Climate exposure, AI exposure, FAT/GBF index, distortion model | — | Later | C02, L10, B10, S07 |

### 3.4 L5–L7 QA, rendering and replication

| Tool | Purpose | Inputs → outputs | Phase | Lic. | Serves |
|---|---|---|---|---|---|
| `caveat_check` engine | Rule engine → BLOCK/WARN/PASS + footnote text | Result + registries → `CaveatReport` | MVP (S2) | Open | All |
| Caveat packs | One rule per hazard (list below the table) | rules | MVP (S2 onward) | Prop. | All; each rule has a seeded test |
| `chart_series` | Line/bar vs peers, IQR band, shaded episodes, dual axis, index baseline | Result → Vega-Lite + SVG/PNG + CSV + source note | MVP (S1) | Open | G01, G02, G06, L01, X01–X04 · T1–T3, T6, T13 |
| `chart_benchmark` | Scatter, bubble, connected trajectory, highlight + fit, slope/rank, KDE | same | MVP (S2) | Open | G03, S01, S04, F05, X05, X06 · T4, T10, T12 |
| `chart_decomposition` | ± stacked bar with total marker or line | same | MVP (S3) | Open | G04, G08, S05, X01, F01 · T7, T9 |
| `standard_tables` | G16 employment table (S3); G14 macro table (S4 historical, S5 projections) | Results → XLSX + HTML | MVP (S3–S5) | Open | G14, G16 · T15, Indonesia Table A.2 |
| House-style fallback | matplotlib look-alikes for Stata/Excel charts | — | MVP (as needed) | Open | Kenya Stata/Excel-style figures |
| `chart_coef` | Coefficient, event-study and IRF plots | — | Next | Open | G10, C02, X11, B12 |
| `figure_inventory` | PDF → figure log (title, source note, page, **dedupe flag**) | PDF → CSV | MVP (S1) | Prop. | All 5 reports |
| `figure_digitizer` | **MVP: printed labels and values quoted in the text first, then manual axis calibration.** Automatic vector-path reading comes later | PDF page → target CSV with tolerance + method | MVP-lite (S1), auto Later | Prop. | All targets |
| `replicate_figure` | FigureSpec YAML → pinned data → method → chart → diff → grade R0–R3 | spec → report | MVP (S1) | Prop. | All targets |
| `tolerances.yaml` + grader | One tolerance table, used by both the golden tests and the grade (§8) | — | MVP (S1) | Prop. | All targets |

**Caveat-pack rules.**
- **Mixing and splicing:** PPP 2017 with 2021; ILO modelled with survey data; series breaks; vintage drift.
- **Undeclared choices:** undeclared definitions; peer set not frozen.
- **Time and source flags:** 2020–21 endpoints; forecast years; **non-Bank forecast in a Bank chart**.
- **Data handling:** licensed data; small cells.
- **Series that are not comparable across versions:**
  - survey LPI vs LPI 2.0;
  - PIP $2.15 line vs $3.00 line;
  - PMR before vs after 2018;
  - B-READY vs Doing Business;
  - Atlas site data vs the Dataverse files;
  - old vs new WB STRI;
  - Findex v01 vs v02;
  - WGI old estimate vs the 2025 0–100 score.

### 3.5 L8 Delivery, literature and (later) server

| Tool | Purpose | Inputs → outputs | Phase | Lic. | Serves |
|---|---|---|---|---|---|
| `export` | Standalone **Stata do-files** and Python scripts per chart. They run from bundled pinned CSVs, can refresh from APIs with the client's keys, and never import `gje` | Result → .do/.py + CSV | MVP (**S3**) | Generator Prop.; output belongs to the client | All deliverables |
| `check_draft` | Extracts numeric claims from a docx/md draft; a deterministic matcher binds them to the results and prior-findings ledgers; title/axis/period linter | draft → binding report | MVP (S3 v0, S5 full) | Prop. | Pre-Review Check; Kenya defects |
| `prior_findings` ledger | Claims from earlier CGJR, CEM, Jobs Diagnostic and SCD reports, each with verbatim quote, number, unit, page and licence; "still holds / changed" queries | IDs → findings table | MVP (S4 v0) | Prop. | First-Look day 3; candidate messages |
| Literature resolvers + PDF extraction | OKR (normal links), OpenAlex, Crossref; D&R API after legal sign-off. `pdfplumber` for captions and "Source:" lines; Docling/GROBID later (weights must be bundled) | query/PDF → resolved IDs, text + bounding boxes | MVP (S4) | Open | Ledger; `source_resolver` |
| Workbench | **MVP: static HTML index**, one card per chart (chart, data, provenance, caveats, code); full offline workbench later | Results → zip | MVP-lite (S4), full Next | Prop. | All packs |
| Sandbox | Runs AI-written custom code: no network, read-only data, resource limits | code → labelled Result | MVP (S5) | Prop. | Custom one-off analyses |
| MCP server | Stdio wrapper over the same functions (13 tools, listed below the table). No shell, telemetry or runtime downloads | — | **Later (week-12 gate)** | Prop. | Product |

**MCP tools:**
- Data: `list_datasets`, `get_series` (aggregates only), `describe_provenance`.
- Methods: `peer_aggregate`, `growth_accounting`, `shift_share`, `benchmark_fit`.
- Output and QA: `render_chart`, `run_caveat_check`, `replicate_figure`, `check_draft`, `query_prior_findings`, `export_code`.

**Phase conflicts between the earlier drafts, resolved here:**
- PIP: MVP.
- Comtrade: Next (BACI covers HHI in the MVP).
- MCP server: Later.
- Docling and GROBID: Later.
- Signing and SBOM: at the product gate.
- Stata export: S3.

---

## 4. Public data and API access

### 4.1 Macro, labor and population

| Source | Domain(s) | Auth / key | Limits | Licence and commercial use | Python | Priority | Notes |
|---|---|---|---|---|---|---|---|
| [WB Indicators API v2](https://datahelpdesk.worldbank.org/knowledgebase/articles/1886674-new-features-and-enhancements-in-the-v2-api) (WDI, WGI, GFDD) | api.worldbank.org/v2 | None (V) | U | [CC BY 4.0](https://www.worldbank.org/en/about/legal/terms-of-use-for-datasets) (V); third-party indicators are flagged in metadata (V); commercial use with attribution (I) | `wbgapi` 1.0.14, MIT, no cache (V, [PyPI](https://pypi.org/project/wbgapi/)) | P1 | [Source 57 = WDI archives](https://datacatalog.worldbank.org/search/dataset/0038555/wdi-database-archives) (V). Series codes are reused across base years (V). [WGI 2025 rescale](https://www.worldbank.org/content/dam/sites/govindicators/doc/The%20Worldwide%20Governance%20Indicators%202025%20Methodology%20Revision.pdf) (V). GFDD not updated since 2022 (V) |
| Data360 API | data360api.worldbank.org (I) | None (V) | U | WB terms (I) | Direct REST | P2 | No vintage endpoint found (U). Hosts `WB_ES` and `WB_LPI_20` |
| [IMF SDMX](https://data.imf.org/en/Resource-Pages/IMF-API) (WEO, BOP, ex-IFS) | api.imf.org, data.imf.org | Public flows: none (I) | New API: U | [Terms](https://www.imf.org/en/About/copyright-and-terms): systematic downloading needs **written permission** (V). Commercial use: **U, ask copyright@imf.org** | `imfp` 2.1.0, on the new API (V, [PyPI](https://pypi.org/project/imfp/)); `sdmx1` has bug [#241](https://github.com/khaeru/sdmx/issues/241) (V) | P1 (S4) | Each WEO vintage is its own dataflow ([guide](https://data.imf.org/-/media/iData/External-Storage/Documents/2C2B2F3E671A4B11ABE756E2441FD6F0/en/WEO-Database-Transition-Guide.pdf), V). IFS is retired with no official crosswalk (I), so we maintain one |
| ILOSTAT | [ilostat.ilo.org/data/bulk](https://ilostat.ilo.org/data/bulk/); [sdmx.ilo.org/rest](https://webapps.ilo.org/ilostat-files/Documents/SDMX_User_Guide.pdf) | None (I) | SDMX: 300k records per query (I) | [CC BY 4.0 since 2023](https://www.ilo.org/node/9321) (V), but the Rilostat README says non-commercial: **confirm** | None; we write a thin bulk reader | P1 (S3) | No archive of past editions (U), so we snapshot them. Kenya pins the Nov 2023 vintage |
| [PWT](https://www.rug.nl/ggdc/blog/penn-world-table-version-11-0-is-published) 11.0 / 10.01 | www.rug.nl, dataverse.nl | None | File | CC BY 4.0 (V) | pandas | P1 (S2) | 11.0 uses 2021 PPPs (V). DOIs 10.34894/FABVLR (11.0) and 10.34894/QT5BCC (10.01) (V) |
| [UN WPP](https://population.un.org/dataportalapi/index.html) | population.un.org | API needs a [token](https://population.un.org/dataportalapi/token/index.html), sent by email (V); bulk CSV needs none | 100 rows per page (V) | Publications under CC BY 3.0 IGO (V); data terms U | httpx | P2 (S4) | [WPP 2024](https://www.un.org/development/desa/pd/world-population-prospects-2024) is the latest edition found (V); no 2026 edition found (U). Pin the WPP2024 CSV |
| UNIDO INDSTAT | stat.unido.org | Web portal; API U | U | [CC BY 4.0](https://unido.org/node/250) (V) | None | P3 | Sector-level substitute for firm charts |
| [GGDC/WIDER ETD](https://www.rug.nl/ggdc/structuralchange/etd/) | www.rug.nl, dataverse.nl | None | File | CC BY 4.0 (V) | pandas | P3 | Data to 2018 (V); country coverage U |
| [Bruegel REER](https://www.bruegel.org/publications/datasets/real-effective-exchange-rates-for-178-countries-a-new-database) | www.bruegel.org | None | File | Free with attribution (V, general data policy) | pandas | P2 (S4) | Overwritten in place (I), so we snapshot it |
| Conference Board TED | U | Registration (I) | U | U | — | P3 | Needed only for North Macedonia Fig 4 |
| FRED | api.stlouisfed.org | Key | U | [Non-commercial](https://fred.stlouisfed.org/docs/api/terms_of_use.html) (V) | — | **Excluded** | Use the primary sources |

### 4.2 Trade, policy indices, firms and poverty

| Source | Domain(s) | Auth / key | Limits | Licence and commercial use | Python | Priority | Notes |
|---|---|---|---|---|---|---|---|
| UN Comtrade | comtradeapi.un.org | Free key (V) | 500 calls a day, 100k records per call (I, [comtradr](https://docs.ropensci.org/comtradr/articles/large_data.html)) | Internal use only; re-dissemination needs permission (V, [UN shop](https://shop.un.org/de/node/55)). Derived analysis in paid work: **U** | [`comtradeapicall`](https://github.com/uncomtrade/comtradeapicall) (V) | Next | Results are cut off at the record cap |
| [CEPII BACI](https://www.cepii.fr/DATA_DOWNLOAD/baci/doc/baci_webpage.html) | www.cepii.fr | None (I) | File | Etalab 2.0; commercial use allowed (V) | pandas | P1 (S4) | Version 202601 (V). Default source for HHI and RCA |
| [WITS/TRAINS](https://wits.worldbank.org/witsapiintro.aspx) | wits.worldbank.org | None (I) | U | Comtrade data reached through WITS is internal-use only (V); TRAINS terms U | `sdmx1` | P2 | MFN vs preferential rates |
| UNCTADstat | unctadstat.unctad.org | None for CSV (V) | U | U | None | P1 (S3) | Revised every year (I), so pin the file hash |
| Harvard Atlas | atlas.hks.harvard.edu | None (V) | ≤120 requests a minute (V, [llms.txt](https://atlas.hks.harvard.edu/llms.txt)) | Research and educational use (V); commercial use U | `ecomplexity` (MIT) | P2 | Cross-check only; we compute ECI ourselves |
| OECD SDMX (PMR, STRI, FDIRI, TiVA) | sdmx.oecd.org | None (I) | Two official figures conflict: [60 downloads/hour](https://www.oecd.org/en/data/insights/data-explainers/2024/11/Api-best-practices-and-recommendations.html) vs 20/hour on a mirror (V). **Plan for 20/hour.** VPN traffic is blocked (V) | CC BY 4.0 from Jul 2024 (V for publications, I for data) | `sdmx1` | P1 (S3) | One bulk query per peer panel |
| [OECD-WBG PMR](https://datacatalog.worldbank.org/search/dataset/0066434/oecd-wbg-pmr-indicators-1998-2024) | www.oecd.org | None (I) | U | U | — | P1 (S3) | Values before 2018 not comparable (I) |
| [WB-WTO STRI](https://data.wto.org/dataset/stpd) | i-tip.wto.org | None (I) | U | U | — | P2 | Cannot be spliced with the old WB STRI |
| [B-READY](https://www.worldbank.org/en/businessready/data) | www.worldbank.org | None | — | U | — | P2 | Not comparable with Doing Business |
| LPI | WDI `LP.LPI.*`; Data360 `WB_LPI_20` (V) | None | — | CC BY 4.0 (V) | `wbgapi` | P2 | [LPI 2.0](https://reproducibility.worldbank.org/catalog/514) is a series break (V) |
| [ES aggregates](https://www.enterprisesurveys.org/en/data) | enterprisesurveys.org, data360api | None (I) | U | CC BY 4.0 (V, search snippet) | REST | P1 (S4) | Standard errors and n included |
| ES microdata | login.enterprisesurveys.org | Registration, about 2 days (V, [protocol](https://www.enterprisesurveys.org/en/data-access-protocol)) | — | Use in paid work: **U** | pyreadstat | Next | No cells under 5 firms (V) |
| [Entrepreneurship DB](https://www.worldbank.org/en/programs/entrepreneurship) | api.worldbank.org | None | — | CC BY 4.0 (V) | `wbgapi` | P1 (S4) | Counts LLCs only (V) |
| PIP | api.worldbank.org/pip/v1 (V, [blog](https://blogs.worldbank.org/en/opendata/how-use-pips-api)) | None (I) | U | U (I: CC BY) | REST | P1 (S4) | $3.00 line on 2021 PPPs; 2017-PPP estimates still available (V) |
| [Findex 2025](https://microdata.worldbank.org/catalog/7860), [Microdata Library](https://microdata.worldbank.org/index.php/terms-of-use), [GLD](https://github.com/worldbank/gld) | microdata.worldbank.org, github.com | Access tiers (V) | — | Mostly research-only, no redistribution (V) | — | Next / client mode | GLD has no Kenya (V) |

### 4.3 Literature and the prior-findings ledger

| Source | Domain(s) | Auth | Limits | Licence and commercial use | Priority | Notes |
|---|---|---|---|---|---|---|
| [WB D&R API](https://documents.worldbank.org/en/publication/documents-reports/api) | search.worldbank.org/api/v3/wds | None (V) | U | [T&C](https://www.worldbank.org/ext/en/legal/terms-conditions): the APIs may not be used for commercial uses of non-dataset Materials (V) | P1 **after legal sign-off** | Use normal links until then |
| WB OKR | openknowledge.worldbank.org | None | U | [CC BY 3.0 IGO](https://blogs.worldbank.org/en/voices/Open-in-Action-at-the-World-Bank) (V); some items NC-ND | P1 | Holds 20 of the 43 Kenya references |
| IMF publications | www.imf.org | — | No API (I) | All Rights Reserved (V) | P2 | Metadata, page cites and short quotes only |
| OpenAlex | api.openalex.org | Free key (V) | $1/day free budget; 100 requests/s (V, [docs](https://developers.openalex.org/api-reference/authentication.md)) | [CC0](https://help.openalex.org/data/licenses/) (V) | P1 | Paid tier U |
| Crossref | api.crossref.org | `mailto` (V) | 10 requests/s for single DOIs, 3/s for lists (V, [blog](https://crossref.org/blog/announcing-changes-to-rest-api-rate-limits/)) | Metadata free (V) | P1 | DOI cross-check |
| Semantic Scholar | api.semanticscholar.org | Key | 100 requests per 5 min (V) | [Commercial licence needed](https://api.semanticscholar.org/license) (V) | **Excluded** | — |
| CORE v3 / arXiv | api.core.ac.uk / export.arxiv.org | None | [5 requests per 10 s](https://www.core.ac.uk/services/api) / [1 request per 3 s](https://info.arxiv.org/help/api/tou.html) (V) | CORE terms under review (U); arXiv metadata CC0 (V) | Next / Low | — |

All literature sources are called with httpx; no SDKs are needed.

**Ledger rules.**
- The AI may cite only IDs returned by a resolver: D&R ID, OKR handle `10986/...`, IMF report number, DOI, or OpenAlex `W...`.
- Quotes and numbers must match the stored text exactly.
- References that cannot be resolved get a flag instead of a failed lookup: "forthcoming", "unpublished" or "report-local key" (Kenya 2023a–g).
- Retrieved text is data, never instructions.

---

## 5. Accounts, keys and allowlist

### 5.1 Register and send now (week 1 unless noted)

- [ ] **Corporate Procurement ruling** on the former-staff vendor bar. Email corporateprocurement@worldbank.org with both founders' appointment end dates. The rule is in "Restrictions on Current and Former Bank Group Staff and Eligibility of Bank Group Vendors", linked from the [Potential Vendor Registry](https://www.worldbank.org/en/about/corporate-procurement/vendors/potential-vendor-registry) (V). The 12-month length is **not verified**.
- [ ] **Licence emails:**
  - copyright@imf.org: systematic downloading and text mining (TDM);
  - comtrade@un.org: derived analysis in paid work;
  - enterprisesurveys@worldbank.org: microdata in paid work;
  - Harvard Growth Lab: commercial use of the Atlas;
  - ILO: whether CC BY covers commercial use (contact address U);
  - WB legal: D&R API use by a vendor.
- [ ] **Data accounts:**
  - Enterprise Surveys microdata (about 2 days);
  - WB Microdata Library;
  - Comtrade free key (use it only after the licence answer);
  - WPP token (optional);
  - OpenAlex key;
  - TED (after reading its terms).
- [ ] **Procurement:** WBGeProcure supplier registration in weeks 3–4; it is needed to win an award (V, [WB vendors](https://www.worldbank.org/en/about/corporate-procurement/vendors)). ADB CMS and an IDB BP number in weeks 7–8.
- [ ] **Tooling:** Anthropic API organisation with spending caps (one project per engagement); private GitHub organisation; Stata licence (price U).

### 5.2 Domains to allowlist in the dev environment

Where to set it: cloud environment → Edit → Network access → Allowed domains. Keep "Allow package managers" on ([docs](https://code.claude.com/docs/en/cloud-environments#network-access)). Generate the list from `registries/hosts.yaml`, and add a CI test that fails when code calls a host not on it. The same file goes to Bank IT later.

**Tested and blocked (V):**
- api.worldbank.org, data360api.worldbank.org, wits.worldbank.org, search.worldbank.org, openknowledge.worldbank.org, microdata.worldbank.org, datacatalog.worldbank.org, pip.worldbank.org, lpi.worldbank.org, www.enterprisesurveys.org
- data.imf.org, www.imf.org, www.elibrary.imf.org
- ilostat.ilo.org
- population.un.org, comtradeapi.un.org
- dataverse.nl, sdmx.oecd.org, data-explorer.oecd.org, atlas.hks.harvard.edu, dataverse.harvard.edu, www.bruegel.org, www.cepii.fr, i-tip.wto.org, data.wto.org, unctadstat.unctad.org
- api.openalex.org, api.crossref.org, cran.r-project.org

**Needed, not yet tested:**
- World Bank: data.worldbank.org, databank.worldbank.org, datatopics.worldbank.org, datahelpdesk.worldbank.org, documents.worldbank.org, documents1.worldbank.org (U), thedocs.worldbank.org, www.worldbank.org, reproducibility.worldbank.org, login.enterprisesurveys.org, datacatalogfiles.worldbank.org (U), data360files.worldbank.org (U)
- IMF: api.imf.org
- ILO: sdmx.ilo.org, webapps.ilo.org, www.ilo.org
- Other data: www.rug.nl, stat.unido.org, www.oecd.org, comtradedeveloper.un.org, the UNCTAD API host (U), www.conference-board.org (U)
- Identifiers: doi.org, hdl.handle.net
- NSOs and ministries (exact hostnames U): www.knbs.or.ke, www.treasury.go.ke, www.centralbank.go.ke, psa.gov.ph, www.bps.go.id, www.stat.gov.mk, www.hcp.ma
- Tooling: files.pythonhosted.org, github.com, objects.githubusercontent.com, raw.githubusercontent.com (pypi.org already works)

**Later only:** api.core.ac.uk, export.arxiv.org, huggingface.co (Docling weights, at build time). **Do not add:** FRED hosts; api.semanticscholar.org until it is licensed.

Until the allowlist is live, develop locally and commit pinned snapshots, which CI replays offline.

---

## 6. Tech stack and repo layout

| Area | Choice | Note |
|---|---|---|
| Language | Python 3.12 (CI also on 3.13) | `mcp` needs ≥3.10 (V) |
| Packaging | `uv` workspace, `uv.lock` | `uv lock --check` in CI |
| Contracts | pydantic v2: `Observation`, `Provenance`, `PeerSet`, `FigureSpec`, `CaveatReport`, `Finding` | `result_id` = content hash |
| I/O | httpx + tenacity; duckdb + pyarrow; polars for transforms, pandas at the boundary | DuckDB 2.0 timing U |
| Estimation | statsmodels, linearmodels, pyfixest, scipy | |
| Charts | altair + vl-convert-python; matplotlib for house-style fallbacks | Vega JS bundled locally, no CDN |
| Files | pdfplumber (MIT), openpyxl, pyreadstat, pandera | **No PyMuPDF** (AGPL or commercial, V, [PyPI](https://pypi.org/project/PyMuPDF/1.19.4)) |
| QA | ruff, pyright, pytest, pytest-socket (no network in CI), pip-audit, licence scan | |
| Later (product gate) | `mcp>=2.3,<3`, CycloneDX SBOM, Sigstore signing, signed wheelhouse + data bundle | |

**One private monorepo for the 12 weeks** (the review defers the repo split). An import-boundary test stops `gjm` from importing `gje`, so splitting later is mechanical.

```
cgjr/
├── pyproject.toml · uv.lock
├── packages/
│   ├── gjm/  (MIT; goes public at the week-12 gate)
│   │   └── src/gjm/{connectors,store,vintage,registry,harmonize,methods,charts,caveat_engine}
│   └── gje/  (proprietary)
│       └── src/gje/{caveat_packs,resolver,replicate,digitize,ledger,draftcheck,exportgen,workbench,sandbox}
├── registries/
│   ├── hosts.yaml          ← allowlist and rate-limit defaults (single source)
│   ├── licences.yaml       ← per source/series; third-party flags
│   ├── peers/<report>.yaml ← peers, criteria, approver, frozen_at, alt set
│   ├── countries/<iso3>.yaml ← FY, rebasing dates, PPP base, NSO hosts
│   ├── sources/<report>.yaml ← hand-curated source_resolver entries
│   └── concordances/
├── specs/figures/{KEN-2026,MKD-2026,IDN-2026,PHL-2025,MAR-2026}/*.yaml
├── targets/<report>/*.csv  ← value, source_type {printed|text|digitized}, page
├── tolerances.yaml         ← one table for golden tests and grades
├── manifests/*.lock        ← committed
├── data/                   ← gitignored cache: raw/ (sha256) · parquet/ · catalog.duckdb
├── tests/{unit,golden,caveats,e2e,boundary,allowlist}
└── engagements/            ← gitignored; client data only here; wiped at delivery (logged)
```

---

## 7. 12-week roadmap

### 7.1 Revised first-15 replication targets

Changes from the inventory list:
- **#6.** North Macedonia Fig 4 needs TED. The review asked for a PWT-based growth-accounting figure from a full CGJR instead, but the item data show none (I, from `figures-5-reports.json`):
  - Morocco 1-4b and 1-5a use the CEM toolkit;
  - Philippines 7 and 1.5, and Indonesia 1.7, use MPO.

  The slot goes to Philippines Fig 1.17. Growth accounting is still tested in S2 as a method check, where R1 is allowed.
- **#11.** Kenya Fig 14 moves to the S2 extras. Morocco Fig 3-4 (PMR) takes the slot, giving more weight to the full CGJRs.
- **#15.** The Morocco population pyramid gives way to the Morocco G16 employment table.

| T | Report · figure (PDF p.) | Arch. | Data | Sprint |
|---|---|---|---|---|
| T1 | Kenya Fig 1 (10) | G01 | WDI, source 57 | S1 |
| T2 | North Macedonia Fig 1 (20) | G01 | WDI + NSO; 2025 forecast caveat | S1 |
| T3 | Indonesia Fig 1.1 (23) | G02 | WDI | S1 |
| T4 | Philippines Fig 1 (16) | G03 | WDI, 2021 PPP | S2 |
| T5 | Kenya Fig 4 (15) | G06 | PWT 10.01 | S2 |
| T6 | Philippines Fig 1.17 (40), unemployment vs MIC IQR | L01 | ILOSTAT + PSA LFS | S3 |
| T7 | Kenya Fig 2 (14) | G08 | KNBS | S3 |
| T8 | Kenya Fig 34 (33) | S02 | ILO modelled + NSO | S3 |
| T9 | Kenya Fig 35 (33) | S05 | ILO + KNBS | S3 |
| T10 | Indonesia Fig 2.1 (37) | S01 | WDI | S2 |
| T11 | Morocco Fig 3-4 (51), PMR | R03 | OECD / OECD-WBG PMR | S3 |
| T12 | Kenya Fig 22 (25) | X05 | WDI, 2017 PPP | S2 |
| T13 | North Macedonia Fig 37 (36) | X04 | UNCTAD + WDI | S3 |
| T14 | North Macedonia Figs 23a–b (28) | R01 | WGI at report vintage (source 57) | S2 |
| T15 | Morocco Annex 2 Employment Table (101) | G16 | ILO modelled (WB SCED) | S3 |

By report: Kenya 6, North Macedonia 3, Indonesia 2, Philippines 2, Morocco 2.

**Grades.**

| Grade | Meaning |
|---|---|
| R3 | Within tolerance at the report's vintage |
| R2 | Within tolerance at the current vintage, with every difference documented as a revision |
| R1 | Method and chart form reproduced, values outside tolerance; disclosed as an approximation |
| R0 | Not reproduced |

A target passes at R2 or better.

### 7.2 Build track

| Sprint | Tools | Targets | Acceptance criteria |
|---|---|---|---|
| **S1** wk 1–2 (12–23 Oct) | Provenance schema; `vintage_pin` + store; connector base (rate limiter, allowlist); `wb_data`; `peer_registry` YAML for the 5 reports; basic `harmonize`; `chart_series`; FigureSpec runner; `tolerances.yaml`; grader; `figure_inventory` | T1, T2, T3 | **Day-1 test:** pull Kenya Fig 1 from source 57 at the report's vintage; if that version is missing, apply the R2 rule. Kenya Fig 1 within ±0.2 pp of ~8.1% (2010), ~−0.3% (2020), ~4.7% (2024) and the 4.6% average for 2011–24. CI reruns all three offline |
| **S2** wk 3–4 | `pwt_data` (10.01 + 11.0); `benchmark_fit`; `chart_benchmark`; `growth_accounting`; `caveat_check` v0 (PPP mixing, forecast, vintage drift, licensed data); `country_registry`; `licence_register` | T4, T5, T10, T12, T14. Extras: Kenya Figs 14 and 15; growth-accounting method check against North Macedonia Fig 4 (R1 allowed) | Kenya Fig 4, 2019 values (2001=100): capital per worker ~136, GDP per worker ~135–136, TFP ~104. Kenya Fig 15: credit 31.6% of GDP; 48th → 38th percentile. Kenya Fig 22 pinned to 2017 PPP; a seeded test confirms mixing in 2021 PPP is **BLOCKed**. WGI pulled at the report's WDI vintage via source 57 (old estimate or new 0–100 score, whichever the report used) |
| **S3** wk 5–6 | `ilo_data`; `national_table_ingest` (KNBS, PSA); `structural_change`; `chart_decomposition`; `trade_data` (UNCTAD); `policy_index_data` (one OECD bulk query); `standard_tables` (G16); **`export` (Stata do-files + Python)**; `check_draft` v0 | T6, T7, T8, T9, T11, T13, T15 | Kenya Fig 35 total = 1.8% (exact to printed precision), with inter-sectoral services ~+1.55 pp. Kenya Fig 2 period labels 3.8 / 4.6 / 0.5 / 5.1, plus an identity flag (the 2020 stacks net to ~+1.2 pp). Every ILO-modelled series raises a WARN. Exported do-files rerun on a clean machine. **15/15 at R2+ (gate: ≥12)** |
| **S4** wk 7–8 | `imf_data` (SDMX 3.0, WEO vintages, IFS crosswalk); `reer_data`; `pop_data` (WPP2024 CSV); `es_aggregates`; `entrepreneurship_db`; `pip_poverty`; BACI in `trade_data`; `standard_tables` G14 (historical columns); `prior_findings` v0 + OKR/OpenAlex/Crossref resolvers; static HTML index | Kenya Figs 7, 9, 10, 11, 12, 13, 23, 26, 29; Indonesia Table A.2 (G16 in a second report); Morocco Fig 4-1a (L08) | **Sample First-Look pack for North Macedonia on public data, built in ≤3 working days.** Kenya Fig 9: debt ~73% of GDP, interest ~30% of revenue. Fig 23: 1.4% vs 0.5%. Fig 26: ECI rank 87 in both years (we compute ECI; the Atlas is a cross-check). Fig 29: elasticities −1 and −0.73 follow exactly from the table |
| **S5** wk 9–10 | `model_output_ingest` (MPO forecast years, G14 projections); full `check_draft` (binder + title/axis/period linter); sandbox; minimal `lfs_tab` (client mode, declared ICLS definition, do-file export) | Kenya §3 (Figs 6–15) at R2+; Kenya Figs 18 and 19 (exact printed labels) | The linter catches the **5** Kenya title/axis/period defects (Figs 6, 16, 19, 23, 28), and the identity check catches Fig 2. Fig 37's headline mismatch and Fig 38's legend colour are logged for manual review. 100% of numbers in a test draft bind. Pilot delivered, if one is signed |
| **S6** wk 11–12 | Coverage push; `firm_kit` + `es_microdata` spike on WBES and synthetic panels (n ≥ 5, dominance rule, disclosure log); `gjm` release prep (licence headers, docs); audit of replicability ratings (§8) | As many fully public items as possible | **40–50 of the 148** fully public items at R2+, measured on the 518 unique figures. Suppression tests pass. Coverage report published internally |

**Deferred past week 12 (review cuts):**
- MCP server (even for internal use), signing, SBOM, the public repo split;
- cross-OS determinism tests, property tests, nightly drift CI;
- the full workbench;
- Docling and GROBID;
- the `source_resolver` knowledge base;
- automatic digitizing;
- full `lfs_kit` and `firm_kit`, `io_kit`, `ltgm`, `gravity`, `chart_coef`, climate modules.

**Demos:**
- Week 4: about 8 figures across 4 countries.
- Week 8: the sample First-Look pack.
- Week 12: the coverage report plus the pilot case study.

### 7.3 Business track

| Week | Actions |
|---|---|
| 1–2 | Request the **vendor-bar ruling** (§5.1) and send the licence emails. Confirm that each of the 5 PDFs can be downloaded publicly from D&R or the OKR; anything obtained during an STC is excluded. Incorporate (jurisdiction: see §13). Founder IP assignment with a dated schedule of pre-existing IP (tool names + commit hashes). Open-core licence policy. E&O insurance quote |
| 3–4 | WBGeProcure registration. Discovery calls with Diego plus 6–8 TTLs. **Ask about AI use on Bank material** and about data sharing |
| 5–6 | Validate pricing. One-page offer (3 packs, 10 working days, outputs + rerunnable code). Security and data-handling note v1 |
| 7–8 | Send the North Macedonia sample pack to 5 TTLs and propose a pilot. Register with ADB and IDB. Contact one statistics office or finance ministry |
| 9–10 | **First paid pilot signed** (First-Look Sprint, $5–8k). Report defects are shown only to the team that owns the report, never in public marketing |
| 11–12 | Case study (with the client's consent). Pipeline review. Security baseline: MFA, encrypted devices, deletion log, written policies |

**Discovery questions:**
1. Which CGJR/CEM teams reach the data-crunch phase in the next 6 months?
2. How is analytic help bought today, from which budget, and up to what contract size can it be sole-sourced (U)?
3. Which charts take longest to produce or redo?
4. What is the policy on vendors using AI on OUO drafts and tables? Would a deterministic-only option help?
5. Which WDI vintage and which forecast source (MPO or WEO) does the team use?
6. Who holds the firm-data agreements, can contractors be added, and are AI tools allowed?
7. For the product later: who in Bank IT approves MCP servers on the AI gateway?

### 7.4 Decision gates

| Gate | Pass condition | If it fails |
|---|---|---|
| **Wk 2: eligibility** | Written ruling: no bar, or the bar has expired | Lead with ADB, IDB and governments until the date passes. No structural workarounds |
| **Wk 6: continue or pivot** | ≥12/15 targets at R2+, and ≥3 TTLs confirm both the pain and a $5–25k budget | If the build works but demand is weak: Pre-Review only, or non-Bank clients. If demand is there but the build is slow: sell First-Look packs only |
| **Wk 10: pilot** | 1 paid pilot signed | Change target clients; no free work |
| **Wk 12: product** | ≥2 paid engagements and a named Bank IT contact | Stay services-only. If it passes: start the MCP server, signing/SBOM, the public `gjm` release and SOC 2 readiness |

---

## 8. Testing and validation

**Tolerance table (`tolerances.yaml`, the only place tolerances are defined)**

| Quantity | Tolerance | Applies when the target is |
|---|---|---|
| Rates and shares | ±0.2 pp | printed or quoted in the text |
| Growth contributions | ±0.2 pp per year | printed or quoted in the text |
| Levels, indices, ratios | ±2% relative | printed or quoted in the text |
| Printed data labels | Exact to printed precision | printed |
| Ranks | Same order (exact rank where printed) | any |
| Identities (components sum to total) | Exact to printed rounding | any |
| Digitized values | The larger of the value tolerance and ±2% of the axis range | digitized |

**Test layers**

| Layer | What it checks | When |
|---|---|---|
| Golden replication | Spec + pinned manifest + targets + tolerance → grade | Every commit, offline (`pytest-socket`) |
| Exact identities | Kenya Figs 18/19 components sum to totals; Fig 35 = 1.8%; Fig 29 elasticities | Every commit |
| Seeded defects | Each caveat rule has a test that must BLOCK or WARN (target: 100% caught) | Every commit |
| Snapshot, boundary, allowlist | Vega-Lite specs stay stable; `gjm` never imports `gje`; no unlisted host; no analytics dependencies | Every commit |
| Export rerun | Do-files and scripts reproduce the CSVs on a clean machine | Before every delivery |
| Live drift | Manual run against the live APIs (nightly CI later) | Weekly from S2 |

**Digitizer protocol (MVP).**
- Targets come from three sources, used in this order: printed data labels, values quoted in the text, manual digitizing.
- Each target row records its source type, page and method.
- Manual digitizing calibrates two ticks per axis and records the calibration points.
- Automatic vector-path reading waits until manual work becomes the bottleneck.

**Audit of replicability ratings (S6).** The 27.6% and 60.4% shares are unaudited.
1. Draw a stratified 10% sample (about 54 items) by report × rating × data level.
2. Both founders rate each item independently against the definition "matches printed values within tolerance, using public data at the report's vintage".
3. Report the agreement rate and correct the shares.
4. Recompute coverage on the 518 unique figures, using the dedupe flag.
5. Outside the company, quote only the audited lower bound.

---

## 9. How the tools deliver the three service packs

| Pack | Price (illustrative) | Inputs from the team | Tools | Outputs | Days |
|---|---|---|---|---|---|
| **First-Look Sprint** | $5–8k | Country, questions, peers (or the team's CEM/SCD peers), vintage cutoff, optional national tables | Connectors, `vintage_pin`, `harmonize`, `caveat_check`, `prior_findings`, methods, charts, `export` | Data profile (coverage matrix), caveat memo, prior-findings "still holds / changed" table, 8–10 charts with an alternative-peer appendix, do-files/scripts, HTML index | 10 |
| **Analysis Pack** | $15–25k | As above, plus the last report's PDF, MPO/MFMod outputs and team tables | All of the above + `replicate_figure`, `structural_change`, `growth_accounting`, `standard_tables`, `model_output_ingest`, `lfs_tab` (client mode) | Replication of the last report (graded R0–R3), decompositions, benchmarking, G14/G16 tables, chart pack, candidate messages labelled AI-drafted with every number bound | 10–15 |
| **Pre-Review Check** | $5–10k | Draft (docx) and its data annex | `check_draft` (deterministic matcher + linter), `caveat_check`, `prior_findings`, targeted recomputation | Binding report (each number: bound / unbound / mismatch), caveat findings, title/axis/period issues, suggested footnotes | 5–7 |

**First-Look Sprint, day by day**

| Day | Work | Tools | Owner |
|---|---|---|---|
| 0 | Intake; peers frozen with criteria and approver; AI mode agreed (AI-assisted or deterministic-only); contract signed | `peer_registry` | Economist |
| 1 | Pull and pin the standard panel (WDI, WEO, ILO, PWT 11, WPP, PIP, ES, Entrepreneurship DB, BACI, OECD); ingest team tables | Connectors, `national_table_ingest`, `vintage_pin` | Engineer |
| 2 | Data profile: coverage, gaps, breaks, modelled vs observed, PPP base; first caveat memo | `harmonize`, `caveat_check` | Both |
| 3 | Prior-findings check against current data | `prior_findings`, resolvers | Economist |
| 4–6 | 8–10 charts. A typical set: G01, G02, G04, S02/S05, L01, X04/X05, F05, R01, B13, plus a rerun with the alternative peers | Methods, charts | Both |
| 7 | QA: rerun the exports on a clean machine; the memo passes `check_draft` | `export`, `check_draft` | Second founder |
| 8 | Assemble: HTML index, PNG/SVG + Vega specs, CSVs, do-files, caveat memo | `export`, index builder | Engineer |
| 9 | Walkthrough with the team; revisions | — | Both |
| 10 | Final delivery. Archive the manifest and ledger, delete client data (logged), and add golden tests built on public data only | `replicate_figure` | Both |

---

## 10. Security, IP and data handling

**Open-source vs proprietary split**

| Component | Licence | Why |
|---|---|---|
| Connectors, store, `vintage_pin`, `harmonize`, methods, charts, caveat engine (`gjm`) | MIT, published at the week-12 gate | Builds trust and matches the Bank's GitHub convention. The WB IGO Rider only reserves the Bank's privileges and immunities, so it does not apply to us (I) |
| Caveat packs, source-resolver data, peer-registry data, ledger, `check_draft`, digitizer, replication engine, export generator, workbench, sandbox, MCP server (`gje`) | Proprietary; listed as pre-existing IP | This is the moat. It is never embedded in deliverables |
| Deliverables (charts, CSVs, do-files, scripts) | The client owns them | Exports never import `gje`; Python exports may import MIT `gjm` |

**Licences of engine dependencies.**
- Only MIT or Apache: pdfplumber, camelot, Docling, GROBID.
- Poppler (GPL) is used only as a separate binary.
- PyMuPDF (AGPL) is excluded.
- A CI scan blocks copyleft dependencies in `gje`.

**Data licences in deliverables.**
- `licences.yaml` generates every source note.
- No raw Comtrade extracts.
- IMF data only as small pinned extracts until the IMF replies.
- The Atlas is a cross-check only.
- FRED and Semantic Scholar are excluded.

**AI and Bank material (the review's top item)**

| Mode | When | What the AI sees |
|---|---|---|
| AI-assisted | Public data only, or the contract explicitly allows AI | Public aggregates, our results, the team's questions |
| Deterministic-only | **Default for OUO drafts and team tables** | Nothing. Matcher, linter, caveats and charts run without AI |
| Client-run | Microdata, enclaves | Nothing. Tested code runs under the team's agreement and only aggregates leave |

**Client-data mode and disclosure control.**
- Code is written with AI outside the enclave, tested on WBES and synthetic panels, then run as plain Stata or Python inside. We assume enclaves allow no AI (I).
- Disclosure rules:
  - at least 5 firms per cell (configurable);
  - a dominance (p%) rule;
  - tail suppression;
  - a disclosure log for every run.
- Only aggregates leave the enclave.

**Other controls.**
- **No telemetry:** no analytics dependencies; outbound calls only to hosts in `hosts.yaml`; enforced by a test.
- **Engagement data:** kept only under `engagements/` (gitignored) on encrypted devices; deleted at delivery and logged.
- **Prompt injection:** retrieved PDFs and web text are data, never instructions.
- **Public sources only:** never internal Bank documents. A two-year rule may apply to projects a founder directly handled (I, from a 2015 note; confirm).

---

## 11. Budget (12 weeks, founder time excluded; all INFERRED)

| Item | Estimate | Notes |
|---|---|---|
| Data and API keys | ~$0 | All free tiers; avoid Orbis, EIU and BPS purchases |
| AI usage (services work) | $2–5k | Monthly cap with alerts; check current API pricing. The Bank's tokens cover the product phase |
| Tooling | $300–1,500 | VM and data cache, domain and email, GitHub plan (U) |
| Stata licence | U | Needed to test the do-files |
| Legal and admin | $2–6k | Incorporation, IP assignment, MSA template, accountant |
| E&O insurance | U | Get a quote in weeks 1–2 |
| **Total** | **~$5–13k + Stata + insurance** | |
| Later: SOC 2 | Type 1 auditor fees ~$7.5–30k (vendor blogs, rough) | Only after the week-12 product gate |

---

## 12. Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Former-staff vendor bar or conflict of interest | Med | Written ruling in week 1; non-Bank clients first if barred; public sources only |
| Bank OUO material sent to the AI | Med | Deterministic-only by default; contract clause; question 4 in every discovery call |
| Report PDFs not public, or defects shown in public | Low–Med | Check D&R/OKR provenance in week 1; show defects only to the team that owns the report |
| WDI source 57 lacks a report's vintage | Med | Day-1 test; R2 rule (documented revisions) |
| IMF or Comtrade refuses the licence request | Med | Small extracts; BACI; compute ECI ourselves; primary sources |
| IMF/ILO API changes (IFS retired, WGI rescaled, PWT on 2021 PPPs) | High | Direct SDMX calls, bulk fallbacks, our own IFS crosswalk, snapshots, caveat rules |
| Unknown rate limits (WB, Data360, PIP, new IMF API) | Med | Per-host limiter with cautious defaults; caching; OECD planned at 20/hour |
| Scope creep (69 archetypes; the plan was about twice what two people can ship) | High | Revised first-15; deferral list; 2–4 hours budgeted per figure |
| Forecast mismatch (WEO vs MPO) | Med | `model_output_ingest` for official forecasts; caveat rule |
| Jobs chapter rests on ILO modelled data | Med | Minimal `lfs_tab` in client mode (S5); WARN on every modelled series |
| Dev network blocks | High (now) | Allowlist (§5.2); local development + committed snapshots |
| Slow procurement, December stall | High | Register in weeks 3–4; deadlines before mid-December; small packs |

---

## 13. Open decisions for the founders

| # | Decision | Options | Recommendation (I) | By |
|---|---|---|---|---|
| D1 | Company jurisdiction | Founders' home country vs a US/UK/EU entity | Decide with an accountant, weighing Bank payment rails, tax and IP holding | Wk 2 |
| D2 | Default AI mode for Bank material | AI-assisted vs deterministic-only | Deterministic-only by default; AI only by contract clause | Wk 2 |
| D3 | Sample-pack country | Kenya (exact targets, but a background report) vs North Macedonia (full CGJR) | North Macedonia, with Kenya as the internal test bed | Wk 6 |
| D4 | Export language priority | Stata-first vs Python-first | Stata-first for delivery, Python in parallel | Wk 4 |
| D5 | When to publish `gjm` | Week 12 vs at the first product conversation | At the week-12 gate, after a licence scan | Wk 12 |
| D6 | TED for growth accounting | Register vs PWT approximation | Read the TED terms first; default to PWT, graded R1 | Wk 3 |
| D7 | Forecast source in charts | WEO vs the team's MPO | The team's MPO via `model_output_ingest`; WEO as a cross-check | Wk 5 |
| D8 | IMF data if the reply is slow or negative | Small extracts vs WDI/national substitutes | Small pinned extracts per engagement | Wk 6 |
| D9 | Roles | Economist (specs, peers, caveats, sales) / Engineer (connectors, CI, export, sandbox) | As stated; both deliver the pilot | Wk 1 |
| D10 | Exact price points (the packs themselves are decided) | Low vs high end of each range | Price the pilot at the low end in exchange for a case study | Wk 6 |
| D11 | Reuse of `data360-mcp` | Fork vs reference only | Reference only; call the API directly | Wk 12 |
| D12 | SOC 2 | Start now vs after the product gate | After the gate and a conversation with Bank IT | Wk 12 |

**Facts still to confirm:**
- rate limits for the WB, Data360, PIP, the new IMF API and WITS;
- licences for PIP, B-READY, STRI, PMR and TED;
- WPP data terms, and whether a 2026 edition exists;
- whether UNCTAD has an API;
- GFDD refresh plans;
- the Bruegel file format;
- ETD country coverage;
- the length of the former-staff bar;
- the Bank's sole-source threshold.

**Evidence base:** `/home/user/whynot.github.io/inventory/` (README, cross-report inventory and review, Kenya inventory, firm-data access, `figures-5-reports.json`).