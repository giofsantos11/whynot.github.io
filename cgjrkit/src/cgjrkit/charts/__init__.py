"""Chart layer: Vega-Lite charts (Altair) in the house style, rendered offline with vl-convert.

* :mod:`cgjrkit.charts.series`: ``line_vs_peers``, ``bar_vs_peers``, ``dual_axis``
* :mod:`cgjrkit.charts.benchmark`: ``scatter_vs_income``, ``kde_plot``, ``slope_rank``
* :mod:`cgjrkit.charts.decomposition`: ``stacked_contributions``
* :mod:`cgjrkit.charts.render`: ``save_chart``, ``render``, ``chart_from_result``, ``add_footer``, ``to_spec``
  (``render`` is not re-exported here so that ``cgjrkit.charts.render`` stays the module)
* :mod:`cgjrkit.charts.style`: colours, fonts and the Altair theme
"""

from cgjrkit.charts.benchmark import kde_plot, scatter_vs_income, slope_rank
from cgjrkit.charts.decomposition import stacked_contributions
from cgjrkit.charts.render import BlockedResultError, add_footer, chart_from_result, save_chart, to_spec
from cgjrkit.charts.series import bar_vs_peers, dual_axis, line_vs_peers

__all__ = [
    "line_vs_peers",
    "bar_vs_peers",
    "dual_axis",
    "scatter_vs_income",
    "kde_plot",
    "slope_rank",
    "stacked_contributions",
    "save_chart",
    "chart_from_result",
    "add_footer",
    "to_spec",
    "BlockedResultError",
]
