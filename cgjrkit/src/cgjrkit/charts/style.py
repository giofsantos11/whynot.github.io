"""House chart style shared by all chart tools.

Charts are produced as Vega-Lite specs (via Altair) so they can be embedded
interactively in the HTML workbench and rendered to PNG/SVG with
``vl-convert``. Every chart carries a source line built from provenance.
"""

from __future__ import annotations

import altair as alt

# Neutral, colour-blind-safe categorical palette. The focus country always
# takes FOCUS; comparators use the rest in order; aggregates use GREY tones.
FOCUS = "#0B6E99"
PALETTE = ["#0B6E99", "#E3812B", "#5AA469", "#9B5DE5", "#C0392B", "#7F8C8D", "#D4A017", "#2E4057"]
GREY = "#9AA5B1"
BAND = "#D9E2EA"       # IQR / peer range band
EVENT = "#EEF1F4"      # shaded episodes (crises, COVID)
FONT = "Helvetica, Arial, sans-serif"

WIDTH = 560
HEIGHT = 320


def theme() -> dict:
    return {
        "config": {
            "font": FONT,
            "view": {"stroke": None, "continuousWidth": WIDTH, "continuousHeight": HEIGHT},
            "title": {"anchor": "start", "fontSize": 14, "fontWeight": "bold", "subtitleFontSize": 11, "subtitleColor": "#555"},
            "axis": {"labelFontSize": 11, "titleFontSize": 11, "gridColor": "#E5E8EB", "domainColor": "#888", "tickColor": "#888"},
            "axisX": {"grid": False},
            "legend": {"orient": "bottom", "title": None, "labelFontSize": 11, "symbolType": "stroke"},
            "range": {"category": PALETTE},
            "line": {"strokeWidth": 2},
        }
    }


def enable() -> None:
    """Register and enable the house theme for Altair."""
    try:  # Altair >= 5.5
        alt.theme.register("cgjrkit", enable=True)(theme)
    except AttributeError:  # older Altair
        alt.themes.register("cgjrkit", theme)
        alt.themes.enable("cgjrkit")


def with_source(chart: alt.TopLevelMixin, source_line: str, note: str = "") -> alt.TopLevelMixin:
    """Attach the note and source line as footer metadata.

    The footer is stored in ``usermeta["cgjrkit_footer"]`` (a list of lines);
    renderers draw it under the chart and the workbench shows it on the
    chart card, so the source line never gets separated from the chart.
    """
    lines = [line for line in (note, source_line) if line]
    if not lines:
        return chart
    return chart.properties(usermeta={"cgjrkit_footer": lines})
