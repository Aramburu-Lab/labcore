"""Dual-theme figure system: one figure, a white and a black variant.

Extracted from five divergent copies of ``lib_plot_themes.py``. Nothing here
imports matplotlib or plotly at module level, so ``import labcore.viz`` works with
neither installed; install the ``viz`` extra to draw.

Typical use::

    apply_mpl_theme(theme="light", scale="paper")
    fig, ax = plt.subplots()
    ...
    save_figure(fig, "out_alignment_depth", outdir, data=frame)
"""

from __future__ import annotations

from labcore.viz.layout import (
    apply_final_polish,
    bar_width_for_n,
    figsize_for_panels,
    legend_above,
    legend_outside_right,
    overlap_safe_xticks,
    panel_grid,
)
from labcore.viz.palette import (
    PALETTES,
    bg_name,
    categorical_colors,
    data_palette,
    group_color,
    group_colors,
    heatmap_cmap,
    heatmap_colorscale,
    neutrals,
    normalize_theme,
)
from labcore.viz.save import (
    save_csv_sidecar,
    save_figure,
    save_mpl_pdf,
    save_plotly_html,
    save_plotly_pdf,
)
from labcore.viz.theme import (
    FONT_SIZES,
    apply_mpl_theme,
    font_size,
    mpl_s_to_plotly_size,
    plotly_template,
    resolve_fonts,
)

__all__ = [
    "FONT_SIZES",
    "PALETTES",
    "apply_final_polish",
    "apply_mpl_theme",
    "bar_width_for_n",
    "bg_name",
    "categorical_colors",
    "data_palette",
    "figsize_for_panels",
    "font_size",
    "group_color",
    "group_colors",
    "heatmap_cmap",
    "heatmap_colorscale",
    "legend_above",
    "legend_outside_right",
    "mpl_s_to_plotly_size",
    "neutrals",
    "normalize_theme",
    "overlap_safe_xticks",
    "panel_grid",
    "plotly_template",
    "resolve_fonts",
    "save_csv_sidecar",
    "save_figure",
    "save_mpl_pdf",
    "save_plotly_html",
    "save_plotly_pdf",
]
