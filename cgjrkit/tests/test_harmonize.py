"""Unit tests for cgjrkit.methods.harmonize (hand-computed expectations, synthetic values)."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import validate_frame
from cgjrkit.methods import harmonize as h
from cgjrkit.methods.harmonize import FrequencyMismatchError, HarmonizeError, PPPMixError


def frame(rows, **defaults) -> pd.DataFrame:
    """Synthetic canonical frame: rows are (iso3, period, value[, status]) tuples."""
    base = {"source": "synthetic", "dataset": "TEST", "indicator": "X", "indicator_label": "Synthetic X",
            "country": "", "unit": "constant 2015 US$", "obs_status": ""}
    base.update(defaults)
    records = []
    for r in rows:
        rec = dict(base, iso3=r[0], period=r[1], value=r[2])
        if len(r) > 3:
            rec["obs_status"] = r[3]
        records.append(rec)
    return pd.DataFrame(records)


def values(res: Result, iso3: str | None = None) -> dict[str, float]:
    df = res.data if iso3 is None else res.data[res.data["iso3"] == iso3]
    return dict(zip(df["period"], df["value"]))


def wrapped(df: pd.DataFrame, source: str = "synthetic") -> Result:
    return Result(data=df, provenance=[Provenance(source=source, dataset="TEST", endpoint="fixture")])


def codes(res: Result) -> set[str]:
    return {c.code for c in res.caveats}


# ---------------------------------------------------------------------------
# periods
# ---------------------------------------------------------------------------
def test_parse_period_forms():
    assert h.parse_period("2024") == h.Period("2024", "A", 2024)
    assert h.parse_period("2024Q3").ordinal == 2024 * 4 + 2
    assert h.parse_period("2024-Q3").freq == "Q"
    assert h.parse_period("2024-03").ordinal == 2024 * 12 + 2
    fy = h.parse_period("FY2024/25")
    assert (fy.freq, fy.year) == ("FY", 2024)
    assert h.parse_period("FY1999/00").year == 1999
    assert h.parse_period("FY2024/2025").year == 2024
    assert h.fiscal_label(2024) == "FY2024/25" and h.fiscal_label(1999) == "FY1999/00"
    assert h.format_period("Q", h.parse_period("2024Q1").ordinal) == "2024Q1"
    assert h.format_period("M", h.parse_period("2007-12").ordinal) == "2007-12"
    for bad in ("2024-13", "FY2024/26", "2011-2019", "24"):
        with pytest.raises(ValueError):
            h.parse_period(bad)


def test_combine_status_priority():
    assert h.combine_status(["", "estimate", "forecast"]) == "forecast"
    assert h.combine_status(["forecast", "break"]) == "break"
    assert h.combine_status(["", float("nan")]) == ""


def test_cagr_value():
    assert h.cagr_value(100, 121, 2) == pytest.approx(10.0)
    assert math.isnan(h.cagr_value(0, 121, 2))
    assert math.isnan(h.cagr_value(100, 121, 0))


# ---------------------------------------------------------------------------
# rebase_index
# ---------------------------------------------------------------------------
def test_rebase_index_single_period_and_provenance():
    df = frame([("KEN", "2010", 50.0), ("KEN", "2011", 40.0), ("KEN", "2012", 60.0),
                ("UGA", "2010", 10.0), ("UGA", "2011", 20.0), ("UGA", "2012", 30.0)])
    res = h.rebase_index(wrapped(df), "2011")
    assert values(res, "KEN") == pytest.approx({"2010": 125.0, "2011": 100.0, "2012": 150.0})
    assert values(res, "UGA") == pytest.approx({"2010": 50.0, "2011": 100.0, "2012": 150.0})
    assert set(res.data["unit"]) == {"index (2011=100)"}
    assert set(res.data["indicator"]) == {"X|idx2011"}
    assert res.data["indicator_label"].iloc[0] == "Synthetic X (index, 2011=100)"
    validate_frame(res.data)
    # provenance: input carried forward, cgjrkit entry appended with params
    assert res.provenance[0].source == "synthetic"
    last = res.provenance[-1]
    assert (last.source, last.tool) == ("cgjrkit", "harmonize.rebase_index")
    assert last.params["base_period"] == "2011" and last.params["by"] == ["indicator", "iso3"]


def test_rebase_index_window_and_keep_code():
    df = frame([("KEN", "2010", 50.0), ("KEN", "2011", 40.0), ("KEN", "2012", 60.0)])
    res = h.rebase_index(df, ("2010", "2011"), suffix="")
    # base = mean(50, 40) = 45
    assert values(res) == pytest.approx({"2010": 100 * 50 / 45, "2011": 100 * 40 / 45, "2012": 100 * 60 / 45})
    assert set(res.data["indicator"]) == {"X"}
    assert set(res.data["unit"]) == {"index (2010-2011=100)"}


def test_rebase_index_monthly_base():
    df = frame([("KEN", "2007-11", 99.0), ("KEN", "2007-12", 110.0), ("KEN", "2008-01", 121.0)], unit="index")
    res = h.rebase_index(df, "2007-12")
    assert values(res) == pytest.approx({"2007-11": 90.0, "2007-12": 100.0, "2008-01": 110.0})


def test_rebase_index_missing_base_drops_group_with_caveat():
    df = frame([("KEN", "2010", 50.0), ("KEN", "2011", 40.0), ("UGA", "2012", 30.0)])
    res = h.rebase_index(df, "2011")
    assert set(res.data["iso3"]) == {"KEN"}
    cav = next(c for c in res.caveats if c.code == "rebase_base_missing")
    assert cav.severity == "warn" and cav.evidence["groups"][0]["group"] == "X/UGA"


def test_rebase_index_frequency_mismatch():
    df = frame([("KEN", "2010Q1", 1.0), ("KEN", "2010Q2", 2.0)])
    with pytest.raises(FrequencyMismatchError):
        h.rebase_index(df, "2010")


def test_rebase_index_ppp_mix_within_series():
    df = pd.concat([
        frame([("KEN", "2016", 4.0)], unit="GDP per capita, PPP (constant 2017 international $)"),
        frame([("KEN", "2017", 5.0)], unit="GDP per capita, PPP (constant 2021 international $)"),
    ])
    with pytest.raises(PPPMixError):
        h.rebase_index(df, "2016")
    res = h.rebase_index(df, "2016", on_ppp_mix="block")
    assert res.blocked and "ppp_base_mix" in codes(res)


def test_duplicate_observations_rejected():
    df = frame([("KEN", "2010", 1.0), ("KEN", "2010", 2.0)])
    with pytest.raises(ValueError):
        h.rebase_index(df, "2010")


# ---------------------------------------------------------------------------
# growth_rate
# ---------------------------------------------------------------------------
def test_growth_rate_pct_and_log():
    df = frame([("KEN", "2010", 100.0), ("KEN", "2011", 110.0), ("KEN", "2012", 99.0)])
    pct = h.growth_rate(df)
    assert values(pct) == pytest.approx({"2011": 10.0, "2012": -10.0})
    assert set(pct.data["unit"]) == {"% change"} and set(pct.data["indicator"]) == {"X|gr_pct"}
    log = h.growth_rate(df, kind="log")
    assert values(log) == pytest.approx({"2011": 100 * math.log(1.1), "2012": 100 * math.log(0.9)})
    assert set(log.data["indicator"]) == {"X|gr_log"}


def test_growth_rate_gap_is_not_bridged():
    df = frame([("KEN", "2010", 100.0), ("KEN", "2011", 110.0), ("KEN", "2013", 121.0)])
    res = h.growth_rate(df)
    assert values(res) == pytest.approx({"2011": 10.0})
    assert "growth_gap" in codes(res)


def test_growth_rate_quarterly_year_on_year():
    rows = [("KEN", f"2023Q{q}", 100.0 + q) for q in range(1, 5)] + [("KEN", "2024Q1", 202.0)]
    res = h.growth_rate(frame(rows), lag=4)
    # 2024Q1 vs 2023Q1: 202 / 101 - 1 = 100 %
    assert values(res) == pytest.approx({"2024Q1": 100.0})
    assert set(res.data["indicator"]) == {"X|gr_pct_lag4"}


def test_growth_rate_status_flags():
    df = frame([("KEN", "2010", 100.0), ("KEN", "2011", 110.0, "forecast"), ("KEN", "2012", 121.0, "break"),
                ("KEN", "2013", 133.1)])
    res = h.growth_rate(df)
    status = dict(zip(res.data["period"], res.data["obs_status"]))
    assert status == {"2011": "forecast", "2012": "break", "2013": ""}
    assert "growth_across_break" in codes(res)


# ---------------------------------------------------------------------------
# period_average / cagr
# ---------------------------------------------------------------------------
def test_period_average_mean_windows():
    rows = [("KEN", str(y), float(y - 2010)) for y in range(2011, 2024)]  # 1..13
    res = h.period_average(frame(rows, unit="%"), {"2011-2019": ("2011", "2019"), "2021-2023": ("2021", "2023")})
    # mean(1..9) = 5; mean(11, 12, 13) = 12
    assert values(res) == pytest.approx({"2011-2019": 5.0, "2021-2023": 12.0})
    assert set(res.data["unit"]) == {"%"} and set(res.data["indicator"]) == {"X|avg"}
    assert res.provenance[-1].params["periods"] == {"2011-2019": ["2011", "2019"], "2021-2023": ["2021", "2023"]}


def test_period_average_incomplete_window():
    df = frame([("KEN", "2011", 2.0), ("KEN", "2013", 4.0)])
    res = h.period_average(df, {"w": ("2011", "2013")})
    assert values(res) == pytest.approx({"w": 3.0})
    cav = next(c for c in res.caveats if c.code == "period_average_incomplete")
    assert cav.evidence["windows"][0] == {"group": "X/KEN", "window": "w", "n": 2, "expected": 3}
    strict = h.period_average(df, {"w": ("2011", "2013")}, require_complete=True)
    assert strict.data.empty


def test_period_average_cagr_and_forecast_flag():
    df = frame([("KEN", "2010", 100.0), ("KEN", "2011", 150.0), ("KEN", "2012", 121.0, "forecast")])
    res = h.period_average(df, {"2010-12": ("2010", "2012")}, method="cagr")
    assert values(res) == pytest.approx({"2010-12": 10.0})  # (121/100)^(1/2) - 1
    assert set(res.data["unit"]) == {"% per year"}
    assert res.data["obs_status"].iloc[0] == "forecast"
    assert "period_includes_forecast" in codes(res)


def test_cagr_quarterly_years():
    df = frame([("KEN", "2010Q4", 100.0), ("KEN", "2012Q4", 121.0)])
    res = h.cagr(df, "2010Q4", "2012Q4")
    assert values(res) == pytest.approx({"2010Q4-2012Q4": 10.0})
    assert res.provenance[-1].tool == "harmonize.cagr"


# ---------------------------------------------------------------------------
# pct_of_frontier
# ---------------------------------------------------------------------------
def test_pct_of_frontier_usa_and_missing_frontier():
    unit = "GDP per capita (constant 2015 US$)"
    df = frame([("USA", "2020", 50000.0), ("KEN", "2020", 5000.0), ("IDN", "2020", 12500.0),
                ("KEN", "2021", 6000.0)], unit=unit)
    res = h.pct_of_frontier(df)
    assert values(res, "KEN") == pytest.approx({"2020": 10.0})
    assert values(res, "IDN") == pytest.approx({"2020": 25.0})
    assert values(res, "USA") == pytest.approx({"2020": 100.0})
    assert set(res.data["unit"]) == {"% of USA"}
    assert "frontier_missing" in codes(res)
    no_frontier = h.pct_of_frontier(df, keep_frontier=False)
    assert "USA" not in set(no_frontier.data["iso3"])


def test_pct_of_frontier_max():
    df = frame([("AAA", "2020", 10.0), ("BBB", "2020", 40.0)])
    res = h.pct_of_frontier(df, "max")
    assert values(res, "AAA") == pytest.approx({"2020": 25.0})


def test_pct_of_frontier_refuses_mixed_ppp():
    df = pd.concat([
        frame([("USA", "2020", 60000.0)], unit="PPP 2021"),
        frame([("KEN", "2020", 5000.0)], unit="PPP 2017"),
    ])
    with pytest.raises(PPPMixError):
        h.pct_of_frontier(df)


# ---------------------------------------------------------------------------
# deflate
# ---------------------------------------------------------------------------
def test_deflate_with_base_period():
    nominal = frame([("KEN", "2020", 110.0), ("KEN", "2021", 132.0)], unit="current LCU")
    deflator = frame([("KEN", "2020", 100.0), ("KEN", "2021", 120.0)], indicator="DEFL", unit="index")
    res = h.deflate(wrapped(nominal), wrapped(deflator, "synthetic2"), base_period="2021")
    # factors: 100/120 and 1 -> real = 132, 132 (constant 2021 prices)
    assert values(res) == pytest.approx({"2020": 132.0, "2021": 132.0})
    assert set(res.data["unit"]) == {"constant 2021 LCU"}
    assert set(res.data["indicator"]) == {"X|real2021"}
    assert {p.source for p in res.provenance} == {"synthetic", "synthetic2", "cgjrkit"}


def test_deflate_implicit_base_and_common_deflator():
    nominal = frame([("KEN", "2020", 110.0), ("UGA", "2020", 55.0)], unit="current US$")
    deflator = frame([("USA", "2020", 110.0)], indicator="CPI", unit="index")
    res = h.deflate(nominal, deflator, match_on=("period",))
    assert values(res, "KEN") == pytest.approx({"2020": 100.0})
    assert values(res, "UGA") == pytest.approx({"2020": 50.0})
    assert "deflator_base_implicit" in codes(res)


def test_deflate_refuses_frequency_mismatch():
    nominal = frame([("KEN", "2020", 110.0)])
    deflator = frame([("KEN", "2020Q1", 100.0)], indicator="DEFL")
    with pytest.raises(FrequencyMismatchError):
        h.deflate(nominal, deflator, base_period="2020")


# ---------------------------------------------------------------------------
# ratio
# ---------------------------------------------------------------------------
def test_ratio_to_gdp_aligns_periods():
    rev = frame([("KEN", "2020", 20.0), ("KEN", "2021", 30.0), ("KEN", "2022", 5.0)],
                indicator="REV", indicator_label="Revenue", unit="current LCU")
    gdp = frame([("KEN", "2020", 100.0), ("KEN", "2021", 120.0)], indicator="GDP", indicator_label="GDP", unit="current LCU")
    res = h.ratio(wrapped(rev), wrapped(gdp))
    assert values(res) == pytest.approx({"2020": 20.0, "2021": 25.0})
    assert set(res.data["unit"]) == {"% of GDP"}
    assert set(res.data["indicator"]) == {"REV/GDP"}
    assert res.data["indicator_label"].iloc[0] == "Revenue (% of GDP)"
    assert "ratio_unmatched" in codes(res)
    validate_frame(res.data)


def test_ratio_several_numerators_and_mixed_sources():
    num = pd.concat([frame([("KEN", "2020", 10.0)], indicator="VAT"), frame([("KEN", "2020", 5.0)], indicator="PIT")])
    den = frame([("KEN", "2020", 200.0)], indicator="GDP", source="other", indicator_label="")
    res = h.ratio(num, den, scale=1)
    assert dict(zip(res.data["indicator"], res.data["value"])) == pytest.approx({"PIT/GDP": 0.025, "VAT/GDP": 0.05})
    assert set(res.data["source"]) == {"cgjrkit"} and set(res.data["dataset"]) == {"derived"}
    assert set(res.data["unit"]) == {"ratio to GDP"}


def test_ratio_refuses_mismatched_frequency():
    rev = frame([("KEN", "FY2020/21", 20.0)], indicator="REV")
    gdp = frame([("KEN", "2020", 100.0)], indicator="GDP")
    with pytest.raises(FrequencyMismatchError, match="calendar_to_fiscal"):
        h.ratio(rev, gdp)


def test_ratio_ppp_mix_raise_or_block():
    num = frame([("KEN", "2020", 10.0)], indicator="A", unit="constant 2017 international $ (PPP 2017)")
    den = frame([("KEN", "2020", 20.0)], indicator="B", unit="constant 2021 international $ (PPP 2021)")
    with pytest.raises(PPPMixError):
        h.ratio(num, den)
    res = h.ratio(num, den, on_ppp_mix="block")
    assert res.blocked
    cav = next(c for c in res.caveats if c.code == "ppp_base_mix")
    assert cav.evidence["groups"][0]["ppp_bases"] == ["2017", "2021"]


def test_ratio_price_basis_warning():
    num = frame([("KEN", "2020", 10.0)], indicator="A", unit="current LCU")
    den = frame([("KEN", "2020", 20.0)], indicator="B", unit="constant 2016 LCU")
    res = h.ratio(num, den)
    assert "ratio_price_basis_mismatch" in codes(res)


def test_ratio_denominator_must_be_single_series():
    num = frame([("KEN", "2020", 10.0)], indicator="A")
    den = pd.concat([frame([("KEN", "2020", 20.0)], indicator="B"), frame([("KEN", "2020", 30.0)], indicator="C")])
    with pytest.raises(HarmonizeError):
        h.ratio(num, den)


# ---------------------------------------------------------------------------
# fiscal <-> calendar
# ---------------------------------------------------------------------------
def test_fiscal_to_calendar_kenya_weights_from_registry():
    df = frame([("KEN", "FY2022/23", 100.0), ("KEN", "FY2023/24", 120.0), ("KEN", "FY2024/25", 150.0)])
    res = h.fiscal_to_calendar(df)  # KEN fiscal year starts in July -> 0.5 / 0.5
    assert values(res) == pytest.approx({"2023": 110.0, "2024": 135.0})
    assert set(res.data["indicator"]) == {"X|cy"}
    assert res.provenance[-1].params["fy_start_month"] == {"KEN": 7}
    assert "fiscal_conversion_edges" in codes(res)
    assert "(m-1)/12" in res.provenance[-1].notes[0]


def test_fiscal_to_calendar_april_start():
    df = frame([("AAA", "FY2022/23", 100.0), ("AAA", "FY2023/24", 120.0)])
    res = h.fiscal_to_calendar(df, fy_start_month=4, kind="stock")
    # CY2023 = 3/12 * FY2022/23 + 9/12 * FY2023/24 = 25 + 90
    assert values(res) == pytest.approx({"2023": 115.0})


def test_calendar_to_fiscal():
    df = frame([("KEN", "2023", 100.0), ("KEN", "2024", 120.0)])
    assert values(h.calendar_to_fiscal(df, 7)) == pytest.approx({"FY2023/24": 110.0})
    # April start: FY2023/24 = 9/12 * CY2023 + 3/12 * CY2024 = 75 + 30
    assert values(h.calendar_to_fiscal(df, {"KEN": 4})) == pytest.approx({"FY2023/24": 105.0})


def test_fiscal_conversion_errors():
    with pytest.raises(FrequencyMismatchError):
        h.fiscal_to_calendar(frame([("KEN", "2023", 1.0)]))
    with pytest.raises(HarmonizeError, match="calendar year"):
        h.calendar_to_fiscal(frame([("MKD", "2023", 1.0), ("MKD", "2024", 2.0)]))  # registry: January start


def test_quarterly_to_fiscal_exact():
    rows = [("KEN", q, v) for q, v in zip(["2023Q3", "2023Q4", "2024Q1", "2024Q2", "2024Q3"], [1.0, 2.0, 3.0, 4.0, 5.0])]
    df = frame(rows)
    assert values(h.quarterly_to_fiscal(df, 7)) == pytest.approx({"FY2023/24": 10.0})
    assert values(h.quarterly_to_fiscal(df, 7, how="mean")) == pytest.approx({"FY2023/24": 2.5})
    res = h.quarterly_to_fiscal(df, 7, how="last")
    assert values(res) == pytest.approx({"FY2023/24": 4.0})
    assert "fiscal_year_incomplete" in codes(res)  # FY2024/25 has one quarter


# ---------------------------------------------------------------------------
# splice
# ---------------------------------------------------------------------------
def test_splice_ratio_marks_break():
    old = frame([("KEN", "2010", 50.0), ("KEN", "2011", 60.0), ("KEN", "2012", 70.0)], indicator="OLD", unit="old base")
    new = frame([("KEN", "2012", 140.0), ("KEN", "2013", 160.0)], indicator="NEW", unit="new base")
    res = h.splice(wrapped(old), wrapped(new, "synthetic2"), "2012")
    assert values(res) == pytest.approx({"2010": 100.0, "2011": 120.0, "2012": 140.0, "2013": 160.0})
    status = dict(zip(res.data["period"], res.data["obs_status"]))
    assert status == {"2010": "", "2011": "", "2012": "break", "2013": ""}
    assert set(res.data["indicator"]) == {"NEW"} and set(res.data["unit"]) == {"new base"}
    cav = next(c for c in res.caveats if c.code == "series_spliced")
    assert cav.severity == "warn" and cav.evidence["splices"][0]["factor"] == pytest.approx(2.0)
    validate_frame(res.data)


def test_splice_level_and_window():
    old = frame([("KEN", "2010", 5.0), ("KEN", "2011", 6.0), ("KEN", "2012", 7.0)], indicator="OLD")
    new = frame([("KEN", "2012", 8.0), ("KEN", "2013", 9.0)], indicator="NEW")
    assert values(h.splice(old, new, "2012", method="level")) == pytest.approx(
        {"2010": 6.0, "2011": 7.0, "2012": 8.0, "2013": 9.0})
    old2 = frame([("KEN", "2010", 50.0), ("KEN", "2011", 60.0), ("KEN", "2012", 70.0)], indicator="OLD")
    new2 = frame([("KEN", "2011", 125.0), ("KEN", "2012", 135.0), ("KEN", "2013", 150.0)], indicator="NEW")
    res = h.splice(old2, new2, ("2011", "2012"))
    # factor = mean(125, 135) / mean(60, 70) = 2; new values used from 2011 (break there)
    assert values(res) == pytest.approx({"2010": 100.0, "2011": 125.0, "2012": 135.0, "2013": 150.0})
    assert res.data.loc[res.data["period"] == "2011", "obs_status"].item() == "break"


def test_splice_level_refuses_ppp_mix_and_skips_incomplete_overlap():
    old = frame([("KEN", "2016", 4.0), ("KEN", "2017", 5.0)], unit="PPP 2017")
    new = frame([("KEN", "2017", 6.0), ("KEN", "2018", 7.0)], unit="PPP 2021")
    with pytest.raises(PPPMixError):
        h.splice(old, new, "2017", method="level")
    res = h.splice(old, new, "2017", method="level", on_ppp_mix="block")
    assert res.blocked
    gap = h.splice(frame([("KEN", "2016", 4.0)]), frame([("KEN", "2018", 7.0)]), "2017")
    assert gap.data.empty and "splice_skipped" in codes(gap)


def test_splice_ratio_bridges_ppp_vintages_but_not_a_mix_within_one_input():
    # PWT 10.01-style old series (PPP 2017) ratio-spliced onto a PWT 11.0-style new series (PPP 2021)
    old = frame([("KEN", "2016", 4.0), ("KEN", "2017", 5.0)], unit="million 2017 US$ (PPP 2017)", indicator="OLD")
    new = frame([("KEN", "2017", 10.0), ("KEN", "2018", 12.0)], unit="million 2021 US$ (PPP 2021)", indicator="NEW")
    res = h.splice(old, new, "2017")
    assert not res.blocked and "ppp_base_mix" not in codes(res)
    assert values(res) == pytest.approx({"2016": 8.0, "2017": 10.0, "2018": 12.0})   # factor 10 / 5 = 2
    assert set(res.data["unit"]) == {"million 2021 US$ (PPP 2021)"}
    cav = next(c for c in res.caveats if c.code == "series_spliced")
    assert cav.evidence["splices"][0]["ppp_bases"] == {"old": ["2017"], "new": ["2021"]}
    assert cav.evidence["ppp_vintage_bridge"] == ["KEN: 2017 -> 2021"] and "bridges PPP vintages" in cav.message
    # an old series that itself mixes PPP bases is still refused
    mixed_old = pd.concat([frame([("KEN", "2015", 3.0)], unit="PPP 2011", indicator="OLD"), old])
    with pytest.raises(PPPMixError):
        h.splice(mixed_old, new, "2017")
    assert h.splice(mixed_old, new, "2017", on_ppp_mix="block").blocked


# ---------------------------------------------------------------------------
# cross-cutting
# ---------------------------------------------------------------------------
def test_inputs_are_not_mutated_and_results_deterministic():
    df = frame([("KEN", "2010", 50.0), ("KEN", "2011", 40.0)])
    before = df.copy()
    a = h.rebase_index(df, "2010")
    b = h.rebase_index(df, "2010")
    pd.testing.assert_frame_equal(df, before)
    assert a.data_sha256 == b.data_sha256
    assert a.provenance[-1] == b.provenance[-1]


def test_caveats_carry_forward():
    df = frame([("KEN", "2010", 50.0), ("KEN", "2011", 40.0)])
    first = h.rebase_index(df, "2010", on_ppp_mix="block")
    from cgjrkit.core.caveats import Caveat

    first.add_caveat(Caveat("upstream", "info", "from a connector"))
    second = h.growth_rate(first)
    assert "upstream" in codes(second)
    assert [p.tool for p in second.provenance] == ["harmonize.rebase_index", "harmonize.growth_rate"]


def test_check_ppp_screen():
    a = frame([("KEN", "2020", 1.0)], unit="PPP 2017")
    b = frame([("UGA", "2020", 1.0)], unit="constant 2021 international $ (PPP 2021)")
    cav = h.check_ppp(a, b)
    assert cav and cav[0].severity == "block"
    assert h.check_ppp(a) == []


# ---------------------------------------------------------------------------
# review fixes: negative bases, frontier aggregates, meta carry-forward
# ---------------------------------------------------------------------------
def test_growth_rate_pct_drops_nonpositive_base_and_keeps_positive_ones():
    df = frame([("KEN", "2019", 0.0), ("KEN", "2020", -10.0), ("KEN", "2021", -5.0), ("KEN", "2022", 5.0),
                ("KEN", "2023", 10.0)], indicator="BAL", unit="% of GDP")
    res = h.growth_rate(df)
    # only 2023 has a positive base: 100 * (10 / 5 - 1) = 100; bases 0 (2020), -10 (2021), -5 (2022) are dropped
    assert values(res) == pytest.approx({"2023": 100.0})
    cav = next(c for c in res.caveats if c.code == "growth_nonpositive_base")
    assert cav.severity == "warn" and cav.evidence["n"] == 3
    assert cav.evidence["at"] == ["BAL/KEN@2020", "BAL/KEN@2021", "BAL/KEN@2022"]
    # positive series are unaffected and carry no such caveat
    assert "growth_nonpositive_base" not in codes(h.growth_rate(frame([("KEN", "2020", 1.0), ("KEN", "2021", 2.0)])))


def test_pct_of_frontier_max_skips_aggregates_unless_asked():
    df = frame([("AAA", "2020", 50.0), ("HIC", "2020", 100.0), ("BBB", "2020", 80.0), ("XAG", "2020", 90.0),
                ("STRUCT:median", "2020", 95.0)])
    res = h.pct_of_frontier(Result(data=df, meta={"aggregates": ["XAG"]}), "max")
    v = values_by_iso(res)
    # BBB (80) is the best country: AAA 62.5, BBB 100; aggregate rows stay, relative to BBB
    assert v["AAA"] == pytest.approx(62.5) and v["BBB"] == pytest.approx(100.0)
    assert v["HIC"] == pytest.approx(125.0) and v["XAG"] == pytest.approx(112.5)
    cav = next(c for c in res.caveats if c.code == "frontier_excludes_aggregates")
    assert cav.severity == "info" and cav.evidence["aggregates"] == ["HIC", "STRUCT:median", "XAG"]
    plain = h.pct_of_frontier(df, "max", max_over_aggregates=True)
    assert values_by_iso(plain)["BBB"] == pytest.approx(80.0)       # HIC (100) is the frontier
    assert "frontier_excludes_aggregates" not in codes(plain)
    # an explicit frontier code is used as given, even an aggregate
    assert values_by_iso(h.pct_of_frontier(df, "HIC"))["BBB"] == pytest.approx(80.0)


def values_by_iso(res: Result) -> dict[str, float]:
    return dict(zip(res.data["iso3"], res.data["value"]))


def test_two_input_methods_merge_aggregate_codes_lists_and_dicts():
    num = frame([("KEN", "2020", 1.0)], indicator="N")
    den = frame([("KEN", "2020", 2.0)], indicator="D")
    out = h.ratio(Result(data=num, meta={"aggregates": ["XBG", "XAG"], "vintage": "a"}),
                  Result(data=den, meta={"aggregates": ["XAG", "XCG"], "vintage": "b"}))
    assert out.meta["aggregates"] == ["XAG", "XBG", "XCG"] and out.meta["vintage"] == "b"
    both_dicts = h.ratio(Result(data=num, meta={"aggregates": {"XBG": "Group B"}}),
                         Result(data=den, meta={"aggregates": {"XAG": "Group A"}}))
    assert both_dicts.meta["aggregates"] == {"XAG": "Group A", "XBG": "Group B"}
    mixed = h.deflate(Result(data=frame([("KEN", "2020", 110.0)], unit="current LCU"), meta={"aggregates": {"XAG": "A"}}),
                      Result(data=frame([("KEN", "2020", 100.0)], indicator="DEFL", unit="index"),
                             meta={"aggregates": ["XBG"]}), base_period="2020")
    assert mixed.meta["aggregates"] == ["XAG", "XBG"]
