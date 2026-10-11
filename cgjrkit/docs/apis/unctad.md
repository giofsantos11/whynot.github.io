# UNCTADstat FDI (`cgjrkit.connectors.unctad`)

Connector: `UNCTAD` in `src/cgjrkit/connectors/unctad.py`.

- `fdi(countries=None, start=None, end=None, measures=("usd", "pct_world", "pct_gdp"), series=None, *, route="bulk", version=None, with_metadata=True)`: FDI inflows, outflows, inward and outward stock (`US.FdiFlowsStock`). `fetch` is an alias.
- `metadata(report)`: release version, last update, measures and dimensions of a report.
- Pure: `build_metadata_request`, `build_bulk_request`, `build_api_request`, `parse_metadata`, `read_archive`, `parse_bulk_csv`, `parse_facts_csv`, `to_canonical`, `to_m49`.

| Item | Value |
|---|---|
| Hosts (`domains`) | `unctadstat-api.unctad.org` (metadata, bulk), `unctadstat-user-api.unctad.org` (keyed data API) |
| Metadata | `GET https://unctadstat-api.unctad.org/api/reportMetadata/US.FdiFlowsStock/en` (keyless JSON) |
| Bulk (default route) | `GET https://unctadstat-api.unctad.org/bulkdownload/US.FdiFlowsStock/US_FdiFlowsStock` (keyless). Returns a **7z** archive with one CSV; needs `py7zr`. Pinned release: `.../bulkdownload/US.FdiFlowsStock/{version}/US_FdiFlowsStock` (UNVERIFIED for this report) |
| Data API (`route="api"`) | `POST https://unctadstat-user-api.unctad.org/US.FdiFlowsStock/cur/Facts`, headers `ClientId`/`ClientSecret`, fields `$select`, `$filter`, `$format=csv` |
| Auth | Bulk and metadata: none. API: free Data Centre credentials in `CGJRKIT_UNCTAD_CLIENT_ID` and `CGJRKIT_UNCTAD_KEY` (secret); a clear error if missing. Credentials go in headers and never into the cache key or provenance |
| Rate limits | Not published. The API refuses queries estimated above 62,500 cells (HTTP 400 "maximal size") and emits transient 401s. The bulk route needs one download per release |
| Licence | CC BY 3.0 IGO; reuse with attribution to UNCTADstat |
| Citation | "UNCTADstat, Foreign direct investment: inward and outward flows and stock (US.FdiFlowsStock)" |
| Vintage | From the metadata: `"UNCTADstat US.FdiFlowsStock v<version> (<lastUpdated date>)"`, e.g. v1912 of 2025-08-20. Without metadata: the `_ST<yyyymm>` stamp of the bulk member name if present, else empty plus the caveat `unctad_vintage_unknown`. With a pinned bulk release (`version=`): the pinned release, `"UNCTADstat US.FdiFlowsStock v<version> (pinned bulk release[, ST<yyyymm>])"`, never the current metadata version. The current-release metadata record then goes to `meta["current_release_provenance"]` (and `meta["release"]`, plus a note on the data provenance) instead of `Result.provenance`, so source lines cite only the pinned release |

## Report structure (`US.FdiFlowsStock`)

| Dimension | Codes |
|---|---|
| `Economy` | UN M49: 3 digits for economies (`404` Kenya, `807` North Macedonia), 4 digits for groups (`0000` World) |
| `Year` | numeric |
| `Direction` | `1` Inward, `2` Outward |
| `Flow` (axis "Modes") | `08` Flow, `09` Stock |

Measures:

| Code | Label | Used as |
|---|---|---|
| `0100` | US$ at current prices (base, magnitude 1) | ÷10⁶ → `USD_MN` |
| `0102`, `0103`, `0106`, `0109` | Same, in hundreds/thousands/millions/billions | `0106` → `USD_MN` |
| `5011` | Percentage of total world | `PCT_WORLD` |
| `5025` | Percentage of GDP (listed by a third party; **absent** from the 2025-08 metadata) | `PCT_GDP` |
| `5026` | Percentage of GFCF (same status) | `PCT_GFCF` |
| `0110` | US$ per capita (same status) | `USD_PC` |

A requested measure that the release lacks raises `unctad_measure_missing`. For X04 charts, divide `FDI_INFLOWS_USD_MN` by WDI GDP (current US$) and footnote the method.

**Bulk CSV** columns: `Year, Economy, Economy Label, Direction, Direction Label, Flow, Flow Label`, then per measure a value column plus `<label> Footnote` and `<label> Missing value`. Value columns are recognised by label: `US$ at current prices in millions` and `Millions of US$ at current prices` (both seen) → 0106, `Percentage of total world` → 5011, and so on.

**Facts CSV** columns (API): `Economy_Code, Economy_Label, ..., Year, Direction_Code, Direction_Label, ..., Flow_Code, Flow_Label, Flow_Note, ..., M<code>_Value, M<code>_Footnote_Code, M<code>_Footnote_Text, M<code>_MissingValue_Code, M<code>_MissingValue_Label`. Notes can contain line breaks inside quotes. `M0100_Value` is in US dollars.

## Mapping to the canonical frame

| Canonical | From |
|---|---|
| `source` / `dataset` | `"unctad"` / `"UNCTADstat FDI"` |
| `indicator` | `{FDI_INFLOWS, FDI_OUTFLOWS, FDI_INWARD_STOCK, FDI_OUTWARD_STOCK}_{USD_MN, PCT_WORLD, PCT_GDP, PCT_GFCF, USD_PC}` |
| `iso3` / `country` | M49 → ISO3 (embedded table from ISO 3166-1 numeric, plus CSK, DDR, ANT, YMD, SUN, YUG=890, SCG=891, XKX=412). Groups keep the provider code (`0000`), listed in `meta["aggregates"]` with labels. Unmapped economy codes (e.g. `736` Sudan before 2011) keep the code and raise a caveat |
| `period` | `Year` |
| `value` / `unit` | `current US$ millions`, `% of world total`, `% of GDP`, `% of gross fixed capital formation`, `current US$ per capita`. Rows with missing values are dropped and counted |
| `obs_status` | `estimate` when the footnote text mentions an estimate (heuristic); footnotes are kept in `meta["footnotes"]` |

## How this was verified

The UNCTAD hosts are blocked in the build environment (proxy 403). Sources read on 2026-10-11:

| Label | Source | What it established |
|---|---|---|
| T (recorded responses) | [DMSILVA87/bda-indice `Calisto Ebo/_cache/unctad_meta_US.FdiFlowsStock.json`, `unctad_data_US.FdiFlowsStock.csv`, `fetch_angola_data.py`](https://github.com/DMSILVA87/bda-indice) | The metadata JSON for this report: version 1912, lastUpdated 2025-08-20, measures 5011 and 0100 (+0102/0103/0106/0109), axes Economy / Year / Direction / Flow (Modes). A real Facts CSV (Angola) with the column layout above, codes `08`/`09` and `1`/`2`, and M0100 in US$. The keyless web-app route `POST https://unctadstat-api.unctad.org/datamart-api/{report}/cur/Facts` (form-urlencoded) and `api/datacenter/reports/{report}/info/en` |
| T | [elkassabgi/econdatalibrary `jobs/ingest_unctad_ds.py`, `updater/strategies/fetchers/_unctad.py`, runbook](https://github.com/elkassabgi/econdatalibrary) (contract "proven live 2026-08-07") | Keyless `reportMetadata` gives `{version, lastUpdated}`. Keyed `POST unctadstat-user-api.unctad.org/{DS}/cur/Facts?culture=en` with `ClientId`/`ClientSecret` headers and `$select/$filter/$format=csv` form fields. 62,500-cell cap. Transient 401s. Licence CC BY 3.0 IGO |
| T | [dhopp1/statschat `helper/tools.py`, `metadata/unctadstat_key.csv`](https://github.com/dhopp1/statschat) | `requests.post(url, headers={clientid, clientsecret}, data={$filter, $select, $format})`. Filter syntax `Year in (...)`, `Economy/Code in ('...')`. The measure list for US.FdiFlowsStock includes M0110, M5025, M5026 (possibly an older release) |
| T | [ianomad/money `scripts/build_economy.py`](https://github.com/ianomad/money) (2026) | Bulk URL. 7z read with `py7zr`. Columns `Year, Economy, Economy Label, Direction Label, Flow Label, Millions of US$ at current prices (+ Footnote)`. 3-digit codes = economies, 4-digit = groups, `0000` World |
| T | [AlexNSV/network_hierarchy `data_handling/fdi.py`](https://github.com/AlexNSV/network_hierarchy), [MilenioICLAC/iclac-mapa-fdi `data/external/README.md`](https://github.com/MilenioICLAC/iclac-mapa-fdi) | Column `US$ at current prices in millions` (older bulk file). `Flow=09 Stock`, `Direction=1 Inward`, M49 codes, bulk = 7z with a CSV inside, keyless, refreshed yearly after the WIR (June) |
| T | DeckardShaw31/Eureka-2026 `src/download_unctad.py` | Versioned bulk path form `bulkdownload/US.LSCI/10012/US_LSCI` (other report) |
| P (snippet) | [UNCTADstat FAQ](https://unctadstat.unctad.org/EN/FAQ.html), via search snippet | CC BY 3.0 IGO; reuse and redistribution allowed when UNCTADstat is cited; free, no registration |

### UNVERIFIED (live tests in `tests/live/test_unctad_live.py`)

| Assumption | Where | Live test |
|---|---|---|
| The bulk file is still 7z with the documented columns | `read_archive`, `parse_bulk_csv` | `test_bulk_columns_and_kenya` |
| `5025` (% of GDP) is absent from the current release | `MEASURES`, caveat | `test_pct_gdp_measure_matches_caveat` (accepts either state and checks the caveat logic) |
| The versioned bulk path works for this report | `build_bulk_request(version=...)` | `test_versioned_bulk_url` |
| The API accepts the OData fields as query parameters on a POST (the provider expects a form body) | `UNCTAD.fdi(route="api")` | `test_api_route_matches_bulk` (needs credentials) |
| Footnote text marks estimates | `to_canonical` | none (heuristic) |

## Caveats raised

| Code | Severity | When |
|---|---|---|
| `unctad_directional` | info | Always: directional principle; differs from BPM6 asset/liability data and WDI net inflows |
| `unctad_revisions` | info | Always: each WIR revises history; pin a snapshot |
| `unctad_measure_missing` | warn | A requested measure is not in the release (e.g. % of GDP) |
| `unctad_negative_flows` | info | Negative flows present |
| `unctad_unmapped_economies` | warn | Economy codes without ISO3 |
| `unctad_vintage_unknown` | warn | Metadata could not be read |
| `unctad_version_mismatch` | warn | A pinned bulk version differs from the current metadata version (the data vintage names the pinned release) |
| `unctad_empty` | warn | No rows |

## Replication notes

- North Macedonia Fig 37 (X04, FDI inflows % of GDP vs the UMIC median/IQR, 1990–2024): `fdi(series=["FDI_INFLOWS"], measures=["usd"])` for all economies, divided by WDI GDP. UMIC membership comes from the World Bank connector.
- FDI inward stock % of GDP (cross-report item 41): `FDI_INWARD_STOCK_USD_MN` ÷ GDP.
- Kenya Fig 23 uses WDI net inflows (BoP, asset/liability basis), not UNCTAD. Expect differences.

## Dependency and core change requests

- `py7zr` (1.1.3, LGPL-2.1+) was installed into the venv to read the 7z bulk file. It is imported lazily; without it the bulk route raises a clear error. Please add it to `pyproject.toml` (optional extra, e.g. `unctad = ["py7zr>=1.0"]`).
- `core.http.fetch` can send only query parameters or a JSON body. The UNCTAD data API expects a form-encoded body (`data=`). A `form_body` parameter, included in the cache key, would make `route="api"` follow the documented contract.

## Fixtures (all synthetic)

| File | Format | Content |
|---|---|---|
| `unctad_fdi_metadata_synthetic.json` | `reportMetadata` JSON | version 9999, lastUpdated 2099-01-01, measures 5011/0100/0106 |
| `unctad_fdi_facts_synthetic.csv` | Facts CSV (full recorded column set) | KEN, UGA, `736`, World; years 2097–2098; multi-line notes; one footnoted estimate; one negative inflow; missing values for `736` in 2098 |
| `unctad_fdi_bulk_synthetic.csv` / `.7z` | bulk CSV, plain and 7z (member `US_FdiFlowsStock_ST209901.csv`) | the same synthetic numbers, in millions |
