"""Crossref literature resolver (``api.crossref.org``).

Resolves Crossref DOIs and runs bibliographic searches, returning the same
**works table** as :mod:`cgjrkit.connectors.openalex`
(:data:`cgjrkit.connectors.handles.WORKS_COLUMNS`). This is a NON-CANONICAL
table (``Result.meta["canonical_frame"]`` is False); it must not be passed to
methods, charts or ``validate_frame``. Crossref has no OpenAlex ID and no
open-access flag, so ``openalex_id`` and ``is_oa`` are always missing;
``cited_by_count`` is Crossref's ``is-referenced-by-count`` (citations
deposited with Crossref only) and is not comparable with OpenAlex counts.

Anti-hallucination rule
-----------------------
Callers, including AI clients, may cite **only identifiers returned by these
resolvers**: the ``doi`` values of a returned works table (also listed in
``Result.meta["returned_ids"]``). Search hits are ranked *candidates*; confirm
title, year and authors before citing one for a reference, ideally by
checking that OpenAlex agrees. A DOI Crossref does not know comes back as an
empty table with the blocking caveat ``literature_unresolved``. That does not
prove the DOI is fake: DataCite, mEDRA and other agencies register DOIs that
Crossref does not serve (e.g. the PWT DOIs ``10.34894/...``), so try
OpenAlex before flagging the reference. Network failures are raised
(``NetworkBlocked``), never reported as "not found".

API facts (see ``docs/apis/literature.md`` for sources)
-------------------------------------------------------
* No key or sign-up. The polite pool is reached with a ``mailto`` query
  parameter or a ``mailto:`` in the User-Agent. The address comes from
  ``$CGJRKIT_CONTACT_EMAIL`` (or ``contact_email=``). By default it is sent as
  the ``mailto`` parameter (as Crossref's examples do), so it becomes part of
  the cache key and of ``Provenance.params``; pass ``mailto_in="header"`` to
  keep it out of both (useful when snapshots are replayed by people or CI
  with a different address). It is always also put in the User-Agent.
* Limits from 1 Dec 2025: polite pool 10 requests/s for single DOIs and
  3 requests/s for lists (3 concurrent); public pool 5 and 1 requests/s.
  ``rows`` is at most 1000; ``offset`` at most 10,000. A process-wide
  sliding-window limiter per pool and route (:data:`RATE_LIMITS`) keeps
  network calls under these rates (it sleeps when full); cache hits are not
  throttled. Calls are sequential, so the concurrency cap is never reached.
* Unknown or non-Crossref DOIs answer HTTP 404 ("Resource not found.").
* Licence: bibliographic metadata are facts, free to reuse; abstracts can be
  copyrighted, so they are never read into the table.

``build_*_request``, :func:`format_filters` and :func:`parse` are pure;
:class:`Crossref` wires them through :func:`cgjrkit.core.http.fetch`.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal
from urllib.parse import quote

import pandas as pd

from cgjrkit.connectors import handles
from cgjrkit.connectors.atlas import MinuteLimiter
from cgjrkit.connectors.base import Connector
from cgjrkit.core import http
from cgjrkit.core.cache import Cache
from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Provenance, Result, utc_now

BASE_URL = "https://api.crossref.org"
DATASET = "works"
LICENSE = "Crossref metadata: facts, free to reuse without restriction (abstracts excluded)"
CITATION = "Crossref"

MAX_ROWS = 1000
MAX_QUERY_BYTES = 3000
#: Fields requested with ``select=`` for searches (all in Crossref's documented select list).
SEARCH_SELECT: tuple[str, ...] = (
    "DOI", "title", "subtitle", "author", "issued", "published", "published-print", "published-online",
    "type", "container-title", "group-title", "publisher", "is-referenced-by-count", "URL", "updated-by",
)
#: Field queries accepted by ``search(field=...)`` (``query.<field>``); None means plain ``query``.
QUERY_FIELDS = {"bibliographic", "author", "editor", "chair", "translator", "contributor", "container-title",
                "publisher-name", "publisher-location", "affiliation", "degree", "funder-name",
                "standards-body-name", "standards-body-acronym", "event-name", "event-location",
                "event-sponsor", "event-acronym", "event-theme"}
#: Date fields tried, in order, for ``year`` (``issued`` is the earliest of print and online).
_YEAR_FIELDS = ("issued", "published", "published-print", "published-online", "posted")

_FILTER_KEY_RE = re.compile(r"^[a-z][a-z0-9.-]*$")

_SEARCH_CAVEAT = Caveat(
    "literature_search_candidates", "info",
    "Search results are ranked candidates, not confirmed matches. Check title, year and authors "
    "(and cross-check with OpenAlex) before citing; cite only the returned DOIs.",
)
_PUBLIC_POOL_CAVEAT = Caveat(
    "crossref_public_pool", "info",
    f"No contact email (${handles.CONTACT_ENV}): requests go to Crossref's public pool "
    "(5 requests/s for single DOIs, 1/s for lists) instead of the polite pool.",
)


class CrossrefError(RuntimeError):
    """Crossref answered with a failure message or an unexpected payload."""


# ---------------------------------------------------------------------------
# pure request builders
# ---------------------------------------------------------------------------
def _mailto_param(mailto: str | None) -> dict[str, str]:
    return {"mailto": mailto} if mailto else {}


def build_work_request(doi: str, *, mailto: str | None = None) -> tuple[str, dict]:
    """Singleton request ``/works/<doi>`` (DOI normalized; unsafe characters percent-encoded)."""
    d = handles.normalize_doi(doi)
    return f"{BASE_URL}/works/{quote(d, safe=handles.PATH_SAFE)}", _mailto_param(mailto)


def format_filters(filters: Mapping[str, Any] | str | None) -> str | None:
    """Render ``filters`` as a Crossref ``filter=`` value.

    A mapping becomes ``name:value`` pairs joined by commas, names sorted for
    stable cache keys. A list value repeats the filter (``type:a,type:b``),
    which Crossref treats as OR for the same filter name. ``bool`` ->
    ``true``/``false``. Dates are ``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD``
    (e.g. ``{"from-pub-date": "2011", "until-pub-date": "2011"}``). A string
    passes through unchanged.
    """
    if filters is None:
        return None
    if isinstance(filters, str):
        s = filters.strip()
        if s and any(":" not in part for part in s.split(",")):
            raise ValueError(f"filter string must be name:value pairs separated by commas: {filters!r}")
        return s or None
    parts = []
    for key in sorted(filters):
        value = filters[key]
        if value is None:
            continue
        if not _FILTER_KEY_RE.match(key):
            raise ValueError(f"invalid Crossref filter name {key!r}")
        values = list(value) if isinstance(value, (list, tuple)) else [value]
        if not values:
            raise ValueError(f"filter {key!r} has an empty value list")
        for v in values:
            s = ("true" if v else "false") if isinstance(v, bool) else str(v).strip()
            if not s or "," in s:
                raise ValueError(f"filter {key!r} value {v!r} is empty or contains a comma")
            parts.append(f"{key}:{s}")
    return ",".join(parts) or None


def build_search_request(
    query: str,
    rows: int = 20,
    *,
    field: str | None = "bibliographic",
    author: str | None = None,
    filters: Mapping[str, Any] | str | None = None,
    mailto: str | None = None,
    select: Sequence[str] | None = SEARCH_SELECT,
) -> tuple[str, dict]:
    """List request ``/works?query.bibliographic=...&rows=...``.

    ``field="bibliographic"`` (default) is Crossref's recommended field for
    citation look-up (titles, authors, ISSNs, years); ``field=None`` uses the
    plain ``query``. ``author`` adds ``query.author``.
    """
    if not isinstance(rows, int) or isinstance(rows, bool) or not 1 <= rows <= MAX_ROWS:
        raise ValueError(f"rows must be an integer between 1 and {MAX_ROWS}")
    if field is not None and field not in QUERY_FIELDS:
        raise ValueError(f"unknown Crossref field query {field!r}; use one of {sorted(QUERY_FIELDS)} or None")
    q = " ".join((query or "").split())
    a = " ".join((author or "").split())
    if not q and not a:
        raise ValueError("give a query (or an author)")
    if len(q.encode()) + len(a.encode()) > MAX_QUERY_BYTES:
        raise ValueError(f"query is longer than {MAX_QUERY_BYTES} bytes")
    params: dict[str, Any] = {}
    if q:
        params[f"query.{field}" if field else "query"] = q
    if a:
        params["query.author"] = a
    flt = format_filters(filters)
    if flt:
        params["filter"] = flt
    params["rows"] = rows
    if select:
        params["select"] = ",".join(select)
    params.update(_mailto_param(mailto))
    return f"{BASE_URL}/works", params


# ---------------------------------------------------------------------------
# pure parsers
# ---------------------------------------------------------------------------
def _load(payload: bytes) -> tuple[list[dict], dict]:
    """Decode a Crossref envelope into ``(items, list_summary)``."""
    try:
        doc = json.loads(payload)
    except (ValueError, UnicodeDecodeError) as exc:
        raise CrossrefError(f"Crossref payload is not JSON: {payload[:200]!r}") from exc
    if not isinstance(doc, dict):
        raise CrossrefError("Crossref payload is not a JSON object")
    if doc.get("status") != "ok":
        msg = doc.get("message")
        if isinstance(msg, list) and msg and isinstance(msg[0], dict):
            msg = "; ".join(str(m.get("message", m)) for m in msg)
        raise CrossrefError(f"Crossref status {doc.get('status')!r} ({doc.get('message-type')}): {msg}")
    mtype, message = doc.get("message-type"), doc.get("message") or {}
    if mtype == "work":
        return [message], {}
    if mtype == "work-list":
        items = message.get("items") or []
        return [i for i in items if isinstance(i, dict)], {
            "total_count": message.get("total-results"),
            "items_per_page": message.get("items-per-page"),
        }
    raise CrossrefError(f"unexpected Crossref message-type {mtype!r} (expected 'work' or 'work-list')")


def _first(value: Any) -> str | None:
    if isinstance(value, list):
        value = next((v for v in value if v), None)
    return " ".join(str(value).split()) if value else None


def _year(item: Mapping[str, Any]) -> int | None:
    for key in _YEAR_FIELDS:
        parts = (item.get(key) or {}).get("date-parts") or []
        if parts and isinstance(parts[0], list) and parts[0] and isinstance(parts[0][0], int):
            return parts[0][0]
    return None


def _venue(item: Mapping[str, Any]) -> str | None:
    """container-title, else institution name (reports, theses), else group-title, else publisher."""
    venue = _first(item.get("container-title"))
    if not venue:
        inst = item.get("institution")
        inst = inst[0] if isinstance(inst, list) and inst else inst
        if isinstance(inst, Mapping):
            venue = _first(inst.get("name"))
    return venue or _first(item.get("group-title")) or _first(item.get("publisher"))


def work_record(item: Mapping[str, Any]) -> dict:
    """One works-table row from a Crossref work item."""
    try:
        doi = handles.normalize_doi(item["DOI"]) if item.get("DOI") else None
    except handles.IdentifierError:
        doi = None
    title = _first(item.get("title"))
    subtitle = _first(item.get("subtitle"))
    if title and subtitle and subtitle.lower() not in title.lower():
        title = f"{title}: {subtitle}"
    names = []
    for a in item.get("author") or []:
        if not isinstance(a, Mapping):
            continue
        name = " ".join(p for p in (a.get("given"), a.get("family")) if p) or a.get("name")
        if name:
            names.append(" ".join(str(name).split()))
    return {
        "openalex_id": None,
        "doi": doi,
        "title": title,
        "year": _year(item),
        "authors": "; ".join(names),
        "venue": _venue(item),
        "type": item.get("type"),
        "cited_by_count": item.get("is-referenced-by-count"),
        "url": handles.doi_url(doi) if doi else item.get("URL"),
        "is_oa": None,
    }


def parse(payload: bytes) -> pd.DataFrame:
    """Works table from a ``work`` or ``work-list`` envelope (rank order kept)."""
    items, _ = _load(payload)
    return handles.make_works_frame([work_record(i) for i in items])


def response_info(payload: bytes) -> dict:
    """Facts about a payload used for caveats and ``Result.meta`` (pure)."""
    items, summary = _load(payload)
    retracted = []
    for i in items:
        doi = work_record(i)["doi"]
        if doi and any(isinstance(u, Mapping) and str(u.get("type", "")).lower() == "retraction"
                       for u in i.get("updated-by") or []):
            retracted.append(doi)
    return {"total_count": summary.get("total_count", len(items)), "returned": len(items), "retracted": retracted}


# ---------------------------------------------------------------------------
# connector
# ---------------------------------------------------------------------------
def _is_not_found(exc: Exception) -> bool:
    """``core.http.FetchError`` messages start with the status code (``"404 from <url>: ..."``)."""
    return str(exc).startswith("404 ")


#: Crossref limits from 1 Dec 2025, requests per second by (pool, route): "single" = /works/<doi>,
#: "list" = /works?query... The polite pool is reached with a mailto (parameter or User-Agent).
RATE_LIMITS: dict[tuple[str, str], int] = {
    ("polite", "single"): 10, ("polite", "list"): 3,
    ("public", "single"): 5, ("public", "list"): 1,
}
#: one sliding-window limiter per (pool, route), shared by all Crossref instances in the process
_LIMITERS: dict[tuple[str, str], MinuteLimiter] = {}


def limiter_for(pool: str, route: str) -> MinuteLimiter:
    """The process-wide limiter for a pool (``polite``/``public``) and route (``single``/``list``)."""
    key = (pool, route)
    if key not in _LIMITERS:
        _LIMITERS[key] = MinuteLimiter(limit=RATE_LIMITS[key], window_s=1.0)
    return _LIMITERS[key]


def _served_from_cache(cache: Cache, url: str, params: dict, refresh: bool) -> bool:
    """True when ``http.fetch`` will answer from the cache (snapshot pin, or latest unless ``refresh``)."""
    snapshot = os.environ.get("CGJRKIT_SNAPSHOT", "")
    if snapshot and cache.pinned(snapshot, Crossref.source, url, dict(params)):
        return True
    return (not refresh) and cache.latest(Crossref.source, url, dict(params)) is not None


class Crossref(Connector):
    """Crossref works resolver. See the module docstring for the citation rule."""

    source = "crossref"
    license = LICENSE
    citation = CITATION
    domains = ("api.crossref.org",)

    def __init__(self, cache: Cache | None = None, *, contact_email: str | None = None,
                 mailto_in: Literal["param", "header"] = "param"):
        super().__init__(cache)
        if mailto_in not in ("param", "header"):
            raise ValueError("mailto_in must be 'param' or 'header'")
        self.contact_email = handles.contact_email(contact_email)
        self.mailto_in = mailto_in

    @property
    def _mailto(self) -> str | None:
        return self.contact_email if self.mailto_in == "param" else None

    def headers(self) -> dict[str, str]:
        """User-Agent identifying the toolkit, with ``mailto:`` when an address is known."""
        return {"User-Agent": handles.polite_user_agent(self.contact_email)}

    @property
    def pool(self) -> str:
        """``"polite"`` when a contact address is known (sent as parameter or in the User-Agent), else ``"public"``."""
        return "polite" if self.contact_email else "public"

    def _get(self, url: str, params: dict, refresh: bool, route: Literal["single", "list"] = "single"):
        """``http.fetch`` behind a client-side limiter (network calls only; cache hits are not throttled)."""
        if not _served_from_cache(self.cache, url, params, refresh):
            limiter_for(self.pool, route).acquire()
        return http.fetch(self.source, url, params, headers=self.headers(), cache=self.cache, refresh=refresh)

    def _meta(self, request: dict, df: pd.DataFrame, resolved: bool, **extra) -> dict:
        return {
            "table": "works",
            "canonical_frame": False,
            "resolver": self.source,
            "request": request,
            "resolved": resolved,
            "returned_ids": handles.returned_ids(df),
            "polite_pool": bool(self.contact_email),
            **extra,
        }

    def _finish(self, res: Result, info: dict) -> Result:
        if info["retracted"]:
            res.add_caveat(Caveat("literature_retracted", "warn",
                                  "Crossref lists a retraction for these works (updated-by); do not cite them as evidence.",
                                  {"dois": info["retracted"]}))
        if not self.contact_email:
            res.add_caveat(_PUBLIC_POOL_CAVEAT)
        return res

    def resolve_doi(self, doi: str, *, refresh: bool = False) -> Result:
        """Resolve a Crossref DOI to a one-row works table (empty + ``literature_unresolved`` on 404)."""
        d = handles.normalize_doi(doi)
        url, params = build_work_request(d, mailto=self._mailto)
        tool = "Crossref.resolve_doi"
        try:
            payload, retrieval = self._get(url, params, refresh)
        except http.FetchError as exc:
            if not _is_not_found(exc):
                raise
            prov = Provenance(source=self.source, dataset=DATASET, endpoint=url, params=params, retrieved_at=utc_now(),
                              license=self.license, citation=self.citation, tool=tool,
                              notes=[f"not resolved: {str(exc)[:300]}"])
            df = handles.empty_works_frame()
            res = Result(data=df, provenance=[prov], meta=self._meta({"doi": d}, df, False))
            res.add_caveat(Caveat("literature_unresolved", "block",
                                  f"Crossref does not know DOI {d}; do not cite it as resolved. It may be registered "
                                  "with another agency (DataCite, mEDRA, ...): try OpenAlex before flagging the reference.",
                                  {"doi": d, "reason": str(exc)[:300]}))
            return self._finish(res, {"retracted": []})
        df = parse(payload)
        info = response_info(payload)
        prov = self.provenance(retrieval, dataset=DATASET, tool=tool,
                               notes=["Crossref metadata are revised by depositors; the payload hash pins this response."])
        res = Result(data=df, provenance=[prov], meta=self._meta({"doi": d}, df, not df.empty))
        if not df.empty and df.loc[0, "doi"] != d:
            res.add_caveat(Caveat("literature_doi_mismatch", "warn",
                                  "Crossref returned a record whose DOI differs from the one requested; confirm before citing.",
                                  {"requested": d, "returned": df.loc[0, "doi"]}))
        return self._finish(res, info)

    def search(
        self,
        query: str,
        rows: int = 20,
        *,
        field: str | None = "bibliographic",
        author: str | None = None,
        filters: Mapping[str, Any] | str | None = None,
        refresh: bool = False,
    ) -> Result:
        """Bibliographic search (``query.bibliographic`` by default); rows keep Crossref's relevance order."""
        url, params = build_search_request(query, rows, field=field, author=author, filters=filters, mailto=self._mailto)
        payload, retrieval = self._get(url, params, refresh, route="list")
        df = parse(payload)
        info = response_info(payload)
        prov = self.provenance(retrieval, dataset=DATASET, tool="Crossref.search",
                               notes=["Crossref ranking and counts change over time; the payload hash pins this response."])
        request = {k: v for k, v in params.items() if k not in ("select", "mailto")}
        res = Result(data=df, provenance=[prov],
                     meta=self._meta(request, df, not df.empty, total_count=info["total_count"]))
        res.add_caveat(_SEARCH_CAVEAT)
        if isinstance(info["total_count"], int) and info["total_count"] > rows:
            res.add_caveat(Caveat("literature_results_truncated", "info",
                                  f"Showing {len(df)} of {info['total_count']} matches; refine the query.",
                                  {"total_count": info["total_count"], "rows": rows}))
        return self._finish(res, info)

    def fetch(self, doi: str, *, refresh: bool = False) -> Result:  # Connector interface
        """Alias of :meth:`resolve_doi`."""
        return self.resolve_doi(doi, refresh=refresh)


# ---------------------------------------------------------------------------
# module-level conveniences
# ---------------------------------------------------------------------------
def resolve_doi(doi: str, *, refresh: bool = False, cache: Cache | None = None) -> Result:
    """Resolve a DOI with Crossref. Cite only DOIs present in the returned table."""
    return Crossref(cache).resolve_doi(doi, refresh=refresh)


def search(query: str, rows: int = 20, *, field: str | None = "bibliographic", author: str | None = None,
           filters: Mapping[str, Any] | str | None = None, refresh: bool = False,
           cache: Cache | None = None) -> Result:
    """Search Crossref works. Hits are candidates; cite only returned DOIs after checking the match."""
    return Crossref(cache).search(query, rows, field=field, author=author, filters=filters, refresh=refresh)
