# World Bank Indicators API v2 (`cgjrkit.connectors.worldbank`)

Connector: `WorldBank` in `src/cgjrkit/connectors/worldbank.py`.
Covers WDI (source 2), WGI (source 3), GFDD (source 32, id unverified), any other API source by id, and the **WDI Database Archives (source 57)** for pinning a past WDI release.

| Item | Value |
|---|---|
| Host (`domains`) | `api.worldbank.org` |
| Base URL | `https://api.worldbank.org/v2` |
| Auth | None (API v1 is retired; v2 needs no key) |
| Rate limits | None published. The connector uses large pages (`per_page=10000`) and the shared cache, so a typical panel is one request |
| Licence | CC BY 4.0 ([WB dataset terms](https://www.worldbank.org/en/about/legal/terms-of-use-for-datasets)). Some indicators come from third parties with their own terms, flagged in the indicator metadata. Attribute the World Bank and the dataset; do not imply endorsement |
| Citation | "World Bank, World Development Indicators" (WGI: "World Bank, Worldwide Governance Indicators"; GFDD: "World Bank, Global Financial Development Database"; archives: "World Bank, WDI Database Archives") |
| Vintage | Current data: `Provenance.vintage = "last updated <lastupdated>"` from the response header. Archives: `"WDI archive <YYYYMM>"` |

## How this was verified

The data hosts (api.worldbank.org, datahelpdesk.worldbank.org) are blocked in the build environment, so nothing was called live. The formats below come from these sources, read on 2026-10-11:

| Label | Source | What it established |
|---|---|---|
| D | [tgherzog/apidocs](https://github.com/tgherzog/apidocs): a rewrite of the Data Help Desk API pages. Its `index.md` maps each official page to its replacement. Files read: [API_Basic_Call_structures.md](https://raw.githubusercontent.com/tgherzog/apidocs/master/API_Basic_Call_structures.md), [API_New_Features_and_Enhancements.md](https://raw.githubusercontent.com/tgherzog/apidocs/master/API_New_Features_and_Enhancements.md), [API_Advanced_Data_Queries.md](https://raw.githubusercontent.com/tgherzog/apidocs/master/API_Advanced_Data_Queries.md), [API_Error_Codes.md](https://raw.githubusercontent.com/tgherzog/apidocs/master/API_Error_Codes.md), [API_Country_Queries.md](https://raw.githubusercontent.com/tgherzog/apidocs/master/API_Country_Queries.md), [API_Indicator_Queries.md](https://raw.githubusercontent.com/tgherzog/apidocs/master/API_Indicator_Queries.md), [API_Metadata.md](https://raw.githubusercontent.com/tgherzog/apidocs/master/API_Metadata.md) | Query parameters (`date`, `mrv`, `mrnev`, `gapfill`, `frequency`, `per_page` with a default of 50, `page`, `footnote`, `source`). Multiple indicators need `source=`. Limits: **60 indicators**, **1,500 characters per path segment**, **4,000 characters per URL**. `obs_status` "F" = forecast. The `countryiso3code` field. The country and indicator JSON. The advanced-query JSON, including the **source 57 example with `version/199704` ("1997 Apr") and time ids `YR1975`**. The error code table and the XML error format |
| P | [wbgapi 1.0.14](https://pypi.org/project/wbgapi/) sdist (`wbgapi/__init__.py`, `data.py`, `source.py`, `economy.py`, README) | The error envelope `[{"message":[{"key","value",...}]}]` and paging on `total`/`per_page`. The advanced response is a dict whose `source` is a dict with `data`. **Source 57 dimensions are country/series/time/version, with versions in `YYYYMM`**. README example: BRA `SP.POP.TOTL` 2000 = 174,174,447 in version 201004 and 175,786,441 in 201604. Aggregates are the countries with `region.id == "NA"`. Version members come from `sources/57/version/all` |
| P | [wbdata 1.1.0](https://pypi.org/project/wbdata/) sdist (`fetcher.py`, tests) | Error envelope with `id`, `key`, `value`. Pagination on `page`/`pages`. `lastupdated` (YYYY-MM-DD) in the header |
| P | wbwdi R package ([perform_request.R](https://raw.githubusercontent.com/tidy-intelligence/r-wbwdi/main/R/perform_request.R), [wdi_get_entities.R](https://raw.githubusercontent.com/tidy-intelligence/r-wbwdi/main/R/wdi_get_entities.R), [wdi_get_indicators.R](https://raw.githubusercontent.com/tidy-intelligence/r-wbwdi/main/R/wdi_get_indicators.R)) | `per_page` between 1 and 32,500. Country fields (`iso2Code`, `region`, `adminregion`, `incomeLevel`, `lendingType`, each `{id, iso2code, value}`). Aggregates have region "Aggregates". Indicator fields (`sourceNote`, `sourceOrganization`, `topics`) |
| P | wbstats R package ([wb_data.R](https://raw.githubusercontent.com/gshs-ornl/wbstats/master/R/wb_data.R)) | Row fields `unit`, `obs_status`, `decimal`, `footnote`. `obs_status` "F" = forecast |
| T | NanookAI worldbank-api skill, whose authors say they checked it against the live API ([SKILL.md](https://raw.githubusercontent.com/NanookAI/worldbank-api/main/skills/worldbank-api/SKILL.md), [endpoints.md](https://raw.githubusercontent.com/NanookAI/worldbank-api/main/skills/worldbank-api/references/endpoints.md), [indicators.md](https://raw.githubusercontent.com/NanookAI/worldbank-api/main/skills/worldbank-api/references/indicators.md)) | A full sample data row. Error envelopes arrive **with HTTP 200**. Error id **175** means the indicator was deleted or archived. `rows` is `null` when nothing matched. **WGI codes are now `GOV_WGI_*.EST`/`.SC`, need `source=3`, and `mrv`/`mrnev` fail on source 3**. Occasional transient HTML "Request Error" pages. Source ids 2, 3, 6, 15, 57 |

D = official documentation, read through the GitHub rewrite. P = the code of a maintained client. T = a third party.

### UNVERIFIED (each has a live test)

| Assumption | Where in the code | Live test |
|---|---|---|
| Source id **32 = Global Financial Development (GFDD)** | `SOURCES[32]` | `test_gfdd_source_id` |
| Archive data path without a trailing `/data` (wbgapi style; the docs show both forms) | `build_request` | `test_wdi_archive_source57_version_fetch` |
| Version listing at `/sources/57/version/all`, in the concept-variable format | `fetch_archive_versions` | `test_wdi_archive_versions_listing` |
| `obs_status` letters other than F (E, P, B, I, A, M, as in SDMX CL_OBS_STATUS) | `OBS_STATUS_MAP` | Only F is documented. Unknown letters are left blank and raise `WB_UNMAPPED_OBS_STATUS` |
| `mrv`/`mrnev` on the archive (advanced) endpoint | `build_request` passes them through | not tested |
| `per_page` up to 32,500 accepted by the server | `MAX_PER_PAGE` | `test_pagination_follows_pages` uses 100 |
| The static aggregate list `KNOWN_AGGREGATES` is current | `aggregate_codes()` | `test_countries_and_aggregates` (the live list is added after `fetch_countries()`) |
| Response fields as documented | `parse_page` | `test_raw_response_fields_match_documented_format` |

## Endpoints and parameters

### Indicator data (sources other than 57)

```
GET /v2/country/{c1;c2|all}/indicator/{i1;i2}?format=json&source=2&date=2001:2024&per_page=10000&page=1
```

- The connector always sends `source=`. The API requires it for multi-indicator requests and for indicators outside WDI (WGI). Without it the API picks the indicator's home source.
- `date=start:end` takes years, `2013Q1:2013Q4` or `2012M01:2012M08`. `YTD:2013` exists for high-frequency data.
- With only `start` or only `end`, the connector sends no `date` and filters on the client, so results stay deterministic.
- `mrv` and `mrnev` are exclusive with `start`/`end` (the API treats them as replacements). For WGI, use an explicit date range (T).
- Lists of codes are split into requests of at most 60 indicators, with path segments of at most 1,400 characters (wbgapi's head room), checked against the 4,000-character URL limit (`plan_chunks`).
- Pages: the connector follows `pages` from the header (numbers may be strings) and makes one cached request, with its own `Provenance`, per page.

Row format (D, T):

```json
[{"page":1,"pages":1,"per_page":50,"total":2,"sourceid":"2","lastupdated":"2026-07-01"},
 [{"indicator":{"id":"NY.GDP.MKTP.KD.ZG","value":"GDP growth (annual %)"},
   "country":{"id":"KE","value":"Kenya"},"countryiso3code":"KEN","date":"2020",
   "value":-0.3,"unit":"","obs_status":"","decimal":1}]]
```

Mapping to the canonical frame:

- `indicator.id` → `indicator`; `indicator.value` → `indicator_label`.
- `countryiso3code` → `iso3`. If it is empty, `country.id` is used and the `WB_ISO3_MISSING` caveat is added.
- `date` → `period` (`2020`, `2020Q1`, `2020M03`→`2020-03`).
- `value` → float (`null`→NaN, and the row is kept).
- `unit`: see below. `obs_status`: see below.
- `dataset` is `WDI`, `WGI` or `GFDD`, or `WB source <id>` for other sources.

### WDI Database Archives (source 57): vintage pinning

Source 57 is a four-dimension database: country, series, time and **version**. A version is one WDI release, `YYYYMM` (e.g. `201004` = "2010 Apr"). It is queried through the *advanced* endpoint (D, P):

```
GET /v2/sources/57/country/{c}/series/{s1;s2}/time/{YR2000;YR2001;...|all}/version/{YYYYMM}?format=json
```

```json
{"page":1,"pages":2,"per_page":50,"total":67,"lastupdated":"2017-07-13",
 "source":{"id":"57","name":"WDI Database Archives",
   "data":[{"variable":[{"concept":"Country","id":"ALB","value":"Albania"},
                        {"concept":"Version","id":"199704","value":"1997 Apr"},
                        {"concept":"Series","id":"AG.AGR.TRAC.NO","value":"Agricultural machinery, tractors"},
                        {"concept":"Time","id":"YR2016","value":"2016"}],
            "value":null}]}}
```

- Call `fetch_indicators(..., version="YYYYMM")` with `source=2` (routed to 57) or `source=57`. A version with any other source raises `ValueError`, and so does source 57 without a version.
- Years are sent as an explicit `YR` list, because range syntax is not documented for this endpoint. A one-sided range sends `time/all` and filters on the client.
- The dataset is `"WDI archive <YYYYMM>"`, so several vintages can share one frame without duplicate keys. `Provenance.vintage = "WDI archive <YYYYMM>"`, the version label goes into `notes`, and `Result.meta["version_label"]` holds the label.
- List the versions with `fetch_archive_versions()`.
- Caveat `WDI_ARCHIVE_CODE_REUSE`: WDI reuses series codes across base years (NY.GDP.MKTP.KD in constant 2010 vs 2015 US$), and aggregate definitions change between releases.
- To pin a *current* request for later replays, use a cache snapshot (`CGJRKIT_SNAPSHOT=<name>`). The archive gives a provider-side vintage; the snapshot gives the exact bytes.

### Reference endpoints

| Method | Endpoint | Output |
|---|---|---|
| `fetch_countries()` | `/v2/country?per_page=1000` | DataFrame: `iso3, iso2, name, region_id, region, admin_region_id, admin_region, income_level_id, income_level, lending_type_id, lending_type, capital_city, longitude, latitude, is_aggregate`. Aggregates have `region.id == "NA"` / "Aggregates". Classifications are the current fiscal year's; historical income groups are not in this endpoint. Calling it also adds the live ISO3 and ISO2 aggregate codes to `aggregate_codes()` (union with the static snapshot). Provenance is in `df.attrs["provenance"]` |
| `fetch_indicator_meta(code, source=None)` | `/v2/indicator/{code}[?source=]` | dict: `id, name, unit` (derived, see below), `unit_api, ppp_base, source_id, source_name, source_note, source_organization, topics, modelled_ilo, provenance`. When an indicator belongs to several sources, pass `source` |
| `fetch_sources()` | `/v2/sources` | DataFrame: `id, code, name, lastupdated, data_available, metadata_available, concepts` |
| `fetch_archive_versions()` | `/v2/sources/57/version/all` | DataFrame: `id` (YYYYMM), `label` |

### Errors

| Form | Example | Connector |
|---|---|---|
| JSON envelope (often HTTP 200) | `[{"message":[{"id":"120","key":"Invalid value","value":"The provided parameter value is not valid"}]}]` | `WorldBankAPIError(message_id, key, value)` with a hint (120 = bad code or uncovered economy; 175 = archived series, try its successor or source 57) |
| XML | `<wb:error><wb:message id="120" key="Invalid value">...</wb:message></wb:error>` | `WorldBankAPIError` |
| HTML / non-JSON (transient "Request Error") | — | `WorldBankResponseError`. Retried once with a fresh request that bypasses the cache |
| HTTP ≥ 400 | handled by `core.http` | `FetchError`. If the body is the JSON envelope, it is re-raised as `WorldBankAPIError` |

Official error codes (D): 105 service unavailable, 110 version not found, 111 format not found, 112 method not found, 115 missing parameter, 120 invalid value, 140 endpoint not found, 150 language not supported, 160 filtering on a value needs a date range, 199 unexpected error. Error 175 (deleted or archived indicator) comes from T.

## Units and PPP base (CONVENTIONS)

The API's `unit` field is usually empty. WDI states the unit in the trailing parenthetical of the indicator name, so `unit_from_name` works as follows:

- `"GDP growth (annual %)"` → `annual %`
- Qualifiers are skipped: `"Unemployment, total (% of total labor force) (modeled ILO estimate)"` → `% of total labor force`
- PPP-based units get their base year appended:
  - `"GDP per capita, PPP (constant 2021 international $)"` → `constant 2021 international $ (PPP 2021)`
  - `"Poverty headcount ratio at $3.00 a day (2021 PPP) (% of population)"` → `% of population (PPP 2021)`
- `current international $` names carry no year. They get `(PPP)`, so `schema.ppp_base()` returns `"unknown"`, and the `PPP_BASE_UNSTATED` caveat is added (the base is whichever ICP round that release used).
- A non-empty API `unit` wins.

## Observation status

| API flag | `obs_status` | Status |
|---|---|---|
| (empty) | `""` | D |
| `F` | `forecast` | D ("forecast") |
| `E`, `P`, `B`, `I` | `estimate`, `provisional`, `break`, `imputed` | UNVERIFIED (SDMX CL_OBS_STATUS letters) |
| `A`, `M` | `""` | UNVERIFIED |
| other | `""`, plus caveat `WB_UNMAPPED_OBS_STATUS` | — |

Flag counts go into `Result.meta["obs_status_raw"]`. Series whose name says "(modeled ILO estimate)" are tagged `modelled` when the API sets no flag. That lets QA checks catch splices of ILO-modelled and survey-based labour series.

## Aggregates

`iso3` keeps the World Bank aggregate codes (`LMC`, `UMC`, `SSA` = Sub-Saharan Africa excluding high income, `SSF` = all of Sub-Saharan Africa, `WLD`, ...). The codes present in a result are listed in `Result.meta["aggregates"]`. The connector uses the static snapshot `KNOWN_AGGREGATES`, plus the live list once `fetch_countries()` (or `fetch_indicators(..., resolve_aggregates=True)`) has run. Kosovo is `XKX` and the Channel Islands `CHI`: World Bank codes, not ISO.

## Caveats attached by `fetch_indicators`

| Code | Severity | When |
|---|---|---|
| `WB_NO_DATA` | warn | empty result |
| `WB_FORECAST` | warn | any `obs_status == forecast` |
| `ILO_MODELLED` | warn | ILO modelled series present |
| `PPP_BASE` | info | PPP-based units present (lists the bases) |
| `PPP_MIXED_BASES` | warn | more than one known PPP base in the result |
| `PPP_BASE_UNSTATED` | info | current international $ series (base unknown) |
| `WDI_ARCHIVE_CODE_REUSE` | info | archive (source 57) request |
| `WGI_2025_REVISION` | info | source 3: 2025 methodology revision, new 0–100 scores, `GOV_WGI_*` codes |
| `GFDD_STALE` | info | source 32: last updated 2022, data to 2021 |
| `LPI_SURVEY_SERIES` | info | `LP.LPI.*`: survey-based LPI to 2023; LPI 2.0 (Data360 `WB_LPI_20`) is a break |
| `ENTREPRENEURSHIP_LLC_ONLY` | info | `IC.BUS.NREG` / `IC.BUS.NDNS.ZS`: counts newly registered LLCs only |
| `WB_UNMAPPED_OBS_STATUS`, `WB_ISO3_MISSING`, `WB_DUPLICATES_DROPPED`, `WB_INCOMPLETE_ROWS` | warn / info | data-quality notes |

## Coverage notes

- **Entrepreneurship Database** is supported through WDI: `IC.BUS.NREG` (new businesses registered, number) and `IC.BUS.NDNS.ZS` (new business density, new registrations per 1,000 people ages 15–64).
- **Logistics Performance Index** (survey editions to 2023) is supported through WDI `LP.LPI.*` (e.g. `LP.LPI.OVRL.XQ`, overall score, 1–5). LPI 2.0 is on Data360, not this API.
- **WGI** live in source 3 under `GOV_WGI_<dim>.EST` (estimate, about −2.5 to 2.5) and `.SC` (0–100 score) (T). The old `CC.EST`-style codes return error 175. Reports from before 2025 need the archives or their own snapshot.
- **GFDD** is fetched with `source=32` (id unverified) and is stale.

## Kenya CGJR replication

| Target | Call | Check |
|---|---|---|
| Fig 1: real GDP growth, Kenya, LMIC, UMIC, SSA excl. high income, 2001–2024 | `fetch_indicators("NY.GDP.MKTP.KD.ZG", ["KEN","LMC","UMC","SSA"], 2001, 2024)` | Live: Kenya ~8.1 in 2010, ~−0.3 in 2020 (±0.5) |
| Figs 14, 22, 29 (2017 PPP) | `version=` a WDI release from before the 2021 PPP switch, via `fetch_archive_versions()` | Do not mix with current 2021-PPP series (`PPP_MIXED_BASES`) |
| Fig 23: FDI (% of GDP) | `BX.KLT.DINV.WD.GD.ZS` | — |

## Tests

- Unit tests: `tests/test_worldbank.py`, run offline. Payloads are pre-loaded into an isolated cache under the exact URL and params, so the full fetch path, including provenance, is exercised.
- Fixtures, all synthetic, in the documented formats, with a `_note` key in the header:
  - `worldbank_indicator_single_page_synthetic.json`
  - `worldbank_indicator_page1_synthetic.json` and `worldbank_indicator_page2_synthetic.json` (multi-page, multi-indicator)
  - `worldbank_indicator_empty_synthetic.json`
  - `worldbank_error_synthetic.json`
  - `worldbank_archive_synthetic.json` (advanced format)
  - `worldbank_countries_synthetic.json`
  - `worldbank_indicator_meta_synthetic.json`
  - `worldbank_archive_versions_synthetic.json`
  - `worldbank_sources_synthetic.json`
- Live tests: `tests/live/test_worldbank_live.py` (`CGJRKIT_LIVE=1 pytest -m live tests/live/test_worldbank_live.py`). They cover Kenya Fig 1, the peer aggregates, multi-indicator requests, pagination, the raw field names, the source 57 archive (against the wbgapi README values), the version listing, countries, metadata, WGI, the GFDD id, the Entrepreneurship and LPI series, and the error envelope.

## Known limitations and requests for core

- `core.http.fetch` caches every 2xx payload, including error envelopes and HTML error pages. The connector retries transient errors once with `refresh=True`, but a cached deterministic error (e.g. 120) stays cached. Request: give `http.fetch` a `validate(payload)` hook so a connector can reject a payload before it is cached.
- No footnotes (`footnote=y`), no `gapfill`, no JSON-stat. Language is fixed to English.
- Sub-annual sources (GEM 15, quarterly debt 20/22/23) work through `start`/`end` strings such as `"2013Q1"`, but no specific tests cover them.
