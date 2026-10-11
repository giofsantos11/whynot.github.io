"""Provenance and result containers.

Every number the toolkit produces must be traceable to a source, a data
vintage, the request parameters and the code that ran.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import pandas as pd

from cgjrkit import __version__
from cgjrkit.core.caveats import Caveat, worst


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def hash_frame(df: pd.DataFrame) -> str:
    """Stable content hash of a frame (order-independent on rows)."""
    if df.empty:
        return hash_bytes(b"")
    cols = sorted(df.columns)
    canon = df[cols].astype(str).sort_values(cols).to_csv(index=False).encode()
    return hash_bytes(canon)


@dataclass
class Provenance:
    """Where a piece of data or a derived result came from."""

    source: str                      # provider key, e.g. "worldbank"
    dataset: str = ""                # e.g. "WDI"
    endpoint: str = ""               # URL or file name actually used
    params: dict = field(default_factory=dict)
    retrieved_at: str = ""           # UTC timestamp of retrieval
    vintage: str = ""                # provider vintage/release if known, e.g. "WEO 2026-04", "PWT 10.01"
    payload_sha256: str = ""         # hash of the raw payload stored in the cache
    license: str = ""                # e.g. "CC BY 4.0"
    citation: str = ""               # suggested source line for charts
    tool: str = ""                   # toolkit function that produced it
    tool_version: str = __version__
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Result:
    """Data plus everything needed to trust it."""

    data: pd.DataFrame
    provenance: list[Provenance] = field(default_factory=list)
    caveats: list[Caveat] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def data_sha256(self) -> str:
        return hash_frame(self.data)

    @property
    def blocked(self) -> bool:
        return worst(self.caveats) == "block"

    def add_caveat(self, caveat: Caveat) -> None:
        if caveat not in self.caveats:
            self.caveats.append(caveat)

    def merged_with(self, other: "Result", data: pd.DataFrame | None = None) -> "Result":
        """Combine provenance and caveats of two results around new data."""
        out = Result(
            data=data if data is not None else pd.concat([self.data, other.data], ignore_index=True),
            provenance=self.provenance + [p for p in other.provenance if p not in self.provenance],
            caveats=list(self.caveats),
            meta={**self.meta, **other.meta},
        )
        for c in other.caveats:
            out.add_caveat(c)
        return out

    def source_line(self) -> str:
        """A chart source line built from provenance citations."""
        cites = []
        for p in self.provenance:
            cite = p.citation or p.dataset or p.source
            if p.vintage:
                cite = f"{cite} ({p.vintage})"
            if cite not in cites:
                cites.append(cite)
        return "Source: " + "; ".join(cites) + "." if cites else ""

    def summary(self, max_rows: int = 20) -> dict:
        """JSON-safe summary for MCP tool responses (never the full microdata)."""
        return {
            "rows": int(len(self.data)),
            "columns": list(self.data.columns),
            "head": json.loads(self.data.head(max_rows).to_json(orient="records")),
            "data_sha256": self.data_sha256,
            "provenance": [p.to_dict() for p in self.provenance],
            "caveats": [c.to_dict() for c in self.caveats],
            "meta": self.meta,
        }
