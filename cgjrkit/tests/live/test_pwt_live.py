"""Live tests for the Penn World Table connector.

Run with ``CGJRKIT_LIVE=1 pytest -m live tests/live/test_pwt_live.py`` once
``dataverse.nl`` (and ``www.rug.nl`` for PWT 10.0) are allowlisted. Each test
confirms an assumption listed as UNVERIFIED in ``docs/apis/pwt.md``.

Replication target: Kenya CGJR Fig 4 (p.15, "TFP, capital per worker, and GDP
per worker Index (2001=100), Kenya", source PWT 10.01). Read from the chart:
in 2019 capital per worker ~136, GDP per worker ~135-136, TFP ~104; TFP dips
to ~97 around 2008; GDP per worker peaks locally at ~109-110 in 2007.
"""

import pandas as pd
import pytest

from cgjrkit.connectors import pwt
from cgjrkit.connectors.pwt import PWT
from cgjrkit.core.schema import ppp_base, validate_frame

pytestmark = pytest.mark.live

TOL = 3.0


def _kenya_indices(res) -> pd.DataFrame:
    w = res.data.pivot(index="period", columns="indicator", values="value")
    out = pd.DataFrame({
        "gdp_per_worker": (w["rgdpna"] / w["emp"]),
        "capital_per_worker": (w["rnna"] / w["emp"]),
        "tfp": w["rtfpna"],
    })
    return 100 * out / out.loc["2001"]


@pytest.mark.parametrize("version", ["10.01", "11.0"])
def test_dataverse_record_lists_pinned_files(version):
    """The pinned file ids and names (third-party evidence) match the DOI's Dataverse record."""
    files, ds_version, prov = PWT().dataset_files(version, refresh=True)
    assert ds_version and prov.payload_sha256
    spec = pwt.VERSIONS[version]
    for fmt, pinned in spec.files.items():
        chosen = pwt.select_file(files, version, fmt)
        assert chosen["id"] == pinned.file_id, (fmt, chosen)
        assert pinned.filename in (chosen["filename"], chosen["original_name"]), chosen


def test_kenya_fig4_pwt1001():
    res = PWT().fetch("10.01", ["rgdpna", "rnna", "rtfpna", "emp"], ["KEN"], 2001, 2019)
    validate_frame(res.data)
    assert res.meta["inferred_reference_year"] == 2017
    idx = _kenya_indices(res)
    assert idx.loc["2019", "capital_per_worker"] == pytest.approx(136.0, abs=TOL)
    assert idx.loc["2019", "gdp_per_worker"] == pytest.approx(135.5, abs=TOL)
    assert idx.loc["2019", "tfp"] == pytest.approx(104.0, abs=TOL)
    assert idx.loc["2008", "tfp"] == pytest.approx(97.0, abs=TOL)
    assert idx.loc["2007", "gdp_per_worker"] == pytest.approx(109.5, abs=TOL)


def test_xlsx_and_dta_agree():
    kw = dict(variables=["rgdpna", "rnna", "emp", "hc", "labsh"], countries=["KEN", "UGA"], start=2010, end=2019)
    a = PWT().fetch("10.01", file_format="xlsx", **kw).data
    b = PWT().fetch("10.01", file_format="dta", **kw).data
    pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-5)


def test_resolve_matches_pinned_and_checksum():
    res = PWT().fetch("10.01", ["emp"], ["KEN"], resolve=True)
    assert res.meta["file"]["file_id"] == 354095
    assert res.meta["file"]["checksum_ok"] in (True, None)


def test_pwt11_reference_year_and_coverage():
    res = PWT().fetch("11.0", ["rgdpna", "rkna", "cgdpo", "emp"], None)
    assert res.meta["inferred_reference_year"] == 2021
    assert res.meta["file_years"][1] == 2023 and res.meta["file_countries"] >= 185
    units = res.data.groupby("indicator")["unit"].first()
    assert ppp_base(units["rgdpna"]) == "2021" and ppp_base(units["cgdpo"]) == "2021"
    labels = res.meta["file_labels"]
    assert "2021" in labels.get("rgdpna", "2021"), labels  # Legend text, e.g. "(in mil. 2021US$)"


def test_pwt100_rug_route():
    res = PWT().fetch("10.0", ["emp"], ["KEN"])
    assert res.meta["inferred_reference_year"] == 2017 and len(res.data) > 0
