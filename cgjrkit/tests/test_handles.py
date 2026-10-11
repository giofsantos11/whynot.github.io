"""Unit tests for the pure identifier utilities (no network, no fixtures needed).

The handle ``10986/11843`` (World Development Report 2013: Jobs) and the DOI
``10.3386/w17143`` (McMillan and Rodrik 2011) are real identifiers used only
as strings here; nothing is looked up.
"""

import inspect

import pandas as pd
import pytest

from cgjrkit.connectors import handles
from cgjrkit.connectors.base import Connector
from cgjrkit.connectors.handles import IdentifierError


# -- DOIs ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "10.3386/w17143",
    "10.3386/W17143",
    "  https://doi.org/10.3386/w17143 ",
    "http://dx.doi.org/10.3386/w17143",
    "https://www.doi.org/10.3386/w17143",
    "doi:10.3386/w17143",
    "DOI: 10.3386/w17143",
    "info:doi/10.3386/w17143",
    "10.3386%2Fw17143",
])
def test_normalize_doi_forms(text):
    assert handles.normalize_doi(text) == "10.3386/w17143"


@pytest.mark.parametrize("text", ["World Bank 2023c", "", "10.12/abc", "10.3386", "https://example.org/x", "10986/11843"])
def test_normalize_doi_rejects(text):
    with pytest.raises(IdentifierError):
        handles.normalize_doi(text)
    assert not handles.is_doi(text)


def test_normalize_doi_rejects_non_strings():
    with pytest.raises(IdentifierError):
        handles.normalize_doi(17143)  # type: ignore[arg-type]


def test_doi_url_and_encoding():
    assert handles.doi_url("doi:10.3386/W17143") == "https://doi.org/10.3386/w17143"
    # parentheses and semicolons stay; '#', '?' and '<>' are percent-encoded (spaces are not valid in DOIs)
    assert handles.doi_url("10.1016/S0140-6736(20)30183-5") == "https://doi.org/10.1016/s0140-6736(20)30183-5"
    assert handles.doi_url("10.5555/a#b?c<d>") == "https://doi.org/10.5555/a%23b%3Fc%3Cd%3E"
    with pytest.raises(IdentifierError):
        handles.doi_url("10.5555/a b")
    assert handles.doi_prefix("https://doi.org/10.1596/978-0-8213-9575-2") == "10.1596"


# -- handles ------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", [
    "10986/11843",
    "hdl:10986/11843",
    "https://hdl.handle.net/10986/11843",
    "http://hdl.handle.net/10986/11843/",
    "https://openknowledge.worldbank.org/handle/10986/11843",
    "https://openknowledge.worldbank.org/handle/10986/11843?show=full",
    "https://openknowledge.worldbank.org/handle/10986/11843#section",
])
def test_normalize_okr_handle_forms(text):
    assert handles.normalize_handle(text) == "10986/11843"
    assert handles.is_okr_handle(text)
    assert handles.handle_url(text) == "https://hdl.handle.net/10986/11843"
    assert handles.okr_url(text) == "https://openknowledge.worldbank.org/handle/10986/11843"


def test_non_okr_handles():
    assert handles.normalize_handle("hdl:20.500.12592/abc-1") == "20.500.12592/abc-1"
    assert not handles.is_okr_handle("20.500.12592/abc-1")
    assert handles.handle_url("20.500.12592/abc-1") == "https://hdl.handle.net/20.500.12592/abc-1"
    with pytest.raises(IdentifierError):
        handles.okr_url("20.500.12592/abc-1")


@pytest.mark.parametrize("text", ["10986/abc", "10986/", "/11843", "World Bank 2023a", "hdl:", "abc/def"])
def test_normalize_handle_rejects(text):
    with pytest.raises(IdentifierError):
        handles.normalize_handle(text)
    assert not handles.is_okr_handle(text)


# -- OpenAlex IDs -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["W2741809807", "w2741809807", "https://openalex.org/W2741809807",
                                  "https://api.openalex.org/works/W2741809807", "openalex:W2741809807"])
def test_normalize_openalex_id(text):
    assert handles.normalize_openalex_id(text) == "W2741809807"
    assert handles.openalex_url(text) == "https://openalex.org/W2741809807"


@pytest.mark.parametrize("text", ["A5023888391", "W", "W12x", "2741809807"])
def test_normalize_openalex_id_rejects(text):
    with pytest.raises(IdentifierError):
        handles.normalize_openalex_id(text)


# -- parse_identifier / identifier_table ------------------------------------------------------------------
def test_parse_identifier_classifies():
    doi = handles.parse_identifier("https://doi.org/10.3386/W17143")
    assert (doi.id_type, doi.identifier, doi.canonical_url, doi.landing_url, doi.verified) == (
        "doi", "10.3386/w17143", "https://doi.org/10.3386/w17143", "https://doi.org/10.3386/w17143", False)
    okr = handles.parse_identifier("hdl:10986/11843")
    assert (okr.id_type, okr.identifier, okr.canonical_url, okr.landing_url) == (
        "handle", "10986/11843", "https://hdl.handle.net/10986/11843",
        "https://openknowledge.worldbank.org/handle/10986/11843")
    other = handles.parse_identifier("20.500.12592/abc-1")
    assert other.id_type == "handle" and other.landing_url == other.canonical_url
    oa = handles.parse_identifier("W2741809807")
    assert (oa.id_type, oa.canonical_url) == ("openalex", "https://openalex.org/W2741809807")
    # a DOI written as a handle URL is still a DOI
    as_handle = handles.parse_identifier("https://hdl.handle.net/10.1596/978-0-8213-9575-2")
    assert (as_handle.id_type, as_handle.identifier) == ("doi", "10.1596/978-0-8213-9575-2")


@pytest.mark.parametrize("text", ["World Bank 2023c", "Hanusch (forthcoming)", "", "IMF Country Report No. 24/12"])
def test_parse_identifier_rejects_non_identifiers(text):
    with pytest.raises(IdentifierError):
        handles.parse_identifier(text)


def test_identifier_table():
    df = handles.identifier_table(["10.3386/w17143", "hdl:10986/11843", "World Bank 2023c"], errors="keep")
    assert list(df.columns) == handles.IDENTIFIER_COLUMNS
    assert df["id_type"].tolist() == ["doi", "handle", "invalid"]
    assert not df["verified"].any()  # constructed links are never "verified"
    assert df.loc[2, "canonical_url"] == ""
    with pytest.raises(IdentifierError):
        handles.identifier_table(["World Bank 2023c"])
    with pytest.raises(ValueError):
        handles.identifier_table([], errors="ignore")


# -- works table helpers --------------------------------------------------------------------------------
def test_make_and_validate_works_frame():
    df = handles.make_works_frame([
        {"openalex_id": "W1", "doi": "10.5555/x", "title": "  A   title ", "year": 2000, "authors": "",
         "venue": None, "type": "article", "cited_by_count": 3, "url": "https://doi.org/10.5555/x", "is_oa": True},
        {"openalex_id": None, "doi": None, "title": None, "year": None, "authors": "B", "venue": float("nan"),
         "type": "report", "cited_by_count": None, "url": None, "is_oa": None},
    ])
    handles.validate_works_frame(df)
    assert list(df.columns) == handles.WORKS_COLUMNS
    assert str(df["year"].dtype) == "Int64" and str(df["is_oa"].dtype) == "boolean"
    assert df.loc[0, "title"] == "A title"
    assert df.loc[0, "authors"] is None and df.loc[1, "venue"] is None
    assert pd.isna(df.loc[1, "year"]) and pd.isna(df.loc[1, "is_oa"])
    assert handles.returned_ids(df) == ["W1", "10.5555/x"]
    empty = handles.empty_works_frame()
    handles.validate_works_frame(empty)
    assert empty.empty


def test_validate_works_frame_rejects():
    df = handles.empty_works_frame()
    with pytest.raises(ValueError):
        handles.validate_works_frame(df.drop(columns="is_oa"))
    bad = handles.make_works_frame([{"doi": "10.5555/x"}])
    bad.loc[0, "doi"] = "10.5555/UPPER"
    with pytest.raises(ValueError):
        handles.validate_works_frame(bad)
    bad_id = handles.make_works_frame([{"openalex_id": "https://openalex.org/W1"}])
    with pytest.raises(ValueError):
        handles.validate_works_frame(bad_id)


# -- contact etiquette ---------------------------------------------------------------------------------
def test_contact_email(monkeypatch):
    monkeypatch.delenv(handles.CONTACT_ENV, raising=False)
    assert handles.contact_email() is None
    monkeypatch.setenv(handles.CONTACT_ENV, " analyst@example.org ")
    assert handles.contact_email() == "analyst@example.org"
    assert handles.contact_email("other@example.org") == "other@example.org"
    monkeypatch.setenv(handles.CONTACT_ENV, "not-an-email")
    with pytest.raises(ValueError):
        handles.contact_email()
    assert "mailto:analyst@example.org" in handles.polite_user_agent("analyst@example.org")
    assert "mailto" not in handles.polite_user_agent(None)


def test_handles_module_makes_no_network_calls():
    """handles.py builds links only: no Connector, no HTTP client imported."""
    assert not any(inspect.isclass(o) and issubclass(o, Connector) for o in vars(handles).values())
    src = inspect.getsource(handles)
    assert "core import http" not in src and "httpx" not in src
