"""Unit tests for cgjrkit.methods.structural_change.

All inputs are synthetic (round numbers, fake ISO3 codes AAA/BBB/...) and
every expectation is computed by hand in the comments. Shift-share examples
use dyadic shares (0.5, 0.25, 0.75) so the identities hold exactly in
floating point.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import COLUMNS
from cgjrkit.methods.harmonize import PPPMixError
from cgjrkit.methods.structural_change import (
    ShiftShareIdentityError,
    StructuralChangeError,
    annualize_factor,
    beta_convergence,
    decompose_productivity,
    sector_contributions,
    sector_panel,
    shift_share,
    va_per_worker,
)

VA_MAP = {"VA_A": "Agriculture", "VA_B": "Services"}
EMP_MAP = {"EMP_A": "Agriculture", "EMP_B": "Services"}


def frame(rows, unit="", **defaults) -> pd.DataFrame:
    """rows: (iso3, period, indicator, value[, obs_status])."""
    base = {"source": "synthetic", "dataset": "TEST", "indicator_label": "", "country": "", "unit": unit,
            "obs_status": ""}
    base.update(defaults)
    out = []
    for r in rows:
        rec = dict(base, iso3=r[0], period=r[1], indicator=r[2], value=r[3])
        if len(r) > 4:
            rec["obs_status"] = r[4]
        out.append(rec)
    return pd.DataFrame(out)


def two_sector(iso3="AAA", emp_status=""):
    """Start 2010: A VA 100 L 50 (y=2), B VA 300 L 50 (y=6) -> Y0 = 4, theta0 = (0.5, 0.5).
    End 2020: A VA 75 L 25 (y=3), B VA 600 L 75 (y=8) -> YT = 6.75, thetaT = (0.25, 0.75). dY = 2.75."""
    va = frame([(iso3, "2010", "VA_A", 100), (iso3, "2010", "VA_B", 300),
                (iso3, "2020", "VA_A", 75), (iso3, "2020", "VA_B", 600)], unit="constant 2015 LCU")
    emp = frame([(iso3, "2010", "EMP_A", 50, emp_status), (iso3, "2010", "EMP_B", 50, emp_status),
                 (iso3, "2020", "EMP_A", 25, emp_status), (iso3, "2020", "EMP_B", 75, emp_status)], unit="persons")
    return va, emp


def pick(res: Result, indicator: str, iso3: str = "AAA", column: str = "value") -> float:
    hit = res.data[(res.data["indicator"] == indicator) & (res.data["iso3"] == iso3)]
    assert len(hit) == 1, f"{indicator} for {iso3}: {len(hit)} rows"
    return float(hit[column].iloc[0])


# ---------------------------------------------------------------------------
# sector_panel
# ---------------------------------------------------------------------------
def test_sector_panel_concordance_sums_and_requires_all_parts():
    df = frame([("AAA", "2010", "C", 10), ("AAA", "2010", "D", 5), ("AAA", "2010", "G", 7),
                ("AAA", "2011", "C", 11), ("AAA", "2011", "G", 8)], unit="constant LCU")
    _, panel, order = sector_panel(df, {"C": "Industry", "D": "Industry", "G": "Services"})
    assert order == ["Industry", "Services"]
    p = panel.set_index(["period", "sector"])["value"]
    assert p[("2010", "Industry")] == 15.0          # C + D
    assert math.isnan(p[("2011", "Industry")])      # D missing in 2011 -> no partial sum
    assert p[("2011", "Services")] == 8.0


def test_sector_panel_uses_sector_column_or_indicator():
    df = frame([("AAA", "2010", "EMP", 1), ("AAA", "2010", "EMP", 2)])
    df["sector"] = ["Agr", "Srv"]
    _, panel, order = sector_panel(df)
    assert order == ["Agr", "Srv"] and list(panel["value"]) == [1.0, 2.0]
    _, panel2, order2 = sector_panel(frame([("AAA", "2010", "X", 1), ("AAA", "2010", "Y", 2)]))
    assert order2 == ["X", "Y"]


def test_sector_panel_rejects_duplicates_and_ppp_mix():
    dup = frame([("AAA", "2010", "VA_A", 1), ("AAA", "2010", "VA_A", 2)])
    with pytest.raises(StructuralChangeError):
        sector_panel(dup, VA_MAP)
    mix = pd.concat([frame([("AAA", "2010", "VA_A", 1)], unit="constant 2017 international $ (PPP 2017)"),
                     frame([("AAA", "2010", "VA_B", 1)], unit="constant 2021 international $ (PPP 2021)")])
    with pytest.raises(PPPMixError):
        sector_panel(mix, VA_MAP)
    with pytest.raises(StructuralChangeError):
        sector_panel(frame([("AAA", "2010", "Z", 1)]), VA_MAP)


# ---------------------------------------------------------------------------
# va_per_worker
# ---------------------------------------------------------------------------
def test_va_per_worker_values_total_and_scale():
    va, emp = two_sector()
    res = va_per_worker(va, emp, va_sectors=VA_MAP, emp_sectors=EMP_MAP)
    d = res.data.set_index(["period", "sector"])["value"]
    assert d[("2010", "Agriculture")] == 2.0 and d[("2010", "Services")] == 6.0
    assert d[("2010", "TOTAL")] == 4.0 and d[("2020", "TOTAL")] == 6.75
    assert set(res.data["unit"]) == {"constant 2015 LCU per worker"}
    assert list(res.data.columns[: len(COLUMNS)]) == COLUMNS
    assert res.provenance[-1].tool == "structural_change.va_per_worker"
    # employment in thousands
    emp_k = emp.assign(value=emp["value"] / 1000.0)
    res_k = va_per_worker(va, emp_k, va_sectors=VA_MAP, emp_sectors=EMP_MAP, emp_scale=1000.0)
    assert np.allclose(res_k.data["value"], res.data["value"])


def test_va_per_worker_caveats_unmatched_current_prices_and_zero_employment():
    va, emp = two_sector()
    va = va.assign(unit="current LCU")
    emp = emp[~((emp["indicator"] == "EMP_B") & (emp["period"] == "2020"))].copy()
    emp.loc[(emp["indicator"] == "EMP_A") & (emp["period"] == "2010"), "value"] = 0.0
    res = va_per_worker(va, emp, va_sectors=VA_MAP, emp_sectors=EMP_MAP)
    codes = {c.code for c in res.caveats}
    assert {"va_per_worker_unmatched", "va_current_prices", "nonpositive_employment"} <= codes
    d = res.data.set_index(["period", "sector"])["value"]
    assert math.isnan(d[("2010", "Agriculture")])
    assert ("2020", "TOTAL") not in d.index  # Services 2020 has no employment -> no total


def test_va_per_worker_keeps_input_provenance_and_flags_modelled():
    va, emp = two_sector(emp_status="modelled")
    emp_res = Result(data=emp, provenance=[Provenance(source="ilostat", dataset="ILOEST")])
    res = va_per_worker(va, emp_res, va_sectors=VA_MAP, emp_sectors=EMP_MAP)
    assert res.provenance[0].source == "ilostat"
    assert "modelled_inputs" in {c.code for c in res.caveats}
    assert set(res.data["obs_status"]) == {"modelled"}


# ---------------------------------------------------------------------------
# shift_share
# ---------------------------------------------------------------------------
def test_shift_share_two_term_hand_values_exact():
    va, emp = two_sector()
    res = shift_share(va, emp, "2010", "2020", "two_term", va_sectors=VA_MAP, emp_sectors=EMP_MAP, annualize="none")
    lev = lambda ind: pick(res, ind, column="level_change")  # noqa: E731
    # within_i = theta_i0 * dy_i: 0.5*1 = 0.5 (A), 0.5*2 = 1.0 (B)
    assert lev("shift_share.within|Agriculture") == 0.5
    assert lev("shift_share.within|Services") == 1.0
    # between_i = y_iT * dtheta_i: 3*(-0.25) = -0.75 (A), 8*0.25 = 2.0 (B)
    assert lev("shift_share.between|Agriculture") == -0.75
    assert lev("shift_share.between|Services") == 2.0
    assert lev("shift_share.within|TOTAL") == 1.5 and lev("shift_share.between|TOTAL") == 1.25
    assert lev("shift_share.total|TOTAL") == 2.75
    # percent of initial productivity (Y0 = 4): within 37.5, between 31.25, total 68.75
    assert pick(res, "shift_share.within|TOTAL") == 37.5
    assert pick(res, "shift_share.between|TOTAL") == 31.25
    assert pick(res, "shift_share.total|TOTAL") == 68.75
    assert pick(res, "shift_share.within|TOTAL", column="share_of_total") == pytest.approx(100 * 1.5 / 2.75)
    m = res.meta["shift_share"]["AAA"]
    assert m["identity_abs_error"] == 0.0 and m["Y0"] == 4.0 and m["YT"] == 6.75
    assert m["employment_shares"]["end"] == {"Agriculture": 0.25, "Services": 0.75}


def test_shift_share_three_term_and_shapley_hand_values():
    va, emp = two_sector()
    r3 = shift_share(va, emp, "2010", "2020", "three_term", va_sectors=VA_MAP, emp_sectors=EMP_MAP, annualize="none")
    lev = lambda r, ind: pick(r, ind, column="level_change")  # noqa: E731
    # static_i = y_i0 * dtheta_i: 2*(-0.25) = -0.5, 6*0.25 = 1.5 ; dynamic_i = dy_i * dtheta_i: -0.25, 0.5
    assert lev(r3, "shift_share.within|TOTAL") == 1.5
    assert lev(r3, "shift_share.static_between|Agriculture") == -0.5
    assert lev(r3, "shift_share.static_between|Services") == 1.5
    assert lev(r3, "shift_share.dynamic_between|Agriculture") == -0.25
    assert lev(r3, "shift_share.dynamic_between|Services") == 0.5
    assert lev(r3, "shift_share.within|TOTAL") + lev(r3, "shift_share.static_between|TOTAL") \
        + lev(r3, "shift_share.dynamic_between|TOTAL") == lev(r3, "shift_share.total|TOTAL") == 2.75
    rs = shift_share(va, emp, "2010", "2020", "shapley", va_sectors=VA_MAP, emp_sectors=EMP_MAP, annualize="none")
    # within_i = mean(theta_i) * dy_i: 0.375*1, 0.625*2 ; between_i = mean(y_i) * dtheta_i: 2.5*(-0.25), 7*0.25
    assert lev(rs, "shift_share.within|Agriculture") == 0.375
    assert lev(rs, "shift_share.within|Services") == 1.25
    assert lev(rs, "shift_share.between|Agriculture") == -0.625
    assert lev(rs, "shift_share.between|Services") == 1.75
    assert lev(rs, "shift_share.within|TOTAL") + lev(rs, "shift_share.between|TOTAL") == 2.75


@pytest.mark.parametrize("annualize, total", [
    ("simple", 68.75 / 10),                                 # cumulative % / years
    ("cagr", 100 * ((6.75 / 4) ** (1 / 10) - 1)),           # CAGR of Y
    ("log", 100 * math.log(6.75 / 4) / 10),                 # log growth per year
])
def test_shift_share_annualized_components_sum_to_total(annualize, total):
    va, emp = two_sector()
    res = shift_share(va, emp, "2010", "2020", "two_term", va_sectors=VA_MAP, emp_sectors=EMP_MAP, annualize=annualize)
    assert pick(res, "shift_share.total|TOTAL") == pytest.approx(total, rel=1e-12)
    w, b = pick(res, "shift_share.within|TOTAL"), pick(res, "shift_share.between|TOTAL")
    assert w + b == pytest.approx(total, rel=1e-12)
    assert w == pytest.approx(total * 1.5 / 2.75, rel=1e-12)  # same proportional scaling for every component
    sectors = res.data[(res.data["sector"] != "TOTAL")]
    assert sectors["value"].sum() == pytest.approx(total, rel=1e-12)


def test_annualize_factor_zero_change_and_errors():
    assert annualize_factor(4.0, 4.0, 10, "cagr") == pytest.approx(100 / 40)
    with pytest.raises(ValueError):
        annualize_factor(4.0, 5.0, 10, "geometric")
    with pytest.raises(StructuralChangeError):
        annualize_factor(0.0, 5.0, 10, "none")


def test_shift_share_aggregate_gap_raises_or_caveats():
    va, emp = two_sector()
    # GDP = sector VA + net taxes of 40 (start) and 75 (end); total employment equals the sector sum
    gdp = frame([("AAA", "2010", "GDP", 440), ("AAA", "2020", "GDP", 750)], unit="constant 2015 LCU")
    with pytest.raises(ShiftShareIdentityError):
        shift_share(va, emp, "2010", "2020", va_sectors=VA_MAP, emp_sectors=EMP_MAP, aggregate_va=gdp)
    res = shift_share(va, emp, "2010", "2020", va_sectors=VA_MAP, emp_sectors=EMP_MAP, aggregate_va=gdp,
                      on_gap="caveat", annualize="none")
    cav = next(c for c in res.caveats if c.code == "shift_share_identity_gap")
    gap = cav.evidence["gaps"][0]
    # aggregate: 4.4 -> 7.5 (dY 3.1); sectors: 4 -> 6.75 (dY 2.75); gap 0.35
    assert gap["aggregate_delta_y"] == pytest.approx(3.1) and gap["gap"] == pytest.approx(0.35)
    assert gap["va_coverage"] == pytest.approx([400 / 440, 675 / 750])
    assert res.meta["shift_share"]["AAA"]["aggregate_check"]["exact"] is False
    # aggregates that equal the sector sums pass silently
    tot = frame([("AAA", "2010", "GDP", 400), ("AAA", "2020", "GDP", 675)])
    ok = shift_share(va, emp, "2010", "2020", va_sectors=VA_MAP, emp_sectors=EMP_MAP, aggregate_va=tot,
                     aggregate_emp=frame([("AAA", "2010", "E", 100), ("AAA", "2020", "E", 100)]))
    assert ok.meta["shift_share"]["AAA"]["aggregate_check"]["exact"] is True
    assert "shift_share_identity_gap" not in {c.code for c in ok.caveats}


def test_shift_share_missing_sector_and_zero_employment():
    va, emp = two_sector()
    with pytest.raises(StructuralChangeError, match="Services"):
        shift_share(va[~((va["indicator"] == "VA_B") & (va["period"] == "2020"))], emp, "2010", "2020",
                    va_sectors=VA_MAP, emp_sectors=EMP_MAP)
    emp0 = emp.copy()
    emp0.loc[emp0.index[0], "value"] = 0.0
    with pytest.raises(StructuralChangeError, match="non-positive"):
        shift_share(va, emp0, "2010", "2020", va_sectors=VA_MAP, emp_sectors=EMP_MAP)
    with pytest.raises(ValueError):
        shift_share(va, emp, "2020", "2010", va_sectors=VA_MAP, emp_sectors=EMP_MAP)
    with pytest.raises(ValueError):
        shift_share(va, emp, "2010", "2020", "four_term", va_sectors=VA_MAP, emp_sectors=EMP_MAP)


def test_shift_share_several_countries_filter_and_caveats():
    va1, emp1 = two_sector("AAA", emp_status="modelled")
    va2, emp2 = two_sector("BBB")
    va2 = va2.assign(value=va2["value"] * 2)  # productivity doubles everywhere: same shares of total
    res = shift_share(pd.concat([va1, va2]), pd.concat([emp1, emp2]), "2010", "2020", va_sectors=VA_MAP,
                      emp_sectors=EMP_MAP, annualize="none")
    assert set(res.data["iso3"]) == {"AAA", "BBB"}
    assert pick(res, "shift_share.total|TOTAL", "BBB", "level_change") == 5.5
    assert pick(res, "shift_share.total|TOTAL", "BBB") == 68.75
    codes = {c.code for c in res.caveats}
    assert "modelled_inputs" in codes and "covid_endpoint" in codes
    only = shift_share(pd.concat([va1, va2]), pd.concat([emp1, emp2]), "2010", "2020", va_sectors=VA_MAP,
                       emp_sectors=EMP_MAP, iso3=["bbb"])
    assert set(only.data["iso3"]) == {"BBB"}
    with pytest.raises(StructuralChangeError):
        shift_share(va1, emp1, "2010", "2020", va_sectors=VA_MAP, emp_sectors=EMP_MAP, iso3=["CCC"])


def test_shift_share_three_sectors_identity_with_nondyadic_values():
    """Kenya Fig 35 layout (3 sectors, 2011-2019): components sum to the CAGR of VA per worker."""
    va = frame([("KEN", "2011", "VA_A", 1234.5), ("KEN", "2011", "VA_I", 987.6), ("KEN", "2011", "VA_S", 2345.6),
                ("KEN", "2019", "VA_A", 1500.1), ("KEN", "2019", "VA_I", 1300.3), ("KEN", "2019", "VA_S", 3900.7)])
    emp = frame([("KEN", "2011", "E_A", 7000), ("KEN", "2011", "E_I", 1300), ("KEN", "2011", "E_S", 4700),
                 ("KEN", "2019", "E_A", 7400), ("KEN", "2019", "E_I", 1800), ("KEN", "2019", "E_S", 7100)])
    vm = {"VA_A": "Agriculture", "VA_I": "Industry", "VA_S": "Services"}
    em = {"E_A": "Agriculture", "E_I": "Industry", "E_S": "Services"}
    for variant in ("two_term", "three_term", "shapley"):
        res = shift_share(va, emp, "2011", "2019", variant, va_sectors=vm, emp_sectors=em)
        y0 = (1234.5 + 987.6 + 2345.6) / 13000
        yT = (1500.1 + 1300.3 + 3900.7) / 16300
        cagr = 100 * ((yT / y0) ** (1 / 8) - 1)
        assert pick(res, "shift_share.total|TOTAL", "KEN") == pytest.approx(cagr, rel=1e-12)
        parts = res.data[(res.data["sector"] != "TOTAL")]["value"].sum()
        assert parts == pytest.approx(cagr, rel=1e-12)
        assert res.meta["shift_share"]["KEN"]["identity_abs_error"] < 1e-12


def test_decompose_productivity_pure_function():
    d = decompose_productivity({"A": 100, "B": 300}, {"A": 75, "B": 600}, {"A": 50, "B": 50}, {"A": 25, "B": 75})
    assert d["terms"]["within"] == {"A": 0.5, "B": 1.0}
    assert d["sum_components"] == d["delta_y"] == 2.75
    with pytest.raises(StructuralChangeError):
        decompose_productivity({"A": 1}, {"A": 1}, {"A": 1, "B": 1}, {"A": 1, "B": 1})


# ---------------------------------------------------------------------------
# sector_contributions
# ---------------------------------------------------------------------------
def contrib_frame():
    """A: 100, 110, 121, 121 ; B: 100, 100, 110, 121 (2000-2003). Total 200, 210, 231, 242."""
    rows = []
    for y, a, b in zip(range(2000, 2004), (100, 110, 121, 121), (100, 100, 110, 121)):
        rows += [("AAA", str(y), "VA_A", a), ("AAA", str(y), "VA_B", b)]
    return frame(rows, unit="constant 2015 LCU")


def contrib(res, sector, window, column="value"):
    hit = res.data[(res.data["sector"] == sector) & (res.data["window"] == window)]
    assert len(hit) == 1
    return float(hit[column].iloc[0])


def test_sector_contributions_exact_method_is_additive():
    res = sector_contributions(contrib_frame(), {"2001-03": ("2001", "2003")}, sectors=VA_MAP, method="exact")
    # mean(s_{t-1} * g_t): A = (0.5*10 + 110/210*10 + 121/231*0)/3 ; B = (0 + 100/210*10 + 110/231*10)/3
    a = (0.5 * 10 + 110 / 210 * 10 + 0) / 3
    b = (0 + 100 / 210 * 10 + 110 / 231 * 10) / 3
    g = (5 + 10 + 100 * (242 / 231 - 1)) / 3
    assert contrib(res, "Agriculture", "2001-03") == pytest.approx(a, rel=1e-12)
    assert contrib(res, "Services", "2001-03") == pytest.approx(b, rel=1e-12)
    assert contrib(res, "TOTAL", "2001-03") == pytest.approx(g, rel=1e-12)
    assert a + b == pytest.approx(g, rel=1e-12)
    assert abs(contrib(res, "RESIDUAL", "2001-03")) < 1e-12
    assert "contributions_do_not_sum" not in {c.code for c in res.caveats}
    assert res.provenance[-1].tool == "structural_change.sector_contributions"


def test_sector_contributions_avg_growth_times_avg_share_and_flag():
    periods = {"2001-03": ("2001", "2003"), "2003": "2003"}
    res = sector_contributions(contrib_frame(), periods, sectors=VA_MAP)
    # mean growth A = B = 20/3 ; mean current shares A = (110/210 + 121/231 + 0.5)/3
    sa = (110 / 210 + 121 / 231 + 0.5) / 3
    assert contrib(res, "Agriculture", "2001-03") == pytest.approx(20 / 3 * sa, rel=1e-12)
    assert contrib(res, "Agriculture", "2001-03", "avg_share") == pytest.approx(100 * sa, rel=1e-12)
    assert contrib(res, "Agriculture", "2001-03", "avg_growth") == pytest.approx(20 / 3, rel=1e-12)
    g = (5 + 10 + 100 * (242 / 231 - 1)) / 3
    assert contrib(res, "RESIDUAL", "2001-03") == pytest.approx(g - 20 / 3, rel=1e-12)  # -0.079 pp
    # single year 2003 with current shares: 0*0.5 + 10*0.5 = 5 pp vs total growth 4.76% (the Kenya Fig 2 pattern)
    assert contrib(res, "Services", "2003") == pytest.approx(5.0)
    assert contrib(res, "TOTAL", "2003") == pytest.approx(100 * (242 / 231 - 1))
    cav = next(c for c in res.caveats if c.code == "contributions_do_not_sum")
    assert {w["window"] for w in cav.evidence["windows"]} == {"2001-03", "2003"}
    # lagged shares in 2003 (shares of 2002): B = 110/231*10 -> additive for a single year
    lag = sector_contributions(contrib_frame(), {"2003": "2003"}, sectors=VA_MAP, share_timing="lagged")
    assert contrib(lag, "Services", "2003") == pytest.approx(110 / 231 * 10)
    assert "contributions_do_not_sum" not in {c.code for c in lag.caveats}


def test_sector_contributions_total_frame_with_taxes_is_flagged():
    gdp = frame([("AAA", str(y), "GDP", v * 1.1) for y, v in zip(range(2000, 2004), (200, 210, 231, 242))])
    res = sector_contributions(contrib_frame(), {"2001-03": ("2001", "2003")}, sectors=VA_MAP, total=gdp,
                               method="exact")
    g = (5 + 10 + 100 * (242 / 231 - 1)) / 3
    # shares are of GDP (sum to 1/1.1), so contributions sum to g/1.1 and the residual is g*(1 - 1/1.1)
    assert contrib(res, "TOTAL", "2001-03") == pytest.approx(g, rel=1e-9)
    assert contrib(res, "RESIDUAL", "2001-03") == pytest.approx(g * (1 - 1 / 1.1), rel=1e-9)
    assert "contributions_do_not_sum" in {c.code for c in res.caveats}


def test_sector_contributions_total_as_sector_and_incomplete_window():
    df = contrib_frame()
    tot = frame([("AAA", str(y), "VA_T", v) for y, v in zip(range(2000, 2004), (200, 210, 231, 242))])
    res = sector_contributions(pd.concat([df, tot]), {"2001-03": ("2001", "2003")},
                               sectors={**VA_MAP, "VA_T": "Total"}, total="Total", method="exact")
    assert set(res.data["sector"]) == {"Agriculture", "Services", "TOTAL", "RESIDUAL"}
    assert abs(contrib(res, "RESIDUAL", "2001-03")) < 1e-12
    short = sector_contributions(df, {"1999-2003": ("1999", "2003")}, sectors=VA_MAP, method="exact")
    assert "contribution_window_incomplete" in {c.code for c in short.caveats}
    assert contrib(short, "Agriculture", "1999-2003", "n_years") == 3
    with pytest.raises(StructuralChangeError):
        sector_contributions(df, {"x": "2001"}, sectors=VA_MAP, total="Mining")


# ---------------------------------------------------------------------------
# beta_convergence
# ---------------------------------------------------------------------------
def levels(xs, gs, years=10, start=2000, ids=("AAA", "BBB", "CCC", "DDD")):
    """Units with ln(y0) = x and log growth g (% per year) over ``years``."""
    rows = []
    for iso, x, g in zip(ids, xs, gs):
        y0 = math.exp(x)
        rows += [(iso, str(start), "GDPPC", y0), (iso, str(start + years), "GDPPC", y0 * math.exp(g * years / 100))]
    return frame(rows, unit="constant 2021 international $ (PPP 2021)")


def test_beta_convergence_perfect_fit_and_speed():
    res = beta_convergence(levels([0, 1, 2], [3, 2, 1]), "2000", "2010")
    reg = res.meta["beta_convergence"]
    assert reg["coef"]["log_initial"] == pytest.approx(-1.0, abs=1e-9)
    assert reg["coef"]["const"] == pytest.approx(3.0, abs=1e-9)
    assert reg["r2"] == pytest.approx(1.0, abs=1e-12)
    lam = -math.log(1 - 0.01 * 10) / 10
    assert reg["speed_lambda"] == pytest.approx(lam, rel=1e-9)
    assert reg["half_life_years"] == pytest.approx(math.log(2) / lam, rel=1e-9)
    assert reg["converging"] is True
    assert list(res.data["value"]) == pytest.approx([3, 2, 1])
    assert "convergence_small_sample" in {c.code for c in res.caveats}


def test_beta_convergence_ols_standard_errors_by_hand():
    # x = 0..3, g = 3, 2.5, 1, 0.5: Sxx = 5, Sxg = -4.5 -> beta = -0.9, alpha = 3.1
    # residuals -0.1, 0.3, -0.3, 0.1 -> RSS 0.2, sigma2 = 0.1, se(beta) = sqrt(0.1/5); TSS 4.25
    res = beta_convergence(levels([0, 1, 2, 3], [3, 2.5, 1, 0.5]), "2000", "2010")
    reg = res.meta["beta_convergence"]
    assert reg["coef"]["log_initial"] == pytest.approx(-0.9, rel=1e-9)
    assert reg["coef"]["const"] == pytest.approx(3.1, rel=1e-9)
    assert reg["se"]["log_initial"] == pytest.approx(math.sqrt(0.1 / 5), rel=1e-9)
    assert reg["r2"] == pytest.approx(1 - 0.2 / 4.25, rel=1e-9)
    assert reg["n"] == 4 and reg["dof"] == 2
    assert list(res.data["residual"]) == pytest.approx([-0.1, 0.3, -0.3, 0.1], abs=1e-9)
    # HC1 robust: (X'X)^-1 X' diag(e^2) X (X'X)^-1 * n/(n-k)
    rob = beta_convergence(levels([0, 1, 2, 3], [3, 2.5, 1, 0.5]), "2000", "2010", robust=True)
    X = np.column_stack([np.ones(4), np.arange(4.0)])
    e = np.array([-0.1, 0.3, -0.3, 0.1])
    inv = np.linalg.inv(X.T @ X)
    cov = inv @ (X.T * e ** 2) @ X @ inv * 4 / 2
    assert rob.meta["beta_convergence"]["se"]["log_initial"] == pytest.approx(math.sqrt(cov[1, 1]), rel=1e-9)


def test_beta_convergence_spells_with_period_effects_and_controls():
    rows = []
    for iso, x in zip(("AAA", "BBB", "CCC", "DDD"), (0.0, 1.0, 2.0, 3.0)):
        y0 = math.exp(x)
        y5 = y0 * math.exp((3 - x) * 5 / 100)                 # spell 1: g = 3 - ln y
        y10 = y5 * math.exp((4 - math.log(y5)) * 5 / 100)     # spell 2: g = 4 - ln y (shift +1)
        rows += [(iso, "2000", "Y", y0), (iso, "2005", "Y", y5), (iso, "2010", "Y", y10)]
    res = beta_convergence(frame(rows), "2000", "2010", spell=5)
    reg = res.meta["beta_convergence"]
    assert reg["n"] == 8 and reg["spells"] == [["2000", "2005"], ["2005", "2010"]]
    assert reg["coef"]["log_initial"] == pytest.approx(-1.0, abs=1e-9)
    assert reg["coef"]["spell_2005"] == pytest.approx(1.0, abs=1e-9)
    assert set(res.data["period"]) == {"2000-2005", "2005-2010"}
    # conditional convergence: a control that explains the intercept differences
    data = levels([0, 1, 2, 3], [3.5, 2.0, 1.5, 0.0])
    ctrl = pd.DataFrame({"iso3": ["AAA", "BBB", "CCC", "DDD"], "z": [1.0, 0.0, 1.0, 0.0]})
    cond = beta_convergence(data, "2000", "2010", controls=ctrl)
    c = cond.meta["beta_convergence"]["coef"]
    # g = 3 - 1*x + 0.5*z exactly: (3.5, 2.0, 1.5, 0.0) with z = (1, 0, 1, 0)
    assert c["log_initial"] == pytest.approx(-1.0, abs=1e-9) and c["z"] == pytest.approx(0.5, abs=1e-9)


def test_beta_convergence_errors_and_skips():
    data = levels([0, 1, 2, 3], [3, 2.5, 1, 0.5])
    data.loc[data.index[-1], "value"] = -1.0
    res = beta_convergence(data, "2000", "2010")
    assert res.meta["beta_convergence"]["n"] == 3
    assert "convergence_units_skipped" in {c.code for c in res.caveats}
    with pytest.raises(StructuralChangeError):
        beta_convergence(levels([0, 1], [1, 2]), "2000", "2010")  # 2 obs, 2 params
    with pytest.raises(ValueError):
        beta_convergence(data, "2000", "2010", spell=20)
    divergent = beta_convergence(levels([0, 1, 2], [1, 2, 3]), "2000", "2010")
    assert divergent.meta["beta_convergence"]["speed_lambda"] is None


# ---------------------------------------------------------------------------
# review fix: calendar and fiscal years are never aligned silently
# ---------------------------------------------------------------------------
def _fy(y: int) -> str:
    return f"FY{y}/{(y + 1) % 100:02d}"


def _va(period_of=str) -> pd.DataFrame:
    rows = []
    for y in range(2010, 2014):
        rows.append(dict(source="s", indicator="AGR", iso3="AAA", period=period_of(y), value=100.0 * 1.1 ** (y - 2010)))
        rows.append(dict(source="s", indicator="IND", iso3="AAA", period=period_of(y), value=100.0))
    return pd.DataFrame(rows)


def test_sector_contributions_refuses_fiscal_share_frame_and_mixed_panels():
    from cgjrkit.methods.harmonize import FrequencyMismatchError

    with pytest.raises(FrequencyMismatchError, match="share_frame"):
        sector_contributions(_va(), {"w": ("2011", "2013")}, share_frame=_va(_fy))
    mixed = pd.concat([_va()[lambda d: d["period"] != "2013"], _va(_fy)[lambda d: d["period"] == "FY2013/14"]])
    with pytest.raises(FrequencyMismatchError, match="mixes calendar-year"):
        sector_contributions(mixed, {"w": ("2011", "2013")})
    with pytest.raises(FrequencyMismatchError, match="window 'w'"):
        sector_contributions(_va(_fy), {"w": ("2011", "2013")})


def test_sector_contributions_all_fiscal_inputs_work():
    # AGR grows 10% a year, IND flat: exact contributions with lagged shares; FY windows on FY data
    res = sector_contributions(_va(_fy), {"w": ("FY2011/12", "FY2011/12")}, method="exact")
    v = dict(zip(res.data["sector"], res.data["value"]))
    # FY2011/12: AGR share in FY2010/11 = 100/200 = 50%, growth 10% -> 5 pp; total 110+100 vs 200 -> 5%
    assert v["AGR"] == pytest.approx(5.0) and v["IND"] == pytest.approx(0.0) and v["TOTAL"] == pytest.approx(5.0)
    assert v["RESIDUAL"] == pytest.approx(0.0, abs=1e-12)
