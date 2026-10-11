# UN World Population Prospects 2024 (`cgjrkit.connectors.wpp`)

Connector: `WPP` in `src/cgjrkit/connectors/wpp.py`.

- `fetch(indicators=("TPopulation1July",), countries="all", start=None, end=None, *, variant="Medium", include_aggregates=False)`: demographic indicators (54 columns, for example `TPopulation1July`, `TFR`, `LEx`, `MedianAgePop`, `NetMigrations`).
- `total_population(countries, start, end, *, variant="Medium", sexes=("total",))`: total population by sex for any of the 18 variants.
- `age_structure(countries, start, end, *, variant="Medium", sexes=("male","female","total"), reference="jul1"|"jan1")`: 5-year age groups, the input for population pyramids.
- `working_age_population(countries, start, end, *, bands=("15-64","15+"), sexes=("total",), dependency=True, shares=True)`: banded sums, shares of population and dependency ratios. Appends a `cgjrkit` provenance step.
- `fetch_api(indicator_ids, locations, start, end)` and `api_locations()`: the Data Portal API. It needs a token in `CGJRKIT_WPP_KEY`.
- Pure functions: `build_request`, `parse_bulk`, `read_bulk`, `aggregate_ages`, `pyramid_table`, `build_api_request`, `parse_api`, `parse_locations`, `build_caveats`.

| Item | Value |
|---|---|
| Host (`domains`) | `population.un.org` |
| Bulk base URL | `https://population.un.org/wpp/assets/Excel%20Files/1_Indicator%20(Standard)/CSV_FILES/` (catalogue: `https://population.un.org/wpp/assets/downloads.json`) |
| Data Portal API | `https://population.un.org/dataportalapi/api/v1` |
| Auth | Bulk: none. API data endpoints: `Authorization: Bearer <token>`. The token is requested by form and sent by email. Catalogue endpoints such as `/locations` answer without a token |
| Rate limits | Not published. The API pages at 100 records. Bulk files are 16-300 MB, so downloads use a 600 s timeout |
| Format | gzip CSV, UTF-8 with BOM, read in chunks with only the needed columns and rows kept |
| Licence | CC BY 3.0 IGO ("Copyright © 2024 by United Nations, made available under a Creative Commons license CC BY 3.0 IGO", footer of the download catalogue). Do not imply UN endorsement |
| Citation | "United Nations, Department of Economic and Social Affairs, Population Division (2024). World Population Prospects 2024, Online Edition" |
| Vintage | `Provenance.vintage = "WPP 2024"` (released 11 July 2024; bulk CSV Last-Modified 13 Dec 2024). Next revision expected in 2027 (third-party note) |

## Files used

| Key | File | Variants | Reference date |
|---|---|---|---|
| `demographic_indicators` | `WPP2024_Demographic_Indicators_Medium.csv.gz` (16,557,272 bytes, 67 columns, 84,360 rows) | Medium | period or 1 July |
| `demographic_indicators_other` | `WPP2024_Demographic_Indicators_OtherVariants.csv.gz` (**name UNVERIFIED**) | all except Medium | as above |
| `total_population` | `WPP2024_TotalPopulationBySex.csv.gz` (720,210 rows) | all 18 | 1 July |
| `population_age5` | `WPP2024_PopulationByAge5GroupSex_Medium.csv.gz` (**existence disputed**: listed in several scripts, but one says it is absent from the portal; the connector falls back to the 1 January file on 404 and raises `wpp_reference_date`) | Medium | 1 July |
| `population_age5_jan1` | `WPP2024_Population1JanuaryByAge5GroupSex_Medium.csv.gz` | Medium | 1 January |
| `population_age5_jan1_other` | `WPP2024_Population1JanuaryByAge5GroupSex_OtherVariants.csv.gz` | all except Medium | 1 January |

Columns shared by all files: `SortOrder, LocID, Notes, ISO3_code, ISO2_code, SDMX_code, LocTypeID, LocTypeName, ParentID, Location, VarID, Variant, Time`. Total population adds `MidPeriod, PopMale, PopFemale, PopTotal, PopDensity`. Age files add `MidPeriod, AgeGrp, AgeGrpStart, AgeGrpSpan, PopMale, PopFemale, PopTotal`. Population is in thousands.

Variants (VarID): Medium 2, High 3, Low 4, Constant fertility 5, Instant replacement 6, Zero migration 7, Constant mortality 8, No change 9, Momentum 10, Instant replacement zero migration 16, No fertility below age 18 17, Accelerated ABR decline 18, "Accelarated ABR decline w/rec." 19 (the misspelling is the provider's), Median PI 202, Upper/Lower 80 PI 203/204, Upper/Lower 95 PI 206/207. Only Medium covers the estimates period.

## Mapping to the canonical frame

| Canonical | From |
|---|---|
| `source` / `dataset` | `"wpp"` / `"WPP2024"` for Medium, `"WPP2024 <Variant>"` otherwise, so variants never collide on the key. The API uses `"WPP2024 API"` |
| `indicator` | Provider column (`TPopulation1July`, `PopTotal`). Age groups: `<column>\|<AgeGrp>` (`PopFemale\|20-24`). Derived: `<column>\|<band>` (`PopTotal\|15-64`), `share\|<column>\|<band>`, `dependency_ratio`, `dependency_ratio_young`, `dependency_ratio_old`. API: `api:<indicatorId>[\|<sex>][\|<age>]` |
| `indicator_label` / `unit` | Built-in table for the 54 demographic indicators (labels written by cgjrkit after the WPP metadata). Population rows are in `thousands`; shares in `% of population (same sex, all ages)`; dependency ratios `per 100 persons aged 15-64` |
| `iso3` / `country` | `ISO3_code` / `Location`. Aggregates (empty ISO3) become `LOC<LocID>` (`LOC900` = World), are excluded unless `include_aggregates=True` or asked for by id, and are listed in `meta["aggregates"]`. Kosovo is `XKX` |
| `period` | `Time` |
| `value` | Provider value; empty means NaN |
| `obs_status` | `forecast` from 2024 (projections), `""` for the 1950-2023 estimates |

Locations can be given as ISO3 codes, numeric UN location ids (`404`, `900`) or `LOC<id>`.

`aggregate_ages` sums 5-year groups into bands. A band that cuts through a group raises `ValueError`. A missing group makes the band NaN rather than silently too small. `pyramid_table(frame, iso3, year)` returns male and female counts and the percentage of the total population for each age group, youngest first.

## Caveats raised

| Code | Severity | When |
|---|---|---|
| `wpp_projection` | warn | Rows from 2024 (projections; the variant is in the evidence) |
| `wpp_togo_interim_update` | warn | Togo in the result. The January 2026 interim update re-adjusted Togo's 2010 and 2022 censuses (2022 population 8.2 million official against 9.1 million in WPP 2024). The bulk CSV predates it; corrected files are in `WPP2024_CSV_files_update.zip`. Aggregates were not revised |
| `wpp_aggregates` | info | Aggregates present |
| `wpp_missing_locations`, `wpp_missing_columns`, `wpp_empty_selection` | warn | Requested locations or columns are absent, or nothing matched (for example a non-Medium variant asked of a Medium file) |
| `wpp_reference_date` | info | The 1 January age file was used in place of 1 July (fallback, or non-Medium variants) |

## How this was verified

The data host is blocked in the build environment, so nothing was called live. Sources read on 2026-10-11:

| Label | Source | What it established |
|---|---|---|
| T | [UNopenGIS/foil4g, "World Population Prospects"](https://raw.githubusercontent.com/UNopenGIS/foil4g/d262e44736a834b64ad19682645665815d8007d7/docs/data_source/UN%20DESA/World%20Population%20Prospects.md) (live checks on 2026-10-06) | Base URL and `downloads.json` (351 files, 42 CSV). Common columns. The 67 columns of the demographic indicators file and their units (thousands, per 1,000, %, years). 84,360 rows = 555 locations × 152 years. Aggregates have empty ISO3 and empty `LocTypeName` for special groupings. The 18 variants with VarIDs. TotalPopulationBySex has 720,210 rows. UTF-8 BOM. Last-Modified 2024-12-13. The Togo interim update of 2026-01-19 and its zip. Licence footer and suggested citation. Data Portal API: catalogue 200, data 401 without a token. Japan values (TPopulation1July 1950 = 86,443.277 and 2024 = 123,753.041; TFR 2024 = 1.2168; LEx 2024 = 84.852; 2100 Medium/High/Low = 76,845.989 / 106,414.196 / 53,421.354) |
| T | [basedosdados `models/world_un_wpp/code/download.py`](https://github.com/basedosdados/pipelines) | Asset URLs for `Demographic_Indicators_Medium`, `Population1JanuaryByAge5GroupSex_Medium` and the two single-age files. Note (checked 2026-07-09) that a 1 July 5-year file "does not exist on the portal" |
| T | [energyandcleanair/creahia `R/population.R`](https://github.com/energyandcleanair/creahia), [kenshin-morioka/ds `world_stats.py`](https://github.com/kenshin-morioka/ds), [open-numbers `etl/scripts/urls.txt`](https://github.com/open-numbers/ddf--unpop--world_population_prospects) | `WPP2024_PopulationByAge5GroupSex_Medium.csv.gz` downloaded from the assets path (contradicts the note above). Age-file columns `ISO3_code, Location, Variant, Time, AgeGrp, AgeGrpStart, AgeGrpSpan, PopMale, PopFemale, PopTotal`. Full list of WPP2024 CSV file names |
| T | Header rows in [sergeionlyart/BY_maps](https://github.com/sergeionlyart/BY_maps), [healthradartool/HealthRADAR](https://github.com/healthradartool/HealthRADAR), [aliakbars/weird-icwsm](https://github.com/aliakbars/weird-icwsm) | Exact column order of the indicators file and of TotalPopulationBySex (`MidPeriod, PopMale, PopFemale, PopTotal, PopDensity`). Aggregate rows have empty `SortOrder`/`ISO3_code` |
| T | [earth-love-united `tools/acquire-wpp-2024.js`](https://github.com/earth-love-united/earthloveunited.org), [datasets/datapressr PROVENANCE.md](https://github.com/datasets/datapressr) | Size 16,557,272 bytes, SHA-256 `286ac36b…82bc`, Last-Modified "Fri, 13 Dec 2024 19:11:17 GMT", 555 rows for 2024 Medium. Licence CC BY 3.0 IGO |
| T | Data Portal clients: [mozilla/bigquery-etl `population_v1/query.py`](https://github.com/mozilla/bigquery-etl), [anthonymoisan/angelmanDataExtract `scraperPopulation.py`](https://github.com/anthonymoisan/angelmanDataExtract), [EAPD-DRB/Population-Data `fetch_un_data.py`](https://github.com/EAPD-DRB/Population-Data), [PSLmodels/OG-Core `demographics.py`](https://github.com/PSLmodels/OG-Core) | `/data/indicators/{ids}/locations/{ids}/start/{y0}/end/{y1}` with `Authorization: Bearer <token>`, `pagingInHeader=false`, `format=json`, `pageNumber`, `pageSize=100`. JSON `{"data": [...], "nextPage": ...}` with fields `locationId, location, iso3, indicatorId, indicatorDisplayName, variant, timeLabel, sex, ageLabel, value`. `/locations?sort=id&pageNumber&pageSize` gives `id, name, iso2, iso3`. Indicator 49 = total population by sex. `format=csv` is pipe-separated with the header on line 2. The server may need legacy TLS renegotiation |
| V | Plan research (`inventory/build-plan.md` §4.1) | The API needs an emailed token; 100 rows per page; WPP 2024 is the latest edition found |

P = provider code (none exists for WPP). T = a third party. V = verified on an official page earlier in the project.

### UNVERIFIED (each has a live test in `tests/live/test_wpp_live.py` where possible)

| Assumption | Where in the code | Live test |
|---|---|---|
| Values and column names of the demographic indicators file | `INDICATORS`, `parse_bulk` | `test_demographic_indicators_japan`, `test_demographic_indicators_has_67_columns` |
| Variant filtering by `VarID`/`Variant` in TotalPopulationBySex | `_row_filter` | `test_total_population_variants_japan_2100` |
| `WPP2024_PopulationByAge5GroupSex_Medium.csv.gz` exists (otherwise the 1 January fallback is used) | `FILES["population_age5"]`, `WPP._age_file` | `test_age5_july_file_and_working_age` |
| `WPP2024_Demographic_Indicators_OtherVariants.csv.gz` exists | `FILES["demographic_indicators_other"]` | `test_other_variants_demographic_file_exists` |
| Indicator labels and the non-population units (cgjrkit wording after the WPP metadata, which was not read) | `INDICATORS` | none: check against `WPP2024_F02_Metadata.xlsx` |
| Data Portal JSON fields, paging, variant names ("Median") and units (persons or thousands) | `parse_api`, `build_api_request` | `test_data_portal_api_total_population` (needs `CGJRKIT_WPP_KEY`) |
| Whether httpx can complete TLS with the Data Portal server, which one client reports needs legacy renegotiation | `core.http` | `test_data_portal_api_total_population` |
