**CGJR plan review: corrections, cuts and additions, in priority order**

Lens A is a senior data engineer. Lens B is a World Bank lead economist. V means verified, I means inferred, and "check" means the fact still needs confirming.

**P0: fix before Sprint 1**

1. **(A+B) Bank material sent to the AI.** The current principles protect microdata, but other Bank material still reaches the founders' AI account. A Pre-Review Check sends a Bank draft, which is probably Official Use Only, and team tables can go the same way.
   - Ask about this in discovery calls and settle it in each contract.
   - Offer a deterministic-only mode. `check_draft`'s matcher, `caveat_check` and the chart tools all run without AI.
   - Expect a TTL's manager to ask about this first (I).

2. **(B) Where the five PDFs came from.**
   - Confirm each report can be downloaded publicly from D&R or the OKR before it goes into golden tests, demos or the Pre-Review pitch. Anything obtained during an STC cannot be used.
   - Do not market report errors in public, such as Kenya Fig 2, where the 2020 stacks net to about +1.2 pp against a printed 0.5. Show defects only to the team that owns the report.
   - Fix the defect count. The roadmap says 8 Kenya title/axis/period defects, but the inventory lists 5 (Figs 6, 16, 19, 23 and 28).

3. **(A) One allowlist and one tool catalog.** The four components give four different host lists. For example, the architecture list leaves out sdmx.ilo.org, the PIP host is unresolved, and dataverse.nl is only inferred. Tool phases also conflict: Comtrade is MVP in one place and Next in another, and so is PIP.
   - Generate the allowlist from the connector registry.
   - Add a CI test that fails when code calls a host not on the list.
   - Use the same file for the Bank IT review later.

4. **(A+B) Test the vintage assumption on day 1.** The rule "replicate at the report's vintage" depends on two things: WDI source 57 holding each report's version, and the WEO vintage dataflows.
   - Make the first task pulling Kenya Fig 1 from source 57 at the report's vintage.
   - If that version is missing, let R0–R3 accept "matches the current vintage, and the difference is a documented revision" instead of counting it as a failure.

5. **(A) Send the licence emails in week 1:** copyright@imf.org (systematic download), comtrade@un.org, enterprisesurveys@worldbank.org and the Harvard Growth Lab. Until the answers arrive:
   - IMF data only as small pinned extracts for each engagement.
   - BACI instead of Comtrade.
   - Compute ECI ourselves with `ecomplexity`.
   - Keep FRED and Semantic Scholar out.
   - Make the "licensed data" caveat rule read the third-party flag in WDI metadata.

   The Bank's dataset terms allow using the APIs to reach the Datasets (V). The commercial ban covers non-dataset Materials, which are the D&R and OKR documents behind the ledger. Until legal signs off, download CC BY items through normal links, not the API in bulk.

**P1: cuts (the plan is about twice what two people can ship in 12 weeks while one of them also sells)**

6. **Defer to the week-12 product gate:**
   - Sigstore signing, the SBOM and the signed wheelhouse;
   - the split into public and private repos;
   - the MCP server, even internally (Claude Code can call the library through scripts);
   - cross-OS determinism tests, hypothesis property tests and the nightly drift CI;
   - the full workbench (keep a static HTML index);
   - Docling and GROBID (use pdfplumber, and handle references by hand);
   - `source_resolver` as a "knowledge base" (start with a hand-curated YAML entry for each replicated figure);
   - automated vector-path digitizing (use printed values and numbers quoted in the text first; the Kenya inventory already holds the targets).

7. **Shrink S1.** It lists 10 tools for 2 weeks. Keep: provenance schema, `vintage_pin` (which is the cache), `wb_data`, `peer_registry` YAML, basic `harmonize`, `chart_series` and the FigureSpec runner. Move `caveat_check` v0 to S2.

8. **Change the first-15 list.**
   - Replace #15, the Morocco population pyramid (L08). It is a distinct chart form but has little analytic value. Use the G16 standardized employment table instead.
   - Replace #6 (North Macedonia, which depends on TED) with a growth-accounting figure from Indonesia, Morocco, North Macedonia or the Philippines that runs on PWT directly.
   - Weight the list toward the four full CGJRs. Kenya is a background report (review B2).

9. **Lower the week-12 target from 70 to 40–50 of the 148 items.** Measure it on an audited 10% sample and on the 518 unique figures (review B3 and B7). Each figure needs a spec, targets and a tolerance review, so budget 2–4 hours per figure once the tooling exists (I).

**P2: additions**

10. **(B) Official forecasts.** Bank reports use MPO/MFMod and GEP projections, not WEO (I, check with TTLs).
    - Use WEO forecasts only as cross-checks.
    - Take forecast years from the team's MPO numbers through `model_output_ingest`.
    - Add a caveat rule for a non-Bank forecast in a Bank chart.

11. **(B) Move the standardized tables (G14/G16) from stretch into S3–S4.** They close every full CGJR, they are tedious to produce, and they are an obvious thing to sell.

12. **(B) Peer sensitivity.**
    - Rerun every benchmark chart with one alternative peer set and include it as an appendix.
    - Have the registry store the selection criteria, who approved them and the date frozen.
    - Start from the team's existing CEM or SCD peers rather than inventing new ones.

13. **(B) A minimal client-mode LFS tabulator in S5.** It covers LFPR, sector shares, wage employment and informality under a declared definition (13th vs 19th ICLS), and exports as do-files. Jobs is half the report title, and GLD has no Kenya. If all of `lfs_kit` waits, the jobs chapter rests on ILO modelled series that carry a warning.

14. **(A) Bring the Stata export forward to S3, not S5.** The week 9–10 pilot promises do-files, and most Bank economists rerun work in Stata (I). Budget a Stata licence for testing. By default the do-files run from bundled pinned CSVs; refreshing from the APIs is optional and uses the client's own keys.

15. **(A) A per-host rate limiter in the connector base class,** with cautious defaults where the limit is unknown.
    - OECD publishes two figures; plan for the lower one, 20 downloads an hour. That means each peer panel has to be one bulk query.
    - The free Comtrade key has a daily call cap.

16. **(A) One tolerance table.** The architecture says ±2% of axis range for digitized values, while the roadmap says ±2% for levels. Keep one table in the repo and have both the golden tests and the R0–R3 grade use it.

17. **(B) A registry for each country** covering fiscal year (Kenya runs July to June), national-accounts rebasing dates (check each NSO) and PPP base. Both `harmonize` and `caveat_check` read it.

18. **(A) WPP.** I found no 2026 edition (search on 11 Oct 2026; check). Pin WPP2024 and use the bulk CSV, so S3 does not depend on getting the emailed token.

19. **(B) Candidate messages.** Label anything AI-drafted as such. The TTL owns the messages, and every number in them must bind to a computed result before it goes out.

Sources:
- [WB dataset terms](https://www.worldbank.org/en/about/legal/terms-of-use-for-datasets)
- [WB T&C, datasets](https://www.worldbank.org/ext/en/legal/terms-conditions/datasets)
- [WB API terms note](https://datahelpdesk.worldbank.org/knowledgebase/articles/889386)
- [WPP 2024](https://www.un.org/development/desa/pd/world-population-prospects-2024)

Evidence files:
- /home/user/whynot.github.io/inventory/README.md
- /home/user/whynot.github.io/inventory/cross-report-review.md
- /home/user/whynot.github.io/inventory/cross-report-inventory.md
- /home/user/whynot.github.io/inventory/kenya-replication-inventory.md