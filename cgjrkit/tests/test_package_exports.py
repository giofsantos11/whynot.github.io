"""Package-level registries: cgjrkit.connectors.CONNECTORS, cgjrkit.methods.METHODS, cgjrkit.qa exports."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import subprocess
import sys
import types

import pytest

import cgjrkit.connectors as connectors_pkg
import cgjrkit.methods as methods_pkg
from cgjrkit.connectors import CONNECTORS, connector_info, get_connector
from cgjrkit.connectors.base import Connector
from cgjrkit.methods import METHODS


def _discovered_connectors() -> dict[str, type[Connector]]:
    found = {}
    for info in pkgutil.iter_modules(connectors_pkg.__path__):
        module = importlib.import_module(f"cgjrkit.connectors.{info.name}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if issubclass(obj, Connector) and obj is not Connector and obj.__module__ == module.__name__:
                found[obj.source] = obj
    return found


def test_connector_registry_covers_every_connector_class():
    discovered = _discovered_connectors()
    assert set(CONNECTORS) == set(discovered), "register new connectors in cgjrkit/connectors/__init__.py"
    for key, cls in CONNECTORS.items():
        assert cls is discovered[key] and cls.source == key and cls.domains, key
    assert get_connector("imf").__name__ == "IMF"
    with pytest.raises(KeyError, match="unknown connector"):
        CONNECTORS["nope"]
    info = connector_info("pwt")
    assert info["class"] == "PWT" and info["domains"] == ["dataverse.nl", "www.rug.nl"]


def test_connector_class_names_and_submodules_both_import():
    from cgjrkit.connectors import PIP, WorldBank, pip

    assert isinstance(pip, types.ModuleType) and PIP is pip.PIP
    assert WorldBank is CONNECTORS["worldbank"]
    with pytest.raises(AttributeError):
        connectors_pkg.NotAConnector  # noqa: B018


def test_registries_load_lazily():
    code = (
        "import sys, cgjrkit.connectors.worldbank, cgjrkit.methods.harmonize\n"
        "loaded = sorted(m for m in sys.modules if m.startswith(('cgjrkit.connectors.', 'cgjrkit.methods.')))\n"
        "print(','.join(loaded))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    loaded = set(out.split(","))
    assert "cgjrkit.connectors.baci" not in loaded and "cgjrkit.methods.benchmark" not in loaded, loaded


def test_method_registry_and_package_exports():
    for name in METHODS:
        assert callable(METHODS[name]), name
    assert {"growth_accounting", "jobs_shapley", "peer_aggregate", "shift_share", "income_conditional_fit"} <= set(METHODS)
    # module names stay modules; the functions come from METHODS or the module
    import cgjrkit.methods.growth_accounting as ga

    assert isinstance(ga, types.ModuleType) and METHODS["growth_accounting"] is ga.growth_accounting
    from cgjrkit.methods import jobs_shapley

    assert isinstance(jobs_shapley, types.ModuleType)
    # every public name of every method module is exported, except the two module-named functions
    for info in pkgutil.iter_modules(methods_pkg.__path__):
        module = importlib.import_module(f"cgjrkit.methods.{info.name}")
        for name in getattr(module, "__all__", []):
            if name == info.name:
                continue
            assert getattr(methods_pkg, name) is getattr(module, name), (info.name, name)
    with pytest.raises(KeyError, match="unknown method"):
        METHODS["nope"]


def test_qa_package_exports_the_caveat_engine():
    from cgjrkit.qa import CaveatReport, list_rules, run_caveat_check
    from cgjrkit.qa import caveat_check

    assert run_caveat_check is caveat_check.run_caveat_check and CaveatReport is caveat_check.CaveatReport
    assert len(list_rules()) >= 10
