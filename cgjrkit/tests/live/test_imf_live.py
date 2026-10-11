"""Live tests for the IMF connector (need api.imf.org and www.imf.org allowlisted).

Run with: CGJRKIT_LIVE=1 pytest -m live tests/live/test_imf_live.py

They confirm the UNVERIFIED parts of docs/apis/imf.md (SDMX-CSV shape and
series attributes on the live API, vintage dataflow naming, PUBLICATION_DATE
probe, time filter, multi-country DataMapper paths, DataMapper metadata) and
check the Kenya CGJR replication target (Fig 9: general government gross debt
about 73% of GDP in 2023).

IMF terms: these tests make a handful of small requests; do not loop them.
"""

from __future__ import annotations

import pytest

from cgjrkit.connectors.imf import IMF, IMFAPIError, build_data_request, read_sdmx_csv
from cgjrkit.core import http
from cgjrkit.core.schema import validate_frame

pytestmark = pytest.mark.live


@pytest.fixture
def imf() -> IMF:
    return IMF()


def _value(df, iso3: str, indicator: str, period: str) -> float:
    sel = df[(df["iso3"] == iso3) & (df["indicator"] == indicator) & (df["period"] == period)]
    assert len(sel) == 1, f"expected one value for {iso3}/{indicator}/{period}, got {len(sel)}"
    return float(sel["value"].iloc[0])


def test_kenya_gross_debt_2023_matches_report_fig9(imf):
    """Kenya CGJR Fig 9: general government gross debt ~73% of GDP in 2023
    (WEO; tolerance 5 pp because later vintages revise debt and GDP)."""
    res = imf.fetch_weo("GGXWDG_NGDP", ["KEN"], start=2015, fallback=False)
    validate_frame(res.data)
    assert _value(res.data, "KEN", "GGXWDG_NGDP", "2023") == pytest.approx(73, abs=5)
    assert set(res.data["unit"]) == {"% of GDP"}
    assert res.provenance[0].vintage.startswith("WEO 20"), res.provenance[0].vintage
    assert "WEO_FORECAST_YEARS" in {c.code for c in res.caveats}


def test_weo_series_attributes_include_last_actual(imf):
    """LATEST_ACTUAL_ANNUAL_DATA arrives with attributes=series (CSV)."""
    res = imf.fetch_weo(["NGDP_RPCH", "GGXWDG_NGDP"], ["KEN", "UGA"], fallback=False)
    recs = {(s["iso3"], s["indicator"]): s for s in res.meta["series"]}
    assert recs, "no series metadata"
    assert any(s["last_actual"] is not None for s in recs.values()), recs
    forecast_years = res.data.loc[res.data["obs_status"] == "forecast", "period"].astype(int)
    assert len(forecast_years) and forecast_years.min() >= 2022


def test_weo_six_cgjr_indicators(imf):
    inds = ["NGDP_RPCH", "GGXWDG_NGDP", "GGXCNL_NGDP", "GGR_NGDP", "GGX_NGDP", "BCA_NGDPD"]
    res = imf.fetch_weo(inds, ["KEN", "UGA", "TZA"], start=2010, end=2024, fallback=False)
    assert set(res.data["indicator"]) == set(inds)
    assert res.meta["missing"] == []
    # values arrive in base units: ratios stay in a plausible percentage range
    debt = res.data[res.data["indicator"] == "GGXWDG_NGDP"]["value"]
    assert debt.between(0, 300).all()


def test_weo_publication_date_probe(imf):
    date, _ = imf.weo_publication_date()
    assert len(date) == 10 and date[:2] == "20", date


def test_weo_vintage_flows_listed_and_fetchable(imf):
    flows = imf.list_weo_vintages()
    assert "WEO" in set(flows["dataflow"])
    assert "WEO_2025_OCT_VINTAGE" in set(flows["dataflow"]), flows
    # bare WEO versions are labelled by the release their PUBLICATION_DATE names
    bare = flows[flows["dataflow"] == "WEO"]
    assert (bare["label_source"] == "PUBLICATION_DATE").all(), flows.attrs.get("probe_failures")
    assert flows["latest"].sum() == 1 and flows.loc[flows["latest"], "dataflow"].item() == "WEO"
    print(flows[["dataflow", "version", "vintage", "publication_date", "selected"]].to_string())
    res = imf.fetch_weo("NGDP_RPCH", ["KEN"], vintage="2025-10")
    assert res.provenance[0].vintage == "WEO 2025-10"
    assert "WEO_2025_OCT_VINTAGE" in res.provenance[0].endpoint
    assert res.data["period"].astype(int).max() <= 2030


def test_weo_vintage_april_2025_from_older_bare_version(imf):
    """April 2025 has no WEO_2025_APR_VINTAGE flow; imf-reader 2.2.0 maps it to bare WEO 6.0.0
    (old bare versions stay live). Confirms the catalogue + PUBLICATION_DATE resolution."""
    res = imf.fetch_weo("NGDP_RPCH", ["KEN"], vintage="2025-04")
    validate_frame(res.data)
    assert res.provenance[0].vintage == "WEO 2025-04"
    reso = res.meta["vintage_resolution"]
    assert reso["label_source"] == "PUBLICATION_DATE" and reso["publication_date"].startswith("2025-04"), reso
    print("April 2025 served by", reso["dataflow"], reso["version"])
    assert len(res.data) > 10


def test_weo_json_matches_csv(imf):
    a = imf.fetch_sdmx("WEO", "KEN.NGDP_RPCH.A", fmt="csv")
    b = imf.fetch_sdmx("WEO", "KEN.NGDP_RPCH.A", fmt="json")
    merged = a.data.merge(b.data, on=["iso3", "indicator", "period"], suffixes=("_csv", "_json"))
    assert len(merged) == len(a.data) > 20
    assert (merged["value_csv"] - merged["value_json"]).abs().max() < 1e-9


def test_csv_header_shape(imf):
    """Confirms the SDMX-CSV envelope (STRUCTURE[;], STRUCTURE_ID, ACTION) and dimension order."""
    url, params, headers = build_data_request("IMF.RES", "WEO", "KEN.NGDP_RPCH.A", params={"attributes": "series"})
    payload, _ = http.fetch("imf", url, params, headers=headers, cache=imf.cache)
    raw = read_sdmx_csv(payload)
    assert raw.attrs["dimensions"] == ["COUNTRY", "INDICATOR", "FREQUENCY"]
    assert raw.attrs["structure_id"].startswith("IMF.RES:WEO(")


def test_bop_kenya_current_account_and_time_filter(imf):
    """Kenya's current account was a deficit of roughly US$4-6 billion in
    2016-2018 (BOP, base units). Also checks that c[TIME_PERIOD] is honoured."""
    res = imf.fetch_bop(["current_account", "direct_investment"], ["KEN"], 2016, 2018)
    validate_frame(res.data)
    assert set(res.data["period"]) == {"2016", "2017", "2018"}
    ca = _value(res.data, "KEN", "NETCD_T.CAB.USD", "2017")
    assert -9e9 < ca < -2e9
    assert "BOP_BPM6_SIGN" in {c.code for c in res.caveats}


def test_bop_reserve_assets_entry(imf):
    """UNVERIFIED: reserve assets under the A_T accounting entry."""
    res = imf.fetch_bop("reserve_assets", ["KEN"], 2016, 2018)
    assert len(res.data) > 0, res.meta


def test_unknown_dataflow_is_an_error_or_empty(imf):
    try:
        res = imf.fetch_sdmx("NOT_A_FLOW_XYZ", "*", agency="IMF.RES")
    except IMFAPIError:
        return
    assert res.data.empty


def test_datamapper_kenya_debt_and_multi_country_path(imf):
    res = imf.fetch_datamapper("GGXWDG_NGDP", ["KEN", "UGA"])
    validate_frame(res.data)
    assert set(res.data["iso3"]) == {"KEN", "UGA"}
    assert _value(res.data, "KEN", "GGXWDG_NGDP", "2023") == pytest.approx(73, abs=5)
    assert "IMF_VINTAGE_UNPINNED" in {c.code for c in res.caveats}


def test_datamapper_indicator_metadata(imf):
    meta = imf.fetch_datamapper_indicators()
    row = meta.set_index("code").loc["GGXWDG_NGDP"]
    assert row["unit"], row
    assert row["dataset"] == "WEO"


def test_datamapper_regions_and_groups(imf):
    """UNVERIFIED format of /api/v1/regions and /groups ({"regions": {code: {"label": ...}}})."""
    groups = imf.fetch_datamapper_groups()
    assert "SSQ" in groups, sorted(groups)[:20]
    assert groups["SSQ"].lower().startswith("sub-saharan africa")
    res = imf.fetch_datamapper("NGDP_RPCH", ["KEN", "SSQ"])
    assert "SSQ" in res.meta["aggregates"] and "KEN" not in res.meta["aggregates"]
