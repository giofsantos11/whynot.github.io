"""Unit tests for cgjrkit.methods.growth_accounting (synthetic PWT-shaped panels, hand-computed numbers).

Country codes AAA/BBB and all values are synthetic round numbers, not data.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import validate_frame
from cgjrkit.methods.growth_accounting import growth_accounting, perpetual_inventory, potential_output
from cgjrkit.methods.harmonize import PPPMixError

UNITS = {"rgdpna": "million 2017 US$ at constant 2017 national prices (PPP 2017)", "rkna": "index, 2017=1",
         "rnna": "million 2017 US$ at constant 2017 national prices (PPP 2017)", "emp": "millions", "hc": "index",
         "labsh": "share of GDP (0-1)"}


def panel(data: dict[str, dict[str, dict[int, float]]], status: dict | None = None, dataset: str = "PWT10.01") -> pd.DataFrame:
    """data[iso3][variable][year] = value (synthetic)."""
    rows = []
    for iso, variables in data.items():
        for var, series in variables.items():
            for year, value in series.items():
                rows.append({"source": "pwt", "dataset": dataset, "indicator": var, "indicator_label": var,
                             "iso3": iso, "country": f"Country {iso}", "period": str(year), "value": value,
                             "unit": UNITS.get(var, ""), "obs_status": (status or {}).get((iso, var, year), "")})
    return pd.DataFrame(rows)


# Y doubles, K triples, L +20%, hc +20% over 2000-2010; labour share 0.6 (alpha = 0.4)
SIMPLE = {"AAA": {"rgdpna": {2000: 100.0, 2010: 200.0}, "rkna": {2000: 1.0, 2010: 3.0},
                  "emp": {2000: 10.0, 2010: 12.0}, "hc": {2000: 2.0, 2010: 2.4},
                  "labsh": {2000: 0.6, 2010: 0.6}}}


def contributions(res: Result, iso3: str = "AAA", period: str | None = None) -> dict[str, float]:
    d = res.data[res.data["iso3"] == iso3]
    if period:
        d = d[d["period"] == period]
    return dict(zip(d["component"], d["value"]))


def test_hand_computed_aggregate_decomposition():
    res = growth_accounting(panel(SIMPLE), {"2000s": ("2000", "2010")})
    c = contributions(res)
    # 100 * ln(2)/10, 0.4 * 100 * ln(3)/10, 0.6 * 100 * ln(1.2)/10 (twice), residual
    assert c["output"] == pytest.approx(6.931472, abs=1e-6)
    assert c["capital"] == pytest.approx(4.394449, abs=1e-6)
    assert c["labour"] == pytest.approx(1.093929, abs=1e-6)
    assert c["human_capital"] == pytest.approx(1.093929, abs=1e-6)
    assert c["tfp"] == pytest.approx(0.349164, abs=1e-6)
    assert list(c) == ["output", "capital", "labour", "human_capital", "tfp"]
    row = res.data.iloc[0]
    assert row["alpha"] == pytest.approx(0.4) and row["years"] == 10
    assert row["start"] == "2000" and row["end"] == "2010" and row["period"] == "2000s"
    assert set(res.data["indicator"]) == {"ga.output", "ga.capital", "ga.labour", "ga.human_capital", "ga.tfp"}
    assert res.data["unit"].iloc[0].startswith("log points per year")
    share = dict(zip(res.data["component"], res.data["share_of_growth"]))
    assert share["output"] == pytest.approx(100.0)
    assert share["capital"] == pytest.approx(100 * 4.394449 / 6.931472, rel=1e-6)
    validate_frame(res.data)


def test_identity_holds_exactly_across_options():
    rng = np.random.default_rng(7)  # synthetic data, fixed seed
    years = range(1990, 2011)
    data = {}
    for iso in ("AAA", "BBB"):
        data[iso] = {
            "rgdpna": {y: 100 * math.exp(0.04 * (y - 1990) + rng.normal(0, 0.02)) for y in years},
            "rkna": {y: 50 * math.exp(0.05 * (y - 1990) + rng.normal(0, 0.01)) for y in years},
            "emp": {y: 10 * math.exp(0.02 * (y - 1990) + rng.normal(0, 0.01)) for y in years},
            "hc": {y: 2 + 0.01 * (y - 1990) for y in years},
            "labsh": {y: 0.5 + 0.1 * rng.random() for y in years},
        }
    p = panel(data)
    windows = {"1990-2000": ("1990", "2000"), "2000-2010": ("2000", "2010")}
    for kw in [dict(), dict(alpha=0.35), dict(per_worker=True), dict(units="percent"), dict(weights="tornqvist"),
               dict(weights="tornqvist", per_worker=True, units="percent"), dict(human_capital=None)]:
        res = growth_accounting(p, windows, **kw)
        total = "output_per_worker" if kw.get("per_worker") else "output"
        for (_, _), g in res.data.groupby(["iso3", "period"]):
            parts = g.loc[g["component"] != total, "value"].sum()
            assert parts == pytest.approx(g.loc[g["component"] == total, "value"].iloc[0], abs=1e-12), kw
        assert res.meta["growth_accounting"]["identity_max_abs_error"] < 1e-12


def test_per_worker_variant_same_tfp():
    agg = contributions(growth_accounting(panel(SIMPLE), [("2000", "2010")]))
    pw_res = growth_accounting(panel(SIMPLE), [("2000", "2010")], per_worker=True)
    pw = contributions(pw_res)
    # 100*ln((200/12)/(100/10))/10 = 100*ln(5/3)/10; 0.4*100*ln((3/12)/(1/10))/10 = 0.4*100*ln(2.5)/10
    assert pw["output_per_worker"] == pytest.approx(5.108256, abs=1e-6)
    assert pw["capital_deepening"] == pytest.approx(3.665163, abs=1e-6)
    assert pw["human_capital"] == pytest.approx(1.093929, abs=1e-6)
    assert pw["tfp"] == pytest.approx(agg["tfp"])
    assert pw_res.data["period"].iloc[0] == "2000-2010"
    assert set(pw_res.data["indicator"]) == {"ga_pw.output_per_worker", "ga_pw.capital_deepening",
                                             "ga_pw.human_capital", "ga_pw.tfp"}


def test_fixed_alpha_and_sensitivity_band():
    res = growth_accounting(panel(SIMPLE), {"2000s": ("2000", "2010")}, alpha=0.3)
    c = contributions(res)
    assert c["capital"] == pytest.approx(0.3 * 100 * math.log(3) / 10)
    assert c["labour"] == pytest.approx(0.7 * 100 * math.log(1.2) / 10)
    d = res.data.set_index("component")
    # band over alpha in {0.3, 0.35, 0.4}
    assert d.loc["capital", "band_min"] == pytest.approx(3.295837, abs=1e-6)
    assert d.loc["capital", "band_max"] == pytest.approx(4.394449, abs=1e-6)
    assert d.loc["tfp", "band_min"] == pytest.approx(0.349164, abs=1e-6)  # alpha = 0.4
    assert d.loc["tfp", "band_max"] == pytest.approx(6.931472 - 3.295837 - 2 * 1.276251, abs=1e-6)  # alpha = 0.3
    assert d.loc["output", "band_min"] == d.loc["output", "band_max"] == pytest.approx(6.931472, abs=1e-6)
    sens = pd.DataFrame(res.meta["alpha_sensitivity"])
    assert sorted(sens["alpha"].unique()) == [0.3, 0.35, 0.4]
    assert len(sens) == 3 * 5
    assert "alpha_from_labour_share" not in {cv.code for cv in res.caveats}
    nob = growth_accounting(panel(SIMPLE), [("2000", "2010")], sensitivity=None)
    assert nob.data["band_min"].isna().all() and nob.meta["alpha_sensitivity"] == []


def test_percent_units_rescale_to_cagr():
    res = growth_accounting(panel(SIMPLE), [("2000", "2010")], units="percent")
    c = contributions(res)
    cagr = 100 * (2 ** 0.1 - 1)  # 7.177346
    assert c["output"] == pytest.approx(7.177346, abs=1e-6) and c["output"] == pytest.approx(cagr)
    factor = cagr / (100 * math.log(2) / 10)
    assert c["capital"] == pytest.approx(4.394449 * factor, abs=1e-6)
    assert sum(v for k, v in c.items() if k != "output") == pytest.approx(cagr)
    assert res.data["unit"].iloc[0].startswith("percent per year")


def test_tornqvist_weights_hand_computed():
    data = {"AAA": {"rgdpna": {2000: 100.0, 2001: 110.0, 2002: 121.0},
                    "rkna": {2000: 100.0, 2001: 105.0, 2002: 115.0},
                    "emp": {2000: 50.0, 2001: 51.0, 2002: 52.0},
                    "hc": {2000: 2.0, 2001: 2.0, 2002: 2.1},
                    "labsh": {2000: 0.6, 2001: 0.5, 2002: 0.7}}}
    res = growth_accounting(panel(data), [("2000", "2002")], weights="tornqvist")
    c = contributions(res)
    a1, a2 = 1 - (0.6 + 0.5) / 2, 1 - (0.5 + 0.7) / 2  # 0.45 and 0.40
    assert c["output"] == pytest.approx(50 * math.log(1.21))
    assert c["capital"] == pytest.approx(50 * (a1 * math.log(1.05) + a2 * math.log(115 / 105)))
    assert c["labour"] == pytest.approx(50 * ((1 - a1) * math.log(51 / 50) + (1 - a2) * math.log(52 / 51)))
    assert c["human_capital"] == pytest.approx(50 * (1 - a2) * math.log(1.05))
    assert res.data["alpha"].iloc[0] == pytest.approx(0.425)
    # TFP growth equals the PWT-style Tornqvist residual dln Y - (1-s) dln K - s dln(emp*hc), summed over years
    s1, s2 = 0.55, 0.60
    tfp = (math.log(1.1) - (1 - s1) * math.log(1.05) - s1 * math.log(51 * 2.0 / (50 * 2.0))
           + math.log(1.1) - (1 - s2) * math.log(115 / 105) - s2 * math.log(52 * 2.1 / (51 * 2.0)))
    assert c["tfp"] == pytest.approx(50 * tfp)
    # Tornqvist needs every year: a gap skips the window
    gap = {"AAA": {k: {y: v for y, v in s.items() if y != 2001} for k, s in data["AAA"].items()}}
    with pytest.raises(ValueError, match="no country/period"):
        growth_accounting(panel(gap), [("2000", "2002")], weights="tornqvist")
    assert contributions(growth_accounting(panel(gap), [("2000", "2002")]))["output"] == pytest.approx(c["output"])
    with pytest.raises(ValueError, match="alpha=None"):
        growth_accounting(panel(data), [("2000", "2002")], alpha=0.3, weights="tornqvist")


def test_period_mean_labour_share_uses_all_years_in_window():
    data = {"AAA": {**SIMPLE["AAA"], "labsh": {2000: 0.5, 2005: 0.6, 2010: 0.7}}}
    res = growth_accounting(panel(data), [("2000", "2010")])
    assert res.data["alpha"].iloc[0] == pytest.approx(0.4)  # 1 - mean(0.5, 0.6, 0.7)


def test_missing_data_skips_windows_and_defaults_alpha():
    data = {"AAA": SIMPLE["AAA"],
            "BBB": {"rgdpna": {2000: 100.0, 2010: 150.0}, "rkna": {2000: 1.0, 2010: 2.0},
                    "emp": {2000: 10.0, 2010: 11.0}, "hc": {2000: 2.0}}}  # hc missing in 2010
    res = growth_accounting(panel(data), {"2000s": ("2000", "2010"), "late": ("2005", "2010")})
    assert set(res.data["iso3"]) == {"AAA"}
    codes = {c.code for c in res.caveats}
    assert "growth_accounting_skipped" in codes
    skipped = res.meta["growth_accounting"]["skipped"]
    assert {(s["iso3"], s["period"]) for s in skipped} == {("BBB", "2000s"), ("BBB", "late"), ("AAA", "late")}

    nohc = growth_accounting(panel(data), {"2000s": ("2000", "2010")}, human_capital=None, iso3="BBB")
    c = contributions(nohc, "BBB")
    assert "human_capital" not in c
    assert nohc.data["alpha"].iloc[0] == pytest.approx(1 / 3)  # BBB has no labour share
    assert c["labour"] == pytest.approx((2 / 3) * 100 * math.log(1.1) / 10)
    assert "alpha_default" in {cv.code for cv in nohc.caveats}


def test_input_validation():
    with pytest.raises(ValueError, match="fraction"):
        growth_accounting(panel({"AAA": {**SIMPLE["AAA"], "labsh": {2000: 60.0, 2010: 60.0}}}), [("2000", "2010")])
    dup = pd.concat([panel(SIMPLE), panel(SIMPLE, dataset="PWT11.0")], ignore_index=True)
    with pytest.raises(ValueError, match="several observations"):
        growth_accounting(dup, [("2000", "2010")])
    with pytest.raises(ValueError, match="end after"):
        growth_accounting(panel(SIMPLE), [("2010", "2000")])
    with pytest.raises(ValueError, match="no 'rkna'"):
        growth_accounting(panel(SIMPLE)[lambda d: d["indicator"] != "rkna"], [("2000", "2010")])
    with pytest.raises(ValueError):
        growth_accounting(panel(SIMPLE), [("2000", "2010")], alpha=1.5)
    with pytest.raises(ValueError, match="no observations"):
        growth_accounting(panel(SIMPLE), [("2000", "2010")], iso3="ZZZ")


def test_rnna_capital_status_flags_and_provenance():
    data = {"AAA": {**SIMPLE["AAA"], "rnna": {2000: 300.0, 2010: 900.0}}}
    p = Result(data=panel(data, status={("AAA", "rgdpna", 2010): "forecast"}),
               provenance=[Provenance(source="pwt", dataset="PWT10.01", vintage="PWT 10.01")])
    res = growth_accounting([p], [("2000", "2010")], capital="rnna")
    codes = {c.code for c in res.caveats}
    assert {"capital_stock_not_services", "forecast_values", "growth_accounting_residual"} <= codes
    assert (res.data["obs_status"] == "forecast").all()
    assert contributions(res)["capital"] == pytest.approx(0.4 * 100 * math.log(3) / 10)
    assert res.provenance[0].vintage == "PWT 10.01"
    assert res.provenance[-1].tool == "growth_accounting.growth_accounting"
    assert res.provenance[-1].params["capital"] == "rnna"
    json.dumps(res.summary())


def test_panel_from_several_frames():
    p = panel(SIMPLE)
    parts = [Result(data=p[p["indicator"] == v].reset_index(drop=True), provenance=[Provenance(source=v)])
             for v in ("rgdpna", "rkna", "emp", "hc", "labsh")]
    res = growth_accounting(parts, [("2000", "2010")])
    assert contributions(res)["tfp"] == pytest.approx(0.349164, abs=1e-6)
    assert [pr.source for pr in res.provenance][:5] == ["rgdpna", "rkna", "emp", "hc", "labsh"]


# ---------------------------------------------------------------------------
# perpetual_inventory
# ---------------------------------------------------------------------------
def inv_frame(series: dict[str, dict[int, float]], unit: str = "constant 2015 LCU") -> pd.DataFrame:
    return pd.DataFrame([{"source": "synthetic", "dataset": "TEST", "indicator": "INV", "indicator_label": "Investment",
                          "iso3": iso, "country": iso, "period": str(y), "value": v, "unit": unit, "obs_status": ""}
                         for iso, s in series.items() for y, v in s.items()])


def test_pim_steady_state_constant_investment():
    # K0 = I (1 + g) / (g + delta) = 100 / 0.05 = 2000 and stays there
    res = perpetual_inventory(inv_frame({"AAA": {y: 100.0 for y in range(2000, 2006)}}), 0.05, g=0.0)
    assert res.data["value"].tolist() == pytest.approx([2000.0] * 6)
    assert set(res.data["indicator"]) == {"INV|pim"}
    assert res.meta["pim"]["AAA"]["K0"] == pytest.approx(2000.0)
    assert res.meta["pim"]["AAA"]["initial_weight_half_life_years"] == pytest.approx(math.log(0.5) / math.log(0.95))
    validate_frame(res.data)


def test_pim_steady_state_growth_estimated_from_investment():
    # I grows 10% a year -> g = 0.1, K0 = 100 * 1.1 / 0.15 = 733.33; K then grows exactly 10% a year
    inv = {"AAA": {2000 + t: 100.0 * 1.1 ** t for t in range(4)}}
    res = perpetual_inventory(inv_frame(inv), 0.05)
    k = res.data["value"].to_numpy()
    assert k[0] == pytest.approx(733.3333333)
    assert k[1] == pytest.approx(0.95 * 733.3333333 + 110.0)
    assert np.allclose(k[1:] / k[:-1], 1.1)
    assert res.meta["pim"]["AAA"]["g"] == pytest.approx(0.1)


def test_pim_given_initial_lagged_timing_and_delta_inputs():
    res = perpetual_inventory(inv_frame({"AAA": {2000: 100.0, 2001: 100.0, 2002: 100.0}}), 0.1, initial=500.0)
    assert res.data["value"].tolist() == pytest.approx([500.0, 550.0, 595.0])
    lag = perpetual_inventory(inv_frame({"AAA": {2000: 100.0, 2001: 200.0, 2002: 200.0}}), 0.1, initial=1000.0,
                              timing="lagged")
    assert lag.data["value"].tolist() == pytest.approx([1000.0, 1000.0, 1100.0])
    ss_lag = perpetual_inventory(inv_frame({"AAA": {2000: 100.0, 2001: 100.0}}), 0.05, g=0.0, timing="lagged")
    assert ss_lag.data["value"].tolist() == pytest.approx([2000.0, 2000.0])
    two = perpetual_inventory(inv_frame({"AAA": {2000: 100.0, 2001: 100.0}, "BBB": {2000: 10.0, 2001: 10.0}}),
                              {"AAA": 0.1, "BBB": 0.2}, initial={"AAA": 1000.0, "BBB": 50.0})
    d = two.data.set_index(["iso3", "period"])["value"]
    assert d[("AAA", "2001")] == pytest.approx(1000.0) and d[("BBB", "2001")] == pytest.approx(50.0)
    dframe = pd.DataFrame([{"source": "pwt", "dataset": "PWT10.01", "indicator": "delta", "indicator_label": "",
                            "iso3": "AAA", "country": "", "period": str(y), "value": v, "unit": "rate (0-1)",
                            "obs_status": ""} for y, v in ((2000, 0.1), (2001, 0.2))])
    tv = perpetual_inventory(inv_frame({"AAA": {2000: 100.0, 2001: 100.0}}), dframe, initial=1000.0)
    assert tv.data["value"].tolist() == pytest.approx([1000.0, 900.0])  # 0.8 * 1000 + 100


def test_pim_errors_and_caveats():
    with pytest.raises(ValueError, match="interior gaps"):
        perpetual_inventory(inv_frame({"AAA": {2000: 1.0, 2002: 1.0}}), 0.05)
    with pytest.raises(ValueError, match="\\[0, 1\\)"):
        perpetual_inventory(inv_frame({"AAA": {2000: 1.0, 2001: 1.0}}), 1.5)
    with pytest.raises(ValueError, match="no entry"):
        perpetual_inventory(inv_frame({"AAA": {2000: 1.0, 2001: 1.0}}), {"BBB": 0.05})
    res = perpetual_inventory(inv_frame({"AAA": {2000: 1.0, 2001: 1.0}}, unit="current LCU"), 0.05, g=0.0)
    assert {"pim_current_prices", "pim_initial_condition"} <= {c.code for c in res.caveats}
    assert res.provenance[-1].tool == "growth_accounting.perpetual_inventory"


# ---------------------------------------------------------------------------
# potential_output
# ---------------------------------------------------------------------------
def smooth_panel(bump: dict[int, float] | None = None, years=range(2000, 2016)) -> pd.DataFrame:
    """Y = A K^0.4 (hc L)^0.6 with log-linear A, K, L, hc (+ optional TFP bumps)."""
    data = {"rgdpna": {}, "rkna": {}, "emp": {}, "hc": {}, "labsh": {}}
    for t, y in enumerate(years):
        a = math.exp(0.01 * t + (bump or {}).get(y, 0.0))
        k, l, h = 100 * 1.05 ** t, 50 * 1.02 ** t, 2 * 1.01 ** t
        data["rgdpna"][y] = a * k ** 0.4 * (h * l) ** 0.6
        data["rkna"][y], data["emp"][y], data["hc"][y], data["labsh"][y] = k, l, h, 0.6
    return panel({"AAA": data})


def test_potential_output_on_smooth_inputs_equals_actual():
    res = potential_output(smooth_panel())
    d = res.data
    pot = d[d["component"] == "potential_output"].set_index("period")["value"]
    act = smooth_panel().query("indicator == 'rgdpna'").set_index("period")["value"]
    assert np.allclose(pot.to_numpy(), act.loc[pot.index].to_numpy(), rtol=1e-8)
    gap = d[d["component"] == "output_gap"]["value"]
    assert np.allclose(gap, 0.0, atol=1e-6)
    comp = d.pivot(index="period", columns="component", values="value").dropna()
    # hand-computed contributions: TFP 1.0, capital 40 ln 1.05, labour 60 ln 1.02, hc 60 ln 1.01 (log points a year)
    assert np.allclose(comp["tfp"], 1.0, atol=1e-6)
    assert np.allclose(comp["capital"], 40 * math.log(1.05))
    assert np.allclose(comp["labour"], 60 * math.log(1.02), atol=1e-6)
    assert np.allclose(comp["human_capital"], 60 * math.log(1.01))
    total = comp[["capital", "labour", "human_capital", "tfp"]].sum(axis=1)
    assert np.allclose(total, comp["potential_growth"], atol=1e-12)
    assert res.meta["potential_output"]["alpha"]["AAA"] == pytest.approx(0.4)
    cav = {c.code: c for c in res.caveats}
    assert cav["potential_output_approximation"].severity == "warn"
    assert "MFMod" in cav["potential_output_approximation"].message
    assert res.provenance[-1].tool == "growth_accounting.potential_output"


def test_potential_output_gap_sums_to_zero_with_tfp_cycle():
    # the HP trend preserves the sample sum, so with smooth employment the output gap sums to zero
    res = potential_output(smooth_panel(bump={2005: 0.03, 2006: 0.02, 2011: -0.04}), alpha=0.4)
    gap = res.data[res.data["component"] == "output_gap"].set_index("period")["value"]
    assert gap.sum() == pytest.approx(0.0, abs=1e-8)
    assert gap["2005"] > 0 and gap["2011"] < 0
    comp = res.data.pivot(index="period", columns="component", values="value").dropna()
    total = comp[["capital", "labour", "human_capital", "tfp"]].sum(axis=1)
    assert np.allclose(total, comp["potential_growth"], atol=1e-12)


def test_potential_output_skips_short_or_gappy_samples():
    with pytest.raises(ValueError, match="enough data"):
        potential_output(smooth_panel(years=range(2000, 2005)))
    res = potential_output(smooth_panel(), end="2010", min_years=5)
    assert res.data["period"].max() == "2010"


# ---------------------------------------------------------------------------
# review fixes: PPP bases and unit labels within a series
# ---------------------------------------------------------------------------
PPP17_UNIT = "million 2017 US$ at constant 2017 national prices (PPP 2017)"
PPP21_UNIT = "million 2021 US$ at constant 2021 national prices (PPP 2021)"


def _two_editions(old_unit: str, new_unit: str, switch: int = 2005) -> pd.DataFrame:
    """rgdpna/rkna in one unit up to ``switch`` and another after (flat within each edition), emp flat."""
    rows = []
    for y in range(2000, 2011):
        old = y <= switch
        unit, ds = (old_unit, "PWT10.01") if old else (new_unit, "PWT11.0")
        rows += [{"source": "pwt", "dataset": ds, "indicator": "rgdpna", "iso3": "AAA", "period": str(y),
                  "value": 100.0 if old else 150.0, "unit": unit},
                 {"source": "pwt", "dataset": ds, "indicator": "rkna", "iso3": "AAA", "period": str(y),
                  "value": 100.0, "unit": unit},
                 {"source": "pwt", "dataset": ds, "indicator": "emp", "iso3": "AAA", "period": str(y),
                  "value": 10.0, "unit": "millions"}]
    return pd.DataFrame(rows)


def test_growth_accounting_ppp_mix_within_a_series_raises_or_blocks():
    df = _two_editions(PPP17_UNIT, PPP21_UNIT)
    with pytest.raises(PPPMixError):
        growth_accounting(df, {"w": ("2000", "2010")}, alpha=0.3, human_capital=None, sensitivity=None)
    res = growth_accounting(df, {"w": ("2000", "2010")}, alpha=0.3, human_capital=None, sensitivity=None,
                            on_ppp_mix="block")
    assert res.blocked
    cav = next(c for c in res.caveats if c.code == "ppp_base_mix")
    assert {g["group"] for g in cav.evidence["groups"]} == {"rgdpna/AAA", "rkna/AAA"}
    with pytest.raises(PPPMixError):
        potential_output(df, alpha=0.3, human_capital=None, min_years=5)


def test_growth_accounting_warns_on_base_year_switch_but_not_across_countries():
    df = _two_editions("constant 2009 LCU", "constant 2016 LCU")
    res = growth_accounting(df, {"w": ("2000", "2010")}, alpha=0.3, human_capital=None, sensitivity=None)
    cav = next(c for c in res.caveats if c.code == "mixed_units")
    assert cav.severity == "warn" and not res.blocked
    roles = {g["group"]: g["role"] for g in cav.evidence["groups"]}
    assert roles == {"rgdpna/AAA": "Y", "rkna/AAA": "K"}
    # two countries with their own (constant) labels are fine: growth rates are unit-free
    two = pd.concat([_two_editions("constant 2009 LCU", "constant 2009 LCU"),
                     _two_editions("constant 2016 LCU", "constant 2016 LCU").assign(iso3="BBB")], ignore_index=True)
    ok = growth_accounting(two, {"w": ("2000", "2010")}, alpha=0.3, human_capital=None, sensitivity=None)
    assert "mixed_units" not in {c.code for c in ok.caveats}
    pot = potential_output(two, alpha=0.3, human_capital=None, min_years=5)
    level_units = pot.data[pot.data["component"] == "potential_output"].groupby("iso3")["unit"].first().to_dict()
    assert level_units == {"AAA": "constant 2009 LCU", "BBB": "constant 2016 LCU"}   # each country's own label


def test_perpetual_inventory_guards_units_of_investment():
    def inv(unit_old, unit_new):
        return pd.DataFrame([{"source": "pwt", "indicator": "I", "iso3": "AAA", "period": str(y), "value": 10.0,
                              "unit": unit_old if y <= 2005 else unit_new} for y in range(2000, 2011)])

    with pytest.raises(PPPMixError):
        perpetual_inventory(inv(PPP17_UNIT, PPP21_UNIT), 0.05, initial=100.0)
    blocked = perpetual_inventory(inv(PPP17_UNIT, PPP21_UNIT), 0.05, initial=100.0, on_ppp_mix="block")
    assert blocked.blocked
    mixed = perpetual_inventory(inv("constant 2009 LCU", "constant 2016 LCU"), 0.05, initial=100.0)
    assert "mixed_units" in {c.code for c in mixed.caveats}
    assert set(mixed.data["unit"]) == {"constant 2009 LCU; constant 2016 LCU"}   # never just the first row's label
    clean = perpetual_inventory(inv("constant 2009 LCU", "constant 2009 LCU"), 0.05, initial=100.0)
    assert set(clean.data["unit"]) == {"constant 2009 LCU"} and "mixed_units" not in {c.code for c in clean.caveats}


def test_panel_list_keeps_aggregate_codes_of_every_input():
    a = Result(data=panel(SIMPLE)[lambda d: d["indicator"].isin(["rgdpna", "rkna"])], meta={"aggregates": ["XAG"]})
    b = Result(data=panel(SIMPLE)[lambda d: ~d["indicator"].isin(["rgdpna", "rkna"])], meta={"aggregates": ["XBG"]})
    res = growth_accounting([a, b], {"2000s": ("2000", "2010")})
    assert {"XAG", "XBG"} <= set(res.meta["aggregates"])
