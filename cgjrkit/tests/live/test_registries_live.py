"""Live checks for cgjrkit/registries (need the hosts allowlisted).

Run with: CGJRKIT_LIVE=1 pytest -m live tests/live/test_registries_live.py

* Every non-excluded host in hosts.yaml must answer HTTPS (any HTTP status
  counts: the check is that the hostname exists and is reachable, which
  confirms the entries marked ``unverified``, e.g. www.stat.gov.mk,
  www.centralbank.go.ke and the UNCTADstat API hosts).
* The statistics-office host of every country file must answer too.
"""

from __future__ import annotations

import pytest

from cgjrkit import registries as reg
from cgjrkit.core import http

pytestmark = pytest.mark.live


def _reachable(host: str) -> None:
    try:
        http.fetch("registry-check", f"https://{host}/", refresh=True, retries=1, timeout=30.0)
    except http.FetchError:
        pass  # the host answered with an HTTP error status: it exists and is reachable
    # http.NetworkBlocked propagates and fails the test


@pytest.mark.parametrize("host", reg.allowlist())
def test_declared_host_answers(host):
    _reachable(host)


@pytest.mark.parametrize("iso3", reg.list_countries())
def test_statistics_office_host_answers(iso3):
    _reachable(reg.statistics_office_host(iso3))
