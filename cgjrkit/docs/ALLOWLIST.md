# Network allowlist

This page lists every host that cgjrkit calls, so the hosts can be allowed in a restricted network: a Claude Code cloud environment, a corporate proxy, or a Bank IT review.
The single source is [`src/cgjrkit/registries/hosts.yaml`](../src/cgjrkit/registries/hosts.yaml). `tests/test_registries.py` fails if a connector declares a host that file does not list, and `registries.allowlist()` returns the list from code.

**Status (2026-10-11).** None of the data hosts could be reached from the build environment (the proxy returned 403 or DNS failed). **No API has been called live yet.** Request and response formats were checked against official documentation, the providers' own client code and recorded responses in maintained clients (sources in `docs/apis/<provider>.md`). Every assumption that could not be checked has a live test in `tests/live/`, and those tests run once the hosts are allowed.

## How to allow the hosts in a Claude Code cloud environment

1. Open the cloud environment menu in the session's title bar and choose **Edit**.
2. Under **Network access**, choose **Limited** and add the hosts below under **Allowed domains**, one per line. Older app versions show this list under **Custom**.
3. Leave **Allow package managers** ticked (in older versions, the box that includes the default list of package managers). `uv` and `pip` need PyPI (`pypi.org`, `files.pythonhosted.org`) to install dependencies.
4. Save the environment.

Steps and screenshots: <https://code.claude.com/docs/en/cloud-environments#network-access>.

If a host is still blocked, cgjrkit raises `NetworkBlocked` with the host name in the message, for example `cannot reach api.worldbank.org (ProxyError) ...`.

### Copy-paste: hosts the connectors call (17)

```
api.worldbank.org
data360api.worldbank.org
api.imf.org
www.imf.org
rplumber.ilo.org
sdmx.ilo.org
population.un.org
dataverse.nl
www.rug.nl
www.bruegel.org
unctadstat-api.unctad.org
unctadstat-user-api.unctad.org
www.cepii.fr
atlas.hks.harvard.edu
sdmx.oecd.org
api.openalex.org
api.crossref.org
```

### Copy-paste: live link checks and reserved hosts (19)

These hosts are not called by any connector. `tests/live/test_handles_live.py` uses the four link-check hosts (first group). `tests/live/test_registries_live.py` checks that every declared host answers, so it needs all of them.

```
openknowledge.worldbank.org
hdl.handle.net
doi.org
www.nber.org
wits.worldbank.org
search.worldbank.org
ilostat.ilo.org
comtradeapi.un.org
unctadstat.unctad.org
stat.unido.org
i-tip.wto.org
www.enterprisesurveys.org
www.knbs.or.ke
www.treasury.go.ke
www.centralbank.go.ke
psa.gov.ph
www.bps.go.id
www.stat.gov.mk
www.hcp.ma
```

## Hosts by provider

"API format verified" describes how the URLs and response layout the code relies on were checked. Nothing has been confirmed by a live call yet. Rate limits are the per-host defaults in `hosts.yaml`. Only the OECD connector enforces its limit (a persistent hourly budget) and the Atlas connector its own (120 requests per minute); core HTTP has no per-host limiter yet.

### World Bank

| Host | Used by | Purpose | Auth / key | API format verified | Rate limit |
|---|---|---|---|---|---|
| `api.worldbank.org` | `worldbank`, `pip` | Indicators API v2 (WDI, WGI, GFDD; WDI Database Archives = source 57); Poverty and Inequality Platform API under `/pip/v1` | none | **Yes**, from documentation: the official API docs (Data Help Desk articles via their published mirror), wbgapi/wbdata source, and pipr (the World Bank's PIP client). Unverified: GFDD source id 32, the archive path form, archive version listing, obs_status letters other than F, PIP Gini/decile scaling | 30 / 60 s (default) |
| `data360api.worldbank.org` | `data360` | Data360 API: Enterprise Surveys (WB_ES), LPI 2.0, metadata, search | none | **Mostly**: the official OpenAPI spec and the official data360-mcp client. Unverified: the `/data360/codelist` endpoint (MCP client only), the metadata POST body form, how WB_ES carries standard errors and sample sizes, and WB_LPI_20 indicator ids | 30 / 60 s (default) |
| `openknowledge.worldbank.org` | live link checks | OKR handle landing pages (`/handle/10986/<n>`) | none | Route confirmed in DSpace source; live check pending | 30 / 60 s |
| `wits.worldbank.org`, `search.worldbank.org` | none yet | Planned: WITS/TRAINS tariffs; Documents & Reports API (after legal sign-off) | none | n/a | 30 / 60 s |

### IMF

| Host | Used by | Purpose | Auth / key | API format verified | Rate limit |
|---|---|---|---|---|---|
| `api.imf.org` | `imf` | SDMX 3.0 API: WEO and WEO vintage dataflows, BOP and other flows | none | **Mostly**: the imfp client source and recorded live responses in its test suite, plus imf-reader. Unverified: the SDMX-CSV column order with `attributes=series`, `LATEST_ACTUAL_ANNUAL_DATA` in full data calls, the `PUBLICATION_DATE` format, quarterly/monthly time filters | 40 / 60 s (client practice) |
| `www.imf.org` | `imf` | DataMapper API (latest-WEO fallback) | none | **Yes** for single-country paths (third-party clients); multi-country paths unverified | 30 / 60 s |

IMF terms require written permission for systematic downloading and commercial reuse. A request to copyright@imf.org is pending, and every IMF result carries an `IMF_TERMS` caveat.

### ILO

| Host | Used by | Purpose | Auth / key | API format verified | Rate limit |
|---|---|---|---|---|---|
| `rplumber.ilo.org` | `ilostat` | ILOSTAT bulk/data service (CSV per indicator, table of contents, code lists); default route | none | **Yes**: Rilostat (the ILO's own R client) plus third-party live checks (2026-10-06). Unverified: whether the literal `+` separator is required, whether server-side filters are honoured, TOC column names | 30 / 60 s |
| `sdmx.ilo.org` | `ilostat` | ILOSTAT SDMX REST (optional route) | none | **Partly**: the URL pattern is documented; the key layout and SDMX-CSV column names are unverified | 30 / 60 s |
| `ilostat.ilo.org` | none yet | ILOSTAT website (reserved) | none | n/a | 30 / 60 s |

### United Nations

| Host | Used by | Purpose | Auth / key | API format verified | Rate limit |
|---|---|---|---|---|---|
| `population.un.org` | `wpp` | World Population Prospects 2024 bulk CSV files; Data Portal API (optional) | none for bulk files; `CGJRKIT_WPP_KEY` (Bearer token) for the Data Portal API | **Yes** for the bulk CSVs (third-party live checks, file sizes, column headers). Unverified: the 1 July five-year age file name (falls back to 1 January), the OtherVariants file name, Data Portal JSON fields and units | 30 / 60 s |
| `unctadstat-api.unctad.org` | `unctad` | UNCTADstat keyless report metadata and 7z bulk download (FDI flows and stocks) | none | **Yes**: recorded third-party responses (metadata JSON, bulk CSV layout). Unverified: the versioned bulk path; measures 5025/5026/0110 are absent from the 2025-08 metadata | 30 / 60 s |
| `unctadstat-user-api.unctad.org` | `unctad` (`route='api'`) | UNCTADstat keyed Facts API | `CGJRKIT_UNCTAD_KEY` + `CGJRKIT_UNCTAD_CLIENT_ID` | **Partly**: from third-party code. The provider expects a form-encoded POST body, which `core.http.fetch` cannot send yet; the query-parameter form used instead is unverified | 30 / 60 s |
| `comtradeapi.un.org`, `unctadstat.unctad.org`, `stat.unido.org` | none yet | Planned: UN Comtrade (key `CGJRKIT_COMTRADE_KEY`), UNCTADstat website, UNIDO | see `hosts.yaml` | n/a | see `hosts.yaml` |

### Files: Penn World Table and Bruegel

| Host | Used by | Purpose | Auth / key | API format verified | Rate limit |
|---|---|---|---|---|---|
| `dataverse.nl` | `pwt` | DataverseNL Data Access API: PWT 10.01 and 11.0 files, dataset JSON | none | **Yes**: the official Dataverse API guides. The pinned file ids (354095/354098, 554105/554030) come from third-party code | 30 / 60 s |
| `www.rug.nl` | `pwt` | Legacy PWT 10.0 direct download only (10.01 and 11.0 are not served there) | none | **Partly**: third-party reports; live test pending | 30 / 60 s |
| `www.bruegel.org` | `bruegel` | Real effective exchange rate database (dataset page, then a zip holding an .xls) | none | **Partly**: the URL pattern and workbook layout come from several third-party scripts. The fallback URL (`ver16Feb2026`) is probably stale; set `CGJRKIT_BRUEGEL_URL` to override it | 30 / 60 s |

### Trade and policy

| Host | Used by | Purpose | Auth / key | API format verified | Rate limit |
|---|---|---|---|---|---|
| `www.cepii.fr` | `baci` | BACI bilateral trade zip archives (HS92 version 202601 is 2.4 GB) | none | **Yes**: the provider Readme and third-party downloads. Unverified: that layouts stay the same across releases | 30 / 60 s |
| `atlas.hks.harvard.edu` | `atlas` | Atlas of Economic Complexity GraphQL API (ECI) | none (courtesy access) | **Yes**: the Growth Lab's official api-docs. Unverified: an ECI-rank field (cgjrkit computes the rank) | 120 / 60 s (published) |
| `sdmx.oecd.org` | `oecd` | OECD SDMX: PMR, PMR (OECD-WBG), STRI, FDI Restrictiveness | none | **Partly**: dataflow ids and agencies come from third-party code; DSD dimension order is unverified for PMR and STRI | 20 / 3600 s (published) |
| `i-tip.wto.org`, `www.enterprisesurveys.org` | none yet | Planned: WTO I-TIP; Enterprise Surveys website | none | n/a | 30 / 60 s |

### Literature

| Host | Used by | Purpose | Auth / key | API format verified | Rate limit |
|---|---|---|---|---|---|
| `api.openalex.org` | `openalex` | Works metadata (DOI lookup, search) | optional `CGJRKIT_OPENALEX_KEY`, sent as a Bearer header | **Yes**: official docs (their GitHub source). Unverified: whether the Bearer header form counts toward the key's budget (the documented form is `?api_key=`) | 100 / 1 s (published) |
| `api.crossref.org` | `crossref` | DOI metadata, bibliographic search | none; `CGJRKIT_CONTACT_EMAIL` joins the polite pool (mailto) | **Yes**: the official REST API docs | 10 / 1 s (published) |
| `doi.org`, `hdl.handle.net`, `www.nber.org` | live link checks | DOI and handle resolution; `www.nber.org` is a redirect target | none | Link formats documented; live checks pending | 30 / 60 s; 10 / 60 s |

### National statistics offices, ministries and central banks (reserved)

`www.knbs.or.ke`, `www.treasury.go.ke`, `www.centralbank.go.ke`, `psa.gov.ph`, `www.bps.go.id`, `www.stat.gov.mk`, `www.hcp.ma`: reserved for a planned national-table ingest tool and checked only by `tests/live/test_registries_live.py`. Hostnames marked `unverified` in `hosts.yaml` (`www.stat.gov.mk`, `www.centralbank.go.ke`) are confirmed by that test.

### Excluded

`api.stlouisfed.org` (FRED) and `api.semanticscholar.org` are declared as `excluded` in `hosts.yaml` because of their licence terms. No code calls them; do not allow them for cgjrkit.

## Running the live tests

The live tests are skipped unless both conditions hold: `pyproject.toml` deselects them by default (`-m 'not live'`), and `tests/conftest.py` skips them without `CGJRKIT_LIVE=1`.

```
CGJRKIT_LIVE=1 .venv/bin/pytest -m live                                  # everything
CGJRKIT_LIVE=1 .venv/bin/pytest -m live tests/live/test_worldbank_live.py  # one provider
```

| Variable | Needed for |
|---|---|
| `CGJRKIT_WPP_KEY` | WPP Data Portal API test (bulk files need no key) |
| `CGJRKIT_UNCTAD_KEY`, `CGJRKIT_UNCTAD_CLIENT_ID` | UNCTADstat keyed API test |
| `CGJRKIT_OPENALEX_KEY` | OpenAlex Bearer-key test (other OpenAlex tests run without it) |
| `CGJRKIT_CONTACT_EMAIL` | Crossref polite pool (optional) |
| `CGJRKIT_BRUEGEL_URL` | Override the Bruegel REER zip URL (optional) |

Licences and terms per source are in `src/cgjrkit/registries/licences.yaml`. The caveat engine (`cgjrkit.qa`) warns or blocks on restricted sources.
