"""Live tests for the CEPII BACI connector.

Run with ``CGJRKIT_LIVE=1 pytest -m live tests/live/test_baci_live.py`` once
``www.cepii.fr`` is allowlisted. Uses the smallest release file (HS22, about
300 MB); the first run downloads and caches it.

Replication: Kenya CGJR Figs 24-25 (EU agri exports at HS6) and X06 market
HHI items have no BACI values printed in ``../inventory``; the tests check the
release layout and well-known facts (Kenya has a revealed comparative
advantage in black tea, HS 090240).
"""

import zipfile

import pytest

from cgjrkit.connectors import baci
from cgjrkit.connectors.baci import BACI
from cgjrkit.core.schema import validate_frame

pytestmark = pytest.mark.live

HS = "HS22"
VERSION = "202601"


def test_release_layout():
    b = BACI()
    path, retrieval = b.archive(HS, VERSION)
    with zipfile.ZipFile(path) as zf:
        members = baci.archive_members(zf.namelist())
        readme = baci.parse_readme(zf.read(members.readme).decode("utf-8", "replace"))
    assert (members.hs, members.version) == (HS, VERSION)
    assert min(members.trade) == 2022 and max(members.trade) >= 2024
    assert readme.get("Version") == VERSION and readme.get("Release Date", "").startswith("2026")


def test_kenya_exports_and_tea_rca():
    b = BACI()
    tot = b.exporter_totals("KEN", [2023], hs=HS, version=VERSION)
    validate_frame(tot.data)
    exports = tot.data.set_index("indicator").loc["exports_total", "value"]
    assert 3e6 < exports < 1.5e7  # thousand US$: Kenya's goods exports are several US$ billion
    rca = b.rca("KEN", [2023], products=["090240"], hs=HS, version=VERSION).data
    assert rca["value"].iloc[0] > 10
    mk = b.market_concentration("KEN", [2023], hs=HS, version=VERSION).data.set_index("indicator")["value"]
    assert 0 < mk["export_market_hhi"] < 0.5 and mk["export_markets"] > 100
