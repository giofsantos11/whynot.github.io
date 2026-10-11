"""Live checks of the structural-change and jobs methods on public data (Kenya CGJR targets).

Run with ``CGJRKIT_LIVE=1 pytest -m live tests/live/test_structural_change_live.py``
once ``api.worldbank.org`` and ``rplumber.ilo.org`` are allowlisted.

What they confirm:

* Kenya CGJR Fig 35 (p.33): growth of value added per worker 1.8% a year
  over 2011-2019, with inter-sectoral (between) services ~+1.55 pp (~85% of
  the total per the text). The report used ILO modelled employment (Nov 2023
  edition) and KNBS value added; ILOSTAT now serves a later edition and WDI
  value added replaces KNBS here, so the tolerance is wide (+-0.8 pp) and the
  identity is the strict check. UNVERIFIED: which shift-share variant and
  annualisation the report used; the test prints all three variants.
* Kenya CGJR Fig 2 (p.14): period labels g = 3.8 (2001-10), 4.6 (2011-19),
  0.5 (2020), 5.1 (2021-24) from KNBS 2025; WDI GDP at constant LCU is the
  public fallback (tolerance 0.7 pp, rebasing differences expected).
* Units assumed by ``jobs_shapley``: ILOSTAT employment in thousands, WDI
  population in persons (employment rate must lie in (0, 1) with
  ``employment_scale=1000``).
"""

from __future__ import annotations

import pytest

from cgjrkit.connectors.ilostat import ILOSTAT
from cgjrkit.connectors.worldbank import WorldBank
from cgjrkit.methods.jobs_shapley import jobs_shapley
from cgjrkit.methods.structural_change import sector_contributions, shift_share

pytestmark = pytest.mark.live

WDI_VA = {"NV.AGR.TOTL.KN": "Agriculture", "NV.IND.TOTL.KN": "Industry", "NV.SRV.TOTL.KN": "Services"}
ILO_SECTORS = {
    "EMP_2EMP_SEX_ECO_NB|SEX_T|ECO_SECTOR_AGR": "Agriculture",
    "EMP_2EMP_SEX_ECO_NB|SEX_T|ECO_SECTOR_IND": "Industry",
    "EMP_2EMP_SEX_ECO_NB|SEX_T|ECO_SECTOR_SER": "Services",
}


def _kenya_inputs():
    va = WorldBank().fetch(list(WDI_VA), ["KEN"], start=2011, end=2019, refresh=True)
    emp = ILOSTAT().fetch("employment_by_sector", ["KEN"], 2011, 2019, sex="SEX_T",
                          classif1=["ECO_SECTOR_AGR", "ECO_SECTOR_IND", "ECO_SECTOR_SER"], refresh=True)
    return va, emp


def test_kenya_fig35_shift_share_identity_and_target():
    va, emp = _kenya_inputs()
    results = {}
    for variant in ("two_term", "three_term", "shapley"):
        res = shift_share(va, emp, "2011", "2019", variant, va_sectors=WDI_VA, emp_sectors=ILO_SECTORS,
                          emp_scale=1000.0, annualize="cagr")
        m = res.meta["shift_share"]["KEN"]
        assert m["identity_abs_error"] <= 1e-9 * max(m["Y0"], m["YT"])
        assert "ilo_modelled" in {c.code for c in res.caveats}  # every ILO-modelled series raises a WARN
        results[variant] = res
        print(variant, res.data[["indicator", "value", "share_of_total"]].to_string())
    total = float(results["two_term"].data.query("indicator == 'shift_share.total|TOTAL'")["value"].iloc[0])
    assert total == pytest.approx(1.8, abs=0.8), f"VA per worker growth {total:.2f}% vs 1.8% printed"
    between_srv = float(results["two_term"].data.query("indicator == 'shift_share.between|Services'")["value"].iloc[0])
    assert between_srv > 0, "services reallocation should be growth-enhancing (Fig 35: ~+1.55 pp)"


def test_kenya_fig2_total_growth_labels_from_wdi():
    wb = WorldBank()
    va = wb.fetch(list(WDI_VA), ["KEN"], start=2000, end=2024, refresh=True)
    gdp = wb.fetch("NY.GDP.MKTP.KN", ["KEN"], start=2000, end=2024, refresh=True)
    periods = {"2001-10": ("2001", "2010"), "2011-19": ("2011", "2019"), "2020": "2020", "2021-24": ("2021", "2024")}
    res = sector_contributions(va, periods, sectors=WDI_VA, total=gdp)
    totals = res.data[res.data["sector"] == "TOTAL"].set_index("window")["value"]
    print(res.data[["window", "sector", "value"]].to_string(), [c.code for c in res.caveats])
    for window, printed in {"2001-10": 3.8, "2011-19": 4.6, "2020": 0.5, "2021-24": 5.1}.items():
        assert totals[window] == pytest.approx(printed, abs=0.7), f"{window}: {totals[window]:.2f} vs {printed}"


def test_jobs_shapley_units_kenya():
    wb = WorldBank()
    gdp = wb.fetch("NY.GDP.MKTP.KN", ["KEN"], start=2011, end=2019, refresh=True)
    pop = wb.fetch("SP.POP.TOTL", ["KEN"], start=2011, end=2019, refresh=True)
    wap = wb.fetch("SP.POP.1564.TO", ["KEN"], start=2011, end=2019, refresh=True)
    emp = ILOSTAT().fetch("employment", ["KEN"], 2011, 2019, sex="SEX_T", classif1="AGE_YTHADULT_YGE15", refresh=True)
    res = jobs_shapley(gdp, pop, wap, emp, {"2011-19": ("2011", "2019")}, employment_scale=1000.0)
    assert "jobs_ratio_above_one" not in {c.code for c in res.caveats}
    m = res.meta["jobs_shapley"]["KEN"]["2011-19"]
    assert 0.3 < m["factors_start"]["employment_rate"] < 1.0
    assert 0.4 < m["factors_start"]["working_age_share"] < 0.7
    parts = res.data[res.data["sector"] == "TOTAL"].set_index("component")["value"]
    assert parts[["output_per_worker", "employment_rate", "working_age_share"]].sum() == pytest.approx(
        parts["gdp_per_capita"], rel=1e-9)
