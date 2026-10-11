# IMF data: SDMX 3.0 API and DataMapper (`cgjrkit.connectors.imf`)

Connector: `IMF` in `src/cgjrkit/connectors/imf.py`.
It covers the **World Economic Outlook** (the latest release and past releases), the **balance of payments** (the IMF.STA `BOP` dataflow, which took over the IFS BOP tables) and **any other dataflow** on the IMF SDMX 3.0 API. The **DataMapper API** is the fallback for the latest WEO.

| Item | Value |
|---|---|
| Hosts (`domains`) | `api.imf.org` (SDMX 3.0), `www.imf.org` (DataMapper fallback) |
| SDMX base URL | `https://api.imf.org/external/sdmx/3.0` |
| DataMapper base URL | `https://www.imf.org/external/datamapper/api/v1` |
| Auth | None for public dataflows such as WEO, BOP and FM. Restricted flows take an `Ocp-Apim-Subscription-Key` header (readimf). This connector sends no key |
| Rate limits | None are published for the new API. The legacy API allowed 10 requests per 5 s per IP. imfp waits 1.5 s between calls. The connector batches countries and indicators into one key (`KEN+UGA.NGDP_RPCH+GGXWDG_NGDP.A`) and caches every payload |
| Licence | [IMF Copyright and Usage terms](https://www.imf.org/en/About/copyright-and-terms). Attribution is required. **Systematic downloading and commercial reuse need written permission** (copyright@imf.org); see the plan in `inventory/build-plan.md` §4. Every result carries an `IMF_TERMS` caveat, and every provenance record carries the same note |
| Citation | "IMF, World Economic Outlook database"; BOP: "IMF, Balance of Payments Statistics"; others in `FLOW_CITATIONS`; DataMapper: "IMF, World Economic Outlook (via IMF DataMapper)" |
| Vintage | WEO: `"WEO YYYY-MM"`, with MM = 04 or 10. A named release is resolved to the (dataflow, version) that serves it, through the catalogue and `PUBLICATION_DATE` probes (see "Vintages" below). The latest flow takes it from the `PUBLICATION_DATE` probe. DataMapper: `"WEO YYYY-MM (DataMapper, unpinned)"` when the indicator metadata names a release, otherwise empty. A release inferred from the retrieval date only steers the forecast flags and is never written as the vintage. Other flows: empty, because the provider revises data in place; the dataflow structure version goes into the notes |

## How this was verified

`api.imf.org`, `www.imf.org` and `data.imf.org` are blocked in the build environment, both through the proxy (403 on CONNECT) and through WebFetch (DNS). **Nothing was called live.** The facts below come from these sources, read on 2026-10-11.

| Label | Source | What it established |
|---|---|---|
| R | **Recorded live responses** in the imfp repository, `tests/responses/*.json`, captured 2025-11-09/10 ([github.com/Promptly-Technologies-LLC/imfp](https://github.com/Promptly-Technologies-LLC/imfp)). I read them for format only, and none of their values went into our fixtures | The catalogue `structure/dataflow/all/*/+` (71 flows): **`IMF.RES` `WEO` 9.0.0 (DSD_WEO 9.0)**, **`IMF.STA` `BOP` 21.0.0 (DSD_BOP 24.0)**, plus the agencies of FM, GDD, HPD, ICSD (IMF.FAD), AFRREO (IMF.AFR), IIP, IRFCL, ER and CPI (IMF.STA). The **DSD_BOP dimensions are `COUNTRY.BOP_ACCOUNTING_ENTRY.INDICATOR.UNIT.FREQUENCY`**. The **SDMX-JSON 2.0 data message** layout: `data.dataSets[0].series` keyed `"0:0:0"`; observations `[value_string, obs_attr...]`; `dimensionGroupAttributes` keyed `":0::"`; `structures[0].dimensions/attributes/measures/links`; observation values as strings; a dataset with no `series` key when nothing matched. Codelists (`data.codelists[0].codes[].id/name`). An unknown dataflow in a structure query returns **HTTP 204 with an empty body**. **Values are in base units**: Kenya `NETCD_T.CAB.USD` 2017 is about −5.74e9 with `SCALE` = 6. The time-filter URL form is `c%5BTIME_PERIOD%5D=ge%3A2016-A1%2Ble%3A2018-A1` (IMF.STA BOP, GFS_SOO). STATUS is sent as an uncoded literal (`NA`, `A`). The BOP codes `NETCD_T`/`NNAFANIL_T`/`A_T` × `CAB`, `GS`, `G`, `S`, `IN1`, `IN2`, `KAB`, `EO`, `FAB`, `D_F`, `P_F`, `O_F`, `R_F` exist for Kenya, with labels from `CL_BOP_INDICATOR` and `CL_BOP_ACCOUNTING_ENTRY` |
| P1 | [imfp 2.1.0](https://pypi.org/project/imfp/) sdist (`imfp/api.py`, `imfp/utils.py`) | The base URL `https://api.imf.org/external/sdmx/3.0/` and the data path `data/dataflow/{agency}/{flow}/+/{key}`. The key is positional, with `+` within a slot and `*` as a wildcard. JSON parameters: `dimensionAtObservation=TIME_PERIOD&attributes=dsd&measures=all`. The time filter is `c[TIME_PERIOD]=ge:YYYY-A1+le:YYYY-A1` and is honoured by **IMF.STA only**. The error envelope is `{message, code, correlationId, path}`. The JSON decoding algorithm covers series, observation and dimension-group attributes |
| P2 | [readimf](https://github.com/charlescoverdale/readimf) (CRAN 0.1.0, GitHub HEAD 2026-09-19), `R/imf_weo.R` and `R/request.R` | WEO: agency `IMF.RES`, key `COUNTRY.INDICATOR.A` with ISO3 codes (`GBR.NGDP_RPCH.A`). **Past releases are dataflows `WEO_YYYY_MON_VINTAGE`** (`"2025-10"` → `WEO_2025_OCT_VINTAGE`). It reads SDMX-CSV (`Accept: application/vnd.sdmx.data+csv;version=2.0.0`). Restricted flows take the `Ocp-Apim-Subscription-Key` header |
| P3 | [imf-reader 2.2.0](https://pypi.org/project/imf-reader/) (ONE Campaign) sdist (`weo/api.py`) and repo (CHANGELOG, `docs/docs/weo-coverage.md`, `tests/test_weo/*`), GitHub HEAD 2026-09-28 | It reads CSV with `Accept: text/csv`. The header carries the envelope `STRUCTURE[;]`, `STRUCTURE_ID`, `ACTION`, and multi-value columns such as `TOPIC[]`. **The "Estimates Start After" field is the series attribute `LATEST_ACTUAL_ANNUAL_DATA`**: `"2024"` or `"FY2023/24"`, with about 10.7% of series in FY form. It is read with **`attributes=series`** (`&firstNObservations=1` for a metadata-only call). The same call gives `METHODOLOGY_NOTES`, `COUNTRY_UPDATE_DATE` (US `m/d/Y`), `START_END_MONTHS_OF_REPORTING_YEAR`, `UNIT` and `SCALE`. The default attribute set differs by WEO version (6.0.0 carries `LATEST_ACTUAL_ANNUAL_DATA`, 9.0.0 does not), which is why we ask for it explicitly. **The release comes from the dataset attribute `PUBLICATION_DATE`** (`attributes=dataset&lastNObservations=1` on `.../{flow}/{version}/USA.NGDP_RPCH.A`; month < 7 means April, otherwise October). The `lastUpdatedAt` annotation is **inverted** on live WEO flows. **Flow discovery** (`_fetch_flow_mapping`) lists `structure/dataflow/IMF.RES/*/*?detail=allstubs` and probes every version of `WEO` and of each `WEO_YYYY_MON_VINTAGE`. If a vintage flow's probe fails, its id gives the label. On a collision the bare flow wins, then the higher version ("Old bare versions stay live and serving data"). Its tests map **`("April", 2025)` to `FlowRef("WEO", "6.0.0")`**, so there is no `WEO_2025_APR_VINTAGE`. `docs/weo-coverage.md`: "April 2025 is the one release available from both sources", and the PPP concepts `PPPGDP`, `PPPPC`, `PPPEX`, `NGDPRPPPPC` have a null `UNIT_CODE`. The API serves **April 2025 onward**, and `WEO_2025_OCT_VINTAGE` exists. WEO 6.0.0's DSD carries `LATEST_ACTUAL_ANNUAL_DATA`, and 9.0.0's carries `COUNTRY_UPDATE_DATE`. `OBS_VALUE` arrives in base units. Areas are ISO3 or `G`-prefixed aggregates (`G001` World); Kosovo is `KOS`. Codelists are `CL_WEO_COUNTRY` and `CL_WEO_INDICATOR` (IMF.RES), `CL_UNIT` and `CL_FREQ` (IMF). Legacy WEO concept codes (`NGDP_RPCH`, `GGXWDG_NGDP`, `GGXCNL_NGDP`, `GGR_NGDP`, `GGX_NGDP`, `BCA_NGDPD`, …) are unchanged, with unit `PT` |
| T1 | [cyanheads/imf-mcp-server](https://github.com/cyanheads/imf-mcp-server) (HEAD 2026-09-30), `docs/design.md` and tests, written against the live portal | The WEO key is `COUNTRY.INDICATOR.FREQUENCY`, with 210 countries and 145 indicators. WEO series attributes are `SCALE`, `DECIMALS_DISPLAYED`, `OVERLAP` and `COUNTRY_UPDATE_DATE`; `UNIT` is a dimension-group attribute on `INDICATOR`. **A `+` key on INDICATOR with no `*` comes back with the dimension-group `values` empty** (issue #34), so JSON units can be missing. An unknown code gives HTTP 200 with zero series. The portal exposes "70+" `_VINTAGE` flows. STATUS is uncoded. Values are in base units. It also states that "the API does not flag which observations are projections" in the default response |
| D | IMF DataMapper API help text, seen through search snippets of `https://www.imf.org/external/datamapper/api/` ("the keys of the objects are the IDs to be used when retrieving time series"; endpoints indicators, countries, regions, groups) and of `.../api/v2/regions` (maps `SSQ` to "Sub-Saharan Africa (Region)"; also lists AFQ, APQ, EUQ, MEQ, NAQ, WHQ), plus client code: [us-bea/well-being](https://github.com/us-bea/well-being) `get_data.py`, [hanlulong/openecon-data](https://github.com/hanlulong/openecon-data) `backend/providers/imf.py`, [Hack23/riksdagsmonitor](https://github.com/Hack23/riksdagsmonitor) `scripts/imf/parsers/datamapper-envelope.ts` | API v1. Series are `/{indicator}` or `/{indicator}/{country}`. The `periods` query takes a comma-separated list of years. The response is **`{"values": {IND: {ISO3: {"YYYY": number}}}}`**. `/indicators` returns `{"indicators": {code: {label, description, source, unit, dataset}}}`. There are no projection flags and no vintage field |
| G | [WEO Database Transition Guide](https://data.imf.org/-/media/iData/External-Storage/Documents/2C2B2F3E671A4B11ABE756E2441FD6F0/en/WEO-Database-Transition-Guide.pdf) (1 Apr 2026), seen only through a search snippet because the PDF host is blocked | WEO is on the IMF Data portal as `IMF.RES:WEO`. The SDMX file downloads were replaced by the API |

### UNVERIFIED (each has a live test in `tests/live/test_imf_live.py`)

| Assumption | Where in the code | Live test |
|---|---|---|
| The SDMX-CSV header seen by imf-reader (envelope columns, then dimensions, then `TIME_PERIOD`, `OBS_VALUE`, attributes) also applies to WEO data calls with `attributes=series` | `read_sdmx_csv`, `build_weo_request` | `test_csv_header_shape`, `test_weo_series_attributes_include_last_actual` |
| `LATEST_ACTUAL_ANNUAL_DATA` is present in the **full** data call (`attributes=series` without `firstNObservations`) | `build_weo_request` | `test_weo_series_attributes_include_last_actual` |
| `LATEST_ACTUAL_ANNUAL_DATA` is also delivered in SDMX-JSON (we request `attributes=all` for JSON) | `build_weo_request(fmt="json")` | `test_weo_json_matches_csv` (values only) |
| The `PUBLICATION_DATE` format: both ISO and US forms are parsed | `parse_date` | `test_weo_publication_date_probe` |
| WEO labels "FY2023/24" as 2023 (the leading year, as imf-reader does). The real convention varies by country; the leading year never labels a projection as actual | `parse_last_actual` | caveat `WEO_FISCAL_YEAR`; not testable automatically |
| Vintage resolution as in imf-reader: every bare `WEO` version answers the `PUBLICATION_DATE` probe at its explicit version, and April 2025 is served by an old bare version (6.0.0 in imf-reader's tests) | `IMF.resolve_weo_vintage`, `weo_vintages_from_flows` | `test_weo_vintage_april_2025_from_older_bare_version`, `test_weo_vintage_flows_listed_and_fetchable` |
| An unknown dataflow answers with an empty body or 204/404 (as the structure endpoint does) | `fetch_sdmx` | `test_unknown_dataflow_is_an_error_or_empty` |
| DataMapper `/regions` and `/groups` return `{"regions"\|"groups": {code: {"label": ...}}}`, like `/indicators` and `/countries`. Without them, aggregates are classified by a static list of the seven three-letter regions plus the non-ISO rule | `parse_datamapper_groups`, `DATAMAPPER_REGION_CODES` | `test_datamapper_regions_and_groups` |
| `c[TIME_PERIOD]` for quarterly and monthly suffixes (`-Q1`/`-Q4`, `-M01`/`-M12`; annual is verified) | `time_filter_param` | `test_bop_kenya_current_account_and_time_filter` (annual only) |
| **Several countries in one DataMapper path, joined by `/`** (`/GGXWDG_NGDP/KEN/UGA`); a single country is verified | `build_datamapper_request` | `test_datamapper_kenya_debt_and_multi_country_path` |
| DataMapper `/indicators` `source` names the release ("World Economic Outlook (April 2026)"). One client describes it as "typically IMF". If it does not name the release, the vintage is left empty and the forecast boundary uses a release inferred from the retrieval date (`infer_release_from_date`, conservative: a release counts as out only after its month ends) | `infer_vintage_from_text`, `infer_release_from_date`, `fetch_datamapper` | `test_datamapper_indicator_metadata` |
| `reserve_assets` = `A_T.R_F`: the code exists, but the A_T ("Assets") entry semantics are unchecked | `BOP_ITEMS` | `test_bop_reserve_assets_entry` |
| `STATUS`/`OBS_STATUS` letters follow SDMX CL_OBS_STATUS (E, F, P, B, I). Unknown values are left blank and reported | `OBS_STATUS_MAP` | none; caveat `IMF_UNMAPPED_OBS_STATUS` |

## Endpoints and parameters

### Data (SDMX 3.0)

```
GET https://api.imf.org/external/sdmx/3.0/data/dataflow/{agency}/{flow}/{version}/{key}
    Accept: text/csv                (default; SDMX-CSV)
    Accept: application/json        (fmt="json"; SDMX-JSON 2.0, adds dimensionAtObservation=TIME_PERIOD&attributes=dsd&measures=all)
```

* `version`: `+` means the latest version of the dataflow.
* `key`: one slot per dimension in DSD order, `+` within a slot, `*` as a wildcard. Unknown codes return an empty message, not an error, so the connector reports requested series that did not come back (`IMF_MISSING_SERIES`).
* `attributes=series` (WEO data calls) returns the series and dimension-group attributes, including `LATEST_ACTUAL_ANNUAL_DATA`.
* `attributes=dataset&lastNObservations=1` returns `PUBLICATION_DATE` (`build_publication_probe`).
* `c[TIME_PERIOD]=ge:2016-A1+le:2018-A1` is the time filter. It is sent only to **IMF.STA** flows, because imfp says the other agencies ignore it. Years are always filtered client side as well.

`http.fetch` keys its cache on URL and parameters, not on headers. JSON requests therefore always carry `dimensionAtObservation` and CSV requests never do, so the two formats never share a cache entry.

### Structure

```
GET .../structure/dataflow/all/*/+                    catalogue (latest version of each flow)  -> IMF.list_dataflows
GET .../structure/dataflow/IMF.RES/*/*?detail=allstubs every IMF.RES flow and version          -> IMF.list_weo_vintages, IMF.resolve_weo_vintage
GET .../structure/codelist/{agency}/{id}/+            codes and names                          -> IMF.fetch_codelist
```

### DataMapper (fallback)

```
GET https://www.imf.org/external/datamapper/api/v1/{indicator}/{c1}/{c2}...    -> {"values": {...}}
GET https://www.imf.org/external/datamapper/api/v1/indicators                  -> {"indicators": {...}}
GET https://www.imf.org/external/datamapper/api/v1/regions | /groups           -> {"regions"|"groups": {code: {"label"}}} (UNVERIFIED)
```

The connector sends no `periods` parameter, so a cached payload serves any year range.

* **Aggregates.** DataMapper regions use three-letter codes that look like ISO3: SSQ "Sub-Saharan Africa (Region)", AFQ, APQ, EUQ, MEQ, NAQ, WHQ. `is_aggregate` treats these codes (`DATAMAPPER_REGION_CODES`) and every non-ISO code (`ADVEC`, `G001`) as aggregates. With `with_metadata=True`, `fetch_datamapper` also reads `/regions` and `/groups` (cached) and adds every code they list to `meta["aggregates"]`, filling `country` with their labels.
* **Forecast boundary.** The publication year comes from the `/indicators` `source` text when it names a release. Otherwise it is inferred from the retrieval date (`infer_release_from_date`). Release days vary (14 April 2026, 22 April 2025, 16 April 2024), so a release counts as out only after its month ends. January–April means the previous October, May–October means April, and November–December means October. Between January and April the DataMapper still serves the previous October WEO, in which the previous year is a projection, and this rule flags it as one.

### Errors

* HTTP ≥ 400: `http.fetch` raises `FetchError`, which becomes `IMFAPIError(status, message, code, correlationId)` by parsing the JSON envelope (`{message, code, correlationId, path}`).
* An HTML page, or JSON where CSV was expected, raises `IMFResponseError`.
* An empty body (204, or no match) yields an empty frame plus an `IMF_NO_DATA` or `IMF_MISSING_SERIES` caveat.

## World Economic Outlook (`IMF.fetch_weo`)

```python
imf = IMF()
res = imf.fetch_weo(["GGXWDG_NGDP", "GGXCNL_NGDP"], ["KEN", "UGA", "G001"], start=2006)
res = imf.fetch_weo("gross_debt", "KEN", vintage="2025-10")      # past release
```

* **Indicators.** Codes or aliases (`WEO_ALIASES`): `real_gdp_growth` → `NGDP_RPCH`, `gross_debt` → `GGXWDG_NGDP`, `net_lending`/`fiscal_balance` → `GGXCNL_NGDP`, `revenue` → `GGR_NGDP`, `expenditure` → `GGX_NGDP`, `current_account` → `BCA_NGDPD`, `primary_balance` → `GGXONLB_NGDP`, and others. Labels and units for common codes are in `WEO_INDICATORS`. Any other WEO code works, with its unit derived from the code suffix and the `UNIT` attribute.
* **Countries.** ISO3 codes and IMF aggregates (`G001` World, other `G…` codes; listed in `meta["aggregates"]`). World Bank aggregate codes (SSA, LMC, …) do not exist in WEO; the missing-series caveat says so. `None` means every economy.
* **Vintages.**
  * `vintage=None` reads `IMF.RES/WEO/+` plus a `PUBLICATION_DATE` probe, which gives `Provenance.vintage = "WEO 2026-04"` and an info caveat `IMF_VINTAGE_LATEST`. If the probe fails, the vintage is left empty and `IMF_VINTAGE_UNRESOLVED` (warn) is attached.
  * `vintage="2025-10"` (also `"Oct 2025"`, `("October", 2025)`, `"WEO_2025_OCT_VINTAGE"`) is resolved by `IMF.resolve_weo_vintage`, which follows imf-reader. Old versions of the bare `WEO` flow stay live and keep serving their release (April 2025 is `WEO` 6.0.0, and there is no `WEO_2025_APR_VINTAGE`), so the conventional vintage-flow name is not enough. The steps:
    1. List `structure/dataflow/IMF.RES/*/*?detail=allstubs`.
    2. Probe `PUBLICATION_DATE` for every version of `WEO` and of each `WEO_YYYY_MON_VINTAGE` (`build_publication_probe(flow, version)`; cached like every request).
    3. Label each (flow, version) by its probe. A vintage flow whose probe failed is labelled by its id. A bare version whose probe failed stays unlabelled.
    4. For each release, select the bare flow over a vintage flow, then the higher version.
    5. Request the data at that **explicit** version, e.g. `.../WEO/6.0.0/KEN.GGXWDG_NGDP.A`.
  * Cost: one catalogue call plus one single-observation probe per WEO (flow, version), a handful today. All of them are cached and pinned by `CGJRKIT_SNAPSHOT` like any payload, so a replication resolves to the same flow offline. The latest path (`vintage=None`) still needs only the data call and one probe.
  * The result records the resolution in `meta["vintage_resolution"]` (dataflow, version, publication date, label source, failed probes, available releases) and `meta["flow_version"]`. Its provenance lists the data call, then the catalogue and the probe used to pick the flow.
  * A release that no flow serves raises `IMFAPIError` (404) listing the available releases. If some probes failed, so the release may exist but could not be identified, the error names those probes. It is `NetworkBlocked` when they could not reach the API.
  * `IMF.list_weo_vintages()` returns the same table: dataflow, version, vintage, label, publication_date, label_source, selected, latest. Bare versions are labelled by the release they serve, never "latest" by assumption. `attrs["probe_failures"]` lists failed probes.
  * Releases before April 2025 are not on the API; they were only in the retired bulk SDMX archive.
* **Fallback.** If the SDMX call fails (`NetworkBlocked`, `IMFAPIError`, `IMFResponseError`) and `vintage` is None, the connector calls `fetch_datamapper` and attaches `IMF_FALLBACK_DATAMAPPER` and `IMF_VINTAGE_UNPINNED`. **An explicit vintage never falls back**, because the DataMapper serves only the latest release. SDMX aggregate codes (non-ISO, such as `G001`) are not DataMapper codes. They are left out of the DataMapper request, listed in `meta["missing"]`, and reported in `IMF_FALLBACK_AGGREGATES_DROPPED` (warn). If only aggregates were requested, the original SDMX error is raised.
* **Labels.** `labels=True` also reads `CL_WEO_COUNTRY` and `CL_WEO_INDICATOR` to fill `country` and any missing `indicator_label`.

### Forecast flags (`obs_status`)

`apply_forecast_flags`, run per series (country × indicator):

1. **The series has `LATEST_ACTUAL_ANNUAL_DATA` = L.** Years after L get `"forecast"`. With `distinguish_estimates=True` and a known publication year P, years in (L, P) get `"estimate"` and years ≥ P get `"forecast"`. FY forms use the leading year (`FY2022/23` → 2022) and add a `WEO_FISCAL_YEAR` caveat with `START_END_MONTHS_OF_REPORTING_YEAR`.
2. **No L, but P is known.** Years ≥ P get `"forecast"`, because a WEO never holds actuals for its own publication year. This is a lower bound and comes with `WEO_FORECAST_BOUNDARY_APPROX` (warn).
3. **Neither** (for example WEO aggregates after a failed `PUBLICATION_DATE` probe). The status is left as is. The series gets rule `"none"`, and `WEO_FORECAST_BOUNDARY_UNKNOWN` (warn) lists it, so its projection years are never silently presented as actuals. Every WEO series gets a rule (`weo_rules=True` for WEO data), even when the payload has no `LATEST_ACTUAL_ANNUAL_DATA` column.

Whenever forecasts are present, `WEO_FORECAST_YEARS` (warn) is attached, with the first forecast period of each series as evidence. The rule used for each series is in `meta["forecast_rules"]`. The per-series metadata (last actual, FY flag, reporting months, update date, unit code, scale) is in `meta["series"]`, capped at 250 records.

## Balance of payments (`IMF.fetch_bop`, `IMF.fetch_sdmx("BOP", ...)`)

The key is `COUNTRY.BOP_ACCOUNTING_ENTRY.INDICATOR.UNIT.FREQUENCY`, for example `KEN.NETCD_T.CAB.USD.A`. The canonical `indicator` is the key without country and frequency, for example `NETCD_T.CAB.USD`.

| Item (`BOP_ITEMS`) | Entry | Indicator | Kenya CGJR use |
|---|---|---|---|
| current_account | NETCD_T | CAB | Fig 10, Fig 13 |
| goods_services / goods / services | NETCD_T | GS / G / S | Fig 10 |
| primary_income / secondary_income | NETCD_T | IN1 / IN2 | Fig 10 |
| capital_account | NETCD_T | KAB | Fig 13 |
| errors_omissions | NETCD_T | EO | Fig 13 |
| financial_account | NNAFANIL_T | FAB | Fig 13 |
| direct_investment / portfolio_investment / other_investment | NNAFANIL_T | D_F / P_F / O_F | Fig 12, Fig 13 |
| reserve_assets (UNVERIFIED entry) | A_T | R_F | Fig 13 |

* `fetch_bop` sends one request (a cross product of entries and indicators) and drops series outside the requested pairs.
* Values are in **base units of US$** (SCALE 6 is informational). Ratios to GDP need WEO `NGDPD` (US$, base units); that division belongs in a method, not in the connector.
* **BPM6 signs.** `NNAFANIL_T` items are net acquisition of assets less net incurrence of liabilities, so a positive value is a net outflow. The Kenya Fig 12 "net FDI" chart flips the sign. Caveat: `BOP_BPM6_SIGN`.

## Other dataflows (`IMF.fetch_sdmx`)

`fetch_sdmx(dataflow, key, params, *, agency=None, version="+", start, end, fmt="csv", country_dim=None, indicator_dims=None, labels=None, units=None)`.

* The agency comes from `KNOWN_AGENCIES` (from the live catalogue). For `X_YYYY_MON_VINTAGE` flows it is the agency of X. Otherwise it is looked up in `structure/dataflow/all/*/+`.
* `iso3` comes from `COUNTRY` (or `REF_AREA`, `JURISDICTION`, `ECONOMY`). A flow without a country dimension gets `_Z`. `indicator` is the remaining non-frequency dimensions joined by `.`.
* WEO flows (`WEO`, `WEO_*_VINTAGE`) get WEO labels, units and forecast flags. No other flow gets forecast flags, including the regional REOs, which also contain projections; this is a known limitation.

## Units and scale

* `OBS_VALUE` is in base units (verified: R, P3, T1). `SCALE` (a power of ten) is recorded in `meta["scale"]` and the `IMF_SCALE_INFORMATIONAL` caveat, and is never applied.
* Unit labels: `WEO_INDICATORS` first. Otherwise the code suffix: `_NGDP`/`_NGDPD` → `% of GDP`, `_RPCH`/`_PCH` → `annual % change`. Otherwise `UNIT_LABELS` (`PT` percent, `USD` current US$, `XDC` current national currency, `IX` index, `PE` persons), and finally the raw code.
* **PPP.** The API does not state the ICP base year, and the PPP concepts have a null `UNIT_CODE` (imf-reader), so their units come from `WEO_INDICATORS`. **Levels:** `PPPGDP` and `PPPPC` are `"current international $ (PPP base year unknown)"`, `NGDPRPPPPC` is `"constant international $ (PPP base year unknown)"`, and any other level code containing PPP gets the generic `"international $ (PPP base year unknown)"`. `schema.ppp_base()` reads all of these as `"unknown"`, and they carry an `IMF_PPP_BASE_UNKNOWN` (warn) caveat. **Not PPP dollars:** `PPPSH` is `"% of world GDP (valued at purchasing power parity)"` and `PPPEX` is `"national currency per international $"`. Neither label contains the token "PPP", so `ppp_base()` returns `None` and PPP-mixing checks are not triggered. The generic rule skips codes ending in `SH` or `EX`.

## Caveats attached

| Code | Severity | When |
|---|---|---|
| `IMF_TERMS` | info | always (IMF terms: attribution; written permission for systematic downloading or commercial reuse) |
| `WEO_FORECAST_YEARS` | warn | forecast observations present |
| `WEO_ESTIMATE_YEARS` | info | `distinguish_estimates=True` and estimates present |
| `WEO_FORECAST_BOUNDARY_APPROX` | warn | some series lacked `LATEST_ACTUAL_ANNUAL_DATA`; publication-year rule used |
| `WEO_FISCAL_YEAR` | info | series with an FY last-actual |
| `WEO_FORECAST_BOUNDARY_UNKNOWN` | warn | series with neither a last actual nor a publication year (rule `"none"`): projections cannot be flagged |
| `IMF_VINTAGE_LATEST` | info | latest WEO resolved from PUBLICATION_DATE, or latest version of another flow |
| `IMF_VINTAGE_UNRESOLVED` | warn | PUBLICATION_DATE probe failed |
| `IMF_VINTAGE_UNPINNED` | warn | DataMapper values (latest WEO, not pinned by the provider). When the metadata names no release, the message and evidence (`release_assumed_from_retrieval_date`) say that the boundary was inferred from the retrieval date |
| `IMF_FALLBACK_DATAMAPPER` | warn | SDMX failed; DataMapper used |
| `IMF_FALLBACK_AGGREGATES_DROPPED` | warn | DataMapper fallback: SDMX aggregate codes (G-codes) requested, which the DataMapper does not use |
| `IMF_DM_GROUPS_UNAVAILABLE` | info | DataMapper `/regions` or `/groups` could not be read; aggregates are classified from the static list |
| `IMF_MISSING_SERIES` | warn | requested country/indicator pairs that returned no data |
| `IMF_NO_DATA` | warn | `fetch_sdmx` matched nothing |
| `IMF_SCALE_INFORMATIONAL` | info | non-zero SCALE present |
| `IMF_PPP_BASE_UNKNOWN` | warn | PPP-based units |
| `IMF_UNMAPPED_OBS_STATUS` | info | STATUS values without a canonical mapping |
| `IMF_LABELS_UNAVAILABLE` | info | codelists or DataMapper metadata could not be read |
| `BOP_BPM6_SIGN` | info | financial-account items requested |
| `BOP_BASE_UNITS` | info | every `fetch_bop` result |

## Kenya CGJR replication

| Figure | Series | Call |
|---|---|---|
| Fig 7: fiscal accounts, 2006–2024 | `GGR_NGDP`, `GGX_NGDP`, `GGXCNL_NGDP`, `GGXONLB_NGDP` | `fetch_weo([...], "KEN", start=2006, end=2024)` |
| Fig 9: interest/revenue vs gross debt, 2022 (93 countries) | `GGXWDG_NGDP` (the interest side comes from WDI) | `fetch_weo("GGXWDG_NGDP", None)`. **Target: Kenya 2023 ≈ 73% of GDP** (`test_kenya_gross_debt_2023_matches_report_fig9`, ±5 pp) |
| Fig 10: current account components | BOP CAB, GS, IN1, IN2 (US$) ÷ WEO NGDPD | `fetch_bop([...], "KEN", 2006, 2024)` and `fetch_weo("NGDPD", "KEN")` |
| Fig 12: net FDI (% of GDP) | BOP `NNAFANIL_T.D_F` with the BPM6 sign flipped | `fetch_bop("direct_investment", "KEN")` |
| Fig 13: BOP identity | BOP CAB, KAB, D_F, P_F, O_F, EO, R_F | `fetch_bop([...], "KEN")` |

The report used an older WEO (October 2023 or April 2024), which the API does not carry. Later vintages revise the numbers: Kenya's fiscal data are on a July–June fiscal year, and debt and GDP have been revised since. The live test therefore uses a ±5 pp tolerance.

## Tests

* `tests/test_imf.py` (83 tests, offline). It covers request builders, vintage parsing, the CSV and JSON readers (including dimension-group decoding and the suppressed-`values` case), CSV and JSON parity, forecast flags (plain, FY, aggregate, publication-year rule, estimates, the "none" rule and its caveat), PPP units, the BOP mapping, catalogue and codelist parsing, the vintage table and collision rules, DataMapper parsing (series, indicators, regions and groups), release inference from the retrieval date, and error parsing. It also runs the full fetch paths against a seeded cache: latest, vintage flow, current release by name, April 2025 from bare WEO 6.0.0, unavailable vintage, failed probes, DataMapper fallback (with G-codes dropped), no fallback for explicit vintages, probe failure, labels, BOP items and time filter, empty results, and agency lookup. `tests/review/test_review_connectors_wb_imf.py` holds the review regression tests.
* Fixtures: `tests/fixtures/imf_*_synthetic.{csv,json}`. They follow the formats above and hold made-up round values; METHODOLOGY_NOTES reads "SYNTHETIC TEST FIXTURE - not IMF data". The catalogue fixture `imf_weo_flows_synthetic.json` lists WEO 9.0.0, WEO 6.0.0 and `WEO_2025_OCT_VINTAGE` 1.0.0 (the layout imf-reader's tests use; no invented `WEO_2025_APR_VINTAGE`). Each flow has its own probe fixture (`imf_weo_publication*_synthetic.csv`).
* `tests/live/test_imf_live.py` (14 tests, `@pytest.mark.live`) checks the Kenya Fig 9 target and the UNVERIFIED rows above. Run with `CGJRKIT_LIVE=1 pytest -m live tests/live/test_imf_live.py` once `api.imf.org` and `www.imf.org` are allowlisted.

## Known limitations and requests for core

* **Cache key ignores headers.** `core.http.fetch` keys on URL and parameters only, so a CSV and a JSON request for the same URL and parameters would collide. The connector works around this (JSON always adds `dimensionAtObservation`). Request: include `Accept` (or an explicit cache-variant argument) in `cache.request_key`.
* **No per-host rate limiter.** The IMF publishes no limits for the new API; the legacy limit was 10 requests per 5 s, and imfp waits 1.5 s between calls. Request: a polite per-host limiter in `core.http` (for example 1 request per second for `api.imf.org`), which also helps with the IMF terms on systematic downloading.
* `core/http.py` mentions `docs/ALLOWLIST.md`, which does not exist yet. The hosts to add are `api.imf.org` and `www.imf.org`.
* REO flows (AFRREO and others) and Fiscal Monitor contain projections but get no forecast flags.
* The DataMapper fiscal codes `rev`/`exp` belong to the Fiscal Monitor (`FM`), not WEO. Pass them explicitly to `fetch_datamapper`; the fallback never swaps codes.
* Pending: the IMF answer on systematic downloading and commercial use (copyright@imf.org, plan §4). Until then, keep extracts small and pinned.
