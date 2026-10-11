# Penn World Table (`cgjrkit.connectors.pwt`)

Connector: `PWT` in `src/cgjrkit/connectors/pwt.py`.

- `fetch(version="10.01", variables=DEFAULT_VARIABLES, countries=None, start=None, end=None, *, file_format="xlsx", resolve=False, url=None, dropna=True, refresh=False)` returns PWT variables as a canonical frame.
- `dataset_files(version)` lists the files in the version's Dataverse record (Dataverse native API).
- Pure functions: `build_request`, `build_file_request`, `build_dataset_request`, `parse`, `parse_dataset_files`, `select_file`, `read_table`, `infer_reference_year`, `checksum_ok`, `build_caveats`. `pwt_mix_caveats(frame | Result | [...])` blocks combining PWT series on different PPP bases.

| Item | Value |
|---|---|
| Hosts (`domains`) | `dataverse.nl` (10.01, 11.0), `www.rug.nl` (10.0 only) |
| Download | Dataverse Data Access API `https://dataverse.nl/api/access/datafile/<id>`, no key. File ids are pinned per version (table below). `resolve=True` reads the dataset record `https://dataverse.nl/api/datasets/:persistentId/?persistentId=doi:<DOI>` and picks the main file by name |
| Formats | `.xlsx` (sheets `Info`, `Legend`, `Data`) and `.dta` (Stata 118). Also read: tab-separated text (the Dataverse "archival" copy of an ingested `.dta`), CSV, and a zip that holds `pwt*.xlsx`/`.dta`. The format is detected from the payload bytes |
| Auth / limits | None. Third-party code reports that Dataverse answers HEAD with 403 while GET works; the connector only uses GET |
| Licence | CC BY 4.0 (PWT pages; CRAN pwt10 docs) |
| Citation | Short form on charts: "Penn World Table (Feenstra, Inklaar and Timmer 2015)". The full reference is in `Result.meta["citation"]`: Feenstra, Robert C., Robert Inklaar and Marcel P. Timmer (2015), "The Next Generation of the Penn World Table", *American Economic Review*, 105(10), 3150-3182 |
| Vintage | `Provenance.vintage = "PWT <version>"` (for example `"PWT 10.01"`). With `resolve=True` the Dataverse dataset version (for example `1.0`) is added to the notes and to `meta["file"]["dataset_version"]` |
| Speed | The xlsx file takes about 4 s to parse at full size and the dta about 0.1 s (measured on a full-size copy). Parsed tables are memoised per payload (4 entries) |

## Versions and files

| Version | DOI | PPP base / reference year | Coverage | xlsx | dta |
|---|---|---|---|---|---|
| `10.01` (default) | 10.34894/QT5BCC | 2017 | 183 countries, 1950–2019; released 23 Jan 2023 | `pwt1001.xlsx`, datafile **354095** | `pwt1001.dta`, datafile **354098** |
| `11.0` | 10.34894/FABVLR | 2021 | 185 countries, 1950–2023; released 7 Oct 2025 | `pwt110.xlsx` (name UNVERIFIED), datafile **554105** | `pwt110.dta` (name UNVERIFIED), datafile **554030** |
| `10.0` (legacy) | 10.15141/S50T0R | 2017 | 183 countries, 1950–2019 | `https://www.rug.nl/ggdc/docs/pwt100.xlsx` | `https://www.rug.nl/ggdc/docs/pwt100.dta` |

Accepted version spellings: `10.01`, `1001`, `pwt1001`, `PWT 10.01`; `11`, `11.0`, `110`; `10.0`, `100`.

**Direct rug.nl links do not exist for 10.01 or 11.0.** `www.rug.nl/ggdc/docs/pwt1001.xlsx`, `pwt110.xlsx`, `pwt110.dta` and `pwt11.xlsx` return HTTP 404 in third-party agent logs. Those releases point to Dataverse. The "file access by persistent ID" form (`/api/access/datafile/:persistentId?persistentId=...`) needs per-file DOIs, which Dataverse provides only when `:FilePIDsEnabled` is on. DataverseNL's setting is unknown, so the connector uses numeric file ids.

**Ingested tabular files.** Dataverse can ingest a `.dta` into a `.tab` file. The default download is then tab-separated text, and `format=original` returns the Stata file. `select_file` matches `originalFileName`. For ingested files `fetch(resolve=True)` sends `format=original`, and the parser can read the TSV form as well. Third-party clients read datafile 354098 directly as Stata, which suggests the main files are not ingested.

## Mapping to the canonical frame

| Canonical | From |
|---|---|
| `source` / `dataset` | `"pwt"` / `"PWT<version>"` (`PWT10.01`, `PWT11.0`, `PWT10.0`). Versions never collide |
| `indicator` | PWT variable name (`rgdpna`, `rnna`, `rkna`, `rtfpna`, `emp`, `hc`, `labsh`, `pop`, `rgdpo`, `cgdpo`, `avh`, `delta`, `csh_i`, ... all 43 numeric variables of 10.01 are catalogued). Variables outside the catalogue are accepted if the file has them, and they keep the file's own label |
| `indicator_label` | The catalogue label with the reference year, for example "Real GDP at constant 2017 national prices". It is the same whichever format was read. The file's own Legend or Stata labels are kept in `meta["file_labels"]` |
| `iso3` / `country` | `countrycode` / `country` (PWT has no aggregates). The CRAN-style column `isocode` is also accepted |
| `period` | `year` as a string |
| `value` | float. Missing values are dropped unless `dropna=False` |
| `unit` | Written by cgjrkit from the Legend definitions. PPP-based variables state the base so that `schema.ppp_base()` can parse it: `rgdpna`/`rnna`/`rconna`/`rdana` → `"million 2017 US$ at constant 2017 national prices (PPP 2017)"`; `rgdpe`/`rgdpo` → `"... at chained PPPs (PPP 2017)"`; `cgdp*`/`cn`/`ccon`/`cda` → `"... at current PPPs (PPP 2017)"`; `ck`/`ctfp`/`cwtfp` → `"index, USA=1 (current PPPs, PPP 2017)"`; `csh_*` → `"share at current PPPs (PPP 2017)"`; `pl_*` → price levels (PPP 2017). Not PPP-based: `rkna`/`rtfpna`/`rwtfpna` → `"index, 2017=1"`, `emp`/`pop` → `"millions"`, `hc` → `"index"`, `labsh` → `"share of GDP (0-1)"`, `delta`/`irr` → `"rate (0-1)"`, `avh`, `xr`. For 11.0 every 2017 becomes 2021 |
| `obs_status` | Always empty. PWT has no status codes. The flags `i_outlier` and `i_xr` raise caveats instead (see below) |

**Reference year check.** `infer_reference_year` finds the year in which `rkna` (or `rtfpna`/`rwtfpna`) equals 1 for most countries. Units use the inferred year. If it differs from the version table, `pwt_reference_year_mismatch` (warn) is raised. This catches a mislabelled file or a wrong version argument.

**Flags.** `i_cig`, `i_xm`, `i_xr`, `i_outlier` and `i_irr` are rejected as `variables`. `i_outlier` counts as set when its value is `1`, `Outlier`, `yes` or `true`. `i_xr` counts as set when its value is `1`, `Estimated` or `yes`. These codings come from the CRAN copy (yes/no, market/estimated) and from Stata value labels. The numeric coding of the archival TSV form is UNVERIFIED.

## Replication: Kenya CGJR Fig 4 (PWT 10.01)

Recipe: GDP per worker = `rgdpna / emp`, capital per worker = `rnna / emp`, TFP = `rtfpna`. Each series is rebased to 2001 = 100. The capital stock is `rnna`, not the capital services index `rkna`: with `rkna` the 2019 value would be about 154.

Checked offline on the PWT 10.01 data redistributed in CRAN package `pwt10` 10.01-0 (GitHub mirror `cran/pwt10`, `data/pwt10.01.rda`). The file was converted to the official xlsx/dta layout in a scratch directory and run through `parse`. It is not used as a fixture.

| Series | 2001 | 2007 | 2008 | 2019 | Report (read from the chart) |
|---|---|---|---|---|---|
| GDP per worker | 100 | 109.6 | 106.4 | **135.8** | ~135–136 in 2019; local peak ~109–110 in 2007 |
| Capital per worker | 100 | 103.3 | 104.8 | **136.1** | ~136 in 2019 |
| TFP | 100 | 101.6 | 97.5 | **104.3** | ~104 in 2019; dip to ~97 around 2008 |

`tests/live/test_pwt_live.py::test_kenya_fig4_pwt1001` asserts these values within ±3 against the live Dataverse file.

## How this was verified

The data hosts (dataverse.nl, www.rug.nl) are blocked in the build environment, so nothing was downloaded from them. Sources read on 2026-10-11:

| Label | Source | What it established |
|---|---|---|
| P | GGDC pages (via search results): [PWT 10.01 release](https://www.rug.nl/ggdc/productivity/pwt/pwt-releases/pwt1001), [PWT 11.0 release](https://www.rug.nl/ggdc/blog/penn-world-table-version-11-0-is-published) | 10.01: 183 countries, 1950–2019, published 23 Jan 2023, "hosted on Dataverse… DOI 10.34894/QT5BCC", CC BY 4.0, cite Feenstra, Inklaar and Timmer (2015). 11.0: published 7 Oct 2025, DOI 10.34894/FABVLR, 185 countries to 2023 |
| P | Dataverse guides source ([dataaccess.rst](https://raw.githubusercontent.com/IQSS/dataverse/develop/doc/sphinx-guides/source/api/dataaccess.rst), [native-api.rst](https://raw.githubusercontent.com/IQSS/dataverse/develop/doc/sphinx-guides/source/api/native-api.rst)) | `/api/access/datafile/$id`. `:persistentId` file access only with `FilePIDsEnabled`. `format=original` applies to tabular files only. `/api/datasets/:persistentId/?persistentId=` returns the dataset JSON |
| T | CRAN `pwt10` 10.01-0 ([GitHub mirror](https://github.com/cran/pwt10): `DESCRIPTION`, `man/pwt10.01.Rd`, `data/pwt10.01.rda`) | Variable list and definitions for 10.01 (52 columns; base year 2017; "in million 2017 USD"; `rkna`/`rtfpna` 2017 = 1). Source DOI 10.34894/QT5BCC. The flag codings. **Kenya values above** |
| T | kvelleby/poldat [`R/pwt.R`](https://raw.githubusercontent.com/kvelleby/poldat/main/R/pwt.R) | 11.0 → `dataverse.nl/api/access/datafile/554030` and 10.01 → `.../354098`, both read with `haven::read_dta`. 10.0/9.1/9.0 → `www.rug.nl/ggdc/docs/pwt100.dta` etc. |
| T | ever-works/awesome `details/penn-world-table.md`; elkassabgi/econdatalibrary (`jobs/ingest_ggdc.py`, `updater/strategies/fetchers/ggdc.py`, `docs/runbook/ggdc.md`); unbalancedparentheses/forex-centuries `scripts/update_sources.py` | 11.0 Excel = datafile **554105**, Stata = **554030**. Sheet `Data`, id columns `countrycode, country, currency_unit, year`. Dataverse HEAD returns 403 but GET works. `/api/files/{id}` gives `data.dataFile.checksum` |
| T | jupyter-naas OWID notebook, smkwray/geoluck `etl/fetch_pwt.py`, harbor-framework task text | 10.01 `pwt1001.xlsx` = datafile **354095**, sheet `Data`, years 1950–2019 |
| T | Agent trajectories in benchflow-ai/skillsbench-trajectories and linyouxue/skill-repair-benchmark (GitHub code search) | `pwt1001.xlsx` sheets `['Info', 'Legend', 'Data']`. The 11.0 Legend printout (`Variable name`, `Variable definition`, one-cell group rows): "rgdpna: Real GDP at constant 2021 national prices (in mil. 2021US$)", "rkna: … (2021=1)", "rtfpna: TFP at constant national prices (2021=1)". The file name `pwt110.xlsx`. HTTP 404 for `www.rug.nl/ggdc/docs/pwt1001.xlsx`, `pwt110.*`, `pwt11.*` |
| T | [FRED/ALFRED PWT 11.0 series](https://alfred.stlouisfed.org/release?rid=285) (via search) | 11.0 `rgdpna` in millions of 2021 US$. Capital/TFP indices moved to 2021 = 1 |
| T | Cosroe/Bachelor_Thesis `pwt1001/MANIFEST.TXT` (a Dataverse "download all" bundle) | File list of the 10.01 record: `pwt1001.xlsx` (6,551,843 bytes), `pwt1001.dta` (Stata 14), plus `Underlying detail/…`, `Earlier vintage National Accounts/…`, ICP files |
| T | Inventory `kenya-figures.json` (Fig 4) | Report values and the recipe |

P = provider documentation. T = third party.

### UNVERIFIED (each has a live test in `tests/live/test_pwt_live.py`)

| Assumption | Where in the code | Live test |
|---|---|---|
| Pinned datafile ids 354095/354098 (10.01) and 554105/554030 (11.0) are the main xlsx/dta files of the DOI records, and the 11.0 names are `pwt110.xlsx`/`pwt110.dta` | `VERSIONS` | `test_dataverse_record_lists_pinned_files` |
| The xlsx and dta files hold the same values | `read_table` | `test_xlsx_and_dta_agree` |
| The dataset JSON layout (`data.latestVersion.files[].dataFile.{id,filename,checksum}`) and that checksums match the served bytes | `parse_dataset_files`, `checksum_ok` | `test_resolve_matches_pinned_and_checksum` |
| 11.0 reference year 2021, data to 2023, ≥185 countries, Legend wording | `VERSIONS["11.0"]`, `infer_reference_year` | `test_pwt11_reference_year_and_coverage` |
| Kenya Fig 4 replicates from the live 10.01 file | — | `test_kenya_fig4_pwt1001` |
| `www.rug.nl/ggdc/docs/pwt100.xlsx` still serves 10.0 | `VERSIONS["10.0"]` | `test_pwt100_rug_route` |
| The 11.0 China note ("official growth series") | `pwt_china_series` caveat | none (from the research notes in the build plan) |

## Caveats raised

| Code | Severity | When |
|---|---|---|
| `pwt_ppp_base` | info | Any PPP-based variable is returned. It names the PPP base |
| `pwt_reference_year_mismatch` | warn | The file's reference year differs from the version table |
| `pwt_coverage` | info | Always when rows are returned: the version's last year (10.x: 2019, 11.0: 2023) |
| `pwt_outlier` | warn | `i_outlier` is set in the selection and a PPP-level variable (`rgdpe`, `rgdpo`, `cgdpe`, `cgdpo`, `ccon`, `cda`, `pl_con`, `pl_da`, `pl_gdpo`) is requested |
| `pwt_estimated_xr` | info | `xr` is requested and some exchange rates are estimated (`i_xr`) |
| `pwt_china_series` | info | 11.0 with CHN in the result |
| `pwt_missing_variables` / `pwt_missing_countries` | warn | Requested items are not in the file |
| `pwt_checksum_mismatch` | warn | `resolve=True` and the payload does not match the Dataverse checksum |
| `pwt_empty` | warn | No rows |
| `pwt_mixed_ppp` | **block** | `pwt_mix_caveats` finds PWT units on more than one PPP base (10.x with 11.0) |
| `pwt_mixed_versions` | warn | `pwt_mix_caveats` finds several PWT datasets on the same PPP base (10.0 with 10.01) |

## Fixtures (all synthetic)

`tests/test_pwt.py` builds its workbooks, Stata files and TSV files in the documented layout: sheets `Info`/`Legend`/`Data`, Legend rows with one-cell group headers, and the wide table `countrycode, country, currency_unit, year, …`. Values are invented round numbers, for example `rgdpna = 1000 × (1 + 0.1 × (year − 2017))`, with `rkna = 1` in the reference year. `tests/fixtures/pwt_dataverse_dataset_synthetic.json` follows the Dataverse dataset JSON layout. Its ids are `9000xx` and its checksums are `000…`, so none of it is real.
