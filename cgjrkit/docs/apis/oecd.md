# OECD SDMX: PMR, STRI, FDI restrictiveness (`cgjrkit.connectors.oecd`)

Connector: `OECD` in `src/cgjrkit/connectors/oecd.py`.

- `fetch(flow, countries=None, start=None, end=None, *, filters=None, server_filter=False, version=None, force=False)`: one dataflow as a canonical frame.
- Panels:
  - `pmr(countries, start, end, measures=None, wbg=False)`
  - `stri(countries, start, end, sectors=None, measures=None, digital=False)`
  - `fdirri(countries, start, end, activities=("_T",), policy_categories=("_T",))`
- `budget_status()` reports network queries used and left in the rolling hour.
- Pure: `get_flow`, `build_key`, `build_request`, `read_sdmx_csv`, `parse`, `oecd_mix_caveats`.

| Item | Value |
|---|---|
| Host (`domains`) | `sdmx.oecd.org` |
| Endpoint | `GET https://sdmx.oecd.org/public/rest/data/{agency},{dataflow},{version}/{key}?dimensionAtObservation=AllDimensions&format=csvfilewithlabels[&startPeriod&endPeriod]` |
| Auth | None. Traffic from VPNs is blocked by the OECD |
| Rate limits | The OECD publishes two figures: 60 downloads/hour (oecd.org) and 20 queries/minute plus 20 downloads/hour (iLibrary copy of the same page). The limit also covers CSV downloads from Data Explorer. **We plan for 20 per hour** |
| Format | SDMX-CSV with labels (`format=csvfilewithlabels`). The parser also reads plain SDMX-CSV and the `labels=both` variant |
| Licence | CC BY 4.0 for OECD content from 1 July 2024 (verified for publications, assumed for these datasets) |
| Citation | Per flow: "OECD, Product Market Regulation (PMR) indicators", "OECD-WBG Product Market Regulation (PMR) database", "OECD, Services Trade Restrictiveness Index (STRI)", "OECD, FDI Regulatory Restrictiveness Index" |
| Vintage | `"<dataflow> v<version>"` from the payload's `STRUCTURE_ID`, e.g. `DSD_PMR@DF_PMR v1.3` |

## Rate-limit design

- **One bulk query per dataflow.** `fetch` requests `key=all` without a period filter. The payload is cached; countries, periods and dimension filters are applied locally, so every peer panel reuses it. `server_filter=True` sends the key and periods instead (smaller payload, separate cache entry).
- **Persistent hourly budget.** `<cache>/ratelimit/oecd.json` stores the timestamps of network queries. The 21st query in a rolling 60 minutes raises `OECDRateLimitError` with the wait time; `force=True` overrides it. Cache and snapshot hits, and offline mode, never count.
- **No retry storms.** `core.http.fetch` is called with `retries=1`, so a 429 is not retried into a longer block.

## Dataflows

| Key | Agency | Dataflow | Version | DSD dimensions (excluding `TIME_PERIOD`) | Status |
|---|---|---|---|---|---|
| `PMR` | OECD.ECO.GCRD | DSD_PMR@DF_PMR | 1.3 | `REF_AREA, MEASURE, FREQ` | id and version seen in a World Bank pipeline; names after `MEASURE` UNVERIFIED |
| `PMR_WBG` | OECD.ECO.GCRD | DSD_PMR@DF_PMR_WBG | latest (empty) | as PMR | id from a dataflow catalogue; version UNVERIFIED |
| `STRI` | OECD.TAD.TPD | DSD_STRI@DF_STRI_MAIN | latest (empty) | `FREQ, REF_AREA, MEASURE, ACTIVITY, METHODOLOGY, STRI_TYPE, UNIT_MEASURE, COUNTERPART_AREA` | id from a catalogue; order inferred from the Digital STRI (same DSD): UNVERIFIED |
| `STRI_DIGITAL` | OECD.TAD.TPD | DSD_STRI@DF_STRI_DIGITAL | 1.0 | as STRI | id, version and key length verified by third-party code |
| `FDIRRI` | OECD.DAF.INV | DSD_FDIRRI_SCORES@DF_FDIRRI_SCORES | 2.0 | `REF_AREA, ACTIVITY, POL_CAT, MEASURE, UNIT_MEASURE` | verified by a third-party response |

The declared order only orders the series dimensions. SDMX-CSV puts dimension columns before `TIME_PERIOD`/`OBS_VALUE` and attributes after the measure, so any other id-like, non-attribute column before the measure is also treated as a dimension: it is appended to the declared order (payload order), listed in `meta["extra_dimensions"]` and reported by the `oecd_extra_dimensions` caveat. Distinct series therefore never collapse onto one indicator if a live DSD has a dimension the (UNVERIFIED) declared order lacks.

Codes seen in third-party material:

- **PMR `MEASURE`**: `PMR`, `STATE_INVOL`, `BARRIER_ENTRY`, `BARRIER_TRADE`, `DISTORT_PUBLIC`, `PUBLIC_OWNER`, `GOVERNANCE`, `PRICE`, `INV_BUS_NET`, `INV_BUS_SER`, `FDI_INDEX`, `TARIFFS`, `TFI`, `BARRIER_SECT`, `SERVBARRIER`, `BARRIER_DOM`, `COMMANDSIMPLIF_BURDEN`, `ADBURDEN`, `ADREG_BURDEN`, `STAKEHOLDER_ENGAG`, `IMPACT_ASSESSMENT`, `REGULATIONS`.
- **FDIRRI**:
  - `POL_CAT`: `_T`, `CLAS1_1` (foreign equity limits), `CLAS1_2` (screening and approval), `CLAS1_3` (key foreign personnel), `_O` (other restrictions).
  - `ACTIVITY`: `_T` (all activities).
  - `MEASURE`: `RR`. `UNIT_MEASURE`: `IX`.

The parser does not depend on any of these codes.

Other flows can be read with `fetch("AGENCY,DSD_X@DF_Y,VERSION")`. Examples in the same catalogue: `OECD.DAF.INV,DSD_FDIRRI_REG_DATABASE@DF_FDIRRI_REG_DATABASE`, and `DF_STRI_HETERO`, `DF_STRI_INTRAEEA` and `DSD_STRI_POLICY@DF_STRI_POLICY_MAIN` under `OECD.TAD.TPD`.

## Mapping to the canonical frame

| Canonical | From |
|---|---|
| `source` / `dataset` | `"oecd"` / flow key (`PMR`, `PMR_WBG`, `STRI`, `STRI_DIGITAL`, `FDIRRI`) |
| `indicator` | `<key>:<codes of all series dimensions except REF_AREA and FREQ, joined by ".">`, e.g. `FDIRRI:_T._T.RR.IX`, `STRI:STRI.LEGAL._Z.MAIN.IX._Z`, `PMR:PMR`. The same series always gets the same code, whatever the query |
| `indicator_label` | Flow label plus the dimension labels, skipping `_Z`/`_X` |
| `iso3` / `country` | `REF_AREA` / its label. Codes that are not ISO3 (e.g. `OECD`) are listed in `meta["aggregates"]` |
| `period` / `value` | `TIME_PERIOD` / `OBS_VALUE` (empty values dropped and counted) |
| `unit` | The flow's scale (PMR 0–6, STRI and FDIRRI 0–1), or the `UNIT_MEASURE` label when it is not "Index"; `UNIT_MULT ≠ 0` is appended |
| `obs_status` | SDMX `OBS_STATUS`: E → estimate, P → provisional, F → forecast, B → break, I → imputed; A/M/L/N/U → "" |

## How this was verified

`sdmx.oecd.org` and `www.oecd.org` are blocked in the build environment. Sources read on 2026-10-11:

| Label | Source | What it established |
|---|---|---|
| T | World Bank [WB-PIDA-Data-Science-Shop/cliaretl `data-raw/input/pmr/url.txt`, `data-raw/source/pmr.R`](https://github.com/WB-PIDA-Data-Science-Shop/cliaretl) | `OECD.ECO.GCRD,DSD_PMR@DF_PMR,1.3/.PUBLIC_OWNER+...+PMR.A?startPeriod=2018&endPeriod=2023&dimensionAtObservation=AllDimensions` (3 key positions); measure codes; pre-2018 values dropped as not comparable |
| T | [subsetsio/connectors `src/oecd/src/constants.py`](https://github.com/subsetsio/connectors) | Dataflow catalogue with agency ids for DF_PMR, DF_PMR_WBG, DF_STRI_MAIN/DIGITAL/HETERO/INTRAEEA, DF_STRI_POLICY_*, DF_FDIRRI_SCORES, DF_FDIRRI_REG_DATABASE |
| T (recorded response) | [JesLied thesis notebook `OECD_FDI_Restrictiveness.ipynb`](https://github.com/JesLied/Jesper-Liedholm-Thesis-Code) | FDIRRI version 2.0, key `._T...`, columns `REF_AREA, ACTIVITY, POL_CAT, MEASURE, UNIT_MEASURE, TIME_PERIOD, OBS_VALUE, DECIMALS, UNIT_MULT, OBS_STATUS`, the POL_CAT codes, and the `labels=both` cell format ("UZB: Uzbekistan") |
| T (recorded response) | [yusukem0131/data_visualization `OECD/get_data_final.ipynb` + saved JSON](https://github.com/yusukem0131/data_visualization) | `OECD.TAD.TPD,DSD_STRI@DF_STRI_DIGITAL,1.0/A.......` and the dimension ids FREQ, REF_AREA, MEASURE, ACTIVITY, METHODOLOGY, STRI_TYPE, UNIT_MEASURE, COUNTERPART_AREA |
| T (recorded responses) | [bdecon/econ_data `APIs/OECD.ipynb`](https://github.com/bdecon/econ_data), seanprb/indonesia-dfm-nowcast, JerBouma/FinanceToolkit, envisioning/hindsight | `format=csvfilewithlabels` columns `STRUCTURE, STRUCTURE_ID, STRUCTURE_NAME, ACTION, REF_AREA, Reference area, ..., TIME_PERIOD, Time period, OBS_VALUE, Observation value, OBS_STATUS, Observation status, UNIT_MULT, Unit multiplier, ...`; empty version (`DSD@DF,/`) and `key=all` accepted |
| T | RiccardoCeccaroni/ORA-Chatbot PMR note (scraped 2026-05-12) | PMR measure codes; Italy PMR 1.29 (2018) and 1.23 (2023), used as a loose live check |
| P (snippet) | OECD, [API rate limiting](https://www.oecd.org/en/data/insights/data-explainers/2024/11/Api-best-practices-and-recommendations.html) and its [iLibrary copy](https://read.oecd-ilibrary.org/en/data/insights/data-explainers/2024/11/Api-best-practices-and-recommendations.html), via search snippets | 60 downloads/hour vs 20 queries/minute and 20 downloads/hour; temporary blocks when exceeded; also applies to Data Explorer CSV downloads |
| V (plan) | `inventory/build-plan.md` §4 | VPN traffic blocked; CC BY 4.0 from July 2024; plan for 20/hour |

### UNVERIFIED (live tests in `tests/live/test_oecd_live.py`, at most 5 queries)

| Assumption | Where | Live test |
|---|---|---|
| PMR DSD order `REF_AREA, MEASURE, FREQ`; Italy PMR ≈ 1.29 (2018), 1.23 (2023) | `FLOWS["PMR"]` | `test_pmr_dimensions_and_values` |
| DF_PMR_WBG under OECD.ECO.GCRD, latest version, includes Morocco | `FLOWS["PMR_WBG"]` | `test_pmr_wbg_has_morocco` |
| DSD_STRI order; DF_STRI_MAIN served with an empty version | `FLOWS["STRI"]` | `test_stri_dimensions` |
| FDIRRI layout stays as recorded | `FLOWS["FDIRRI"]` | `test_fdirri_layout` |
| Attribute ids (OBS_STATUS, UNIT_MULT, DECIMALS, ...) are the only non-dimension id columns | `KNOWN_ATTRIBUTES` | the dimension checks above |

## Caveats raised

| Code | Severity | When |
|---|---|---|
| `oecd_licence` | info | Always |
| `pmr_methodology_break` | warn | PMR rows before and from 2018 together (pre-2018 vintages not comparable) |
| `pmr_survey_years` | info | Countries surveyed in different years |
| `pmr_oecd_wbg_mix` | warn | `oecd_mix_caveats`: OECD PMR and OECD-WBG PMR rows combined |
| `stri_scale` | info | STRI flows: 0 (open) to 1 (closed); sector scores are not additive |
| `fdirri_scale` | info | FDIRRI: 0–1, statutory restrictions only |
| `oecd_dimensions_unverified` | info | Flow whose DSD order is not verified |
| `oecd_extra_dimensions` | info | The payload has dimension columns (before `TIME_PERIOD`) missing from the declared DSD order; they were appended to the indicator code |
| `oecd_unknown_obs_status` | info | Unmapped OBS_STATUS codes |
| `oecd_empty` | warn | No rows |

## Replication notes

- R03 (Morocco Fig 3-4, economy-wide PMR vs OECD): `pmr(["MAR"], wbg=True, measures="PMR")` plus `pmr(["OECD"], measures="PMR")`. Check survey years (`pmr_survey_years`) and do not mix pre-2018 values.
- X09 (Indonesia, North Macedonia: STRI by sector, FDIRRI): `stri(peers)` and `fdirri(peers, policy_categories=None)`. The WB-WTO STRI (i-tip.wto.org) is a separate database and is not covered here.

## Core change requests

- A per-host rate limiter and retry policy in `core.http` would replace this connector's own hourly budget.
- An option to keep 429 responses out of the retry loop would also help.

## Fixtures (all synthetic)

| File | Layout | Content |
|---|---|---|
| `oecd_pmr_synthetic.csv` | csvfilewithlabels | ITA, MEX, OECD; PMR for 2013/2018/2023 (round values), BARRIER_TRADE 2018, one empty cell |
| `oecd_stri_synthetic.csv` | csvfilewithlabels | IDN, MEX; two sectors × two measures × 2023–2024; status P |
| `oecd_fdirri_synthetic.csv` | csvfilewithlabels | KEN, MKD, PHL; two activities × three policy categories × 2022–2023 |
| `oecd_fdirri_labels_both_synthetic.csv` | labels=both | two rows, same values as above |
