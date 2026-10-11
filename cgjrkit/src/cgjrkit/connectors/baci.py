"""CEPII BACI connector: reconciled bilateral trade at HS6 (bulk zip per HS revision)
with DuckDB aggregation helpers (exporter totals, export market and product
concentration, Balassa RCA, product counts).

Download (keyless ``GET``, one zip per HS revision and release)::

    https://www.cepii.fr/DATA_DOWNLOAD/baci/data/BACI_{HS}_V{version}.zip

``HS`` is ``HS92`` (1995-), ``HS96``, ``HS02``, ``HS07``, ``HS12``, ``HS17`` or
``HS22`` (2022-); ``version`` is the release stamp ``YYYYMM`` (current:
``202601``, released 2026-01-22; a letter suffix such as ``202401b`` marks a
corrected release). Each zip holds:

* ``BACI_{HS}_Y{year}_V{version}.csv``: one file per year, columns ``t,i,j,k,v,q``
  (year, exporter, importer, HS6 product, value in **thousand current US$**,
  quantity in **metric tons**; ``k`` is text with leading zeros; ``q`` can be
  empty or ``NA``);
* ``country_codes_V{version}.csv``: ``country_code, country_name, country_iso2,
  country_iso3`` (statistical areas such as 490 "Other Asia, nes" carry codes like
  ``S19``; Namibia's ISO2 is the string ``NA``);
* ``product_codes_{HS}_V{version}.csv``: ``code, description``;
* ``Readme.txt``: ``Version``, ``Release Date``, ``Content``, ``List of Variables``.

Sizes (V202601, third-party measurements): HS92 2.4 GB zipped (8.7 GB of CSV),
HS17 0.8 GB, HS22 about 0.3 GB. ``core.http.fetch`` holds the zip in memory once
while caching it (see the core change request in ``docs/apis/baci.md``); a zip
downloaded by hand can be used with ``local_zip=``. Year files are then streamed
out of the cached zip one at a time and converted with DuckDB to Parquet under
``<cache>/baci/<HS>_V<version>_<sha16>/`` (values as ``DECIMAL(18,3)``, so every
sum is exact and results do not depend on thread scheduling). Nothing is loaded
whole into pandas; only aggregated results are.

Licence: Etalab Open Licence 2.0 (any use, commercial included, with
attribution). Cite Gaulier, G. and Zignago, S. (2010), "BACI: International
Trade Database at the Product-Level. The 1994-2007 Version", CEPII Working
Paper 2010-23, and the version used. Do not mix BACI versions.

Design: :func:`build_request`, :func:`archive_members`, :func:`parse_readme`,
:func:`read_country_codes`, :func:`read_product_codes` and the ``q_*`` SQL
helpers (read-only over Parquet files) are pure; :func:`extract_archive` writes
the Parquet store; :class:`BACI` wires everything through
:func:`cgjrkit.core.http.fetch`.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import re
import shutil
import zipfile
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from cgjrkit.connectors.base import Connector
from cgjrkit.core import http
from cgjrkit.core.cache import Cache, Retrieval
from cgjrkit.core.caveats import Caveat
from cgjrkit.core.provenance import Provenance, Result, utc_now
from cgjrkit.core.schema import empty_frame, make_frame, validate_frame

BASE_URL = "https://www.cepii.fr/DATA_DOWNLOAD/baci/data/"
DOC_URL = "https://www.cepii.fr/DATA_DOWNLOAD/baci/doc/baci_webpage.html"
DEFAULT_VERSION = "202601"
DEFAULT_HS = "HS92"
#: first year of each HS revision in BACI
HS_FIRST_YEAR: dict[str, int] = {"HS92": 1995, "HS96": 1996, "HS02": 2002, "HS07": 2007, "HS12": 2012,
                                 "HS17": 2017, "HS22": 2022}
VALUE_UNIT = "thousand current US$"
LICENSE = "Etalab Open Licence 2.0 (commercial use allowed, with attribution)"
CITATION = "CEPII BACI (Gaulier and Zignago, 2010)"
TRADE_COLUMNS = ["t", "i", "j", "k", "v", "q"]
LEVELS = (2, 4, 6)

_VERSION_RE = re.compile(r"^V?(\d{6}[a-z]?)$", re.I)
_TRADE_RE = re.compile(r"^BACI_(HS\d\d)_Y(\d{4})_V(\d{6}[a-z]?)\.csv$", re.I)
_COUNTRY_RE = re.compile(r"^country_codes_V(\d{6}[a-z]?)\.csv$", re.I)
_PRODUCT_RE = re.compile(r"^product_codes_(HS\d\d)_V(\d{6}[a-z]?)\.csv$", re.I)
_ISO3_RE = re.compile(r"^[A-Z]{3}$")
#: archives with a large download; a log warning precedes it. Only HS92 (2,417,732,497 bytes, V202601) is
#: measured; HS96-HS12 span more years than HS17 (0.8 GB), so they are flagged too, without a size.
LARGE_ARCHIVES: dict[str, str] = {"HS92": "2.4 GB", "HS96": "size not measured, larger than HS17's 0.8 GB",
                                  "HS02": "size not measured, larger than HS17's 0.8 GB",
                                  "HS07": "size not measured, larger than HS17's 0.8 GB",
                                  "HS12": "size not measured, larger than HS17's 0.8 GB"}
_log = logging.getLogger(__name__)


class BACIError(RuntimeError):
    """A BACI archive, member or request is invalid."""


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------
def normalize_hs(hs: str) -> str:
    """``"HS92"``, ``"92"``, ``"hs1992"`` -> ``"HS92"``."""
    s = str(hs).strip().upper().removeprefix("HS")
    if len(s) == 4 and s.isdigit():
        s = s[2:]
    code = f"HS{s.zfill(2)}"
    if code not in HS_FIRST_YEAR:
        raise ValueError(f"unknown HS revision {hs!r}; use one of {sorted(HS_FIRST_YEAR)}")
    return code


def normalize_version(version: str | int) -> str:
    """``"202601"``, ``"V202601"``, ``"202401b"`` -> the bare release stamp."""
    m = _VERSION_RE.match(str(version).strip())
    if not m:
        raise ValueError(f"invalid BACI version {version!r}; expected YYYYMM, optionally with a letter (202401b)")
    return m.group(1).lower()


def build_request(hs: str = DEFAULT_HS, version: str | int = DEFAULT_VERSION) -> tuple[str, dict]:
    """URL of the zip for one HS revision and release."""
    return f"{BASE_URL}BACI_{normalize_hs(hs)}_V{normalize_version(version)}.zip", {}


@dataclass
class ArchiveMembers:
    hs: str
    version: str
    trade: dict[int, str]          # year -> member name
    country_codes: str
    product_codes: str
    readme: str = ""


def archive_members(names: Iterable[str]) -> ArchiveMembers:
    """Classify the members of a BACI zip; raise if the layout is not BACI's."""
    trade: dict[int, str] = {}
    hs = version = ""
    country = product = readme = ""
    for name in names:
        base = name.rsplit("/", 1)[-1]
        if m := _TRADE_RE.match(base):
            if hs and (m.group(1).upper() != hs or m.group(3).lower() != version):
                raise BACIError(f"archive mixes HS revisions or versions: {base}")
            hs, version = m.group(1).upper(), m.group(3).lower()
            trade[int(m.group(2))] = name
        elif _COUNTRY_RE.match(base):
            country = name
        elif _PRODUCT_RE.match(base):
            product = name
        elif base.lower() == "readme.txt":
            readme = name
    if not trade:
        raise BACIError("no BACI_<HS>_Y<year>_V<version>.csv members in the archive")
    if not country or not product:
        raise BACIError("the archive lacks country_codes_V*.csv or product_codes_HS*_V*.csv")
    return ArchiveMembers(hs, version, dict(sorted(trade.items())), country, product, readme)


def parse_readme(text: str) -> dict[str, str]:
    """``key: value`` blocks of ``Readme.txt`` (``Version``, ``Release Date``, ``Content``...)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    out: dict[str, str] = {}
    for block in (b.strip() for b in text.split("\n\n")):
        if not block or block.startswith("List of Variables"):
            continue
        lines = block.splitlines()
        if ":" in lines[0]:
            key, first = lines[0].split(":", 1)
            out[key.strip()] = " ".join([first.strip()] + [ln.strip() for ln in lines[1:]]).strip()
    return out


def _fix_text(value: str) -> str:
    """Undo UTF-8 text that was decoded as latin-1 (seen in some BACI releases)."""
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


def read_country_codes(payload: bytes) -> pd.DataFrame:
    """``code`` (int), ``name``, ``iso2``, ``iso3`` from ``country_codes_V*.csv``."""
    df = pd.read_csv(io.BytesIO(payload), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df.columns = [c.strip().lower() for c in df.columns]
    need = {"country_code", "country_name", "country_iso3"}
    if not need <= set(df.columns):
        raise BACIError(f"country codes file lacks {sorted(need - set(df.columns))}")
    out = pd.DataFrame({
        "code": pd.to_numeric(df["country_code"], errors="coerce").astype("Int64"),
        "name": df["country_name"].map(lambda s: _fix_text(str(s).strip())),
        "iso2": df.get("country_iso2", pd.Series("", index=df.index)).str.strip(),
        "iso3": df["country_iso3"].str.strip().str.upper(),
    }).dropna(subset=["code"])
    out["code"] = out["code"].astype(int)
    return out.drop_duplicates("code").sort_values("code").reset_index(drop=True)


def read_product_codes(payload: bytes) -> pd.DataFrame:
    """``code`` (6-digit text) and ``description`` from ``product_codes_HS*_V*.csv``."""
    df = pd.read_csv(io.BytesIO(payload), dtype=str, keep_default_na=False, encoding="utf-8-sig")
    df.columns = [c.strip().lower() for c in df.columns]
    if not {"code", "description"} <= set(df.columns):
        raise BACIError("product codes file lacks code/description")
    out = pd.DataFrame({"code": df["code"].str.strip().str.zfill(6),
                        "description": df["description"].map(lambda s: _fix_text(str(s).strip()))})
    return out.drop_duplicates("code").sort_values("code").reset_index(drop=True)


# ---------------------------------------------------------------------------
# extraction (zip member -> Parquet), streaming
# ---------------------------------------------------------------------------
def sha256_file(path: Path | str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


#: per-process memo of local-zip hashes: (resolved path, size, mtime_ns) -> sha256
_LOCAL_SHA_MEMO: dict[tuple[str, int, int], str] = {}


def _local_zip_sha256(path: Path) -> str:
    """sha256 of a user-supplied zip, hashed once per file state (path, size, mtime)."""
    st = path.stat()
    key = (str(path.resolve()), st.st_size, st.st_mtime_ns)
    if key not in _LOCAL_SHA_MEMO:
        _LOCAL_SHA_MEMO[key] = sha256_file(path)
    return _LOCAL_SHA_MEMO[key]


def _csv_to_parquet_sql(src: str, dst: str) -> str:
    cols = ", ".join(f"'{c}': 'VARCHAR'" for c in TRADE_COLUMNS)
    src_q, dst_q = src.replace("'", "''"), dst.replace("'", "''")
    return (
        "COPY (SELECT CAST(trim(t) AS INTEGER) AS t, CAST(trim(i) AS INTEGER) AS i, CAST(trim(j) AS INTEGER) AS j, "
        "lpad(trim(k), 6, '0') AS k, CAST(trim(v) AS DECIMAL(18,3)) AS v, "
        "TRY_CAST(NULLIF(trim(q), 'NA') AS DOUBLE) AS q "
        f"FROM read_csv('{src_q}', header=true, delim=',', quote='\"', columns={{{cols}}})) "
        f"TO '{dst_q}' (FORMAT PARQUET)"
    )


def extract_archive(zip_path: Path | str, dest: Path | str, years: Iterable[int] | None = None,
                    zip_sha256: str = "") -> dict:
    """Extract the requested years (all when ``None``) of a BACI zip to Parquet in ``dest``.

    Each year file is streamed to a temporary CSV, checked for the ``t,i,j,k,v,q``
    header, converted with DuckDB and deleted. Code lists and the readme are copied.
    Already-extracted years are skipped. Returns the manifest (also written to
    ``dest/manifest.json``).
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    manifest_path = dest / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    with zipfile.ZipFile(zip_path) as zf:
        members = archive_members(zf.namelist())
        manifest.update({"hs": members.hs, "version": members.version, "zip_sha256": zip_sha256 or manifest.get("zip_sha256", ""),
                         "available_years": sorted(members.trade)})
        manifest.setdefault("years", {})
        for name, target in ((members.country_codes, "country_codes.csv"), (members.product_codes, "product_codes.csv"),
                             (members.readme, "Readme.txt")):
            if name and not (dest / target).exists():
                (dest / target).write_bytes(zf.read(name))
        wanted = sorted(members.trade) if years is None else sorted({int(y) for y in years})
        missing = [y for y in wanted if y not in members.trade]
        if missing:
            raise BACIError(f"years {missing} are not in BACI {members.hs} V{members.version} "
                            f"(available {min(members.trade)}-{max(members.trade)})")
        con = duckdb.connect()
        try:
            for year in wanted:
                pq_name = f"trade_Y{year}.parquet"
                if str(year) in manifest["years"] and (dest / pq_name).exists():
                    continue
                tmp_csv = dest / f".tmp_Y{year}.csv"
                with zf.open(members.trade[year]) as src, open(tmp_csv, "wb") as out:
                    shutil.copyfileobj(src, out, length=1 << 20)
                with open(tmp_csv, encoding="utf-8-sig") as fh:
                    header = [h.strip().lower() for h in fh.readline().split(",")]
                if header != TRADE_COLUMNS:
                    tmp_csv.unlink(missing_ok=True)
                    raise BACIError(f"{members.trade[year]}: header {header} is not {TRADE_COLUMNS}")
                tmp_pq = dest / f".tmp_{pq_name}"
                try:
                    con.execute(_csv_to_parquet_sql(str(tmp_csv), str(tmp_pq)))
                finally:
                    tmp_csv.unlink(missing_ok=True)
                rows = con.execute(f"SELECT count(*), min(t), max(t) FROM read_parquet('{str(tmp_pq)}')").fetchone()
                if rows[0] and (rows[1] != year or rows[2] != year):
                    tmp_pq.unlink(missing_ok=True)
                    raise BACIError(f"{members.trade[year]}: column t ({rows[1]}-{rows[2]}) does not match the file year")
                tmp_pq.replace(dest / pq_name)
                manifest["years"][str(year)] = {"member": members.trade[year], "parquet": pq_name, "rows": int(rows[0])}
        finally:
            con.close()
    manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    return manifest


@dataclass
class BaciTables:
    """An extracted BACI release: Parquet year files plus code lists."""

    dest: Path
    manifest: dict
    countries: pd.DataFrame
    products: pd.DataFrame
    readme: dict = field(default_factory=dict)

    @property
    def hs(self) -> str:
        return self.manifest["hs"]

    @property
    def version(self) -> str:
        return self.manifest["version"]

    def files(self, years: Iterable[int]) -> list[str]:
        out = []
        for y in sorted({int(v) for v in years}):
            info = self.manifest["years"].get(str(y))
            if not info:
                raise BACIError(f"year {y} is not extracted")
            out.append(str(self.dest / info["parquet"]))
        return out

    @classmethod
    def load(cls, dest: Path | str) -> BaciTables:
        dest = Path(dest)
        manifest = json.loads((dest / "manifest.json").read_text())
        countries = read_country_codes((dest / "country_codes.csv").read_bytes())
        products = read_product_codes((dest / "product_codes.csv").read_bytes())
        readme = parse_readme((dest / "Readme.txt").read_text(encoding="utf-8", errors="replace")) \
            if (dest / "Readme.txt").exists() else {}
        return cls(dest, manifest, countries, products, readme)


# ---------------------------------------------------------------------------
# SQL helpers (read-only over Parquet files)
# ---------------------------------------------------------------------------
def _flows(files: Sequence[str]) -> str:
    if not files:
        raise BACIError("no Parquet files given")
    return "read_parquet([" + ", ".join("'" + f.replace("'", "''") + "'" for f in files) + "])"


def _code_filter(column: str, codes: Sequence[int] | None) -> str:
    if codes is None:
        return "TRUE"
    if not codes:
        return "FALSE"
    return f"{column} IN ({', '.join(str(int(c)) for c in sorted(set(codes)))})"


def _product_expr(level: int) -> str:
    if level not in LEVELS:
        raise ValueError(f"level must be one of {LEVELS}")
    return "k" if level == 6 else f"left(k, {level})"


def q_exporter_totals(con: duckdb.DuckDBPyConnection, files: Sequence[str], codes: Sequence[int] | None = None) -> pd.DataFrame:
    """Per exporter-year: total exports (exact), HS6 products and destinations with positive value."""
    sql = (f"SELECT i AS code, t AS year, CAST(sum(v) AS DOUBLE) AS value, "
           f"count(DISTINCT CASE WHEN v > 0 THEN k END) AS n_products, "
           f"count(DISTINCT CASE WHEN v > 0 THEN j END) AS n_partners "
           f"FROM {_flows(files)} WHERE {_code_filter('i', codes)} GROUP BY i, t ORDER BY i, t")
    return con.execute(sql).df()


def q_importer_totals(con: duckdb.DuckDBPyConnection, files: Sequence[str], codes: Sequence[int] | None = None) -> pd.DataFrame:
    """Per importer-year: total imports (exact, FOB-reconciled) and origins with positive value."""
    sql = (f"SELECT j AS code, t AS year, CAST(sum(v) AS DOUBLE) AS value, "
           f"count(DISTINCT CASE WHEN v > 0 THEN k END) AS n_products, "
           f"count(DISTINCT CASE WHEN v > 0 THEN i END) AS n_partners "
           f"FROM {_flows(files)} WHERE {_code_filter('j', codes)} GROUP BY j, t ORDER BY j, t")
    return con.execute(sql).df()


def _hhi_sql(files: Sequence[str], codes: Sequence[int] | None, unit_expr: str) -> str:
    # exact DECIMAL sums of x and x^2; HHI = sum(x^2) / (sum x)^2
    return (
        f"WITH cells AS (SELECT i, t, {unit_expr} AS unit, sum(v) AS x FROM {_flows(files)} "
        f"WHERE {_code_filter('i', codes)} GROUP BY i, t, unit HAVING sum(v) > 0) "
        "SELECT i AS code, t AS year, count(*) AS n_units, "
        "CAST(sum(CAST(x AS DECIMAL(38,3)) * CAST(x AS DECIMAL(38,3))) AS DOUBLE) / "
        "(CAST(sum(x) AS DOUBLE) * CAST(sum(x) AS DOUBLE)) AS hhi, "
        "CAST(max(x) AS DOUBLE) / CAST(sum(x) AS DOUBLE) AS top_share, CAST(sum(x) AS DOUBLE) AS total "
        "FROM cells GROUP BY i, t ORDER BY i, t"
    )


def q_market_hhi(con: duckdb.DuckDBPyConnection, files: Sequence[str], codes: Sequence[int] | None = None) -> pd.DataFrame:
    """Export-market concentration: HHI (0-1) of destination shares, markets, top-market share."""
    return con.execute(_hhi_sql(files, codes, "j")).df()


def q_product_hhi(con: duckdb.DuckDBPyConnection, files: Sequence[str], codes: Sequence[int] | None = None,
                  level: int = 6) -> pd.DataFrame:
    """Export-product concentration: HHI (0-1) of product shares at HS ``level`` digits."""
    return con.execute(_hhi_sql(files, codes, _product_expr(level))).df()


def q_rca(con: duckdb.DuckDBPyConnection, files: Sequence[str], codes: Sequence[int] | None = None, level: int = 6,
          products: Sequence[str] | None = None) -> pd.DataFrame:
    """Balassa RCA = (x_cp / X_c) / (x_wp / X_w); world = all BACI exporters that year.

    Returns ``code, year, product, value, rca`` for the selected exporters, products
    with positive exports only.
    """
    pexpr = _product_expr(level)
    pfilter = "TRUE"
    if products:
        clean = sorted({str(p).strip() for p in products})
        pfilter = "product IN (" + ", ".join("'" + p.replace("'", "''") + "'" for p in clean) + ")"
    sql = (
        f"WITH cp AS (SELECT i, t, {pexpr} AS product, sum(v) AS x FROM {_flows(files)} GROUP BY i, t, product "
        "HAVING sum(v) > 0), "
        "c AS (SELECT i, t, sum(x) AS xc FROM cp GROUP BY i, t), "
        "w AS (SELECT t, product, sum(x) AS xw FROM cp GROUP BY t, product), "
        "tot AS (SELECT t, sum(x) AS xt FROM cp GROUP BY t) "
        "SELECT cp.i AS code, cp.t AS year, cp.product, CAST(cp.x AS DOUBLE) AS value, "
        "(CAST(cp.x AS DOUBLE) / CAST(c.xc AS DOUBLE)) / (CAST(w.xw AS DOUBLE) / CAST(tot.xt AS DOUBLE)) AS rca "
        "FROM cp JOIN c USING (i, t) JOIN w USING (t, product) JOIN tot USING (t) "
        f"WHERE {_code_filter('cp.i', codes)} AND {pfilter} ORDER BY code, year, product"
    )
    return con.execute(sql).df()


def q_count_products(con: duckdb.DuckDBPyConnection, files: Sequence[str], codes: Sequence[int] | None = None,
                     level: int = 6, min_value: float | None = None, min_rca: float | None = None) -> pd.DataFrame:
    """Number of products per exporter-year with exports above ``min_value`` (thousand US$)
    and/or RCA at or above ``min_rca``."""
    if min_value is None and min_rca is None:
        raise ValueError("give min_value (thousand US$) and/or min_rca")
    df = q_rca(con, files, codes, level)
    mask = pd.Series(True, index=df.index)
    if min_value is not None:
        mask &= df["value"] > float(min_value)
    if min_rca is not None:
        mask &= df["rca"] >= float(min_rca)
    counted = df[mask].groupby(["code", "year"]).size().rename("n").reset_index()
    base = df[["code", "year"]].drop_duplicates()
    out = base.merge(counted, on=["code", "year"], how="left").fillna({"n": 0})
    out["n"] = out["n"].astype(int)
    return out.sort_values(["code", "year"]).reset_index(drop=True)


def q_country_product(con: duckdb.DuckDBPyConnection, files: Sequence[str], level: int = 4,
                      codes: Sequence[int] | None = None) -> pd.DataFrame:
    """Exporter x product x year export values (exact sums) for complexity methods."""
    pexpr = _product_expr(level)
    sql = (f"SELECT i AS code, t AS year, {pexpr} AS product, CAST(sum(v) AS DOUBLE) AS value "
           f"FROM {_flows(files)} WHERE {_code_filter('i', codes)} GROUP BY i, t, product HAVING sum(v) > 0 "
           "ORDER BY code, year, product")
    return con.execute(sql).df()


# ---------------------------------------------------------------------------
# canonical frames
# ---------------------------------------------------------------------------
def _country_lookup(countries: pd.DataFrame) -> dict[int, tuple[str, str]]:
    out = {}
    for code, iso3, name in zip(countries["code"], countries["iso3"], countries["name"]):
        out[int(code)] = (iso3 if iso3 else f"BACI{int(code)}", name)
    return out


def codes_for(countries: pd.DataFrame, wanted: Sequence[str] | str | None) -> list[int] | None:
    """BACI numeric codes for ISO3 codes (or numeric codes); ``None`` = all."""
    if wanted is None:
        return None
    items = [wanted] if isinstance(wanted, str) else list(wanted)
    flat = [p.strip().upper() for item in items for p in str(item).split(",") if p.strip()]
    out: set[int] = set()
    unknown = []
    for item in flat:
        if item.isdigit():
            out.add(int(item))
            continue
        hits = countries.loc[countries["iso3"] == item, "code"].tolist()
        if not hits:
            unknown.append(item)
        out.update(int(h) for h in hits)
    if unknown:
        raise ValueError(f"not in the BACI country list: {unknown}")
    return sorted(out)


def to_frame(df: pd.DataFrame, countries: pd.DataFrame, columns: dict[str, tuple[str, str, str]],
             dataset: str) -> tuple[pd.DataFrame, dict]:
    """Canonical frame from an aggregated table with ``code, year`` and value columns.

    ``columns``: value column -> (indicator, label, unit).
    """
    lookup = _country_lookup(countries)
    records = []
    unmapped: set[int] = set()
    for row in df.itertuples(index=False):
        code = int(row.code)
        iso3, name = lookup.get(code, (f"BACI{code}", ""))
        if code not in lookup:
            unmapped.add(code)
        for col, (ind, label, unit) in columns.items():
            val = getattr(row, col)
            if pd.isna(val):
                continue
            records.append({"indicator": ind, "indicator_label": label, "iso3": iso3, "country": name,
                            "period": str(int(row.year)), "value": float(val), "unit": unit})
    frame = make_frame(records, source="baci", dataset=dataset) if records else empty_frame()
    frame = Connector.finalize(frame)
    areas = sorted({c for c in frame["iso3"].unique() if not _ISO3_RE.match(c)})
    return frame, {"unmapped_codes": sorted(unmapped), "aggregates": areas}


def baci_mix_caveats(objs: pd.DataFrame | Result | Sequence[pd.DataFrame | Result]) -> list[Caveat]:
    """Block combining BACI rows from different releases or HS revisions."""
    items = [objs] if isinstance(objs, (pd.DataFrame, Result)) else list(objs)
    frames = [o.data if isinstance(o, Result) else o for o in items]
    rows = pd.concat([f[f["source"] == "baci"] for f in frames if len(f)], ignore_index=True) if frames else empty_frame()
    datasets = sorted(set(rows["dataset"])) if len(rows) else []
    if len(datasets) > 1:
        return [Caveat("baci_mixed_versions", "block",
                       "BACI rows from different releases or HS revisions are combined; CEPII advises against mixing "
                       "versions (the same flow can differ). Rebuild from one release.", {"datasets": datasets})]
    return []


# ---------------------------------------------------------------------------
# connector
# ---------------------------------------------------------------------------
class BACI(Connector):
    """CEPII BACI bulk files with DuckDB aggregation helpers."""

    source = "baci"
    license = LICENSE
    citation = CITATION
    domains = ("www.cepii.fr",)

    def __init__(self, cache: Cache | None = None, store_root: Path | str | None = None):
        super().__init__(cache)
        self.store_root = Path(store_root) if store_root else self.cache.root / "baci"

    # -- archive and store ------------------------------------------------------
    def cached_retrieval(self, url: str, params: dict, refresh: bool = False) -> Retrieval | None:
        """The retrieval ``http.fetch`` would serve from the cache, without reading the payload.

        Same order as :func:`cgjrkit.core.http.fetch`: the pin of the active
        ``CGJRKIT_SNAPSHOT`` first, then the latest cached retrieval unless
        ``refresh``. ``http.fetch`` returns the cached bytes (the whole zip, 2.4 GB
        for HS92); BACI only needs the object's path, so cache hits skip it.
        """
        snapshot = os.environ.get("CGJRKIT_SNAPSHOT", "")
        if snapshot:
            pinned = self.cache.pinned(snapshot, self.source, url, dict(params))
            if pinned:
                return pinned
        if not refresh:
            return self.cache.latest(self.source, url, dict(params))
        return None

    def archive(self, hs: str = DEFAULT_HS, version: str | int = DEFAULT_VERSION, *, refresh: bool = False,
                local_zip: Path | str | None = None) -> tuple[Path, Retrieval]:
        """Path of the BACI zip (downloaded and cached, or ``local_zip``) and its retrieval record.

        Cache hits never read the zip: the path ``<cache>/objects/<sha256>`` is
        returned directly. ``http.fetch`` runs only on a cache miss (or ``refresh``).
        A ``local_zip`` is hashed once per process and file state (path, size, mtime).
        """
        url, params = build_request(hs, version)
        if local_zip is not None:
            path = Path(local_zip)
            if not path.exists():
                raise BACIError(f"local zip not found: {path}")
            sha = _local_zip_sha256(path)
            return path, Retrieval(key="local", sha256=sha, retrieved_at=utc_now(), url=f"file://{path.resolve()}",
                                   params={"downloaded_from": url}, source=self.source)
        retrieval = self.cached_retrieval(url, params, refresh)
        if retrieval is None:
            hs_code = normalize_hs(hs)
            offline = os.environ.get("CGJRKIT_OFFLINE", "") not in ("", "0", "false")  # same test as core.http
            if hs_code in LARGE_ARCHIVES and not offline:
                _log.warning("BACI %s is a large download (%s); core.http.fetch holds it in memory while "
                             "caching it. Use local_zip= for a file downloaded by hand, or a later HS revision "
                             "(HS17/HS22) when the years allow.", hs_code, LARGE_ARCHIVES[hs_code])
            try:
                payload, retrieval = http.fetch(self.source, url, params, cache=self.cache, refresh=refresh,
                                                timeout=7200.0)
            except http.FetchError as exc:
                raise BACIError(f"BACI download failed ({url}): {exc}") from exc
            del payload  # the cache keeps the bytes under objects/<sha256>
        path = self.cache.root / "objects" / retrieval.sha256
        if not path.exists():
            raise BACIError(f"cached BACI object missing: {path}; re-download with refresh=True")
        return path, retrieval

    def tables(self, hs: str = DEFAULT_HS, version: str | int = DEFAULT_VERSION, years: Iterable[int] | None = None,
               *, refresh: bool = False, local_zip: Path | str | None = None) -> tuple[BaciTables, Retrieval]:
        """Extract (once) and open the requested years. ``years=None`` = the latest year in the zip."""
        hs_code, ver = normalize_hs(hs), normalize_version(version)
        zip_path, retrieval = self.archive(hs_code, ver, refresh=refresh, local_zip=local_zip)
        dest = self.store_root / f"{hs_code}_V{ver}_{retrieval.sha256[:16]}"
        if years is None:
            with zipfile.ZipFile(zip_path) as zf:
                members = archive_members(zf.namelist())
            years = [max(members.trade)]
        years = sorted({int(y) for y in years})
        first = HS_FIRST_YEAR[hs_code]
        early = [y for y in years if y < first]
        if early:
            raise ValueError(f"{hs_code} starts in {first}; years {early} need an older HS revision (HS92 from 1995)")
        manifest = extract_archive(zip_path, dest, years, zip_sha256=retrieval.sha256)
        if manifest["hs"] != hs_code or manifest["version"] != ver:
            raise BACIError(f"archive holds {manifest['hs']} V{manifest['version']}, not {hs_code} V{ver}")
        return BaciTables.load(dest), retrieval

    # -- provenance and results ---------------------------------------------------
    def _result(self, frame: pd.DataFrame, meta: dict, tables: BaciTables, retrieval: Retrieval, tool: str,
                params: dict, extra_caveats: list[Caveat] | None = None) -> Result:
        validate_frame(frame)
        release = tables.readme.get("Release Date", "")
        notes = [f"zip sha256 {retrieval.sha256}", "values in thousand current US$ (FOB, reconciled mirror flows)"]
        if release:
            notes.append(f"release date {release}")
        if retrieval.key == "local":
            notes.append("local zip supplied by the user")
        prov = self.provenance(retrieval, dataset=f"BACI {tables.hs}", vintage=f"BACI V{tables.version}",
                               tool="BACI.tables", notes=notes)
        prov.citation = f"{CITATION}, version {tables.version}, {tables.hs}"
        method = Provenance(source="cgjrkit", dataset=f"BACI {tables.hs} V{tables.version}", tool=tool, params=params,
                            notes=["DuckDB aggregation over Parquet; exact DECIMAL(18,3) sums"])
        caveats = [
            Caveat("baci_units", "info", "BACI values are thousand current US$ (FOB) and quantities metric tons; "
                   "flows are reconciled from exporter and importer declarations.", {"version": tables.version}),
            Caveat("baci_version", "info", "Results depend on the BACI release and HS revision; do not mix releases. "
                   "The latest years of a release are revised in the next one.",
                   {"hs": tables.hs, "version": tables.version}),
        ]
        if meta.get("unmapped_codes"):
            caveats.append(Caveat("baci_unmapped_codes", "warn", "Some BACI country codes are not in the release's "
                                  "country list; they are shown as BACI<code>.", {"codes": meta["unmapped_codes"]}))
        if meta.get("aggregates"):
            caveats.append(Caveat("baci_areas", "info", "Statistical areas without an ISO3 code (e.g. S19 'Other Asia, "
                                  "nes') keep the BACI code.", {"codes": meta["aggregates"]}))
        if not len(frame):
            caveats.append(Caveat("baci_empty", "warn", "No BACI observations matched the request.", {}))
        res = Result(data=frame, provenance=[prov, method], caveats=[],
                     meta={**meta, "hs": tables.hs, "version": tables.version, "release_date": release,
                           "store": str(tables.dest)})
        for c in caveats + (extra_caveats or []):
            res.add_caveat(c)
        return res

    def _run(self, hs: str, version: str | int, years: Iterable[int] | None, refresh: bool,
             local_zip: Path | str | None):
        tables, retrieval = self.tables(hs, version, years, refresh=refresh, local_zip=local_zip)
        if years is None:
            yrs = [max(int(y) for y in tables.manifest["available_years"])]
        else:
            yrs = sorted({int(y) for y in years})
        return tables, retrieval, tables.files(yrs), yrs

    # -- aggregation helpers ---------------------------------------------------------
    def exporter_totals(self, exporters: Sequence[str] | str | None = None, years: Iterable[int] | None = None, *,
                        hs: str = DEFAULT_HS, version: str | int = DEFAULT_VERSION, refresh: bool = False,
                        local_zip: Path | str | None = None) -> Result:
        """Total exports, number of HS6 products and of destinations per exporter-year."""
        tables, retrieval, files, yrs = self._run(hs, version, years, refresh, local_zip)
        codes = codes_for(tables.countries, exporters)
        with duckdb.connect() as con:
            df = q_exporter_totals(con, files, codes)
        frame, meta = to_frame(df, tables.countries, {
            "value": ("exports_total", "Exports of goods, all HS6 products (BACI)", VALUE_UNIT),
            "n_products": ("export_products", "HS6 products exported (value > 0)", "number of products"),
            "n_partners": ("export_markets", "Destination economies (value > 0)", "number of economies"),
        }, f"BACI {tables.hs} V{tables.version}")
        return self._result(frame, meta, tables, retrieval, "BACI.exporter_totals",
                            {"exporters": exporters, "years": yrs, "hs": tables.hs, "version": tables.version})

    def importer_totals(self, importers: Sequence[str] | str | None = None, years: Iterable[int] | None = None, *,
                        hs: str = DEFAULT_HS, version: str | int = DEFAULT_VERSION, refresh: bool = False,
                        local_zip: Path | str | None = None) -> Result:
        """Total imports (FOB-reconciled values) and number of origins per importer-year."""
        tables, retrieval, files, yrs = self._run(hs, version, years, refresh, local_zip)
        codes = codes_for(tables.countries, importers)
        with duckdb.connect() as con:
            df = q_importer_totals(con, files, codes)
        frame, meta = to_frame(df, tables.countries, {
            "value": ("imports_total", "Imports of goods, all HS6 products (BACI, FOB)", VALUE_UNIT),
            "n_partners": ("import_origins", "Origin economies (value > 0)", "number of economies"),
        }, f"BACI {tables.hs} V{tables.version}")
        return self._result(frame, meta, tables, retrieval, "BACI.importer_totals",
                            {"importers": importers, "years": yrs, "hs": tables.hs, "version": tables.version})

    def market_concentration(self, exporters: Sequence[str] | str | None = None, years: Iterable[int] | None = None, *,
                             hs: str = DEFAULT_HS, version: str | int = DEFAULT_VERSION, refresh: bool = False,
                             local_zip: Path | str | None = None) -> Result:
        """Export-market concentration: HHI of destination shares (0-1), number of markets and
        share of the largest market (%)."""
        tables, retrieval, files, yrs = self._run(hs, version, years, refresh, local_zip)
        codes = codes_for(tables.countries, exporters)
        with duckdb.connect() as con:
            df = q_market_hhi(con, files, codes)
        df["top_share"] = df["top_share"] * 100.0
        frame, meta = to_frame(df, tables.countries, {
            "hhi": ("export_market_hhi", "Herfindahl-Hirschman index of export destinations", "index, 0 to 1"),
            "n_units": ("export_markets", "Destination economies (value > 0)", "number of economies"),
            "top_share": ("export_market_top_share", "Share of the largest destination in exports", "% of exports"),
        }, f"BACI {tables.hs} V{tables.version}")
        return self._result(frame, meta, tables, retrieval, "BACI.market_concentration",
                            {"exporters": exporters, "years": yrs, "hs": tables.hs, "version": tables.version},
                            [Caveat("hhi_scale", "info", "HHI is on a 0-1 scale (multiply by 10,000 for the "
                                    "antitrust convention).", {})])

    def product_concentration(self, exporters: Sequence[str] | str | None = None, years: Iterable[int] | None = None,
                              level: int = 6, *, hs: str = DEFAULT_HS, version: str | int = DEFAULT_VERSION,
                              refresh: bool = False, local_zip: Path | str | None = None) -> Result:
        """Export-product concentration: HHI of product shares (0-1) at HS ``level`` digits."""
        tables, retrieval, files, yrs = self._run(hs, version, years, refresh, local_zip)
        codes = codes_for(tables.countries, exporters)
        with duckdb.connect() as con:
            df = q_product_hhi(con, files, codes, level)
        df["top_share"] = df["top_share"] * 100.0
        frame, meta = to_frame(df, tables.countries, {
            "hhi": (f"export_product_hhi_hs{level}", f"Herfindahl-Hirschman index of export products (HS {level}-digit)",
                    "index, 0 to 1"),
            "n_units": (f"export_products_hs{level}", f"Products exported (HS {level}-digit, value > 0)",
                        "number of products"),
            "top_share": (f"export_product_top_share_hs{level}", f"Share of the largest HS {level}-digit product",
                          "% of exports"),
        }, f"BACI {tables.hs} V{tables.version}")
        return self._result(frame, meta, tables, retrieval, "BACI.product_concentration",
                            {"exporters": exporters, "years": yrs, "level": level, "hs": tables.hs,
                             "version": tables.version},
                            [Caveat("hhi_scale", "info", "HHI is on a 0-1 scale (multiply by 10,000 for the "
                                    "antitrust convention).", {})])

    def rca(self, exporters: Sequence[str] | str | None = None, years: Iterable[int] | None = None, level: int = 6,
            products: Sequence[str] | None = None, *, min_rca: float | None = None, hs: str = DEFAULT_HS,
            version: str | int = DEFAULT_VERSION, refresh: bool = False,
            local_zip: Path | str | None = None) -> Result:
        """Balassa revealed comparative advantage by exporter, product (HS ``level`` digits) and year.

        Indicator codes are ``rca_hs<level>:<product code>``; the world is every BACI
        exporter in the year. ``min_rca`` keeps only products at or above it.
        """
        tables, retrieval, files, yrs = self._run(hs, version, years, refresh, local_zip)
        codes = codes_for(tables.countries, exporters)
        with duckdb.connect() as con:
            df = q_rca(con, files, codes, level, products)
        if min_rca is not None:
            df = df[df["rca"] >= float(min_rca)]
        desc = dict(zip(tables.products["code"], tables.products["description"]))
        if level < 6:
            desc = {}
        lookup = _country_lookup(tables.countries)
        unmapped = sorted({int(c) for c in df["code"].unique() if int(c) not in lookup})
        out = pd.DataFrame({
            "indicator": f"rca_hs{level}:" + df["product"].astype(str),
            "indicator_label": [f"Revealed comparative advantage (Balassa), {tables.hs} {p}"
                                + (f": {desc[p]}" if p in desc else "") for p in df["product"].astype(str)],
            "iso3": [lookup.get(int(c), (f"BACI{int(c)}", ""))[0] for c in df["code"]],
            "country": [lookup.get(int(c), ("", ""))[1] for c in df["code"]],
            "period": df["year"].astype(int).astype(str),
            "value": df["rca"].astype(float),
            "unit": "index (RCA >= 1: revealed advantage)",
        })
        frame = Connector.finalize(make_frame(out, source="baci", dataset=f"BACI {tables.hs} V{tables.version}")) \
            if len(out) else empty_frame()
        meta = {"unmapped_codes": unmapped,
                "aggregates": sorted({c for c in frame["iso3"].unique() if not _ISO3_RE.match(c)}),
                "products": int(df["product"].nunique()) if len(df) else 0}
        return self._result(frame, meta, tables, retrieval, "BACI.rca",
                            {"exporters": exporters, "years": yrs, "level": level, "products": products,
                             "min_rca": min_rca, "hs": tables.hs, "version": tables.version})

    def count_products(self, exporters: Sequence[str] | str | None = None, years: Iterable[int] | None = None,
                       level: int = 6, *, min_value: float | None = 100.0, min_rca: float | None = None,
                       hs: str = DEFAULT_HS, version: str | int = DEFAULT_VERSION, refresh: bool = False,
                       local_zip: Path | str | None = None) -> Result:
        """Number of products exported above ``min_value`` (thousand US$; default 100 = US$100,000)
        and/or with RCA at or above ``min_rca``."""
        tables, retrieval, files, yrs = self._run(hs, version, years, refresh, local_zip)
        codes = codes_for(tables.countries, exporters)
        with duckdb.connect() as con:
            df = q_count_products(con, files, codes, level, min_value, min_rca)
        parts, label = [], []
        if min_value is not None:
            parts.append(f"gt{min_value:g}k")
            label.append(f"exports above US${min_value:g} thousand")
        if min_rca is not None:
            parts.append(f"rca{min_rca:g}")
            label.append(f"RCA >= {min_rca:g}")
        frame, meta = to_frame(df, tables.countries, {
            "n": (f"export_products_hs{level}_" + "_".join(parts),
                  f"HS {level}-digit products with " + " and ".join(label), "number of products"),
        }, f"BACI {tables.hs} V{tables.version}")
        return self._result(frame, meta, tables, retrieval, "BACI.count_products",
                            {"exporters": exporters, "years": yrs, "level": level, "min_value": min_value,
                             "min_rca": min_rca, "hs": tables.hs, "version": tables.version})

    def country_product_table(self, years: Iterable[int] | None = None, level: int = 4,
                              exporters: Sequence[str] | str | None = None, *, hs: str = DEFAULT_HS,
                              version: str | int = DEFAULT_VERSION, refresh: bool = False,
                              local_zip: Path | str | None = None) -> Result:
        """Exports by exporter, HS ``level``-digit product and year (input for ECI methods).

        Indicator codes are ``exports_hs<level>:<product>``, values in thousand US$.
        """
        tables, retrieval, files, yrs = self._run(hs, version, years, refresh, local_zip)
        codes = codes_for(tables.countries, exporters)
        with duckdb.connect() as con:
            df = q_country_product(con, files, level, codes)
        lookup = _country_lookup(tables.countries)
        out = pd.DataFrame({
            "indicator": f"exports_hs{level}:" + df["product"].astype(str),
            "indicator_label": f"Exports, {tables.hs} {level}-digit product",
            "iso3": [lookup.get(int(c), (f"BACI{int(c)}", ""))[0] for c in df["code"]],
            "country": [lookup.get(int(c), ("", ""))[1] for c in df["code"]],
            "period": df["year"].astype(int).astype(str),
            "value": df["value"].astype(float),
            "unit": VALUE_UNIT,
        })
        frame = Connector.finalize(make_frame(out, source="baci", dataset=f"BACI {tables.hs} V{tables.version}")) \
            if len(out) else empty_frame()
        meta = {"unmapped_codes": sorted({int(c) for c in df["code"].unique() if int(c) not in lookup}),
                "aggregates": sorted({c for c in frame["iso3"].unique() if not _ISO3_RE.match(c)})}
        return self._result(frame, meta, tables, retrieval, "BACI.country_product_table",
                            {"exporters": exporters, "years": yrs, "level": level, "hs": tables.hs,
                             "version": tables.version})

    def fetch(self, exporters: Sequence[str] | str | None = None, years: Iterable[int] | None = None,
              **kwargs: Any) -> Result:
        """Alias of :meth:`exporter_totals`."""
        return self.exporter_totals(exporters, years, **kwargs)
