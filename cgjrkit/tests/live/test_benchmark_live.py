"""Live checks of cgjrkit.methods.benchmark on WDI (needs api.worldbank.org allowlisted).

Run with ``CGJRKIT_LIVE=1 pytest -m live tests/live/test_benchmark_live.py``.

Replication targets (``../inventory/kenya-replication-inventory.md``):

* Kenya Fig 15 (p.19): domestic credit to the private sector 31.6% of GDP in
  2023; Kenya at the 48th percentile in 2001 and the 38th in 2023, 104
  countries. UNVERIFIED: the report's sample filter and percentile definition;
  the test uses a balanced sample and ``method="weak"`` with a loose tolerance
  and prints what each definition gives, so the matching one can be pinned.
* Kenya Fig 14 (p.19): private credit vs log GDP per capita (PPP, 2017 $),
  2023, fitted curve (form not stated): checks the fit runs on real data, drops
  aggregates and keeps the PPP 2017 vintage unmixed.
"""

from __future__ import annotations

import pytest

from cgjrkit.connectors.worldbank import WorldBank
from cgjrkit.methods.benchmark import income_conditional_fit, kernel_density, percentile_rank

pytestmark = pytest.mark.live

CREDIT = "FS.AST.PRVT.GD.ZS"
GDPPC_PPP = "NY.GDP.PCAP.PP.KD"


@pytest.fixture(scope="module")
def credit():
    return WorldBank().fetch_indicators(CREDIT, "all", 2001, 2023)


def test_kenya_fig15_percentiles(credit):
    res = percentile_rank(credit, "KEN", ["2001", "2023"], balanced=True)
    focus = {f["period"]: f for f in res.meta["focus"]}
    assert focus["2023"]["level"] == pytest.approx(31.6, abs=2.0)
    for method in ("weak", "strict", "mean"):
        alt = percentile_rank(credit, "KEN", ["2001", "2023"], balanced=True, method=method)
        print(method, {f["period"]: (round(f["percentile"], 1), f["n"]) for f in alt.meta["focus"]})
    assert focus["2001"]["percentile"] == pytest.approx(48.0, abs=6.0)
    assert focus["2023"]["percentile"] == pytest.approx(38.0, abs=6.0)
    assert 80 <= focus["2023"]["n"] <= 190


def test_kenya_fig15_kde(credit):
    res = kernel_density(credit, ["2001", "2023"], focus="KEN", balanced=True)
    assert set(res.meta["focus"]["by_period"]) == {"2001", "2023"}
    assert "WLD" not in set(res.data["iso3"])


def test_kenya_fig14_fit():
    wb = WorldBank()
    x = wb.fetch_indicators(GDPPC_PPP, "all", 2023, 2023)
    y = wb.fetch_indicators(CREDIT, "all", 2023, 2023)
    res = income_conditional_fit(x, y, "2023", kind="quadratic", focus="KEN")
    pts = res.data[res.data["role"] == "country"]
    assert "WLD" not in set(pts["iso3"]) and "SSF" not in set(pts["iso3"])
    assert res.meta["fit"]["n"] > 100
    assert res.meta["focus"]["actual"] == pytest.approx(31.6, abs=2.0)
    assert not res.blocked
