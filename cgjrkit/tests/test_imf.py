"""Unit tests for the IMF connector (SDMX 3.0 API and DataMapper fallback).

All tests run offline. Fixtures in tests/fixtures/imf_*_synthetic.* follow the
response formats documented in docs/apis/imf.md (SDMX-CSV and SDMX-JSON 2.0 as
returned by api.imf.org, DataMapper JSON) and contain made-up values. Tests of
the full fetch path pre-load payloads into an isolated cache under the exact
URL/params the connector requests.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from cgjrkit.connectors.imf import (
    BOP_ITEMS,
    IMF,
    IMFAPIError,
    IMFResponseError,
    apply_forecast_flags,
    build_data_request,
    build_datamapper_request,
    build_key,
    build_publication_probe,
    build_weo_request,
    infer_vintage_from_text,
    normalize_countries,
    normalize_indicators,
    normalize_period,
    parse_codelist,
    parse_dataflows,
    parse_datamapper,
    parse_datamapper_indicators,
    parse_date,
    parse_last_actual,
    parse_publication_date,
    parse_sdmx,
    parse_vintage,
    parse_weo,
    read_sdmx_csv,
    read_sdmx_json,
    resolve_indicator,
    structure_version,
    time_filter_param,
    unit_for,
    vintage_from_flow,
    vintage_from_publication_date,
    vintage_label,
    weo_flow_for,
    weo_vintages_from_flows,
    build_codelist_request,
    build_datamapper_indicators_request,
    build_weo_vintages_request,
    build_dataflows_request,
    build_datamapper_groups_request,
    forecast_caveats,
    infer_release_from_date,
    is_aggregate,
    is_weo_flow,
    parse_datamapper_groups,
    weo_flow_mapping,
)
from cgjrkit.core import cache as cachemod
from cgjrkit.core import http
from cgjrkit.core.cache import Cache
from cgjrkit.core.schema import COLUMNS, OBS_STATUS, validate_frame

SDMX = "https://api.imf.org/external/sdmx/3.0"
DM = "https://www.imf.org/external/datamapper/api/v1"


def load(fixtures_dir, name: str) -> bytes:
    return (fixtures_dir / name).read_bytes()


@pytest.fixture
def cache(tmp_path) -> Cache:
    return Cache(tmp_path / "imfcache")


@pytest.fixture
def imf(cache) -> IMF:
    return IMF(cache=cache)


def put(cache: Cache, url: str, params: dict, payload: bytes) -> None:
    cache.put("imf", url, params, payload)


def seed(cache: Cache, request: tuple, payload: bytes) -> None:
    url, params = request[0], request[1]
    put(cache, url, params, payload)


def value(df: pd.DataFrame, iso3: str, indicator: str, period: str) -> float:
    sel = df[(df["iso3"] == iso3) & (df["indicator"] == indicator) & (df["period"] == period)]
    assert len(sel) == 1, f"expected one row for {iso3}/{indicator}/{period}, got {len(sel)}"
    return float(sel["value"].iloc[0])


def status(df: pd.DataFrame, iso3: str, indicator: str, period: str) -> str:
    sel = df[(df["iso3"] == iso3) & (df["indicator"] == indicator) & (df["period"] == period)]
    return str(sel["obs_status"].iloc[0])


def codes(result) -> set[str]:
    return {c.code for c in result.caveats}


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def test_resolve_indicator_aliases_and_codes():
    assert resolve_indicator("gross_debt") == "GGXWDG_NGDP"
    assert resolve_indicator("Real_GDP_Growth") == "NGDP_RPCH"
    assert resolve_indicator("current_account") == "BCA_NGDPD"
    assert resolve_indicator("GGR_NGDP") == "GGR_NGDP"
    assert normalize_indicators("gross_debt, revenue;expenditure") == ["GGXWDG_NGDP", "GGR_NGDP", "GGX_NGDP"]
    assert normalize_indicators(["NGDP_RPCH", "real_gdp_growth"]) == ["NGDP_RPCH"]


def test_normalize_indicators_rejects_bad_codes():
    with pytest.raises(ValueError):
        normalize_indicators([])
    with pytest.raises(ValueError):
        normalize_indicators(["NGDP.RPCH"])


def test_normalize_countries():
    assert normalize_countries(None) is None
    assert normalize_countries("all") is None
    assert normalize_countries("*") is None
    assert normalize_countries("ken, uga") == ["KEN", "UGA"]
    assert normalize_countries(["KEN", "G001", "KEN"]) == ["KEN", "G001"]
    with pytest.raises(ValueError):
        normalize_countries(["K/EN"])


def test_build_key():
    assert build_key(["KEN", "UGA"], ["NGDP_RPCH"], "A") == "KEN+UGA.NGDP_RPCH.A"
    assert build_key(None, "GGXWDG_NGDP+NGDP_RPCH", "A") == "*.GGXWDG_NGDP+NGDP_RPCH.A"
    assert build_key("*", [], "") == "*.*.*"


def test_normalize_period():
    assert normalize_period("2024") == "2024"
    assert normalize_period("2024-A1") == "2024"
    assert normalize_period("2024-Q3") == "2024Q3"
    assert normalize_period("2024Q3") == "2024Q3"
    assert normalize_period("2024-M03") == "2024-03"
    assert normalize_period("2024-03") == "2024-03"
    assert normalize_period("2024-S2") == "2024S2"
    assert normalize_period("2024-W05") == "2024-W05"


def test_parse_last_actual():
    assert parse_last_actual("2024") == (2024, False)
    assert parse_last_actual("FY2023/24") == (2023, True)
    assert parse_last_actual("FY2023/2024") == (2023, True)
    assert parse_last_actual("") == (None, False)
    assert parse_last_actual("N/A") == (None, False)
    assert parse_last_actual(None) == (None, False)


def test_parse_date_formats():
    assert parse_date("2026-04-14") == "2026-04-14"
    assert parse_date("2026-04-14T09:00:00Z") == "2026-04-14"
    assert parse_date("4/14/2026") == "2026-04-14"
    assert parse_date("garbage") == ""
    assert parse_date("") == ""


# ---------------------------------------------------------------------------
# WEO vintages
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("latest", None),
        ("2025-10", (2025, 10)),
        ("2025-04", (2025, 4)),
        ("2025-OCT", (2025, 10)),
        ("Oct 2025", (2025, 10)),
        ("October 2025", (2025, 10)),
        ("WEO 2026-04", (2026, 4)),
        ("WEO_2025_OCT_VINTAGE", (2025, 10)),
        (("October", 2025), (2025, 10)),
        ((2026, 4), (2026, 4)),
    ],
)
def test_parse_vintage(raw, expected):
    assert parse_vintage(raw) == expected


@pytest.mark.parametrize("raw", ["2025-07", "July 2025", "nonsense", "1850-04"])
def test_parse_vintage_rejects(raw):
    with pytest.raises(ValueError):
        parse_vintage(raw)


def test_weo_flow_and_labels():
    assert weo_flow_for(None) == "WEO"
    assert weo_flow_for((2025, 10)) == "WEO_2025_OCT_VINTAGE"
    assert weo_flow_for((2025, 4)) == "WEO_2025_APR_VINTAGE"
    assert vintage_from_flow("WEO_2025_OCT_VINTAGE") == (2025, 10)
    assert vintage_from_flow("WEO") is None
    assert vintage_label((2026, 4)) == "WEO 2026-04"
    assert vintage_label(None) == ""
    assert vintage_from_publication_date("2026-04-14") == (2026, 4)
    assert vintage_from_publication_date("10/14/2025") == (2025, 10)
    assert vintage_from_publication_date("") is None
    assert infer_vintage_from_text("World Economic Outlook (April 2026)") == (2026, 4)
    assert infer_vintage_from_text("Fiscal Monitor (Oct. 2025)") == (2025, 10)
    assert infer_vintage_from_text("IMF") is None


# ---------------------------------------------------------------------------
# request builders
# ---------------------------------------------------------------------------
def test_build_weo_request_latest_csv():
    url, params, headers = build_weo_request(["gross_debt", "NGDP_RPCH"], ["KEN", "UGA"])
    assert url == f"{SDMX}/data/dataflow/IMF.RES/WEO/+/KEN+UGA.GGXWDG_NGDP+NGDP_RPCH.A"
    assert params == {"attributes": "series"}
    assert headers == {"Accept": "text/csv"}


def test_build_weo_request_vintage_and_all_countries():
    url, params, _ = build_weo_request("GGXWDG_NGDP", None, (2025, 10))
    assert url == f"{SDMX}/data/dataflow/IMF.RES/WEO_2025_OCT_VINTAGE/+/*.GGXWDG_NGDP.A"


def test_build_weo_request_json_keeps_cache_keys_apart():
    url_c, p_c, _ = build_weo_request("NGDP_RPCH", "KEN", fmt="csv")
    url_j, p_j, h_j = build_weo_request("NGDP_RPCH", "KEN", fmt="json")
    assert url_c == url_j
    assert p_c != p_j
    assert "dimensionAtObservation" in p_j and "dimensionAtObservation" not in p_c
    assert h_j == {"Accept": "application/json"}


def test_build_data_request_validation():
    with pytest.raises(ValueError):
        build_data_request("IMF.STA", "BOP", "KEN", fmt="xml")
    with pytest.raises(ValueError):
        build_data_request("IMF/STA", "BOP", "KEN")
    url, params, _ = build_data_request("IMF.STA", "BOP", "KEN.NETCD_T.CAB.USD.A", params={"dimensionAtObservation": "x"})
    assert "dimensionAtObservation" not in params  # CSV never carries it
    assert url.endswith("/data/dataflow/IMF.STA/BOP/+/KEN.NETCD_T.CAB.USD.A")


def test_build_publication_probe():
    url, params, headers = build_publication_probe()
    assert url == f"{SDMX}/data/dataflow/IMF.RES/WEO/+/USA.NGDP_RPCH.A"
    assert params == {"attributes": "dataset", "lastNObservations": "1"}
    assert headers["Accept"] == "text/csv"


def test_time_filter_param():
    assert time_filter_param(2016, 2018, "A") == {"c[TIME_PERIOD]": "ge:2016-A1+le:2018-A1"}
    assert time_filter_param(2016, None, "Q") == {"c[TIME_PERIOD]": "ge:2016-Q1"}
    assert time_filter_param(None, 2018, "M") == {"c[TIME_PERIOD]": "le:2018-M12"}
    assert time_filter_param(None, None) == {}


def test_structure_requests():
    assert build_dataflows_request()[0] == f"{SDMX}/structure/dataflow/all/*/+"
    url, params, _ = build_weo_vintages_request()
    assert url == f"{SDMX}/structure/dataflow/IMF.RES/*/*" and params == {"detail": "allstubs"}
    assert build_codelist_request("CL_WEO_COUNTRY", "IMF.RES")[0] == f"{SDMX}/structure/codelist/IMF.RES/CL_WEO_COUNTRY/+"
    with pytest.raises(ValueError):
        build_codelist_request("CL/X")


def test_build_datamapper_request():
    assert build_datamapper_request("GGXWDG_NGDP", ["KEN"]) == (f"{DM}/GGXWDG_NGDP/KEN", {})
    assert build_datamapper_request("GGXWDG_NGDP", ["KEN", "UGA"])[0] == f"{DM}/GGXWDG_NGDP/KEN/UGA"
    assert build_datamapper_request("rev")[0] == f"{DM}/rev"
    assert build_datamapper_indicators_request()[0] == f"{DM}/indicators"
    with pytest.raises(ValueError):
        build_datamapper_request("A/B")


# ---------------------------------------------------------------------------
# readers
# ---------------------------------------------------------------------------
def test_read_sdmx_csv_structure(fixtures_dir):
    raw = read_sdmx_csv(load(fixtures_dir, "imf_weo_csv_synthetic.csv"))
    assert raw.attrs["dimensions"] == ["COUNTRY", "INDICATOR", "FREQUENCY"]
    assert "LATEST_ACTUAL_ANNUAL_DATA" in raw.attrs["attributes"]
    assert raw.attrs["structure_id"] == "IMF.RES:WEO(9.0.0)"
    assert "STRUCTURE" not in raw.columns and "ACTION" not in raw.columns
    assert structure_version(raw.attrs["structure_id"]) == "9.0.0"


def test_read_sdmx_csv_empty_and_errors(fixtures_dir):
    assert read_sdmx_csv(b"").empty
    with pytest.raises(IMFResponseError):
        read_sdmx_csv(b"<html><body>Service unavailable</body></html>")
    with pytest.raises(IMFAPIError) as ei:
        read_sdmx_csv(load(fixtures_dir, "imf_error_synthetic.json"))
    assert "Synthetic error message" in str(ei.value)
    with pytest.raises(IMFResponseError):
        read_sdmx_csv(b"A,B\n1,2\n")
    with pytest.raises(IMFResponseError):
        read_sdmx_csv(b'{"data": {}}')


def test_read_sdmx_json_decodes_all_attribute_levels(fixtures_dir):
    raw = read_sdmx_json(load(fixtures_dir, "imf_weo_json_synthetic.json"))
    assert raw.attrs["dimensions"] == ["COUNTRY", "INDICATOR", "FREQUENCY"]
    assert raw.attrs["structure_id"] == "IMF.RES:WEO(9.0.0)"
    debt = raw[raw["INDICATOR"] == "GGXWDG_NGDP"]
    assert set(debt["UNIT"]) == {"PT"}  # dimension group
    assert set(debt["SCALE"]) == {"0"}  # coded series attribute
    assert set(debt["LATEST_ACTUAL_ANNUAL_DATA"]) == {"FY2022/23"}  # literal series attribute
    assert set(debt["COUNTRY_UPDATE_DATE"]) == {"1/15/2026"}
    assert set(debt["DERIVATION_TYPE"]) == {"M"}  # observation attribute
    null_obs = raw[(raw["INDICATOR"] == "NGDP_RPCH") & (raw["TIME_PERIOD"] == "2028")]
    assert null_obs["OBS_VALUE"].iloc[0] == "" and null_obs["STATUS"].iloc[0] == "NA"


def test_read_sdmx_json_empty_dataset(fixtures_dir):
    raw = read_sdmx_json(load(fixtures_dir, "imf_empty_json_synthetic.json"))
    assert raw.empty
    assert read_sdmx_json(b"").empty


def test_read_sdmx_json_suppressed_group_values_give_no_unit():
    """An index into an empty dimension-group ``values`` list must not be read as a unit."""
    msg = {
        "data": {
            "dataSets": [{"series": {"0:0": {"observations": {"0": ["1.0"]}}}, "dimensionGroupAttributes": {":0:": [0]}}],
            "structures": [
                {
                    "dimensions": {
                        "series": [{"id": "COUNTRY", "values": [{"id": "KEN"}]}, {"id": "INDICATOR", "values": [{"id": "X"}]}],
                        "observation": [{"id": "TIME_PERIOD", "values": [{"value": "2020"}]}],
                    },
                    "attributes": {"dimensionGroup": [{"id": "UNIT", "relationship": {"dimensions": ["INDICATOR"]}, "values": []}]},
                }
            ],
        }
    }
    raw = read_sdmx_json(json.dumps(msg).encode())
    assert "UNIT" not in raw.columns


# ---------------------------------------------------------------------------
# WEO parsing and forecast flags
# ---------------------------------------------------------------------------
def test_parse_weo_csv_canonical_frame(fixtures_dir):
    parsed = parse_weo(load(fixtures_dir, "imf_weo_csv_synthetic.csv"), publication_year=2026)
    df = parsed.frame
    validate_frame(df)
    assert list(df.columns) == COLUMNS
    assert set(df["source"]) == {"imf"} and set(df["dataset"]) == {"WEO"}
    assert value(df, "KEN", "GGXWDG_NGDP", "2023") == 65.0
    assert value(df, "KEN", "NGDPD", "2020") == 1e11  # base units, SCALE 9 not applied
    assert df.loc[df["indicator"] == "GGXWDG_NGDP", "unit"].unique().tolist() == ["% of GDP"]
    assert df.loc[df["indicator"] == "NGDP_RPCH", "unit"].unique().tolist() == ["annual % change"]
    assert df.loc[df["indicator"] == "NGDPD", "unit"].unique().tolist() == ["current US$"]
    assert df.loc[df["indicator"] == "GGXWDG_NGDP", "indicator_label"].iloc[0] == "General government gross debt"
    assert parsed.dropped_missing == 1  # KEN BCA_NGDPD 2027 is empty
    assert not ((df["iso3"] == "KEN") & (df["indicator"] == "BCA_NGDPD") & (df["period"] == "2027")).any()
    assert set(df["obs_status"]) <= OBS_STATUS


def test_parse_weo_forecast_flags_from_last_actual(fixtures_dir):
    df = parse_weo(load(fixtures_dir, "imf_weo_csv_synthetic.csv"), publication_year=2026).frame
    # plain year: last actual 2023
    assert status(df, "KEN", "NGDP_RPCH", "2023") == ""
    assert status(df, "KEN", "NGDP_RPCH", "2024") == "forecast"
    # fiscal year FY2022/23 -> leading year 2022
    assert status(df, "KEN", "GGXWDG_NGDP", "2022") == ""
    assert status(df, "KEN", "GGXWDG_NGDP", "2023") == "forecast"
    # aggregate with last actual 2024
    assert status(df, "G001", "NGDP_RPCH", "2024") == ""
    assert status(df, "G001", "NGDP_RPCH", "2025") == "forecast"
    # no last actual: publication-year rule (2026 on)
    assert status(df, "UGA", "NGDP_RPCH", "2025") == ""
    assert status(df, "UGA", "NGDP_RPCH", "2026") == "forecast"


def test_parse_weo_distinguish_estimates(fixtures_dir):
    df = parse_weo(load(fixtures_dir, "imf_weo_csv_synthetic.csv"), publication_year=2026, distinguish_estimates=True).frame
    assert status(df, "KEN", "GGXWDG_NGDP", "2023") == "estimate"
    assert status(df, "KEN", "GGXWDG_NGDP", "2025") == "estimate"
    assert status(df, "KEN", "GGXWDG_NGDP", "2026") == "forecast"


def test_parse_weo_series_metadata(fixtures_dir):
    parsed = parse_weo(load(fixtures_dir, "imf_weo_csv_synthetic.csv"), publication_year=2026)
    s = parsed.series.set_index(["iso3", "indicator"])
    assert s.loc[("KEN", "GGXWDG_NGDP"), "last_actual"] == 2022
    assert s.loc[("KEN", "GGXWDG_NGDP"), "last_actual_raw"] == "FY2022/23"
    assert bool(s.loc[("KEN", "GGXWDG_NGDP"), "fiscal_year"]) is True
    assert s.loc[("KEN", "GGXWDG_NGDP"), "country_update_date"] == "2026-01-15"
    assert s.loc[("KEN", "NGDPD"), "scale"] == "9"
    assert pd.isna(s.loc[("UGA", "NGDP_RPCH"), "last_actual"]) or s.loc[("UGA", "NGDP_RPCH"), "last_actual"] is None
    assert parsed.forecast_rules["UGA/NGDP_RPCH"] == "publication_year"
    assert parsed.forecast_rules["KEN/NGDP_RPCH"] == "last_actual"


def test_parse_weo_without_publication_year_flags_only_known_series(fixtures_dir):
    parsed = parse_weo(load(fixtures_dir, "imf_weo_csv_synthetic.csv"))
    df = parsed.frame
    assert status(df, "KEN", "NGDP_RPCH", "2024") == "forecast"
    assert set(df.loc[(df["iso3"] == "UGA") & (df["indicator"] == "NGDP_RPCH"), "obs_status"]) == {""}
    assert parsed.forecast_rules["UGA/NGDP_RPCH"] == "none"
    unknown = next(c for c in forecast_caveats(df, parsed.series, parsed.forecast_rules, None) if c.code == "WEO_FORECAST_BOUNDARY_UNKNOWN")
    assert unknown.severity == "warn" and "UGA/NGDP_RPCH" in unknown.evidence["series"]
    assert "KEN/NGDP_RPCH" not in unknown.evidence["series"]


def test_weo_rules_cover_every_series_even_without_last_actual_column():
    csv = (
        "STRUCTURE[;],STRUCTURE_ID,ACTION,COUNTRY,INDICATOR,FREQUENCY,TIME_PERIOD,OBS_VALUE\n"
        "dataflow,IMF.RES:WEO(9.0.0),I,G001,NGDP_RPCH,A,2099,1\n"
    )
    assert parse_weo(csv).forecast_rules == {"G001/NGDP_RPCH": "none"}
    assert parse_sdmx(csv, dataset="BOP").forecast_rules == {}  # no WEO rules for other flows


def test_csv_and_json_give_the_same_frame(fixtures_dir):
    csv_df = parse_weo(load(fixtures_dir, "imf_weo_csv_synthetic.csv"), publication_year=2026).frame
    json_p = parse_weo(load(fixtures_dir, "imf_weo_json_synthetic.json"), fmt="json", publication_year=2026)
    json_df = json_p.frame
    sub = csv_df[(csv_df["iso3"] == "KEN") & csv_df["indicator"].isin(["GGXWDG_NGDP", "NGDP_RPCH"])].reset_index(drop=True)
    pd.testing.assert_frame_equal(sub, json_df.reset_index(drop=True))
    assert json_p.obs_status_raw == {"NA": 1}
    assert json_p.dropped_missing == 1


def test_apply_forecast_flags_pure():
    frame = pd.DataFrame(
        {
            "iso3": ["KEN", "KEN", "KEN"],
            "indicator": ["X", "X", "X"],
            "period": ["2023", "2024Q1", "2025"],
            "obs_status": ["", "", "break"],
        }
    )
    out, rules = apply_forecast_flags(frame, {("KEN", "X"): 2023})
    assert out["obs_status"].tolist() == ["", "forecast", "forecast"]
    assert rules == {"KEN/X": "last_actual"}
    assert frame["obs_status"].tolist() == ["", "", "break"]  # input untouched


def test_unit_for_rules():
    from cgjrkit.core.schema import ppp_base

    assert unit_for("GGXWDG_NGDP") == "% of GDP"
    assert unit_for("XYZ_NGDP", "PT") == "% of GDP"
    assert unit_for("XYZ_RPCH", "PT") == "annual % change"
    assert unit_for("ABC", "USD") == "current US$"
    # PPP: only dollar levels are PPP-dollar units (UNIT_CODE is null for these concepts)
    assert unit_for("PPPGDP") == "current international $ (PPP base year unknown)"
    assert unit_for("PPPPC") == "current international $ (PPP base year unknown)"
    assert unit_for("NGDPRPPPPC") == "constant international $ (PPP base year unknown)"
    assert unit_for("XYZPPP") == "international $ (PPP base year unknown)"  # generic level rule
    assert unit_for("PPPSH", "PT") == "% of world GDP (valued at purchasing power parity)"
    assert unit_for("PPPEX") == "national currency per international $"
    assert unit_for("XYZPPPSH", "PT") == "percent" and unit_for("XYZPPPEX", "") == ""
    assert ppp_base(unit_for("PPPGDP")) == "unknown"
    assert ppp_base(unit_for("PPPSH")) is None and ppp_base(unit_for("PPPEX")) is None
    assert unit_for("ABC", "QQ") == "QQ"
    assert unit_for("ABC", "PT", {"ABC": "custom"}) == "custom"


def test_parse_publication_date(fixtures_dir):
    assert parse_publication_date(load(fixtures_dir, "imf_weo_publication_synthetic.csv")) == "2026-04-14"
    assert parse_publication_date(load(fixtures_dir, "imf_weo_csv_synthetic.csv")) == ""


# ---------------------------------------------------------------------------
# generic SDMX (BOP)
# ---------------------------------------------------------------------------
def test_parse_sdmx_bop_indicator_mapping(fixtures_dir):
    parsed = parse_sdmx(load(fixtures_dir, "imf_bop_csv_synthetic.csv"), dataset="BOP")
    df = parsed.frame
    validate_frame(df)
    assert "NETCD_T.CAB.USD" in set(df["indicator"])
    assert value(df, "KEN", "NETCD_T.CAB.USD", "2017") == -2e9
    assert status(df, "KEN", "NETCD_T.CAB.USD", "2018") == "estimate"  # STATUS 'E'
    assert parsed.unknown_obs_status == {"ZZ": 1}
    assert set(df["unit"]) == {"current US$"}
    assert parsed.forecast_rules == {}  # no forecast logic for BOP


# ---------------------------------------------------------------------------
# structure messages and DataMapper
# ---------------------------------------------------------------------------
def test_parse_dataflows_and_weo_vintages(fixtures_dir):
    flows = parse_dataflows(load(fixtures_dir, "imf_weo_flows_synthetic.json"))
    assert {"id", "agency", "version", "name", "structure", "last_updated"} <= set(flows.columns)
    dates = {("WEO", "9.0.0"): "2026-04-14", ("WEO", "6.0.0"): "2025-04-22", ("WEO_2025_OCT_VINTAGE", "1.0.0"): "2025-10-14"}
    v = weo_vintages_from_flows(flows, dates)
    assert v[["dataflow", "version", "vintage"]].values.tolist() == [
        ["WEO", "9.0.0", "2026-04"],
        ["WEO_2025_OCT_VINTAGE", "1.0.0", "2025-10"],
        ["WEO", "6.0.0", "2025-04"],  # an old bare version keeps serving its release
    ]
    assert v["selected"].all() and v["latest"].tolist() == [True, False, False]
    assert set(v["label_source"]) == {"PUBLICATION_DATE"}
    assert weo_flow_mapping(v) == {(2026, 4): ("WEO", "9.0.0"), (2025, 10): ("WEO_2025_OCT_VINTAGE", "1.0.0"), (2025, 4): ("WEO", "6.0.0")}
    # without probes: bare versions are unlabelled (never "latest" by assumption), vintage flows use their id
    bare = weo_vintages_from_flows(flows)
    assert bare.loc[bare["dataflow"] == "WEO", "vintage"].tolist() == ["", ""]
    assert set(bare.loc[bare["dataflow"] == "WEO", "label"]) == {"WEO (release unknown)"}
    assert weo_flow_mapping(bare) == {(2025, 10): ("WEO_2025_OCT_VINTAGE", "1.0.0")}
    assert bare.loc[bare["dataflow"] == "WEO_2025_OCT_VINTAGE", "label_source"].item() == "flow id"
    assert is_weo_flow("WEO") and is_weo_flow("WEO_2025_OCT_VINTAGE") and not is_weo_flow("PCPS") and not is_weo_flow("FM_2025_OCT_VINTAGE")


def test_weo_flow_mapping_collision_rules():
    """imf-reader's precedence: a bare WEO flow wins over a vintage flow for the same
    release, then the higher dataflow version wins."""
    flows = pd.DataFrame(
        {"id": ["WEO_2025_OCT_VINTAGE", "WEO", "WEO", "WEO"], "agency": "IMF.RES", "version": ["1.0.0", "9.0.0", "10.0.0", "6.0.0"], "name": "", "structure": "", "last_updated": ""}
    )
    dates = {("WEO_2025_OCT_VINTAGE", "1.0.0"): "2025-10-14", ("WEO", "9.0.0"): "2025-10-14", ("WEO", "10.0.0"): "2025-10-15", ("WEO", "6.0.0"): "4/22/2025"}
    mapping = weo_flow_mapping(weo_vintages_from_flows(flows, dates))
    assert mapping == {(2025, 10): ("WEO", "10.0.0"), (2025, 4): ("WEO", "6.0.0")}


def test_parse_codelist(fixtures_dir):
    cl = parse_codelist(load(fixtures_dir, "imf_codelist_country_synthetic.json"))
    assert dict(zip(cl["code"], cl["name"]))["KEN"] == "Kenya"
    assert parse_codelist(b'{"data": {"codelists": []}}').empty


def test_parse_datamapper(fixtures_dir):
    df = parse_datamapper(load(fixtures_dir, "imf_datamapper_debt_synthetic.json"))
    validate_frame(df)
    assert set(df["iso3"]) == {"KEN", "UGA"}
    assert value(df, "KEN", "GGXWDG_NGDP", "2023") == 65.0
    assert set(df["dataset"]) == {"WEO (DataMapper)"}
    assert set(df["unit"]) == {"% of GDP"}
    only_ken = parse_datamapper(load(fixtures_dir, "imf_datamapper_debt_synthetic.json"), countries=["KEN"])
    assert set(only_ken["iso3"]) == {"KEN"}
    assert parse_datamapper(b'{"api": {"version": "1"}}').empty


def test_parse_datamapper_groups_and_aggregates(fixtures_dir):
    regions = parse_datamapper_groups(load(fixtures_dir, "imf_datamapper_regions_synthetic.json"), "regions")
    groups = parse_datamapper_groups(load(fixtures_dir, "imf_datamapper_groups_synthetic.json"), "groups")
    assert set(regions) == {"SSQ", "WHQ"} and set(groups) == {"ADVEC", "SYQ"}
    assert build_datamapper_groups_request("groups") == (f"{DM}/groups", {})
    with pytest.raises(ValueError):
        build_datamapper_groups_request("countries")
    assert is_aggregate("G001") and is_aggregate("ADVEC") and is_aggregate("SSQ") and is_aggregate("WHQ")
    assert not is_aggregate("KEN") and not is_aggregate("SYQ") and is_aggregate("SYQ", frozenset(groups))


def test_infer_release_from_date():
    assert infer_release_from_date("2026-02-10T00:00:00Z") == (2025, 10)
    assert infer_release_from_date("2026-04-30") == (2025, 10)  # mid-April release dates vary: wait for month end
    assert infer_release_from_date("2026-05-01") == (2026, 4)
    assert infer_release_from_date("2026-10-31") == (2026, 4)
    assert infer_release_from_date("2026-11-01") == (2026, 10)
    assert infer_release_from_date("") is None


def test_parse_datamapper_indicators(fixtures_dir):
    meta = parse_datamapper_indicators(load(fixtures_dir, "imf_datamapper_indicators_synthetic.json"))
    row = meta.set_index("code").loc["GGXWDG_NGDP"]
    assert row["unit"] == "Percent of GDP" and row["dataset"] == "WEO"
    assert infer_vintage_from_text(row["source"]) == (2026, 4)


def test_imf_api_error_from_fetch_error():
    exc = http.FetchError('404 from https://api.imf.org/x: {"code": "NotFound", "message": "No such flow", "correlationId": "abc"}')
    err = IMFAPIError.from_fetch_error(exc)
    assert err.status == 404 and err.message == "No such flow" and err.code == "NotFound" and err.correlation_id == "abc"
    assert err.url == "https://api.imf.org/x"
    truncated = IMFAPIError.from_fetch_error(http.FetchError('500 from https://h/y: {"message": "boom", "detail'))
    assert truncated.status == 500 and truncated.message == "boom"
    other = IMFAPIError.from_fetch_error(http.FetchError("giving up on https://h after 3 attempts: x"))
    assert other.status == 0


# ---------------------------------------------------------------------------
# full fetch paths (seeded cache, offline)
# ---------------------------------------------------------------------------
def seed_weo_catalogue(cache, fixtures_dir, probes: bool = True):
    """The IMF.RES allstubs catalogue (WEO 9.0.0, WEO 6.0.0, WEO_2025_OCT_VINTAGE 1.0.0)
    and, optionally, the PUBLICATION_DATE probe of each WEO (flow, version)."""
    seed(cache, build_weo_vintages_request(), load(fixtures_dir, "imf_weo_flows_synthetic.json"))
    if probes:
        seed(cache, build_publication_probe("WEO", "9.0.0"), load(fixtures_dir, "imf_weo_publication_synthetic.csv"))
        seed(cache, build_publication_probe("WEO", "6.0.0"), load(fixtures_dir, "imf_weo_publication_600_synthetic.csv"))
        seed(cache, build_publication_probe("WEO_2025_OCT_VINTAGE", "1.0.0"), load(fixtures_dir, "imf_weo_publication_2025oct_synthetic.csv"))


def seed_latest_weo(cache, fixtures_dir, inds=("GGXWDG_NGDP", "NGDP_RPCH", "BCA_NGDPD", "NGDPD"), ctys=("KEN", "UGA", "G001")):
    seed(cache, build_weo_request(list(inds), list(ctys)), load(fixtures_dir, "imf_weo_csv_synthetic.csv"))
    seed(cache, build_publication_probe(), load(fixtures_dir, "imf_weo_publication_synthetic.csv"))


def test_fetch_weo_latest(imf, cache, fixtures_dir):
    seed_latest_weo(cache, fixtures_dir)
    res = imf.fetch_weo(["GGXWDG_NGDP", "NGDP_RPCH", "BCA_NGDPD", "NGDPD"], ["KEN", "UGA", "G001"])
    validate_frame(res.data)
    assert res.provenance[0].vintage == "WEO 2026-04"
    assert res.provenance[0].dataset == "WEO"
    assert res.provenance[0].citation == "IMF, World Economic Outlook database"
    assert res.provenance[0].payload_sha256
    assert "copyright@imf.org" in res.provenance[0].license
    assert any("copyright@imf.org" in n for n in res.provenance[0].notes)
    assert any("version 9.0.0" in n for n in res.provenance[0].notes)
    assert res.provenance[1].tool == "IMF.weo_publication_date"
    assert res.meta["publication_date"] == "2026-04-14"
    assert res.meta["aggregates"] == ["G001"]
    assert res.meta["structure_version"] == "9.0.0"
    assert res.meta["scale"]["NGDPD"] == "9"
    assert {"IMF_TERMS", "IMF_VINTAGE_LATEST", "WEO_FORECAST_YEARS", "WEO_FORECAST_BOUNDARY_APPROX", "WEO_FISCAL_YEAR", "IMF_SCALE_INFORMATIONAL"} <= codes(res)
    fc = next(c for c in res.caveats if c.code == "WEO_FORECAST_YEARS")
    assert fc.severity == "warn" and fc.evidence["first_period"]["KEN/GGXWDG_NGDP"] == "2023"
    assert res.source_line() == "Source: IMF, World Economic Outlook database (WEO 2026-04)."
    assert not res.blocked
    ken = next(s for s in res.meta["series"] if s["iso3"] == "KEN" and s["indicator"] == "GGXWDG_NGDP")
    assert ken["last_actual"] == 2022 and ken["fiscal_year"] is True
    json.dumps(res.summary())  # JSON-safe


def test_fetch_weo_years_and_alias(imf, cache, fixtures_dir):
    seed_latest_weo(cache, fixtures_dir, inds=("GGXWDG_NGDP",), ctys=("KEN",))
    res = imf.fetch_weo("gross_debt", "KEN", start=2021, end=2024)
    assert sorted(res.data["period"].unique()) == ["2021", "2022", "2023", "2024"]
    # the shared fixture also carries other series; the frame keeps what the API returned
    assert value(res.data, "KEN", "GGXWDG_NGDP", "2022") == 60.0


def test_fetch_weo_missing_series_caveat(imf, cache, fixtures_dir):
    seed_latest_weo(cache, fixtures_dir, inds=("GGXWDG_NGDP",), ctys=("KEN", "SSA"))
    res = imf.fetch_weo("GGXWDG_NGDP", ["KEN", "SSA"])
    miss = next(c for c in res.caveats if c.code == "IMF_MISSING_SERIES")
    assert "SSA/GGXWDG_NGDP" in miss.evidence["missing"]
    assert "G-codes" in miss.message
    assert res.meta["missing"] == ["SSA/GGXWDG_NGDP"]


def test_fetch_weo_vintage_flow(imf, cache, fixtures_dir):
    seed_weo_catalogue(cache, fixtures_dir)
    seed(cache, build_weo_request(["GGXWDG_NGDP"], ["KEN"], flow="WEO_2025_OCT_VINTAGE", version="1.0.0"), load(fixtures_dir, "imf_weo_2025oct_csv_synthetic.csv"))
    res = imf.fetch_weo("GGXWDG_NGDP", "KEN", vintage="Oct 2025")
    assert res.provenance[0].vintage == "WEO 2025-10"
    assert "/WEO_2025_OCT_VINTAGE/1.0.0/" in res.provenance[0].endpoint  # explicit version, not "+"
    # data, then how the release was resolved: catalogue and the selected flow's probe
    assert [p.tool for p in res.provenance] == ["IMF.fetch_weo", "IMF.list_weo_vintages", "IMF.weo_publication_date"]
    assert res.meta["vintage_resolution"]["label_source"] == "PUBLICATION_DATE"
    assert res.meta["flow_version"] == "1.0.0"
    assert "IMF_VINTAGE_LATEST" not in codes(res)
    assert status(res.data, "KEN", "GGXWDG_NGDP", "2022") == ""
    assert status(res.data, "KEN", "GGXWDG_NGDP", "2023") == "forecast"


def test_fetch_weo_current_release_by_name(imf, cache, fixtures_dir):
    """The current release has no vintage flow: it resolves to the bare WEO
    version whose PUBLICATION_DATE says it holds it, requested explicitly."""
    seed_weo_catalogue(cache, fixtures_dir)
    seed(cache, build_weo_request(["GGXWDG_NGDP"], ["KEN"], flow="WEO", version="9.0.0"), load(fixtures_dir, "imf_weo_csv_synthetic.csv"))
    res = imf.fetch_weo("GGXWDG_NGDP", "KEN", vintage="2026-04")
    assert res.provenance[0].vintage == "WEO 2026-04"
    assert res.meta["dataflow"] == "WEO" and res.meta["flow_version"] == "9.0.0"
    assert res.meta["requests"] == [f"{SDMX}/data/dataflow/IMF.RES/WEO/9.0.0/KEN.GGXWDG_NGDP.A"]
    assert res.meta["publication_date"] == "2026-04-14"


def test_fetch_weo_april_2025_from_older_bare_weo_version(imf, cache, fixtures_dir):
    """April 2025 has no WEO_2025_APR_VINTAGE flow: it is served by bare WEO 6.0.0
    (imf-reader 2.2.0 maps ('April', 2025) to FlowRef('WEO', '6.0.0'))."""
    seed_weo_catalogue(cache, fixtures_dir)
    seed(cache, build_weo_request(["GGXWDG_NGDP"], ["KEN"], flow="WEO", version="6.0.0"), load(fixtures_dir, "imf_weo_2025apr_csv_synthetic.csv"))
    res = imf.fetch_weo("GGXWDG_NGDP", "KEN", vintage="2025-04")
    validate_frame(res.data)
    assert res.provenance[0].vintage == "WEO 2025-04"
    assert "/WEO/6.0.0/" in res.provenance[0].endpoint
    assert any("version 6.0.0" in n for n in res.provenance[0].notes)
    assert status(res.data, "KEN", "GGXWDG_NGDP", "2023") == "" and status(res.data, "KEN", "GGXWDG_NGDP", "2024") == "forecast"
    assert res.source_line() == "Source: IMF, World Economic Outlook database (WEO 2025-04)."


def test_fetch_weo_catalogued_vintage_with_no_matching_series(imf, cache, fixtures_dir):
    """The vintage flow exists (catalogue) but the codes match nothing: an empty
    result with a missing-series caveat, not an error."""
    seed_weo_catalogue(cache, fixtures_dir)
    seed(cache, build_weo_request(["GGXWDG_NGDP"], ["XKX"], flow="WEO_2025_OCT_VINTAGE", version="1.0.0"), b"")
    res = imf.fetch_weo("GGXWDG_NGDP", "XKX", vintage="2025-10")
    assert res.data.empty
    validate_frame(res.data)
    assert res.provenance[0].vintage == "WEO 2025-10"
    assert res.meta["missing"] == ["XKX/GGXWDG_NGDP"]
    assert "IMF_MISSING_SERIES" in codes(res)


def test_fetch_weo_unavailable_vintage_raises(imf, cache, fixtures_dir):
    seed_weo_catalogue(cache, fixtures_dir)
    with pytest.raises(IMFAPIError) as ei:
        imf.fetch_weo("GGXWDG_NGDP", "KEN", vintage="2024-10")
    assert ei.value.status == 404
    assert "WEO 2024-10" in str(ei.value) and "WEO 2026-04, WEO 2025-10, WEO 2025-04" in str(ei.value)


def test_fetch_weo_vintage_with_failed_probes(imf, cache, fixtures_dir):
    """Probes not reachable: bare versions stay unlabelled, so April 2025 cannot be
    found (NetworkBlocked, naming the failed probes); a vintage flow still resolves
    from its id."""
    seed_weo_catalogue(cache, fixtures_dir, probes=False)
    with pytest.raises(http.NetworkBlocked, match="probes failed"):
        imf.fetch_weo("GGXWDG_NGDP", "KEN", vintage="2025-04")
    seed(cache, build_weo_request(["GGXWDG_NGDP"], ["KEN"], flow="WEO_2025_OCT_VINTAGE", version="1.0.0"), load(fixtures_dir, "imf_weo_2025oct_csv_synthetic.csv"))
    res = imf.fetch_weo("GGXWDG_NGDP", "KEN", vintage="2025-10")
    assert res.provenance[0].vintage == "WEO 2025-10"
    assert res.meta["vintage_resolution"]["label_source"] == "flow id"
    assert set(res.meta["vintage_resolution"]["probe_failures"]) == {"WEO/9.0.0", "WEO/6.0.0", "WEO_2025_OCT_VINTAGE/1.0.0"}


def test_fetch_weo_vintage_never_falls_back_to_datamapper(imf, cache, fixtures_dir):
    url, _ = build_datamapper_request("GGXWDG_NGDP", ["KEN"])
    put(cache, url, {}, load(fixtures_dir, "imf_datamapper_debt_synthetic.json"))
    with pytest.raises(http.NetworkBlocked):
        imf.fetch_weo("GGXWDG_NGDP", "KEN", vintage="2025-10")


def test_fetch_weo_probe_failure_leaves_vintage_empty(imf, cache, fixtures_dir):
    seed(cache, build_weo_request(["GGXWDG_NGDP"], ["KEN"]), load(fixtures_dir, "imf_weo_csv_synthetic.csv"))
    res = imf.fetch_weo("GGXWDG_NGDP", "KEN")  # probe not cached -> offline NetworkBlocked
    assert res.provenance[0].vintage == ""
    assert "IMF_VINTAGE_UNRESOLVED" in codes(res)
    assert status(res.data, "KEN", "GGXWDG_NGDP", "2023") == "forecast"  # last-actual rule still applies
    # series without a last actual cannot be flagged at all: a caveat names them
    unknown = next(c for c in res.caveats if c.code == "WEO_FORECAST_BOUNDARY_UNKNOWN")
    assert "UGA/NGDP_RPCH" in unknown.evidence["series"] and "KEN/GGXWDG_NGDP" not in unknown.evidence["series"]


def test_fetch_weo_labels(imf, cache, fixtures_dir):
    seed_latest_weo(cache, fixtures_dir, inds=("GGXWDG_NGDP",), ctys=("KEN",))
    seed(cache, build_codelist_request("CL_WEO_COUNTRY", "IMF.RES"), load(fixtures_dir, "imf_codelist_country_synthetic.json"))
    seed(cache, build_codelist_request("CL_WEO_INDICATOR", "IMF.RES"), load(fixtures_dir, "imf_codelist_indicator_synthetic.json"))
    res = imf.fetch_weo("GGXWDG_NGDP", "KEN", labels=True)
    assert set(res.data.loc[res.data["iso3"] == "KEN", "country"]) == {"Kenya"}
    assert set(res.data.loc[res.data["iso3"] == "G001", "country"]) == {"World"}


def test_fetch_weo_falls_back_to_datamapper(imf, cache, fixtures_dir):
    url, params = build_datamapper_request("GGXWDG_NGDP", ["KEN", "UGA"])
    put(cache, url, params, load(fixtures_dir, "imf_datamapper_debt_synthetic.json"))
    put(cache, f"{DM}/indicators", {}, load(fixtures_dir, "imf_datamapper_indicators_synthetic.json"))
    res = imf.fetch_weo("GGXWDG_NGDP", ["KEN", "UGA"])  # SDMX not cached -> offline -> fallback
    validate_frame(res.data)
    assert {"IMF_FALLBACK_DATAMAPPER", "IMF_VINTAGE_UNPINNED", "IMF_TERMS", "WEO_FORECAST_YEARS"} <= codes(res)
    assert res.provenance[0].vintage == "WEO 2026-04 (DataMapper, unpinned)"
    assert res.provenance[0].tool == "IMF.fetch_datamapper"
    assert set(res.data["dataset"]) == {"WEO (DataMapper)"}
    assert status(res.data, "KEN", "GGXWDG_NGDP", "2025") == ""
    assert status(res.data, "KEN", "GGXWDG_NGDP", "2026") == "forecast"
    assert "NetworkBlocked" in res.meta["fallback_reason"]


def test_fetch_weo_fallback_drops_sdmx_aggregate_codes(imf, cache, fixtures_dir):
    """G-codes are SDMX codes; the DataMapper has its own (SSQ, ADVEC, ...)."""
    url, params = build_datamapper_request("GGXWDG_NGDP", ["KEN", "UGA"])
    put(cache, url, params, load(fixtures_dir, "imf_datamapper_debt_synthetic.json"))
    res = imf.fetch_weo("GGXWDG_NGDP", ["KEN", "UGA", "G001"])
    dropped = next(c for c in res.caveats if c.code == "IMF_FALLBACK_AGGREGATES_DROPPED")
    assert dropped.evidence["dropped"] == ["G001"]
    assert "G001/GGXWDG_NGDP" in res.meta["missing"]
    assert set(res.data["iso3"]) == {"KEN", "UGA"}
    with pytest.raises(http.NetworkBlocked):  # only aggregates requested: nothing to fall back to
        imf.fetch_weo("GGXWDG_NGDP", ["G001"])


def test_fetch_datamapper_aggregates_from_regions_and_groups(imf, cache, fixtures_dir):
    url, params = build_datamapper_request("NGDP_RPCH", ["KEN", "SSQ", "SYQ"])
    payload = {"values": {"NGDP_RPCH": {"KEN": {"2099": 1}, "SSQ": {"2099": 2}, "SYQ": {"2099": 3}}}, "api": {"version": "1"}}
    put(cache, url, params, json.dumps(payload).encode())
    put(cache, f"{DM}/indicators", {}, load(fixtures_dir, "imf_datamapper_indicators_synthetic.json"))
    seed(cache, build_datamapper_groups_request("regions"), load(fixtures_dir, "imf_datamapper_regions_synthetic.json"))
    seed(cache, build_datamapper_groups_request("groups"), load(fixtures_dir, "imf_datamapper_groups_synthetic.json"))
    res = imf.fetch_datamapper("NGDP_RPCH", ["KEN", "SSQ", "SYQ"])
    assert res.meta["aggregates"] == ["SSQ", "SYQ"]
    assert set(res.data.loc[res.data["iso3"] == "SYQ", "country"]) == {"Synthetic label: three-letter test group"}
    assert "IMF_DM_GROUPS_UNAVAILABLE" not in codes(res)


def test_fetch_weo_no_fallback_raises(imf):
    with pytest.raises(http.NetworkBlocked):
        imf.fetch_weo("GGXWDG_NGDP", "KEN", fallback=False)


@pytest.mark.parametrize(
    "retrieved, pub_year, release",
    [("2026-06-01T00:00:00Z", 2026, "WEO 2026-04"), ("2026-03-01T00:00:00Z", 2025, "WEO 2025-10"), ("2026-12-01T00:00:00Z", 2026, "WEO 2026-10")],
)
def test_fetch_datamapper_without_metadata_infers_release_from_retrieval_date(imf, cache, fixtures_dir, monkeypatch, retrieved, pub_year, release):
    url, params = build_datamapper_request("NGDP_RPCH", ["KEN"])
    monkeypatch.setattr(cachemod, "utc_now", lambda: retrieved)
    put(cache, url, params, load(fixtures_dir, "imf_datamapper_growth_synthetic.json"))
    res = imf.fetch_datamapper("NGDP_RPCH", "KEN", with_metadata=False)
    assert res.provenance[0].vintage == ""  # a guess is never written as the vintage
    flagged = res.data[res.data["obs_status"] == "forecast"]["period"].map(int)
    assert flagged.min() == pub_year and (flagged >= pub_year).all()
    unpinned = next(c for c in res.caveats if c.code == "IMF_VINTAGE_UNPINNED")
    assert unpinned.evidence["publication_year_used"] == pub_year
    assert unpinned.evidence["release_assumed_from_retrieval_date"] == release
    assert "inferred from the retrieval date" in unpinned.message


def test_fetch_sdmx_bop_with_time_filter(imf, cache, fixtures_dir):
    key = "KEN.NETCD_T.CAB.USD.A"
    url, params, _ = build_data_request("IMF.STA", "BOP", key, params={"c[TIME_PERIOD]": "ge:2016-A1+le:2017-A1"})
    put(cache, url, params, load(fixtures_dir, "imf_bop_csv_synthetic.csv"))
    res = imf.fetch_sdmx("BOP", key, start=2016, end=2017)
    validate_frame(res.data)
    assert sorted(res.data["period"].unique()) == ["2016", "2017"]  # client-side filter too
    assert res.provenance[0].dataset == "BOP"
    assert res.provenance[0].vintage == ""  # structure version is not a data release
    assert any("version 21.0.0" in n for n in res.provenance[0].notes)
    assert res.provenance[0].citation == "IMF, Balance of Payments Statistics"
    assert res.meta["agency"] == "IMF.STA"
    assert "IMF_VINTAGE_LATEST" in codes(res)


def test_fetch_sdmx_key_as_slots_and_json(imf, cache, fixtures_dir):
    url, params, headers = build_data_request("IMF.RES", "WEO", "KEN.GGXWDG_NGDP+NGDP_RPCH.A", fmt="json")
    put(cache, url, params, load(fixtures_dir, "imf_weo_json_synthetic.json"))
    res = imf.fetch_sdmx("WEO", ["KEN", ["GGXWDG_NGDP", "NGDP_RPCH"], "A"], fmt="json")
    assert set(res.data["indicator"]) == {"GGXWDG_NGDP", "NGDP_RPCH"}
    assert set(res.data["dataset"]) == {"WEO"}
    assert status(res.data, "KEN", "NGDP_RPCH", "2024") == "forecast"


def test_fetch_sdmx_empty_result(imf, cache):
    url, params, _ = build_data_request("IMF.STA", "BOP", "XXX.NETCD_T.CAB.USD.A")
    put(cache, url, params, b"")
    res = imf.fetch_sdmx("BOP", "XXX.NETCD_T.CAB.USD.A")
    assert res.data.empty
    validate_frame(res.data)
    assert "IMF_NO_DATA" in codes(res)


def test_fetch_sdmx_rejects_bad_key(imf):
    with pytest.raises(ValueError):
        imf.fetch_sdmx("BOP", "KEN/../x", agency="IMF.STA")


def test_resolve_agency_from_catalogue(imf, cache, fixtures_dir):
    assert imf.resolve_agency("BOP") == "IMF.STA"
    assert imf.resolve_agency("WEO_2025_OCT_VINTAGE") == "IMF.RES"
    url, params, _ = build_dataflows_request()
    put(cache, url, params, load(fixtures_dir, "imf_catalogue_synthetic.json"))
    assert imf.resolve_agency("SYNTHFLOW") == "IMF.SYN"
    with pytest.raises(ValueError):
        imf.resolve_agency("NOPE")


def test_fetch_bop_items(imf, cache, fixtures_dir):
    key = "KEN+UGA.NETCD_T+NNAFANIL_T.CAB+GS+D_F.USD.A"
    url, params, _ = build_data_request("IMF.STA", "BOP", key, params={"c[TIME_PERIOD]": "ge:2016-A1+le:2018-A1"})
    put(cache, url, params, load(fixtures_dir, "imf_bop_csv_synthetic.csv"))
    res = imf.fetch_bop(["current_account", "goods_services", "direct_investment"], ["KEN", "UGA"], 2016, 2018)
    validate_frame(res.data)
    assert set(res.data["indicator"]) == {"NETCD_T.CAB.USD", "NETCD_T.GS.USD", "NNAFANIL_T.D_F.USD"}
    assert "NNAFANIL_T.GS.USD" not in set(res.data["indicator"])  # cross-product series dropped
    assert value(res.data, "KEN", "NNAFANIL_T.D_F.USD", "2016") == -5e8
    assert res.data.loc[res.data["indicator"] == "NETCD_T.CAB.USD", "indicator_label"].iloc[0] == BOP_ITEMS["current_account"][2]
    assert {"BOP_BPM6_SIGN", "BOP_BASE_UNITS", "IMF_MISSING_SERIES"} <= codes(res)
    assert "UGA/NETCD_T.GS.USD" in res.meta["missing"]
    assert res.provenance[0].tool == "IMF.fetch_bop"


def test_fetch_bop_rejects_unknown_item(imf):
    with pytest.raises(ValueError):
        imf.fetch_bop("not_an_item", "KEN")


def test_list_weo_vintages_and_codelist(imf, cache, fixtures_dir):
    seed_weo_catalogue(cache, fixtures_dir)
    v = imf.list_weo_vintages()
    assert "WEO_2025_OCT_VINTAGE" in set(v["dataflow"])
    # bare versions are labelled by the release they probe to, not "latest"
    assert v.loc[(v["dataflow"] == "WEO") & (v["version"] == "6.0.0"), "label"].item() == "WEO 2025-04"
    assert v.loc[v["latest"], ["dataflow", "version", "label"]].values.tolist() == [["WEO", "9.0.0", "WEO 2026-04"]]
    assert "latest" not in set(v["vintage"])
    assert v.attrs["provenance"][0]["tool"] == "IMF.list_weo_vintages"
    assert [p["tool"] for p in v.attrs["provenance"][1:]] == ["IMF.weo_publication_date"] * 3
    assert v.attrs["probe_failures"] == {}
    seed(cache, build_codelist_request("CL_WEO_COUNTRY", "IMF.RES"), load(fixtures_dir, "imf_codelist_country_synthetic.json"))
    assert "KEN" in set(imf.fetch_codelist("CL_WEO_COUNTRY", "IMF.RES")["code"])


def test_connector_declares_domains_and_terms():
    assert set(IMF.domains) == {"api.imf.org", "www.imf.org"}
    assert IMF.source == "imf"
    assert "written permission" in IMF.license
