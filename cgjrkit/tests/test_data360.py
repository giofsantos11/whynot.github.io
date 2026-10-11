"""Unit tests for the Data360 connector (synthetic fixtures, no network)."""

import json

import pandas as pd
import pytest

from cgjrkit.connectors import data360 as d360
from cgjrkit.connectors.data360 import Data360
from cgjrkit.core.cache import Cache
from cgjrkit.core.http import NetworkBlocked
from cgjrkit.core.schema import validate_frame


def _load(fixtures_dir, name) -> bytes:
    return (fixtures_dir / name).read_bytes()


def _seed(url, params, payload, body=None):
    """Store a payload in the test cache under the key http.fetch will look up."""
    key_params = dict(params or {})
    if body is not None:
        key_params["__body__"] = body
    Cache().put(Data360.source, url, key_params, payload)


#: Breakdown codes of the synthetic ES fixture that carry statistics. The real WB_ES
#: encoding is UNVERIFIED, so the module default (ES_STATISTIC_CODES) is empty.
SYNTH_STATS = {"SYNTH_SE": "se", "SYNTH_NOBS": "n"}


def _row(**over) -> dict:
    """One synthetic row in the documented 24-field layout (OpenAPI DataResponse sample)."""
    row = {
        "OBS_VALUE": "10", "TIME_FORMAT": "P1Y", "UNIT_MULT": 0, "COMMENT_OBS": None, "OBS_STATUS": "A",
        "OBS_CONF": "PU", "AGG_METHOD": "_Z", "DECIMALS": "0", "COMMENT_TS": "Synthetic", "DATA_SOURCE": "SYN",
        "LATEST_DATA": False, "DATABASE_ID": "SYN", "INDICATOR": "SYN_A", "REF_AREA": "KEN", "SEX": "_T",
        "AGE": "_T", "URBANISATION": "_T", "COMP_BREAKDOWN_1": "_Z", "COMP_BREAKDOWN_2": "_Z",
        "COMP_BREAKDOWN_3": "_Z", "TIME_PERIOD": "2099", "FREQ": "A", "UNIT_MEASURE": "PT", "UNIT_TYPE": None,
    }
    row.update(over)
    return row


def _lpi_rows(fixtures_dir):
    rows = []
    for page in ("data360_wb_lpi_20_page1_synthetic.json", "data360_wb_lpi_20_page2_synthetic.json"):
        rows += d360.parse_page(_load(fixtures_dir, page))[0]
    return rows


# -- request builders ------------------------------------------------------------


def test_build_data_request_is_sorted_and_validated():
    url, params = d360.build_data_request("WB_ES", ["WB_ES_T_B", "WB_ES_T_A"], ["uga", "KEN"], 2010, 2020, {"SEX": "F"})
    assert url == "https://data360api.worldbank.org/data360/data"
    assert params == {
        "DATABASE_ID": "WB_ES",
        "INDICATOR": "WB_ES_T_A,WB_ES_T_B",
        "REF_AREA": "KEN,UGA",
        "SEX": "F",
        "timePeriodFrom": "2010",
        "timePeriodTo": "2020",
        "skip": 0,
        "top": 1000,
    }
    # argument order does not change the request (stable cache keys)
    assert d360.build_data_request("WB_ES", "WB_ES_T_A,WB_ES_T_B", "KEN,UGA", 2010, 2020, {"SEX": "F"}) == (url, params)
    with pytest.raises(ValueError, match="unsupported Data360 filter"):
        d360.build_data_request("WB_ES", filters={"BOGUS": "x"})
    with pytest.raises(ValueError, match="invalid Data360 indicator id"):
        d360.build_data_request("WB_ES", ["WB_ES' or 1 eq 1"])


def test_build_metadata_request_follows_openapi_example():
    url, body = d360.build_metadata_request(indicator_id="WB_ES_T_SYNTH1")
    assert url.endswith("/data360/metadata")
    assert body == {"query": "&$filter=series_description/idno eq 'WB_ES_T_SYNTH1'"}
    _, body = d360.build_metadata_request(database_id="WB_ES", select=["idno", "name"])
    assert body["query"] == "&$filter=series_description/database_id eq 'WB_ES'&$select=series_description/idno,series_description/name"
    with pytest.raises(ValueError):
        d360.build_metadata_request()
    with pytest.raises(ValueError):
        d360.build_metadata_request("A", "B")


def test_other_builders():
    assert d360.build_indicators_request("WB_LPI_20") == (d360.INDICATORS_URL, {"datasetId": "WB_LPI_20"})
    assert d360.build_disaggregation_request("WB_ES", "WB_ES_T_X")[1] == {"datasetId": "WB_ES", "indicatorId": "WB_ES_T_X"}
    assert d360.build_codelist_request("ref_area") == (d360.CODELIST_URL, {"type": "REF_AREA"})
    url, body = d360.build_search_request("access to finance", top=5)
    assert url == d360.SEARCH_URL and body["search"] == "access to finance" and body["top"] == 5 and body["count"] is True


# -- parsing: Enterprise Surveys ------------------------------------------------------


def test_parse_es_totals_and_statistics(fixtures_dir):
    rows, count = d360.parse_page(_load(fixtures_dir, "data360_wb_es_synthetic.json"))
    assert count == len(rows) == 11
    parsed = d360.parse_rows(rows, database_id="WB_ES", detect_statistics=True, statistic_codes=SYNTH_STATS)
    df = parsed.frame
    validate_frame(df)
    assert set(df["indicator"]) == {"WB_ES_T_SYNTH1", "WB_ES_T_SYNTH2"}
    assert (df["source"] == "data360").all() and (df["dataset"] == "WB_ES").all()
    val = df.set_index(["indicator", "iso3", "period"])["value"]
    assert val[("WB_ES_T_SYNTH1", "KEN", "2018")] == 20.0
    assert val[("WB_ES_T_SYNTH2", "UGA", "2013")] == 60.0
    # OBS_STATUS "E" -> estimate
    status = df.set_index(["indicator", "iso3", "period"])["obs_status"]
    assert status[("WB_ES_T_SYNTH1", "UGA", "2013")] == "estimate"
    assert df["unit"].eq("percent").all()  # builtin label for PT
    assert parsed.aggregates == ["SSF"]
    assert parsed.dropped_breakdown_rows == 1  # the firm-size breakdown row
    assert parsed.dropped_codes == {"COMP_BREAKDOWN_1": ["SYNTH_SIZE_SMALL"]}
    assert parsed.untotalled == {}

    extras = {(e["indicator"], e["iso3"], e["period"], e["field"]): e["value"] for e in parsed.extras}
    # statistic carried in a breakdown dimension (codes listed in statistic_codes)
    assert extras[("WB_ES_T_SYNTH1", "KEN", "2018", "se")] == 2.0
    assert extras[("WB_ES_T_SYNTH1", "KEN", "2018", "n")] == 500.0
    # statistic carried in COMMENT_OBS
    assert extras[("WB_ES_T_SYNTH2", "KEN", "2018", "se")] == 1.5
    assert extras[("WB_ES_T_SYNTH2", "KEN", "2018", "n")] == 1000.0
    # companion indicators WB_ES_SE_* / WB_ES_N_*
    assert extras[("WB_ES_T_SYNTH2", "UGA", "2013", "se")] == 3.0
    assert extras[("WB_ES_T_SYNTH2", "UGA", "2013", "n")] == 300.0


def test_parse_es_unlisted_statistic_codes_are_breakdowns(fixtures_dir):
    """Default: no breakdown code is read as a statistic (the WB_ES encoding is
    UNVERIFIED). The SE/NOBS-coded rows are subgroups of the total, so they are
    dropped and counted; COMMENT_OBS and companion-id statistics still work."""
    assert d360.ES_STATISTIC_CODES == {}
    rows, _ = d360.parse_page(_load(fixtures_dir, "data360_wb_es_synthetic.json"))
    parsed = d360.parse_rows(rows, database_id="WB_ES", detect_statistics=True)
    assert set(parsed.frame["indicator"]) == {"WB_ES_T_SYNTH1", "WB_ES_T_SYNTH2"}
    assert parsed.dropped_breakdown_rows == 3
    assert parsed.dropped_by_indicator == {"WB_ES_T_SYNTH1": 3}
    assert parsed.dropped_codes == {"COMP_BREAKDOWN_1": ["SYNTH_SIZE_SMALL"], "COMP_BREAKDOWN_3": ["SYNTH_NOBS", "SYNTH_SE"]}
    fields = {(e["indicator"], e["iso3"], e["period"], e["field"]) for e in parsed.extras}
    assert ("WB_ES_T_SYNTH1", "KEN", "2018", "se") not in fields
    assert {("WB_ES_T_SYNTH2", "KEN", "2018", "se"), ("WB_ES_T_SYNTH2", "UGA", "2013", "n")} <= fields


def test_statistic_code_needs_its_estimate_row():
    """A listed statistic code is only a statistic next to the estimate it belongs to."""
    se_only = _row(COMP_BREAKDOWN_3="SYN_SE", OBS_VALUE="2")
    parsed = d360.parse_rows([se_only], detect_statistics=True, statistic_codes={"SYN_SE": "se"})
    assert parsed.extras == []
    assert parsed.frame["indicator"].tolist() == ["SYN_A|SYN_SE"]  # kept as its own, clearly coded series
    with_estimate = d360.parse_rows([_row(), se_only], detect_statistics=True, statistic_codes={"SYN_SE": "se"})
    assert with_estimate.frame["indicator"].tolist() == ["SYN_A"]
    assert with_estimate.extras == [{"indicator": "SYN_A", "iso3": "KEN", "period": "2099", "field": "se", "value": 2.0}]


def test_companion_indicator_needs_its_base_in_the_payload():
    lone = [_row(INDICATOR="WB_ES_SE_SYN", OBS_VALUE="3")]
    parsed = d360.parse_rows(lone, detect_statistics=True)
    assert parsed.frame["indicator"].tolist() == ["WB_ES_SE_SYN"] and parsed.extras == []
    paired = d360.parse_rows([_row(INDICATOR="WB_ES_T_SYN"), lone[0]], detect_statistics=True)
    assert paired.frame["indicator"].tolist() == ["WB_ES_T_SYN"]
    assert [(e["indicator"], e["field"], e["value"]) for e in paired.extras] == [("WB_ES_T_SYN", "se", 3.0)]


# -- totals_only: drop a row only when a more aggregated row exists ---------------------


def test_totals_only_keeps_series_without_a_total():
    """The OpenAPI sample's female-only WDI series has SEX='F' and no '_T' row."""
    rows = [_row(INDICATOR="SYN_FE", SEX="F", TIME_PERIOD=t) for t in ("2098", "2099")]
    parsed = d360.parse_rows(rows)
    assert parsed.frame["indicator"].unique().tolist() == ["SYN_FE|F"]
    assert len(parsed.frame) == 2 and parsed.dropped_breakdown_rows == 0
    assert parsed.untotalled == {"SYN_FE": ["SEX"]}
    assert parsed.frame["indicator_label"].iloc[0] == "Synthetic (F)"
    cav = {c.code: c for c in Data360._generic_caveats(parsed, {}, ["SYN_FE"])}
    assert "data360_empty" not in cav and "data360_missing_indicators" not in cav
    assert cav["data360_no_total"].evidence["series"] == {"SYN_FE": ["SEX"]}


def test_totals_only_value_rank_score_without_total_become_separate_series():
    """WEF_TTDI-style COMP_BREAKDOWN_1 = value / rank / score, no '_T'."""
    rows = [_row(INDICATOR="SYN_IDX", COMP_BREAKDOWN_1=c, OBS_VALUE=v) for c, v in (("SYN_VAL", "4"), ("SYN_RNK", "10"), ("SYN_SCR", "50"))]
    parsed = d360.parse_rows(rows)
    assert set(parsed.frame["indicator"]) == {"SYN_IDX|SYN_VAL", "SYN_IDX|SYN_RNK", "SYN_IDX|SYN_SCR"}
    assert parsed.dropped_breakdown_rows == 0


def test_totals_only_drops_only_dominated_rows():
    rows = [
        _row(OBS_VALUE="50"),  # SEX=_T, AGE=_T: the total
        _row(SEX="F", OBS_VALUE="40"),
        _row(SEX="F", AGE="Y15T24", OBS_VALUE="30"),
        _row(INDICATOR="SYN_B", AGE="Y15T24", OBS_VALUE="20"),  # no all-total row for SYN_B ...
        _row(INDICATOR="SYN_B", SEX="F", OBS_VALUE="10"),  # ... and neither row is a total of the other
    ]
    parsed = d360.parse_rows(rows)
    assert set(parsed.frame["indicator"]) == {"SYN_A", "SYN_B|Y15T24", "SYN_B|F"}
    assert parsed.dropped_breakdown_rows == 2 and parsed.dropped_by_indicator == {"SYN_A": 2}
    assert parsed.untotalled == {"SYN_B": ["SEX", "AGE"]}
    # a total in another year does not make this year's subgroup row a breakdown of it
    other_year = d360.parse_rows([_row(TIME_PERIOD="2098"), _row(SEX="F")])
    assert set(other_year.frame["indicator"]) == {"SYN_A", "SYN_A|F"}


def test_breakdown_caveats_distinguish_filtered_from_missing():
    parsed = d360.parse_rows([_row(), _row(SEX="F")])
    cav = {c.code: c for c in Data360._generic_caveats(parsed, {}, ["SYN_A", "SYN_GONE"])}
    assert cav["data360_breakdowns_dropped"].severity == "info"
    assert cav["data360_breakdowns_dropped"].evidence["dropped_breakdown_rows"] == 1
    assert cav["data360_missing_indicators"].evidence["indicators"] == ["SYN_GONE"]
    # if a filter ever leaves a requested indicator with nothing, the caveat says so instead of "no observations"
    emptied = d360.ParsedData(frame=parsed.frame.iloc[0:0], dropped_breakdown_rows=2, dropped_by_indicator={"SYN_A": 2}, rows_by_indicator={"SYN_A": 2})
    cav = {c.code: c for c in Data360._generic_caveats(emptied, {}, ["SYN_A"])}
    assert "data360_empty" not in cav and "data360_missing_indicators" not in cav
    assert cav["data360_all_rows_filtered"].evidence["dropped_breakdown_rows"] == 2
    assert cav["data360_breakdowns_dropped"].severity == "warn"
    assert cav["data360_breakdowns_dropped"].evidence["indicators_with_no_rows_left"] == ["SYN_A"]


def test_provenance_license_from_metadata():
    meta = {"A": {"license": "CC BY 4.0"}, "B": {"license": "CC BY-NC 4.0"}, "C": {"license": ""}}
    assert d360.provenance_license(meta, ["A"]) == ("CC BY 4.0", [])
    assert d360.provenance_license(meta, ["A", "B"]) == ("CC BY 4.0; CC BY-NC 4.0", [])
    assert d360.provenance_license(meta, ["A", "C"]) == ("CC BY 4.0; unverified for 1 indicator(s)", ["C"])
    assert d360.provenance_license({}, ["A"]) == (d360.LICENSE_UNVERIFIED, ["A"])


def test_parse_es_breakdowns_kept_when_requested(fixtures_dir):
    rows, _ = d360.parse_page(_load(fixtures_dir, "data360_wb_es_synthetic.json"))
    parsed = d360.parse_rows(
        rows, database_id="WB_ES", totals_only=False, keep_dims=d360.DIMENSIONS, detect_statistics=True, statistic_codes=SYNTH_STATS
    )
    df = parsed.frame
    validate_frame(df)
    row = df[df["indicator"] == "WB_ES_T_SYNTH1|SYNTH_SIZE_SMALL"]
    assert row["value"].tolist() == [40.0]
    assert parsed.series["WB_ES_T_SYNTH1|SYNTH_SIZE_SMALL"]["COMP_BREAKDOWN_1"] == "SYNTH_SIZE_SMALL"
    assert parsed.dropped_breakdown_rows == 0


def test_parse_without_statistic_detection_keeps_companions_separate(fixtures_dir):
    rows, _ = d360.parse_page(_load(fixtures_dir, "data360_wb_es_synthetic.json"))
    parsed = d360.parse_rows(rows, database_id="WB_ES")
    assert {"WB_ES_SE_SYNTH2", "WB_ES_N_SYNTH2"} <= set(parsed.frame["indicator"])
    assert not any(e["field"] == "se" and e["indicator"] == "WB_ES_T_SYNTH1" for e in parsed.extras)


# -- parsing: LPI 2.0 ----------------------------------------------------------------------


def test_parse_lpi20_metrics_and_observation_groups(fixtures_dir):
    parsed = d360.parse_rows(_lpi_rows(fixtures_dir), database_id="WB_LPI_20", keep_dims=("COMP_BREAKDOWN_1",))
    df = parsed.frame
    validate_frame(df)
    assert set(df["indicator"]) == {"CT_DT_M|WB_LPI_M", "CT_DT_M|WB_LPI_MD", "CT_DT_M|WB_LPI_IQR_75_25", "TRS_T", "CT_MC"}
    med = df[(df["indicator"] == "CT_DT_M|WB_LPI_MD") & (df["iso3"] == "KEN")].set_index("period")["value"]
    assert med.to_dict() == {"2023": 8.0, "2024": 7.0}
    assert df.loc[df["indicator"] == "CT_MC", "unit"].item() == "partners"
    assert df.loc[df["indicator"] == "TRS_T", "unit"].item() == "days"
    assert df.loc[df["indicator"] == "CT_DT_M|WB_LPI_MD", "indicator_label"].iloc[0] == "CT_DT_M (Median)"
    assert parsed.aggregates == ["G_LLD"]
    groups = {(e["indicator"], e["period"], e["field"]): e["value"] for e in parsed.extras if e["iso3"] == "KEN"}
    assert groups[("CT_DT_M|WB_LPI_MD", "2023", "n_obs_group")] == "1000-10000"
    assert groups[("CT_DT_M|WB_LPI_MD", "2024", "n")] == 36.0


def test_parse_page_accepts_odata_count_and_rejects_garbage():
    rows, count = d360.parse_page({"@odata.count": 3, "value": [{"INDICATOR": "X"}]})
    assert count == 3 and len(rows) == 1
    with pytest.raises(ValueError, match="no 'value'"):
        d360.parse_page(b'{"message": "error"}')


def test_unit_folding_and_conflicts():
    base = {"DATABASE_ID": "SYN", "REF_AREA": "KEN", "TIME_PERIOD": "2020", "FREQ": "A", "OBS_STATUS": "A"}
    rows = [
        {**base, "INDICATOR": "SYN_X", "OBS_VALUE": "1.0", "UNIT_MEASURE": "KD"},
        {**base, "INDICATOR": "SYN_X", "OBS_VALUE": "2.0", "UNIT_MEASURE": "CD"},
        {**base, "INDICATOR": "SYN_Y", "OBS_VALUE": "3.0", "UNIT_MEASURE": "PT", "UNIT_MULT": 6},
    ]
    df = d360.parse_rows(rows, database_id="SYN").frame
    validate_frame(df)
    assert set(df["indicator"]) == {"SYN_X|KD", "SYN_X|CD", "SYN_Y"}
    assert df.loc[df["indicator"] == "SYN_Y", "unit"].item() == "percent (x10^6)"
    conflict = [{**base, "INDICATOR": "SYN_Z", "OBS_VALUE": "1"}, {**base, "INDICATOR": "SYN_Z", "OBS_VALUE": "2"}]
    with pytest.raises(ValueError, match="conflicting values"):
        d360.parse_rows(conflict, database_id="SYN")
    # exact duplicates are harmless
    assert len(d360.parse_rows(conflict[:1] * 2, database_id="SYN").frame) == 1


def test_parse_comment_obs_and_periods():
    assert d360.parse_comment_obs("Number of observation group: 10000+") == [("n_obs_group", "10000+")]
    assert d360.parse_comment_obs("Number of observations: 1,234; Std. error: 0.25") == [("n", 1234.0), ("se", 0.25)]
    assert d360.parse_comment_obs("N = 50 | free text") == [("n", 50.0), ("comment_obs", "free text")]
    assert d360.normalize_period("2024") == "2024"
    assert d360.normalize_period("2024-Q2") == "2024Q2"
    assert d360.normalize_period("2024-M03") == "2024-03"
    assert d360.normalize_period("2024-01-01", "A") == "2024"
    assert d360.normalize_period("2024-05-01", "M") == "2024-05"
    assert d360.normalize_period("2024-05-01", "Q") == "2024Q2"
    assert d360.is_aggregate("SSF") and d360.is_aggregate("G_LLD") and not d360.is_aggregate("KEN")


def test_is_cc_by():
    assert d360.is_cc_by("CC BY 4.0") and d360.is_cc_by("CC-BY-4.0")
    assert d360.is_cc_by("Creative Commons Attribution 4.0 International")
    assert not d360.is_cc_by("CC BY-NC 4.0") and not d360.is_cc_by("CC BY-ND 4.0")
    assert not d360.is_cc_by("Synthetic restricted licence") and not d360.is_cc_by("")


# -- parsing: metadata and helper endpoints ---------------------------------------------------


def test_parse_metadata_and_helpers(fixtures_dir):
    meta = d360.parse_metadata(_load(fixtures_dir, "data360_metadata_synth1_synthetic.json"))
    m = meta["WB_ES_T_SYNTH1"]
    assert m["name"] == "Synthetic ES indicator A"
    assert m["version_date"] == "2099-01-01" and m["license"] == "CC BY 4.0"
    assert m["aliases"] == ["SYN.1"]
    assert d360.parse_indicators(_load(fixtures_dir, "data360_indicators_synthetic.json")) == ["WB_ES_T_SYNTH1", "WB_ES_T_SYNTH2"]
    assert d360.parse_codelist(_load(fixtures_dir, "data360_codelist_ref_area_synthetic.json"))["KEN"] == "Kenya"
    assert [s["idno"] for s in d360.parse_search(_load(fixtures_dir, "data360_search_synthetic.json"))] == ["WB_ES_T_SYNTH1", "WB_ES_T_SYNTH2"]
    disagg = d360.parse_disaggregation(_load(fixtures_dir, "data360_disaggregation_synthetic.json"))
    assert disagg["COMP_BREAKDOWN_1"] == ["_T", "SYNTH_SIZE_SMALL"]


# -- fetch (offline, cache seeded with fixtures) -------------------------------------------------


def test_fetch_lpi20_paginates_and_records_provenance(fixtures_dir):
    for skip, page in ((0, "page1"), (4, "page2")):
        url, params = d360.build_data_request("WB_LPI_20", skip=skip, top=4)
        _seed(url, params, _load(fixtures_dir, f"data360_wb_lpi_20_{page}_synthetic.json"))
    res = Data360().fetch_lpi20(page_size=4, with_metadata=False, with_labels=False)
    validate_frame(res.data)
    assert len(res.data) == 7 and res.meta["rows_received"] == 7 == res.meta["total_count"]
    data_provs = [p for p in res.provenance if p.tool == "Data360.fetch_lpi20"]
    assert len(data_provs) == 2 and all(p.payload_sha256 and p.dataset == "WB_LPI_20" for p in data_provs)
    assert data_provs[0].params["skip"] == 0 and data_provs[1].params["skip"] == 4
    codes = {c.code for c in res.caveats}
    assert {"lpi20_series_break", "lpi20_statistics"} <= codes
    assert res.meta["aggregates"] == ["G_LLD"]
    assert any("no release/vintage endpoint" in n for n in data_provs[0].notes)
    # no metadata read: the per-indicator licence is not claimed
    assert all(p.license == d360.LICENSE_UNVERIFIED for p in data_provs)
    assert "data360_license_unverified" in codes

    med = Data360().fetch_lpi20(page_size=4, with_metadata=False, with_labels=False, metrics=["WB_LPI_MD"])
    assert set(med.data["indicator"]) == {"CT_DT_M|WB_LPI_MD"}
    assert all(e["indicator"] == "CT_DT_M|WB_LPI_MD" for e in med.meta["extras"])


def test_fetch_enterprise_surveys_with_metadata_and_labels(fixtures_dir):
    inds = ["WB_ES_T_SYNTH1", "WB_ES_T_SYNTH2"]
    url, params = d360.build_data_request("WB_ES", inds, ["KEN", "UGA", "SSF"])
    _seed(url, params, _load(fixtures_dir, "data360_wb_es_synthetic.json"))
    for ind, fx in (("WB_ES_T_SYNTH1", "synth1"), ("WB_ES_T_SYNTH2", "synth2")):
        murl, body = d360.build_metadata_request(indicator_id=ind)
        _seed(murl, None, _load(fixtures_dir, f"data360_metadata_{fx}_synthetic.json"), body=body)
    for ctype, fx in (("REF_AREA", "ref_area"), ("UNIT_MEASURE", "unit_measure")):
        curl, cparams = d360.build_codelist_request(ctype)
        _seed(curl, cparams, _load(fixtures_dir, f"data360_codelist_{fx}_synthetic.json"))

    res = Data360().fetch_enterprise_surveys(inds, ["KEN", "UGA", "SSF"], statistic_codes=SYNTH_STATS)
    df = res.data
    validate_frame(df)
    assert df.loc[df["indicator"] == "WB_ES_T_SYNTH1", "indicator_label"].iloc[0] == "Synthetic ES indicator A"
    assert df["unit"].eq("Percent of firms (synthetic)").all()
    assert df.loc[df["iso3"] == "KEN", "country"].iloc[0] == "Kenya"
    assert res.meta["standard_errors_found"] and res.meta["sample_sizes_found"]
    assert res.meta["aggregates"] == ["SSF"]
    tools = [p.tool for p in res.provenance]
    assert tools.count("Data360.metadata") == 2 and "Data360.codelist[REF_AREA]" in tools
    data_prov = next(p for p in res.provenance if p.tool == "Data360.fetch_enterprise_surveys")
    assert data_prov.vintage == "version_date 2099-02-01"
    assert data_prov.license == "CC BY 4.0; Synthetic restricted licence"  # per-indicator licences, not the API's
    assert next(p for p in res.provenance if p.tool == "Data360.metadata").license == "CC BY 4.0"
    assert "World Bank Data360 (version_date 2099-02-01)" in res.source_line()
    codes = {c.code: c for c in res.caveats}
    assert codes["data360_license"].severity == "warn"
    assert "es_survey_waves" in codes and "es_se_missing" not in codes
    assert "data360_license_unverified" not in codes
    assert codes["data360_flagged_obs"].evidence["counts"] == {"estimate": 1}
    se = d360.extras_frame(res).set_index(["indicator", "iso3", "period"])
    assert se.loc[("WB_ES_T_SYNTH2", "UGA", "2013"), "se"] == 3.0
    json.dumps(res.summary())  # MCP responses must be JSON-serialisable


def test_fetch_es_without_enrichment_still_returns_data(fixtures_dir):
    url, params = d360.build_data_request("WB_ES", ["WB_ES_T_SYNTH1"])
    payload = json.loads(_load(fixtures_dir, "data360_wb_es_synthetic.json"))
    payload["value"] = [r for r in payload["value"] if r["INDICATOR"] == "WB_ES_T_SYNTH1" and r["COMP_BREAKDOWN_3"] == "_T"]
    payload["count"] = len(payload["value"])
    _seed(url, params, json.dumps(payload).encode())
    res = Data360().fetch_enterprise_surveys("WB_ES_T_SYNTH1")
    assert len(res.data) == 4
    data_prov = res.provenance[0]
    assert data_prov.vintage == "" and any("metadata unavailable" in n for n in data_prov.notes)
    codes = {c.code for c in res.caveats}
    assert "es_se_missing" in codes
    assert not res.meta["standard_errors_found"]


def test_fetch_es_requires_indicators_and_offline_needs_cache():
    with pytest.raises(ValueError, match="pass WB_ES indicator ids"):
        Data360().fetch_enterprise_surveys([])
    with pytest.raises(NetworkBlocked):
        Data360().fetch("WB_ES", ["WB_ES_T_SYNTH1"], with_metadata=False, with_labels=False)


def test_extras_frame_empty():
    from cgjrkit.core.provenance import Result

    assert list(d360.extras_frame(Result(pd.DataFrame())).columns) == ["indicator", "iso3", "period"]
