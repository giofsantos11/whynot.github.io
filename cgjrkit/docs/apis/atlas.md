# Atlas of Economic Complexity (`cgjrkit.connectors.atlas`)

Connector: `Atlas` in `src/cgjrkit/connectors/atlas.py`. **Cross-check only**: the toolkit computes ECI itself from BACI (methods layer). The Atlas is compared against it.

- `fetch(countries=None, start=2013, end=2023, *, product_class="HS92", indicators=("eci", "eci_rank"))`: ECI, computed ECI rank, and optionally `export_value` and `import_value`. `eci()` is a shortcut.
- Pure: `build_query`, `build_request`, `read_payload`, `parse`, `rank_eci`. `MinuteLimiter` is the rate limiter.

| Item | Value |
|---|---|
| Host (`domains`) | `atlas.hks.harvard.edu` |
| Endpoint | `POST https://atlas.hks.harvard.edu/api/graphql`, JSON body `{"query": "..."}` |
| Auth | None |
| Rate limit | **120 requests per minute**. Enforced in-process by a sliding-window limiter that sleeps; cached requests do not count. One request serves all countries for a year range |
| Licence / terms | Public access "as a courtesy". Meant for stand-alone economic analysis, "not to support other software applications". Endpoints and values "may change suddenly and without warning". Research and educational use; **commercial use unconfirmed** (ask growthlabtools@hks.harvard.edu). Every result carries the `atlas_licence` warning |
| Citation | Growth Lab at Harvard University, "The Atlas of Economic Complexity", atlas.hks.harvard.edu |
| Vintage | None exposed by the API. `vintage` stays empty; the retrieval date is in the provenance notes. Pin the payload with `CGJRKIT_SNAPSHOT` |

## Query

```graphql
{ locationCountry { countryId iso3Code nameShortEn formerCountry }
  countryYear(productClass: HS92, yearMin: 2013, yearMax: 2023) { countryId year eci [exportValue importValue] } }
```

`countryId` follows UN M49 where possible; ISO3 codes come from `locationCountry`. Ids with no ISO3 become `ATLAS<id>`. `productClass` is `HS92` (default), `HS12`, `SITC`, or `HS22` (UNVERIFIED).

**ECI rank.** We could not verify a rank field in the public API, so the rank is computed: countries with a non-null ECI in the year, excluding `formerCountry`, ranked by ECI descending. 1 is the most complex; ties share the lower rank. Ranks are computed before country filtering, so a peer subset keeps its world ranks. The universe size per year is in `meta["rank_universe"]`.

## Mapping to the canonical frame

| Canonical | From |
|---|---|
| `source` / `dataset` | `"atlas"` / `"Atlas <class>"` |
| `indicator` | `eci_<class>` (e.g. `eci_hs92`), `eci_rank_<class>`, `export_value`, `import_value` |
| `iso3` / `country` | `iso3Code` / `nameShortEn` |
| `period` / `value` | `year` / ECI (standardized index), rank, or current US$ |

## How this was verified

`atlas.hks.harvard.edu` is blocked in the build environment. Sources read on 2026-10-11:

| Label | Source | What it established |
|---|---|---|
| P | Growth Lab API docs, [harvard-growth-lab/api-docs `atlas.md`](https://raw.githubusercontent.com/harvard-growth-lab/api-docs/7ff06af598256e5ee2bf8215b8b150f8cdbac586/atlas.md) (last updated January 2026) | Base URL, POST with JSON, no authentication, 120 requests/minute, courtesy access, caching advice. Examples `countryYear(countryId:, yearMin:, yearMax:) { year gdp gdppc population exportValue importValue eci }` and `locationCountry { countryId iso3Code nameEn }`. Ids follow M49. Classifications HS92, HS12, SITC. Citation and contact |
| P (via plan) | [atlas.hks.harvard.edu/llms.txt](https://atlas.hks.harvard.edu/llms.txt) v1.0 (2025-11-20), quoted in `inventory/build-plan.md` | ≤120 requests/minute; research and educational use |
| T | [accidentalscientist portfolio `refresh_world_lens_data.py`](https://github.com/accidentalscientist/accidental-scientist-portfolio) | `countryYear(productClass: HS92, yearMin:, yearMax:)` **without** `countryId` returns all countries; `locationCountry { countryId iso3Code nameShortEn formerCountry }`; `{"data": ..., "errors": ...}` envelope |
| T | [peeedge/WITW `scripts/fetch-data.mjs`](https://github.com/peeedge/WITW), [argendatafundar/etl Atlas helper](https://github.com/argendatafundar/etl) | `formerCountry`, `iso2Code` fields; the web app's `GetCountries` query (`locationLevel, legacyCountryId, inCp, inMv, isTrusted, ...`) |
| T | fabcity/planetai-node `atlas-economic-complexity.yaml` (2026-09-26) | The Atlas moved from CID to HKS on 2026-09-19 and the old API host no longer resolves. dataAvailability: HS92 1995–2024, HS12 2012–2024, HS22 2022–2024, SITC 1962–2024. Dataverse tables marked CC0 1.0 (their reading, not ours) |

### UNVERIFIED (live tests in `tests/live/test_atlas_live.py`)

| Assumption | Where | Live test |
|---|---|---|
| The computed rank universe matches the Atlas rankings (Kenya 87 in 2013 and 2023; Vietnam 58 → 48) | `rank_eci` | `test_kenya_vietnam_eci_ranks` (±5, prints exact values) |
| ECI is null outside the ranked set (universe of about 130–150 countries) | `rank_eci` | `test_rank_universe_size` |
| `countryYear` accepts `productClass` (incl. HS12) and returns `exportValue`/`importValue` | `build_query` | `test_trade_values_and_product_class` |
| `HS22` is a valid `productClass` value | `PRODUCT_CLASSES` | none |

## Caveats raised

| Code | Severity | When |
|---|---|---|
| `atlas_licence` | warn | Always: courtesy access, research/educational, commercial use unconfirmed |
| `atlas_unversioned` | warn | Always: no data version; pin a snapshot |
| `atlas_cross_check_only` | info | Always: Atlas trade data are re-cleaned Comtrade mirror data and differ from BACI and from the Atlas Dataverse files |
| `atlas_rank_universe` | info | Always: rank computed locally; evidence holds the universe size per year |
| `atlas_empty` | warn | No rows |

A cached payload with GraphQL `errors` is fetched again once with `refresh=True`, because `core.http.fetch` caches any 200 response.

## Replication note

Kenya CGJR Fig 26 (slope chart of ECI ranks, 2013 and 2023): `Atlas().fetch(peers, 2013, 2023)`. Ranks shift with Atlas revisions (the inventory flags "ranks depend on vintage"), so pin a snapshot when the chart is built and report the retrieval date.

## Core change request

`core.http.fetch` caches every 2xx payload, including GraphQL error envelopes. A hook to reject a payload before caching, or `Cache.forget(key)`, would remove the refresh-retry workaround.

## Fixtures (all synthetic)

- `atlas_eci_synthetic.json`: GraphQL `data` with `locationCountry` (incl. a former country and a country with a null ECI) and `countryYear` for 2097–2098. ECI values are round numbers with a tie.
- `atlas_error_synthetic.json`: GraphQL `errors` envelope for an unknown field.
