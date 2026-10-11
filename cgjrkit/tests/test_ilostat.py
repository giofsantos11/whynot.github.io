"""Unit tests for the ILOSTAT connector (synthetic fixtures, no network)."""

import gzip

import pandas as pd
import pytest

from cgjrkit.connectors import ilostat as ilo
from cgjrkit.connectors.ilostat import ILOSTAT, ILOSTATError
from cgjrkit.core.cache import Cache
from cgjrkit.core.schema import OBS_STATUS, ppp_base, validate_frame

EMP = "ilostat_emp_2emp_sex_eco_synthetic.csv"
UNE = "ilostat_une_deap_survey_synthetic.csv"
GDP = "ilostat_gdp_205u_both_synthetic.csv"
TOC = "ilostat_toc_synthetic.csv"
SDMX = "ilostat_sdmx_une_2eap_synthetic.csv"


def _load(fixtures_dir, name) -> bytes:
    return (fixtures_dir / name).read_bytes()


def _seed(url, params, payload):
    """Seed the cache the way ILOSTAT.fetch requests it (composed URL, no params)."""
    Cache().put(ILOSTAT.source, ilo.request_url(url, params), None, payload)


def _seed_toc(fixtures_dir):
    url, params = ilo.build_toc_request()
    _seed(url, params, _load(fixtures_dir, TOC))


def _toc_entry(fixtures_dir, id_):
    return ilo.toc_entry(ilo.parse_toc(_load(fixtures_dir, TOC)), id_)


# -- identifiers and request builders ------------------------------------------------------


def test_resolve_indicator_forms():
    assert ilo.resolve_indicator("EMP_2EMP_SEX_ECO_NB_A") == ("EMP_2EMP_SEX_ECO_NB", "A")
    assert ilo.resolve_indicator("une_tune_sex_age_nb_q") == ("UNE_TUNE_SEX_AGE_NB", "Q")
    assert ilo.resolve_indicator("GDP_205U_NOC_NB") == ("GDP_205U_NOC_NB", "A")
    assert ilo.resolve_indicator("GDP_205U_NOC_NB", freq="q") == ("GDP_205U_NOC_NB", "Q")
    assert ilo.resolve_indicator("unemployment_rate") == ("UNE_2EAP_SEX_AGE_RT", "A")
    assert ilo.resolve_indicator("unemployment_rate", modelled=False) == ("UNE_DEAP_SEX_AGE_RT", "A")
    assert ilo.resolve_indicator("LFPR") == ("EAP_2WAP_SEX_AGE_RT", "A")
    assert ilo.resolve_indicator("output_per_worker_ppp") == ("GDP_211P_NOC_NB", "A")
    with pytest.raises(ValueError, match="no national survey"):
        ilo.resolve_indicator("output_per_worker", modelled=False)
    with pytest.raises(ValueError, match="invalid ILOSTAT indicator"):
        ilo.resolve_indicator("unemployment rate kenya")
    with pytest.raises(ValueError, match="freq"):
        ilo.resolve_indicator("GDP_205U_NOC_NB", freq="W")


def test_catalog_codes_are_well_formed():
    for name, spec in ilo.CATALOG.items():
        for code in (spec.modelled, spec.survey):
            if code:
                assert ilo.resolve_indicator(code) == (code, "A"), name
    assert ilo.classification_dims("EMP_2EMP_SEX_ECO_NB") == ["SEX", "ECO"]
    assert ilo.classification_dims("GDP_205U_NOC_NB") == []


def test_build_request_params_and_literal_plus():
    url, params = ilo.build_request("unemployment_rate", ["uga", "KEN"], 2000, 2024, sex="SEX_T",
                                    classif1=["AGE_YTHADULT_YGE15", "ECO_*"])
    assert url == "https://rplumber.ilo.org/data/indicator/"
    assert params == {
        "id": "UNE_2EAP_SEX_AGE_RT_A",
        "ref_area": "KEN+UGA",
        "sex": "SEX_T",
        "classif1": "AGE_YTHADULT_YGE15",  # wildcard prefixes stay client side
        "timefrom": "2000",
        "timeto": "2024",
        "type": "code",
        "lang": "en",
        "format": ".csv",
    }
    full = ilo.request_url(url, params)
    assert "ref_area=KEN+UGA" in full and "%2B" not in full
    assert full.startswith("https://rplumber.ilo.org/data/indicator/?id=UNE_2EAP_SEX_AGE_RT_A&")
    _, p = ilo.build_request("GDP_205U_NOC_NB_A")
    assert p == {"id": "GDP_205U_NOC_NB_A", "type": "code", "lang": "en", "format": ".csv"}


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"countries": ["KENYA"]}, "ref_area"),
        ({"start": 2024, "end": 2000}, "after end"),
        ({"start": "last"}, "years"),
        ({"type": "xml"}, "type"),
        ({"lang": "de"}, "lang"),
        ({"sex": "SEX T!"}, "classification"),
    ],
)
def test_build_request_validation(kwargs, match):
    with pytest.raises(ValueError, match=match):
        ilo.build_request("GDP_205U_NOC_NB", **kwargs)


def test_build_sdmx_request_key_layout():
    url, params = ilo.build_sdmx_request("EMP_2EMP_SEX_ECO_NB_A", ["UGA", "KEN"], 2000, 2024, sex="SEX_T")
    assert url == "https://sdmx.ilo.org/rest/data/ILO,DF_EMP_2EMP_SEX_ECO_NB,1.0/KEN+UGA.A..SEX_T."
    assert params == {"startPeriod": "2000", "endPeriod": "2024", "format": "csv"}
    url, params = ilo.build_sdmx_request("GDP_205U_NOC_NB")
    assert url.endswith("/ILO,DF_GDP_205U_NOC_NB,1.0/.A.") and params == {"format": "csv"}
    url, _ = ilo.build_sdmx_request("SDG_0852_SEX_AGE_RT", "JPN")
    assert url.endswith("/JPN.A...")  # REF_AREA.FREQ.MEASURE.SEX.AGE, as in the published example


# -- label helpers ----------------------------------------------------------------------------


def test_edition_parsing():
    assert ilo.edition_from_label("Employment by sex and age -- ILO modelled estimates, Nov. 2025 (thousands)") == "Nov. 2025"
    assert ilo.edition_from_label("Employment-to-population ratio -- ILO modelled estimates, November 2018 (%)") == "Nov. 2018"
    assert ilo.edition_from_label("Output per worker (GDP constant 2015 US $) -- ILO modelled estimates, May 2024") == "May. 2024"
    assert ilo.edition_from_label("Unemployment rate by sex and age (%)") == ""
    assert ilo.normalize_edition("Nov 2023") == ilo.normalize_edition("November 2023") == ilo.normalize_edition("2023-11") == "Nov. 2023"
    with pytest.raises(ValueError):
        ilo.normalize_edition("latest")


def test_unit_from_label():
    assert ilo.unit_from_label("Employment by sex and economic activity -- ILO modelled estimates, Nov. 2025 (thousands)") == "thousands"
    assert ilo.unit_from_label("Unemployment rate by sex and age (%)") == "%"
    assert ilo.unit_from_label("Output per worker (GDP constant 2015 US $) -- ILO modelled estimates, Nov. 2025") == "constant 2015 US$ per worker"
    ppp = ilo.unit_from_label("Output per worker (GDP constant 2021 international $ at PPP) -- ILO modelled estimates, Nov. 2025")
    assert ppp == "constant 2021 international $ per worker (PPP 2021)" and ppp_base(ppp) == "2021"
    tot = ilo.unit_from_label("Gross Domestic Product (GDP in millions, constant 2021 international $ at PPP) -- ILO modelled estimates, Nov. 2025")
    assert tot.startswith("millions of constant 2021") and ppp_base(tot) == "2021"
    hour = ilo.unit_from_label("Output per hour worked (GDP constant 2021 international $ at PPP) -- ILO modelled estimates, Nov. 2025")
    assert "per hour worked" in hour
    assert ilo.unit_from_label("", "UNE_2EAP_SEX_AGE_RT") == "%"
    assert ilo.unit_from_label("", "EMP_2EMP_SEX_AGE_NB") == ""
    # no label (type='code' without the table of contents): PPP codes keep a PPP marker, base "unknown"
    assert ilo.unit_from_label("", "GDP_211P_NOC_NB") == "constant international $ per worker (PPP unknown)"
    assert ppp_base(ilo.unit_from_label("", "GDP_211P_NOC_NB")) == "unknown"
    assert ppp_base(ilo.unit_from_label("", "GDP_2HRW_NOC_NB")) == "unknown"
    assert "per hour worked" in ilo.unit_from_label("", "GDP_2HRW_NOC_NB")
    assert ilo.unit_from_label("", "GDP_205U_NOC_NB") == "constant US$ per worker (base year unknown)"
    # a label that names PPP without the base year (catalog label) also keeps the marker
    noyear = ilo.unit_from_label("Output per worker (GDP constant international $ at PPP)")
    assert noyear == "constant international $ per worker (PPP unknown)" and ppp_base(noyear) == "unknown"


def test_periods_and_labels():
    assert [ilo.normalize_period(t) for t in ("2024", "2024Q1", "2024q3", "2024M03", "2024m3", "2024.0")] == [
        "2024", "2024Q1", "2024Q3", "2024-03", "2024-03", "2024"]
    assert ilo.classif_label("SEX_F") == "Female"
    assert ilo.classif_label("ECO_ISIC4_C") == "Economic activity (ISIC-Rev.4): C. Manufacturing"
    assert "composed by cgjrkit" in ilo.classif_label("ECO_ISIC4_HJ") and "H+J" in ilo.classif_label("ECO_ISIC4_HJ")
    assert ilo.classif_label("OCU_SKILL_L3-4") == "OCU_SKILL_L3-4"
    assert ilo.is_aggregate("X01") and not ilo.is_aggregate("KEN") and not ilo.is_aggregate("XKX")


# -- payload decoding ---------------------------------------------------------------------------


def test_read_table_variants(fixtures_dir):
    raw = _load(fixtures_dir, EMP)
    assert raw.startswith(b"\xef\xbb\xbf")
    plain = ilo.read_table(raw)
    gz = ilo.read_table(gzip.compress(raw))
    assert list(plain.columns)[0] == "ref_area" and plain.equals(gz)
    assert ilo.read_table(b"").empty
    with pytest.raises(ILOSTATError, match="JSON"):
        ilo.read_table(b'{"error": "dataset not found"}')
    with pytest.raises(ILOSTATError, match="HTML"):
        ilo.read_table(b"<html><body>Just a moment...</body></html>")
    with pytest.raises(ILOSTATError, match="gzip"):
        ilo.read_table(gzip.compress(raw)[:40])


def test_parse_toc(fixtures_dir):
    toc = ilo.parse_toc(_load(fixtures_dir, TOC))
    assert list(toc["id"]) == ["EMP_2EMP_SEX_ECO_NB_A", "GDP_211P_NOC_NB_A", "UNE_DEAP_SEX_AGE_RT_A"]
    emp = ilo.toc_entry(toc, "EMP_2EMP_SEX_ECO_NB_A")
    assert emp.modelled and emp.edition == "Nov. 2099" and emp.last_update == "2099-12-01" and emp.database == "ILOEST"
    une = ilo.toc_entry(toc, "UNE_DEAP_SEX_AGE_RT_A")
    assert not une.modelled and une.edition == "" and une.last_update == "2099-02-15"
    assert {"data.start", "data.end", "n.records", "classification"} <= set(toc.columns)
    assert ilo.toc_entry(toc, "NOPE_A") is None
    # the open-numbers mirror writes the same columns with underscores
    ddf_style = _load(fixtures_dir, TOC).replace(b"indicator.label", b"indicator_label").replace(b"last.update", b"last_update")
    toc2 = ilo.parse_toc(ddf_style)
    assert ilo.toc_entry(toc2, "EMP_2EMP_SEX_ECO_NB_A").edition == "Nov. 2099"


def test_parse_dictionary(fixtures_dir):
    dic = ilo.parse_dictionary(_load(fixtures_dir, "ilostat_dic_classif1_synthetic.csv"))
    assert list(dic.columns[:2]) == ["code", "label"]
    assert dic.set_index("code").loc["ECO_AGGREGATE_AGR", "label"] == "Economic activity (Aggregate): Agriculture"


# -- parsing observations ---------------------------------------------------------------------


def test_parse_modelled_employment_by_sector(fixtures_dir):
    entry = _toc_entry(fixtures_dir, "EMP_2EMP_SEX_ECO_NB_A")
    p = ilo.parse(_load(fixtures_dir, EMP), indicator="EMP_2EMP_SEX_ECO_NB", toc=entry, countries=["KEN", "X01"],
                  sex="SEX_T", classif1="ECO_AGGREGATE_*", start=2020, end=2024)
    df = p.frame
    validate_frame(df)
    assert set(df["obs_status"]) == {"modelled"} and set(df["dataset"]) == {"ILOEST"}
    assert set(df["iso3"]) == {"KEN", "X01"} and set(df["period"]) == {"2020", "2021", "2022", "2023", "2024"}
    assert set(df["indicator"].str.split("|").str[2]) == {
        "ECO_AGGREGATE_TOTAL", "ECO_AGGREGATE_AGR", "ECO_AGGREGATE_MAN", "ECO_AGGREGATE_CON", "ECO_AGGREGATE_MEL",
        "ECO_AGGREGATE_MKT", "ECO_AGGREGATE_PUB"}
    agr = df[(df["indicator"] == "EMP_2EMP_SEX_ECO_NB|SEX_T|ECO_AGGREGATE_AGR") & (df["iso3"] == "KEN")].set_index("period")["value"]
    assert agr["2020"] == 510.0 and agr["2024"] == 550.0
    assert df["unit"].unique().tolist() == ["thousands"]
    assert df["indicator_label"].iloc[0].startswith("Employment by sex and economic activity -- ILO modelled estimates, Nov. 2099")
    assert p.edition == "Nov. 2099"
    assert p.duplicates_dropped == 1
    assert p.real_value_periods == {"KEN": "2020-2022"} and p.last_real_period == "2022"
    assert p.series["EMP_2EMP_SEX_ECO_NB|SEX_T|ECO_AGGREGATE_MKT"]["classif1_label"].startswith("Economic activity (Aggregate): Trade")
    # synthetic cells are base + 10 per year after 2019; the six aggregate bases sum to the total base
    ken21 = df[(df["iso3"] == "KEN") & (df["period"] == "2021")].set_index("indicator")["value"]
    parts = ken21.drop("EMP_2EMP_SEX_ECO_NB|SEX_T|ECO_AGGREGATE_TOTAL")
    assert len(parts) == 6
    assert parts.sum() - 6 * 20.0 == pytest.approx(ken21["EMP_2EMP_SEX_ECO_NB|SEX_T|ECO_AGGREGATE_TOTAL"] - 20.0)
    codes = {c.code: c for c in ilo.build_caveats(p)}
    assert codes["ilo_modelled"].severity == "warn" and "never splice" in codes["ilo_modelled"].message
    assert codes["ilostat_modelled_projection"].evidence["periods"] == ["2023", "2024"]
    assert "ilostat_edition_unknown" not in codes


def test_modelled_detected_without_toc(fixtures_dir):
    p = ilo.parse(_load(fixtures_dir, EMP), countries="UGA", sex="SEX_F", classif1="ECO_SECTOR_AGR")
    assert len(p.frame) == 6 and set(p.frame["obs_status"]) == {"modelled"}  # XA: source prefix
    assert p.frame["indicator_label"].iloc[0] == "EMP_2EMP_SEX_ECO_NB | Female | Economic activity (Broad sector): Agriculture"
    codes = {c.code for c in ilo.build_caveats(p)}
    assert {"ilo_modelled", "ilostat_edition_unknown"} <= codes


def test_parse_survey_sources_flags_and_notes(fixtures_dir):
    p = ilo.parse(_load(fixtures_dir, UNE))
    df = p.frame
    validate_frame(df)
    assert set(df["dataset"]) == {"ILOSTAT"} and "modelled" not in set(df["obs_status"])
    assert set(df["obs_status"]) <= OBS_STATUS
    ken = df[df["iso3"] == "KEN"].set_index(["indicator", "period"])["value"]
    assert ken[("UNE_DEAP_SEX_AGE_RT|SEX_T|AGE_YTHADULT_YGE15@BA:1111", "2019")] == 10.0
    assert ken[("UNE_DEAP_SEX_AGE_RT|SEX_T|AGE_YTHADULT_YGE15@AA:2222", "2019")] == 12.0
    # only the clashing series is disambiguated; youth and Uganda keep plain keys
    assert ken[("UNE_DEAP_SEX_AGE_RT|SEX_T|AGE_YTHADULT_Y15-24", "2019")] == 20.0
    assert p.disambiguated == ["UNE_DEAP_SEX_AGE_RT|SEX_T|AGE_YTHADULT_YGE15|KEN"]
    status = df.set_index(["iso3", "indicator", "period"])["obs_status"]
    assert status[("KEN", "UNE_DEAP_SEX_AGE_RT|SEX_T|AGE_YTHADULT_YGE15@BA:1111", "2020")] == "break"
    uga = df[df["iso3"] == "UGA"].set_index("period")["value"]
    assert pd.isna(uga["2020"]) and uga["2021"] == 6.0
    assert df["unit"].unique().tolist() == ["%"]
    assert p.notes == {"UNE_DEAP_SEX_AGE_RT|SEX_T|AGE_YTHADULT_YGE15@BA:1111|KEN": ["R1:3903", "T35:5"]}
    codes = {c.code: c for c in ilo.build_caveats(p)}
    assert codes["ilostat_multiple_sources"].severity == "warn"
    assert codes["ilostat_unreliable"].evidence == {"rows": 1}
    assert codes["ilostat_unknown_obs_status"].evidence == {"codes": {"Z": 1}}
    assert codes["ilostat_series_break"].evidence == {"rows": 1}
    assert codes["ilostat_missing_values"].evidence == {"rows": 1}
    assert "ilo_modelled" not in codes


def test_source_filters(fixtures_dir):
    lfs = ilo.parse(_load(fixtures_dir, UNE), sources="BA")  # source type prefix: labour force surveys
    assert not lfs.disambiguated and "AA:2222" not in lfs.sources
    exact = ilo.parse(_load(fixtures_dir, UNE), sources=["AA:2222"])
    assert set(exact.frame["iso3"]) == {"KEN"} and len(exact.frame) == 1
    assert ilo.parse(_load(fixtures_dir, UNE), sources="BA:111").frame.empty  # exact code, not a prefix of BA:1111


def test_parse_type_both_labels(fixtures_dir):
    p = ilo.parse(_load(fixtures_dir, GDP))
    df = p.frame
    assert set(df["country"]) == {"Kenya", "Uganda"} and set(df["obs_status"]) == {"modelled"}
    assert df["unit"].unique().tolist() == ["constant 2015 US$ per worker"]
    assert p.edition == "Nov. 2099" and p.sources == {"XA:9001": "ILO - Modelled Estimates"}
    assert df[(df["iso3"] == "KEN") & (df["period"] == "2024")]["value"].item() == 1400.0


def test_best_source_column_and_mixed_caveat():
    csv = (
        "ref_area,source,indicator,sex,classif1,time,obs_value,obs_status,best_source\n"
        "KEN,BA:1,EMP_TEMP_SEX_AGE_NB,SEX_T,AGE_YTHADULT_YGE15,2019,100,,1\n"
        "KEN,AA:2,EMP_TEMP_SEX_AGE_NB,SEX_T,AGE_YTHADULT_YGE15,2019,110,,0\n"
        "KEN,XA:9,EMP_TEMP_SEX_AGE_NB,SEX_T,AGE_YTHADULT_YGE25,2019,50,R,1\n"
    )
    p = ilo.parse(csv.encode())
    assert p.best_source_dropped == 1 and not p.disambiguated
    assert p.frame.set_index("indicator").loc["EMP_TEMP_SEX_AGE_NB|SEX_T|AGE_YTHADULT_YGE15", "value"] == 100.0
    codes = {c.code for c in ilo.build_caveats(p)}
    assert "ilo_modelled_and_survey_mixed" in codes
    both = ilo.parse(csv.encode(), best_source=False)
    assert both.disambiguated and len(both.frame) == 3


def test_parse_sdmx(fixtures_dir):
    p = ilo.parse_sdmx(_load(fixtures_dir, SDMX), indicator="UNE_2EAP_SEX_AGE_RT")
    df = p.frame
    validate_frame(df)
    assert list(df["indicator"].unique()) == ["UNE_2EAP_SEX_AGE_RT|SEX_T|AGE_YTHADULT_YGE15"]
    assert set(df["obs_status"]) == {"modelled"} and list(df["value"]) == [5.0, 6.0, 7.0]
    assert p.last_real_period == "2021"
    with pytest.raises(ILOSTATError, match="TIME_PERIOD"):
        ilo.parse_sdmx(b"A,B\n1,2\n", indicator="X_Y_Z")


def test_edition_mismatch_and_ppp_caveats(fixtures_dir):
    entry = _toc_entry(fixtures_dir, "EMP_2EMP_SEX_ECO_NB_A")
    p = ilo.parse(_load(fixtures_dir, EMP), toc=entry, countries="KEN", sex="SEX_T", classif1="ECO_SECTOR_TOTAL")
    mism = {c.code: c for c in ilo.build_caveats(p, expect_edition="Nov 2023")}["ilostat_edition_mismatch"]
    assert mism.evidence == {"expected": "Nov. 2023", "returned": "Nov. 2099"}
    assert "ilostat_edition_mismatch" not in {c.code for c in ilo.build_caveats(p, expect_edition="2099-11")}
    ppp_csv = "ref_area,source,indicator,time,obs_value,obs_status\nKEN,XA:1,GDP_211P_NOC_NB,2020,100,R\n"
    ppp = ilo.parse(ppp_csv.encode(), toc=_toc_entry(fixtures_dir, "GDP_211P_NOC_NB_A"))
    assert ppp_base(ppp.frame["unit"].iloc[0]) == "2021"
    assert "ilostat_ppp_code_reuse" in {c.code for c in ilo.build_caveats(ppp)}


def test_ppp_base_unknown_caveat_without_label():
    csv = b"ref_area,source,indicator,time,obs_value,obs_status\nKEN,XA:9001,GDP_211P_NOC_NB,2098,1000.0,R\n"
    p = ilo.parse(csv, indicator="GDP_211P_NOC_NB", toc=None)
    codes = {c.code: c for c in ilo.build_caveats(p)}
    assert codes["ilostat_unit_base_unknown"].severity == "warn"
    assert codes["ilostat_unit_base_unknown"].evidence["units"] == ["constant international $ per worker (PPP unknown)"]
    usd = ilo.parse(csv.replace(b"GDP_211P", b"GDP_205U"), indicator="GDP_205U_NOC_NB")
    assert "ilostat_unit_base_unknown" in {c.code for c in ilo.build_caveats(usd)}


_ILO_PEERS = """ref_area,source,indicator,sex,time,obs_value,obs_status
KEN,XA:9001,UNE_2EAP_SEX_RT,SEX_T,2096,10.0,R
KEN,XA:9001,UNE_2EAP_SEX_RT,SEX_T,2097,10.0,
KEN,XA:9001,UNE_2EAP_SEX_RT,SEX_F,2096,10.0,
KEN,XA:9001,UNE_2EAP_SEX_RT,SEX_F,2097,10.0,
UGA,XA:9002,UNE_2EAP_SEX_RT,SEX_T,2096,20.0,R
UGA,XA:9002,UNE_2EAP_SEX_RT,SEX_T,2097,20.0,R
TZA,XA:9003,UNE_2EAP_SEX_RT,SEX_T,2096,30.0,
TZA,XA:9003,UNE_2EAP_SEX_RT,SEX_T,2097,30.0,
X01,XA:9004,UNE_2EAP_SEX_RT,SEX_T,2096,40.0,
X01,XA:9004,UNE_2EAP_SEX_RT,SEX_T,2097,40.0,
"""


def test_modelled_projection_is_per_country_and_series():
    """Synthetic: KEN real to 2096 (totals only), UGA real to 2097, TZA never real, X01 is an aggregate."""
    p = ilo.parse(_ILO_PEERS.encode())
    assert p.last_real_by_area == {"KEN": "2096", "UGA": "2097"} and p.last_real_period == "2097"
    assert p.projected_periods == {"KEN": ["2097"]}
    # KEN SEX_F has no R row at all, TZA none either; X01 (aggregate) is left out
    assert p.no_real_periods == {"KEN": ["2096", "2097"], "TZA": ["2096", "2097"]}
    codes = {c.code: c for c in ilo.build_caveats(p)}
    proj = codes["ilostat_modelled_projection"]
    assert proj.evidence == {"periods": ["2097"], "by_country": {"KEN": ["2097"]}, "last_real_by_country": {"KEN": "2096"}}
    assert "KEN after 2096" in proj.message
    assert codes["ilostat_modelled_no_real_value"].evidence == {"by_country": {"KEN": "2096-2097", "TZA": "2096-2097"}}
    # without any R flag in the payload (e.g. no obs_status column) nothing is inferred
    bare = ilo.parse(_ILO_PEERS.replace(",R\n", ",\n").encode())
    assert bare.projected_periods == {} and bare.no_real_periods == {}


def test_parse_rejects_label_only_tables():
    with pytest.raises(ILOSTATError, match="type='code'"):
        ilo.parse(b"ref_area.label,indicator.label,time.label,obs_value\nKenya,X,2020,1\n")


# -- connector (offline, cache seeded with fixtures) ---------------------------------------------


def test_fetch_offline_end_to_end(fixtures_dir):
    _seed_toc(fixtures_dir)
    url, params = ilo.build_request("EMP_2EMP_SEX_ECO_NB", ["KEN", "UGA", "X01"], 2019, 2024, sex="SEX_T")
    _seed(url, params, _load(fixtures_dir, EMP))
    res = ILOSTAT().fetch("employment_by_sector", ["KEN", "UGA", "X01"], 2019, 2024, sex="SEX_T",
                          classif1="ECO_SECTOR_*", expect_edition="Nov. 2023")
    validate_frame(res.data)
    assert set(res.data["obs_status"]) == {"modelled"}
    assert res.meta["aggregates"] == ["X01"] and res.meta["edition"] == "Nov. 2099"
    assert res.meta["last_update"] == "2099-12-01" and res.meta["id"] == "EMP_2EMP_SEX_ECO_NB_A"
    prov = res.provenance[0]
    assert prov.vintage == "ILOEST Nov. 2099" and prov.license == "CC BY 4.0" and prov.dataset == "ILOEST"
    assert prov.params["id"] == "EMP_2EMP_SEX_ECO_NB_A" and prov.payload_sha256
    assert "ref_area=KEN+UGA+X01" in prov.endpoint
    assert any(p.dataset == "ILOSTAT table of contents" for p in res.provenance)
    codes = {c.code for c in res.caveats}
    assert {"ilo_modelled", "ilostat_edition_mismatch", "ilostat_modelled_projection"} <= codes
    assert "ILOEST Nov. 2099" in res.source_line()


def test_fetch_wrappers_and_missing_toc(fixtures_dir):
    # no table of contents in the cache: offline fetch still works, the note says why
    url, params = ilo.build_request("EMP_2EMP_SEX_ECO_NB", "KEN", sex="SEX_T")
    _seed(url, params, _load(fixtures_dir, EMP))
    res = ILOSTAT().employment_by_sector("KEN", level="aggregate")
    assert len(res.data) == 7 * 6 and res.provenance[0].vintage == ""
    assert any("table of contents unavailable" in n for n in res.provenance[0].notes)
    assert "ilostat_edition_unknown" in {c.code for c in res.caveats}
    with pytest.raises(ValueError, match="level"):
        ILOSTAT().employment_by_sector("KEN", level="division")
    with pytest.raises(ValueError, match="basis"):
        ILOSTAT().output_per_worker("KEN", basis="eur")


def test_fetch_survey_vintage_from_toc(fixtures_dir):
    _seed_toc(fixtures_dir)
    url, params = ilo.build_request("UNE_DEAP_SEX_AGE_RT", None)
    _seed(url, params, _load(fixtures_dir, UNE))
    res = ILOSTAT().unemployment_rate(None, sex=None, age=None, modelled=False, sources="BA")
    assert res.provenance[0].vintage == "ILOSTAT update 2099-02-15"
    assert res.meta["indicator_label"] == "Unemployment rate by sex and age (%)"
    assert res.data["indicator_label"].iloc[0].startswith("Unemployment rate by sex and age (%) | Total")


def test_fetch_via_sdmx(fixtures_dir):
    url, params = ilo.build_sdmx_request("UNE_2EAP_SEX_AGE_RT", "KEN", 2020, 2022)
    Cache().put(ILOSTAT.source, url, params, _load(fixtures_dir, SDMX))
    res = ILOSTAT().fetch("UNE_2EAP_SEX_AGE_RT", "KEN", 2020, 2022, via="sdmx", use_toc=False)
    assert len(res.data) == 3 and res.meta["via"] == "sdmx"
    assert any("UNVERIFIED" in n for n in res.provenance[0].notes)
    with pytest.raises(ValueError, match="via"):
        ILOSTAT().fetch("UNE_2EAP_SEX_AGE_RT", via="ftp", use_toc=False)


def test_connector_declares_domains():
    assert set(ILOSTAT.domains) == {"rplumber.ilo.org", "sdmx.ilo.org"}
    assert ILOSTAT.source == "ilostat"
