# CGJR Replication Toolkit: First-Pass Inventory (October 2026)

**Scope.** This inventory covers the modules, indicators, charts, data sources and MCP tools needed to replicate the standard exhibits in World Bank Country Growth and Jobs Reports (CGJRs). It is a first pass built from public information and domain knowledge. The user's CGJR PDFs in Downloads were not available in this cloud environment, and worldbank.org could not be fetched directly. Every "verified" item therefore comes from web-search snippets.

**Status tags**
- **V** (VERIFIED): confirmed in search snippets for the cited source. Keys such as [S5] are listed at the end.
- **SP** (STANDARD PRACTICE): typical of Country Economic Memorandums (CEMs) and Jobs Diagnostics, but not yet seen in a CGJR.
- **U** (UNCERTAIN).

**ID scheme.** These are the join keys for the PDF figure log:
- **MOD-** module
- **IND-** indicator (`module.n`, for example 04.3)
- **CHT-** chart type
- **SRC-** data source
- **Tool name** (for example `growth_jobs_shapley`), which also serves as the method key

---

## 1. How a CGJR is structured

### 1.1 Confirmed facts

| Fact | Status |
|---|---|
| A core diagnostic launched in early 2025 that "puts jobs at the center." It identifies what holds back growth and what drives employment and job quality. | V [S1] |
| The "revamped and renamed" CEM. It sits alongside the CPSD 2.0 and the Poverty and Equity Assessment. | V [S2] |
| Diagnoses macro drivers of growth plus drivers of business performance. | V [S3] |
| Uses "policy-impact modeling tools." Ends in a **policy matrix** that supports Development Policy Operations (DPOs). | V [S1] |
| First 4 reports in FY25; about 20 planned for FY26. | V [S4] |
| A Cambodia document labels the product "CGJR / CEM3.0". An Open Learning Campus course covers a "CEM 2.0 Country Scan". The lineage CEM 2.0 → CGJR is inferred. | V that both labels appear [S12][S13]; lineage U |
| Philippines contents: Ch1 macro features (growth/jobs patterns, potential output); Ch2 micro features (productivity, "allocative challenge"); Ch3 spatial; Ch4 digital; Ch5 climate and firms; Ch6 policy roadmap; annex "Standardized Employment Table". | V [S5] |
| Morocco order: macro features → reform-linked projections → firm dynamics (job creation, productivity, allocation, wages). Released with the IFC CPSD. | V [S9] |
| No public CGJR methodology note was found. | U |

### 1.2 Confirmed CGJRs (incomplete list; use as replication targets)

| Country | Title | Date | Headline numbers to check | Status |
|---|---|---|---|---|
| Philippines | *Running Uphill* | Jul 2025 | >90% of growth since 2010 from capital, TFP <10%. Reforms: ~7% growth (6.8% in one source), 5.1M jobs, real wages +12.9% by 2040 | V [S5][S6] |
| Mauritania | *Beyond Extractives* | Nov 2025 | Labor force participation <50%; 5 priority reforms | V [S7] |
| Indonesia | *Running Faster, for Longer* | Jan 2026 | GDP up to +10%, 16.5M "better jobs" by 2040 | V [S8] |
| Morocco | *Scaling the Atlas* | Apr 2026 | 1.7M jobs by 2035; GDP ~20% above baseline; working-age population grew ~2.5x faster than jobs | V [S9]; a 2.5M figure's horizon is U |
| Zimbabwe | Growth and Jobs Report (exact title U) | Sep 2026 | GDP +10.7% (2030) and +26.9% (2040) vs baseline; up to 230k jobs; ~80% of workers informal | V [S10] |
| Seychelles | *Modernize, Utilize, Digitalize* | Sep 2026 | GDP +13% by 2040; 2,900 jobs plus the equivalent of 7,600 better-paying jobs; fiscal cost 0.7% of GDP a year for 3 years | V [S11] |

Two traps: the Seychelles report of March 2026 is a Country Climate and Development Report (CCDR), not a CGJR. Ghana's "Jobs and Growth Analysis" is not confirmed as published (U).

### 1.3 Modules

| ID | Module | Typical content | Status |
|---|---|---|---|
| MOD-01 | Growth performance and convergence | Growth vs peers, income gap, demand side, investment | V (PH, MA) |
| MOD-02 | Sources of growth | Growth accounting, TFP, potential output | V (PH) |
| MOD-03 | Growth–jobs link and demography | Shapley decomposition, working-age population vs jobs | V (MA, PH) |
| MOD-04 | Structural transformation | Sector shares, within- vs between-sector productivity, tradables | V (PH, ID) |
| MOD-05 | Labor markets, job quality, earnings | Participation, informality, wages, Standardized Employment Table | V (MR, MA, ZW, PH) |
| MOD-06 | Human capital and skills | HCI+, learning, skills mismatch | V theme (MR, SC, PH) |
| MOD-07 | Firms and dynamism | Size distribution, misallocation, job flows, competition | V (MA, ID, PH); possibly handed off to the CPSD (U) |
| MOD-08 | Trade, global value chains (GVCs), FDI | Diversification, complexity, GVCs | SP; V as a policy area (ID, SC) |
| MOD-09 | Infrastructure and digital | Power, logistics, connectivity, AI exposure | V theme (ZW, SC, PH) |
| MOD-10 | Macro-fiscal foundations | Stability, debt, tax, public investment | V theme (ZW, SC, MA) |
| MOD-11 | Regulation, institutions, finance | Regulation, labor code, governance, finance | V theme (MR, MA) |
| MOD-12 | Spatial and climate | Subnational gaps, firm climate exposure | V (PH); otherwise SP |
| MOD-13 | Reform simulation | Baseline vs reform paths for GDP, jobs, wages, debt | V (all six) |
| MOD-14 | Priorities and policy matrix | 3–5 pillars mapped to DPO prior actions | V [S1] |

**The modelling engine is unconfirmed.** MFMod-GJ, a macro-structural model with production, demographics and structural-change blocks, exists (V [S14]). That it drives MOD-13 is U: it is inferred only because its outputs match what CGJRs report. The Long Term Growth Model (LTGM) is a public Solow-Swan toolkit (V [S15]).

---

## 2. Indicator and chart inventory

**Chart types (CHT-)**

| ID | Chart | ID | Chart |
|---|---|---|---|
| 01 | Line or indexed line vs peers | 09 | Coefficient plot |
| 02 | Bars or ranked bars vs peers | 10 | Heatmap, treemap or transition matrix |
| 03 | Stacked contribution bars with total | 11 | Choropleth map |
| 04 | Scatter vs log GDP per capita with fit and peer highlights (bubble variant) | 12 | Scenario lines (baseline vs reform) |
| 05 | Stacked area or 100% bars | 13 | Waterfall |
| 06 | Marimekko | 14 | Population pyramid or life-cycle curve |
| 07 | Grouped bars (sex, age, education) | 15 | Formatted table |
| 08 | Density, box plot or histogram | | |

**Levels:** M = macro, S = sector, H = worker or household microdata, F = firm, R = subnational.

### MOD-01 Growth performance and convergence
| ID | Indicator | CHT | Lvl | Source | Tool | Status |
|---|---|---|---|---|---|---|
| 01.1 | Real GDP growth, period averages vs peers | 01/02 | M | WDI, WEO | `macro_descriptives` | V (PH vs Korea, Malaysia) [S5] |
| 01.2 | GDP per capita (PPP) relative to US | 01 | M | WDI, PWT | `macro_descriptives` | SP |
| 01.3 | Growth needed to reach high income by a target year | 02/12 | M | WDI | `ltgm_scenarios` | V (PH) [S5] |
| 01.4 | Growth episodes, volatility | 01 | M | WDI | `macro_descriptives` | SP |
| 01.5 | Demand-side contributions (C, G, I, NX) | 03 | M | National accounts | `macro_descriptives` | SP |
| 01.6 | Investment/GDP, public vs private | 02 | M | WDI, IMF | `macro_descriptives` | V (PH 17.1%→22.5%) [S5] |
| 01.7 | Savings, current account, net FDI (% GDP) | 01 | M | WDI, IMF | `macro_descriptives` | SP |
| 01.8 | Resource rents, commodity dependence | 05 | M | WDI | `macro_descriptives` | SP (MR theme V [S7]) |
| 01.9 | Real effective exchange rate | 01 | M | IMF, Bruegel | `macro_descriptives` | SP |

### MOD-02 Sources of growth
| ID | Indicator | CHT | Lvl | Source | Tool | Status |
|---|---|---|---|---|---|---|
| 02.1 | Growth split into capital, labor, human capital and TFP, by period | 03 | M | PWT, national accounts | `growth_accounting` | V (PH) [S5] |
| 02.2 | TFP level vs US and peers | 01 | M | PWT | `growth_accounting` | SP |
| 02.3 | Capital per worker; public vs private capital | 01 | M | PWT, IMF ICSD | `growth_accounting` | SP |
| 02.4 | Potential growth, output gap | 01/03 | M | Production function | `potential_output` | V (PH) [S5] |
| 02.5 | Baseline assumptions (TFP, investment/GDP, labor force) | 12 | M | Model | `ltgm_scenarios` | V (PH TFP 1.3%, I/GDP 22%) [S5] |
| 02.6 | TFP determinants (innovation, education, markets, infrastructure, institutions) | 02 | M | LTGM-TFP | `ltgm_scenarios` | U (tool exists [S15]) |

### MOD-03 Growth–jobs link and demography
| ID | Indicator | CHT | Lvl | Source | Tool | Status |
|---|---|---|---|---|---|---|
| 03.1 | Shapley split of GDP per capita growth: output per worker, employment rate, participation, working-age share | 03 | M | WDI, ILOSTAT, LFS | `growth_jobs_shapley` | V (method, JobStructure) [S17] |
| 03.2 | Sector contributions to productivity and employment growth | 03 | S | WDI, LFS | `growth_jobs_shapley` | V (method) [S17] |
| 03.3 | Working-age population vs employment growth | 01 | M | UN WPP, LFS | `demographic_jobs_gap` | V (MA) [S9] |
| 03.4 | Employment elasticity of growth | 02 | M/S | WDI, ILOSTAT | `employment_elasticity` | SP |
| 03.5 | Labor productivity vs real wage growth | 02 | M | National accounts, LFS | `macro_descriptives` | V (PH +44% vs +24%) [S6] |
| 03.6 | Age pyramid, dependency ratio, new labor-market entrants, jobs needed | 14/12 | M | WPP, LFS | `demographic_jobs_gap` | SP |
| 03.7 | Labor share, unit labor cost | 01 | M | PWT, national accounts | `macro_descriptives` | SP |

### MOD-04 Structural transformation
| ID | Indicator | CHT | Lvl | Source | Tool | Status |
|---|---|---|---|---|---|---|
| 04.1 | Value-added shares by sector | 05 | S | National accounts, ETD | `sector_productivity_decomp` | V (method) [S18] |
| 04.2 | Employment shares by sector | 05 | S | LFS, ILOSTAT | `sector_productivity_decomp` | V (method) [S18] |
| 04.3 | Relative labor productivity by sector | 06 | S | National accounts + LFS | `sector_productivity_decomp` | V indicator; chart SP |
| 04.4 | Within- vs between-sector productivity growth | 03 | S | ETD, national accounts + LFS | `sector_productivity_decomp` | V theme (PH); form U |
| 04.5 | Change in employment share vs relative productivity | 04 | S | ETD | `sector_productivity_decomp` | SP |
| 04.6 | Jobs created, tradable vs non-tradable | 02/03 | S | LFS | `sector_productivity_decomp` | V (PH 8.5M of 11.7M) [S6] |
| 04.7 | Manufacturing share vs GDP per capita | 04 | M/S | WDI | `benchmark_vs_income` | SP |
| 04.8 | Agricultural productivity, yields, agricultural productivity gap | 02 | S | FAOSTAT, USDA ERS | `benchmark_vs_income` | SP |
| 04.9 | Modern vs traditional services | 05 | S | National accounts, LFS | `sector_productivity_decomp` | SP |

### MOD-05 Labor markets, job quality, earnings
| ID | Indicator | CHT | Lvl | Source | Tool | Status |
|---|---|---|---|---|---|---|
| 05.1 | Labor force participation by sex vs peers | 01/02 | H | ILOSTAT, LFS, JOIN | `labor_indicators` | V (MA women; MR <50%) [S7][S9] |
| 05.2 | Participation by age and sex | 14 | H | LFS | `labor_indicators` | SP |
| 05.3 | Employment-to-population vs GDP per capita | 04 | H/M | ILOSTAT | `benchmark_vs_income` | SP |
| 05.4 | Unemployment and NEET by sex, age, education | 07 | H | LFS, ILOSTAT | `labor_indicators` | SP |
| 05.5 | Status in employment (wage, self-employed, employer, unpaid family) | 05 | H | LFS, JOIN | `labor_indicators` | SP |
| 05.6 | Wage-employment share vs GDP per capita | 04 | H/M | JOIN, ILOSTAT | `benchmark_vs_income` | SP |
| 05.7 | Informality by sector, sex, firm size | 07 | H | LFS | `classify_informality` → `labor_indicators` | V (ZW ~80%) [S10] |
| 05.8 | Hours worked, underemployment | 08 | H | LFS | `labor_indicators` | SP |
| 05.9 | Employment by education and occupation skill | 05 | H | LFS | `labor_indicators` | SP |
| 05.10 | Urban/rural and youth/adult splits | 07 | H/R | JOIN, LFS | `labor_indicators` | V (JOIN splits) [S18] |
| 05.11 | Public-sector employment share, public wage premium | 02 | H | WWBI, LFS | `earnings_regression` | SP |
| 05.12 | Gender gaps in job type and pay | 03/07 | H | LFS | `wage_gap_decomposition` | SP |
| 05.13 | Real earnings by sector | 01 | H | LFS | `labor_indicators` | V (PH +24%) [S6] |
| 05.14 | Returns to education, formality, sector | 09 | H | LFS | `earnings_regression` | SP |
| 05.15 | Earnings distribution, low-pay share | 08 | H | LFS | `labor_indicators` | SP |
| 05.16 | Poverty, working poor | 01 | H | PIP, ILOSTAT | `poverty_growth` | SP |
| 05.17 | Employment under 13th vs 19th ICLS definitions | 13 | H | LFS | `classify_employment_icls` | SP |
| 05.18 | Labor-market transitions | 10 | H | Panel LFS | `labor_indicators` (panel mode) | U |
| 05.19 | Migration, remittances (% GDP) | 01 | M | WDI, KNOMAD | `macro_descriptives` | SP |
| 05.20 | Standardized Employment Table | 15 | H | Harmonized LFS | `labor_indicators` | V exists (PH) [S5]; contents U |

### MOD-06 Human capital and skills
| ID | Indicator | CHT | Lvl | Source | Tool | Status |
|---|---|---|---|---|---|---|
| 06.1 | HCI+ vs GDP per capita | 04 | M | WDI (HD_HCIP_*) | `benchmark_vs_income` | SP (index V [S38]) |
| 06.2 | Learning outcomes, learning poverty | 02 | M | HLO, PISA, UIS | `benchmark_vs_income` | SP |
| 06.3 | Workforce educational attainment and projections | 05 | H/M | LFS, Wittgenstein Centre | `labor_indicators` | SP |
| 06.4 | Skills mismatch | 07 | H | LFS, STEP | `labor_indicators` | V theme (PH) [S5] |

### MOD-07 Firms and dynamism
| ID | Indicator | CHT | Lvl | Source | Tool | Status |
|---|---|---|---|---|---|---|
| 07.1 | Firm-size distribution (firms and jobs), "missing middle" | 02 | F | Census, registry, WBES | `firm_dynamics` | SP |
| 07.2 | Productivity dispersion (P90/P10) | 08 | F | Census, WBES | `firm_productivity` | SP |
| 07.3 | Allocative efficiency by sector | 02/03 | F | Census panel | `misallocation_decomp` | V theme (PH) [S5]; method U |
| 07.4 | Dynamic Olley-Pakes decomposition (within, between, entry, exit) | 03 | F | Firm panel | `misallocation_decomp` | SP |
| 07.5 | Firm TFP and markups over time | 01 | F | Census, Orbis | `firm_productivity` | V (Georgia CEM) [S20] |
| 07.6 | Firm entry and exit rates, business density | 01 | F | Registry, Entrepreneurship DB | `firm_dynamics` | SP |
| 07.7 | Job creation and destruction by firm size and age | 03 | F | Registry, social security | `firm_dynamics` | V theme (MA) [S9] |
| 07.8 | Exporter, FDI and ownership premia | 09 | F | WBES, census | `firm_productivity` | SP |
| 07.9 | Top business constraints vs peers | 02 | F | WBES | `firm_benchmark_wbes` | V (tool) [S19] |
| 07.10 | Access to finance; private credit/GDP | 02 | F/M | WBES, GFDD | `firm_benchmark_wbes` | SP |
| 07.11 | Technology adoption | 02 | F | FAT, WBES | `firm_benchmark_wbes` | V (PH) [S5] |
| 07.12 | Market concentration, SOE presence, product-market regulation | 02 | S/F | Census, BOS, PMR | `competition_indicators` | V theme (ID, MA) [S8][S9] |
| 07.13 | Firm wages vs productivity | 04 | F | Census, admin data | `firm_productivity` | V theme (MA) [S9] |

### MOD-08 Trade, GVCs, FDI
| ID | Indicator | CHT | Lvl | Source | Tool | Status |
|---|---|---|---|---|---|---|
| 08.1 | Exports/GDP vs GDP per capita | 04 | M | WDI | `benchmark_vs_income` | SP |
| 08.2 | Export composition | 05/10 | S | BACI | `trade_diversification` | SP |
| 08.3 | Concentration (HHI, Theil); number of products and markets | 01 | M | BACI | `trade_diversification` | SP |
| 08.4 | Revealed comparative advantage (RCA) by product | 10 | S | BACI | `trade_complexity` | SP |
| 08.5 | Complexity and product space | 04 | S | Atlas | `trade_complexity` | SP |
| 08.6 | Services exports | 01 | M | WDI | `macro_descriptives` | SP |
| 08.7 | Backward and forward GVC participation | 03 | S | TiVA, Eora | `gvc_participation` | SP |
| 08.8 | FDI inflows, total and by sector | 01/02 | M/S | UNCTAD | `macro_descriptives` | SP |
| 08.9 | Tariffs, non-tariff measures, FDI and services restrictiveness | 02 | M | WITS, OECD | `benchmark_vs_income` | SP |
| 08.10 | Number of exporters, entry, survival | 01 | F | Customs, Exporter Dynamics Database | `exporter_dynamics` | V (PH decline) [S6] |

### MOD-09 Infrastructure and digital
| ID | Indicator | CHT | Lvl | Source | Tool | Status |
|---|---|---|---|---|---|---|
| 09.1 | Electricity access, outages, cost of power shortages | 02 | M/F | WDI, WBES | `benchmark_vs_income` | V theme (ZW 6.1% of GDP) [S10] |
| 09.2 | Logistics, ports, travel time | 02 | M | LPI, CPPI | `benchmark_vs_income` | SP |
| 09.3 | Internet use, price, speed; digital payments | 02 | M/H | ITU, Ookla, Findex | `benchmark_vs_income` | SP |
| 09.4 | Occupational AI and digital exposure | 05 | H | LFS + ILO index | `digital_ai_exposure` | V (PH) [S5]; method U |

### MOD-10 Macro-fiscal foundations
| ID | Indicator | CHT | Lvl | Source | Tool | Status |
|---|---|---|---|---|---|---|
| 10.1 | Inflation, fiscal balance, public debt | 01 | M | WEO, MPO | `macro_descriptives` | SP (theme V: ZW, SC) |
| 10.2 | Debt composition, arrears, debt sustainability risk | 05 | M | IDS, DSA | `macro_descriptives` | SP |
| 10.3 | Tax/GDP, revenue mix, tax expenditures | 02 | M | GRD, GFS | `benchmark_vs_income` | SP |
| 10.4 | Spending mix (education, health, capex, wage bill, subsidies) | 05 | M | BOOST, GFS | `macro_descriptives` | SP |
| 10.5 | Public investment efficiency | 04 | M | IMF PIMA | `benchmark_vs_income` | V theme (MA) [S9]; form U |

### MOD-11 Regulation, institutions, finance
| ID | Indicator | CHT | Lvl | Source | Tool | Status |
|---|---|---|---|---|---|---|
| 11.1 | Governance, business regulation, legal gender gaps | 02 | M | WGI, B-READY, WBL | `benchmark_vs_income` | SP |
| 11.2 | Minimum wage/median wage; employment protection | 02 | M/H | ILO, LFS | `labor_indicators` | SP (MR labor code V [S7]) |
| 11.3 | Social protection and pension coverage | 02 | H | ASPIRE | `benchmark_vs_income` | SP |
| 11.4 | Financial depth, non-performing loans, bank claims on government | 01 | M | GFDD, IMF FAS | `macro_descriptives` | SP |

### MOD-12 Spatial and climate
| ID | Indicator | CHT | Lvl | Source | Tool | Status |
|---|---|---|---|---|---|---|
| 12.1 | Subnational GDP per capita, jobs, productivity, nightlights | 11 | R | Regional accounts, DOSE, Black Marble | `spatial_indicators` | V (PH) [S5] |
| 12.2 | Urbanization vs GDP per capita | 04 | R/M | WDI, GHSL | `benchmark_vs_income` | SP |
| 12.3 | Firm climate exposure and losses | 02/11 | F/R | WBES, EM-DAT | `spatial_indicators` | V (PH) [S5] |
| 12.4 | Heat-stress hours lost; green/brown jobs | 02 | H/S | ILO, O*NET crosswalk | `digital_ai_exposure` | U |

### MOD-13 Reform simulation
| ID | Indicator | CHT | Lvl | Source | Tool | Status |
|---|---|---|---|---|---|---|
| 13.1 | Baseline vs reform GDP growth | 12 | M | Supplied model output | `scenario_compare` | V (PH) [S6] |
| 13.2 | GDP level vs baseline (%) | 12 | M | Model output | `scenario_compare` | V (MA, ZW, SC) [S9][S10][S11] |
| 13.3 | Additional jobs by horizon | 02 | M | Model output | `scenario_compare` | V (PH, MA, ZW, SC) |
| 13.4 | "Better jobs" / job-equivalents of wage gains | 02 | M | Model output | `scenario_compare` | V reported (ID, SC); definition U |
| 13.5 | Real wage effect by sector or skill | 02 | S | Model output | `scenario_compare` | V (PH, SC) |
| 13.6 | Growth gain by reform pillar | 13 | M | Model output | `scenario_compare` | V partial (PH) [S5] |
| 13.7 | Fiscal cost and debt path | 12 | M | Model output | `fiscal_reform_cost` | V (SC) [S11] |

### MOD-14 Priorities and policy matrix
| ID | Indicator | CHT | Lvl | Source | Tool | Status |
|---|---|---|---|---|---|---|
| 14.1 | Policy matrix (pillar → constraint → reform → timing → results indicator) | 15 | — | Report | `policy_matrix` | V [S1] |
| 14.2 | Constraint ranking or evidence heatmap | 10 | — | Synthesis | `policy_matrix` | U |

**Totals:** 104 indicators. By first tag: 42 V, 58 SP, 4 U. Most V items confirm a theme or a published number, not the chart form.

---

## 3. Data source inventory

| ID | Source | Provides | Access | Format/API | Frequency | Caveats | Status |
|---|---|---|---|---|---|---|---|
| SRC-01 | WDI / Data360 | GDP, sectors, ILO-modelled jobs, trade, demography; WBES, HCI+, WGI series | Open | `wbgapi`; Data360 REST; `data360-mcp` | Several updates a year | Mix of modelled and observed series; pin the vintage | V [S21][S22] |
| SRC-02 | IMF WEO | Macro, fiscal, 5-year forecasts | Open | data.imf.org SDMX 3.0 | Apr, Oct | Vintages differ | V [S23] |
| SRC-03 | IMF topical datasets (formerly IFS), BOP, GFS, ICSD | Prices, BOP, fiscal, public/private capital | Open | SDMX 3.0 | Continuous | Legacy IFS retired Nov 2025; old codes need mapping | V [S23] |
| SRC-04 | National accounts (statistics office, UNSD) | GDP by production and expenditure, deflators, GFCF | Open | Excel/PDF | Annual, quarterly | Breaks when national accounts are rebased | SP |
| SRC-05 | Macro Poverty Outlook | Projections | Open, CC BY | CSV | Twice a year | Built on internal models | V [S24] |
| SRC-06 | Penn World Table 11.0 | PPP GDP, capital, human capital (`hc`), labor share, TFP | Open | Excel/Stata | Oct 2025 (data to 2023, 2021 PPPs) | Lag; labor share imputed for many low-income countries | V [S25] |
| SRC-07 | GGDC/UNU-WIDER ETD | 12-sector value added and employment | Open, CC BY | Excel | 2023: 51 countries, to 2018 | Ends 2018 | V [S26] |
| SRC-08 | Barro-Lee; Wittgenstein Centre | Attainment, projections | Open | CSV | Barro-Lee ends 2015 | Use Wittgenstein for recent years | SP |
| SRC-09 | ILOSTAT / ILOEST | Jobs by sector, status, occupation; unemployment; informality; earnings | Open | Bulk CSV, SDMX; `Rilostat` | ILOEST each November | Modelled years get revised | V [S27][S50] |
| SRC-10 | Global Labor Database (GLD) | Harmonized LFS microdata and code | Code open; data partly licensed | Stata, GitHub | Rolling | Uneven coverage | V [S28] |
| SRC-11 | JOIN | 100+ labor indicators | Open, CC BY | CSV | Irregular | Coverage counts differ (164 vs 168 countries) | V [S29] |
| SRC-12 | LFS and household surveys; IPUMS-International | Worker-level microdata; census microdata | Login or licence | NADA API; Stata | Per survey | Redistribution limits; reweighting | V [S30]; IPUMS SP |
| SRC-13 | Worldwide Bureaucracy Indicators (WWBI) | Public employment and wages | Open | CSV | Irregular | Survey-based | SP |
| SRC-14 | PIP | Poverty, Gini, nowcasts | Open | API; `pipr` | Jun 2025: 2021 PPPs; poverty lines $3.00/$4.20/$8.30 | Do not mix PPP vintages | V [S31][S49] |
| SRC-15 | UN WPP 2024 | Population by age and sex; projections | Open | CSV | Every 2 years | Differs from censuses | V [S37] |
| SRC-16 | HCI+ | Human capital, 0–325 scale | Open | WDI HD_HCIP_* | Feb 2026 | Not comparable with the old HCI | V [S38] |
| SRC-17 | HLO, UIS | Learning, enrolment | Open | CSV, API | Annual | Comparability across tests | SP |
| SRC-18 | Enterprise Surveys (WBES) | Firm sales, jobs, constraints, TFP | Indicators open; microdata licensed | Stata | Every 3–5 years per country | Formal firms with 5+ workers only | V [S32] |
| SRC-19 | Firm census, registry, tax, social-security records | Entry/exit, job flows, misallocation | Restricted | Client-supplied | Annual | Firm ID breaks | SP |
| SRC-20 | ORBIS; UNIDO INDSTAT | Firm accounts; manufacturing | Licensed; partly free | Varies | Annual | Large-firm bias | SP/U |
| SRC-21 | Entrepreneurship DB; Informal Economy DB | Business entry density; informal output share | Open | Excel | Irregular | Model-based | SP |
| SRC-22 | UN Comtrade | HS bilateral trade | Key for full access | `comtradeapicall` | Monthly | Non-reporting countries | V [S33] |
| SRC-23 | CEPII BACI | Reconciled HS6 flows | Open | CSV v202601 | Annual | ~2-year lag | V [S34] |
| SRC-24 | Atlas of Economic Complexity | ECI, product space | Open | GraphQL; Dataverse | Annual | Thin services coverage | V [S35] |
| SRC-25 | WITS/TRAINS | Tariffs, non-tariff measures | Open | Web, API | Annual | — | SP |
| SRC-26 | OECD TiVA/ICIO; Eora; ADB MRIO | Input-output tables | Open, partly licensed | CSV | Irregular | Low-income countries missing | SP |
| SRC-27 | UNCTAD FDI; fDi Markets | FDI flows, greenfield projects | Open; licensed | CSV | Annual (WIR Jul 2026) | Conduit flows | V [S36] |
| SRC-28 | Exporter Dynamics Database; customs microdata | Exporter counts, entry, survival | Open; restricted | CSV | Irregular | Update status U | SP/U |
| SRC-29 | WGI | Governance | Open | WDI GOV_WGI_* | Dec 2025 revision | New 0–100 scale | V [S39] |
| SRC-30 | B-READY | Business regulation | Open | Excel | Annual | Partial coverage | V [S40] |
| SRC-31 | WBL, PMR, FDI Restrictiveness Index, Businesses of the State (BOS), services trade policy | Legal gender gaps, regulation, SOEs | Open | Excel | Irregular | Coverage varies | SP |
| SRC-32 | Global Findex 2025 | Financial inclusion | Open | CSV, microdata | About every 3 years | Few data points over time | V [S41] |
| SRC-33 | GFDD; IMF FAS | Financial depth, access | Open | CSV, SDMX | Annual | — | SP |
| SRC-34 | ITU DataHub; Ookla | Connectivity, price, speed | Open | CSV | Annual | Many values estimated | V [S42] (ITU) |
| SRC-35 | LPI, CPPI, ASPIRE, KNOMAD, FAOSTAT, USDA ERS | Logistics, social protection, migration, agriculture | Open | CSV | Irregular to annual | — | SP |
| SRC-36 | IDS, DSA, BOOST, GRD, PIMA | Debt, spending, revenue, public investment management | Open (BOOST partial) | CSV, Excel | Annual | PIMA is report-based | SP |
| SRC-37 | Black Marble, GHSL, WorldPop, DOSE, Global Data Lab | Nightlights, built-up area, population, subnational GDP and HDI | Open (NASA token) | `blackmarblepy`; raster | Daily to annual | Blooming; weak for farm areas | V [S43]; rest SP |
| SRC-38 | EM-DAT, CCKP, ILO heat-stress and AI-exposure indices | Climate and exposure | Open | CSV | Annual | Exposure methods differ | SP |
| SRC-39 | CGJR annex scenario tables / MFMod-GJ runs | Baseline and reform paths | User-supplied (model is internal to the Bank) | Excel | Per report | Engine not public | U |

---

## 4. Tool suite (MCP tools, Python)

**Shared contract for every tool**
- **Input:** a long panel with fields `iso3, year, dim, variable, value, unit, source, vintage, obs_flag (observed/modelled/imputed)`.
- **Output:** `{table, chart_spec, assumptions, provenance, warnings}`.
- **Determinism:** seeds are fixed and every parameter is explicit.
- **Licensed data:** the tool returns aggregates only.

| Tool | Purpose | Inputs | Outputs | Method | Build on | Priority |
|---|---|---|---|---|---|---|
| **Connectors** | | | | | | |
| `fetch_wdi` | WDI/Data360 series | iso3, codes, years | Panel + provenance | API | `wbgapi`, `data360-mcp` | MVP |
| `fetch_imf` | WEO, GFS, BOP, ICSD | Dataset, codes, vintage | Panel | SDMX 3.0 | `sdmx1` (check), httpx | MVP |
| `fetch_ilostat` | Labor aggregates | Indicator, breakdowns | Panel with modelled flag | Bulk CSV/SDMX | pandas | MVP |
| `fetch_pwt` | PWT 11.0 | iso3, variables | Panel | Cached file | pandas | MVP |
| `fetch_un_wpp` | Population, projections | iso3, variant | Panel | Cached CSV | pandas | Next |
| `fetch_etd` | Sector value added and employment | iso3 | Panel | Cached file | pandas | Next |
| `fetch_pip` | Poverty | iso3, line, PPP year | Panel | API | httpx | Next |
| `fetch_trade` | BACI, Comtrade, Atlas, WITS | Reporter, HS level, years | Flows | CSV/API/GraphQL | `comtradeapicall`, gql | Next |
| `fetch_catalog` | WGI, B-READY, WBL, WWBI, ASPIRE, LPI, GFDD, IDS, ITU, PMR | Source key, iso3 | Panel | Loader registry | pandas | Next |
| `fetch_spatial` | Nightlights, GHSL, boundaries | iso3, admin level | Zonal table | Zonal statistics | `blackmarblepy`, rasterio, geopandas | Later |
| `load_user_data` | Client national accounts, LFS, firm panels, customs data, scenario tables | File, schema | Validated data | Schema check | pandera, pyreadstat | MVP |
| **Preparation** | | | | | | |
| `harmonize_panel` | Units, deflation, PPP, splicing, annualization | Panel, base year, PPP vintage | Harmonized panel | Ratio splice, chain-linking | pandas | MVP |
| `concord_codes` | ISIC/ETD/LFS sectors, tradables flag; HS↔SITC; ISCO; ICSE-93↔18 | Codes | Mapped table | Lookups | pandas | MVP (sector subset) |
| `select_peers` | Regional, structural and aspirational peers | iso3, criteria | Peers + reasons | k-nearest neighbours | scikit-learn | MVP |
| **Microdata** | | | | | | |
| `lfs_harmonize` | Raw LFS to GLD-style schema | Microdata, map | Harmonized file | Rule mapping | GLD code | Next |
| `labor_indicators` | Rates by group; Standardized Employment Table; transitions | Microdata or aggregates, definitions | Table with SEs | Weighted ratios, linearization | samplics (check), statsmodels | MVP |
| `classify_employment_icls` | Employment under 13th vs 19th ICLS | Microdata | Two series + bridge | Rules | pandas | Next |
| `classify_informality` | National, ILO or 21st ICLS informality rules | Microdata, rule set | Flag | Rules [S44] | pandas | Next |
| `earnings_regression` | Mincer, quantile, Heckman; public wage premium | Microdata, spec | Coefficients | Weighted least squares, clustered SEs | statsmodels, pyfixest | Next |
| `wage_gap_decomposition` | Gender and formal/informal pay gaps | Microdata | Explained vs unexplained | Oaxaca-Blinder, RIF | `oaxaca` [S45] | Later |
| `poverty_growth` | Growth incidence curves, poverty decompositions | PIP or survey data | Tables | Datt-Ravallion, Shapley | numpy | Later |
| **Macro** | | | | | | |
| `macro_descriptives` | Growth, period averages, demand contributions, ratios | Panel, periods | Tables | Identities | pandas | MVP |
| `growth_accounting` | Capital, labor, human capital and TFP shares | GDP, investment, employment, schooling, α, δ | Contributions | Perpetual inventory; Mincer `hc` | numpy | MVP |
| `potential_output` | Potential growth, output gap | Growth-accounting output | Series | Production function, HP filter | statsmodels | Next |
| `ltgm_scenarios` | Solow-Swan paths; growth needed | Investment, TFP, human capital, WPP | Paths | LTGM replica (labelled approximate) | numpy | Next |
| `scenario_compare` | Charts from supplied scenarios | Baseline and reform tables | Deviations, jobs, pillar split | Differencing | pandas | MVP |
| `fiscal_reform_cost` | Debt path under reform costs | Fiscal paths, r, g | Trajectories | Debt dynamics | numpy | Later |
| **Decompositions** | | | | | | |
| `growth_jobs_shapley` | Split of GDP per capita growth, including by sector | Value added, employment, population by age | Contributions | Shapley (JobStructure replica) [S17] | numpy | MVP |
| `sector_productivity_decomp` | Sector shares, relative productivity, within/between | Sector value added + employment | Decomposition | Shift-share (variant is a parameter) | pandas | MVP |
| `employment_elasticity` | Jobs response to growth | GDP, employment | Elasticities | Arc and regression | statsmodels | Next |
| `demographic_jobs_gap` | Working-age population vs jobs, entrants, jobs needed | WPP, employment rates | Series | Rate × projection | pandas | Next |
| **Firms** | | | | | | |
| `firm_benchmark_wbes` | Constraints, finance, technology vs peers | WBES data | Ranked data | Weighted means | pandas | Next |
| `firm_dynamics` | Size distribution, job flows, entry and exit | Firm panel | Job-flow table | Davis-Haltiwanger-Schuh | pandas | Next |
| `firm_productivity` | TFP, dispersion, premia, wages | Firm panel | Estimates | OLS/FE, Olley-Pakes, Levinsohn-Petrin, Ackerberg-Caves-Frazer | scipy (port `prodest` [S46]) | Later |
| `misallocation_decomp` | Olley-Pakes covariance, Melitz-Polanec, Hsieh-Klenow | Firm panel + TFP | Decomposition | Published formulas | numpy | Later |
| `competition_indicators` | Concentration, SOE share, markups | Census, BOS | Indices | HHI, CR4, De Loecker-Warzynski | pandas | Later |
| **Trade** | | | | | | |
| `trade_diversification` | Composition, HHI/Theil, margins | HS6 flows | Indices | Cadot et al. | pandas | Next |
| `trade_complexity` | RCA, ECI/PCI, density | HS6 flows | Indices | Hidalgo-Hausmann | `ecomplexity` [S47] | Next |
| `gvc_participation` | Backward and forward participation | Input-output tables | Shares | Borin-Mancini | pymrio; port `exvatools` [S48] | Later |
| `exporter_dynamics` | Exporter counts and survival | Customs data, EDD | Series | Cohorts | pandas | Later |
| **Benchmarking** | | | | | | |
| `benchmark_vs_income` | Indicator vs log GDP per capita; income-aligned peer comparisons | Panel, peers | Fit, residuals | OLS, lowess | statsmodels | MVP |
| **Spatial and digital** | | | | | | |
| `spatial_indicators` | Subnational aggregates, map data | Regional data, rasters | Geo table | Zonal statistics | geopandas | Later |
| `digital_ai_exposure` | AI, digital and green task exposure | LFS occupations, exposure index | Shares | Crosswalk | pandas | Later |
| **Quality and validation** | | | | | | |
| `caveat_guard` | Blocking checks (section 6) | Panel + metadata | Pass, warn or block | Rule engine | pandera | MVP |
| `replicate_figure` | Compare tool output with published values | Output, reference values, tolerance | Pass/fail report | Tolerance tests | pandas | MVP |
| `figure_inventory` | Extract figures, captions and source notes from PDFs | PDF | Figure log | Caption parsing | PyMuPDF, pdfplumber | Next (run first) |
| `digitize_chart` | Read values off chart images | Image, axis calibration | Values | Semi-automatic | opencv | Next |
| **Output** | | | | | | |
| `render_chart` | All CHT types in house style, source note composed automatically | chart_spec, table | SVG/PNG + CSV | Declarative spec | altair (Vega-Lite), matplotlib | MVP |
| `policy_matrix` | Policy matrix | Pillars, constraints, figure IDs, reforms, indicators | Table | Schema (section 8) | pydantic | Next |
| `build_workbench` | Country pack: all modules, charts, provenance log | iso3, data manifest | Folder, Excel, notebook | Orchestration | papermill, openpyxl | Next |

**Count:** 50 tools: 18 MVP, 21 Next, 11 Later.

---

## 5. MVP set

| # | Tool | Covers | Why it is in the MVP |
|---|---|---|---|
| 1 | `fetch_wdi` | Most of MOD-01, 04, 06, 09–11 | One API holds about half of all series |
| 2 | `fetch_imf` | 01.6–01.9, 10.1 | Forecast and fiscal panels |
| 3 | `fetch_ilostat` | 03.1, 04.2, 05.1–05.6 | Labor benchmarks across countries |
| 4 | `fetch_pwt` | MOD-02 | Capital, human capital, TFP |
| 5 | `load_user_data` | MOD-05, 07, 13 | Microdata and scenario tables always come from the client |
| 6 | `harmonize_panel` | All | Deflators, PPP, splicing |
| 7 | `concord_codes` (sectors only) | MOD-04, 05 | Consistent sector mappings |
| 8 | `select_peers` | All peer charts | Nearly every figure compares the country with peers |
| 9 | `caveat_guard` | All | Blocks invalid output |
| 10 | `macro_descriptives` | MOD-01, 10; parts of 03, 08 | Largest block of figures (SP) |
| 11 | `growth_accounting` | MOD-02 | Confirmed headline analysis (PH) |
| 12 | `growth_jobs_shapley` | 03.1–03.2 | Core of the "productivity and employment" framing |
| 13 | `sector_productivity_decomp` | MOD-04 | Confirmed in PH and ID |
| 14 | `labor_indicators` | MOD-05, 06.3–06.4, 05.20 | The jobs core; the Standardized Employment Table is likely a common template |
| 15 | `benchmark_vs_income` | About 25 bar and scatter indicators | One method covers many simple charts |
| 16 | `scenario_compare` | MOD-13 | Every CGJR reports reform paths |
| 17 | `render_chart` | CHT 01–10, 12–15 | Charts and tables |
| 18 | `replicate_figure` | — | Measures success |

**Estimated coverage.** These are estimates, to be replaced by the PDF count:
- About **55–65% of figures**, if harmonized LFS microdata and annex scenario tables are supplied.
- About **40–45%** with open data only.

**Rationale (SP).** In CEMs and Jobs Diagnostics, most exhibits are macro, growth, growth–jobs, structural-transformation and labor figures. Simple peer bars and scatters cover much of the human capital, infrastructure and institutions content. Firm chapters (which may sit in the CPSD), complexity, GVCs and maps vary by country and often rely on restricted data, so they come in Next and Later.

---

## 6. Caveats the tools must enforce

`caveat_guard` blocks on the following, rather than only warning:

1. Never splice ILO-modelled and survey series. Flag modelled years.
2. Never mix 2017- and 2021-PPP data (PWT, PIP, WDI).
3. Detect and mark series breaks: national-accounts rebasing, survey redesign, census reweighting, and the switch from 13th to 19th ICLS.
4. Require declared definitions: working age (15+ or 15–64), unemployment, informality rule, employment standard.
5. Vintage traps: WGI rescaling, HCI replaced by HCI+, IFS breakup, B-READY coverage, PIP poverty lines.
6. Warn on 2020–21 endpoints. Periods are fixed before results are seen.
7. Productivity vs wages: show both GDP-deflator and CPI versions.
8. The peer set is fixed and logged before analysis.
9. Sensitivity reporting: a band of α values, the shift-share variant, the RCA threshold, Heckman without an exclusion restriction, and Hsieh-Klenow measurement error.
10. No licensed microdata in outputs. Small cells are suppressed.
11. LTGM-style outputs are labelled as approximations, never as "the CGJR model".

---

## 7. Validation plan against real CGJR PDFs

**1. Figure log.** Run `figure_inventory`, then review by hand. Log every figure, table, box and annex exhibit in each CGJR: the user's Downloads set plus the six reports in 1.2.

| Field | Purpose |
|---|---|
| report_id, chapter, figure number, page | Key |
| Title and subtitle (verbatim) | Assign the IND |
| "Source:" note (verbatim) | Map to SRC; check the data inventory |
| "Note:" text (definitions, periods) | Feeds `caveat_guard` |
| CHT, IND (one or more), MOD | Mapping |
| Countries/peers, years, units | Replication spec |
| Data access: open / licensed / internal model | Feasibility |
| Status: existing IND / new IND / out of scope | Gap list |

**2. Review.** One partner codes the figures. The other re-codes a random 20%, and the two reconcile.

**3. Coverage matrix.** Compute the share of figures mapped to an IND and tool, by module and by report. Add new IND rows for gaps. Upgrade SP to V with a citation such as "PDF PH-2025 p.X".

**4. Pilots.** Philippines first (most public detail, plus the Standardized Employment Table annex), then Morocco (firm dynamics and the CPSD handoff), then Indonesia. About 10 figures per report, spread across modules.

**5. Replication grades.**

| Grade | Meaning |
|---|---|
| R0 | Not attempted, or the data is unavailable (record why) |
| R1 | Same indicator and chart type; numbers not checked |
| R2 | Same source, same or nearest documented vintage, same method; numbers within tolerance |
| R3 | R2, plus the same peers, periods and layout. This is what **"replicated"** means |

**Tolerances**
- Rates and shares: ±0.2 pp
- Growth contributions: ±0.2 pp a year
- Levels: ±2%
- Peer rankings: same order
- Decomposition identities: must sum exactly
- Digitized values: ±2% of the axis range

Reference values come from annex tables, figures quoted in the text, or `digitize_chart`.

**6. Outputs.** A coverage dashboard, a tool backlog, and regression tests: each replicated figure becomes a permanent `replicate_figure` test.

---

## 8. Open questions

1. **Reform engine.** Is it MFMod-GJ, a CGE model, or something else? How are sector jobs and wages generated? Check the model annexes.
2. **"Better jobs."** How are wage gains turned into job-equivalents (Indonesia 16.5M, Seychelles 7,600)?
3. **Standardized Employment Table.** Is it one template across all CGJRs? If so, it is the highest-value build.
4. **Growth–jobs decomposition.** Is JobStructure/JSCE the standard? Which working-age definition do the reports use?
5. **Informality definition.** National proxy, ILO harmonized, or 21st ICLS?
6. **Firm chapters.** Census-based TFP and misallocation, or WBES descriptives only? How much is handed to the CPSD?
7. **Peer selection.** Is there a documented rule that the tools could encode?
8. **Guidance.** Is CEM 2.0 → CGJR ("CEM 3.0") official? Is there an internal note listing required figures?
9. **Policy matrix fields.** The proposed schema: pillar, constraint, evidence figure IDs, reform, timing, agency, results indicator (baseline and target), modelled impact, DPO prior action. Confirm it against published matrices.
10. **How many CGJRs.** How many came out in FY26 beyond the six confirmed? About 20 were planned.

---

## Sources (all read as search snippets)

**CGJR program and reports**
- [S1] https://www.worldbank.org/en/publication/country-growth-and-jobs-report
- [S2] https://www.worldbank.org/asa
- [S3] https://ieg.worldbankgroup.org/node/56441
- [S4] https://www.devcommittee.org/content/dam/sites/devcommittee/doc/documents/2025/Final_DC2025-0002.pdf
- [S5] Philippines:
  - https://www.worldbank.org/en/country/philippines/publication/philippines-growth-and-jobs-report-2025
  - https://documents1.worldbank.org/curated/en/099071425163025586/pdf/P501984-3dd1e5be-b4ad-4575-a025-c4cd497c5f39.pdf
  - https://openknowledge.worldbank.org/entities/publication/e85140d9-21cc-4d00-ab07-b38542f9237d/full
- [S6] https://www.worldbank.org/en/news/press-release/2025/07/15/reforms-to-boost-job-creation-could-help-transform-ph-into-a-middle-class-society-by-2040
- [S7] https://www.worldbank.org/en/news/press-release/2025/11/24/diversifying-the-economy-a-key-lever-for-sustainable-growth-in-mauritania
- [S8] https://www.worldbank.org/en/country/indonesia/publication/indonesia-growth-and-jobs-report-running-faster-for-longer-structural-transformation-productivity-and-good-jobs
- [S9] https://www.worldbank.org/en/news/press-release/2026/04/28/morocco-can-accelerate-growth-attract-private-investment-and-generate-millions-of-jobs-through-structural-and-other-poli
- [S10] https://www.worldbank.org/en/news/press-release/2026/09/04/zimbabwe-must-leverage-macroeconomic-stability
- [S11] https://www.worldbank.org/en/brief/2026/09/17/modernize-utilize-digitalize-how-to-raise-productivity-to-deliver-growth-good-jobs
- [S12] https://data.opendevelopmentcambodia.net/en/dataset/e61fd924-7b7d-421d-8036-3832672ba0e1/resource/cfc0f289-0b10-46ee-a679-a48856a9249b/download/00_cambodia-from-vision-to-reality.pdf
- [S13] https://olc.worldbank.org/content/cem-20-country-scan

**Models and Jobs Group tools**
- [S14] https://www.worldbank.org/en/programs/economicpolicy-macro-modeling/overview
- [S15] https://www.worldbank.org/en/research/brief/the-long-term-growth-model-fundamentals-extensions-and-applications
- [S16] https://datatopics.worldbank.org/jobsdiagnostics/jobs-tools.html ; https://datacatalog.worldbank.org/search/dataset/0064624
- [S17] JobStructure Tool and JSCE:
  - https://datacatalogfiles.worldbank.org/ddh-published/0064624/DR0091787/JD_JobStructure_Tool_10Jun2023.pdf
  - https://datacatalogfiles.worldbank.org/ddh-published/0064624/DR0091790/JD_Jobs_Structural_Change_Excel_JSCE_10Jun2023.pdf
- [S18] https://datatopics.worldbank.org/JobsDiagnostics/docs/summary-of-key-steps-in-jobs-diagnostics.pdf
- [S19] https://blogs.worldbank.org/en/jobs/what-constrains-firm-growth-and-job-creation-new-tool-using-enterprise-surveys ; https://datacatalogfiles.worldbank.org/ddh-published/0064624/DR0091801/JD_WBES_Benchmarking_Tool_26Jun2023.pdf
- [S20] https://www.worldbank.org/en/results/2023/08/17/georgia-country-economic-memorandum-delivering-another-decade-of-success

**Data sources**
- [S21] https://pypi.org/project/wbgapi ; https://github.com/worldbank/data360-mcp
- [S22] https://datatopics.worldbank.org/world-development-indicators/release-note/feb-2026.html
- [S23] https://data.imf.org/en/news/accessing
- [S24] https://datacatalog.worldbank.org/search/dataset/0038389
- [S25] https://www.rug.nl/ggdc/blog/penn-world-table-version-11-0-is-published
- [S26] https://www.rug.nl/ggdc/structuralchange/etd/
- [S27] https://ilostat.ilo.org/data/bulk/ ; https://ilostat.ilo.org/methods/concepts-and-definitions/ilo-modelled-estimates/
- [S28] https://worldbank.github.io/gld/
- [S29] https://www.s4ye.org/node/1816
- [S30] https://dimewiki.worldbank.org/wiki/Microdata_Catalog
- [S31] https://www.worldbank.org/en/news/factsheet/2025/06/05/june-2025-update-to-global-poverty-lines ; https://openknowledge.worldbank.org/entities/publication/03639d66-7449-4176-8a4b-8f57ae76e815
- [S32] https://microdata.worldbank.org/catalog/322 ; https://documents1.worldbank.org/curated/en/306091607457083831/pdf/Measuring-Total-Factor-Productivity-Using-the-Enterprise-Surveys-A-Methodological-Note.pdf
- [S33] https://github.com/uncomtrade/comtradeapicall
- [S34] https://www.cepii.fr/DATA_DOWNLOAD/baci/doc/release_notes_202601.pdf
- [S35] https://atlas.hks.harvard.edu/llms.txt
- [S36] https://unctad.org/system/files/official-document/wir2026_ch01_en.pdf
- [S37] https://www.un.org/development/desa/pd/world-population-prospects-2024
- [S38] https://blogs.worldbank.org/en/opendata/five-things-you-can-learn-from-the-new-human-capital-index-plus- ; https://data.worldbank.org/indicator/HD_HCIP_OVRL_TO
- [S39] https://www.worldbank.org/content/dam/sites/govindicators/doc/The%20Worldwide%20Governance%20Indicators%202025%20Methodology%20Revision.pdf
- [S40] https://www.worldbank.org/en/businessready/about-us/covered-economies
- [S41] https://www.worldbank.org/en/publication/globalfindex
- [S42] https://etradeforall.org/node/9904
- [S43] https://pypi.org/project/blackmarblepy

**Standards and libraries**
- [S44] https://unsiap.or.jp/on_line/2024/INF/Resolution%20I%20concerning%20statistics%20on%20the%20informal%20economy%20EN%20FINAL.pdf
- [S45] https://www.statsmodels.org/stable/_modules/statsmodels/stats/oaxaca.html ; https://pypi.org/project/oaxaca/
- [S46] https://ideas.repec.org/a/tsj/stataj/y18y2018i3p618-662.html
- [S47] https://pypi.org/project/ecomplexity/
- [S48] https://cran.r-project.org/web/packages/exvatools/refman/exvatools.html
- [S49] https://cran.r-project.org/web/packages/pipr/index.html
- [S50] https://cran.r-project.org/web/packages/Rilostat/readme/README.html