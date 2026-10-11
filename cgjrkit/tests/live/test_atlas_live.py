"""Live tests for the Atlas of Economic Complexity connector.

Run with ``CGJRKIT_LIVE=1 pytest -m live tests/live/test_atlas_live.py`` once
``atlas.hks.harvard.edu`` is allowlisted (one GraphQL request).

Replication target (``../inventory/kenya-replication-inventory.md``): Kenya
CGJR Fig 26, ECI rank 87 in both 2013 and 2023; Vietnam 58 -> 48. The Atlas
revises data between releases and our rank universe (countries with an ECI,
former countries excluded) is UNVERIFIED against the Atlas rankings page, so
ranks are checked within +/-5 and the exact values are printed.
"""

import pytest

from cgjrkit.connectors.atlas import Atlas
from cgjrkit.core.schema import validate_frame

pytestmark = pytest.mark.live


def test_kenya_vietnam_eci_ranks():
    res = Atlas().fetch(["KEN", "VNM"], 2013, 2023)
    validate_frame(res.data)
    rank = res.data[res.data["indicator"] == "eci_rank_hs92"].set_index(["iso3", "period"])["value"]
    print({k: int(v) for k, v in rank.items()}, res.meta["rank_universe"])
    assert abs(rank[("KEN", "2013")] - 87) <= 5
    assert abs(rank[("KEN", "2023")] - 87) <= 5
    assert abs(rank[("VNM", "2013")] - 58) <= 5
    assert abs(rank[("VNM", "2023")] - 48) <= 5


def test_rank_universe_size():
    """The Atlas ranks roughly 130-150 countries; a much larger universe means the ECI field is
    populated for countries outside the rankings and the rank rule must change."""
    res = Atlas().fetch(None, 2023, 2023)
    n = res.meta["rank_universe"]["2023"]
    assert 100 <= n <= 160, n


def test_trade_values_and_product_class():
    """UNVERIFIED: countryYear accepts productClass and serves exportValue/importValue."""
    res = Atlas().fetch("KEN", 2022, 2022, product_class="HS12", indicators=["eci", "export_value", "import_value"])
    assert {"eci_hs12", "export_value", "import_value"} <= set(res.data["indicator"])
