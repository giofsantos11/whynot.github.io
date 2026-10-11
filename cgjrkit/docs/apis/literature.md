# Literature resolvers: OpenAlex, Crossref and identifier links

Modules: `cgjrkit.connectors.openalex`, `cgjrkit.connectors.crossref` and `cgjrkit.connectors.handles`. They feed the future prior-findings ledger (`inventory/build-plan.md` sections 3.5 and 4.3).

| Module | Public API | Network |
|---|---|---|
| `openalex.py` | `resolve_doi(doi)`, `search_works(query, filters=None, per_page=25, *, page=1, sort=None)`; class `OpenAlex` with `resolve(doi_or_W_id)`, `resolve_doi`, `resolve_dois(dois)` (batch of up to 100), `search_works`, `headers()` | `api.openalex.org` |
| `crossref.py` | `resolve_doi(doi)`, `search(query, rows=20, *, field="bibliographic", author=None, filters=None)`; class `Crossref(contact_email=None, mailto_in="param")` | `api.crossref.org` |
| `handles.py` | `normalize_doi`, `doi_url`, `normalize_handle`, `handle_url`, `okr_url`, `is_okr_handle`, `normalize_openalex_id`, `openalex_url`, `parse_identifier`, `identifier_table`; works-table helpers `WORKS_COLUMNS`, `make_works_frame`, `empty_works_frame`, `validate_works_frame`, `returned_ids`; `contact_email`, `polite_user_agent` | **none** (pure) |

All `build_*_request`, `format_filters`, `parse` and `response_info` functions are pure. The connector classes wire them through `cgjrkit.core.http.fetch` (cache, snapshots, retries on 429/5xx).

## Anti-hallucination rule

Callers, including AI clients, may cite **only identifiers returned by these resolvers**: an `openalex_id` (`W...`) or `doi` value in a works table that `resolve_doi`, `resolve_dois`, `search_works` or `search` returned. They are also listed in `Result.meta["returned_ids"]`. The docstrings of all three modules repeat this rule.

- **Search hits are candidates.** Each search result carries the caveat `literature_search_candidates` (info). Before citing a hit for a given reference, check its title, year and authors, ideally by confirming that OpenAlex and Crossref agree (ledger rule 3).
- **Unresolved means flagged, not failed.** A DOI that returns 404 comes back as an empty works table with `meta["resolved"] = False` and the **blocking** caveat `literature_unresolved`. The ledger then flags the reference as "unpublished", "forthcoming" or "report-local key" (Kenya 2023a–g) instead of crashing.
- **Network failures are not "not found".** `NetworkBlocked` and HTTP errors other than 404 are raised.
- **Constructed links are not verified.** `handles.py` only checks that a string is well formed. `identifier_table` always reports `verified=False`. OKR handles have no open resolver here yet (see "Not implemented").

## The works table (non-canonical)

`Result.data` is **not** the canonical long frame of `cgjrkit.core.schema`. `Result.meta["table"]` is `"works"` and `Result.meta["canonical_frame"]` is `False`. Do not pass it to methods, charts or `validate_frame`; use `handles.validate_works_frame`.

| Column | dtype | OpenAlex source | Crossref source |
|---|---|---|---|
| `openalex_id` | object | `id` without `https://openalex.org/` (`W123`) | always missing |
| `doi` | object | `doi` without `https://doi.org/`, lower-cased | `DOI`, lower-cased |
| `title` | object | `title`, else `display_name` | `title[0]`, plus `": " + subtitle[0]` when the subtitle is not already in the title |
| `year` | Int64 | `publication_year` | first year in `issued`, `published`, `published-print`, `published-online`, `posted` |
| `authors` | object | `"; "`-joined `authorships[].author.display_name`, else `raw_author_name` (at most 100 authorships) | `"; "`-joined `given family`, else `name` (organizations) |
| `venue` | object | `primary_location.source.display_name` | `container-title[0]`, else `institution[0].name`, else `group-title`, else `publisher` |
| `type` | object | OpenAlex type (`article`, `preprint`, `book-chapter`, ...) | Crossref type (`journal-article`, `report`, `posted-content`, ...). **The vocabularies differ** |
| `cited_by_count` | Int64 | `cited_by_count` | `is-referenced-by-count` (Crossref-deposited citations only). **Not comparable** with OpenAlex |
| `url` | object | `https://doi.org/<doi>`, else `primary_location.landing_page_url`, else `https://openalex.org/W...` | `https://doi.org/<doi>`, else `URL` |
| `is_oa` | boolean | `open_access.is_oa` | always missing (Crossref has no OA flag) |

Text is whitespace-collapsed. Blank values become missing. Rows keep the resolver's order (relevance for searches, input order for `resolve_dois`). Abstracts are never read, because OpenAlex ships only inverted indexes and Crossref abstracts can be copyrighted.

## OpenAlex

| Item | Value |
|---|---|
| Host (`domains`) | `api.openalex.org` |
| Calls | `GET /works/doi:<doi>` or `/works/W<id>` (singleton); `GET /works?search=&filter=&per_page=&page=&sort=` (list); `GET /works?filter=doi:a\|b\|...&per_page=100` (batch; always the maximum page size, same cost, so duplicate OpenAlex records for one DOI cannot push another DOI off the page) |
| `select=` | `id,doi,title,display_name,publication_year,type,cited_by_count,authorships,primary_location,open_access,is_retracted,updated_date`. All are root-level Work fields in the official OpenAPI spec |
| Auth | Optional key from `$CGJRKIT_OPENALEX_KEY`, sent as `Authorization: Bearer <key>` (see UNVERIFIED). Never put in params, cache keys or provenance. A 429/403 while a key is set raises `OpenAlexRateLimitError` (a `core.http.FetchError` subclass) whose message says the header may not have been recognised and points to `/rate-limit?api_key=<key>` |
| Contact | `$CGJRKIT_CONTACT_EMAIL`, if set, goes only into the User-Agent (`mailto:`). The polite pool and the `mailto` parameter were retired in Feb 2026 |
| Pricing and limits | Singleton free. List+filter $0.0001 per call, `search=` $0.001 per call. Free daily budget: $1 with a free key, $0.10 without. At most 100 requests/s. 429 when the budget or rate is exceeded (the errors page also lists 403 for rate limiting). `per_page` 1–100. At most 100 OR values per filter. Request URL at most about 4 KB (4094 bytes). Basic paging up to 10,000 results |
| Errors | JSON `{"error": ..., "message": ...}`. 404 "not found". 301 redirect for merged entities (followed by `http.fetch`) |
| Licence | CC0 (OpenAPI `info.license`; licence register `openalex`) |
| Citation | "OpenAlex" |
| Vintage | None. It is a live index without release identifiers, so the payload SHA-256 pins the response. Singletons record `updated_date` in the provenance notes and in `meta["record_updated"]` |

**Filters.** `filters` takes a mapping or a raw OpenAlex filter string. Mapping keys are sorted so that equal requests share a cache key. `bool` becomes `true`/`false`, lists become OR (`a|b`), and strings pass operators through (`">100"`, `"!paratext"`, `"2010-2015"`). Commas inside values are rejected. Examples: `{"publication_year": 2011}`, `{"type": ["article", "preprint"]}`, `{"authorships.author.id": "A5023888391"}`. Following OpenAlex's own advice, resolve names to IDs first and do not filter on display names.

**Batch truncation.** `resolve_dois` compares `meta.count` with the number of records returned. When OpenAlex matched more records than it returned (duplicate records for some DOIs), DOIs missing from the page are looked up with free singleton calls. Only DOIs still missing (404) are unresolved. DOIs whose singleton call failed (offline, unreachable, rate limit, server error) go to `meta["unchecked"]`: they are not in the returned table, so they cannot be cited, but they are not reported as "not found" either.

**Caveats.** `literature_search_candidates` (info, searches). `literature_unresolved` (block: 404, or DOIs missing from a batch that was not truncated, or still missing after the singleton fallback). `openalex_duplicate_records` (info: a batch returned several OpenAlex records for one DOI; evidence lists them). `literature_doi_mismatch` (warn: the returned DOI differs from the one requested). `literature_retracted` (warn: `is_retracted`). `openalex_authors_truncated` (info: 100 authorships). `literature_results_truncated` (info: more matches than were returned; in a batch, warn when some singleton fallbacks failed). `openalex_no_api_key` (info: list or search calls without a key).

**Meta.** `table`, `canonical_frame`, `resolver`, `request` (params without `select`), `resolved`, `returned_ids` and `api_key_sent_as_header` (a boolean, never the key; it says the key was sent, not that OpenAlex honoured it, see UNVERIFIED). Searches add `total_count` and `cost_usd`. Batches add `unresolved`, `unchecked`, `checked_individually` and `cost_usd`.

## Crossref

| Item | Value |
|---|---|
| Host (`domains`) | `api.crossref.org` |
| Calls | `GET /works/<doi>` (singleton, full record); `GET /works?query.bibliographic=&query.author=&filter=&rows=&select=` (list) |
| `select=` (search only) | `DOI,title,subtitle,author,issued,published,published-print,published-online,type,container-title,group-title,publisher,is-referenced-by-count,URL,updated-by` (all in Crossref's documented select list) |
| Auth | None |
| Polite pool | `mailto` from `$CGJRKIT_CONTACT_EMAIL` (or `contact_email=`). Default `mailto_in="param"` sends it as the `mailto` query parameter, so the address is part of the cache key and of `Provenance.params`. `mailto_in="header"` sends it only in the User-Agent (`... (research toolkit; mailto:<email>)`), which keeps snapshots replayable by people or CI with another address. The User-Agent always carries it. Without an address, requests go to the public pool and the caveat `crossref_public_pool` (info) is added. A malformed address raises `ValueError` |
| Limits (from 1 Dec 2025) | Polite pool: 10 requests/s for single DOIs and 3 requests/s for lists, with 3 concurrent requests. Public pool: 5 and 1 requests/s. `rows` 1–1000. `offset` up to 10,000. Limits are advertised in the `X-Rate-Limit-Limit` and `X-Rate-Limit-Interval` headers. The connector keeps network calls under these rates with a process-wide sliding-window limiter per pool and route (`crossref.RATE_LIMITS`: polite 10/s single, 3/s list; public 5/s single, 1/s list; it sleeps when full, reusing `atlas.MinuteLimiter` with a 1 s window). Cache hits are not throttled. Calls are sequential, so the 3-concurrent cap is never reached |
| Envelope | `{"status": "ok", "message-type": "work" \| "work-list", "message-version": "1.0.0", "message": {...}}`. A list message holds `items`, `total-results`, `items-per-page` and `query` |
| Errors | Unknown and non-Crossref DOIs return HTTP 404 "Resource not found.". Validation errors return `{"status": "failed", "message-type": "validation-failure", "message": [{"type", "value", "message"}]}` |
| Licence | Bibliographic metadata are facts, free to reuse. Abstracts can be copyrighted and are not read (licence register `crossref`) |
| Citation | "Crossref" |
| Vintage | None. Records are revised by depositors, so the payload hash pins the response |

**Search.** `field="bibliographic"` (default) is Crossref's recommended field for citation look-up: it covers titles, authors, ISSNs and years. `field=None` uses the plain `query`. `author=` adds `query.author`. `query.title` was retired by Crossref and is rejected. `filters` is a mapping or a string. A list value repeats the filter name (`type:a,type:b`), which Crossref ORs. Dates are `YYYY[-MM[-DD]]`, as in `{"from-pub-date": "2011", "until-pub-date": "2011"}`.

**404 does not mean fake.** Crossref serves only Crossref DOIs. DataCite DOIs (such as the PWT DOIs `10.34894/...`) and mEDRA DOIs return 404 here. The unresolved caveat says so: try OpenAlex before flagging the reference.

**Caveats.** `literature_search_candidates`, `literature_unresolved` (block), `literature_doi_mismatch` (warn), `literature_retracted` (warn: an `updated-by` entry of type `retraction`), `literature_results_truncated` (info) and `crossref_public_pool` (info).

## Identifier links (`handles.py`, no network)

| Identifier | Accepted forms | Canonical URL | Landing URL |
|---|---|---|---|
| DOI | `10.x/y`, `doi:`, `info:doi/`, `https://doi.org/`, `http://dx.doi.org/`, percent-encoded | `https://doi.org/<doi>` (lower-cased; `#`, `?`, `%`, `<>`, `[]` and quotes are percent-encoded) | same |
| World Bank OKR handle | `10986/<digits>`, `hdl:`, `https://hdl.handle.net/`, `https://openknowledge.worldbank.org/handle/` (query and fragment dropped) | `https://hdl.handle.net/10986/<n>` | `https://openknowledge.worldbank.org/handle/10986/<n>` |
| Other handle | `<prefix>/<suffix>` (numeric, dotted prefix) | `https://hdl.handle.net/<prefix>/<suffix>` | same |
| OpenAlex work | `W<digits>`, `https://openalex.org/W...` | `https://openalex.org/W<digits>` | same |

`parse_identifier` tries DOIs first, then OpenAlex IDs, then handles. A DOI written as a handle URL is classified as a DOI. Strings such as "World Bank 2023c" or "IMF Country Report No. 24/12" raise `IdentifierError`, and `identifier_table(..., errors="keep")` marks them `invalid`. World Bank Publications DOIs use the prefix `10.1596`.

The works-table helpers and the contact-email helpers live in `handles.py` so that both resolvers share them without importing each other.

## Not implemented (by design)

- **World Bank Documents & Reports API** (`search.worldbank.org/api/v3/wds`) and the **OKR DSpace REST API**. The World Bank's terms say "you may not in any event use the APIs to facilitate commercial uses of the Materials", so both wait for legal sign-off (plan section 4.3). Ordinary links (`okr_url`, `handle_url`) are used instead.
- **Handle verification.** The Handle System proxy offers a JSON REST API (`https://hdl.handle.net/api/handles/<handle>` returns `{"responseCode": 1, "handle": ..., "values": [{"index", "type": "URL", "data": {"format", "value"}, "ttl", "timestamp"}]}`; `responseCode` 100 means "not found", with HTTP 404). It could confirm that an OKR handle exists without touching World Bank APIs. It is not wired in because `hdl.handle.net` is not yet declared in `registries/hosts.yaml`, and `tests/test_registries.py` requires every connector domain to be declared there. See the final report for the requested registry change.

## How this was verified

The API hosts are blocked in the build environment (proxy 403 on CONNECT for `api.openalex.org` and `api.crossref.org`, checked on 2026-10-11), and WebFetch could not resolve `developers.openalex.org`, `help.openalex.org` or `www.crossref.org`. Nothing was called live. Sources read on 2026-10-11:

| Label | Source | What it established |
|---|---|---|
| P | OpenAlex docs repository `ourresearch/docs` (the source of developers.openalex.org): [llms.txt](https://raw.githubusercontent.com/ourresearch/docs/main/llms.txt), [api-reference/openapi.json](https://raw.githubusercontent.com/ourresearch/docs/main/api-reference/openapi.json), [api-reference/authentication.mdx](https://raw.githubusercontent.com/ourresearch/docs/main/api-reference/authentication.mdx), [api-reference/errors.mdx](https://raw.githubusercontent.com/ourresearch/docs/main/api-reference/errors.mdx), [guides/get.mdx](https://raw.githubusercontent.com/ourresearch/docs/main/guides/get.mdx), [guides/searching.mdx](https://raw.githubusercontent.com/ourresearch/docs/main/guides/searching.mdx), [guides/filtering.mdx](https://raw.githubusercontent.com/ourresearch/docs/main/guides/filtering.mdx), [guides/deprecations.mdx](https://raw.githubusercontent.com/ourresearch/docs/main/guides/deprecations.mdx), [api-reference/works.mdx](https://raw.githubusercontent.com/ourresearch/docs/main/api-reference/works.mdx) | Base URL. `api_key` query parameter (the OpenAPI `securitySchemes` and parameter marked "required"). Pricing ($0.10/day keyless, $1/day with a free key; singleton free, list $0.0001, search $0.001). 100 requests/s. 429 (and 403) on limits. `per_page` max 100. 100 OR values. About 4 KB URL limit. `/works/doi:<doi>` and `/works/https://doi.org/<doi>`. Work, Location, OpenAccess, Authorship, DehydratedSource and Meta schemas (including `meta.cost_usd`). Error object `{error, message}`. 301 for merged entities. Polite pool replaced by keys in Feb 2026 ("the `mailto` parameter is ignored"). `host_venue` removed in favour of `primary_location`. "Putting `api_key` and `mailto` in request headers" is mentioned in the search guide. `Authorization: Bearer` is documented for collections. Data licence CC0 (OpenAPI `info.license`) |
| T | [pyalex 0.21](https://pypi.org/project/pyalex/) (PyPI wheel, `pyalex/api.py` and README) | Sends the key as `Authorization: Bearer <key>` and the email as a `From` header. README: key required from 13 Feb 2026, singletons free, list calls 1 credit, 100 requests/s. DOI look-up via `Works()["https://doi.org/..."]` |
| P | Crossref REST API docs ([rest-api-doc README](https://raw.githubusercontent.com/CrossRef/rest-api-doc/master/README.md) (marked deprecated after the Elasticsearch migration, but the routes are unchanged), [api_format.md](https://raw.githubusercontent.com/CrossRef/rest-api-doc/master/api_format.md)) | `mailto` parameter or a `mailto:` User-Agent for the polite pool (HTTPS only). `X-Rate-Limit-*` headers. URL-encode DOIs. 404 for non-Crossref DOIs. Message types `work` and `work-list`. `rows` max 1000. `offset` max 10k. `cursor`. `select`. `query.bibliographic` and `query.author`, with `query.title` retired. Repeated filters with the same name are ORed. Work fields (`title`, `subtitle`, `author` given/family/name, `issued` date-parts, `container-title`, `group-title`, `is-referenced-by-count`, `URL`, `type`) |
| P | Crossref blog ["Announcing changes to REST API rate limits"](https://crossref.org/blog/announcing-changes-to-rest-api-rate-limits/) (via web search summary; the page itself could not be fetched) and [access and authentication](https://crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/) (search snippet) | From 1 Dec 2025: polite pool 10 requests/s single and 3 requests/s lists (concurrency 3); public pool 5 and 1. Tiers: public, polite, Metadata Plus |
| T | [habanero 2.9.2](https://pypi.org/project/habanero/) (`select.py`, `request.py`, `habanero_utils.py`) and [crossrefapi 1.7.0](https://pypi.org/project/crossrefapi/) (`restful.py`) | The valid `select` field list. Error messages are a string or a list of objects with `message`. DOIs are appended to `/works/` unencoded apart from special characters. The User-Agent `mailto:` convention. `Crossref-Plus-API-Token` header for Metadata Plus |
| P | DSpace UI source ([app-routes.ts](https://raw.githubusercontent.com/DSpace/dspace-angular/main/src/app/app-routes.ts), [lookup-by-id-routes.ts](https://raw.githubusercontent.com/DSpace/dspace-angular/main/src/app/lookup-by-id/lookup-by-id-routes.ts), [lookup-guard.ts](https://raw.githubusercontent.com/DSpace/dspace-angular/main/src/app/lookup-by-id/lookup-guard.ts)) | DSpace 7+ keeps a `handle/<prefix>/<suffix>` route that looks items up by handle, so `https://openknowledge.worldbank.org/handle/10986/<n>` works on the DSpace 7 OKR. The OKR runs DSpace 7 according to plan-research.md (Atmire case study) |
| T | Web search (OKR record and library catalogues) | Handle `10986/11843` is World Development Report 2013: Jobs (CC BY 3.0 IGO). DOI `10.3386/w17143` is NBER WP 17143, McMillan and Rodrik, "Globalization, Structural Change and Productivity Growth", June 2011 ([NBER](https://www.nber.org/papers/w17143)) |
| T | [pyhandle 1.5.0](https://pypi.org/project/pyhandle/) (EUDAT; `handlesystemconnector.py`, `hsresponses.py`, tests) and the DOI Handbook (search snippet on REST response codes) | The Handle REST API at `/api/handles/<prefix>/<suffix>`, the JSON `responseCode`/`values` layout, and response code 1 for success and 100 for not found. Recorded for the planned handle verifier only |

P = documentation or code from the provider. T = a third party.

### UNVERIFIED (each has a live test where possible)

| Assumption | Where in the code | Live test |
|---|---|---|
| OpenAlex accepts the key as `Authorization: Bearer <key>` for budget accounting (the documented form is `api_key=`: the authentication page, re-read on 2026-10-11, shows only the query parameter; the header is what pyalex 0.21 sends, `api.py` line 408, and the search guide mentions keys in headers). Until confirmed, `meta["api_key_sent_as_header"]` makes no claim that the key was honoured, and 429/403 with a key raise `OpenAlexRateLimitError`. If this fails, the key must go into the query string, which would put it into cache keys and provenance, so the connector would need a redaction mechanism in `core/http.py` first | `OpenAlex.headers()` | `tests/live/test_openalex_live.py::test_bearer_key_is_recognised` (needs `CGJRKIT_OPENALEX_KEY`) |
| Keyless singleton look-ups work, despite the OpenAPI spec marking `api_key` as required (the pricing page gives a $0.10/day keyless budget) | `OpenAlex.__init__` (key optional) | `test_keyless_singleton_is_allowed` |
| OpenAlex answers 404 for an unknown DOI on `/works/doi:<doi>` (the errors page says 404 for missing entities) | `_is_not_found` | `test_unknown_doi_is_404_unresolved` |
| `select=` is accepted on the singleton route together with `doi:` look-ups (the OpenAPI spec lists `select` on `/works/{id}`) | `build_work_request` | `test_resolve_mcmillan_rodrik_2011` |
| The batch `filter=doi:a\|b` accepts bare lower-case DOIs (docs show both bare and `https://doi.org/` forms) | `build_dois_request` | `test_batch_doi_filter` |
| Crossref's `select` list (from habanero) is still accepted together with `query.bibliographic`, `query.author` and date filters | `SEARCH_SELECT`, `build_search_request` | `tests/live/test_crossref_live.py::test_search_select_and_filters_accepted` |
| Which Crossref field names NBER as the venue for working papers (`institution`, `container-title`, `group-title` or `publisher`); `institution` is not in the 2018 format table but appears in newer records | `crossref._venue` | `test_resolve_mcmillan_rodrik_2011` (asserts only that a venue exists) |
| Retractions appear in Crossref `updated-by` entries with `type: "retraction"` (Retraction Watch data added to Crossref in 2023) | `crossref.response_info` | none (no stable retracted DOI chosen) |
| `core.http.FetchError` messages start with the status code (`"404 from ..."`). This holds for the current core, but `FetchError` has no `status_code` attribute | `_is_not_found` in both resolvers | unit tests (`test_not_found_is_unresolved*`) |
| The OKR `/handle/10986/<n>` route and the `hdl.handle.net` redirect work live (verified in DSpace source only) | `handles.okr_url`, `handles.handle_url` | `tests/live/test_handles_live.py` |

## Allowlist

`api.openalex.org` and `api.crossref.org` (resolvers). For the link checks in `tests/live/test_handles_live.py`: `openknowledge.worldbank.org`, `hdl.handle.net`, `doi.org`, and the redirect target `www.nber.org`.
