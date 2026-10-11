"""Content-addressed cache with vintage pinning.

Raw API payloads are stored once under ``objects/<sha256>``. An index maps a
request key (provider + URL + params) to every retrieval of that request, so a
replication can be re-run against the exact payload it used (a *snapshot*)
even after the provider revises its data.

Layout under the cache root (``$CGJRKIT_CACHE`` or ``~/.cache/cgjrkit``)::

    objects/<sha256>              raw payload bytes
    index/<request_key>.json      list of retrievals, newest last
    snapshots/<name>.json         {request_key: sha256} pins for a replication
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path

from cgjrkit.core.provenance import hash_bytes, utc_now


def default_root() -> Path:
    return Path(os.environ.get("CGJRKIT_CACHE", Path.home() / ".cache" / "cgjrkit"))


def request_key(source: str, url: str, params: dict | None = None) -> str:
    """Stable key for a request, independent of parameter order."""
    canon = json.dumps({"source": source, "url": url, "params": params or {}}, sort_keys=True, default=str)
    return hashlib.sha256(canon.encode()).hexdigest()[:32]


@dataclass
class Retrieval:
    key: str
    sha256: str
    retrieved_at: str
    url: str
    params: dict
    source: str


class Cache:
    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root else default_root()
        for sub in ("objects", "index", "snapshots"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    # -- payloads -----------------------------------------------------------
    def put(self, source: str, url: str, params: dict | None, payload: bytes) -> Retrieval:
        sha = hash_bytes(payload)
        obj = self.root / "objects" / sha
        if not obj.exists():
            obj.write_bytes(payload)
        key = request_key(source, url, params)
        rec = Retrieval(key=key, sha256=sha, retrieved_at=utc_now(), url=url, params=params or {}, source=source)
        history = self.history(key)
        history.append(rec.__dict__)
        (self.root / "index" / f"{key}.json").write_text(json.dumps(history, indent=1))
        return rec

    def get_object(self, sha256: str) -> bytes:
        return (self.root / "objects" / sha256).read_bytes()

    def history(self, key: str) -> list[dict]:
        path = self.root / "index" / f"{key}.json"
        return json.loads(path.read_text()) if path.exists() else []

    def latest(self, source: str, url: str, params: dict | None = None) -> Retrieval | None:
        history = self.history(request_key(source, url, params))
        return Retrieval(**history[-1]) if history else None

    # -- snapshots (vintage pinning) ------------------------------------------
    def pin(self, snapshot: str, retrieval: Retrieval) -> None:
        path = self.root / "snapshots" / f"{snapshot}.json"
        pins = json.loads(path.read_text()) if path.exists() else {}
        pins[retrieval.key] = retrieval.sha256
        path.write_text(json.dumps(pins, indent=1, sort_keys=True))

    def pinned(self, snapshot: str, source: str, url: str, params: dict | None = None) -> Retrieval | None:
        path = self.root / "snapshots" / f"{snapshot}.json"
        if not path.exists():
            return None
        key = request_key(source, url, params)
        sha = json.loads(path.read_text()).get(key)
        if not sha:
            return None
        for rec in reversed(self.history(key)):
            if rec["sha256"] == sha:
                return Retrieval(**rec)
        return Retrieval(key=key, sha256=sha, retrieved_at="", url=url, params=params or {}, source=source)
