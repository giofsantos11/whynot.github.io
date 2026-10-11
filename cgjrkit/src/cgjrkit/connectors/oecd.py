"""OECD SDMX REST connector: Product Market Regulation (PMR, OECD and OECD-WBG),
Services Trade Restrictiveness Index (STRI) and FDI Regulatory Restrictiveness
Index (FDIRRI).

Endpoint (``GET``, keyless)::

    https://sdmx.oecd.org/public/rest/data/{agency},{dataflow},{version}/{key}
        ?dimensionAtObservation=AllDimensions&format=csvfilewithlabels
        [&startPeriod=YYYY&endPeriod=YYYY]

``version`` may be empty (latest version); ``key`` is ``all`` or a dot-separated
filter in DSD dimension order (``+`` joins codes, an empty position means all).

``format=csvfilewithlabels`` returns SDMX-CSV with the columns ``STRUCTURE,
STRUCTURE_ID, STRUCTURE_NAME, ACTION``, then one code column and one label
column per dimension (``REF_AREA, Reference area, ...``), ``TIME_PERIOD, Time
period, OBS_VALUE, Observation value`` and attribute code/label pairs
(``OBS_STATUS, Observation status, UNIT_MULT, ...``). The plain SDMX-CSV form
(``DATAFLOW`` first, codes only) and the ``labels=both`` form (``"REF_AREA:
Reference area"`` headers, ``"KEN: Kenya"`` cells) are read too.

Rate limits: the OECD states 60 data downloads per hour on oecd.org and 20
queries per minute plus 20 downloads per hour on an OECD iLibrary copy of the
same page. This connector plans for **20 network queries per hour**: each
panel is one bulk query for the whole dataflow (``key=all``, no period
filter) that is cached and then filtered locally, and a persistent hourly
budget (``<cache>/ratelimit/oecd.json``) refuses the 21st network query in any
60-minute window. Cache hits never count. ``http.fetch`` is called with
``retries=1`` so a 429 answer is not retried into a longer block.

Dataflows (see ``docs/apis/oecd.md`` for evidence and what is UNVERIFIED):

* ``PMR``: OECD.ECO.GCRD, DSD_PMR@DF_PMR, version 1.3;
* ``PMR_WBG``: OECD.ECO.GCRD, DSD_PMR@DF_PMR_WBG, latest version;
* ``STRI``: OECD.TAD.TPD, DSD_STRI@DF_STRI_MAIN, latest version;
* ``STRI_DIGITAL``: OECD.TAD.TPD, DSD_STRI@DF_STRI_DIGITAL, version 1.0;
* ``FDIRRI``: OECD.DAF.INV, DSD_FDIRRI_SCORES@DF_FDIRRI_SCORES, version 2.0.

Licence: OECD content published from 1 July 2024 is CC BY 4.0 (verified for
publications; assumed for these datasets).

Design: :func:`build_key`, :func:`build_request`, :func:`read_sdmx_csv` and
:func:`parse` are pure; :class:`OECD` wires them through
:func:`cgjrkit.core.http.fetch` and enforces the hourly budget.
"""

from __future__ import annotations

import io
import json
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from cgjrkit.connectors.base import Connector
from cgjrkit.core import http
from cgjrkit.core.cache import Cache, Retrieval
from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Result
from cgjrkit.core.schema import empty_frame, make_frame, validate_frame, year_of

BASE_URL = "https://sdmx.oecd.org/public/rest"
DATA_URL = BASE_URL + "/data/{agency},{dataflow},{version}/{key}"
#: network queries allowed per rolling hour (the stricter of the two published figures)
HOURLY_LIMIT = 20
HOURLY_WINDOW_S = 3600.0
FORMAT = "csvfilewithlabels"

LICENSE = "CC BY 4.0 (OECD open-by-default policy for content from 1 July 2024; assumed for these datasets)"
CITATION = "OECD Data Explorer (sdmx.oecd.org)"

#: SDMX CL_OBS_STATUS letters -> canonical obs_status (unknown letters map to "").
OBS_STATUS_MAP: dict[str, str] = {
    "": "", "A": "", "M": "", "L": "", "N": "", "U": "",
    "B": "break", "E": "estimate", "F": "forecast", "I": "imputed", "P": "provisional",
}

#: Column ids that are never series dimensions.
_META_COLUMNS = {"STRUCTURE", "STRUCTURE_ID", "STRUCTURE_NAME", "ACTION", "DATAFLOW", "TIME_PERIOD", "OBS_VALUE"}
#: Common SDMX attribute ids (OECD DSDs). ``UNIT_MEASURE`` is deliberately absent:
#: it is a dimension in the STRI and FDIRRI DSDs.
KNOWN_ATTRIBUTES = {
    "OBS_STATUS", "OBS_STATUS_1", "CONF_STATUS", "UNIT_MULT", "DECIMALS", "BASE_PER", "OBS_NOTE",
    "TIME_FORMAT", "COMMENT_OBS", "COMMENT_TS", "REF_YEAR_PRICE", "DATA_COMP", "OBS_CONF",
}
_ID_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_ISO3_RE = re.compile(r"^[A-Z]{3}$")
_STRUCTURE_ID_RE = re.compile(r"^(?P<agency>[^:]+):(?P<id>[^(]+)\((?P<version>[^)]+)\)$")
#: labels skipped when building readable indicator labels
_TRIVIAL_CODES = {"_Z", "_X"}


# ---------------------------------------------------------------------------
# dataflow registry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Flow:
    """An OECD dataflow this connector knows about."""

    key: str                       # short name used in the canonical ``dataset`` column
    agency: str
    dataflow: str
    version: str                   # "" = latest version
    label: str
    unit: str                      # default unit label (the UNIT_MEASURE label wins when present)
    citation: str
    dims: tuple[str, ...] = ()     # DSD dimension order (excl. TIME_PERIOD) when known
    dims_verified: bool = False
    version_verified: bool = False
    notes: str = ""

    @property
    def ref(self) -> str:
        return f"{self.agency},{self.dataflow},{self.version}"


FLOWS: dict[str, Flow] = {
    "PMR": Flow(
        "PMR", "OECD.ECO.GCRD", "DSD_PMR@DF_PMR", "1.3",
        "OECD Product Market Regulation indicators",
        "index, 0 (least restrictive) to 6 (most restrictive)",
        "OECD, Product Market Regulation (PMR) indicators",
        # key ".MEASURE.A" seen in a World Bank pipeline: 3 positions. Names other
        # than REF_AREA and MEASURE are UNVERIFIED.
        dims=("REF_AREA", "MEASURE", "FREQ"), dims_verified=False, version_verified=True,
        notes="2018 methodology; values before 2018 are not comparable",
    ),
    "PMR_WBG": Flow(
        "PMR_WBG", "OECD.ECO.GCRD", "DSD_PMR@DF_PMR_WBG", "",
        "OECD-World Bank Group Product Market Regulation indicators",
        "index, 0 (least restrictive) to 6 (most restrictive)",
        "OECD-WBG Product Market Regulation (PMR) database",
        dims=("REF_AREA", "MEASURE", "FREQ"), dims_verified=False, version_verified=False,
        notes="non-OECD economies surveyed with the World Bank Group; same DSD as PMR",
    ),
    "STRI": Flow(
        "STRI", "OECD.TAD.TPD", "DSD_STRI@DF_STRI_MAIN", "",
        "OECD Services Trade Restrictiveness Index",
        "index, 0 (open) to 1 (closed)",
        "OECD, Services Trade Restrictiveness Index (STRI)",
        # order inferred from a generic SDMX-ML response of DF_STRI_DIGITAL (same DSD)
        # and its key "A......." (8 positions): UNVERIFIED.
        dims=("FREQ", "REF_AREA", "MEASURE", "ACTIVITY", "METHODOLOGY", "STRI_TYPE", "UNIT_MEASURE", "COUNTERPART_AREA"),
        dims_verified=False, version_verified=False,
    ),
    "STRI_DIGITAL": Flow(
        "STRI_DIGITAL", "OECD.TAD.TPD", "DSD_STRI@DF_STRI_DIGITAL", "1.0",
        "OECD Digital Services Trade Restrictiveness Index",
        "index, 0 (open) to 1 (closed)",
        "OECD, Digital Services Trade Restrictiveness Index",
        dims=("FREQ", "REF_AREA", "MEASURE", "ACTIVITY", "METHODOLOGY", "STRI_TYPE", "UNIT_MEASURE", "COUNTERPART_AREA"),
        dims_verified=False, version_verified=True,
    ),
    "FDIRRI": Flow(
        "FDIRRI", "OECD.DAF.INV", "DSD_FDIRRI_SCORES@DF_FDIRRI_SCORES", "2.0",
        "OECD FDI Regulatory Restrictiveness Index",
        "index, 0 (open) to 1 (closed)",
        "OECD, FDI Regulatory Restrictiveness Index",
        dims=("REF_AREA", "ACTIVITY", "POL_CAT", "MEASURE", "UNIT_MEASURE"), dims_verified=True, version_verified=True,
    ),
}

#: aliases accepted by :func:`get_flow` (case-insensitive)
FLOW_ALIASES: dict[str, str] = {
    "PRODUCT_MARKET_REGULATION": "PMR", "OECD_WBG_PMR": "PMR_WBG", "PMR_OECD_WBG": "PMR_WBG",
    "SERVICES_TRADE_RESTRICTIVENESS": "STRI", "STRI_MAIN": "STRI", "DSTRI": "STRI_DIGITAL",
    "FDI_RRI": "FDIRRI", "FDI_RESTRICTIVENESS": "FDIRRI", "FDIRRI_SCORES": "FDIRRI",
}


class OECDError(RuntimeError):
    """An OECD payload could not be read or a request could not be built."""


class OECDRateLimitError(OECDError):
    """The hourly network budget for sdmx.oecd.org is used up."""

    def __init__(self, used: int, limit: int, retry_after_s: float):
        self.used, self.limit, self.retry_after_s = used, limit, retry_after_s
        super().__init__(
            f"OECD hourly budget used: {used}/{limit} network queries in the last hour. "
            f"Next slot in {retry_after_s:.0f} s. Serve from cache, wait, or pass force=True."
        )


def get_flow(name: str | Flow) -> Flow:
    """Resolve a short key (``"PMR"``), an alias, a dataflow id or ``AGENCY,DATAFLOW,VERSION``."""
    if isinstance(name, Flow):
        return name
    raw = str(name).strip()
    up = raw.upper().replace("-", "_").replace(" ", "_")
    if up in FLOWS:
        return FLOWS[up]
    if up in FLOW_ALIASES:
        return FLOWS[FLOW_ALIASES[up]]
    for flow in FLOWS.values():
        if raw == flow.dataflow or raw.split("@")[-1] == flow.dataflow.split("@")[-1]:
            return flow
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) in (2, 3) and "@" in parts[1]:
        version = parts[2] if len(parts) == 3 else ""
        short = parts[1].split("@")[-1].removeprefix("DF_")
        return Flow(short, parts[0], parts[1], version, parts[1], "", f"OECD, {parts[1]}")
    raise ValueError(f"unknown OECD dataflow {name!r}; known: {sorted(FLOWS)} or 'AGENCY,DSD@DF,VERSION'")


# ---------------------------------------------------------------------------
# pure request builders
# ---------------------------------------------------------------------------
def _codes(value: str | Sequence[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [v for v in re.split(r"[+,]", value) if v.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def build_key(flow: str | Flow, filters: Mapping[str, str | Sequence[str]] | None = None) -> str:
    """SDMX data key for ``filters`` ({dimension id: code(s)}), or ``"all"``.

    Needs the DSD dimension order (``Flow.dims``); dimensions not in ``filters``
    stay empty (all codes). Server-side keys are only needed when a bulk query
    would be too large; the default panels use ``"all"`` and filter locally.
    """
    spec = get_flow(flow)
    if not filters:
        return "all"
    if not spec.dims:
        raise OECDError(f"dimension order of {spec.dataflow} is unknown; use key='all' and filter locally")
    unknown = sorted(set(filters) - set(spec.dims))
    if unknown:
        raise ValueError(f"{spec.key} has no dimension(s) {unknown}; dimensions: {list(spec.dims)}")
    return ".".join("+".join(_codes(filters.get(dim))) for dim in spec.dims)


def build_request(
    flow: str | Flow,
    key: str = "all",
    start: int | str | None = None,
    end: int | str | None = None,
    version: str | None = None,
) -> tuple[str, dict]:
    """URL and query parameters for one SDMX-CSV-with-labels data query."""
    spec = get_flow(flow)
    ver = spec.version if version is None else str(version)
    url = DATA_URL.format(agency=spec.agency, dataflow=spec.dataflow, version=ver, key=key or "all")
    params: dict[str, str] = {"dimensionAtObservation": "AllDimensions", "format": FORMAT}
    if start is not None:
        params["startPeriod"] = str(start)
    if end is not None:
        params["endPeriod"] = str(end)
    return url, params


# ---------------------------------------------------------------------------
# pure parsing
# ---------------------------------------------------------------------------
@dataclass
class SdmxCsv:
    """An SDMX-CSV table split into codes and labels."""

    frame: pd.DataFrame                  # code columns, "<ID>__label" columns, TIME_PERIOD, OBS_VALUE
    dims: list[str]                      # series dimensions in payload order (excl. TIME_PERIOD)
    attributes: list[str]
    structure_id: str = ""               # e.g. "OECD.DAF.INV:DSD_FDIRRI_SCORES@DF_FDIRRI_SCORES(2.0)"
    structure_name: str = ""
    layout: str = ""                     # "labels" | "codes" | "labels_both"
    extra_dims: list[str] = field(default_factory=list)  # dimension columns not in the declared DSD order

    @property
    def version(self) -> str:
        m = _STRUCTURE_ID_RE.match(self.structure_id or "")
        return m.group("version") if m else ""


def _split_both(value: str) -> tuple[str, str]:
    code, sep, label = str(value).partition(": ")
    return (code.strip(), label.strip()) if sep else (str(value).strip(), "")


def read_sdmx_csv(payload: bytes | str, dims: Sequence[str] | None = None) -> SdmxCsv:
    """Read SDMX-CSV (with labels, codes only, or ``labels=both``) into :class:`SdmxCsv`.

    ``dims`` (the DSD dimension order) orders the series dimensions when every
    listed dimension is present. Any other id-like, non-attribute column that
    stands before ``TIME_PERIOD``/``OBS_VALUE`` (SDMX-CSV puts dimensions there
    and attributes after the measure) is a dimension too: it is appended in
    payload order and listed in ``extra_dims``, so distinct series never
    collapse onto one indicator when the declared (often UNVERIFIED) order lacks
    a dimension. Without usable ``dims``, dimensions are all id-like columns
    that are not structural columns or known attributes.
    """
    text = payload.decode("utf-8-sig") if isinstance(payload, bytes) else payload.lstrip("﻿")
    if not text.strip():
        raise OECDError("empty OECD payload")
    head = text.lstrip()[:200]
    if head.startswith("<") or head.startswith("{"):
        raise OECDError(f"OECD returned a non-CSV payload (error page or wrong format): {head[:120]!r}")
    raw = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, header=None)
    header = [str(h).strip() for h in raw.iloc[0].tolist()]
    body = raw.iloc[1:].reset_index(drop=True)
    body.columns = range(body.shape[1])

    out = pd.DataFrame(index=body.index)
    ids: list[str] = []
    layout = "codes"
    if any(": " in h and _ID_RE.match(h.split(": ", 1)[0]) for h in header):
        layout = "labels_both"
        for pos, h in enumerate(header):
            cid = h.split(": ", 1)[0].strip()
            col = body[pos].astype(str)
            if cid in ("TIME_PERIOD", "OBS_VALUE") or cid in _META_COLUMNS:
                out[cid] = col.str.strip()
            else:
                split = col.map(_split_both)
                out[cid] = split.map(lambda t: t[0])
                out[cid + "__label"] = split.map(lambda t: t[1])
            ids.append(cid)
    else:
        pos = 0
        while pos < len(header):
            h = header[pos]
            if _ID_RE.match(h):
                out[h] = body[pos].astype(str).str.strip()
                ids.append(h)
                nxt = header[pos + 1] if pos + 1 < len(header) else None
                if nxt is not None and not _ID_RE.match(nxt):
                    out[h + "__label"] = body[pos + 1].astype(str).str.strip()
                    layout = "labels"
                    pos += 2
                    continue
            pos += 1
    missing = [c for c in ("TIME_PERIOD", "OBS_VALUE") if c not in out.columns]
    if missing:
        raise OECDError(f"OECD CSV lacks column(s) {missing}; header starts {header[:8]}")
    candidates = [c for c in ids if c not in _META_COLUMNS]
    extra: list[str] = []
    if dims and all(d in candidates for d in dims):
        measure_at = min(ids.index(c) for c in ("TIME_PERIOD", "OBS_VALUE") if c in ids)
        before_measure = [c for c in ids[:measure_at] if c not in _META_COLUMNS]
        extra = [c for c in before_measure if c not in dims and c not in KNOWN_ATTRIBUTES]
        series_dims = list(dims) + extra
    else:
        series_dims = [c for c in candidates if c not in KNOWN_ATTRIBUTES]
    attributes = [c for c in candidates if c not in series_dims]
    sid = out["STRUCTURE_ID"].iloc[0] if "STRUCTURE_ID" in out.columns and len(out) else ""
    if not sid and "DATAFLOW" in out.columns and len(out):
        sid = out["DATAFLOW"].iloc[0]
    sname = out["STRUCTURE_NAME"].iloc[0] if "STRUCTURE_NAME" in out.columns and len(out) else ""
    return SdmxCsv(out, series_dims, attributes, str(sid), str(sname), layout, extra)


@dataclass
class ParsedOECD:
    frame: pd.DataFrame
    meta: dict = field(default_factory=dict)
    caveats: list[Caveat] = field(default_factory=list)


def _series_table(df: pd.DataFrame, key_dims: list[str], spec: Flow) -> pd.DataFrame:
    """One row per distinct series: indicator code, readable label and unit."""
    label_cols = [d + "__label" for d in key_dims if d + "__label" in df.columns]
    unit_cols = [c for c in ("UNIT_MEASURE", "UNIT_MEASURE__label", "UNIT_MULT") if c in df.columns]
    cols = list(dict.fromkeys(key_dims + label_cols + unit_cols))
    series = df[cols].drop_duplicates().reset_index(drop=True) if cols else pd.DataFrame(index=[0])
    indicators, labels, units = [], [], []
    for _, row in series.iterrows():
        codes = [str(row[d]) for d in key_dims]
        parts = []
        for d in key_dims:
            code = str(row[d])
            if code in _TRIVIAL_CODES:
                continue
            lab = str(row.get(d + "__label", "") or "")
            parts.append(lab or code)
        indicators.append(f"{spec.key}:" + ".".join(codes))
        labels.append(spec.label + (": " + "; ".join(parts) if parts else ""))
        unit_label = str(row.get("UNIT_MEASURE__label", "") or "")
        unit = spec.unit if (not unit_label or unit_label.lower() == "index") else unit_label
        mult = str(row.get("UNIT_MULT", "") or "").strip()
        if mult and mult != "0":
            unit = f"{unit} (x10^{mult})"
        units.append(unit or "")
    series["indicator"] = indicators
    series["indicator_label"] = labels
    series["unit"] = units
    return series


def parse(
    payload: bytes | str,
    flow: str | Flow,
    countries: Sequence[str] | str | None = None,
    start: int | str | None = None,
    end: int | str | None = None,
    filters: Mapping[str, str | Sequence[str]] | None = None,
) -> ParsedOECD:
    """Canonical frame from an OECD SDMX-CSV payload.

    ``indicator`` is ``"<flow key>:<codes of the series dimensions except
    REF_AREA and FREQ, joined by '.'>"`` (e.g. ``"FDIRRI:_T._T.RR.IX"``), so the
    same series always gets the same code whatever the query. ``countries``,
    ``start``/``end`` and ``filters`` ({dimension: codes}) are applied locally.
    """
    spec = get_flow(flow)
    table = read_sdmx_csv(payload, spec.dims)
    df = table.frame
    if "REF_AREA" not in df.columns:
        raise OECDError(f"{spec.key}: no REF_AREA column; dimensions found: {table.dims}")
    n_raw = len(df)
    mask = pd.Series(True, index=df.index)
    for dim, wanted in (filters or {}).items():
        codes = set(_codes(wanted))
        if dim not in df.columns:
            raise ValueError(f"{spec.key}: no dimension {dim!r} in the payload; dimensions: {table.dims}")
        if codes:
            mask &= df[dim].isin(codes)
    if countries is not None:
        wanted_countries = {c.upper() for c in _codes(countries)}
        if wanted_countries:
            mask &= df["REF_AREA"].str.upper().isin(wanted_countries)
    years = pd.to_numeric(df["TIME_PERIOD"].map(year_of), errors="coerce")
    if start is not None:
        mask &= years >= int(start)
    if end is not None:
        mask &= years <= int(end)
    values = pd.to_numeric(df["OBS_VALUE"].replace({"NaN": "", "NA": ""}), errors="coerce")
    dropped_missing = int((mask & values.isna()).sum())
    df = df.loc[mask & values.notna()].copy()
    df["value"] = values.loc[df.index]

    key_dims = [d for d in table.dims if d not in ("REF_AREA", "FREQ")]
    if len(df):
        series = _series_table(df, key_dims, spec)
        join_cols = [c for c in series.columns if c not in ("indicator", "indicator_label", "unit")]
        df = df.merge(series, on=join_cols, how="left") if join_cols else df.assign(
            indicator=series["indicator"].iloc[0], indicator_label=series["indicator_label"].iloc[0],
            unit=series["unit"].iloc[0])
        status = df["OBS_STATUS"].str.strip().str.upper() if "OBS_STATUS" in df.columns else pd.Series("", index=df.index)
        out = pd.DataFrame({
            "indicator": df["indicator"],
            "indicator_label": df["indicator_label"],
            "iso3": df["REF_AREA"],
            "country": df["REF_AREA__label"].where(df["REF_AREA__label"] != "", df["REF_AREA"])
            if "REF_AREA__label" in df.columns else df["REF_AREA"],
            "period": df["TIME_PERIOD"],
            "value": df["value"],
            "unit": df["unit"],
            "obs_status": status.map(lambda s: OBS_STATUS_MAP.get(s, "")),
        })
        frame = make_frame(out, source="oecd", dataset=spec.key)
        unknown_status = sorted({s for s in status.unique() if s and s not in OBS_STATUS_MAP})
    else:
        frame = empty_frame()
        unknown_status = []
    frame = Connector.finalize(frame)
    aggregates = sorted({c for c in frame["iso3"].unique() if not _ISO3_RE.match(c)})
    meta = {
        "flow": spec.ref,
        "structure_id": table.structure_id,
        "structure_name": table.structure_name,
        "structure_version": table.version,
        "layout": table.layout,
        "dimensions": table.dims,
        "extra_dimensions": table.extra_dims,
        "attributes": table.attributes,
        "rows_in_payload": n_raw,
        "dropped_missing_values": dropped_missing,
        "aggregates": aggregates,
        "unknown_obs_status": unknown_status,
        "dims_verified": spec.dims_verified,
    }
    caveats = _caveats(spec, frame, meta)
    return ParsedOECD(frame, meta, caveats)


def _caveats(spec: Flow, df: pd.DataFrame, meta: dict) -> list[Caveat]:
    out: list[Caveat] = []
    if not len(df):
        out.append(Caveat("oecd_empty", "warn", f"No {spec.key} observations matched the request.",
                          {"flow": spec.ref}))
        return out
    years = sorted({y for y in df["period"].map(year_of) if y is not None})
    if spec.key in ("PMR", "PMR_WBG") and years and min(years) < 2018 <= max(years):
        out.append(Caveat(
            "pmr_methodology_break", "warn",
            "PMR values before 2018 use the pre-2018 questionnaire and indicator tree; they are not comparable "
            "with the 2018 methodology (2018, 2023/24 vintages). Compare within one methodology.",
            {"years": years}))
    if spec.key in ("PMR", "PMR_WBG"):
        by_country = df.groupby("iso3")["period"].apply(lambda s: sorted(set(s))).to_dict()
        if len({tuple(v) for v in by_country.values()}) > 1:
            out.append(Caveat("pmr_survey_years", "info",
                              "Countries were surveyed in different years; benchmark on the nearest survey year.",
                              {"periods_by_country": {k: v for k, v in list(by_country.items())[:40]}}))
    if spec.key.startswith("STRI"):
        out.append(Caveat("stri_scale", "info",
                          "STRI values run from 0 (open) to 1 (closed) by sector; sector scores are not additive.",
                          {"flow": spec.ref}))
    if spec.key == "FDIRRI":
        out.append(Caveat("fdirri_scale", "info",
                          "FDI Regulatory Restrictiveness Index: 0 (open) to 1 (closed); statutory restrictions only, "
                          "not implementation.", {"flow": spec.ref}))
    if not spec.dims_verified:
        out.append(Caveat("oecd_dimensions_unverified", "info",
                          f"The DSD dimension order of {spec.dataflow} is not verified; codes were read from the "
                          "payload columns.", {"dimensions": meta.get("dimensions", [])}))
    if meta.get("extra_dimensions"):
        out.append(Caveat("oecd_extra_dimensions", "info",
                          f"The {spec.dataflow} payload has dimension columns missing from the expected DSD order; "
                          "they were appended to the indicator code so distinct series stay distinct.",
                          {"extra_dimensions": meta["extra_dimensions"], "expected": list(spec.dims)}))
    if meta.get("unknown_obs_status"):
        out.append(Caveat("oecd_unknown_obs_status", "info", "Unmapped OBS_STATUS codes were treated as ordinary.",
                          {"codes": meta["unknown_obs_status"]}))
    return out


# ---------------------------------------------------------------------------
# hourly budget (persistent)
# ---------------------------------------------------------------------------
class HourlyBudget:
    """Rolling-window budget of network queries, persisted as JSON timestamps.

    ``check_and_record()`` raises :class:`OECDRateLimitError` when ``limit``
    queries were recorded in the last ``window_s`` seconds; otherwise it records
    one. ``clock`` is injectable for tests.
    """

    def __init__(self, path: Path | str, limit: int = HOURLY_LIMIT, window_s: float = HOURLY_WINDOW_S,
                 clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.limit = int(limit)
        self.window_s = float(window_s)
        self.clock = clock

    def _load(self) -> list[float]:
        try:
            data = json.loads(self.path.read_text())
            return [float(t) for t in data.get("timestamps", [])]
        except (OSError, ValueError, AttributeError):
            return []

    def _save(self, stamps: list[float]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"limit": self.limit, "window_s": self.window_s, "timestamps": stamps}))

    def recent(self) -> list[float]:
        now = self.clock()
        return sorted(t for t in self._load() if now - t < self.window_s)

    def status(self) -> dict:
        stamps = self.recent()
        retry = (stamps[0] + self.window_s - self.clock()) if len(stamps) >= self.limit else 0.0
        return {"used": len(stamps), "limit": self.limit, "remaining": max(0, self.limit - len(stamps)),
                "retry_after_s": max(0.0, retry)}

    def check_and_record(self, force: bool = False) -> None:
        stamps = self.recent()
        if len(stamps) >= self.limit and not force:
            raise OECDRateLimitError(len(stamps), self.limit, stamps[0] + self.window_s - self.clock())
        stamps.append(self.clock())
        self._save(stamps)


def _offline() -> bool:
    return os.environ.get("CGJRKIT_OFFLINE", "") not in ("", "0", "false")


def served_from_cache(cache: Cache, source: str, url: str, params: dict | None, refresh: bool = False) -> bool:
    """True when :func:`cgjrkit.core.http.fetch` would answer from a snapshot or the cache."""
    snapshot = os.environ.get("CGJRKIT_SNAPSHOT", "")
    if snapshot and cache.pinned(snapshot, source, url, dict(params or {})):
        return True
    return (not refresh) and cache.latest(source, url, dict(params or {})) is not None


# ---------------------------------------------------------------------------
# connector
# ---------------------------------------------------------------------------
class OECD(Connector):
    """OECD PMR, OECD-WBG PMR, STRI and FDIRRI from sdmx.oecd.org (one cached bulk query per flow)."""

    source = "oecd"
    license = LICENSE
    citation = CITATION
    domains = ("sdmx.oecd.org",)

    def __init__(self, cache: Cache | None = None, hourly_limit: int = HOURLY_LIMIT,
                 budget: HourlyBudget | None = None):
        super().__init__(cache)
        self.budget = budget or HourlyBudget(self.cache.root / "ratelimit" / "oecd.json", limit=hourly_limit)

    def budget_status(self) -> dict:
        """Network queries used and remaining in the current rolling hour."""
        return self.budget.status()

    def _get(self, url: str, params: dict, refresh: bool, force: bool) -> tuple[bytes, Retrieval, bool]:
        cached = served_from_cache(self.cache, self.source, url, params, refresh)
        if not cached and not _offline():
            self.budget.check_and_record(force=force)
        try:
            payload, retrieval = http.fetch(self.source, url, params, cache=self.cache, refresh=refresh,
                                            timeout=300.0, retries=1)
        except http.FetchError as exc:
            raise OECDError(f"OECD query failed ({url}): {exc}") from exc
        return payload, retrieval, cached

    def fetch(
        self,
        flow: str | Flow,
        countries: Sequence[str] | str | None = None,
        start: int | str | None = None,
        end: int | str | None = None,
        *,
        filters: Mapping[str, str | Sequence[str]] | None = None,
        server_filter: bool = False,
        version: str | None = None,
        refresh: bool = False,
        force: bool = False,
    ) -> Result:
        """One OECD dataflow as a canonical frame.

        By default the whole dataflow is requested once (``key=all``, no period
        filter) and cached; ``countries``, ``start``/``end`` and ``filters`` are
        applied locally, so every peer panel reuses one payload. With
        ``server_filter=True`` the filters and periods go into the query (smaller
        payload, separate cache entry and budget use). ``force=True`` bypasses the
        hourly budget (not the provider's limit).
        """
        spec = get_flow(flow)
        if server_filter:
            server = dict(filters or {})
            if countries is not None:
                server["REF_AREA"] = _codes(countries)
            key = build_key(spec, server)
            url, params = build_request(spec, key, start, end, version)
        else:
            url, params = build_request(spec, "all", None, None, version)
        payload, retrieval, cached = self._get(url, params, refresh, force)
        parsed = parse(payload, spec, countries=countries, start=start, end=end, filters=filters)
        df = parsed.frame
        validate_frame(df)
        vintage = f"{spec.dataflow} v{parsed.meta['structure_version'] or spec.version or 'latest'}"
        notes = [f"dataflow {spec.ref or spec.dataflow}", f"served from {'cache' if cached else 'network'}",
                 "OECD rate limit planned at 20 queries/hour (bulk query, local filtering)"]
        if spec.notes:
            notes.append(spec.notes)
        prov = self.provenance(retrieval, dataset=spec.key, vintage=vintage, tool=f"OECD.fetch[{spec.key}]", notes=notes)
        prov.citation = spec.citation
        caveats = list(parsed.caveats)
        caveats.append(Caveat("oecd_licence", "info", "OECD data: " + LICENSE + ". Attribute the OECD.",
                              {"flow": spec.ref}))
        meta = {**parsed.meta, "budget": self.budget_status(), "served_from_cache": cached,
                "request": {"url": url, "params": params}}
        res = Result(data=df, provenance=[prov], caveats=[], meta=meta)
        for c in caveats:
            res.add_caveat(c)
        return res

    # -- panels -------------------------------------------------------------
    def pmr(
        self,
        countries: Sequence[str] | str | None = None,
        start: int | str | None = None,
        end: int | str | None = None,
        measures: Sequence[str] | str | None = None,
        *,
        wbg: bool = False,
        refresh: bool = False,
        force: bool = False,
    ) -> Result:
        """PMR indicators (``measures`` e.g. ``"PMR"``, ``"STATE_INVOL"``, ``"BARRIER_ENTRY"``,
        ``"BARRIER_TRADE"``, ``"FDI_INDEX"``, ``"TARIFFS"``; ``None`` = all). ``wbg=True``
        reads the OECD-WBG dataflow (non-OECD economies)."""
        filters = {"MEASURE": _codes(measures)} if measures else None
        return self.fetch("PMR_WBG" if wbg else "PMR", countries, start, end, filters=filters,
                          refresh=refresh, force=force)

    def stri(
        self,
        countries: Sequence[str] | str | None = None,
        start: int | str | None = None,
        end: int | str | None = None,
        sectors: Sequence[str] | str | None = None,
        measures: Sequence[str] | str | None = None,
        *,
        digital: bool = False,
        refresh: bool = False,
        force: bool = False,
    ) -> Result:
        """STRI by sector (``sectors``: ACTIVITY codes; ``measures``: MEASURE codes such as the
        overall index or policy areas; ``None`` = all). ``digital=True`` reads the Digital STRI."""
        filters: dict[str, list[str]] = {}
        if sectors:
            filters["ACTIVITY"] = _codes(sectors)
        if measures:
            filters["MEASURE"] = _codes(measures)
        return self.fetch("STRI_DIGITAL" if digital else "STRI", countries, start, end, filters=filters or None,
                          refresh=refresh, force=force)

    def fdirri(
        self,
        countries: Sequence[str] | str | None = None,
        start: int | str | None = None,
        end: int | str | None = None,
        activities: Sequence[str] | str | None = ("_T",),
        policy_categories: Sequence[str] | str | None = ("_T",),
        *,
        refresh: bool = False,
        force: bool = False,
    ) -> Result:
        """FDI Regulatory Restrictiveness Index (``activities``: ACTIVITY codes, ``"_T"`` = all
        activities; ``policy_categories``: ``"_T"`` total, ``"CLAS1_1"`` foreign equity limits,
        ``"CLAS1_2"`` screening and approval, ``"CLAS1_3"`` key foreign personnel, ``"_O"``
        other restrictions; ``None`` = all)."""
        filters: dict[str, list[str]] = {"MEASURE": ["RR"]}
        if activities:
            filters["ACTIVITY"] = _codes(activities)
        if policy_categories:
            filters["POL_CAT"] = _codes(policy_categories)
        return self.fetch("FDIRRI", countries, start, end, filters=filters, refresh=refresh, force=force)


def oecd_mix_caveats(objs: pd.DataFrame | Result | Sequence[pd.DataFrame | Result]) -> list[Caveat]:
    """Warn when OECD PMR and OECD-WBG PMR rows are combined (different survey rounds and
    country coverage) or when PMR rows span the 2018 methodology change."""
    items = [objs] if isinstance(objs, (pd.DataFrame, Result)) else list(objs)
    frames = [o.data if isinstance(o, Result) else o for o in items]
    rows = pd.concat([f[f["source"] == "oecd"] for f in frames if len(f)], ignore_index=True) if frames else empty_frame()
    out: list[Caveat] = []
    if not len(rows):
        return out
    datasets = set(rows["dataset"])
    if {"PMR", "PMR_WBG"} <= datasets:
        out.append(Caveat("pmr_oecd_wbg_mix", "warn",
                          "OECD PMR and OECD-WBG PMR rows are combined; check that survey years and the methodology "
                          "match before benchmarking.", {"datasets": sorted(datasets)}))
    pmr = rows[rows["dataset"].isin(["PMR", "PMR_WBG"])]
    years = sorted({y for y in pmr["period"].map(year_of) if y is not None})
    if years and min(years) < 2018 <= max(years):
        out.append(Caveat("pmr_methodology_break", "warn",
                          "PMR values before and after the 2018 methodology change are combined.", {"years": years}))
    return out
