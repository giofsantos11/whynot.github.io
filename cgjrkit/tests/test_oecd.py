"""Unit tests for the OECD SDMX connector (synthetic fixtures, no network)."""

import pandas as pd
import pytest

from cgjrkit.connectors import oecd
from cgjrkit.connectors.oecd import OECD, HourlyBudget, OECDError, OECDRateLimitError
from cgjrkit.core import http
from cgjrkit.core.cache import Cache
from cgjrkit.core.schema import validate_frame

PMR = "oecd_pmr_synthetic.csv"
STRI = "oecd_stri_synthetic.csv"
FDIRRI = "oecd_fdirri_synthetic.csv"
FDIRRI_BOTH = "oecd_fdirri_labels_both_synthetic.csv"


def _load(fixtures_dir, name) -> bytes:
    return (fixtures_dir / name).read_bytes()


def _seed(flow, payload, **kw):
    url, params = oecd.build_request(flow, "all", None, None, kw.get("version"))
    Cache().put(OECD.source, url, params, payload)


# -- registry and request builders ------------------------------------------------------------


def test_get_flow_resolution():
    assert oecd.get_flow("PMR").dataflow == "DSD_PMR@DF_PMR"
    assert oecd.get_flow("pmr_wbg").dataflow == "DSD_PMR@DF_PMR_WBG"
    assert oecd.get_flow("fdi_rri").key == "FDIRRI"
    assert oecd.get_flow("DSD_STRI@DF_STRI_MAIN").key == "STRI"
    custom = oecd.get_flow("OECD.SDD.X,DSD_A@DF_B,1.0")
    assert (custom.agency, custom.dataflow, custom.version, custom.key) == ("OECD.SDD.X", "DSD_A@DF_B", "1.0", "B")
    with pytest.raises(ValueError, match="unknown OECD dataflow"):
        oecd.get_flow("not_a_flow")


def test_build_request_urls():
    url, params = oecd.build_request("PMR")
    assert url == "https://sdmx.oecd.org/public/rest/data/OECD.ECO.GCRD,DSD_PMR@DF_PMR,1.3/all"
    assert params == {"dimensionAtObservation": "AllDimensions", "format": "csvfilewithlabels"}
    url, params = oecd.build_request("STRI", start=2014, end=2024)
    assert url == "https://sdmx.oecd.org/public/rest/data/OECD.TAD.TPD,DSD_STRI@DF_STRI_MAIN,/all"  # latest version
    assert params["startPeriod"] == "2014" and params["endPeriod"] == "2024"
    url, _ = oecd.build_request("FDIRRI", version="1.0")
    assert url.endswith("DSD_FDIRRI_SCORES@DF_FDIRRI_SCORES,1.0/all")


def test_build_key():
    assert oecd.build_key("FDIRRI") == "all"
    assert oecd.build_key("FDIRRI", {"REF_AREA": ["KEN", "PHL"], "ACTIVITY": "_T", "MEASURE": "RR"}) == "KEN+PHL._T..RR."
    assert oecd.build_key("PMR", {"MEASURE": "PMR+BARRIER_TRADE"}) == ".PMR+BARRIER_TRADE."
    with pytest.raises(ValueError, match="no dimension"):
        oecd.build_key("FDIRRI", {"SECTOR": "X"})
    with pytest.raises(OECDError, match="dimension order"):
        oecd.build_key("OECD.SDD.X,DSD_A@DF_B,1.0", {"REF_AREA": "KEN"})


# -- SDMX-CSV reading --------------------------------------------------------------------------


def test_read_csv_with_labels(fixtures_dir):
    t = oecd.read_sdmx_csv(_load(fixtures_dir, FDIRRI), oecd.FLOWS["FDIRRI"].dims)
    assert t.layout == "labels"
    assert t.dims == ["REF_AREA", "ACTIVITY", "POL_CAT", "MEASURE", "UNIT_MEASURE"]
    assert set(t.attributes) == {"DECIMALS", "UNIT_MULT", "OBS_STATUS"}
    assert t.version == "2.0" and t.structure_id.startswith("OECD.DAF.INV:")
    assert t.frame.loc[0, "REF_AREA__label"] == "Kenya"


def test_read_labels_both_and_codes_only(fixtures_dir):
    t = oecd.read_sdmx_csv(_load(fixtures_dir, FDIRRI_BOTH))
    assert t.layout == "labels_both" and t.version == "2.0"
    assert t.dims == ["REF_AREA", "ACTIVITY", "POL_CAT", "MEASURE", "UNIT_MEASURE"]
    assert t.frame.loc[1, "REF_AREA"] == "PHL" and t.frame.loc[1, "REF_AREA__label"] == "Philippines"
    codes_only = ("DATAFLOW,REF_AREA,MEASURE,FREQ,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n"
                  "OECD.ECO.GCRD:DSD_PMR@DF_PMR(1.3),ITA,PMR,A,2018,1.5,A\n")
    t2 = oecd.read_sdmx_csv(codes_only)
    assert t2.layout == "codes" and t2.dims == ["REF_AREA", "MEASURE", "FREQ"] and t2.version == "1.3"


def test_extra_dimension_appended_to_declared_order(fixtures_dir):
    """A dimension missing from the declared (UNVERIFIED) PMR order stays a dimension (synthetic payload)."""
    csv = ("STRUCTURE,STRUCTURE_ID,ACTION,REF_AREA,MEASURE,METHODOLOGY,FREQ,TIME_PERIOD,OBS_VALUE,OBS_STATUS,ADJUSTMENT\n"
           "DATAFLOW,OECD.ECO.GCRD:DSD_PMR@DF_PMR(1.3),I,ITA,PMR,M2018,A,2098,1.0,A,N\n"
           "DATAFLOW,OECD.ECO.GCRD:DSD_PMR@DF_PMR(1.3),I,ITA,PMR,M1998,A,2098,2.0,A,N\n")
    t = oecd.read_sdmx_csv(csv, oecd.FLOWS["PMR"].dims)
    # declared order first, the extra dimension appended; columns after OBS_VALUE stay attributes
    assert t.dims == ["REF_AREA", "MEASURE", "FREQ", "METHODOLOGY"] and t.extra_dims == ["METHODOLOGY"]
    assert t.attributes == ["OBS_STATUS", "ADJUSTMENT"]
    p = oecd.parse(csv, "PMR")
    validate_frame(p.frame)
    assert sorted(p.frame["indicator"]) == ["PMR:PMR.M1998", "PMR:PMR.M2018"]
    ex = {c.code: c for c in p.caveats}["oecd_extra_dimensions"]
    assert ex.severity == "info" and ex.evidence["extra_dimensions"] == ["METHODOLOGY"]
    # the regular fixture layout is unchanged
    assert oecd.read_sdmx_csv(_load(fixtures_dir, FDIRRI), oecd.FLOWS["FDIRRI"].dims).extra_dims == []


def test_read_rejects_error_pages():
    with pytest.raises(OECDError, match="non-CSV"):
        oecd.read_sdmx_csv(b"<html><body>NoRecordsFound</body></html>")
    with pytest.raises(OECDError, match="empty"):
        oecd.read_sdmx_csv(b"")
    with pytest.raises(OECDError, match="TIME_PERIOD"):
        oecd.read_sdmx_csv("STRUCTURE,REF_AREA,Reference area\nDATAFLOW,ITA,Italy\n")


# -- parse --------------------------------------------------------------------------------------


def test_parse_pmr(fixtures_dir):
    p = oecd.parse(_load(fixtures_dir, PMR), "PMR")
    df = p.frame
    validate_frame(df)
    assert set(df["indicator"]) == {"PMR:PMR", "PMR:BARRIER_TRADE"}
    v = df.set_index(["indicator", "iso3", "period"])["value"]
    assert v[("PMR:PMR", "ITA", "2018")] == 1.5 and v[("PMR:BARRIER_TRADE", "MEX", "2018")] == 1.0
    assert df.loc[df["iso3"] == "ITA", "country"].iloc[0] == "Italy"
    assert "Product market regulation" in df.loc[df["indicator"] == "PMR:PMR", "indicator_label"].iloc[0]
    assert df["unit"].iloc[0].startswith("index, 0 (least restrictive)")
    assert p.meta["dropped_missing_values"] == 1  # the empty FDI_INDEX cell
    assert p.meta["aggregates"] == ["OECD"] and p.meta["structure_version"] == "1.3"
    codes = {c.code for c in p.caveats}
    assert "pmr_methodology_break" in codes and "oecd_dimensions_unverified" in codes


def test_parse_filters_locally(fixtures_dir):
    p = oecd.parse(_load(fixtures_dir, PMR), "PMR", countries=["ita", "MEX"], start=2018, end=2023,
                   filters={"MEASURE": "PMR"})
    df = p.frame
    assert set(df["iso3"]) == {"ITA", "MEX"} and set(df["period"]) == {"2018", "2023"}
    assert set(df["indicator"]) == {"PMR:PMR"}
    assert "pmr_methodology_break" not in {c.code for c in p.caveats}
    with pytest.raises(ValueError, match="no dimension"):
        oecd.parse(_load(fixtures_dir, PMR), "PMR", filters={"SECTOR": "X"})


def test_parse_stri(fixtures_dir):
    p = oecd.parse(_load(fixtures_dir, STRI), "STRI", countries="IDN", filters={"MEASURE": "STRI"})
    df = p.frame
    validate_frame(df)
    assert set(df["indicator"]) == {"STRI:STRI.LEGAL._Z.MAIN.IX._Z", "STRI:STRI.TELECOM._Z.MAIN.IX._Z"}
    legal = df[df["indicator"] == "STRI:STRI.LEGAL._Z.MAIN.IX._Z"].set_index("period")
    assert legal.loc["2023", "value"] == 0.5 and legal.loc["2024", "value"] == 0.51
    assert (df["obs_status"] == "provisional").all()
    label = legal["indicator_label"].iloc[0]
    assert "Legal services" in label and "Not applicable" not in label
    assert {"stri_scale", "oecd_dimensions_unverified"} <= {c.code for c in p.caveats}


def test_fdirri_layouts_agree(fixtures_dir):
    a = oecd.parse(_load(fixtures_dir, FDIRRI), "FDIRRI", countries=["KEN", "PHL"], start=2023,
                   filters={"ACTIVITY": "_T", "POL_CAT": "_T"}).frame
    b = oecd.parse(_load(fixtures_dir, FDIRRI_BOTH), "FDIRRI").frame
    cols = ["indicator", "iso3", "period", "value"]
    pd.testing.assert_frame_equal(a[cols].reset_index(drop=True), b[cols].reset_index(drop=True))
    assert set(a["indicator"]) == {"FDIRRI:_T._T.RR.IX"}


def test_parse_empty_selection(fixtures_dir):
    p = oecd.parse(_load(fixtures_dir, PMR), "PMR", countries=["ZZZ"])
    assert p.frame.empty and "oecd_empty" in {c.code for c in p.caveats}
    validate_frame(p.frame)


# -- hourly budget -------------------------------------------------------------------------------


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_hourly_budget(tmp_path):
    clock = FakeClock()
    b = HourlyBudget(tmp_path / "oecd.json", limit=3, window_s=3600, clock=clock)
    for _ in range(3):
        b.check_and_record()
        clock.t += 10
    assert b.status()["remaining"] == 0
    with pytest.raises(OECDRateLimitError) as exc:
        b.check_and_record()
    assert exc.value.used == 3 and 0 < exc.value.retry_after_s <= 3600
    b.check_and_record(force=True)  # force bypasses the budget and is still recorded
    assert b.status()["used"] == 4
    clock.t += 3600
    assert b.status() == {"used": 0, "limit": 3, "remaining": 3, "retry_after_s": 0.0}
    # persisted across instances
    b.check_and_record()
    assert HourlyBudget(tmp_path / "oecd.json", limit=3, clock=clock).status()["used"] == 1


# -- connector (offline) -----------------------------------------------------------------------------


def test_fetch_from_cache(fixtures_dir):
    _seed("PMR", _load(fixtures_dir, PMR))
    res = OECD().fetch("PMR", countries=["ITA"], start=2018)
    validate_frame(res.data)
    assert set(res.data["iso3"]) == {"ITA"} and set(res.data["period"]) == {"2018", "2023"}
    prov = res.provenance[0]
    assert prov.source == "oecd" and prov.dataset == "PMR" and prov.vintage == "DSD_PMR@DF_PMR v1.3"
    assert prov.license.startswith("CC BY 4.0") and prov.endpoint.endswith("DSD_PMR@DF_PMR,1.3/all")
    assert prov.citation.startswith("OECD, Product Market Regulation")
    assert res.meta["served_from_cache"] is True and res.meta["budget"]["used"] == 0
    assert "oecd_licence" in {c.code for c in res.caveats}
    assert "OECD" in res.source_line()


def test_panels(fixtures_dir):
    _seed("FDIRRI", _load(fixtures_dir, FDIRRI))
    _seed("STRI", _load(fixtures_dir, STRI))
    _seed("PMR", _load(fixtures_dir, PMR))
    o = OECD()
    fd = o.fdirri(["KEN", "MKD", "PHL"])
    assert len(fd.data) == 6 and set(fd.data["indicator"]) == {"FDIRRI:_T._T.RR.IX"}
    fd_all = o.fdirri("PHL", activities=None, policy_categories=None)
    assert fd_all.data["indicator"].nunique() == 6  # 2 activities x 3 policy categories
    st = o.stri(["MEX"], sectors="TELECOM", measures="CLAS1_1")
    assert set(st.data["indicator"]) == {"STRI:CLAS1_1.TELECOM._Z.MAIN.IX._Z"}
    pm = o.pmr(["ITA", "MEX"], 2018, 2023, measures="PMR")
    assert len(pm.data) == 4
    with pytest.raises(http.NetworkBlocked):
        o.pmr(wbg=True)  # OECD-WBG dataflow not cached, offline


def test_budget_counts_network_calls_only(fixtures_dir, monkeypatch, tmp_path):
    payload = _load(fixtures_dir, FDIRRI)
    calls = []

    def fake_fetch(source, url, params=None, *, cache=None, refresh=False, **kw):
        hit = None if refresh else cache.latest(source, url, params)
        if hit:  # like http.fetch: cache first, no network
            return cache.get_object(hit.sha256), hit
        calls.append((url, refresh, kw.get("retries")))
        return payload, cache.put(source, url, params, payload)

    monkeypatch.delenv("CGJRKIT_OFFLINE", raising=False)
    monkeypatch.setattr(oecd.http, "fetch", fake_fetch)
    clock = FakeClock()
    o = OECD(budget=HourlyBudget(tmp_path / "b.json", limit=1, clock=clock))
    o.fdirri("KEN")                                   # network: uses the only slot
    assert o.budget_status()["used"] == 1 and calls[-1][2] == 1  # retries=1
    o.fdirri("PHL")                                   # same bulk query: served from cache, not counted
    assert o.budget_status()["used"] == 1
    with pytest.raises(OECDRateLimitError):
        o.fdirri("KEN", refresh=True)
    o.fdirri("KEN", refresh=True, force=True)
    clock.t += 3601
    o.fdirri("KEN", refresh=True)
    assert len(calls) == 3


def test_server_filter_builds_key(fixtures_dir, monkeypatch):
    seen = {}

    def fake_fetch(source, url, params=None, *, cache=None, refresh=False, **kw):
        seen["url"], seen["params"] = url, params
        return _load(fixtures_dir, FDIRRI), cache.put(source, url, params, b"x")

    monkeypatch.setattr(oecd.http, "fetch", fake_fetch)
    OECD().fetch("FDIRRI", ["KEN"], 2022, 2023, filters={"ACTIVITY": "_T", "POL_CAT": "_T", "MEASURE": "RR"},
                 server_filter=True)
    assert seen["url"].endswith("DF_FDIRRI_SCORES,2.0/KEN._T._T.RR.")
    assert seen["params"]["startPeriod"] == "2022" and seen["params"]["endPeriod"] == "2023"


def test_mix_caveats(fixtures_dir):
    a = oecd.parse(_load(fixtures_dir, PMR), "PMR", start=2018).frame
    b = a.assign(dataset="PMR_WBG")
    codes = {c.code for c in oecd.oecd_mix_caveats([a, b])}
    assert codes == {"pmr_oecd_wbg_mix"}
    full = oecd.parse(_load(fixtures_dir, PMR), "PMR").frame
    assert {c.code for c in oecd.oecd_mix_caveats(full)} == {"pmr_methodology_break"}
    assert OECD.domains == ("sdmx.oecd.org",)
