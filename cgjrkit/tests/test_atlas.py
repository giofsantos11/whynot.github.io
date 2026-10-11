"""Unit tests for the Atlas of Economic Complexity connector (synthetic fixtures, no network)."""

import json

import pandas as pd
import pytest

from cgjrkit.connectors import atlas
from cgjrkit.connectors.atlas import Atlas, AtlasError, MinuteLimiter
from cgjrkit.core import http
from cgjrkit.core.cache import Cache
from cgjrkit.core.schema import validate_frame

ECI = "atlas_eci_synthetic.json"
ERR = "atlas_error_synthetic.json"


def _load(fixtures_dir, name) -> bytes:
    return (fixtures_dir / name).read_bytes()


def _seed(payload, start=2097, end=2098, product_class="HS92", trade_values=False):
    url, body = atlas.build_request(start, end, product_class, trade_values)
    Cache().put(Atlas.source, url, {"__body__": body}, payload)


class NoCallLimiter(MinuteLimiter):
    def acquire(self):  # cache hits must not consume the rate limit
        raise AssertionError("limiter called for a cached request")


def test_build_query_and_request():
    q = atlas.build_query(2013, 2023)
    assert "locationCountry { countryId iso3Code nameShortEn formerCountry }" in q
    assert "countryYear(productClass: HS92, yearMin: 2013, yearMax: 2023) { countryId year eci exportValue importValue }" in q
    assert "exportValue" not in atlas.build_query(2013, 2023, "sitc", trade_values=False)
    url, body = atlas.build_request(2013, 2023, "HS12")
    assert url == "https://atlas.hks.harvard.edu/api/graphql" and "productClass: HS12" in body["query"]
    with pytest.raises(ValueError, match="product_class"):
        atlas.build_query(2013, 2023, "HS2017")
    with pytest.raises(ValueError, match="after"):
        atlas.build_query(2023, 2013)


def test_parse_ranks(fixtures_dir):
    p = atlas.parse(_load(fixtures_dir, ECI))
    df = p.frame
    validate_frame(df)
    rank = df[df["indicator"] == "eci_rank_hs92"].set_index(["iso3", "period"])["value"]
    # 2097: JPN 2.0, VNM 0.5, KEN -0.5, TZA -0.5 (tie), UGA -1.0; SUN (former) and NRU (null) excluded
    assert rank[("JPN", "2097")] == 1 and rank[("VNM", "2097")] == 2
    assert rank[("KEN", "2097")] == 3 and rank[("TZA", "2097")] == 3 and rank[("UGA", "2097")] == 5
    assert ("SUN", "2097") not in rank.index and ("NRU", "2097") not in rank.index
    assert rank[("TZA", "2098")] == 4 and rank[("UGA", "2098")] == 5
    eci = df[df["indicator"] == "eci_hs92"].set_index(["iso3", "period"])["value"]
    assert eci[("KEN", "2098")] == -0.4 and eci[("SUN", "2097")] == 3.0  # former country keeps its ECI
    assert p.meta["rank_universe"] == {"2097": 5, "2098": 5}
    assert p.meta["former_countries"] == ["SUN"]
    assert df.loc[df["iso3"] == "KEN", "country"].iloc[0] == "Kenya"
    assert {"atlas_cross_check_only", "atlas_rank_universe", "atlas_unversioned"} <= {c.code for c in p.caveats}


def test_parse_country_filter_keeps_world_ranks(fixtures_dir):
    p = atlas.parse(_load(fixtures_dir, ECI), countries="KEN,VNM", start=2098, end=2098)
    df = p.frame
    assert set(df["iso3"]) == {"KEN", "VNM"} and set(df["period"]) == {"2098"}
    rank = df[df["indicator"] == "eci_rank_hs92"].set_index("iso3")["value"]
    assert rank["VNM"] == 2 and rank["KEN"] == 3


def test_parse_trade_values_and_errors(fixtures_dir):
    p = atlas.parse(_load(fixtures_dir, ECI), countries=["KEN"], indicators=["export_value", "import_value"])
    assert set(p.frame["indicator"]) == {"export_value", "import_value"}
    assert (p.frame["unit"] == "current US$").all()
    with pytest.raises(ValueError, match="unknown Atlas indicator"):
        atlas.parse(_load(fixtures_dir, ECI), indicators=["pci"])
    with pytest.raises(AtlasError, match="GraphQL error"):
        atlas.parse(_load(fixtures_dir, ERR))
    with pytest.raises(AtlasError, match="not JSON"):
        atlas.read_payload(b"<html>bad gateway</html>")
    with pytest.raises(AtlasError, match="no 'data'"):
        atlas.read_payload(b'{"data": null}')
    empty = atlas.parse(json.dumps({"data": {"locationCountry": [], "countryYear": []}}))
    assert empty.frame.empty and "atlas_empty" in {c.code for c in empty.caveats}


def test_unmapped_country_ids():
    payload = json.dumps({"data": {"locationCountry": [], "countryYear": [
        {"countryId": 1, "year": 2097, "eci": 1.0}, {"countryId": 2, "year": 2097, "eci": 0.0}]}})
    p = atlas.parse(payload)
    assert set(p.frame["iso3"]) == {"ATLAS1", "ATLAS2"} and p.meta["unmapped_country_ids"] == [1, 2]


def test_rank_eci_direct():
    df = pd.DataFrame({"year": [1, 1, 1, 2], "eci": [0.1, 0.3, None, 5.0], "former": [False, False, False, True]})
    out = atlas.rank_eci(df)
    assert list(out["eci_rank"].iloc[:2]) == [2, 1] and pd.isna(out["eci_rank"].iloc[2]) and pd.isna(out["eci_rank"].iloc[3])
    assert list(out["rank_universe"]) == [2, 2, 0, 0]


def test_minute_limiter():
    t = {"now": 0.0}
    slept = []

    def sleep(s):
        slept.append(s)
        t["now"] += s

    lim = MinuteLimiter(limit=2, window_s=60, clock=lambda: t["now"], sleep=sleep)
    assert lim.acquire() == 0 and lim.acquire() == 0
    t["now"] = 10.0
    assert lim.acquire() == pytest.approx(50.0) and slept == [pytest.approx(50.0)]
    assert atlas.REQUESTS_PER_MINUTE == 120


def test_fetch_from_cache(fixtures_dir):
    _seed(_load(fixtures_dir, ECI))
    res = Atlas(limiter=NoCallLimiter()).fetch(["KEN", "VNM"], 2097, 2098)
    validate_frame(res.data)
    assert len(res.data) == 8
    prov = res.provenance[0]
    assert prov.source == "atlas" and prov.dataset == "Atlas HS92" and prov.endpoint == atlas.API_URL
    assert prov.params["__body__"]["query"].startswith("{ locationCountry")
    assert "research and educational" in prov.license
    assert {"atlas_licence", "atlas_unversioned"} <= {c.code for c in res.caveats}
    assert res.meta["rank_universe"]["2097"] == 5


def test_cached_error_is_refetched(fixtures_dir, monkeypatch):
    _seed(_load(fixtures_dir, ERR))
    good = _load(fixtures_dir, ECI)
    calls = []

    def fake_fetch(source, url, params=None, *, cache=None, refresh=False, **kw):
        calls.append(refresh)
        hit = None if refresh else cache.latest(source, url, {"__body__": kw["json_body"]})
        if hit:
            return cache.get_object(hit.sha256), hit
        return good, cache.put(source, url, {"__body__": kw["json_body"]}, good)

    monkeypatch.setattr(atlas.http, "fetch", fake_fetch)
    res = Atlas(limiter=MinuteLimiter(clock=lambda: 0.0, sleep=lambda s: None)).fetch("KEN", 2097, 2098)
    assert calls == [False, True] and len(res.data) == 4


def test_offline_without_cache_raises():
    with pytest.raises(http.NetworkBlocked):
        Atlas(limiter=MinuteLimiter(clock=lambda: 0.0, sleep=lambda s: None)).fetch("KEN", 2013, 2023)
