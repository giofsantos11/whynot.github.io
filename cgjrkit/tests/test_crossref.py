"""Unit tests for the Crossref resolver (synthetic fixtures, no network).

Fixtures ``tests/fixtures/crossref_*_synthetic.json`` follow the documented
envelope (``status``, ``message-type`` "work"/"work-list", ``message``) and the
Work fields of Crossref's api_format.md (see docs/apis/literature.md). Every
value is synthetic: DOIs use the Crossref test prefix ``10.5555`` and titles
start with "SYNTHETIC TEST RECORD".
"""

import json

import pandas as pd
import pytest

from cgjrkit.connectors import crossref, handles
from cgjrkit.connectors.crossref import Crossref, CrossrefError
from cgjrkit.core import http
from cgjrkit.core.cache import Cache, Retrieval

EMAIL = "analyst@example.org"
SELECT = ",".join(crossref.SEARCH_SELECT)


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv(handles.CONTACT_ENV, raising=False)


class _FakeClock:
    """Deterministic clock for the rate limiters: sleeping advances time instead of waiting."""

    def __init__(self):
        self.now, self.slept = 0.0, []

    def __call__(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.slept.append(round(s, 6))
        self.now += s


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Fresh process-wide limiters on a fake clock for every test (no real sleeping)."""
    clk = _FakeClock()
    monkeypatch.setattr(crossref, "_LIMITERS", {
        key: crossref.MinuteLimiter(limit=n, window_s=1.0, clock=clk, sleep=clk.sleep)
        for key, n in crossref.RATE_LIMITS.items()})
    return clk


def _payload(fixtures_dir, name: str) -> bytes:
    return (fixtures_dir / name).read_bytes()


def _seed(url: str, params: dict, payload: bytes) -> None:
    Cache().put(Crossref.source, url, params, payload)


# -- request builders ---------------------------------------------------------------------------------
def test_build_work_request():
    assert crossref.build_work_request("https://doi.org/10.3386/W17143") == \
        ("https://api.crossref.org/works/10.3386/w17143", {})
    assert crossref.build_work_request("10.3386/w17143", mailto=EMAIL) == \
        ("https://api.crossref.org/works/10.3386/w17143", {"mailto": EMAIL})
    url, _ = crossref.build_work_request("10.1016/S0140-6736(20)30183-5")
    assert url == "https://api.crossref.org/works/10.1016/s0140-6736(20)30183-5"
    url, _ = crossref.build_work_request("10.5555/a#b?c")
    assert url == "https://api.crossref.org/works/10.5555/a%23b%3Fc"
    with pytest.raises(handles.IdentifierError):
        crossref.build_work_request("hdl:10986/11843")


def test_format_filters():
    assert crossref.format_filters(None) is None
    assert crossref.format_filters({"until-pub-date": "2011", "from-pub-date": "2011", "has-orcid": True,
                                    "type": ["report", "journal-article"], "member": None}) == \
        "from-pub-date:2011,has-orcid:true,type:report,type:journal-article,until-pub-date:2011"
    assert crossref.format_filters("type:report") == "type:report"
    with pytest.raises(ValueError):
        crossref.format_filters({"type": "a,b"})
    with pytest.raises(ValueError):
        crossref.format_filters({"Bad_Name": 1})
    with pytest.raises(ValueError):
        crossref.format_filters({"type": []})
    with pytest.raises(ValueError):
        crossref.format_filters("type")


def test_build_search_request():
    url, params = crossref.build_search_request("  globalization  structural change ", 5)
    assert url == "https://api.crossref.org/works"
    assert params == {"query.bibliographic": "globalization structural change", "rows": 5, "select": SELECT}
    _, params = crossref.build_search_request("x", 1000, field=None, author="Rodrik", filters={"from-pub-date": "2011"},
                                              mailto=EMAIL, select=None)
    assert params == {"query": "x", "query.author": "Rodrik", "filter": "from-pub-date:2011", "rows": 1000,
                      "mailto": EMAIL}
    _, params = crossref.build_search_request("", author="Rodrik")
    assert "query.bibliographic" not in params and params["query.author"] == "Rodrik"
    for bad in (0, 1001, True):
        with pytest.raises(ValueError):
            crossref.build_search_request("x", bad)
    with pytest.raises(ValueError):
        crossref.build_search_request("x", field="title")  # query.title was retired by Crossref
    with pytest.raises(ValueError):
        crossref.build_search_request("  ")
    with pytest.raises(ValueError):
        crossref.build_search_request("x " * 2000)


# -- parsers --------------------------------------------------------------------------------------------
def test_parse_singleton(fixtures_dir):
    payload = _payload(fixtures_dir, "crossref_work_synthetic.json")
    df = crossref.parse(payload)
    handles.validate_works_frame(df)
    row = df.iloc[0].to_dict()
    assert pd.isna(row.pop("is_oa"))  # Crossref has no open-access flag
    assert row == {
        "openalex_id": None,
        "doi": "10.5555/synthetic.0001",
        "title": "SYNTHETIC TEST RECORD: A Work About Structural Change: Evidence From Made-Up Data",
        "year": 2000,
        "authors": "Synthetic Author One; Synthetic Author Two; Synthetic Consortium",
        "venue": "Synthetic Institute of Tests",  # institution name when container-title is empty
        "type": "report",
        "cited_by_count": 50,
        "url": "https://doi.org/10.5555/synthetic.0001",
    }
    assert "SYNTHETIC ABSTRACT" not in df.to_csv()  # abstracts are never read


def test_parse_list_edge_cases(fixtures_dir):
    df = crossref.parse(_payload(fixtures_dir, "crossref_works_list_synthetic.json"))
    handles.validate_works_frame(df)
    assert df["doi"].tolist() == ["10.5555/synthetic.0001", "10.5555/synthetic.0002", "10.5555/synthetic.0003"]
    assert df.loc[0, "venue"] == "Synthetic Journal of Tests"
    assert df.loc[1, "year"] == 2001  # issued is [[null]]; falls back to published-online
    assert df.loc[1, "venue"] == "Synthetic Preprint Group"  # group-title fallback
    assert df.loc[2, "title"] is None and pd.isna(df.loc[2, "year"]) and df.loc[2, "authors"] is None
    assert df.loc[2, "venue"] == "Synthetic Publisher"  # publisher as last resort
    assert df["openalex_id"].isna().all() and df["is_oa"].isna().all()


def test_parse_errors(fixtures_dir):
    with pytest.raises(CrossrefError, match="no such filter"):
        crossref.parse(_payload(fixtures_dir, "crossref_error_synthetic.json"))
    with pytest.raises(CrossrefError):
        crossref.parse(b"Resource not found.")
    with pytest.raises(CrossrefError, match="message-type"):
        crossref.parse(json.dumps({"status": "ok", "message-type": "member", "message": {}}).encode())
    with pytest.raises(CrossrefError):
        crossref.parse(b'["ok"]')


def test_response_info(fixtures_dir):
    info = crossref.response_info(_payload(fixtures_dir, "crossref_works_list_synthetic.json"))
    assert info == {"total_count": 500, "returned": 3, "retracted": ["10.5555/synthetic.0002"]}
    single = crossref.response_info(_payload(fixtures_dir, "crossref_work_synthetic.json"))
    assert single["total_count"] == 1 and single["retracted"] == []


# -- connector wiring (cache-seeded, offline) -----------------------------------------------------------
def test_resolve_doi_polite_param(monkeypatch, fixtures_dir):
    monkeypatch.setenv(handles.CONTACT_ENV, EMAIL)
    url, params = crossref.build_work_request("10.5555/synthetic.0001", mailto=EMAIL)
    _seed(url, params, _payload(fixtures_dir, "crossref_work_synthetic.json"))
    res = crossref.resolve_doi("doi:10.5555/SYNTHETIC.0001")
    assert res.data.loc[0, "doi"] == "10.5555/synthetic.0001"
    assert res.meta["resolved"] and res.meta["polite_pool"] and not res.meta["canonical_frame"]
    assert res.meta["returned_ids"] == ["10.5555/synthetic.0001"]
    prov = res.provenance[0]
    assert prov.source == "crossref" and prov.tool == "Crossref.resolve_doi"
    assert prov.params == {"mailto": EMAIL} and prov.payload_sha256
    assert res.caveats == []


def test_resolve_doi_header_mode_keeps_email_out_of_cache_key(monkeypatch, fixtures_dir):
    calls = []

    def fake_fetch(source, url, params=None, **kw):
        calls.append({"params": params, **kw})
        payload = _payload(fixtures_dir, "crossref_work_synthetic.json")
        return payload, Cache().put(source, url, params, payload)

    monkeypatch.setattr(http, "fetch", fake_fetch)
    res = Crossref(contact_email=EMAIL, mailto_in="header").resolve_doi("10.5555/synthetic.0001")
    assert calls[0]["params"] == {}
    assert f"mailto:{EMAIL}" in calls[0]["headers"]["User-Agent"]
    assert EMAIL not in json.dumps([p.to_dict() for p in res.provenance])
    with pytest.raises(ValueError):
        Crossref(mailto_in="query")  # type: ignore[arg-type]


def test_public_pool_caveat(fixtures_dir):
    url, params = crossref.build_work_request("10.5555/synthetic.0001")
    _seed(url, params, _payload(fixtures_dir, "crossref_work_synthetic.json"))
    res = Crossref().resolve_doi("10.5555/synthetic.0001")
    assert [c.code for c in res.caveats] == ["crossref_public_pool"] and not res.meta["polite_pool"]


def test_invalid_contact_email(monkeypatch):
    monkeypatch.setenv(handles.CONTACT_ENV, "nobody")
    with pytest.raises(ValueError):
        Crossref()


def test_not_found_is_unresolved(monkeypatch):
    def fake_fetch(source, url, params=None, **kw):
        raise http.FetchError(f"404 from {url}: Resource not found.")

    monkeypatch.setattr(http, "fetch", fake_fetch)
    res = Crossref(contact_email=EMAIL).resolve_doi("10.34894/qt5bcc")  # e.g. a DataCite DOI
    assert res.data.empty and res.blocked and not res.meta["resolved"]
    cav = res.caveats[0]
    assert cav.code == "literature_unresolved" and "DataCite" in cav.message
    assert res.provenance[0].payload_sha256 == ""


def test_other_failures_are_raised(monkeypatch):
    with pytest.raises(http.NetworkBlocked):  # offline, empty cache
        Crossref().resolve_doi("10.5555/synthetic.0001")

    def fake_fetch(source, url, params=None, **kw):
        raise http.FetchError(f"400 from {url}: bad")

    monkeypatch.setattr(http, "fetch", fake_fetch)
    with pytest.raises(http.FetchError):
        Crossref().search("x")


def test_doi_mismatch_is_flagged(fixtures_dir):
    url, params = crossref.build_work_request("10.5555/other.9999")
    _seed(url, params, _payload(fixtures_dir, "crossref_work_synthetic.json"))
    res = Crossref().resolve_doi("10.5555/other.9999")
    assert "literature_doi_mismatch" in {c.code for c in res.caveats}


def test_search_from_cache(fixtures_dir):
    url, params = crossref.build_search_request("synthetic structural change", 3, mailto=EMAIL)
    _seed(url, params, _payload(fixtures_dir, "crossref_works_list_synthetic.json"))
    res = crossref.Crossref(contact_email=EMAIL).search("synthetic structural change", 3)
    assert len(res.data) == 3 and res.meta["total_count"] == 500
    assert res.meta["request"] == {"query.bibliographic": "synthetic structural change", "rows": 3}
    sev = {c.code: c.severity for c in res.caveats}
    assert sev == {"literature_search_candidates": "info", "literature_results_truncated": "info",
                   "literature_retracted": "warn"}
    assert res.source_line() == "Source: Crossref."


def test_fetch_alias(fixtures_dir):
    url, params = crossref.build_work_request("10.5555/synthetic.0001")
    _seed(url, params, _payload(fixtures_dir, "crossref_work_synthetic.json"))
    assert Crossref().fetch("10.5555/synthetic.0001").data.loc[0, "type"] == "report"
    assert Crossref.domains == ("api.crossref.org",)


# -- client-side rate limiting -----------------------------------------------------------------------
def test_rate_limits_by_pool_and_route(fixtures_dir, monkeypatch, clock):
    calls = []

    def fake_fetch(source, url, params=None, **kw):  # a network answer that is not stored, so no call is a cache hit
        calls.append(url)
        return _payload(fixtures_dir, "crossref_works_list_synthetic.json"), Retrieval(
            key="synthetic", sha256="0" * 64, retrieved_at="2099-01-01T00:00:00Z", url=url, params=params or {},
            source=source)

    monkeypatch.setattr(http, "fetch", fake_fetch)
    public = Crossref()  # public pool: 1 list request per second
    for _ in range(3):
        public.search("synthetic structural change", rows=2)
    assert len(calls) == 3 and clock.slept == [1.0, 1.0]
    polite = Crossref(contact_email=EMAIL, mailto_in="header")  # polite pool: 3 list requests per second
    assert polite.pool == "polite" and public.pool == "public"
    clock.slept.clear()
    for _ in range(4):
        polite.search("synthetic structural change", rows=2)
    assert clock.slept == [1.0]
    assert crossref.RATE_LIMITS == {("polite", "single"): 10, ("polite", "list"): 3,
                                    ("public", "single"): 5, ("public", "list"): 1}


def test_cache_hits_are_not_throttled(fixtures_dir, clock):
    url, params = crossref.build_search_request("synthetic structural change", 2)
    _seed(url, params, _payload(fixtures_dir, "crossref_works_list_synthetic.json"))
    for _ in range(5):
        Crossref().search("synthetic structural change", rows=2)
    assert clock.slept == [] and len(crossref._LIMITERS[("public", "list")]._calls) == 0
