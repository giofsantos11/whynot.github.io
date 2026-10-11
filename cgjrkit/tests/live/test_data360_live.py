"""Live tests for the Data360 connector.

Run with ``CGJRKIT_LIVE=1 pytest -m live tests/live/test_data360_live.py`` once
``data360api.worldbank.org`` is allowlisted. Each test confirms one assumption
recorded as UNVERIFIED in ``docs/apis/data360.md``.
"""

import pytest

from cgjrkit.connectors import data360 as d360
from cgjrkit.connectors.data360 import Data360
from cgjrkit.core.http import fetch
from cgjrkit.core.schema import validate_frame

pytestmark = pytest.mark.live

# Real WB_ES indicator ids used in World Bank team code (govhr, cliaretl, wdr2025gov).
ES_FINANCE = "WB_ES_T_OBST1"  # firms identifying access to finance as a major constraint
ES_ELECTRICITY = "WB_ES_T_IN12"  # firms identifying electricity as a major constraint

DOCUMENTED_FIELDS = {
    "OBS_VALUE", "TIME_FORMAT", "UNIT_MULT", "COMMENT_OBS", "OBS_STATUS", "OBS_CONF", "AGG_METHOD", "DECIMALS",
    "COMMENT_TS", "DATA_SOURCE", "LATEST_DATA", "DATABASE_ID", "INDICATOR", "REF_AREA", "SEX", "AGE",
    "URBANISATION", "COMP_BREAKDOWN_1", "COMP_BREAKDOWN_2", "COMP_BREAKDOWN_3", "TIME_PERIOD", "FREQ",
    "UNIT_MEASURE", "UNIT_TYPE",
}


def test_data_endpoint_envelope_and_row_schema():
    url, params = d360.build_data_request("WB_ES", ES_FINANCE, "KEN")
    payload, _ = fetch("data360", url, params, refresh=True)
    rows, count = d360.parse_page(payload)
    assert rows and count is not None and count >= len(rows)
    assert DOCUMENTED_FIELDS <= set(rows[0]), set(rows[0]) ^ DOCUMENTED_FIELDS


def test_comma_separated_indicators_and_countries():
    res = Data360().fetch("WB_ES", [ES_FINANCE, ES_ELECTRICITY], ["KEN", "UGA"], with_metadata=False, with_labels=False)
    assert {ES_FINANCE, ES_ELECTRICITY} <= {s["INDICATOR"] for s in res.meta["series"].values()}
    assert {"KEN", "UGA"} <= set(res.data["iso3"])


def test_enterprise_surveys_kenya_and_se_n_encoding():
    res = Data360().fetch_enterprise_surveys([ES_FINANCE], ["KEN"])
    validate_frame(res.data)
    periods = set(res.data["period"])
    # Kenya ES waves include 2013 and 2018 (enterprisesurveys.org country page).
    assert periods & {"2013", "2018"}, periods
    assert ((res.data["value"] >= 0) & (res.data["value"] <= 100)).all()
    # Consistency of the SE/n detector: either something was found or the caveat says it was not.
    found = res.meta["standard_errors_found"] and res.meta["sample_sizes_found"]
    assert found or any(c.code == "es_se_missing" for c in res.caveats)
    print("WB_ES SE found:", res.meta["standard_errors_found"], "n found:", res.meta["sample_sizes_found"],
          "dropped breakdown rows:", res.meta["dropped_breakdown_rows"],
          "dropped codes (candidates for ES_STATISTIC_CODES):", res.meta["dropped_breakdown_codes"])
    # metadata was read, so the data provenance carries the indicator's own licence
    data_prov = next(p for p in res.provenance if p.tool == "Data360.fetch_enterprise_surveys")
    assert data_prov.license and data_prov.license != d360.LICENSE_UNVERIFIED, data_prov.license


def test_es_breakdown_codes_report():
    """Shows which COMP_BREAKDOWN codes WB_ES uses (to confirm or refute the SE/n encodings)."""
    disagg = Data360().disaggregation("WB_ES", ES_FINANCE, refresh=True)
    assert "REF_AREA" in disagg
    codes = {k: v for k, v in disagg.items() if k.startswith("COMP_BREAKDOWN")}
    print("WB_ES breakdown codes:", codes)
    assert "_T" in sum(codes.values(), []) or not codes


def test_metadata_post_body_form():
    meta = Data360().metadata(indicator_id=ES_FINANCE, refresh=True)
    assert ES_FINANCE in meta, "OpenAPI body form rejected: switch build_metadata_request to the data360-mcp form"
    assert meta[ES_FINANCE]["name"]
    print("version_date:", meta[ES_FINANCE]["version_date"], "license:", meta[ES_FINANCE]["license"])


def test_lpi20_indicator_ids_and_metrics():
    ids = Data360().list_indicators("WB_LPI_20", refresh=True)
    assert ids
    print("WB_LPI_20 ids (first 25):", ids[:25])
    res = Data360().fetch_lpi20(countries=["KEN"])
    validate_frame(res.data)
    metrics = {s.get("COMP_BREAKDOWN_1") for s in res.meta["series"].values()}
    assert metrics & set(d360.LPI20_METRICS), metrics
    assert set(res.data["period"]) <= {"2023", "2024"}
    assert any(e["field"] in ("n_obs_group", "n") for e in res.meta["extras"])


def test_series_without_total_are_kept():
    """The OpenAPI sample: WB_WDI_SE_PRM_CMPT_FE_ZS comes with SEX='F' and no '_T' row."""
    res = Data360().fetch("WB_WDI", "WB_WDI_SE_PRM_CMPT_FE_ZS", "KEN", with_labels=False)
    assert len(res.data), res.caveats
    assert "data360_empty" not in {c.code for c in res.caveats}
    print("indicator ids:", sorted(res.data["indicator"].unique()), "untotalled:", res.meta["untotalled_series"])


def test_codelist_endpoint():
    names = Data360().codelist("REF_AREA", refresh=True)
    assert names.get("KEN", "").lower().startswith("kenya")
    units = Data360().codelist("UNIT_MEASURE", refresh=True)
    assert "PT" in units


def test_search_endpoint():
    hits = Data360().search("enterprise surveys access to finance", top=5, refresh=True)
    assert hits and all("idno" in h for h in hits)


def test_pagination_reaches_total_count():
    res = Data360().fetch_lpi20(with_metadata=False, with_labels=False)
    assert res.meta["total_count"] and res.meta["rows_received"] == res.meta["total_count"]
    assert len([p for p in res.provenance if p.tool == "Data360.fetch_lpi20"]) >= 2
