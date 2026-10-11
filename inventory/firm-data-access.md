# Are the firm census data publicly available?

## Short answer
No. In none of the four countries can you download the census, survey or registry panels behind the TFP, Olley-Pakes, concentration and job-flow charts. Kenya's report has no firm-microdata items. Access ranges from an on-site enclave (Philippines) to a paid purchase (Indonesia) to confidential administrative data that agency staff analysed jointly with the World Bank (Morocco, North Macedonia). The free option is World Bank Enterprise Survey (WBES) microdata, which covers 24 of the 146 items, plus published aggregate tables.

## By country

| Country (items) | Dataset in report | Holder | Access | How WB teams got it | Public substitute |
|---|---|---|---|---|---|
| Philippines (30) | ASPBI/CPBI panel, List of Establishments, customs transactions (~19 items) | PSA | **Research enclave.** You write to the National Statistician and sign an agreement. Work is on site and outputs are checked before release. Fee shown as "None" ([catalog](https://psada.psa.gov.ph/catalog/ASPBI/about), [help](https://psada.psa.gov.ph/help)) | Enclave. The customs source note says so; the panel was likely used the same way (INFERRED) | ASPBI tables by industry and region ([PSA](https://psa.gov.ph/statistics/survey/business-and-industry/index)) |
| | FAT 2024 survey (6) | World Bank | Not released (INFERRED) | The World Bank's own survey | None |
| Indonesia (42) | IBS manufacturing survey (15), SE2016 business listing (2) | BPS | **Purchase** through Silastik with a signed data-use agreement ([standard](https://ppid.bps.go.id/upload/doc/Standar_Pelayanan_Publik_Penjualan_Data_Mikro_dan_Peta_Digital_Online_1680505694.pdf)). SE2016 listing costs Rp 143.9m, about US$8,800 ([Silastik](https://silastik.bps.go.id/v3/index.php/mikrodata/cetak_info/Zk5BSHIzRnJPRThEcDgrYzhjV2ovdz09)). Unclear whether a foreign private firm can buy | BPS free channel for international organizations, or a purchase (INFERRED) | Statistik Industri Manufaktur 2023 ([BPS](https://www.bps.go.id/en/publication/2025/07/31/19e60551bde44c9ff6aefcba/statistik-industri-manufaktur-indonesia-2023.html)) |
| | TICMI listed firms (8), BOS (5), FAT (5) | Stock-exchange (IDX) subsidiary; World Bank | TICMI is paid; BOS is a dashboard only; FAT is not released | — | IDX company filings, partial (INFERRED) |
| Morocco (27) | OMTPME firm panel (18), DGI tax records (2). CNSS records feed 8 more items tagged labor/mixed | OMTPME (under Bank Al-Maghrib), DGI, CNSS | **Confidential.** Tax secrecy under tax code Art. 246 ([fiscamaroc](https://www.fiscamaroc.com/dispositions-communes-208/article-246-secret-professionnel-377.htm)). No researcher access route found | Source notes say "World Bank and OMTPME/DGI staff calculations" (VERIFIED). The data probably never left the agency (INFERRED) | OMTPME annual report tables by size class ([Médias24](https://medias24.com/2026/01/07/omtpme-publie-la-sixieme-edition-de-son-rapport-annuel-1608061/)) |
| North Macedonia (47) | Central Registry annual accounts (33 items, 24 of them via the ICA 2.0 toolkit), linked to customs, FITD and Ministry of Finance subsidy files | Central Registry, Customs, FITD, Ministry of Finance | Registry sells **single-firm** listings; no bulk route found ([CRM](https://www.crm.com.mk/mk/uslugi/dobijam-potvrda-ili-informatsija/finansiski-informatsii/listing-od-obrabotena-godishna-smetka?idV=11247)). Linked files **confidential**. Statistics office microdata only in its "safe room" ([SSO](https://www.stat.gov.mk/meta/esms/ESMS_APD_Q_EN.pdf)) | An extract through the government counterpart (INFERRED). Source note says FITD data were "provided by the FITD" | MakStat business statistics by size class; Registry Statistical Bulletin |

## Public alternatives

| Source | Can replicate | Cannot |
|---|---|---|
| WBES microdata: free after registration, no cells under 5 firms ([protocol](https://www.enterprisesurveys.org/en/data-access-protocol)) | 24 items: obstacles, size and productivity distributions, Lorenz curve, regulatory delays (North Macedonia 10, Indonesia 8, Morocco 5, Philippines 1). Also a one-year TFP file | Panels, entry/exit, job flows, concentration, firms with fewer than 5 workers |
| National aggregate tables (listed above) | Philippines 3.6, 3.10, 3.11; Morocco F2-2; Indonesia 2.3–2.4 roughly | Anything within a sector: dispersion, Olley-Pakes, markups |
| UNIDO INDSTAT, open ([UNIDO](https://stat.unido.org/content/general/databases-for-free)) | Manufacturing productivity by sector, shift-share | Anything at firm level |
| Published World Bank papers ([OKR](https://openknowledge.worldbank.org/entities/publication/67e1af29-387c-4c23-9fb9-9b8f37fd389f/full)) | Redraw published numbers, e.g. Morocco 2-3 to 2-5 | Re-estimate them |
| Orbis (licensed, no published price) | Concentration among large firms | SME TFP that represents Indonesia, the Philippines or Morocco (INFERRED) |
| PSE EDGE and IDX filings | Panels of listed firms | Unlisted firms |
| CompNet, OECD MultiProd | Peer benchmarks | None of the four countries is covered |

## What this means for the business

- **Client-provided data is the realistic model for the other ~120 items.** Sell tested code, not data. The code would cover TFPR quantiles, Olley-Pakes and Melitz-Polanec decompositions, concentration and Davis-Haltiwanger job flows. A World Bank team or agency then runs it under its own agreement.
- **Work inside NSO or World Bank environments.** The PSA enclave is a good pilot: no fee is shown. It is on site, though, and personal devices are held during sessions ([agreement](https://rssocar.psa.gov.ph/system/files/documents/Data-Enclave-Access-Agreement.pdf)). Whether a private firm is eligible is unconfirmed. Assume enclaves allow no AI tools (INFERRED). Write the code with AI outside, test it on WBES and synthetic data, then run plain Stata/Python inside.
- **Plan for aggregate-only outputs.** Build disclosure control into the code: minimum cell sizes, and suppressing tails that could identify a firm. Outputs then pass PSA-style review and the WBES 5-firm rule.
- **Which charts to do first:** the 24 WBES charts, then the aggregate-table charts, then the redrawn Morocco figures. Use them to show off the method library.

## What to ask Diego/TTLs

1. What agreement covered each dataset: PSA enclave or a memorandum of understanding, BPS free channel or purchase, OMTPME/DGI/MIEPEEC protocols, the Central Registry extract? Can contractors be added to it?
2. Can a contracted firm run code itself, or must agency staff run it? Are AI tools allowed?
3. Can the code for the ICA 2.0 Toolkit, the Firms and Climate Tool and the World Business Dynamics Database be shared with consultants?
4. When will the FAT data for the Philippines, Indonesia and Morocco be released?
5. Is there a secure World Bank environment for replication work, and what disclosure rules apply there?

*Note:* PSA, BPS and worldbank.org pages would not open directly. Facts marked VERIFIED come from search-result snippets of the cited pages, not the pages themselves.

---

# Country research notes

## philippines

**Short answer: no.** The Philippine firm census and survey microdata (CPBI/ASPBI) are not public. You can only use them inside a PSA Data Enclave Center, on site and under supervision, and every output is checked before it leaves. PSA does publish aggregate tables, which cover a few of the report's charts.

**What the report uses (philippines-2025)**
30 of the report's 103 items are tagged "firm microdata". By dataset:
- **PSA ASPBI and/or CPBI: 17 items.** ASPBI alone: Fig 6, 11, 2.3, 2.4, 2.6, 2.7a, 3.5, 3.6, 3.7, 3.10, 3.11. CPBI 2018 alone: Fig 2.1, 2.2a, 2.2b. The ASPBI/CPBI panel for 2012–2021 (2012–2018 for 2.10/2.11): Fig 2.10, 2.11, 5.2. These are the TFP, reallocation, firm-age, exporter/FDI, regional and flood-exposure charts.
- **PSA List of Establishments 2023:** Fig 2.7a, together with ASPBI. Fig 2.7b is a World Bank index built on the same data.
- **PSA trade transaction (customs) database:** Fig 2.5a. The source note itself says it is "accessible at the Data Enclave Center".
- **Firm Adoption of Technology (FAT) Survey 2024, World Bank:** Fig 2.9, 4.1b, 4.2, 4.3, 4.4, 4.5.
- **One item each:**
  - Philippine Stock Exchange listed-firm data (2.5b)
  - World Bank Enterprise Survey, WBES (4.7a)
  - World Bank 2024f, a published paper (4.1a)
  - World Bank dashboards (Table 1)
  - Fig 5.3, whose source line (BLGF/OECD) looks like a copy error

**Access, dataset by dataset**

1. **ASPBI and CPBI: enclave only (VERIFIED).**
   - PSA's microdata catalog says the ASPBI files "are classified as files that are accessible only in the PSA Data Enclave" (https://psada.psa.gov.ph/catalog/ASPBI/about).
   - How to apply: send a letter to the National Statistician naming the survey, the years and the purpose, then sign a Data Enclave Access Agreement. The data can only be handled on PSA premises, under a PSA officer, during office hours. Personal devices are held for the session. Outputs get "a full disclosure review" before release. Sources: https://psada.psa.gov.ph/help, https://rssocar.psa.gov.ph/system/files/documents/Data-Enclave-Access-Agreement.pdf
   - Enclave centers opened at the PSA Central Office in December 2022 and in 14 regional offices in July 2023. The datasets include CPBI/ASPBI and the List of Establishments (https://psa.gov.ph/content/psa-launches-its-data-enclave-centers-its-14-regional-offices).
   - Researchers from UP Baguio used the 2010 ASPBI there in 2024 (https://rssocar.psa.gov.ph/system/files/attachment-dir/CAR-SPR-2024-16_Data-Enclave-Center-UPB.pdf).
   - **Cost:** the PSA Citizen's Charter shows the fee as "None" in the step I could see (https://psa.gov.ph/content/addressing-and-processing-data-request-data-enclave-psas-censuses-or-surveys-through). I could not see the full fee table.
   - **Legal basis:** Section 26 of RA 10625 allows only aggregates in which no business can be identified.
   - **No public use file found.** A 2020 FOI request for CPBI/ASPBI microdata was refused (https://foi.gov.ph/requests/cpbi-aspbi-qsbpi-2000-2006-2012-microdata-upd-481636359403).
   - Not checked: whether a foreign or private firm is eligible, and how long approval takes.

2. **List of Establishments: confidential, enclave only (VERIFIED for the 2004 round only).** Only the 2004 round is in PSA's catalog, and it is enclave only (https://psada.psa.gov.ph/catalog/ULE). I assume the 2023 list is treated the same way (INFERRED).

3. **Trade transaction data:** firm-level files are enclave only according to the report's own source note (VERIFIED). PSA's catalog lists transaction files for Foreign Trade Statistics 2014–2022 (https://psada.psa.gov.ph/catalog/248), but I could not confirm their access level. They may be public files with firm identifiers removed (unconfirmed).

4. **FAT 2024 Philippines: likely not public (INFERRED).** The World Bank Microdata Library's FAT collection lists a 15-country file, and search results did not show the Philippines in it (https://microdata.worldbank.org/catalog/fat). I could not open the catalog directly to confirm.

5. **WBES Philippines 2023: free after registration (VERIFIED).** It is listed as PHL_2023_WBES_v01_M (https://microdata.worldbank.org/catalog/6464). Access is through the Data Access Protocol (https://www.enterprisesurveys.org/en/data-access-protocol).

6. **PSE listed firms: public.** Annual reports (SEC 17-A) can be downloaded free from PSE EDGE (https://edge.pse.com.ph/page/aboutPseEdge.do). Building the panel from them takes real work.

**Published tables that can stand in (partly VERIFIED)**
PSA publishes ASPBI results by industry and region: number of establishments, employment and value added. Final results are out for 2021, and 2022 regional results were released in November 2024 (https://psa.gov.ph/statistics/survey/business-and-industry/index).
- **Roughly replicable from these tables:** Fig 6/3.6 (growth in the number of firms), 3.10 (formal employment growth) and 3.11 (sector contributions).
- **Need the microdata:** TFP, productivity-quantile shares, firm-age profiles, exporter/FDI premia, concentration, spillovers and flood effects. That is about 13 items.

**How World Bank teams get these data (INFERRED)**
- The report says outright that the trade data were used at the Data Enclave Center.
- I found no public World Bank–PSA data-sharing agreement. The ASPBI/CPBI panel was probably used in the enclave or under a project-specific arrangement with PSA.
- The CPBI 2018 TFP numbers line up with work by the World Bank's East Asia and Pacific Chief Economist office, which also covered Vietnam and Indonesia.

**What this means for you:** you can do the same TFP and reallocation analysis yourself by applying to a PSA enclave, with no fee shown. But it is on-site, slow, and outputs must be approved. Plan to replicate around 5 charts from public tables and the WBES and PSE data, and treat about 23 as enclave-dependent. Of those 23, 6 come from the FAT survey, which only the World Bank holds.

PSA's sites would not load from here, so the PSA details above come from search-result snippets of those URLs rather than the pages themselves.

---

## indonesia

**Short answer for Indonesia: the core firm data can be bought, but it is not free to download.** The firm-productivity chapter runs mostly on BPS's annual Large and Medium Manufacturing Survey (IBS). BPS sells that data to approved users under a signed data-use agreement. I found no evidence that a foreign private firm can buy it directly, so that is the main open question.

**What the report cites (indonesia-2026: 42 items tagged "firm microdata", plus 4 "mixed" billionaire charts)**

| Dataset named in the source notes | Items | Access |
|---|---|---|
| "BPS Manufacturing Firm Survey" (IBS); Figs 2.3 and 2.4 say "WBPS", probably a typo | 15 (2.3–2.6, 2.8–2.15, 3.5–3.7) | Sold by BPS under a data-use agreement |
| "Indonesian Census 2006, 2016", most likely the Economic Census SE2006/SE2016 | 2 (2.22, 2.23) | Sold by BPS; very expensive |
| World Bank Enterprise Surveys | 8 (2.7, 3.1–3.4, 3.8, 3.22, 3.23) | Free with registration |
| "Indonesia WB FAT Survey" (firm technology adoption) | 5 (2.25–2.29) | Not confirmed public |
| TICMI, the stock-exchange data library (listed firms, plus billionaire charts 3.14–3.17) | 8 | Paid data service |
| World Bank Businesses of the State (BOS) database | 5 (4.1–4.5) | Dashboard only |
| Official procurement portals | 1 (3.26) | Public |
| UBS Securitas group map | 1 (3.13) | Proprietary |
| Financial regulator OJK and Haver | 1 (4.8) | Public tables / commercial |

Fig 3.4 ("top 5 percent… past 31 years") is credited to the Enterprise Surveys, but a 31-year span points to IBS.

**1. BPS manufacturing survey (IBS): sold, not open**
- VERIFIED: The international survey catalog (IHSN) lists the Annual Manufacturing Survey for years from 1975 on. Each entry says the data sit on BPS's own portal, mikrodata.bps.go.id (for example, 2014 is catalog ID 776). https://catalog.ihsn.org/catalog/8625/get-microdata
- VERIFIED: BPS sells firm-level data through its online data service, Silastik. Buyers need ID, a description of how they will use the data, and a signed, stamped data-use agreement. https://ppid.bps.go.id/upload/doc/Standar_Pelayanan_Publik_Penjualan_Data_Mikro_dan_Peta_Digital_Online_1680505694.pdf and https://ppid.bps.go.id/upload/doc/Leaflet_Silastik_1730856403.pdf
- VERIFIED: Prices are set by government regulation PP 13/2024, in force since 14 March 2024. It replaced PP 7/2015, which charged per 1,000 bytes at falling rates. https://peraturan.bpk.go.id/Details/277803
- I could not find the actual price for an IBS year. A BPS leaflet says government agencies, foreign country representatives and international organizations can get data free ("Rp0"), with a small quota (about 10 MB). The leaflet is undated. https://ppid.bps.go.id/upload/doc/Pengenaan_Tarif_Nol_Rupiah_di_BPS__Leaflet__1646724492.pdf
- VERIFIED: BPS states that respondents' data are confidential and published only as aggregates.
- VERIFIED: Academics do use the panel. One 2025 paper uses 2001–2015 and reports response rates falling from 74% in 2004 to 47% in 2017. https://arxiv.org/pdf/2510.11139
- INFERRED: The released files probably carry anonymized plant IDs rather than names.
- INFERRED: Nothing I found says whether a foreign company with no Indonesian partner can buy. The requirements (Indonesian-style ID, university letters) suggest the practical route is through an Indonesian university or research partner.

**2. Economic Census (SE2016)**
- VERIFIED: Silastik lists the "Direktori Perusahaan Hasil Listing SE2016" (the business listing) at Rp 143,855,129, roughly US$8,800. It is a 6.7 GB file with names, addresses and worker counts. https://silastik.bps.go.id/v3/index.php/mikrodata/cetak_info/Zk5BSHIzRnJPRThEcDgrYzhjV2ovdz09
- INFERRED: I did not find the full census modules on sale.

**3. Substitute published tables (no purchase needed)**
- VERIFIED: BPS's "Statistik Industri Manufaktur Indonesia 2023" (released July 2025) gives firm counts, workers, inputs, output and value added by industry code. https://www.bps.go.id/en/publication/2025/07/31/19e60551bde44c9ff6aefcba/statistik-industri-manufaktur-indonesia-2023.html
- VERIFIED: "Indikator Industri Manufaktur 2021" gives productivity by 2-digit industry. https://www.bps.go.id/en/publication/2023/05/30/5936c02d88945022e472289e/indikator-industri-manufaktur-indonesia--2021.html
- INFERRED: These tables can roughly reproduce the sector-share charts (2.3, 2.4, and parts of 2.10/2.11). They cannot reproduce TFP, Olley-Pakes decompositions, entry/exit effects, markups, concentration–productivity links or size-transition charts. Those need the firm-level panel.

**4. The other sources**
- VERIFIED: Enterprise Surveys data are free after registering and signing a confidentiality agreement. https://www.enterprisesurveys.org/en/data-access-protocol
- VERIFIED: The Indonesia 2023 Enterprise Survey has 2,955 firms. https://microdata.worldbank.org/catalog/6449
- FAT survey: the World Bank catalog lists 16 FAT studies, but none of the results I saw showed an Indonesia wave. https://microdata.worldbank.org/catalog/fat. INFERRED: probably not released.
- VERIFIED: TICMI is a stock-exchange subsidiary selling listed-company financials back to 1977. I found no price. INFERRED: filings on idx.co.id are a partial free substitute. INFERRED: the billionaire charts likely use Forbes data despite the TICMI credit.
- VERIFIED: BOS is shown through a dashboard launched in January 2024 and is built on ORBIS, a commercial database. I found no raw download. https://www.worldbank.org/en/programs/competitiveness-for-jobs-and-economic-transformation/brief/business-of-the-state
- VERIFIED: Opentender.net, built by Indonesia Corruption Watch with the national procurement agency (LKPP), holds more than 1.8 million tenders since 2008 in a standard open format. https://www.open-contracting.org/2021/07/19/opentender-net-the-site-helping-indonesians-spot-shady-government-spending/

**5. How World Bank teams get the data**
I found no public agreement between BPS and the World Bank. One search in Indonesian turned up BPS agreements with OJK, the central bank and IsDB, but none with the Bank. INFERRED: Bank teams probably use BPS's free channel for international organizations or buy the data, then work under the standard data-use agreement.

**What this means for the firm:** The firm-microdata charts (about 31 of the 46, all except the Enterprise Survey, procurement and OJK charts) need either a paid BPS purchase, likely through an Indonesian partner, or proprietary sources. The Enterprise Survey and procurement charts can be rebuilt for free.

Pages on worldbank.org and bps.go.id could not be fetched directly, so every VERIFIED item is based on search-result snippets of the linked page.

---

## morocco

**Morocco: is the firm data public? (morocco-2026)**

**Short answer: no.** Most of the firm-level figures in the Morocco report rest on administrative data that is not public. The underlying firm-by-firm records are held by OMTPME (the Moroccan SME observatory), DGI (the tax administration) and CNSS (social security). Only the Enterprise Survey items, and parts of the size-distribution chart, can be rebuilt from public sources.

**What the report uses** (from `/home/user/whynot.github.io/inventory/figures-5-reports.json`)

27 items are tagged "firm microdata". A further 8 items rely on CNSS records but are tagged household/labor or mixed.

| Dataset named in source notes | Items | Count |
|---|---|---|
| OMTPME firm data (described as "firm-level administrative data"; joint World Bank and OMTPME staff calculations; Dalvit et al. 2024) | F2-2, 2-3a, 2-4a/b, 2-5a/b (Olley-Pakes), 2-6a/b, 2-13a/b, 3-1, 3-2a/b, 3-3 (competition risk), 3-6a/b/c, 3-11 | 18 |
| DGI corporate income tax records ("World Bank and DGI staff calculations") | F3-7a (effective tax rate by firm size), 3-7b (number of tax payers by profit) | 2 |
| World Bank Enterprise Survey 2023, and WBES 2019-2024 | F2-8, 2-9, 3-10b | 3 |
| FAT technology survey, Morocco (results described as "preliminary") | F2-3b, 2-7 | 2 |
| World Bank and OMTPME survey on HR practices in manufacturing, 2025 | F4-11a/b | 2 |
| CNSS worker and firm records, analysed by the Employment Observatory of the ministry (MIEPEEC) | F3-9a/b, 4-6c/d, 4-7a/b, 4-12a/b (wage decomposition method) | 8 |

The report has no HCP firm census behind these figures. HCP is used only for the labour force survey and national accounts.

**How each dataset can be accessed**

- **OMTPME: confidential, no public access route found.**
  - VERIFIED: it is run under Bank Al-Maghrib, the central bank. It merges DGI, CNSS, OMPIC and Bank Al-Maghrib data under bilateral data-sharing agreements signed in 2017, which are framed around personal-data protection ([ecoactu](https://ecoactu.ma/observatoire-marocain-des-tpme/), [le360](https://fr.le360.ma/economie/lobservatoire-de-la-tpme-et-le-ministere-du-commerce-et-de-lindustrie-vont-echanger-leurs-donnees-231941/)).
  - VERIFIED: the World Bank describes its work as "the first collaboration between the World Bank and the OMTPME" ([OKR](https://openknowledge.worldbank.org/entities/publication/67e1af29-387c-4c23-9fb9-9b8f37fd389f/full)).
  - INFERRED: I found no researcher-access procedure, no remote-access service and no price. The source notes say "World Bank *and OMTPME staff* calculations", which suggests the code was run by or with OMTPME staff rather than the data being handed over.
- **DGI tax records: confidential by law.**
  - VERIFIED: Article 246 of the tax code (CGI) imposes criminal professional secrecy. The stated exceptions are only Customs, the Treasury (TGR), the Office des Changes and CNSS ([fiscamaroc](https://www.fiscamaroc.com/dispositions-communes-208/article-246-secret-professionnel-377.htm)). That text is dated 2011 and later amendments were not checked.
  - INFERRED: access depends on an agreement with DGI. The source notes credit "World Bank and DGI staff calculations".
- **CNSS: confidential administrative data.**
  - INFERRED: no public microdata or access procedure was found.
  - VERIFIED: an April 2025 data breach exposed records of about 2 million members ([TelQuel](https://telquel.ma/2025/04/09/des-hackers-algeriens-ont-pirate-les-donnees-de-pres-de-2-millions-dadherents-de-la-cnss_1925967)). INFERRED: this probably makes access harder.
- **Enterprise Survey 2023: free with registration.**
  - VERIFIED: listed in the World Bank Microdata Library as MAR_2023_WBES_v01_M, 598 establishments ([catalog 6455](https://microdata.worldbank.org/catalog/6455)).
  - VERIFIED: users sign the Enterprise Surveys data access protocol (a confidentiality agreement) and get access within about 2 business days, free of charge ([login.enterprisesurveys.org](https://login.enterprisesurveys.org/)).
- **FAT Morocco: not public.**
  - VERIFIED: the public FAT collection covers 15 countries and Morocco is not one of them ([microdata.worldbank.org/catalog/fat](https://microdata.worldbank.org/catalog/fat)).
  - INFERRED: it may be released later.
- **HR practices survey 2025: not public.** INFERRED: it is new and its results are unpublished.

**Published tables that could stand in**

- VERIFIED: OMTPME's annual reports (6th edition, January 2026) publish firm counts and turnover by size class. For 2024: micro 86.6% of firms, large 0.5%; large firms hold 63.7% of turnover ([Hespress](https://fr.hespress.com/457430-omtpme-tissu-entrepreneurial-en-croissance-en-2024-mais-sous-forte-pression.html), [Médias24](https://medias24.com/2026/01/07/omtpme-publie-la-sixieme-edition-de-son-rapport-annuel-1608061/)). Regional reports also exist. This is enough to rebuild F2-2.
- VERIFIED: the World Bank 2024 policy note and the summer 2024 Economic Monitor chapter contain the source versions of Figures 2-3 to 2-5 ([OKR](https://openknowledge.worldbank.org/entities/publication/67e1af29-387c-4c23-9fb9-9b8f37fd389f/full)). Their published numbers can be redrawn, but the estimates cannot be re-run.
- VERIFIED: HCP posts anonymized household microdata (2014 census, 2014 household consumption survey, time-use survey), but no firm microdata ([HCP](https://www.hcp.ma/La-mise-en-ligne-des-micro-donnees-anonymisees-du-Recensement-General-de-la-Population-et-de-l-Habitat-de-2014_a2328.html)). VERIFIED: HCP's 2023/24 establishment count publishes aggregates only (1.13 million establishments) ([le360](https://fr.le360.ma/economie/tissu-economique-le-hcp-dresse-une-cartographie-des-entreprises-marocaines_2BPRB77OFBF2XOF7EUFL72X7KM/)).
- VERIFIED: the ERF portal hosts older Moroccan firm surveys: the 2002/03 micro and small enterprise survey and a 2022 enterprise digitization survey ([ERF](https://erfdataportal.com/index.php/catalog/44)). They are not substitutes for administrative panels.
- INFERRED: DGI activity reports and the CNSS annual figures (for example, 4.07 million declared jobs in 2024) give totals only. I did not confirm any breakdown by profit bracket that would reproduce F3-7b.

**How World Bank teams get these data**

VERIFIED from the source notes: every admin-data figure is a *joint* calculation with the agency holding the data (OMTPME, DGI, or MIEPEEC for CNSS). INFERRED: this points to institutional partnerships and agreements, with analysis done alongside agency staff, not data the team can download. VERIFIED: a draft national statistics law (bill 46.26, adopted by the government council on 2 July 2026) cites both data access and confidentiality ([Hespress](https://fr.hespress.com/480097-maroc-vers-une-refonte-du-systeme-statistique-national.html)). It could create a researcher-access route, but none exists yet.

**What this means for the firm**

- **Can be rebuilt now:** 3 Enterprise Survey items (registration needed), plus F2-2 and the redrawn versions of Figures 2-3 to 2-5 from published tables.
- **Out of reach:** about 22 items. Getting them would need a formal partnership with OMTPME, DGI or CNSS. INFERRED: that is realistic only through a World Bank or government counterpart, not as a commercial purchase.

---

## north-macedonia

**North Macedonia: can the firm microdata behind the report be obtained?**

**Short answer:** No, not as free microdata. The "firm census" in this report is not a statistical-office census. It is the **Central Registry's annual-accounts register**, which holds the balance sheets and income statements of all registered firms. You can look up one firm at a time for a fee. I found no official way to download the whole register or get a research extract. The customs, FITD (innovation fund) and Ministry of Finance subsidy files look confidential.

**What the inventory shows** (47 items tagged "firm microdata" in /home/user/whynot.github.io/inventory/figures-5-reports.json)
- **33 items use Central Registry data** ("Central Business Registry", "national business registry data"). 24 of them went through the World Bank's internal ICA 2.0 Toolkit. They cover TFPR quantiles, Melitz-Polanec decomposition, misallocation, top-4 concentration and entrenchment, SOE productivity, and Davis-Haltiwanger job creation. Fig 53 says the data cover "all registered (formal) firms, except self-employed (one person) firms", for 2017-19 and 2020-22. Some of these items join the registry to other files:
  - customs data: Figs 60-62 and 65;
  - FITD beneficiary lists: Figs 57a/b and 87a;
  - Ministry of Finance subsidy data: Fig 65;
  - weather data, through the World Bank's Firms and Climate Tool: Figs 83-84.
- **10 items use the World Bank Enterprise Surveys** (2019 and 2023 waves): Figs 25, 31, 43-49.
- **4 items use other sources:** 2 from the Business of the State (BOS) database (Fig 56a and its Overview copy), 1 from the Community Innovation Survey 2017-18/2021-22 (Fig 87b), and 1 from "preliminary results" of the World Business Dynamics Database (Productivity Table).

**Access, source by source**

1. **Central Registry (crm.com.mk): you can buy single firms, not the database.**
   - VERIFIED (search snippets of crm.com.mk pages; I could not open the site directly): the Registry calls the data "of public character". Its annual-accounts register has financial data from 1994 onward. It issues a "listing of a processed annual account" (balance sheet and income statement, two years) for one firm, after a request form and payment, and the listing is collected at the counter. There is also a general "request for data from the annual-accounts register" form. The Registry publishes a **Statistical Bulletin** of aggregates from the trade register and the annual-accounts register. https://www.crm.com.mk/mk/uslugi/dobijam-potvrda-ili-informatsija/finansiski-informatsii/listing-od-obrabotena-godishna-smetka?idV=11247 ; https://www.crm.com.mk/mk/uslugi/dobijam-potvrda-ili-informatsija/delovni-podatotsi-i-potvrdi-za-subjekti/statistichki-bilten
   - I found no official price list. A reseller charges US$43.50 per firm record (https://records.knowyourcustomer.com/coverage/macedonia). An older access-to-information note mentions about 256 MKD (about €5) for a company profile. That figure may be out of date and may not apply to annual accounts (https://access-info.org/?p=13556).
   - VERIFIED: commercial vendors resell registry-sourced data: Orbis/Bureau van Dijk, InfobelPRO and CompanyData (https://www.infobelpro.com/en/blog/b2b-data-providers-macedonia). Their coverage of value added, employment and capital, which TFP needs, is unverified.
   - VERIFIED: the "200 Largest" ranking (revenue, profit, employees) is built with Central Registry data (https://club200.mk/article/The-20th-jubilee-edition-of-the-200-LARGEST-AND-SU).
2. **State Statistical Office (SSO / MakStat): researchers only, inside its secure room.**
   - VERIFIED (snippets): the Law on State Statistics allows release of individual records without identifiers for scientific research, on written request, to scientists registered with the ministry responsible for science (https://dsbb.imf.org/sdds/dqaf-base/country/MKD/category/POP00). A Household Budget Survey metadata note says access is only in the SSO's "safe room", with an approved request and a signed agreement (https://www.stat.gov.mk/meta/esms/ESMS_APD_Q_EN.pdf).
   - INFERRED: a foreign private firm probably does not qualify. The same rules probably apply to the innovation survey (Fig 87b).
   - VERIFIED: the SSO publishes structural business statistics in MakStat by NACE Rev. 2 and size class, and sends them to Eurostat (https://www.oecd.org/en/publications/sme-policy-index-for-western-balkans-and-turkiye-2026-economy-profile-for-north-macedonia_f8e57052-en/full-report/north-macedonia-sme-definition-and-data-coverage_a97aea84.html). These tables can stand in for the sector productivity and employment-shift charts.
3. **Customs, FITD and Ministry of Finance subsidies: not available.**
   - No public microdata found. INFERRED: the source notes say the FITD data were "provided by the FITD", so the team got them directly from the agency.
   - VERIFIED substitute: the World Bank Exporter Dynamics Database includes North Macedonia, but only through 2014, and only as aggregates (https://microdata.worldbank.org/catalog/2545).
4. **Enterprise Surveys: free after registration.** You accept a confidentiality agreement and may not report cells with fewer than 5 firms (VERIFIED: https://www.enterprisesurveys.org/en/data-access-protocol). All 10 Enterprise Survey items can be replicated.
5. **ICA 2.0 Toolkit, World Business Dynamics Database, Firms and Climate Tool:** I found no public versions (INFERRED: World Bank internal). BOS: I could not confirm a public firm-level download.

**How the World Bank got the registry data**
- VERIFIED: the May 2025 World Bank consultation deck for North Macedonia cites "WB Staff calculations based on Central Registry data" (https://documents1.worldbank.org/curated/en/099062426083023892/pdf/P507002-c2da7ece-2fea-45b9-91d7-0d1bcc865c5a.pdf).
- I found no public agreement between the World Bank and the Central Registry or Customs. INFERRED: the team got a firm-level extract through the government counterpart. Linking registry, customs and subsidy files needs a common firm ID, which only the government can provide. The National Bank has also run firm-level TFP work (Working Paper 5/2018), so official users do get access.

**What this means for the firm**
- Ready now: the 10 Enterprise Survey charts and the macro and sector charts.
- Approximate with published tables: MakStat structural business statistics and the Registry's Statistical Bulletin.
- The registry-based TFP, concentration and job-creation charts need one of three routes: a quote from the Central Registry for a bulk anonymized extract, an Orbis licence (check coverage first), or a local partner such as a university or think tank that applies through the government. The customs-linked and subsidy-linked charts are realistically out of reach.

---

## alternatives

**Short answer: no.** No public dataset can stand in for the census and business-registry panels these charts use in any of the 4 countries. That matters for TFP dispersion, Olley-Pakes/Melitz-Polanec decompositions, top-4 concentration, markups and job flows. The free layer that does exist is World Bank Enterprise Surveys (WBES) microdata plus published aggregate tables. WBES can redo about 24 of the 146 firm-microdata items. Aggregate tables can only roughly stand in for some size and structure charts.

**What the inventory shows** (`/home/user/whynot.github.io/inventory/figures-5-reports.json`). Firm-microdata items by country: Indonesia 42, Morocco 27, North Macedonia 47, Philippines 30, Kenya 0. Item counts by source:

| Source | Indonesia | Morocco | N. Macedonia | Philippines |
|---|---|---|---|---|
| WBES | 8 | 5 | 10 | 1 |
| Firm-level Adoption of Technology (FAT) survey | 4 | 2 | — | 6 |
| ICA 2.0 toolkit | — | — | 24 | 2 |
| Businesses of the State (BOS) database | 5 | — | 4 | — |

The rest are national datasets: BPS manufacturing survey, PSA ASPBI/CPBI, OMTPME/DGI, and North Macedonia's Central Registry plus customs data.

**Source by source**

1. **WBES microdata.** Free, but you must register and sign the Data Access Protocol. You may not publish anything firm-specific, and statistics based on fewer than 5 firms may not be reported. VERIFIED (enterprisesurveys.org/en/data-access-protocol, from search snippet). Country-level indicators can be downloaded publicly. VERIFIED (enterprisesurveys.org/data).
   - **Latest survey per country**, all VERIFIED in the Microdata Library: Indonesia 2023 (2,955 firms, catalog 6449), Philippines 2023 (1,002 firms, catalog 6464), Morocco 2023 (598 firms, catalog 6455), North Macedonia 2023 (catalog 6460).
   - **Ready-made TFP:** a file called "Firm Level TFP Estimates and Factor Ratios" also needs registration. VERIFIED (World Bank Policy Research Working Paper 9491).
   - **Charts it can redo:**
     - Obstacles: Morocco 2-8 and 2-9, North Macedonia 43-45.
     - Productivity and size distribution curves: Indonesia 2.7, 3.1 and 3.2.
     - Lorenz curve of sales: Indonesia 3.3.
     - Regulatory delays: Indonesia 3.22 and 3.23.
     - The North Macedonia "ICA 2.0" charts that rest on WBES: 25, 31 and 46-49.
   - **Limits:** formal firms with 5+ employees, mostly one year at a time. It cannot produce job flows, entry/exit or concentration.

2. **FAT survey.** Hosted in the Microdata Library; a free login is required. VERIFIED (microdata.worldbank.org/collections/fat/about). Released countries include Brazil, Kenya, Burkina Faso, Chile, Ethiopia, Senegal and Croatia 2023. I found no Philippine (2024), Indonesian or Moroccan file, so those are INFERRED as not released yet. That puts about 12 charts out of reach for now.

3. **ICA 2.0 toolkit.** It exists as a World Bank policy toolkit ("Enabling Productive Investment"), presented in June 2024. VERIFIED (enterprise-development.org, 20240603 DCED BEWG minutes). I found no public code, so INFERRED as internal to the Bank. The methods are standard: TFPR quantiles, Davis-Haltiwanger job creation, Melitz-Polanec. You can recode them yourselves; the data, not the code, is what stops you.

4. **Other World Bank databases**
   - **Entrepreneurship Database:** new limited-liability firm registrations, 188 economies, 2006-2024, CC BY 4.0. VERIFIED (worldbank.org/en/programs/entrepreneurship; WDI indicator IC.BUS.NDNS.ZS). Covers entry-rate charts only. I did not check coverage of each of the 4 countries.
   - **World Business Dynamics Database:** I found no public record of it. North Macedonia cites only "preliminary results", so INFERRED as unreleased.
   - **BOS database:** built from Orbis, 90+ countries. VERIFIED (OKR 10986/38447). I found no public download (INFERRED). It feeds 9 charts.

5. **CompNet.** Industry-level productivity dispersion, markups, concentration and job flows. The 10th vintage covers 18 European countries and access needs CompNet Executive Committee approval. VERIFIED (iwh-halle.de/en/compnet/data/10th-vintage). The 9th vintage list includes Croatia, Slovenia and Romania but none of the 4 countries. VERIFIED (b2find 9th vintage record). Useful only as EU peer benchmarks for North Macedonia.

6. **OECD MultiProd / DynEmp.** Output is micro-aggregated and the firm records stay with national partners. The core set is 22 countries, 1995-2012. VERIFIED (OECD metadata paper 2019/03). INFERRED that none of the 4 is covered. Peer benchmarks only.

7. **Orbis.** Licensed from Moody's/BvD with no published price (VERIFIED: no amounts found). Depth varies by country, and it needs reweighting to be representative. VERIFIED (CEPR DP 10829, Kalemli-Özcan et al.). INFERRED: reasonable for concentration among large firms, weak for SMEs in Indonesia, the Philippines and Morocco.

8. **National published tables**, all VERIFIED:
   - **North Macedonia:** State Statistical Office business statistics by size class on MakStat, latest year 2024 (OECD SME Policy Index 2026).
   - **Philippines:** PSA ASPBI tables by industry, split by establishments under and over 20 workers (psa.gov.ph/content/annual-survey-philippine-business-and-industry-aspbi).
   - **Indonesia:** BPS manufacturing statistics (firms with 20+ workers); BPS also sells microdata through its SILASTIK portal ("Pembelian Data Mikro"). Price not found.
   - **Morocco:** OMTPME 2025 annual report, data for 2017-2024 (fnh.ma).
   - **What they can stand in for:** size distribution, Morocco 2-2, North Macedonia 24/88/89, Philippines 3.10-3.11. Not dispersion.

9. **UNIDO INDSTAT.** Open data since 2022. VERIFIED (stat.unido.org/content/general/databases-for-free). Manufacturing only, at 4-digit industry level: establishments, employment, wages, output and value added. Supports productivity across sectors and shift-share charts. I did not check the gaps for each country.

10. **Exporter Dynamics Database.** Exporter counts and concentration from customs records, 70 countries, North Macedonia included. VERIFIED (Microdata Library catalog 2545). I could not confirm Morocco.

**Which chart types are covered**

| CGJR chart type | Public substitute |
|---|---|
| Obstacles | WBES: full |
| Size distribution | WBES + national tables + OMTPME: good |
| TFP dispersion | WBES TFP file: partial (one year, manufacturing). CompNet/MultiProd: peers only |
| Concentration / markups | Nothing public at firm level. Orbis (licensed): partial |
| Job flows / entry-exit | Nothing public for the 4. Entrepreneurship Database: entry counts only |

To get the census-based charts, a team would need a data-sharing agreement with the statistics office, registry or tax authority, or a paid microdata purchase. Only BPS (SILASTIK) offers a purchase route in what I found; the other access routes are covered in the country briefs.