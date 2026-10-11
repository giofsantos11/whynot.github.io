"""Live tests for the PIP connector.

Run with ``CGJRKIT_LIVE=1 pytest -m live tests/live/test_pip_live.py`` once
``api.worldbank.org`` is allowlisted. Each test confirms an assumption listed
in ``docs/apis/pip.md``.

Replication note: Kenya CGJR Fig 29 uses national-poverty-line rates from the
2023 Kenya Poverty Assessment (KIHBS/KCHS), not PIP international-line rates,
so there is no published PIP value in ``../inventory`` to match exactly; the
tests below check structure, units and plausibility instead.
"""

import pandas as pd
import pytest

from cgjrkit.connectors import pip as pipmod
from cgjrkit.connectors.pip import PIP
from cgjrkit.core.http import fetch
from cgjrkit.core.schema import ppp_base, validate_frame

pytestmark = pytest.mark.live


def test_versions_table():
    versions, prov = PIP().versions(refresh=True)
    ppps = {v["ppp_version"] for v in versions}
    assert {2017, 2021} <= ppps
    latest_2021 = pipmod.resolve_version(versions, 2021)
    # 2021 PPPs were introduced in the June 2025 update.
    assert latest_2021["release_version"] >= "20250601"
    assert prov.payload_sha256


def test_pip_csv_has_documented_columns():
    versions, _ = PIP().versions()
    v = pipmod.resolve_version(versions, 2021)
    url, params = pipmod.build_request("KEN", "all", 3.0, 2021, v["release_version"], v["version"])
    payload, _ = fetch("pip", url, params, refresh=True)
    table = pipmod._read_table(payload)
    expected = {"country_code", "reporting_year", "reporting_level", "welfare_type", "poverty_line", "headcount",
                "poverty_gap", "poverty_severity", "watts", "mean", "median", "gini", "is_interpolated",
                "survey_comparability", "estimate_type"}
    assert expected <= set(table.columns), expected - set(table.columns)
    hc = pd.to_numeric(table["headcount"], errors="coerce").dropna()
    assert hc.between(0, 1).all(), "headcount is no longer a 0-1 fraction: revisit the x100 scaling"
    gini = pd.to_numeric(table["gini"], errors="coerce").dropna()
    assert gini.between(0, 1).all(), "gini is not on a 0-1 scale: revisit MEASURES['gini'].fraction"
    deciles = table[[f"decile{k}" for k in range(1, 11)]].apply(pd.to_numeric, errors="coerce").dropna()
    assert ((deciles.sum(axis=1) - 1).abs() < 0.02).all(), "decile shares do not sum to 1"


def test_kenya_poverty_2021_ppp():
    res = PIP().fetch_poverty("KEN", povline=3.0, ppp_version=2021)
    validate_frame(res.data)
    hc = res.data[res.data["indicator"].str.startswith("headcount@3.00")]
    assert len(hc["period"].unique()) >= 2
    assert hc["value"].between(0, 100).all() and res.meta["scaled_x100"]
    assert ppp_base(hc["unit"].iloc[0]) == "2021"
    data_prov = [p for p in res.provenance if p.tool == "PIP.fetch_poverty"][0]
    assert data_prov.vintage == res.meta["release_version"] and len(data_prov.vintage) == 8
    print(hc[["period", "value"]].to_string(index=False))


def test_kenya_2017_ppp_still_served():
    res = PIP().fetch_poverty("KEN", povline=2.15, ppp_version=2017)
    assert not res.data.empty and set(res.data["dataset"]) == {"PIP_PPP2017"}
    assert not any(c.code == "pip_line_ppp_mismatch" for c in res.caveats)


def test_fill_gaps_marks_interpolated_years():
    res = PIP().fetch_poverty("KEN", years="all", fill_gaps=True, measures=["headcount"])
    assert (res.data["obs_status"] == "imputed").any()


def test_world_aggregate():
    res = PIP().fetch_aggregates("WLD", years="all", povline=3.0, ppp_version=2021, measures=["headcount"])
    assert "WLD" in set(res.data["iso3"])
    assert res.data["value"].between(0, 100).all()
