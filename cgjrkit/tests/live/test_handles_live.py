"""Live checks for the link forms built by ``cgjrkit.connectors.handles``.

``handles.py`` itself never calls the network; these tests only confirm that
the URLs it builds work. Run with ``CGJRKIT_LIVE=1 pytest -m live
tests/live/test_handles_live.py`` once ``openknowledge.worldbank.org``,
``hdl.handle.net``, ``doi.org`` and the redirect target ``www.nber.org`` are
allowlisted.

Identifiers: handle 10986/11843 is World Development Report 2013: Jobs on the
World Bank OKR; DOI 10.3386/w17143 is McMillan and Rodrik (2011), cited in the
Kenya CGJR.
"""

import pytest

from cgjrkit.connectors import handles
from cgjrkit.core import http

pytestmark = pytest.mark.live

OKR_HANDLE = "10986/11843"
DOI = "10.3386/w17143"


def test_okr_handle_route_serves_the_item():
    """UNVERIFIED live (verified in DSpace source): /handle/10986/<n> is served by the OKR's DSpace UI."""
    payload, retrieval = http.fetch("handles-live-check", handles.okr_url(OKR_HANDLE), {}, refresh=True)
    assert payload and retrieval.sha256
    assert b"World Development Report 2013" in payload or b"Jobs" in payload


def test_handle_proxy_redirects_to_okr():
    """hdl.handle.net/10986/<n> resolves (follows redirects) to an OKR page."""
    payload, _ = http.fetch("handles-live-check", handles.handle_url(OKR_HANDLE), {}, refresh=True)
    assert payload


def test_doi_proxy_resolves_replication_doi():
    payload, _ = http.fetch("handles-live-check", handles.doi_url(DOI), {}, refresh=True)
    assert b"17143" in payload or b"Structural Change" in payload
