"""Identifier utilities for the literature resolvers (pure; no network).

This module turns identifier strings into canonical, citable URLs and defines
the *works table* shared by :mod:`cgjrkit.connectors.openalex` and
:mod:`cgjrkit.connectors.crossref`. It never calls an API:

* **DOIs** -> ``https://doi.org/<doi>`` (the DOI proxy).
* **Handles** -> ``https://hdl.handle.net/<prefix>/<suffix>`` (the Handle
  System proxy). World Bank Open Knowledge Repository (OKR) handles use the
  prefix ``10986``; for them :func:`okr_url` also gives the repository link
  ``https://openknowledge.worldbank.org/handle/10986/<n>``, a route the
  DSpace 7+ user interface that runs the OKR keeps for handle look-ups.
* **OpenAlex work IDs** -> ``https://openalex.org/W<digits>``.

The World Bank Documents & Reports API (``search.worldbank.org/api/v3/wds``)
and the OKR's own REST API are deliberately **not** used: the World Bank's
terms restrict API use for commercial uses of non-dataset materials, so they
wait for legal sign-off (``inventory/build-plan.md`` section 4.3). Ordinary
links like the ones built here are fine.

Anti-hallucination rule
-----------------------
Callers, including AI clients, may cite **only identifiers returned by a
resolver**: a DOI or OpenAlex ``W`` ID present in a works table returned by
:func:`cgjrkit.connectors.openalex.resolve_doi`,
:func:`~cgjrkit.connectors.openalex.search_works`,
:func:`cgjrkit.connectors.crossref.resolve_doi` or
:func:`~cgjrkit.connectors.crossref.search`. A URL built here from a
caller-supplied string is *constructed, not verified*: it proves the string is
well formed, not that the document exists or says what the caller claims.
:func:`identifier_table` therefore always reports ``verified=False``. OKR
handles have no open resolver in this toolkit yet; cite them only when they
come from a trusted source (the report's own reference list, an OKR page
opened by a person) and keep them flagged as unverified in the ledger.
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from urllib.parse import quote, unquote

import pandas as pd

from cgjrkit import __version__

#: Environment variable holding the contact address sent to literature APIs
#: (Crossref ``mailto`` polite pool; OpenAlex User-Agent).
CONTACT_ENV = "CGJRKIT_CONTACT_EMAIL"

DOI_PROXY = "https://doi.org/"
HANDLE_PROXY = "https://hdl.handle.net/"
OKR_HANDLE_BASE = "https://openknowledge.worldbank.org/handle/"
OPENALEX_BASE = "https://openalex.org/"

#: Handle prefix of the World Bank Open Knowledge Repository.
OKR_PREFIX = "10986"
#: DOI prefix used by World Bank Publications (many OKR items also carry one).
WORLD_BANK_DOI_PREFIX = "10.1596"

#: A DOI: ``10.`` + registrant code (4-9 digits, optionally with dotted sub-codes) + ``/`` + suffix.
DOI_RE = re.compile(r"^10\.\d{4,9}(?:\.\d+)*/\S+$")
#: A Handle: numeric (optionally dotted) prefix + ``/`` + non-empty suffix.
HANDLE_RE = re.compile(r"^(\d+(?:\.\d+)*)/(\S+)$")
OPENALEX_WORK_RE = re.compile(r"^[Ww](\d+)$")

_DOI_PREFIXES = re.compile(r"^(?:doi:\s*|info:doi/|https?://(?:dx\.|www\.)?doi\.org/)", re.IGNORECASE)
_HANDLE_PREFIXES = re.compile(
    r"^(?:hdl:\s*|info:hdl/|https?://hdl\.handle\.net/|https?://openknowledge\.worldbank\.org/handle/)", re.IGNORECASE
)
_OPENALEX_PREFIXES = re.compile(r"^(?:openalex:\s*|https?://(?:api\.)?openalex\.org/(?:works/)?)", re.IGNORECASE)
#: Characters left as is in DOI and Handle URL paths; everything else
#: (``#``, ``?``, ``%``, spaces, quotes, ``<>``, ``[]``, ...) is percent-encoded.
PATH_SAFE = "/:;()!$&'*+,=@-._~"

# -- works table -----------------------------------------------------------------------------------------
#: Columns of the works table returned by the literature resolvers. This is a
#: NON-CANONICAL table: it is not the long observation frame of
#: :mod:`cgjrkit.core.schema` and must not be passed to methods or charts.
WORKS_COLUMNS = [
    "openalex_id",     # OpenAlex work key "W123..." (None for Crossref rows)
    "doi",             # normalized lower-case DOI "10.xxxx/..." or None
    "title",           # work title as given by the resolver
    "year",            # publication year (Int64, <NA> if unknown)
    "authors",         # "; "-joined author names in byline order
    "venue",           # journal / series / repository / institution name
    "type",            # resolver's own type vocabulary (OpenAlex and Crossref differ)
    "cited_by_count",  # resolver's own citation count (not comparable across resolvers)
    "url",             # canonical URL: https://doi.org/<doi>, else landing page, else OpenAlex URL
    "is_oa",           # open-access flag (boolean, <NA> when the resolver has none, e.g. Crossref)
]
_WORKS_STR = ["openalex_id", "doi", "title", "authors", "venue", "type", "url"]

#: Columns of :func:`identifier_table` (also non-canonical).
IDENTIFIER_COLUMNS = ["input", "id_type", "identifier", "canonical_url", "landing_url", "verified"]


class IdentifierError(ValueError):
    """The string is not a well-formed identifier of the expected kind."""


# ---------------------------------------------------------------------------
# DOIs
# ---------------------------------------------------------------------------
def normalize_doi(text: str) -> str:
    """Return the bare, lower-case DOI (``"10.3386/w17143"``).

    Accepts ``doi:``, ``info:doi/``, ``https://doi.org/`` and
    ``http://dx.doi.org/`` forms and percent-encoded DOIs. DOI names are case
    insensitive, so the result is lower-cased for comparison and stable cache
    keys. Nothing else is stripped: a DOI copied with trailing punctuation
    stays wrong and fails to resolve, which is the safe outcome.

    Raises :class:`IdentifierError` if the string is not a DOI.
    """
    if not isinstance(text, str):
        raise IdentifierError(f"DOI must be a string, got {type(text).__name__}")
    s = text.strip()
    s = _DOI_PREFIXES.sub("", s).strip()
    if "%" in s:
        s = unquote(s)
    s = s.lower()
    if not DOI_RE.match(s):
        raise IdentifierError(f"not a DOI: {text!r} (expected 10.<registrant>/<suffix>)")
    return s


def is_doi(text: str) -> bool:
    try:
        normalize_doi(text)
    except IdentifierError:
        return False
    return True


def doi_url(doi: str) -> str:
    """Canonical DOI link, ``https://doi.org/<doi>`` (special characters percent-encoded)."""
    return DOI_PROXY + quote(normalize_doi(doi), safe=PATH_SAFE)


def doi_prefix(doi: str) -> str:
    """Registrant prefix of a DOI (``"10.3386"``)."""
    return normalize_doi(doi).split("/", 1)[0]


# ---------------------------------------------------------------------------
# Handles (World Bank OKR and others)
# ---------------------------------------------------------------------------
def normalize_handle(text: str) -> str:
    """Return the bare handle ``"<prefix>/<suffix>"``.

    Accepts ``hdl:10986/4391``, ``https://hdl.handle.net/10986/4391`` and
    ``https://openknowledge.worldbank.org/handle/10986/4391`` (query strings,
    fragments and trailing slashes are dropped). OKR handles (prefix
    ``10986``) must have a numeric suffix. DOIs are handles too (prefix
    ``10.``); use :func:`normalize_doi` for them.

    Raises :class:`IdentifierError` if the string is not a handle.
    """
    if not isinstance(text, str):
        raise IdentifierError(f"handle must be a string, got {type(text).__name__}")
    s = _HANDLE_PREFIXES.sub("", text.strip()).strip()
    s = re.split(r"[?#]", s, maxsplit=1)[0].rstrip("/")
    if "%" in s:
        s = unquote(s)
    m = HANDLE_RE.match(s)
    if not m:
        raise IdentifierError(f"not a handle: {text!r} (expected <prefix>/<suffix>, e.g. 10986/4391)")
    prefix, suffix = m.groups()
    if prefix == OKR_PREFIX and not suffix.isdigit():
        raise IdentifierError(f"not a World Bank OKR handle: {text!r} (expected 10986/<digits>)")
    return f"{prefix}/{suffix}"


def is_okr_handle(text: str) -> bool:
    """True for a well-formed World Bank OKR handle (``10986/<digits>``)."""
    try:
        return normalize_handle(text).split("/", 1)[0] == OKR_PREFIX
    except IdentifierError:
        return False


def handle_url(handle: str) -> str:
    """Canonical Handle System link, ``https://hdl.handle.net/<prefix>/<suffix>``."""
    return HANDLE_PROXY + quote(normalize_handle(handle), safe=PATH_SAFE)


def okr_url(handle: str) -> str:
    """OKR repository link ``https://openknowledge.worldbank.org/handle/10986/<n>``.

    Raises :class:`IdentifierError` for handles outside the OKR prefix.
    """
    h = normalize_handle(handle)
    if h.split("/", 1)[0] != OKR_PREFIX:
        raise IdentifierError(f"{handle!r} is not a World Bank OKR handle (prefix {OKR_PREFIX})")
    return OKR_HANDLE_BASE + h


# ---------------------------------------------------------------------------
# OpenAlex IDs
# ---------------------------------------------------------------------------
def normalize_openalex_id(text: str) -> str:
    """Return the OpenAlex work key ``"W<digits>"`` (accepts the full ``https://openalex.org/W...`` form)."""
    if not isinstance(text, str):
        raise IdentifierError(f"OpenAlex ID must be a string, got {type(text).__name__}")
    s = _OPENALEX_PREFIXES.sub("", text.strip()).strip().rstrip("/")
    m = OPENALEX_WORK_RE.match(s)
    if not m:
        raise IdentifierError(f"not an OpenAlex work ID: {text!r} (expected W<digits>)")
    return "W" + m.group(1)


def openalex_url(work_id: str) -> str:
    return OPENALEX_BASE + normalize_openalex_id(work_id)


# ---------------------------------------------------------------------------
# generic parsing
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Identifier:
    """A parsed identifier with its canonical and landing URLs.

    ``verified`` is always False here: nothing has been looked up. Only a
    resolver result makes an identifier citable (see the module docstring).
    """

    input: str
    id_type: str          # "doi" | "handle" | "openalex"
    identifier: str       # normalized form
    canonical_url: str    # doi.org / hdl.handle.net / openalex.org link
    landing_url: str      # OKR page for 10986 handles, else the canonical URL
    verified: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def parse_identifier(text: str) -> Identifier:
    """Classify and normalize a DOI, Handle or OpenAlex work ID.

    DOIs are tried first (they are handles with a ``10.`` prefix), then
    OpenAlex IDs, then other handles. Raises :class:`IdentifierError` if the
    string is none of these (for example a bare report title or a
    report-local key such as "World Bank 2023c").
    """
    raw = text if isinstance(text, str) else str(text)
    s = raw.strip()
    if _DOI_PREFIXES.match(s) or DOI_RE.match(s.lower()) or re.match(r"^10\.\d", s):
        doi = normalize_doi(s)
        url = doi_url(doi)
        return Identifier(raw, "doi", doi, url, url)
    if _OPENALEX_PREFIXES.match(s) or OPENALEX_WORK_RE.match(s):
        wid = normalize_openalex_id(s)
        url = openalex_url(wid)
        return Identifier(raw, "openalex", wid, url, url)
    handle = normalize_handle(s)
    if handle.startswith("10."):  # a DOI written in handle form (e.g. hdl.handle.net/10.1596/...)
        doi = normalize_doi(handle)
        url = doi_url(doi)
        return Identifier(raw, "doi", doi, url, url)
    canonical = handle_url(handle)
    landing = okr_url(handle) if handle.split("/", 1)[0] == OKR_PREFIX else canonical
    return Identifier(raw, "handle", handle, canonical, landing)


def identifier_table(texts: Iterable[str], *, errors: str = "raise") -> pd.DataFrame:
    """Parse several identifiers into a table with :data:`IDENTIFIER_COLUMNS`.

    ``errors="raise"`` (default) raises on the first malformed string;
    ``errors="keep"`` keeps it as a row with ``id_type="invalid"`` and empty
    URLs so the caller can flag it. ``verified`` is always False.
    """
    if errors not in ("raise", "keep"):
        raise ValueError("errors must be 'raise' or 'keep'")
    rows = []
    for t in texts:
        try:
            rows.append(parse_identifier(t).to_dict())
        except IdentifierError:
            if errors == "raise":
                raise
            rows.append({"input": t, "id_type": "invalid", "identifier": "", "canonical_url": "",
                         "landing_url": "", "verified": False})
    return pd.DataFrame(rows, columns=IDENTIFIER_COLUMNS)


# ---------------------------------------------------------------------------
# works table helpers (shared by openalex.py and crossref.py)
# ---------------------------------------------------------------------------
def _clean_text(value: object) -> str | None:
    """``None`` for missing/blank values, else the stripped string."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    s = " ".join(str(value).split())
    return s or None


def empty_works_frame() -> pd.DataFrame:
    """An empty works table with the documented dtypes."""
    return make_works_frame([])


def make_works_frame(records: list[dict]) -> pd.DataFrame:
    """Build a works table from row dicts (missing keys become missing values).

    Dtypes: text columns ``object`` (``None`` when missing), ``year`` and
    ``cited_by_count`` nullable ``Int64``, ``is_oa`` nullable ``boolean``.
    Row order is kept (resolver rank order for searches).
    """
    df = pd.DataFrame.from_records(records, columns=WORKS_COLUMNS) if records else pd.DataFrame(columns=WORKS_COLUMNS)
    for col in _WORKS_STR:
        df[col] = pd.Series([_clean_text(v) for v in df[col]], index=df.index, dtype="object")
    df["year"] = pd.to_numeric(df["year"], errors="coerce").astype("Int64")
    df["cited_by_count"] = pd.to_numeric(df["cited_by_count"], errors="coerce").astype("Int64")
    df["is_oa"] = df["is_oa"].astype("boolean")
    return df.reset_index(drop=True)


def validate_works_frame(df: pd.DataFrame) -> None:
    """Raise ValueError if ``df`` is not a well-formed works table."""
    if list(df.columns) != WORKS_COLUMNS:
        raise ValueError(f"works table must have columns {WORKS_COLUMNS}, got {list(df.columns)}")
    if str(df["year"].dtype) != "Int64" or str(df["cited_by_count"].dtype) != "Int64":
        raise ValueError("year and cited_by_count must be nullable Int64")
    if str(df["is_oa"].dtype) != "boolean":
        raise ValueError("is_oa must be nullable boolean")
    for doi in df["doi"].dropna():
        if doi != doi.lower() or not DOI_RE.match(doi):
            raise ValueError(f"DOI not normalized: {doi!r}")
    for wid in df["openalex_id"].dropna():
        if not re.fullmatch(r"W\d+", wid):
            raise ValueError(f"OpenAlex ID not normalized: {wid!r}")


def contact_email(value: str | None = None) -> str | None:
    """Contact address for polite API use: ``value`` or ``$CGJRKIT_CONTACT_EMAIL``.

    Returns None when neither is set; raises ValueError for a malformed address.
    """
    email = (value if value is not None else os.environ.get(CONTACT_ENV, "")).strip()
    if not email:
        return None
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError(f"{CONTACT_ENV} (or contact_email) is not an email address: {email!r}")
    return email


def polite_user_agent(email: str | None) -> str:
    """User-Agent identifying the toolkit, with ``mailto:`` when an address is known."""
    if email:
        return f"cgjrkit/{__version__} (research toolkit; mailto:{email})"
    return f"cgjrkit/{__version__} (research toolkit)"


def returned_ids(df: pd.DataFrame) -> list[str]:
    """Identifiers in a works table that callers may cite (OpenAlex keys and DOIs), in row order."""
    out: list[str] = []
    for wid, doi in zip(df["openalex_id"], df["doi"]):
        for v in (wid, doi):
            if v is not None and not pd.isna(v) and v not in out:
                out.append(v)
    return out
