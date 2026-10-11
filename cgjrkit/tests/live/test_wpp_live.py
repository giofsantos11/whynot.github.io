"""Live tests for the WPP connector.

Run with ``CGJRKIT_LIVE=1 pytest -m live tests/live/test_wpp_live.py`` once
``population.un.org`` is allowlisted. The bulk files are large (16-300 MB);
the first run downloads them into the cache.

Expected values for Japan are WPP 2024 Medium-variant figures as read from
the bulk CSV by a third party on 2026-10-06 (UNopenGIS/foil4g, "World
Population Prospects"); they confirm column names, units and the variant
layout. The Morocco CGJR pyramid (Fig 4-1a, archetype L08) is checked for
shape only (no printed values in ../inventory).
"""

import os

import pytest

from cgjrkit.connectors import wpp
from cgjrkit.connectors.wpp import WPP
from cgjrkit.core.http import fetch
from cgjrkit.core.schema import validate_frame

pytestmark = pytest.mark.live


def test_demographic_indicators_japan():
    res = WPP().fetch(["TPopulation1July", "TFR", "LEx"], "JPN", 1950, 2100)
    validate_frame(res.data)
    v = res.data.set_index(["indicator", "period"])["value"]
    assert v[("TPopulation1July", "1950")] == pytest.approx(86443.277, abs=0.01)
    assert v[("TPopulation1July", "2024")] == pytest.approx(123753.041, abs=0.01)
    assert v[("TFR", "2024")] == pytest.approx(1.2168, abs=1e-4)
    assert v[("LEx", "2024")] == pytest.approx(84.852, abs=1e-3)
    assert res.provenance[0].vintage == "WPP 2024"


def test_demographic_indicators_has_67_columns():
    url, _ = wpp.build_request("demographic_indicators")
    payload, _ = fetch("wpp", url, {})
    df, missing, rows = wpp.read_bulk(payload, wpp.LOCATION_COLUMNS + list(wpp.INDICATORS))
    assert not missing, f"columns not in the file: {missing}"
    assert rows == 84_360  # 555 locations x 152 years (third-party count)


def test_total_population_variants_japan_2100():
    w = WPP()
    vals = {}
    for variant in ("Medium", "High", "Low"):
        res = w.total_population("JPN", 2100, 2100, variant=variant)
        vals[variant] = res.data["value"].item()
    assert vals["Medium"] == pytest.approx(76845.989, abs=0.01)
    assert vals["High"] == pytest.approx(106414.196, abs=0.01)
    assert vals["Low"] == pytest.approx(53421.354, abs=0.01)


def test_age5_july_file_and_working_age():
    """UNVERIFIED: whether WPP2024_PopulationByAge5GroupSex_Medium.csv.gz (1 July) exists;
    the connector falls back to the 1 January file and says so."""
    res = WPP().age_structure("JPN", 2024, 2024)
    total = res.data[res.data["indicator"].str.startswith("PopTotal|")]["value"].sum()
    ref = WPP().fetch("TPopulation1July" if res.meta["reference"] == "1 July" else "TPopulation1Jan", "JPN", 2024, 2024)
    assert total == pytest.approx(ref.data["value"].item(), rel=1e-3)
    assert len(res.meta["age_groups"]) == 21 and res.meta["age_groups"][-1] == "100+"
    wa = WPP().working_age_population("KEN", 2020, 2030)
    v = wa.data.set_index(["period", "indicator"])["value"]
    assert 50 < v[("2024", "share|PopTotal|15-64")] < 70
    assert 50 < v[("2024", "dependency_ratio")] < 100


def test_morocco_pyramid_shape():
    res = WPP().age_structure("MAR", 2024, 2024)
    pyr = wpp.pyramid_table(res.data, "MAR", 2024)
    assert len(pyr) == 21 and (pyr["male"] > 0).all() and (pyr["female"] > 0).all()
    assert (pyr["male_pct"].sum() + pyr["female_pct"].sum()) == pytest.approx(100.0)


def test_other_variants_demographic_file_exists():
    """UNVERIFIED file name WPP2024_Demographic_Indicators_OtherVariants.csv.gz."""
    res = WPP().fetch("TFR", "KEN", 2030, 2030, variant="High")
    assert res.data["dataset"].iloc[0] == "WPP2024 High"


@pytest.mark.skipif(not os.environ.get(wpp.KEY_ENV), reason="needs a Data Portal token in CGJRKIT_WPP_KEY")
def test_data_portal_api_total_population():
    res = WPP().fetch_api(49, "JPN", 2024, 2024)
    validate_frame(res.data)
    assert len(res.data), "no rows: check field names in parse_api against the live JSON"
    both = res.data[res.data["indicator"] == "api:49"]
    assert len(both) >= 1
