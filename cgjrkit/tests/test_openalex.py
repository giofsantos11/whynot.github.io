"""Unit tests for the OpenAlex resolver (synthetic fixtures, no network).

Fixtures ``tests/fixtures/openalex_*_synthetic.json`` follow the Work and list
schemas of the official OpenAPI spec (developers.openalex.org/api-reference/
openapi.json; see docs/apis/literature.md). Every value is synthetic: IDs
``W100000000x``, the Crossref test prefix ``10.5555`` and titles starting
with "SYNTHETIC TEST RECORD".
"""

import json

import pandas as pd
import pytest

from cgjrkit.connectors import handles, openalex
from cgjrkit.connectors.openalex import OpenAlex, OpenAlexError
from cgjrkit.core import http
from cgjrkit.core.cache import Cache

SELECT = ",".join(openalex.SELECT_FIELDS)


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv(openalex.KEY_ENV, raising=False)
    monkeypatch.delenv(handles.CONTACT_ENV, raising=False)


def _payload(fixtures_dir, name: str) -> bytes:
    return (fixtures_dir / name).read_bytes()


def _seed(url: str, params: dict, payload: bytes) -> None:
    Cache().put(OpenAlex.source, url, params, payload)


# -- request builders ---------------------------------------------------------------------------------
def test_build_work_request_doi_and_id():
    url, params = openalex.build_work_request("https://doi.org/10.3386/W17143")
    assert url == "https://api.openalex.org/works/doi:10.3386/w17143"
    assert params == {"select": SELECT}
    url, params = openalex.build_work_request("https://openalex.org/W2741809807", select=None)
    assert (url, params) == ("https://api.openalex.org/works/W2741809807", {})
    url, _ = openalex.build_work_request("10.5555/a#b")
    assert url == "https://api.openalex.org/works/doi:10.5555/a%23b"
    with pytest.raises(handles.IdentifierError):
        openalex.build_work_request("hdl:10986/11843")  # OKR handles are not OpenAlex IDs
    with pytest.raises(handles.IdentifierError):
        openalex.build_work_request("World Bank 2023c")


def test_format_filters():
    assert openalex.format_filters(None) is None
    assert openalex.format_filters({}) is None
    flt = {"type": ["article", "preprint"], "publication_year": "2010-2012", "is_oa": True,
           "cited_by_count": ">100", "language": None}
    assert openalex.format_filters(flt) == \
        "cited_by_count:>100,is_oa:true,publication_year:2010-2012,type:article|preprint"
    assert openalex.format_filters({"type": {"preprint", "article"}}) == "type:article|preprint"
    assert openalex.format_filters(" publication_year:2011,type:article ") == "publication_year:2011,type:article"
    with pytest.raises(ValueError):
        openalex.format_filters({"title": "a, b"})  # comma would split the filter
    with pytest.raises(ValueError):
        openalex.format_filters({"Bad Key": 1})
    with pytest.raises(ValueError):
        openalex.format_filters({"doi": [f"10.5555/{i}" for i in range(101)]})
    with pytest.raises(ValueError):
        openalex.format_filters({"type": []})
    with pytest.raises(ValueError):
        openalex.format_filters("publication_year")


def test_build_search_request():
    url, params = openalex.build_search_request("  structural   change ", {"publication_year": 2011}, 10)
    assert url == "https://api.openalex.org/works"
    assert params == {"search": "structural change", "filter": "publication_year:2011", "per_page": 10, "select": SELECT}
    _, params = openalex.build_search_request(None, "type:article", 100, page=3, sort="cited_by_count:desc", select=None)
    assert params == {"filter": "type:article", "per_page": 100, "page": 3, "sort": "cited_by_count:desc"}
    for bad in (0, 101, True, 2.5):
        with pytest.raises(ValueError):
            openalex.build_search_request("x", per_page=bad)
    with pytest.raises(ValueError):
        openalex.build_search_request("x", page=0)
    with pytest.raises(ValueError):
        openalex.build_search_request("   ")
    with pytest.raises(ValueError):
        openalex.build_search_request("x " * 2000)


def test_build_dois_request():
    url, params, norm = openalex.build_dois_request(["10.5555/B", "https://doi.org/10.5555/a", "doi:10.5555/b"])
    assert url == "https://api.openalex.org/works"
    assert norm == ["10.5555/b", "10.5555/a"]
    assert params == {"filter": "doi:10.5555/b|10.5555/a", "per_page": 100, "select": SELECT}
    with pytest.raises(ValueError):
        openalex.build_dois_request([])
    with pytest.raises(ValueError):
        openalex.build_dois_request([f"10.5555/{i}" for i in range(101)])
    with pytest.raises(ValueError):
        openalex.build_dois_request(["10.5555/a|b"])


# -- parsers --------------------------------------------------------------------------------------------
def test_parse_singleton(fixtures_dir):
    df = openalex.parse(_payload(fixtures_dir, "openalex_work_synthetic.json"))
    handles.validate_works_frame(df)
    assert len(df) == 1
    row = df.iloc[0].to_dict()
    assert row == {
        "openalex_id": "W1000000001",
        "doi": "10.5555/synthetic.0001",
        "title": "SYNTHETIC TEST RECORD: A Work About Structural Change",
        "year": 2000,
        "authors": "Synthetic Author One; Synthetic Author Two",
        "venue": "Synthetic Journal of Tests",
        "type": "article",
        "cited_by_count": 100,
        "url": "https://doi.org/10.5555/synthetic.0001",
        "is_oa": True,
    }


def test_parse_list_edge_cases(fixtures_dir):
    df = openalex.parse(_payload(fixtures_dir, "openalex_works_list_synthetic.json"))
    handles.validate_works_frame(df)
    assert df["openalex_id"].tolist() == ["W1000000001", "W1000000002", "W1000000003"]  # rank order kept
    wp = df.iloc[1]
    assert wp["doi"] is None
    assert wp["title"] == "SYNTHETIC TEST RECORD: Working Paper Without DOI"  # display_name fallback
    assert wp["authors"] == "Synthetic Author Three"  # raw_author_name fallback, whitespace collapsed
    assert wp["venue"] is None and wp["url"] == "https://example.org/synthetic/wp-0002"
    assert not pd.isna(wp["is_oa"]) and not wp["is_oa"]
    chapter = df.iloc[2]
    assert chapter["doi"] == "10.5555/synthetic.0003"  # lower-cased
    assert pd.isna(chapter["year"]) and chapter["authors"] is None and chapter["venue"] is None
    assert chapter["url"] == "https://doi.org/10.5555/synthetic.0003"
    assert pd.isna(chapter["is_oa"])


def test_parse_empty_and_errors(fixtures_dir):
    empty = openalex.parse(_payload(fixtures_dir, "openalex_works_empty_synthetic.json"))
    assert empty.empty and list(empty.columns) == handles.WORKS_COLUMNS
    with pytest.raises(OpenAlexError, match="Invalid filter"):
        openalex.parse(_payload(fixtures_dir, "openalex_error_synthetic.json"))
    with pytest.raises(OpenAlexError):
        openalex.parse(b"<html>not json</html>")
    with pytest.raises(OpenAlexError):
        openalex.parse(b'{"unexpected": true}')
    with pytest.raises(OpenAlexError):
        openalex.parse(b"[1, 2]")


def test_response_info(fixtures_dir):
    info = openalex.response_info(_payload(fixtures_dir, "openalex_works_list_synthetic.json"))
    assert info["total_count"] == 1000 and info["returned"] == 3 and info["cost_usd"] == 0.001
    assert info["retracted"] == ["W1000000002"] and info["authors_truncated"] == []
    single = openalex.response_info(_payload(fixtures_dir, "openalex_work_synthetic.json"))
    assert single["singleton"] and single["updated_date"] == "2000-01-01T00:00:00.000000"


def test_authors_truncated_flag(fixtures_dir):
    work = json.loads(_payload(fixtures_dir, "openalex_work_synthetic.json"))
    work["authorships"] = [work["authorships"][0]] * openalex.MAX_AUTHORSHIPS
    assert openalex.response_info(json.dumps(work).encode())["authors_truncated"] == ["W1000000001"]


# -- connector wiring (cache-seeded, offline) -----------------------------------------------------------
def test_resolve_doi_from_cache(fixtures_dir):
    payload = _payload(fixtures_dir, "openalex_work_synthetic.json")
    url, params = openalex.build_work_request("10.5555/synthetic.0001")
    _seed(url, params, payload)
    res = OpenAlex().resolve_doi("https://doi.org/10.5555/SYNTHETIC.0001")
    assert res.data.loc[0, "openalex_id"] == "W1000000001"
    assert res.meta["resolved"] and not res.meta["canonical_frame"] and res.meta["table"] == "works"
    assert res.meta["returned_ids"] == ["W1000000001", "10.5555/synthetic.0001"]
    assert res.meta["request"] == {"doi": "10.5555/synthetic.0001"}
    assert res.meta["record_updated"] == "2000-01-01T00:00:00.000000"
    prov = res.provenance[0]
    assert prov.source == "openalex" and prov.dataset == "works" and prov.tool == "OpenAlex.resolve_doi"
    assert prov.endpoint == url and prov.params == params
    assert prov.payload_sha256 and prov.license.startswith("CC0")
    assert res.caveats == []  # singletons are free; no key caveat


def test_resolve_by_openalex_id(fixtures_dir):
    payload = _payload(fixtures_dir, "openalex_work_synthetic.json")
    url, params = openalex.build_work_request("W1000000001")
    _seed(url, params, payload)
    res = OpenAlex().resolve("https://openalex.org/W1000000001")
    assert res.meta["request"] == {"openalex": "W1000000001"} and res.provenance[0].tool == "OpenAlex.resolve"


def test_resolve_doi_mismatch_is_flagged(fixtures_dir):
    payload = _payload(fixtures_dir, "openalex_work_synthetic.json")
    url, params = openalex.build_work_request("10.5555/other.9999")
    _seed(url, params, payload)
    res = OpenAlex().resolve_doi("10.5555/other.9999")
    codes = {c.code: c for c in res.caveats}
    assert codes["literature_doi_mismatch"].severity == "warn"
    assert codes["literature_doi_mismatch"].evidence == {"requested": "10.5555/other.9999",
                                                         "returned": "10.5555/synthetic.0001"}


def test_api_key_goes_in_header_only(monkeypatch, fixtures_dir):
    monkeypatch.setenv(openalex.KEY_ENV, "synthetic-key-123")
    monkeypatch.setenv(handles.CONTACT_ENV, "analyst@example.org")
    calls = []

    def fake_fetch(source, url, params=None, **kw):
        calls.append({"source": source, "url": url, "params": params, **kw})
        rec = Cache().put(source, url, params, _payload(fixtures_dir, "openalex_work_synthetic.json"))
        return _payload(fixtures_dir, "openalex_work_synthetic.json"), rec

    monkeypatch.setattr(http, "fetch", fake_fetch)
    res = OpenAlex().resolve_doi("10.5555/synthetic.0001")
    call = calls[0]
    assert call["headers"]["Authorization"] == "Bearer synthetic-key-123"
    assert "mailto:analyst@example.org" in call["headers"]["User-Agent"]
    assert "synthetic-key-123" not in json.dumps(call["params"])
    assert "synthetic-key-123" not in json.dumps([p.to_dict() for p in res.provenance])
    assert "synthetic-key-123" not in json.dumps(res.meta) and res.meta["api_key_sent_as_header"]


def test_not_found_is_unresolved_not_an_error(monkeypatch):
    def fake_fetch(source, url, params=None, **kw):
        raise http.FetchError(f"404 from {url}: Work not found")

    monkeypatch.setattr(http, "fetch", fake_fetch)
    res = OpenAlex().resolve_doi("10.5555/missing.0000")
    assert res.data.empty and list(res.data.columns) == handles.WORKS_COLUMNS
    assert not res.meta["resolved"] and res.meta["returned_ids"] == []
    assert res.blocked and res.caveats[0].code == "literature_unresolved"
    prov = res.provenance[0]
    assert prov.payload_sha256 == "" and prov.notes[0].startswith("not resolved: 404")
    assert prov.endpoint == "https://api.openalex.org/works/doi:10.5555/missing.0000"


def test_other_failures_are_raised(monkeypatch):
    # offline with an empty cache: a network failure must not look like "not found"
    with pytest.raises(http.NetworkBlocked):
        OpenAlex().resolve_doi("10.5555/synthetic.0001")

    def fake_fetch(source, url, params=None, **kw):
        raise http.FetchError(f"400 from {url}: bad request")

    monkeypatch.setattr(http, "fetch", fake_fetch)
    with pytest.raises(http.FetchError):
        OpenAlex().resolve_doi("10.5555/synthetic.0001")


def test_search_works_from_cache(fixtures_dir):
    payload = _payload(fixtures_dir, "openalex_works_list_synthetic.json")
    url, params = openalex.build_search_request("synthetic structural change", {"publication_year": "2000-2002"}, 3)
    _seed(url, params, payload)
    res = openalex.search_works("synthetic structural change", {"publication_year": "2000-2002"}, 3)
    assert len(res.data) == 3
    assert res.meta["total_count"] == 1000 and res.meta["cost_usd"] == 0.001
    assert res.meta["request"] == {"search": "synthetic structural change", "filter": "publication_year:2000-2002",
                                   "per_page": 3}
    assert res.meta["returned_ids"] == ["W1000000001", "10.5555/synthetic.0001", "W1000000002", "W1000000003",
                                        "10.5555/synthetic.0003"]
    sev = {c.code: c.severity for c in res.caveats}
    assert sev == {"literature_search_candidates": "info", "literature_retracted": "warn",
                   "literature_results_truncated": "info", "openalex_no_api_key": "info"}
    assert not res.blocked
    assert res.source_line() == "Source: OpenAlex."


def test_resolve_dois_batch(fixtures_dir):
    payload = _payload(fixtures_dir, "openalex_works_dois_synthetic.json")
    requested = ["10.5555/synthetic.0003", "10.5555/SYNTHETIC.0001", "10.5555/missing.9999"]
    url, params, _ = openalex.build_dois_request(requested)
    _seed(url, params, payload)
    res = OpenAlex(api_key="synthetic-key").resolve_dois(requested)
    assert res.data["doi"].tolist() == ["10.5555/synthetic.0003", "10.5555/synthetic.0001"]  # input order
    assert res.meta["unresolved"] == ["10.5555/missing.9999"] and not res.meta["resolved"]
    blocking = [c for c in res.caveats if c.severity == "block"]
    assert len(blocking) == 1 and blocking[0].evidence == {"dois": ["10.5555/missing.9999"]}
    assert "openalex_no_api_key" not in {c.code for c in res.caveats}


def _work(wid: str, doi: str) -> dict:
    """A synthetic Work object (official Work schema fields; obviously fake values)."""
    return {"id": f"https://openalex.org/{wid}", "doi": f"https://doi.org/{doi}", "title": "SYNTHETIC TEST RECORD",
            "display_name": "SYNTHETIC TEST RECORD", "publication_year": 2099, "type": "article", "cited_by_count": 0,
            "authorships": [], "primary_location": None, "open_access": {"is_oa": False},
            "is_retracted": False, "updated_date": "2099-01-01T00:00:00"}


_DOIS = ["10.5555/synthetic.a", "10.5555/synthetic.b"]


def _seed_truncated_batch():
    """Batch answer where duplicate records for DOI a fill the page: count 3, two results, both for a."""
    url, params, _ = openalex.build_dois_request(_DOIS)
    assert params["per_page"] == openalex.MAX_PER_PAGE
    payload = {"meta": {"count": 3, "page": 1, "per_page": 2, "cost_usd": 0.0001},
               "results": [_work("W9000000001", _DOIS[0]), _work("W9000000002", _DOIS[0])]}
    _seed(url, params, json.dumps(payload).encode())


def test_resolve_dois_truncated_batch_falls_back_to_singletons():
    _seed_truncated_batch()
    url, params = openalex.build_work_request(_DOIS[1])
    _seed(url, params, json.dumps(_work("W9000000003", _DOIS[1])).encode())
    res = OpenAlex(api_key="synthetic-key").resolve_dois(_DOIS)
    assert res.data["openalex_id"].tolist() == ["W9000000001", "W9000000002", "W9000000003"]
    assert res.meta["unresolved"] == [] and res.meta["unchecked"] == [] and res.meta["resolved"]
    assert res.meta["checked_individually"] == [_DOIS[1]] and not res.blocked
    assert [p.tool for p in res.provenance] == ["OpenAlex.resolve_dois", "OpenAlex.resolve_doi"]
    codes = {c.code: c for c in res.caveats}
    assert codes["literature_results_truncated"].severity == "info"
    assert codes["openalex_duplicate_records"].evidence == {"records": {_DOIS[0]: ["W9000000001", "W9000000002"]}}
    handles.validate_works_frame(res.data)


def test_resolve_dois_truncated_batch_singleton_404_is_unresolved(monkeypatch):
    _seed_truncated_batch()
    single_url, _ = openalex.build_work_request(_DOIS[1])
    original = http.fetch

    def fake_fetch(source, url, params=None, **kw):
        if url == single_url:
            raise http.FetchError(f"404 from {url}: Work not found")
        return original(source, url, params, **kw)

    monkeypatch.setattr(http, "fetch", fake_fetch)
    res = OpenAlex(api_key="synthetic-key").resolve_dois(_DOIS)
    assert res.meta["unresolved"] == [_DOIS[1]] and res.blocked
    assert {c.code: c for c in res.caveats}["literature_unresolved"].evidence == {"dois": [_DOIS[1]]}


def test_resolve_dois_truncated_batch_offline_is_unchecked_not_unresolved():
    _seed_truncated_batch()  # singleton for DOI b not cached; offline mode -> NetworkBlocked
    res = OpenAlex(api_key="synthetic-key").resolve_dois(_DOIS)
    assert res.meta["unresolved"] == [] and res.meta["unchecked"] == [_DOIS[1]] and not res.meta["resolved"]
    assert not res.blocked and _DOIS[1] not in res.meta["returned_ids"]
    trunc = {c.code: c for c in res.caveats}["literature_results_truncated"]
    assert trunc.severity == "warn" and trunc.evidence["unchecked"] == [_DOIS[1]]


def test_rate_limit_with_key_names_the_header_caveat(monkeypatch):
    def fake_fetch(source, url, params=None, **kw):
        raise http.FetchError(f"giving up on {url} after 3 attempts: 429 from {url}")

    monkeypatch.setattr(http, "fetch", fake_fetch)
    with pytest.raises(openalex.OpenAlexRateLimitError, match="api_key query parameter") as err:
        OpenAlex(api_key="synthetic-key-123").search_works("synthetic")
    assert "synthetic-key-123" not in str(err.value)
    with pytest.raises(http.FetchError) as plain:  # without a key: the core error, unchanged
        OpenAlex(api_key="").search_works("synthetic")
    assert not isinstance(plain.value, openalex.OpenAlexRateLimitError)


def test_fetch_alias_and_module_function(fixtures_dir):
    payload = _payload(fixtures_dir, "openalex_work_synthetic.json")
    url, params = openalex.build_work_request("10.5555/synthetic.0001")
    _seed(url, params, payload)
    a = OpenAlex().fetch("10.5555/synthetic.0001")
    b = openalex.resolve_doi("doi:10.5555/synthetic.0001")
    assert a.data_sha256 == b.data_sha256
    assert OpenAlex.domains == ("api.openalex.org",)
