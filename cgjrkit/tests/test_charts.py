"""Tests for the chart layer (cgjrkit.charts).

All data are SYNTHETIC: small frames built here with round, obviously
artificial values and "(synthetic)" country labels. Nothing is fetched;
rendering uses vl-convert offline.
"""

from __future__ import annotations

import json
import math
import re

import numpy as np
import pandas as pd
import pytest

from cgjrkit.charts import benchmark as cb
from cgjrkit.charts import decomposition as cd
from cgjrkit.charts import render
from cgjrkit.charts import series as cs
from cgjrkit.charts import style
from cgjrkit.charts._common import AGG_GREYS, CONTEXT, DARK, PEER_COLORS, PeriodAxis, log_ticks
from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import make_frame


# ---------------------------------------------------------------------------
# synthetic inputs and spec helpers
# ---------------------------------------------------------------------------
NAMES = {
    "KEN": "Kenya (synthetic)",
    "GHA": "Ghana (synthetic)",
    "TZA": "Tanzania (synthetic)",
    "LMC": "Lower middle income (synthetic)",
    "UMC": "Upper middle income (synthetic)",
}


def growth_frame(years=range(2001, 2011), forecast_from: int | None = None) -> pd.DataFrame:
    """Synthetic annual growth: country i has value 1 + i + (year - 2001) / 10, KEN dips to -1 in 2009."""
    rows = []
    for i, (iso, name) in enumerate(NAMES.items()):
        for y in years:
            v = -1.0 if (iso == "KEN" and y == 2009) else 1.0 + i + (y - 2001) / 10
            rows.append({"iso3": iso, "country": name, "period": str(y), "value": v, "indicator": "SYN.GROWTH",
                         "indicator_label": "Synthetic growth", "unit": "%",
                         "obs_status": "forecast" if forecast_from and y >= forecast_from else ""})
    return make_frame(rows, source="synthetic", dataset="SYN")


def synth_result(df: pd.DataFrame, caveats=(), meta=None) -> Result:
    prov = [Provenance(source="synthetic", dataset="SYN", citation="Synthetic test source", vintage="test-1")]
    return Result(data=df, provenance=prov, caveats=list(caveats), meta=dict(meta or {}))


def units(spec):
    """Yield every unit (mark-bearing) spec in a Vega-Lite spec."""
    if isinstance(spec, dict):
        if "mark" in spec:
            yield spec
        for key in ("layer", "vconcat", "hconcat", "concat"):
            for sub in spec.get(key, []) or []:
                yield from units(sub)
        if isinstance(spec.get("spec"), dict):
            yield from units(spec["spec"])


def mark_type(unit) -> str:
    m = unit["mark"]
    return m if isinstance(m, str) else m["type"]


def marks(spec, kind):
    return [u for u in units(spec) if mark_type(u) == kind]


def scale_map(spec, channel="color", field=None) -> dict:
    """``{domain value: range value}`` of the first explicit scale on ``channel``."""
    for u in units(spec):
        enc = (u.get("encoding") or {}).get(channel)
        if isinstance(enc, dict) and isinstance(enc.get("scale"), dict) and "domain" in enc["scale"]:
            if field and enc.get("field") != field:
                continue
            return dict(zip(enc["scale"]["domain"], enc["scale"]["range"]))
    return {}


def dataset(spec) -> pd.DataFrame:
    return render.plotted_data(spec)


def svg_texts(svg: str) -> list[str]:
    return [re.sub(r"<[^>]+>", " ", t).strip() for t in re.findall(r"<text[^>]*>(.*?)</text>", svg, flags=re.S)]


# ---------------------------------------------------------------------------
# line_vs_peers
# ---------------------------------------------------------------------------
def test_line_vs_peers_colours_roles_and_theme():
    chart = cs.line_vs_peers(growth_frame(), "KEN", title="Growth (synthetic)", subtitle="Percent")
    spec = render.to_spec(chart)
    cmap = scale_map(spec, "color")
    assert cmap[NAMES["KEN"]] == style.FOCUS
    # peers sorted by label take the peer colours in order; aggregates are grey
    assert cmap[NAMES["GHA"]] == PEER_COLORS[0]
    assert cmap[NAMES["TZA"]] == PEER_COLORS[1]
    assert cmap[NAMES["LMC"]] == style.GREY
    assert cmap[NAMES["UMC"]] == AGG_GREYS[1]
    dash = scale_map(spec, "strokeDash")
    assert dash[NAMES["KEN"]] == [1, 0] and dash[NAMES["LMC"]] != [1, 0]
    meta = spec["usermeta"]["cgjrkit_chart"]
    assert meta["focus"] == "KEN" and meta["peers"] == ["GHA", "TZA"] and meta["aggregates"] == ["LMC", "UMC"]
    assert meta["colors"][NAMES["KEN"]] == style.FOCUS
    # house theme applied regardless of the global Altair theme
    assert spec["config"]["font"] == style.FONT
    assert spec["config"]["range"]["category"] == style.PALETTE
    assert spec["title"] == {"text": "Growth (synthetic)", "subtitle": "Percent"}
    # focus is drawn last (on top) and thicker
    lines = marks(spec, "line")
    assert lines[-1]["mark"]["strokeWidth"] > lines[0]["mark"]["strokeWidth"]
    assert "focus" in lines[-1]["transform"][0]["filter"]
    # values cross zero -> zero rule
    rules = marks(spec, "rule")
    assert rules and render.plotted_data(spec).query("layer == 'reference'")["y"].tolist() == [0.0]


def test_line_vs_peers_band_events_and_csv_layers():
    df = growth_frame()
    lmc = df[df["iso3"] == "LMC"]
    band = (lmc.assign(value=lmc["value"] - 1), lmc.assign(value=lmc["value"] + 1))
    chart = cs.line_vs_peers(df, "KEN", peers=["GHA"], band=band, band_label="LMIC IQR (synthetic)",
                             events=[("2008", "2009", "GFC"), ("2010", "2010", "One year")])
    spec = render.to_spec(chart)
    assert scale_map(spec, "color")["LMIC IQR (synthetic)"] == style.BAND
    assert marks(spec, "area")
    rects = marks(spec, "rect")
    assert rects and rects[0]["mark"]["color"] == style.EVENT
    data = dataset(spec)
    ev = data[data["layer"] == "event"].set_index("label")
    assert ev.loc["GFC", "x0"] == 2007.5 and ev.loc["GFC", "x1"] == 2009.5
    assert ev.loc["One year", "x0"] == 2009.5 and ev.loc["One year", "x1"] == 2010.5
    b = data[data["layer"] == "band"].set_index("period")
    assert b.loc["2001", "low"] == pytest.approx(3.0) and b.loc["2001", "high"] == pytest.approx(5.0)
    # explicit peers: TZA left out, aggregates kept
    s = data[data["layer"] == "series"]
    assert set(s["series"]) == {"KEN", "GHA", "LMC", "UMC"}


def test_line_band_from_peer_aggregate_frame():
    rows = []
    for stat, shift in (("p25", -1.0), ("p75", 1.0), ("median", 0.0)):
        for y in (2001, 2002):
            rows.append({"iso3": f"STRUCT:{stat}", "country": f"Structural (synthetic) ({stat})", "period": str(y),
                         "value": 10.0 + shift, "indicator": "SYN", "stat": stat, "group": "STRUCT"})
    agg = pd.DataFrame(rows)
    main = pd.DataFrame([{"iso3": "KEN", "country": "Kenya (synthetic)", "period": str(y), "value": 10.0 + y - 2001,
                          "indicator": "SYN"} for y in (2001, 2002)])
    chart = cs.line_vs_peers(pd.concat([main, agg[agg["stat"] == "median"]]), "KEN", band=agg)
    data = dataset(render.to_spec(chart))
    band = data[data["layer"] == "band"]
    assert band["low"].tolist() == [9.0, 9.0] and band["high"].tolist() == [11.0, 11.0]
    # the non-ISO peer aggregate code is treated as an aggregate (grey)
    meta = render.to_spec(chart)["usermeta"]["cgjrkit_chart"]
    assert meta["aggregates"] == ["STRUCT:median"]
    with pytest.raises(ValueError, match="no 'p10' rows"):
        cs.line_vs_peers(main, "KEN", band=agg, band_stats=("p10", "p90"))


def test_line_index_base_rebases_and_validates():
    df = growth_frame()
    chart = cs.line_vs_peers(df, "KEN", index_base="2001")
    spec = render.to_spec(chart)
    s = dataset(spec).query("layer == 'series'")
    at_base = s[s["period"] == "2001"]
    assert np.allclose(at_base["value"], 100.0)
    gha = s[(s["series"] == "GHA") & (s["period"] == "2003")]["value"].iloc[0]
    assert gha == pytest.approx((2.0 + 0.2) / 2.0 * 100)
    assert dataset(spec).query("layer == 'reference'")["y"].tolist() == [100.0]
    assert spec["layer"][-1]["encoding"]["y"]["title"] == "Index (2001 = 100)"
    with pytest.raises(ValueError, match="index_base"):
        cs.line_vs_peers(df, "KEN", index_base="1999")
    with pytest.raises(ValueError, match="cannot be combined with band"):
        cs.line_vs_peers(df, "KEN", index_base="2001", band=(df, df))


def test_line_forecast_segments_and_footer_note():
    chart = cs.line_vs_peers(growth_frame(range(2001, 2008), forecast_from=2006), "KEN", peers=[])
    spec = render.to_spec(chart)
    dashed = [u for u in marks(spec, "line") if u["mark"].get("strokeDash") == cs.FORECAST_DASH]
    assert dashed, "forecast layer drawn dashed"
    data = dataset(spec).query("layer == 'series'")
    bridge = data[data["segment"] == "forecast_bridge"]
    assert set(bridge["period"]) == {"2005"} and len(bridge) == 3  # KEN + two aggregates
    assert "Note: Dashed segments are forecasts." in render.footer_lines(chart)


def test_line_input_validation():
    df = growth_frame()
    with pytest.raises(ValueError, match="focus 'XXX'"):
        cs.line_vs_peers(df, "XXX")
    with pytest.raises(ValueError, match="peers \\['NGA'\\]"):
        cs.line_vs_peers(df, "KEN", peers=["NGA"])
    two = pd.concat([df, df.assign(indicator="OTHER")])
    with pytest.raises(ValueError, match="several indicators"):
        cs.line_vs_peers(two, "KEN")
    with pytest.raises(ValueError, match="more than one value"):
        cs.line_vs_peers(pd.concat([df, df]), "KEN")
    gha = df[df.iso3 == "GHA"]
    many = pd.concat([df[df.iso3 == "KEN"]] + [gha.assign(iso3=f"PA{chr(65 + i)}", country=f"Peer {i} (synthetic)")
                                               for i in range(8)])
    with pytest.raises(ValueError, match="peer colours"):
        cs.line_vs_peers(many, "KEN")
    chart = cs.line_vs_peers(many, "KEN", peer_style="grey")
    spec = render.to_spec(chart)
    assert any(u["mark"].get("color") == CONTEXT for u in marks(spec, "line"))
    assert len(spec["usermeta"]["cgjrkit_chart"]["context"]) == 8


def test_line_multi_indicator_for_one_country():
    rows = [{"iso3": "KEN", "country": "Kenya (synthetic)", "period": str(y), "indicator": code,
             "indicator_label": label, "value": base + (y - 2001)}
            for code, label, base in (("tfp", "TFP (synthetic)", 100.0), ("kpw", "Capital per worker (synthetic)", 50.0))
            for y in (2001, 2002, 2003)]
    chart = cs.line_vs_peers(make_frame(rows, source="synthetic"), focus="kpw", series="indicator", index_base="2001",
                             end_labels=True)
    spec = render.to_spec(chart)
    cmap = scale_map(spec, "color")
    assert cmap["Capital per worker (synthetic)"] == style.FOCUS and cmap["TFP (synthetic)"] == PEER_COLORS[0]
    labels = dataset(spec).query("layer == 'end_label'")
    assert sorted(labels["label"]) == ["Capital per worker (synthetic)", "TFP (synthetic)"]


def test_period_axis_kinds():
    assert PeriodAxis.from_periods(["2002", "2001"]).kind == "year"
    q = PeriodAxis.from_periods(["2020Q2", "2020Q1"])
    assert q.kind == "temporal" and q.position("2020Q2") == "2020-04-01"
    assert q.span("2020Q1", "2020Q4")[:2] == ("2020-01-01", "2021-01-01")
    m = PeriodAxis.from_periods(["2020-12", "2021-01"])
    assert m.span("2020-12", "2020-12")[1] == "2021-01-01"
    o = PeriodAxis.from_periods(["2011-19", "2001-10", "2020", "FY2024/25"])
    assert o.kind == "ordinal" and o.order == ("2001-10", "2011-19", "2020", "FY2024/25")
    assert o.position("2020") == 2 and o.span("2020", "2020")[:2] == (1.5, 2.5)
    with pytest.raises(ValueError):
        o.position("1999")


def test_line_quarterly_axis_renders_year_ticks():
    rows = [{"iso3": iso, "country": iso, "period": f"{y}Q{q}", "value": 100.0 + q + (y - 2018) * 4, "indicator": "SYN"}
            for iso in ("KEN", "GHA") for y in range(2018, 2024) for q in range(1, 5)]
    chart = cs.line_vs_peers(pd.DataFrame(rows), "KEN", events=[("2020Q1", "2020Q4", "COVID")])
    spec = render.to_spec(chart)
    x = spec["layer"][0]["encoding"]["x"]
    assert x["type"] == "temporal" and x["scale"]["type"] == "utc"
    svg = render.render(chart, "svg")
    texts = svg_texts(svg)
    assert "2019" in texts and "2023" in texts and "July" not in texts


# ---------------------------------------------------------------------------
# bar_vs_peers and dual_axis
# ---------------------------------------------------------------------------
def test_bar_vs_peers_single_period_roles_sort_and_bold_focus():
    df = growth_frame()
    chart = cs.bar_vs_peers(df, "KEN", period="2010", value_labels=True)
    spec = render.to_spec(chart)
    cmap = scale_map(spec, "color")
    assert cmap == {"focus": style.FOCUS, "peer": PEER_COLORS[0], "aggregate": style.GREY}
    meta = spec["usermeta"]["cgjrkit_chart"]
    assert meta["order"] == ["UMC", "LMC", "TZA", "GHA", "KEN"]  # descending by 2010 value
    bar = marks(spec, "bar")[0]
    cond = bar["encoding"]["x"]["axis"]["labelFontWeight"]
    assert NAMES["KEN"] in cond["condition"]["test"] and cond["condition"]["value"] == "bold"
    assert len(marks(spec, "text")) == 2  # positive and negative value labels
    with pytest.raises(ValueError, match="not in the frame"):
        cs.bar_vs_peers(df, "KEN", period="1990")


def test_bar_vs_peers_grouped_horizontal():
    df = growth_frame()
    chart = cs.bar_vs_peers(df, "KEN", peers=["GHA"], period=["2001", "2010"], horizontal=True,
                            period_labels={"2001": "2001 (syn)"})
    spec = render.to_spec(chart)
    bar = marks(spec, "bar")[0]
    assert bar["encoding"]["yOffset"]["field"] == "period_label"
    assert bar["encoding"]["y"]["field"] == "label" and bar["encoding"]["x"]["field"] == "value"
    assert scale_map(spec, "color") == {"2001 (syn)": style.PALETTE[0], "2010": style.PALETTE[1]}


def test_dual_axis_independent_scales_and_axis_colours():
    df = growth_frame()
    left = df[df["iso3"] == "KEN"].assign(indicator_label="Growth (synthetic)")
    right = df[df["iso3"] == "GHA"].assign(indicator="SYN.OTHER", indicator_label="Other (synthetic)", unit="index")
    chart = cs.dual_axis(left, right, left_mark="bar", events=[("2008", "2009", "Episode")])
    spec = render.to_spec(chart)
    assert spec["resolve"]["scale"]["y"] == "independent"
    bars, lines = marks(spec, "bar"), marks(spec, "line")
    assert bars[0]["encoding"]["y"]["axis"]["orient"] == "left"
    assert lines[0]["encoding"]["y"]["axis"]["orient"] == "right"
    cmap = scale_map(spec, "color")
    assert cmap == {"Growth (synthetic)": style.PALETTE[0], "Other (synthetic) (right axis)": style.PALETTE[1]}
    assert bars[0]["encoding"]["y"]["axis"]["titleColor"] == style.PALETTE[0]
    assert lines[0]["encoding"]["y"]["axis"]["titleColor"] == style.PALETTE[1]
    assert lines[0]["encoding"]["y"]["title"] == "index"
    assert render.render(chart, "svg").startswith("<svg")


# ---------------------------------------------------------------------------
# benchmark charts
# ---------------------------------------------------------------------------
def fit_chart_frame() -> pd.DataFrame:
    """Synthetic frame in the documented income_conditional_fit output layout."""
    rows = []
    for i, iso in enumerate(["AAA", "BBB", "CCC", "KEN", "GHA", "DDD"]):
        x = 1000.0 * 2 ** i
        rows.append({"role": "country", "iso3": iso, "country": f"{iso} (synthetic)", "period": "2023", "x": x,
                     "y": 10.0 + 5 * i, "fitted": 10.0 + 5 * i, "residual": 0.0, "ci_lower": np.nan, "ci_upper": np.nan,
                     "in_fit": iso != "DDD", "is_focus": iso == "KEN",
                     "exclusion_reason": "y_out_of_bounds" if iso == "DDD" else "", "obs_status": ""})
    for g in np.linspace(1000, 32000, 5):
        rows.append({"role": "curve", "iso3": "", "country": "", "period": "2023", "x": g, "y": np.nan,
                     "fitted": 10.0 + 5 * math.log2(g / 1000), "residual": np.nan, "ci_lower": 8.0, "ci_upper": 40.0,
                     "in_fit": False, "is_focus": False, "exclusion_reason": "", "obs_status": ""})
    for p, x in (("2011", 3000.0), ("2015", 5000.0)):
        rows.append({"role": "focus_path", "iso3": "KEN", "country": "KEN (synthetic)", "period": p, "x": x, "y": 20.0,
                     "fitted": 20.0, "residual": 0.0, "ci_lower": np.nan, "ci_upper": np.nan, "in_fit": False,
                     "is_focus": True, "exclusion_reason": "", "obs_status": ""})
    return pd.DataFrame(rows)


def test_scatter_vs_income_layers_colours_labels_and_trajectory():
    meta = {"fit": {"kind": "quadratic", "n": 5, "r2": 0.5, "log_x": True, "x_label": "GDP per capita (synthetic)",
                    "x_unit": "PPP 2021", "y_label": "Credit (synthetic)", "y_unit": "% of GDP", "ci": 0.95}}
    res = Result(fit_chart_frame(), [Provenance(source="synthetic", citation="Synthetic test source")], meta=meta)
    chart = cb.scatter_vs_income(res, peers=["GHA"], label_countries=["AAA"], ci=True)
    spec = render.to_spec(chart)
    meta_c = spec["usermeta"]["cgjrkit_chart"]
    assert meta_c["focus"] == "KEN" and meta_c["trajectory"] is True and meta_c["log_x"] is True
    circles = marks(spec, "circle")
    assert any(u["mark"].get("color") == style.FOCUS and u["mark"].get("size", 0) >= 100 for u in circles)
    assert any(u["mark"].get("color") == style.GREY for u in circles)
    assert any(u["mark"].get("color") == DARK for u in marks(spec, "line"))  # fitted curve
    assert any(u["mark"].get("color") == style.BAND for u in marks(spec, "area"))  # CI band
    assert scale_map(spec, "color") == {"GHA": PEER_COLORS[0]}
    hollow = [u for u in marks(spec, "point") if u["mark"].get("filled") is False]
    assert hollow, "excluded countries drawn hollow"
    x = circles[0]["encoding"]["x"]
    assert x["scale"]["type"] == "log" and x["axis"]["values"] == [1000.0, 2000.0, 5000.0, 10000.0, 20000.0]
    assert x["title"] == "GDP per capita (synthetic) (PPP 2021), log scale"
    data = dataset(spec)
    text = dict(zip(data["iso3"] + "|" + data["kind"], data["label_text"]))
    assert text["KEN|focus"] == "KEN (synthetic) 2023"
    assert text["AAA|other"] == "AAA" and text["GHA|peer"] == "GHA" and text["BBB|other"] == ""
    assert sorted(data.loc[data["kind"] == "path", "label_text"]) == ["2011", "2015"]
    footer = render.footer_lines(chart)
    assert any("quadratic fit" in line and "5 countries" in line for line in footer)
    assert "Note: Hollow markers are left out of the fit." in footer


def test_scatter_from_frames_plain_and_with_fit():
    xr, yr = [], []
    for i in range(12):
        iso = "KEN" if i == 5 else f"C{chr(65 + i)}X"
        for p in ("2015", "2023"):
            xr.append({"iso3": iso, "country": f"{iso} (synthetic)", "period": p, "value": 1000.0 * (i + 1),
                       "indicator": "SYN.X", "unit": "constant 2021 international $ (PPP 2021)"})
            yr.append({"iso3": iso, "country": f"{iso} (synthetic)", "period": p,
                       "value": 10.0 + 3 * math.log(i + 1) + (1 if i % 2 else -1) + (2 if p == "2023" else 0),
                       "indicator": "SYN.Y", "unit": "% of GDP"})
    xr.append({"iso3": "LMC", "country": "LMIC (synthetic)", "period": "2023", "value": 3000.0, "indicator": "SYN.X"})
    yr.append({"iso3": "LMC", "country": "LMIC (synthetic)", "period": "2023", "value": 12.0, "indicator": "SYN.Y"})
    X = synth_result(make_frame(xr, source="synthetic"))
    Y = synth_result(make_frame(yr, source="synthetic"))
    with pytest.raises(ValueError, match="period="):
        cb.scatter_vs_income((X, Y), "KEN")
    plain = cb.scatter_vs_income((X, Y), "KEN", period="2023", trajectory=["2015"])
    data = dataset(render.to_spec(plain))
    assert "LMC" not in set(data["iso3"])  # aggregates are not countries
    assert data["kind"].value_counts()["path"] == 1 and not marks(render.to_spec(plain), "area")
    fitted = cb.scatter_vs_income((X, Y), "KEN", period="2023", fit_kind="linear", trajectory=["2015"])
    spec = render.to_spec(fitted)
    assert any(u["mark"].get("color") == DARK for u in marks(spec, "line"))
    footer = render.footer_lines(fitted)
    assert footer[-1] == "Source: Synthetic test source (test-1)."
    assert any(line.startswith("Note: Line shows a linear fit") for line in footer)


def test_kde_plot_from_density_frame_and_from_canonical():
    rows = []
    for p in ("2001", "2023"):
        for x in np.linspace(0, 100, 11):
            rows.append({"role": "density", "period": p, "iso3": "", "country": "", "x": x, "density": 0.01,
                         "percentile": np.nan, "n": 10, "is_focus": False})
        rows.append({"role": "country", "period": p, "iso3": "KEN", "country": "Kenya (synthetic)", "x": 30.0,
                     "density": 0.01, "percentile": 48.0 if p == "2001" else 38.0, "n": 10, "is_focus": True})
        rows.append({"role": "focus", "period": p, "iso3": "KEN", "country": "Kenya (synthetic)", "x": 30.0,
                     "density": 0.01, "percentile": 48.0 if p == "2001" else 38.0, "n": 10, "is_focus": True})
    chart = cb.kde_plot(pd.DataFrame(rows), focus="KEN", rug=True)
    spec = render.to_spec(chart)
    assert scale_map(spec, "color") == {"2001": style.PALETTE[0], "2023": style.PALETTE[1]}
    rule = marks(spec, "rule")[0]
    assert rule["mark"]["strokeDash"] == [4, 3]
    labels = dataset(spec).query("layer == 'focus'")["label_text"].tolist()
    assert labels == ["Kenya (synthetic) 2001: 30.0 (48th percentile)", "Kenya (synthetic) 2023: 30.0 (38th percentile)"]
    assert marks(spec, "tick")
    # canonical frame -> kernel_density is run first, provenance goes to the footer
    canon = make_frame([{"iso3": f"C{chr(65 + i)}X" if i != 3 else "KEN", "country": "synthetic", "period": p,
                         "value": 10.0 * i + (5 if p == "2023" else 0), "indicator": "SYN"}
                        for i in range(8) for p in ("2001", "2023")], source="synthetic")
    chart2 = cb.kde_plot(synth_result(canon), focus="KEN")
    d2 = dataset(render.to_spec(chart2))
    assert set(d2["role"]) >= {"density", "focus"}
    assert render.footer_lines(chart2)[-1] == "Source: Synthetic test source (test-1)."


def test_ordinal_suffixes():
    assert [cb.ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21, 22, 38, 101, 111)] == [
        "1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "38th", "101st", "111th"]


def test_slope_rank_ranks_percentiles_and_balance():
    rows = []
    vals = {"KEN": (50.0, 20.0), "GHA": (40.0, 30.0), "TZA": (30.0, 40.0), "UGA": (20.0, 50.0), "RWA": (10.0, None)}
    for iso, (a, b) in vals.items():
        rows.append({"iso3": iso, "country": f"{iso} (synthetic)", "period": "2001", "value": a, "indicator": "SYN"})
        if b is not None:
            rows.append({"iso3": iso, "country": f"{iso} (synthetic)", "period": "2023", "value": b, "indicator": "SYN"})
    rows.append({"iso3": "LMC", "country": "LMIC (synthetic)", "period": "2001", "value": 99.0, "indicator": "SYN"})
    rows.append({"iso3": "LMC", "country": "LMIC (synthetic)", "period": "2023", "value": 99.0, "indicator": "SYN"})
    df = make_frame(rows, source="synthetic")
    chart = cb.slope_rank(df, focus="KEN", peers=["GHA"], measure="rank")
    spec = render.to_spec(chart)
    d = dataset(spec)
    ken = d[d["iso3"] == "KEN"].set_index("period")["y"]
    assert ken["2001"] == 1 and ken["2023"] == 4  # RWA dropped (balanced), LMC never ranked
    assert "RWA" not in set(d["iso3"]) and "LMC" not in set(d["iso3"])
    assert spec["usermeta"]["cgjrkit_chart"]["n"] == {"2001": 4, "2023": 4}
    assert scale_map(spec, "color") == {"KEN (synthetic)": style.FOCUS, "GHA (synthetic)": PEER_COLORS[0]}
    assert "Note: Ranks among 4 countries with data in every period shown." in render.footer_lines(chart)
    y = marks(spec, "line")[0]["encoding"]["y"]
    assert y["scale"]["reverse"] is True and y["axis"] is None
    assert d.loc[(d["iso3"] == "KEN") & (d["period"] == "2001"), "label_left"].iloc[0] == "KEN (synthetic) 1"
    pct = dataset(render.to_spec(cb.slope_rank(df, focus="KEN", measure="percentile", balanced=False)))
    kp = pct[pct["iso3"] == "KEN"].set_index("period")["y"]
    assert kp["2001"] == pytest.approx(100.0) and kp["2023"] == pytest.approx(25.0)
    val = render.to_spec(cb.slope_rank(df, focus="KEN"))
    assert val["usermeta"]["cgjrkit_chart"]["aggregates"] == ["LMC"]


# ---------------------------------------------------------------------------
# stacked contributions
# ---------------------------------------------------------------------------
def contrib_frame() -> pd.DataFrame:
    """Synthetic sector contributions; the '2020' total deliberately differs from the sum (identity flag)."""
    comps = {"2001-2010": [1.0, 0.5, 2.0], "2011-2019": [0.5, -0.5, 3.0], "2020": [-1.0, -2.0, 2.0]}
    rows = []
    for w, vals in comps.items():
        for c, v in zip(["agr", "ind", "ser"], vals):
            rows.append({"period": w, "component": c, "value": v, "unit": "percentage points"})
        rows.append({"period": w, "component": "total_growth", "value": 0.5 if w == "2020" else sum(vals),
                     "unit": "percentage points"})
    return pd.DataFrame(rows)


def test_stacked_contributions_geometry_total_and_identity_flag():
    chart = cd.stacked_contributions(contrib_frame(), component_labels={"agr": "Agriculture", "ind": "Industry",
                                                                        "ser": "Services"},
                                     value_labels=True, total_labels="g = {:.1f}%",
                                     period_labels={"2001-2010": "2001-10\ng = 3.5%"})
    spec = render.to_spec(chart)
    d = dataset(spec)
    seg = d[d["layer"] == "segment"].set_index(["x", "component"])
    # positives stack up from 0 in component order, negatives stack down
    assert (seg.loc[("2011-2019", "agr"), "y0"], seg.loc[("2011-2019", "agr"), "y1"]) == (0.0, 0.5)
    assert (seg.loc[("2011-2019", "ind"), "y0"], seg.loc[("2011-2019", "ind"), "y1"]) == (0.0, -0.5)
    assert (seg.loc[("2011-2019", "ser"), "y0"], seg.loc[("2011-2019", "ser"), "y1"]) == (0.5, 3.5)
    assert (seg.loc[("2020", "ind"), "y0"], seg.loc[("2020", "ind"), "y1"]) == (-1.0, -3.0)
    tot = d[d["layer"] == "total"].set_index("x")
    assert tot.loc["2001-2010", "value"] == 3.5 and tot.loc["2020", "sum_components"] == -1.0
    assert tot.loc["2020", "total_text"] == "g = 0.5%"
    assert tot.loc["2011-2019", "y_top"] == 3.5
    meta = spec["usermeta"]
    assert meta["cgjrkit_chart"]["total"] == "total_growth"
    assert meta["cgjrkit_chart"]["colors"] == {"Agriculture": style.PALETTE[0], "Industry": style.PALETTE[1],
                                               "Services": style.PALETTE[2]}
    gaps = meta["cgjrkit_identity"]["gaps"]
    assert [g["x"] for g in gaps] == ["2020"] and gaps[0]["gap"] == pytest.approx(1.5)
    assert any("do not add up" in line and "2020 (+1.5)" in line for line in render.footer_lines(chart))
    dot = marks(spec, "point")[0]
    assert dot["mark"]["color"] == DARK and dot["encoding"]["shape"]["scale"]["domain"] == ["Total growth"]
    bar = marks(spec, "bar")[0]
    assert '"2001-2010": ["2001-10", "g = 3.5%"]' in bar["encoding"]["x"]["axis"]["labelExpr"]
    assert bar["mark"]["stroke"] == "white"  # 1px surface gap between segments
    texts = marks(spec, "text")
    assert len(texts) == 2  # segment labels + total labels


def test_stacked_contributions_options():
    df = contrib_frame()
    # explicit sum total, no identity check possible
    spec = render.to_spec(cd.stacked_contributions(df[df.component != "total_growth"], total="auto", total_mark="tick"))
    assert spec["usermeta"]["cgjrkit_chart"]["total_mode"] == "sum"
    assert "cgjrkit_identity" not in spec["usermeta"]
    assert marks(spec, "tick")
    line = render.to_spec(cd.stacked_contributions(df, total_mark="line", horizontal=True))
    assert marks(line, "line") and marks(line, "bar")[0]["encoding"]["y"]["field"] == "x"
    none = render.to_spec(cd.stacked_contributions(df, total=None))
    assert not marks(none, "point")
    with pytest.raises(ValueError, match="total component"):
        cd.stacked_contributions(df, total="missing")
    with pytest.raises(ValueError, match="several values"):
        cd.stacked_contributions(pd.concat([df.assign(iso3="KEN"), df.assign(iso3="GHA")]))
    facet = cd.stacked_contributions(pd.concat([df.assign(country="Kenya (synthetic)"),
                                                df.assign(country="Ghana (synthetic)")]),
                                     facet="country", component_order=["ser", "agr", "ind"])
    fspec = render.to_spec(facet)
    assert fspec["facet"]["column"]["field"] == "facet"
    assert fspec["usermeta"]["cgjrkit_chart"]["components"] == ["ser", "agr", "ind"]
    assert len(fspec["usermeta"]["cgjrkit_identity"]["gaps"]) == 2


# ---------------------------------------------------------------------------
# rendering, footers, Result integration
# ---------------------------------------------------------------------------
def test_save_chart_writes_files_offline_with_footer(tmp_path):
    chart = cs.line_vs_peers(growth_frame(), "KEN", title="Growth (synthetic)")
    chart = render.add_footer(chart, "Source: Synthetic test source.", "Note: Values are synthetic.")
    paths = render.save_chart(chart, tmp_path / "out" / "fig1", formats=("vl.json", "svg", "png", "pdf"))
    assert set(paths) == {"vl.json", "svg", "png", "pdf", "csv"}
    assert all(p.exists() and p.stat().st_size > 0 for p in paths.values())
    assert paths["png"].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert paths["pdf"].read_bytes()[:5] == b"%PDF-"
    texts = svg_texts(paths["svg"].read_text())
    assert "Source: Synthetic test source." in texts and "Note: Values are synthetic." in texts
    assert "Growth (synthetic)" in texts
    spec = json.loads(paths["vl.json"].read_text())
    assert spec["usermeta"]["cgjrkit_footer_drawn"] is True
    assert spec["usermeta"]["cgjrkit_footer"][-1] == "Source: Synthetic test source."
    assert len(spec["vconcat"]) == 2 and spec["resolve"]["legend"]["color"] == "independent"
    footer_marks = [u["mark"]["text"] for u in units(spec["vconcat"][1])]
    assert footer_marks == ["Note: Values are synthetic.", "Source: Synthetic test source."]
    csv = pd.read_csv(paths["csv"])
    assert {"layer", "series", "period", "value"} <= set(csv.columns)
    assert len(csv[csv["layer"] == "series"]) == 50
    # the vl.json is exactly what was rendered: re-rendering it gives the same SVG
    assert render.render(spec, "svg") == paths["svg"].read_text()


def test_save_chart_custom_data_and_no_footer(tmp_path):
    chart = cs.bar_vs_peers(growth_frame(), "KEN", period="2005")
    custom = pd.DataFrame({"a": [1, 2]})
    paths = render.save_chart(chart, tmp_path / "bar", data_frame=custom, formats=("svg",), footer=False)
    assert pd.read_csv(paths["csv"]).equals(custom)
    with pytest.raises(ValueError, match="unknown formats"):
        render.save_chart(chart, tmp_path / "x", formats=("gif",))


def test_render_refuses_external_data_urls():
    spec = {"$schema": "https://vega.github.io/schema/vega-lite/v6.json", "data": {"url": "https://example.invalid/d.csv"},
            "mark": "point", "encoding": {"x": {"field": "a", "type": "quantitative"}}}
    with pytest.raises(ValueError, match="External data url not allowed"):
        render.render(spec, "svg", footer=False)


def test_render_html_is_self_contained():
    html = render.render(cs.bar_vs_peers(growth_frame(), "KEN", period="2005"), "html")
    assert "<html" in html.lower()
    assert not re.search(r"<script[^>]+src=[\"']https?://", html)


def test_footer_wraps_long_lines_and_render_is_deterministic(tmp_path):
    long = "Note: " + "synthetic words " * 40
    chart = render.add_footer(cs.bar_vs_peers(growth_frame(), "KEN", period="2005", width=300), "Source: S.", long)
    spec = render.with_footer(render.to_spec(chart))
    lines = [u["mark"]["text"] for u in units(spec["vconcat"][1])]
    assert len(lines) > 3 and lines[-1] == "Source: S."
    assert all(len(line) <= int(300 / (10 * 0.53)) for line in lines)
    a = render.save_chart(chart, tmp_path / "a", formats=("vl.json", "svg"))
    b = render.save_chart(chart, tmp_path / "b", formats=("vl.json", "svg"))
    assert a["svg"].read_text() == b["svg"].read_text()
    assert a["vl.json"].read_text() == b["vl.json"].read_text()


def test_add_footer_keeps_chart_metadata_and_replaces_source():
    chart = cs.line_vs_peers(growth_frame(), "KEN")
    chart = render.add_footer(chart, "Source: First.", ["Note: one."])
    chart = render.add_footer(chart, "Source: Second.", "Note: two.")
    assert render.footer_lines(chart) == ["Note: one.", "Note: two.", "Source: Second."]
    assert render.to_spec(chart)["usermeta"]["cgjrkit_chart"]["focus"] == "KEN"
    # style.with_source (house helper) still yields a footer the renderer draws
    plain = style.with_source(cs.bar_vs_peers(growth_frame(), "KEN", period="2005"), "Source: S.", "Note: N.")
    assert render.footer_lines(plain) == ["Note: N.", "Source: S."]


def test_to_spec_leaves_global_altair_state_alone():
    import altair as alt

    before = alt.theme.active
    spec = render.to_spec(cs.bar_vs_peers(growth_frame(), "KEN", period="2005"))
    assert alt.theme.active == before and spec["config"]["font"] == style.FONT


def test_chart_from_result_attaches_source_and_caveats():
    cav = [Caveat("forecast_values", "warn", "Some values are forecasts (synthetic)."),
           Caveat("context", "info", "Context only (synthetic).")]
    res = synth_result(growth_frame(), cav, meta={"aggregates": ["LMC", "UMC"]})
    res.provenance.append(Provenance(source="cgjrkit", dataset="harmonize", tool="harmonize.rebase"))
    chart = render.chart_from_result(res, "line", focus="KEN", note="Note: Synthetic chart.")
    lines = render.footer_lines(chart)
    assert lines == ["Note: Synthetic chart.", "Note: Some values are forecasts (synthetic).",
                     "Source: Synthetic test source (test-1)."]
    assert res.source_line() != lines[-1]  # cgjrkit method entries are not data sources
    with_info = render.footer_lines(render.chart_from_result(res, "bar", focus="KEN", period="2005", include_info=True))
    assert "Note: Context only (synthetic)." in with_info
    um = render.to_spec(chart)["usermeta"]["cgjrkit_provenance"]
    assert um["data_sha256"] == res.data_sha256 and {"code": "forecast_values", "severity": "warn"} in um["caveats"]
    svg = render.render(chart, "svg")
    assert "Source: Synthetic test source (test-1)." in svg_texts(svg)


def test_chart_from_result_blocks_and_validates():
    blocked = synth_result(growth_frame(), [Caveat("ppp_mix", "block", "Mixes PPP 2017 and 2021 (synthetic).")])
    with pytest.raises(render.BlockedResultError, match="ppp_mix"):
        render.chart_from_result(blocked, "line", focus="KEN")
    ok = synth_result(growth_frame())
    with pytest.raises(ValueError, match="unknown chart kind"):
        render.chart_from_result(ok, "pie")
    with pytest.raises(ValueError, match="right="):
        render.chart_from_result(ok, "dual_axis")
    right = synth_result(growth_frame()[lambda d: d.iso3 == "GHA"].assign(indicator="SYN.R"),
                         [Caveat("modelled_values", "warn", "Modelled (synthetic).")])
    dual = render.chart_from_result(synth_result(growth_frame()[lambda d: d.iso3 == "KEN"]), "dual_axis", right=right)
    assert "Note: Modelled (synthetic)." in render.footer_lines(dual)


def test_chart_from_result_stacked_and_scatter_kinds():
    res = synth_result(contrib_frame())
    chart = render.chart_from_result(res, "stacked", title="Synthetic")
    assert render.footer_lines(chart)[-1] == "Source: Synthetic test source (test-1)."
    meta = {"fit": {"kind": "linear", "n": 5, "r2": 0.9, "log_x": True}}
    sc = render.chart_from_result(Result(fit_chart_frame(), [Provenance(source="synthetic", citation="Syn")], meta=meta),
                                  "scatter")
    assert render.footer_lines(sc)[-1] == "Source: Syn."


def test_log_ticks():
    assert log_ticks(900, 110000) == [1000.0, 2000.0, 5000.0, 10000.0, 20000.0, 50000.0, 100000.0]
    assert log_ticks(1, 1e9) == [1.0, 10.0, 100.0, 1000.0, 10000.0, 100000.0, 1000000.0, 10000000.0, 100000000.0, 1e9]
    assert log_ticks(-1, 10) == []


def test_with_source_keeps_existing_chart_metadata():
    chart = style.with_source(cs.line_vs_peers(growth_frame(), "KEN"), "Source: S.", "Note: N.")
    usermeta = render.to_spec(chart)["usermeta"]
    assert usermeta["cgjrkit_footer"] == ["Note: N.", "Source: S."]
    assert usermeta["cgjrkit_chart"]["focus"] == "KEN"  # builder metadata survives


# ---------------------------------------------------------------------------
# Results given to builders: BLOCK refusal and provenance footers (review fixes)
# ---------------------------------------------------------------------------
BLOCK = Caveat("ppp_mixing", "block", "Mixes PPP 2017 and 2021 (synthetic).")
WARN = Caveat("syn_warn", "warn", "Synthetic warning.")


def density_canonical() -> pd.DataFrame:
    return make_frame([{"iso3": f"C{chr(65 + i)}X" if i != 3 else "KEN", "country": "synthetic", "period": p,
                        "value": 10.0 * i + (5 if p == "2023" else 0), "indicator": "SYN"}
                       for i in range(8) for p in ("2001", "2023")], source="synthetic")


def _builder_calls(make):
    """(name, call) for every public builder; ``make(frame)`` wraps a frame as the data argument."""
    g = growth_frame()
    return [
        ("line", lambda: cs.line_vs_peers(make(g), "KEN")),
        ("bar", lambda: cs.bar_vs_peers(make(g), "KEN", period="2005")),
        ("dual_axis", lambda: cs.dual_axis(make(g[g.iso3 == "KEN"]), synth_result(g[g.iso3 == "GHA"]))),
        ("slope", lambda: cb.slope_rank(make(g), focus="KEN")),
        ("stacked", lambda: cd.stacked_contributions(make(contrib_frame()))),
        ("kde", lambda: cb.kde_plot(make(density_canonical()), focus="KEN")),
        ("scatter", lambda: cb.scatter_vs_income(make(fit_chart_frame()), "KEN")),
        ("scatter_pair", lambda: cb.scatter_vs_income((make(g), synth_result(g)), "KEN", period="2005")),
    ]


@pytest.mark.parametrize("name", [n for n, _ in _builder_calls(lambda f: f)])
def test_every_builder_refuses_a_blocked_result(name):
    call = dict(_builder_calls(lambda f: synth_result(f, [BLOCK])))[name]
    with pytest.raises(render.BlockedResultError, match="ppp_mixing"):
        call()


@pytest.mark.parametrize("name", [n for n, _ in _builder_calls(lambda f: f)])
def test_every_builder_carries_the_source_line_and_warnings_of_a_result(name):
    chart = dict(_builder_calls(lambda f: synth_result(f, [WARN])))[name]()
    lines = render.footer_lines(chart)
    assert lines[-1] == "Source: Synthetic test source (test-1)."
    assert "Note: Synthetic warning." in lines
    assert render.to_spec(chart)["usermeta"]["cgjrkit_provenance"]["caveats"] == [{"code": "syn_warn", "severity": "warn"}]


def test_blocked_band_or_size_result_is_refused_and_band_provenance_joins_the_source_line():
    res = synth_result(growth_frame())
    band = Result(data=growth_frame()[lambda d: d.iso3 == "GHA"].assign(low=1.0, high=2.0)[["period", "low", "high"]],
                  provenance=[Provenance(source="synthetic2", citation="Band source (synthetic)")])
    with pytest.raises(render.BlockedResultError):
        cs.line_vs_peers(res, "KEN", band=Result(band.data, band.provenance, [BLOCK]))
    with pytest.raises(render.BlockedResultError):
        render.chart_from_result(res, "line", focus="KEN", band=Result(band.data, band.provenance, [BLOCK]))
    with pytest.raises(render.BlockedResultError):
        cb.scatter_vs_income(fit_chart_frame(), "KEN", size=Result(growth_frame(), [], [BLOCK]))
    lines = render.footer_lines(render.chart_from_result(res, "line", focus="KEN", band=band))
    assert lines[-1] == "Source: Synthetic test source (test-1); Band source (synthetic)."
    assert render.footer_lines(cs.line_vs_peers(res, "KEN", band=band))[-1] == lines[-1]


# ---------------------------------------------------------------------------
# stacked contributions: totals, method defaults, row filters, tick size (review fixes)
# ---------------------------------------------------------------------------
def test_stacked_auto_total_prefers_gdp_per_capita_over_output_per_worker():
    js = pd.DataFrame({"period": "2010-20", "component": ["gdp_per_capita", "output_per_worker", "employment_rate",
                                                          "working_age_share"], "value": [50.0, 90.0, 55.0, -95.0]})
    meta = render.to_spec(cd.stacked_contributions(js))["usermeta"]
    assert meta["cgjrkit_chart"]["total"] == "gdp_per_capita" and meta["cgjrkit_identity"]["ok"] is True
    pw = pd.DataFrame({"period": "w", "component": ["output_per_worker", "capital_deepening", "tfp"], "value": [3.0, 2.0, 1.0]})
    assert render.to_spec(cd.stacked_contributions(pw))["usermeta"]["cgjrkit_chart"]["total"] == "output_per_worker"


def _jobs_shapley_with_sectors() -> Result:
    from cgjrkit.methods.jobs_shapley import jobs_shapley

    def one(rows):
        return synth_result(make_frame([{"iso3": "AAA", "country": "AAA (synthetic)", "period": p, "indicator": ind,
                                          "value": v} for p, ind, v in rows], source="synthetic", dataset="SYN"))

    def series(ind, a, b):
        return one([("2010", ind, a), ("2020", ind, b)])

    va = one([("2010", "VA_A", 200.0), ("2010", "VA_I", 300.0), ("2020", "VA_A", 500.0), ("2020", "VA_I", 1000.0)])
    emp = one([("2010", "E_A", 150.0), ("2010", "E_I", 100.0), ("2020", "E_A", 175.0), ("2020", "E_I", 200.0)])
    return jobs_shapley(series("GDP", 500.0, 1500.0), series("POP", 1000.0, 2000.0), series("WAP", 500.0, 500.0),
                        series("EMP", 250.0, 375.0), {"2010-20": ("2010", "2020")}, annualize="none",
                        va_by_sector=va, emp_by_sector=emp, va_sectors={"VA_A": "A", "VA_I": "I"},
                        emp_sectors={"E_A": "A", "E_I": "I"})


def test_method_chart_defaults_pick_the_report_chart_of_each_method():
    js = _jobs_shapley_with_sectors()
    assert render.method_chart_defaults(js, "line") == {}
    d = render.method_chart_defaults(js, "stacked")
    assert d == {"total": "gdp_per_capita",
                 "where": {"component": ["gdp_per_capita", "output_per_worker", "employment_rate", "working_age_share"]}}
    meta = render.to_spec(render.chart_from_result(js, "stacked"))["usermeta"]
    assert meta["cgjrkit_chart"]["components"] == ["output_per_worker", "employment_rate", "working_age_share"]
    assert meta["cgjrkit_identity"]["ok"] is True and meta["cgjrkit_provenance"]["defaults"]["total"] == "gdp_per_capita"
    # an explicit row filter replaces the default: the within/between split by sector
    split = render.chart_from_result(js, "stacked", x="sector", total=None,
                                     where={"component": ["output_per_worker.within", "output_per_worker.between"]})
    sm = render.to_spec(split)["usermeta"]
    assert sm["cgjrkit_chart"]["x_order"] == ["A", "I"] and sm["cgjrkit_chart"]["total_mode"] == "none"
    # sector_contributions: one segment per sector, TOTAL as the total
    from cgjrkit.methods.structural_change import sector_contributions

    va = synth_result(make_frame([{"iso3": "AAA", "country": "AAA (synthetic)", "period": str(y), "indicator": ind,
                                   "value": 100.0 * (1.1 if ind == "VA_A" else 1.2) ** (y - 2015)}
                                  for y in range(2015, 2021) for ind in ("VA_A", "VA_I")], source="synthetic"))
    sc = sector_contributions(va, {"2016-20": ("2016", "2020")}, sectors={"VA_A": "A", "VA_I": "I"}, method="exact")
    scm = render.to_spec(render.chart_from_result(sc, "stacked"))["usermeta"]["cgjrkit_chart"]
    assert scm["component"] == "sector" and scm["total"] == "TOTAL" and scm["components"] == ["A", "I", "RESIDUAL"]


def test_filter_rows_validates_and_keeps_provenance():
    res = synth_result(contrib_frame(), [WARN])
    out = render.filter_rows(res, {"component": ["agr", "total_growth"], "period": "2020"})
    assert sorted(out.data["component"]) == ["agr", "total_growth"] and out.provenance == res.provenance
    assert out.caveats == res.caveats
    with pytest.raises(ValueError, match="no column"):
        render.filter_rows(res, {"nope": "x"})
    with pytest.raises(ValueError, match="keeps no rows"):
        render.filter_rows(res, {"component": "nope"})
    with pytest.raises(ValueError, match="non-empty list"):
        render.filter_rows(res, {"component": [{"a": 1}]})
    with pytest.raises(ValueError, match="non-empty list"):
        render.filter_rows(res, {"component": []})


@pytest.mark.parametrize("horizontal,facet", [(False, None), (True, None), (False, "country"), (True, "country")])
def test_total_tick_is_no_wider_than_a_bar(horizontal, facet):
    df = contrib_frame()
    if facet:
        df = pd.concat([df.assign(country="Kenya (synthetic)"), df.assign(country="Ghana (synthetic)")])
    spec = render.to_spec(cd.stacked_contributions(df, total_mark="tick", horizontal=horizontal, facet=facet))
    inner = spec.get("spec", spec)
    (tick,) = [u for u in units(spec) if mark_type(u) == "tick"]
    n = 3
    extent = inner["height"] if horizontal else inner["width"]
    step = extent / (n - 0.35 + 2 * 0.2)  # band scale with paddingInner 0.35, paddingOuter 0.2
    assert tick["mark"]["size"] == pytest.approx(step * 0.65, abs=0.1)
    assert tick["mark"]["size"] <= extent / n


# ---------------------------------------------------------------------------
# scatter: countries left out of the fit (review fix)
# ---------------------------------------------------------------------------
def test_scatter_draws_an_excluded_peer_or_focus_hollow_in_its_colour():
    cf = fit_chart_frame()
    out = (cf["role"] == "country") & cf["iso3"].isin(["GHA", "KEN"])
    cf.loc[out, "in_fit"] = False
    cf.loc[out, "exclusion_reason"] = "excluded"
    chart = cb.scatter_vs_income(cf, "KEN", peers=["GHA", "AAA"])
    spec = render.to_spec(chart)
    d = dataset(spec).query("role == 'country'").set_index("iso3")
    assert d.loc["GHA", "kind"] == "peer" and bool(d.loc["GHA", "hollow"]) is True
    assert d.loc["KEN", "kind"] == "focus" and bool(d.loc["KEN", "hollow"]) is True
    assert bool(d.loc["AAA", "hollow"]) is False
    hollow = [u for u in marks(spec, "point") if u["mark"].get("filled") is False]
    filters = {u["transform"][0]["filter"] for u in hollow}
    assert {"datum.kind === 'peer' && datum.hollow", "datum.kind === 'focus' && datum.hollow"} <= filters
    focus_ring = [u for u in hollow if "focus" in u["transform"][0]["filter"]][0]
    assert focus_ring["mark"]["color"] == style.FOCUS
    assert "Note: Hollow markers are left out of the fit." in render.footer_lines(chart)
    assert render.render(chart, "svg").startswith("<svg")


# ---------------------------------------------------------------------------
# save_chart: no silent overwrite when asked
# ---------------------------------------------------------------------------
def test_save_chart_overwrite_false_refuses_existing_files_and_writes_nothing(tmp_path):
    chart = cs.bar_vs_peers(growth_frame(), "KEN", period="2005")
    assert render.chart_paths(tmp_path / "fig", ("vl.json", "svg")) == {
        "vl.json": tmp_path / "fig.vl.json", "svg": tmp_path / "fig.svg", "csv": tmp_path / "fig.csv"}
    (tmp_path / "fig.csv").write_text("synthetic user file\n")
    with pytest.raises(FileExistsError, match="fig.csv"):
        render.save_chart(chart, tmp_path / "fig", formats=("vl.json", "svg"), overwrite=False)
    assert (tmp_path / "fig.csv").read_text() == "synthetic user file\n" and not (tmp_path / "fig.vl.json").exists()
    paths = render.save_chart(chart, tmp_path / "new", formats=("vl.json",), overwrite=False)
    assert set(paths) == {"vl.json", "csv"} and all(p.is_file() for p in paths.values())
    render.save_chart(chart, tmp_path / "fig", formats=("vl.json",))  # default: overwrite (Python API)
    assert (tmp_path / "fig.csv").read_text().startswith("layer,")
