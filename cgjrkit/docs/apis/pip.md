# Poverty and Inequality Platform API (`cgjrkit.connectors.pip`)

Connector: `PIP` in `src/cgjrkit/connectors/pip.py`.

- `fetch_poverty(countries, years="all", povline=3.0, ppp_version=2021, release_version=None, fill_gaps=False, *, welfare_type="all", reporting_level="national", measures=None, nowcast=False)` returns country statistics from `/pip`.
- `fetch_aggregates(regions="all", years="all", povline=3.0, ppp_version=2021, release_version=None)` returns World Bank regional and global aggregates from `/pip-grp?group_by=wb`.
- `versions()` returns the data-version table from `/versions`.
- `poverty_mix_caveats(frame | Result | [Result])` is a pure check that blocks mixing PPP rounds and warns on mixed poverty lines.

| Item | Value |
|---|---|
| Host (`domains`) | `api.worldbank.org` |
| Base URL | `https://api.worldbank.org/pip/v1` |
| Auth | None (no key in any client) |
| Rate limits | Not published. The API answers 429 "Rate limit is exceeded. Try again in N seconds" (pipr `retry_after`). `core.http.fetch` retries 429 with backoff |
| Format | `format=csv`, parsed with pandas. A JSON array of records is also accepted |
| Licence | Assumed CC BY 4.0 under the World Bank dataset terms. **PIP-specific terms are UNVERIFIED** (the licence string says so) |
| Citation | "World Bank, Poverty and Inequality Platform (version `<version>`)" |
| Vintage | `Provenance.vintage = release_version` (`YYYYMMDD`). The full version id (e.g. `20250930_2021_01_02_PROD`) is in `meta["version"]` and the provenance notes |

## Endpoints and parameters

| Call | Parameters used |
|---|---|
| `/pip` | `country` (ISO3 comma list or `all`), `year` (comma list, `all`, `MRV`), `povline` (0–2700, at most 3 decimals), `fill_gaps` (`true`/`false`), `welfare_type` (`all`/`income`/`consumption`), `reporting_level` (`all`/`national`/`urban`/`rural`), `ppp_version` (YYYY), `release_version` (YYYYMMDD), `version` (`YYYYMMDD_PPP_XX_YY_IDENTITY`), `nowcast` (only when true; requires `fill_gaps`), `format=csv` |
| `/pip-grp` | `country` (region codes or `all`), `year`, `povline`, `group_by=wb`, `ppp_version`, `release_version`, `version`, `format=csv` |
| `/versions` | `format=csv`. Columns `version`, `release_version`, `ppp_version` |
| Also exist, not wrapped | `/aux?table=...`, `/citation`, `/pip-info`, `/health-check`, `/valid-params`, `/cp-download` |

**Release pinning.** With `release_version=None`, the connector reads `/versions`, takes the latest release for the requested `ppp_version`, and sends `ppp_version`, `release_version` and `version` explicitly, as OWID's production ETL does. With an explicit `release_version` that `/versions` does not list for that PPP round, the call fails. If `/versions` is unreachable, an explicit release is sent without the version id (with a note); with no release given, the call raises a clear error.

**`/pip` columns (44):** `region_name, region_code, country_name, country_code, reporting_year, reporting_level, survey_acronym, survey_coverage, survey_year, welfare_type, survey_comparability, comparable_spell, poverty_line, headcount, poverty_gap, poverty_severity, watts, mean, median, mld, gini, polarization, decile1..decile10, cpi, ppp, reporting_pop, reporting_gdp, reporting_pce, is_interpolated, distribution_type, estimation_type, spl, spr, pg, estimate_type`.

## Mapping to the canonical frame

| Canonical | From |
|---|---|
| `source` / `dataset` | `"pip"` / `"PIP_PPP<ppp_version>"`. Keeping 2017- and 2021-PPP results in separate datasets means they never collide on the canonical key |
| `indicator` | Measure name. Line-dependent measures get `@<line>` (`headcount@3.00`, `poverty_gap@3.00`, `poverty_severity@3.00`, `watts@3.00`). Non-national reporting levels get `\|urban`/`\|rural`. `\|income`/`\|consumption` is added **only** where a country-year has both welfare types (listed in `meta["welfare_duplicates"]`, caveat `pip_welfare_duplicates`) |
| `iso3` / `country` | `country_code` / `country_name`. For `/pip-grp`: `region_code` / `region_name`, listed in `meta["aggregates"]` |
| `period` | `reporting_year`. The fractional `survey_year` is kept in `meta["survey_info"]` |
| `value` | `headcount`, `poverty_gap`, `poverty_severity`, `spr` and the decile shares ×100 (percent). `gini` ×100 (a 0–100 index, the WDI convention). `mean`, `median`, `spl` in PPP $/day. `watts`, `mld`, `polarization`, `pg` as given. Guard: if any `headcount` is above 1, nothing is rescaled and `pip_scale_assumption` (warn) is raised |
| `unit` | States the line and the PPP round, e.g. `"% of population below $3.00/day (PPP 2021)"` and `"2021 international $ per person per day (PPP 2021)"`. `schema.ppp_base()` returns `"2021"` |
| `obs_status` | `estimate_type` containing "projection" → forecast, "nowcast" → modelled. `is_interpolated` TRUE, or `estimation_type` interpolation/extrapolation → imputed |

With `fill_gaps=True` and `nowcast=False`, rows whose `estimate_type` contains "nowcast" are dropped, as pipr does. They are counted in `meta["dropped_nowcast_rows"]`.

`meta["survey_info"]` holds one record per country-year-series: `welfare_type, reporting_level, survey_acronym, survey_coverage, survey_year, survey_comparability, comparable_spell, distribution_type, estimation_type, estimate_type, is_interpolated, cpi, ppp, reporting_pop, reporting_gdp, reporting_pce`.

## Standard poverty lines

| PPP round | International line | LMIC | UMIC |
|---|---|---|---|
| 2021 | 3.00 | 4.20 | 8.30 |
| 2017 | 2.15 | 3.65 | 6.85 |
| 2011 | 1.90 | 3.20 | 5.50 |

`povline=None` uses the international line of the round. A line that is not standard for the requested round raises `pip_line_ppp_mismatch` (warn), naming the round it belongs to. For example, $2.15 at 2021 PPPs is flagged as a 2017-PPP line.

## How this was verified

The data host is blocked in the build environment, so nothing was called live. Sources read on 2026-10-11:

| Label | Source | What it established |
|---|---|---|
| P | World Bank [pipr](https://github.com/worldbank/pipr) 1.5.0 ([data-raw/data.R](https://raw.githubusercontent.com/worldbank/pipr/HEAD/data-raw/data.R), [R/build_request.R](https://raw.githubusercontent.com/worldbank/pipr/HEAD/R/build_request.R), [R/get_stats.R](https://raw.githubusercontent.com/worldbank/pipr/HEAD/R/get_stats.R), [R/utils.R](https://raw.githubusercontent.com/worldbank/pipr/HEAD/R/utils.R), [R/other.R](https://raw.githubusercontent.com/worldbank/pipr/HEAD/R/other.R), [R/get_aux.R](https://raw.githubusercontent.com/worldbank/pipr/HEAD/R/get_aux.R), [vignette](https://raw.githubusercontent.com/worldbank/pipr/HEAD/vignettes/articles/get-stats-example.Rmd)) | `prod_url = https://api.worldbank.org/pip`, then `/v1/<endpoint>`. Comma lists (`.multi = "comma"`). Endpoints `pip`, `pip-grp` (`group_by` wb/none), `versions`, `pip-info`, `health-check`, `aux`. Parameters and local validation: `release_version` YYYYMMDD, `ppp_version` one 4-digit year, `version` `^[0-9]{8}_[0-9]{4}_[0-9]{2}_[0-9]{2}_[A-Z]+$` (example `20260324_2021_01_02_PROD`), `welfare_type`, `reporting_level`, `nowcast`. Nowcast rows are filtered client-side when not requested. 429 handling. The vignette says "44 columns" and documents `is_interpolated` and `estimate_type` |
| P | CRAN [worldbank](https://github.com/m-muecke/worldbank) R client ([R/pip.R](https://raw.githubusercontent.com/m-muecke/worldbank/HEAD/R/pip.R), [tests/testthat/test-pip.R](https://raw.githubusercontent.com/m-muecke/worldbank/HEAD/tests/testthat/test-pip.R)) | Base `https://api.worldbank.org/pip/v1` with `format=csv`. `povline` between 0 and 2700 with at most 3 decimals. Default line = the round's international line ($3 at 2021, $2.15 at 2017). `nowcast` requires `fill_gaps`. `year` lists such as `year=2019,2020`. `/citation`, `/valid-params`, `/cp-download` |
| T | Our World in Data production ETL ([snapshots/wb/2026-09-22/world_bank_pip.py](https://raw.githubusercontent.com/owid/etl/HEAD/snapshots/wb/2026-09-22/world_bank_pip.py)) | `/versions?format=csv` with columns `ppp_version, release_version, version`, choosing the max release per PPP round. `/pip?...&ppp_version=&version=&release_version=&format=csv`. `/pip-grp?group_by=wb...`. **`headcount` is a 0–1 fraction** ("if any headcount is below 0.99"). Lines: 2017 = 2.15/3.65/6.85, 2021 = 3.00/4.20/8.30. Columns `country_name`, `reporting_year`, `region_code`, `region_name`, `poverty_line` |
| T | OWID notebook [PIP structure and API](https://raw.githubusercontent.com/owid/notebooks/HEAD/MichalisMoatsos/PIP_Structure_and_how_the_API_works.Rmd) | Column definitions (headcount, poverty_gap, poverty_severity, watts, mean, median, mld, gini, polarization, deciles, survey_comparability, comparable_spell, is_interpolated, distribution_type, estimation_type, cpi, ppp, reporting_pop). Fractional `survey_year`. CHN/IND/IDN have urban, rural and national rows. Some country-years have both income and consumption surveys |
| T | Header of the `/pip` CSV in community test code (y-magma/weco `webapp/test/infrastructure/worldBankPipClient.spec.ts`, values synthetic) | The exact order of the 44 columns, including `spl, spr, pg, estimate_type` |

P = code by the provider. T = a third party.

### UNVERIFIED (each has a live test in `tests/live/test_pip_live.py` where possible)

| Assumption | Where in the code | Live test |
|---|---|---|
| `gini` and decile shares are 0–1 fractions, like `headcount` | `MEASURES[...].fraction` | `test_pip_csv_has_documented_columns` |
| The 44-column set is current for `/pip` CSV | `INFO_COLUMNS`, `MEASURES` | `test_pip_csv_has_documented_columns` |
| `/pip-grp` columns beyond `region_name, region_code, reporting_year, poverty_line, headcount` (the fixture also has `poverty_gap, poverty_severity, watts, mean, pop_in_poverty`). Measures absent from the payload are skipped | `parse_rows(aggregate=True)` | `test_world_aggregate` |
| 2017-PPP estimates are still served alongside 2021 | — | `test_kenya_2017_ppp_still_served`, `test_versions_table` |
| `estimate_type` value "projection" (only "nowcast" is confirmed by pipr). Values of `estimation_type` (interpolation/extrapolation/survey) and `distribution_type` (micro/group/aggregate/imputed) | `_obs_status`, caveats | `test_fill_gaps_marks_interpolated_years` |
| PIP licence (CC BY 4.0 assumed) | `PIP.license` | none |

## Caveats raised

| Code | Severity | When |
|---|---|---|
| `pip_ppp_base` | info | Always: values are on one PPP round |
| `pip_line_ppp_mismatch` | warn | The line is not standard for the requested PPP round |
| `pip_mixed_ppp` | **block** | `poverty_mix_caveats` finds more than one PPP base among PIP rows |
| `pip_mixed_lines` | warn | More than one poverty line in one result or combined results |
| `pip_interpolated` | warn (fill_gaps) / info | Imputed years present |
| `pip_nowcast` | warn | Nowcast or projection rows present |
| `pip_welfare_duplicates` | warn | A country-year has both income and consumption estimates |
| `pip_welfare_mix` | info | A country series mixes income and consumption over time |
| `pip_comparability_break` | info | `survey_comparability` changes within a series |
| `pip_distribution_type` | info | Grouped, aggregated or imputed distributions |
| `pip_scale_assumption` | warn | Values were already in percent, so nothing was rescaled |
| `pip_inconsistent_repeats` | warn | With several lines, a line-independent measure (Gini, mean, deciles) differed between requests. The first value is kept. Normally such measures are de-duplicated silently |
| `pip_empty` | warn | No rows |

## Replication note

The Kenya CGJR's Fig 29 (growth elasticity of poverty) uses **national-line** poverty rates from the 2023 Kenya Poverty Assessment, so PIP cannot reproduce it exactly. P01-type charts (headcount, Gini) use PIP. Earlier reports used $2.15 at 2017 PPPs, so set `ppp_version=2017, povline=2.15` and pin `release_version` to match them.

## Fixtures (all synthetic)

`tests/fixtures/pip_*_synthetic.csv` use the documented column layout with invented round values (headcount 0.5/0.4/0.3, deciles summing to 1). Release dates are in 2099 so they are obviously not real.
