# Tool catalog

This page lists every public tool in cgjrkit: the MCP tools an AI client calls, the connectors, methods, charts and checks behind them, and how far each one has been tested. State on 2026-10-11, version 0.1.0.

**How to read the status columns**

* **Unit tests (offline).** Tests run without the network, on synthetic fixtures in the provider's documented format or on hand-computed examples. All 837 offline tests pass (`.venv/bin/pytest`).
* **Live tests (pending).** These tests call the real API and, where possible, check values published in the reports. None has run yet, because the build environment blocks the provider hosts (see [ALLOWLIST.md](ALLOWLIST.md)). They run with `CGJRKIT_LIVE=1 .venv/bin/pytest -m live` once the hosts are allowed.
* **Unverified assumptions.** The number of API behaviours or method choices each builder could not confirm, taken from the builders' reports and `docs/apis/<provider>.md`. Each one is marked UNVERIFIED in the code or docs, and most have a live test. The count leaves out the general caveat that no host could be reached.

In the per-function tables, **unit** means that the function is called by name in that module's offline tests. **indirect** means it is exercised only through other functions, or not at all. **live (pending)** means a live test calls it.

## MCP tools (preview server)

`cgjrkit-mcp` (or `python -m cgjrkit.mcp_server`) serves these tools over stdio; the module is `src/cgjrkit/mcp_server.py`. Each tool that produces data stores the `Result` in a per-session store and returns a `result_id`, which later tools accept wherever a frame is expected. Summaries contain the first rows, the data hash, provenance, caveats and meta, never the full payload. A frame may also be passed inline as canonical records; inline data get a `warn` caveat (`inline_data_unverified`).

| Tool | Wraps | Inputs | Output | Status |
|---|---|---|---|---|
| `list_connectors` | `cgjrkit.connectors.CONNECTORS` | none | each connector's key, class, licence, citation, hosts, canonical-frame flag, and the methods `get_series` accepts (signatures) | unit |
| `get_series` | any connector method annotated `-> Result` | `connector`, `params` (keyword arguments), `method='fetch'`, `max_rows` | `result_id` + summary; refuses the parameters `url`, `local_zip`, `token` and `force` (no arbitrary URLs, local files or secrets in tool arguments, and no bypass of the OECD hourly request budget) | unit (served from a seeded cache); live calls depend on the allowlist |
| `peer_aggregate` | `methods.peers.peer_aggregate` | `data`, `members`, `peerset_name`, `stats`, `min_n`, `frozen_at`, `approver`, `criteria`, `code`, `focus`, `by`, `drop_below_min_n`, `on_ppp_mix` | `result_id` + summary (iso3 `<CODE>:<stat>`, columns `stat`, `n`, `group`) | unit |
| `growth_accounting` | `methods.growth_accounting.growth_accounting` | `panel` (one or several result ids, or records), `periods` {label: [base, end]}, `alpha`, `iso3`, indicator names, `weights`, `per_worker`, `units` | `result_id` + summary (component rows; `meta['alpha_sensitivity']`) | unit |
| `shift_share` | `methods.structural_change.shift_share` | `va_by_sector`, `emp_by_sector`, `start`, `end`, `variant`, sector maps, `aggregate_va`/`aggregate_emp`, `annualize`, `on_gap`, `emp_scale`, `iso3` | `result_id` + summary | unit |
| `jobs_shapley` | `methods.jobs_shapley.jobs_shapley` | `gdp`, `population`, `working_age`, `employment`, `periods`, optional sector frames and maps, `sector_variant`, `method`, `annualize`, scales, `iso3` | `result_id` + summary | unit |
| `benchmark_fit` | `methods.benchmark.income_conditional_fit` | `x`, `y`, `period` (one or [start, end]), `kind`, `log_x`, `focus`, `exclude`, `log_y`, `focus_path`, `ci`, `frac`, `grid_points`, `percentile_method`, `aggregates`, `on_ppp_mix` | `result_id` + summary (`meta['fit']`, `meta['focus']`) | unit |
| `render_chart` | `qa.caveat_check.run_caveat_check` + `charts.render.chart_from_result` + `save_chart` | `result`, `kind` (line, bar, dual_axis, scatter, kde, slope, stacked), `output_dir`, `stem`, `formats`, `title`, `subtitle`, `note`, `right`, `options` (builder options; `band`/`size` take a result_id; `where` filters rows), `include_info` | file paths per format (+ CSV of the plotted data), footer lines, `status`, the inputs' caveats and the `caveat_check` result; refuses inputs the caveat engine BLOCKs (Result caveats and engine rules), reserved options, and existing files (see below) | unit |
| `run_caveat_check` | `qa.caveat_check.run_caveat_check` | `results` (ids or records), `context`, `rules`, `skip` | `status` PASS/WARN/BLOCK, caveats, footnotes, rules run, inherited caveats, rule errors | unit |

`render_chart` guards, in order:

* **Provenance footer.** The source line is always built from the provenance of every Result used (`result`, `right`, and Result-valued options). The options `source_line`, `note`, `include_info`, `right`, `result` and `kind` are refused (`mcp_server.RESERVED_CHART_OPTIONS`); extra text goes in `note`, above the caveat notes and the source line.
* **No typed data.** The frame-valued builder options `band` (`line`) and `size` (`scatter`) take a result_id from the session, a `[low, high]` pair of result_ids (`band`), or inline records (flagged `inline_data_unverified`); value mappings typed by the client are refused.
* **Caveat engine.** The inputs go through `run_caveat_check` with the default context (no replication mode, no redistribution). Any BLOCK, from a rule (`ppp_mixing`, `ilo_modelled` splices, `missing_provenance`, ...) or a caveat the inputs already carry, refuses the chart and nothing is written. The engine's footnotes (forecast years, ILO modelled estimates, series breaks, COVID-19 endpoints) are added to the footer. Run `run_caveat_check` yourself with a context (for example `{"mode": "replication"}`) for stricter checks.
* **Files.** `render_chart` never replaces a file it did not write in this session: if any of `<stem>.<format>` or `<stem>.csv` exists, the call is refused before anything is rendered. With `--output-root DIR` (or `CGJRKIT_OUTPUT_DIR`), `output_dir` must lie inside DIR (relative paths are taken inside it); without a root it may be any directory the server process can write to.
* **Method defaults for `kind="stacked"`** (`charts.render.method_chart_defaults`): `jobs_shapley` stacks the three factors under the `gdp_per_capita` total (sector rows left out), `shift_share` the economy-wide terms under `total` (`where={"sector": "TOTAL"}`), `growth_accounting` the factors under `output` (or `output_per_worker`), `sector_contributions` one segment per sector. `options` override them; `options["where"]` (e.g. `{"component": "within"}` with `"component": "sector"`) charts another slice.

`tests/test_mcp_server.py` (21 tests) calls every tool through the MCP SDK client, both in-process and over the real stdio entry point, with hand-checked values; `tests/review/test_review_charts_qa_mcp.py` (12 tests) holds the review probes for the charts, the caveat engine and the server. Unverified: behaviour with desktop MCP clients other than the SDK's own client.

## Status by module

| Module | Public tools | Unit tests | Live tests (pending) | Unverified assumptions | Hosts | Notes |
|---|---|---|---|---|---|---|
| `connectors.worldbank` | 20 | 58 | 13 | 10 | api.worldbank.org | [apis/worldbank.md](apis/worldbank.md) |
| `connectors.data360` | 28 | 17 | 9 | 6 | data360api.worldbank.org | [apis/data360.md](apis/data360.md) |
| `connectors.pip` | 14 | 24 | 6 | 4 | api.worldbank.org | [apis/pip.md](apis/pip.md) |
| `connectors.imf` | 34 | 73 | 12 | 11 | api.imf.org, www.imf.org | [apis/imf.md](apis/imf.md) |
| `connectors.ilostat` | 29 | 30 | 8 | 9 | rplumber.ilo.org, sdmx.ilo.org | [apis/ilostat.md](apis/ilostat.md) |
| `connectors.wpp` | 22 | 18 | 7 | 4 | population.un.org | [apis/wpp.md](apis/wpp.md) |
| `connectors.pwt` | 21 | 35 | 7 | 6 | dataverse.nl, www.rug.nl | [apis/pwt.md](apis/pwt.md) |
| `connectors.bruegel` | 23 | 25 | 4 | 5 | www.bruegel.org | [apis/bruegel.md](apis/bruegel.md) |
| `connectors.unctad` | 17 | 15 | 5 | 4 | unctadstat-api.unctad.org, unctadstat-user-api.unctad.org | [apis/unctad.md](apis/unctad.md) |
| `connectors.baci` | 30 | 12 | 2 | 1 | www.cepii.fr | [apis/baci.md](apis/baci.md) |
| `connectors.atlas` | 8 | 10 | 3 | 2 | atlas.hks.harvard.edu | [apis/atlas.md](apis/atlas.md) |
| `connectors.oecd` | 13 | 17 | 5 | 4 | sdmx.oecd.org | [apis/oecd.md](apis/oecd.md) |
| `connectors.openalex` | 16 | 18 | 6 | 4 | api.openalex.org | [apis/literature.md](apis/literature.md) |
| `connectors.crossref` | 13 | 16 | 6 | 3 | api.crossref.org | [apis/literature.md](apis/literature.md) |
| `connectors.handles` | 18 | 50 | 3 | 1 | none (link checks only) | [apis/literature.md](apis/literature.md) |
| `methods.harmonize` | 17 | 41 | 0 | 0 | none | module docstring |
| `methods.peers` | 7 | 14 | 0 | 1 | none | module docstring |
| `methods.benchmark` | 7 | 25 | 3 | 2 | none (live tests use WDI) | module docstring |
| `methods.growth_accounting` | 3 | 18 | 3 | 4 | none (live tests use PWT) | module docstring |
| `methods.structural_change` | 7 | 25 | 3 | 4 | none (live tests use WDI, ILOSTAT) | module docstring |
| `methods.jobs_shapley` | 3 | 12 | (in structural_change live) | 1 | none | module docstring |
| `methods.standard_tables` | 5 | 8 | 0 | 2 | none | module docstring |
| `charts` (series, benchmark, decomposition, render) | 21 | 57 | 0 | 4 | none (renders offline) | package docstring |
| `qa.caveat_check` | 6 | 83 | 0 | 3 | none | [caveat-rules.md](caveat-rules.md) |
| `registries` | 13 | 11 | 41 (host reachability) | 5 | none | `registries/hosts.yaml`, `licences.yaml` |
| `mcp_server` | 9 MCP tools | 21 | 0 | 1 | as the connectors | this page |
| package registries (`connectors.CONNECTORS`, `methods.METHODS`, `qa`) | 3 | 5 | 0 | 0 | none | `tests/test_package_exports.py` |
| `core` (schema, provenance, cache, http) | n/a | 8 | 0 | 0 | none | [CONVENTIONS.md](CONVENTIONS.md) |

Not implemented yet: `cgjrkit.replication` (YAML figure specs, the replication runner and `replication/targets/`) and `cgjrkit.workbench` (the HTML workbench builder) are empty placeholder packages; they are planned, and no tool depends on them.

Registries for code: `cgjrkit.connectors.CONNECTORS` maps each provider key to its connector class (loaded lazily). `cgjrkit.methods.METHODS` maps each method name to its function. Every public method function can also be imported from `cgjrkit.methods`, except `growth_accounting` and `jobs_shapley`, whose names belong to their modules.

## Functions by module

The tables below are generated from the code (signatures, return annotations, the first docstring line). Defaults longer than 14 characters are shown as `…`. Connector methods inherited from `Connector` (`provenance`, `finalize`) and internal result containers (`Parsed*` dataclasses and similar) are left out.

#### `cgjrkit.connectors.worldbank`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `normalize_period` | date | str | API dates to canonical periods: `2020`, `2020Q1`, `2020M03` -> `2020-03`, `YR2020` -> `2020`. | unit |
| `unit_from_name` | name, api_unit='' | str | Unit label for a series, following CONVENTIONS. | unit |
| `map_obs_status` | raw, label='' | tuple[str, bool] | Map an API `obs_status` flag to `schema.OBS_STATUS`. | unit |
| `plan_chunks` | indicators, countries | list[tuple[list[str], list[str] \| str]] | Split a request so each URL respects the API limits (60 indicators, 1,500 characters per path segment). | unit |
| `build_request` | indicators, countries='all', start=None, end=None, source=2, version=None, mrv=None, *, mrnev=None, per_page=10000, page=1 | tuple[str, dict[str, str]] | Return `(url, params)` for one page of one indicator/country chunk. | unit, live (pending) |
| `parse_page` | payload, *, source=2, version=None, url='' | ParsedPage | Parse one page of an indicator or advanced (archive) data response. | unit |
| `parse` | payload, *, source=2, version=None, url='' | DataFrame | Canonical frame for one data payload (either response format), sorted. | unit |
| `parse_countries` | payload, url='' | DataFrame | `/v2/country` page -> one row per economy or aggregate. | unit |
| `parse_indicator_meta` | payload, source=None, url='' | dict | `/v2/indicator/{code}` -> metadata dict. | unit |
| `parse_concept_variables` | payload, url='' | DataFrame | Members of one dimension of a multi-dimensional source, e.g. the versions of the WDI Database Archives: `id` and `label` columns. | unit |
| `parse_sources` | payload, url='' | DataFrame | `/v2/sources` page -> id, code, name, lastupdated, availability flags. | unit |
| `build_caveats` | df, *, source, version, unknown_obs_status=None, missing_iso3=(), duplicates_dropped=0, incomplete_dropped=0 | list[Caveat] | Data caveats for an indicator result (pure). | unit |
| `WorldBank` | cache=None | WorldBank | World Bank Indicators API v2: WDI, WGI, GFDD, other sources, WDI archives. | unit, live (pending) |
| `WorldBank.aggregate_codes` |  | frozenset[str] | Aggregate codes: the static snapshot, plus the live ISO3/ISO2 codes once fetch_countries() has run (the union keeps codes that older WDI... | unit, live (pending) |
| `WorldBank.fetch` | indicators, countries='all', **kwargs | Result | Alias of `fetch_indicators`. | unit, live (pending) |
| `WorldBank.fetch_archive_versions` | refresh=False | DataFrame | Versions (WDI releases) in the WDI Database Archives: `id` (`YYYYMM`) and `label` (e.g. `"2010 Apr"`). | unit, live (pending) |
| `WorldBank.fetch_countries` | refresh=False | DataFrame | Economies and aggregates with ISO codes, region, income level, lending type and an `is_aggregate` flag. Classifications are the current f... | unit, live (pending) |
| `WorldBank.fetch_indicator_meta` | code, source=None, refresh=False | dict | Indicator metadata: name, unit (derived per CONVENTIONS, PPP base included), source note, source organization, topics, provenance. | unit, live (pending) |
| `WorldBank.fetch_indicators` | indicators, countries='all', start=None, end=None, source=2, version=None, mrv=None, *, mrnev=None, per_page=10000, resolve_aggregates=False, refresh=False | Result | Fetch indicator observations as a canonical `Result`. | unit, live (pending) |
| `WorldBank.fetch_sources` | refresh=False | DataFrame | Databases available through the API (id, code, name, lastupdated, ...). | unit, live (pending) |

#### `cgjrkit.connectors.data360`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `build_data_request` | database_id, indicators=None, countries=None, start_year=None, end_year=None, filters=None, skip=0, top=1000 | tuple[str, dict] | Return `(url, params)` for one page of `GET /data360/data`. | unit, live (pending) |
| `build_metadata_request` | indicator_id=None, database_id=None, select=None | tuple[str, dict] | Return `(url, json_body)` for `POST /data360/metadata`. | unit, live (pending) |
| `build_indicators_request` | database_id | tuple[str, dict] | Return `(url, params)` for `GET /data360/indicators` (indicator ids of a database). | unit |
| `build_disaggregation_request` | database_id, indicator_id | tuple[str, dict] | Return `(url, params)` for `GET /data360/disaggregation`. | unit |
| `build_codelist_request` | codelist_type | tuple[str, dict] | Return `(url, params)` for `GET /data360/codelist?type=...` (REF_AREA, UNIT_MEASURE). | unit |
| `build_search_request` | query, top=10, skip=0, filter=None, select=… | tuple[str, dict] | Return `(url, json_body)` for `POST /data360/searchv2` (OpenAPI `SearchQuerySchema`). | unit |
| `parse_page` | payload | tuple[list[dict], int \| None] | Return `(rows, total_count)` from one `/data360/data` response. | unit, live (pending) |
| `normalize_period` | time_period, freq='' | str | Map a Data360 `TIME_PERIOD` to the canonical period string. | unit |
| `is_aggregate` | code | bool | True for World Bank aggregate codes and non-ISO3 groupings (e.g. `G_LLD`). | unit |
| `parse_comment_obs` | text | list[tuple[str, object]] | Split a `COMMENT_OBS` string into `(field, value)` pairs. | unit |
| `parse_rows` | rows, *, database_id='', totals_only=True, keep_dims=(), detect_statistics=False, unit_labels=None, indicator_units=None, indicator_labels=None, dim_labels=None, country_names=None | ParsedData | Map Data360 rows onto the canonical frame (pure). | unit |
| `parse` | payload, **options | DataFrame | Parse one `/data360/data` payload into the canonical frame (see `parse_rows`). | indirect |
| `parse_metadata` | payload | dict[str, dict] | Return `{indicator_id: {...}}` from a `/data360/metadata` response. | unit |
| `parse_indicators` | payload | list[str] | Return the indicator ids from `/data360/indicators` (a bare JSON list of ids). | unit |
| `parse_codelist` | payload | dict[str, str] | Return `{code: name}` from `/data360/codelist` (items `{"Id": ..., "Name": ...}`). | unit |
| `parse_disaggregation` | payload | dict[str, list[str]] | Return `{field_name: [values]}` from `/data360/disaggregation`. | unit |
| `parse_search` | payload | list[dict] | Return the `series_description` records of a `/data360/searchv2` response. | unit |
| `is_cc_by` | license_name | bool | True for plain CC BY licences; False for NC/ND variants and anything else. | unit |
| `extras_frame` | result | DataFrame | Pivot `result.meta["extras"]` to one row per observation (se, n, n_obs_group, ...). | unit |
| `Data360` | cache=None | Data360 | World Bank Data360 API (no key needed; no documented rate limit). | unit, live (pending) |
| `Data360.codelist` | codelist_type, *, refresh=False | dict[str, str] | Global codelist `{code: name}` (REF_AREA or UNIT_MEASURE). | unit, live (pending) |
| `Data360.disaggregation` | database_id, indicator_id, *, refresh=False | dict[str, list[str]] | Available dimension values (TIME_PERIOD, REF_AREA, SEX, COMP_BREAKDOWN_x, ...). | indirect, live (pending) |
| `Data360.fetch` | database_id, indicators=None, countries=None, start_year=None, end_year=None, *, filters=None, totals_only=True, keep_dims=(), detect_statistics=False, with_metadata=True, with_labels=True, refresh=False, page_size=1000, max_pages=500, tool=… | Result | Fetch observations from `/data360/data` (all pages) as a canonical Result. | unit, live (pending) |
| `Data360.fetch_enterprise_surveys` | indicators, countries=None, start_year=None, end_year=None, *, breakdowns=False, **kwargs | Result | Enterprise Surveys aggregates (Data360 database `WB_ES`). | unit, live (pending) |
| `Data360.fetch_lpi20` | indicators=None, countries=None, *, metrics=None, **kwargs | Result | Logistics Performance Indicators 2.0 (Data360 database `WB_LPI_20`). | unit, live (pending) |
| `Data360.list_indicators` | database_id, *, refresh=False | list[str] | Indicator ids available in a Data360 database (e.g. `WB_ES`, `WB_LPI_20`). | indirect, live (pending) |
| `Data360.metadata` | indicator_id=None, database_id=None, *, select=None, refresh=False | dict[str, dict] | Indicator metadata (name, unit, version date, licence, citation) keyed by indicator id. | unit, live (pending) |
| `Data360.search` | query, *, top=10, skip=0, filter=None, refresh=False | list[dict] | Free-text indicator search (vector search on the metadata index). | unit, live (pending) |

#### `cgjrkit.connectors.pip`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `line_code` | povline | str | Poverty line as used in indicator codes and units: `3.0 -> "3.00"`, `2.155 -> "2.155"`. | indirect |
| `build_request` | countries='all', years='all', povline=3.0, ppp_version=2021, release_version=None, version=None, fill_gaps=False, *, welfare_type='all', reporting_level='national', nowcast=False | tuple[str, dict] | Return `(url, params)` for `GET /pip`. | unit, live (pending) |
| `build_group_request` | regions='all', years='all', povline=3.0, ppp_version=2021, release_version=None, version=None | tuple[str, dict] | Return `(url, params)` for `GET /pip-grp?group_by=wb` (WB regions and World). | unit |
| `build_versions_request` |  | tuple[str, dict] | Return `(url, params)` for `GET /versions` (CSV). | unit |
| `parse_versions` | payload | list[dict] | Return `[{"version", "release_version", "ppp_version"}]` from `/versions`, newest first. | unit |
| `resolve_version` | versions, ppp_version, release_version=None | dict | Pick the version record for a PPP round: the given release, or the latest one. | unit, live (pending) |
| `parse_rows` | table, *, ppp_version, povline=None, measures=None, drop_nowcast=False, aggregate=False | ParsedPoverty | Map a PIP table onto the canonical frame (pure). | unit |
| `parse` | payload, *, ppp_version, povline=None, measures=None, drop_nowcast=False, aggregate=False | DataFrame | Parse one `/pip` (or `/pip-grp` with `aggregate=True`) payload into the canonical frame. | unit |
| `poverty_mix_caveats` | data | list[Caveat] | Caveats for frames that combine PIP poverty lines or PPP rounds. | unit |
| `PIP` | cache=None | PIP | World Bank Poverty and Inequality Platform API (no key). | unit, live (pending) |
| `PIP.fetch` | *args, **kwargs | Result | Alias of `fetch_poverty`. | unit, live (pending) |
| `PIP.fetch_aggregates` | regions='all', years='all', povline=3.0, ppp_version=2021, release_version=None, *, measures=None, refresh=False | Result | World Bank regional and global aggregates (PIP `/pip-grp?group_by=wb`). | unit, live (pending) |
| `PIP.fetch_poverty` | countries, years='all', povline=3.0, ppp_version=2021, release_version=None, fill_gaps=False, *, welfare_type='all', reporting_level='national', measures=None, nowcast=False, refresh=False | Result | Poverty and inequality statistics for countries (PIP `/pip`). | unit, live (pending) |
| `PIP.versions` | *, refresh=False | tuple[list[dict], Provenance] | Available PIP data versions (newest first) and the provenance of the lookup. | unit, live (pending) |

#### `cgjrkit.connectors.imf`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `build_key` | *parts | str | Positional SDMX key: one slot per dimension, `+` within a slot, `*` for a wildcard (`None`, `""` or `"*"`). | unit |
| `normalize_period` | raw | str | SDMX period -> canonical period: `2024`, `2024-A1` -> `2024`; `2024-Q1` -> `2024Q1`; `2024-M03`/`2024-03` -> `2024-03`. | unit |
| `parse_last_actual` | raw | tuple[int \| None, bool] | `LATEST_ACTUAL_ANNUAL_DATA` -> (year, is_fiscal_year). | unit |
| `parse_vintage` | vintage | tuple[int, int] \| None | Normalise a WEO vintage to `(year, month)` with month 4 or 10. | unit |
| `weo_flow_for` | vintage | str | `None` -> `"WEO"` (latest); `(2025, 10)` -> `"WEO_2025_OCT_VINTAGE"`. | unit |
| `vintage_label` | vintage | str | `(2026, 4)` -> `"WEO 2026-04"` (the Provenance.vintage convention). | unit |
| `infer_vintage_from_text` | text | tuple[int, int] \| None | `"World Economic Outlook (April 2026)"` -> `(2026, 4)`. | unit |
| `build_data_request` | agency, flow, key, *, version='+', params=None, fmt='csv' | tuple[str, dict[str, str], dict[str, str]] | `(url, params, headers)` for an SDMX 3.0 data query. | unit, live (pending) |
| `build_weo_request` | indicators, countries=None, vintage=None, *, fmt='csv' | tuple[str, dict[str, str], dict[str, str]] | WEO data request: key `COUNTRY.INDICATOR.A` on `IMF.RES/WEO` (latest) or `IMF.RES/WEO_YYYY_MON_VINTAGE`, asking for series attributes so... | unit |
| `build_publication_probe` | flow='WEO', version='+' | tuple[str, dict[str, str], dict[str, str]] | One-observation request that returns the dataset attribute PUBLICATION_DATE. | unit |
| `time_filter_param` | start, end, freq='A' | dict[str, str] | SDMX 3.0 time filter `c[TIME_PERIOD]=ge:2016-A1+le:2018-A1`. | unit |
| `build_datamapper_request` | indicator, countries=None | tuple[str, dict[str, str]] | DataMapper series URL: `/api/v1/{indicator}` plus `/{c1}/{c2}...`. | unit |
| `read_sdmx_csv` | payload, url='' | DataFrame | Parse an SDMX-CSV data message into the raw table. | unit, live (pending) |
| `read_sdmx_json` | payload, url='' | DataFrame | Parse an SDMX-JSON 2.0 data message (series-keyed, `dimensionAtObservation=TIME_PERIOD`) into the same raw table as `read_sdmx_csv`. | unit |
| `apply_forecast_flags` | frame, last_actual, *, publication_year=None, distinguish_estimates=False | tuple[DataFrame, dict[str, str]] | Set `obs_status` for WEO-style series. | unit |
| `parse_sdmx` | payload, *, fmt='csv', dataset='', url='', **kwargs | ParsedSDMX | Pure: SDMX-CSV/JSON payload -> `ParsedSDMX` (see `raw_to_canonical`). | unit |
| `parse_weo` | payload, *, fmt='csv', publication_year=None, distinguish_estimates=False, country_names=None, url='' | ParsedSDMX | Pure: WEO data message -> canonical frame with forecast flags. | unit |
| `parse_publication_date` | payload, fmt='csv' | str | ISO PUBLICATION_DATE from an `attributes=dataset` probe, or `""`. | unit |
| `parse_dataflows` | payload | DataFrame | Dataflow catalogue (`data.dataflows`) -> id, agency, version, name, structure, last_updated (the `lastUpdatedAt` annotation; not reliable... | unit |
| `parse_codelist` | payload | DataFrame | Codelist message -> `code`, `name` (English). | unit |
| `parse_datamapper_indicators` | payload | DataFrame | `/api/v1/indicators` -> code, label, description, source, unit, dataset. | unit |
| `parse_datamapper` | payload, *, dataset=…, labels=None, units=None, countries=None | DataFrame | DataMapper series JSON `{"values": {IND: {ISO: {"YYYY": value}}}}` -> canonical frame (obs_status left empty; see `apply_forecast_flags`). | unit |
| `IMF` | cache=None | IMF | IMF data: WEO (latest and vintage dataflows), BOP and any SDMX 3.0 dataflow on api.imf.org; DataMapper fallback for the latest WEO. | unit, live (pending) |
| `IMF.fetch` | indicators, countries=None, **kwargs | Result | Alias of `fetch_weo`. | unit, live (pending) |
| `IMF.fetch_bop` | items, countries, start=None, end=None, *, unit='USD', freq='A', refresh=False | Result | Balance-of-payments items (`BOP_ITEMS` names, or `"ENTRY.INDICATOR"` pairs) from IMF.STA `BOP`, in base units of `unit` (default US$). | unit, live (pending) |
| `IMF.fetch_codelist` | codelist_id, agency='IMF', refresh=False | DataFrame | Codes and English names, e.g. `("CL_WEO_COUNTRY", "IMF.RES")`. | unit |
| `IMF.fetch_datamapper` | indicators, countries=None, start=None, end=None, *, with_metadata=True, refresh=False | Result | Latest-WEO values from the DataMapper API (one request per indicator). | unit, live (pending) |
| `IMF.fetch_datamapper_indicators` | refresh=False | DataFrame |  | indirect, live (pending) |
| `IMF.fetch_sdmx` | dataflow, key='*', params=None, *, agency=None, version='+', start=None, end=None, fmt='csv', country_dim=None, indicator_dims=None, labels=None, units=None, server_time_filter=True, refresh=False | Result | Any SDMX 3.0 dataflow as a canonical `Result`. | unit, live (pending) |
| `IMF.fetch_weo` | indicators, countries=None, vintage=None, start=None, end=None, *, fallback=True, distinguish_estimates=False, labels=False, refresh=False | Result | World Economic Outlook series as a canonical `Result`. | unit, live (pending) |
| `IMF.list_dataflows` | agency='all', refresh=False | DataFrame | Dataflow catalogue (latest version of each flow). | indirect |
| `IMF.list_weo_vintages` | refresh=False | DataFrame | WEO dataflows: bare `WEO` (latest) and `WEO_YYYY_MON_VINTAGE` flows, every version, from the `detail=allstubs` catalogue of IMF.RES. | unit, live (pending) |
| `IMF.resolve_agency` | flow, refresh=False | str | Agency of a dataflow: static table, vintage-flow base, then the catalogue. | unit |
| `IMF.weo_publication_date` | flow='WEO', refresh=False | tuple[str, Retrieval] | PUBLICATION_DATE of a WEO flow (ISO date, `""` when absent). | unit, live (pending) |

#### `cgjrkit.connectors.ilostat`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `resolve_indicator` | indicator, freq='A', modelled=True | tuple[str, str] | Return `(code, freq)` for an ILOSTAT id, an indicator code or a catalog name. | unit |
| `classification_dims` | code | list[str] | Classification segments of an indicator code: `EMP_2EMP_SEX_ECO_NB` -> `["SEX", "ECO"]`; `NOC` (no classification) -> `[]`. | unit |
| `normalize_period` | time | str | ILOSTAT time codes to canonical periods: `2024`; `2024Q1`/`2024q1` -> `2024Q1`; `2024M03`/`2024m03` -> `2024-03`. | unit |
| `edition_from_label` | label | str | Edition of an ILO modelled series from its label, normalised to `"Nov. 2025"`. | unit, live (pending) |
| `normalize_edition` | edition | str | Accept `"Nov 2023"`, `"November 2023"`, `"Nov. 2023"`, `"2023-11"` -> `"Nov. 2023"`. | unit |
| `classif_label` | code | str | Human label for a classification code (built-in table; combined ISIC sections are composed and suffixed with "[label composed by cgjrkit]... | unit |
| `unit_from_label` | label, code='' | str | Unit label from an ILOSTAT indicator label (trailing parenthetical), with PPP units carrying their base year as CONVENTIONS require. | unit |
| `request_url` | url, params | str | Compose `url?query` keeping `+` literal between multiple values, as the published ILOSTAT examples do (httpx would encode it as `%2B`). | unit, live (pending) |
| `build_request` | indicator, countries=None, start=None, end=None, *, freq='A', sex=None, classif1=None, classif2=None, type='code', lang='en' | tuple[str, dict[str, str]] | Bulk data request `(url, params)` for one indicator file. | unit, live (pending) |
| `build_toc_request` | lang='en' | tuple[str, dict[str, str]] |  | unit |
| `build_dictionary_request` | var, lang='en' | tuple[str, dict[str, str]] |  | indirect |
| `build_sdmx_request` | indicator, countries=None, start=None, end=None, *, freq='A', sex=None, classif1=None, classif2=None | tuple[str, dict[str, str]] | SDMX REST data request. Key = REF_AREA.FREQ.MEASURE.<one dimension per classification segment of the code> (UNVERIFIED layout, see module... | unit |
| `read_table` | payload, url='' | DataFrame | Decode a CSV payload (gzip or plain, optional BOM) to an all-string frame. | unit, live (pending) |
| `parse_toc` | payload, url='' | DataFrame | Table of contents -> frame with `id, indicator, label, freq, last_update, database, modelled, edition` plus the remaining provider column... | unit |
| `parse_dictionary` | payload, url='' | DataFrame | Code list -> frame `code, label` (first two columns of the provider file; the rest is kept). | unit |
| `standardize` | table, *, indicator='', countries=None, start=None, end=None, sex=None, classif1=None, classif2=None, sources=None, best_source=True, toc=None, dataset=None | ParsedILO | Turn an ILOSTAT observation table (bulk CSV columns) into the canonical frame. | indirect |
| `parse` | payload, *, url='', **filters | ParsedILO | Parse a bulk CSV payload (see `standardize` for `filters`). | unit |
| `parse_sdmx` | payload, *, indicator, url='', **filters | ParsedILO | Parse an SDMX-CSV payload into the same structure as `parse`. | unit |
| `vintage_for` | parsed, toc | str | `"ILOEST Nov. 2025"` for modelled series with a known edition; otherwise `"ILOSTAT update YYYY-MM-DD"` from the table of contents, or "". | indirect |
| `build_caveats` | parsed, *, expect_edition=None | list[Caveat] | Caveats for a parsed ILOSTAT result (modelled data, editions, sources, flags). | unit |
| `ILOSTAT` | cache=None | ILOSTAT | ILOSTAT bulk download service and SDMX API. | unit, live (pending) |
| `ILOSTAT.employment_by_sector` | countries=None, start=None, end=None, *, level='aggregate', sex='SEX_T', modelled=True, **kw | Result | Employment by economic activity (thousands). `level`: `"sector"` (agriculture, industry, services), `"aggregate"` (ISIC aggregates: AGR,... | unit, live (pending) |
| `ILOSTAT.employment_to_population_ratio` | countries=None, start=None, end=None, *, sex='SEX_T', age=…, modelled=True, **kw | Result | Employment-to-population ratio (%), ILOEST by default. | indirect |
| `ILOSTAT.fetch` | indicator, countries=None, start=None, end=None, *, freq='A', sex=None, classif1=None, classif2=None, sources=None, best_source=True, modelled=True, via='bulk', type='code', use_toc=True, expect_edition=None, refresh=False | Result | Fetch one ILOSTAT indicator as a canonical `Result`. | unit, live (pending) |
| `ILOSTAT.fetch_dictionary` | var, lang='en', refresh=False | DataFrame | Code list (`classif1`, `source`, `obs_status`, `ref_area`, ...) as `code, label`. | indirect |
| `ILOSTAT.fetch_toc` | lang='en', refresh=False | DataFrame | Table of contents by indicator (one row per bulk file). Provenance in `attrs`. | indirect, live (pending) |
| `ILOSTAT.labour_force_participation_rate` | countries=None, start=None, end=None, *, sex='SEX_T', age=…, modelled=True, **kw | Result | Labour force participation rate (%), ILOEST by default. | indirect |
| `ILOSTAT.output_per_worker` | countries=None, start=None, end=None, *, basis='2015usd', **kw | Result | Output per worker (ILOEST): `basis="2015usd"` (GDP constant 2015 US$) or `"ppp"` (constant international $ at PPP; base year in `unit`). | unit, live (pending) |
| `ILOSTAT.unemployment_rate` | countries=None, start=None, end=None, *, sex='SEX_T', age=…, modelled=True, **kw | Result | Unemployment rate (%), ILOEST by default. | unit, live (pending) |

#### `cgjrkit.connectors.wpp`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `normalize_variant` | variant | str | Canonical variant name (`"medium"` -> `"Medium"`, `3` -> `"High"`). | unit |
| `dataset_for` | variant | str | `"WPP2024"` for the Medium variant, `"WPP2024 High"` etc. otherwise, so variants never collide on the canonical key. | unit |
| `normalize_countries` | countries | tuple[list[str] \| None, list[str]] | Return `(iso3_codes, loc_ids)`; `None`/`"all"` -> `(None, [])`. | unit |
| `parse_age_group` | label | tuple[int, float] | `"15-19"` -> `(15, 19)`; `"100+"` -> `(100, inf)`. | unit |
| `build_request` | key | tuple[str, dict] | Bulk file request `(url, params)`; bulk files take no parameters. | unit, live (pending) |
| `read_bulk` | payload, columns, row_filter=None, *, chunksize=250000, url='' | tuple[DataFrame, list[str], int] | Read selected columns of a WPP CSV (gzip or plain, BOM-tolerant) in chunks, keeping only rows that pass `row_filter`. Returns `(frame, mi... | unit, live (pending) |
| `parse_bulk` | payload, value_columns, *, countries='all', start=None, end=None, variant='Medium', include_aggregates=False, age=False, reference='', file='', url='' | ParsedWPP | Parse a WPP bulk CSV into the canonical long frame. | unit |
| `aggregate_ages` | age_frame, bands=…, *, dependency=True, shares=True | DataFrame | Sum 5-year (or single-year) age groups into bands, per location, period and sex. | unit |
| `pyramid_table` | age_frame, iso3, period | DataFrame | Population pyramid for one location and year: rows are age groups (youngest first), columns `male`, `female` (thousands) and `male_pct`,... | unit, live (pending) |
| `read_token` | token=None | str | Token from the argument or `CGJRKIT_WPP_KEY`; a pasted `"Bearer "` prefix is removed. | unit |
| `build_api_request` | indicator_ids, location_ids, start, end, *, page=1, page_size=100 | tuple[str, dict[str, str]] | `/data/indicators/{ids}/locations/{ids}/start/{y0}/end/{y1}` with JSON paging (`pagingInHeader=false`, `pageNumber`, `pageSize`; third-pa... | unit |
| `build_locations_request` | page=1, page_size=100 | tuple[str, dict[str, str]] |  | unit |
| `parse_api` | payload, url='' | tuple[DataFrame, str \| None] | Data Portal JSON page -> `(canonical frame, nextPage)`. | unit, live (pending) |
| `parse_locations` | payload, url='' | tuple[DataFrame, str \| None] |  | unit |
| `build_caveats` | info, frame=None, *, fallback_note='' | list[Caveat] |  | unit |
| `WPP` | cache=None | WPP | UN World Population Prospects 2024 (bulk CSV; Data Portal API with a token). | unit, live (pending) |
| `WPP.age_structure` | countries, start=None, end=None, *, variant='Medium', sexes=…, reference='jul1', include_aggregates=False, refresh=False | Result | Population by 5-year age group and sex (thousands), the input of population pyramids (`pyramid_table`). Indicators look like `PopFemale\|2... | unit, live (pending) |
| `WPP.api_locations` | refresh=False, max_pages=100 | DataFrame | Locations from the Data Portal (`id` = UN location id, `iso3`); no token needed (third-party report). | indirect |
| `WPP.fetch` | indicators=…, countries='all', start=None, end=None, *, variant='Medium', include_aggregates=False, refresh=False | Result | Demographic indicators (`TPopulation1July`, `TFR`, `LEx`, `MedianAgePop`, ...). | unit, live (pending) |
| `WPP.fetch_api` | indicator_ids, locations, start, end, *, token=None, max_pages=1000, refresh=False | Result | Data Portal API query (token required: `CGJRKIT_WPP_KEY`). `locations` are UN location ids or ISO3 codes (resolved with `api_locations`). | unit, live (pending) |
| `WPP.total_population` | countries='all', start=None, end=None, *, variant='Medium', sexes=('total',), include_aggregates=False, refresh=False | Result | Total population (thousands, 1 July) by sex from `WPP2024_TotalPopulationBySex.csv.gz` (all variants). `sexes`: any of `"total"`, `"male"... | unit, live (pending) |
| `WPP.working_age_population` | countries, start=None, end=None, *, bands=…, sexes=('total',), variant='Medium', reference='jul1', dependency=True, shares=True, refresh=False | Result | Working-age population (`PopTotal\|15-64`, `PopTotal\|15+` in thousands), shares of the population, and dependency ratios, summed from 5-ye... | unit, live (pending) |

#### `cgjrkit.connectors.pwt`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `normalize_version` | version | str | `"10.01"`, `"1001"`, `"pwt1001"`, `"PWT 10.01"` -> `"10.01"`; `"11"`/`"110"` -> `"11.0"`. | unit |
| `get_version` | version | PWTVersion |  | indirect |
| `normalize_variables` | variables | list[str] | Lower-cased, de-duplicated variable names (order kept). Flags are rejected. | unit |
| `normalize_countries` | countries | list[str] \| None | Upper-case ISO3 codes, or `None` for all countries. | unit |
| `variable_meta` | variable, reference_year | tuple[str, str] | `(label, unit)` for a variable at a reference year; unknown variables -> `(variable, "")`. | unit |
| `is_ppp_based` | variable | bool |  | unit |
| `build_request` | version='10.01', file_format='xlsx' | tuple[str, dict] | `(url, params)` for the pinned data file of a version. | unit |
| `build_file_request` | file_id, *, original=False | tuple[str, dict] | Dataverse access request for a file id; `original=True` asks for the saved original of an ingested tabular file (`format=original`). | unit |
| `build_dataset_request` | version='10.01' | tuple[str, dict] | Dataverse native API request for the dataset record (latest published version). | unit |
| `parse_dataset_files` | payload | tuple[list[dict], str] | Return `(files, dataset_version)` from a Dataverse dataset JSON record. | unit |
| `select_file` | files, version, file_format='xlsx' | dict | Pick the main PWT data file of a version from a Dataverse file list. | unit, live (pending) |
| `checksum_ok` | payload, checksum_type, value | bool \| None | Compare a payload with a Dataverse checksum; `None` when it cannot be checked. | unit, live (pending) |
| `sniff_format` | payload | str | `"xlsx"`, `"zip"`, `"dta"`, `"tsv"`, `"csv"`, `"html"` or `"json"`. | unit |
| `read_table` | payload | PWTTable | Read a PWT file (xlsx, dta, tab/csv text or a zip holding one) into a wide table. | unit |
| `infer_reference_year` | wide | int \| None | Year in which `rkna` (or `rtfpna`/`rwtfpna`) equals 1 for most countries. | unit |
| `parse` | payload, *, version='10.01', variables=None, countries=None, start=None, end=None, dropna=True | ParsedPWT | Parse a PWT data file into the canonical long frame. | unit |
| `build_caveats` | info | list[Caveat] |  | unit |
| `pwt_mix_caveats` | objs | list[Caveat] | Block combining PWT rows on different PPP bases; warn on mixed PWT versions. | unit |
| `PWT` | cache=None | PWT | Penn World Table 10.01 / 11.0 (DataverseNL, DOI-pinned) and 10.0 (rug.nl). | unit, live (pending) |
| `PWT.dataset_files` | version='10.01', refresh=False | tuple[list[dict], str, Provenance] | List the files of a version's Dataverse record: `(files, dataset_version, provenance)`. | unit, live (pending) |
| `PWT.fetch` | version='10.01', variables=…, countries=None, start=None, end=None, *, file_format='xlsx', resolve=False, url=None, dropna=True, refresh=False | Result | PWT variables as a canonical frame. | unit, live (pending) |

#### `cgjrkit.connectors.bruegel`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `code_to_iso3` | code | tuple[str, bool] | Map a Bruegel country code to ISO3. Returns `(code_or_iso3, mapped)`; aggregates (`EA`) and unknown codes are returned unchanged with `ma... | unit |
| `normalize_frequency` | frequency | list[str] | `"monthly"`/`"m"` -> `["MONTHLY"]`; `"all"`/`None` -> every frequency. | unit |
| `normalize_measure` | measure | list[str] |  | unit |
| `normalize_baskets` | baskets | list[int] \| None |  | unit |
| `normalize_countries` | countries | list[str] \| None | Upper-case ISO3 / ISO2 / provider codes, or `None` for all. | unit |
| `parse_version` | name | str \| None | `"REER_database_ver22Sep2026.zip"` -> `"2026-09-22"`; `None` when absent. | unit, live (pending) |
| `sheet_info` | name | tuple[str, str, int] \| None | `"REER_MONTHLY_120"` -> `("REER", "MONTHLY", 120)`; `None` for other sheets. | unit |
| `indicator_label` | measure, frequency, basket | str |  | unit |
| `find_download_links` | html, base=… | list[str] | REER workbook links (zip/xls/xlsx under `/sites/default/files/`) in a page, in order. | unit |
| `pick_latest` | links | str \| None | The link with the newest `ver<DDMonYYYY>` date (zip preferred on ties); else the first link. | unit |
| `build_request` | url=None | tuple[str, dict] | `(url, params)` for the workbook: explicit URL, `$CGJRKIT_BRUEGEL_URL` or the fallback. | unit |
| `build_page_request` |  | tuple[str, dict] |  | unit |
| `open_workbook` | payload | Workbook | Open the Bruegel workbook from a zip or a bare .xls/.xlsx payload. | unit, live (pending) |
| `parse_readme` | raw | dict[str, str] | Code -> country name from the `READ ME` sheet. | unit, live (pending) |
| `parse_sheet` | raw, sheet | tuple[DataFrame, list[str]] | Long frame `code, period, value` from one REER/NEER sheet, plus skipped headers. | indirect, live (pending) |
| `infer_base_year` | long, tol=0.05 | str \| None | Year whose average index is 100 for most series (`None` if no clear base). | unit |
| `select_sheets` | sheets, measures, frequencies, baskets | tuple[list[str], list[int]] | Data sheets matching the request, and requested baskets with no sheet. | indirect |
| `parse` | payload, *, countries=None, frequency='monthly', baskets=None, measure='REER', start=None, end=None, dropna=True, source_name='' | ParsedREER | Parse the Bruegel workbook (zip, .xls or .xlsx) into the canonical frame. | unit, live (pending) |
| `build_caveats` | info, *, sha256='', history=(), fallback_url=False, url='' | list[Caveat] |  | unit |
| `BruegelREER` | cache=None, url=None | BruegelREER | Bruegel REER/NEER database (zipped Excel workbook, overwritten in place). | unit, live (pending) |
| `BruegelREER.fetch` | countries=None, frequency='monthly', baskets=None, measure='REER', start=None, end=None, *, url=None, dropna=True, refresh=False | Result | REER (or NEER) indices as a canonical frame. | unit, live (pending) |
| `BruegelREER.resolve_url` | url=None, refresh=False | tuple[str, bool, list[Provenance]] | Workbook URL: argument, instance URL, `$CGJRKIT_BRUEGEL_URL`, the dataset page, then the fallback. | unit, live (pending) |
| `BruegelREER.sheets` | url=None, refresh=False | DataFrame | Catalogue of data sheets: `sheet, measure, frequency, basket` (no values parsed). | unit, live (pending) |

#### `cgjrkit.connectors.unctad`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `bulk_file_name` | report | str | Bulk file stem: `US.FdiFlowsStock` -> `US_FdiFlowsStock`. | indirect |
| `build_metadata_request` | report=… | tuple[str, dict] |  | unit |
| `build_bulk_request` | report=…, version=None | tuple[str, dict] | Bulk 7z URL; `version` pins a release (path form UNVERIFIED for this report). | unit, live (pending) |
| `to_m49` | code | str | ISO3 (`KEN`) or M49 (`404`/`0000`) -> UNCTAD economy code. | unit |
| `economy_codes` | countries | set[str] | UNCTAD economy codes for ISO3/M49 inputs (`None` or empty = no filter). | unit |
| `build_api_request` | report=…, measures=…, economies=None, years=None | tuple[str, dict, tuple[str, str]] | Keyed Facts query: `(url, fields, (client id header, client secret header))`. | unit |
| `api_credentials` |  | tuple[str, str] | `(client id, client secret)` from the environment, or a clear error. | unit |
| `parse_metadata` | payload | ReportMetadata | Read `/api/reportMetadata/{report}/en`. | unit |
| `read_archive` | payload | tuple[str, str] | `(member name, CSV text)` from a 7z, zip, gzip or plain-CSV payload. | unit, live (pending) |
| `measure_code_for_label` | label | str \| None | Measure code of a bulk-CSV value column label, or `None`. | unit |
| `parse_bulk_csv` | text | DataFrame | Long raw table from the bulk CSV (`Year, Economy, Economy Label, Direction( Label), Flow( Label)` + one column per measure). | unit |
| `parse_facts_csv` | text | DataFrame | Long raw table from the Facts CSV (`Economy_Code, ..., M0100_Value, M0100_Footnote_Text, ...`). | unit |
| `to_canonical` | raw, countries=None, start=None, end=None, measures=…, series=None | ParsedFDI | Canonical frame from a raw long table (bulk or Facts). | unit |
| `UNCTAD` | cache=None | UNCTAD | UNCTADstat FDI flows and stocks (bulk 7z by default; keyed data API optional). | unit, live (pending) |
| `UNCTAD.fdi` | countries=None, start=None, end=None, measures=…, series=None, *, route='bulk', version=None, with_metadata=True, refresh=False | Result | FDI inflows, outflows and stocks as a canonical frame. | unit, live (pending) |
| `UNCTAD.fetch` | *args, **kwargs | Result | Alias of `fdi`. | indirect, live (pending) |
| `UNCTAD.metadata` | report=…, refresh=False | tuple[ReportMetadata, Provenance] | Release version, last update and measures of a report (keyless). | unit, live (pending) |

#### `cgjrkit.connectors.baci`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `normalize_hs` | hs | str | `"HS92"`, `"92"`, `"hs1992"` -> `"HS92"`. | unit |
| `normalize_version` | version | str | `"202601"`, `"V202601"`, `"202401b"` -> the bare release stamp. | unit |
| `build_request` | hs='HS92', version='202601' | tuple[str, dict] | URL of the zip for one HS revision and release. | unit |
| `archive_members` | names | ArchiveMembers | Classify the members of a BACI zip; raise if the layout is not BACI's. | unit, live (pending) |
| `parse_readme` | text | dict[str, str] | `key: value` blocks of `Readme.txt` (`Version`, `Release Date`, `Content`...). | unit, live (pending) |
| `read_country_codes` | payload | DataFrame | `code` (int), `name`, `iso2`, `iso3` from `country_codes_V*.csv`. | unit |
| `read_product_codes` | payload | DataFrame | `code` (6-digit text) and `description` from `product_codes_HS*_V*.csv`. | unit |
| `sha256_file` | path, chunk=1048576 | str |  | indirect |
| `extract_archive` | zip_path, dest, years=None, zip_sha256='' | dict | Extract the requested years (all when `None`) of a BACI zip to Parquet in `dest`. | unit |
| `q_exporter_totals` | con, files, codes=None | DataFrame | Per exporter-year: total exports (exact), HS6 products and destinations with positive value. | unit |
| `q_importer_totals` | con, files, codes=None | DataFrame | Per importer-year: total imports (exact, FOB-reconciled) and origins with positive value. | unit |
| `q_market_hhi` | con, files, codes=None | DataFrame | Export-market concentration: HHI (0-1) of destination shares, markets, top-market share. | unit |
| `q_product_hhi` | con, files, codes=None, level=6 | DataFrame | Export-product concentration: HHI (0-1) of product shares at HS `level` digits. | unit |
| `q_rca` | con, files, codes=None, level=6, products=None | DataFrame | Balassa RCA = (x_cp / X_c) / (x_wp / X_w); world = all BACI exporters that year. | unit |
| `q_count_products` | con, files, codes=None, level=6, min_value=None, min_rca=None | DataFrame | Number of products per exporter-year with exports above `min_value` (thousand US$) and/or RCA at or above `min_rca`. | unit |
| `q_country_product` | con, files, level=4, codes=None | DataFrame | Exporter x product x year export values (exact sums) for complexity methods. | unit |
| `codes_for` | countries, wanted | list[int] \| None | BACI numeric codes for ISO3 codes (or numeric codes); `None` = all. | unit |
| `to_frame` | df, countries, columns, dataset | tuple[DataFrame, dict] | Canonical frame from an aggregated table with `code, year` and value columns. | indirect |
| `baci_mix_caveats` | objs | list[Caveat] | Block combining BACI rows from different releases or HS revisions. | unit |
| `BACI` | cache=None, store_root=None | BACI | CEPII BACI bulk files with DuckDB aggregation helpers. | unit, live (pending) |
| `BACI.archive` | hs='HS92', version='202601', *, refresh=False, local_zip=None | tuple[Path, Retrieval] | Path of the BACI zip (downloaded and cached, or `local_zip`) and its retrieval record. | indirect, live (pending) |
| `BACI.count_products` | exporters=None, years=None, level=6, *, min_value=100.0, min_rca=None, hs='HS92', version='202601', refresh=False, local_zip=None | Result | Number of products exported above `min_value` (thousand US$; default 100 = US$100,000) and/or with RCA at or above `min_rca`. | unit |
| `BACI.country_product_table` | years=None, level=4, exporters=None, *, hs='HS92', version='202601', refresh=False, local_zip=None | Result | Exports by exporter, HS `level`-digit product and year (input for ECI methods). | unit |
| `BACI.exporter_totals` | exporters=None, years=None, *, hs='HS92', version='202601', refresh=False, local_zip=None | Result | Total exports, number of HS6 products and of destinations per exporter-year. | unit, live (pending) |
| `BACI.fetch` | exporters=None, years=None, **kwargs | Result | Alias of `exporter_totals`. | unit |
| `BACI.importer_totals` | importers=None, years=None, *, hs='HS92', version='202601', refresh=False, local_zip=None | Result | Total imports (FOB-reconciled values) and number of origins per importer-year. | unit |
| `BACI.market_concentration` | exporters=None, years=None, *, hs='HS92', version='202601', refresh=False, local_zip=None | Result | Export-market concentration: HHI of destination shares (0-1), number of markets and share of the largest market (%). | unit, live (pending) |
| `BACI.product_concentration` | exporters=None, years=None, level=6, *, hs='HS92', version='202601', refresh=False, local_zip=None | Result | Export-product concentration: HHI of product shares (0-1) at HS `level` digits. | unit |
| `BACI.rca` | exporters=None, years=None, level=6, products=None, *, min_rca=None, hs='HS92', version='202601', refresh=False, local_zip=None | Result | Balassa revealed comparative advantage by exporter, product (HS `level` digits) and year. | unit, live (pending) |
| `BACI.tables` | hs='HS92', version='202601', years=None, *, refresh=False, local_zip=None | tuple[BaciTables, Retrieval] | Extract (once) and open the requested years. `years=None` = the latest year in the zip. | unit |

#### `cgjrkit.connectors.atlas`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `build_query` | year_min, year_max, product_class='HS92', trade_values=True | str | GraphQL query for ECI of every country, `year_min`..`year_max`. | unit |
| `build_request` | year_min, year_max, product_class='HS92', trade_values=True | tuple[str, dict] | `(url, json_body)` for `cgjrkit.core.http.fetch` (`method="POST"`). | unit |
| `read_payload` | payload | dict | Decode a GraphQL response; raise `AtlasError` on `errors` or a missing `data`. | unit |
| `rank_eci` | df | DataFrame | Add `eci_rank` and `rank_universe` per year to a table with `year, eci, former`. | unit |
| `parse` | payload, countries=None, start=None, end=None, indicators=…, product_class='HS92' | ParsedAtlas | Canonical frame of ECI (and computed ranks) from a GraphQL response. | unit |
| `Atlas` | cache=None, limiter=None | Atlas | Atlas of Economic Complexity GraphQL API (ECI cross-check). | unit, live (pending) |
| `Atlas.eci` | countries=None, start=2013, end=2023, product_class='HS92', refresh=False | Result | Shortcut for ECI and computed rank. | unit, live (pending) |
| `Atlas.fetch` | countries=None, start=2013, end=2023, *, product_class='HS92', indicators=…, refresh=False | Result | ECI and ECI rank for `countries` (ISO3; `None` = all), `start`..`end`. | unit, live (pending) |

#### `cgjrkit.connectors.oecd`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `get_flow` | name | Flow | Resolve a short key (`"PMR"`), an alias, a dataflow id or `AGENCY,DATAFLOW,VERSION`. | unit |
| `build_key` | flow, filters=None | str | SDMX data key for `filters` ({dimension id: code(s)}), or `"all"`. | unit |
| `build_request` | flow, key='all', start=None, end=None, version=None | tuple[str, dict] | URL and query parameters for one SDMX-CSV-with-labels data query. | unit, live (pending) |
| `read_sdmx_csv` | payload, dims=None | SdmxCsv | Read SDMX-CSV (with labels, codes only, or `labels=both`) into `SdmxCsv`. | unit, live (pending) |
| `parse` | payload, flow, countries=None, start=None, end=None, filters=None | ParsedOECD | Canonical frame from an OECD SDMX-CSV payload. | unit |
| `served_from_cache` | cache, source, url, params, refresh=False | bool | True when `cgjrkit.core.http.fetch` would answer from a snapshot or the cache. | unit, live (pending) |
| `OECD` | cache=None, hourly_limit=20, budget=None | OECD | OECD PMR, OECD-WBG PMR, STRI and FDIRRI from sdmx.oecd.org (one cached bulk query per flow). | unit, live (pending) |
| `OECD.budget_status` |  | dict | Network queries used and remaining in the current rolling hour. | unit, live (pending) |
| `OECD.fdirri` | countries=None, start=None, end=None, activities=('_T',), policy_categories=('_T',), *, refresh=False, force=False | Result | FDI Regulatory Restrictiveness Index (`activities`: ACTIVITY codes, `"_T"` = all activities; `policy_categories`: `"_T"` total, `"CLAS1_1... | unit, live (pending) |
| `OECD.fetch` | flow, countries=None, start=None, end=None, *, filters=None, server_filter=False, version=None, refresh=False, force=False | Result | One OECD dataflow as a canonical frame. | unit, live (pending) |
| `OECD.pmr` | countries=None, start=None, end=None, measures=None, *, wbg=False, refresh=False, force=False | Result | PMR indicators (`measures` e.g. `"PMR"`, `"STATE_INVOL"`, `"BARRIER_ENTRY"`, `"BARRIER_TRADE"`, `"FDI_INDEX"`, `"TARIFFS"`; `None` = all)... | unit, live (pending) |
| `OECD.stri` | countries=None, start=None, end=None, sectors=None, measures=None, *, digital=False, refresh=False, force=False | Result | STRI by sector (`sectors`: ACTIVITY codes; `measures`: MEASURE codes such as the overall index or policy areas; `None` = all). `digital=T... | unit, live (pending) |
| `oecd_mix_caveats` | objs | list[Caveat] | Warn when OECD PMR and OECD-WBG PMR rows are combined (different survey rounds and country coverage) or when PMR rows span the 2018 metho... | unit |

#### `cgjrkit.connectors.openalex`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `build_work_request` | identifier, *, select=… | tuple[str, dict] | Singleton request for a DOI (`/works/doi:<doi>`) or an OpenAlex ID (`/works/W<id>`). | unit |
| `format_filters` | filters | str \| None | Render `filters` as an OpenAlex `filter=` value. | unit |
| `build_search_request` | query, filters=None, per_page=25, *, page=1, sort=None, select=… | tuple[str, dict] | List request `/works?search=...&filter=...&per_page=...`. | unit |
| `build_dois_request` | dois, *, select=… | tuple[str, dict, list[str]] | Batch look-up `/works?filter=doi:a\|b\|...` for up to 100 DOIs. | unit |
| `work_record` | work | dict | One works-table row from an OpenAlex Work object. | indirect |
| `parse` | payload | DataFrame | Works table from a singleton Work or a `{"meta", "results"}` list payload (rank order kept). | unit |
| `response_info` | payload | dict | Facts about a payload used for caveats and `Result.meta` (pure). | unit |
| `OpenAlex` | cache=None, *, api_key=None, contact_email=None | OpenAlex | OpenAlex works resolver. See the module docstring for the citation rule. | unit, live (pending) |
| `OpenAlex.fetch` | doi, *, refresh=False | Result | Alias of `resolve_doi`. | unit, live (pending) |
| `OpenAlex.headers` |  | dict[str, str] | Request headers: User-Agent (with `mailto:` if known) and the key as a Bearer token. | unit, live (pending) |
| `OpenAlex.resolve` | identifier, *, refresh=False | Result | Resolve one DOI or OpenAlex work ID (free singleton call). | unit |
| `OpenAlex.resolve_doi` | doi, *, refresh=False | Result | Resolve a DOI to a one-row works table (empty + `literature_unresolved` if not found). | unit, live (pending) |
| `OpenAlex.resolve_dois` | dois, *, refresh=False | Result | Resolve up to 100 DOIs in one list call (`filter=doi:a\|b\|...`, $0.0001). | unit, live (pending) |
| `OpenAlex.search_works` | query, filters=None, per_page=25, *, page=1, sort=None, refresh=False | Result | Search works (`search=`, $0.001) and/or filter them (list+filter, $0.0001). | unit, live (pending) |
| `resolve_doi` | doi, *, refresh=False, cache=None | Result | Resolve a DOI with OpenAlex. Cite only IDs present in the returned table. | unit, live (pending) |
| `search_works` | query, filters=None, per_page=25, *, page=1, sort=None, refresh=False, cache=None | Result | Search OpenAlex works. Hits are candidates; cite only returned IDs after checking the match. | unit, live (pending) |

#### `cgjrkit.connectors.crossref`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `build_work_request` | doi, *, mailto=None | tuple[str, dict] | Singleton request `/works/<doi>` (DOI normalized; unsafe characters percent-encoded). | unit |
| `format_filters` | filters | str \| None | Render `filters` as a Crossref `filter=` value. | unit |
| `build_search_request` | query, rows=20, *, field=…, author=None, filters=None, mailto=None, select=… | tuple[str, dict] | List request `/works?query.bibliographic=...&rows=...`. | unit |
| `work_record` | item | dict | One works-table row from a Crossref work item. | indirect |
| `parse` | payload | DataFrame | Works table from a `work` or `work-list` envelope (rank order kept). | unit |
| `response_info` | payload | dict | Facts about a payload used for caveats and `Result.meta` (pure). | unit |
| `Crossref` | cache=None, *, contact_email=None, mailto_in='param' | Crossref | Crossref works resolver. See the module docstring for the citation rule. | unit, live (pending) |
| `Crossref.fetch` | doi, *, refresh=False | Result | Alias of `resolve_doi`. | unit |
| `Crossref.headers` |  | dict[str, str] | User-Agent identifying the toolkit, with `mailto:` when an address is known. | unit |
| `Crossref.resolve_doi` | doi, *, refresh=False | Result | Resolve a Crossref DOI to a one-row works table (empty + `literature_unresolved` on 404). | unit, live (pending) |
| `Crossref.search` | query, rows=20, *, field=…, author=None, filters=None, refresh=False | Result | Bibliographic search (`query.bibliographic` by default); rows keep Crossref's relevance order. | unit, live (pending) |
| `resolve_doi` | doi, *, refresh=False, cache=None | Result | Resolve a DOI with Crossref. Cite only DOIs present in the returned table. | unit, live (pending) |
| `search` | query, rows=20, *, field=…, author=None, filters=None, refresh=False, cache=None | Result | Search Crossref works. Hits are candidates; cite only returned DOIs after checking the match. | unit, live (pending) |

#### `cgjrkit.connectors.handles`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `normalize_doi` | text | str | Return the bare, lower-case DOI (`"10.3386/w17143"`). | unit |
| `is_doi` | text | bool |  | unit |
| `doi_url` | doi | str | Canonical DOI link, `https://doi.org/<doi>` (special characters percent-encoded). | unit, live (pending) |
| `doi_prefix` | doi | str | Registrant prefix of a DOI (`"10.3386"`). | unit |
| `normalize_handle` | text | str | Return the bare handle `"<prefix>/<suffix>"`. | unit |
| `is_okr_handle` | text | bool | True for a well-formed World Bank OKR handle (`10986/<digits>`). | unit |
| `handle_url` | handle | str | Canonical Handle System link, `https://hdl.handle.net/<prefix>/<suffix>`. | unit, live (pending) |
| `okr_url` | handle | str | OKR repository link `https://openknowledge.worldbank.org/handle/10986/<n>`. | unit, live (pending) |
| `normalize_openalex_id` | text | str | Return the OpenAlex work key `"W<digits>"` (accepts the full `https://openalex.org/W...` form). | unit |
| `openalex_url` | work_id | str |  | unit |
| `parse_identifier` | text | Identifier | Classify and normalize a DOI, Handle or OpenAlex work ID. | unit |
| `identifier_table` | texts, *, errors='raise' | DataFrame | Parse several identifiers into a table with `IDENTIFIER_COLUMNS`. | unit |
| `empty_works_frame` |  | DataFrame | An empty works table with the documented dtypes. | unit |
| `make_works_frame` | records | DataFrame | Build a works table from row dicts (missing keys become missing values). | unit |
| `validate_works_frame` | df | None | Raise ValueError if `df` is not a well-formed works table. | unit |
| `contact_email` | value=None | str \| None | Contact address for polite API use: `value` or `$CGJRKIT_CONTACT_EMAIL`. | unit |
| `polite_user_agent` | email | str | User-Agent identifying the toolkit, with `mailto:` when an address is known. | unit |
| `returned_ids` | df | list[str] | Identifiers in a works table that callers may cite (OpenAlex keys and DOIs), in row order. | unit |

#### `cgjrkit.methods.harmonize`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `period_frequency` | text | str | Frequency code of a period string: `"A"`, `"Q"`, `"M"` or `"FY"`. | indirect |
| `fiscal_label` | start_year | str | Canonical fiscal-year label: `fiscal_label(2024) == "FY2024/25"`. | unit |
| `format_period` | freq, ordinal | str | Inverse of :attr:`Period.ordinal`. | unit |
| `combine_status` | statuses | str | Combine `obs_status` flags, keeping the most important one (see `STATUS_PRIORITY`). | unit |
| `cagr_value` | first, last, years | float | Compound annual growth rate in percent: `100 * ((last / first) ** (1 / years) - 1)`. | unit |
| `check_ppp` | *frames, by=() | list[Caveat] | Return a `block` caveat if the frames (together, or within each `by` group) mix PPP bases. | unit |
| `rebase_index` | frame, base_period, by=…, *, base_value=100.0, suffix=None, on_ppp_mix='raise' | Result | Index each series to `base_period` = `base_value` (default 100). | unit |
| `growth_rate` | frame, kind='pct', by=…, *, lag=1, scale=100.0, suffix=None, on_ppp_mix='raise' | Result | Growth over `lag` periods of the series' own frequency. | unit |
| `period_average` | frame, periods, method='mean', by=…, *, require_complete=False, suffix=None, on_ppp_mix='raise' | Result | Average (or CAGR) of each series over named, inclusive windows. | unit |
| `cagr` | frame, start, end, by=…, *, label=None, suffix=None, on_ppp_mix='raise' | Result | Compound annual growth rate (percent per year) between the levels at `start` and `end`. | unit |
| `pct_of_frontier` | frame, frontier_iso3='USA', by=('indicator',), *, scale=100.0, keep_frontier=True, max_over_aggregates=False, suffix=None, on_ppp_mix='raise' | Result | Each value as a percent of the frontier's value in the same period. | unit |
| `deflate` | nominal, deflator, base_period=None, *, match_on=…, suffix=None, on_ppp_mix='raise' | Result | Convert nominal values to real terms: `real_t = nominal_t / (deflator_t / deflator_base)`. | unit |
| `ratio` | numerator, denominator, scale=100.0, *, match_on=…, indicator=None, indicator_label=None, unit=None, on_ppp_mix='raise' | Result | `scale * numerator / denominator` aligned on `match_on` (default iso3 and period). | unit |
| `fiscal_to_calendar` | frame, fy_start_month=None, *, kind='flow', by=…, suffix=None, on_ppp_mix='raise' | Result | Convert fiscal-year values (`"FY2024/25"`) to calendar years (`"2024"`). | unit |
| `calendar_to_fiscal` | frame, fy_start_month=None, *, kind='flow', by=…, suffix=None, on_ppp_mix='raise' | Result | Convert calendar-year values to fiscal years (`"FY2024/25"`). | unit |
| `quarterly_to_fiscal` | frame, fy_start_month=None, *, how='sum', by=…, suffix=None, on_ppp_mix='raise' | Result | Exact fiscal-year values from quarterly data (`"2024Q3"` ... `"2025Q2"` -> `"FY2024/25"`). | unit |
| `splice` | old, new, overlap_period, method='ratio', *, by=('iso3',), indicator=None, on_ppp_mix='raise' | Result | Join an `old` series onto a `new` one at `overlap_period`. | unit |

#### `cgjrkit.methods.peers`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `PeerSet` | name, members, criteria='', approver='', frozen_at=None, notes='', code='', focus='' | PeerSet | A named, documented set of peer countries. | unit |
| `load_peersets` | path_or_text | dict[str, PeerSet] | Load peer sets from a YAML file path or YAML text, keyed by `code`. | unit |
| `save_peersets` | peersets, path, *, report='' | Path | Write several sets to one YAML file (`{report, peer_sets: [...]}`). | unit |
| `normalize_classification` | table, *, iso3_col='iso3', year_col='year', group_col='income_group' | DataFrame | Return a long `iso3, year, group` table (one row per country-year). | unit |
| `income_group` | classification, iso3, year | str \| None | Group of `iso3` in `year` (None if not classified). | unit |
| `members_by_year` | classification, group | dict[int, list[str]] | `{year: sorted ISO3 members}` of `group`. | unit |
| `peer_aggregate` | frame, peerset=None, stats=…, min_n=3, *, by=('indicator',), classification=None, groups=None, group_prefix='IG_', drop_below_min_n=False, quantile_method='linear', on_ppp_mix='raise' | Result | Peer-group statistics per indicator and period, with member counts. | unit |

#### `cgjrkit.methods.benchmark`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `aggregate_codes` | *results | frozenset[str] | Codes treated as aggregates: inputs' `meta["aggregates"]` plus World Bank aggregate codes. | indirect |
| `percentile_of` | values, score, method='weak' | float | Percentile (0-100) of `score` within `values` (NaN ignored); see `PERCENTILE_METHODS`. | unit |
| `income_conditional_fit` | x_frame, y_frame, period, kind='linear', log_x=True, focus=None, exclude=None, *, log_y=False, focus_path=None, y_bounds=(None, None), frac=…, lowess_it=3, grid_points=100, ci=0.95, percentile_method='weak', aggregates='drop', on_ppp_mix='raise' | Result | Fit `y` on (log) `x` across countries in one period and score every country against the curve. | unit, live (pending) |
| `percentile_rank` | frame, iso3, period, *, method='weak', descending=True, balanced=False, aggregates='drop', on_ppp_mix='raise' | Result | Rank and percentile of every country, per indicator and period, with a focus summary. | unit, live (pending) |
| `kernel_density` | frame, period, grid_points=200, *, focus=None, bw_method=None, cut=3.0, balanced=False, percentile_method='weak', aggregates='drop', on_ppp_mix='raise' | Result | Gaussian kernel density of one indicator across countries, per period, on a common grid. | unit, live (pending) |
| `zscore` | frame, period=None, *, reference=None, invert=(), ddof=1, composite=False, min_components=1, focus=None, aggregates='drop', on_ppp_mix='raise' | Result | Standardise each indicator across countries within each period: `z = (x - mean) / sd`. | unit |
| `distance_to_frontier` | frame, frontier='max', period=None, *, measure='gap', higher_is_better=True, worst=None, focus=None, aggregates='drop', on_ppp_mix='raise' | Result | Distance of every country to a frontier, per indicator and period. | unit |

#### `cgjrkit.methods.growth_accounting`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `growth_accounting` | panel, periods, alpha=None, *, iso3=None, output='rgdpna', capital='rkna', labour='emp', human_capital='hc', labour_share='labsh', weights='period_mean', per_worker=False, units='log', sensitivity=…, on_ppp_mix='raise' | Result | Average annual contributions of capital, labour, human capital and TFP to growth. | unit, live (pending) |
| `perpetual_inventory` | investment, delta, initial='steady_state', g=None, *, g_years=10, timing='current', indicator=None, on_ppp_mix='raise' | Result | Capital stock by the perpetual inventory method. | unit |
| `potential_output` | panel, alpha=None, *, iso3=None, output='rgdpna', capital='rkna', labour='emp', human_capital='hc', labour_share='labsh', lamb=100.0, smooth_human_capital=False, start=None, end=None, min_years=8, on_ppp_mix='raise' | Result | Production-function potential output from HP-filtered TFP and employment (an APPROXIMATION). | unit, live (pending) |

#### `cgjrkit.methods.structural_change`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `sector_panel` | frame, sectors=None, *, name='frame' | tuple[Result, DataFrame, list[str]] | Tidy sector panel from a canonical frame. | unit |
| `annualize_factor` | y0, yT, years, annualize | float | Factor `k` turning a level change component into percentage points. | unit |
| `va_per_worker` | va_frame, emp_frame, *, va_sectors=None, emp_sectors=None, va_scale=1.0, emp_scale=1.0, total='TOTAL', unit=None | Result | Value added per worker by sector: `VA_i * va_scale / (L_i * emp_scale)`. | unit |
| `decompose_productivity` | va0, vaT, emp0, empT, variant='two_term' | dict[str, Any] | Shift-share terms for one country from sector dicts (pure arithmetic, no frames). | unit |
| `shift_share` | va_by_sector, emp_by_sector, start, end, variant='two_term', *, va_sectors=None, emp_sectors=None, aggregate_va=None, aggregate_emp=None, annualize='cagr', on_gap='raise', gap_tolerance=1e-06, emp_scale=1.0, iso3=None | Result | Decompose the change in aggregate labour productivity between `start` and `end`. | unit, live (pending) |
| `sector_contributions` | va_by_sector, periods, *, sectors=None, total=None, method=…, share_timing='current', share_frame=None, share_sectors=None, tolerance_pp=0.05, iso3=None | Result | Sector contributions to real value-added growth over named windows (Kenya Fig 2). | unit, live (pending) |
| `beta_convergence` | panel, start, end, *, indicator=None, iso3=None, growth='log', spell=None, period_effects=True, controls=None, robust=False | Result | Beta-convergence regression `g_i = alpha + beta * ln(y_i0) [+ controls] + e_i`. | unit |

#### `cgjrkit.methods.jobs_shapley`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `shapley_multipliers` | x0, xT | list[float] | Shapley multipliers for the product `prod(x)` moving from `x0` to `xT`. | unit |
| `shapley_product` | x0, xT | list[float] | Shapley contributions `m_k * (xT_k - x0_k)` to `prod(xT) - prod(x0)`. | unit |
| `jobs_shapley` | gdp, population, working_age, employment, periods, *, va_by_sector=None, emp_by_sector=None, va_sectors=None, emp_sectors=None, sector_variant='shapley', method='shapley', annualize='cagr', population_scale=1.0, working_age_scale=1.0, employment_scale=1.0, sector_emp_scale=None, indicators=None, iso3=None | Result | Decompose growth in GDP per capita into output per worker, employment rate and working-age share. | unit, live (pending) |

#### `cgjrkit.methods.standard_tables`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `standardized_employment_table` | emp_by_sector, focus, peer_sets, periods, *, sectors=None, share_year=None, peer_periods=None, total=None, total_label='Total', stat='median', growth='cagr', min_n=3, title=None, source_note='' | Result | Standardized employment table: employment growth and sector shares, focus vs peers. | unit |
| `MacroRow` | label, frame, agg='mean', section='', indicator=None, source='', decimals=None | MacroRow | One row of the standardized macro table. | unit |
| `standardized_macro_table` | frames, focus, peers, periods, *, peer_periods=None, stat='median', min_n=3, require_complete=False, title=None, show_source=True, source_note='' | Result | Standardized macro table: period averages for the focus country vs peer statistics. | unit |
| `to_xlsx` | table, path, *, sheet_name='Table' | Path | Write a standardized table to an XLSX file with formatted headers (openpyxl). | unit |
| `to_html` | table, path=None, *, full_document=False, include_style=True | str | Render a standardized table as HTML (and write it to `path` if given). | unit |

Every public chart builder below takes a DataFrame or a Result. A Result (also as `band`, `size`, `right` or one of an `(x, y)` pair) with a `block` caveat raises `BlockedResultError`; a Result's `warn` caveats and data source line go to the chart footer. A chart built only from DataFrames has no source line: add one with `charts.render.add_footer`.

#### `cgjrkit.charts.series`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `line_vs_peers` | frame, focus=None, peers=None, band=None, events=(), index_base=None, title='', subtitle='', y_title=None, *, aggregates=None, series='iso3', label=None, band_label=…, band_stats=('p25', 'p75'), band_group=None, peer_style='palette', mark_forecasts=True, end_labels=False, points=False, reference_line=None, x_title=None, y_format=',.1f', width=560, height=320 | alt.LayerChart | Line chart of one indicator for the focus country against peers and aggregates. | unit |
| `bar_vs_peers` | frame, focus=None, peers=None, period=None, title='', subtitle='', y_title=None, *, aggregates=None, series='iso3', label=None, color_by=None, peer_color=None, horizontal=False, sort='descending', value_labels=False, value_format=',.1f', period_labels=None, width=None, height=None | alt.LayerChart | Bars by country for one period, or grouped bars (one per period) by country. | unit |
| `dual_axis` | left_frame, right_frame, title='', subtitle='', left_title=None, right_title=None, *, left_mark='line', right_mark='line', series='iso3', label=None, right_suffix=…, events=(), left_format=',.1f', right_format=',.1f', width=560, height=320 | alt.LayerChart | Two measures on independent left and right y axes (e.g. REER level and inflation). | unit |

#### `cgjrkit.charts.benchmark`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `ordinal` | n | str | `1 -> "1st"`, `22 -> "22nd"`, `13 -> "13th"`. | unit |
| `scatter_vs_income` | data, focus=None, label_countries=(), trajectory=True, *, peers=(), curve=True, ci=False, period=None, fit_kind=None, log_x=None, size=None, size_title='', label_field='iso3', x_title=None, y_title=None, title='', subtitle='', x_format=',.0f', y_format=',.0f', fit_note=True, width=560, height=320 | alt.LayerChart | Scatter of an outcome against income with fitted curve, focus, peers, labels and trajectory. | unit |
| `kde_plot` | density, focus=None, periods=None, *, rug=False, focus_lines=True, bw_method=None, x_title=None, y_title='Density', title='', subtitle='', x_format=',.0f', value_format=',.1f', width=560, height=320 | alt.LayerChart | Kernel densities across countries, one line per period, with the focus country marked. | unit |
| `slope_rank` | frame, periods=None, focus=None, peers=None, *, measure='value', descending=True, balanced=True, aggregates=None, show_others=True, labels=True, show_axis=False, value_format=None, title='', subtitle='', y_title=None, width=360, height=320 | alt.LayerChart | Slope chart of values, ranks or percentiles between periods (two or more). | unit |

#### `cgjrkit.charts.decomposition`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `stacked_contributions` | frame, total='auto', *, x='period', component='component', value='value', facet=None, component_order=None, component_labels=None, total_label=None, x_order=None, period_labels=None, total_mark='dot', value_labels=False, label_min=0.0, value_format='.1f', total_labels=False, colors=None, horizontal=False, check_identity=True, tolerance=0.05, flag_gaps=True, title='', subtitle='', y_title=None, width=None, height=320 | alt.LayerChart \| alt.FacetChart | Stacked positive/negative contributions with a total marker. | unit |

#### `cgjrkit.charts.render`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `to_spec` | chart | dict | Vega-Lite spec dict of `chart` with the house theme and no row limit. | unit |
| `footer_lines` | chart_or_spec | list[str] | The footer lines stored in `usermeta["cgjrkit_footer"]` (empty list if none). | unit |
| `add_footer` | chart, source_line='', notes=() | Altair chart | Merge notes and a source line into the chart's footer, keeping other `usermeta`. | unit |
| `with_footer` | spec, lines=None, *, font_size=10, line_height=13, color='#555555' | dict | Return a spec that draws `lines` (default: the spec's footer) under the chart. | unit |
| `plotted_data` | spec | DataFrame | The data a spec plots, as one frame (datasets stacked; a `dataset` column if no `layer` column). | unit |
| `render` | chart, fmt='svg', *, footer=True, scale=2.0 | str \| bytes | Render to `"svg"` (str), `"png"`/`"pdf"` (bytes), `"html"` (str) or `"vl.json"` (str), offline. | unit |
| `save_chart` | chart, path_stem, data_frame=None, formats=…, *, footer=True, scale=2.0, data_csv=True, overwrite=True | dict[str, Path] | Write the chart in `formats` and the plotted data as CSV; return `{format: path}`. `overwrite=False` refuses existing files and writes nothing. | unit |
| `chart_paths` | path_stem, formats=…, data_csv=True | dict[str, Path] | The files `save_chart` writes for these arguments, `{format: path}` (`csv` last). | unit |
| `caveat_notes` | caveats, include_info=False | list[str] | Footnote lines for caveats: every `warn` (and `info` if asked), de-duplicated, in order. | indirect |
| `data_source_line` | result | str | `result.source_line()` restricted to data providers. | indirect |
| `method_chart_defaults` | result, kind | dict[str, Any] | Default chart options for a method's output (what the standard report chart shows). | unit |
| `filter_rows` | result, where | Result | Rows of `result` where each column equals the given value (or one of a list of values). | unit |
| `chart_from_result` | result, kind, *, note='', source_line=None, include_info=False, right=None, where=None, **kw | Altair chart | Build a chart of `kind` from a Result and attach its source line and caveat footnotes. | unit |

#### `cgjrkit.qa.caveat_check`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `CaveatReport` | status, caveats=<factory>, footnotes=<factory>, rules_run=<factory>, inherited=<factory>, errors=<factory> | CaveatReport | Outcome of `run_caveat_check`. | unit |
| `register_rule` | name, description, severities=('warn',), *, replace=False | Callable[[Callable[[CheckInput], RuleOutput]], Callable[[CheckInput], RuleOutput]] | Decorator: register `func(inp: CheckInput) -> RuleOutput` under `name`. | unit |
| `list_rules` |  | list[dict] | Registered rules (name, description, severities) in run order. | unit |
| `period_years` | period | tuple[int, int] \| None | First and last calendar year a single period touches. | unit |
| `window_years` | label | tuple[int, int] \| None | Start and end year of a window label: `"2011-2019"`, `"2016-21"`, `"2019Q1-2021Q4"`. | unit |
| `run_caveat_check` | results, context=None, *, rules=None, skip=() | CaveatReport | Run the registered caveat rules over the inputs of one output. | unit |

#### `cgjrkit.registries`

| Function | Inputs | Returns | What it does | Tests |
|---|---|---|---|---|
| `load_hosts` |  | dict[str, dict] | All host entries keyed by hostname. | unit |
| `host_info` | host | dict | Registry entry for `host` (a hostname or a URL). Raises KeyError if undeclared. | unit |
| `rate_limit` | host | RateLimit | Rate-limit default for `host`; the registry default for undeclared hosts. | unit |
| `allowlist` | *, include_planned=True, include_excluded=False | list[str] | Sorted hostnames to allowlist. | unit, live (pending) |
| `undeclared_hosts` | domains | list[str] | Hosts in `domains` (hostnames or URLs) that hosts.yaml does not declare. | unit |
| `load_licences` |  | dict[str, dict] | All licence entries keyed by source (connector `source` keys where they exist). | unit |
| `licence_for` | source | dict | Licence entry for `source`. Raises KeyError if the source is not registered. | unit |
| `licence_caveat` | source | Caveat \| None | A `licence_<source>` caveat at the registry's suggested severity, or None. | unit |
| `list_countries` |  | list[str] | ISO3 codes with a country file. | unit, live (pending) |
| `load_country` | iso3 | dict | Country entry for `iso3`. Raises KeyError if there is no file. | unit |
| `fiscal_year_start_month` | iso3 | int | Month (1-12) in which the government fiscal year starts (Kenya: 7). | unit |
| `national_accounts_base_year` | iso3 | int \| str | National-accounts base year, or `"unknown"`. | unit |
| `statistics_office_host` | iso3 | str | Hostname of the national statistics office. | unit, live (pending) |
