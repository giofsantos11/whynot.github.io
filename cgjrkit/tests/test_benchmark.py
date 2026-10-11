"""Unit tests for cgjrkit.methods.benchmark (synthetic values, hand-computed results).

All frames are synthetic (country codes AAA, BBB, ... and round numbers); no
value here is real data.
"""

from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import validate_frame
from cgjrkit.methods.benchmark import (
    distance_to_frontier,
    income_conditional_fit,
    kernel_density,
    percentile_of,
    percentile_rank,
    zscore,
)
from cgjrkit.methods.harmonize import PPPMixError


def frame(indicator: str, rows, unit: str = "% of GDP", **defaults) -> pd.DataFrame:
    """rows: (iso3, period, value[, obs_status])."""
    base = {"source": "synthetic", "dataset": "TEST", "indicator": indicator, "indicator_label": f"Synthetic {indicator}",
            "unit": unit, "obs_status": ""}
    base.update(defaults)
    out = []
    for r in rows:
        rec = dict(base, iso3=r[0], country=f"Country {r[0]}", period=r[1], value=r[2])
        if len(r) > 3:
            rec["obs_status"] = r[3]
        out.append(rec)
    return pd.DataFrame(out)


def phi(z: float) -> float:
    return math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)


def countries(res: Result) -> pd.DataFrame:
    return res.data[res.data["role"] == "country"].set_index("iso3")


# ---------------------------------------------------------------------------
# income_conditional_fit
# ---------------------------------------------------------------------------
X3 = frame("X", [("AAA", "2020", 1.0), ("BBB", "2020", 2.0), ("CCC", "2020", 3.0), ("WLD", "2020", 2.0)], unit="index")
Y3 = frame("Y", [("AAA", "2020", 1.0), ("BBB", "2020", 2.0), ("CCC", "2020", 2.0), ("WLD", "2020", 9.0)])


def test_linear_fit_hand_computed():
    # x = 1,2,3; y = 1,2,2: slope = Sxy/Sxx = 1/2, intercept = 5/3 - 1 = 2/3,
    # residuals -1/6, 1/3, -1/6; SSR = 1/6, SST = 2/3, R2 = 0.75, sigma = sqrt(SSR/1).
    res = income_conditional_fit(X3, Y3, "2020", kind="linear", log_x=False, focus="BBB", ci=None)
    fit = res.meta["fit"]
    assert fit["coefficients"]["b0"] == pytest.approx(2 / 3)
    assert fit["coefficients"]["b1"] == pytest.approx(0.5)
    assert fit["r2"] == pytest.approx(0.75)
    assert fit["n"] == 3
    assert fit["residual_sd"] == pytest.approx(math.sqrt(1 / 6))
    c = countries(res)
    assert "WLD" not in c.index  # World Bank aggregate never enters the fit
    assert res.meta["dropped"]["aggregates"] == ["WLD"]
    assert c.loc["AAA", "residual"] == pytest.approx(-1 / 6)
    assert c.loc["BBB", "residual"] == pytest.approx(1 / 3)
    assert c.loc["CCC", "fitted"] == pytest.approx(2 / 3 + 1.5)
    f = res.meta["focus"]
    assert f["predicted"] == pytest.approx(5 / 3)
    assert f["actual"] == 2.0
    assert f["residual_z"] == pytest.approx((1 / 3) / math.sqrt(1 / 6))
    assert f["residual_percentile"] == 100.0 and f["above_curve"] is True
    assert bool(c.loc["BBB", "is_focus"]) and not bool(c.loc["AAA", "is_focus"])


def test_confidence_band_at_mean_of_x():
    # At x = mean(x) = 2 the s.e. of the fitted mean is sigma/sqrt(n); t(0.975, 1 df) = 12.7062.
    res = income_conditional_fit(X3, Y3, "2020", kind="linear", log_x=False, grid_points=3, ci=0.95)
    curve = res.data[res.data["role"] == "curve"].reset_index(drop=True)
    assert curve["x"].tolist() == pytest.approx([1.0, 2.0, 3.0])
    half = 12.7062047 * math.sqrt(1 / 6) / math.sqrt(3)
    assert curve.loc[1, "fitted"] == pytest.approx(5 / 3)
    assert curve.loc[1, "ci_lower"] == pytest.approx(5 / 3 - half, rel=1e-6)
    assert curve.loc[1, "ci_upper"] == pytest.approx(5 / 3 + half, rel=1e-6)


def test_log_linear_and_quadratic_exact():
    xs = [500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0]
    iso = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    x = frame("GDPPC", [(i, "2023", v) for i, v in zip(iso, xs)], unit="constant 2017 international $ (PPP 2017)")
    y_lin = frame("Y", [(i, "2023", 2 + 3 * math.log(v)) for i, v in zip(iso, xs)])
    res = income_conditional_fit(x, y_lin, "2023", kind="linear")
    assert res.meta["fit"]["coefficients"]["b0"] == pytest.approx(2.0)
    assert res.meta["fit"]["coefficients"]["b1"] == pytest.approx(3.0)
    assert res.meta["fit"]["r2"] == pytest.approx(1.0)
    assert np.allclose(countries(res)["residual"], 0.0, atol=1e-9)
    curve = res.data[res.data["role"] == "curve"]
    assert curve["x"].iloc[0] == pytest.approx(500.0) and curve["x"].iloc[-1] == pytest.approx(16000.0)
    assert np.allclose(curve["fitted"], 2 + 3 * np.log(curve["x"]))
    # the grid is evenly spaced in log x
    assert np.allclose(np.diff(np.log(curve["x"])), np.log(32) / 99)

    y_q = frame("Y", [(i, "2023", 1 + 2 * math.log(v) - 0.5 * math.log(v) ** 2) for i, v in zip(iso, xs)])
    resq = income_conditional_fit(x, y_q, "2023", kind="quadratic")
    co = resq.meta["fit"]["coefficients"]
    assert (co["b0"], co["b1"], co["b2"]) == pytest.approx((1.0, 2.0, -0.5), abs=1e-6)
    assert resq.meta["fit"]["equation"] == "y = b0 + b1*ln(x) + b2*ln(x)^2"


def test_lowess_reproduces_a_line():
    iso = [f"A{c}A" for c in "ABCDEFGHIJ"]
    x = frame("X", [(i, "2020", float(k + 1)) for k, i in enumerate(iso)], unit="index")
    y = frame("Y", [(i, "2020", 1.0 + 2.0 * (k + 1)) for k, i in enumerate(iso)])
    res = income_conditional_fit(x, y, "2020", kind="lowess", log_x=False, frac=0.5)
    assert np.allclose(countries(res)["residual"], 0.0, atol=1e-9)
    curve = res.data[res.data["role"] == "curve"]
    assert np.allclose(curve["fitted"], 1.0 + 2.0 * curve["x"])
    assert curve["ci_lower"].isna().all()
    assert res.meta["fit"]["coefficients"] == {}


def test_exclude_and_y_bounds_leave_points_out_of_fit():
    rows_x = [("AAA", "2024", 1.0), ("BBB", "2024", 2.0), ("CCC", "2024", 3.0), ("DDD", "2024", 4.0), ("EEE", "2024", 5.0)]
    rows_y = [("AAA", "2024", 1.0), ("BBB", "2024", 2.0), ("CCC", "2024", 2.0), ("DDD", "2024", 50.0), ("EEE", "2024", 150.0)]
    x, y = frame("X", rows_x, unit="index"), frame("Y", rows_y)
    res = income_conditional_fit(x, y, "2024", log_x=False, exclude=["DDD"], y_bounds=(None, 100.0))
    c = countries(res)
    assert c.loc["DDD", "exclusion_reason"] == "excluded" and not bool(c.loc["DDD", "in_fit"])
    assert c.loc["EEE", "exclusion_reason"] == "y_out_of_bounds"
    # same coefficients as the 3-point fit above; excluded points are still scored
    assert res.meta["fit"]["coefficients"]["b1"] == pytest.approx(0.5)
    assert res.meta["fit"]["n"] == 3 and res.meta["fit"]["n_excluded"] == 2
    assert c.loc["DDD", "fitted"] == pytest.approx(2 / 3 + 0.5 * 4)


def test_log_drops_nonpositive_and_reports_it():
    x = frame("X", [("AAA", "2020", 1.0), ("BBB", "2020", 10.0), ("CCC", "2020", 100.0), ("DDD", "2020", 1000.0),
                    ("EEE", "2020", 0.0)], unit="US$")
    y = frame("Y", [(i, "2020", float(k)) for k, i in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE"])])
    res = income_conditional_fit(x, y, "2020")
    assert res.meta["dropped"]["x_nonpositive"] == ["EEE"]
    assert "log_nonpositive_dropped" in {c.code for c in res.caveats}
    assert "EEE" not in countries(res).index


def test_focus_path_scored_against_main_curve_and_missing_focus():
    xs = {"AAA": 1.0, "BBB": 2.0, "CCC": 3.0, "DDD": 4.0}
    x = frame("X", [(i, "2024", v) for i, v in xs.items()] + [("BBB", "2011", 1.5), ("BBB", "2015", 1.8)], unit="index")
    y = frame("Y", [(i, "2024", 2 * v) for i, v in xs.items()] + [("BBB", "2011", 4.0)])
    res = income_conditional_fit(x, y, "2024", log_x=False, focus="BBB", focus_path=["2011", "2015"])
    path = res.data[res.data["role"] == "focus_path"]
    assert path["period"].tolist() == ["2011"]
    assert path["fitted"].iloc[0] == pytest.approx(3.0)  # y = 2x on the 2024 curve
    assert path["residual"].iloc[0] == pytest.approx(1.0)
    assert "focus_path_missing" in {c.code for c in res.caveats}

    res2 = income_conditional_fit(x, y, "2024", log_x=False, focus="ZZZ")
    assert res2.meta["focus"] == {}
    assert "focus_missing" in {c.code for c in res2.caveats}


def test_ppp_mix_raises_or_blocks():
    x = pd.concat([
        frame("GDPPC", [("AAA", "2023", 1000.0), ("BBB", "2023", 2000.0)], unit="constant 2017 international $ (PPP 2017)"),
        frame("GDPPC", [("CCC", "2023", 3000.0), ("DDD", "2023", 4000.0)], unit="constant 2021 international $ (PPP 2021)"),
    ], ignore_index=True)
    y = frame("Y", [("AAA", "2023", 1.0), ("BBB", "2023", 2.0), ("CCC", "2023", 2.5), ("DDD", "2023", 4.0)])
    with pytest.raises(PPPMixError):
        income_conditional_fit(x, y, "2023")
    res = income_conditional_fit(x, y, "2023", on_ppp_mix="block")
    assert res.blocked
    assert "mixed_units" in {c.code for c in res.caveats}


def test_provenance_caveats_and_status_flags_carry_forward():
    x = Result(data=X3, provenance=[Provenance(source="synthetic", dataset="X", vintage="v1")],
               caveats=[Caveat("input_caveat", "info", "carried")], meta={"aggregates": ["BBB"]})
    y = frame("Y", [("AAA", "2020", 1.0), ("BBB", "2020", 2.0), ("CCC", "2020", 2.0, "forecast"),
                    ("DDD", "2020", 3.0)])
    xx = pd.concat([X3, frame("X", [("DDD", "2020", 4.0)], unit="index")], ignore_index=True)
    x.data = xx
    res = income_conditional_fit(x, y, "2020", log_x=False)
    assert "BBB" not in countries(res).index  # listed as aggregate in the input meta
    codes = {c.code for c in res.caveats}
    assert {"input_caveat", "forecast_values", "small_sample"} <= codes
    assert res.provenance[0].dataset == "X"
    assert res.provenance[-1].tool == "benchmark.income_conditional_fit"
    assert res.provenance[-1].params["kind"] == "linear"
    assert countries(res).loc["CCC", "obs_status"] == "forecast"
    json.dumps(res.summary())  # JSON-safe for tool responses


def test_fit_input_errors():
    with pytest.raises(ValueError):
        income_conditional_fit(X3, Y3, "2020", kind="spline")
    two = pd.concat([Y3, frame("Z", [("AAA", "2020", 1.0)])], ignore_index=True)
    with pytest.raises(ValueError, match="exactly one indicator"):
        income_conditional_fit(X3, two, "2020", log_x=False)
    with pytest.raises(ValueError, match="at least"):
        income_conditional_fit(X3, Y3, "2020", kind="quadratic", log_x=False)
    with pytest.raises(ValueError, match="no observations"):
        income_conditional_fit(X3, Y3, "1999")
    with pytest.raises(ValueError, match="lowess returned NaN"):
        income_conditional_fit(X3, Y3, "2020", kind="lowess", log_x=False)


def test_different_periods_for_x_and_y():
    x = frame("X", [("AAA", "2022", 1.0), ("BBB", "2022", 2.0), ("CCC", "2022", 3.0)], unit="index")
    res = income_conditional_fit(x, Y3, ("2022", "2020"), log_x=False)
    assert res.meta["fit"]["x_period"] == "2022" and res.meta["fit"]["y_period"] == "2020"
    assert set(countries(res)["period"]) == {"2022|2020"}


# ---------------------------------------------------------------------------
# percentile_rank
# ---------------------------------------------------------------------------
P = frame("CREDIT", [("AAA", "2001", 10.0), ("BBB", "2001", 20.0), ("CCC", "2001", 30.0), ("DDD", "2001", 40.0),
                     ("AAA", "2023", 40.0), ("BBB", "2023", 20.0), ("CCC", "2023", 30.0), ("EEE", "2023", 50.0),
                     ("SSF", "2023", 25.0)])


def test_percentile_of_definitions():
    assert percentile_of([1, 2, 3, 4], 3) == 75.0
    assert percentile_of([1, 2, 3, 4], 3, "strict") == 50.0
    assert percentile_of([1, 2, 3, 4], 3, "mean") == 62.5
    assert percentile_of([1, 2, np.nan], 2) == 100.0
    with pytest.raises(ValueError):
        percentile_of([1], 1, "bogus")


def test_percentile_rank_hand_computed():
    res = percentile_rank(P, "BBB", "2001")
    d = res.data.set_index("iso3")
    assert d["value"].to_dict() == {"AAA": 25.0, "BBB": 50.0, "CCC": 75.0, "DDD": 100.0}
    assert d["rank"].to_dict() == {"AAA": 4, "BBB": 3, "CCC": 2, "DDD": 1}
    assert d.loc["BBB", "level"] == 20.0 and d.loc["BBB", "n"] == 4
    assert set(res.data["indicator"]) == {"CREDIT|pctile"}
    assert res.data["unit"].iloc[0] == "percentile (0-100, weak)"
    assert res.meta["focus"] == [{"indicator": "CREDIT", "period": "2001", "level": 20.0, "rank": 3, "n": 4,
                                  "percentile": 50.0}]
    validate_frame(res.data)


def test_percentile_rank_balanced_and_aggregates():
    res = percentile_rank(P, "BBB", ["2001", "2023"], balanced=True)
    # DDD (no 2023) and EEE (no 2001) dropped; SSF is a World Bank aggregate
    assert set(res.data["iso3"]) == {"AAA", "BBB", "CCC"}
    assert res.meta["dropped"]["aggregates"] == ["SSF"]
    assert res.meta["dropped"]["unbalanced"] == ["DDD", "EEE"]
    f = {x["period"]: x["percentile"] for x in res.meta["focus"]}
    assert f["2001"] == pytest.approx(200 / 3) and f["2023"] == pytest.approx(100 / 3)
    assert "unbalanced_sample_dropped" in {c.code for c in res.caveats}


def test_percentile_rank_ties_and_methods():
    t = frame("X", [("AAA", "2020", 10.0), ("BBB", "2020", 20.0), ("CCC", "2020", 20.0), ("DDD", "2020", 30.0)])
    d = percentile_rank(t, None, "2020").data.set_index("iso3")
    assert d.loc["BBB", "rank"] == 2 and d.loc["CCC", "rank"] == 2 and d.loc["AAA", "rank"] == 4
    assert d.loc["BBB", "value"] == 75.0
    d2 = percentile_rank(t, None, "2020", method="strict", descending=False).data.set_index("iso3")
    assert d2.loc["BBB", "value"] == 25.0 and d2.loc["AAA", "rank"] == 1


def test_percentile_rank_missing_focus_caveat():
    res = percentile_rank(P, "DDD", ["2001", "2023"])
    assert "focus_missing" in {c.code for c in res.caveats}


# ---------------------------------------------------------------------------
# kernel_density
# ---------------------------------------------------------------------------
def test_kernel_density_hand_computed():
    # values 0 and 2: sample s.d. sqrt(2); factor 1/sqrt(2) -> kernel s.d. (bandwidth) 1.
    k = frame("CREDIT", [("AAA", "2023", 0.0), ("BBB", "2023", 2.0)])
    res = kernel_density(k, "2023", grid_points=9, bw_method=1 / math.sqrt(2), focus="BBB")
    dens = res.data[res.data["role"] == "density"].reset_index(drop=True)
    assert dens["x"].tolist() == pytest.approx(list(range(-3, 6)))  # [min - 3bw, max + 3bw]
    assert dens.loc[4, "density"] == pytest.approx(phi(1.0))         # x = 1
    assert dens.loc[3, "density"] == pytest.approx(0.5 * (phi(0.0) + phi(2.0)))  # x = 0
    assert res.meta["kde"]["by_period"]["2023"]["bandwidth"] == pytest.approx(1.0)
    f = res.data[res.data["role"] == "focus"].iloc[0]
    assert f["iso3"] == "BBB" and f["x"] == 2.0 and f["percentile"] == 100.0
    assert f["density"] == pytest.approx(0.5 * (phi(2.0) + phi(0.0)))
    assert res.meta["focus"]["by_period"]["2023"]["percentile"] == 100.0
    # KDE CDF at 2 = 0.5*(Phi(2) + Phi(0))
    assert res.meta["focus"]["by_period"]["2023"]["kde_cdf"] == pytest.approx(50 * (0.9772499 + 0.5), rel=1e-6)


def test_kernel_density_common_grid_integrates_to_one_and_small_sample():
    vals_a = [5.0, 10.0, 12.0, 20.0, 31.0, 40.0]
    vals_b = [15.0, 18.0, 22.0, 35.0, 50.0, 80.0]
    rows = [(f"C{chr(65 + i)}A", "2001", v) for i, v in enumerate(vals_a)]
    rows += [(f"C{chr(65 + i)}A", "2023", v) for i, v in enumerate(vals_b)]
    res = kernel_density(frame("CREDIT", rows), ["2001", "2023"], grid_points=4001, focus="CAA")
    dens = res.data[res.data["role"] == "density"]
    g1 = dens[dens["period"] == "2001"]
    g2 = dens[dens["period"] == "2023"]
    assert np.allclose(g1["x"].to_numpy(), g2["x"].to_numpy())  # common grid
    for g in (g1, g2):
        assert np.trapezoid(g["density"], g["x"]) == pytest.approx(1.0, abs=0.01)
    assert {c.code for c in res.caveats} >= {"small_sample"}
    assert len(res.data[res.data["role"] == "country"]) == 12
    fp = res.meta["focus"]["by_period"]
    assert fp["2001"]["percentile"] == pytest.approx(100 / 6) and fp["2023"]["percentile"] == pytest.approx(100 / 6)


def test_kernel_density_errors():
    with pytest.raises(ValueError, match="distinct"):
        kernel_density(frame("X", [("AAA", "2020", 1.0), ("BBB", "2020", 1.0)]), "2020")
    with pytest.raises(ValueError, match="no observations"):
        kernel_density(frame("X", [("AAA", "2020", 1.0)]), "2021")


# ---------------------------------------------------------------------------
# zscore
# ---------------------------------------------------------------------------
Z = pd.concat([
    frame("A", [("AAA", "2022", 1.0), ("BBB", "2022", 2.0), ("CCC", "2022", 3.0)]),
    frame("B", [("AAA", "2022", 10.0), ("BBB", "2022", 30.0), ("CCC", "2022", 20.0)], unit="days"),
], ignore_index=True)


def test_zscore_hand_computed():
    res = zscore(Z, "2022")
    a = res.data[res.data["indicator"] == "A|z"].set_index("iso3")
    assert a["value"].tolist() == pytest.approx([-1.0, 0.0, 1.0])  # mean 2, sample sd 1
    assert a.loc["CCC", "level"] == 3.0 and a.loc["CCC", "mean"] == 2.0 and a.loc["CCC", "sd"] == 1.0
    pop = zscore(Z, "2022", ddof=0).data
    assert pop[pop["indicator"] == "A|z"]["value"].tolist() == pytest.approx([-math.sqrt(1.5), 0.0, math.sqrt(1.5)])
    validate_frame(res.data)


def test_zscore_reference_invert_composite():
    ref = zscore(Z, "2022", reference=["AAA", "BBB"]).data.set_index(["indicator", "iso3"])
    # reference mean 1.5, sd(ddof=1) = sqrt(0.5)
    assert ref.loc[("A|z", "CCC"), "value"] == pytest.approx(1.5 / math.sqrt(0.5))
    res = zscore(Z, "2022", invert=["B"], composite=True, focus="BBB")
    d = res.data.set_index(["indicator", "iso3"])
    # B: mean 20, sd 10 -> z = -1, 1, 0, inverted (lower days = better) -> 1, -1, 0
    assert d.loc[("B|z", "AAA"), "value"] == pytest.approx(1.0)
    assert d.loc[("B|z", "BBB"), "value"] == pytest.approx(-1.0)
    assert d.loc[("B|z", "AAA"), "unit"].startswith("z-score (sign inverted")
    # composite = mean of the two z-scores
    assert d.loc[("zscore_composite", "AAA"), "value"] == pytest.approx(0.0)
    assert d.loc[("zscore_composite", "BBB"), "value"] == pytest.approx(-0.5)
    assert d.loc[("zscore_composite", "CCC"), "value"] == pytest.approx(0.5)
    assert d.loc[("zscore_composite", "CCC"), "n_components"] == 2
    assert bool(d.loc[("zscore_composite", "BBB"), "is_focus"])
    validate_frame(res.data)


def test_zscore_zero_variance():
    res = zscore(frame("A", [("AAA", "2022", 1.0), ("BBB", "2022", 1.0)]))
    assert res.data["value"].isna().all()
    assert "zscore_zero_variance" in {c.code for c in res.caveats}


# ---------------------------------------------------------------------------
# distance_to_frontier
# ---------------------------------------------------------------------------
D = frame("SCORE", [("AAA", "2024", 50.0), ("BBB", "2024", 80.0), ("CCC", "2024", 100.0), ("EAS", "2024", 120.0)])


def test_distance_to_max_frontier():
    res = distance_to_frontier(D, "max", focus="BBB")
    d = res.data.set_index("iso3")
    assert "EAS" not in d.index
    assert d["gap"].to_dict() == {"AAA": 50.0, "BBB": 20.0, "CCC": 0.0}
    assert d["pct_of_frontier"].to_dict() == {"AAA": 50.0, "BBB": 80.0, "CCC": 100.0}
    assert d["score"].to_dict() == {"AAA": 0.0, "BBB": 60.0, "CCC": 100.0}
    assert (d["frontier_iso3"] == "CCC").all()
    assert d["value"].to_dict() == d["gap"].to_dict()
    assert set(res.data["indicator"]) == {"SCORE|dtf_gap"}
    assert res.meta["focus"]["values"][0]["score"] == 60.0
    validate_frame(res.data)


def test_distance_to_min_frontier_and_country_frontier():
    d = distance_to_frontier(D, "min", measure="score").data.set_index("iso3")
    assert d["gap"].to_dict() == {"AAA": 0.0, "BBB": 30.0, "CCC": 50.0}
    assert d["value"].to_dict() == {"AAA": 100.0, "BBB": 40.0, "CCC": 0.0}
    c = distance_to_frontier(D, "BBB", measure="pct").data.set_index("iso3")
    assert c["gap"].to_dict() == {"AAA": 30.0, "BBB": 0.0, "CCC": -20.0}
    assert c["value"].to_dict() == {"AAA": 62.5, "BBB": 100.0, "CCC": 125.0}
    assert c["unit"].iloc[0] == "% of frontier (BBB)"
    fixed = distance_to_frontier(D, "max", measure="score", worst=0.0).data.set_index("iso3")
    assert fixed["value"].to_dict() == {"AAA": 50.0, "BBB": 80.0, "CCC": 100.0}


def test_distance_to_missing_country_frontier():
    with pytest.raises(ValueError, match="frontier"):
        distance_to_frontier(D, "USA")
    with pytest.raises(ValueError):
        distance_to_frontier(D, "best")


# ---------------------------------------------------------------------------
# review fixes: PPP and unit guards in every cross-country statistic
# ---------------------------------------------------------------------------
PPP17 = "constant 2017 international $ (PPP 2017)"
PPP21 = "constant 2021 international $ (PPP 2021)"


def _mixed_ppp() -> pd.DataFrame:
    return pd.concat([frame("NY.GDP.PCAP.PP.KD", [("AAA", "2020", 1000.0), ("BBB", "2020", 2000.0)], unit=PPP17),
                      frame("NY.GDP.PCAP.PP.KD", [("CCC", "2020", 3000.0), ("DDD", "2020", 4000.0),
                                                  ("EEE", "2020", 5000.0)], unit=PPP21)], ignore_index=True)


STATS_CALLS = [
    (percentile_rank, {"iso3": "AAA", "period": "2020"}),
    (zscore, {"period": "2020"}),
    (kernel_density, {"period": "2020"}),
    (distance_to_frontier, {"period": "2020"}),
]


@pytest.mark.parametrize("fn, kwargs", STATS_CALLS, ids=[f.__name__ for f, _ in STATS_CALLS])
def test_cross_country_stats_raise_or_block_on_ppp_mix(fn, kwargs):
    with pytest.raises(PPPMixError):
        fn(_mixed_ppp(), **kwargs)
    res = fn(_mixed_ppp(), on_ppp_mix="block", **kwargs)
    assert res.blocked
    cav = next(c for c in res.caveats if c.code == "ppp_base_mix")
    assert cav.evidence["groups"] == [{"group": "NY.GDP.PCAP.PP.KD", "ppp_bases": ["2017", "2021"]}]
    assert "mixed_units" in {c.code for c in res.caveats}
    assert res.provenance[-1].params["on_ppp_mix"] == "block"


@pytest.mark.parametrize("fn, kwargs", STATS_CALLS, ids=[f.__name__ for f, _ in STATS_CALLS])
def test_cross_country_stats_warn_on_mixed_non_ppp_units_and_ignore_aggregates(fn, kwargs):
    labels = pd.concat([frame("X", [("AAA", "2020", 1.0), ("BBB", "2020", 2.0)], unit="% of GDP"),
                        frame("X", [("CCC", "2020", 3.0), ("DDD", "2020", 4.0)], unit="% of GNI")], ignore_index=True)
    res = fn(labels, **kwargs)
    cav = next(c for c in res.caveats if c.code == "mixed_units")
    assert cav.severity == "warn" and cav.evidence["groups"] == [{"group": "X", "units": ["% of GDP", "% of GNI"]}]
    # an aggregate on another PPP base is dropped before the check (aggregates="drop")
    clean = pd.concat([frame("Y", [("AAA", "2020", 1.0), ("BBB", "2020", 2.0), ("CCC", "2020", 4.0)], unit=PPP21),
                       frame("Y", [("WLD", "2020", 9.0)], unit=PPP17)], ignore_index=True)
    ok = fn(clean, **kwargs)
    assert not ok.blocked and not {"ppp_base_mix", "mixed_units"} & {c.code for c in ok.caveats}


def test_income_conditional_fit_keeps_aggregate_codes_of_both_axes():
    x = Result(data=X3, meta={"aggregates": ["XAG"]})
    y = Result(data=Y3, meta={"aggregates": ["XBG"]})
    res = income_conditional_fit(x, y, "2020")
    assert {"XAG", "XBG"} <= set(res.meta["aggregates"])
