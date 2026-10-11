"""Seeded-defect tests for cgjrkit.qa.caveat_check.

Every rule has at least one test that must BLOCK or WARN on a planted
defect, and a clean counterpart that must not fire. All data below are
synthetic round numbers built inline; none of it is real data.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import make_frame
from cgjrkit.qa import caveat_check as cc
from cgjrkit.qa.caveat_check import CaveatReport, RuleOutput, run_caveat_check

EXPECTED_RULES = [
    "ppp_mixing",
    "ilo_modelled",
    "forecast_years",
    "vintage_drift",
    "missing_provenance",
    "series_break",
    "covid_endpoints",
    "peers_not_frozen",
    "small_n",
    "licence_restricted",
]


# ---------------------------------------------------------------------------
# builders (synthetic data only)
# ---------------------------------------------------------------------------
def prov(source: str = "pwt", dataset: str = "PWT11.0", vintage: str = "PWT 11.0", citation: str = "", **kw) -> Provenance:
    return Provenance(source=source, dataset=dataset, vintage=vintage, citation=citation,
                      payload_sha256=kw.pop("payload_sha256", "0" * 64), retrieved_at="2026-01-01T00:00:00Z", **kw)


def rows(years, *, source="pwt", dataset="PWT11.0", indicator="IND", iso3="KEN", country="Kenya", unit="index",
         status="", label="Synthetic indicator", value=100.0, **extra) -> list[dict]:
    out = []
    for y in years:
        st = status(y) if callable(status) else status
        out.append({"source": source, "dataset": dataset, "indicator": indicator, "indicator_label": label, "iso3": iso3,
                    "country": country, "period": str(y), "value": value, "unit": unit, "obs_status": st, **extra})
    return out


def result(records, provenance=None, *, meta=None, caveats=None) -> Result:
    df = make_frame(records)
    extra = [c for c in pd.DataFrame(records).columns if c not in df.columns]
    for c in extra:
        df[c] = pd.DataFrame(records)[c].to_numpy()
    return Result(data=df, provenance=[prov()] if provenance is None else provenance, meta=meta or {},
                  caveats=caveats or [])


def codes(report: CaveatReport, severity: str | None = None) -> list[str]:
    return [c.code for c in report.caveats if severity is None or c.severity == severity]


def only(rule: str, results, context=None) -> CaveatReport:
    return run_caveat_check(results, context, rules=[rule])


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------
def test_registry_holds_every_rule_in_order():
    assert list(cc.RULES)[: len(EXPECTED_RULES)] == EXPECTED_RULES
    listed = cc.list_rules()
    assert [r["name"] for r in listed][: len(EXPECTED_RULES)] == EXPECTED_RULES
    assert all(r["description"] and r["severities"] for r in listed)


def test_clean_result_passes():
    res = result(rows(range(2010, 2020)))
    report = run_caveat_check(res)
    assert report.status == "PASS"
    assert report.footnotes == []
    assert report.rules_run == EXPECTED_RULES
    assert not report.blocked
    assert [c for c in report.caveats if c.severity != "info"] == []


def test_single_result_and_list_are_equivalent():
    res = result(rows(range(2010, 2013)))
    assert run_caveat_check(res).to_dict() == run_caveat_check([res]).to_dict()


def test_report_is_json_safe_and_deterministic():
    a = result(rows([2024, 2025, 2026], status=lambda y: "forecast" if y >= 2025 else ""))
    b = result(rows([2010, 2011], unit="US$ (PPP 2017)"), [prov(vintage="PWT 10.01", dataset="PWT10.01")])
    first = run_caveat_check([a, b]).to_dict()
    second = run_caveat_check([a, b]).to_dict()
    assert first == second
    json.dumps(first)


def test_inherited_block_caveat_blocks_unless_disabled():
    blocker = Caveat("PPP_MIXED", "block", "planted defect")
    res = result(rows([2020]), caveats=[blocker])
    report = run_caveat_check(res)
    assert report.status == "BLOCK"
    assert blocker in report.inherited and blocker in report.caveats
    assert report.by_rule()["inherited"] == [blocker]
    assert run_caveat_check(res, {"inherit_caveats": False}).status == "PASS"


def test_rules_subset_skip_and_unknown_names():
    res = result(rows([2024, 2025], status="forecast"))
    assert run_caveat_check(res, rules=["series_break"]).status == "PASS"
    assert run_caveat_check(res, skip=["forecast_years"]).status == "PASS"
    assert run_caveat_check(res, rules=["forecast_years"]).status == "WARN"
    with pytest.raises(ValueError, match="unknown caveat rules"):
        run_caveat_check(res, rules=["no_such_rule"])
    with pytest.raises(ValueError):
        run_caveat_check(res, skip=["typo"])


def test_rule_error_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(cc, "RULES", dict(cc.RULES))

    @cc.register_rule("explodes", "test rule that raises")
    def _explodes(inp):  # pragma: no cover - body raises
        raise RuntimeError("boom")

    report = run_caveat_check(result(rows([2020])))
    assert report.status == "WARN"
    assert "explodes" in report.errors and "boom" in report.errors["explodes"]
    assert any(c.code == "rule_error" and c.evidence["rule"] == "explodes" for c in report.caveats)


def test_register_rule_rejects_duplicates_and_bad_severity(monkeypatch):
    monkeypatch.setattr(cc, "RULES", dict(cc.RULES))
    with pytest.raises(ValueError, match="already registered"):
        cc.register_rule("ppp_mixing", "dup")(lambda inp: RuleOutput())
    cc.register_rule("ppp_mixing", "replaced", replace=True)(lambda inp: RuleOutput())
    assert cc.RULES["ppp_mixing"].description == "replaced"
    with pytest.raises(ValueError, match="severities"):
        cc.register_rule("x", "bad", ("fatal",))


def test_bad_inputs():
    with pytest.raises(ValueError):
        run_caveat_check([])
    with pytest.raises(TypeError):
        run_caveat_check(["not a result"])


def test_unknown_context_keys_are_reported_as_info():
    report = run_caveat_check(result(rows([2020])), {"replication_mode": True})
    cav = [c for c in report.caveats if c.code == "context_keys_unused"]
    assert cav and cav[0].severity == "info" and cav[0].evidence["keys"] == ["replication_mode"]
    assert report.status == "PASS"


# ---------------------------------------------------------------------------
# period helpers
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("text, expected", [
    ("2024", (2024, 2024)), ("2024Q1", (2024, 2024)), ("2024-03", (2024, 2024)), ("2024M12", (2024, 2024)),
    ("FY2019/20", (2019, 2020)), ("2011-2019", None), ("2019-20", None), ("abc", None),
])
def test_period_years(text, expected):
    assert cc.period_years(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("2011-2019", (2011, 2019)), ("2016-21", (2016, 2021)), ("2011–2019", (2011, 2019)),
    ("2019Q1-2021Q4", (2019, 2021)), ("2024-03", None), ("2024", None), ("2020-2010", None), ("2015 to 2020", (2015, 2020)),
])
def test_window_years(text, expected):
    assert cc.window_years(text) == expected


# ---------------------------------------------------------------------------
# ppp_mixing
# ---------------------------------------------------------------------------
def test_ppp_mixing_blocks_2017_with_2021():
    """Seeded defect (plan S2): Kenya Fig 22 pinned to 2017 PPP mixed with a 2021 PPP series."""
    a = result(rows([2019], indicator="GDPPC", unit="constant 2017 international $ (PPP 2017)"))
    b = result(rows([2019], indicator="GDPPC21", unit="constant 2021 international $ (PPP 2021)"))
    report = only("ppp_mixing", [a, b])
    assert report.status == "BLOCK"
    cav = report.caveats[0]
    assert cav.code == "ppp_mixing" and cav.severity == "block"
    assert sorted(cav.evidence["ppp_bases"]) == ["2017", "2021"]


def test_ppp_single_base_passes():
    a = result(rows([2019], unit="constant 2021 international $ (PPP 2021)"))
    b = result(rows([2019], indicator="B", unit="million 2021 US$ at chained PPPs (PPP 2021)"))
    assert only("ppp_mixing", [a, b]).caveats == []


def test_ppp_unstated_base_warns_and_unknown_plus_known_blocks():
    unknown = result(rows([2019], unit="current international $ (PPP)"))
    report = only("ppp_mixing", unknown)
    assert report.status == "WARN" and codes(report) == ["ppp_base_unstated"]
    known = result(rows([2019], indicator="B", unit="constant 2021 international $ (PPP 2021)"))
    assert only("ppp_mixing", [unknown, known]).status == "BLOCK"


def test_ppp_context_pin_blocks_other_base():
    res = result(rows([2019], unit="constant 2021 international $ (PPP 2021)"))
    report = only("ppp_mixing", res, {"ppp_base": "2017"})
    assert report.status == "BLOCK" and codes(report) == ["ppp_base_mismatch"]
    assert only("ppp_mixing", res, {"ppp_base": "2021"}).status == "PASS"


def test_ppp_reads_extra_unit_columns():
    res = result(rows([2019], unit="constant 2021 international $ (PPP 2021)", level_unit="US$ (PPP 2017)"))
    assert only("ppp_mixing", res).status == "BLOCK"


# ---------------------------------------------------------------------------
# ilo_modelled
# ---------------------------------------------------------------------------
def ilo_rows(years, status="modelled", indicator="EMP_2EMP_SEX_AGE_NB|SEX_T|AGE_YTHADULT_YGE15", dataset="ILOEST", **kw):
    return rows(years, source="ilostat", dataset=dataset, indicator=indicator, status=status,
                label=kw.pop("label", "Employment by sex and age -- ILO modelled estimates, Nov. 2025 (thousands)"),
                unit="thousands", **kw)


ILO_PROV = prov("ilostat", "ILOEST", "ILOEST Nov. 2025", "ILOSTAT, International Labour Organization (ILO)")


def test_ilo_modelled_warns_with_footnote():
    report = only("ilo_modelled", result(ilo_rows(range(2015, 2025)), [ILO_PROV]))
    assert report.status == "WARN"
    assert codes(report) == ["ilo_modelled"]
    assert report.caveats[0].evidence["vintages"] == ["ILOEST Nov. 2025"]
    assert report.footnotes == [
        "Values are ILO modelled estimates (ILOSTAT, International Labour Organization (ILO), ILOEST Nov. 2025)."]


def test_wdi_modelled_ilo_label_counts_as_ilo():
    res = result(rows(range(2015, 2020), source="worldbank", dataset="WDI", indicator="SL.UEM.TOTL.ZS", status="modelled",
                      label="Unemployment, total (% of total labor force) (modeled ILO estimate)"),
                 [prov("worldbank", "WDI", "last updated 2026-07-01", "World Bank, World Development Indicators")])
    report = only("ilo_modelled", res)
    assert report.status == "WARN" and codes(report) == ["ilo_modelled"]


def test_ilo_splice_in_one_series_blocks():
    """Seeded defect: ILO modelled 2010-14 continued by national survey values from 2015 (break flagged)."""
    recs = ilo_rows(range(2010, 2015)) + ilo_rows(range(2015, 2021), status=lambda y: "break" if y == 2015 else "",
                                                  dataset="spliced")
    report = only("ilo_modelled", result(recs, [ILO_PROV]))
    assert report.status == "BLOCK"
    splice = [c for c in report.caveats if c.code == "ilo_modelled_splice"][0]
    assert splice.evidence["n"] == 1
    assert splice.evidence["series"][0]["modelled_periods"] == ["2010", "2014"]
    assert splice.evidence["series"][0]["non_modelled_periods"] == ["2015", "2020"]


def test_ilo_splice_across_results_blocks():
    modelled = result(ilo_rows(range(2010, 2015)), [ILO_PROV])
    survey = result(ilo_rows(range(2015, 2020), status="", dataset="ILOSTAT"), [prov("ilostat", "ILOSTAT", "")])
    assert only("ilo_modelled", [modelled, survey]).status == "BLOCK"


def test_modelled_with_forecast_or_estimate_is_not_a_splice():
    recs = ilo_rows(range(2015, 2024)) + ilo_rows([2024, 2025], status="forecast") + ilo_rows([2026], status="estimate")
    report = only("ilo_modelled", result(recs, [ILO_PROV]))
    assert report.status == "WARN" and "ilo_modelled_splice" not in codes(report)


def test_modelled_and_survey_as_separate_indicators_only_warn():
    recs = ilo_rows(range(2015, 2020)) + ilo_rows(range(2015, 2020), status="", dataset="ILOSTAT",
                                                  indicator="EMP_TEMP_SEX_AGE_NB|SEX_T|AGE_YTHADULT_YGE15",
                                                  label="Employment by sex and age (thousands)")
    report = only("ilo_modelled", result(recs, [ILO_PROV]))
    assert report.status == "WARN"
    assert report.footnotes and report.footnotes[0].startswith("Values for Employment by sex and age -- ILO modelled")


def test_missing_values_do_not_create_a_splice():
    recs = ilo_rows(range(2010, 2015)) + ilo_rows([2015], status="", value=float("nan"))
    assert only("ilo_modelled", result(recs, [ILO_PROV])).status == "WARN"


def test_non_ilo_modelled_values_are_info():
    res = result(rows(range(2015, 2020), source="wpp", dataset="WPP2024", status="modelled", label="Population"),
                 [prov("wpp", "WPP2024", "WPP 2024")])
    report = only("ilo_modelled", res)
    assert report.status == "PASS" and codes(report) == ["modelled_estimates"]


def test_harmonize_splice_output_is_blocked():
    harmonize = pytest.importorskip("cgjrkit.methods.harmonize")
    old = result(ilo_rows(range(2010, 2017), value=100.0), [ILO_PROV])
    new = result(ilo_rows(range(2015, 2021), status="", dataset="ILOSTAT", value=110.0,
                          indicator="EMP_TEMP_SEX_AGE_NB|SEX_T|AGE_YTHADULT_YGE15", label="Employment (thousands)"),
                 [prov("ilostat", "ILOSTAT", "")])
    spliced = harmonize.splice(old, new, ("2015", "2016"), method="ratio")
    report = run_caveat_check(spliced)
    assert report.status == "BLOCK"
    assert "ilo_modelled_splice" in codes(report, "block")
    assert "series_break" in codes(report)


# ---------------------------------------------------------------------------
# forecast_years
# ---------------------------------------------------------------------------
WEO_PROV = prov("imf", "WEO", "WEO 2026-04", "IMF, World Economic Outlook database")


def weo_rows(years, first_forecast, iso3="KEN"):
    return rows(years, source="imf", dataset="WEO", indicator="NGDP_RPCH", iso3=iso3, unit="%",
                status=lambda y: "forecast" if y >= first_forecast else "")


def test_forecast_years_warns_with_footnote():
    report = only("forecast_years", result(weo_rows(range(2020, 2031), 2026), [WEO_PROV]))
    assert report.status == "WARN"
    assert codes(report) == ["forecast_years"]
    assert report.caveats[0].evidence["rows"] == 5
    assert report.footnotes == ["Values for 2026 onward are forecasts (IMF, World Economic Outlook database, WEO 2026-04)."]


def test_forecast_first_year_varies_by_series():
    recs = weo_rows(range(2020, 2030), 2025) + weo_rows(range(2020, 2030), 2026, iso3="UGA")
    report = only("forecast_years", result(recs, [WEO_PROV]))
    assert report.footnotes == ["Values for 2025 onward are forecasts (IMF, World Economic Outlook database, WEO 2026-04); "
                                "the first forecast year varies by series (2025-2026)."]


def test_forecast_without_vintage_says_so():
    report = only("forecast_years", result(weo_rows(range(2024, 2028), 2026), [prov("imf", "WEO", "", "IMF WEO")]))
    assert report.footnotes == ["Values for 2026 onward are forecasts (IMF WEO, vintage not recorded)."]


def test_forecast_inside_window_average():
    res = result(rows(["2024-2026"], source="imf", dataset="WEO", status="forecast"), [WEO_PROV])
    report = only("forecast_years", res)
    assert report.status == "WARN"
    assert report.footnotes == ["Averages for 2024-2026 include forecast years (IMF, World Economic Outlook database, WEO 2026-04)."]


def test_no_forecast_no_caveat():
    assert only("forecast_years", result(weo_rows(range(2020, 2025), 2030), [WEO_PROV])).caveats == []


# ---------------------------------------------------------------------------
# vintage_drift
# ---------------------------------------------------------------------------
def test_vintage_drift_warns():
    a = result(weo_rows(range(2020, 2024), 2030), [WEO_PROV])
    b = result(weo_rows(range(2020, 2024), 2030, iso3="UGA"), [prov("imf", "WEO", "WEO 2025-10", "IMF, World Economic Outlook database")])
    report = only("vintage_drift", [a, b])
    assert report.status == "WARN"
    assert report.caveats[0].evidence["vintages"] == ["WEO 2025-10", "WEO 2026-04"]
    assert report.footnotes == ["IMF, World Economic Outlook database data combine vintages WEO 2025-10 and WEO 2026-04."]


def test_same_vintage_or_different_datasets_do_not_drift():
    a = result(rows([2019]), [prov("pwt", "PWT11.0", "PWT 11.0")])
    b = result(rows([2019], iso3="UGA"), [prov("pwt", "PWT11.0", "PWT 11.0"), prov("pwt", "PWT10.01", "PWT 10.01")])
    assert only("vintage_drift", [a, b]).caveats == []


def test_unstated_vintage_next_to_stated_is_info():
    res = result(rows([2019]), [prov("pwt", "PWT11.0", "PWT 11.0"), prov("pwt", "PWT11.0", "", endpoint="other")])
    report = only("vintage_drift", res)
    assert report.status == "PASS" and codes(report) == ["vintage_unstated"]


# ---------------------------------------------------------------------------
# missing_provenance
# ---------------------------------------------------------------------------
def test_no_provenance_blocks():
    report = only("missing_provenance", result(rows([2019]), []))
    assert report.status == "BLOCK" and codes(report) == ["missing_provenance"]


def test_plain_dataframe_input_blocks():
    report = only("missing_provenance", make_frame(rows([2019])))
    assert report.status == "BLOCK"


def test_unbuildable_source_line_blocks():
    res = result(rows([2019]), [Provenance(source="")])
    report = only("missing_provenance", res)
    assert report.status == "BLOCK" and "no source line" in report.caveats[0].message


def test_only_cgjrkit_steps_blocks():
    res = result(rows([2019], source="cgjrkit", dataset="derived"),
                 [Provenance(source="cgjrkit", dataset="harmonize", tool="harmonize.growth_rate")])
    report = only("missing_provenance", res)
    assert report.status == "BLOCK"
    assert report.caveats[0].evidence["tools"] == ["harmonize.growth_rate"]


def test_non_provenance_entries_block():
    res = result(rows([2019]), [{"source": "pwt"}])
    assert only("missing_provenance", res).status == "BLOCK"


def test_data_source_missing_from_provenance_warns():
    recs = rows([2019]) + rows([2019], source="imf", dataset="WEO", indicator="X")
    report = only("missing_provenance", result(recs, [prov()]))
    assert report.status == "WARN"
    assert report.caveats[0].code == "provenance_incomplete" and report.caveats[0].evidence["sources"] == ["imf"]


def test_unpinned_provenance_is_info_and_complete_provenance_is_clean():
    assert only("missing_provenance", result(rows([2019]))).caveats == []
    report = only("missing_provenance", result(rows([2019]), [prov(payload_sha256="")]))
    assert report.status == "PASS" and codes(report) == ["provenance_unpinned"]


# ---------------------------------------------------------------------------
# series_break
# ---------------------------------------------------------------------------
def test_series_break_warns_with_footnote():
    res = result(rows(range(2013, 2018), label="Employment", status=lambda y: "break" if y == 2015 else ""))
    report = only("series_break", res)
    assert report.status == "WARN"
    assert report.caveats[0].evidence["at"] == [{"indicator": "IND", "iso3": "KEN", "period": "2015"}]
    assert report.footnotes == ["Series break in 2015: Employment (Kenya)."]


def test_many_series_breaks_are_summarised():
    recs = []
    for i, y in enumerate(range(2010, 2016)):
        recs += rows([y], indicator=f"IND{i}", label=f"Indicator {i}", status="break")
    report = only("series_break", result(recs))
    assert len(report.footnotes) == cc.MAX_BREAK_FOOTNOTES + 1
    assert report.footnotes[-1] == "Series breaks in 2 further indicator-periods."


def test_no_break_no_caveat():
    assert only("series_break", result(rows(range(2013, 2018)))).caveats == []


# ---------------------------------------------------------------------------
# covid_endpoints
# ---------------------------------------------------------------------------
def test_covid_window_from_meta_warns():
    res = result(rows(["2011-2019", "2016-2020"]), meta={"periods": {"2011-2019": ["2011", "2019"], "2016-2020": ["2016", "2020"]}})
    report = only("covid_endpoints", res)
    assert report.status == "WARN"
    assert [w["label"] for w in report.caveats[0].evidence["windows"]] == ["2016-2020"]
    assert report.footnotes == ["Averages for 2016-2020 start or end in 2020-2021 (COVID-19 years)."]


def test_window_starting_in_2021_from_period_labels_warns():
    report = only("covid_endpoints", result(rows(["2011-2019", "2021-2023"])))
    assert report.status == "WARN"
    assert report.caveats[0].evidence["windows"][0]["label"] == "2021-2023"


def test_pre_covid_and_spanning_windows_pass():
    res = result(rows(["2011-2019", "2015-2024"]), meta={"periods": {"2011-2019": ("2011", "2019")}})
    assert only("covid_endpoints", res).caveats == []


def test_plotted_period_list_is_not_a_window():
    """charts store meta['periods'] as the plotted periods; ['2020', '2021'] is not a window."""
    res = result(rows([2020, 2021]), meta={"periods": ["2020", "2021"]})
    assert only("covid_endpoints", res).caveats == []


def test_covid_context_periods_and_custom_years():
    res = result(rows([2019]))
    assert only("covid_endpoints", res, {"periods": {"FY2015/16-FY2019/20": ("FY2015/16", "FY2019/20")}}).status == "WARN"
    assert only("covid_endpoints", res, {"periods": {"2015-2019": ("2015", "2019")}, "covid_years": [2019]}).status == "WARN"


def test_covid_window_from_period_average_provenance():
    harmonize = pytest.importorskip("cgjrkit.methods.harmonize")
    base = result(rows(range(2010, 2024), value=5.0, unit="%"))
    avg = harmonize.period_average(base, {"2011-19": ("2011", "2019"), "2020-23": ("2020", "2023")})
    report = only("covid_endpoints", avg)
    assert report.status == "WARN"
    labels = {w["label"] for w in report.caveats[0].evidence["windows"]}
    assert labels == {"2020-23"}


# ---------------------------------------------------------------------------
# peers_not_frozen
# ---------------------------------------------------------------------------
UNFROZEN = {"STRUCT": {"name": "Structural peers", "code": "STRUCT", "members": ["GHA", "TZA", "UGA"], "frozen_at": None}}
FROZEN = {"STRUCT": {**UNFROZEN["STRUCT"], "frozen_at": "2026-03-01"}}


def test_unfrozen_peers_block_in_replication_mode():
    res = result(rows([2019]), meta={"peer_sets": UNFROZEN})
    report = only("peers_not_frozen", res, {"mode": "replication"})
    assert report.status == "BLOCK" and codes(report) == ["peers_not_frozen"]
    assert report.caveats[0].evidence["peer_sets"] == ["STRUCT"]
    assert only("peers_not_frozen", res, {"replication": True}).status == "BLOCK"


def test_unfrozen_peers_warn_outside_replication():
    res = result(rows([2019]), meta={"peer_sets": UNFROZEN})
    assert only("peers_not_frozen", res).status == "WARN"


def test_frozen_peers_pass_and_late_freeze_is_flagged():
    res = result(rows([2019]), meta={"peer_sets": FROZEN})
    assert only("peers_not_frozen", res, {"mode": "replication"}).caveats == []
    late = only("peers_not_frozen", res, {"mode": "replication", "results_at": "2026-02-01"})
    assert late.status == "BLOCK" and codes(late) == ["peers_frozen_late"]


def test_peer_set_objects_in_context_and_inherited_caveat():
    peers = pytest.importorskip("cgjrkit.methods.peers")
    ps = peers.PeerSet(name="Aspirational", members=["VNM", "MAR"])
    res = result(rows([2019]))
    assert only("peers_not_frozen", res, {"mode": "replication", "peer_sets": [ps]}).status == "BLOCK"
    frozen = ps.freeze("Task team leader", "2026-03-01")
    assert only("peers_not_frozen", res, {"mode": "replication", "peer_set": frozen}).status == "PASS"
    cav = Caveat("peer_set_not_frozen", "warn", "inherited", {"peer_set": "ASPIRATIONAL"})
    inherited = result(rows([2019]), caveats=[cav])
    assert only("peers_not_frozen", inherited, {"mode": "replication"}).status == "BLOCK"


def test_peer_aggregate_output_unfrozen_blocks_in_replication():
    peers = pytest.importorskip("cgjrkit.methods.peers")
    recs = []
    for iso in ("GHA", "TZA", "UGA"):
        recs += rows([2019, 2020], iso3=iso, country=iso, value=10.0)
    agg = peers.peer_aggregate(result(recs), peers.PeerSet(name="Structural", members=["GHA", "TZA", "UGA"]),
                               stats=("median",), min_n=3)
    report = run_caveat_check(agg, {"mode": "replication"})
    assert report.status == "BLOCK"
    assert "peers_not_frozen" in codes(report, "block")


# ---------------------------------------------------------------------------
# small_n
# ---------------------------------------------------------------------------
def test_small_n_from_data_column_warns():
    recs = rows([2019, 2020], iso3="STRUCT:median", country="Structural (median)", group="STRUCT", n=2)
    res = result(recs, [prov(), Provenance(source="cgjrkit", tool="peers.peer_aggregate", params={"min_n": 3})])
    report = only("small_n", res)
    assert report.status == "WARN"
    assert report.caveats[0].evidence["n_aggregates"] == 2
    assert report.footnotes == ["Some peer aggregates are based on fewer than 3 countries with data (lowest n = 2)."]


def test_small_n_respects_context_and_default():
    recs = rows([2019], iso3="STRUCT:median", group="STRUCT", n=4)
    res = result(recs)
    assert only("small_n", res).caveats == []           # default min_n = 3
    assert only("small_n", res, {"min_n": 5}).status == "WARN"


def test_small_n_from_table_cells():
    res = result(rows([2019]), meta={"cells": [{"row": "Agriculture", "column": "peer_2011-19", "period": "2011-19",
                                                "value": 1.0, "n": 1}]})
    report = only("small_n", res)
    assert report.status == "WARN" and report.caveats[0].evidence["aggregates"][0]["n"] == 1


def test_peer_aggregate_with_too_few_members_warns():
    peers = pytest.importorskip("cgjrkit.methods.peers")
    recs = rows([2019], iso3="GHA", value=10.0) + rows([2019], iso3="TZA", value=20.0)
    ps = peers.PeerSet(name="Structural", members=["GHA", "TZA", "UGA"], frozen_at="2026-03-01")
    agg = peers.peer_aggregate(result(recs), ps, stats=("median", "mean"), min_n=3)
    report = only("small_n", agg)
    assert report.status == "WARN"
    assert report.caveats[0].evidence["aggregates"] == [
        {"result": 0, "aggregate": "STRUCTURAL", "indicator": "IND", "period": "2019", "n": 2, "min_n": 3}]


# ---------------------------------------------------------------------------
# licence_restricted
# ---------------------------------------------------------------------------
def comtrade_result():
    return result(rows([2019], source="comtrade", dataset="HS"), [prov("comtrade", "HS", "", "United Nations Comtrade Database")])


def test_restricted_licence_warns_and_blocks_for_redistribution():
    pytest.importorskip("cgjrkit.registries")
    report = only("licence_restricted", comtrade_result())
    assert report.status == "WARN" and codes(report) == ["licence_restricted"]
    assert report.caveats[0].evidence["commercial_use"] == "restricted"
    blocked = only("licence_restricted", comtrade_result(), {"redistribution": True})
    assert blocked.status == "BLOCK"
    assert only("licence_restricted", comtrade_result(), {"purpose": "redistribution"}).status == "BLOCK"


def test_open_licence_is_info_or_nothing():
    pytest.importorskip("cgjrkit.registries")
    wb = result(rows([2019], source="worldbank", dataset="WDI"), [prov("worldbank", "WDI", "last updated 2026-07-01")])
    report = only("licence_restricted", wb, {"redistribution": True})
    assert report.status == "PASS" and codes(report) == ["licence_restricted"]
    assert report.caveats[0].severity == "info"
    assert only("licence_restricted", result(rows([2019]))).caveats == []   # PWT: CC BY 4.0, caveat none


def test_unknown_commercial_use_warns_even_for_redistribution():
    pytest.importorskip("cgjrkit.registries")
    imf = result(weo_rows([2020], 2030), [WEO_PROV])
    assert only("licence_restricted", imf, {"redistribution": True}).status == "WARN"


def test_excluded_source_blocks_only_for_redistribution():
    pytest.importorskip("cgjrkit.registries")
    fred = result(rows([2019], source="fred", dataset="FRED"), [prov("fred", "FRED", "")])
    assert only("licence_restricted", fred).status == "WARN"
    assert only("licence_restricted", fred, {"redistribution": True}).status == "BLOCK"


def test_unregistered_source_warns():
    pytest.importorskip("cgjrkit.registries")
    res = result(rows([2019], source="client_nso", dataset="LFS tables"), [prov("client_nso", "LFS tables", "2025")])
    report = only("licence_restricted", res)
    assert report.status == "WARN" and codes(report) == ["licence_unregistered"]


def test_registry_absent_is_tolerated(monkeypatch):
    def missing(name, *args, **kwargs):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(cc.importlib, "import_module", missing)
    report = only("licence_restricted", comtrade_result())
    assert report.status == "PASS" and codes(report) == ["licence_registry_unavailable"]
    nc = result(rows([2019], source="data360", dataset="WB_X"),
                [prov("data360", "WB_X", "", license="CC BY-NC 4.0")])
    report = only("licence_restricted", nc)
    assert report.status == "WARN" and "licence_noncommercial" in codes(report)
    assert only("licence_restricted", nc, {"redistribution": True}).status == "BLOCK"


# ---------------------------------------------------------------------------
# combined
# ---------------------------------------------------------------------------
def test_combined_report_orders_footnotes_by_rule():
    weo = result(weo_rows(range(2020, 2028), 2026), [WEO_PROV])
    emp = result(ilo_rows(range(2015, 2020)), [ILO_PROV])
    report = run_caveat_check([weo, emp])
    assert report.status == "WARN"
    assert report.footnotes[0].startswith("Values are") is False
    assert report.footnotes[0].startswith("Values for")      # ILO footnote (rule 2) before forecast footnote (rule 3)
    assert "ILO modelled estimates" in report.footnotes[0]
    assert report.footnotes[1].startswith("Values for 2026 onward are forecasts")
    by_rule = report.by_rule()
    assert {"ilo_modelled", "forecast_years", "licence_restricted"} <= set(by_rule)


# ---------------------------------------------------------------------------
# extra edge cases
# ---------------------------------------------------------------------------
def test_peer_merge_is_conservative_and_reads_provenance_params():
    # context says frozen, but the result was computed with an unfrozen set -> still unfrozen
    res = result(rows([2019]), meta={"peer_sets": UNFROZEN})
    assert only("peers_not_frozen", res, {"mode": "replication", "peer_sets": FROZEN}).status == "BLOCK"
    # standard tables record peer sets only in their provenance params
    table = result(rows([2019]), [prov(), Provenance(source="cgjrkit", tool="standard_tables.standardized_macro_table",
                                                     params={"peer_sets": list(UNFROZEN.values())})])
    assert only("peers_not_frozen", table, {"mode": "replication"}).status == "BLOCK"


def test_series_dimensions_keep_series_apart():
    recs = ilo_rows([2019, 2020], sex="SEX_M") + ilo_rows([2019, 2020], status="", sex="SEX_F")
    report = only("ilo_modelled", result(recs, [ILO_PROV]))
    assert "ilo_modelled_splice" not in codes(report)


def test_messages_use_singular_for_one():
    report = only("forecast_years", result(weo_rows([2025, 2026], 2026), [WEO_PROV]))
    assert report.caveats[0].message == "1 observation flagged as forecast (periods 2026)."
