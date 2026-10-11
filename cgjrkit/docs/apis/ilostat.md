# ILOSTAT (`cgjrkit.connectors.ilostat`)

Connector: `ILOSTAT` in `src/cgjrkit/connectors/ilostat.py`.

- `fetch(indicator, countries=None, start=None, end=None, *, freq="A", sex=None, classif1=None, classif2=None, sources=None, best_source=True, modelled=True, via="bulk", type="code", use_toc=True, expect_edition=None, refresh=False)` returns one indicator as a canonical `Result`.
- Wrappers: `employment_by_sector(level="sector"|"aggregate"|"isic4")`, `labour_force_participation_rate`, `unemployment_rate`, `employment_to_population_ratio` (all with `sex`, `age`, `modelled`), and `output_per_worker(basis="2015usd"|"ppp")`.
- Reference data: `fetch_toc()` (table of contents by indicator) and `fetch_dictionary(var)` (code lists).
- Pure functions: `build_request`, `request_url`, `build_sdmx_request`, `parse`, `parse_sdmx`, `parse_toc`, `parse_dictionary`, `build_caveats`, `unit_from_label`, `edition_from_label`.

| Item | Value |
|---|---|
| Hosts (`domains`) | `rplumber.ilo.org` (bulk service, default), `sdmx.ilo.org` (SDMX, optional) |
| Data URL | `https://rplumber.ilo.org/data/indicator/?id=<CODE>_<FREQ>&ref_area=KEN+UGA&sex=SEX_T&classif1=...&timefrom=2000&timeto=2024&type=code&lang=en&format=.csv` |
| Table of contents | `https://rplumber.ilo.org/metadata/toc/indicator?lang=en&format=.csv` |
| Code lists | `https://rplumber.ilo.org/metadata/dic/?var=<name>&lang=en&format=.csv` |
| SDMX | `https://sdmx.ilo.org/rest/data/ILO,DF_<CODE>,1.0/<REF_AREA>.<FREQ>.<MEASURE>.<classif...>?startPeriod=&endPeriod=&format=csv` |
| Auth | None |
| Rate limits | None published. SDMX returns at most 300,000 records per query (Rilostat docs, older). Files are generated on request; HEAD answers 405 and `Range` is ignored |
| Format | CSV (UTF-8, header, comma, optional BOM). `format=.csv` is used because its bytes are stable (the `.csv.gz` file name and gzip header carry a generation time). Gzip bodies are also accepted |
| Licence | CC BY 4.0 for databases and datasets published from 3 May 2023 (ILO Rights and permissions). The Rilostat README still says "non-commercial": confirm with the ILO before commercial delivery |
| Citation | "ILOSTAT, International Labour Organization (ILO)" |
| Vintage | ILO modelled estimates: `Provenance.vintage = "ILOEST <edition>"` (e.g. `ILOEST Nov. 2025`), read from the indicator label. Other series: `"ILOSTAT update YYYY-MM-DD"` from the table of contents `last.update`. ILOSTAT has no archive of past editions or versions; pin snapshots (`CGJRKIT_SNAPSHOT`) |

## Indicators wired for the CGJR charts

| Catalog name | ILO modelled (ILOEST) | National (survey) | Classification |
|---|---|---|---|
| `employment_by_sector` | `EMP_2EMP_SEX_ECO_NB` | `EMP_TEMP_SEX_ECO_NB` | `ECO_SECTOR_*` (AGR, IND, SER, NAG), `ECO_AGGREGATE_*` (AGR, MAN, CON, MEL, MKT, PUB), `ECO_ISIC4_*` (A, B, C, DE, F, G, HJ, I, K, LMN, O, P, Q, RSTU in ILOEST) |
| `employment` | `EMP_2EMP_SEX_AGE_NB` | `EMP_TEMP_SEX_AGE_NB` | `AGE_YTHADULT_*` |
| `labour_force_participation_rate` (`lfpr`) | `EAP_2WAP_SEX_AGE_RT` | `EAP_DWAP_SEX_AGE_RT` | `AGE_YTHADULT_YGE15`, `Y15-24`, `YGE25`, `Y15-64` |
| `unemployment_rate` | `UNE_2EAP_SEX_AGE_RT` | `UNE_DEAP_SEX_AGE_RT` | as above |
| `employment_to_population_ratio` (`epr`) | `EMP_2WAP_SEX_AGE_RT` | `EMP_DWAP_SEX_AGE_RT` | as above |
| `output_per_worker` | `GDP_205U_NOC_NB` (GDP constant 2015 US$) | none | none |
| `output_per_worker_ppp` | `GDP_211P_NOC_NB` (constant 2021 international $ at PPP in the Nov. 2025 edition) | none | none |
| `output_per_hour_ppp` | `GDP_2HRW_NOC_NB` | none | none |

Any other ILOSTAT id or code can be passed directly. The national series are the "previous ILO definition" (13th ICLS) tables in the LFS database.

## Mapping to the canonical frame

| Canonical | From |
|---|---|
| `source` / `dataset` | `"ilostat"` / `"ILOEST"` for ILO modelled rows, `"ILOSTAT"` for other rows, so modelled and survey data never share a key |
| `indicator` | `<indicator code>` plus the classification codes present, joined with `\|`: `EMP_2EMP_SEX_ECO_NB\|SEX_T\|ECO_AGGREGATE_AGR`. When several sources report the same series and period, `@<source code>` is appended to every affected series (`...\|AGE_YTHADULT_YGE15@BA:1111`) and they are listed in `meta["disambiguated"]` |
| `indicator_label` | Indicator label (from `type=both` or the table of contents), then ` \| ` and the sex and classification labels (built-in table, or labels from the payload) |
| `iso3` / `country` | `ref_area` / `ref_area.label` when present. ILO aggregates start with `X` (`X01` = World) and are listed in `meta["aggregates"]` |
| `period` | `time`: `2024`; `2024Q1` (also `2024q1`); `2024M03` or `2024m03` becomes `2024-03` |
| `value` | `obs_value` (empty means NaN) |
| `unit` | Trailing parenthetical of the label (`thousands`, `%`). Output per worker: `constant 2015 US$ per worker`, or `constant 2021 international $ per worker (PPP 2021)` so that `schema.ppp_base()` sees the base year. Without a label (`type=code` payload and no table-of-contents entry, e.g. TOC unreachable or `use_toc=False`): `%` for `_RT` codes; `GDP_211P` → `constant international $ per worker (PPP unknown)`, `GDP_2HRW` → `constant international $ per hour worked (PPP unknown)` (so `ppp_base()` returns `"unknown"` and PPP-mixing checks still fire), `GDP_205U` → `constant US$ per worker (base year unknown)`. These codes are reused across base years, so the code alone never names a base year. A label that names PPP without a year gives the same `(PPP unknown)` unit |
| `obs_status` | ILO modelled rows: always `modelled`. Other rows: `B` break, `I` imputed, `M` modelled, `A` estimate, `E` estimate, `P` provisional, `F` forecast, `R`/`U`/empty `""` (`U` raises a caveat). Raw flags are counted in `meta["obs_status_raw"]` |

ILO modelled rows are identified by any of: source code prefix `XA:` (shown as "ILO - Modelled Estimates"), a source or indicator label containing "modelled estimates", or the table of contents (`database` = `ILOEST`). Indicator codes with `2` in the second segment are not used as the test, because `POP_2POP_SEX_AGE_NB` ("UN estimates and projections") has the same shape.

`meta` also holds `series` (indicator, sex, classif1, classif2 and their labels for each series key), `sources` (code to label), `real_value_periods` (ILOEST years flagged `R` = "Real value", by country, e.g. `"2005-2019"`), `last_real_period` (latest over all countries, for reference only), `last_real_by_country`, `notes` (footnote codes by series and country), `edition`, `last_update` and the request URL.

## Caveats raised

| Code | Severity | When |
|---|---|---|
| `ilo_modelled` | warn | Any ILO modelled row. Imputed values are not based on national data; every edition revises the whole history; never splice with national survey series or another edition |
| `ilostat_modelled_projection` | warn | ILOEST periods after the last year flagged `R` **of the same series and country** (computed per series and country, not over the whole peer panel). These are nowcasts or projections (ILOEST runs about two years past the last real observation). Evidence: `periods` (union), `by_country` {iso3: [periods]}, `last_real_by_country` |
| `ilostat_modelled_no_real_value` | warn | ILOEST series with no `R` value at all in the result (by country; ILO aggregates `X..` excluded, and only when the payload carries `R` flags): every value is imputed or projected |
| `ilostat_edition_mismatch` | warn | `expect_edition` (e.g. `"Nov 2023"` for the Kenya CGJR) differs from the edition served |
| `ilostat_edition_unknown` | info | Modelled rows without a readable edition (no table of contents and `type=code`) |
| `ilo_modelled_and_survey_mixed` | warn | One result holds both kinds of rows |
| `ilostat_multiple_sources` | warn | Series were disambiguated with `@<source>`; pick one with `sources=` |
| `ilostat_series_break` | warn | `B` flags present |
| `ilostat_unreliable` | warn | `U` flags present |
| `ilostat_ppp_code_reuse` | info | `GDP_211P*`: the code says 211P but the PPP base follows the edition (2011, then 2017, now 2021) |
| `ilostat_unit_base_unknown` | warn | A unit's PPP or price base year could not be read (no label): `(PPP unknown)` / `(base year unknown)` units. Re-fetch with `type="both"` or with the table of contents |
| `ilostat_unknown_obs_status`, `ilostat_missing_values` | info | Unrecognised flags, empty values |

## Request details

- **Literal `+`.** Every published example joins multiple values with a literal `+` (`ref_area=AUT+BEL+FRA`). httpx would send `%2B`, and whether the R plumber service decodes that the same way is unverified, so `request_url()` composes the query string itself and `http.fetch` gets the full URL. Parameters are emitted in a fixed order and countries and codes are sorted, so cache keys are stable.
- **Filters.** `ref_area`, `sex`, `classif1`, `classif2`, `timefrom` and `timeto` are sent to the service and applied again client side. A trailing `*` (`classif1="ECO_AGGREGATE_*"`) is a prefix filter applied only client side. `sources=` keeps exact source codes (`"BA:259"`) or source-type prefixes (`"BA"` = labour force survey, `"XA"` = ILO modelled).
- **`type`.** `code` is the default because it is compact. The table of contents supplies the indicator label, edition and last update. `type=both` adds `<col>.label` columns, so the edition can be read from the payload alone.
- **`best_source`.** The data behind Rilostat has a `best_source` column (0/1). If the CSV carries it, rows with 0 are dropped by default.
- **Duplicates.** Exact duplicate rows are dropped (a third-party client notes that the output is "not quite clean"). The number is in `meta["duplicates_dropped"]`.

## How this was verified

The ILO hosts are blocked in the build environment, so nothing was called live. Sources read on 2026-10-11:

| Label | Source | What it established |
|---|---|---|
| P | ILO [Rilostat](https://github.com/ilostat/Rilostat) 2.5.0 (2026-04-24): `R/get_ilostat.R`, `R/Rilostat.R`, `R/get_ilostat_toc.R`, `R/get_ilostat_dic.R`, `R/label_ilostat.R`, `R/data-internal.R`, rendered `vignettes/Rilostat.html`, `docs/articles/Bulkdownload.html` | Rilostat reads `https://rplumber.ilo.org/files/{indicator,ref_area}/<id>.rds` (an RDS mirror of the same tables), `.../table_of_contents_<lang>.rds` and `.../dic/<var>_<lang>.rds`. The reference column set is `ref_area, source, indicator, sex, classif1, classif2, time, obs_value, obs_status, note_classif, note_indicator, note_source` (plus `.label` variants). Time suffixes are `Q1`-`Q4` and `M01`-`M12`. There is a `best_source` column. The TOC has `id, indicator, indicator.label, freq, freq.label, data.start, data.end, last.update` (`dd/mm/YYYY HH:MM:SS`) and `n.records`. The vignette shows ILO modelled rows with source `XA:2198` and ids built as `<code>_<A\|Q\|M>`. The 2019 guide says the repository is updated weekly; code lists have the code first and the label second |
| T | [UNopenGIS/foil4g, `docs/data_source/ILO/ILOSTAT.md`](https://raw.githubusercontent.com/UNopenGIS/foil4g/d262e44736a834b64ad19682645665815d8007d7/docs/data_source/ILO/ILOSTAT.md) (live checks on 2026-10-06) | `https://rplumber.ilo.org/data/indicator?id=<ID>&format=.csv.gz` (also `.csv`, `.json`, `.parquet` and others). `ref_area` and `timefrom` filter rows. TOC at `metadata/toc/indicator?lang=en&format=.csv` (1,964 files, 20 columns including `rep_var, classification, n.records.all, n.ref_area, with.region, subject, database`). Code lists at `metadata/dic?var=obs_status`. CSV header `ref_area,source,indicator,sex,classif1,time,obs_value,obs_status,note_classif,note_indicator,note_source`. `BA:` = labour force survey, `XA:` = "ILO - Modelled Estimates". `obs_status` codes A Adjusted, B Break, M Model-based extrapolation, U Unreliable, R Real value, I Imputation. SDMX `.../ILO,DF_<code>,1.0/<key>?startPeriod=&format=csv` with key `JPN.A...` for `SDG_0852_SEX_AGE_RT`. SDMX-CSV carries source names, `UNIT_MEASURE` and `DECIMALS`. No versions (only `last.update`). HEAD returns 405, `Range` is ignored. `ilostat.ilo.org` shows a Cloudflare challenge. The licence text is quoted from the ILO Rights and permissions page |
| T | Community code calling the service: [socyb/global `ilostat_dashboard_server.py`](https://github.com/socyb/global), [dev-anagram/IS_Projekt `adds.txt`](https://github.com/dev-anagram/IS_Projekt), [sdg-data-canada `R/indicator_8-8-2.R`](https://github.com/sdg-data-canada-odd-donnees/sdg-data-donnees-odd), [blavatnik-index `R/utils/ilo_api.R`](https://github.com/blavatnik-index/bipa2024_sourcedata), [ccew-unibw `base/datasets/ilo.py`](https://github.com/ccew-unibw/ccvi-data), [wilsoniumite/labor](https://github.com/wilsoniumite/labor) (`exposure.py` and a cached CSV) | Parameters `ref_area` (multiple values joined with a literal `+`), `sex`, `classif1`, `classif2`, `timefrom`, `timeto`, `type=code\|label\|both`, `lang`, `format=.csv`, `channel=ilostat`. `metadata/dic/?var=..&lang=en&format=.csv`. The cached `EMP_2EMP_SEX_ECO_NB_A` CSV has a UTF-8 BOM, quoted fields and classif1 codes `ECO_SECTOR_{TOTAL,AGR,NAG,IND,SER}`, `ECO_AGGREGATE_{TOTAL,AGR,MAN,CON,MEL,MKT,PUB}` and `ECO_ISIC4_{TOTAL,A,B,C,DE,F,G,HJ,I,K,LMN,O,P,Q,RSTU}`. The `.csv` response body can be gzip-encoded. "Data seems not quite clean": rows are de-duplicated |
| T | [OWID etl `garden/worldbank_wdi/2026-07-27/wdi.py`](https://raw.githubusercontent.com/owid/etl/70c97056dfd7a61bc8f94925372eb6ff0c66f93e/etl/steps/data/garden/worldbank_wdi/2026-07-27/wdi.py) | In ILOEST, `obs_status == "R"` marks years based on real data. The modelled series runs about two years past the last real observation (nowcasts). The `rplumber.ilo.org/data/indicator/?id=UNE_2EAP_SEX_AGE_RT_A&ref_area=AUS&format=.csv` form works |
| T | [open-numbers/ddf--ilo--ilostat](https://github.com/open-numbers/ddf--ilo--ilostat) (`ddf--concepts--continuous.csv`, `ddf--entities--classif1.csv`, `ddf--entities--sex.csv`; TOC snapshot of Feb 2026) | Labels and codes of every catalog indicator, e.g. "Output per worker (GDP constant 2021 international $ at PPP) -- ILO modelled estimates, Nov. 2025" for `GDP_211P_NOC_NB`, with `collection`/`database` `ILOEST` and last updates on 02/12/2025. National series are in the `LFS` database ("previous ILO definition"). Classification labels for `ECO_SECTOR_*`, `ECO_AGGREGATE_*`, `ECO_ISIC4_*` (single sections), `AGE_YTHADULT_*` and `SEX_*` |
| T | [DMSILVA87/bda-indice `_cache/ilo_GDP_205U_NOC_NB.csv`](https://github.com/DMSILVA87/bda-indice) | `type=both` header `ref_area, ref_area.label, source, source.label, indicator, indicator.label, time, obs_value`, label "... -- ILO modelled estimates, Nov. 2025", source `XA:2010` "ILO - Modelled Estimates" |
| T | [bis-med-it/gingado `docs/machine_controls.html`](https://github.com/bis-med-it/gingado) | SDMX dataflow `DF_GDP_211P_NOC_NB` series keys are `(REF_AREA, FREQ=A, MEASURE=GDP_211P_NB)`, consistent with REF_AREA.FREQ.MEASURE plus one dimension per classification |
| T | [gonzalezmorales/dagstuhl-example-sdg-ilo `ILO__CL_MEASURE(1.0).csv`](https://github.com/gonzalezmorales/dagstuhl-example-sdg-ilo) | ILO measure descriptions: "Imputed observations are not based on national data, are subject to high uncertainty and should not be used for country comparisons or rankings" |

P = code by the provider (ILO). T = a third party.

### UNVERIFIED (each has a live test in `tests/live/test_ilostat_live.py`)

| Assumption | Where in the code | Live test |
|---|---|---|
| The table of contents CSV still has `indicator.label`, `last.update` and `database` (ILOEST) | `parse_toc` | `test_toc_lists_catalog_and_flags_iloest` |
| `type=code` CSV columns and the `XA:` prefix on modelled rows; `timefrom`/`timeto` honoured | `standardize` | `test_bulk_csv_columns_and_modelled_source` |
| Server-side `sex`/`classif1` filters and the literal `+` separator | `build_request`, `request_url` | `test_literal_plus_and_server_side_filters` |
| `type=both` labels carry the edition | `edition_from_label` | `test_type_both_adds_labels_with_edition` |
| ISIC aggregates add up to the total in ILOEST | used by shift-share methods | `test_kenya_employment_by_sector_identity` |
| `GDP_211P` label states its PPP base | `unit_from_label` | `test_output_per_worker_ppp_unit` |
| A `type=code` payload has no `indicator.label`, so without the table of contents the PPP base is unknown (unit `... (PPP unknown)`, caveat `ilostat_unit_base_unknown`) | `unit_from_label`, `CODE_UNITS` | `test_output_per_worker_ppp_unit_without_label` |
| The last ILOEST year flagged `R` is the last year based on national data, per country (as read by OWID etl); later years are nowcasts/projections | `_modelled_projections`, `ilostat_modelled_projection` | `test_projection_caveat_is_per_country` |
| Survey series carry no `XA:` source | modelled detection | `test_survey_series_is_not_modelled` |
| SDMX key layout REF_AREA.FREQ.MEASURE.<classif...>, classification codes as in the bulk files, SDMX-CSV column names (`REF_AREA, TIME_PERIOD, OBS_VALUE, OBS_STATUS, SOURCE`) | `build_sdmx_request`, `sdmx_to_table` | `test_sdmx_matches_bulk` |
| `obs_status` codes E, F, P (taken from SDMX, not seen in the ILOSTAT code list) | `OBS_STATUS_MAP` | none (unknown codes raise an info caveat) |
| Whether the plain `cgjrkit` User-Agent is accepted. The third party used a browser User-Agent; listing pages could come back empty without one | `core.http` | all live tests |
| Labels composed for combined ISIC codes (`ECO_ISIC4_HJ` and similar) are cgjrkit wording, marked "[label composed by cgjrkit]" | `classif_label` | none (use `fetch_dictionary("classif1")` for provider labels) |

### Kenya replication

Kenya CGJR Figs 31-35 cite "ILOSTAT (Nov 2023)" modelled estimates (footnote 24 says Nov 2022). ILOSTAT serves only the current edition. To replicate the printed figures you need either a snapshot taken when that edition was live, or WDI Database Archives (source 57) series labelled "modeled ILO estimate" for the matching WDI release. `expect_edition="Nov 2023"` flags the mismatch.
