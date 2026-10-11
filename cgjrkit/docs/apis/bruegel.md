# Bruegel real effective exchange rates (`cgjrkit.connectors.bruegel`)

Connector: `BruegelREER` in `src/cgjrkit/connectors/bruegel.py`.

- `fetch(countries=None, frequency="monthly", baskets=None, measure="REER", start=None, end=None, *, url=None, dropna=True, refresh=False)` returns REER (or NEER) indices as a canonical frame.
- `sheets(url=None)` lists the data sheets in the current file: measure, frequency and partner basket.
- `resolve_url(url=None)` returns the download URL used: the explicit URL, then `BruegelREER(url=...)`, then `$CGJRKIT_BRUEGEL_URL`, then the newest REER zip linked from the dataset page, then `FALLBACK_URL`.
- Pure functions: `find_download_links`, `pick_latest`, `build_request`, `open_workbook`, `parse_sheet`, `parse_readme`, `parse`, `infer_base_year`, `code_to_iso3`, `parse_version`, `build_caveats`.

| Item | Value |
|---|---|
| Host (`domains`) | `www.bruegel.org` |
| Dataset page | `https://www.bruegel.org/publications/datasets/real-effective-exchange-rates-for-178-countries-a-new-database` |
| File | `https://www.bruegel.org/sites/default/files/<YYYY-MM>/REER_database_ver<DDMonYYYY>.zip`, which holds `REER_database_ver<DDMonYYYY>.xls` (BIFF, about 9 MB, read with `xlrd`). Bare `.xls`/`.xlsx` workbooks and zipped `.xlsx` are also read. The format is detected from the bytes |
| Fallback URL | `.../2026-02/REER_database_ver16Feb2026.zip`, taken from third-party code. It goes stale with each release, and using it raises `bruegel_url_fallback` |
| Auth / limits | None |
| Licence | Bruegel datasets are free for any purpose with attribution, with no endorsement implied (Bruegel general data policy). **Terms specific to the REER file are UNVERIFIED** |
| Citation | Short form on charts: "Bruegel REER database (Darvas 2012, 2021)". In full (`meta["citation"]`): Darvas, Z. (2012) 'Real effective exchange rates for 178 countries: a new database', Working Paper 2012/06, Bruegel; Darvas, Z. (2021) 'Timely measurement of real effective exchange rates', Working Paper 15/2021, Bruegel |
| Vintage | `"Bruegel REER <YYYY-MM-DD>"`, taken from the `ver<DDMonYYYY>` token in the workbook or URL name. Without a token it is `"Bruegel REER snapshot sha256:<12 hex> (<retrieval date>)"`. The payload SHA-256 is always in provenance and `meta["payload_sha256"]` |

## Snapshotting a file that is overwritten in place

Bruegel keeps no archive. Every download goes through `core.http.fetch`, which stores the raw zip by content hash. Later calls are served from the cache unless `refresh=True`. Under `CGJRKIT_SNAPSHOT=<name>` the dataset page and the zip are pinned, so a replication re-runs on the same bytes. When the cache history for a URL holds more than one SHA-256 (after a `refresh=True` that found changed content), `bruegel_file_changed` (warn) lists the hashes and retrieval times.

## Workbook layout and parsing

- **Data sheets** are named `<REER|NEER>_<MONTHLY|ANNUAL>_<N>`, where N is the number of trading partners in the basket. Sheets are discovered by regex because basket sizes change between releases: `REER_MONTHLY_143` (Jun 2021 file), `REER_MONTHLY_120` (Jul and Sep 2026 files), `REER_ANNUAL_65` (2024 file), `NEER_MONTHLY_38` (Apr 2020 file). The dataset page (Mar 2026) describes an annual broad basket of 172 partners from 1995, a narrow basket of 67 from 1960, and a monthly broad basket of 120 from 1993. The 2021 paper's annex lists baskets of 120 and 51. `QUARTERLY` sheets are accepted if they appear. Other sheets (`READ ME`, CPI, weights) are listed in `meta["other_sheets"]`.
- **Headers** are in the first row: column A is the period, and the other columns are `<REER|NEER>_<N>_<code>`. A header whose measure or basket does not match its sheet is skipped and reported in `meta["skipped_columns"]`. Up to 15 leading rows are searched for the header row.
- **Periods** are `1993M01` (monthly) → `"1993-01"`; a year number or string (annual) → `"1960"`; `1999Q3` → `"1999Q3"`; Excel dates → `"YYYY-MM"`. Rows whose column A is not a period, such as notes, are skipped.
- **Country codes** are ISO 3166 alpha-2 (KE, VN, DE), mapped to ISO3 with an embedded table of 249 codes. Exceptions: `SQ` → `SRB` (Bruegel's code for Serbia), and `EA` (euro area), which is kept as `iso3="EA"` and listed in `meta["aggregates"]`. Defensive mappings: `XK`→`XKX`, `UK`→`GBR`, `EL`→`GRC`, `AN`→`ANT`. Codes that cannot be mapped are kept as they are, with `bruegel_unmapped_codes`.
- **Country names** come from the `READ ME` table: a header row containing `Country` and `code`, read until three blank rows.
- **Base period.** `infer_base_year` returns the year in which the annual mean of at least 60% of series is 100 ± 0.05. Units are then `"index, <year>=100"`, otherwise `"index"`. The provider's base year is UNVERIFIED (the live test prints it).

## Mapping to the canonical frame

| Canonical | From |
|---|---|
| `source` / `dataset` | `"bruegel"` / `"REER"` (NEER sheets use the same dataset; the indicator tells them apart) |
| `indicator` | The sheet name, which encodes measure, frequency and basket: `REER_MONTHLY_120`, `REER_ANNUAL_172`, `NEER_MONTHLY_120` |
| `indicator_label` | For example "Real effective exchange rate (CPI-based), monthly, against 120 trading partners (increase = appreciation)" |
| `iso3` / `country` | ISO3 from the column code (EA kept) / name from `READ ME` |
| `period` | `"YYYY-MM"` (monthly), `"YYYY"` (annual), `"YYYYQn"` (quarterly) |
| `value` | Index level. Blank cells are dropped unless `dropna=False` |
| `unit` | `"index, 2007=100"` when a base year is inferred, else `"index"` |
| `obs_status` | Empty. The file has no flags. The recent months computed with forecast inflation are flagged by caveat only (see below) |

`countries` accepts ISO3 (`KEN`), ISO2 or Bruegel codes (`KE`, `EA`). `start` and `end` compare as period strings: `start="2005-01"`; `end="2019"` keeps every 2019 month.

## How this was verified

`www.bruegel.org` and `wordpress.bruegel.org` do not resolve in the build environment, so the dataset page and the file were never fetched. Sources read on 2026-10-11:

| Label | Source | What it established |
|---|---|---|
| P | Search-result extracts of the [dataset page](https://www.bruegel.org/publications/datasets/real-effective-exchange-rates-for-178-countries-a-new-database) and [Bruegel data policy](https://wordpress.bruegel.org/?p=6946) | 177 countries monthly and 178 annually plus the euro area. Last update 11 Mar 2026 when indexed. Annual broad 172 partners (from 1995), narrow 67 (from 1960). Monthly broad 120 (from 1993). Datasets free with attribution |
| P | Search-result extracts of [Darvas (2021) WP 15/2021](https://www.bruegel.org/working-paper/timely-measurement-real-effective-exchange-rates) and its online annex | The real-time method combines actual nominal rates with forecast inflation (the basis for `bruegel_realtime_cpi`). Baskets of 120 and 51 partners. The recommended citations |
| T | unbalancedparentheses/forex-centuries [`scripts/update_sources.py`](https://raw.githubusercontent.com/unbalancedparentheses/forex-centuries/main/scripts/update_sources.py) | Download links scraped from the dataset page with `https://www.bruegel.org/sites/default/files/[^"']+REER[^"']*\.zip` (or the relative form). The fallback `.../2026-02/REER_database_ver16Feb2026.zip`. The zip holds the `.xls` |
| T | DJiangwei/macro-dashboard-chartbook [`generate_macro_dashboards.py`](https://raw.githubusercontent.com/DJiangwei/macro-dashboard-chartbook/main/generate_macro_dashboards.py) | The `REER_database_ver16Jul2026.xls` file (xlrd). Sheets `READ ME` and `REER_MONTHLY_120`. Headers in row 0, dates in column 0 as `YYYYMXX` strings, blank cells for missing values. Serbia is `SQ`. `READ ME` rows from 31 have the country name in column 1 and the ISO code in column 2 (a column 25 holds the `REER_120_XX` header). Its docstring calls headers "sequential IDs", which conflicts with the other sources. The parser follows the header pattern and uses `READ ME` only for names |
| T | thuyhuongctu/Fdi-Distance-Trade-Vietnam `controls_monthly.csv` | "Bruegel REER database (Darvas), version 22 Sep 2026, sheet REER_MONTHLY_120, series REER_120_VN (CPI-based, 120 trading partners)" |
| T | KMueller-Lab/Global-Macro-Database [`Bruegel_reer.do`](https://raw.githubusercontent.com/KMueller-Lab/Global-Macro-Database/main/code/clean/aggregators/Bruegel_reer.do) | Sheet `REER_ANNUAL_65`. Column A is the year and row 1 the headers. Headers `REER_65_<ISO2>` reshaped on the ISO2 suffix. `EA` dropped, `SQ` → `RS` (Serbia), the rest merged on ISO2 |
| T | vlad-yeghiazaryan/PPP-in-EAEU `EAEU_DataPrep.ipynb` | Jun 2021 file: sheet `REER_MONTHLY_143` with 177 country columns from 1993-01. Dates parse with `%YM%m`. `READ ME` has a table with columns `Country` and `code` (header on row 48) that maps the header suffix to names |
| T | burakbayramli `tser_120_exch/data.py` | Apr 2020 file: `REER_database_ver6Apr2020.zip` holds `REER_database_ver6Apr2020.xls`. Sheet `NEER_MONTHLY_38`, column `NEER_38_DE`. Dates like `1977M01` |
| T | Inventory `kenya-figures.json` (Figs 11, 20, 28) | Replication targets: monthly, 120 partners, Dec 2007 = 100 |

P = provider (seen through search extracts only). T = third party.

### UNVERIFIED (each has a live test in `tests/live/test_bruegel_live.py`)

| Assumption | Where in the code | Live test |
|---|---|---|
| The dataset page still links the zip under `/sites/default/files/` with a `ver<DDMonYYYY>` name | `find_download_links`, `pick_latest`, `parse_version` | `test_dataset_page_links_a_reer_zip` |
| The current file has `REER_MONTHLY_120`, monthly and annual sheets, `<MEASURE>_<N>_<ISO2>` headers, `YYYYMmm` periods from 1993-94, ≥170 country columns, and a `READ ME` table with `Country`/`code` | `parse_sheet`, `parse_readme`, `_period` | `test_workbook_layout` |
| The provider's base period (inferred, not assumed) | `infer_base_year` | printed via `meta["base_years"]` |
| Kenya Fig 11 (monthly, 120, Dec 2007 = 100) replicates within ±6–8 index points | — | `test_kenya_fig11_monthly_120` |
| Some annual basket reproduces Kenya Fig 20 (2007 = 100: 2001 ≈ 81, 2019 ≈ 153) | — | `test_kenya_fig20_annual` |
| Licence terms specific to the REER file | `LICENSE` | none |
| Whether the file flags the forecast-inflation months (none is assumed; the caveat is general) | `bruegel_realtime_cpi` | none |

## Caveats raised

| Code | Severity | When |
|---|---|---|
| `bruegel_snapshot` | info | Always: the file is overwritten in place; gives the SHA-256 and version |
| `bruegel_file_changed` | warn | The cache history for the URL holds more than one SHA-256 |
| `bruegel_url_fallback` | warn | The dataset page could not be read or had no link, so `FALLBACK_URL` was used |
| `bruegel_realtime_cpi` | warn | Monthly REERs are returned: the latest months use forecast inflation and are revised |
| `bruegel_base` | info | Inferred base years per sheet, with a reminder to rebase (Kenya Fig 11: Dec 2007 = 100, 120 partners) |
| `bruegel_basket_missing` | warn | A requested basket is not in this release. Lists the sheets available |
| `bruegel_unmapped_codes` | warn | Column codes with no ISO3 mapping |
| `bruegel_aggregates` | info | EA (euro area) is in the result |
| `bruegel_missing_countries` | warn | Requested countries are not in the file |
| `bruegel_empty` | warn | No rows |

## Replication notes

- **Kenya Fig 11:** `fetch("KEN", "monthly", baskets=[120], start="2005-01")`, then rebase to `2007-12` = 100. The report's axis label says "2007=100" but the title says Dec 2007.
- **Kenya Figs 20 and 28** use annual REERs with the basket not stated. Fig 28's axis label says 2000 = 100 while its title says 2007 = 100. Pick the basket explicitly and disclose it.
- REER levels depend on the basket: Kenya Fig 11 uses 120 partners. The cross-report inventory notes that the basket choice changes levels.

## Fixtures (all synthetic)

`tests/fixtures/bruegel_reer_synthetic.zip` holds `REER_database_ver01Jan2000_synthetic.xls`. Its `READ ME` sheet is marked "SYNTHETIC TEST FILE - NOT BRUEGEL DATA" and contains a `Country`/`code` table (Kenya, Uganda, Serbia `SQ`, Euro area `EA`, and a fictitious "Testland" `QZ`). Its sheets are `REER_MONTHLY_120` (with a deliberately mismatched `REER_51_TZ` column and a trailing note row), `REER_MONTHLY_51`, `REER_ANNUAL_172`, `REER_ANNUAL_67`, `NEER_MONTHLY_120` and an ignored `CPI_MONTHLY`. Values follow `100 + slope × (year − 2007) + (month − 6.5)`, so every series averages exactly 100 in 2007. Serbia is blank in 2006. The file was written with `xlwt` 1.3.0 from a scratch directory, so xlwt is not a project dependency. The xlsx variants are built in `tests/test_bruegel.py` with openpyxl.
