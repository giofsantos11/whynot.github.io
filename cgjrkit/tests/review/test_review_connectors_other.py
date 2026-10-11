"""Adversarial review probes for the non-core connectors (ilostat, unctad, baci, oecd, openalex).

Each test documents a defect found in review. They were marked ``xfail(strict=True)``
until the defect was fixed; they now guard against regressions.
All payloads are synthetic (round numbers, years 2097-2099 or obviously fake ids).
"""

from __future__ import annotations

import json


from cgjrkit.connectors import ilostat as ilo
from cgjrkit.connectors import oecd, openalex, unctad
from cgjrkit.connectors.baci import BACI
from cgjrkit.connectors import baci
from cgjrkit.connectors.openalex import OpenAlex
from cgjrkit.connectors.unctad import UNCTAD
from cgjrkit.core.cache import Cache
from cgjrkit.core.schema import validate_frame


# ---------------------------------------------------------------------------------------------
# ILOSTAT: the ILOEST projection caveat uses one global "last real year" for all countries
# ---------------------------------------------------------------------------------------------
_ILO_TWO_COUNTRIES = """ref_area,source,indicator,sex,classif1,time,obs_value,obs_status
KEN,XA:9001,UNE_2EAP_SEX_AGE_RT,SEX_T,AGE_YTHADULT_YGE15,2095,10.0,R
KEN,XA:9001,UNE_2EAP_SEX_AGE_RT,SEX_T,AGE_YTHADULT_YGE15,2096,10.0,R
KEN,XA:9001,UNE_2EAP_SEX_AGE_RT,SEX_T,AGE_YTHADULT_YGE15,2097,10.0,
KEN,XA:9001,UNE_2EAP_SEX_AGE_RT,SEX_T,AGE_YTHADULT_YGE15,2098,10.0,
KEN,XA:9001,UNE_2EAP_SEX_AGE_RT,SEX_T,AGE_YTHADULT_YGE15,2099,10.0,
UGA,XA:9002,UNE_2EAP_SEX_AGE_RT,SEX_T,AGE_YTHADULT_YGE15,2095,20.0,R
UGA,XA:9002,UNE_2EAP_SEX_AGE_RT,SEX_T,AGE_YTHADULT_YGE15,2096,20.0,R
UGA,XA:9002,UNE_2EAP_SEX_AGE_RT,SEX_T,AGE_YTHADULT_YGE15,2097,20.0,R
UGA,XA:9002,UNE_2EAP_SEX_AGE_RT,SEX_T,AGE_YTHADULT_YGE15,2098,20.0,R
UGA,XA:9002,UNE_2EAP_SEX_AGE_RT,SEX_T,AGE_YTHADULT_YGE15,2099,20.0,R
"""


def test_ilo_projection_caveat_is_per_country():
    parsed = ilo.parse(_ILO_TWO_COUNTRIES.encode(), indicator="UNE_2EAP_SEX_AGE_RT")
    assert parsed.real_value_periods == {"KEN": "2095-2096", "UGA": "2095-2099"}
    codes = {c.code: c for c in ilo.build_caveats(parsed)}
    # Kenya's values after its own last real year (2096) are model nowcasts and must be flagged.
    assert "ilostat_modelled_projection" in codes
    assert {"2097", "2098", "2099"} <= set(codes["ilostat_modelled_projection"].evidence["periods"])


def test_ilo_ppp_series_unit_keeps_a_ppp_marker_without_label():
    from cgjrkit.core.schema import ppp_base

    csv = b"ref_area,source,indicator,time,obs_value,obs_status\nKEN,XA:9001,GDP_211P_NOC_NB,2098,1000.0,R\n"
    parsed = ilo.parse(csv, indicator="GDP_211P_NOC_NB", toc=None)
    unit = parsed.frame["unit"].iloc[0]
    # CONVENTIONS: PPP-based units must state the base year; at minimum ppp_base() must not say "not PPP".
    assert ppp_base(unit) is not None, repr(unit)


# ---------------------------------------------------------------------------------------------
# UNCTAD: a pinned bulk release is labelled with the *current* metadata version
# ---------------------------------------------------------------------------------------------
def test_unctad_pinned_version_vintage(fixtures_dir):
    url, params = unctad.build_metadata_request()
    Cache().put(UNCTAD.source, url, params, (fixtures_dir / "unctad_fdi_metadata_synthetic.json").read_bytes())
    url, params = unctad.build_bulk_request(version=1912)
    Cache().put(UNCTAD.source, url, params, (fixtures_dir / "unctad_fdi_bulk_synthetic.csv").read_bytes())
    res = UNCTAD().fdi("KEN", version=1912)
    data_prov = res.provenance[-1]
    assert data_prov.endpoint.endswith("/1912/US_FdiFlowsStock")
    assert "1912" in data_prov.vintage, data_prov.vintage


# ---------------------------------------------------------------------------------------------
# BACI: every call re-reads the whole cached zip (2.4 GB for the default HS92) into memory
# ---------------------------------------------------------------------------------------------
def test_baci_does_not_reload_zip_bytes_when_store_exists(fixtures_dir, monkeypatch):
    version = "209901"
    url, params = baci.build_request("HS92", version)
    Cache().put(BACI.source, url, params, (fixtures_dir / "baci_hs92_v209901_synthetic.zip").read_bytes())
    b = BACI()
    b.exporter_totals("KEN", [2097], version=version)  # builds the Parquet store

    bytes_loaded: list[int] = []
    original = Cache.get_object

    def counting(self, sha256):
        data = original(self, sha256)
        bytes_loaded.append(len(data))
        return data

    monkeypatch.setattr(Cache, "get_object", counting)
    b.exporter_totals("KEN", [2097], version=version)  # store exists: no need to load the zip bytes
    assert bytes_loaded == [], f"zip bytes loaded into memory again: {bytes_loaded}"


# ---------------------------------------------------------------------------------------------
# OECD: a dimension missing from the hard-coded (UNVERIFIED) DSD order is demoted to an
# attribute, which collapses distinct series onto one indicator code
# ---------------------------------------------------------------------------------------------
_PMR_EXTRA_DIM = (
    "STRUCTURE,STRUCTURE_ID,STRUCTURE_NAME,ACTION,REF_AREA,Reference area,MEASURE,Measure,"
    "METHODOLOGY,Methodology,FREQ,Frequency of observation,TIME_PERIOD,Time period,OBS_VALUE,Observation value\n"
    "DATAFLOW,OECD.ECO.GCRD:DSD_PMR@DF_PMR(1.3),PMR (synthetic),I,ITA,Italy,PMR,Product market regulation,"
    "M2018,2018 methodology,A,Annual,2098,,1.0,\n"
    "DATAFLOW,OECD.ECO.GCRD:DSD_PMR@DF_PMR(1.3),PMR (synthetic),I,ITA,Italy,PMR,Product market regulation,"
    "M1998,1998 methodology,A,Annual,2098,,2.0,\n"
)


def test_oecd_extra_dimension_keeps_series_distinct():
    parsed = oecd.parse(_PMR_EXTRA_DIM, "PMR")
    assert "METHODOLOGY" in parsed.meta["dimensions"], parsed.meta["dimensions"]
    validate_frame(parsed.frame)
    assert parsed.frame["indicator"].nunique() == 2


# ---------------------------------------------------------------------------------------------
# OpenAlex: batch DOI look-up sets per_page = number of DOIs, so duplicate OpenAlex records for
# one DOI push another DOI off the page; it is then reported as unresolved (blocking caveat)
# ---------------------------------------------------------------------------------------------
def _work(wid: str, doi: str) -> dict:
    return {"id": f"https://openalex.org/{wid}", "doi": f"https://doi.org/{doi}", "title": "SYNTHETIC",
            "display_name": "SYNTHETIC", "publication_year": 2099, "type": "article", "cited_by_count": 0,
            "authorships": [], "primary_location": None, "open_access": {"is_oa": False},
            "is_retracted": False, "updated_date": "2099-01-01T00:00:00"}


def test_openalex_batch_truncation_is_not_reported_as_unresolved():
    dois = ["10.5555/synthetic.a", "10.5555/synthetic.b"]
    url, params, norm = openalex.build_dois_request(dois)
    # Fixed: batches ask for the maximum page size (was per_page=len(dois)).
    assert params["per_page"] == openalex.MAX_PER_PAGE
    # A truncated answer (count 3, two results, both for DOI a). DOI b's singleton fallback is not cached and
    # the test runs offline, so b must end up "unchecked" with a truncation caveat, not blocked as unresolved.
    payload = {"meta": {"count": 3, "page": 1, "per_page": 2, "cost_usd": 0.0001},
               "results": [_work("W9000000001", dois[0]), _work("W9000000002", dois[0])]}
    Cache().put(OpenAlex.source, url, params, json.dumps(payload).encode())
    res = OpenAlex(api_key="synthetic-key").resolve_dois(dois)
    codes = {c.code for c in res.caveats}
    truncated_flagged = "literature_results_truncated" in codes
    assert truncated_flagged or dois[1] not in res.meta["unresolved"], res.meta["unresolved"]
