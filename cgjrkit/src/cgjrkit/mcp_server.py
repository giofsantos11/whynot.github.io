"""PREVIEW MCP server: cgjrkit's deterministic tools over stdio.

The AI client chooses tools; the tools compute. The server exposes a fixed set of
tools and never returns full data payloads, only :meth:`Result.summary` (row count,
columns, the first rows, data hash, provenance, caveats, meta):

* ``list_connectors``: the registered data connectors, their hosts, licences and fetch methods
* ``get_series``: run one connector fetch method (e.g. ``worldbank.fetch``) with keyword params
* ``peer_aggregate``, ``growth_accounting``, ``shift_share``, ``jobs_shapley``,
  ``benchmark_fit``: methods from :mod:`cgjrkit.methods`
* ``render_chart``: build a house-style chart from a result and write it to files
* ``run_caveat_check``: PASS / WARN / BLOCK for one output and its inputs

Every tool that produces a :class:`~cgjrkit.core.provenance.Result` stores it in
an in-memory, per-session store and returns its ``result_id``. Later calls take
that id wherever a frame is expected, so data never round-trips through the
model. A frame argument may instead be a list of canonical records (inline data);
such values get an ``inline_data_unverified`` warning, because cgjrkit did not
retrieve them and cannot vouch for their source.

Run with ``cgjrkit-mcp`` (``[project.scripts]``) or ``python -m cgjrkit.mcp_server``.
Options: ``--cache DIR`` (sets ``CGJRKIT_CACHE``), ``--offline`` (serve only from
the cache), ``--snapshot NAME`` (vintage-pinned replay; see :mod:`cgjrkit.core.cache`),
``--output-root DIR`` (``render_chart`` writes only inside DIR; default
``CGJRKIT_OUTPUT_DIR``, else no restriction).

``get_series`` refuses the connector parameters in :data:`RESTRICTED_PARAMS`
(``url``, ``local_zip``, ``token``, ``force``): over MCP, connectors only reach
their declared provider hosts, never local files, secrets stay in environment
variables, and provider rate budgets (OECD: 20 requests per hour) cannot be
bypassed.

``render_chart`` guards what it writes and what it draws:

* the footer's source line always comes from provenance: the options in
  :data:`RESERVED_CHART_OPTIONS` (``source_line``, ...) are refused;
* frame-valued chart options (:data:`FRAME_OPTIONS`: ``band``, ``size``) take a
  result_id (or inline records), never values typed by the client;
* the inputs go through :func:`cgjrkit.qa.caveat_check.run_caveat_check`
  (default context) first: a BLOCK refuses the chart and nothing is written;
  the engine's footnotes are added to the footer;
* it never replaces a file it did not write itself in this session, and with an
  output root it writes only inside that directory.

Status: preview. The tool set and the argument names may change. Connector calls
need the provider hosts on the network allowlist (``docs/ALLOWLIST.md``).
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import math
import os
import re
from collections import OrderedDict
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import numpy as np
import pandas as pd
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from cgjrkit import __version__
from cgjrkit.connectors import CONNECTORS, connector_info
from cgjrkit.connectors.base import Connector
from cgjrkit.core.caveats import Caveat, worst
from cgjrkit.core.provenance import Provenance, Result
from cgjrkit.core.schema import COLUMNS, make_frame

SERVER_NAME = "cgjrkit"

#: upper bound on rows returned in a summary's ``head``
MAX_ROWS = 200
#: lists and dicts in summaries are cut to this many items
MAX_ITEMS = 50
#: strings in summaries are cut to this many characters
MAX_STR = 4000
#: Results kept per session (least recently used are dropped first)
STORE_CAPACITY = 128

#: provenance source for values passed inline by the client
INLINE_SOURCE = "inline"
INLINE_CAVEAT = "inline_data_unverified"

#: connector parameters get_series refuses: they would point a connector at an
#: arbitrary URL or local file, or carry a secret through the client's transcript
RESTRICTED_PARAMS = {
    "url": "custom download URLs are disabled over MCP (set CGJRKIT_BRUEGEL_URL for Bruegel, or use the Python API)",
    "local_zip": "local files are disabled over MCP (use the Python API)",
    "token": "pass API keys through environment variables (e.g. CGJRKIT_WPP_KEY), never as tool arguments",
    "force": "provider request budgets (OECD: 20 requests per hour) cannot be bypassed over MCP; serve from the "
             "cache, wait for the next slot, or use the Python API",
}

CHART_FORMATS = ("vl.json", "svg", "png", "pdf", "html")
#: environment variable naming the only directory render_chart may write into (``--output-root``)
OUTPUT_ROOT_ENV = "CGJRKIT_OUTPUT_DIR"
#: render_chart options the client may not set: they would replace the provenance-derived footer,
#: or they are render_chart arguments of their own
RESERVED_CHART_OPTIONS = {
    "source_line": "the source line is built from provenance and cannot be replaced; add your text with 'note' "
                   "(it is printed above the caveat notes and the source line)",
    "note": "use the 'note' argument",
    "include_info": "use the 'include_info' argument",
    "right": "use the 'right' argument (a result_id)",
    "result": "use the 'result' argument",
    "kind": "use the 'kind' argument",
}
#: chart-builder options whose value is a frame: they take a result_id from this session (or inline
#: records); ``band`` also takes a [low, high] pair of result_ids
FRAME_OPTIONS = ("band", "size")
_STEM_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

INSTRUCTIONS = """\
cgjrkit replicates the standard charts of World Bank Country Growth and Jobs Reports from public APIs.
You choose the tools; the tools compute. Never type numbers into a chart or table yourself.

Workflow:
1. list_connectors: providers, hosts, licences and the fetch methods get_series accepts.
2. get_series(connector, params, method): fetch data. Returns a result_id and a summary
   (first rows, provenance, caveats), never the full payload.
3. Pass result_ids to the methods (peer_aggregate, growth_accounting, shift_share,
   jobs_shapley, benchmark_fit). Each returns a new result_id.
4. render_chart(result, kind, output_dir) writes the chart files and returns their paths. It runs the
   caveat check on its inputs, refuses BLOCKed inputs, and never overwrites files it did not write.
   options are chart-builder settings (focus, peers, index_base, ...); 'band' and 'size' take a
   result_id; 'where' filters rows, e.g. {'sector': 'TOTAL'}. The source line cannot be changed.
5. run_caveat_check(results, context) before you use an output: BLOCK means do not use it as is.

Frame arguments take a result_id from this session, or inline canonical records
(source, dataset, indicator, indicator_label, iso3, country, period, value, unit, obs_status).
Inline values are flagged inline_data_unverified. Result ids live only for this server session.
If a host is not reachable, the error names it; it must be added to the network allowlist.
"""

# -- argument types (module level so the SDK can resolve the annotations) ------

FrameInput = Annotated[
    str | list[dict[str, Any]],
    Field(
        description=(
            "A result_id returned by an earlier call in this session, or a list of canonical records "
            "(keys: source, dataset, indicator, indicator_label, iso3, country, period, value, unit, "
            "obs_status; extra keys such as 'sector' are kept). Inline records are flagged as unverified."
        )
    ),
]
PanelInput = Annotated[
    str | list[str] | list[dict[str, Any]],
    Field(description="A result_id, a list of result_ids (concatenated), or a list of canonical records."),
]
MaxRows = Annotated[int, Field(ge=0, le=MAX_ROWS, description="Rows to include in the summary's 'head'.")]
SectorMap = Annotated[
    dict[str, str] | None,
    Field(description="Optional map from indicator code to sector label, e.g. {'NV.AGR.TOTL.KN': 'AGR'}."),
]

READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
READ_ONLY_NETWORK = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)
# render_chart only creates files, or replaces charts it wrote itself in this session (it refuses to
# overwrite anything else), so it is not destructive; repeating a call rewrites the same files.
WRITES_FILES = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


# -- result store --------------------------------------------------------------


def result_fingerprint(result: Result) -> str:
    """Deterministic id for a Result: its data hash plus provenance and caveats."""
    canon = json.dumps(
        {
            "data": result.data_sha256,
            "provenance": [p.to_dict() for p in result.provenance],
            "caveats": [c.to_dict() for c in result.caveats],
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


class ResultStore:
    """Results produced in one server session, keyed by ``res_<fingerprint>`` (bounded, LRU)."""

    def __init__(self, capacity: int = STORE_CAPACITY):
        self.capacity = capacity
        self._items: OrderedDict[str, Result] = OrderedDict()

    def put(self, result: Result) -> str:
        key = f"res_{result_fingerprint(result)}"
        self._items[key] = result
        self._items.move_to_end(key)
        while len(self._items) > self.capacity:
            self._items.popitem(last=False)
        return key

    def get(self, key: str) -> Result:
        if key not in self._items:
            known = ", ".join(list(self._items)[-10:]) or "none yet"
            raise ToolError(
                f"unknown result_id {key!r}. Result ids live only for this server session "
                f"(most recent: {known})."
            )
        self._items.move_to_end(key)
        return self._items[key]

    def __contains__(self, key: object) -> bool:
        return key in self._items

    def __len__(self) -> int:
        return len(self._items)


# -- helpers -----------------------------------------------------------------


def jsonable(obj: Any, *, max_items: int = MAX_ITEMS, max_str: int = MAX_STR, _depth: int = 0) -> Any:
    """Convert ``obj`` to JSON-safe, size-limited data (NaN -> None, long lists cut)."""
    if _depth > 8:
        return str(obj)[:200]
    if obj is None or isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (float, np.floating)):
        value = float(obj)
        return value if math.isfinite(value) else None
    if isinstance(obj, str):
        return obj if len(obj) <= max_str else obj[:max_str] + f"... ({len(obj) - max_str} more characters)"
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (Caveat, Provenance)):
        obj = obj.to_dict()
    elif is_dataclass(obj) and not isinstance(obj, type):
        obj = asdict(obj)
    if isinstance(obj, pd.DataFrame):
        records = json.loads(obj.head(max_items).to_json(orient="records", date_format="iso"))
        if len(obj) > max_items:
            records.append(f"... ({len(obj) - max_items} more rows)")
        return records
    if isinstance(obj, (pd.Series, np.ndarray)):
        obj = list(obj)
    kw = {"max_items": max_items, "max_str": max_str, "_depth": _depth + 1}
    if isinstance(obj, Mapping):
        items = list(obj.items())
        out = {str(k): jsonable(v, **kw) for k, v in items[:max_items]}
        if len(items) > max_items:
            out["__truncated__"] = f"{len(items) - max_items} more keys"
        return out
    if isinstance(obj, (list, tuple, set, frozenset)):
        seq = sorted(obj, key=str) if isinstance(obj, (set, frozenset)) else list(obj)
        out_list = [jsonable(v, **kw) for v in seq[:max_items]]
        if len(seq) > max_items:
            out_list.append(f"... ({len(seq) - max_items} more items)")
        return out_list
    return str(obj)


@contextmanager
def tool_errors(label: str) -> Iterator[None]:
    """Report library errors to the client with their message (the SDK would hide it otherwise)."""
    try:
        yield
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(f"{label} failed: {type(exc).__name__}: {exc}") from exc


def data_source_line(result: Result) -> str:
    """``result.source_line()`` over the data providers only (cgjrkit method entries are not sources)."""
    data_prov = [p for p in result.provenance if p.source != "cgjrkit"]
    if not data_prov:
        return result.source_line()
    return Result(data=result.data.iloc[0:0], provenance=data_prov).source_line()


def status_of(caveats: Sequence[Caveat]) -> str:
    level = worst(list(caveats))
    return {"block": "BLOCK", "warn": "WARN"}.get(level or "", "PASS")


def inline_result(records: list[dict[str, Any]], name: str) -> Result:
    """Wrap client-supplied records in a Result that says where they came from."""
    if not records:
        raise ToolError(f"{name}: no records given")
    if not all(isinstance(r, dict) for r in records):
        raise ToolError(f"{name}: inline data must be a list of objects (canonical records)")
    raw = pd.DataFrame(records)
    frame = make_frame(raw, source=INLINE_SOURCE, dataset="client-supplied")
    for col in raw.columns:
        if col not in COLUMNS:
            frame[col] = raw[col].to_numpy()
    rows = int(len(frame))
    prov = Provenance(
        source=INLINE_SOURCE,
        dataset="client-supplied",
        params={"argument": name, "rows": rows},
        tool="mcp_server.inline_data",
        notes=["Values were passed inline by the MCP client; cgjrkit did not retrieve them."],
    )
    caveat = Caveat(
        INLINE_CAVEAT,
        "warn",
        f"Values for '{name}' were supplied inline by the client, not retrieved by a cgjrkit connector; "
        "their source cannot be verified.",
        {"argument": name, "rows": rows, "claimed_sources": sorted(set(frame["source"]))},
    )
    return Result(data=frame, provenance=[prov], caveats=[caveat], meta={"inline": True})


def fetch_methods(cls: type[Connector]) -> dict[str, Callable[..., Any]]:
    """Public methods of a connector class annotated ``-> Result`` (what get_series may call)."""
    out = {}
    for name, fn in inspect.getmembers(cls, inspect.isfunction):
        if name.startswith("_"):
            continue
        ret = inspect.signature(fn).return_annotation
        if ret is Result or ret == "Result":
            out[name] = fn
    return out


def describe_method(fn: Callable[..., Any]) -> dict[str, str]:
    sig = inspect.signature(fn)
    params = [p for p in sig.parameters.values() if p.name != "self"]
    doc = inspect.getdoc(fn) or ""
    return {
        "name": fn.__name__,
        "signature": str(sig.replace(parameters=params, return_annotation=inspect.Signature.empty)),
        "summary": doc.strip().splitlines()[0] if doc.strip() else "",
    }


def _clean(params: Mapping[str, Any]) -> dict[str, Any]:
    """Drop None values so the library defaults apply."""
    return {k: v for k, v in params.items() if v is not None}


# -- server --------------------------------------------------------------------


class CgjrkitTools:
    """The tool implementations, bound to one session's result store and connector instances."""

    def __init__(self, store: ResultStore | None = None, output_root: str | os.PathLike[str] | None = None):
        self.store = store or ResultStore()
        self._connectors: dict[str, Connector] = {}
        root = output_root if output_root is not None else (os.environ.get(OUTPUT_ROOT_ENV) or None)
        self.output_root: Path | None = Path(root).expanduser().resolve() if root else None
        self._written: set[Path] = set()  # files render_chart wrote in this session (it may replace these)

    # shared plumbing ---------------------------------------------------------
    def frame(self, ref: Any, name: str) -> Result:
        if isinstance(ref, Result):
            return ref
        if isinstance(ref, str):
            return self.store.get(ref)
        if isinstance(ref, list):
            return inline_result(ref, name)
        raise ToolError(f"{name}: expected a result_id or a list of records, got {type(ref).__name__}")

    def summarize(self, result: Result, max_rows: int = 20, **extra: Any) -> dict[str, Any]:
        result_id = self.store.put(result)
        summary = result.summary(max_rows=max(0, min(int(max_rows), MAX_ROWS)))
        out = {
            "result_id": result_id,
            **extra,
            "status": status_of(result.caveats),
            "source_line": data_source_line(result),
            **summary,
        }
        return jsonable(out, max_items=max(MAX_ITEMS, int(max_rows)))

    def output_dir(self, output_dir: str) -> Path:
        """Resolve ``output_dir``; with an output root, relative paths are taken inside it and escapes refused."""
        path = Path(output_dir).expanduser()
        if self.output_root is None:
            return path.resolve()
        resolved = (path if path.is_absolute() else self.output_root / path).resolve()
        if resolved != self.output_root and self.output_root not in resolved.parents:
            raise ToolError(f"output_dir {output_dir!r} is outside the output root {str(self.output_root)!r} "
                            f"(--output-root / {OUTPUT_ROOT_ENV}); pass a directory inside it")
        return resolved

    def chart_options(self, options: Mapping[str, Any] | None) -> dict[str, Any]:
        """Validate render_chart ``options``: refuse reserved keys, resolve frame-valued options to Results."""
        kw = dict(options or {})
        reserved = sorted(set(kw) & set(RESERVED_CHART_OPTIONS))
        if reserved:
            raise ToolError("; ".join(f"option {k!r} refused: {RESERVED_CHART_OPTIONS[k]}" for k in reserved))
        for key in FRAME_OPTIONS:
            if key not in kw or kw[key] is None:
                continue
            value = kw[key]
            name = f"options.{key}"
            if isinstance(value, str):
                kw[key] = self.store.get(value)
            elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
                kw[key] = inline_result(value, name)
            elif key == "band" and isinstance(value, list) and len(value) == 2 and all(isinstance(v, str) for v in value):
                kw[key] = (self.store.get(value[0]), self.store.get(value[1]))
            else:
                raise ToolError(f"{name} must be a result_id from this session or a list of canonical records"
                                + (" (or a [low, high] pair of result_ids)" if key == "band" else "")
                                + "; values typed into a chart carry no provenance")
        return kw

    def connector(self, name: str) -> Connector:
        if name not in CONNECTORS:
            raise ToolError(f"unknown connector {name!r}; available: {', '.join(CONNECTORS)}")
        if name not in self._connectors:
            self._connectors[name] = CONNECTORS[name]()
        return self._connectors[name]

    # tools -------------------------------------------------------------------
    def list_connectors(self) -> dict[str, Any]:
        """List the data connectors: provider key, licence, citation, hosts, and the methods get_series accepts.

        Each method entry gives its keyword parameters (pass them in get_series' ``params``).
        Hosts must be on the network allowlist (docs/ALLOWLIST.md) for live calls.
        """
        rows = []
        for name in CONNECTORS:
            with tool_errors(f"loading connector {name}"):
                info = connector_info(name)
                info["methods"] = [describe_method(fn) for fn in fetch_methods(CONNECTORS[name]).values()]
            rows.append(info)
        return jsonable(
            {"connectors": rows, "count": len(rows), "restricted_params": RESTRICTED_PARAMS}, max_items=200
        )

    def get_series(
        self,
        connector: Annotated[str, Field(description="Connector key from list_connectors, e.g. 'worldbank', 'imf', 'pwt'.")],
        params: Annotated[
            dict[str, Any] | None,
            Field(description="Keyword arguments for the method, e.g. {'indicators': 'NY.GDP.MKTP.KD.ZG', 'countries': ['KEN'], 'start': 2010}."),
        ] = None,
        method: Annotated[str, Field(description="Connector method returning a Result (see list_connectors); default 'fetch'.")] = "fetch",
        max_rows: MaxRows = 20,
    ) -> dict[str, Any]:
        """Fetch data through a cgjrkit connector and return a result_id plus a summary (never the full payload).

        Payloads are cached by content hash; provenance (endpoint, params, retrieval time, payload hash,
        vintage, licence) and caveats come with the summary.
        """
        conn = self.connector(connector)
        methods = fetch_methods(type(conn))
        if method not in methods:
            raise ToolError(f"{connector} has no data method {method!r}; choose from {sorted(methods)}")
        fn = getattr(conn, method)
        kwargs = dict(params or {})
        restricted = sorted(set(kwargs) & set(RESTRICTED_PARAMS))
        if restricted:
            raise ToolError("; ".join(f"parameter {name!r} refused: {RESTRICTED_PARAMS[name]}" for name in restricted))
        try:
            inspect.signature(fn).bind(**kwargs)
        except TypeError as exc:
            raise ToolError(f"{connector}.{method}{describe_method(methods[method])['signature']}: {exc}") from exc
        with tool_errors(f"{connector}.{method}"):
            result = fn(**kwargs)
        if not isinstance(result, Result):
            raise ToolError(f"{connector}.{method} did not return a Result")
        return self.summarize(result, max_rows, connector=connector, method=method)

    def peer_aggregate(
        self,
        data: FrameInput,
        members: Annotated[list[str], Field(description="ISO3 codes of the peer countries.")],
        peerset_name: Annotated[str, Field(description="Name of the peer set (labels the aggregates).")] = "Peers",
        stats: Annotated[list[str] | None, Field(description="Statistics: mean, median, p25, p75, min, max, ... Default mean, median, p25, p75.")] = None,
        min_n: Annotated[int, Field(ge=1, description="Minimum members with data; smaller aggregates get a peer_small_n caveat.")] = 3,
        frozen_at: Annotated[str | None, Field(description="ISO date on which the peer set was frozen; unfrozen sets get a caveat.")] = None,
        approver: str = "",
        criteria: Annotated[str, Field(description="How the peers were chosen (recorded in the result).")] = "",
        code: Annotated[str, Field(description="Aggregate code; default derived from the name.")] = "",
        focus: Annotated[str, Field(description="ISO3 of the report country (recorded).")] = "",
        by: Annotated[list[str] | None, Field(description="Grouping columns; must include 'indicator'. Default ['indicator'].")] = None,
        drop_below_min_n: bool = False,
        on_ppp_mix: Annotated[Literal["raise", "block"], Field(description="'raise' errors on mixed PPP bases; 'block' returns a BLOCKed result.")] = "raise",
        max_rows: MaxRows = 20,
    ) -> dict[str, Any]:
        """Peer-group statistics (mean, median, quartiles) per indicator and period, with member counts.

        Output rows use iso3 '<code>:<stat>' (e.g. 'PEERS:median') with columns stat, n and group.
        """
        res = self.frame(data, "data")
        with tool_errors("peer_aggregate"):
            from cgjrkit.methods.peers import PeerSet
            from cgjrkit.methods.peers import peer_aggregate as run

            peerset = PeerSet(
                name=peerset_name, members=members, criteria=criteria, approver=approver,
                frozen_at=frozen_at, code=code, focus=focus,
            )
            kwargs = _clean({"stats": stats, "by": by})
            out = run(res, peerset, min_n=min_n, drop_below_min_n=drop_below_min_n, on_ppp_mix=on_ppp_mix, **kwargs)
        return self.summarize(out, max_rows, tool="peer_aggregate")

    def growth_accounting(
        self,
        panel: PanelInput,
        periods: Annotated[
            dict[str, list[str]],
            Field(description="Window label -> [base year, end year], e.g. {'2001-10': ['2000', '2010']} (growth from the base year)."),
        ],
        alpha: Annotated[float | None, Field(gt=0, lt=1, description="Capital share; default from the labour share (1 - labsh).")] = None,
        iso3: Annotated[list[str] | None, Field(description="Countries to include (default all in the panel).")] = None,
        output: str = "rgdpna",
        capital: str = "rkna",
        labour: str = "emp",
        human_capital: Annotated[str | None, Field(description="Human capital indicator; null to leave it out.")] = "hc",
        labour_share: Annotated[str | None, Field(description="Labour share indicator; null to use a fixed alpha.")] = "labsh",
        weights: Literal["period_mean", "tornqvist"] = "period_mean",
        per_worker: bool = False,
        units: Literal["log", "percent"] = "log",
        max_rows: MaxRows = 20,
    ) -> dict[str, Any]:
        """Growth accounting: average annual contributions of capital, labour, human capital and TFP to growth.

        Expects a PWT-shaped panel (indicators rgdpna, rkna, emp, hc, labsh by default). Contributions add up
        exactly to output growth; meta['alpha_sensitivity'] reports alternative capital shares.
        """
        if isinstance(panel, list) and panel and all(isinstance(p, str) for p in panel):
            frames: Any = [self.frame(p, "panel") for p in panel]
        else:
            frames = self.frame(panel, "panel")
        windows = {}
        for label, span in periods.items():
            if len(span) != 2:
                raise ToolError(f"periods[{label!r}] must be [base year, end year], got {span}")
            windows[label] = (str(span[0]), str(span[1]))
        with tool_errors("growth_accounting"):
            from cgjrkit.methods.growth_accounting import growth_accounting as run

            out = run(
                frames, windows, alpha, iso3=iso3, output=output, capital=capital, labour=labour,
                human_capital=human_capital, labour_share=labour_share, weights=weights,
                per_worker=per_worker, units=units,
            )
        return self.summarize(out, max_rows, tool="growth_accounting")

    def shift_share(
        self,
        va_by_sector: Annotated[FrameInput, Field(description="Value added by sector (constant prices), one indicator per sector.")],
        emp_by_sector: Annotated[FrameInput, Field(description="Employment by sector, one indicator per sector.")],
        start: Annotated[str, Field(description="Start period, e.g. '2011'.")],
        end: Annotated[str, Field(description="End period, e.g. '2019'.")],
        variant: Literal["two_term", "three_term", "shapley"] = "two_term",
        va_sectors: SectorMap = None,
        emp_sectors: SectorMap = None,
        aggregate_va: Annotated[FrameInput | None, Field(description="Optional economy-wide VA to check sector coverage.")] = None,
        aggregate_emp: Annotated[FrameInput | None, Field(description="Optional total employment to check sector coverage.")] = None,
        annualize: Literal["cagr", "simple", "log", "none"] = "cagr",
        on_gap: Literal["raise", "caveat"] = "raise",
        emp_scale: Annotated[float, Field(gt=0, description="Multiply employment by this (e.g. 1000 for thousands).")] = 1.0,
        iso3: list[str] | None = None,
        max_rows: MaxRows = 20,
    ) -> dict[str, Any]:
        """Shift-share decomposition of labour-productivity growth into within-sector and structural-change terms."""
        va = self.frame(va_by_sector, "va_by_sector")
        emp = self.frame(emp_by_sector, "emp_by_sector")
        agg_va = self.frame(aggregate_va, "aggregate_va") if aggregate_va is not None else None
        agg_emp = self.frame(aggregate_emp, "aggregate_emp") if aggregate_emp is not None else None
        with tool_errors("shift_share"):
            from cgjrkit.methods.structural_change import shift_share as run

            out = run(
                va, emp, str(start), str(end), variant, va_sectors=va_sectors, emp_sectors=emp_sectors,
                aggregate_va=agg_va, aggregate_emp=agg_emp, annualize=annualize, on_gap=on_gap,
                emp_scale=emp_scale, iso3=iso3,
            )
        return self.summarize(out, max_rows, tool="shift_share")

    def jobs_shapley(
        self,
        gdp: Annotated[FrameInput, Field(description="Real GDP (constant prices).")],
        population: Annotated[FrameInput, Field(description="Total population.")],
        working_age: Annotated[FrameInput, Field(description="Working-age population.")],
        employment: Annotated[FrameInput, Field(description="Total employment.")],
        periods: Annotated[
            dict[str, str | list[str]],
            Field(description="Window label -> [start, end] (or a single period), e.g. {'2011-19': ['2011', '2019']}."),
        ],
        va_by_sector: FrameInput | None = None,
        emp_by_sector: FrameInput | None = None,
        va_sectors: SectorMap = None,
        emp_sectors: SectorMap = None,
        sector_variant: Literal["two_term", "three_term", "shapley"] = "shapley",
        method: Literal["shapley", "log"] = "shapley",
        annualize: Literal["cagr", "simple", "log", "none"] = "cagr",
        population_scale: float = 1.0,
        working_age_scale: float = 1.0,
        employment_scale: float = 1.0,
        sector_emp_scale: float | None = None,
        iso3: list[str] | None = None,
        max_rows: MaxRows = 20,
    ) -> dict[str, Any]:
        """Shapley decomposition of GDP per capita growth into output per worker, employment rate and working-age share.

        With sector frames, output per worker is split further into within-sector and between-sector terms.
        """
        frames = {
            "gdp": self.frame(gdp, "gdp"),
            "population": self.frame(population, "population"),
            "working_age": self.frame(working_age, "working_age"),
            "employment": self.frame(employment, "employment"),
        }
        va = self.frame(va_by_sector, "va_by_sector") if va_by_sector is not None else None
        emp = self.frame(emp_by_sector, "emp_by_sector") if emp_by_sector is not None else None
        windows = {k: (tuple(str(x) for x in v) if isinstance(v, list) else str(v)) for k, v in periods.items()}
        with tool_errors("jobs_shapley"):
            from cgjrkit.methods.jobs_shapley import jobs_shapley as run

            out = run(
                frames["gdp"], frames["population"], frames["working_age"], frames["employment"], windows,
                va_by_sector=va, emp_by_sector=emp, va_sectors=va_sectors, emp_sectors=emp_sectors,
                sector_variant=sector_variant, method=method, annualize=annualize,
                population_scale=population_scale, working_age_scale=working_age_scale,
                employment_scale=employment_scale, sector_emp_scale=sector_emp_scale, iso3=iso3,
            )
        return self.summarize(out, max_rows, tool="jobs_shapley")

    def benchmark_fit(
        self,
        x: Annotated[FrameInput, Field(description="The x variable, usually GDP per capita (one indicator).")],
        y: Annotated[FrameInput, Field(description="The outcome to benchmark (one indicator).")],
        period: Annotated[str | list[str], Field(description="One period, or [start, end] to average over a window.")],
        kind: Literal["linear", "quadratic", "cubic", "lowess"] = "linear",
        log_x: bool = True,
        focus: Annotated[str | None, Field(description="ISO3 of the report country, scored against the curve.")] = None,
        exclude: Annotated[list[str] | None, Field(description="ISO3 codes to leave out of the fit.")] = None,
        log_y: bool = False,
        focus_path: Annotated[list[str] | None, Field(description="Periods for the focus country's trajectory.")] = None,
        ci: Annotated[float | None, Field(gt=0, lt=1, description="Confidence level of the band; null for none.")] = 0.95,
        frac: Annotated[float, Field(gt=0, le=1, description="LOWESS span.")] = 2 / 3,
        grid_points: Annotated[int, Field(ge=10, le=1000)] = 100,
        percentile_method: Literal["weak", "strict", "mean", "rank"] = "weak",
        aggregates: Literal["drop", "keep"] = "drop",
        on_ppp_mix: Literal["raise", "block"] = "raise",
        max_rows: MaxRows = 20,
    ) -> dict[str, Any]:
        """Income-conditional benchmark: fit y on (log) x across countries and score the focus country against the curve.

        meta['fit'] holds coefficients, R2 and n; meta['focus'] the actual and predicted values, residual and percentile.
        """
        xr = self.frame(x, "x")
        yr = self.frame(y, "y")
        if isinstance(period, list):
            if len(period) != 2:
                raise ToolError("period must be one period or [start, end]")
            when: Any = (str(period[0]), str(period[1]))
        else:
            when = str(period)
        with tool_errors("benchmark_fit"):
            from cgjrkit.methods.benchmark import income_conditional_fit as run

            out = run(
                xr, yr, when, kind=kind, log_x=log_x, focus=focus, exclude=exclude, log_y=log_y,
                focus_path=focus_path, ci=ci, frac=frac, grid_points=grid_points,
                percentile_method=percentile_method, aggregates=aggregates, on_ppp_mix=on_ppp_mix,
            )
        return self.summarize(out, max_rows, tool="benchmark_fit")

    def render_chart(
        self,
        result: Annotated[FrameInput, Field(description="The result to chart (a result_id, usually from a method tool).")],
        kind: Annotated[
            Literal["line", "bar", "dual_axis", "scatter", "kde", "slope", "stacked"],
            Field(description="line/bar (vs peers), dual_axis (needs right), scatter (benchmark_fit output), kde, slope, stacked (decompositions)."),
        ],
        output_dir: Annotated[str, Field(description="Directory to write into (created if missing; inside the server's output root if one is set).")],
        stem: Annotated[str, Field(description="File name stem (letters, digits, '.', '_', '-'). Existing files are never overwritten (except this session's own charts).")] = "chart",
        formats: Annotated[list[str] | None, Field(description="Any of vl.json, svg, png, pdf, html. Default vl.json, svg, png.")] = None,
        title: str = "",
        subtitle: str = "",
        note: Annotated[str, Field(description="Extra footer note above the caveat notes and the source line.")] = "",
        right: Annotated[FrameInput | None, Field(description="Right-axis result for kind='dual_axis'.")] = None,
        options: Annotated[
            dict[str, Any] | None,
            Field(description=(
                "Extra chart-builder options, e.g. {'focus': 'KEN', 'peers': ['UGA', 'TZA'], 'index_base': '2010'}. "
                "'band' (line) and 'size' (scatter) take a result_id; 'where' filters rows, e.g. {'sector': 'TOTAL'}. "
                "source_line, note, include_info, right are refused (the source line comes from provenance)."
            )),
        ] = None,
        include_info: Annotated[bool, Field(description="Also footnote info-level caveats.")] = False,
    ) -> dict[str, Any]:
        """Render a house-style chart (Vega-Lite) from a result and write it to files; returns the file paths.

        The inputs (result, right and result-valued options) go through the caveat check first: BLOCK refuses
        the chart and nothing is written. The source line (from provenance), caveat footnotes and the engine's
        footnotes are drawn under the chart, and the plotted data is written as CSV. Existing files are never
        overwritten, except charts this session wrote itself.
        """
        from cgjrkit.charts import render

        res = self.frame(result, "result")
        right_res = self.frame(right, "right") if right is not None else None
        if not _STEM_RE.match(stem):
            raise ToolError(f"stem {stem!r} must match {_STEM_RE.pattern}")
        fmts = list(dict.fromkeys(formats or ("vl.json", "svg", "png")))
        bad = [f for f in fmts if f not in CHART_FORMATS]
        if bad:
            raise ToolError(f"unknown formats {bad}; choose from {list(CHART_FORMATS)}")
        kw = self.chart_options(options)
        if title:
            kw["title"] = title
        if subtitle:
            kw["subtitle"] = subtitle
        out_dir = self.output_dir(output_dir)
        targets = render.chart_paths(out_dir / stem, fmts)
        existing = [p for p in targets.values() if p.exists() or p.is_symlink()]
        foreign = [str(p) for p in existing if p.resolve() not in self._written or p.is_symlink()]
        if foreign:
            raise ToolError(f"refusing to overwrite existing files {foreign}; choose another stem or output_dir "
                            "(render_chart only replaces charts it wrote in this session)")

        from cgjrkit.qa.caveat_check import run_caveat_check

        inputs = [res] + ([right_res] if right_res is not None else []) + [
            r for v in kw.values() for r in (v if isinstance(v, tuple) else (v,)) if isinstance(r, Result)]
        with tool_errors("render_chart caveat check"):
            report = run_caveat_check(inputs)
        if report.blocked:
            blocking = [c for c in report.caveats if c.severity == "block"]
            raise ToolError("render_chart refused: the caveat check BLOCKs these inputs: "
                            + "; ".join(f"[{c.code}] {c.message}" for c in blocking)
                            + ". Nothing was written.")
        notes = [note] + [f if f.startswith("Note") else f"Note: {f}" for f in report.footnotes]
        with tool_errors(f"render_chart({kind})"):
            chart = render.chart_from_result(res, kind, note=notes, include_info=include_info, right=right_res, **kw)
            paths = render.save_chart(chart, out_dir / stem, formats=fmts, overwrite=bool(existing))
            footer = render.footer_lines(chart)
        self._written.update(p.resolve() for p in paths.values())
        rule_caveats = [c for c in report.caveats if c not in report.inherited]
        return jsonable({
            "result_id": self.store.put(res),
            "kind": kind,
            "files": {fmt: str(path) for fmt, path in paths.items()},
            "footer": footer,
            "status": report.status,
            "caveats": [c.to_dict() for c in res.caveats],
            "caveat_check": {
                "status": report.status,
                "caveats": [{"code": c.code, "severity": c.severity, "message": c.message} for c in rule_caveats],
                "footnotes": list(report.footnotes),
            },
        })

    def run_caveat_check(
        self,
        results: Annotated[list[FrameInput], Field(description="The results that make up one output (result_ids or inline records).")],
        context: Annotated[
            dict[str, Any] | None,
            Field(description=(
                "Optional context: mode ('replication'), redistribution (bool), ppp_base, min_n, peer_sets, "
                "periods, covid_years, results_at, inherit_caveats."
            )),
        ] = None,
        rules: Annotated[list[str] | None, Field(description="Run only these rules (default all).")] = None,
        skip: Annotated[list[str] | None, Field(description="Rules to skip.")] = None,
    ) -> dict[str, Any]:
        """Run the caveat rules over one output's inputs: PASS, WARN (footnote it) or BLOCK (do not use as is).

        Rules: ppp_mixing, ilo_modelled, forecast_years, vintage_drift, missing_provenance, series_break,
        covid_endpoints, peers_not_frozen, small_n, licence_restricted.
        """
        if not results:
            raise ToolError("results must name at least one result")
        resolved = [self.frame(r, f"results[{i}]") for i, r in enumerate(results)]
        with tool_errors("run_caveat_check"):
            from cgjrkit.qa.caveat_check import run_caveat_check as run

            report = run(resolved, context, rules=rules, skip=skip or ())
        return jsonable({"results": [self.store.put(r) for r in resolved], **report.to_dict()}, max_items=200)


#: tool name -> (annotations, title); the tool set is fixed
TOOLS: dict[str, tuple[ToolAnnotations, str]] = {
    "list_connectors": (READ_ONLY, "List data connectors"),
    "get_series": (READ_ONLY_NETWORK, "Fetch data from a public provider"),
    "peer_aggregate": (READ_ONLY, "Peer-group statistics"),
    "growth_accounting": (READ_ONLY, "Growth accounting"),
    "shift_share": (READ_ONLY, "Shift-share productivity decomposition"),
    "jobs_shapley": (READ_ONLY, "Jobs Shapley decomposition"),
    "benchmark_fit": (READ_ONLY, "Income-conditional benchmark fit"),
    "render_chart": (WRITES_FILES, "Render a chart to files"),
    "run_caveat_check": (READ_ONLY, "Caveat check (PASS/WARN/BLOCK)"),
}


def create_server(store: ResultStore | None = None, output_root: str | os.PathLike[str] | None = None) -> MCPServer:
    """Build the MCP server with the fixed cgjrkit tool set (one result store per server).

    ``output_root`` (default ``$CGJRKIT_OUTPUT_DIR``) is the only directory render_chart may write
    into; without one, render_chart may write anywhere the process can (it still never overwrites).
    """
    tools = CgjrkitTools(store, output_root=output_root)
    server = MCPServer(
        name=SERVER_NAME,
        title="cgjrkit (preview)",
        description="Deterministic tools for Country Growth and Jobs Report charts from public APIs.",
        instructions=INSTRUCTIONS,
        version=__version__,
    )
    for name, (hints, title) in TOOLS.items():
        server.add_tool(getattr(tools, name), name=name, title=title, annotations=hints)
    server.cgjrkit_tools = tools  # type: ignore[attr-defined]  # handle for tests and embedding
    return server


def main(argv: Sequence[str] | None = None) -> None:
    """Entry point for ``cgjrkit-mcp``: serve the tools over stdio."""
    parser = argparse.ArgumentParser(prog="cgjrkit-mcp", description=__doc__.splitlines()[0])
    parser.add_argument("--cache", help="cache root (sets CGJRKIT_CACHE)")
    parser.add_argument("--offline", action="store_true", help="serve only from the cache (sets CGJRKIT_OFFLINE=1)")
    parser.add_argument("--snapshot", help="replay and pin payloads under this snapshot name (sets CGJRKIT_SNAPSHOT)")
    parser.add_argument("--output-root", help=f"render_chart writes only inside this directory (default ${OUTPUT_ROOT_ENV})")
    args = parser.parse_args(argv)
    if args.cache:
        os.environ["CGJRKIT_CACHE"] = args.cache
    if args.offline:
        os.environ["CGJRKIT_OFFLINE"] = "1"
    if args.snapshot:
        os.environ["CGJRKIT_SNAPSHOT"] = args.snapshot
    create_server(output_root=args.output_root).run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
