"""Live tests for the ILOSTAT connector.

Run with ``CGJRKIT_LIVE=1 pytest -m live tests/live/test_ilostat_live.py`` once
``rplumber.ilo.org`` and ``sdmx.ilo.org`` are allowlisted. Each test confirms an
assumption listed as UNVERIFIED (or third-party only) in ``docs/apis/ilostat.md``.

Replication note: Kenya CGJR Figs 31-35 cite "ILOSTAT (Nov 2023)" modelled
estimates. ILOSTAT serves only the current ILOEST edition, so these tests
check structure, identities and the edition-mismatch caveat rather than the
printed Kenya values (Fig 35's 1.8% also needs KNBS value added).
"""

import pytest

from cgjrkit.connectors import ilostat as ilo
from cgjrkit.connectors.ilostat import ILOSTAT
from cgjrkit.core.http import fetch
from cgjrkit.core.schema import ppp_base, validate_frame

pytestmark = pytest.mark.live


def _raw(indicator, countries=None, **kw):
    url, params = ilo.build_request(indicator, countries, **kw)
    payload, _ = fetch("ilostat", ilo.request_url(url, params), None, refresh=True)
    return ilo.read_table(payload)


def test_toc_lists_catalog_and_flags_iloest():
    toc = ILOSTAT().fetch_toc(refresh=True)
    assert {"id", "label", "last_update", "database", "modelled"} <= set(toc.columns)
    for name, spec in ilo.CATALOG.items():
        for code in (spec.modelled, spec.survey):
            if code:
                assert ilo.dataset_id(code) in set(toc["id"]), f"{name}: {code} not in the table of contents"
    emp = ilo.toc_entry(toc, "EMP_2EMP_SEX_ECO_NB_A")
    assert emp.modelled and emp.database == "ILOEST", "database column no longer says ILOEST"
    assert emp.edition, f"no edition in label {emp.label!r}: revisit edition_from_label"
    assert len(emp.last_update) == 10
    une = ilo.toc_entry(toc, "UNE_DEAP_SEX_AGE_RT_A")
    assert not une.modelled


def test_bulk_csv_columns_and_modelled_source():
    t = _raw("UNE_2EAP_SEX_AGE_RT_A", "KEN", start=2015, end=2020)
    assert {"ref_area", "source", "indicator", "sex", "classif1", "time", "obs_value"} <= set(t.columns)
    assert set(t["ref_area"]) == {"KEN"} and t["source"].str.startswith("XA:").all()
    assert t["time"].str.fullmatch(r"\d{4}").all()
    assert set(t["time"].astype(int)) <= set(range(2015, 2021)), "timefrom/timeto were ignored"


def test_literal_plus_and_server_side_filters():
    t = _raw("UNE_2EAP_SEX_AGE_RT_A", ["KEN", "UGA"], sex="SEX_T", classif1="AGE_YTHADULT_YGE15", start=2019, end=2019)
    assert set(t["ref_area"]) == {"KEN", "UGA"}, "the literal '+' separator was not understood"
    assert set(t["sex"]) == {"SEX_T"} and set(t["classif1"]) == {"AGE_YTHADULT_YGE15"}


def test_type_both_adds_labels_with_edition():
    t = _raw("GDP_205U_NOC_NB_A", "KEN", start=2019, end=2020, type="both")
    assert {"ref_area.label", "indicator.label", "source.label"} <= set(t.columns)
    assert ilo.edition_from_label(t["indicator.label"].iloc[0])
    assert ilo.is_modelled_label(t["source.label"].iloc[0])


def test_kenya_employment_by_sector_identity():
    res = ILOSTAT().employment_by_sector(["KEN", "UGA"], 2011, 2022, level="aggregate", refresh=True, expect_edition="Nov 2023")
    validate_frame(res.data)
    assert set(res.data["obs_status"]) == {"modelled"}
    assert res.provenance[0].vintage.startswith("ILOEST ")
    codes = {c.code for c in res.caveats}
    assert "ilo_modelled" in codes
    if res.meta["edition"] != "Nov. 2023":
        assert "ilostat_edition_mismatch" in codes
    ken = res.data[(res.data["iso3"] == "KEN") & (res.data["period"] == "2019")].set_index("indicator")["value"]
    total = ken["EMP_2EMP_SEX_ECO_NB|SEX_T|ECO_AGGREGATE_TOTAL"]
    parts = ken.drop("EMP_2EMP_SEX_ECO_NB|SEX_T|ECO_AGGREGATE_TOTAL")
    assert abs(parts.sum() - total) / total < 0.005, "ISIC aggregates no longer add up to the total"
    assert 10_000 < total < 40_000  # Kenya employment in thousands, order of magnitude only


def test_output_per_worker_ppp_unit():
    res = ILOSTAT().output_per_worker("KEN", 2015, 2020, basis="ppp", refresh=True)
    unit = res.data["unit"].iloc[0]
    assert ppp_base(unit) in ("2017", "2021"), unit
    assert "ilostat_ppp_code_reuse" in {c.code for c in res.caveats}


def test_output_per_worker_ppp_unit_without_label():
    """UNVERIFIED: a type=code payload carries no indicator label, so without the table of contents the PPP
    base is unknown; the unit must keep a PPP marker and the base-unknown caveat must fire."""
    res = ILOSTAT().output_per_worker("KEN", 2015, 2020, basis="ppp", use_toc=False, refresh=True)
    unit = res.data["unit"].iloc[0]
    if res.meta["indicator_label"]:  # the service started sending labels with type=code
        assert ppp_base(unit) in ("2017", "2021"), unit
    else:
        assert ppp_base(unit) == "unknown", unit
        assert "ilostat_unit_base_unknown" in {c.code for c in res.caveats}


def test_projection_caveat_is_per_country():
    """ILOEST 'R' flags per country: values after each country's own last real year are flagged."""
    res = ILOSTAT().unemployment_rate(["KEN", "ZAF", "UGA"], 2010, 2026, refresh=True)
    last = res.meta["last_real_by_country"]
    assert last, "no ILOEST value flagged R (obs_status) in the payload"
    proj = {c.code: c for c in res.caveats}.get("ilostat_modelled_projection")
    for iso3, period in last.items():
        later = sorted(set(res.data.loc[(res.data["iso3"] == iso3) & (res.data["period"] > period), "period"]))
        if later:
            assert proj is not None and set(later) <= set(proj.evidence["by_country"][iso3]), (iso3, period, later)


def test_survey_series_is_not_modelled():
    res = ILOSTAT().unemployment_rate("ZAF", 2015, 2020, modelled=False, refresh=True)
    assert len(res.data) and "modelled" not in set(res.data["obs_status"])
    assert res.provenance[0].vintage.startswith("ILOSTAT update ")


def test_sdmx_matches_bulk():
    bulk = ILOSTAT().unemployment_rate("KEN", 2015, 2020, refresh=True)
    sdmx = ILOSTAT().fetch("UNE_2EAP_SEX_AGE_RT", "KEN", 2015, 2020, sex="SEX_T", classif1="AGE_YTHADULT_YGE15",
                           via="sdmx", refresh=True)
    assert len(sdmx.data), "SDMX key layout REF_AREA.FREQ.MEASURE.SEX.AGE is wrong, or SDMX-CSV columns differ"
    b = bulk.data.set_index("period")["value"]
    s = sdmx.data[sdmx.data["indicator"] == bulk.data["indicator"].iloc[0]].set_index("period")["value"]
    common = b.index.intersection(s.index)
    assert len(common) >= 3 and (abs(b[common] - s[common]) < 0.01).all()
