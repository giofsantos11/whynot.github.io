# cgjrkit status (2026-10-11)

This is a preview of the public-data layer. **No API has been called live yet**: every provider host is blocked in the build environment. All counts below come from `pytest` runs on 2026-10-11.

## Test summary

```
pytest -q                837 passed, 151 deselected in 40.85s   (also clean with -W error)
pytest -q -m live --co   151 live tests in 19 files (they skip here)
```

## What was built

| Layer | Contents | Offline tests |
|---|---|---|
| Data connectors | `worldbank` (incl. source 57 vintages), `data360`, `pip`, `imf`, `ilostat`, `wpp`, `pwt`, `bruegel`, `unctad`, `baci`, `atlas`, `oecd` | 358 |
| Literature connectors | `openalex`, `crossref`, `handles` | 90 |
| Methods | `harmonize`, `peers`, `benchmark`, `growth_accounting`, `structural_change`, `jobs_shapley`, `standard_tables` (27 entries in `METHODS`), plus `registries` | 165 + 11 |
| Charts | `series`, `benchmark`, `decomposition`, `render` (Vega-Lite to SVG/PNG, footer built from provenance) | 57 |
| QA | `qa.caveat_check` (BLOCK/WARN/PASS plus footnotes) | 83 |
| MCP preview | `cgjrkit-mcp` (9 tools), `CONNECTORS`/`METHODS` registries | 21 + 5 |
| Core, review probes | core regressions; fix-round probes (no xfails left) | 8 + 39 |

**MCP smoke test (this run).** The in-process `mcp.Client` and the stdio entry point (`--offline`) each list all 9 tools. `list_connectors` returns 14 connectors. `peer_aggregate` on synthetic records gives mean 4.0 and median 2.5, with status WARN because the data were passed inline. Offline, `get_series` returns a `NetworkBlocked` ToolError, and a `url` argument is refused.

## What still needs live validation

Allow the hosts in [ALLOWLIST.md](ALLOWLIST.md): 17 connector hosts, plus 19 link-check hosts that only `test_registries_live.py` needs. Then run `CGJRKIT_LIVE=1 pytest -m live`. Both the variable and `-m live` are required.

| Connector or method | Live tests | Hosts |
|---|---|---|
| worldbank / pip | 13 / 6 | api.worldbank.org |
| data360 | 10 | data360api.worldbank.org |
| imf | 14 | api.imf.org, www.imf.org |
| ilostat | 10 | rplumber.ilo.org, sdmx.ilo.org |
| wpp | 7 | population.un.org (the API test also needs `CGJRKIT_WPP_KEY`) |
| pwt / bruegel | 7 / 4 | dataverse.nl, www.rug.nl / www.bruegel.org |
| unctad | 5 | unctadstat-api.unctad.org, unctadstat-user-api.unctad.org |
| baci / atlas / oecd | 2 / 3 / 5 | www.cepii.fr / atlas.hks.harvard.edu / sdmx.oecd.org |
| openalex / crossref / handles | 6 / 6 / 3 | api.openalex.org / api.crossref.org / link-check hosts |
| benchmark, growth_accounting, structural_change | 3 each | through WDI, PWT, ILOSTAT |
| registries | 41 | every declared host |

These include the Kenya replication checks for Figs 1, 4, 9, 11, 14, 15, 20 and 35.

## Unverified API assumptions (main ones)

Each is marked UNVERIFIED in the code and in `docs/apis/<provider>.md`. Most have a named live test.

- **World Bank:** source 32 is GFDD; the archive path and version listing; obs_status letters other than F; WGI codes on source 3.
- **Data360 / PIP:** how WB_ES exposes standard errors and sample sizes (otherwise an `es_se_missing` warn); the metadata POST body; `/data360/codelist`; PIP Gini and decile shares as fractions; the PIP licence.
- **IMF:** SDMX-CSV layout; LATEST_ACTUAL_ANNUAL_DATA in full calls; WEO fiscal-year labels; DataMapper paths with several countries; the BOP `A_T` code.
- **ILOSTAT / WPP:** the literal `+` separator and server-side filters; SDMX key layout; WPP file names; Data Portal fields, units and TLS.
- **PWT / Bruegel:** the pinned Dataverse file ids; the Bruegel link pattern and header layout.
- **Trade and policy:** missing UNCTAD % of GDP measures and the versioned bulk path; the Atlas ECI rank, which cgjrkit computes itself; OECD PMR/STRI dimension order.
- **Literature:** OpenAlex key sent as a Bearer header; Crossref `select` with a bibliographic query.
- **Methods:** PWT rtfpna as a Tornqvist residual; the definitions behind Kenya Figs 14, 15 and 35.

## Known issues

1. **Open core requests:** keep HTTP-200 error envelopes out of the cache; add headers to the cache key; a per-host rate limiter; a literal `+` in query strings; response headers; streaming large downloads (BACI 2.4 GB); form-encoded POST bodies; `FetchError.status_code`; secret parameters kept out of provenance.
2. **Missing vintages:** the APIs do not serve WEO releases before April 2025 or the ILOSTAT Nov 2023 edition. These need pinned snapshots or WDI source 57.
3. **Licences:** the IMF permission for systematic downloading is pending (results carry `IMF_TERMS`). The ILO, PIP, UNCTAD, OECD and Bruegel terms are still to confirm.
4. **Code health:**
   - Methods import private helpers from `harmonize`.
   - The chart palette fails the lightness and chroma checks.
   - Some counts in `docs/TOOLS.md` are stale (imf is listed as 73 tests but has 83).
   - The package docstring lists replication and workbench as layers, but both are empty.
   - `get_series` accepts `refresh=True`, which re-downloads BACI.
5. **MCP:** tested only with the SDK's own client. `render_chart` writes anywhere unless `--output-root` is set.

## Next steps (build-plan sprints)

The proprietary pieces of the plan were intentionally **not** built in this public repo: the replication runner (`replicate_figure`, FigureSpec, tolerances and the R0-R3 grader), `figure_inventory`/`figure_digitizer`, `source_resolver`, the frozen peer registries, the results ledger, `check_draft`, `export`, `prior_findings` and the workbench. `replication/` and `workbench/` are empty placeholders.

- **S1:** allowlist the hosts, run the live suite, settle the caching and rate-limit requests, and pin a first snapshot.
- **S2:** confirm the PWT and WDI checks (Figs 1, 4, 14, 15) and fix the palette.
- **S3:** run ILOSTAT, OECD and BACI live. WITS/TRAINS and `national_table_ingest` are not built yet.
- **S4:** IMF (Fig 9), Bruegel (Figs 11, 20), WPP, PIP, and the Enterprise Surveys standard errors.
