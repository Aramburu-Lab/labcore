"""Font resolution, matplotlib rcParams and the matching plotly template.

matplotlib and plotly are both imported LAZILY inside the functions that need
them, so ``import labcore.viz`` works with neither installed. Consumers exist that
ship matplotlib but not plotly (coverage plotting in a pysam/pyBigWig image) and
eager-importing plotly here used to crash them at startup.
"""

from __future__ import annotations

import logging
import math
from functools import lru_cache

from labcore.viz.palette import data_palette, neutrals, normalize_theme

# matplotlib's findfont() falls back to DejaVu Sans happily but logs
# "findfont: Font family 'X' not found." PER CALL, which is dozens of spam lines
# in one SLURM log. The chain below is resolved once and cached; this silences the
# residual noise so the fallback path is quiet.
logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

# Two scales, keyed by output intent — not one base size. `paper` is a journal
# multi-panel where 8 pt body is the norm; `deck` is a projector, and is the
# original library's default (base 14 -> ~11-12 pt printed, legible across a room).
FONT_SIZES: dict[str, dict[str, float]] = {
    "paper": {
        "suptitle": 11.0,
        "panel_title": 10.0,
        "axis_label": 8.0,
        "annot": 7.0,
        "caption": 7.0,
        "tick": 6.0,
        "value": 6.0,
    },
    "deck": {
        "suptitle": 19.0,
        "panel_title": 18.0,
        "axis_label": 16.0,
        "annot": 14.0,
        "caption": 13.0,
        "tick": 14.0,
        "value": 13.0,
    },
}

# Strokes track the scale too: the deck row is the original's presentation preset
# (thick spines, dark grid), which prints as a heavy cage at journal panel size.
_STROKES = {
    "paper": {"axes": 0.8, "tick": 0.8, "tick_len": 3.0, "line": 1.2,
              "marker": 4.0, "grid": 0.6},
    "deck": {"axes": 1.6, "tick": 1.4, "tick_len": 6.0, "line": 2.2,
             "marker": 7.0, "grid": 0.9},
}


def _scale_name(scale: str) -> str:
    """Return a known scale name, warning-free fallback to 'paper'."""
    return scale if scale in FONT_SIZES else "paper"


def font_size(role: str, scale: str = "paper") -> float:
    """Return the point size for a text role.

    Literals used to run from 5.0 to 11 across scripts with no shared meaning: the
    same measure header was 9 in one figure and 10 in another. Ask for the role.

    Args:
        role: One of suptitle, panel_title, axis_label, annot, caption, tick, value.
        scale: "paper" or "deck".

    Returns:
        Size in points.

    Raises:
        KeyError: If the role is not in the table.
    """
    return FONT_SIZES[_scale_name(scale)][role]


@lru_cache(maxsize=1)
def _available_fonts() -> frozenset[str]:
    """Family names of every font matplotlib can see, or empty if it is absent."""
    try:
        import matplotlib.font_manager as fm
    except ImportError:
        return frozenset()
    return frozenset(f.name for f in fm.fontManager.ttflist)


def _pick(preferred: tuple[str, ...], default: str) -> str:
    """First installed family from `preferred`, else `default`."""
    available = _available_fonts()
    for name in preferred:
        if name in available:
            return name
    return default


@lru_cache(maxsize=4)
def _resolve_fonts(choice: str) -> tuple[tuple[str, str], ...]:
    """Cached font resolution, returned as an immutable pair sequence."""
    sans = _pick(("Arial", "Helvetica", "Liberation Sans"), "DejaVu Sans")
    sans_css = f"{sans}, Arial, Helvetica, sans-serif"
    if choice == "serif":
        serif = _pick(("Georgia", "Times New Roman", "Times", "Liberation Serif"),
                      "DejaVu Serif")
        return (("title", serif), ("body", sans),
                ("title_css", f'{serif}, "Times New Roman", serif'),
                ("body_css", sans_css))
    return (("title", sans), ("body", sans),
            ("title_css", sans_css), ("body_css", sans_css))


def resolve_fonts(choice: str = "sans") -> dict[str, str]:
    """Resolve the title and body faces for a font choice, once per process.

    The chain is Arial -> Helvetica -> Liberation Sans -> DejaVu Sans; containers
    that ship none of the first three (the rnaseq python image is one) land
    silently on DejaVu rather than warning per draw call.

    Args:
        choice: "sans" (house style, title and body share the face) or "serif"
            (Georgia masthead over a sans body).

    Returns:
        Keys ``title``, ``body`` (installed family names, for matplotlib) and
        ``title_css``, ``body_css`` (CSS fallback strings, for plotly).
    """
    return dict(_resolve_fonts(choice))


def apply_mpl_theme(theme: str = "light", font: str = "sans",
                    scale: str = "paper") -> None:
    """Set matplotlib rcParams for one theme, font choice and size scale.

    Backgrounds are saved transparent (the house default) so one figure drops onto
    a white page or a dark slide without a baked box; only the ink flips between
    the two variants.

    Args:
        theme: Any spelling accepted by :func:`normalize_theme`.
        font: "sans" or "serif".
        scale: "paper" (journal multi-panel) or "deck" (projector).
    """
    import matplotlib as mpl
    import matplotlib.pyplot as plt

    name = normalize_theme(theme)
    scale = _scale_name(scale)
    plt.style.use("dark_background" if name == "dark" else "default")

    ink = neutrals(name, transparent=False)
    sizes = FONT_SIZES[scale]
    stroke = _STROKES[scale]
    fg, bg = ink["ink"], ink["plot_bg"]

    mpl.rcParams.update({
        "font.family": [resolve_fonts(font)["body"]],
        "font.size": sizes["annot"],
        "axes.titlesize": sizes["panel_title"],
        "axes.titleweight": "bold",
        "axes.titlepad": 10.0,
        "axes.labelsize": sizes["axis_label"],
        "axes.labelweight": "bold",
        "axes.labelpad": 6.0,
        "xtick.labelsize": sizes["tick"],
        "ytick.labelsize": sizes["tick"],
        "legend.fontsize": sizes["caption"],
        "legend.title_fontsize": sizes["caption"],
        "legend.frameon": False,
        "figure.titlesize": sizes["suptitle"],
        "figure.titleweight": "bold",
        "axes.prop_cycle": mpl.cycler(color=data_palette("dual", name)),
        "axes.facecolor": bg,
        "figure.facecolor": bg,
        "savefig.facecolor": bg,
        "axes.edgecolor": fg,
        "axes.labelcolor": fg,
        "axes.linewidth": stroke["axes"],
        "text.color": fg,
        "xtick.color": fg,
        "ytick.color": fg,
        "xtick.major.size": stroke["tick_len"],
        "ytick.major.size": stroke["tick_len"],
        "xtick.major.width": stroke["tick"],
        "ytick.major.width": stroke["tick"],
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": ink["grid"],
        "grid.alpha": 0.40,
        "grid.linestyle": "--",
        "grid.linewidth": stroke["grid"],
        "lines.linewidth": stroke["line"],
        "lines.markersize": stroke["marker"],
        "patch.linewidth": 1.0,
        "patch.edgecolor": fg,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.12,
        # 300 dpi is what Nature, Cell and eLife ask for at print size. Vector PDFs
        # ignore it; it keeps any raster fallback publication-grade.
        "savefig.dpi": 300,
        "savefig.transparent": True,
        # 42 = embed TrueType. Without it matplotlib writes Type 3, which converts
        # glyphs to outlines: the PDF text is then uneditable in Illustrator and
        # unsearchable. This single pair is the most-lost setting in every
        # from-scratch rewrite of this module.
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    })


def plotly_template(theme: str = "light", font: str = "sans",
                    scale: str = "paper"):
    """Return a plotly Template matching :func:`apply_mpl_theme`.

    Args:
        theme: Any spelling accepted by :func:`normalize_theme`.
        font: "sans" or "serif".
        scale: "paper" or "deck".

    Returns:
        A ``plotly.graph_objects.layout.Template``.
    """
    import copy

    import plotly.io as pio

    name = normalize_theme(theme)
    sizes = FONT_SIZES[_scale_name(scale)]
    ink = neutrals(name)
    fg = ink["ink"]

    # deepcopy, NOT a bare reference: layout.update() below mutates in place and
    # pio.templates[base] is plotly's GLOBAL registry entry. Without the copy every
    # call permanently rewrites the stock template for the whole process — so the
    # second theme in a two-theme loop inherits the first theme's colours, and any
    # other library asking for the stock template silently gets ours.
    template = copy.deepcopy(pio.templates["plotly_dark" if name == "dark"
                                           else "plotly_white"])

    fonts = resolve_fonts(font)
    family = fonts["title_css"] if font == "serif" else fonts["body_css"]

    # Modern plotly replaced `titlefont` with `title.font`; the nested-dict form
    # works on both 5.x and 6.x.
    axis = {
        "gridcolor": "rgba(128,128,128,0.25)",
        "linecolor": fg,
        "tickfont": {"size": sizes["tick"], "color": fg},
        "title": {"font": {"size": sizes["axis_label"], "color": fg}},
    }
    template.layout.update(
        font={"family": family, "size": sizes["annot"], "color": fg},
        title={"font": {"size": sizes["suptitle"], "color": fg}},
        # Transparent, not the stock opaque #111: an opaque canvas dropped on a
        # slide renders as a grey frame around the figure.
        paper_bgcolor=ink["paper"],
        plot_bgcolor=ink["plot_bg"],
        colorway=data_palette("dual", name),
        xaxis=axis,
        yaxis=axis,
        legend={"font": {"size": sizes["caption"], "color": fg}},
    )
    return template


def mpl_s_to_plotly_size(s: float) -> float:
    """Convert a matplotlib scatter ``s`` to the equivalent plotly marker size.

    The units are not the same, which is the entire reason this exists:
    matplotlib's ``s`` is marker AREA in points squared, plotly's ``marker.size``
    is DIAMETER in points. Passing the same number to both draws a plotly dot
    about 5.7x wider, so the PDF and the HTML of one figure disagree about how big
    a point is. sqrt(area) recovers the diameter: s=32 is 5.66 pt across in both.

    Args:
        s: matplotlib marker area in points squared. Negatives clamp to 0.

    Returns:
        Marker diameter in points.
    """
    return math.sqrt(max(float(s), 0.0))
