import pandas as pd
import pytest

from cgjrkit.core.cache import Cache, request_key
from cgjrkit.core.caveats import Caveat
from cgjrkit.core.http import NetworkBlocked, fetch
from cgjrkit.core.provenance import Provenance, Result, hash_frame
from cgjrkit.core.schema import COLUMNS, make_frame, to_wide, validate_frame, year_of


def test_make_frame_fills_defaults_and_types():
    df = make_frame([{"indicator": "X", "iso3": "KEN", "period": "2020", "value": "1.5"}], source="worldbank", dataset="WDI")
    assert list(df.columns) == COLUMNS
    assert df.loc[0, "source"] == "worldbank"
    assert df.loc[0, "value"] == 1.5
    validate_frame(df)


def test_validate_rejects_duplicates():
    rec = {"indicator": "X", "iso3": "KEN", "period": "2020", "value": 1.0}
    df = make_frame([rec, rec], source="s")
    with pytest.raises(ValueError, match="duplicate"):
        validate_frame(df)


def test_to_wide_and_year_of():
    df = make_frame([{"indicator": "X", "iso3": c, "period": p, "value": v} for c, p, v in [("KEN", "2020", 1), ("UGA", "2020", 2), ("KEN", "2021", 3)]], source="s")
    wide = to_wide(df)
    assert wide.loc["2021", "KEN"] == 3
    assert year_of("2024Q1") == 2024 and year_of("FY2024") is None


def test_cache_snapshot_pinning(tmp_path):
    cache = Cache(tmp_path)
    r1 = cache.put("s", "http://x", {"a": 1}, b"old")
    cache.pin("rep", r1)
    cache.put("s", "http://x", {"a": 1}, b"new")
    assert cache.get_object(cache.latest("s", "http://x", {"a": 1}).sha256) == b"new"
    assert cache.get_object(cache.pinned("rep", "s", "http://x", {"a": 1}).sha256) == b"old"
    assert request_key("s", "u", {"a": 1, "b": 2}) == request_key("s", "u", {"b": 2, "a": 1})


def test_fetch_offline_serves_cache_or_raises(tmp_path):
    cache = Cache(tmp_path)
    with pytest.raises(NetworkBlocked):
        fetch("s", "http://example.invalid/x", {"q": 1}, cache=cache)
    cache.put("s", "http://example.invalid/x", {"q": 1}, b"payload")
    payload, rec = fetch("s", "http://example.invalid/x", {"q": 1}, cache=cache)
    assert payload == b"payload" and rec.sha256


def test_result_source_line_and_merge():
    df = make_frame([{"indicator": "X", "iso3": "KEN", "period": "2020", "value": 1}], source="s")
    a = Result(df, [Provenance("worldbank", "WDI", citation="World Development Indicators", vintage="2026-09")])
    b = Result(df, [Provenance("imf", "WEO", citation="IMF World Economic Outlook")], [Caveat("x", "warn", "m")])
    m = a.merged_with(b)
    assert "World Development Indicators (2026-09)" in m.source_line()
    assert len(m.caveats) == 1 and not m.blocked
    assert hash_frame(df) == hash_frame(df.iloc[::-1])


def test_source_line_skips_empty_cites():
    empty = Result(data=pd.DataFrame(), provenance=[Provenance(source="")])
    assert empty.source_line() == ""
    mixed = Result(data=pd.DataFrame(), provenance=[Provenance(source=""), Provenance(source="wb", citation="WDI")])
    assert mixed.source_line() == "Source: WDI."


def test_ppp_base_reads_both_word_orders():
    from cgjrkit.core.schema import ppp_base

    assert ppp_base("constant 2021 international $ (PPP 2021)") == "2021"
    assert ppp_base("GDP per capita (2017 PPP)") == "2017"
    assert ppp_base("GDP, PPP (constant 2017 international $)") == "2017"
    assert ppp_base("current international $ (PPP)") == "unknown"
    assert ppp_base("constant 2015 US$") is None
