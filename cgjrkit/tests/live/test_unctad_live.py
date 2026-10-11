"""Live tests for the UNCTADstat FDI connector.

Run with ``CGJRKIT_LIVE=1 pytest -m live tests/live/test_unctad_live.py`` once
``unctadstat-api.unctad.org`` is allowlisted (and ``unctadstat-user-api.unctad.org``
plus ``CGJRKIT_UNCTAD_CLIENT_ID``/``CGJRKIT_UNCTAD_KEY`` for the API test).
Needs ``py7zr`` for the bulk 7z file.

Replication: North Macedonia CGJR Fig 37 (FDI inflows % of GDP vs the UMIC
median/IQR, archetype X04) and Kenya CGJR Fig 23 (FDI 1.4% of GDP in 2011-19 vs
0.5% in 2021-23, WDI) have no UNCTAD values printed in ``../inventory``; the
tests check structure and plausible magnitudes.
"""

import os

import pytest

from cgjrkit.connectors import unctad
from cgjrkit.connectors.unctad import UNCTAD
from cgjrkit.core.http import fetch
from cgjrkit.core.schema import validate_frame

pytestmark = pytest.mark.live


def test_metadata():
    meta, prov = UNCTAD().metadata(refresh=True)
    assert meta.name == "US.FdiFlowsStock" and meta.version.isdigit()
    assert {"0100", "5011"} <= set(meta.measures)
    assert {"Economy", "Year", "Direction", "Flow"} <= set(meta.dimensions)
    assert prov.vintage.startswith("UNCTADstat US.FdiFlowsStock v")


def test_bulk_columns_and_kenya():
    pytest.importorskip("py7zr")
    url, params = unctad.build_bulk_request()
    payload, _ = fetch("unctad", url, params)
    assert payload[:6] == b"7z\xbc\xaf\x27\x1c", "bulk file is no longer 7z"
    name, text = unctad.read_archive(payload)
    header = text.splitlines()[0]
    for col in ("Year", "Economy", "Economy Label", "Direction Label", "Flow Label"):
        assert f'"{col}"' in header or col in header.split(","), header
    res = UNCTAD().fdi(["KEN", "MKD"], 2011, 2019, measures=["usd"])
    validate_frame(res.data)
    ken = res.data[(res.data["iso3"] == "KEN") & (res.data["indicator"] == "FDI_INFLOWS_USD_MN")]
    assert len(ken) == 9 and 200 < ken["value"].mean() < 3000
    mkd = res.data[(res.data["iso3"] == "MKD") & (res.data["indicator"] == "FDI_INWARD_STOCK_USD_MN")]
    assert len(mkd) == 9 and (mkd["value"] > 0).all()


def test_pct_gdp_measure_matches_caveat():
    """UNVERIFIED: whether US.FdiFlowsStock still carries % of GDP (5025)."""
    pytest.importorskip("py7zr")
    meta, _ = UNCTAD().metadata()
    res = UNCTAD().fdi("KEN", 2015, 2020, measures=["pct_gdp"])
    if "5025" in meta.measures:
        assert len(res.data) > 0
    else:
        assert "unctad_measure_missing" in {c.code for c in res.caveats}


def test_versioned_bulk_url():
    """UNVERIFIED: /bulkdownload/{report}/{version}/{file} pins a release for this report."""
    pytest.importorskip("py7zr")
    meta, _ = UNCTAD().metadata()
    res = UNCTAD().fdi("KEN", 2020, 2020, measures=["usd"], version=meta.version)
    assert len(res.data) > 0 and "unctad_version_mismatch" not in {c.code for c in res.caveats}
    # the data provenance names the pinned release
    assert res.provenance[-1].vintage.startswith(f"UNCTADstat US.FdiFlowsStock v{meta.version} (pinned bulk release")


@pytest.mark.skipif(not (os.environ.get(unctad.CLIENT_ID_ENV) and os.environ.get(unctad.KEY_ENV)),
                    reason="needs UNCTADstat API credentials")
def test_api_route_matches_bulk():
    """UNVERIFIED: the Facts endpoint accepts the OData fields as query parameters on POST."""
    api = UNCTAD().fdi(["KEN"], 2018, 2020, measures=["usd"], route="api")
    bulk = UNCTAD().fdi(["KEN"], 2018, 2020, measures=["usd"], route="bulk")
    a = api.data.set_index(["indicator", "period"])["value"]
    b = bulk.data.set_index(["indicator", "period"])["value"]
    common = a.index.intersection(b.index)
    assert len(common) > 0
    assert (abs(a[common] - b[common]) < 0.5).all()
