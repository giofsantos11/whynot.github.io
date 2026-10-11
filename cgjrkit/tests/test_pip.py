"""Unit tests for the PIP connector (synthetic fixtures, no network)."""

import json

import pytest

from cgjrkit.connectors import pip as pipmod
from cgjrkit.connectors.pip import PIP, poverty_mix_caveats
from cgjrkit.core.cache import Cache
from cgjrkit.core.schema import ppp_base, validate_frame

LATEST_2021 = {"version": "20990601_2021_01_02_PROD", "release_version": "20990601", "ppp_version": 2021}


def _load(fixtures_dir, name) -> bytes:
    return (fixtures_dir / name).read_bytes()


def _seed(url, params, payload):
    Cache().put(PIP.source, url, params, payload)


def _seed_versions(fixtures_dir):
    url, params = pipmod.build_versions_request()
    _seed(url, params, _load(fixtures_dir, "pip_versions_synthetic.csv"))


def _seed_pip(fixtures_dir, fixture, countries, povline=3.0, ppp=2021, version=LATEST_2021, **kw):
    url, params = pipmod.build_request(countries, kw.pop("years", "all"), povline, ppp, version["release_version"], version["version"] or None, kw.pop("fill_gaps", False), **kw)
    _seed(url, params, _load(fixtures_dir, fixture))


# -- request builders and validation ---------------------------------------------------


def test_build_request_params():
    url, params = pipmod.build_request(["UGA", "KEN"], [2021, 2015], 3.0, 2021, "20250930", "20250930_2021_01_02_PROD")
    assert url == "https://api.worldbank.org/pip/v1/pip"
    assert params == {
        "country": "KEN,UGA",
        "year": "2015,2021",
        "povline": 3.0,
        "fill_gaps": "false",
        "welfare_type": "all",
        "reporting_level": "national",
        "ppp_version": 2021,
        "release_version": "20250930",
        "version": "20250930_2021_01_02_PROD",
        "format": "csv",
    }
    _, p = pipmod.build_request("all", "MRV", 2.15, 2017, fill_gaps=True, nowcast=True)
    assert p["country"] == "all" and p["year"] == "MRV" and p["fill_gaps"] == "true" and p["nowcast"] == "true"
    assert "release_version" not in p and "version" not in p
    assert pipmod.build_request("ken,uga")[1]["country"] == "KEN,UGA"  # normalised, as the API expects uppercase
    gurl, gp = pipmod.build_group_request("WLD", 2019, 3.0, 2021)
    assert gurl.endswith("/pip-grp") and gp["group_by"] == "wb" and gp["country"] == "WLD"


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"countries": "KENYA"}, "uppercase ISO3"),
        ({"release_version": "2024-06-27"}, "YYYYMMDD"),
        ({"release_version": "20241345"}, "not a valid date"),
        ({"ppp_version": "2017a"}, "4-digit PPP year"),
        ({"povline": 2.1555}, "3 decimals"),
        ({"povline": 3000.0}, "between 0 and 2700"),
        ({"years": "latest"}, "years must be"),
        ({"nowcast": True}, "requires fill_gaps"),
        ({"welfare_type": "expenditure"}, "welfare_type"),
        ({"version": "20250930"}, "YYYYMMDD_PPP"),
    ],
)
def test_build_request_validation(kwargs, match):
    args = {"countries": "KEN", **kwargs}
    with pytest.raises(ValueError, match=match):
        pipmod.build_request(**args)


def test_versions_parse_and_resolve(fixtures_dir):
    versions = pipmod.parse_versions(_load(fixtures_dir, "pip_versions_synthetic.csv"))
    assert versions[0]["release_version"] == "20990601"
    assert pipmod.resolve_version(versions, 2021) == LATEST_2021
    assert pipmod.resolve_version(versions, 2017, "20990101")["version"] == "20990101_2017_01_02_PROD"
    with pytest.raises(ValueError, match="not available"):
        pipmod.resolve_version(versions, 2021, "20990301")
    with pytest.raises(ValueError, match="no data version"):
        pipmod.resolve_version(versions, 2011)
    with pytest.raises(ValueError, match="unexpected PIP versions table"):
        pipmod.parse_versions(b"a,b\n1,2\n")


# -- parsing ------------------------------------------------------------------------------


def test_parse_country_table_units_and_codes(fixtures_dir):
    parsed = pipmod.parse_rows(pipmod._read_table(_load(fixtures_dir, "pip_ken_uga_synthetic.csv")), ppp_version=2021, povline=3.0)
    df = parsed.frame
    validate_frame(df)
    assert (df["dataset"] == "PIP_PPP2021").all() and (df["source"] == "pip").all()
    val = df.set_index(["indicator", "iso3", "period"])["value"]
    assert val[("headcount@3.00", "KEN", "2005")] == 50.0  # fraction 0.5 -> percent
    assert val[("gini", "KEN", "2021")] == 40.0  # 0.4 -> 0-100 index
    assert val[("mean", "UGA", "2019")] == 4.0  # $/day, not rescaled
    assert val[("decile10", "KEN", "2021")] == 30.0
    # KEN 2015 has income and consumption surveys: kept as two labelled series
    assert val[("headcount@3.00|consumption", "KEN", "2015")] == 40.0
    assert val[("headcount@3.00|income", "KEN", "2015")] == 45.0
    assert parsed.welfare_duplicates == [{"iso3": "KEN", "period": "2015", "reporting_level": "national"}]
    unit = df.set_index("indicator")["unit"]
    assert unit["headcount@3.00"].iloc[0] == "% of population below $3.00/day (PPP 2021)"
    assert ppp_base(unit["headcount@3.00"].iloc[0]) == "2021"
    assert ppp_base(unit["mean"].iloc[0]) == "2021"
    assert df.loc[df["indicator"] == "headcount@3.00", "country"].iloc[0] in ("Kenya", "Uganda")
    info = {(r["iso3"], r["period"], r["series_suffix"]): r for r in parsed.survey_info}
    assert info[("KEN", "2005", "")]["survey_year"] == 2005.5
    assert info[("KEN", "2015", "|income")]["welfare_type"] == "income"
    assert parsed.aggregates == []


def test_parse_fill_gaps_flags_and_nowcast(fixtures_dir):
    table = pipmod._read_table(_load(fixtures_dir, "pip_ken_fillgaps_synthetic.csv"))
    dropped = pipmod.parse_rows(table, ppp_version=2021, povline=3.0, drop_nowcast=True, measures=["headcount"])
    assert dropped.dropped_nowcast_rows == 1
    status = dropped.frame.set_index("period")["obs_status"].to_dict()
    assert status == {"2015": "", "2016": "imputed", "2017": "imputed"}
    kept = pipmod.parse_rows(table, ppp_version=2021, povline=3.0, measures=["headcount"])
    assert kept.frame.set_index("period").loc["2023", "obs_status"] == "modelled"


def test_parse_group_table(fixtures_dir):
    parsed = pipmod.parse_rows(pipmod._read_table(_load(fixtures_dir, "pip_grp_synthetic.csv")), ppp_version=2021, povline=3.0, aggregate=True)
    df = parsed.frame
    validate_frame(df)
    assert parsed.aggregates == ["SSF", "WLD"]
    assert df.set_index(["indicator", "iso3"])["value"][("headcount@3.00", "WLD")] == 10.0
    assert df.loc[df["iso3"] == "WLD", "country"].iloc[0] == "World"
    assert "gini" not in set(df["indicator"])  # measures absent from the payload are skipped


def test_parse_scale_guard_and_errors():
    csv = b"country_code,country_name,reporting_year,poverty_line,headcount\nKEN,Kenya,2015,3,45.0\n"
    parsed = pipmod.parse_rows(pipmod._read_table(csv), ppp_version=2021, povline=3.0)
    assert not parsed.scaled_x100
    assert parsed.frame["value"].tolist() == [45.0]
    with pytest.raises(ValueError, match="PIP API error"):
        pipmod._read_table(b'{"error": ["Invalid query arguments"], "details": {}}')
    with pytest.raises(ValueError, match="unknown PIP measures"):
        pipmod.parse_rows(pipmod._read_table(csv), ppp_version=2021, measures=["bogus"])
    assert pipmod.parse(b"", ppp_version=2021).empty
    # JSON array of records is accepted too
    js = json.dumps([{"country_code": "KEN", "country_name": "Kenya", "reporting_year": 2015, "poverty_line": 3, "headcount": 0.4}]).encode()
    assert pipmod.parse(js, ppp_version=2021, measures=["headcount"])["value"].tolist() == [40.0]


# -- fetch (offline, cache seeded with fixtures) ------------------------------------------------


def test_fetch_poverty_end_to_end(fixtures_dir):
    _seed_versions(fixtures_dir)
    _seed_pip(fixtures_dir, "pip_ken_uga_synthetic.csv", ["KEN", "UGA"])
    res = PIP().fetch_poverty(["KEN", "UGA"])
    validate_frame(res.data)
    assert res.meta["release_version"] == "20990601" and res.meta["version"] == LATEST_2021["version"]
    assert res.meta["poverty_lines"] == ["3.00"] and res.meta["ppp_version"] == 2021
    tools = [p.tool for p in res.provenance]
    assert tools == ["PIP.versions", "PIP.fetch_poverty"]
    data_prov = res.provenance[1]
    assert data_prov.vintage == "20990601"
    assert data_prov.params["release_version"] == "20990601" and data_prov.params["version"] == LATEST_2021["version"]
    assert data_prov.payload_sha256 and "version 20990601_2021_01_02_PROD" in data_prov.citation
    assert "(20990601)" in res.source_line()
    codes = {c.code: c for c in res.caveats}
    assert {"pip_ppp_base", "pip_welfare_duplicates", "pip_comparability_break", "pip_distribution_type", "pip_welfare_mix"} <= set(codes)
    assert "pip_line_ppp_mismatch" not in codes and "pip_mixed_ppp" not in codes
    assert codes["pip_comparability_break"].evidence["countries"] == ["KEN"]
    assert not res.blocked
    json.dumps(res.summary())


def test_fetch_poverty_line_mismatch_and_multiple_lines(fixtures_dir):
    _seed_versions(fixtures_dir)
    _seed_pip(fixtures_dir, "pip_ken_2017ppp_synthetic.csv", "KEN", povline=2.15)
    res = PIP().fetch_poverty("KEN", povline=2.15, ppp_version=2021)
    mismatch = next(c for c in res.caveats if c.code == "pip_line_ppp_mismatch")
    assert mismatch.severity == "warn" and mismatch.evidence["standard_for_other_ppp"] == {"2.15": [2017]}

    _seed_pip(fixtures_dir, "pip_ken_uga_synthetic.csv", "KEN", povline=3.0)
    _seed_pip(fixtures_dir, "pip_ken_420_synthetic.csv", "KEN", povline=4.2)
    multi = PIP().fetch_poverty("KEN", povline=[4.2, 3.0])
    assert {"headcount@3.00", "headcount@4.20"} <= set(multi.data["indicator"])
    mixed = next(c for c in multi.caveats if c.code == "pip_mixed_lines")
    assert mixed.severity == "warn" and mixed.evidence["poverty_lines"] == ["3.00", "4.20"]
    assert len([p for p in multi.provenance if p.tool == "PIP.fetch_poverty"]) == 2


def test_multiple_lines_do_not_duplicate_line_independent_measures(fixtures_dir):
    import io

    import pandas as pd

    _seed_versions(fixtures_dir)
    base = _load(fixtures_dir, "pip_ken_uga_synthetic.csv")
    t = pd.read_csv(io.BytesIO(base))
    t["poverty_line"] = 4.2
    t["headcount"] = t["headcount"] + 0.1  # still synthetic, derived from the synthetic fixture
    _seed_pip(fixtures_dir, "pip_ken_uga_synthetic.csv", ["KEN", "UGA"], povline=3.0)
    url, params = pipmod.build_request(["KEN", "UGA"], "all", 4.2, 2021, LATEST_2021["release_version"], LATEST_2021["version"])
    _seed(url, params, t.to_csv(index=False).encode())
    res = PIP().fetch_poverty(["KEN", "UGA"], povline=[3.0, 4.2])
    validate_frame(res.data)
    gini = res.data[res.data["indicator"].str.startswith("gini")]
    assert len(gini) == 5  # one per country-year-series, not one per line
    hc = res.data.set_index(["indicator", "iso3", "period"])["value"]
    assert hc[("headcount@4.20", "KEN", "2005")] == pytest.approx(60.0)
    assert hc[("headcount@3.00", "KEN", "2005")] == 50.0
    assert len(res.meta["welfare_duplicates"]) == 1
    assert "pip_inconsistent_repeats" not in {c.code for c in res.caveats}


def test_mixing_ppp_rounds_blocks(fixtures_dir):
    _seed_versions(fixtures_dir)
    v2017 = {"version": "20990601_2017_01_02_PROD", "release_version": "20990601", "ppp_version": 2017}
    _seed_pip(fixtures_dir, "pip_ken_2017ppp_synthetic.csv", "KEN", povline=2.15, ppp=2017, version=v2017)
    _seed_pip(fixtures_dir, "pip_ken_uga_synthetic.csv", "KEN", povline=3.0)
    r2017 = PIP().fetch_poverty("KEN", povline=None, ppp_version=2017, measures=["headcount", "gini"])
    r2021 = PIP().fetch_poverty("KEN", povline=None, ppp_version=2021, measures=["headcount", "gini"])
    assert r2017.meta["poverty_lines"] == ["2.15"] and r2021.meta["poverty_lines"] == ["3.00"]
    assert set(r2017.data["dataset"]) == {"PIP_PPP2017"}
    cavs = poverty_mix_caveats([r2017, r2021])
    assert {c.code: c.severity for c in cavs} == {"pip_mixed_ppp": "block", "pip_mixed_lines": "warn"}
    merged = r2017.merged_with(r2021)
    validate_frame(merged.data)  # different PPP datasets never collide on the canonical key
    for c in poverty_mix_caveats(merged):
        merged.add_caveat(c)
    assert merged.blocked


def test_fetch_poverty_fill_gaps_drops_nowcast_by_default(fixtures_dir):
    _seed_versions(fixtures_dir)
    _seed_pip(fixtures_dir, "pip_ken_fillgaps_synthetic.csv", "KEN", fill_gaps=True)
    res = PIP().fetch_poverty("KEN", fill_gaps=True, measures=["headcount"])
    assert res.meta["dropped_nowcast_rows"] == 1
    assert next(c for c in res.caveats if c.code == "pip_interpolated").severity == "warn"
    assert "2023" not in set(res.data["period"])


def test_fetch_poverty_release_handling(fixtures_dir):
    # explicit release, versions endpoint unavailable -> proceeds without the version id, with a note
    _seed_pip(fixtures_dir, "pip_ken_uga_synthetic.csv", "KEN", version={"version": "", "release_version": "20990101"})
    res = PIP().fetch_poverty("KEN", release_version="20990101")
    prov = res.provenance[0]
    assert prov.vintage == "20990101" and "version" not in prov.params
    assert any("versions lookup failed" in n for n in prov.notes)
    # no release and no versions table -> clear error
    with pytest.raises(RuntimeError, match="pass release_version"):
        PIP().fetch_poverty("UGA")
    # release not offered for that PPP round
    _seed_versions(fixtures_dir)
    with pytest.raises(ValueError, match="not available"):
        PIP().fetch_poverty("KEN", release_version="20990301")


def test_fetch_aggregates(fixtures_dir):
    _seed_versions(fixtures_dir)
    url, params = pipmod.build_group_request("all", "all", 3.0, 2021, LATEST_2021["release_version"], LATEST_2021["version"])
    _seed(url, params, _load(fixtures_dir, "pip_grp_synthetic.csv"))
    res = PIP().fetch_aggregates()
    validate_frame(res.data)
    assert res.meta["aggregates"] == ["SSF", "WLD"]
    assert res.provenance[-1].tool == "PIP.fetch_aggregates" and res.provenance[-1].vintage == "20990601"


def test_connector_declares_domains():
    assert PIP.domains == ("api.worldbank.org",)
    assert PIP().fetch.__doc__.startswith("Alias")
