"""Adversarial review probes for connectors worldbank, data360, pip and imf.

Each test documents a defect found in review. They were marked ``xfail(strict=True)``
until the defect was fixed; all eight defects are now fixed, so these are regression tests.
All payloads are synthetic (round numbers, obviously fake ids, values not from any
provider); only the response *layouts* follow the documented formats cited in each test.
"""

from __future__ import annotations

import json

from cgjrkit.connectors import data360 as d360
from cgjrkit.connectors import imf as imfmod
from cgjrkit.connectors.data360 import Data360
from cgjrkit.connectors.imf import IMF, IMFAPIError, build_datamapper_request, unit_for
from cgjrkit.core import cache as cachemod
from cgjrkit.core.cache import Cache, Retrieval


# ---------------------------------------------------------------------------------------------
# Data360: totals_only drops every row of an indicator that has no "_T" total
# ---------------------------------------------------------------------------------------------
def _d360_row(**over) -> dict:
    """One row in the 24-field layout of the official OpenAPI DataResponse sample
    (worldbank/open-api-specs "Data360 Open_API.json"); values are synthetic."""
    row = {
        "OBS_VALUE": "50.0", "TIME_FORMAT": "P1Y", "UNIT_MULT": 0, "COMMENT_OBS": None, "OBS_STATUS": "A",
        "OBS_CONF": "PU", "AGG_METHOD": "_Z", "DECIMALS": "2", "COMMENT_TS": "Synthetic label (female)",
        "DATA_SOURCE": "WB_WDI", "LATEST_DATA": False, "DATABASE_ID": "WB_WDI",
        "INDICATOR": "WB_WDI_SE_PRM_CMPT_FE_ZS", "REF_AREA": "KEN", "SEX": "F", "AGE": "_T",
        "URBANISATION": "_T", "COMP_BREAKDOWN_1": "_Z", "COMP_BREAKDOWN_2": "_Z", "COMP_BREAKDOWN_3": "_Z",
        "TIME_PERIOD": "2099", "FREQ": "A", "UNIT_MEASURE": "PT", "UNIT_TYPE": None,
    }
    row.update(over)
    return row


def test_data360_totals_only_keeps_series_without_a_total():
    rows = [_d360_row(), _d360_row(TIME_PERIOD="2098", OBS_VALUE="40.0")]
    parsed = d360.parse_rows(rows, database_id="WB_WDI")  # Data360.fetch defaults: totals_only=True, keep_dims=()
    cav = {c.code for c in Data360._generic_caveats(parsed, {}, ["WB_WDI_SE_PRM_CMPT_FE_ZS"])}
    assert len(parsed.frame) == 2, f"all rows dropped (dropped_breakdown_rows={parsed.dropped_breakdown_rows})"
    assert "data360_empty" not in cav


# ---------------------------------------------------------------------------------------------
# Data360 / Enterprise Surveys: a breakdown code with an "_N" (or "_SE") segment is read as a
# sample size (standard error) and filed under the economy-wide series
# ---------------------------------------------------------------------------------------------
def test_es_no_subgroup_is_not_a_sample_size():
    total = _d360_row(DATABASE_ID="WB_ES", INDICATOR="WB_ES_T_SYNTH9", SEX="_T", COMP_BREAKDOWN_1="_T", OBS_VALUE="40")
    non_exporters = dict(total, COMP_BREAKDOWN_1="SYNTH_EXPORTER_N", OBS_VALUE="30")  # 30 % of non-exporting firms
    parsed = d360.parse_rows([total, non_exporters], database_id="WB_ES", detect_statistics=True)  # ES wrapper defaults
    fields = {(e["indicator"], e["field"], e["value"]) for e in parsed.extras}
    assert ("WB_ES_T_SYNTH9", "n", 30.0) not in fields, f"subgroup estimate recorded as sample size: {fields}"


# ---------------------------------------------------------------------------------------------
# IMF: WEO releases held by an older version of the bare WEO dataflow are unreachable
# ---------------------------------------------------------------------------------------------
_CATALOGUE = json.dumps(
    {
        "data": {
            "dataflows": [
                {"id": "WEO", "agencyID": "IMF.RES", "version": "9.0.0", "name": "synthetic stub"},
                {"id": "WEO", "agencyID": "IMF.RES", "version": "6.0.0", "name": "synthetic stub"},
                {"id": "WEO_2025_OCT_VINTAGE", "agencyID": "IMF.RES", "version": "1.0.0", "name": "synthetic stub"},
            ]
        }
    }
).encode()
_PROBE = (
    "STRUCTURE[;],STRUCTURE_ID,ACTION,COUNTRY,INDICATOR,FREQUENCY,TIME_PERIOD,OBS_VALUE,PUBLICATION_DATE\n"
    "dataflow,IMF.RES:WEO({v}),I,USA,NGDP_RPCH,A,2030,1,{d}\n"
)
_DATA_600 = (
    "STRUCTURE[;],STRUCTURE_ID,ACTION,COUNTRY,INDICATOR,FREQUENCY,TIME_PERIOD,OBS_VALUE,UNIT,SCALE,LATEST_ACTUAL_ANNUAL_DATA\n"
    "dataflow,IMF.RES:WEO(6.0.0),I,KEN,GGXWDG_NGDP,A,2023,10,PT,0,2023\n"
    "dataflow,IMF.RES:WEO(6.0.0),I,KEN,GGXWDG_NGDP,A,2024,20,PT,0,2023\n"
).encode()


def _fake_get(urls: list[str]):
    def get(url, params=None, headers=None, refresh=False):
        urls.append(url)
        if "structure/dataflow/IMF.RES/*/*" in url:
            body = _CATALOGUE
        elif "/WEO_2025_APR_VINTAGE/" in url:
            raise IMFAPIError(404, "NoRecordsFound (synthetic)", url)
        elif "/WEO/+/USA.NGDP_RPCH.A" in url:
            body = _PROBE.format(v="9.0.0", d="2026-04-14").encode()
        elif "/WEO/6.0.0/USA.NGDP_RPCH.A" in url:
            body = _PROBE.format(v="6.0.0", d="2025-04-22").encode()
        elif "/WEO/6.0.0/" in url:
            body = _DATA_600
        else:
            raise IMFAPIError(404, "not seeded in this review test", url)
        return body, Retrieval(key="k", sha256="0" * 64, retrieved_at="2099-01-01T00:00:00Z", url=url, params=params or {}, source="imf")

    return get


def test_weo_vintage_served_by_older_bare_weo_version(tmp_path):
    imf = IMF(cache=Cache(tmp_path / "c"))
    urls: list[str] = []
    imf._get = _fake_get(urls)  # type: ignore[method-assign]
    res = imf.fetch_weo("GGXWDG_NGDP", "KEN", vintage="2025-04")
    assert res.provenance[0].vintage == "WEO 2025-04"
    assert "/WEO/6.0.0/" in res.provenance[0].endpoint


# ---------------------------------------------------------------------------------------------
# IMF DataMapper: the retrieval-year fallback labels the previous release's projections as actuals
# ---------------------------------------------------------------------------------------------
def test_datamapper_retrieved_before_april_flags_previous_year(tmp_path, monkeypatch, fixtures_dir):
    cache = Cache(tmp_path / "c")
    url, params = build_datamapper_request("NGDP_RPCH", ["KEN"])
    monkeypatch.setattr(cachemod, "utc_now", lambda: "2026-02-10T00:00:00Z")
    cache.put("imf", url, params, (fixtures_dir / "imf_datamapper_growth_synthetic.json").read_bytes())
    res = IMF(cache=cache).fetch_datamapper("NGDP_RPCH", "KEN", with_metadata=False)
    # In February 2026 the DataMapper serves the October 2025 WEO, where 2025 is a projection.
    st = res.data.set_index("period")["obs_status"]
    assert st["2025"] == "forecast", f"2025 labelled {st['2025']!r} (publication year used: 2026)"


# ---------------------------------------------------------------------------------------------
# IMF: series whose forecast boundary is unknown are left unflagged without a caveat naming them
# ---------------------------------------------------------------------------------------------
def test_weo_unflagged_series_are_reported(tmp_path, fixtures_dir):
    cache = Cache(tmp_path / "c")
    url, params, _ = imfmod.build_weo_request(["NGDP_RPCH"], ["UGA"])
    cache.put("imf", url, params, (fixtures_dir / "imf_weo_csv_synthetic.csv").read_bytes())
    res = IMF(cache=cache).fetch_weo("NGDP_RPCH", "UGA")  # probe not cached -> offline -> unresolved
    assert res.meta["forecast_rules"].get("UGA/NGDP_RPCH") == "none"
    evidence = json.dumps([c.evidence for c in res.caveats])
    assert "UGA/NGDP_RPCH" in evidence, "no caveat says this series' projections are unflagged"


# ---------------------------------------------------------------------------------------------
# IMF units: every code containing "PPP" is labelled international $
# ---------------------------------------------------------------------------------------------
def test_unit_for_ppp_share_and_conversion_rate():
    assert "international $" not in unit_for("PPPSH", "PT")
    assert unit_for("PPPEX", "") != "international $ (PPP base year unknown)"


# ---------------------------------------------------------------------------------------------
# Data360: provenance licence is always "CC BY 4.0", even when the indicator metadata says otherwise
# ---------------------------------------------------------------------------------------------
def test_data360_provenance_license_follows_indicator_metadata():
    ind = "SYNTH_DB_IND1"
    url, params = d360.build_data_request("SYNTH_DB", [ind])
    page = {"count": 1, "value": [_d360_row(DATABASE_ID="SYNTH_DB", INDICATOR=ind, SEX="_T")]}
    Cache().put(Data360.source, url, params, json.dumps(page).encode())
    murl, body = d360.build_metadata_request(indicator_id=ind)
    meta = {"value": [{"series_description": {"idno": ind, "name": "Synthetic", "license": [{"name": "CC BY-NC 4.0"}]}}]}
    Cache().put(Data360.source, murl, {"__body__": body}, json.dumps(meta).encode())
    res = Data360().fetch("SYNTH_DB", [ind], with_labels=False)
    assert "data360_license" in {c.code for c in res.caveats}  # the caveat works ...
    data_prov = next(p for p in res.provenance if p.tool == "Data360.fetch")
    assert data_prov.license != "CC BY 4.0"  # ... but the provenance record still claims CC BY 4.0


# ---------------------------------------------------------------------------------------------
# IMF DataMapper: three-letter region codes (SSQ, AFQ, APQ, ...) are treated as countries
# ---------------------------------------------------------------------------------------------
def test_datamapper_region_codes_listed_as_aggregates(tmp_path):
    cache = Cache(tmp_path / "c")
    url, params = build_datamapper_request("NGDP_RPCH", ["KEN", "SSQ"])
    payload = {"values": {"NGDP_RPCH": {"KEN": {"2099": 1}, "SSQ": {"2099": 2}}}, "api": {"version": "1"}}
    cache.put("imf", url, params, json.dumps(payload).encode())
    res = IMF(cache=cache).fetch_datamapper("NGDP_RPCH", ["KEN", "SSQ"], with_metadata=False)
    assert "SSQ" in res.meta["aggregates"]
