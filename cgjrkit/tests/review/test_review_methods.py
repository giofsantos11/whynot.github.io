"""Adversarial review probes for ``cgjrkit.methods`` (math, guards, edge cases).

Each test documents a defect found in review. They were first marked
``xfail(strict=True)``; every defect has since been fixed and the markers removed, so
these are now regression tests. Expected values are hand calculations written next to
the assertion. All frames are synthetic (round numbers, fake ISO3 codes AAA/BBB/...).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from cgjrkit.core.provenance import Result
from cgjrkit.core.schema import make_frame
from cgjrkit.methods import benchmark as bm
from cgjrkit.methods import harmonize as hz
from cgjrkit.methods import structural_change as sc
from cgjrkit.methods.growth_accounting import growth_accounting, perpetual_inventory
from cgjrkit.methods.harmonize import FrequencyMismatchError, PPPMixError
from cgjrkit.methods.peers import PeerSet, peer_aggregate
from cgjrkit.methods.standard_tables import MacroRow, standardized_macro_table

PPP17 = "million 2017 US$ at constant 2017 national prices (PPP 2017)"  # PWT 10.01 unit label
PPP21 = "million 2021 US$ at constant 2021 national prices (PPP 2021)"  # PWT 11.0 unit label


def _ppp_guarded(call) -> bool:
    """True when ``call()`` refuses a PPP mix (PPPMixError) or returns a blocked / ppp_base_mix Result."""
    try:
        res = call()
    except PPPMixError:
        return True
    return res.blocked or any(c.code == "ppp_base_mix" for c in res.caveats)


# ---------------------------------------------------------------------------------------------
# 1. growth_accounting / perpetual_inventory: no PPP guard inside a role
# ---------------------------------------------------------------------------------------------
def _ga_panel_two_pwt_vintages() -> Result:
    """Same country, rgdpna/rkna from PWT 10.01 (PPP 2017) up to 2005 and PWT 11.0 (PPP 2021) after.

    Real output and capital are flat in each vintage; only the unit (and level) changes in 2006.
    """
    rows = []
    for y in range(2000, 2011):
        old = y <= 2005
        unit, ds = (PPP17, "PWT10.01") if old else (PPP21, "PWT11.0")
        rows += [
            dict(source="pwt", dataset=ds, indicator="rgdpna", iso3="AAA", period=str(y), value=100.0 if old else 150.0, unit=unit),
            dict(source="pwt", dataset=ds, indicator="rkna", iso3="AAA", period=str(y), value=100.0, unit=unit),
            dict(source="pwt", dataset=ds, indicator="emp", iso3="AAA", period=str(y), value=10.0, unit="millions"),
        ]
    return Result(make_frame(rows))


def test_growth_accounting_guards_ppp_mix_within_a_role():
    panel = _ga_panel_two_pwt_vintages()
    assert _ppp_guarded(lambda: growth_accounting(panel, {"w": ("2000", "2010")}, alpha=0.3,
                                                  human_capital=None, sensitivity=None))


def test_perpetual_inventory_guards_ppp_mix():
    inv = make_frame([dict(source="pwt", indicator="I", iso3="AAA", period=str(y), value=10.0,
                           unit=PPP17 if y <= 2005 else PPP21) for y in range(2000, 2011)])
    assert _ppp_guarded(lambda: perpetual_inventory(inv, 0.05, initial=100.0))


# ---------------------------------------------------------------------------------------------
# 2. benchmark: PPP guard only in income_conditional_fit
# ---------------------------------------------------------------------------------------------
def _gdppc_mixed_ppp():
    rows = []
    for i, iso in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE"]):
        unit = ("constant 2017 international $ (PPP 2017)" if i < 2 else "constant 2021 international $ (PPP 2021)")
        rows.append(dict(source="worldbank", dataset="WDI", indicator="NY.GDP.PCAP.PP.KD", iso3=iso, period="2020",
                         value=1000.0 * (i + 1), unit=unit))
    return make_frame(rows)


@pytest.mark.parametrize("fn, kwargs", [
    (bm.percentile_rank, {"iso3": "AAA", "period": "2020"}),
    (bm.zscore, {"period": "2020"}),
    (bm.kernel_density, {"period": "2020"}),
    (bm.distance_to_frontier, {"period": "2020"}),
], ids=["percentile_rank", "zscore", "kernel_density", "distance_to_frontier"])
def test_benchmark_cross_country_stats_guard_ppp_mix(fn, kwargs):
    frame = _gdppc_mixed_ppp()
    assert _ppp_guarded(lambda: fn(frame, **kwargs))


# ---------------------------------------------------------------------------------------------
# 3. sector_contributions silently aligns fiscal years with calendar years across frames
# ---------------------------------------------------------------------------------------------
def test_sector_contributions_refuses_fiscal_total_with_calendar_sectors():
    rows = []
    for y in range(2010, 2014):
        rows.append(dict(source="s", indicator="AGR", iso3="AAA", period=str(y), value=100.0 * 1.1 ** (y - 2010)))
        rows.append(dict(source="s", indicator="IND", iso3="AAA", period=str(y), value=100.0))
    va = make_frame(rows)
    total_fy = make_frame([dict(source="t", indicator="GDP_FY", iso3="AAA", period=hz.fiscal_label(y),
                                value=300.0 * 1.2 ** (y - 2010)) for y in range(2010, 2014)])
    with pytest.raises(FrequencyMismatchError):
        sc.sector_contributions(va, {"w": ("2011", "2013")}, total=total_fy, method="exact")


# ---------------------------------------------------------------------------------------------
# 4. standardized_macro_table: CAGR rows and mean rows of one column cover different years
# ---------------------------------------------------------------------------------------------
def test_macro_table_cagr_row_covers_the_same_growth_years_as_mean_row():
    # levels: 1999-2001 = 100, 2002-2003 = 110 -> growth 0, 0, 0, 10, 0 (%), years 1999..2003
    levels = {1999: 100.0, 2000: 100.0, 2001: 100.0, 2002: 110.0, 2003: 110.0}
    growth = {y: 100.0 * (levels[y] / levels[y - 1] - 1.0) for y in range(2000, 2004)}
    lv = make_frame([dict(source="s", dataset="D", indicator="LVL", iso3="AAA", period=str(y), value=v)
                     for y, v in levels.items()])
    gr = make_frame([dict(source="s", dataset="D", indicator="GR", iso3="AAA", period=str(y), value=v)
                     for y, v in growth.items()])
    peers = PeerSet(name="P", members=["BBB", "CCC", "DDD"], approver="review", frozen_at="2026-01-01")
    t = standardized_macro_table({"g_mean": MacroRow("g_mean", gr, agg="mean"),
                                  "g_cagr": MacroRow("g_cagr", lv, agg="cagr")},
                                 "AAA", peers, {"2002-2003": ("2002", "2003")})
    cells = dict(zip(t.data["row"], t.data["AAA|2002-2003"]))
    assert cells["g_mean"] == pytest.approx(5.0)                       # (10 + 0) / 2
    # same two growth years compounded: 100 * ((110/100) ** (1/2) - 1) = 4.881
    assert cells["g_cagr"] == pytest.approx(100.0 * (math.sqrt(1.1) - 1.0))  # code returns 0.0 (only 2003)


def test_macro_table_reports_an_uncomputable_focus_cell():
    fy = make_frame([dict(source="s", indicator="REV", iso3="AAA", period=hz.fiscal_label(y), value=10.0)
                     for y in range(2010, 2015)])
    peers = PeerSet(name="P", members=["BBB", "CCC", "DDD"], approver="review", frozen_at="2026-01-01")
    try:
        t = standardized_macro_table({"Revenue": fy}, "AAA", peers, {"2010-2014": ("2010", "2014")})
    except FrequencyMismatchError:
        return
    assert np.isnan(t.data.loc[0, "AAA|2010-2014"])
    assert {c.code for c in t.caveats} - {"peer_no_data", "peer_set_not_frozen"}


# ---------------------------------------------------------------------------------------------
# 5. smaller edge cases
# ---------------------------------------------------------------------------------------------
def test_growth_rate_pct_flags_or_drops_negative_base():
    f = make_frame([dict(source="s", indicator="BAL", iso3="AAA", period=p, value=v)
                    for p, v in (("2020", -10.0), ("2021", -5.0), ("2022", 5.0))])
    res = hz.growth_rate(f)
    flagged = any("negative" in c.code or "sign" in c.code for c in res.caveats)
    assert flagged or res.data.empty


def test_pct_of_frontier_max_ignores_aggregates():
    f = make_frame([dict(source="worldbank", indicator="X", iso3=i, period="2020", value=v)
                    for i, v in (("AAA", 50.0), ("HIC", 100.0), ("BBB", 80.0))])
    res = hz.pct_of_frontier(f, "max")
    vals = dict(zip(res.data["iso3"], res.data["value"]))
    assert vals["BBB"] == pytest.approx(100.0)   # BBB is the best country: 80 / 80


def test_peer_median_follows_quantile_method():
    ps = PeerSet(name="P", members=["AAA", "BBB", "CCC", "DDD"], approver="review", frozen_at="2026-01-01")
    f = make_frame([dict(source="s", indicator="I", iso3=i, period="2020", value=v)
                    for i, v in zip(ps.members, (1.0, 2.0, 3.0, 4.0))])
    res = peer_aggregate(f, ps, stats=["median"], quantile_method="lower")
    assert res.data["value"].iloc[0] == pytest.approx(np.percentile([1, 2, 3, 4], 50, method="lower"))  # 2.0


def test_two_input_methods_keep_aggregate_codes_of_both_inputs():
    num = Result(make_frame([dict(source="s", indicator="N", iso3="AAA", period="2020", value=1.0)]),
                 meta={"aggregates": ["XAG"]})
    den = Result(make_frame([dict(source="s", indicator="D", iso3="AAA", period="2020", value=2.0)]),
                 meta={"aggregates": ["XBG"]})
    out = hz.ratio(num, den)
    assert {"XAG", "XBG"} <= set(out.meta.get("aggregates", []))
