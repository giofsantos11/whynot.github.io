"""Unit tests for the preview MCP server (cgjrkit.mcp_server).

Every call goes through the MCP SDK's in-process client, so arguments and
results cross the real tool layer (JSON-schema validation, structured output).
Data are synthetic, in-memory records (AAA/BBB/... and round numbers) or the
synthetic World Bank fixture; nothing touches the network (conftest sets
CGJRKIT_OFFLINE=1 and an isolated cache).
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import anyio
import numpy as np
import pandas as pd
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters

from cgjrkit.connectors import CONNECTORS
from cgjrkit.connectors.worldbank import build_request
from cgjrkit.core.cache import Cache
from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.mcp_server import INLINE_CAVEAT, create_server, jsonable

EXPECTED_TOOLS = {
    "list_connectors", "get_series", "peer_aggregate", "growth_accounting", "shift_share",
    "jobs_shapley", "benchmark_fit", "render_chart", "run_caveat_check",
}


def call(server, name: str, args: dict | None = None):
    """Call one tool through an in-process MCP client session."""

    async def go():
        async with Client(server) as client:
            return await client.call_tool(name, args or {})

    return anyio.run(go)


def ok(server, name: str, args: dict | None = None) -> dict:
    res = call(server, name, args)
    assert not res.is_error, res.content[0].text
    json.dumps(res.structured_content, allow_nan=False)  # JSON-safe, no NaN
    return res.structured_content


def error_text(server, name: str, args: dict | None = None) -> str:
    res = call(server, name, args)
    assert res.is_error
    return res.content[0].text


def records(rows, **defaults) -> list[dict]:
    """rows: (iso3, period, indicator, value) -> canonical records (synthetic)."""
    base = {"source": "synthetic", "dataset": "TEST", "indicator_label": "", "country": "", "unit": "", "obs_status": ""}
    base.update(defaults)
    return [dict(base, iso3=r[0], period=r[1], indicator=r[2], value=r[3]) for r in rows]


def head_by(summary: dict, key: str) -> dict:
    return {row[key]: row["value"] for row in summary["head"]}


# Y doubles, K triples, L +20%, hc +20% over 2000-2010; labour share 0.6 (alpha = 0.4)
PWT_PANEL = records(
    [("AAA", str(y), var, v) for var, series in {
        "rgdpna": {2000: 100.0, 2010: 200.0}, "rkna": {2000: 1.0, 2010: 3.0}, "emp": {2000: 10.0, 2010: 12.0},
        "hc": {2000: 2.0, 2010: 2.4}, "labsh": {2000: 0.6, 2010: 0.6},
    }.items() for y, v in series.items()],
    source="pwt", dataset="PWT10.01",
)


@pytest.fixture
def server():
    return create_server()


# ---------------------------------------------------------------------------
# tool listing
# ---------------------------------------------------------------------------
def test_lists_the_fixed_tool_set_with_annotations(server):
    async def go():
        async with Client(server) as client:
            return await client.list_tools()

    tools = {t.name: t for t in anyio.run(go).tools}
    assert set(tools) == EXPECTED_TOOLS
    for name, tool in tools.items():
        assert tool.description and tool.input_schema["type"] == "object", name
        if name == "render_chart":
            assert tool.annotations.read_only_hint is False and tool.annotations.destructive_hint is False
        else:
            assert tool.annotations.read_only_hint is True, name
    assert tools["get_series"].annotations.open_world_hint is True
    assert set(tools["growth_accounting"].input_schema["required"]) == {"panel", "periods"}
    assert set(tools["get_series"].input_schema["required"]) == {"connector"}


def test_list_connectors_describes_every_registered_connector(server):
    out = ok(server, "list_connectors")
    assert out["count"] == len(CONNECTORS) == 14
    by_name = {c["name"]: c for c in out["connectors"]}
    assert list(by_name) == list(CONNECTORS)
    wb = by_name["worldbank"]
    assert wb["domains"] == ["api.worldbank.org"] and wb["license"] == "CC BY 4.0"
    methods = {m["name"] for m in wb["methods"]}
    assert {"fetch", "fetch_indicators"} <= methods and "fetch_countries" not in methods  # DataFrame, not Result
    assert by_name["openalex"]["canonical_frame"] is False


# ---------------------------------------------------------------------------
# method tools with in-memory data
# ---------------------------------------------------------------------------
def test_growth_accounting_with_inline_records_hand_values(server):
    out = ok(server, "growth_accounting", {"panel": PWT_PANEL, "periods": {"2000s": ["2000", "2010"]}})
    c = {row["component"]: row["value"] for row in out["head"]}
    # 100 ln(2)/10; 0.4 * 100 ln(3)/10; 0.6 * 100 ln(1.2)/10 (labour and hc); residual
    assert c["output"] == pytest.approx(10 * math.log(2), abs=1e-9)
    assert c["capital"] == pytest.approx(4 * math.log(3), abs=1e-9)
    assert c["labour"] == pytest.approx(6 * math.log(1.2), abs=1e-9)
    assert c["human_capital"] == pytest.approx(6 * math.log(1.2), abs=1e-9)
    assert c["tfp"] == pytest.approx(0.349164, abs=1e-6)
    assert out["rows"] == 5 and out["result_id"].startswith("res_")
    # inline values are labelled as such and never presented as provider data
    assert out["status"] == "WARN"
    assert INLINE_CAVEAT in {cv["code"] for cv in out["caveats"]}
    tools = [p["tool"] for p in out["provenance"]]
    assert tools[0] == "mcp_server.inline_data" and tools[-1].endswith("growth_accounting")
    assert out["source_line"] == "Source: client-supplied."


def test_method_result_chains_into_chart_and_caveat_check(server, tmp_path):
    ga = ok(server, "growth_accounting", {"panel": PWT_PANEL, "periods": {"2000s": ["2000", "2010"]}})
    chart = ok(server, "render_chart", {
        "result": ga["result_id"], "kind": "stacked", "output_dir": str(tmp_path / "charts"),
        "stem": "ga_test", "formats": ["vl.json", "svg"], "title": "Synthetic growth accounting",
    })
    files = chart["files"]
    assert {"vl.json", "svg", "csv"} <= set(files)
    for path in files.values():
        assert Path(path).is_file() and Path(path).parent == (tmp_path / "charts").resolve()
    assert Path(files["svg"]).read_text().lstrip().startswith("<svg")
    assert chart["result_id"] == ga["result_id"]
    assert any("supplied inline" in line for line in chart["footer"])  # warn caveat footnoted

    report = ok(server, "run_caveat_check", {"results": [ga["result_id"]]})
    assert report["results"] == [ga["result_id"]]
    assert report["status"] == "WARN" and "ppp_mixing" in report["rules_run"]
    assert INLINE_CAVEAT in {c["code"] for c in report["inherited"]}


def test_shift_share_inline_components_sum_to_cagr(server):
    va = records([("KEN", "2011", "VA_A", 1234.5), ("KEN", "2011", "VA_I", 987.6), ("KEN", "2011", "VA_S", 2345.6),
                  ("KEN", "2019", "VA_A", 1500.1), ("KEN", "2019", "VA_I", 1300.3), ("KEN", "2019", "VA_S", 3900.7)])
    emp = records([("KEN", "2011", "E_A", 7000), ("KEN", "2011", "E_I", 1300), ("KEN", "2011", "E_S", 4700),
                   ("KEN", "2019", "E_A", 7400), ("KEN", "2019", "E_I", 1800), ("KEN", "2019", "E_S", 7100)])
    out = ok(server, "shift_share", {
        "va_by_sector": va, "emp_by_sector": emp, "start": "2011", "end": "2019", "variant": "two_term",
        "va_sectors": {"VA_A": "Agriculture", "VA_I": "Industry", "VA_S": "Services"},
        "emp_sectors": {"E_A": "Agriculture", "E_I": "Industry", "E_S": "Services"}, "max_rows": 200,
    })
    y0 = (1234.5 + 987.6 + 2345.6) / 13000
    yT = (1500.1 + 1300.3 + 3900.7) / 16300
    cagr = 100 * ((yT / y0) ** (1 / 8) - 1)
    total = [r["value"] for r in out["head"] if r["indicator"] == "shift_share.total|TOTAL"]
    parts = sum(r["value"] for r in out["head"] if r["sector"] != "TOTAL")
    assert total == [pytest.approx(cagr, rel=1e-9)]
    assert parts == pytest.approx(cagr, rel=1e-9)
    assert out["rows"] == len(out["head"])


def test_jobs_shapley_inline_hand_values(server):
    def one(ind, values):
        return records([("AAA", p, ind, v) for p, v in values.items()])

    out = ok(server, "jobs_shapley", {
        "gdp": one("GDP", {"2010": 500, "2020": 1500}), "population": one("POP", {"2010": 1000, "2020": 2000}),
        "working_age": one("WAP", {"2010": 500, "2020": 500}), "employment": one("EMP", {"2010": 250, "2020": 375}),
        "periods": {"2010-20": ["2010", "2020"]}, "annualize": "none", "max_rows": 200,
    })
    rows = {r["component"]: r["value"] for r in out["head"] if r["sector"] == "TOTAL"}
    # GDP per capita 0.5 -> 0.75; Shapley values 11/24, 13/48, -23/48 as percent of 0.5
    assert rows["gdp_per_capita"] == pytest.approx(50.0, rel=1e-12)
    assert rows["output_per_worker"] == pytest.approx(100 * (11 / 24) / 0.5, rel=1e-12)
    assert rows["employment_rate"] == pytest.approx(100 * (13 / 48) / 0.5, rel=1e-12)
    assert rows["working_age_share"] == pytest.approx(100 * (-23 / 48) / 0.5, rel=1e-12)


def test_peer_aggregate_inline_median_and_counts(server):
    data = records([(iso, "2020", "IND", v) for iso, v in {"AAA": 1.0, "BBB": 2.0, "CCC": 3.0, "DDD": 10.0}.items()])
    out = ok(server, "peer_aggregate", {
        "data": data, "members": ["AAA", "BBB", "CCC", "DDD"], "peerset_name": "Synthetic peers",
        "stats": ["mean", "median"], "frozen_at": "2026-01-01", "approver": "test",
    })
    values = head_by(out, "iso3")
    assert values["SYNTHETIC_PEERS:mean"] == pytest.approx(4.0)
    assert values["SYNTHETIC_PEERS:median"] == pytest.approx(2.5)
    assert {r["n"] for r in out["head"]} == {4}
    assert "peer_set_not_frozen" not in {c["code"] for c in out["caveats"]}


def test_benchmark_fit_inline_linear_hand_values(server):
    x = records([(iso, "2020", "X", v) for iso, v in {"AAA": 1.0, "BBB": 2.0, "CCC": 3.0, "DDD": 4.0}.items()])
    y = records([(iso, "2020", "Y", v) for iso, v in {"AAA": 3.0, "BBB": 5.0, "CCC": 7.0, "DDD": 9.5}.items()])
    out = ok(server, "benchmark_fit", {"x": x, "y": y, "period": "2020", "kind": "linear", "log_x": False, "focus": "DDD"})
    fit = out["meta"]["fit"]
    # Sxy = 10.75, Sxx = 5 -> b1 = 2.15, b0 = 6.125 - 2.15 * 2.5 = 0.75
    assert fit["coefficients"]["b1"] == pytest.approx(2.15, rel=1e-12)
    assert fit["coefficients"]["b0"] == pytest.approx(0.75, rel=1e-12)
    assert fit["n"] == 4
    assert out["meta"]["focus"]["predicted"] == pytest.approx(0.75 + 2.15 * 4, rel=1e-12)


# ---------------------------------------------------------------------------
# get_series (offline, from a seeded cache) and errors
# ---------------------------------------------------------------------------
def test_get_series_from_seeded_cache_then_chart(server, fixtures_dir, tmp_path):
    url, params = build_request("NY.GDP.MKTP.KD.ZG", ["KEN", "LMC", "SSA"], 2019, 2021)
    payload = (fixtures_dir / "worldbank_indicator_single_page_synthetic.json").read_bytes()
    Cache().put("worldbank", url, params, payload)  # the isolated test cache (CGJRKIT_CACHE)

    out = ok(server, "get_series", {
        "connector": "worldbank", "method": "fetch_indicators",
        "params": {"indicators": "NY.GDP.MKTP.KD.ZG", "countries": ["KEN", "LMC", "SSA"], "start": 2019, "end": 2021},
        "max_rows": 3,
    })
    assert out["connector"] == "worldbank" and out["rows"] == 9 and len(out["head"]) == 3
    (prov,) = out["provenance"]
    assert prov["source"] == "worldbank" and prov["endpoint"] == url and prov["payload_sha256"]
    assert "WB_FORECAST" in {c["code"] for c in out["caveats"]}
    assert out["meta"]["aggregates"] == ["LMC", "SSA"]
    assert out["source_line"] == "Source: World Bank, World Development Indicators (last updated 2000-01-01)."

    chart = ok(server, "render_chart", {
        "result": out["result_id"], "kind": "line", "output_dir": str(tmp_path), "formats": ["vl.json"],
        "options": {"focus": "KEN"},
    })
    spec = json.loads(Path(chart["files"]["vl.json"]).read_text())
    assert spec["$schema"].startswith("https://vega.github.io/schema/vega-lite/")


def test_get_series_errors_are_reported_to_the_client(server):
    assert "unknown connector" in error_text(server, "get_series", {"connector": "nope"})
    msg = error_text(server, "get_series", {"connector": "worldbank", "method": "fetch_countries"})
    assert "no data method" in msg and "fetch_indicators" in msg
    msg = error_text(server, "get_series", {"connector": "worldbank", "params": {"countries": ["KEN"]}})
    assert "missing a required argument: 'indicators'" in msg
    msg = error_text(server, "get_series", {"connector": "worldbank", "params": {"indicators": "NY.GDP.MKTP.KD.ZG"}})
    assert "NetworkBlocked" in msg and "api.worldbank.org" in msg  # offline, nothing cached
    # no arbitrary URLs, local files or secrets through tool arguments
    msg = error_text(server, "get_series", {"connector": "pwt", "params": {"url": "https://example.org/x.xlsx"}})
    assert "'url' refused" in msg
    msg = error_text(server, "get_series", {"connector": "wpp", "method": "fetch_api", "params": {
        "indicator_ids": [49], "locations": [404], "start": 2000, "end": 2001, "token": "secret"}})
    assert "'token' refused" in msg and "secret" not in msg.replace("'token' refused", "")


def test_bad_references_and_arguments(server, tmp_path):
    assert "unknown result_id" in error_text(server, "growth_accounting", {"panel": "res_missing", "periods": {"x": ["2000", "2010"]}})
    assert "base year, end year" in error_text(server, "growth_accounting", {"panel": PWT_PANEL, "periods": {"x": ["2000"]}})
    ga = ok(server, "growth_accounting", {"panel": PWT_PANEL, "periods": {"2000s": ["2000", "2010"]}})
    assert "stem" in error_text(server, "render_chart", {"result": ga["result_id"], "kind": "stacked", "output_dir": str(tmp_path), "stem": "../escape"})
    assert "unknown formats" in error_text(server, "render_chart", {"result": ga["result_id"], "kind": "stacked", "output_dir": str(tmp_path), "formats": ["gif"]})
    assert "at least one" in error_text(server, "run_caveat_check", {"results": []})


def test_blocked_result_cannot_be_charted(server, tmp_path):
    frame = pd.DataFrame(records([("AAA", "2020", "IND", 1.0), ("BBB", "2020", "IND", 2.0)]))
    blocked = Result(
        data=frame.astype({"value": "float64"}),
        provenance=[Provenance(source="synthetic", dataset="TEST")],
        caveats=[Caveat("synthetic_block", "block", "synthetic blocking caveat")],
    )
    result_id = server.cgjrkit_tools.store.put(blocked)
    msg = error_text(server, "render_chart", {"result": result_id, "kind": "bar", "output_dir": str(tmp_path), "options": {"period": "2020"}})
    assert "BLOCK" in msg and "synthetic_block" in msg
    assert not any(tmp_path.iterdir())
    report = ok(server, "run_caveat_check", {"results": [result_id]})
    assert report["status"] == "BLOCK"


# ---------------------------------------------------------------------------
# render_chart guards: provenance footer, files, caveat engine, result-valued options
# ---------------------------------------------------------------------------
LINE_ROWS = records([(iso, str(y), "IND", float(k + y - 2009)) for k, iso in enumerate(["AAA", "BBB", "CCC", "DDD"])
                     for y in range(2010, 2015)], country="(synthetic)")


def stored(server, rows=LINE_ROWS, caveats=()) -> str:
    """Store a Result with a complete synthetic provenance entry; return its id."""
    frame = pd.DataFrame(rows).astype({"value": "float64"})
    prov = Provenance(source="synthetic", dataset="TEST", citation="Synthetic test source", vintage="t1",
                      payload_sha256="0" * 64)
    return server.cgjrkit_tools.store.put(Result(data=frame, provenance=[prov], caveats=list(caveats)))


def test_render_chart_refuses_reserved_options(server, tmp_path):
    rid = stored(server)
    for key, value in (("source_line", "Source: Fabricated (synthetic)."), ("note", "x"), ("include_info", True),
                       ("right", rid)):
        msg = error_text(server, "render_chart", {"result": rid, "kind": "line", "output_dir": str(tmp_path),
                                                   "options": {"focus": "AAA", key: value}})
        assert f"option '{key}' refused" in msg
    assert not any(tmp_path.iterdir())
    out = ok(server, "render_chart", {"result": rid, "kind": "line", "output_dir": str(tmp_path), "formats": ["vl.json"],
                                      "note": "Note: Client note (synthetic).", "options": {"focus": "AAA"}})
    assert out["footer"][0] == "Note: Client note (synthetic)."
    assert out["footer"][-1] == "Source: Synthetic test source (t1)."


def test_render_chart_never_overwrites_files_it_did_not_write(server, tmp_path):
    rid = stored(server)
    args = {"result": rid, "kind": "line", "output_dir": str(tmp_path), "stem": "fig", "formats": ["vl.json"],
            "options": {"focus": "AAA"}}
    (tmp_path / "fig.csv").write_text("synthetic user file\n")
    msg = error_text(server, "render_chart", args)
    assert "refusing to overwrite" in msg and "fig.csv" in msg
    assert (tmp_path / "fig.csv").read_text() == "synthetic user file\n" and not (tmp_path / "fig.vl.json").exists()
    (tmp_path / "fig.csv").unlink()
    first = ok(server, "render_chart", args)
    again = ok(server, "render_chart", {**args, "title": "Re-rendered (synthetic)"})  # own files may be replaced
    assert again["files"] == first["files"]
    assert "Re-rendered (synthetic)" in Path(again["files"]["vl.json"]).read_text()
    fresh = create_server()  # another session did not write them
    rid2 = stored(fresh)
    assert "refusing to overwrite" in error_text(fresh, "render_chart", {**args, "result": rid2})


def test_render_chart_output_root_confines_writes(tmp_path, monkeypatch):
    root = tmp_path / "charts"
    server = create_server(output_root=root)
    rid = stored(server)
    base = {"result": rid, "kind": "line", "formats": ["vl.json"], "options": {"focus": "AAA"}}
    out = ok(server, "render_chart", {**base, "output_dir": "kenya"})  # relative: inside the root
    assert Path(out["files"]["vl.json"]).parent == (root / "kenya").resolve()
    for outside in (str(tmp_path / "elsewhere"), "../escape", str(root / ".." / "x")):
        assert "outside the output root" in error_text(server, "render_chart", {**base, "output_dir": outside})
    assert not (tmp_path / "elsewhere").exists() and not (tmp_path / "escape").exists()
    monkeypatch.setenv("CGJRKIT_OUTPUT_DIR", str(root))
    assert create_server().cgjrkit_tools.output_root == root.resolve()


def test_render_chart_runs_the_caveat_engine_and_refuses_blocks(server, tmp_path):
    units = {"GDPPC17": "constant 2017 international $ (PPP 2017)", "GDPPC21": "constant 2021 international $ (PPP 2021)"}
    rows = [dict(r, unit=units[r["indicator"]])
            for r in records([("AAA", str(y), ind, float(y - 2000)) for ind in units for y in (2010, 2011)])]
    msg = error_text(server, "render_chart", {"result": stored(server, rows), "kind": "line", "output_dir": str(tmp_path),
                                              "options": {"series": "indicator"}})
    assert "BLOCK" in msg and "ppp_mixing" in msg and "Nothing was written" in msg
    assert not any(tmp_path.iterdir())
    # engine footnotes reach the footer (forecast years) and the check is reported
    fc = [dict(r, obs_status="forecast" if r["period"] == "2014" else "") for r in LINE_ROWS]
    out = ok(server, "render_chart", {"result": stored(server, fc), "kind": "line", "output_dir": str(tmp_path),
                                      "formats": ["vl.json"], "options": {"focus": "AAA"}})
    assert out["caveat_check"]["status"] == "WARN" and out["status"] == "WARN"
    assert "forecast_years" in {c["code"] for c in out["caveat_check"]["caveats"]}
    assert any(line.startswith("Note: Values for 2014 onward are forecasts") for line in out["footer"])


def test_render_chart_resolves_result_ids_in_frame_options(server, tmp_path):
    rid = stored(server)
    pa = ok(server, "peer_aggregate", {"data": rid, "members": ["BBB", "CCC", "DDD"], "frozen_at": "2026-01-01"})
    out = ok(server, "render_chart", {"result": rid, "kind": "line", "output_dir": str(tmp_path), "formats": ["vl.json"],
                                      "options": {"focus": "AAA", "peers": [], "band": pa["result_id"]}})
    spec = json.loads(Path(out["files"]["vl.json"]).read_text())
    assert spec["usermeta"]["cgjrkit_chart"]["band"] == "Interquartile range"
    assert "peers.peer_aggregate" in spec["usermeta"]["cgjrkit_provenance"]["tools"]  # the band's provenance travels
    lo = stored(server, [dict(r, value=r["value"] - 1) for r in LINE_ROWS if r["iso3"] == "BBB"])
    hi = stored(server, [dict(r, value=r["value"] + 1) for r in LINE_ROWS if r["iso3"] == "BBB"])
    ok(server, "render_chart", {"result": rid, "kind": "line", "output_dir": str(tmp_path), "stem": "pair",
                                "formats": ["vl.json"], "options": {"focus": "AAA", "peers": [], "band": [lo, hi]}})
    msg = error_text(server, "render_chart", {"result": rid, "kind": "line", "output_dir": str(tmp_path), "stem": "typed",
                                              "options": {"focus": "AAA", "band": [[1, 2], [3, 4]]}})
    assert "options.band must be a result_id" in msg
    msg = error_text(server, "render_chart", {"result": rid, "kind": "scatter", "output_dir": str(tmp_path), "stem": "sz",
                                              "options": {"size": {"AAA": 5.0}}})
    assert "options.size must be a result_id" in msg
    assert "unknown result_id" in error_text(server, "render_chart", {
        "result": rid, "kind": "line", "output_dir": str(tmp_path), "stem": "u", "options": {"band": "res_missing"}})


def test_render_chart_stacked_defaults_and_where_filter(server, tmp_path):
    va = records([("KEN", "2011", "VA_A", 1000.0), ("KEN", "2011", "VA_I", 1000.0),
                  ("KEN", "2019", "VA_A", 1200.0), ("KEN", "2019", "VA_I", 1600.0)])
    emp = records([("KEN", "2011", "E_A", 100.0), ("KEN", "2011", "E_I", 100.0),
                   ("KEN", "2019", "E_A", 100.0), ("KEN", "2019", "E_I", 125.0)])
    ss = ok(server, "shift_share", {"va_by_sector": va, "emp_by_sector": emp, "start": "2011", "end": "2019",
                                    "va_sectors": {"VA_A": "A", "VA_I": "I"}, "emp_sectors": {"E_A": "A", "E_I": "I"}})
    out = ok(server, "render_chart", {"result": ss["result_id"], "kind": "stacked", "output_dir": str(tmp_path),
                                      "stem": "ss", "formats": ["vl.json"]})
    spec = json.loads(Path(out["files"]["vl.json"]).read_text())
    meta = spec["usermeta"]
    assert meta["cgjrkit_chart"]["total"] == "total" and meta["cgjrkit_chart"]["components"] == ["within", "between"]
    assert meta["cgjrkit_provenance"]["where"] == {"sector": "TOTAL"} and meta["cgjrkit_identity"]["ok"] is True
    by_sector = ok(server, "render_chart", {"result": ss["result_id"], "kind": "stacked", "output_dir": str(tmp_path),
                                            "stem": "ss_within", "formats": ["vl.json"],
                                            "options": {"where": {"component": "within"}, "component": "sector",
                                                        "total": "TOTAL"}})
    meta2 = json.loads(Path(by_sector["files"]["vl.json"]).read_text())["usermeta"]
    assert meta2["cgjrkit_chart"]["components"] == ["A", "I"] and meta2["cgjrkit_identity"]["ok"] is True
    msg = error_text(server, "render_chart", {"result": ss["result_id"], "kind": "stacked", "output_dir": str(tmp_path),
                                              "stem": "bad", "options": {"where": {"nope": "x"}}})
    assert "where: no column 'nope'" in msg


def test_get_series_refuses_the_budget_override(server):
    msg = error_text(server, "get_series", {"connector": "oecd", "params": {"flow": "X", "force": True}})
    assert "'force' refused" in msg and "20 requests per hour" in msg


# ---------------------------------------------------------------------------
# helpers and the stdio entry point
# ---------------------------------------------------------------------------
def test_jsonable_is_json_safe_and_bounded():
    out = jsonable({
        "nan": float("nan"), "inf": np.float64("inf"), "i": np.int64(3), "s": {"b", "a"},
        "long": list(range(120)), "df": pd.DataFrame({"v": [1.0, np.nan]}), "p": Path("/tmp/x"),
    }, max_items=50)
    json.dumps(out, allow_nan=False)
    assert out["nan"] is None and out["inf"] is None and out["i"] == 3 and out["s"] == ["a", "b"]
    assert len(out["long"]) == 51 and out["long"][-1] == "... (70 more items)"
    assert out["df"] == [{"v": 1.0}, {"v": None}] and out["p"] == "/tmp/x"


def test_stdio_entry_point_serves_the_tools(tmp_path):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "cgjrkit.mcp_server", "--offline", "--cache", str(tmp_path / "cache"),
              "--output-root", str(tmp_path / "charts")],
    )

    async def go():
        async with Client(params, read_timeout_seconds=60) as client:
            tools = await client.list_tools()
            res = await client.call_tool("growth_accounting", {"panel": PWT_PANEL, "periods": {"2000s": ["2000", "2010"]}})
            outside = await client.call_tool("render_chart", {
                "result": res.structured_content["result_id"], "kind": "stacked", "output_dir": str(tmp_path / "x")})
            return tools, res, outside

    tools, res, outside = anyio.run(go)
    assert {t.name for t in tools.tools} == EXPECTED_TOOLS
    assert outside.is_error and "outside the output root" in outside.content[0].text
    assert not res.is_error
    c = {row["component"]: row["value"] for row in res.structured_content["head"]}
    assert c["output"] == pytest.approx(10 * math.log(2), abs=1e-9)
