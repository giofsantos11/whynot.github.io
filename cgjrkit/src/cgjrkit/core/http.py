"""HTTP access with caching, retries and clear errors.

Connectors never call ``httpx`` directly; they call :func:`fetch`, which
returns the payload bytes plus the cache retrieval record used for
provenance.

Environment variables:

* ``CGJRKIT_OFFLINE=1``: never touch the network; serve from cache or fail.
* ``CGJRKIT_SNAPSHOT=<name>``: serve pinned payloads for that snapshot when
  available, and pin new retrievals into it.
* ``CGJRKIT_CACHE``: cache root (see :mod:`cgjrkit.core.cache`).
"""

from __future__ import annotations

import os
import time

import httpx

from cgjrkit import __version__
from cgjrkit.core.cache import Cache, Retrieval

USER_AGENT = f"cgjrkit/{__version__} (research toolkit; contact: maintainers)"


class NetworkBlocked(RuntimeError):
    """The request could not reach the host (proxy denial, DNS, offline mode)."""


class FetchError(RuntimeError):
    """The host answered with an error status."""


def _offline() -> bool:
    return os.environ.get("CGJRKIT_OFFLINE", "") not in ("", "0", "false")


def fetch(
    source: str,
    url: str,
    params: dict | None = None,
    *,
    headers: dict | None = None,
    cache: Cache | None = None,
    refresh: bool = False,
    timeout: float = 60.0,
    retries: int = 3,
    method: str = "GET",
    json_body: dict | None = None,
) -> tuple[bytes, Retrieval]:
    """Fetch ``url`` (cached). Returns ``(payload, retrieval)``.

    Order of resolution: pinned snapshot -> cache (unless ``refresh``) ->
    network. Network results are stored in the cache and pinned to the
    active snapshot, if any.
    """
    cache = cache or Cache()
    cache_params = dict(params or {})
    if json_body is not None:
        cache_params["__body__"] = json_body
    snapshot = os.environ.get("CGJRKIT_SNAPSHOT", "")

    if snapshot:
        pinned = cache.pinned(snapshot, source, url, cache_params)
        if pinned:
            return cache.get_object(pinned.sha256), pinned
    if not refresh:
        latest = cache.latest(source, url, cache_params)
        if latest:
            return cache.get_object(latest.sha256), latest
    if _offline():
        raise NetworkBlocked(f"offline mode and no cached payload for {source}: {url}")

    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True, headers=hdrs) as client:
                resp = client.request(method, url, params=params, json=json_body)
        except (httpx.ConnectError, httpx.ProxyError, httpx.ConnectTimeout) as exc:
            raise NetworkBlocked(
                f"cannot reach {httpx.URL(url).host} ({exc.__class__.__name__}). "
                "If this runs in a restricted environment, allowlist the domain (see docs/ALLOWLIST.md)."
            ) from exc
        except httpx.TransportError as exc:  # read timeouts, resets
            last_exc = exc
            time.sleep(2**attempt)
            continue
        if resp.status_code in (429, 500, 502, 503, 504):
            last_exc = FetchError(f"{resp.status_code} from {url}")
            wait = float(resp.headers.get("Retry-After", 2**attempt)) if resp.headers.get("Retry-After", "").isdigit() else 2**attempt
            time.sleep(min(wait, 30))
            continue
        if resp.status_code >= 400:
            raise FetchError(f"{resp.status_code} from {url}: {resp.text[:300]}")
        retrieval = cache.put(source, url, cache_params, resp.content)
        if snapshot:
            cache.pin(snapshot, retrieval)
        return resp.content, retrieval
    raise FetchError(f"giving up on {url} after {retries} attempts: {last_exc}")
