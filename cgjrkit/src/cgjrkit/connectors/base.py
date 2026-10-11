"""Base class and conventions for data connectors.

Conventions every connector follows:

1. ``build_request(...) -> (url, params)`` is pure.
2. ``parse(payload: bytes, ...) -> pd.DataFrame`` is pure and returns the
   canonical frame (:mod:`cgjrkit.core.schema`). It is unit-tested with
   fixtures in the provider's documented response format.
3. ``fetch(...) -> Result`` wires the two together through
   :func:`cgjrkit.core.http.fetch` (cache, snapshots, retries) and attaches
   :class:`~cgjrkit.core.provenance.Provenance` (endpoint, params, payload
   hash, retrieval time, license, citation, vintage when known).
4. Provider flags (estimates, forecasts, ILO modelled values, breaks) are
   kept in ``obs_status`` so caveat checks can see them.
5. No API keys in code: read them from environment variables named
   ``CGJRKIT_<PROVIDER>_KEY`` and fail with a clear message if missing.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from cgjrkit.core.cache import Cache, Retrieval
from cgjrkit.core.provenance import Provenance, Result


class Connector(ABC):
    #: provider key used in the canonical frame's ``source`` column
    source: str = ""
    #: license of the data, e.g. "CC BY 4.0" (verify against provider terms)
    license: str = ""
    #: default citation for chart source lines
    citation: str = ""
    #: domains this connector needs (for docs/ALLOWLIST.md)
    domains: tuple[str, ...] = ()

    def __init__(self, cache: Cache | None = None):
        self.cache = cache or Cache()

    def provenance(self, retrieval: Retrieval, *, dataset: str = "", vintage: str = "", tool: str = "", notes: list[str] | None = None) -> Provenance:
        return Provenance(
            source=self.source,
            dataset=dataset,
            endpoint=retrieval.url,
            params=retrieval.params,
            retrieved_at=retrieval.retrieved_at,
            vintage=vintage,
            payload_sha256=retrieval.sha256,
            license=self.license,
            citation=self.citation,
            tool=tool or f"{self.__class__.__name__}.fetch",
            notes=notes or [],
        )

    @abstractmethod
    def fetch(self, *args, **kwargs) -> Result:  # pragma: no cover - interface
        ...

    @staticmethod
    def finalize(df: pd.DataFrame) -> pd.DataFrame:
        """Sort a canonical frame deterministically."""
        return df.sort_values(["indicator", "iso3", "period"]).reset_index(drop=True)
