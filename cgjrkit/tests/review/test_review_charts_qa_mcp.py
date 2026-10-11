"""Adversarial review probes for charts, the caveat engine and the MCP server.

Each test documents a defect found in review. They were written as
``xfail(strict=True)`` probes; the defects are fixed and the markers removed,
so they now guard the fixed behaviour. The remaining tests are seeded checks
that guard behaviour the review relied on.

All data are SYNTHETIC: round numbers, obviously fake codes (AAA, BBB, ...)
or "(synthetic)" labels. Nothing touches the network (conftest sets offline
mode and an isolated cache); charts render offline with vl-convert.
"""

from __future__ import annotations

import json
from pathlib import Path

import anyio
import pandas as pd
import pytest
from mcp import Client

from cgjrkit.charts import decomposition as cd
from cgjrkit.charts import render
from cgjrkit.charts import series as cs
from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import make_frame
from cgjrkit.mcp_server import create_server
from cgjrkit.methods.jobs_shapley import jobs_shapley
from cgjrkit.qa.caveat_check import run_caveat_check


# ---------------------------------------------------------------------------
# helpers (synthetic inputs only)
# ---------------------------------------------------------------------------
def _result(rows, *, source="synthetic", dataset="SYN", caveats=(), unit="u") -> Result:
    """rows: (iso3, period, indicator, value) -> Result with a complete synthetic provenance entry."""
    df = make_frame(
        [dict(iso3=i, country=f"{i} (synthetic)", period=p, indicator=ind, value=v, unit=unit) for i, p, ind, v in rows],
        source=source, dataset=dataset,
    )
    prov = Provenance(source=source, dataset=dataset, citation="Synthetic source", vintage="test-1", payload_sha256="0" * 64)
    return Result(data=df, provenance=[prov], caveats=list(caveats))


def _line_rows():
    return [(iso, str(y), "SYN", float(k + y - 2009)) for k, iso in enumerate(["AAA", "BBB", "CCC", "DDD"])
            for y in range(2010, 2015)]


def _units(spec):
    if isinstance(spec, dict):
        if "mark" in spec:
            yield spec
        for key in ("layer", "vconcat", "hconcat", "concat"):
            for sub in spec.get(key, []) or []:
                yield from _units(sub)
        if isinstance(spec.get("spec"), dict):
            yield from _units(spec["spec"])


def _call(server, name: str, args: dict):
    async def go():
        async with Client(server) as client:
            return await client.call_tool(name, args)

    return anyio.run(go)


# ---------------------------------------------------------------------------
# charts
# ---------------------------------------------------------------------------
def test_stacked_auto_total_on_jobs_shapley_result_picks_gdp_per_capita():
    def one(ind, values):
        return _result([("AAA", p, ind, v) for p, v in values.items()])

    # GDP per capita 0.5 -> 0.75 (+50%); Shapley parts 91.67 + 54.17 - 95.83 = 50 (exact identity)
    js = jobs_shapley(one("GDP", {"2010": 500.0, "2020": 1500.0}), one("POP", {"2010": 1000.0, "2020": 2000.0}),
                      one("WAP", {"2010": 500.0, "2020": 500.0}), one("EMP", {"2010": 250.0, "2020": 375.0}),
                      {"2010-20": ("2010", "2020")}, annualize="none")
    chart = render.chart_from_result(js, "stacked")  # what render_chart(kind="stacked") does over MCP
    um = chart.to_dict()["usermeta"]
    footer = " ".join(um.get("cgjrkit_footer", []))
    assert um["cgjrkit_chart"]["total"] == "gdp_per_capita"
    assert "gdp_per_capita" not in um["cgjrkit_chart"]["components"]
    assert "do not add up" not in footer


def test_horizontal_total_tick_fits_inside_its_bar():
    comp = pd.DataFrame([
        {"period": p, "component": c, "value": v}
        for p, parts in {"2001-10": (1.0, -0.5), "2011-19": (2.0, 1.0), "2020-24": (0.5, -2.0)}.items()
        for c, v in zip(("capital", "tfp", "output"), (*parts, sum(parts)))
    ])
    spec = render.to_spec(cd.stacked_contributions(comp, horizontal=True, total_mark="tick"))
    (tick,) = [u for u in _units(spec) if isinstance(u["mark"], dict) and u["mark"].get("type") == "tick"]
    n_bars = 3
    band_step = spec["height"] / n_bars  # upper bound on one category's band (padding makes it smaller)
    assert tick["mark"]["size"] <= band_step


@pytest.mark.parametrize("build", [
    lambda r: cs.line_vs_peers(r, focus="AAA"),
    lambda r: cs.bar_vs_peers(r, focus="AAA", period="2014"),
])
def test_chart_builders_refuse_blocked_results(build):
    blocked = _result(_line_rows(), caveats=[Caveat("ppp_mixing", "block", "Inputs mix PPP bases (synthetic).")])
    with pytest.raises(render.BlockedResultError):
        build(blocked)


def test_scatter_marks_an_excluded_focus_as_left_out_of_the_fit():
    from cgjrkit.methods.benchmark import income_conditional_fit

    isos = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    x = _result([(iso, "2020", "X", 1000.0 * 2 ** k) for k, iso in enumerate(isos)])
    y = _result([(iso, "2020", "Y", 10.0 + 5 * k + (1.0 if k % 2 else -1.0)) for k, iso in enumerate(isos)])
    fit = income_conditional_fit(x, y, "2020", kind="linear", log_x=True, focus="CCC", exclude=["CCC"])
    assert not fit.data.loc[(fit.data["role"] == "country") & (fit.data["iso3"] == "CCC"), "in_fit"].any()
    chart = render.chart_from_result(fit, "scatter")
    assert any("left out of the fit" in line for line in render.footer_lines(chart))


def test_builders_keep_the_source_line_and_caveats_of_a_result_they_are_given():
    """Fixed: a builder called with a Result carries its source line and warn caveats, like chart_from_result;
    a builder called with a plain frame still has no source line."""
    res = _result(_line_rows(), caveats=[Caveat("syn_warn", "warn", "Synthetic warning.")])
    expected = ["Note: Synthetic warning.", "Source: Synthetic source (test-1)."]
    assert render.footer_lines(cs.line_vs_peers(res, focus="AAA")) == expected
    assert render.footer_lines(render.chart_from_result(res, "line", focus="AAA")) == expected
    assert render.footer_lines(cs.line_vs_peers(res.data, focus="AAA")) == []


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
@pytest.fixture
def server():
    return create_server()


def test_render_chart_options_cannot_replace_the_source_line(server, tmp_path):
    rid = server.cgjrkit_tools.store.put(_result(_line_rows()))
    res = _call(server, "render_chart", {
        "result": rid, "kind": "line", "output_dir": str(tmp_path), "formats": ["vl.json"],
        "options": {"focus": "AAA", "source_line": "Source: Fabricated provider (synthetic)."},
    })
    if res.is_error:  # refusing the option is an acceptable fix
        return
    assert res.structured_content["footer"][-1] == "Source: Synthetic source (test-1)."


def test_render_chart_does_not_overwrite_existing_files(server, tmp_path):
    victim = tmp_path / "notes.csv"
    victim.write_text("synthetic user file\n")
    rid = server.cgjrkit_tools.store.put(_result(_line_rows()))
    res = _call(server, "render_chart", {
        "result": rid, "kind": "line", "output_dir": str(tmp_path), "stem": "notes", "formats": ["vl.json"],
        "options": {"focus": "AAA"},
    })
    assert victim.read_text() == "synthetic user file\n" or res.is_error


def test_render_chart_can_draw_a_peer_band_from_a_result_id(server, tmp_path):
    rid = server.cgjrkit_tools.store.put(_result(_line_rows()))
    pa = _call(server, "peer_aggregate", {"data": rid, "members": ["BBB", "CCC", "DDD"], "frozen_at": "2026-01-01"})
    assert not pa.is_error
    res = _call(server, "render_chart", {
        "result": rid, "kind": "line", "output_dir": str(tmp_path), "formats": ["vl.json"],
        "options": {"focus": "AAA", "peers": [], "band": pa.structured_content["result_id"]},
    })
    assert not res.is_error, res.content[0].text
    spec = json.loads(Path(res.structured_content["files"]["vl.json"]).read_text())
    assert any(isinstance(u["mark"], dict) and u["mark"].get("type") == "area" for u in _units(spec))


def test_render_chart_refuses_what_the_caveat_check_blocks(server, tmp_path):
    rows = [dict(source="synthetic", dataset="SYN", indicator=ind, iso3="AAA", period=str(y), value=float(y - 2000),
                 unit=unit) for ind, unit in (("GDPPC17", "constant 2017 international $ (PPP 2017)"),
                                              ("GDPPC21", "constant 2021 international $ (PPP 2021)"))
            for y in (2010, 2011)]
    check = _call(server, "run_caveat_check", {"results": [rows]})
    assert check.structured_content["status"] == "BLOCK"  # the engine itself catches it
    res = _call(server, "render_chart", {
        "result": rows, "kind": "line", "output_dir": str(tmp_path), "formats": ["vl.json"],
        "options": {"series": "indicator"},
    })
    assert res.is_error and not any(tmp_path.iterdir())


# ---------------------------------------------------------------------------
# caveat rules on real method outputs (seeded, pass today)
# ---------------------------------------------------------------------------
def test_covid_rule_fires_on_shift_share_and_growth_accounting_windows():
    from cgjrkit.methods.growth_accounting import growth_accounting
    from cgjrkit.methods.structural_change import shift_share

    va = _result([("AAA", "2016", "VA_A", 100.0), ("AAA", "2016", "VA_I", 100.0),
                  ("AAA", "2020", "VA_A", 120.0), ("AAA", "2020", "VA_I", 130.0)])
    emp = _result([("AAA", "2016", "E_A", 10.0), ("AAA", "2016", "E_I", 10.0),
                   ("AAA", "2020", "E_A", 11.0), ("AAA", "2020", "E_I", 12.0)])
    ss = shift_share(va, emp, "2016", "2020", "two_term", va_sectors={"VA_A": "A", "VA_I": "I"},
                     emp_sectors={"E_A": "A", "E_I": "I"})
    assert "covid_endpoints" in {c.code for c in run_caveat_check(ss).caveats}

    panel = _result([("AAA", str(y), var, val) for var, s in {
        "rgdpna": {2015: 100.0, 2020: 150.0}, "rkna": {2015: 1.0, 2020: 2.0}, "emp": {2015: 10.0, 2020: 11.0},
        "hc": {2015: 2.0, 2020: 2.2}, "labsh": {2015: 0.6, 2020: 0.6}}.items() for y, val in s.items()],
        source="pwt", dataset="PWT10.01")
    ga = growth_accounting(panel, {"late 2010s": ("2015", "2020")})  # label is not window-shaped: params must carry it
    report = run_caveat_check(ga)
    assert "covid_endpoints" in {c.code for c in report.caveats}
    assert any("COVID-19" in f for f in report.footnotes)


def test_peer_aggregate_unfrozen_and_small_n_reach_the_report():
    from cgjrkit.methods.peers import PeerSet, peer_aggregate

    data = _result([(iso, "2020", "SYN", v) for iso, v in {"AAA": 1.0, "BBB": 2.0}.items()])
    out = peer_aggregate(data, PeerSet(name="Synthetic peers", members=["AAA", "BBB"]), min_n=3)
    report = run_caveat_check(out, {"mode": "replication"})
    codes = {c.code: c.severity for c in report.caveats}
    assert codes.get("peers_not_frozen") == "block" and codes.get("small_n") == "warn"
    assert report.status == "BLOCK"
