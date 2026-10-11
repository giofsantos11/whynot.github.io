# World Bank Data360 API (`cgjrkit.connectors.data360`)

Connector: `Data360` in `src/cgjrkit/connectors/data360.py`.
Covers the generic `/data360/data` endpoint for any Data360 database, plus indicator listing, metadata, search, disaggregation and codelists. Two wrappers serve the CGJR charts:

- `fetch_enterprise_surveys(indicators, countries, ...)` covers Enterprise Surveys aggregates (database `WB_ES`, archetype B13).
- `fetch_lpi20(indicators=None, countries=None, metrics=None)` covers Logistics Performance Indicators 2.0 (database `WB_LPI_20`, archetype N01).

| Item | Value |
|---|---|
| Host (`domains`) | `data360api.worldbank.org` |
| Base URL | `https://data360api.worldbank.org` (OpenAPI `servers[0].url`) |
| Auth | None (no key in the OpenAPI spec or the data360-mcp config; HCI+ notes: "No API key, no registration, CORS-open") |
| Rate limits | None documented |
| Page size | At most **1000 rows per call**. The connector pages with `skip` until it reaches `count` (or `@odata.count`). It also sends `top` (from data360-mcp, not in the spec) |
| Licence | The API itself: CC BY 4.0 (OpenAPI `info.license`: "Creative Commons 4.0 by Attribution"); this is `Data360.license`, used on metadata and codelist provenance records. Data360 also hosts databases from other publishers (OECD, IMF, WEF, ...) under their own terms, and each indicator's metadata has its own `license[]`. **`Provenance.license` of the data records is the metadata licence of the indicators in the result** (all `license[].name` values, joined with `"; "`). When the metadata could not be read it is `"unverified (Data360 per-indicator licence not read)"` and the result carries `data360_license_unverified` (info). `data360_license` (warn) flags any licence that is not plain CC BY |
| Citation | "World Bank Data360". The source line appends the vintage |
| Vintage | No release or vintage endpoint. `Provenance.vintage = "version_date <YYYY-MM-DD>"`, the latest `series_description.version_statement.version_date` among the requested indicators. If metadata is unavailable the vintage is empty, and a note says that the payload hash pins the snapshot |

## Endpoints

| Call | Method | Parameters / body | Response |
|---|---|---|---|
| `/data360/data` | GET | `DATABASE_ID` (required), `INDICATOR` (comma list), `REF_AREA` (comma list), `SEX`, `AGE`, `URBANISATION`, `COMP_BREAKDOWN_1..3`, `TIME_PERIOD`, `FREQ`, `UNIT_MEASURE`, `UNIT_TYPE`, `UNIT_MULT`, `timePeriodFrom`, `timePeriodTo`, `skip` (all in the spec); `top` (data360-mcp only, **not in the spec**) | `{"count": <total>, "value": [rows]}` (spec sample). data360-mcp reads `@odata.count` instead; `parse_page` accepts both |
| `/data360/metadata` | POST | `{"query": "&$filter=series_description/idno eq '<ID>'[&$select=series_description/<f>,...]"}` | `{"@odata.count", "value": [{"series_description": {...}}]}` |
| `/data360/indicators` | GET | `datasetId` | Bare JSON list of indicator ids |
| `/data360/disaggregation` | GET | `datasetId`, `indicatorId` | `[{"field_name", "label_name", "field_value": [...]}]` |
| `/data360/searchv2` | POST | `{"search", "top", "skip", "count", "select", "filter", "orderby"}` | `{"@odata.count", "value": [{"series_description": {...}}]}` |
| `/data360/codelist` | GET | `type=REF_AREA` or `UNIT_MEASURE` | `{"value": [{"Id", "Name"}]}` (**not in the OpenAPI spec**) |

Each data row has the same 24 fields: `OBS_VALUE` (string), `TIME_FORMAT`, `UNIT_MULT`, `COMMENT_OBS`, `OBS_STATUS`, `OBS_CONF`, `AGG_METHOD`, `DECIMALS`, `COMMENT_TS`, `DATA_SOURCE`, `LATEST_DATA`, `DATABASE_ID`, `INDICATOR`, `REF_AREA`, `SEX`, `AGE`, `URBANISATION`, `COMP_BREAKDOWN_1`, `COMP_BREAKDOWN_2`, `COMP_BREAKDOWN_3`, `TIME_PERIOD`, `FREQ`, `UNIT_MEASURE`, `UNIT_TYPE`.

## Mapping to the canonical frame

| Canonical | From |
|---|---|
| `source` | `"data360"` |
| `dataset` | `DATABASE_ID` (e.g. `WB_ES`, `WB_LPI_20`) |
| `indicator` | `INDICATOR`, then `\|<code>` for each non-trivial dimension of the row that survived the totals filter, in the order SEX, AGE, URBANISATION, COMP_BREAKDOWN_1..3. Then `\|<UNIT_MEASURE>`, but only when one estimate series comes in several units. Examples: `WB_ES_T_OBST1`, `CT_DT_M\|WB_LPI_MD`, `WB_WDI_SE_PRM_CMPT_FE_ZS\|F` |
| `indicator_label` | Metadata `name`, else `COMMENT_TS`. Dimension labels are appended (LPI metric labels are built in) |
| `iso3` / `country` | `REF_AREA` / the REF_AREA codelist name. Aggregates (WB codes such as `SSF`, `WLD`, and non-ISO3 groups such as `G_LLD`) are listed in `meta["aggregates"]` |
| `period` | `TIME_PERIOD`, normalised: `2024`, `2024Q1`, `2024-03`. Full dates are cut to `FREQ` |
| `value` | `float(OBS_VALUE)` |
| `unit` | Metadata `measurement_unit`, else the UNIT_MEASURE codelist name, else a built-in label (`PT` percent, `D` days, `PRTN` partners, ...), else the code. Gets ` (x10^k)` when `UNIT_MULT` is not 0 |
| `obs_status` | SDMX `OBS_STATUS`: A→"", B→break, E→estimate, F→forecast, I→imputed, P→provisional. Other codes map to "" and are kept in the extras table |

**Totals filter (`totals_only=True`, the default).** "Trivial" means `_T` (total) or `_Z` (not applicable). A row is dropped only when a **more aggregated row exists** for it: a row with the same `INDICATOR`, `REF_AREA`, `TIME_PERIOD` and `keep_dims` codes whose other non-trivial codes are a strict subset of this row's. So an ES firm-size row is dropped because the economy-wide (`_T`) row is there. Rows with no such total are kept as separate series, with their codes appended to the indicator id. Examples: the female-only WDI series in the OpenAPI sample (`SEX="F"`, no `_T` row), WEF_TTDI value/rank/score (`COMP_BREAKDOWN_1` = `VAL`/`RNK`/`SCR`), and SE4ALL types. These series are listed in `meta["untotalled_series"]` and caveat `data360_no_total` (info). Dropped rows are counted in `meta["dropped_breakdown_rows"]`, and their codes per dimension are in `meta["dropped_breakdown_codes"]`. Caveat `data360_breakdowns_dropped` reports them, as info, or as warn if a requested indicator has nothing left. data360-mcp follows the same idea: it defaults a dimension to `_T` only "if `_T` in values and len(values) > 1".

**Extras table** (`meta["extras"]`, JSON records `{indicator, iso3, period, field, value}`; `extras_frame(result)` pivots it):

- `se` and `n`: standard error and sample size (ES only, see below). A row is filed as a statistic only if (a) one of its breakdown codes is listed **exactly** in `statistic_codes` (default `ES_STATISTIC_CODES`, which stays **empty** until the live test shows the real WB_ES codes), or (b) it is a companion id (`WB_ES_SE_<x>`, `WB_ES_N_<x>`, `<x>_SE`, `<x>_NOBS`) of an indicator in the same payload. In both cases the matching estimate row must exist. Code fragments are never pattern-matched: `..._EXPORTER_N` ("non-exporters") is a subgroup, not a sample size;
- `n_obs_group`: observation-count band from `COMMENT_OBS`, e.g. `"1000-10000"` (LPI 2.0);
- `comment_obs`: other `COMMENT_OBS` text;
- `obs_status_raw`: unmapped SDMX status codes.

## How this was verified

The data host is blocked in the build environment, so nothing was called live. Sources read on 2026-10-11:

| Label | Source | What it established |
|---|---|---|
| D | Official OpenAPI spec [worldbank/open-api-specs "Data360 Open_API.json"](https://raw.githubusercontent.com/worldbank/open-api-specs/HEAD/Data360%20Open_API.json) | Server URL, the five `/data360/*` paths, the `/data` query parameters (`DATABASE_ID`, `INDICATOR`, `REF_AREA`, `SEX`, `AGE`, `URBANISATION`, `COMP_BREAKDOWN_1..3`, `TIME_PERIOD`, `FREQ`, `UNIT_MEASURE`, `UNIT_TYPE`, `UNIT_MULT`, `timePeriodFrom`, `timePeriodTo`, `skip`; **no `top`**), the `DataResponse` schema (`count` + 24-field rows, `OBS_VALUE` as string), with a sample whose rows include `WB_WDI_SE_PRM_CMPT_FE_ZS` with `SEX "F"` and no total row, "maximum of 1000 records" per call with `skip`, the metadata POST body `{"query": "&$filter=...&$select=..."}`, the `SearchQuerySchema`, and the licence (CC BY 4.0) |
| P | World Bank [data360-mcp](https://github.com/worldbank/data360-mcp) 0.1.2 sdist from PyPI (`src/data360/api.py`, `providers.py`, `config.py`, `.env.example`, tests) | Base `https://data360api.worldbank.org`. **`top`** (not `$top`) on `/data` ("API uses 'top' not '$top'"); this is the only source for `top`. **`@odata.count`** as the total (the spec sample uses `count`; the connector accepts both). REF_AREA comma lists. `/data360/codelist?type=REF_AREA\|UNIT_MEASURE` with `{"Id","Name"}` items. Disaggregation `[{field_name, field_value}]`. "Do not use FREQ for filtering". Defaults a dimension to `_T` only when `_T` is among several values |
| P | [data360-mcp docs/payload_analysis.md](https://raw.githubusercontent.com/worldbank/data360-mcp/HEAD/docs/payload_analysis.md) | The 24-field schema holds across 16 databases. `_T` = total and `_Z` = not applicable. Some series have no total in a breakdown dimension (`COMP_BREAKDOWN_1` values `WEF_TTDI_VAL`/`RNK`/`SCR`, `WB_SE4ALL_TYPE_*`). `OBS_STATUS` was always "A" in their survey, and `COMMENT_OBS` was always null outside LPI |
| P | World Bank [HCI-Plus API.md](https://raw.githubusercontent.com/worldbank/HCI-Plus/HEAD/API.md) | The 1000-row cap, paging with `skip` against `count`, and UNIT_MEASURE codes `PT`, `YR`, `SCORE`. "The metadata endpoint rejects GET" |
| P | World Bank team R code: [govhr data360api.R](https://raw.githubusercontent.com/WB-PIDA-Data-Science-Shop/govhr/HEAD/R/data360api.R), [govhr enterprise_surveys.R](https://raw.githubusercontent.com/WB-PIDA-Data-Science-Shop/govhr/HEAD/data-raw/enterprise_surveys.R), [cliaretl wb_enterprise.R](https://raw.githubusercontent.com/WB-PIDA-Data-Science-Shop/cliaretl/HEAD/data-raw/source/wb_enterprise.R), [wdr2025gov 15-enterprise_surveys.R](https://raw.githubusercontent.com/galileukim/wdr2025gov/HEAD/data-raw/source/15-enterprise_surveys.R) | Comma-joined `INDICATOR`. `WB_ES` ids such as `WB_ES_T_OBST1`, `WB_ES_T_IN12`, `WB_ES_T_WK10`, `WB_ES_T_MGMT1`. **Economy-wide ES value = `COMP_BREAKDOWN_1/2/3 == "_T"` (and SEX/AGE/URBANISATION `_T`)**. Bulk CSV at `data360files.worldbank.org/data360-data/data/WB_ES/<ID>.csv` with `*_LABEL` columns |
| T | Public SDMX-CSV export of `WB_LPI_20` committed in [favibe/AfricanTradeLogistics](https://github.com/favibe/AfricanTradeLogistics) (`data/raw/WB_LPI_20.csv`, structure `WB.DATA360:DS_DATA360(1.3)`, 12,327 rows), used **only to learn the layout** | 21 indicators (e.g. `CT_DT_M`, `TRS_T`, `CT_MC`) in units `D`/`PRTN`/`ALLIANC`/`SERV`/`TRANS`. Periods 2023 and 2024. **The statistic sits in `COMP_BREAKDOWN_1`** (`WB_LPI_M`, `WB_LPI_MD`, `WB_LPI_IQR_75_25`, `WB_LPI_IQR_80_20`, `WB_LPI_M_TEU`, `WB_LPI_MD_TEU`, or `_Z`). **The observation-count band sits in `COMMENT_OBS`** ("Number of observation group: 1000-10000", "...: 36"). Groups `G_COA`, `G_LLD`, `G_SIC` |
| T | Sample responses in [Sikirulahi1/worldbank-mcp tests/fixtures/sample_responses](https://github.com/Sikirulahi1/worldbank-mcp/tree/main/tests/fixtures/sample_responses) | A full data row (`TIME_FORMAT "602"`, `UNIT_MEASURE "PT_YR"`) and a full metadata document: `version_statement.version_date`, `license[].name = "CC BY 4.0"`, `measurement_unit`, `aliases`, `csv_link`, `api_link` |

D = official documentation. P = code by the provider or its own teams. T = a third party.

### UNVERIFIED (each has a live test in `tests/live/test_data360_live.py`)

| Assumption | Where in the code | Live test |
|---|---|---|
| **How `WB_ES` exposes standard errors and sample sizes.** The parser reads (a) breakdown codes listed exactly in `ES_STATISTIC_CODES` (empty until verified; fill it from the live test's output), (b) companion ids `WB_ES_SE_*`/`WB_ES_N_*` or `*_SE`/`*_NOBS` of an indicator in the same payload, and (c) `COMMENT_OBS` text ("Number of observations: n", "Standard error: x"), always next to the matching estimate row. If none is present, the result carries `es_se_missing` (warn). Its evidence lists the dropped breakdown codes | `ES_STATISTIC_CODES`, `_statistic_from_indicator`, `parse_comment_obs` | `test_enterprise_surveys_kenya_and_se_n_encoding`, `test_es_breakdown_codes_report` |
| The server honours `top` (data360-mcp; not in the spec). Pagination does not depend on it: paging follows `count` | `build_data_request` | `test_pagination_reaches_total_count` |
| A series without a `_T` row in a breakdown dimension is a complete series in its own right (kept, code appended), not a partial pull | `parse_rows` totals filter | none; caveat `data360_no_total` |
| The metadata POST body in the OpenAPI form (`"&$filter=..."`). data360-mcp sends `{"query": "<filter>", "select": ...}` | `build_metadata_request` | `test_metadata_post_body_form` |
| `/data360/codelist` exists and returns `{"value":[{"Id","Name"}]}` | `build_codelist_request`, `parse_codelist` | `test_codelist_endpoint` |
| The API's `WB_LPI_20` indicator ids match the CSV export's bare codes (`CT_DT_M`), not a `WB_LPI_20_` prefix. The wrapper avoids depending on this by pulling all indicators when none are given | `fetch_lpi20` docs | `test_lpi20_indicator_ids_and_metrics` |
| REF_AREA and INDICATOR comma lists work together in one call | `build_data_request` | `test_comma_separated_indicators_and_countries` |
| `count` is the total across pages | `fetch` pagination | `test_pagination_reaches_total_count` |
| `OBS_STATUS` letters other than "A" follow SDMX CL_OBS_STATUS | `OBS_STATUS_MAP` | none (only "A" observed so far) |
| `TIME_PERIOD` formats other than `YYYY` | `normalize_period` | none |

## Caveats raised

| Code | Severity | When |
|---|---|---|
| `data360_empty` | warn | The API returned no rows |
| `data360_all_rows_filtered` | warn | The API returned rows, but the totals filter dropped all of them (replaces `data360_empty` in that case) |
| `data360_missing_indicators` | warn | A requested indicator returned no rows from the API |
| `data360_breakdowns_dropped` | info, or warn if a requested indicator was left with no rows | Breakdown rows dropped by `totals_only`. Evidence: counts per indicator and the dropped codes |
| `data360_no_total` | info | Series kept without a total in some dimension, with the code appended to the id |
| `data360_license_unverified` | info | The per-indicator licence could not be read from the metadata. `Provenance.license` then says "unverified ..." |
| `data360_flagged_obs` | info | Some rows carry provider status flags |
| `data360_license` | warn | Indicator metadata reports a licence other than plain CC BY. `is_cc_by()` accepts "CC BY 4.0" and "Creative Commons Attribution", and rejects NC/ND variants |
| `es_survey_waves` | info | Always for ES: values are survey waves, irregular across countries |
| `es_se_missing` | warn | ES result without standard errors and/or sample sizes |
| `es_small_cells` | info | ES subgroup breakdowns requested |
| `lpi20_series_break` | warn | Always for LPI 2.0: not comparable with the survey LPI (WDI `LP.LPI.*`) |
| `lpi20_statistics` | info | Always for LPI 2.0: several statistics per indicator |

## Fixtures (all synthetic)

`tests/fixtures/data360_*_synthetic.json` follow the documented layouts above. Each has a `_note` key, which the parser ignores. Values are invented round numbers. Indicator ids `WB_ES_T_SYNTH*`, breakdown codes `SYNTH_*`, and the SE/N encodings are hypothetical and exist only to exercise the parser. The unit tests pass the fixture's statistic codes (`SYNTH_SE`, `SYNTH_NOBS`) explicitly through `statistic_codes`, and they also check that the default (empty `ES_STATISTIC_CODES`) treats those rows as subgroups. The LPI fixture uses the code layout of the CSV export with invented values.
