"""Deterministic methods over canonical frames (each returns a :class:`~cgjrkit.core.provenance.Result`).

Modules
-------
* :mod:`~cgjrkit.methods.harmonize`: rebasing, growth rates, period averages,
  CAGR, % of frontier, deflating, ratios, fiscal/calendar conversion, splicing
* :mod:`~cgjrkit.methods.peers`: peer sets and peer-group statistics
* :mod:`~cgjrkit.methods.benchmark`: income-conditional fits, percentile ranks,
  kernel densities, z-scores, distance to frontier
* :mod:`~cgjrkit.methods.growth_accounting`: growth accounting, perpetual
  inventory, an HP-filter potential-output approximation
* :mod:`~cgjrkit.methods.structural_change`: VA per worker, shift-share,
  sector contributions, beta convergence
* :mod:`~cgjrkit.methods.jobs_shapley`: Shapley decomposition of GDP per capita
* :mod:`~cgjrkit.methods.standard_tables`: standardized employment and macro tables

Every public name in those modules' ``__all__`` can be imported from this
package (``from cgjrkit.methods import peer_aggregate``), with two exceptions:
``growth_accounting`` and ``jobs_shapley`` are also the names of their modules.
``cgjrkit.methods.growth_accounting`` stays the *module* (so
``import cgjrkit.methods.growth_accounting as ga`` and monkeypatching keep
working); get the functions from the module or from :data:`METHODS`::

    from cgjrkit.methods import METHODS
    METHODS["growth_accounting"](panel, {"2001-10": ("2000", "2010")})

Names load lazily: importing one method module does not import scipy and
statsmodels for the others.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterator, Mapping
from typing import Any

#: module -> public names re-exported at package level (mirrors each module's __all__)
_MODULE_EXPORTS: dict[str, tuple[str, ...]] = {
    "harmonize": (
        "HarmonizeError", "FrequencyMismatchError", "PPPMixError", "Period", "parse_period",
        "period_frequency", "format_period", "fiscal_label", "combine_status", "cagr_value",
        "check_ppp", "rebase_index", "growth_rate", "period_average", "cagr", "pct_of_frontier",
        "deflate", "ratio", "fiscal_to_calendar", "calendar_to_fiscal", "quarterly_to_fiscal", "splice",
    ),
    "peers": (
        "PeerSet", "load_peersets", "save_peersets", "normalize_classification", "income_group",
        "members_by_year", "peer_aggregate", "STATS",
    ),
    "benchmark": (
        "FIT_KINDS", "PERCENTILE_METHODS", "income_conditional_fit", "percentile_rank",
        "kernel_density", "zscore", "distance_to_frontier", "percentile_of", "aggregate_codes",
    ),
    "growth_accounting": (
        "DEFAULT_ALPHA", "DEFAULT_SENSITIVITY", "COMPONENT_LABELS", "perpetual_inventory",
        "potential_output",
    ),
    "structural_change": (
        "StructuralChangeError", "ShiftShareIdentityError", "SectorMap", "sector_panel",
        "annualize_factor", "va_per_worker", "decompose_productivity", "shift_share",
        "sector_contributions", "beta_convergence", "TOTAL",
    ),
    "jobs_shapley": ("JobsShapleyError", "shapley_multipliers", "shapley_product", "FACTORS"),
    "standard_tables": (
        "MacroRow", "standardized_employment_table", "standardized_macro_table", "to_xlsx", "to_html",
    ),
}

#: method name -> module, for the Result-returning methods (the tool surface)
_METHOD_MODULES: dict[str, str] = {
    **{name: "harmonize" for name in (
        "rebase_index", "growth_rate", "period_average", "cagr", "pct_of_frontier", "deflate",
        "ratio", "fiscal_to_calendar", "calendar_to_fiscal", "quarterly_to_fiscal", "splice",
    )},
    "peer_aggregate": "peers",
    **{name: "benchmark" for name in (
        "income_conditional_fit", "percentile_rank", "kernel_density", "zscore", "distance_to_frontier",
    )},
    **{name: "growth_accounting" for name in ("growth_accounting", "perpetual_inventory", "potential_output")},
    **{name: "structural_change" for name in (
        "va_per_worker", "shift_share", "sector_contributions", "beta_convergence",
    )},
    "jobs_shapley": "jobs_shapley",
    **{name: "standard_tables" for name in ("standardized_employment_table", "standardized_macro_table")},
}

_NAME_TO_MODULE: dict[str, str] = {
    name: module for module, names in _MODULE_EXPORTS.items() for name in names
}


def _load(module: str, name: str) -> Any:
    return getattr(importlib.import_module(f"{__name__}.{module}"), name)


class MethodRegistry(Mapping[str, Callable[..., Any]]):
    """Read-only mapping ``method name -> function`` that imports on first access."""

    def __getitem__(self, name: str) -> Callable[..., Any]:
        if name not in _METHOD_MODULES:
            raise KeyError(f"unknown method {name!r}; available: {', '.join(_METHOD_MODULES)}")
        return _load(_METHOD_MODULES[name], name)

    def __iter__(self) -> Iterator[str]:
        return iter(_METHOD_MODULES)

    def __len__(self) -> int:
        return len(_METHOD_MODULES)

    def __contains__(self, name: object) -> bool:
        return name in _METHOD_MODULES

    def __repr__(self) -> str:
        return f"MethodRegistry({list(_METHOD_MODULES)})"


#: every Result-returning method, by name (includes growth_accounting and jobs_shapley)
METHODS = MethodRegistry()


def __getattr__(name: str) -> Any:
    # Only re-exported names resolve here; submodule names fall through to the
    # import system (AttributeError), so the modules stay importable as modules.
    if name in _NAME_TO_MODULE:
        return _load(_NAME_TO_MODULE[name], name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_NAME_TO_MODULE))


__all__ = ["METHODS", "MethodRegistry", *_NAME_TO_MODULE]
