"""Unit tests for cgjrkit.registries (hosts, licences, countries)."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path

import pytest

import cgjrkit.connectors as connectors_pkg
from cgjrkit import registries as reg
from cgjrkit.connectors.base import Connector


def _connector_classes() -> tuple[list[type[Connector]], dict[str, str]]:
    """Every Connector subclass in cgjrkit.connectors, plus modules that failed to import."""
    classes, failed = [], {}
    for info in pkgutil.iter_modules(connectors_pkg.__path__):
        if info.name == "base":
            continue
        name = f"cgjrkit.connectors.{info.name}"
        try:
            module = importlib.import_module(name)
        except Exception as exc:  # another module's import problem; reported, not hidden
            failed[name] = repr(exc)
            continue
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, Connector) and obj is not Connector and obj.__module__ == name:
                classes.append(obj)
    return classes, failed


# ---------------------------------------------------------------------------
# hosts
# ---------------------------------------------------------------------------
def test_hosts_schema():
    hosts = reg.load_hosts()
    assert len(hosts) >= 20
    for host, entry in hosts.items():
        assert host == host.lower() and "/" not in host, host
        for key in ("connectors", "purpose", "status", "dev_network", "auth", "rate_limit", "phase", "evidence"):
            assert key in entry, (host, key)
        assert entry["status"] in reg.HOST_STATUS, host
        assert entry["dev_network"] in {"blocked", "untested"}, host
        assert entry["phase"] in {"mvp", "next", "later", "excluded"}, host
        assert entry["rate_limit"]["basis"] in {"published", "client_practice", "conservative_default"}, host
        assert isinstance(entry["evidence"], list) and entry["evidence"], host
        if entry["phase"] == "excluded":
            assert entry["connectors"] == [] and entry["rate_limit"]["requests"] == 0
        else:
            assert entry["rate_limit"]["requests"] > 0 and entry["rate_limit"]["per_seconds"] > 0, host


def test_every_connector_domain_is_declared():
    classes, failed = _connector_classes()
    assert classes, f"no connectors imported (failures: {failed})"
    domains = {d for cls in classes for d in cls.domains}
    assert reg.undeclared_hosts(domains) == [], "add these hosts to registries/hosts.yaml"
    # and hosts.yaml names only connector modules that exist
    modules = {info.name for info in pkgutil.iter_modules(connectors_pkg.__path__)}
    named = {c for entry in reg.load_hosts().values() for c in entry["connectors"]}
    assert named <= modules, named - modules


def test_every_connector_source_has_a_licence():
    classes, _ = _connector_classes()
    sources = {cls.source for cls in classes if cls.source}
    missing = sorted(s for s in sources if s not in reg.load_licences())
    assert missing == [], "add these sources to registries/licences.yaml"


def test_host_lookup_rate_limits_and_allowlist():
    assert reg.host_info("https://api.worldbank.org/v2/country?format=json")["connectors"] == ["worldbank", "pip"]
    with pytest.raises(KeyError):
        reg.host_info("evil.example.com")
    oecd = reg.rate_limit("sdmx.oecd.org")
    assert (oecd.requests, oecd.per_seconds, oecd.basis) == (20, 3600.0, "published")
    assert oecd.min_interval == pytest.approx(180.0)
    assert reg.rate_limit("atlas.hks.harvard.edu").min_interval == pytest.approx(0.5)
    default = reg.rate_limit("unknown.example.org")
    assert (default.requests, default.per_seconds, default.basis) == (30, 60.0, "conservative_default")
    assert reg.rate_limit("api.stlouisfed.org").min_interval == float("inf")

    full = reg.allowlist()
    assert "api.worldbank.org" in full and "www.knbs.or.ke" in full
    assert "api.stlouisfed.org" not in full and "api.semanticscholar.org" not in full
    assert "api.stlouisfed.org" in reg.allowlist(include_excluded=True)
    used = reg.allowlist(include_planned=False)
    assert "comtradeapi.un.org" not in used and "api.imf.org" in used
    assert full == sorted(full)
    assert reg.undeclared_hosts(["https://api.worldbank.org/v2/x", "EVIL.example.com:443"]) == ["evil.example.com"]


def test_registry_loaders_return_copies():
    reg.load_hosts()["api.worldbank.org"]["purpose"] = "mutated"
    assert reg.load_hosts()["api.worldbank.org"]["purpose"] != "mutated"


# ---------------------------------------------------------------------------
# licences
# ---------------------------------------------------------------------------
def test_licences_schema():
    lic = reg.load_licences()
    for source, entry in lic.items():
        for key in ("name", "licence", "licence_status", "commercial_use", "commercial_use_status",
                    "third_party_flags", "caveat", "citation", "notes", "evidence"):
            assert key in entry, (source, key)
        assert entry["licence_status"] in reg.STATUS_LETTERS, source
        assert entry["commercial_use_status"] in reg.STATUS_LETTERS, source
        assert entry["commercial_use"] in reg.COMMERCIAL_USE, source
        assert entry["caveat"] in reg.CAVEAT_LEVELS, source
        assert isinstance(entry["third_party_flags"], bool), source


def test_licence_values_follow_the_plan():
    lic = reg.load_licences()
    assert (lic["imf"]["commercial_use"], lic["imf"]["commercial_use_status"], lic["imf"]["caveat"]) == ("unknown", "U", "warn")
    assert lic["worldbank"]["licence"].startswith("CC BY 4.0") and lic["worldbank"]["commercial_use_status"] == "I"
    assert lic["worldbank"]["third_party_flags"] is True
    assert (lic["pwt"]["commercial_use_status"], lic["pwt"]["caveat"]) == ("V", "none")
    assert (lic["fred"]["commercial_use"], lic["fred"]["caveat"]) == ("no", "block")
    assert lic["comtrade"]["commercial_use"] == "restricted"
    assert lic["baci"]["licence"] == "Etalab Open Licence 2.0"
    assert lic["wpp"]["licence"].startswith("CC BY 3.0 IGO")


def test_licence_caveat():
    imf = reg.licence_caveat("imf")
    assert imf is not None and imf.code == "licence_imf" and imf.severity == "warn"
    assert imf.evidence["commercial_use_status"] == "U"
    assert reg.licence_caveat("pwt") is None
    assert reg.licence_caveat("fred").severity == "block"
    unknown = reg.licence_caveat("not_a_source")
    assert unknown.severity == "warn"
    with pytest.raises(KeyError):
        reg.licence_for("not_a_source")


# ---------------------------------------------------------------------------
# countries
# ---------------------------------------------------------------------------
def test_country_files_present_and_schema():
    assert reg.list_countries() == ["IDN", "KEN", "MAR", "MKD", "PHL"]
    hosts = reg.load_hosts()
    for iso3 in reg.list_countries():
        entry = reg.load_country(iso3)
        assert entry["iso3"] == iso3
        for key in ("name", "currency", "fiscal_year", "statistics_office", "national_accounts", "ppp"):
            assert key in entry, (iso3, key)
        fy = entry["fiscal_year"]
        assert 1 <= fy["start_month"] <= 12 and fy["status"] in reg.HOST_STATUS
        assert fy["end_month"] == (fy["start_month"] - 2) % 12 + 1
        assert entry["statistics_office"]["status"] in reg.HOST_STATUS
        assert entry["statistics_office"]["host"] in hosts, iso3
        na = entry["national_accounts"]
        assert isinstance(na["base_year"], int) or na["base_year"] == "unknown", iso3
        assert na["status"] in reg.HOST_STATUS
        assert entry["ppp"]["notes"], iso3


def test_country_lookups():
    assert reg.fiscal_year_start_month("KEN") == 7
    assert [reg.fiscal_year_start_month(c) for c in ("MKD", "IDN", "PHL", "MAR")] == [1, 1, 1, 1]
    assert reg.fiscal_year_start_month("ken") == 7
    assert {c: reg.national_accounts_base_year(c) for c in reg.list_countries()} == {
        "IDN": 2010, "KEN": 2016, "MAR": 2014, "MKD": "unknown", "PHL": 2018}
    assert reg.statistics_office_host("KEN") == "www.knbs.or.ke"
    assert "2017" in reg.load_country("KEN")["ppp"]["report_ppp_bases"]
    with pytest.raises(KeyError):
        reg.load_country("XXX")


def test_registry_files_ship_inside_the_package():
    root = Path(reg.__file__).parent
    assert (root / "hosts.yaml").is_file() and (root / "licences.yaml").is_file()
    assert sorted(p.name for p in (root / "countries").glob("*.yaml")) == [
        "IDN.yaml", "KEN.yaml", "MAR.yaml", "MKD.yaml", "PHL.yaml"]
