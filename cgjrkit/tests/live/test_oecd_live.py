"""Live tests for the OECD SDMX connector.

Run with ``CGJRKIT_LIVE=1 pytest -m live tests/live/test_oecd_live.py`` once
``sdmx.oecd.org`` is allowlisted. The whole file makes at most 5 network
queries (one bulk query per dataflow), inside the 20-per-hour plan. Each test
confirms an assumption marked UNVERIFIED in ``docs/apis/oecd.md``.

Replication: Morocco CGJR Fig 3-4 (economy-wide PMR vs OECD, archetype R03)
has no printed values in ``../inventory``. Italy's economy-wide PMR (2018:
1.29, 2023: 1.23) is a third-party reading of the same dataflow and is used as
a loose check.
"""

import pytest

from cgjrkit.connectors import oecd
from cgjrkit.connectors.oecd import OECD
from cgjrkit.core.http import fetch
from cgjrkit.core.schema import validate_frame

pytestmark = pytest.mark.live


def _table(flow):
    url, params = oecd.build_request(flow)
    payload, _ = fetch("oecd", url, params, retries=1)
    return oecd.read_sdmx_csv(payload)


def test_pmr_dimensions_and_values():
    """UNVERIFIED: PMR DSD order (REF_AREA, MEASURE, FREQ); measure codes PMR, BARRIER_TRADE."""
    t = _table("PMR")
    assert t.layout == "labels"
    assert t.dims == list(oecd.FLOWS["PMR"].dims), f"PMR dimensions are {t.dims}"
    res = OECD().pmr(["ITA"], 2018, 2023, measures="PMR")
    validate_frame(res.data)
    v = res.data.set_index("period")["value"]
    assert v["2018"] == pytest.approx(1.29, abs=0.03) and v["2023"] == pytest.approx(1.23, abs=0.03)
    assert res.meta["served_from_cache"] is True  # the bulk payload above was reused


def test_pmr_wbg_has_morocco():
    """UNVERIFIED: DF_PMR_WBG under OECD.ECO.GCRD, latest version, with non-OECD economies."""
    res = OECD().pmr(["MAR", "IDN", "PHL"], wbg=True)
    validate_frame(res.data)
    assert "MAR" in set(res.data["iso3"])
    assert res.meta["structure_version"]
    assert res.data["value"].between(0, 6).all()


def test_stri_dimensions():
    """UNVERIFIED: DSD_STRI order FREQ, REF_AREA, MEASURE, ACTIVITY, METHODOLOGY, STRI_TYPE, UNIT_MEASURE,
    COUNTERPART_AREA; DF_STRI_MAIN served with an empty (latest) version."""
    t = _table("STRI")
    assert t.dims == list(oecd.FLOWS["STRI"].dims), f"STRI dimensions are {t.dims}"
    res = OECD().stri(["IDN", "MKD"])
    validate_frame(res.data)
    assert {"IDN"} <= set(res.data["iso3"])
    assert res.data["value"].between(0, 1).all()


def test_fdirri_layout():
    """Verified dimension order (REF_AREA, ACTIVITY, POL_CAT, MEASURE, UNIT_MEASURE), version 2.0."""
    t = _table("FDIRRI")
    assert t.dims == list(oecd.FLOWS["FDIRRI"].dims) and t.version == "2.0"
    res = OECD().fdirri(["KEN", "MKD", "PHL"])
    validate_frame(res.data)
    assert set(res.data["indicator"]) == {"FDIRRI:_T._T.RR.IX"}
    assert res.data["value"].between(0, 1).all()


def test_budget_after_live_queries():
    status = OECD().budget_status()
    assert status["used"] <= oecd.HOURLY_LIMIT
