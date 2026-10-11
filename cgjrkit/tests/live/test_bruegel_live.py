"""Live tests for the Bruegel REER connector.

Run with ``CGJRKIT_LIVE=1 pytest -m live tests/live/test_bruegel_live.py`` once
``www.bruegel.org`` is allowlisted. Each test confirms an assumption listed as
UNVERIFIED in ``docs/apis/bruegel.md``.

Replication targets (Kenya CGJR, values read from the charts, so tolerances are
wide and the current file may be revised relative to the report's vintage):

* Fig 11 (p.18): monthly REER, 120 trading partners, Dec 2007 = 100: ~68 in
  Jan 2005; peak ~165 around early 2020; trough ~122 around early 2024; ~148 in
  Jun 2025.
* Fig 20 (p.24): annual REER (basket not stated), 2007 = 100: ~81 in
  2001-2002, ~153 in 2019.
"""

import pytest

from cgjrkit.connectors import bruegel
from cgjrkit.connectors.bruegel import BruegelREER
from cgjrkit.core.http import fetch
from cgjrkit.core.schema import validate_frame

pytestmark = pytest.mark.live


def test_dataset_page_links_a_reer_zip():
    url, fallback, provs = BruegelREER().resolve_url(refresh=True)
    assert not fallback, "no REER download link found on the dataset page"
    assert "REER" in url.upper() and url.lower().endswith((".zip", ".xls", ".xlsx")), url
    assert bruegel.parse_version(url), f"no ver<DDMonYYYY> token in {url}"
    assert provs and provs[0].payload_sha256


def test_workbook_layout():
    conn = BruegelREER()
    cat = conn.sheets()
    assert "REER_MONTHLY_120" in set(cat["sheet"]), cat
    assert {"monthly", "annual"} <= set(cat["frequency"])
    url, _, _ = conn.resolve_url()
    payload, _ = fetch("bruegel", url)
    wb = bruegel.open_workbook(payload)
    assert wb.member.lower().endswith((".xls", ".xlsx")) and bruegel.parse_version(wb.member)
    readme = next(s for s in wb.sheets if s.strip().upper() == "READ ME")
    names = bruegel.parse_readme(wb.parse(readme))
    assert names.get("KE", "").lower().startswith("kenya"), list(names.items())[:5]
    raw = wb.parse("REER_MONTHLY_120")
    long, skipped = bruegel.parse_sheet(raw, "REER_MONTHLY_120")
    assert long["code"].nunique() >= 170 and not skipped
    assert long["period"].min() <= "1994-01"


def test_kenya_fig11_monthly_120():
    res = BruegelREER().fetch("KEN", frequency="monthly", baskets=[120], start="2005-01")
    validate_frame(res.data)
    s = res.data.set_index("period")["value"]
    s = 100 * s / s.loc["2007-12"]
    assert s.loc["2005-01"] == pytest.approx(68, abs=6)
    assert s.loc["2019-01":"2020-12"].max() == pytest.approx(165, abs=8)
    assert s.loc["2023-06":"2024-06"].min() == pytest.approx(122, abs=8)
    assert s.loc["2025-06"] == pytest.approx(148, abs=8)
    assert {"bruegel_snapshot", "bruegel_realtime_cpi"} <= {c.code for c in res.caveats}


def test_kenya_fig20_annual():
    res = BruegelREER().fetch("KEN", frequency="annual", start="2001", end="2019")
    assert len(res.meta["sheets_used"]) >= 1
    hits = {}
    for ind, g in res.data.groupby("indicator"):
        s = g.set_index("period")["value"]
        s = 100 * s / s.loc["2007"]
        hits[ind] = (round(float(s.loc["2001"]), 1), round(float(s.loc["2019"]), 1))
    assert any(abs(v2019 - 153) <= 10 and abs(v2001 - 81) <= 8 for v2001, v2019 in hits.values()), hits
