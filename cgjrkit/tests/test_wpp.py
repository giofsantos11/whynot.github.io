"""Unit tests for the WPP connector (synthetic fixtures, no network)."""

import gzip
import math

import pandas as pd
import pytest

from cgjrkit.connectors import wpp
from cgjrkit.connectors.wpp import WPP, WPPError
from cgjrkit.core import http
from cgjrkit.core.cache import Cache
from cgjrkit.core.schema import validate_frame

DEMO = "wpp_demographic_indicators_synthetic.csv.gz"
TOTAL = "wpp_total_population_synthetic.csv.gz"
AGE5 = "wpp_population_age5_synthetic.csv.gz"


def _load(fixtures_dir, name) -> bytes:
    return (fixtures_dir / name).read_bytes()


def _seed_file(key, payload):
    url, params = wpp.build_request(key)
    Cache().put(WPP.source, url, params, payload)


def _age_frame(fixtures_dir, countries=("KEN", "UGA")):
    return wpp.parse_bulk(_load(fixtures_dir, AGE5), ["PopTotal", "PopMale", "PopFemale"], countries=list(countries), age=True).frame


# -- helpers and request builders --------------------------------------------------------------


def test_variants_and_datasets():
    assert wpp.normalize_variant(None) == "Medium"
    assert wpp.normalize_variant("high") == "High"
    assert wpp.normalize_variant(4) == "Low" and wpp.normalize_variant("207") == "Lower 95 PI"
    assert wpp.normalize_variant("accelarated abr decline w/rec.") == "Accelarated ABR decline w/rec."
    assert len(wpp.VARIANTS) == 18
    with pytest.raises(ValueError, match="unknown WPP variant"):
        wpp.normalize_variant("optimistic")
    with pytest.raises(ValueError, match="VarID"):
        wpp.normalize_variant(1)
    assert wpp.dataset_for("Medium") == "WPP2024" and wpp.dataset_for("high") == "WPP2024 High"


def test_normalize_countries():
    assert wpp.normalize_countries("all") == (None, [])
    assert wpp.normalize_countries(None) == (None, [])
    assert wpp.normalize_countries(["ken", "UGA", 900, "LOC1560", "0404"]) == (["KEN", "UGA"], ["404", "900", "1560"])
    assert wpp.normalize_countries("KEN,UGA") == (["KEN", "UGA"], [])
    with pytest.raises(ValueError, match="invalid WPP location"):
        wpp.normalize_countries(["Kenya"])


def test_build_request_urls():
    url, params = wpp.build_request("demographic_indicators")
    assert url == ("https://population.un.org/wpp/assets/Excel%20Files/1_Indicator%20(Standard)/CSV_FILES/"
                   "WPP2024_Demographic_Indicators_Medium.csv.gz") and params == {}
    assert wpp.build_request("total_population")[0].endswith("WPP2024_TotalPopulationBySex.csv.gz")
    assert wpp.build_request("population_age5_jan1")[0].endswith("WPP2024_Population1JanuaryByAge5GroupSex_Medium.csv.gz")
    with pytest.raises(ValueError, match="unknown WPP file key"):
        wpp.build_request("migrant_stock")
    assert wpp.parse_age_group("15-19") == (15, 19) and wpp.parse_age_group("100+") == (100, math.inf)
    with pytest.raises(ValueError):
        wpp.parse_age_group("15 to 19")


# -- bulk parsing ---------------------------------------------------------------------------


def test_parse_demographic_indicators(fixtures_dir):
    info = wpp.parse_bulk(_load(fixtures_dir, DEMO), ["TPopulation1July", "TFR", "LEx"], countries=["KEN", "TGO"], start=2023, end=2025)
    df = info.frame
    validate_frame(df)
    assert set(df["iso3"]) == {"KEN", "TGO"} and set(df["period"]) == {"2023", "2024", "2025"}
    pop = df[(df["indicator"] == "TPopulation1July") & (df["iso3"] == "KEN")].set_index("period")
    assert pop.loc["2023", "value"] == 1010.0 and pop.loc["2023", "unit"] == "thousands"
    assert pop.loc["2023", "obs_status"] == "" and pop.loc["2024", "obs_status"] == "forecast"
    tfr = df[df["indicator"] == "TFR"].iloc[0]
    assert tfr["indicator_label"] == "Total fertility rate" and tfr["unit"] == "live births per woman"
    assert set(df["dataset"]) == {"WPP2024"} and set(df["source"]) == {"wpp"}
    assert info.rows_read == 20 and not info.aggregates and not info.missing_locations
    codes = {c.code: c for c in wpp.build_caveats(info)}
    assert codes["wpp_projection"].evidence == {"first": "2024", "last": "2025", "variant": "Medium"}
    assert codes["wpp_togo_interim_update"].severity == "warn"


def test_aggregates_and_missing(fixtures_dir):
    payload = _load(fixtures_dir, DEMO)
    countries_only = wpp.parse_bulk(payload, ["TPopulation1July"], end=2022)
    assert set(countries_only.frame["iso3"]) == {"KEN", "UGA", "TGO"}
    with_aggs = wpp.parse_bulk(payload, ["TPopulation1July"], end=2022, include_aggregates=True)
    assert with_aggs.aggregates == {"LOC1560": "African Union", "LOC900": "World"}
    assert with_aggs.frame.set_index("iso3").loc["LOC900", "value"] == 4000.0
    by_id = wpp.parse_bulk(payload, ["TPopulation1July"], countries=[900, "KEN"], start=2022, end=2022)
    assert set(by_id.frame["iso3"]) == {"LOC900", "KEN"}
    miss = wpp.parse_bulk(payload, ["TPopulation1July", "NotAColumn"], countries=["KEN", "FRA"], end=2022)
    assert miss.missing_locations == ["FRA"] and miss.missing_columns == ["NotAColumn"]
    codes = {c.code for c in wpp.build_caveats(miss)}
    assert {"wpp_missing_locations", "wpp_missing_columns"} <= codes
    empty = wpp.parse_bulk(payload, ["TPopulation1July"], countries="KEN", variant="High")
    assert empty.frame.empty and "wpp_empty_selection" in {c.code for c in wpp.build_caveats(empty)}
    with pytest.raises(ValueError, match="after end"):
        wpp.parse_bulk(payload, ["TFR"], start=2025, end=2020)


def test_parse_total_population_variants(fixtures_dir):
    payload = _load(fixtures_dir, TOTAL)
    med = wpp.parse_bulk(payload, ["PopTotal"], countries="KEN", variant="Medium").frame
    high = wpp.parse_bulk(payload, ["PopTotal"], countries="KEN", variant=3).frame
    assert med.set_index("period")["value"].to_dict() == {"2023": 1000.0, "2024": 1000.0, "2025": 1000.0}
    assert high.set_index("period")["value"].to_dict() == {"2024": 1100.0, "2025": 1100.0}
    assert set(high["dataset"]) == {"WPP2024 High"} and set(high["obs_status"]) == {"forecast"}


def test_parse_age_groups(fixtures_dir):
    info = wpp.parse_bulk(_load(fixtures_dir, AGE5), ["PopMale", "PopFemale"], countries="KEN", start=2024, age=True, reference="1 July")
    df = info.frame
    validate_frame(df)
    assert info.age_groups[:3] == ["0-4", "5-9", "10-14"] and info.age_groups[-1] == "100+" and len(info.age_groups) == 21
    row = df[df["indicator"] == "PopFemale|15-19"].iloc[0]
    assert row["value"] == 8.0 and row["indicator_label"] == "Population aged 15-19, female, 1 July" and row["unit"] == "thousands"
    with pytest.raises(WPPError, match="AgeGrp"):
        wpp.parse_bulk(_load(fixtures_dir, DEMO), ["TFR"], age=True)


def test_aggregate_ages_values(fixtures_dir):
    out = wpp.aggregate_ages(_age_frame(fixtures_dir))
    validate_frame(out)
    v = out.set_index(["iso3", "period", "indicator"])["value"]
    # synthetic groups: male = i+1, female = 2(i+1) for the i-th 5-year group (0-4 is i=0)
    assert v[("KEN", "2023", "PopMale|15-64")] == 85.0          # groups 15-19..60-64 -> 4+...+13
    assert v[("KEN", "2023", "PopTotal|15-64")] == 255.0
    assert v[("KEN", "2023", "PopTotal|15+")] == 675.0          # 3 * (4+...+21)
    assert v[("KEN", "2023", "share|PopTotal|15-64")] == pytest.approx(255 / 693 * 100)
    assert v[("KEN", "2023", "dependency_ratio")] == pytest.approx((18 + 420) / 255 * 100)
    assert v[("KEN", "2023", "dependency_ratio_young")] == pytest.approx(18 / 255 * 100)
    assert v[("KEN", "2023", "dependency_ratio_old")] == pytest.approx(420 / 255 * 100)
    # Uganda lacks the 40-44 group: banded sums are NaN, never silently too small
    assert pd.isna(v[("UGA", "2023", "PopTotal|15-64")]) and pd.isna(v[("UGA", "2023", "dependency_ratio")])
    assert set(out.loc[out["period"] == "2024", "obs_status"]) == {"forecast"}
    custom = wpp.aggregate_ages(_age_frame(fixtures_dir, ("KEN",)), bands=("0-14", "25-54"), dependency=False, shares=False)
    assert set(custom["indicator"].str.split("|").str[1]) == {"0-14", "25-54"}
    with pytest.raises(ValueError, match="cuts through"):
        wpp.aggregate_ages(_age_frame(fixtures_dir), bands=("15-62",))
    with pytest.raises(ValueError, match="age-structure"):
        wpp.aggregate_ages(wpp.parse_bulk(_load(fixtures_dir, DEMO), ["TFR"]).frame)


def test_pyramid_table(fixtures_dir):
    pyr = wpp.pyramid_table(_age_frame(fixtures_dir), "KEN", 2024)
    assert list(pyr.index[:2]) == ["0-4", "5-9"] and pyr.index[-1] == "100+"
    assert pyr.loc["20-24", "male"] == 5.0 and pyr.loc["20-24", "female"] == 10.0
    assert (pyr["male_pct"].sum() + pyr["female_pct"].sum()) == pytest.approx(100.0)
    with pytest.raises(ValueError, match="no age-structure rows"):
        wpp.pyramid_table(_age_frame(fixtures_dir), "KEN", 1990)


def test_read_bulk_errors(fixtures_dir):
    with pytest.raises(WPPError, match="HTML"):
        wpp.read_bulk(b"<!DOCTYPE html><html>Not found</html>", ["LocID"])
    with pytest.raises(WPPError, match="not a WPP bulk file"):
        wpp.parse_bulk(b"a,b\n1,2\n", ["TFR"])
    with pytest.raises(WPPError, match="unreadable"):
        wpp.parse_bulk(_load(fixtures_dir, DEMO)[:60], ["TFR"])
    plain = gzip.decompress(_load(fixtures_dir, DEMO))  # uncompressed input also works
    assert len(wpp.parse_bulk(plain, ["TFR"], countries="KEN").frame) == 4


# -- Data Portal API -------------------------------------------------------------------------


def test_api_parsing(fixtures_dir):
    frame, nxt = wpp.parse_api(_load(fixtures_dir, "wpp_api_page1_synthetic.json"))
    assert nxt.endswith("pageNumber=2") and list(frame["value"]) == [1000000.0, 1100000.0]
    assert set(frame["indicator"]) == {"api:49"} and set(frame["dataset"]) == {"WPP2024 API"}
    frame2, nxt2 = wpp.parse_api(_load(fixtures_dir, "wpp_api_page2_synthetic.json"))
    assert nxt2 is None and set(frame2["indicator"]) == {"api:49", "api:49|Female"}
    locs, _ = wpp.parse_locations(_load(fixtures_dir, "wpp_api_locations_synthetic.json"))
    assert locs.set_index("iso3").loc["KEN", "id"] == 404
    with pytest.raises(WPPError, match="non-JSON"):
        wpp.parse_api(b"Unauthorized")
    url, params = wpp.build_api_request([49], ["404"], 2023, 2025, page=2)
    assert url == "https://population.un.org/dataportalapi/api/v1/data/indicators/49/locations/404/start/2023/end/2025"
    assert params == {"pagingInHeader": "false", "format": "json", "pageNumber": "2", "pageSize": "100"}
    with pytest.raises(ValueError, match="start and end"):
        wpp.build_api_request(49, 404, 2025, 2020)


def test_read_token(monkeypatch):
    monkeypatch.delenv(wpp.KEY_ENV, raising=False)
    with pytest.raises(WPPError, match="CGJRKIT_WPP_KEY"):
        wpp.read_token()
    monkeypatch.setenv(wpp.KEY_ENV, "Bearer abc.def")
    assert wpp.read_token() == "abc.def"
    assert wpp.read_token("xyz") == "xyz"


# -- connector (offline, cache seeded with fixtures) ---------------------------------------------


def test_fetch_offline(fixtures_dir):
    _seed_file("demographic_indicators", _load(fixtures_dir, DEMO))
    res = WPP().fetch(["TPopulation1July", "MedianAgePop"], ["KEN", "UGA"], 2022, 2024)
    validate_frame(res.data)
    assert len(res.data) == 2 * 2 * 3
    prov = res.provenance[0]
    assert prov.vintage == "WPP 2024" and prov.license == "CC BY 3.0 IGO" and prov.dataset == "WPP2024"
    assert prov.endpoint.endswith("WPP2024_Demographic_Indicators_Medium.csv.gz") and prov.payload_sha256
    assert "World Population Prospects 2024" in res.source_line() and "(WPP 2024)" in res.source_line()
    assert res.meta["variant"] == "Medium" and res.meta["first_projection_year"] == 2024
    assert "wpp_projection" in {c.code for c in res.caveats}
    with pytest.raises(ValueError, match="unknown WPP demographic indicator"):
        WPP().fetch(["GDP"], "KEN")


def test_total_population_offline(fixtures_dir):
    _seed_file("total_population", _load(fixtures_dir, TOTAL))
    res = WPP().total_population(["KEN", "UGA"], variant="low", sexes=("total", "female"))
    assert set(res.data["dataset"]) == {"WPP2024 Low"} and set(res.data["indicator"]) == {"PopTotal", "PopFemale"}
    assert res.data.set_index(["iso3", "period", "indicator"]).loc[("UGA", "2025", "PopTotal"), "value"] == 1800.0
    assert set(res.data["unit"]) == {"thousands"}
    assert res.data["indicator_label"].str.contains("as of 1 July").all()
    with pytest.raises(ValueError, match="unknown sex"):
        WPP().total_population("KEN", sexes=("men",))


def test_age_structure_falls_back_to_january(fixtures_dir, monkeypatch):
    _seed_file("population_age5_jan1", _load(fixtures_dir, AGE5))
    real_fetch = http.fetch
    july = wpp.build_request("population_age5")[0]

    def fake_fetch(source, url, params=None, **kw):
        if url == july:
            raise http.FetchError(f"404 from {url}: Not Found")
        return real_fetch(source, url, params, **kw)

    monkeypatch.setattr(wpp.http, "fetch", fake_fetch)
    res = WPP().age_structure("KEN", 2024, 2024)
    assert res.meta["file"] == "WPP2024_Population1JanuaryByAge5GroupSex_Medium.csv.gz" and res.meta["reference"] == "1 January"
    note = {c.code: c for c in res.caveats}["wpp_reference_date"]
    assert "404" in note.message
    assert res.data["indicator_label"].str.endswith("1 January").all()


def test_working_age_population_offline(fixtures_dir):
    _seed_file("population_age5", _load(fixtures_dir, AGE5))
    res = WPP().working_age_population(["KEN"], 2023, 2024, sexes=("male",))
    validate_frame(res.data)
    inds = set(res.data["indicator"])
    assert {"PopMale|15-64", "PopMale|15+", "share|PopMale|15-64", "dependency_ratio", "dependency_ratio_old"} <= inds
    assert not any(i.startswith("PopTotal") for i in inds)
    assert res.provenance[-1].source == "cgjrkit" and res.provenance[-1].tool == "WPP.working_age_population"
    assert res.provenance[0].vintage == "WPP 2024"
    v = res.data.set_index(["period", "indicator"])["value"]
    assert v[("2023", "PopMale|15-64")] == 85.0


def test_fetch_api_offline(fixtures_dir, monkeypatch):
    monkeypatch.setenv(wpp.KEY_ENV, "synthetic-token")
    lurl, lparams = wpp.build_locations_request(1)
    Cache().put(WPP.source, lurl, lparams, _load(fixtures_dir, "wpp_api_locations_synthetic.json"))
    for page, name in ((1, "wpp_api_page1_synthetic.json"), (2, "wpp_api_page2_synthetic.json")):
        url, params = wpp.build_api_request(49, ["404"], 2023, 2025, page=page)
        Cache().put(WPP.source, url, params, _load(fixtures_dir, name))
    res = WPP().fetch_api(49, "KEN", 2023, 2025)
    validate_frame(res.data)
    assert len(res.data) == 4 and len(res.provenance) == 2 and res.meta["locations"] == ["404"]
    assert res.provenance[0].vintage == "WPP 2024"
    monkeypatch.delenv(wpp.KEY_ENV)
    with pytest.raises(WPPError, match="token"):
        WPP().fetch_api(49, "KEN", 2023, 2025)


def test_connector_declares_domains():
    assert WPP.domains == ("population.un.org",) and WPP.source == "wpp"
