"""Unit tests for the Bruegel REER connector (synthetic workbooks, no network).

``tests/fixtures/bruegel_reer_synthetic.zip`` holds a synthetic ``.xls`` in the
documented layout (``READ ME`` sheet, ``REER_MONTHLY_120`` etc. with headers
``REER_120_KE``, periods ``2006M01``); see ``docs/apis/bruegel.md`` for how it
was generated. The xlsx variants are built in the tests. No value is real.
"""

import io
import zipfile
from datetime import datetime

import pandas as pd
import pytest

from cgjrkit.connectors import bruegel
from cgjrkit.connectors.bruegel import BruegelError, BruegelREER
from cgjrkit.core.cache import Cache
from cgjrkit.core.schema import validate_frame

FIXTURE = "bruegel_reer_synthetic.zip"
URL = "https://www.bruegel.org/sites/default/files/2000-01/REER_database_ver01Jan2000_synthetic.zip"
SLOPE = {"KE": 10, "UG": 5}


@pytest.fixture
def payload(fixtures_dir) -> bytes:
    return (fixtures_dir / FIXTURE).read_bytes()


def _seed(url, payload, params=None):
    return Cache().put(BruegelREER.source, url, params or {}, payload)


def synthetic_xlsx(datetime_periods: bool = False) -> bytes:
    """An xlsx workbook in the Bruegel layout (synthetic values, KE and UG only)."""
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        readme = pd.DataFrame([["SYNTHETIC TEST FILE", None, None], [None, None, None],
                               [None, "Country", "code"], [None, "Kenya", "KE"], [None, "Uganda", "UG"]])
        readme.to_excel(w, sheet_name="READ ME", index=False, header=False)
        rows = []
        for y in (2006, 2007, 2008):
            for m in range(1, 13):
                period = datetime(y, m, 1) if datetime_periods else f"{y}M{m:02d}"
                rows.append([period] + [100 + SLOPE[c] * (y - 2007) + (m - 6.5) for c in ("KE", "UG")])
        sheet = pd.DataFrame(rows, columns=[None, "REER_120_KE", "REER_120_UG"])
        sheet.to_excel(w, sheet_name="REER_MONTHLY_120", index=False)
    return buf.getvalue()


# -- helpers ----------------------------------------------------------------------------------------


def test_code_to_iso3():
    assert bruegel.code_to_iso3("KE") == ("KEN", True)
    assert bruegel.code_to_iso3("sq") == ("SRB", True)  # Bruegel's code for Serbia
    assert bruegel.code_to_iso3("EA") == ("EA", False)  # aggregate kept as provider code
    assert bruegel.code_to_iso3("QZ") == ("QZ", False)
    assert bruegel.code_to_iso3("KEN") == ("KEN", True)
    assert len(bruegel.ISO2_TO_ISO3) == 249 and bruegel.ISO2_TO_ISO3["RS"] == "SRB"


def test_normalizers():
    assert bruegel.normalize_frequency("M") == ["MONTHLY"] and bruegel.normalize_frequency("annual") == ["ANNUAL"]
    assert bruegel.normalize_frequency("all") == ["MONTHLY", "QUARTERLY", "ANNUAL"]
    with pytest.raises(ValueError, match="frequency"):
        bruegel.normalize_frequency("weekly")
    assert bruegel.normalize_measure("neer") == ["NEER"] and bruegel.normalize_measure(None) == ["REER", "NEER"]
    with pytest.raises(ValueError, match="measure"):
        bruegel.normalize_measure("RER")
    assert bruegel.normalize_baskets(120) == [120] and bruegel.normalize_baskets("51, 120") == [51, 120]
    assert bruegel.normalize_baskets(None) is None and bruegel.normalize_baskets("all") is None
    assert bruegel.normalize_countries("ken, ke") == ["KE", "KEN"] and bruegel.normalize_countries(None) is None
    with pytest.raises(ValueError, match="invalid country"):
        bruegel.normalize_countries(["Kenya"])


def test_parse_version_and_sheet_info():
    assert bruegel.parse_version("REER_database_ver22Sep2026.zip") == "2026-09-22"
    assert bruegel.parse_version("files/2026-02/REER_database_ver6Apr2020.xls") == "2020-04-06"
    assert bruegel.parse_version("REER_database_ver01Jan2000_synthetic.xls") == "2000-01-01"
    assert bruegel.parse_version("REER_database_ver31Feb2026.zip") is None
    assert bruegel.parse_version("REER_database.zip") is None
    assert bruegel.sheet_info("REER_MONTHLY_120") == ("REER", "MONTHLY", 120)
    assert bruegel.sheet_info("neer_annual_65 ") == ("NEER", "ANNUAL", 65)
    assert bruegel.sheet_info("READ ME") is None and bruegel.sheet_info("CPI_MONTHLY") is None


def test_find_download_links_and_pick_latest():
    html = """<html><a href="/sites/default/files/2026-02/REER_database_ver16Feb2026.zip">Feb</a>
    <a href='https://www.bruegel.org/sites/default/files/2026-09/REER_database_ver22Sep2026.zip'>Sep</a>
    <a href="/sites/default/files/2026-09/REER_database_ver22Sep2026.zip">dup</a>
    <a href="/sites/default/files/2026-01/annual-report.pdf">pdf</a>
    <a href="/sites/default/files/2025-11/other_dataset.xlsx">other</a></html>"""
    links = bruegel.find_download_links(html.encode())
    assert links == ["https://www.bruegel.org/sites/default/files/2026-02/REER_database_ver16Feb2026.zip",
                     "https://www.bruegel.org/sites/default/files/2026-09/REER_database_ver22Sep2026.zip"]
    assert bruegel.pick_latest(links).endswith("ver22Sep2026.zip")
    assert bruegel.pick_latest(["https://www.bruegel.org/sites/default/files/x/REER.zip"]).endswith("REER.zip")
    assert bruegel.pick_latest([]) is None


def test_build_request(monkeypatch):
    assert bruegel.build_request("https://example.org/r.zip") == ("https://example.org/r.zip", {})
    monkeypatch.delenv(bruegel.URL_ENV, raising=False)
    assert bruegel.build_request() == (bruegel.FALLBACK_URL, {})
    monkeypatch.setenv(bruegel.URL_ENV, "https://example.org/env.zip")
    assert bruegel.build_request()[0] == "https://example.org/env.zip"
    assert bruegel.build_page_request() == (bruegel.DATASET_PAGE, {})


# -- parsing ----------------------------------------------------------------------------------------


def test_open_workbook_lists_sheets(payload):
    wb = bruegel.open_workbook(payload)
    assert wb.engine == "xlrd" and wb.member == "REER_database_ver01Jan2000_synthetic.xls"
    assert {"READ ME", "REER_MONTHLY_120", "REER_MONTHLY_51", "REER_ANNUAL_172", "NEER_MONTHLY_120"} <= set(wb.sheets)


def test_parse_default_monthly_reer(payload):
    info = bruegel.parse(payload)
    df = info.frame
    validate_frame(df)
    assert info.sheets_used == ["REER_MONTHLY_51", "REER_MONTHLY_120"]
    assert set(df["source"]) == {"bruegel"} and set(df["dataset"]) == {"REER"}
    assert info.version == "2000-01-01" and info.member.endswith("_synthetic.xls")
    assert info.base_years["REER_MONTHLY_120"] == "2007" and set(df["unit"]) == {"index, 2007=100"}
    assert df["period"].str.fullmatch(r"\d{4}-\d{2}").all()
    m120 = df[df["indicator"] == "REER_MONTHLY_120"]
    assert set(m120["iso3"]) == {"KEN", "UGA", "SRB", "EA", "QZ"}
    assert m120[m120["iso3"] == "SRB"]["period"].min() == "2007-01"  # 2006 blank cells dropped
    assert info.unmapped_codes == ["QZ"] and info.aggregates == {"EA": "Euro area"}
    assert info.skipped_columns == ["REER_51_TZ"]  # wrong basket in this sheet
    assert info.other_sheets == ["READ ME", "CPI_MONTHLY"]
    names = dict(zip(m120["iso3"], m120["country"], strict=True))
    assert names["KEN"] == "Kenya" and names["SRB"] == "Serbia" and names["EA"] == "Euro area"
    label = m120["indicator_label"].iloc[0]
    assert "120 trading partners" in label and "monthly" in label and "appreciation" in label


def test_parse_values_basket_country_and_period_filters(payload):
    info = bruegel.parse(payload, countries=["KEN"], baskets=120, start="2007", end="2007")
    df = info.frame.set_index("period")["value"]
    assert len(df) == 12 and df.index.min() == "2007-01" and df.index.max() == "2007-12"
    assert df["2007-01"] == pytest.approx(94.5) and df["2007-12"] == pytest.approx(105.5)
    assert df.mean() == pytest.approx(100.0)
    by_iso2 = bruegel.parse(payload, countries="KE", baskets=[120]).frame
    assert set(by_iso2["iso3"]) == {"KEN"} and len(by_iso2) == 36


def test_parse_annual_and_neer(payload):
    annual = bruegel.parse(payload, frequency="annual", baskets=[172], countries=["UGA"]).frame
    assert list(annual["period"]) == ["2005", "2006", "2007", "2008"]
    assert list(annual["value"]) == [90.0, 95.0, 100.0, 105.0]
    assert set(annual["indicator"]) == {"REER_ANNUAL_172"}
    neer = bruegel.parse(payload, measure="NEER", countries=["KEN"]).frame
    assert set(neer["indicator"]) == {"NEER_MONTHLY_120"}
    assert neer["indicator_label"].iloc[0].startswith("Nominal effective exchange rate")
    everything = bruegel.parse(payload, frequency="all", measure="all")
    assert len(everything.sheets_used) == 5


def test_missing_basket_and_country(payload):
    info = bruegel.parse(payload, baskets=[120, 999], countries=["KEN", "ZZZ"])
    assert info.missing_baskets == [999] and info.missing_countries == ["ZZZ"]
    codes = {c.code for c in bruegel.build_caveats(info)}
    assert {"bruegel_basket_missing", "bruegel_missing_countries", "bruegel_snapshot"} <= codes
    empty = bruegel.parse(payload, baskets=[999])
    assert len(empty.frame) == 0 and "bruegel_empty" in {c.code for c in bruegel.build_caveats(empty)}


def test_xlsx_bare_and_zipped_match_xls(payload):
    ref = bruegel.parse(payload, countries=["KEN", "UGA"], baskets=[120]).frame
    book = synthetic_xlsx()
    bare = bruegel.parse(book, countries=["KEN", "UGA"], baskets=[120])
    assert bare.member == "" and bare.version is None
    pd.testing.assert_frame_equal(bare.frame, ref)
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as zf:
        zf.writestr("__MACOSX/._REER_database_ver02Feb2000.xlsx", b"junk")
        zf.writestr("REER_database_ver02Feb2000.xlsx", book)
    zipped = bruegel.parse(zbuf.getvalue(), countries=["KEN", "UGA"], baskets=[120])
    assert zipped.member == "REER_database_ver02Feb2000.xlsx" and zipped.version == "2000-02-02"
    pd.testing.assert_frame_equal(zipped.frame, ref)


def test_datetime_period_cells():
    info = bruegel.parse(synthetic_xlsx(datetime_periods=True), countries=["KEN"])
    assert info.frame["period"].iloc[0] == "2006-01" and len(info.frame) == 36


def test_period_helper():
    assert bruegel._period("1993M01", "MONTHLY") == "1993-01"
    assert bruegel._period("1993m1", "MONTHLY") == "1993-01"
    assert bruegel._period(1960.0, "ANNUAL") == "1960" and bruegel._period("1960", "ANNUAL") == "1960"
    assert bruegel._period("1999Q3", "QUARTERLY") == "1999Q3"
    assert bruegel._period(datetime(2001, 8, 1), "QUARTERLY") == "2001Q3"
    assert bruegel._period("Source: Bruegel", "MONTHLY") is None
    assert bruegel._period(1960.0, "MONTHLY") is None and bruegel._period(float("nan"), "ANNUAL") is None


def test_infer_base_year_none_without_clear_base():
    long = pd.DataFrame({"code": ["KE", "KE", "UG", "UG"], "period": ["2006", "2007"] * 2,
                         "value": [80.0, 90.0, 120.0, 130.0]})
    assert bruegel.infer_base_year(long) is None
    assert bruegel.infer_base_year(long.iloc[0:0]) is None


@pytest.mark.parametrize("data, match", [
    (b"<!DOCTYPE html><html>Access denied</html>", "HTML"),
    (b"\x00\x01garbage", "unknown format"),
])
def test_bad_payloads(data, match):
    with pytest.raises(BruegelError, match=match):
        bruegel.parse(data)


def test_zip_without_workbook():
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as zf:
        zf.writestr("readme.txt", "synthetic")
    with pytest.raises(BruegelError, match="no .xls"):
        bruegel.parse(zbuf.getvalue())


def test_parse_readme_variants():
    raw = pd.DataFrame([["notes", None, None], [None, "COUNTRY", "Code"], [None, "Kenya", "KE"],
                        [None, None, None], [None, "Uganda", "UG"], [None, None, None], [None, None, None],
                        [None, None, None], [None, "After table", "XX"]])
    assert bruegel.parse_readme(raw) == {"KE": "Kenya", "UG": "Uganda"}
    assert bruegel.parse_readme(pd.DataFrame([["nothing here"]])) == {}


# -- connector (cache seeded, offline) -----------------------------------------------------------------


def test_fetch_explicit_url(payload):
    ret = _seed(URL, payload)
    res = BruegelREER().fetch("KEN", baskets=120, url=URL)
    validate_frame(res.data)
    assert set(res.data["iso3"]) == {"KEN"} and len(res.data) == 36
    prov = res.provenance[-1]
    assert prov.vintage == "Bruegel REER 2000-01-01" and prov.payload_sha256 == ret.sha256
    assert prov.endpoint == URL and "Darvas" in prov.citation and "attribution" in prov.license
    assert "Working Paper 15/2021" in res.meta["citation"]
    codes = {c.code for c in res.caveats}
    assert {"bruegel_snapshot", "bruegel_realtime_cpi", "bruegel_base"} <= codes
    assert "bruegel_file_changed" not in codes and "bruegel_url_fallback" not in codes
    assert res.meta["payload_sha256"] == ret.sha256 and res.meta["sheets_used"] == ["REER_MONTHLY_120"]
    assert res.meta["base_years"]["REER_MONTHLY_120"] == "2007"


def test_fetch_instance_url_and_env(payload, monkeypatch):
    _seed(URL, payload)
    assert len(BruegelREER(url=URL).fetch("UGA", frequency="annual").data) == 8
    env_url = "https://www.bruegel.org/sites/default/files/2000-02/env_REER.zip"
    _seed(env_url, payload)
    monkeypatch.setenv(bruegel.URL_ENV, env_url)
    res = BruegelREER().fetch("UGA", frequency="annual", baskets=[67])
    assert res.meta["url"] == env_url and len(res.data) == 4


def test_fetch_discovers_link_from_page(payload, monkeypatch):
    monkeypatch.delenv(bruegel.URL_ENV, raising=False)
    page = f'<a href="{URL.replace("https://www.bruegel.org", "")}">Download</a>'.encode()
    _seed(bruegel.DATASET_PAGE, page)
    _seed(URL, payload)
    res = BruegelREER().fetch("KEN", baskets=[51])
    assert res.meta["url"] == URL and not res.meta["used_fallback_url"]
    assert [p.tool for p in res.provenance] == ["BruegelREER.resolve_url", "BruegelREER.fetch"]


def test_fetch_falls_back_when_page_unavailable(payload, monkeypatch):
    monkeypatch.delenv(bruegel.URL_ENV, raising=False)
    _seed(bruegel.FALLBACK_URL, payload)  # offline: the dataset page is not cached
    res = BruegelREER().fetch("KEN", baskets=[120])
    assert res.meta["used_fallback_url"] and "bruegel_url_fallback" in {c.code for c in res.caveats}


def test_fetch_flags_file_changed(payload):
    _seed(URL, synthetic_xlsx())
    _seed(URL, payload)  # same URL, new content: the file was overwritten upstream
    res = BruegelREER().fetch("KEN", baskets=[120], url=URL)
    cav = {c.code: c for c in res.caveats}
    assert cav["bruegel_file_changed"].severity == "warn"
    assert len(cav["bruegel_file_changed"].evidence["sha256_history"]) == 2
    assert res.meta["retrievals"] == 2


def test_fetch_snapshot_vintage_without_version(monkeypatch):
    url = "https://www.bruegel.org/sites/default/files/x/REER_latest.xlsx"
    ret = _seed(url, synthetic_xlsx())
    res = BruegelREER().fetch("KEN", url=url)
    assert res.provenance[-1].vintage.startswith(f"Bruegel REER snapshot sha256:{ret.sha256[:12]}")


def test_sheets_catalogue(payload):
    _seed(URL, payload)
    cat = BruegelREER(url=URL).sheets()
    assert list(cat.columns) == ["sheet", "measure", "frequency", "basket"]
    assert set(cat["sheet"]) == {"REER_MONTHLY_120", "REER_MONTHLY_51", "REER_ANNUAL_172", "REER_ANNUAL_67",
                                 "NEER_MONTHLY_120"}
    assert cat.set_index("sheet").loc["REER_ANNUAL_172", "frequency"] == "annual"
