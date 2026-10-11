"""Canonical tidy data frame shared by every connector and method.

Every connector returns observations in this long format so that methods,
charts and caveat checks can be written once.
"""

from __future__ import annotations

import pandas as pd

COLUMNS = [
    "source",          # data provider key, e.g. "worldbank", "imf", "ilostat"
    "dataset",         # dataset within the provider, e.g. "WDI", "WEO"
    "indicator",       # provider indicator code, e.g. "NY.GDP.MKTP.KD.ZG"
    "indicator_label", # human-readable indicator name
    "iso3",            # ISO 3166-1 alpha-3 code, or a provider aggregate code (e.g. "LMC")
    "country",         # country or aggregate name
    "period",          # string period: "2024", "2024Q1", "2024-03", or "FY2024/25"
    "value",           # float; NaN when missing
    "unit",            # unit label, e.g. "% of GDP", "constant 2015 US$"
    "obs_status",      # provider flag: "" | "estimate" | "forecast" | "modelled" | "provisional" | ...
]

REQUIRED = ["source", "indicator", "iso3", "period", "value"]


def empty_frame() -> pd.DataFrame:
    """Return an empty frame with the canonical columns and dtypes."""
    df = pd.DataFrame({c: pd.Series(dtype="object") for c in COLUMNS})
    df["value"] = df["value"].astype("float64")
    return df


def make_frame(records: list[dict] | pd.DataFrame, **defaults: str) -> pd.DataFrame:
    """Build a canonical frame from records, filling missing columns.

    ``defaults`` fill columns that are constant for a request (for example
    ``source="worldbank", dataset="WDI"``) when a record does not set them.
    """
    df = pd.DataFrame(records) if not isinstance(records, pd.DataFrame) else records.copy()
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = defaults.get(col, "")
        elif col in defaults:
            df[col] = df[col].where(df[col].notna() & (df[col] != ""), defaults[col])
    df = df[COLUMNS]
    df["value"] = pd.to_numeric(df["value"], errors="coerce").astype("float64")
    for col in COLUMNS:
        if col != "value":
            df[col] = df[col].fillna("").astype(str)
    return df.reset_index(drop=True)


def validate_frame(df: pd.DataFrame) -> None:
    """Raise ValueError if ``df`` does not follow the canonical schema."""
    missing = [c for c in COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"frame is missing columns: {missing}")
    for col in REQUIRED:
        if col != "value" and (df[col].astype(str) == "").any():
            raise ValueError(f"column {col!r} has empty values")
    if not pd.api.types.is_float_dtype(df["value"]):
        raise ValueError("column 'value' must be float")
    dupes = df.duplicated(subset=["source", "dataset", "indicator", "iso3", "period"])
    if dupes.any():
        raise ValueError(f"frame has {int(dupes.sum())} duplicate observations")


def to_wide(df: pd.DataFrame, index: str = "period", columns: str = "iso3") -> pd.DataFrame:
    """Pivot a single-indicator frame to wide format (periods x countries)."""
    if df["indicator"].nunique() > 1:
        raise ValueError("to_wide expects a single indicator; filter first")
    return df.pivot_table(index=index, columns=columns, values="value", aggfunc="first").sort_index()


def year_of(period: str) -> int | None:
    """Best-effort calendar year of a period string ("2024", "2024Q1", "2024-03")."""
    head = str(period)[:4]
    return int(head) if head.isdigit() else None


# -- conventions shared by connectors, methods and caveat checks -------------

#: Allowed values for ``obs_status`` (connectors map provider flags onto these).
OBS_STATUS = {
    "",             # ordinary observed value
    "estimate",     # provider estimate
    "forecast",     # projection/forecast year (e.g. WEO years after the last actual)
    "modelled",     # model-based estimate (e.g. ILO modelled estimates)
    "provisional",  # preliminary/provisional
    "break",        # series break at this observation
    "imputed",      # imputed/interpolated by the provider
}


def ppp_base(unit: str) -> str | None:
    """Return the PPP base year ("2011", "2017", "2021") encoded in a unit label.

    Connectors MUST write PPP-based units with the base year, e.g.
    ``"constant 2021 international $ (PPP 2021)"`` or ``"PPP 2017"``, so
    caveat checks can block mixing PPP vintages.
    """
    import re

    if not unit or "ppp" not in unit.lower():
        return None
    m = (
        re.search(r"ppp\s*\(?\s*(19|20)(\d{2})", unit, flags=re.I)
        or re.search(r"(19|20)(\d{2})\s*(?:international|intl)", unit, flags=re.I)
        or re.search(r"(19|20)(\d{2})\s*ppp", unit, flags=re.I)  # "(2021 PPP)", common in World Bank labels
    )
    return (m.group(1) + m.group(2)) if m else "unknown"
