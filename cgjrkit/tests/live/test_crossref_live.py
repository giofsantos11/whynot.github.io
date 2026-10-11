"""Live tests for the Crossref resolver.

Run with ``CGJRKIT_LIVE=1 pytest -m live tests/live/test_crossref_live.py``
once ``api.crossref.org`` (and ``api.openalex.org`` for the cross-check) are
allowlisted. Set ``CGJRKIT_CONTACT_EMAIL`` to a real address to use the polite
pool. Each test confirms an assumption listed in ``docs/apis/literature.md``.

Replication target: McMillan and Rodrik (2011), NBER Working Paper 17143,
DOI 10.3386/w17143, cited in the Kenya CGJR (shift-share method, Fig 35).
"""

import os

import pytest

from cgjrkit.connectors import crossref, handles
from cgjrkit.connectors.crossref import Crossref
from cgjrkit.connectors.openalex import OpenAlex

pytestmark = pytest.mark.live

DOI = "10.3386/w17143"
MISSING = "10.5555/cgjrkit-live-test-does-not-exist-0000"


def test_resolve_mcmillan_rodrik_2011():
    res = Crossref().resolve_doi(DOI, refresh=True)
    assert res.meta["resolved"] and len(res.data) == 1
    row = res.data.iloc[0]
    assert row["doi"] == DOI
    title = row["title"].lower()
    assert "globalization" in title and "structural change" in title
    assert row["year"] == 2011
    assert "McMillan" in row["authors"] and "Rodrik" in row["authors"]
    # UNVERIFIED: which field carries "National Bureau of Economic Research" for NBER working papers
    assert row["venue"], "no container-title, institution, group-title or publisher in the record"
    assert row["type"], "Crossref type missing"


def test_unknown_doi_is_404_unresolved():
    res = Crossref().resolve_doi(MISSING, refresh=True)
    assert not res.meta["resolved"] and res.blocked


def test_bibliographic_search_finds_mcmillan_rodrik():
    res = crossref.search("McMillan Rodrik 2011 Globalization, Structural Change and Productivity Growth",
                          rows=10, refresh=True)
    assert DOI in res.data["doi"].dropna().tolist(), res.data[["doi", "title", "year"]].to_string()
    assert isinstance(res.meta["total_count"], int)


def test_search_select_and_filters_accepted():
    """UNVERIFIED: the select list and from/until-pub-date filters are accepted together (no 400)."""
    res = crossref.search("Globalization Structural Change Productivity Growth", rows=5, author="Rodrik",
                          filters={"from-pub-date": "2011", "until-pub-date": "2011"}, refresh=True)
    assert (res.data["year"].dropna() == 2011).all()


@pytest.mark.skipif(not os.environ.get(handles.CONTACT_ENV), reason=f"set {handles.CONTACT_ENV} for the polite pool")
def test_polite_pool_mailto_param_accepted():
    res = Crossref(mailto_in="param").resolve_doi(DOI, refresh=True)
    assert res.meta["polite_pool"] and res.provenance[0].params.get("mailto")


def test_cross_check_with_openalex():
    """Crossref and OpenAlex agree on title, year and authors for the replication DOI (ledger rule 3)."""
    cr = Crossref().resolve_doi(DOI).data.iloc[0]
    oa = OpenAlex().resolve_doi(DOI).data.iloc[0]
    assert cr["year"] == oa["year"] == 2011
    def norm(s: str) -> str:
        return "".join(ch for ch in s.lower() if ch.isalnum())

    assert norm(oa["title"]).startswith(norm(cr["title"])[:40])
    for name in ("McMillan", "Rodrik"):
        assert name in cr["authors"] and name in oa["authors"]
