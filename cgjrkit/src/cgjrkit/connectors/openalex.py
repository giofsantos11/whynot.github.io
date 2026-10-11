"""OpenAlex literature resolver (``api.openalex.org``).

Resolves DOIs and searches scholarly works for the prior-findings ledger.
Results are a **works table** (:data:`cgjrkit.connectors.handles.WORKS_COLUMNS`:
``openalex_id, doi, title, year, authors, venue, type, cited_by_count, url,
is_oa``). This is a NON-CANONICAL table: it is not the long observation frame
of :mod:`cgjrkit.core.schema`, ``Result.meta["canonical_frame"]`` is False,
and it must not be passed to methods, charts or ``validate_frame``.

Anti-hallucination rule
-----------------------
Callers, including AI clients, may cite **only identifiers returned by these
resolvers**: the ``openalex_id`` and ``doi`` values of a returned works table
(also listed in ``Result.meta["returned_ids"]``). Search hits are ranked
*candidates*: confirm title, year and authors (ideally against Crossref, see
:mod:`cgjrkit.connectors.crossref`) before citing one for a given reference.
A DOI that does not resolve comes back as an empty table with the blocking
caveat ``literature_unresolved``; it must not be cited. Network failures are
raised (``NetworkBlocked``), never reported as "not found".

API facts (see ``docs/apis/literature.md`` for sources)
-------------------------------------------------------
* Singleton ``GET /works/doi:<doi>`` (or ``/works/W<id>``) is free; list and
  filter calls cost $0.0001, ``search=`` calls $0.001. Free daily budget:
  $1 with a free key, $0.10 without. At most 100 requests/s; 429 (and 403)
  when exceeded. ``per_page`` is 1-100; up to 100 OR values per filter.
* Key: read from ``$CGJRKIT_OPENALEX_KEY`` (optional; keyless calls get the
  small budget). It is sent as ``Authorization: Bearer <key>`` so that it
  never enters cache keys, cache indexes or provenance. The documented form
  is the ``api_key`` query parameter (the official authentication page shows
  no header form); the header form is what pyalex 0.21 sends, but its
  acceptance for budget accounting is UNVERIFIED (live test
  ``test_bearer_key_is_recognised``). ``Result.meta["api_key_sent_as_header"]``
  therefore says only that the key was sent, and a 429/403 while a key is set
  raises :class:`OpenAlexRateLimitError` naming this possibility.
* The old ``mailto`` "polite pool" was retired in February 2026 (``mailto``
  is ignored). ``$CGJRKIT_CONTACT_EMAIL``, when set, only goes into the
  User-Agent header.
* Licence: OpenAlex data are CC0.

``build_*_request``, :func:`format_filters`, :func:`parse` and
:func:`response_info` are pure; :class:`OpenAlex` wires them through
:func:`cgjrkit.core.http.fetch` (cache, snapshots, retries).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from urllib.parse import quote

import pandas as pd

from cgjrkit.connectors import handles
from cgjrkit.connectors.base import Connector
from cgjrkit.core import http
from cgjrkit.core.cache import Cache
from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Provenance, Result, utc_now

BASE_URL = "https://api.openalex.org"
KEY_ENV = "CGJRKIT_OPENALEX_KEY"
DATASET = "works"
LICENSE = "CC0 1.0 (OpenAlex data)"
CITATION = "OpenAlex"

MAX_PER_PAGE = 100
MAX_OR_VALUES = 100
#: OpenAlex lists at most this many authorships per work.
MAX_AUTHORSHIPS = 100
#: Keep the whole request URL under OpenAlex's ~4 KB limit (4094 bytes).
MAX_QUERY_BYTES = 3000

#: Root-level Work fields requested with ``select=`` (names from the official OpenAPI spec).
SELECT_FIELDS: tuple[str, ...] = (
    "id", "doi", "title", "display_name", "publication_year", "type", "cited_by_count",
    "authorships", "primary_location", "open_access", "is_retracted", "updated_date",
)

_FILTER_KEY_RE = re.compile(r"^[a-z_][a-z0-9_.]*$")

_SEARCH_CAVEAT = Caveat(
    "literature_search_candidates", "info",
    "Search results are ranked candidates, not confirmed matches. Check title, year and authors "
    "(and cross-check with Crossref) before citing; cite only the returned IDs.",
)
_NO_KEY_CAVEAT = Caveat(
    "openalex_no_api_key", "info",
    f"No OpenAlex API key (${KEY_ENV}): list and search calls draw on the keyless budget of $0.10/day "
    "(about 100 searches). A free key raises it to $1/day.",
)


class OpenAlexError(RuntimeError):
    """OpenAlex answered with an error object or an unexpected payload."""


class OpenAlexRateLimitError(http.FetchError):
    """429/403 from OpenAlex while an API key was set (the key may not have been recognised)."""


def _is_rate_limited(exc: Exception) -> bool:
    """``core.http.FetchError`` text: ``"403 from <url>: ..."`` or ``"giving up on <url> after N attempts: 429 from ..."``."""
    msg = str(exc)
    return msg.startswith("403 ") or msg.startswith("429 ") or ": 429 from " in msg


# ---------------------------------------------------------------------------
# pure request builders
# ---------------------------------------------------------------------------
def _select_param(select: Sequence[str] | None) -> dict[str, str]:
    return {"select": ",".join(select)} if select else {}


def build_work_request(identifier: str, *, select: Sequence[str] | None = SELECT_FIELDS) -> tuple[str, dict]:
    """Singleton request for a DOI (``/works/doi:<doi>``) or an OpenAlex ID (``/works/W<id>``)."""
    parsed = handles.parse_identifier(identifier)
    if parsed.id_type == "doi":
        path = quote(f"doi:{parsed.identifier}", safe=handles.PATH_SAFE)
    elif parsed.id_type == "openalex":
        path = parsed.identifier
    else:
        raise handles.IdentifierError(f"OpenAlex resolves DOIs and OpenAlex work IDs, not {parsed.id_type}s: {identifier!r}")
    return f"{BASE_URL}/works/{path}", _select_param(select)


def format_filters(filters: Mapping[str, Any] | str | None) -> str | None:
    """Render ``filters`` as an OpenAlex ``filter=`` value.

    A mapping becomes ``key:value`` pairs joined by commas (AND), keys sorted
    so equal requests get equal cache keys. Values: ``bool`` -> ``true``/
    ``false``; lists, tuples and sets -> values joined by ``|`` (OR, at most
    100); strings pass through, so operators such as ``">100"``,
    ``"!paratext"`` and ranges ``"2010-2015"`` work. ``None`` values are
    skipped. A string is passed through unchanged (after a basic check).
    Commas inside values are rejected because they would split the filter.
    """
    if filters is None:
        return None
    if isinstance(filters, str):
        s = filters.strip()
        if not s:
            return None
        if any(":" not in part for part in s.split(",")):
            raise ValueError(f"filter string must be key:value pairs separated by commas: {filters!r}")
        return s
    parts = []
    for key in sorted(filters):
        value = filters[key]
        if value is None:
            continue
        if not _FILTER_KEY_RE.match(key):
            raise ValueError(f"invalid OpenAlex filter field {key!r}")
        if isinstance(value, (list, tuple, set, frozenset)):
            values = sorted(value, key=str) if isinstance(value, (set, frozenset)) else list(value)
            if not values:
                raise ValueError(f"filter {key!r} has an empty value list")
            if len(values) > MAX_OR_VALUES:
                raise ValueError(f"filter {key!r} has {len(values)} OR values; OpenAlex allows at most {MAX_OR_VALUES}")
            rendered = "|".join(_filter_value(key, v) for v in values)
        else:
            rendered = _filter_value(key, value)
        parts.append(f"{key}:{rendered}")
    return ",".join(parts) or None


def _filter_value(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    s = str(value).strip()
    if not s or "," in s:
        raise ValueError(f"filter {key!r} value {value!r} is empty or contains a comma")
    return s


def build_search_request(
    query: str | None,
    filters: Mapping[str, Any] | str | None = None,
    per_page: int = 25,
    *,
    page: int = 1,
    sort: str | None = None,
    select: Sequence[str] | None = SELECT_FIELDS,
) -> tuple[str, dict]:
    """List request ``/works?search=...&filter=...&per_page=...``.

    ``query`` may be empty when ``filters`` are given (a cheaper list+filter
    call). ``page`` is sent only when above 1 and ``sort`` only when given.
    """
    if not isinstance(per_page, int) or isinstance(per_page, bool) or not 1 <= per_page <= MAX_PER_PAGE:
        raise ValueError(f"per_page must be an integer between 1 and {MAX_PER_PAGE}")
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise ValueError("page must be a positive integer")
    q = " ".join((query or "").split())
    if len(q.encode()) > MAX_QUERY_BYTES:
        raise ValueError(f"query is longer than {MAX_QUERY_BYTES} bytes; OpenAlex limits request URLs to about 4 KB")
    flt = format_filters(filters)
    if not q and not flt:
        raise ValueError("give a search query, filters, or both")
    params: dict[str, Any] = {}
    if q:
        params["search"] = q
    if flt:
        params["filter"] = flt
    params["per_page"] = per_page
    if page > 1:
        params["page"] = page
    if sort:
        params["sort"] = sort
    params.update(_select_param(select))
    return f"{BASE_URL}/works", params


def build_dois_request(dois: Iterable[str], *, select: Sequence[str] | None = SELECT_FIELDS) -> tuple[str, dict, list[str]]:
    """Batch look-up ``/works?filter=doi:a|b|...`` for up to 100 DOIs.

    Returns ``(url, params, normalized_dois)``; DOIs are normalized and
    de-duplicated in input order. ``per_page`` is always the maximum (100):
    the call costs the same, and OpenAlex can hold several (unmerged) records
    for one DOI, so ``per_page=len(dois)`` could push another DOI off the page.
    """
    norm: list[str] = []
    for d in dois:
        n = handles.normalize_doi(d)
        if "|" in n or "," in n:
            raise ValueError(f"DOI {n!r} contains '|' or ','; resolve it on its own with resolve_doi")
        if n not in norm:
            norm.append(n)
    if not norm:
        raise ValueError("no DOIs given")
    if len(norm) > MAX_OR_VALUES:
        raise ValueError(f"at most {MAX_OR_VALUES} DOIs per batch, got {len(norm)}")
    params = {"filter": "doi:" + "|".join(norm), "per_page": MAX_PER_PAGE, **_select_param(select)}
    return f"{BASE_URL}/works", params, norm


# ---------------------------------------------------------------------------
# pure parsers
# ---------------------------------------------------------------------------
def _load(payload: bytes) -> tuple[list[dict], dict, bool]:
    """Decode a payload into ``(works, meta, is_singleton)``."""
    try:
        doc = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as exc:
        raise OpenAlexError(f"OpenAlex payload is not JSON: {payload[:200]!r}") from exc
    if not isinstance(doc, dict):
        raise OpenAlexError("OpenAlex payload is not a JSON object")
    if "error" in doc and "id" not in doc:
        raise OpenAlexError(f"OpenAlex error: {doc.get('error')}: {doc.get('message', '')}")
    if "results" in doc:
        results = doc.get("results") or []
        if not isinstance(results, list):
            raise OpenAlexError("OpenAlex 'results' is not a list")
        return [w for w in results if isinstance(w, dict)], dict(doc.get("meta") or {}), False
    if "id" in doc:
        return [doc], {}, True
    raise OpenAlexError(f"unknown OpenAlex response shape (keys: {sorted(doc)[:10]})")


def _work_key(work: Mapping[str, Any]) -> str | None:
    """OpenAlex key ``W...`` of a Work object, or None if missing or malformed."""
    try:
        return handles.normalize_openalex_id(work["id"]) if work.get("id") else None
    except handles.IdentifierError:
        return None


def work_record(work: Mapping[str, Any]) -> dict:
    """One works-table row from an OpenAlex Work object."""
    openalex_id = _work_key(work)
    try:
        doi = handles.normalize_doi(work["doi"]) if work.get("doi") else None
    except handles.IdentifierError:
        doi = None
    names = []
    for a in work.get("authorships") or []:
        if not isinstance(a, Mapping):
            continue
        name = (a.get("author") or {}).get("display_name") or a.get("raw_author_name")
        if name:
            names.append(" ".join(str(name).split()))
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    if doi:
        url = handles.doi_url(doi)
    elif location.get("landing_page_url"):
        url = location["landing_page_url"]
    else:
        url = handles.openalex_url(openalex_id) if openalex_id else None
    is_oa = (work.get("open_access") or {}).get("is_oa")
    return {
        "openalex_id": openalex_id,
        "doi": doi,
        "title": work.get("title") or work.get("display_name"),
        "year": work.get("publication_year"),
        "authors": "; ".join(names),
        "venue": source.get("display_name"),
        "type": work.get("type"),
        "cited_by_count": work.get("cited_by_count"),
        "url": url,
        "is_oa": is_oa if isinstance(is_oa, bool) else None,
    }


def parse(payload: bytes) -> pd.DataFrame:
    """Works table from a singleton Work or a ``{"meta", "results"}`` list payload (rank order kept)."""
    works, _, _ = _load(payload)
    return handles.make_works_frame([work_record(w) for w in works])


def response_info(payload: bytes) -> dict:
    """Facts about a payload used for caveats and ``Result.meta`` (pure)."""
    works, meta, singleton = _load(payload)
    return {
        "singleton": singleton,
        "total_count": meta.get("count") if not singleton else len(works),
        "returned": len(works),
        "cost_usd": meta.get("cost_usd"),
        "retracted": [k for w in works if w.get("is_retracted") and (k := _work_key(w))],
        "authors_truncated": [k for w in works
                              if len(w.get("authorships") or []) >= MAX_AUTHORSHIPS and (k := _work_key(w))],
        "updated_date": works[0].get("updated_date") if singleton and works else None,
    }


# ---------------------------------------------------------------------------
# connector
# ---------------------------------------------------------------------------
def _is_not_found(exc: Exception) -> bool:
    """``core.http.FetchError`` messages start with the status code (``"404 from <url>: ..."``)."""
    return str(exc).startswith("404 ")


class OpenAlex(Connector):
    """OpenAlex works resolver. See the module docstring for the citation rule."""

    source = "openalex"
    license = LICENSE
    citation = CITATION
    domains = ("api.openalex.org",)

    def __init__(self, cache: Cache | None = None, *, api_key: str | None = None, contact_email: str | None = None):
        super().__init__(cache)
        self.api_key = (api_key if api_key is not None else os.environ.get(KEY_ENV, "")).strip() or None
        self.contact_email = handles.contact_email(contact_email)

    # -- helpers -------------------------------------------------------------
    def headers(self) -> dict[str, str]:
        """Request headers: User-Agent (with ``mailto:`` if known) and the key as a Bearer token."""
        hdrs = {"User-Agent": handles.polite_user_agent(self.contact_email)}
        if self.api_key:
            hdrs["Authorization"] = f"Bearer {self.api_key}"
        return hdrs

    def _get(self, url: str, params: dict, refresh: bool):
        try:
            return http.fetch(self.source, url, params, headers=self.headers(), cache=self.cache, refresh=refresh)
        except http.FetchError as exc:
            if self.api_key and _is_rate_limited(exc):
                raise OpenAlexRateLimitError(
                    f"{exc} -- an API key is set but is sent as 'Authorization: Bearer'; OpenAlex documents only the "
                    "api_key query parameter, so the header may be ignored and the call charged to the keyless "
                    "$0.10/day budget (UNVERIFIED; live test test_bearer_key_is_recognised). Check the key's budget "
                    "at https://api.openalex.org/rate-limit?api_key=<key> before retrying."
                ) from exc
            raise

    def _meta(self, request: dict, df: pd.DataFrame, resolved: bool, **extra) -> dict:
        return {
            "table": "works",
            "canonical_frame": False,
            "resolver": self.source,
            "request": request,
            "resolved": resolved,
            "returned_ids": handles.returned_ids(df),
            # the key is sent as a Bearer header, whose acceptance is UNVERIFIED: this says it was sent, not honoured
            "api_key_sent_as_header": bool(self.api_key),
            **extra,
        }

    def _unresolved(self, url: str, params: dict, tool: str, request: dict, reason: str) -> Result:
        prov = Provenance(source=self.source, dataset=DATASET, endpoint=url, params=params, retrieved_at=utc_now(),
                          license=self.license, citation=self.citation, tool=tool,
                          notes=[f"not resolved: {reason}"])
        df = handles.empty_works_frame()
        res = Result(data=df, provenance=[prov], meta=self._meta(request, df, False))
        res.add_caveat(Caveat("literature_unresolved", "block",
                              f"OpenAlex could not resolve {request}; do not cite it as resolved. Flag the reference "
                              "(e.g. 'unpublished', 'forthcoming', 'report-local key') or try Crossref.",
                              {"request": request, "reason": reason[:300]}))
        return res

    def _common_caveats(self, res: Result, info: dict) -> None:
        if info["retracted"]:
            res.add_caveat(Caveat("literature_retracted", "warn",
                                  "OpenAlex marks these works as retracted (Retraction Watch); do not cite them as evidence.",
                                  {"openalex_ids": info["retracted"]}))
        if info["authors_truncated"]:
            res.add_caveat(Caveat("openalex_authors_truncated", "info",
                                  f"OpenAlex lists at most {MAX_AUTHORSHIPS} authorships per work; author lists may be cut.",
                                  {"openalex_ids": info["authors_truncated"]}))

    # -- public API ------------------------------------------------------------
    def resolve(self, identifier: str, *, refresh: bool = False) -> Result:
        """Resolve one DOI or OpenAlex work ID (free singleton call)."""
        parsed = handles.parse_identifier(identifier)
        url, params = build_work_request(parsed.identifier)
        request = {parsed.id_type: parsed.identifier}
        tool = "OpenAlex.resolve_doi" if parsed.id_type == "doi" else "OpenAlex.resolve"
        try:
            payload, retrieval = self._get(url, params, refresh)
        except http.FetchError as exc:
            if _is_not_found(exc):
                return self._unresolved(url, params, tool, request, str(exc))
            raise
        df = parse(payload)
        info = response_info(payload)
        notes = ["OpenAlex is a live index without release identifiers; the payload hash pins this response."]
        if info["updated_date"]:
            notes.append(f"record updated_date {info['updated_date']}")
        prov = self.provenance(retrieval, dataset=DATASET, tool=tool, notes=notes)
        res = Result(data=df, provenance=[prov],
                     meta=self._meta(request, df, not df.empty, record_updated=info["updated_date"]))
        self._common_caveats(res, info)
        if parsed.id_type == "doi" and not df.empty and df.loc[0, "doi"] != parsed.identifier:
            res.add_caveat(Caveat("literature_doi_mismatch", "warn",
                                  "OpenAlex returned a work whose DOI differs from the one requested (merged or "
                                  "alternate record); confirm before citing.",
                                  {"requested": parsed.identifier, "returned": df.loc[0, "doi"]}))
        return res

    def resolve_doi(self, doi: str, *, refresh: bool = False) -> Result:
        """Resolve a DOI to a one-row works table (empty + ``literature_unresolved`` if not found)."""
        return self.resolve(handles.normalize_doi(doi), refresh=refresh)

    def resolve_dois(self, dois: Iterable[str], *, refresh: bool = False) -> Result:
        """Resolve up to 100 DOIs in one list call (``filter=doi:a|b|...``, ``per_page=100``, $0.0001).

        Rows follow the input order (a DOI with several OpenAlex records keeps
        them all, with the ``openalex_duplicate_records`` caveat). When
        ``meta.count`` exceeds the number of records returned (duplicate records
        filled the page), DOIs missing from the page are looked up one by one
        with free singleton calls before anything is declared unresolved.
        Only DOIs still missing after that are listed in the blocking
        ``literature_unresolved`` caveat and ``meta["unresolved"]``. DOIs whose
        singleton look-up failed (network or rate limit) are listed in
        ``meta["unchecked"]`` with a ``literature_results_truncated`` warning;
        they are neither resolved nor reported as "not found".
        """
        url, params, norm = build_dois_request(dois)
        payload, retrieval = self._get(url, params, refresh)
        df = parse(payload)
        info = response_info(payload)
        prov = self.provenance(retrieval, dataset=DATASET, tool="OpenAlex.resolve_dois",
                               notes=["OpenAlex is a live index without release identifiers; the payload hash pins this response."])
        provs = [prov]
        extra_caveats: list[Caveat] = []
        found = set(df["doi"].dropna())
        missing = [d for d in norm if d not in found]
        total = info["total_count"]
        truncated = isinstance(total, int) and total > info["returned"]
        unresolved: list[str] = []
        unchecked: list[str] = []
        checked: list[str] = []
        if missing and truncated:
            frames = [df]
            for doi in missing:
                checked.append(doi)
                try:
                    single = self.resolve(doi, refresh=refresh)  # free singleton; a 404 comes back empty
                except (http.FetchError, http.NetworkBlocked):  # rate limit, server error, unreachable, offline
                    unchecked.append(doi)
                    continue
                if single.data.empty:
                    unresolved.append(doi)
                    continue
                frames.append(single.data)
                provs.extend(p for p in single.provenance if p not in provs)
                extra_caveats.extend(c for c in single.caveats if c.code != "literature_unresolved")
            non_empty = [f for f in frames if not f.empty]
            if non_empty:  # every frame is a works table, so concat keeps the works dtypes
                df = pd.concat(non_empty, ignore_index=True)
        else:
            unresolved = missing
        order = {d: i for i, d in enumerate(norm)}
        df = (df.assign(_o=df["doi"].map(order)).sort_values("_o", kind="stable", na_position="last")
              .drop(columns="_o").reset_index(drop=True))
        res = Result(data=df, provenance=provs,
                     meta=self._meta({"dois": norm}, df, not unresolved and not unchecked, unresolved=unresolved,
                                     unchecked=unchecked, checked_individually=checked, cost_usd=info["cost_usd"]))
        self._common_caveats(res, info)
        for c in extra_caveats:
            res.add_caveat(c)
        dup = df[df["doi"].notna() & df["doi"].duplicated(keep=False)]
        if len(dup):
            res.add_caveat(Caveat("openalex_duplicate_records", "info",
                                  "OpenAlex holds several (unmerged) records for these DOIs; cite the record whose "
                                  "title, year and authors match the reference.",
                                  {"records": {d: sorted(set(g["openalex_id"].dropna()))
                                               for d, g in dup.groupby("doi", sort=True)}}))
        if truncated:
            res.add_caveat(Caveat(
                "literature_results_truncated", "warn" if unchecked else "info",
                f"OpenAlex matched {total} records but returned {info['returned']} (duplicate records for some DOIs); "
                "DOIs missing from the page were looked up one by one."
                + (" Some look-ups failed: those DOIs are unchecked, not unresolved." if unchecked else ""),
                {"total_count": total, "returned": info["returned"], "per_page": params["per_page"],
                 "checked_individually": checked, "unchecked": unchecked}))
        if unresolved:
            res.add_caveat(Caveat("literature_unresolved", "block",
                                  f"OpenAlex did not return {len(unresolved)} of {len(norm)} DOIs; do not cite them as resolved.",
                                  {"dois": unresolved}))
        if not self.api_key:
            res.add_caveat(_NO_KEY_CAVEAT)
        return res

    def search_works(
        self,
        query: str | None,
        filters: Mapping[str, Any] | str | None = None,
        per_page: int = 25,
        *,
        page: int = 1,
        sort: str | None = None,
        refresh: bool = False,
    ) -> Result:
        """Search works (``search=``, $0.001) and/or filter them (list+filter, $0.0001).

        ``filters`` is a mapping such as ``{"publication_year": "2010-2012",
        "type": "article"}`` or a raw OpenAlex filter string (see
        :func:`format_filters`). Rows keep OpenAlex's relevance order.
        """
        url, params = build_search_request(query, filters, per_page, page=page, sort=sort)
        payload, retrieval = self._get(url, params, refresh)
        df = parse(payload)
        info = response_info(payload)
        prov = self.provenance(retrieval, dataset=DATASET, tool="OpenAlex.search_works",
                               notes=["OpenAlex is a live index; ranking and counts change over time. The payload hash pins this response."])
        request = {k: v for k, v in params.items() if k != "select"}
        res = Result(data=df, provenance=[prov],
                     meta=self._meta(request, df, not df.empty, total_count=info["total_count"], cost_usd=info["cost_usd"]))
        res.add_caveat(_SEARCH_CAVEAT)
        self._common_caveats(res, info)
        if isinstance(info["total_count"], int) and info["total_count"] > page * per_page:
            res.add_caveat(Caveat("literature_results_truncated", "info",
                                  f"Showing {len(df)} of {info['total_count']} matches; refine the query or page through results.",
                                  {"total_count": info["total_count"], "page": page, "per_page": per_page}))
        if not self.api_key:
            res.add_caveat(_NO_KEY_CAVEAT)
        return res

    def fetch(self, doi: str, *, refresh: bool = False) -> Result:  # Connector interface
        """Alias of :meth:`resolve_doi`."""
        return self.resolve_doi(doi, refresh=refresh)


# ---------------------------------------------------------------------------
# module-level conveniences
# ---------------------------------------------------------------------------
def resolve_doi(doi: str, *, refresh: bool = False, cache: Cache | None = None) -> Result:
    """Resolve a DOI with OpenAlex. Cite only IDs present in the returned table."""
    return OpenAlex(cache).resolve_doi(doi, refresh=refresh)


def search_works(
    query: str | None,
    filters: Mapping[str, Any] | str | None = None,
    per_page: int = 25,
    *,
    page: int = 1,
    sort: str | None = None,
    refresh: bool = False,
    cache: Cache | None = None,
) -> Result:
    """Search OpenAlex works. Hits are candidates; cite only returned IDs after checking the match."""
    return OpenAlex(cache).search_works(query, filters, per_page, page=page, sort=sort, refresh=refresh)
