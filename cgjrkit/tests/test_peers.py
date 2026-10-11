"""Unit tests for cgjrkit.methods.peers (synthetic values, hand-computed statistics)."""

from __future__ import annotations

import pandas as pd
import pytest

from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import COLUMNS, validate_frame
from cgjrkit.methods.harmonize import PPPMixError
from cgjrkit.methods.peers import (
    PeerSet,
    income_group,
    load_peersets,
    members_by_year,
    normalize_classification,
    peer_aggregate,
    save_peersets,
)


def frame(rows, **defaults) -> pd.DataFrame:
    base = {"source": "synthetic", "dataset": "TEST", "indicator": "X", "indicator_label": "Synthetic X",
            "country": "", "unit": "% of GDP", "obs_status": ""}
    base.update(defaults)
    return pd.DataFrame([dict(base, iso3=r[0], period=r[1], value=r[2], **({"obs_status": r[3]} if len(r) > 3 else {}))
                         for r in rows])


def stat_values(res: Result, code: str, period: str) -> dict[str, float]:
    df = res.data[(res.data["group"] == code) & (res.data["period"] == period)]
    return dict(zip(df["stat"], df["value"]))


FROZEN = PeerSet(name="Structural peers", code="STRUCT", members=["AAA", "BBB", "CCC", "DDD"],
                 criteria="Synthetic test set", approver="Test approver", frozen_at="2026-03-01")


# ---------------------------------------------------------------------------
# PeerSet
# ---------------------------------------------------------------------------
def test_peerset_validation_and_defaults():
    ps = PeerSet(name="Aspirational peers", members=["vnm", " mys "])
    assert ps.members == ["VNM", "MYS"]
    assert ps.code == "ASPIRATIONAL_PEERS"
    assert not ps.is_frozen
    with pytest.raises(ValueError):
        PeerSet(name="x", members=["VN"])
    with pytest.raises(ValueError):
        PeerSet(name="x", members=["VNM", "VNM"])
    with pytest.raises(ValueError):
        PeerSet(name="", members=["VNM"])
    with pytest.raises(ValueError):
        PeerSet(name="x", members=["VNM"], frozen_at="01/03/2026")
    with pytest.raises(TypeError):
        PeerSet(name="x", members="VNM")


def test_peerset_freeze_returns_copy():
    ps = PeerSet(name="Peers", members=["GHA"])
    frozen = ps.freeze("Task team leader", "2026-05-01")
    assert frozen.is_frozen and frozen.frozen_at == "2026-05-01" and frozen.approver == "Task team leader"
    assert not ps.is_frozen
    with pytest.raises(ValueError):
        ps.freeze("", "2026-05-01")


def test_peerset_yaml_roundtrip_single(tmp_path):
    path = FROZEN.save(tmp_path / "structural.yaml")
    text = path.read_text()
    assert "frozen_at: '2026-03-01'" in text  # quoted, so YAML keeps it a string
    loaded = PeerSet.load(path)
    assert loaded == FROZEN


def test_peersets_multi_file_and_unquoted_date(tmp_path):
    alt = PeerSet(name="Alternative peers", code="ALT", members=["EEE"], notes="Robustness check")
    path = save_peersets([FROZEN, alt], tmp_path / "KEN-2026.yaml", report="KEN-2026")
    sets = load_peersets(path)
    assert list(sets) == ["STRUCT", "ALT"]
    assert sets["ALT"].notes == "Robustness check"
    assert PeerSet.load(path, name="Alternative peers") == alt
    with pytest.raises(ValueError):
        PeerSet.load(path)  # two sets, no name
    text = "name: Peers\nmembers: [GHA, TZA]\nfrozen_at: 2026-03-01\napprover: TTL\n"
    ps = load_peersets(text)["PEERS"]
    assert ps.frozen_at == "2026-03-01"
    with pytest.raises(ValueError):
        load_peersets("name: Peers\nmembers: [GHA]\ncolour: red\n")


# ---------------------------------------------------------------------------
# peer_aggregate with a fixed peer set
# ---------------------------------------------------------------------------
def test_peer_aggregate_statistics_and_counts():
    rows = [("AAA", "2020", 1.0), ("BBB", "2020", 2.0), ("CCC", "2020", 3.0), ("DDD", "2020", 4.0),
            ("AAA", "2021", 10.0), ("BBB", "2021", 20.0), ("CCC", "2021", float("nan")),
            ("KEN", "2020", 99.0)]  # not a member
    src = Result(data=frame(rows), provenance=[Provenance(source="synthetic", dataset="TEST")])
    res = peer_aggregate(src, FROZEN)
    # 2020: values 1..4 -> mean 2.5, median 2.5, p25 1.75, p75 3.25 (linear interpolation)
    assert stat_values(res, "STRUCT", "2020") == pytest.approx({"mean": 2.5, "median": 2.5, "p25": 1.75, "p75": 3.25})
    # 2021: CCC is missing -> n = 2 < min_n = 3, still computed with a caveat
    assert stat_values(res, "STRUCT", "2021") == pytest.approx({"mean": 15.0, "median": 15.0, "p25": 12.5, "p75": 17.5})
    n = dict(zip(res.data["period"], res.data["n"]))
    assert n == {"2020": 4, "2021": 2}
    assert set(res.data["iso3"]) == {"STRUCT:mean", "STRUCT:median", "STRUCT:p25", "STRUCT:p75"}
    assert res.data.loc[res.data["iso3"] == "STRUCT:median", "country"].iloc[0] == "Structural peers (median)"
    small = next(c for c in res.caveats if c.code == "peer_small_n")
    assert small.evidence["aggregates"] == [{"group": "STRUCT", "key": "X/2021", "n": 2}]
    assert "peer_set_not_frozen" not in {c.code for c in res.caveats}
    validate_frame(res.data)
    assert list(res.data.columns[: len(COLUMNS)]) == COLUMNS
    # provenance and meta
    assert res.provenance[0].source == "synthetic"
    assert res.provenance[-1].tool == "peers.peer_aggregate"
    assert res.provenance[-1].params["peerset"]["members"] == ["AAA", "BBB", "CCC", "DDD"]
    assert "STRUCT:median" in res.meta["aggregates"]
    assert res.meta["peer_sets"]["STRUCT"]["frozen_at"] == "2026-03-01"


def test_peer_aggregate_drop_small_n_and_other_stats():
    rows = [("AAA", "2020", 1.0), ("BBB", "2020", 2.0), ("CCC", "2020", 6.0), ("AAA", "2021", 5.0)]
    res = peer_aggregate(frame(rows), FROZEN, stats=("min", "max", "sd", "count"), drop_below_min_n=True)
    assert set(res.data["period"]) == {"2020"}
    # sd(1, 2, 6) with ddof=1: mean 3, squared deviations 4+1+9=14, 14/2=7
    assert stat_values(res, "STRUCT", "2020") == pytest.approx({"min": 1.0, "max": 6.0, "sd": 7 ** 0.5, "count": 3.0})


def test_peer_aggregate_unfrozen_and_missing_members():
    ps = PeerSet(name="Draft", members=["AAA", "BBB", "ZZZ"])
    res = peer_aggregate(frame([("AAA", "2020", 1.0), ("BBB", "2020", 3.0)]), ps, min_n=2)
    codes = {c.code for c in res.caveats}
    assert {"peer_set_not_frozen", "peer_members_missing"} <= codes
    assert "peer_small_n" not in codes
    missing = next(c for c in res.caveats if c.code == "peer_members_missing")
    assert missing.evidence["members"] == ["ZZZ"]


def test_peer_aggregate_forecast_status_propagates():
    rows = [("AAA", "2025", 1.0), ("BBB", "2025", 2.0, "forecast"), ("CCC", "2025", 3.0)]
    res = peer_aggregate(frame(rows), FROZEN)
    assert set(res.data["obs_status"]) == {"forecast"}


def test_peer_aggregate_refuses_mixed_ppp():
    df = pd.concat([frame([("AAA", "2020", 1.0)], unit="PPP 2017"), frame([("BBB", "2020", 2.0)], unit="PPP 2021")])
    with pytest.raises(PPPMixError):
        peer_aggregate(df, FROZEN)
    res = peer_aggregate(df, FROZEN, on_ppp_mix="block", min_n=1)
    assert res.blocked


def test_peer_aggregate_per_indicator():
    df = pd.concat([frame([("AAA", "2020", 1.0), ("BBB", "2020", 3.0)], indicator="X"),
                    frame([("AAA", "2020", 10.0), ("BBB", "2020", 30.0)], indicator="Y")])
    res = peer_aggregate(df, FROZEN, stats=("mean",), min_n=2)
    assert dict(zip(res.data["indicator"], res.data["value"])) == pytest.approx({"X": 2.0, "Y": 20.0})


# ---------------------------------------------------------------------------
# income-group membership by year
# ---------------------------------------------------------------------------
CLASSIFICATION = pd.DataFrame({
    "iso3": ["AAA", "BBB", "CCC", "DDD"] * 2,
    "year": [2010] * 4 + [2011] * 4,
    "income_group": ["L", "L", "LM", "LM", "L", "LM", "LM", "LM"],
})


def test_normalize_classification_long_and_wide():
    long = normalize_classification(CLASSIFICATION)
    assert list(long.columns) == ["iso3", "year", "group"]
    wide = pd.DataFrame({"iso3": ["AAA", "BBB"], "2010": ["L", ".."], "2011": ["LM", "L"]})
    norm = normalize_classification(wide)
    assert norm.to_dict("records") == [
        {"iso3": "AAA", "year": 2010, "group": "L"},
        {"iso3": "AAA", "year": 2011, "group": "LM"},
        {"iso3": "BBB", "year": 2011, "group": "L"},
    ]
    assert income_group(wide, "aaa", 2011) == "LM"
    assert income_group(wide, "BBB", 2010) is None
    assert members_by_year(CLASSIFICATION, "LM") == {2010: ["CCC", "DDD"], 2011: ["BBB", "CCC", "DDD"]}
    with pytest.raises(ValueError):
        normalize_classification(pd.DataFrame({"iso3": ["AAA", "AAA"], "year": [2010, 2010], "income_group": ["L", "LM"]}))


def test_peer_aggregate_by_income_group_and_year():
    rows = [("AAA", "2010", 1.0), ("BBB", "2010", 3.0), ("CCC", "2010", 5.0), ("DDD", "2010", 7.0),
            ("AAA", "2011", 2.0), ("BBB", "2011", 4.0), ("CCC", "2011", 6.0), ("DDD", "2011", 8.0),
            ("AAA", "2012", 9.0)]
    res = peer_aggregate(frame(rows), classification=CLASSIFICATION, stats=("mean",), min_n=2)
    # 2010: L = {AAA, BBB} -> 2; LM = {CCC, DDD} -> 6. 2011: L = {AAA} -> 2 (n=1); LM = {BBB, CCC, DDD} -> 6
    assert stat_values(res, "IG_L", "2010") == pytest.approx({"mean": 2.0})
    assert stat_values(res, "IG_LM", "2010") == pytest.approx({"mean": 6.0})
    assert stat_values(res, "IG_L", "2011") == pytest.approx({"mean": 2.0})
    assert stat_values(res, "IG_LM", "2011") == pytest.approx({"mean": 6.0})
    n = {(g, p): k for g, p, k in zip(res.data["group"], res.data["period"], res.data["n"])}
    assert n[("IG_L", "2011")] == 1 and n[("IG_LM", "2011")] == 3
    codes = {c.code for c in res.caveats}
    assert "peer_small_n" in codes
    assert "income_class_missing_years" in codes  # 2012 not classified
    assert "2012" not in set(res.data["period"])


def test_peer_aggregate_peerset_within_income_group():
    ps = PeerSet(name="Peers", code="P", members=["AAA", "BBB", "CCC"], frozen_at="2026-01-01", approver="TTL")
    rows = [("AAA", "2011", 2.0), ("BBB", "2011", 4.0), ("CCC", "2011", 6.0), ("DDD", "2011", 8.0)]
    res = peer_aggregate(frame(rows), ps, classification=CLASSIFICATION, groups=["LM"], stats=("mean",), min_n=1)
    # LM members in 2011 that are also peers: BBB, CCC -> mean 5 (DDD excluded)
    assert stat_values(res, "P_LM", "2011") == pytest.approx({"mean": 5.0})
    assert set(res.data["group"]) == {"P_LM"}


def test_peer_aggregate_argument_errors():
    with pytest.raises(ValueError):
        peer_aggregate(frame([("AAA", "2020", 1.0)]))
    with pytest.raises(ValueError):
        peer_aggregate(frame([("AAA", "2020", 1.0)]), FROZEN, stats=("mode",))
    with pytest.raises(ValueError):
        peer_aggregate(frame([("AAA", "2020", 1.0)]), FROZEN, by=("iso3",))
