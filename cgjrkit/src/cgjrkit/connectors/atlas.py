"""Harvard Growth Lab Atlas of Economic Complexity (GraphQL API): ECI and ECI rank,
used as a cross-check of ECI computed from BACI.

Endpoint (``POST``, JSON body ``{"query": ...}``, keyless)::

    https://atlas.hks.harvard.edu/api/graphql

Query used (fields verified in the Growth Lab API docs and in third-party code
that ran against the live API; see ``docs/apis/atlas.md``)::

    {
      locationCountry { countryId iso3Code nameShortEn formerCountry }
      countryYear(productClass: HS92, yearMin: 2013, yearMax: 2023) {
        countryId year eci exportValue importValue
      }
    }

``countryId`` follows UN M49 where possible; ISO3 codes come from
``locationCountry``. The API has no ECI-rank field that we could verify, so the
rank is computed here: countries with a non-null ECI in a year (former
countries excluded) are ranked by ECI descending (1 = most complex, ties share
the lower rank). Whether this universe equals the Atlas rankings page is
UNVERIFIED; the live test compares Kenya with the Kenya CGJR (rank 87 in 2013
and 2023).

Terms: the API is "provided as a courtesy", rate-limited to **120 requests per
minute**, and meant for stand-alone analysis; the Atlas states research and
educational use, commercial use is unconfirmed. Results carry a licence caveat;
the toolkit computes ECI itself and uses the Atlas only to cross-check.

Design: :func:`build_query`, :func:`build_request`, :func:`parse` and
:func:`rank_eci` are pure; :class:`Atlas` wires them through
:func:`cgjrkit.core.http.fetch` with a sliding-window rate limiter.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

import pandas as pd

from cgjrkit.connectors.base import Connector
from cgjrkit.core import http
from cgjrkit.core.cache import Cache, Retrieval
from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Result
from cgjrkit.core.schema import empty_frame, make_frame, validate_frame

API_URL = "https://atlas.hks.harvard.edu/api/graphql"
REQUESTS_PER_MINUTE = 120
#: classifications accepted by countryYear(productClass: ...). HS92, HS12 and SITC
#: are in the API docs; HS22 is reported by a third party (UNVERIFIED).
PRODUCT_CLASSES = ("HS92", "HS12", "HS22", "SITC")
DEFAULT_PRODUCT_CLASS = "HS92"

LICENSE = ("Atlas of Economic Complexity API: public courtesy access for research and educational use; "
           "commercial use unconfirmed (ask growthlabtools@hks.harvard.edu)")
CITATION = "Growth Lab at Harvard University, The Atlas of Economic Complexity (atlas.hks.harvard.edu)"

INDICATORS: dict[str, tuple[str, str]] = {
    "eci": ("Economic Complexity Index", "index (standardized, mean 0)"),
    "eci_rank": ("Economic Complexity Index rank", "rank (1 = most complex)"),
    "export_value": ("Exports of goods and services", "current US$"),
    "import_value": ("Imports of goods and services", "current US$"),
}
_FIELD_OF = {"eci": "eci", "export_value": "exportValue", "import_value": "importValue"}
_ISO3_RE = re.compile(r"^[A-Z]{3}$")


class AtlasError(RuntimeError):
    """The Atlas API answered with GraphQL errors or an unreadable payload."""


# ---------------------------------------------------------------------------
# pure request builders
# ---------------------------------------------------------------------------
def _check_class(product_class: str) -> str:
    pc = str(product_class).upper()
    if pc not in PRODUCT_CLASSES:
        raise ValueError(f"product_class must be one of {PRODUCT_CLASSES}, got {product_class!r}")
    return pc


def build_query(year_min: int, year_max: int, product_class: str = DEFAULT_PRODUCT_CLASS,
                trade_values: bool = True) -> str:
    """GraphQL query for ECI of every country, ``year_min``..``year_max``."""
    y0, y1 = int(year_min), int(year_max)
    if y0 > y1:
        raise ValueError(f"year_min {y0} is after year_max {y1}")
    pc = _check_class(product_class)
    fields = "countryId year eci" + (" exportValue importValue" if trade_values else "")
    return ("{ locationCountry { countryId iso3Code nameShortEn formerCountry } "
            f"countryYear(productClass: {pc}, yearMin: {y0}, yearMax: {y1}) {{ {fields} }} }}")


def build_request(year_min: int, year_max: int, product_class: str = DEFAULT_PRODUCT_CLASS,
                  trade_values: bool = True) -> tuple[str, dict]:
    """``(url, json_body)`` for :func:`cgjrkit.core.http.fetch` (``method="POST"``)."""
    return API_URL, {"query": build_query(year_min, year_max, product_class, trade_values)}


# ---------------------------------------------------------------------------
# pure parsing
# ---------------------------------------------------------------------------
@dataclass
class ParsedAtlas:
    frame: pd.DataFrame
    meta: dict = field(default_factory=dict)
    caveats: list[Caveat] = field(default_factory=list)


def read_payload(payload: bytes | str) -> dict:
    """Decode a GraphQL response; raise :class:`AtlasError` on ``errors`` or a missing ``data``."""
    try:
        doc = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise AtlasError(f"Atlas payload is not JSON: {str(payload)[:120]!r}") from exc
    if not isinstance(doc, dict):
        raise AtlasError("Atlas payload is not a JSON object")
    if doc.get("errors"):
        msgs = "; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in doc["errors"])
        raise AtlasError(f"Atlas GraphQL error: {msgs[:500]}")
    data = doc.get("data")
    if not isinstance(data, dict):
        raise AtlasError("Atlas payload has no 'data' object")
    return data


def _iso_codes(countries: Sequence[str] | str | None) -> set[str]:
    if countries is None:
        return set()
    items = [countries] if isinstance(countries, str) else list(countries)
    return {p.strip().upper() for item in items for p in str(item).split(",") if p.strip()}


def rank_eci(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``eci_rank`` and ``rank_universe`` per year to a table with ``year, eci, former``.

    Rows with a null ECI or ``former`` True get no rank. Ties share the lower rank
    (``method="min"``), ranks are 1 = highest ECI.
    """
    out = df.copy()
    eligible = out["eci"].notna() & ~out["former"].fillna(False).astype(bool)
    out["eci_rank"] = pd.NA
    out["rank_universe"] = 0
    for _year, idx in out[eligible].groupby("year").groups.items():
        ranks = out.loc[idx, "eci"].rank(ascending=False, method="min")
        out.loc[idx, "eci_rank"] = ranks.astype(int)
        out.loc[idx, "rank_universe"] = len(idx)
    return out


def parse(
    payload: bytes | str,
    countries: Sequence[str] | str | None = None,
    start: int | None = None,
    end: int | None = None,
    indicators: Sequence[str] = ("eci", "eci_rank"),
    product_class: str = DEFAULT_PRODUCT_CLASS,
) -> ParsedAtlas:
    """Canonical frame of ECI (and computed ranks) from a GraphQL response.

    Ranks are computed over all countries in the payload before ``countries``
    filtering, so a peer subset keeps world ranks.
    """
    wanted = [i.lower() for i in indicators]
    bad = [i for i in wanted if i not in INDICATORS]
    if bad:
        raise ValueError(f"unknown Atlas indicator(s) {bad}; known: {sorted(INDICATORS)}")
    data = read_payload(payload)
    locs = pd.DataFrame(data.get("locationCountry") or [])
    years = pd.DataFrame(data.get("countryYear") or [])
    if years.empty:
        return ParsedAtlas(empty_frame(), {"countries": 0}, [Caveat("atlas_empty", "warn", "The Atlas returned no "
                                                                    "countryYear rows.", {})])
    for col in ("countryId", "year"):
        if col not in years.columns:
            raise AtlasError(f"countryYear rows lack {col!r}; columns: {list(years.columns)}")
    if not locs.empty:
        locs = locs.rename(columns={"iso3Code": "iso3", "nameShortEn": "country", "formerCountry": "former"})
        keep = [c for c in ("countryId", "iso3", "country", "former") if c in locs.columns]
        years = years.merge(locs[keep].drop_duplicates("countryId"), on="countryId", how="left")
    for col, default in (("iso3", None), ("country", ""), ("former", False)):
        if col not in years.columns:
            years[col] = default
    years["iso3"] = years["iso3"].where(years["iso3"].notna() & (years["iso3"] != ""),
                                        "ATLAS" + years["countryId"].astype(str))
    years["country"] = years["country"].fillna("")
    years["former"] = years["former"].fillna(False).astype(bool)
    years["year"] = pd.to_numeric(years["year"], errors="coerce").astype("Int64")
    years["eci"] = pd.to_numeric(years["eci"], errors="coerce") if "eci" in years.columns else float("nan")
    ranked = rank_eci(years)

    mask = pd.Series(True, index=ranked.index)
    codes = _iso_codes(countries)
    if codes:
        mask &= ranked["iso3"].str.upper().isin(codes)
    if start is not None:
        mask &= ranked["year"] >= int(start)
    if end is not None:
        mask &= ranked["year"] <= int(end)
    sel = ranked[mask]

    records: list[dict] = []
    for ind in wanted:
        label, unit = INDICATORS[ind]
        if ind == "eci_rank":
            vals = sel["eci_rank"]
            unit = f"{unit}, among countries with an ECI that year"
        else:
            col = _FIELD_OF[ind]
            vals = pd.to_numeric(sel[col], errors="coerce") if col in sel.columns else pd.Series(pd.NA, index=sel.index)
        for (_, row), val in zip(sel.iterrows(), vals):
            if pd.isna(val):
                continue
            records.append({"indicator": f"{ind}_{product_class.lower()}" if ind.startswith("eci") else ind,
                            "indicator_label": f"{label} ({product_class})" if ind.startswith("eci") else label,
                            "iso3": row["iso3"], "country": row["country"], "period": str(int(row["year"])),
                            "value": float(val), "unit": unit})
    frame = make_frame(records, source="atlas", dataset=f"Atlas {product_class}") if records else empty_frame()
    frame = Connector.finalize(frame)
    universe = ranked[ranked["rank_universe"] > 0].groupby("year")["rank_universe"].first()
    meta = {
        "product_class": product_class,
        "countries_in_payload": int(ranked["countryId"].nunique()),
        "rank_universe": {str(int(y)): int(n) for y, n in universe.items()},
        "former_countries": sorted(ranked.loc[ranked["former"], "iso3"].unique().tolist()),
        "unmapped_country_ids": sorted(ranked.loc[ranked["iso3"].str.startswith("ATLAS"), "countryId"].astype(int).unique().tolist()),
        "aggregates": sorted({c for c in frame["iso3"].unique() if not _ISO3_RE.match(c)}),
    }
    caveats = [
        Caveat("atlas_cross_check_only", "info",
               "Atlas ECI is a cross-check; the toolkit computes ECI from BACI. Atlas trade data are re-cleaned "
               "Comtrade mirror data and differ from BACI and from the Atlas Dataverse files.", {}),
        Caveat("atlas_rank_universe", "info",
               "ECI ranks are computed locally among countries with a non-null ECI in each year (former countries "
               "excluded); the Atlas rankings page may use a different country set.", {"universe": meta["rank_universe"]}),
        Caveat("atlas_unversioned", "warn",
               "The Atlas API exposes no data version and values change when the Atlas is updated; pin the payload "
               "with a snapshot (CGJRKIT_SNAPSHOT) and cite the retrieval date.", {}),
    ]
    if not len(frame):
        caveats.append(Caveat("atlas_empty", "warn", "No Atlas observations matched the request.", {}))
    return ParsedAtlas(frame, meta, caveats)


# ---------------------------------------------------------------------------
# rate limiting
# ---------------------------------------------------------------------------
class MinuteLimiter:
    """Sliding-window limiter: at most ``limit`` calls per ``window_s`` seconds (sleeps when full)."""

    def __init__(self, limit: int = REQUESTS_PER_MINUTE, window_s: float = 60.0,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.limit, self.window_s, self.clock, self.sleep = int(limit), float(window_s), clock, sleep
        self._calls: deque[float] = deque()

    def acquire(self) -> float:
        """Block until a call is allowed; return the seconds waited."""
        waited = 0.0
        while True:
            now = self.clock()
            while self._calls and now - self._calls[0] >= self.window_s:
                self._calls.popleft()
            if len(self._calls) < self.limit:
                self._calls.append(now)
                return waited
            pause = self.window_s - (now - self._calls[0])
            self.sleep(max(pause, 0.0))
            waited += max(pause, 0.0)


#: one limiter per process, shared by all Atlas instances
_LIMITER = MinuteLimiter()


def _served_from_cache(cache: Cache, url: str, body: dict, refresh: bool) -> bool:
    params = {"__body__": body}
    snapshot = os.environ.get("CGJRKIT_SNAPSHOT", "")
    if snapshot and cache.pinned(snapshot, Atlas.source, url, params):
        return True
    return (not refresh) and cache.latest(Atlas.source, url, params) is not None


# ---------------------------------------------------------------------------
# connector
# ---------------------------------------------------------------------------
class Atlas(Connector):
    """Atlas of Economic Complexity GraphQL API (ECI cross-check)."""

    source = "atlas"
    license = LICENSE
    citation = CITATION
    domains = ("atlas.hks.harvard.edu",)

    def __init__(self, cache: Cache | None = None, limiter: MinuteLimiter | None = None):
        super().__init__(cache)
        self.limiter = limiter or _LIMITER

    def _post(self, url: str, body: dict, refresh: bool) -> tuple[bytes, Retrieval]:
        if not _served_from_cache(self.cache, url, body, refresh):
            self.limiter.acquire()
        try:
            return http.fetch(self.source, url, None, cache=self.cache, refresh=refresh, method="POST",
                              json_body=body, timeout=120.0)
        except http.FetchError as exc:
            raise AtlasError(f"Atlas request failed: {exc}") from exc

    def fetch(
        self,
        countries: Sequence[str] | str | None = None,
        start: int = 2013,
        end: int = 2023,
        *,
        product_class: str = DEFAULT_PRODUCT_CLASS,
        indicators: Sequence[str] = ("eci", "eci_rank"),
        refresh: bool = False,
    ) -> Result:
        """ECI and ECI rank for ``countries`` (ISO3; ``None`` = all), ``start``..``end``.

        One request fetches every country for the year range (ranks need the full
        set); results are cached. A cached GraphQL error payload is re-fetched once.
        """
        pc = _check_class(product_class)
        url, body = build_request(start, end, pc, trade_values=any(i in ("export_value", "import_value")
                                                                     for i in indicators))
        payload, retrieval = self._post(url, body, refresh)
        try:
            parsed = parse(payload, countries, start, end, indicators, pc)
        except AtlasError:
            if refresh:
                raise
            payload, retrieval = self._post(url, body, True)
            parsed = parse(payload, countries, start, end, indicators, pc)
        df = parsed.frame
        validate_frame(df)
        prov = self.provenance(retrieval, dataset=f"Atlas {pc}", vintage="", tool="Atlas.fetch",
                               notes=["GraphQL countryYear/locationCountry", "ECI rank computed by cgjrkit",
                                      f"retrieved {retrieval.retrieved_at}", "rate limit: 120 requests/minute"])
        res = Result(data=df, provenance=[prov], caveats=[], meta={**parsed.meta, "query": body["query"]})
        for c in parsed.caveats:
            res.add_caveat(c)
        res.add_caveat(Caveat("atlas_licence", "warn", LICENSE + ".", {"terms": "https://github.com/harvard-growth-lab/api-docs"}))
        return res

    def eci(self, countries: Sequence[str] | str | None = None, start: int = 2013, end: int = 2023,
            product_class: str = DEFAULT_PRODUCT_CLASS, refresh: bool = False) -> Result:
        """Shortcut for ECI and computed rank."""
        return self.fetch(countries, start, end, product_class=product_class, refresh=refresh)
