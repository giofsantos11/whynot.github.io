"""Unit tests for the UNCTADstat FDI connector (synthetic fixtures, no network)."""

import gzip
import io
import zipfile

import pandas as pd
import pytest

from cgjrkit.connectors import unctad
from cgjrkit.connectors.unctad import UNCTAD, UNCTADError
from cgjrkit.core import http
from cgjrkit.core.cache import Cache
from cgjrkit.core.schema import validate_frame

META = "unctad_fdi_metadata_synthetic.json"
FACTS = "unctad_fdi_facts_synthetic.csv"
BULK_CSV = "unctad_fdi_bulk_synthetic.csv"
BULK_7Z = "unctad_fdi_bulk_synthetic.7z"


def _load(fixtures_dir, name) -> bytes:
    return (fixtures_dir / name).read_bytes()


def _seed_meta(fixtures_dir):
    url, params = unctad.build_metadata_request()
    Cache().put(UNCTAD.source, url, params, _load(fixtures_dir, META))


def _seed_bulk(payload, version=None):
    url, params = unctad.build_bulk_request(version=version)
    Cache().put(UNCTAD.source, url, params, payload)


# -- request builders -------------------------------------------------------------------------


def test_request_builders():
    assert unctad.build_metadata_request() == ("https://unctadstat-api.unctad.org/api/reportMetadata/US.FdiFlowsStock/en", {})
    assert unctad.build_bulk_request() == (
        "https://unctadstat-api.unctad.org/bulkdownload/US.FdiFlowsStock/US_FdiFlowsStock", {})
    assert unctad.build_bulk_request(version=1912)[0] == (
        "https://unctadstat-api.unctad.org/bulkdownload/US.FdiFlowsStock/1912/US_FdiFlowsStock")
    url, fields, headers = unctad.build_api_request(measures=("0100", "M5011"), economies=["KEN", "800"],
                                                    years=[2023, 2022, 2023])
    assert url == "https://unctadstat-user-api.unctad.org/US.FdiFlowsStock/cur/Facts"
    assert fields["$select"] == "Economy,Year,Direction,Flow,M0100,M5011"
    assert fields["$filter"] == "Economy/Code in ('404','800') and Year in (2022,2023)"
    assert fields["$format"] == "csv" and headers == ("ClientId", "ClientSecret")
    assert "$filter" not in unctad.build_api_request()[1]


def test_economy_codes():
    assert unctad.to_m49("ken") == "404" and unctad.to_m49("24") == "024" and unctad.to_m49("0000") == "0000"
    assert unctad.to_m49("TWN") == "158" and unctad.to_m49("SUN") == "810"
    assert unctad.economy_codes("KEN,UGA") == {"404", "800"} and unctad.economy_codes(None) == set()
    with pytest.raises(ValueError, match="unknown economy"):
        unctad.to_m49("Kenya")
    assert unctad.M49_TO_ISO3["807"] == "MKD" and len(unctad.M49_TO_ISO3) >= 249


def test_credentials(monkeypatch):
    monkeypatch.delenv(unctad.KEY_ENV, raising=False)
    monkeypatch.delenv(unctad.CLIENT_ID_ENV, raising=False)
    with pytest.raises(UNCTADError, match="CGJRKIT_UNCTAD_CLIENT_ID"):
        unctad.api_credentials()
    monkeypatch.setenv(unctad.CLIENT_ID_ENV, "id-synthetic")
    monkeypatch.setenv(unctad.KEY_ENV, "secret-synthetic")
    assert unctad.api_credentials() == ("id-synthetic", "secret-synthetic")


# -- metadata and archives ------------------------------------------------------------------------


def test_parse_metadata(fixtures_dir):
    m = unctad.parse_metadata(_load(fixtures_dir, META))
    assert (m.name, m.version, m.last_updated) == ("US.FdiFlowsStock", "9999", "2099-01-01T00:00:00")
    assert set(m.measures) == {"5011", "0100", "0106"}
    assert m.dimensions == ["Economy", "Year", "Direction", "Flow"]
    assert m.vintage == "UNCTADstat US.FdiFlowsStock v9999 (2099-01-01)"
    with pytest.raises(UNCTADError, match="not JSON"):
        unctad.parse_metadata(b"<html/>")


def test_read_archive_formats(fixtures_dir):
    text = _load(fixtures_dir, BULK_CSV)
    assert unctad.read_archive(text)[1].startswith('"Year"')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("US_FdiFlowsStock.csv", text)
    assert unctad.read_archive(buf.getvalue())[0] == "US_FdiFlowsStock.csv"
    assert unctad.read_archive(gzip.compress(text))[1] == text.decode()
    with pytest.raises(UNCTADError, match="non-CSV"):
        unctad.read_archive(b"<html>error</html>")


def test_read_7z(fixtures_dir):
    pytest.importorskip("py7zr")
    name, text = unctad.read_archive(_load(fixtures_dir, BULK_7Z))
    assert name == "US_FdiFlowsStock_ST209901.csv"
    assert text == _load(fixtures_dir, BULK_CSV).decode()


def test_measure_labels():
    f = unctad.measure_code_for_label
    assert f("US$ at current prices in millions") == "0106"
    assert f("Millions of US$ at current prices") == "0106"
    assert f("US$ at current prices") == "0100"
    assert f("Percentage of total world") == "5011"
    assert f("Percentage of Gross Domestic Product") == "5025"
    assert f("Percentage of Gross Fixed Capital Formation") == "5026"
    assert f("US$ at current prices per capita") == "0110"
    assert f("Economy Label") is None


# -- parsing ---------------------------------------------------------------------------------------------


def test_bulk_and_facts_agree(fixtures_dir):
    bulk = unctad.to_canonical(unctad.parse_bulk_csv(_load(fixtures_dir, BULK_CSV).decode()), ["KEN", "UGA"],
                               2097, 2098, measures=["usd", "pct_world"]).frame
    facts = unctad.to_canonical(unctad.parse_facts_csv(_load(fixtures_dir, FACTS).decode()), ["KEN", "UGA"],
                                2097, 2098, measures=["usd", "pct_world"]).frame
    cols = ["indicator", "iso3", "period", "value", "unit"]
    pd.testing.assert_frame_equal(bulk[cols], facts[cols])
    validate_frame(bulk)
    v = bulk.set_index(["indicator", "iso3", "period"])["value"]
    assert v[("FDI_INFLOWS_USD_MN", "KEN", "2097")] == 100.0
    assert v[("FDI_INWARD_STOCK_USD_MN", "KEN", "2098")] == 2000.0
    assert v[("FDI_OUTFLOWS_USD_MN", "UGA", "2097")] == 5.0
    assert v[("FDI_INFLOWS_PCT_WORLD", "KEN", "2097")] == 0.1


def test_to_canonical_details(fixtures_dir):
    raw = unctad.parse_facts_csv(_load(fixtures_dir, FACTS).decode())
    p = unctad.to_canonical(raw)  # all economies, default measures (usd, pct_world, pct_gdp)
    df = p.frame
    validate_frame(df)
    assert p.meta["aggregates"] == ["0000"] and p.meta["aggregate_labels"] == {"0000": "World"}
    assert p.meta["unmapped_economies"] == ["736"] and p.meta["measures_used"] == {"usd": "0100", "pct_world": "5011"}
    assert p.meta["dropped_missing_values"] == 8  # 736 in 2098: 4 series x 2 measures
    codes = {c.code for c in p.caveats}
    assert {"unctad_measure_missing", "unctad_negative_flows", "unctad_unmapped_economies", "unctad_directional"} <= codes
    missing = next(c for c in p.caveats if c.code == "unctad_measure_missing")
    assert missing.evidence["missing"] == ["pct_gdp"]
    ken = df[(df["iso3"] == "KEN") & (df["indicator"] == "FDI_INFLOWS_USD_MN")].set_index("period")
    assert ken.loc["2098", "obs_status"] == "estimate" and ken.loc["2097", "obs_status"] == ""
    assert df.loc[df["iso3"] == "UGA", "value"].min() == -50.0
    assert p.meta["footnotes"][0]["text"] == "Synthetic estimate"


def test_series_and_measure_filters(fixtures_dir):
    raw = unctad.parse_bulk_csv(_load(fixtures_dir, BULK_CSV).decode())
    p = unctad.to_canonical(raw, "KEN", measures="usd", series=["FDI_INWARD_STOCK"])
    assert set(p.frame["indicator"]) == {"FDI_INWARD_STOCK_USD_MN"}
    assert "unctad_measure_missing" not in {c.code for c in p.caveats}
    with pytest.raises(ValueError, match="unknown FDI measure"):
        unctad.to_canonical(raw, measures=["pct_exports"])
    with pytest.raises(UNCTADError, match="no measure columns"):
        unctad.parse_bulk_csv("Year,Economy,Economy Label\n2097,404,Kenya\n")
    with pytest.raises(UNCTADError, match="Economy_Code"):
        unctad.parse_facts_csv("Year,Value\n2097,1\n")


# -- connector (offline) ------------------------------------------------------------------------------------


def test_fdi_bulk_route(fixtures_dir):
    pytest.importorskip("py7zr")
    _seed_meta(fixtures_dir)
    _seed_bulk(_load(fixtures_dir, BULK_7Z))
    res = UNCTAD().fdi(["KEN", "UGA"], 2097, 2098, measures=["usd"])
    validate_frame(res.data)
    assert set(res.data["iso3"]) == {"KEN", "UGA"} and len(res.data) == 16
    assert [p.tool for p in res.provenance] == ["UNCTAD.metadata", "UNCTAD.fdi[bulk]"]
    prov = res.provenance[-1]
    assert prov.vintage == "UNCTADstat US.FdiFlowsStock v9999 (2099-01-01)" and prov.license.startswith("CC BY 3.0 IGO")
    assert "bulk member US_FdiFlowsStock_ST209901.csv" in prov.notes
    assert res.meta["release"]["version"] == "9999" and res.meta["bulk_member"] == "US_FdiFlowsStock_ST209901.csv"
    assert "UNCTADstat" in res.source_line()


def test_fdi_bulk_without_metadata(fixtures_dir):
    _seed_bulk(_load(fixtures_dir, BULK_CSV))  # a plain-CSV payload is accepted too
    res = UNCTAD().fdi("KEN", measures=["pct_world"])
    assert "unctad_vintage_unknown" in {c.code for c in res.caveats}
    assert res.provenance[-1].vintage == ""
    assert set(res.data["indicator"]) == {"FDI_INFLOWS_PCT_WORLD", "FDI_OUTFLOWS_PCT_WORLD",
                                          "FDI_INWARD_STOCK_PCT_WORLD", "FDI_OUTWARD_STOCK_PCT_WORLD"}


def test_fdi_pinned_version(fixtures_dir):
    _seed_meta(fixtures_dir)
    _seed_bulk(_load(fixtures_dir, BULK_CSV), version=1912)
    res = UNCTAD().fdi("KEN", version=1912)
    assert "unctad_version_mismatch" in {c.code for c in res.caveats}
    data_prov = res.provenance[-1]
    assert data_prov.endpoint.endswith("/1912/US_FdiFlowsStock")
    # the data provenance names the pinned release, not the current metadata release (v9999)
    assert data_prov.vintage == "UNCTADstat US.FdiFlowsStock v1912 (pinned bulk release)"
    assert "current metadata release: UNCTADstat US.FdiFlowsStock v9999 (2099-01-01)" in data_prov.notes
    # the current-release metadata record is kept in meta, so the source line cites the pinned release only
    assert [p.tool for p in res.provenance] == ["UNCTAD.fdi[bulk]"] and res.meta["pinned_version"] == "1912"
    assert res.meta["current_release_provenance"]["vintage"].startswith("UNCTADstat US.FdiFlowsStock v9999")
    assert "v1912" in res.source_line() and "v9999" not in res.source_line()
    assert unctad.pinned_vintage("US.FdiFlowsStock", "1912", "US_FdiFlowsStock_ST201912.csv") == (
        "UNCTADstat US.FdiFlowsStock v1912 (pinned bulk release, ST201912)")


def test_fdi_api_route(fixtures_dir, monkeypatch):
    _seed_meta(fixtures_dir)
    monkeypatch.setenv(unctad.CLIENT_ID_ENV, "id-synthetic")
    monkeypatch.setenv(unctad.KEY_ENV, "secret-synthetic")
    # measures usd/pct_world/pct_gdp -> 0100, 5011, 5025; 5025 is dropped because the metadata lacks it
    url, fields, _ = unctad.build_api_request(measures=["0100", "5011"], economies=["KEN"], years=[2097, 2098])
    Cache().put(UNCTAD.source, url, fields, _load(fixtures_dir, FACTS))
    res = UNCTAD().fdi(["KEN"], 2097, 2098, route="api")
    validate_frame(res.data)
    assert res.provenance[-1].tool == "UNCTAD.fdi[api]"
    assert "secret-synthetic" not in str(res.provenance[-1].to_dict())  # credentials never in provenance
    assert set(res.data["iso3"]) == {"KEN"} and len(res.data) == 16


def test_fdi_errors(fixtures_dir):
    with pytest.raises(ValueError, match="route"):
        UNCTAD().fdi("KEN", route="sdmx", with_metadata=False)
    with pytest.raises(ValueError, match="needs countries"):
        UNCTAD().fdi(None, route="api", with_metadata=False)
    with pytest.raises(http.NetworkBlocked):
        UNCTAD().fdi("KEN", with_metadata=False)  # nothing cached, offline
    assert UNCTAD.domains == ("unctadstat-api.unctad.org", "unctadstat-user-api.unctad.org")
