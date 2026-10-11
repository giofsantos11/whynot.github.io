"""Data connectors, one module per public provider.

``CONNECTORS`` maps each connector's ``source`` key to its class::

    from cgjrkit.connectors import CONNECTORS
    wb = CONNECTORS["worldbank"]()
    result = wb.fetch("NY.GDP.MKTP.KD.ZG", ["KEN", "UGA"], start=2010, end=2024)

The registry imports a connector module only when that connector is first
looked up. Importing ``cgjrkit.connectors.worldbank`` therefore does not pull in
every other provider (BACI needs duckdb, UNCTAD's bulk route needs py7zr), and
an import problem in one connector cannot break the others.

Class names (``WorldBank``, ``IMF``, ...) can also be imported from this
package; they load lazily in the same way. Modules that are not connectors:
``base`` (the abstract :class:`~cgjrkit.connectors.base.Connector`) and
``handles`` (DOI/handle helpers shared by the literature connectors).
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from cgjrkit.connectors.base import Connector

#: source key -> (module under cgjrkit.connectors, class name)
_REGISTRY: dict[str, tuple[str, str]] = {
    # World Bank
    "worldbank": ("worldbank", "WorldBank"),
    "data360": ("data360", "Data360"),
    "pip": ("pip", "PIP"),
    # IMF, ILO, UN
    "imf": ("imf", "IMF"),
    "ilostat": ("ilostat", "ILOSTAT"),
    "wpp": ("wpp", "WPP"),
    # files: Penn World Table, Bruegel REER
    "pwt": ("pwt", "PWT"),
    "bruegel": ("bruegel", "BruegelREER"),
    # trade and policy
    "unctad": ("unctad", "UNCTAD"),
    "baci": ("baci", "BACI"),
    "atlas": ("atlas", "Atlas"),
    "oecd": ("oecd", "OECD"),
    # literature (non-canonical "works" tables)
    "openalex": ("openalex", "OpenAlex"),
    "crossref": ("crossref", "Crossref"),
}

#: class name -> source key, for lazy ``from cgjrkit.connectors import WorldBank``
_CLASS_NAMES: dict[str, str] = {cls: key for key, (_, cls) in _REGISTRY.items()}

#: connectors whose results are not canonical frames (literature metadata tables)
NON_CANONICAL: frozenset[str] = frozenset({"openalex", "crossref"})


def _load(key: str) -> type[Connector]:
    module, cls = _REGISTRY[key]
    return getattr(importlib.import_module(f"{__name__}.{module}"), cls)


class ConnectorRegistry(Mapping[str, "type[Connector]"]):
    """Read-only mapping ``source key -> Connector class`` that imports on first access."""

    def __getitem__(self, key: str) -> type[Connector]:
        if key not in _REGISTRY:
            raise KeyError(f"unknown connector {key!r}; available: {', '.join(_REGISTRY)}")
        return _load(key)

    def __iter__(self) -> Iterator[str]:
        return iter(_REGISTRY)

    def __len__(self) -> int:
        return len(_REGISTRY)

    def __contains__(self, key: object) -> bool:
        return key in _REGISTRY

    def __repr__(self) -> str:
        return f"ConnectorRegistry({list(_REGISTRY)})"


CONNECTORS = ConnectorRegistry()


def get_connector(name: str) -> type[Connector]:
    """Return the connector class registered under ``name`` (its ``source`` key)."""
    return CONNECTORS[name]


def connector_names() -> list[str]:
    """Registered connector keys, in registry order."""
    return list(_REGISTRY)


def connector_info(name: str) -> dict[str, Any]:
    """Static description of a connector: class, module, licence, citation, hosts."""
    cls = CONNECTORS[name]
    return {
        "name": name,
        "class": cls.__name__,
        "module": cls.__module__,
        "source": cls.source,
        "license": cls.license,
        "citation": cls.citation,
        "domains": list(cls.domains),
        "canonical_frame": name not in NON_CANONICAL,
    }


def __getattr__(name: str) -> Any:
    # Only connector class names resolve here. Anything else must raise
    # AttributeError so ``from cgjrkit.connectors import pip`` still imports the
    # submodule.
    if name in _CLASS_NAMES:
        return _load(_CLASS_NAMES[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_CLASS_NAMES))


__all__ = [
    "CONNECTORS",
    "NON_CANONICAL",
    "ConnectorRegistry",
    "connector_info",
    "connector_names",
    "get_connector",
    *_CLASS_NAMES,
]
