"""Live checks of cgjrkit.methods.growth_accounting on Penn World Table 10.01.

Run with ``CGJRKIT_LIVE=1 pytest -m live tests/live/test_growth_accounting_live.py``
once ``dataverse.nl`` is allowlisted (PWT files are downloaded from DataverseNL).

What they confirm:

* UNVERIFIED in this environment: PWT's ``rtfpna`` growth is the Tornqvist
  residual ``dln rgdpna - (1 - s) dln rkna - s dln(emp * hc)`` with ``s`` the
  labour share averaged over t-1 and t (Feenstra, Inklaar and Timmer 2015).
  If true, ``growth_accounting(..., weights="tornqvist")`` reproduces the
  cumulative change in ``rtfpna``.
* Replication target, Kenya CGJR Fig 4 (p.15, PWT 10.01, 2001=100): in 2019
  GDP per worker ~135-136 and TFP ~104 (read from the chart; tolerance 3).
"""

from __future__ import annotations

import math

import pytest

from cgjrkit.connectors.pwt import PWT
from cgjrkit.methods.growth_accounting import growth_accounting, potential_output

pytestmark = pytest.mark.live

YEARS = 2019 - 2001


@pytest.fixture(scope="module")
def kenya():
    return PWT().fetch("10.01", ["rgdpna", "rkna", "rnna", "rtfpna", "emp", "hc", "labsh"], ["KEN"], 2001, 2019)


def _contrib(res) -> dict[str, float]:
    return dict(zip(res.data["component"], res.data["value"]))


def test_tornqvist_tfp_reproduces_pwt_rtfpna(kenya):
    res = growth_accounting(kenya, [("2001", "2019")], weights="tornqvist", sensitivity=None)
    w = kenya.data.pivot(index="period", columns="indicator", values="value")
    pwt_dln_tfp = math.log(w.loc["2019", "rtfpna"] / w.loc["2001", "rtfpna"])
    ours = _contrib(res)["tfp"] * YEARS / 100.0
    assert ours == pytest.approx(pwt_dln_tfp, abs=0.005)


def test_kenya_fig4_indices_from_per_worker_accounting(kenya):
    res = growth_accounting(kenya, [("2001", "2019")], per_worker=True, weights="tornqvist")
    c = _contrib(res)
    gdp_per_worker_2019 = 100.0 * math.exp(c["output_per_worker"] * YEARS / 100.0)
    tfp_2019 = 100.0 * math.exp(c["tfp"] * YEARS / 100.0)
    assert gdp_per_worker_2019 == pytest.approx(135.5, abs=3.0)
    assert tfp_2019 == pytest.approx(104.0, abs=3.0)
    assert res.meta["growth_accounting"]["identity_max_abs_error"] < 1e-9


def test_potential_output_runs_on_kenya(kenya):
    res = potential_output(kenya, iso3="KEN")
    growth = res.data[res.data["component"] == "potential_growth"]["value"]
    assert 2.0 < growth.mean() < 8.0  # plausibility only: Kenya grew ~4-5% a year in 2002-2019
    assert "potential_output_approximation" in {c.code for c in res.caveats}
