**Verdict.** The arithmetic holds. These totals all reconcile:
- report, module and data-level tables (536 items);
- the 69 archetype rows (y/p/n/u = 148/176/190/22);
- the MVP subset (203 items, 113/72/14/4);
- source tags (956).

The problems are in labels, routing and framing.

**A. Internal inconsistencies**
1. **P01 vs `pip_poverty`.** P01 is counted among the 30 MVP archetypes, but `pip_poverty` is in the Next tier. Either move PIP into the MVP (its API is open) or drop P01 from the MVP (−5 items, −2 yes, giving 111/148).
2. **"Next adds 26 yes" is too low.** It leaves out the archetypes `lfs_kit` itself unlocks: L03 1, L04 1, L05 2, L06 1, L13 1. B11 adds 1 more through `es_microdata`. Next reaches about 145/148, not 139.
3. **B07 is misrouted.** Its 3 yes items use the public Entrepreneurship Database (open CSV), but it is assigned to `firm_kit`, which runs on client data.
4. **Section 2 contradicts the module table.** Section 2 shows "—" for fiscal in I and N, and for digital in N. The module table has I fiscal 6, N fiscal 3, N digital 3, and F03 lists N. Indonesia's 6 fiscal items also map to none of F01–F03, so they need to be located.
5. **B-READY report coverage.** Indicator #105 says MN; Section 5 says MP.
6. **PMR count.** Section 5 implies about 22 PMR items: 8 tagged (in Morocco's source block) plus "~14 Indonesia items tagged Other". R03 has only 14 items across I and M combined, so one of these numbers is wrong.
7. **National accounts.** The generic "National accounts (NSO)" key (25 tags) appears among Indonesia's sources, so coverage should read IKMNP, not KMNP.
8. **G01 and G08 look under-assigned.**
   - G01 (real GDP growth) has 4 items and no Philippines, yet indicator #1 is IKMNP.
   - G08 has 3 items, but `by_method` shows at least 4 sector-contribution decompositions plus 2 regional ones.
   - Check whether these items were absorbed into G03, G14, S05 or SP1.
9. **"Kenya is 92.5% public" conflates yes with partly.** Fully public is 72.5%.
10. **ICA 2.0 is tagged only in N and P (30 tags).** Calling the I and M firm chapters "ICA-style" is an inference: M cites Dalvit, the Begazo methodology, DGI and CNSS. Do not hard-code an ICA schema into `firm_kit`.

**B. Overclaims**
1. **The "standard library of 69" is not all standard.**
   - Only 37 archetypes appear in 3 or more reports (334 items, 62%).
   - 8 appear in one report only (S06, SP3, G13, SP2, B11, B12, S03, C01; 49 items), and 24 appear in two (153 items).
   - Several high counts likely come mostly from one report: X09, R03 and F07 (mostly I), the IQR band (12 of 14 in P), and possibly G12.
   - Add a "largest single-report share" column and label any archetype with more than 70% from one report "country-specific".
2. **The common architecture rests on 4 reports.** Kenya is a background report, not a CGJR, so write "4 of 4 full CGJRs" and do not generalize to the whole series.
3. **Replicability is the extractor's judgment and has not been audited.**
   - Kenya has only 3 "no" items, one of them CCDR. So items sourced to a *forthcoming* Jobs Diagnostic, IMF staff reports, the 2023 World Bank CEM, trade and poverty studies, and KNBS PDFs must be rated yes or partly.
   - Indonesia's roughly 95 "Other" tags hide public OECD PMR and STRI data, which probably understates I.
   - Audit a stratified 10% sample before quoting 27.6% / 60.4%.
   - Define "replicable" as "matches printed values within a set tolerance, using public data at the report's vintage."
4. **MVP coverage framing.** The "central 27.8%" (yes plus half of partly) has no empirical basis. Also, "yes" does not mean automatable: `national_table_ingest` is manual PDF work. Lead with the lower bound.
5. **LTGM is listed as a model wall**, but Section 5 calls it public. G11's 5 "no" items need a stated reason (internal assumptions) or a new tag.
6. **"8 templates cover 84%" counts chart forms, not reproducible specs.** Coefficient and IQR charts need estimates computed upstream. Say "render", not "replicate".
7. **The roughly 18 duplicates are still in the archetype and replicability counts.** The 11 Philippines duplicates inflate L01 and the IQR template. Recompute on the 518 unique figures.
8. **Two first targets are not cleanly "yes".** Target 2 includes a 2025 forecast, and target 6 depends on TED with a PWT fallback.

**C. Missing archetypes visible in the items**
- **Financial inclusion:** there are 3 Findex tags but no archetype; F05 covers only credit depth and bank structure.
- **Gravity, export potential and AVEs as a method archetype:** at least 6 items across I, N and P (AVE estimation, residuals, simulation, Ganz et al. 2025). They are currently split between X05 and X09.
- **Labor flows and transition matrices beyond youth:** panel transitions, transition probabilities, and size or productivity transition matrices.
- **Prices and monetary conditions:** monthly CPI, the yield curve and indexed price comparisons are scattered across X01, F02 and SP3.
- **Sector-contribution growth decomposition:** after the recount in A8 it likely deserves standard (3+ report) status.

**D. Tool gaps**
1. **Add `source_resolver` to the MVP.** 359 of 956 tags (37.6%) are "WB staff calculations" or a bare "Other". Section 1 calls normalization an MVP task, but no MVP tool does it.
2. **Add `figure_digitizer` / figure-data ingest.** `replicate_figure` diffs against "digitized values" that no tool produces.
3. **Add `vintage_pin`** for WDI archives, the PWT version and the PPP base (2017 vs 2021). The diff tolerance cannot be defined without it.
4. **Add `peer_registry`.** Each report's structural, aspirational and named peer lists must be stored as data; `peer_set` cannot infer them.
5. **Move cheap public wins into the MVP:**
   - Enterprise Surveys aggregates (B13, +6 yes);
   - Entrepreneurship Database connector (B07, +3);
   - regional accounts through `national_table_ingest` plus `structural_change` (SP1, +8);
   - PIP (P01).

   The MVP then covers about 130 of 148 fully public items (88%).
6. **Move `model_output_ingest` into the MVP.** From supplied results it renders about 50 items (G12, G11, G13 and the projection columns of G14), the closing block of every full CGJR.
7. **Add a `dedupe` flag** for repeated figures within a report.
8. **Check `imf_data`.** Confirm that `imfp` works with the IMF data portal as it was rebuilt in 2025; if not, call SDMX directly.