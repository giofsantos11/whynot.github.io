# Conventions for cgjrkit modules

Read this before writing a connector, method, chart or check.

## Data shape
- Every connector returns a `Result` (`cgjrkit.core.provenance`) whose `.data` is the canonical long frame from `cgjrkit.core.schema` (`make_frame`, `validate_frame`).
- `period` is a string: `"2024"`, `"2024Q1"`, `"2024-03"`; fiscal years as `"FY2024/25"` only when the provider reports them that way.
- `iso3` is ISO 3166-1 alpha-3 for countries. Aggregates keep the provider code (World Bank: `LMC`, `UMC`, `SSA`/`SSF`, `WLD`, ...); record aggregate codes in `Result.meta["aggregates"]`.
- `obs_status` uses only the values in `schema.OBS_STATUS` (forecast, estimate, modelled, provisional, break, imputed, or empty).
- PPP-based units must state the base year in `unit` (e.g. `"constant 2021 international $ (PPP 2021)"`); `schema.ppp_base()` parses it.

## Provenance
- Use `Connector.provenance(retrieval, dataset=..., vintage=...)`. Fill `vintage` whenever the provider exposes a release/edition (WEO April 2026, PWT 10.01, WPP 2024, ILOSTAT edition date, WDI archive version).
- Methods return a new `Result` that carries forward input provenance and appends a `Provenance(source="cgjrkit", tool="<method name>", params={...})`.

## HTTP
- Never call httpx directly; use `cgjrkit.core.http.fetch` (cache, snapshots, retries, offline mode).
- No API keys in code. Read `CGJRKIT_<PROVIDER>_KEY` from the environment; raise a clear error if a required key is missing.
- Declare every host a connector calls in its `domains` tuple.

## Separation of pure and impure code
- `build_request(...)` and `parse(payload, ...)` are pure functions; `fetch(...)` wires them through `http.fetch`.
- Unit tests call `parse` with fixtures. Fixtures live in `tests/fixtures/` and MUST:
  - follow the provider's documented response format (cite the doc/source you used in `docs/apis/<provider>.md`);
  - contain obviously synthetic values (round numbers, or clearly labelled) and have `_synthetic` in the file name;
  - never be presented as real data.
- Live tests go in `tests/live/test_<provider>_live.py`, marked `@pytest.mark.live`; they call the real API and, where possible, check published values from the reports (replication targets in `../inventory/`).

## Caveats
- Attach `Caveat(code, severity, message, evidence)` to results when the data warrants it (forecast years present, ILO modelled values, PPP base, known series breaks, licence restrictions). Severity `block` only for outputs that must not be used as is.

## Scope
- Public APIs and files only. No microdata tools.
- Do not edit `core/` or `connectors/base.py`; if you need a change there, describe it in your final report.
- Do not commit. Do not edit other agents' modules.
