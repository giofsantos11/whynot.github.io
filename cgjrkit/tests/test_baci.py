"""Unit tests for the CEPII BACI connector (synthetic zip fixture, no network).

Hand-computed expectations for the synthetic year 2097 (values in thousand US$):
Kenya exports 1000 = 100 to UGA + 300 to USA + 600 to FRA (+ a zero flow to TZA),
so market HHI = .1^2 + .3^2 + .6^2 = .46; by product 060311 = 600, 090111 = 260,
090240 = 140, so product HHI = .36 + .0676 + .0196 = .4472. World exports = 4100;
RCA(KEN, 060311) = (600/1000) / (600/4100) = 4.1 and RCA(KEN, 090111) =
(260/1000) / (660/4100).
"""

import zipfile

import duckdb
import pytest

from cgjrkit.connectors import baci
from cgjrkit.connectors.baci import BACI, BACIError
from cgjrkit.core import http
from cgjrkit.core.cache import Cache
from cgjrkit.core.schema import validate_frame

ZIP = "baci_hs92_v209901_synthetic.zip"
V = "209901"


@pytest.fixture
def zip_path(fixtures_dir):
    return fixtures_dir / ZIP


@pytest.fixture
def store(zip_path, tmp_path):
    manifest = baci.extract_archive(zip_path, tmp_path / "store", [2097, 2098], zip_sha256="synthetic")
    return baci.BaciTables.load(tmp_path / "store"), manifest


# -- pure helpers --------------------------------------------------------------------------------


def test_normalizers_and_url():
    assert baci.normalize_hs("hs92") == "HS92" and baci.normalize_hs("17") == "HS17" and baci.normalize_hs("HS2022") == "HS22"
    assert baci.normalize_version("V202601") == "202601" and baci.normalize_version("202401B") == "202401b"
    assert baci.build_request() == ("https://www.cepii.fr/DATA_DOWNLOAD/baci/data/BACI_HS92_V202601.zip", {})
    assert baci.build_request("HS22", "202401b")[0].endswith("/BACI_HS22_V202401b.zip")
    with pytest.raises(ValueError, match="HS revision"):
        baci.normalize_hs("HS2020")
    with pytest.raises(ValueError, match="invalid BACI version"):
        baci.normalize_version("2026")


def test_archive_members(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        m = baci.archive_members(zf.namelist())
    assert (m.hs, m.version) == ("HS92", V) and list(m.trade) == [2097, 2098]
    assert m.country_codes == f"country_codes_V{V}.csv" and m.product_codes == f"product_codes_HS92_V{V}.csv"
    with pytest.raises(BACIError, match="mixes"):
        baci.archive_members(["BACI_HS92_Y2020_V202601.csv", "BACI_HS17_Y2021_V202601.csv"])
    with pytest.raises(BACIError, match="lacks"):
        baci.archive_members(["BACI_HS92_Y2020_V202601.csv"])
    with pytest.raises(BACIError, match="no BACI"):
        baci.archive_members(["readme.txt"])


def test_readme_and_code_lists(zip_path):
    with zipfile.ZipFile(zip_path) as zf:
        readme = baci.parse_readme(zf.read("Readme.txt").decode())
        countries = baci.read_country_codes(zf.read(f"country_codes_V{V}.csv"))
        products = baci.read_product_codes(zf.read(f"product_codes_HS92_V{V}.csv"))
    assert readme["Version"] == V and readme["Release Date"] == "2099 01 01"
    assert "SYNTHETIC" in readme["Content"]
    nam = countries.set_index("code").loc[516]
    assert nam["iso2"] == "NA" and nam["iso3"] == "NAM"  # Namibia's ISO2 is not a missing value
    assert countries.set_index("code").loc[490, "iso3"] == "S19"
    assert products["code"].str.len().eq(6).all()
    garbled = "country_code,country_name,country_iso2,country_iso3\n384,CÃ´te d'Ivoire,CI,CIV\n".encode("utf-8")
    assert baci.read_country_codes(garbled).loc[0, "name"] == "Côte d'Ivoire"
    padded = baci.read_product_codes(b"code,description\n10121,Horses\n")
    assert padded.loc[0, "code"] == "010121"


# -- extraction -------------------------------------------------------------------------------------


def test_extract_archive(store, zip_path, tmp_path):
    tables, manifest = store
    assert manifest["available_years"] == [2097, 2098] and manifest["years"]["2097"]["rows"] == 15
    assert (tables.dest / "trade_Y2097.parquet").exists() and not list(tables.dest.glob(".tmp*"))
    assert tables.hs == "HS92" and tables.version == V and tables.readme["Version"] == V
    with duckdb.connect() as con:
        row = con.execute(f"SELECT typeof(v), typeof(k), typeof(q) FROM read_parquet('{tables.files([2097])[0]}') LIMIT 1").fetchone()
        nulls = con.execute(f"SELECT count(*) FROM read_parquet('{tables.files([2097])[0]}') WHERE q IS NULL").fetchone()[0]
    assert row == ("DECIMAL(18,3)", "VARCHAR", "DOUBLE") and nulls == 4  # empty and NA quantities
    again = baci.extract_archive(zip_path, tables.dest, [2097], zip_sha256="synthetic")  # idempotent
    assert again["years"] == manifest["years"]
    with pytest.raises(BACIError, match="not in BACI"):
        baci.extract_archive(zip_path, tables.dest, [2050])
    with pytest.raises(BACIError, match="not extracted"):
        baci.BaciTables.load(tmp_path / "store").files([2050])


def _bad_zip(tmp_path, header="t,i,j,k,v,q", year_in_rows=2001):
    path = tmp_path / "bad.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("BACI_HS92_Y2001_V209901.csv", f"{header}\n{year_in_rows},4,12,010121,1.5,\n")
        zf.writestr("country_codes_V209901.csv", "country_code,country_name,country_iso2,country_iso3\n4,A,AF,AFG\n")
        zf.writestr("product_codes_HS92_V209901.csv", "code,description\n010121,Horses\n")
    return path


def test_extract_rejects_bad_files(tmp_path):
    with pytest.raises(BACIError, match="header"):
        baci.extract_archive(_bad_zip(tmp_path, header="year,exporter,importer,product,value,quantity"), tmp_path / "s1")
    with pytest.raises(BACIError, match="does not match"):
        baci.extract_archive(_bad_zip(tmp_path, year_in_rows=2002), tmp_path / "s2")


# -- SQL helpers ------------------------------------------------------------------------------------------


def test_sql_helpers(store):
    tables, _ = store
    f97 = tables.files([2097])
    with duckdb.connect() as con:
        tot = baci.q_exporter_totals(con, f97, [404]).iloc[0]
        assert (tot["value"], tot["n_products"], tot["n_partners"]) == (1000.0, 3, 3)
        imp = baci.q_importer_totals(con, f97, [404]).iloc[0]
        assert (imp["value"], imp["n_partners"]) == (800.0, 3)
        mk = baci.q_market_hhi(con, f97, [404]).iloc[0]
        assert mk["hhi"] == pytest.approx(0.46) and mk["n_units"] == 3 and mk["top_share"] == pytest.approx(0.6)
        pr = baci.q_product_hhi(con, f97, [404], level=6).iloc[0]
        assert pr["hhi"] == pytest.approx(0.4472)
        assert baci.q_product_hhi(con, f97, [404], level=2).iloc[0]["n_units"] == 2  # chapters 06 and 09
        rca = baci.q_rca(con, f97, [404]).set_index("product")["rca"]
        assert rca["060311"] == pytest.approx(4.1) and rca["090111"] == pytest.approx(0.26 / (660 / 4100))
        sel = baci.q_rca(con, f97, [404], products=["090111"])
        assert list(sel["product"]) == ["090111"]
        assert baci.q_count_products(con, f97, [404], min_value=150).iloc[0]["n"] == 2
        assert baci.q_count_products(con, f97, [404], min_rca=2).iloc[0]["n"] == 2
        assert baci.q_count_products(con, f97, [404], min_value=150, min_rca=2).iloc[0]["n"] == 1
        cp = baci.q_country_product(con, f97, level=4, codes=[404])
        assert dict(zip(cp["product"], cp["value"])) == {"0603": 600.0, "0901": 260.0, "0902": 140.0}
        with pytest.raises(ValueError, match="min_value"):
            baci.q_count_products(con, f97)
        with pytest.raises(ValueError, match="level"):
            baci.q_product_hhi(con, f97, level=3)
        assert baci.q_exporter_totals(con, f97, []).empty


def test_codes_for(store):
    tables, _ = store
    assert baci.codes_for(tables.countries, None) is None
    assert baci.codes_for(tables.countries, "KEN,usa") == [404, 842]
    assert baci.codes_for(tables.countries, ["999"]) == [999]
    with pytest.raises(ValueError, match="not in the BACI country list"):
        baci.codes_for(tables.countries, ["ZZZ"])


# -- connector ------------------------------------------------------------------------------------------------


def test_connector_with_local_zip(zip_path):
    b = BACI()
    res = b.market_concentration(["KEN", "UGA"], [2097], version=V, local_zip=zip_path)
    validate_frame(res.data)
    v = res.data.set_index(["indicator", "iso3"])["value"]
    assert v[("export_market_hhi", "KEN")] == pytest.approx(0.46) and v[("export_market_hhi", "UGA")] == pytest.approx(0.5)
    assert v[("export_market_top_share", "KEN")] == pytest.approx(60.0)
    prov, method = res.provenance
    assert (prov.source, prov.dataset, prov.vintage) == ("baci", "BACI HS92", f"BACI V{V}")
    assert prov.license.startswith("Etalab") and prov.endpoint.startswith("file://")
    assert prov.params == {"downloaded_from": baci.build_request("HS92", V)[0]}
    assert "release date 2099 01 01" in prov.notes
    assert method.source == "cgjrkit" and method.tool == "BACI.market_concentration"
    assert method.params["years"] == [2097] and method.params["hs"] == "HS92"
    assert {"baci_units", "baci_version", "hhi_scale"} <= {c.code for c in res.caveats}
    assert res.data["dataset"].unique().tolist() == [f"BACI HS92 V{V}"]


def test_connector_helpers(zip_path):
    b = BACI()
    kw = {"version": V, "local_zip": zip_path}
    tot = b.exporter_totals(["KEN"], [2097, 2098], **kw).data.set_index(["indicator", "period"])["value"]
    assert tot[("exports_total", "2097")] == 1000.0 and tot[("exports_total", "2098")] == 1000.0
    assert tot[("export_markets", "2097")] == 3
    latest = b.importer_totals("KEN", **kw)  # years=None -> latest year in the zip
    assert latest.data["period"].unique().tolist() == ["2098"] and latest.meta["release_date"] == "2099 01 01"
    unmapped = latest.meta["unmapped_codes"]
    assert unmapped == []  # importer filter keeps KEN only
    allx = b.exporter_totals(None, [2098], **kw)
    assert "BACI999" in set(allx.data["iso3"]) and allx.meta["unmapped_codes"] == [999]
    assert "baci_unmapped_codes" in {c.code for c in allx.caveats}
    areas = b.exporter_totals(None, [2097], **kw)
    assert "S19" in set(areas.data["iso3"]) and "baci_areas" in {c.code for c in areas.caveats}
    pc = b.product_concentration("KEN", [2097], level=6, **kw).data.set_index("indicator")["value"]
    assert pc["export_product_hhi_hs6"] == pytest.approx(0.4472) and pc["export_products_hs6"] == 3
    rca = b.rca("KEN", [2097], **kw).data.set_index("indicator")
    assert rca.loc["rca_hs6:060311", "value"] == pytest.approx(4.1)
    assert rca.loc["rca_hs6:060311", "indicator_label"].endswith("Synthetic product A (cut flowers, roses)")
    assert set(b.rca("KEN", [2097], min_rca=2, **kw).data["indicator"]) == {"rca_hs6:060311", "rca_hs6:090240"}
    cnt = b.count_products("KEN", [2097], min_value=150, **kw).data
    assert cnt["indicator"].tolist() == ["export_products_hs6_gt150k"] and cnt["value"].tolist() == [2.0]
    cnt2 = b.count_products("KEN", [2097], min_value=None, min_rca=1, **kw).data
    assert cnt2["indicator"].tolist() == ["export_products_hs6_rca1"] and cnt2["value"].tolist() == [3.0]
    cp = b.country_product_table([2097], level=4, exporters="KEN", **kw).data
    assert set(cp["indicator"]) == {"exports_hs4:0603", "exports_hs4:0901", "exports_hs4:0902"}
    assert b.fetch("UGA", [2097], **kw).data.set_index("indicator").loc["exports_total", "value"] == 400.0


def test_connector_from_cache_and_determinism(zip_path):
    url, params = baci.build_request("HS92", V)
    Cache().put(BACI.source, url, params, zip_path.read_bytes())
    r1 = BACI().market_concentration(None, [2097, 2098], version=V)
    r2 = BACI().market_concentration(None, [2097, 2098], version=V)  # reuses the Parquet store
    assert r1.data_sha256 == r2.data_sha256
    assert r1.provenance[0].endpoint == url and r1.provenance[0].payload_sha256
    assert r1.meta["store"].endswith(f"HS92_V{V}_{r1.provenance[0].payload_sha256[:16]}")


def test_archive_cache_hits_do_not_read_the_zip(zip_path, monkeypatch):
    """Cache hits resolve the object path without reading the payload (HS92 is 2.4 GB)."""
    url, params = baci.build_request("HS92", V)
    first = Cache().put(BACI.source, url, params, zip_path.read_bytes())
    # a second retrieval of the same request (newer payload) to tell snapshot pins from "latest"
    newer = Cache().put(BACI.source, url, params, zip_path.read_bytes() + b"\0")
    Cache().pin("pinned-run", first)

    def no_reads(self, sha256):
        raise AssertionError("payload bytes read on a cache hit")

    def no_fetch(*a, **k):
        raise AssertionError("http.fetch called on a cache hit")

    monkeypatch.setattr(Cache, "get_object", no_reads)
    monkeypatch.setattr(http, "fetch", no_fetch)
    b = BACI()
    path, ret = b.archive("HS92", V)
    assert ret.sha256 == newer.sha256 and path == b.cache.root / "objects" / newer.sha256
    monkeypatch.setenv("CGJRKIT_SNAPSHOT", "pinned-run")
    path, ret = b.archive("HS92", V)
    assert ret.sha256 == first.sha256 and path.read_bytes() == zip_path.read_bytes()
    # refresh=True bypasses "latest" (not the snapshot pin), as http.fetch does
    monkeypatch.delenv("CGJRKIT_SNAPSHOT")
    assert b.cached_retrieval(url, params, refresh=True) is None


def test_archive_miss_goes_through_http_fetch(zip_path, monkeypatch, caplog):
    monkeypatch.delenv("CGJRKIT_OFFLINE")
    calls = []

    def fake_fetch(source, url, params, **kw):
        calls.append((source, url, kw.get("refresh")))
        return zip_path.read_bytes(), Cache().put(source, url, params, zip_path.read_bytes())

    monkeypatch.setattr(http, "fetch", fake_fetch)
    with caplog.at_level("WARNING", logger="cgjrkit.connectors.baci"):
        path, ret = BACI().archive("HS92", V)
    assert calls == [("baci", baci.build_request("HS92", V)[0], False)] and path.exists()
    assert "large download" in caplog.text
    BACI().archive("HS92", V)  # now cached: no second fetch
    assert len(calls) == 1


def test_local_zip_hashed_once(zip_path, tmp_path, monkeypatch):
    local = tmp_path / "BACI_HS92_V209901.zip"
    local.write_bytes(zip_path.read_bytes())
    calls = []
    original = baci.sha256_file
    monkeypatch.setattr(baci, "sha256_file", lambda p: calls.append(p) or original(p))
    b = BACI()
    _, r1 = b.archive("HS92", V, local_zip=local)
    _, r2 = b.archive("HS92", V, local_zip=local)
    assert r1.sha256 == r2.sha256 == baci.sha256_file(zip_path) and len(calls) == 2  # once + the direct call
    local.write_bytes(zip_path.read_bytes() + b"\0")  # file changed: hashed again
    _, r3 = b.archive("HS92", V, local_zip=local)
    assert r3.sha256 != r1.sha256


def test_connector_errors(zip_path):
    b = BACI()
    with pytest.raises(BACIError, match="not HS17"):
        b.exporter_totals("KEN", [2097], hs="HS17", version=V, local_zip=zip_path)
    with pytest.raises(ValueError, match="starts in 2022"):
        b.exporter_totals("KEN", [2010], hs="HS22", version=V, local_zip=zip_path)
    with pytest.raises(BACIError, match="local zip not found"):
        b.exporter_totals("KEN", [2097], version=V, local_zip="/nonexistent/BACI.zip")
    with pytest.raises(http.NetworkBlocked):
        b.exporter_totals("KEN", [2097], version="202601")  # not cached, offline


def test_mix_caveats(zip_path):
    r = BACI().exporter_totals("KEN", [2097], version=V, local_zip=zip_path)
    other = r.data.assign(dataset="BACI HS17 V202601")
    assert [c.code for c in baci.baci_mix_caveats([r, other])] == ["baci_mixed_versions"]
    assert baci.baci_mix_caveats(r) == []
    assert BACI.domains == ("www.cepii.fr",)
