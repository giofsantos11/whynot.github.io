# CEPII BACI (`cgjrkit.connectors.baci`)

Connector: `BACI` in `src/cgjrkit/connectors/baci.py`. Bulk files only (there is no API).

- `exporter_totals(exporters, years)`: total exports, HS6 products and destinations with a positive value.
- `importer_totals(importers, years)`: total imports (FOB-reconciled) and origins.
- `market_concentration(exporters, years)`: HHI of export destinations (0–1), number of markets, share of the largest market.
- `product_concentration(exporters, years, level=6)`: HHI of export products at HS 2/4/6 digits, number of products, top-product share.
- `rca(exporters, years, level=6, products=None, min_rca=None)`: Balassa RCA by product.
- `count_products(exporters, years, level=6, min_value=100, min_rca=None)`: number of products above a value threshold (thousand US$) and/or an RCA threshold.
- `country_product_table(years, level=4)`: exporter × product × year values, the input for ECI methods.
- `archive(hs, version)` / `tables(hs, version, years)`: download (cached) and extract to Parquet.
- `baci_mix_caveats(objs)`: blocks combining rows from different BACI releases or HS revisions.

Every helper takes `hs` (default `HS92`), `version` (default `202601`), `years` (default: the latest year in the release), `refresh` and `local_zip` (a zip downloaded by hand).

| Item | Value |
|---|---|
| Host (`domains`) | `www.cepii.fr` |
| URL | `https://www.cepii.fr/DATA_DOWNLOAD/baci/data/BACI_{HS}_V{version}.zip` |
| HS revisions | `HS92` (1995–), `HS96` (1996–), `HS02` (2002–), `HS07` (2007–), `HS12` (2012–), `HS17` (2017–), `HS22` (2022–) |
| Current version | `202601`, released 2026-01-22, data to 2024. Corrected releases carry a letter (`202401b`) |
| Auth / rate limit | None (static files) |
| Sizes (V202601) | HS92 2,417,732,497 bytes zipped (≈8.7 GB of CSV); HS17 794,583,540 bytes; HS22 ≈300 MB |
| Licence | Etalab Open Licence 2.0: any use, commercial included, with attribution |
| Citation | Gaulier, G. and Zignago, S. (2010), *BACI: International Trade Database at the Product-Level. The 1994-2007 Version*, CEPII Working Paper 2010-23; plus the version and HS revision |
| Vintage | `Provenance.vintage = "BACI V<version>"`; the readme's release date is in the notes and `meta["release_date"]` |

## Archive layout

| Member | Content |
|---|---|
| `BACI_{HS}_Y{year}_V{version}.csv` | one per year; columns `t,i,j,k,v,q`: year, exporter code, importer code, HS6 product (text with leading zeros), value in **thousand current US$** (FOB, reconciled), quantity in **metric tons** (empty or `NA` when unknown) |
| `country_codes_V{version}.csv` | `country_code, country_name, country_iso2, country_iso3`. Areas without ISO3 have codes such as `S19` (490 "Other Asia, nes"). Namibia's ISO2 is the string `NA` (read with `keep_default_na=False`). Some releases double-encode names (fixed in `read_country_codes`) |
| `product_codes_{HS}_V{version}.csv` | `code, description` |
| `Readme.txt` | `Version: 202601`, `Release Date: 2026 01 22`, `Content`, `List of Variables`, `Reference` blocks |

## Processing

1. `http.fetch` downloads and caches the zip (by content hash). The zip sits at `<cache>/objects/<sha256>`, the cache's documented layout. Later calls never read the zip bytes: `BACI.cached_retrieval` resolves the retrieval the way `http.fetch` does (the `CGJRKIT_SNAPSHOT` pin first, then the latest cached retrieval unless `refresh=True`) and the connector opens the object path directly; `http.fetch` runs only on a cache miss. (`http.fetch` returns the whole cached payload as bytes on a hit, which for HS92 would mean 2.4 GB in memory per call.) A `local_zip=` is hashed once per process and file state (resolved path, size, mtime). A log warning precedes a network download of HS92–HS12.
2. `extract_archive` streams each requested year out of the zip to a temporary CSV and checks the header `t,i,j,k,v,q`. DuckDB then converts it to `trade_Y<year>.parquet` (`t,i,j` INTEGER, `k` VARCHAR(6, zero-padded), `v` DECIMAL(18,3), `q` DOUBLE with `NA` → NULL) and checks that `t` equals the file year. The temporary CSV is deleted. The store is `<cache>/baci/<HS>_V<version>_<sha16>/` with `manifest.json`, the code lists and the readme. Years are extracted on demand; a year already extracted is skipped.
3. Aggregations are DuckDB SQL over the Parquet files. Sums use exact DECIMAL arithmetic: HHI = Σx²/(Σx)² with Σx² in DECIMAL(38,6). Results therefore do not depend on thread scheduling, and `Result.data_sha256` is stable. Only aggregated tables reach pandas.

Definitions:

- **HHI**: Σ sᵢ² over destinations (market) or products (product), shares of the exporter's total, 0–1 scale (×10,000 for the antitrust convention). Zero flows are excluded.
- **RCA (Balassa)**: (x_cp / X_c) / (x_wp / X_w). The world is every BACI exporter in the year (statistical areas included) at the chosen HS level.
- **Products / markets**: distinct codes with a positive value.

## Mapping to the canonical frame

| Canonical | From |
|---|---|
| `source` / `dataset` | `"baci"` / `"BACI <HS> V<version>"` (so releases never collide) |
| `indicator` | `exports_total`, `export_products`, `export_markets`, `imports_total`, `import_origins`, `export_market_hhi`, `export_market_top_share`, `export_product_hhi_hs<L>`, `export_products_hs<L>`, `export_product_top_share_hs<L>`, `rca_hs<L>:<code>`, `export_products_hs<L>_gt<N>k[_rca<R>]`, `exports_hs<L>:<code>` |
| `iso3` / `country` | `country_iso3` / `country_name` from the release's country list. Areas keep the BACI code (`S19`), listed in `meta["aggregates"]`. Codes not in the list become `BACI<code>` (`meta["unmapped_codes"]`) |
| `period` | `t` |
| `value` / `unit` | As defined above; values in `thousand current US$` |

Provenance has two entries: the BACI download (endpoint, zip hash, vintage, licence, citation), then `Provenance(source="cgjrkit", tool="BACI.<helper>", params=...)`.

## How this was verified

`www.cepii.fr` is blocked in the build environment (proxy 403), so nothing was downloaded. Sources read on 2026-10-11:

| Label | Source | What it established |
|---|---|---|
| P | CEPII `Readme.txt` of V202601, verbatim copy in [EdoDeo0/Paper_PTA `Data/Matching/Readme_BACI.txt`](https://raw.githubusercontent.com/EdoDeo0/Paper_PTA/e36781fcf19ae88b9a4a7527e2042889f667be7d/Data/Matching/Readme_BACI.txt) | Version 202601, release date 2026 01 22, values in thousand USD, quantities in metric tons, variables `t,i,j,k,v,q`, reference |
| P (snippet) | CEPII [DescriptionBACI.html](https://www.cepii.fr/DATA_DOWNLOAD/baci/doc/DescriptionBACI.html) and [release notes 202601](https://www.cepii.fr/DATA_DOWNLOAD/baci/doc/release_notes_202601.pdf), via search snippets | File names `BACI_HSxx_Yyear_Vversion.csv`, versions `YYYYMM`, do not treat `k` as numeric, do not mix versions. The 202601 update refreshed the Comtrade source and simplified FOB conversion |
| T | ONE Campaign `bblocks-data-importers` 0.5.0 (PyPI, MIT), `baci/baci.py` and `baci/data_manager.py` | Links are read from `section#download-links` of `baci_webpage.html`. Member patterns `BACI_<HS>*.csv`, `product_codes*`, `country_codes*`. Country columns `country_code, country_name, country_iso2, country_iso3`; product columns `code, description`. Readme parsed as `key: value` blocks |
| T | [JacobFV/world-model `data/cepii_baci_hs92/README.md`, `data/cepii_baci/README.md`, `pipeline.py`, tests](https://raw.githubusercontent.com/JacobFV/world-model/890cdfa85ef181c668fd841e102f12a17652c3a6/data/cepii_baci/README.md) | URL pattern for V202601. HS92 size 2,417,732,497 bytes (verified 2026-09-15); HS17 795 MB, 2017–2024. Member names. Empty `q`. Area 490 = `S19`. Latin-1 double encoding in names. Files sorted by `t,i,j,k` |
| T | [materials-atlas `check_baci_release.py`](https://github.com/materials-atlas/critical-materials-atlas), [cran/datazoom.amazonia `R/download.R`](https://github.com/cran/datazoom.amazonia), energyLS/shift, iiasa/message-ix-models (`pooch.py`), elkassabgi/econdatalibrary | HS17 V202601 794,583,540 bytes, through 2024. HS92 V202601 1995–2024. HS22 V202601 2022–2024. V202501 zip hash pinned by IIASA. Letter-suffixed versions (`V202401b`) |

P = the provider's own text. T = a third party.

### UNVERIFIED (live tests in `tests/live/test_baci_live.py`)

| Assumption | Where | Live test |
|---|---|---|
| HS22 V202601 covers 2022–2024 and its readme says release date 2026 | `HS_FIRST_YEAR`, defaults | `test_release_layout` |
| `v` has at most 3 decimals (cast to DECIMAL(18,3) rounds anything finer to US$1) | `_csv_to_parquet_sql` | `test_kenya_exports_and_tea_rca` (magnitudes only) |
| Every release uses the same member names and header | `archive_members`, `extract_archive` | `test_release_layout` |
| Kenya magnitudes: goods exports of several US$ billion, RCA in black tea above 10 | — | `test_kenya_exports_and_tea_rca` |

## Caveats raised

| Code | Severity | When |
|---|---|---|
| `baci_units` | info | Always: thousand current US$ (FOB), metric tons, reconciled mirror flows |
| `baci_version` | info | Always: results depend on the release and HS revision; the latest years are revised in the next release |
| `baci_unmapped_codes` | warn | Trade codes not in the release's country list |
| `baci_areas` | info | Statistical areas without ISO3 in the result |
| `hhi_scale` | info | HHI outputs (0–1 scale) |
| `baci_empty` | warn | No rows |
| `baci_mixed_versions` | **block** | `baci_mix_caveats` finds more than one `dataset` (release or HS revision) |

## Replication notes

- X06 (export market HHI, destinations) and Kenya Figs 24–25 (EU agri exports at HS6): use `market_concentration` and `rca` on one pinned release. Use HS92 for long series; HS17/HS22 give finer recent product detail but start in 2017/2022.
- ECI (Kenya Fig 26) is computed by methods from `country_product_table(level=4)` and cross-checked with the Atlas connector. Atlas trade data are cleaned differently, so ranks will not match exactly.

## Core change requests

- `core.http.fetch` holds the whole payload in memory and returns bytes. The HS92 zip is 2.4 GB. A streaming mode would write chunks to the cache object while hashing and return a path. Cache hits no longer go through `http.fetch` (see Processing, step 1), so only the first download is affected; it still needs about 2.4 GB of free memory for HS92. A public `Cache.object_path(sha)` accessor would also help: the connector reads `<cache>/objects/<sha>` directly, as documented in `core/cache.py`.

## Fixtures (all synthetic)

`tests/fixtures/baci_hs92_v209901_synthetic.zip`: release "209901", years 2097–2098. It uses the BACI member names, the `t,i,j,k,v,q` header, the country/product code list columns and the readme layout. Values are invented round numbers chosen so HHI and RCA can be checked by hand (see the docstring of `tests/test_baci.py`). It includes an empty and an `NA` quantity, a zero flow, area `S19`, Namibia `NA` and an unmapped code 999. Product descriptions are labelled "Synthetic product …". Generator: kept outside the repository; the fixture is small and self-describing.
