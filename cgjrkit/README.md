# cgjrkit

Deterministic tools for replicating the standard exhibits in World Bank Country Growth and Jobs Reports (CGJRs) from **public APIs**. Microdata tools are out of scope for this package.

## Design

- **The AI chooses, the tools compute.** Each tool is a deterministic Python function with typed inputs and outputs. The AI never produces numbers itself.
- **Provenance on every number.** Every result records the source, dataset, endpoint, parameters, retrieval time, payload hash, vintage and the tool that produced it.
- **Vintage pinning.** Raw payloads are stored by content hash, and snapshots pin a replication to the exact payloads it used (`cgjrkit.core.cache`).
- **One data shape.** All connectors return the same long-format frame (`cgjrkit.core.schema`).
- **Caveats travel with results.** Some caveats block output: for example, mixing 2017- and 2021-PPP data, or splicing ILO modelled estimates with survey data.

## Layout

```
src/cgjrkit/
  core/          schema, provenance, caveats, cache (vintage pinning), http
  connectors/    one module per public data provider
  methods/       transforms, peers, benchmarking, growth accounting, structural change, decompositions
  charts/        house style and chart builders (Vega-Lite via Altair; PNG/SVG via vl-convert)
  qa/            caveat checks
  replication/   YAML figure specs and the replication runner; targets/ holds report figures
  workbench/     HTML workbench builder
  mcp_server.py  MCP server exposing the tools
tests/           offline unit tests (fixtures follow each provider's documented format)
tests/live/      tests that call real APIs (CGJRKIT_LIVE=1 pytest -m live)
docs/            API notes and the domain allowlist
```

## Development

```
uv venv .venv && uv pip install -p .venv -e ".[dev]"
.venv/bin/pytest            # offline unit tests
CGJRKIT_LIVE=1 .venv/bin/pytest -m live   # live API tests (needs network access to the API domains)
```

## Environment variables

| Variable | Effect |
|---|---|
| `CGJRKIT_CACHE` | Cache root (default `~/.cache/cgjrkit`) |
| `CGJRKIT_OFFLINE=1` | Serve from cache only; never call the network |
| `CGJRKIT_SNAPSHOT=<name>` | Use and record pinned payloads for a replication |
| `CGJRKIT_<PROVIDER>_KEY` | API keys where a provider requires one (for example Comtrade) |
