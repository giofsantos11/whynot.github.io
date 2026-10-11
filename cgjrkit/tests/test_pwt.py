"""Unit tests for the Penn World Table connector (synthetic files, no network).

The PWT workbooks/Stata files used here are built in the test in the
documented layout (sheets ``Info``/``Legend``/``Data``; wide table
``countrycode, country, currency_unit, year, <variables>``) with invented
round numbers. They are not PWT data.
"""

import hashlib
import io
import json
import zipfile

import pandas as pd
import pytest

from cgjrkit.connectors import pwt
from cgjrkit.connectors.pwt import PWT, PWTError
from cgjrkit.core import http
from cgjrkit.core.cache import Cache
from cgjrkit.core.provenance import Result
from cgjrkit.core.schema import ppp_base, validate_frame

DATASET_JSON = "pwt_dataverse_dataset_synthetic.json"
COUNTRIES = {"KEN": ("Kenya", "Kenyan Shilling"), "UGA": ("Uganda", "Ugandan Shilling"), "CHN": ("China", "Yuan")}


# -- synthetic files -----------------------------------------------------------------------------


def synthetic_wide(ref_year: int = 2017, years=range(2015, 2020)) -> pd.DataFrame:
    """A small PWT-shaped wide table with obviously synthetic values."""
    rows = []
    for k, (iso, (name, cur)) in enumerate(COUNTRIES.items()):
        for y in years:
            t = y - ref_year
            rows.append({
                "countrycode": iso, "country": name, "currency_unit": cur, "year": y,
                "rgdpe": 1000.0 * (k + 1) + 10 * t, "rgdpo": 1000.0 * (k + 1) + 20 * t,
                "pop": 50.0 + k, "emp": 20.0 + t + k, "avh": float("nan") if iso == "KEN" else 2000.0,
                "hc": 2.0 + 0.1 * t, "cgdpo": 1000.0 * (k + 1) + 30 * t, "cn": 3000.0 * (k + 1),
                "rgdpna": 1000.0 * (k + 1) * (1 + 0.1 * t), "rnna": 4000.0 * (k + 1) * (1 + 0.05 * t),
                "rkna": 1.0 + 0.05 * t, "rtfpna": 1.0 + 0.01 * t, "labsh": 0.5, "delta": 0.05, "xr": 100.0,
                "csh_i": 0.2, "pl_gdpo": 0.4,
                "i_outlier": "Outlier" if (iso == "UGA" and y == 2016) else "Regular",
                "i_xr": "Estimated" if (iso == "KEN" and y == 2015) else "Market",
            })
    return pd.DataFrame(rows)


LEGEND = [
    ("Variable name", "Variable definition"),
    ("Identifier variables", None),
    ("countrycode", "3-letter ISO country code"),
    ("year", "Year"),
    ("Real GDP, employment and population levels", None),
    ("rgdpna", "Real GDP at constant 2017 national prices (in mil. 2017US$)"),
    ("rnna", "Capital stock at constant 2017 national prices (in mil. 2017US$)"),
    ("emp", "Number of persons engaged (in millions)"),
]


def to_xlsx(wide: pd.DataFrame, legend=LEGEND, data_sheet: str = "Data") -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame({"Penn World Table": ["SYNTHETIC TEST WORKBOOK - not PWT data"]}).to_excel(w, sheet_name="Info", index=False)
        if legend is not None:
            pd.DataFrame(legend).to_excel(w, sheet_name="Legend", index=False, header=False)
        wide.to_excel(w, sheet_name=data_sheet, index=False)
    return buf.getvalue()


def to_dta(wide: pd.DataFrame) -> bytes:
    df = wide.copy()
    for c in ("i_outlier", "i_xr"):
        df[c] = pd.Categorical(df[c])
    buf = io.BytesIO()
    df.to_stata(buf, write_index=False, version=118,
                variable_labels={"rgdpna": "Real GDP at constant 2017 national prices (in mil. 2017US$)"})
    return buf.getvalue()


def to_tsv(wide: pd.DataFrame) -> bytes:
    return wide.to_csv(sep="\t", index=False).encode()


@pytest.fixture(scope="module")
def xlsx_payload() -> bytes:
    return to_xlsx(synthetic_wide())


def _seed(url, params, payload):
    Cache().put(PWT.source, url, params, payload)


# -- helpers and request builders ---------------------------------------------------------------


def test_normalize_version():
    assert pwt.normalize_version(None) == "10.01"
    for alias in ("10.01", "1001", "pwt1001", "PWT 10.01"):
        assert pwt.normalize_version(alias) == "10.01"
    for alias in ("11", "11.0", "110", "pwt110"):
        assert pwt.normalize_version(alias) == "11.0"
    assert pwt.normalize_version("10.0") == "10.0"
    with pytest.raises(ValueError, match="unknown PWT version"):
        pwt.normalize_version("9.1")


def test_version_table():
    v1001, v11 = pwt.VERSIONS["10.01"], pwt.VERSIONS["11.0"]
    assert (v1001.doi, v1001.reference_year, v1001.last_year) == ("10.34894/QT5BCC", 2017, 2019)
    assert (v11.doi, v11.reference_year, v11.last_year) == ("10.34894/FABVLR", 2021, 2023)
    assert v1001.dataset == "PWT10.01" and v1001.vintage == "PWT 10.01"
    assert not pwt.VERSIONS["10.0"].on_dataverse


def test_normalize_variables():
    assert pwt.normalize_variables(None) == list(pwt.DEFAULT_VARIABLES)
    assert pwt.normalize_variables("RGDPNA, emp emp;countrycode") == ["rgdpna", "emp"]
    with pytest.raises(ValueError, match="flag"):
        pwt.normalize_variables(["i_outlier"])
    with pytest.raises(ValueError, match="invalid"):
        pwt.normalize_variables(["rgdp-na"])
    with pytest.raises(ValueError, match="no PWT variables"):
        pwt.normalize_variables(["year"])


def test_normalize_countries():
    assert pwt.normalize_countries(None) is None and pwt.normalize_countries("all") is None
    assert pwt.normalize_countries("uga,ken KEN") == ["KEN", "UGA"]
    with pytest.raises(ValueError, match="alpha-3"):
        pwt.normalize_countries(["KE"])


def test_build_request():
    assert pwt.build_request("10.01", "xlsx") == ("https://dataverse.nl/api/access/datafile/354095", {})
    assert pwt.build_request("10.01", "dta") == ("https://dataverse.nl/api/access/datafile/354098", {})
    assert pwt.build_request("11.0", "xlsx") == ("https://dataverse.nl/api/access/datafile/554105", {})
    assert pwt.build_request("11", ".dta") == ("https://dataverse.nl/api/access/datafile/554030", {})
    assert pwt.build_request("10.0") == ("https://www.rug.nl/ggdc/docs/pwt100.xlsx", {})
    with pytest.raises(ValueError, match="file format"):
        pwt.build_request("10.01", "csv")
    assert pwt.build_file_request(7) == ("https://dataverse.nl/api/access/datafile/7", {})
    assert pwt.build_file_request(7, original=True)[1] == {"format": "original"}


def test_build_dataset_request():
    url, params = pwt.build_dataset_request("10.01")
    assert url == "https://dataverse.nl/api/datasets/:persistentId/"
    assert params == {"persistentId": "doi:10.34894/QT5BCC"}
    assert pwt.build_dataset_request("11.0")[1] == {"persistentId": "doi:10.34894/FABVLR"}
    with pytest.raises(ValueError, match="not hosted"):
        pwt.build_dataset_request("10.0")


def test_variable_meta_and_ppp_flags():
    label, unit = pwt.variable_meta("rgdpna", 2021)
    assert label == "Real GDP at constant 2021 national prices"
    assert unit == "million 2021 US$ at constant 2021 national prices (PPP 2021)" and ppp_base(unit) == "2021"
    assert pwt.variable_meta("rkna", 2017)[1] == "index, 2017=1" and ppp_base("index, 2017=1") is None
    assert pwt.variable_meta("emp", 2017)[1] == "millions"
    assert pwt.variable_meta("newvar", 2017) == ("newvar", "")
    assert pwt.is_ppp_based("cgdpo") and pwt.is_ppp_based("csh_i") and not pwt.is_ppp_based("hc")


# -- file reading --------------------------------------------------------------------------------


def test_sniff_format(xlsx_payload):
    wide = synthetic_wide()
    assert pwt.sniff_format(xlsx_payload) == "xlsx"
    assert pwt.sniff_format(to_dta(wide)) == "dta"
    assert pwt.sniff_format(to_tsv(wide)) == "tsv"
    assert pwt.sniff_format(wide.to_csv(index=False).encode()) == "csv"
    assert pwt.sniff_format(b"<!DOCTYPE html><html>") == "html"
    assert pwt.sniff_format(b'{"status":"ERROR"}') == "json"


def test_read_table_xlsx_legend_and_copy(xlsx_payload):
    table = pwt.read_table(xlsx_payload)
    assert table.file_format == "xlsx" and table.sheet == "Data"
    assert table.labels["rgdpna"].startswith("Real GDP at constant 2017")
    assert "Variable name" not in table.labels and "Identifier variables" not in table.labels
    table.data["rgdpna"] = 0.0  # mutating the copy must not leak into the memoised table
    assert pwt.read_table(xlsx_payload).data["rgdpna"].iloc[0] != 0.0


def test_read_table_finds_data_sheet_by_columns():
    payload = to_xlsx(synthetic_wide(), legend=None, data_sheet="Sheet7")
    table = pwt.read_table(payload)
    assert table.sheet == "Sheet7" and table.labels == {}


def test_parse_xlsx_canonical(xlsx_payload):
    info = pwt.parse(xlsx_payload, version="10.01", variables=["rgdpna", "rnna", "emp", "avh", "rtfpna"],
                     countries=["KEN", "UGA"], start=2016, end=2019)
    df = info.frame
    validate_frame(df)
    assert set(df["source"]) == {"pwt"} and set(df["dataset"]) == {"PWT10.01"}
    assert set(df["iso3"]) == {"KEN", "UGA"} and set(df["period"]) == {"2016", "2017", "2018", "2019"}
    assert info.inferred_reference_year == 2017 and info.reference_year == 2017
    # avh is NaN for KEN and dropped; present for UGA
    assert set(df.loc[df["indicator"] == "avh", "iso3"]) == {"UGA"}
    units = df.groupby("indicator")["unit"].first()
    assert units["rgdpna"] == "million 2017 US$ at constant 2017 national prices (PPP 2017)"
    assert ppp_base(units["rnna"]) == "2017" and units["emp"] == "millions" and units["rtfpna"] == "index, 2017=1"
    row = df[(df["indicator"] == "rgdpna") & (df["iso3"] == "KEN") & (df["period"] == "2018")].iloc[0]
    assert row["value"] == pytest.approx(1100.0) and row["country"] == "Kenya"
    assert row["indicator_label"] == "Real GDP at constant 2017 national prices"
    assert info.outliers == [{"iso3": "UGA", "year": 2016}]
    assert info.file_labels["rgdpna"].endswith("(in mil. 2017US$)")
    assert info.file_years == (2015, 2019) and info.file_countries == 3


def test_parse_dropna_false_keeps_missing(xlsx_payload):
    info = pwt.parse(xlsx_payload, variables=["avh"], countries="KEN", dropna=False)
    assert len(info.frame) == 5 and info.frame["value"].isna().all()


def test_parse_missing_variables_and_countries(xlsx_payload):
    info = pwt.parse(xlsx_payload, variables=["rgdpna", "statcap"], countries=["KEN", "ZZZ"])
    assert info.missing_variables == ["statcap"] and info.missing_countries == ["ZZZ"]
    codes = {c.code for c in pwt.build_caveats(info)}
    assert {"pwt_missing_variables", "pwt_missing_countries", "pwt_coverage", "pwt_ppp_base"} <= codes


def test_parse_empty_selection(xlsx_payload):
    info = pwt.parse(xlsx_payload, variables=["rgdpna"], countries=["KEN"], start=1990, end=1991)
    assert len(info.frame) == 0
    assert "pwt_empty" in {c.code for c in pwt.build_caveats(info)}
    with pytest.raises(ValueError, match="after end"):
        pwt.parse(xlsx_payload, start=2019, end=2015)


def test_dta_tsv_and_zip_match_xlsx(xlsx_payload):
    wide = synthetic_wide()
    kw = dict(version="10.01", variables=["rgdpna", "rnna", "emp", "hc", "csh_i"], countries=["KEN", "UGA", "CHN"])
    ref = pwt.parse(xlsx_payload, **kw)
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as zf:
        zf.writestr("readme.txt", "synthetic")
        zf.writestr("pwt1001.xlsx", xlsx_payload)
    for payload, fmt in ((to_dta(wide), "dta"), (to_tsv(wide), "tsv"), (zbuf.getvalue(), "xlsx")):
        other = pwt.parse(payload, **kw)
        assert other.file_format == fmt
        pd.testing.assert_frame_equal(other.frame, ref.frame)
    dta = pwt.parse(to_dta(wide), version="10.01", variables=["rgdpe"], countries=["UGA"])
    assert dta.outliers == [{"iso3": "UGA", "year": 2016}]  # Stata value labels are read
    assert dta.file_labels == {}  # rgdpe has no Stata label in this file


def test_cran_style_isocode_column_is_accepted():
    wide = synthetic_wide().rename(columns={"countrycode": "isocode"})
    info = pwt.parse(to_tsv(wide), variables=["emp"], countries=["KEN"])
    assert set(info.frame["iso3"]) == {"KEN"}


@pytest.mark.parametrize("payload, match", [
    (b"<!DOCTYPE html><html><body>Not found</body></html>", "HTML"),
    (b'{"status":"ERROR","message":"File not found for given id."}', "File not found"),
])
def test_error_payloads(payload, match):
    with pytest.raises(PWTError, match=match):
        pwt.parse(payload)


def test_table_without_identifiers_raises():
    with pytest.raises(PWTError, match="identifier columns"):
        pwt.parse(b"a\tb\n1\t2\n")


def test_zip_without_pwt_file_raises():
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as zf:
        zf.writestr("notes.txt", "synthetic")
    with pytest.raises(PWTError, match="no pwt"):
        pwt.parse(zbuf.getvalue())


def test_reference_year_mismatch_uses_file_year():
    payload = to_tsv(synthetic_wide(ref_year=2021, years=range(2019, 2024)))
    info = pwt.parse(payload, version="10.01", variables=["rgdpna"], countries=["KEN"])
    assert info.inferred_reference_year == 2021 and info.reference_year == 2021
    assert set(info.frame["unit"]) == {"million 2021 US$ at constant 2021 national prices (PPP 2021)"}
    cav = {c.code: c for c in pwt.build_caveats(info)}
    assert cav["pwt_reference_year_mismatch"].severity == "warn"
    assert cav["pwt_reference_year_mismatch"].evidence == {"expected": 2017, "inferred": 2021}


def test_pwt11_units_and_china_caveat():
    payload = to_tsv(synthetic_wide(ref_year=2021, years=range(2019, 2024)))
    info = pwt.parse(payload, version="11.0", variables=["rgdpna", "cgdpo"], countries=None)
    assert set(info.frame["dataset"]) == {"PWT11.0"}
    assert {ppp_base(u) for u in info.frame["unit"]} == {"2021"}
    codes = {c.code for c in pwt.build_caveats(info)}
    assert "pwt_china_series" in codes and "pwt_reference_year_mismatch" not in codes


def test_outlier_and_xr_caveats(xlsx_payload):
    info = pwt.parse(xlsx_payload, variables=["rgdpo", "xr"], countries=None)
    cav = {c.code: c for c in pwt.build_caveats(info)}
    assert cav["pwt_outlier"].severity == "warn" and cav["pwt_outlier"].evidence["count"] == 1
    assert cav["pwt_estimated_xr"].evidence["observations"] == [{"iso3": "KEN", "year": 2015}]
    quiet = pwt.parse(xlsx_payload, variables=["emp"], countries=None)
    assert "pwt_outlier" not in {c.code for c in pwt.build_caveats(quiet)}


def test_infer_reference_year_none_without_index_columns():
    wide = synthetic_wide().drop(columns=["rkna", "rtfpna"])
    assert pwt.infer_reference_year(wide) is None
    info = pwt.parse(to_tsv(wide), version="11.0", variables=["rgdpna"])
    assert info.reference_year == 2021  # falls back to the version table


# -- Dataverse record -----------------------------------------------------------------------------


def test_parse_dataset_files_and_select(fixtures_dir):
    files, ds_version = pwt.parse_dataset_files((fixtures_dir / DATASET_JSON).read_bytes())
    assert ds_version == "9.1" and len(files) == 4
    xlsx = pwt.select_file(files, "10.01", "xlsx")
    assert xlsx["id"] == 900001 and not xlsx["tabular"] and xlsx["checksum_type"] == "MD5"
    dta = pwt.select_file(files, "10.01", "dta")  # ingested: stored as .tab, original name .dta
    assert dta["id"] == 900002 and dta["tabular"] and dta["original_name"] == "pwt1001.dta"
    assert pwt.select_file(files, "11.0", "xlsx")["id"] == 900001  # falls back to the pwt<digits>.xlsx pattern


def test_select_file_ignores_subfolders_and_errors(fixtures_dir):
    files, _ = pwt.parse_dataset_files((fixtures_dir / DATASET_JSON).read_bytes())
    only_detail = [f for f in files if f["directory"] or f["filename"].endswith(".pdf")]
    with pytest.raises(PWTError, match="no main PWT"):
        pwt.select_file(only_detail, "10.01", "xlsx")


def test_parse_dataset_files_errors():
    with pytest.raises(PWTError, match="Dataverse error: Dataset not found"):
        pwt.parse_dataset_files(b'{"status":"ERROR","message":"Dataset not found"}')
    with pytest.raises(PWTError, match="did not return JSON"):
        pwt.parse_dataset_files(b"<html>")


def test_checksum_ok():
    md5 = hashlib.md5(b"abc").hexdigest()
    assert pwt.checksum_ok(b"abc", "MD5", md5) is True
    assert pwt.checksum_ok(b"abd", "MD5", md5) is False
    assert pwt.checksum_ok(b"abc", "SHA-1", hashlib.sha1(b"abc").hexdigest().upper()) is True
    assert pwt.checksum_ok(b"abc", "UNF", "x") is None and pwt.checksum_ok(b"abc", "MD5", "") is None


# -- connector (cache seeded, offline) -------------------------------------------------------------


def test_fetch_pinned_file(xlsx_payload):
    _seed(*pwt.build_request("10.01", "xlsx"), xlsx_payload)
    res = PWT().fetch("10.01", ["rgdpna", "rnna", "emp", "rtfpna"], ["KEN"], 2015, 2019)
    assert isinstance(res, Result)
    validate_frame(res.data)
    assert list(res.data.columns)[:3] == ["source", "dataset", "indicator"]
    assert res.data.equals(res.data.sort_values(["indicator", "iso3", "period"]).reset_index(drop=True))
    prov = res.provenance[-1]
    assert prov.vintage == "PWT 10.01" and prov.dataset == "PWT10.01" and prov.license == "CC BY 4.0"
    assert prov.endpoint == "https://dataverse.nl/api/access/datafile/354095"
    assert prov.payload_sha256 == hashlib.sha256(xlsx_payload).hexdigest()
    assert "Feenstra" in prov.citation and "AER" not in prov.citation and any("DOI 10.34894/QT5BCC" in n for n in prov.notes)
    assert res.meta["ppp_base"] == "2017" and res.meta["file"]["file_id"] == 354095
    assert "American Economic Review" in res.meta["citation"]
    codes = {c.code for c in res.caveats}
    assert {"pwt_ppp_base", "pwt_coverage"} <= codes and not res.blocked
    assert "PWT 10.01" in res.source_line()


def test_fetch_dta_and_url_override(xlsx_payload):
    wide = synthetic_wide()
    _seed(*pwt.build_request("10.01", "dta"), to_dta(wide))
    res = PWT().fetch("10.01", ["emp"], ["UGA"], file_format="dta")
    assert res.meta["file"]["filename"] == "pwt1001.dta" and res.meta["file_format_read"] == "dta"
    mirror = "https://example.org/mirror/pwt1001.xlsx"
    _seed(mirror, {}, xlsx_payload)
    res2 = PWT().fetch("10.01", ["emp"], ["UGA"], url=mirror)
    assert res2.provenance[-1].endpoint == mirror and "custom download URL" in res2.provenance[-1].notes
    pd.testing.assert_frame_equal(res.data, res2.data)
    with pytest.raises(ValueError, match="file_format"):
        PWT().fetch("10.01", file_format="csv")


def test_fetch_resolve_via_dataverse_record(xlsx_payload):
    record = {
        "status": "OK",
        "data": {"latestVersion": {"versionNumber": 9, "versionMinorNumber": 1, "files": [
            {"label": "pwt1001.xlsx", "dataFile": {"id": 900001, "filename": "pwt1001.xlsx",
                                                   "checksum": {"type": "MD5", "value": hashlib.md5(xlsx_payload).hexdigest()}}},
        ]}},
    }
    _seed(*pwt.build_dataset_request("10.01"), json.dumps(record).encode())
    _seed(*pwt.build_file_request(900001), xlsx_payload)
    res = PWT().fetch("10.01", ["rgdpna"], ["KEN"], resolve=True)
    assert res.meta["file"]["dataset_version"] == "9.1" and res.meta["file"]["checksum_ok"] is True
    assert [p.tool for p in res.provenance] == ["PWT.dataset_files", "PWT.fetch"]
    assert res.provenance[0].endpoint == "https://dataverse.nl/api/datasets/:persistentId/"
    assert "pwt_checksum_mismatch" not in {c.code for c in res.caveats}


def test_fetch_resolve_checksum_mismatch_and_ingested_original(fixtures_dir, xlsx_payload):
    _seed(*pwt.build_dataset_request("10.01"), (fixtures_dir / DATASET_JSON).read_bytes())
    _seed(*pwt.build_file_request(900002, original=True), to_dta(synthetic_wide()))
    res = PWT().fetch("10.01", ["emp"], ["KEN"], file_format="dta", resolve=True)
    assert res.provenance[-1].params == {"format": "original"}
    assert res.meta["file"]["checksum_ok"] is False
    assert "pwt_checksum_mismatch" in {c.code for c in res.caveats}


def test_fetch_offline_without_cache_raises():
    with pytest.raises(http.NetworkBlocked):
        PWT().fetch("11.0", ["emp"])


def test_mix_caveats(xlsx_payload):
    a = pwt.parse(xlsx_payload, version="10.01", variables=["rgdpna"]).frame
    b = pwt.parse(to_tsv(synthetic_wide(ref_year=2021, years=range(2019, 2024))), version="11.0",
                  variables=["rgdpna"]).frame
    c = pwt.parse(xlsx_payload, version="10.0", variables=["rgdpna"]).frame
    assert pwt.pwt_mix_caveats(a) == []
    blocked = pwt.pwt_mix_caveats([a, Result(data=b)])
    assert [x.code for x in blocked] == ["pwt_mixed_ppp"] and blocked[0].severity == "block"
    assert blocked[0].evidence["ppp_bases"] == ["2017", "2021"]
    warned = pwt.pwt_mix_caveats([a, c])
    assert [x.code for x in warned] == ["pwt_mixed_versions"] and warned[0].severity == "warn"


def test_per_worker_index_recipe(xlsx_payload):
    """The Kenya Fig 4 recipe (rgdpna/emp, rnna/emp, rtfpna rebased) on synthetic data."""
    df = pwt.parse(xlsx_payload, variables=["rgdpna", "rnna", "emp", "rtfpna"], countries=["KEN"]).frame
    w = df.pivot(index="period", columns="indicator", values="value")
    gdp_pw = 100 * (w.rgdpna / w.emp) / (w.rgdpna / w.emp).loc["2015"]
    assert gdp_pw.loc["2015"] == pytest.approx(100.0)
    # synthetic: rgdpna 800 -> 1200, emp 18 -> 22 between 2015 and 2019
    assert gdp_pw.loc["2019"] == pytest.approx(100 * (1200 / 22) / (800 / 18))
