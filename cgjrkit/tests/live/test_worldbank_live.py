"""Live tests for the World Bank connector (need api.worldbank.org allowlisted).

Run with: CGJRKIT_LIVE=1 pytest -m live tests/live/test_worldbank_live.py

They confirm the UNVERIFIED parts of docs/apis/worldbank.md (archive endpoint
and versions listing, GFDD source id, error envelope) and check replication
targets from ../inventory/kenya-replication-inventory.md.
"""

from __future__ import annotations

import pytest

from cgjrkit.connectors.worldbank import WorldBank, WorldBankAPIError, build_request, decode
from cgjrkit.core import http
from cgjrkit.core.schema import validate_frame

pytestmark = pytest.mark.live


@pytest.fixture
def wb() -> WorldBank:
    return WorldBank()


def _value(df, iso3: str, period: str, indicator: str | None = None) -> float:
    sel = (df["iso3"] == iso3) & (df["period"] == period)
    if indicator:
        sel &= df["indicator"] == indicator
    vals = df.loc[sel, "value"]
    assert len(vals) == 1, f"expected one value for {iso3} {period}, got {len(vals)}"
    return float(vals.iloc[0])


def test_kenya_real_gdp_growth_matches_report_fig1(wb):
    """Kenya CGJR Fig 1: real GDP growth ~8.1% in 2010 and ~-0.3% in 2020
    (WDI; tolerance 0.5 pp because national accounts revise)."""
    res = wb.fetch_indicators("NY.GDP.MKTP.KD.ZG", ["KEN"], 2001, 2024)
    validate_frame(res.data)
    df = res.data
    assert _value(df, "KEN", "2010") == pytest.approx(8.1, abs=0.5)
    assert _value(df, "KEN", "2020") == pytest.approx(-0.3, abs=0.5)
    assert set(df["unit"]) == {"annual %"}
    assert res.provenance[0].vintage.startswith("last updated ")
    assert res.provenance[0].license == "CC BY 4.0"


def test_kenya_fig1_peer_aggregates(wb):
    """Fig 1 compares Kenya with LMIC, UMIC and SSA excluding high income."""
    res = wb.fetch_indicators("NY.GDP.MKTP.KD.ZG", ["KEN", "LMC", "UMC", "SSA"], 2001, 2024)
    assert set(res.data["iso3"]) == {"KEN", "LMC", "UMC", "SSA"}
    assert res.meta["aggregates"] == ["LMC", "SSA", "UMC"]
    assert res.data["period"].min() == "2001"


def test_multi_indicator_request_with_source(wb):
    res = wb.fetch_indicators(["NY.GDP.MKTP.KD.ZG", "SP.POP.TOTL"], ["KEN"], 2019, 2020, source=2)
    assert set(res.data["indicator"]) == {"NY.GDP.MKTP.KD.ZG", "SP.POP.TOTL"}
    assert len(res.meta["requests"]) == 1


def test_pagination_follows_pages(wb):
    res = wb.fetch_indicators("SP.POP.TOTL", "all", 2019, 2020, per_page=100)
    assert len(res.provenance) > 1  # several pages were requested
    assert len(res.data) > 400  # ~265 economies and aggregates x 2 years
    validate_frame(res.data)


def test_raw_response_fields_match_documented_format():
    url, params = build_request("NY.GDP.MKTP.KD.ZG", ["KEN"], 2019, 2020)
    payload, _ = http.fetch("worldbank", url, params, refresh=True)
    obj = decode(payload, url)
    header, rows = obj
    assert {"page", "pages", "per_page", "total", "lastupdated"} <= set(header)
    assert {"indicator", "country", "countryiso3code", "date", "value", "unit", "obs_status", "decimal"} <= set(rows[0])


def test_wdi_archive_source57_version_fetch(wb):
    """WDI Database Archives: Brazil population in the April 2010 release.
    Expected value from the wbgapi README example (db=57, version 201004)."""
    res = wb.fetch_indicators("SP.POP.TOTL", ["BRA"], 2000, 2004, source=2, version="201004")
    validate_frame(res.data)
    assert _value(res.data, "BRA", "2000") == pytest.approx(174174447.0, rel=1e-9)
    assert res.provenance[0].vintage == "WDI archive 201004"
    assert res.meta["source_id"] == 57
    later = wb.fetch_indicators("SP.POP.TOTL", ["BRA"], 2000, 2000, version="201604")
    assert _value(later.data, "BRA", "2000") == pytest.approx(175786441.0, rel=1e-9)


def test_wdi_archive_versions_listing(wb):
    versions = wb.fetch_archive_versions()
    assert "201004" in set(versions["id"])
    assert versions["id"].str.fullmatch(r"\d{6}").all()


def test_countries_and_aggregates(wb):
    df = wb.fetch_countries()
    assert len(df) > 250
    by = df.set_index("iso3")
    assert not by.loc["KEN", "is_aggregate"] and by.loc["KEN", "iso2"] == "KE"
    assert by.loc["KEN", "income_level_id"] == "LMC"
    assert by.loc["SSF", "is_aggregate"] and by.loc["LMC", "is_aggregate"]
    assert {"SSF", "LMC", "UMC", "SSA", "WLD"} <= set(wb.aggregate_codes())


def test_indicator_metadata(wb):
    meta = wb.fetch_indicator_meta("NY.GDP.MKTP.KD.ZG")
    assert meta["name"] == "GDP growth (annual %)" and meta["unit"] == "annual %"
    assert meta["source_note"] and meta["source_organization"]
    ppp = wb.fetch_indicator_meta("NY.GDP.PCAP.PP.KD", source=2)
    assert ppp["ppp_base"] in {"2017", "2021"}


def test_wgi_source3(wb):
    res = wb.fetch_indicators("GOV_WGI_CC.EST", ["KEN"], 2015, 2023, source=3)
    assert len(res.data) > 0 and set(res.data["dataset"]) == {"WGI"}
    assert res.provenance[0].citation == "World Bank, Worldwide Governance Indicators"


def test_gfdd_source_id(wb):
    """Confirms the UNVERIFIED mapping 32 -> Global Financial Development."""
    sources = wb.fetch_sources().set_index("id")
    assert "financial development" in sources.loc[32, "name"].lower()
    assert "archive" in sources.loc[57, "name"].lower()


def test_entrepreneurship_and_lpi_series_are_in_wdi(wb):
    res = wb.fetch_indicators(["IC.BUS.NDNS.ZS", "IC.BUS.NREG", "LP.LPI.OVRL.XQ"], ["KEN"], 2006, 2024)
    present = set(res.data.dropna(subset=["value"])["indicator"])
    assert {"IC.BUS.NDNS.ZS", "LP.LPI.OVRL.XQ"} <= present
    assert {"LPI_SURVEY_SERIES", "ENTREPRENEURSHIP_LLC_ONLY"} <= {c.code for c in res.caveats}


def test_error_envelope_for_invalid_indicator(wb):
    with pytest.raises((WorldBankAPIError, http.FetchError)) as ei:
        wb.fetch_indicators("NOT.A.REAL.CODE", ["KEN"], 2019, 2020)
    if isinstance(ei.value, WorldBankAPIError):
        assert ei.value.message_id in {"120", "175"}
