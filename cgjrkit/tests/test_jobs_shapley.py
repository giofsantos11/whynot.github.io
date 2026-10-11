"""Unit tests for cgjrkit.methods.jobs_shapley (synthetic values, hand-computed Shapley values).

Base case (fake country AAA, 2010 -> 2020):

=================  ======  ======
                     2010    2020
=================  ======  ======
GDP                   500    1500
population           1000    2000
working-age pop.      500     500
employment            250     375
-----------------  ------  ------
a = GDP / E             2       4
b = E / WAP           0.5    0.75
c = WAP / P           0.5    0.25
y = GDP / P           0.5    0.75
=================  ======  ======

Shapley multipliers (average over the 6 orderings):
m_a = b0c0/3 + (bTc0 + b0cT)/6 + bTcT/3 = 11/48, so phi_a = 2 * 11/48 = 11/24;
m_b = 13/12, phi_b = 0.25 * 13/12 = 13/48; m_c = 23/12, phi_c = -0.25 * 23/12 = -23/48.
phi_a + phi_b + phi_c = 12/48 = 0.25 = y_T - y_0.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import COLUMNS
from cgjrkit.methods.jobs_shapley import (
    JobsShapleyError,
    jobs_shapley,
    shapley_multipliers,
    shapley_product,
)

WINDOW = {"2010-20": ("2010", "2020")}


def series(indicator, values, iso3="AAA", unit="", status=""):
    return pd.DataFrame([{"source": "synthetic", "dataset": "TEST", "indicator": indicator, "indicator_label": "",
                          "iso3": iso3, "country": "Testland", "period": p, "value": v, "unit": unit,
                          "obs_status": status} for p, v in values.items()])


def base(emp_scale=1.0, emp_status=""):
    gdp = series("GDP", {"2010": 500, "2020": 1500}, unit="constant 2015 LCU")
    pop = series("POP", {"2010": 1000, "2020": 2000})
    wap = series("WAP", {"2010": 500, "2020": 500})
    emp = series("EMP", {"2010": 250 / emp_scale, "2020": 375 / emp_scale}, status=emp_status)
    return gdp, pop, wap, emp


def sectors(va_extra=0.0):
    """2010: A VA 125 E 125, B VA 375 E 125 ; 2020: A VA 250 E 125, B VA 1250 E 250 (sums = GDP, E)."""
    va = pd.concat([series("VA_A", {"2010": 125, "2020": 250}),
                    series("VA_B", {"2010": 375 - va_extra, "2020": 1250 - va_extra})])
    es = pd.concat([series("E_A", {"2010": 125, "2020": 125}), series("E_B", {"2010": 125, "2020": 250})])
    return va, es


SECTOR_KW = {"va_sectors": {"VA_A": "Agriculture", "VA_B": "Services"},
             "emp_sectors": {"E_A": "Agriculture", "E_B": "Services"}}


def row(res, component, sector="TOTAL", column="value"):
    hit = res.data[(res.data["component"] == component) & (res.data["sector"] == sector)]
    assert len(hit) == 1, (component, sector, len(hit))
    return float(hit[column].iloc[0])


# ---------------------------------------------------------------------------
# Shapley arithmetic
# ---------------------------------------------------------------------------
def test_shapley_product_three_factors_by_hand():
    phi = shapley_product([2, 0.5, 0.5], [4, 0.75, 0.25])
    assert phi == pytest.approx([11 / 24, 13 / 48, -23 / 48], rel=1e-15)
    assert sum(phi) == pytest.approx(4 * 0.75 * 0.25 - 2 * 0.5 * 0.5, rel=1e-15)
    assert shapley_multipliers([2, 0.5, 0.5], [4, 0.75, 0.25]) == pytest.approx([11 / 48, 13 / 12, 23 / 12])


def test_shapley_two_factors_is_midpoint_and_n_factors_exact():
    # two factors: m_1 = (x2_0 + x2_T)/2
    assert shapley_multipliers([3, 5], [4, 9]) == pytest.approx([7.0, 3.5])
    x0, xT = [1.1, 2.3, 0.7, 1.9, 3.1], [1.4, 2.0, 0.9, 2.2, 2.5]
    assert sum(shapley_product(x0, xT)) == pytest.approx(math.prod(xT) - math.prod(x0), rel=1e-12)
    assert shapley_product([2.0], [5.0]) == [3.0]
    with pytest.raises(ValueError):
        shapley_multipliers([1] * 9, [2] * 9)
    with pytest.raises(ValueError):
        shapley_multipliers([1, 2], [1])


# ---------------------------------------------------------------------------
# jobs_shapley
# ---------------------------------------------------------------------------
def test_jobs_shapley_cumulative_percent_and_shares():
    res = jobs_shapley(*base(), WINDOW, annualize="none")
    assert list(res.data.columns[: len(COLUMNS)]) == COLUMNS
    # percent of initial GDP per capita (0.5): phi / 0.5 * 100
    assert row(res, "gdp_per_capita") == pytest.approx(50.0, rel=1e-12)
    assert row(res, "output_per_worker") == pytest.approx(100 * (11 / 24) / 0.5, rel=1e-12)
    assert row(res, "employment_rate") == pytest.approx(100 * (13 / 48) / 0.5, rel=1e-12)
    assert row(res, "working_age_share") == pytest.approx(100 * (-23 / 48) / 0.5, rel=1e-12)
    assert row(res, "output_per_worker", column="level_change") == pytest.approx(11 / 24, rel=1e-12)
    assert row(res, "output_per_worker", column="share_of_total") == pytest.approx(100 * (11 / 24) / 0.25, rel=1e-12)
    factors = res.data[res.data["component"].isin(["output_per_worker", "employment_rate", "working_age_share"])]
    assert factors["value"].sum() == pytest.approx(row(res, "gdp_per_capita"), rel=1e-12)
    assert factors["share_of_total"].sum() == pytest.approx(100.0, rel=1e-12)
    assert row(res, "employment_rate", column="factor_start") == 0.5
    assert row(res, "working_age_share", column="factor_end") == 0.25
    m = res.meta["jobs_shapley"]["AAA"]["2010-20"]
    assert m["multipliers"]["output_per_worker"] == pytest.approx(11 / 48)
    assert m["identity_abs_error"] < 1e-15
    assert res.provenance[-1].tool == "jobs_shapley.jobs_shapley"


@pytest.mark.parametrize("annualize, total", [
    ("cagr", 100 * (1.5 ** 0.1 - 1)),
    ("simple", 5.0),
    ("log", 100 * math.log(1.5) / 10),
])
def test_jobs_shapley_annualized_contributions_sum_to_total(annualize, total):
    res = jobs_shapley(*base(), WINDOW, annualize=annualize)
    assert row(res, "gdp_per_capita") == pytest.approx(total, rel=1e-12)
    parts = [row(res, f) for f in ("output_per_worker", "employment_rate", "working_age_share")]
    assert sum(parts) == pytest.approx(total, rel=1e-12)
    assert parts[0] == pytest.approx(total * (11 / 24) / 0.25, rel=1e-12)  # proportional to the Shapley value


def test_jobs_shapley_scales_and_ratio_check():
    scaled = jobs_shapley(*base(emp_scale=1000.0), WINDOW, employment_scale=1000.0)
    plain = jobs_shapley(*base(), WINDOW)
    assert list(scaled.data["value"]) == pytest.approx(list(plain.data["value"]), rel=1e-12)
    wrong = jobs_shapley(*base(emp_scale=0.001), WINDOW)  # employment 1000x too large
    assert "jobs_ratio_above_one" in {c.code for c in wrong.caveats}


def test_jobs_shapley_sector_split_is_exact():
    va, es = sectors()
    res = jobs_shapley(*base(), WINDOW, va_by_sector=va, emp_by_sector=es, annualize="none", **SECTOR_KW)
    # sector productivity 1, 3 -> 2, 5; theta 1/2, 1/2 -> 1/3, 2/3 (Shapley mid-point shift-share)
    # within_A = (1/2 + 1/3)/2 * 1 = 5/12 ; contribution = m_a * 5/12 = 11/48 * 5/12
    assert row(res, "output_per_worker.within", "Agriculture", "level_change") == pytest.approx(11 / 48 * 5 / 12)
    # between_B = (3 + 5)/2 * (2/3 - 1/2) = 2/3 ; contribution = 11/48 * 2/3
    assert row(res, "output_per_worker.between", "Services", "level_change") == pytest.approx(11 / 48 * 2 / 3)
    opw = res.data[res.data["component"].str.startswith("output_per_worker.")]
    assert opw["value"].sum() == pytest.approx(row(res, "output_per_worker"), rel=1e-12)
    assert row(res, "output_per_worker.residual", column="value") == 0.0
    er = res.data[res.data["component"].str.startswith("employment_rate.")]
    assert er["value"].sum() == pytest.approx(row(res, "employment_rate"), rel=1e-12)
    # employment rate by sector: A 125/500 -> 125/500 (0), B 125/500 -> 250/500 (+0.25 = all of db)
    assert row(res, "employment_rate.sector", "Agriculture") == 0.0
    assert row(res, "employment_rate.sector", "Services") == pytest.approx(row(res, "employment_rate"), rel=1e-12)
    assert "jobs_sector_residual" not in {c.code for c in res.caveats}


def test_jobs_shapley_sector_residual_when_va_does_not_add_to_gdp():
    va, es = sectors(va_extra=25.0)  # sector VA falls 25 short of GDP (net taxes)
    res = jobs_shapley(*base(), WINDOW, va_by_sector=va, emp_by_sector=es, sector_variant="two_term",
                       annualize="cagr", **SECTOR_KW)
    resid = row(res, "output_per_worker.residual")
    assert resid != 0.0
    opw = res.data[res.data["component"].str.startswith("output_per_worker.")]
    assert opw["value"].sum() == pytest.approx(row(res, "output_per_worker"), rel=1e-12)
    cav = next(c for c in res.caveats if c.code == "jobs_sector_residual")
    assert cav.evidence["at"][0]["va_coverage"] == pytest.approx([475 / 500, 1475 / 1500])


def test_jobs_shapley_log_method():
    res = jobs_shapley(*base(), WINDOW, method="log")
    assert row(res, "output_per_worker") == pytest.approx(100 * math.log(2) / 10)
    assert row(res, "employment_rate") == pytest.approx(100 * math.log(1.5) / 10)
    assert row(res, "working_age_share") == pytest.approx(100 * math.log(0.5) / 10)
    parts = sum(row(res, f) for f in ("output_per_worker", "employment_rate", "working_age_share"))
    assert parts == pytest.approx(row(res, "gdp_per_capita"), rel=1e-12)
    none = jobs_shapley(*base(), WINDOW, method="log", annualize="none")
    assert row(none, "gdp_per_capita") == pytest.approx(100 * math.log(1.5))


def test_jobs_shapley_single_year_window_status_and_provenance():
    gdp, pop, wap, emp = base(emp_status="modelled")
    gdp = pd.concat([gdp, series("GDP", {"2019": 1400}, unit="constant 2015 LCU")])
    pop = pd.concat([pop, series("POP", {"2019": 2000})])
    wap = pd.concat([wap, series("WAP", {"2019": 500})])
    emp = pd.concat([emp, series("EMP", {"2019": 350}, status="modelled")])
    emp_res = Result(data=emp, provenance=[Provenance(source="ilostat", dataset="ILOEST")])
    res = jobs_shapley(gdp, pop, wap, emp_res, {"2020": "2020"})
    assert set(res.data["start"]) == {"2019"} and set(res.data["end"]) == {"2020"}
    # y: 0.7 -> 0.75 in one year: CAGR = 7.142857...%
    assert row(res, "gdp_per_capita") == pytest.approx(100 * (0.75 / 0.7 - 1), rel=1e-12)
    assert set(res.data["obs_status"]) == {"modelled"}
    codes = {c.code for c in res.caveats}
    assert {"modelled_inputs", "covid_endpoint"} <= codes
    assert any(p.source == "ilostat" for p in res.provenance)


def test_jobs_shapley_errors():
    gdp, pop, wap, emp = base()
    with pytest.raises(JobsShapleyError):
        jobs_shapley(gdp, pop, wap, emp, {"2010-30": ("2010", "2030")})
    with pytest.raises(ValueError):
        jobs_shapley(gdp, pop, wap, emp, WINDOW, va_by_sector=sectors()[0])
    with pytest.raises(ValueError):
        jobs_shapley(gdp, pop, wap, emp, WINDOW, method="ratio")
    with pytest.raises(JobsShapleyError):
        jobs_shapley(gdp, pop, wap, emp, WINDOW, iso3=["BBB"])
    with pytest.raises(ValueError):
        jobs_shapley(gdp, pop, wap, emp, WINDOW, indicators={"gdp_pc": "X"})
