"""Registries: hosts, licences and country facts as versioned YAML data.

Files in this package:

* ``hosts.yaml``: every host cgjrkit calls or plans to call, with the
  connector(s), purpose, rate-limit default, verification status and phase.
  It is the single source for the network allowlist.
* ``licences.yaml``: licence, commercial-use status (V/I/U, from
  ``inventory/build-plan.md`` section 4), citation and suggested caveat
  severity per source.
* ``countries/<ISO3>.yaml``: fiscal-year start month, statistics office
  host, national-accounts base year (or ``"unknown"``) and PPP notes.

Loaders return plain dicts/dataclasses (deep copies, so callers cannot
mutate the cached registry). Peer sets live with the reports, not here; see
:mod:`cgjrkit.methods.peers`.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import yaml

from cgjrkit.core.caveats import Caveat

__all__ = [
    "REGISTRY_DIR",
    "RateLimit",
    "load_hosts",
    "host_info",
    "rate_limit",
    "allowlist",
    "undeclared_hosts",
    "load_licences",
    "licence_for",
    "licence_caveat",
    "list_countries",
    "load_country",
    "fiscal_year_start_month",
    "national_accounts_base_year",
    "statistics_office_host",
]

REGISTRY_DIR = Path(__file__).parent
COUNTRY_DIR = REGISTRY_DIR / "countries"

HOST_STATUS = {"verified", "unverified"}
STATUS_LETTERS = {"V", "I", "U"}
COMMERCIAL_USE = {"yes", "yes_with_attribution", "restricted", "no", "unknown"}
CAVEAT_LEVELS = {"none", "info", "warn", "block"}


@lru_cache(maxsize=None)
def _read(path: str) -> Any:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _load(name: str) -> Any:
    return copy.deepcopy(_read(str(REGISTRY_DIR / name)))


def _normalize_host(host: str) -> str:
    h = str(host).strip().lower()
    if "://" in h:
        h = urlparse(h).hostname or ""
    return h.split("/")[0].split(":")[0]


# ---------------------------------------------------------------------------
# hosts
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RateLimit:
    """At most ``requests`` per ``per_seconds``; ``basis`` says how sure we are."""

    requests: int
    per_seconds: float
    basis: str
    note: str = ""

    @property
    def min_interval(self) -> float:
        """Seconds to wait between requests to stay inside the limit (inf when excluded)."""
        return float("inf") if self.requests <= 0 else self.per_seconds / self.requests


def load_hosts() -> dict[str, dict]:
    """All host entries keyed by hostname."""
    return _load("hosts.yaml")["hosts"]


def host_info(host: str) -> dict:
    """Registry entry for ``host`` (a hostname or a URL). Raises KeyError if undeclared."""
    h = _normalize_host(host)
    hosts = load_hosts()
    if h not in hosts:
        raise KeyError(f"host {h!r} is not declared in cgjrkit/registries/hosts.yaml")
    return hosts[h]


def rate_limit(host: str) -> RateLimit:
    """Rate-limit default for ``host``; the registry default for undeclared hosts."""
    data = _load("hosts.yaml")
    entry = data["hosts"].get(_normalize_host(host), {})
    rl = entry.get("rate_limit") or data["defaults"]["rate_limit"]
    return RateLimit(int(rl["requests"]), float(rl["per_seconds"]), str(rl.get("basis", "")), str(rl.get("note", "")))


def allowlist(*, include_planned: bool = True, include_excluded: bool = False) -> list[str]:
    """Sorted hostnames to allowlist.

    ``include_planned=False`` keeps only hosts used by an existing connector;
    excluded hosts (FRED, Semantic Scholar) are left out unless asked for.
    """
    out = []
    for host, entry in load_hosts().items():
        if entry.get("phase") == "excluded" and not include_excluded:
            continue
        if not include_planned and not entry.get("connectors"):
            continue
        out.append(host)
    return sorted(out)


def undeclared_hosts(domains: Iterable[str]) -> list[str]:
    """Hosts in ``domains`` (hostnames or URLs) that hosts.yaml does not declare."""
    hosts = load_hosts()
    return sorted({h for d in domains if (h := _normalize_host(d)) and h not in hosts})


# ---------------------------------------------------------------------------
# licences
# ---------------------------------------------------------------------------
def load_licences() -> dict[str, dict]:
    """All licence entries keyed by source (connector ``source`` keys where they exist)."""
    return _load("licences.yaml")["sources"]


def licence_for(source: str) -> dict:
    """Licence entry for ``source``. Raises KeyError if the source is not registered."""
    lic = load_licences()
    if source not in lic:
        raise KeyError(f"source {source!r} is not registered in cgjrkit/registries/licences.yaml")
    return lic[source]


def licence_caveat(source: str) -> Caveat | None:
    """A ``licence_<source>`` caveat at the registry's suggested severity, or None.

    Unregistered sources get a ``warn`` caveat, because their terms are unknown.
    """
    try:
        entry = licence_for(source)
    except KeyError:
        return Caveat(f"licence_{source}", "warn", f"Licence terms for {source!r} are not registered; check before use.",
                      {"source": source})
    level = entry.get("caveat", "none")
    if level == "none":
        return None
    msg = (f"{entry['name']}: {entry['licence']} (licence {entry['licence_status']}); commercial use "
           f"{entry['commercial_use']} ({entry['commercial_use_status']}). {entry.get('notes', '')}").strip()
    return Caveat(f"licence_{source}", level, msg,
                  {"source": source, "licence": entry["licence"], "commercial_use": entry["commercial_use"],
                   "commercial_use_status": entry["commercial_use_status"]})


# ---------------------------------------------------------------------------
# countries
# ---------------------------------------------------------------------------
def list_countries() -> list[str]:
    """ISO3 codes with a country file."""
    return sorted(p.stem for p in COUNTRY_DIR.glob("*.yaml"))


def load_country(iso3: str) -> dict:
    """Country entry for ``iso3``. Raises KeyError if there is no file."""
    code = str(iso3).strip().upper()
    path = COUNTRY_DIR / f"{code}.yaml"
    if not path.exists():
        raise KeyError(f"no country registry file for {code!r} (have: {list_countries()})")
    return copy.deepcopy(_read(str(path)))


def fiscal_year_start_month(iso3: str) -> int:
    """Month (1-12) in which the government fiscal year starts (Kenya: 7)."""
    entry = load_country(iso3)
    month = entry.get("fiscal_year", {}).get("start_month")
    if not isinstance(month, int) or not 1 <= month <= 12:
        raise KeyError(f"fiscal-year start month for {iso3} is unknown in the country registry")
    return month


def national_accounts_base_year(iso3: str) -> int | str:
    """National-accounts base year, or ``"unknown"``."""
    value = load_country(iso3).get("national_accounts", {}).get("base_year", "unknown")
    return value if isinstance(value, int) else "unknown"


def statistics_office_host(iso3: str) -> str:
    """Hostname of the national statistics office."""
    return load_country(iso3)["statistics_office"]["host"]
