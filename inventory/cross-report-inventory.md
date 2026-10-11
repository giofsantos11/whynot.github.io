# Cross-Report Inventory: Replicating Country Growth and Jobs Report Charts

**Scope.** The 5 reports covered are Indonesia 2026 (I), Kenya 2026 (K, the background report behind Kenya's CGJR), Morocco 2026 (M), North Macedonia 2026 (N) and Philippines 2025 (P). Together they contain **536 extracted items**: 479 charts, 49 tables, 7 diagrams and 1 map.

**How the numbers were built.** Every item was assigned to exactly one of **69 chart archetypes**. Module, data-level and replicability totals were re-tallied from the item list, and they match the deterministic aggregates exactly (for example, 148 yes / 176 partly / 190 no / 22 unclear). "Replicable" means the extractor's judgment of whether the item can be rebuilt from **public data**.

**Duplicates.** About 18 items repeat another figure in the same report: Philippines Overview Figs 3–13 recur in Chapters 1–3, North Macedonia's Key Messages figures recur in later chapters, and Indonesia Overview Figs 1–2 recur as Figs 1.17–1.18. That leaves roughly 518 unique figures.

---

## 1. Overview

### 1.1 Items and public replicability by report

| Report | Items | Yes | Partly | No | Unclear | Yes % | Yes+partly % |
|---|---|---|---|---|---|---|---|
| Indonesia 2026 | 159 | 34 | 63 | 47 | 15 | 21.4% | 61.0% |
| Kenya 2026 (background) | 40 | 29 | 8 | 3 | 0 | 72.5% | 92.5% |
| Morocco 2026 | 99 | 24 | 24 | 48 | 3 | 24.2% | 48.5% |
| North Macedonia 2026 | 135 | 39 | 47 | 49 | 0 | 28.9% | 63.7% |
| Philippines 2025 | 103 | 22 | 34 | 43 | 4 | 21.4% | 54.4% |
| **Total** | **536** | **148** | **176** | **190** | **22** | **27.6%** | **60.4%** |

### 1.2 Data level by report, with replicability

| Data level | I | K | M | N | P | Total | Share | Yes | Partly | No | Unclear |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Macro | 51 | 28 | 24 | 37 | 35 | 175 | 32.6% | 106 | 64 | 2 | 3 |
| Firm microdata | 42 | 0 | 27 | 47 | 30 | 146 | 27.2% | 8 | 32 | 104 | 2 |
| Sector | 30 | 7 | 6 | 13 | 6 | 62 | 11.6% | 30 | 25 | 5 | 2 |
| Model output | 20 | 1 | 12 | 13 | 14 | 60 | 11.2% | 0 | 15 | 45 | 0 |
| Household/labor microdata | 2 | 0 | 22 | 17 | 8 | 49 | 9.1% | 3 | 25 | 21 | 0 |
| Mixed | 8 | 2 | 7 | 2 | 4 | 23 | 4.3% | 1 | 13 | 3 | 6 |
| Unknown (diagrams, matrices) | 6 | 2 | 1 | 6 | 6 | 21 | 3.9% | 0 | 2 | 10 | 9 |

### 1.3 Module by report

| Module | I | K | M | N | P | Total | Reports |
|---|---|---|---|---|---|---|---|
| Labor markets & jobs | 11 | 3 | 25 | 21 | 17 | 77 | 5 |
| Growth & macro | 22 | 4 | 16 | 16 | 15 | 73 | 5 |
| Firms & private sector | 19 | 0 | 17 | 24 | 9 | 69 | 4 |
| Trade, external & FDI | 22 | 12 | 3 | 25 | 6 | 68 | 5 |
| Governance, institutions & business environment | 30 | 0 | 8 | 15 | 5 | 58 | 4 |
| Productivity & structural change | 26 | 6 | 7 | 11 | 6 | 56 | 5 |
| Fiscal & public sector | 6 | 8 | 7 | 3 | 5 | 29 | 5 |
| Digital technology | 8 | 0 | 2 | 3 | 9 | 22 | 4 |
| Spatial, urban & regional | 0 | 0 | 1 | 2 | 16 | 19 | 3 |
| Finance & credit | 8 | 4 | 3 | 0 | 0 | 15 | 3 |
| Human capital & skills | 0 | 0 | 5 | 7 | 3 | 15 | 3 |
| Climate, energy & environment | 0 | 1 | 2 | 5 | 4 | 12 | 4 |
| Infrastructure | 3 | 0 | 0 | 1 | 5 | 9 | 3 |
| Poverty & inequality | 2 | 1 | 0 | 0 | 2 | 5 | 3 |
| Demographics | 0 | 0 | 2 | 2 | 0 | 4 | 2 |
| Other (frameworks, matrices) | 2 | 1 | 1 | 0 | 1 | 5 | 4 |

**What this shows**

- **Macro charts are the easy public core.** 106 of the 175 macro items are fully replicable, and only 2 are not. Macro and sector items together hold 136 of the 148 "yes" items (92%).
- **Firm microdata is the biggest wall.** It accounts for 104 of the 190 non-replicable items (55%). It drives Chapter 2 of 4 reports (I, M, N, P). Kenya has none, which is why Kenya is 92.5% public.
- **Model output is the second wall.** 45 of the 60 model items are "no": MFMod-SA, MANAGE CGE, LTGM, CCDR and the CEM distortion tools. Every full CGJR (I, M, N, P) ends with a simulated reform package.
- **Labor and growth modules appear everywhere**, with 5/5 coverage. Firm and governance modules appear in all 4 full CGJRs. Spatial content is concentrated in the Philippines (16 of 19 items).
- **Indonesia is regulation-heavy.** It has 30 governance items, covering PMR, competition law and business-of-the-state (BOS) analysis. About 95 of its source tags read only "Other": they cover OECD PMR, OECD STRI, the BOS data and the billionaire data, and many of those sources are actually public. Normalizing source attribution is therefore an MVP task.

---

## 2. Common report architecture

| Block | I | K | M | N | P | Dominant archetypes |
|---|---|---|---|---|---|---|
| Key messages / Overview / Executive summary (reuses chapter figures) | Overview, Figs 1–3 | Exec Summary + priorities table | Overview | Key Messages + Overview | Exec Summary, Preface, Overview (Figs 1–14) | Z01, repeats of chapter charts |
| **Ch. 1 Macroeconomic features of growth and jobs** | Ch1 | Ch2 "Setting the scene" + Ch3 "How was growth financed" | Ch1 | Ch1 | Ch1 | G01–G08, G10–G11, S01–S05, L01–L02, F01–F02, X01–X05 |
| **Ch. 2 Microeconomic features (firm productivity)** | Ch2 | (4.C, sector-level only) | Ch2 | Ch2 | Ch2 | B02–B06, B10, B13, S04 |
| Markets, competition, business environment | Ch3–4 | — | Ch3 | Ch3 | Ch2 sections | B08, R01, R03, R04, F07, F03 |
| Trade & investment | Ch5 | 4.B External constraints | Ch1 sections | Ch4 | Ch1–2 sections | X04–X12 |
| Human capital & labor deep-dive | — | — | Ch4 | Ch5 | Ch1/Ch3 boxes | L03–L13 |
| Spatial | — | — | Ch4 (regions) | Annex 1 | Ch3 | SP1–SP3 |
| Digital / technology | Ch2 sections | — | Ch2 sections | — | Ch4 | B10, N01, L10 |
| Climate | — | 4.D | Ch4 (droughts) | Annex 2 | Ch5 | C01, C02 |
| Fiscal constraints | — | 4.A | Box 1-1 | — | Ch3 (local government units, LGUs) | F01–F03 |
| **Policy roadmap + simulated impacts** | Ch6 + Annex 3 matrix | Priorities table (no simulation) | Ch5 | Ch6 + Annex 5 | Overview + Ch6 | Z02, G12, G13 |
| **Standardized tables annex** | Annex 1–2 | — | Annex 1–2 | Standardized tables | Standardized tables | G14, G16 |

**Patterns across the reports**

- **4 of 5 reports share one CGJR spine:** Overview → Macro features → Micro features → 2–4 thematic chapters → Policy roadmap with MFMod or CGE simulation → Standardized tables (I, M, N, P).
- **Kenya follows a different structure.** The background report is organized around constraints (fiscal, external, productivity, climate). It has no firm microdata, no reform simulation and no standardized tables.
- **Peer benchmarking is universal (5/5).**
  - M, N and P use explicit "structural" and "aspirational" peer groups; I uses them in its standardized tables.
  - I and K chart against 9–10 named peers.
  - P and N use middle-income (MIC) or upper-middle-income (UMIC) median and interquartile range (IQR) bands. P has 12 of the 14 "line with IQR band" charts (N has the other 2).
- **The micro chapter reuses one toolkit.** In I, M, N and P the firm chapter is built on firm census or admin data run through ICA-style procedures: TFP estimation, Olley-Pakes decomposition, quintile profiles and concentration measures.

---

## 3. Standard chart library (69 archetypes)

### 3.1 Chart forms (from `by_chart_type`, grouped)

| Form | Items | Form | Items |
|---|---|---|---|
| Line (time series, index) | 94 | Combo bar+line / dual axis | 19 |
| Stacked bar (incl. 100%, with markers or line) | 85 | Distribution (kernel, histogram, Lorenz, box, growth incidence, pyramid) | 16 |
| Bar / horizontal bar (sorted, reference lines, peer markers) | 76 | Scenario / projection line | 15 |
| Scatter / bubble / binned / connected | 61 | Dot / strip / radar / slope / pie | 15 |
| Grouped / clustered bar | 50 | Line with IQR band | 14 |
| Data table | 36 | Policy / qualitative table | 13 |
| Coefficient / confidence-interval plot | 33 | Diagram / map / chord | 9 |

Eight chart templates would cover about 84% of items (449 of 536; about 87% if scenario lines count as lines): line, stacked bar with total marker, bar with peer markers, grouped bar, scatter with highlighted country and fitted curve, coefficient plot, IQR band and data table.

### 3.2 Archetypes, ranked by number of reports, then items

Public column = yes / partly / no (unclear in parentheses). "Level" is the dominant data level.

| # | ID | Archetype | Reports | Items | Typical indicators | Typical sources | Method | Level | Public y/p/n |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Z02 | Policy matrix / roadmap / reform timeline | 5 IKMNP | 13 | Action × area × priority / pillar / EU alignment | Staff | Qualitative | — | 0/2/9 (2) |
| 2 | G04 | Growth accounting & potential growth | 5 IKMNP | 8 | K, L, human capital, TFP contributions (pp); potential growth; capital contribution vs MIC IQR | PWT, MPO, CEM toolkit, TED, MFMod | Solow / production-function decomposition | Macro | 1/7/0 |
| 3 | G12 | Reform-simulation results | 4 IMNP | 27 | GDP, sectoral value added, demand, employment, wages, "more & better jobs" (% deviation from baseline) | MFMod-SA, MANAGE | Macro-structural / CGE | Model | 0/1/26 |
| 4 | B08 | Concentration, markups, competition risk | 4 IMNP | 17 | CR4, HHI, entrenchment, markups, % revenue/employment at risk | Firm census, listed firms | Concentration ratios, regression | Firm | 0/3/14 |
| 5 | S04 | Sector productivity vs employment (structural-change scatter) | 4 IMNP | 13 | Log VA/worker (relative) vs employment share or change; wage growth | NSO + LFS, firm census | Connected / bubble scatter | Sector | 2/7/4 |
| 6 | L05 | Skills: attainment, mismatch, occupations, returns | 4 IMNP | 13 | Over/under-qualification, occupation mix, Mincer returns, digital skills, vacancies | LFS microdata, ITU, vacancy scrape | Survey tabulation, Mincer | HH | 2/6/4 (1) |
| 7 | G11 | Long-term growth scenarios | 4 IMNP | 11 | GDP/GDPpc growth paths, GNI pc vs HIC or EU threshold, output gap, inflation, current account | LTGM, MFMod | Projection | Model | 0/6/5 |
| 8 | B02 | Firm TFP levels, trends, premia | 4 IMNP | 11 | TFPR/TFPQ, sales or VA per worker by firm type, size or peer | Firm census, ES, ICA 2.0 | Production-function estimation | Firm | 0/3/8 |
| 9 | B13 | Enterprise Survey obstacles; business environment → productivity | 4 IMNP | 11 | % firms citing obstacle; days and dispersion; innovation rates; coefficients | ES (+ICA 2.0) | Tabulation, regression | Firm | 6/5/0 |
| 10 | X06 | Export sophistication & diversification | 4 IKMN | 10 | ECI rank, market HHI, destinations, GVC participation, services exports, agri exports to EU | Atlas, WDI, EORA, TiSMoS | Ratios, rankings | Macro | 5/4/0 (1) |
| 11 | B03 | Aggregate productivity decomposition | 4 IMNP | 9 | Within / between / entry / exit contributions | Firm census, World Business Dynamics DB | Olley-Pakes, Melitz-Polanec | Firm | 0/0/9 |
| 12 | X05 | Trade openness & export gaps | 4 KMNP | 7 | X, M, X+M % GDP; vs income; gravity export gap or potential | WDI, EORA, gravity | Income scatter, gravity | Macro | 3/4/0 |
| 13 | G14 | Standardized macro / projection tables | 4 IMNP | 6 | ~40 rows: growth, demand, potential, TFP, poverty, labor, sectors, external, fiscal | WDI, MPO, ILO, PWT, MFMod | Period averages vs peers | Macro | 1/2/3 |
| 14 | G01 | Real GDP (per capita) growth vs peers & income groups | 4 IKMN | 4 | Growth rates, period averages, volatility, negative years | WDI, NSO | Growth rates | Macro | 4/0/0 |
| 15 | G16 | Standardized employment table | 4 IMNP | 4 | Employment growth and shares by sector vs peers | ILO modelled, LFS | Growth / shares | Sector | 4/0/0 |
| 16 | B10 | Firm technology adoption | 3 IMP | 14 | GBF sophistication (average vs top 20%), drivers, barriers, premia | FAT survey | OLS-adjusted means | Firm | 0/2/12 |
| 17 | R01 | Governance & institutional indices | 3 IMN | 13 | WGI ranks, policy capture, BTI, EIU risk, B-READY, reform z-scores | WGI, BTI, EIU, B-READY | Benchmarking | Macro | 6/5/2 |
| 18 | R04 | Competition enforcement & regulatory inventories | 3 INP | 12 | Cases by type, sector and outcome; restrictive provisions; merger thresholds; permit duplication | Authority records, WB merger DB | Counts, benchmark regression | Macro | 0/9/3 |
| 19 | L01 | Headline labor-market trends | 3 MNP | 12 | Employment rate, LFPR, unemployment by sex, age, education, area | LFS, ILOSTAT | Series vs MIC IQR | Macro/HH | 5/7/0 |
| 20 | SP1 | Regional income, contributions, convergence | 3 MNP | 12 | Regional GDPpc, VA/worker, growth contributions, β-convergence, regional employment | Regional accounts, LFS | Decomposition, β-regression | Macro | 8/4/0 |
| 21 | F05 | Financial depth & banking structure | 3 IKM | 11 | Private credit % GDP (vs income, kernel), bank concentration, NIM/ROA, state-owned bank (SOB) shares | WDI/GFDD, regulator | Benchmark scatter | Macro | 7/4/0 |
| 22 | B06 | Jobs & wages by firm productivity | 3 MNP | 11 | Employment share, net job creation, wages, labor share by productivity quantile | Firm census | Quantile tabulation, binscatter | Firm | 0/0/11 |
| 23 | G06 | Capital deepening & returns | 3 KNP | 8 | K/worker vs US, private K/Y, return to capital, deepening vs widening, TFP index | PWT, ICSD, MPO | Perpetual inventory, indices | Macro | 3/5/0 |
| 24 | S02 | Employment by sector | 3 KNP | 8 | Levels, shares, tradable vs non-tradable, contributions to employment growth | ILO modelled, LFS, firm census | Shares / growth | Sector | 3/2/3 |
| 25 | C02 | Climate exposure & impacts on firms and workers | 3 MNP | 8 | Exposure shares; effects on productivity, wages, employment; adaptation | Weather data + firm census / LFS | Event study, fixed effects | Firm/HH | 1/1/5 (1) |
| 26 | N01 | Infrastructure service cost & quality | 3 INP | 7 | Electricity price, broadband price and speed, LPI, QI4SD | ITU, Ookla, LPI, UNIDO | Benchmarking | Macro | 3/4/0 |
| 27 | Z01 | Conceptual framework diagrams | 3 IKP | 6 | Pillars and channels | — | — | — | 0/0/2 (4) |
| 28 | X12 | Exporter dynamics & export support | 3 INP | 6 | Exporter counts, premia, jobs from export entry, subsidy effects | Firm census + customs | Tabulation, local projections | Firm | 0/1/5 |
| 29 | L13 | Migration & foreign labor | 3 IMP | 6 | Foreign-worker permits, emigrant mismatch, migrants' occupations | Ministry, DIOC, census | Tabulation, probit | Mixed | 1/3/2 |
| 30 | G03 | Cross-country growth benchmarking | 3 IMP | 5 | Catch-up vs initial income, growth vs volatility, years per income group | WDI, MPO | Benchmark scatter | Macro | 3/2/0 |
| 31 | P01 | Poverty & distribution | 3 IKP | 5 | Headcount, growth incidence curve, growth elasticity, top-10% shares | PIP/WDI, HH survey | Tabulation | Macro/HH | 2/3/0 |
| 32 | G07 | Investment rate & composition | 3 IKM | 5 | GFCF % GDP vs peers; public vs private; FDI vs domestic by industry | WEO, IMF Article IV | Ratios | Macro | 1/4/0 |
| 33 | B05 | Firm lifecycle & size transitions | 3 IMP | 5 | Employment or TFP by age vs peers; size graduation | Firm census | Fixed-effects age profiles | Firm | 0/0/5 |
| 34 | X04 | FDI inflows & stock | 3 KMN | 5 | Net FDI % GDP vs peers or UMIC IQR; inward stock | WDI, IFS, UNCTAD | Ratios, IQR | Macro | 5/0/0 |
| 35 | F02 | Public debt & debt service | 3 KMP | 4 | Debt % GDP (domestic/external), interest/revenue vs debt, yield curve | WEO, MEF | Series, scatter | Macro | 3/1/0 |
| 36 | S05 | Shift-share decomposition | 3 KMN | 4 | Within vs between labor productivity / TFP growth | NSO + ILO | Shift-share | Sector | 1/3/0 |
| 37 | G02 | Convergence to frontier | 3 IMN | 3 | GDPpc % of US or EU; change vs high-income | WDI | Ratio | Macro | 3/0/0 |
| 38 | X09 | Services-trade & FDI restrictiveness | 2 IN | 15 | STRI by sector and mode, ad-valorem equivalents (AVE), FDIRI, restricted activities, local-content rules | OECD STRI, WB STRI, OECD FDIRI | Composite index, gravity | Macro | 11/1/2 (1) |
| 39 | R03 | Product market regulation | 2 IM | 14 | PMR economy-wide, sub-indices, network sectors, professions, digital, price controls | OECD / OECD-WBG PMR | Benchmarking | Macro | 11/1/0 (2) |
| 40 | F07 | State footprint (BOS / SOEs) | 2 IN | 12 | BOS presence, revenue, employment, ownership; PMR vs BOS | WB BOS DB, firm census | Ratios, scatter | Firm | 0/11/1 |
| 41 | G10 | Growth correlates & reform effects | 2 IM | 8 | Growth-regression contributions; local projections; reformer catch-up | CEM toolkit, panel | Regression | Macro | 0/8/0 |
| 42 | L06 | Youth transitions & NEET | 2 MN | 8 | NEET, status after school, exit and re-entry | LFS panel, CNSS, tracer survey | Transition tabulation | HH | 1/1/6 |
| 43 | L07 | Gender gaps | 2 MN | 8 | GEGI, inactivity reasons, wage-gap decomposition, hiring experiment | LFS, CNSS, HR survey | Tabulation, AKM, experiment | HH | 0/4/4 |
| 44 | B09 | Large firms & billionaire wealth | 2 IP | 7 | Listed-firm sales % GDP, billionaires per million, wealth by sector | Listed firms, rich lists | Ratios | Mixed | 0/3/0 (4) |
| 45 | F01 | Fiscal aggregates & fiscal–macro links | 2 KM | 7 | Revenue, spending, balances by type; government consumption vs REER; crowding-out | WEO, Treasury, MEF | Ratios, scatter | Macro | 6/1/0 |
| 46 | L03 | Job quality: wage employment, formality | 2 MP | 7 | Wage-employment share vs MIC; formal/informal jobs; minimum wage vs productivity | LFS, GMID | Ratios | Macro | 1/3/1 (2) |
| 47 | B01 | Firm size distribution | 2 IM | 6 | Kernel density of log sales, Lorenz curve, size-class shares | ES, firm census | Density, Lorenz | Firm | 0/4/1 (1) |
| 48 | F03 | Tax policy & tax wedges | 2 MN | 6 | Effective CIT by size, bunching, statutory CIT, tax wedge, formalization tax | DGI, CNSS, PwC, WB-wiiw | Ratios, microsimulation | Mixed | 1/3/2 |
| 49 | B04 | Misallocation | 2 IN | 5 | TFP–employment covariance, SD of log TFPR, links to entry and jobs | Firm census | Dispersion, binscatter | Firm | 0/0/5 |
| 50 | L04 | Wage levels & profiles | 2 KM | 5 | Real formal wages; wage distributions; tenure and cohort profiles | KNBS, LFS, CNSS | Tabulation | HH | 1/0/4 |
| 51 | S07 | Distortion elasticities | 2 MP | 5 | Output response to distortion cuts (own sector vs value chain); wedges | GLORIA + CPT model; CEM 3.0 tool | Input-output general-equilibrium model | Model | 0/2/1 (2) |
| 52 | L08 | Demography & labor-supply projections | 2 MN | 5 | Pyramid, dependency ratio, projections, labor demand vs supply | UN WPP, SSO | Series, projection | Macro | 3/2/0 |
| 53 | L10 | AI & green-transition exposure | 2 NP | 5 | AI exposure × complementarity, brown jobs, task content | LFS + exposure indices | Crosswalk tabulation | HH | 0/5/0 |
| 54 | X01 | External & monetary stability | 2 KP | 4 | Current-account components, BoP, reserves and import cover, CPI | IFS, CBK, PSA | Ratios | Macro | 4/0/0 |
| 55 | X02 | Real effective exchange rate | 2 KP | 4 | REER index; REER vs net exports | Bruegel | Index, scatter | Macro | 4/0/0 |
| 56 | X08 | Tariff structure & incidence | 2 KN | 4 | Applied tariffs, input-tariff cost share, EU-alignment simulation | WDI, UTAS/WITS, customs | Input-output incidence | Macro | 1/3/0 |
| 57 | L02 | Jobs–growth relationship | 2 KM | 4 | Jobs gap, net jobs vs growth, employment elasticity, new jobs vs entrants | LFS, NSO | Elasticity, counterfactual | Macro | 0/4/0 |
| 58 | B07 | Firm entry, exit, density | 2 MN | 4 | Entry and exit rates, business density, entrant productivity | Entrepreneurship DB, firm census | Ratios | Macro | 3/0/1 |
| 59 | X11 | FDI spillovers | 2 NP | 4 | Backward, forward, horizontal TFP and energy spillovers | Firm census + input-output tables | Javorcik regression | Firm | 0/0/4 |
| 60 | S01 | Manufacturing share vs income | 2 IN | 3 | Manufacturing VA and employment shares vs log GDPpc or time | WDI | Polynomial / local smoothing | Macro | 3/0/0 |
| 61 | G08 | Growth decomposition by sector or expenditure | 2 KN | 3 | Sector contributions to growth; real demand indices | NSO | Share-weighted contributions | Macro | 3/0/0 |
| 62 | S06 | Services linkages, input-output tables, job multipliers | 1 I | 11 | Services typology, services-input share, flows, multipliers, connectivity vs capabilities | Input-output tables, ILOSTAT, firm census | Input-output analysis, Bartik instrument | Sector | 7/1/2 (1) |
| 63 | SP3 | Subnational finance, governance, prices, connectivity | 1 P | 11 | LGU transfers, spending, budget execution; CMCI scores; CPI; RORO ports | BLGF, COA, DTI, PSA | Ratios, scatter, marginal effects | Macro | 2/7/2 |
| 64 | G13 | Sector / trade-policy CGE scenarios | 1 I | 9 | Nickel/battery output, value added, labor income; STRI gap closure; services productivity shocks | CGE | CGE | Model | 0/0/9 |
| 65 | SP2 | Regional firm dynamics | 1 P | 6 | TFP growth, firm-count growth, intensive vs extensive margin by region | Firm census | Decomposition | Firm | 0/2/4 |
| 66 | B11 | Firm finance & trade credit | 1 M | 5 | Days payable vs days sales outstanding by size or productivity; external finance | OMTPME, ES | Tabulation | Firm | 1/0/4 |
| 67 | B12 | Public support program evaluation | 1 N | 4 | FITD projects, productivity transitions, matched event study, CIS coefficients | FITD, firm census, CIS | Event study | Firm | 0/0/4 |
| 68 | S03 | Labor productivity by sector; GDP per worker index | 1 K | 2 | GDP/worker index vs peers; VA/worker by sector | ILO, NSO | Ratios | Sector | 2/0/0 |
| 69 | C01 | Climate damage projections (CCDR) | 1 K | 1 | GDP deviation by damage channel, 2050 | CCDR model | Projection | Model | 0/0/1 |

**Reading notes**

- **S03 is undercounted as a building block.** VA per worker by sector is the input to S04 and S05, so it is implicitly needed in all 5 reports.
- **No report contains a Shapley decomposition of GDP-per-capita growth** (productivity, employment rate, demography). The nearest equivalents are G04 (growth accounting) and S05 (shift-share).
- **The "income- or size-conditional scatter with highlighted country" form spans archetypes** and appears in 4 reports: K Figs 14, 22, 37; I Figs 2.1–2.2, 3.14; P Fig 4.6; N Figs 54–55, 76R.

---

## 4. Indicator dictionary (normalized; ~110 most-charted indicators)

Reports are listed as letters. "std" means the indicator appears only or mainly in standardized tables.

| # | Indicator | Unit | Typical source | Reports |
|---|---|---|---|---|
| 1 | Real GDP growth | % y/y; period average | WDI, NSO, MPO | IKMNP |
| 2 | GDP per capita growth | % | WDI | IMP |
| 3 | GDP per capita (PPP) relative to frontier | % of US / high-income / EU | WDI, PWT | IMNP |
| 4 | GNI per capita (Atlas) vs high-income threshold | US$; % of threshold | WDI | IMP |
| 5 | Growth volatility | SD of growth or output gap | WDI, MPO | IM |
| 6 | Potential GDP growth | % | MPO, MFMod | IMNP |
| 7 | Output gap / potential vs observed GDP | % ; index | MFMod | IMP |
| 8 | Contributions of capital, labor, human capital, TFP | pp | PWT, MPO, CEM toolkit, TED | IKMNP |
| 9 | TFP index / TFP response (economy-wide) | index | PWT, cross-country panel | IK |
| 10 | Capital stock per worker; private K/Y; return to capital | % of US; % GDP; % | PWT, ICSD, staff perpetual inventory | KNP |
| 11 | Investment (GFCF), private / public | % GDP | WEO, WDI, NSO | IKMNP |
| 12 | Private investment by industry, domestic vs FDI | % GDP | Investment agency (Other) | I |
| 13 | Real demand components (C, G, I, X, M) | index; % change | NSO, WDI | IKMNP |
| 14 | Sectoral contribution to GDP growth | pp | NSO | KN (+P regional) |
| 15 | Value-added shares (agriculture / industry / services) | % GDP | WDI, NSO | IMNP (std) |
| 16 | Manufacturing value added | % GDP; index to peak | WDI | IN |
| 17 | Scenario GDP / GDPpc growth | % | LTGM, MFMod | IMNP |
| 18 | GNI pc projection vs high-income or EU benchmark | % | LTGM, MFMod | INP |
| 19 | Reform impact on GDP | % deviation from baseline | MFMod, MANAGE | IMNP |
| 20 | Reform impact on employment, real wages, labor income | % deviation | MFMod, MANAGE | IMNP |
| 21 | More / better / additional jobs | millions | MFMod, MANAGE | IMP |
| 22 | Sectoral value added under reforms | % deviation; pp | MFMod, MANAGE | MP |
| 23 | Poverty headcount ($2.15, $3.65, national) | % of population | PIP / WDI, HH survey | IKNP |
| 24 | Gini index | 0–100 | PIP / WDI | INP (std) |
| 25 | Growth incidence curve; growth elasticity of poverty | % by percentile; elasticity | HH survey | KP |
| 26 | Top-10% income or wealth share | % | Other (wealth DB) | I |
| 27 | Government revenue (by tax) | % GDP | WEO, Treasury, MEF | KM |
| 28 | Government expenditure (by economic type) | % GDP | WEO, Treasury, MEF | KM |
| 29 | Fiscal / primary balance | % GDP | WEO, MPO | IKMNP |
| 30 | Public debt (domestic / external); interest/revenue; yields | % GDP; %; % by maturity | WEO, MEF, IMF | IKMNP |
| 31 | Government investment / development spending | % GDP | Treasury, IMF Article IV | K |
| 32 | Statutory and effective CIT rate | % | PwC, DGI records | M |
| 33 | Labor tax wedge / formalization tax rate | % of labor cost | CNSS, WB-wiiw | MN |
| 34 | BOS / SOE revenue, employment, market presence | % GDP; % of markets | WB BOS DB | IN |
| 35 | Subnational transfers, spending, budget execution | PHP bn; % GDP; % | BLGF, COA | P |
| 36 | Current-account balance and components | % GDP | IFS, WEO, MPO | IKMNP |
| 37 | BoP financial account; reserves; import cover | % GDP; US$; months | IFS, central bank | K (+std INP) |
| 38 | CPI inflation | % | NSO, WEO | INP |
| 39 | Real effective exchange rate | index | Bruegel, IFS | IKNP |
| 40 | Net FDI / FDI inflows | % GDP | IFS, WDI, UNCTAD | IKMNP |
| 41 | FDI inward stock | % GDP | UNCTAD | N |
| 42 | Exports, imports, trade openness | % GDP | WDI, EORA | IKMNP |
| 43 | Economic Complexity Index rank | rank | Atlas | KM |
| 44 | Export-market / supplier HHI; destination shares; GVC participation | index; % | WDI / WITS, EORA | N |
| 45 | Services exports (share, category, digitally delivered) | %; US$ | WDI, TiSMoS, UNCTAD | IN |
| 46 | Agri-food exports to EU (share; products > US$10m) | %; count | WB trade study | K |
| 47 | Export gap / export potential | % GDP | Gravity model | NP |
| 48 | Applied tariffs; upstream tariff cost share | %; % of input costs | WDI, UTAS / WITS | KN |
| 49 | STRI by sector or mode; ad-valorem equivalent | 0–100; % | OECD STRI, WB STRI | IN |
| 50 | FDI restrictiveness (FDIRI; restricted activities; minimum capital) | 0–1; count; US$ | OECD FDIRI, national law | IN |
| 51 | Digital trade restrictiveness | index | Other | I |
| 52 | Domestic credit to private sector | % GDP | WDI / GFDD | IKMNP |
| 53 | Financial structure; SOB shares; bank concentration; NIM; ROA | %; % assets | GFDD, regulator | I |
| 54 | Private vs government credit | % GDP | KNBS / CBK | K |
| 55 | Share of working capital / investment financed externally | % | Enterprise Surveys | M |
| 56 | Lending interest rate | % | WDI | INP (std) |
| 57 | Employment by sector (levels; tradable vs non-tradable) | millions | ILO modelled, LFS | KNP |
| 58 | Employment shares by sector | % | ILO modelled, LFS | IKMNP |
| 59 | Employment growth by sector | % p.a. | ILO, LFS, firm census | IMNP |
| 60 | Value added per worker by sector | LCU / US$ (log) | NSO + ILO / LFS | IKMNP |
| 61 | GDP per worker index | index | ILO, WDI | KP |
| 62 | Within / between productivity growth | pp | NSO + ILO | KMN |
| 63 | Services-input intensity of manufacturing | % of inputs | Input-output tables | I |
| 64 | Local job multipliers | jobs per job | Firm census | I |
| 65 | Employment-to-population / employment rate | % | LFS, ILO | IMNP |
| 66 | Labor force participation (by sex, age, area, education) | % | LFS, ILO | IMNP |
| 67 | Unemployment rate (by sex, education) | % | LFS, ILO | IMNP |
| 68 | Jobs gap; net job creation; employment elasticity | thousands; ratio | LFS, NSO | KM |
| 69 | Wage-employment share | % of employment | LFS, GMID | INP |
| 70 | Formal vs informal jobs | millions; % | LFS, CNSS | KMP |
| 71 | Real wages (average; public / private; by sector) | LCU; % growth | KNBS, admin wages, LFS | KMNP |
| 72 | Wage distribution; tenure and cohort profiles; gender wage gap | log index; % | LFS, CNSS | M |
| 73 | Minimum wage vs productivity; share below minimum wage | % of NCR; % | PSA, LFS | P |
| 74 | NEET rate; school-to-work status; exit/re-entry | %; probability | LFS, CNSS | MN |
| 75 | Over/under-qualification (vertical / horizontal mismatch) | % of workers | LFS | MN |
| 76 | Occupational skill composition | % of workers | LFS, census | IP |
| 77 | Returns to education | % per year | LFS | N |
| 78 | Educational attainment / years of schooling | %; years | LFS, std tables | IMNP |
| 79 | Population by age and sex; dependency ratio | %; ratio | UN WPP, SSO | IMNP |
| 80 | Labor demand vs active population projection | thousands | Staff projection | N |
| 81 | Gender Employment Gap Index; inactivity reasons | index; % | LFS | N |
| 82 | AI exposure × complementarity; brown jobs; task content | % of workers | LFS + exposure indices | NP |
| 83 | Foreign workers; emigrant mismatch; migrants' occupations | thousands; % | Ministry, DIOC, census | IMP |
| 84 | Firm TFP (TFPR / TFPQ) level and growth | log; % | Firm census, ES | IMNP |
| 85 | Firm labor productivity (VA or sales per worker) | US$ PPP; log | Firm census, ES | MNP |
| 86 | Firm size distribution; sales concentration | density; cumulative share | ES, firm census | IM |
| 87 | Employment / wages by firm productivity quantile | %; wage | Firm census | MNP |
| 88 | Labor share / profit share of value added | % | Firm census | M |
| 89 | Firm entry / exit; business density; number of firms | %; per 1,000 adults | Entrepreneurship DB, firm census | MNP |
| 90 | Firm-age growth profile; size graduation | index (age 1 = 1); % | Firm census | IMP |
| 91 | CR4 / HHI / entrenchment | %; index | Firm census, listed firms | INP |
| 92 | Markups; sales and input distortion wedges | ratio; % | Firm census, ES | IM |
| 93 | Revenue / employment / VA in high competition-risk industries | % | Firm census | MP |
| 94 | Within / between / entry / exit firm productivity decomposition | %; pp | Firm census | IMNP |
| 95 | Misallocation (Olley-Pakes covariance, SD of log TFPR) | index | Firm census | IN |
| 96 | GBF technology sophistication; adoption drivers and barriers | 1–5; % of firms | FAT survey | IMP |
| 97 | Firms citing major obstacles | % of firms | Enterprise Surveys | MNP |
| 98 | Regulatory days and discretion (CV / SD) | days | Enterprise Surveys | IN |
| 99 | Exporter / foreign-ownership premia; exporter counts | log difference; count | Firm census, customs | INP |
| 100 | FDI spillover elasticities | log points | Firm census + input-output tables | NP |
| 101 | Days payable / days sales outstanding | days | OMTPME | M |
| 102 | WGI percentile ranks; policy-capture index | percentile | WGI | IN |
| 103 | Structural-reform z-scores | z-score | WDI, WGI, other | I |
| 104 | PMR scores (economy-wide, network sectors, professions, digital) | 0–6 | OECD / OECD-WBG PMR | IM |
| 105 | B-READY, BTI, EIU risk scores | various | B-READY, BTI, EIU | MN |
| 106 | Competition cases; restrictive provisions; merger thresholds | count; US$ m | Authority records, WB DB | IN |
| 107 | Electricity price | US¢/kWh; index | GlobalPetrolPrices, B-READY | P |
| 108 | Broadband / mobile price and speed | % of GNI pc; Mbps | ITU, Ookla | IP |
| 109 | LPI; QI4SD | score | WB LPI, UNIDO | N |
| 110 | Regional GDPpc, VA/worker, β-convergence | PPP $; coefficient | Regional accounts | NP |
| 111 | Climate damages by channel | % GDP deviation | CCDR model | K |
| 112 | Firm / job exposure to climate hazards; adaptation | % of firms or jobs | Weather data + firm census / LFS | MNP |

---

## 5. Data source inventory

"Mentions" are source tags; one item can carry several, so they sum to 956. Families are normalized from `by_source`.

| Source family | Mentions | Reports | Access | Python access | Caveats |
|---|---|---|---|---|---|
| World Bank staff calculations | 256 | 5 | n/a (transformation) | — | Not a source: always resolve to the underlying dataset |
| Firm census / admin firm data (PSA ASPBI/CPBI, OMTPME, DGI, NSO census; plus derived Dalvit, Begazo, Hsieh-Klenow, WBDD) | 89 + 15 derived | IMNP | Licensed or confidential microdata | Client-side only | Needs a secure enclave and disclosure control |
| Other (unspecified) | 103 | I, N | Mixed; often public (OECD PMR, OECD STRI, BOS, rich lists) | Case by case | Source normalization needed (~95 Indonesia tags) |
| Labour force surveys (microdata and published; HCP, SSO, PSA, BPS) | 69 | IMNP | Registration or licensed microdata; published aggregates are open | Client data; WB Global Labor Database (GLD) harmonized files where available | Series breaks; ILO modelled ≠ LFS |
| WDI (incl. WGI and GFDD via the WB API) | 52 (+8 WGI, +3 tagged "Findex") | 5 | Open API | `wbgapi`; World Bank Data360 API / data360 MCP where available | Vintage drift; PPP base (2017 vs 2021) |
| National accounts (NSO, incl. regional; KNBS, PSA, MakStat) | 44 | KMNP | Open download (varies) | Manual / CSV ingest | Rebasing; fiscal vs calendar year |
| MFMod / MPO (WB internal macro) | 35 | IMNP | Internal WB; some MFMod country models released for the open-source `modelflow` package | `modelflow` (limited countries) | Treat outputs as inputs |
| ICA 2.0 toolkit (incl. FDI linkages tool) | 30 | NP | Internal WB Stata toolkit applied to firm census | Re-implement methods | Method, not data |
| Enterprise Surveys | 25 | IMNP | Aggregate indicators open; microdata by registration | Download; `pandas.read_stata` | Survey-year comparability; weights |
| CGE (MANAGE, Indonesia CGE) | 22 | IP | Internal model | — | Outputs only |
| ILO (ILOSTAT + modelled estimates) | 18 | IKMNP | Open (SDMX, bulk) | `sdmx1` (ILO agency) or bulk CSV | Modelled estimates are revised annually |
| IMF (WEO, IFS, ICSD, staff reports) | 13 | KMNP | Open | `imfp`, `sdmx1`; WEO bulk files | IMF data portal / API changed in 2025: pin endpoints |
| National ministries / treasury / central bank (Kenya Treasury, CBK, Morocco MEF, Philippines BLGF and COA) | 11 | KMP | Open publications | Manual ingest | PDF tables; fiscal-year alignment |
| FAT survey | 10 | MP (+I tagged Other) | Not public | — | GBF index needs the codebook |
| WGI | 8 | IN | Open | `wbgapi` (source 3) | Percentile ranks are re-estimated each vintage |
| CNSS social-security admin data | 8 | M | Confidential | — | — |
| OECD / OECD-WBG PMR | 8 (+~14 Indonesia items tagged Other) | IM | Open download / OECD SDMX | `sdmx1` | Survey-year mismatch across countries |
| Customs / trade transaction microdata | 8 | NP | Confidential | — | — |
| Climate data & tools (SPEI, weather, Firms & Climate tool, CCDR) | 8 | KMNP | Open (weather) / internal (tools) | `xarray` + ERA5 / CHIRPS | Firm geocodes required |
| Bruegel / Darvas REER | 7 | KP | Open download | `pandas.read_excel` | Partner-basket choice changes levels (Kenya Fig 11 uses 120 partners) |
| MRIO (EORA, GLORIA) | 5 | MN | EORA by registration or licence; GLORIA open download | Custom loaders | Very large files |
| Penn World Table | 4 | IKNP | Open download | `pandas.read_stata` | Version changes (e.g., 10.01 vs later releases) shift TFP |
| UN WPP / national population projections | 4 | MN | Open API (free token) | REST | Projection variant |
| UNCTAD / Invest_Data | 4 | N | Open | Bulk / REST | FDI definitions differ from IFS |
| Entrepreneurship Database | 3 | MN | Open download | CSV | LLC-only coverage |
| B-READY / Doing Business | 3 | MP | Open download | CSV | Doing Business discontinued in 2021; B-READY is new |
| Atlas of Economic Complexity | 2 | KM | Open (Dataverse / API) | REST / GraphQL | ECI ranks shift with revisions |
| Conference Board TED | 2 | N | Free registration | Download | Licence terms |
| Other public indices (BTI, LPI, QI4SD, WB STRI, OECD FDIRI, UTAS/WITS, WB merger DB, TiSMoS, BOS DB, ITU, Ookla) | ~25 | NP (+I tagged Other) | Mostly open | `world_trade_data` (WITS), OECD SDMX, CSV | Irregular update years |
| EIU | 1 | N | Licensed | — | Exclude or require a user licence |
| CEM toolkit / Convergence tool / LTGM | 4 + 2 + 1 | MNP (+I LTGM) | Internal WB, except LTGM (public Excel) | Re-implement LTGM | — |

Supporting connector for derived trade metrics such as HHI: UN Comtrade via `comtradeapicall` (free key) or CEPII BACI.

---

## 6. Methods inventory

Families are grouped from `by_method`; counts sum to 536.

| Method family | Items | Key archetypes | Implementation notes |
|---|---|---|---|
| Ratio / share (incl. elasticities, ETR, tax wedge, perpetual inventory) | 107 | G02, F01, F05, X04–X06, S02, SP1 | Generic numerator/denominator transform with unit and base checks |
| Survey tabulation (LFS, ES, FAT, admin) | 69 | L01–L07, B06, B13 | Weighted tabulation with SEs; harmonized variable maps; minimum cell size |
| Simple series (levels, indices, rankings, counts) | 53 | G01, X01–X02, R04 | Rebase, period averages, rankings |
| Regression (local projections, event study, DiD, gravity, Mincer, AKM, fixed effects) | 50 | G10, C02, X11, X12, B12, L05, L07 | `linearmodels` / `pyfixest`; deterministic seeds; tidy coefficient output for coefficient plots |
| Benchmarking scatter (income-conditional / correlation) | 38 | G03, F05, X05, S01, R01, SP3 | Quadratic in log GDPpc or lowess; highlight country plus peers; label the trajectory years |
| CGE / macro-structural (MFMod, MANAGE, input-output distortion model) | 36 | G12, G13, S07 | Out of scope to run: ingest result tables against a schema |
| Benchmarking (composite index, group averages, distance to frontier, z-scores) | 35 | R01, R03, X09, B13 | Peer averages (structural / aspirational / OECD); z-score composites |
| Projection / simulation (LTGM, scenarios, gravity, tax-benefit microsimulation) | 32 | G11, L08, X05, F03 | LTGM re-implementation (public); scenario paths as inputs |
| Policy matrix / qualitative / conceptual | 29 | Z01, Z02 | Template rendering only |
| Distribution / concentration (kernel, Lorenz, HHI, CR4, bunching, dispersion) | 19 | B01, B04, B08, F05 | `scipy.stats.gaussian_kde`; concentration on firm or market data |
| TFP estimation (Levinsohn-Petrin / Ackerberg-Caves-Frazer, revenue- or quantity-based) | 16 | B02, B03, B06 | `prodest`-style Python port; sector-specific production functions |
| Shift-share / growth decomposition | 15 | S05, G08, SP1, SP3 | Within/between (static and dynamic); share-weighted contributions |
| Growth accounting / potential output | 13 | G04, G06 | Cobb-Douglas with α = 1/3 or labor share; Penn World Table human capital; HP filter or production-function potential |
| Growth rate | 10 | G01, S02 | Compound annual growth rate (CAGR), log differences, period averages |
| Firm productivity decomposition (Olley-Pakes, Melitz-Polanec) | 9 | B03 | Weights by employment or value added; entry/exit windows |
| Index decomposition | 5 | R03 | PMR and STRI sub-index trees |

---

## 7. Tool suite

### 7.1 MVP (20 tools): the public macro and cross-country index layer

| # | Tool | Type | Function | Archetypes |
|---|---|---|---|---|
| 1 | `wb_data` | Connector | WDI, WGI, GFDD and income classification history via `wbgapi` / Data360 | G01–G03, F05, X04–X05, S01, R01, L01, G14, G16 |
| 2 | `imf_data` | Connector | WEO, IFS, ICSD via SDMX / `imfp` | F01, F02, X01, G06, G07 |
| 3 | `ilo_data` | Connector | ILOSTAT modelled sectoral employment, LFPR, unemployment | S02, S03, S05, L01, G16 |
| 4 | `pwt_data` | Connector | Penn World Table (plus optional TED with a user key) | G04, G06 |
| 5 | `reer_data` | Connector | Bruegel REER | X02 |
| 6 | `pop_data` | Connector | UN WPP | L08 |
| 7 | `trade_data` | Connector | UNCTAD FDI, Atlas ECI, WITS/UTAS tariffs, Comtrade/BACI for HHI | X04, X06, X08 |
| 8 | `policy_index_data` | Connector | OECD SDMX (PMR, STRI, FDIRI), WB-OECD PMR, WB STRI, B-READY, BTI, LPI | R01, R03, X09, N01 |
| 9 | `national_table_ingest` | Connector | Schema-validated loader for NSO, ministry and central-bank tables (CSV/XLSX), with fiscal-year mapping | G08, F01, X01, S04, SP1 |
| 10 | `peer_set` | Transform | Structural/aspirational peers, income-group and regional aggregates, median/IQR bands | All benchmark archetypes |
| 11 | `harmonize` | Transform | Rebase, period averages, % of frontier, PPP vintage, unit conversion | All |
| 12 | `growth_accounting` | Method | Solow/human-capital decomposition, perpetual-inventory capital, return to capital, potential output | G04, G06 |
| 13 | `structural_change` | Method | Shift-share, sector contributions, VA/worker by sector | G08, S02–S05 |
| 14 | `benchmark_fit` | Method | Income-conditional fits, kernel densities, z-scores, distance to frontier | G03, S01, F05, X05, R01 |
| 15 | `chart_series` | Chart | Line or bar vs peers, IQR band, shaded episodes, dual axis | G01, G02, G06, L01, X01–X04 |
| 16 | `chart_decomposition` | Chart | Stacked bar with total marker | G04, G08, S05, X01, F01 |
| 17 | `chart_benchmark` | Chart | Scatter, bubble or connected trajectory with highlight and fit; slope/rank charts | G03, S01, S04, F05, X05, X06 |
| 18 | `standard_tables` | Chart | CGJR standardized macro and employment tables | G14, G16 |
| 19 | `caveat_check` | QA | Vintage and PPP footers, series breaks, ILO-modelled warnings, peer disclosure, title/source lint | All |
| 20 | `replicate_figure` | Replication | YAML spec (report, label, indicators, peers, period, method, chart) → data + PNG/SVG + diff vs digitized values + provenance | All |

`caveat_check` is justified by defects observed in the extractions:

- **Duplicated printed titles:** I Figs 1.5, 3.7, 3.8, 6.1.
- **Numbering errors:** I Chapter 4 Figs "2.23–2.25"; I Table 6.3 used twice.
- **In-chart mislabels:** I Figs 1.14, 3.10.
- **Source copy errors:** P Fig 5.3; P Fig 10 cites "Darvas" for wage employment.
- **Title–content mismatch:** N Fig 64a.
- **Unstated or incomplete sources:** M Fig 3-9b comparator source; P Fig 4.7b source line.

### 7.2 Next (about 12 tools)

| Tool | Archetypes unlocked |
|---|---|
| `es_indicators` connector (aggregates) + `es_microdata` tabulator | B13, B01, F05 (firm finance) |
| `pip_poverty` (PIP API) + growth incidence curve | P01 |
| `lfs_kit`: harmonized LFS tabulator (LFPR, NEET, mismatch, wage employment, Mincer, transitions) on client or GLD data | L01–L07, L13 |
| `firm_kit`: TFP (Levinsohn-Petrin / ACF), Olley-Pakes / Melitz-Polanec, misallocation, CR4/HHI/markups, quantile profiles, lifecycle, entry/exit, run on client data | B02–B08, X12, SP2 |
| `io_kit`: OECD ICIO/TiVA, GLORIA, EORA (services linkages, GVC participation, tariff incidence) | S06, X06, X08 |
| `regional_kit`: regional accounts + β-convergence | SP1, SP3 |
| `model_output_ingest`: MFMod/MANAGE/LTGM/CCDR result schema → charts | G11, G12, G13, C01 |
| `ltgm`: Python port of the public Long-Term Growth Model | G11 |
| `gravity`: PPML on CEPII gravity + BACI (export gaps, AVEs) | X05, X09 |
| `tax_wedge`: tax wedge / formalization-tax calculator | F03 |
| `policy_matrix`: roadmap template | Z02 |
| `chart_coef`: coefficient, event-study and impulse-response plots | G10, C02, X11, B12 |

### 7.3 Later

- Climate exposure (firm geocodes × ERA5/CHIRPS; C02).
- AI-exposure crosswalks (Felten; Pizzinelli/Cazzaniga; L10).
- FAT/GBF index module (B10).
- Distortion-elasticity model (Caliendo-Parro-Tsyvinski; S07).
- Competition-case coding (R04).
- Rich-list and listed-firm connectors (licensed; B09).
- Maps (RORO, SP3).
- EIU (licensed).

### 7.4 Estimated MVP coverage (estimate; arithmetic from the tagged items)

**Public ceiling (from the aggregates)**

- yes = 148 / 536 = **27.6%**
- yes + partly = 324 / 536 = **60.4%**

**What the MVP covers**

- The MVP serves 30 archetypes: G01–G04, G06–G08, G14, G16, P01, F01, F02, F05, X01, X02, X04–X06, X08, X09, S01–S05, L01, L08, R01, R03, N01.
- Those archetypes contain **203 items** (37.9% of 536): 113 yes, 72 partly, 14 no, 4 unclear.

**Estimated share replicable from public data**

| Scenario | Calculation | Share of all 536 items |
|---|---|---|
| Lower bound (yes only) | 113 / 536 | **21.1%** |
| Central (yes + half of partly) | (113 + 36) / 536 = 149 / 536 | **27.8%** |
| Upper bound (yes + all partly) | 185 / 536 | **34.5%** |

**Other ways to read it**

- The MVP captures **113 of the 148 fully public items (76%)**.
- By report, MVP "yes" items are N 29, K 28, I 27, M 18, P 11.
- Kenya alone: (28 + 7/2) / 40 ≈ **79%** central coverage.
- The "Next" tier adds 26 more "yes" items (SP1 8, S06 7, B13 6, B07 3, SP3 2), reaching **139 of 148 (94%)** of fully public items.

---

## 8. What cannot be replicated from public data

There are 190 "no" items and 22 "unclear" items, 212 in total (39.6%).

| Barrier | "No" items | Main archetypes | Handling |
|---|---|---|---|
| Firm census / tax / customs microdata (NSO restricted, DGI, OMTPME, PSA ASPBI/CPBI) | 104 (+32 partly) | B02–B06, B08, B11, B12, X11, X12, SP2, S02 (firm-based) | **Client-provided-data mode.** The deterministic `firm_kit` runs inside the client or NSO enclave. Only aggregates leave it, with a minimum cell size (n ≥ 3–5 firms) and dominance rules. A versioned input schema mirrors ICA 2.0 variables. |
| Model outputs (MFMod-SA, MANAGE, Indonesia CGE, LTGM runs, CCDR, CEM distortion tools) | 45 (+15 partly) | G11, G12, G13, C01, S07, G14 (projection tables) | **Model outputs as inputs.** A results schema (scenario × variable × year × deviation type) drives the charts. Re-run LTGM (public) and `modelflow` models where they have been released; otherwise out of scope. |
| LFS / census / social-security worker microdata | 21 (+25 partly) | L03–L07, L10, L13, C02 (Morocco) | `lfs_kit` on client-supplied or registered microdata (national LFS, GLD). Published aggregates (ILOSTAT, SSO) give partial substitutes for L01. |
| Proprietary surveys (FAT, HR experiment, Community Innovation Survey) | ~15 (already counted in the firm row) | B10, L07, B12 | Client data only; ship codebooks (GBF index) |
| Diagrams and policy matrices | 10 (+9 unclear) | Z01, Z02 | Not replication targets; template generator only |
| Licensed indices (EIU, rich lists, some MRIO) | few | R01, B09, S07 | Licensed connectors on the user's credentials, or excluded |

Separately, the extraction marked 72 MVP-archetype items as only "partly" replicable. The usual reasons are internal inputs (MPO potential growth, CEM toolkit capital series), national PDF-only tables, or staff adjustments. Each one needs a disclosed fallback, for example PWT instead of MPO capital stock. `caveat_check` should print that fallback on the chart.

---

## 9. Recommended first replication targets

All are rated "yes" (public), and together they cover 14 MVP archetypes.

| # | Report | Figure | Page | Archetype | Data needed | Why first |
|---|---|---|---|---|---|---|
| 1 | Kenya | Fig 1, Real GDP growth vs LMIC/UMIC/SSA, 2001–2024 | 10 | G01 | WDI | Simplest peer/income-group line; shaded episodes |
| 2 | North Macedonia | Fig 1, Average real GDP growth by period vs structural/aspirational peers | 20 | G01 | WDI, NSO | Tests `peer_set` group averages (2025 needs a forecast caveat) |
| 3 | Indonesia | Fig 1.1, Real GDP per capita % of USA, 1975–2022 | 23 | G02 | WDI | Distance to frontier, 9 named peers |
| 4 | Philippines | Fig 1, Catch-up rate vs initial relative income, 1990–2023 | 16 | G03 | WDI (PPP 2021) | Cross-country scatter with highlighted sub-period |
| 5 | Kenya | Fig 4, TFP, capital per worker, GDP per worker (2001 = 100) | 15 | G06 | PWT | Validates the PWT connector and rebasing |
| 6 | North Macedonia | Fig 4, Growth accounting vs peers by period | 21 | G04 | TED (registration) + NSO; PWT fallback | Core CGJR decomposition chart |
| 7 | Kenya | Fig 2, Sectoral contributions to real VA growth | 14 | G08 | KNBS via `national_table_ingest` (WDI fallback) | Stacked decomposition with total |
| 8 | Kenya | Fig 34, Employment shares by sector, 2001–2022 | 33 | S02 | ILO modelled + NSO | Feeds S03–S05 |
| 9 | Kenya | Fig 35, VA-per-worker growth decomposition, 2011–2019 | 33 | S05 | ILO + NSO | Built-in check value: total growth = 1.8% |
| 10 | Indonesia | Fig 2.1, Manufacturing VA share (indexed to peak) vs log GDPpc | 37 | S01 | WDI | Polynomial income-conditional curves |
| 11 | Kenya | Fig 14, Private credit % GDP vs GDPpc (PPP), 2023 | 19 | F05 | WDI | Canonical income-conditional scatter |
| 12 | Kenya | Fig 22, Exports % GDP vs GDPpc with Kenya 2011/2015/2024 | 25 | X05 | WDI | Scatter with trajectory and peer labels |
| 13 | North Macedonia | Fig 37, FDI inflows % GDP vs UMIC median/IQR, 1990–2024 | 36 | X04 | UNCTAD, WDI | IQR-band template (also used 12× in the Philippines) |
| 14 | North Macedonia | Figs 23a–b, WGI government effectiveness and control of corruption vs peers | 28 | R01 | WGI | Grouped horizontal bars, multi-year |
| 15 | Morocco | Fig 4-1a, Population pyramid (several years) | 65 | L08 | UN WPP | Distinct chart form; open API |

**Stretch targets**

- P Fig 1.17, unemployment vs MIC IQR (L01).
- I Table A.2 / N p96, standardized employment table (G16).
- M Fig 3-4, economy-wide PMR vs OECD (R03).
- K Fig 11, REER (X02).
- K Fig 7, fiscal accounts from WEO (F01).

Targets 1–15 exercise 16 of the 20 MVP tools. The stretch set adds the remaining four: `imf_data`, `reer_data`, `policy_index_data` and `standard_tables`.