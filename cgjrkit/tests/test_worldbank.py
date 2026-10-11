"""Unit tests for the World Bank Indicators API v2 connector.

All tests run offline. Fixtures in tests/fixtures/worldbank_*_synthetic.json
follow the documented response formats (docs/apis/worldbank.md) and contain
made-up values. Tests that exercise the full fetch path pre-load payloads into
an isolated cache under the exact URL/params the connector will request.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from cgjrkit.connectors import worldbank as wbmod
from cgjrkit.connectors.worldbank import (
    KNOWN_AGGREGATES,
    WorldBank,
    WorldBankAPIError,
    WorldBankResponseError,
    build_caveats,
    build_request,
    map_obs_status,
    normalize_period,
    parse,
    parse_concept_variables,
    parse_countries,
    parse_indicator_meta,
    parse_page,
    parse_sources,
    plan_chunks,
    resolve_source,
    unit_from_name,
)
from cgjrkit.core.cache import Cache
from cgjrkit.core.schema import COLUMNS, OBS_STATUS, ppp_base, validate_frame


def load(fixtures_dir, name: str) -> bytes:
    return (fixtures_dir / name).read_bytes()


@pytest.fixture
def cache(tmp_path) -> Cache:
    return Cache(tmp_path / "wbcache")


@pytest.fixture
def wb(cache) -> WorldBank:
    return WorldBank(cache=cache)


def put(cache: Cache, url: str, params: dict, payload: bytes) -> None:
    cache.put("worldbank", url, params, payload)


# ---------------------------------------------------------------------------
# build_request / chunking
# ---------------------------------------------------------------------------
def test_build_request_single_indicator_with_date_range():
    url, params = build_request("NY.GDP.MKTP.KD.ZG", ["ken", "LMC"], 2001, 2024)
    assert url == "https://api.worldbank.org/v2/country/KEN;LMC/indicator/NY.GDP.MKTP.KD.ZG"
    assert params == {"format": "json", "per_page": "10000", "page": "1", "source": "2", "date": "2001:2024"}


def test_build_request_multi_indicator_sends_source_and_semicolons():
    url, params = build_request(["NY.GDP.MKTP.KD.ZG", "SL.UEM.TOTL.ZS"], "all", source=2, mrv=5, page=3)
    assert url.endswith("/country/all/indicator/NY.GDP.MKTP.KD.ZG;SL.UEM.TOTL.ZS")
    assert params["source"] == "2" and params["mrv"] == "5" and params["page"] == "3"
    assert "date" not in params


def test_build_request_wgi_source_and_string_inputs():
    url, params = build_request("GOV_WGI_CC.EST", "KEN;UGA", 2015, 2023, source=3)
    assert "/country/KEN;UGA/indicator/GOV_WGI_CC.EST" in url
    assert params["source"] == "3"


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"start": 2000, "end": 2010, "mrv": 3}, "mrv/mrnev"),
        ({"mrv": 1, "mrnev": 1}, "mrv or mrnev"),
        ({"start": 2010, "end": 2000}, "after end"),
        ({"per_page": 0}, "per_page"),
        ({"mrv": 0}, "positive"),
    ],
)
def test_build_request_rejects_bad_combinations(kwargs, match):
    with pytest.raises(ValueError, match=match):
        build_request("NY.GDP.MKTP.KD.ZG", "KEN", **kwargs)


def test_build_request_rejects_injection_and_bad_codes():
    with pytest.raises(ValueError, match="indicator"):
        build_request("NY.GDP/../x", "KEN")
    with pytest.raises(ValueError, match="country"):
        build_request("NY.GDP.MKTP.KD.ZG", ["KENYA"])
    with pytest.raises(ValueError, match="at least one"):
        build_request([], "KEN")


def test_build_request_archive_uses_advanced_endpoint():
    url, params = build_request("SP.POP.TOTL", ["BRA"], 2000, 2002, source=2, version="201004")
    assert url == (
        "https://api.worldbank.org/v2/sources/57/country/BRA/series/SP.POP.TOTL/time/YR2000;YR2001;YR2002/version/201004"
    )
    assert "source" not in params and "date" not in params
    url_all, _ = build_request("SP.POP.TOTL", "all", version=201004, source=57)
    assert url_all.endswith("/country/all/series/SP.POP.TOTL/time/all/version/201004")


def test_resolve_source_rules():
    assert resolve_source(2, None) == (2, None)
    assert resolve_source(2, "202004") == (57, "202004")
    assert resolve_source(57, 199704) == (57, "199704")
    with pytest.raises(ValueError, match="source 57"):
        resolve_source(3, "202004")
    with pytest.raises(ValueError, match="needs version"):
        resolve_source(57, None)
    with pytest.raises(ValueError, match="YYYYMM"):
        resolve_source(2, "2020-04")


def test_plan_chunks_respects_60_indicator_limit():
    codes = [f"X.{i:03d}" for i in range(61)]
    chunks = plan_chunks(codes, ["KEN"])
    assert [len(c[0]) for c in chunks] == [60, 1]
    assert all(c[1] == ["KEN"] for c in chunks)
    many = [f"C{i:02d}" for i in range(99)] * 1
    long_countries = [f"{a}{b}{c}" for a in "ABCDEFGHIJ" for b in "ABCDEFGHIJ" for c in "ABCD"]
    chunks = plan_chunks(["X.1"], long_countries)
    assert len(chunks) > 1 and all(len(";".join(c[1])) <= wbmod.MAX_SEGMENT_CHARS for c in chunks)
    assert plan_chunks(many[:2], "all") == [(many[:2], "all")]


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------
def test_parse_single_page(fixtures_dir):
    df = parse(load(fixtures_dir, "worldbank_indicator_single_page_synthetic.json"))
    validate_frame(df)
    assert list(df.columns) == COLUMNS
    assert len(df) == 9
    assert set(df["obs_status"]) <= OBS_STATUS
    ken = df[df["iso3"] == "KEN"].set_index("period")
    assert ken.loc["2020", "value"] == -1.0
    assert ken.loc["2021", "obs_status"] == "forecast"
    assert ken.loc["2019", "unit"] == "annual %"
    assert ken.loc["2019", "dataset"] == "WDI" and ken.loc["2019", "source"] == "worldbank"
    assert ken.loc["2019", "indicator_label"] == "GDP growth (annual %)"
    lmc = df[df["iso3"] == "LMC"].set_index("period")
    assert math.isnan(lmc.loc["2020", "value"])
    assert set(df["iso3"]) == {"KEN", "LMC", "SSA"}
    # deterministic sort
    assert list(df[["indicator", "iso3", "period"]].itertuples(index=False, name=None)) == sorted(
        df[["indicator", "iso3", "period"]].itertuples(index=False, name=None)
    )


def test_parse_page_info_and_flags(fixtures_dir):
    page = parse_page(load(fixtures_dir, "worldbank_indicator_page1_synthetic.json"))
    assert (page.info.page, page.info.pages, page.info.per_page, page.info.total) == (1, 2, 3, 5)
    assert page.info.lastupdated == "2000-01-01" and page.info.sourceid == "2"
    assert page.obs_status_raw == {}


def test_parse_empty(fixtures_dir):
    page = parse_page(load(fixtures_dir, "worldbank_indicator_empty_synthetic.json"))
    assert page.frame.empty and list(page.frame.columns) == COLUMNS
    assert page.info.pages == 0
    validate_frame(page.frame)


def test_parse_error_envelope(fixtures_dir):
    with pytest.raises(WorldBankAPIError) as ei:
        parse(load(fixtures_dir, "worldbank_error_synthetic.json"), url="https://api.worldbank.org/v2/x")
    err = ei.value
    assert err.message_id == "120" and err.key == "Invalid value"
    assert "not valid" in str(err) and "invalid parameter value" in str(err)
    assert not err.transient


def test_parse_xml_error_and_html_page():
    xml = (
        b'\xef\xbb\xbf<?xml version="1.0" encoding="utf-8"?><wb:error xmlns:wb="http://www.worldbank.org">'
        b'<wb:message id="175" key="Invalid format">The indicator was deleted or archived</wb:message></wb:error>'
    )
    with pytest.raises(WorldBankAPIError) as ei:
        parse(xml)
    assert ei.value.message_id == "175" and "archived" in str(ei.value)
    with pytest.raises(WorldBankResponseError):
        parse(b"<html><body>Request Error</body></html>")
    with pytest.raises(WorldBankResponseError):
        parse(b"")
    with pytest.raises(WorldBankResponseError):
        parse(b'{"unexpected": true, "total": 3}')


def test_parse_archive_advanced_format(fixtures_dir):
    page = parse_page(load(fixtures_dir, "worldbank_archive_synthetic.json"), source=57, version="201004")
    df = page.frame
    validate_frame(df)
    assert len(df) == 6  # the incomplete fragment row is skipped
    assert set(df["dataset"]) == {"WDI archive 201004"}
    assert page.version_labels == {"201004": "2010 Apr"}
    ken = df[(df["iso3"] == "KEN") & (df["indicator"] == "SP.POP.TOTL")].set_index("period")
    assert ken.loc["2001", "value"] == 200.0 and math.isnan(ken.loc["2002", "value"])
    gdp = df[df["indicator"] == "NY.GDP.MKTP.KD"].iloc[0]
    assert gdp["unit"] == "constant 2000 US$" and gdp["period"] == "2000"
    assert page.info.per_page == 10000  # string in the advanced format


def test_parse_countries(fixtures_dir):
    df = parse_countries(load(fixtures_dir, "worldbank_countries_synthetic.json"))
    assert list(df["iso3"]) == ["KEN", "UGA", "SSF", "LMC"]
    flags = dict(zip(df["iso3"], df["is_aggregate"]))
    assert flags == {"KEN": False, "UGA": False, "SSF": True, "LMC": True}
    ken = df.set_index("iso3").loc["KEN"]
    assert ken["iso2"] == "KE" and ken["income_level_id"] == "LMC" and ken["region"] == "Sub-Saharan Africa"
    assert ken["lending_type"] == "IDA" and ken["longitude"] == 10.0
    assert math.isnan(df.set_index("iso3").loc["SSF", "latitude"])


def test_parse_indicator_meta(fixtures_dir):
    meta = parse_indicator_meta(load(fixtures_dir, "worldbank_indicator_meta_synthetic.json"))
    assert meta["id"] == "NY.GDP.PCAP.PP.KD"
    assert meta["unit"] == "constant 2021 international $ (PPP 2021)"
    assert meta["ppp_base"] == "2021"
    assert meta["source_id"] == "2" and meta["source_name"] == "World Development Indicators"
    assert meta["source_note"].startswith("SYNTHETIC") and meta["source_organization"].startswith("SYNTHETIC")
    assert meta["topics"] == ["Economy & Growth"] and meta["modelled_ilo"] is False


def test_parse_versions_and_sources(fixtures_dir):
    versions = parse_concept_variables(load(fixtures_dir, "worldbank_archive_versions_synthetic.json"))
    assert dict(zip(versions["id"], versions["label"]))["201004"] == "2010 Apr"
    sources = parse_sources(load(fixtures_dir, "worldbank_sources_synthetic.json"))
    assert list(sources["id"]) == [2, 3, 57]
    assert sources.set_index("id").loc[57, "name"] == "WDI Database Archives"
    assert bool(sources.set_index("id").loc[57, "metadata_available"]) is False


# ---------------------------------------------------------------------------
# units, flags, periods
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name, expected, base",
    [
        ("GDP growth (annual %)", "annual %", None),
        ("GDP (constant 2015 US$)", "constant 2015 US$", None),
        ("GDP per capita, PPP (constant 2021 international $)", "constant 2021 international $ (PPP 2021)", "2021"),
        ("GDP per capita, PPP (constant 2017 international $)", "constant 2017 international $ (PPP 2017)", "2017"),
        ("GNI per capita, PPP (current international $)", "current international $ (PPP)", "unknown"),
        ("Poverty headcount ratio at $3.00 a day (2021 PPP) (% of population)", "% of population (PPP 2021)", "2021"),
        ("GDP per person employed (constant 2021 PPP $)", "constant 2021 PPP $ (PPP 2021)", "2021"),
        ("Unemployment, total (% of total labor force) (modeled ILO estimate)", "% of total labor force", None),
        ("Labor force participation rate, total (% of total population ages 15+) (national estimate)", "% of total population ages 15+", None),
        ("New business density (new registrations per 1,000 people ages 15-64)", "new registrations per 1,000 people ages 15-64", None),
        ("Logistics performance index: Overall (1=low to 5=high)", "1=low to 5=high", None),
        ("Population, total", "", None),
        ("Price level ratio of PPP conversion factor (GDP) to market exchange rate", "", None),
    ],
)
def test_unit_from_name(name, expected, base):
    unit = unit_from_name(name)
    assert unit == expected
    assert ppp_base(unit) == base


def test_unit_from_api_field_wins():
    assert unit_from_name("Some series (ignored)", api_unit="index") == "index"


def test_map_obs_status():
    assert map_obs_status("F") == ("forecast", True)
    assert map_obs_status(" f ") == ("forecast", True)
    assert map_obs_status("") == ("", True)
    assert map_obs_status(None, "Unemployment (modeled ILO estimate)") == ("modelled", True)
    assert map_obs_status("Q") == ("", False)
    for status in ("F", "E", "P", "B", "I", "A", "M"):
        assert map_obs_status(status)[0] in OBS_STATUS


@pytest.mark.parametrize(
    "raw, expected",
    [("2020", "2020"), ("2020Q3", "2020Q3"), ("2012M01", "2012-01"), ("2012M1", "2012-01"), ("YR1975", "1975"), ("yr2001", "2001")],
)
def test_normalize_period(raw, expected):
    assert normalize_period(raw) == expected


# ---------------------------------------------------------------------------
# full fetch path through the cache (offline)
# ---------------------------------------------------------------------------
def test_fetch_indicators_single_page(wb, cache, fixtures_dir):
    url, params = build_request("NY.GDP.MKTP.KD.ZG", ["KEN", "LMC", "SSA"], 2019, 2021)
    put(cache, url, params, load(fixtures_dir, "worldbank_indicator_single_page_synthetic.json"))
    res = wb.fetch_indicators("NY.GDP.MKTP.KD.ZG", ["KEN", "LMC", "SSA"], 2019, 2021)
    validate_frame(res.data)
    assert len(res.data) == 9
    assert res.meta["aggregates"] == ["LMC", "SSA"]
    assert res.meta["lastupdated"] == "2000-01-01" and res.meta["source_id"] == 2
    assert res.meta["indicators"] == {"NY.GDP.MKTP.KD.ZG": "GDP growth (annual %)"}
    assert res.meta["obs_status_raw"] == {"F": 1}
    (prov,) = res.provenance
    assert prov.source == "worldbank" and prov.dataset == "WDI"
    assert prov.license == "CC BY 4.0"
    assert prov.citation == "World Bank, World Development Indicators"
    assert prov.vintage == "last updated 2000-01-01"
    assert prov.endpoint == url and prov.payload_sha256 and prov.tool == "WorldBank.fetch_indicators"
    assert prov.params["date"] == "2019:2021"
    codes = {c.code: c for c in res.caveats}
    assert "WB_FORECAST" in codes and codes["WB_FORECAST"].evidence["periods"] == ["2021"]
    assert res.source_line() == "Source: World Bank, World Development Indicators (last updated 2000-01-01)."
    assert not res.blocked


def test_fetch_indicators_multi_page_multi_indicator(wb, cache, fixtures_dir):
    inds = ["NY.GDP.PCAP.PP.KD", "SL.UEM.TOTL.ZS"]
    for page, name in ((1, "page1"), (2, "page2")):
        url, params = build_request(inds, ["KEN", "UGA"], 2020, 2021, per_page=3, page=page)
        put(cache, url, params, load(fixtures_dir, f"worldbank_indicator_{name}_synthetic.json"))
    res = wb.fetch_indicators(inds, ["KEN", "UGA"], 2020, 2021, per_page=3)
    df = res.data
    validate_frame(df)
    assert len(df) == 5 and len(res.provenance) == 2 and len(res.meta["requests"]) == 2
    assert [p.notes[-1] for p in res.provenance] == ["page 1 of 2", "page 2 of 2"]
    pp = df[df["indicator"] == "NY.GDP.PCAP.PP.KD"]
    assert set(pp["unit"]) == {"constant 2021 international $ (PPP 2021)"}
    uem = df[df["indicator"] == "SL.UEM.TOTL.ZS"]
    assert set(uem["obs_status"]) == {"modelled"} and set(uem["unit"]) == {"% of total labor force"}
    codes = {c.code for c in res.caveats}
    assert {"PPP_BASE", "ILO_MODELLED"} <= codes and "PPP_MIXED_BASES" not in codes
    assert res.meta["aggregates"] == []


def test_fetch_indicators_one_sided_range_filters_client_side(wb, cache, fixtures_dir):
    url, params = build_request("NY.GDP.MKTP.KD.ZG", ["KEN", "LMC", "SSA"])
    assert "date" not in params
    put(cache, url, params, load(fixtures_dir, "worldbank_indicator_single_page_synthetic.json"))
    res = wb.fetch_indicators("NY.GDP.MKTP.KD.ZG", ["KEN", "LMC", "SSA"], start=2020)
    assert sorted(res.data["period"].unique()) == ["2020", "2021"]
    res2 = wb.fetch_indicators("NY.GDP.MKTP.KD.ZG", ["KEN", "LMC", "SSA"], end=2019)
    assert sorted(res2.data["period"].unique()) == ["2019"]


def test_fetch_indicators_empty(wb, cache, fixtures_dir):
    url, params = build_request("IC.BUS.NREG", ["KEN"], 1990, 1991)
    put(cache, url, params, load(fixtures_dir, "worldbank_indicator_empty_synthetic.json"))
    res = wb.fetch_indicators("IC.BUS.NREG", ["KEN"], 1990, 1991)
    assert res.data.empty and list(res.data.columns) == COLUMNS
    assert [c.code for c in res.caveats] == ["WB_NO_DATA"]
    assert res.meta["aggregates"] == [] and len(res.provenance) == 1


def test_fetch_indicators_error_envelope(wb, cache, fixtures_dir):
    url, params = build_request("NOT.A.CODE", ["KEN"])
    put(cache, url, params, load(fixtures_dir, "worldbank_error_synthetic.json"))
    with pytest.raises(WorldBankAPIError, match="120"):
        wb.fetch_indicators("NOT.A.CODE", ["KEN"])


def test_fetch_transient_html_retries_then_raises_offline(wb, cache):
    url, params = build_request("NY.GDP.MKTP.KD.ZG", ["KEN"])
    put(cache, url, params, b"<html>Request Error</html>")
    # The retry bypasses the cache; offline mode blocks it, so the original error surfaces.
    with pytest.raises(WorldBankResponseError):
        wb.fetch_indicators("NY.GDP.MKTP.KD.ZG", ["KEN"])


def test_fetch_wgi_source_sets_dataset_citation_and_caveat(wb, cache, fixtures_dir):
    url, params = build_request("NY.GDP.MKTP.KD.ZG", ["KEN"], 2019, 2021, source=3)
    put(cache, url, params, load(fixtures_dir, "worldbank_indicator_single_page_synthetic.json"))
    res = wb.fetch_indicators("NY.GDP.MKTP.KD.ZG", ["KEN"], 2019, 2021, source=3)
    assert set(res.data["dataset"]) == {"WGI"}
    assert res.provenance[0].citation == "World Bank, Worldwide Governance Indicators"
    assert "WGI_2025_REVISION" in {c.code for c in res.caveats}


def test_fetch_archive_version_routes_to_source_57(wb, cache, fixtures_dir):
    url, params = build_request(["SP.POP.TOTL", "NY.GDP.MKTP.KD"], ["KEN", "SSF"], 2000, 2002, version="201004")
    put(cache, url, params, load(fixtures_dir, "worldbank_archive_synthetic.json"))
    res = wb.fetch_indicators(["SP.POP.TOTL", "NY.GDP.MKTP.KD"], ["KEN", "SSF"], 2000, 2002, source=2, version="201004")
    validate_frame(res.data)
    prov = res.provenance[0]
    assert prov.vintage == "WDI archive 201004"
    assert prov.citation == "World Bank, WDI Database Archives"
    assert prov.dataset == "WDI archive 201004"
    assert "2010 Apr" in prov.notes[0]
    assert res.meta["source_id"] == 57 and res.meta["version"] == "201004" and res.meta["version_label"] == "2010 Apr"
    assert res.meta["aggregates"] == ["SSF"]
    assert "WDI_ARCHIVE_CODE_REUSE" in {c.code for c in res.caveats}
    assert "(WDI archive 201004)" in res.source_line()


def test_fetch_countries_refreshes_aggregate_codes(wb, cache, fixtures_dir):
    url = f"{wbmod.BASE_URL}/country"
    put(cache, url, {"format": "json", "per_page": "1000", "page": "1"}, load(fixtures_dir, "worldbank_countries_synthetic.json"))
    assert wb.aggregate_codes() == KNOWN_AGGREGATES
    df = wb.fetch_countries()
    assert list(df["iso3"]) == ["KEN", "LMC", "SSF", "UGA"]
    assert {"ZG", "XN"} <= set(wb.aggregate_codes()) and KNOWN_AGGREGATES <= wb.aggregate_codes()
    assert df.attrs["provenance"][0]["license"] == "CC BY 4.0"


def test_fetch_indicator_meta(wb, cache, fixtures_dir):
    url = f"{wbmod.BASE_URL}/indicator/NY.GDP.PCAP.PP.KD"
    put(cache, url, {"format": "json", "per_page": "50", "source": "2"}, load(fixtures_dir, "worldbank_indicator_meta_synthetic.json"))
    meta = wb.fetch_indicator_meta("NY.GDP.PCAP.PP.KD", source=2)
    assert meta["name"] == "GDP per capita, PPP (constant 2021 international $)"
    assert meta["unit"].endswith("(PPP 2021)")
    assert meta["provenance"]["endpoint"] == url and meta["provenance"]["citation"] == "World Bank, World Development Indicators"


def test_fetch_archive_versions_and_sources(wb, cache, fixtures_dir):
    put(cache, f"{wbmod.BASE_URL}/sources/57/version/all", {"format": "json", "per_page": "1000", "page": "1"},
        load(fixtures_dir, "worldbank_archive_versions_synthetic.json"))
    put(cache, f"{wbmod.BASE_URL}/sources", {"format": "json", "per_page": "1000", "page": "1"},
        load(fixtures_dir, "worldbank_sources_synthetic.json"))
    versions = wb.fetch_archive_versions()
    assert list(versions["id"]) == ["199704", "201004", "202004"]
    sources = wb.fetch_sources()
    assert 57 in set(sources["id"])


# ---------------------------------------------------------------------------
# caveats and connector contract
# ---------------------------------------------------------------------------
def test_unknown_obs_status_flag_is_reported():
    payload = (
        b'[{"page":1,"pages":1,"per_page":50,"total":1,"lastupdated":"2000-01-01"},'
        b'[{"indicator":{"id":"X.Y","value":"Synthetic series (index)"},"country":{"id":"KE","value":"Kenya"},'
        b'"countryiso3code":"","date":"2020","value":1.0,"unit":"","obs_status":"Q","decimal":0}]]'
    )
    page = parse_page(payload)
    assert page.unknown_obs_status == {"Q": 1}
    assert page.frame.loc[0, "obs_status"] == "" and page.frame.loc[0, "iso3"] == "KE"
    assert page.missing_iso3 == ["KE"]
    cav = {c.code for c in build_caveats(page.frame, source=2, version=None, unknown_obs_status=page.unknown_obs_status, missing_iso3=page.missing_iso3)}
    assert {"WB_UNMAPPED_OBS_STATUS", "WB_ISO3_MISSING"} <= cav


def test_caveats_for_mixed_ppp_lpi_entrepreneurship_and_gfdd():
    df = pd.DataFrame(
        [
            {"indicator": "A", "unit": "constant 2017 international $ (PPP 2017)"},
            {"indicator": "B", "unit": "constant 2021 international $ (PPP 2021)"},
            {"indicator": "LP.LPI.OVRL.XQ", "unit": "1=low to 5=high"},
            {"indicator": "IC.BUS.NDNS.ZS", "unit": "new registrations per 1,000 people ages 15-64"},
        ]
    )
    df["obs_status"] = ""
    codes = {c.code: c for c in build_caveats(df, source=32, version=None)}
    assert codes["PPP_MIXED_BASES"].severity == "warn"
    assert {"LPI_SURVEY_SERIES", "ENTREPRENEURSHIP_LLC_ONLY", "GFDD_STALE"} <= set(codes)


def test_connector_contract():
    assert WorldBank.source == "worldbank"
    assert WorldBank.license == "CC BY 4.0"
    assert WorldBank.domains == ("api.worldbank.org",)
    assert wbmod.SOURCES[2].dataset == "WDI" and wbmod.SOURCES[3].dataset == "WGI" and wbmod.SOURCES[32].dataset == "GFDD"
    assert wbmod.dataset_for(99) == "WB source 99"


def test_fetch_error_with_json_body_becomes_api_error():
    exc = wbmod.http.FetchError('404 from https://api.worldbank.org/v2/x: [{"message":[{"id":"120","key":"Invalid value","value":"bad"}]}]')
    with pytest.raises(WorldBankAPIError, match="120"):
        WorldBank._raise_from_fetch_error(exc, "https://api.worldbank.org/v2/x")
    # plain HTTP errors are left alone
    assert WorldBank._raise_from_fetch_error(wbmod.http.FetchError("500 from x: oops"), "x") is None


def test_fetch_alias_resolve_aggregates_and_json_summary(wb, cache, fixtures_dir):
    import json

    put(cache, f"{wbmod.BASE_URL}/country", {"format": "json", "per_page": "1000", "page": "1"},
        load(fixtures_dir, "worldbank_countries_synthetic.json"))
    url, params = build_request("NY.GDP.MKTP.KD.ZG", ["KEN", "LMC", "SSA"], 2019, 2021)
    put(cache, url, params, load(fixtures_dir, "worldbank_indicator_single_page_synthetic.json"))
    res = wb.fetch("NY.GDP.MKTP.KD.ZG", ["KEN", "LMC", "SSA"], start=2019, end=2021, resolve_aggregates=True)
    assert res.meta["aggregates"] == ["LMC", "SSA"]
    assert "XN" in wb.aggregate_codes()  # ISO2 code of an aggregate, learned from /v2/country
    summary = json.loads(json.dumps(res.summary(max_rows=3)))
    assert summary["rows"] == 9 and summary["provenance"][0]["license"] == "CC BY 4.0"
