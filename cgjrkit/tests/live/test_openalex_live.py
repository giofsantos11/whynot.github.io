"""Live tests for the OpenAlex resolver.

Run with ``CGJRKIT_LIVE=1 pytest -m live tests/live/test_openalex_live.py``
once ``api.openalex.org`` is allowlisted. Set ``CGJRKIT_OPENALEX_KEY`` for the
key test (it is skipped otherwise). Each test confirms an assumption listed as
UNVERIFIED in ``docs/apis/literature.md``.

Replication target: the Kenya CGJR cites McMillan and Rodrik (2011),
"Globalization, Structural Change and Productivity Growth", NBER Working
Paper 17143, DOI 10.3386/w17143 (method behind Kenya Fig 35).
"""

import json
import os
import re

import pytest

from cgjrkit.connectors import openalex
from cgjrkit.connectors.openalex import OpenAlex
from cgjrkit.core import http

pytestmark = pytest.mark.live

DOI = "10.3386/w17143"
MISSING = "10.5555/cgjrkit-live-test-does-not-exist-0000"


def _check_mcmillan_rodrik(row):
    assert row["doi"] == DOI
    assert re.fullmatch(r"W\d+", row["openalex_id"])
    title = row["title"].lower()
    assert "globalization" in title and "structural change" in title and "productivity" in title
    assert row["year"] == 2011
    assert "McMillan" in row["authors"] and "Rodrik" in row["authors"]
    assert row["url"] == "https://doi.org/10.3386/w17143"


def test_resolve_mcmillan_rodrik_2011():
    """Singleton /works/doi:<doi> with select= works and maps onto the works table."""
    res = OpenAlex().resolve_doi(DOI, refresh=True)
    assert res.meta["resolved"] and len(res.data) == 1
    _check_mcmillan_rodrik(res.data.iloc[0])
    assert res.provenance[0].payload_sha256 and res.provenance[0].license.startswith("CC0")
    assert not res.blocked


def test_keyless_singleton_is_allowed():
    """UNVERIFIED: singleton look-ups work without a key (docs: keyless budget $0.10/day, singletons free)."""
    res = OpenAlex(api_key="").resolve_doi(DOI, refresh=True)
    assert res.meta["resolved"] and not res.meta["api_key_sent_as_header"]


def test_unknown_doi_is_404_unresolved():
    """UNVERIFIED: an unknown DOI answers 404 (mapped to an empty table + blocking caveat)."""
    res = OpenAlex().resolve_doi(MISSING, refresh=True)
    assert not res.meta["resolved"] and res.data.empty
    assert any(c.code == "literature_unresolved" and c.severity == "block" for c in res.caveats)


def test_search_finds_mcmillan_rodrik():
    """search= plus a publication_year filter ranks the NBER paper among the top hits."""
    res = openalex.search_works("Globalization, Structural Change and Productivity Growth McMillan Rodrik",
                                {"publication_year": 2011}, per_page=10, refresh=True)
    assert DOI in res.data["doi"].dropna().tolist(), res.data[["doi", "title", "year"]].to_string()
    assert isinstance(res.meta["total_count"], int)
    assert res.meta["cost_usd"] is None or res.meta["cost_usd"] > 0  # meta.cost_usd documented since 2026


def test_batch_doi_filter():
    """filter=doi:a|b returns known DOIs and omits unknown ones."""
    res = OpenAlex().resolve_dois([DOI, MISSING], refresh=True)
    assert res.data["doi"].tolist() == [DOI]
    assert res.meta["unresolved"] == [MISSING]


@pytest.mark.skipif(not os.environ.get(openalex.KEY_ENV), reason=f"set {openalex.KEY_ENV} to test key handling")
def test_bearer_key_is_recognised():
    """UNVERIFIED: the key sent as 'Authorization: Bearer <key>' is accepted for budget accounting.

    /rate-limit reports the keyed daily budget ($1 or more) only if the header
    is recognised. If this fails, the key must be sent as the documented
    ``api_key`` query parameter (which would put it into cache keys: see
    docs/apis/literature.md before changing the connector).
    """
    payload, _ = http.fetch("openalex", f"{openalex.BASE_URL}/rate-limit", {}, headers=OpenAlex().headers(),
                            refresh=True)
    doc = json.loads(payload)
    assert doc["rate_limit"]["daily_budget_usd"] >= 1, doc
