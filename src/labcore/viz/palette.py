"""Theme normalisation, colourblind-safe palettes and structural neutrals.

Extracted from ``General_tools/lib_plot_themes.py`` (theme model, dual palette,
neutrals) merged with ``Riboseq/Phase_4_downstream/bin/lib_plot_themes.py``
(Okabe-Ito tables, reference/target roles, heatmap ramps).

House default: figures are transparent-background with a dual-safe data palette,
so the DATA colours stay constant across the two variants and only the ink flips.
A static image cannot carry text legible on both pure white and pure black, which
is why every figure ships twice.
"""

from __future__ import annotations

import sys


def _warn(msg: str) -> None:
    """Emit a one-line warning on stderr."""
    print(f"labcore.viz: {msg}", file=sys.stderr)


def normalize_theme(theme: str) -> str:
    """Map any accepted theme spelling onto the internal name.

    Args:
        theme: "light", "white", "dark" or "black" (case-insensitive).

    Returns:
        "light" or "dark". Unknown spellings warn and fall back to "light".
    """
    name = (theme or "light").lower()
    if name in ("black", "dark"):
        return "dark"
    if name not in ("white", "light"):
        _warn(f"unknown theme {theme!r}; using 'light'")
    return "light"


def bg_name(theme: str) -> str:
    """Return the output-file suffix for a theme.

    Files are named ``_white`` / ``_black`` while ``light`` / ``dark`` stay the
    internal argument (D-005a): four projects already read the file spelling, and
    the suffix describes the background the figure is meant to be placed on.

    Args:
        theme: Any spelling accepted by :func:`normalize_theme`.

    Returns:
        "white" for the light theme, "black" for the dark one.
    """
    return "white" if normalize_theme(theme) == "light" else "black"


# Okabe-Ito 8-class set, ordered for maximum perceptual distance between adjacent
# classes, so a 3-class plot picks orange / sky-blue / green rather than orange /
# sky-blue / blue. The dark row is the same set brightened for a dark backdrop.
_OKABE_ITO = {
    "light": ["#E69F00", "#56B4E9", "#009E73", "#CC79A7",
              "#0072B2", "#D55E00", "#F0E442", "#000000"],
    "dark": ["#FFB347", "#7DCFFC", "#7CCFBA", "#E0A8C0",
             "#56B4E9", "#E69F00", "#F7EE8A", "#FFFFFF"],
}

# Mostly-cool field led by one warm terracotta accent; the dark row brightens
# every hue so it stays vivid on charcoal.
_EDITORIAL = {
    "light": ["#B5654A", "#3A6E8F", "#5C8A6E", "#C9972F", "#3D405B",
              "#6E7B8B", "#8A6D5B", "#A6577C", "#5B6F47", "#9A8A55"],
    "dark": ["#D98E6A", "#6BA3C4", "#84B89A", "#E0B252", "#9CA0D8",
             "#9FB0C0", "#BFA48E", "#D08CB0", "#9DB37A", "#CBBA86"],
}

# Saturated mid-luminance hues (~0.25-0.40 relative luminance) that read on BOTH
# a bright and a dark background, so a transparent figure's data colours do not
# have to flip between its two variants. Same list for both themes on purpose.
_DUAL_SAFE = [
    "#E54B4B", "#D98E04", "#2FA24E", "#3F82C8", "#9B5DE5",
    "#17A398", "#EC7A30", "#D957A0", "#7E8B33", "#5566CC",
]

PALETTES: dict[str, dict[str, list[str]]] = {
    "dual": {"light": _DUAL_SAFE, "dark": _DUAL_SAFE},
    "editorial": _EDITORIAL,
    "okabe-ito": _OKABE_ITO,
}


def data_palette(name: str = "dual", theme: str = "light") -> list[str]:
    """Return the category colour list for a palette name and theme.

    Args:
        name: "dual" (house default, constant across themes), "editorial" or
            "okabe-ito".
        theme: Any spelling accepted by :func:`normalize_theme`.

    Returns:
        A fresh list of hex colours.
    """
    palette = PALETTES.get(name)
    if palette is None:
        _warn(f"unknown palette {name!r}; using 'dual'")
        palette = PALETTES["dual"]
    return list(palette[normalize_theme(theme)])


def categorical_colors(theme: str, n: int) -> list[str]:
    """Return n theme-aware Okabe-Ito colours, cycling past the 8th.

    The first eight stay distinguishable under protanomaly, deuteranomaly and
    tritanomaly. Use for plots whose categories are not a two-condition contrast.

    Args:
        theme: Any spelling accepted by :func:`normalize_theme`.
        n: How many colours are needed.

    Returns:
        A list of n hex colours; entries repeat once n exceeds 8.
    """
    base = _OKABE_ITO[normalize_theme(theme)]
    return [base[i % len(base)] for i in range(max(n, 0))]


# Two-condition palette indexed by ROLE, not by condition name. The original was
# keyed by one cohort's names ("WT"/"M3cKO"), which made a shared library carry
# one project's vocabulary: every consumer wanting "the reference colour" wrote
# palette()["WT"], which on any other cohort is a KeyError or a silent fallback to
# a hardcoded hex — how several figures ended up disagreeing with their legends.
# There is deliberately no third colour; categorical_colors() covers more groups.
GROUP_COLORS: dict[str, dict[str, str]] = {
    "light": {"reference": "#0072B2", "target": "#D55E00"},
    "dark": {"reference": "#56B4E9", "target": "#E69F00"},
}

# matplotlib colormap name -> plotly colorscale name, for the ramps shipped here.
_PLOTLY_COLORSCALES = {
    "viridis": "Viridis", "magma": "Magma", "plasma": "Plasma",
    "inferno": "Inferno", "cividis": "Cividis", "turbo": "Turbo",
    "ylgnbu": "YlGnBu", "rdbu": "RdBu", "blues": "Blues", "reds": "Reds",
}


def group_colors(theme: str, labels) -> dict[str, str]:
    """Map the two conditions of a contrast onto the reference/target colours.

    Assignment is POSITIONAL: entry 0 of ``labels`` is the reference (cool), entry
    1 the target (warm). That keeps panels colour-matched to every other figure
    without pretending the palette is keyed by condition name. Duplicates collapse
    (first occurrence wins) so a repeated name cannot shift target onto reference.

    Args:
        theme: Any spelling accepted by :func:`normalize_theme`.
        labels: Condition names in contrast order; only the first two are used.

    Returns:
        Label to hex colour for at most two labels.
    """
    roles = GROUP_COLORS[normalize_theme(theme)]
    ordered = list(dict.fromkeys(labels))[:2]
    return dict(zip(ordered, (roles["reference"], roles["target"]), strict=False))


def group_color(theme: str, label: str, labels, fallback: str | None = None) -> str:
    """Return one label's contrast colour, with an off-contrast fallback.

    Args:
        theme: Any spelling accepted by :func:`normalize_theme`.
        label: The condition to look up.
        labels: Condition names in contrast order.
        fallback: Colour for labels past the first two. Defaults to the theme's
            muted ink, so an off-contrast series reads as de-emphasised rather
            than borrowing another condition's hue.

    Returns:
        A hex colour.
    """
    if fallback is None:
        fallback = neutrals(theme, transparent=False)["muted"]
    return group_colors(theme, labels).get(label, fallback)


def heatmap_cmap(theme: str) -> str:
    """Return the matplotlib colormap name for continuous fills.

    Args:
        theme: Any spelling accepted by :func:`normalize_theme`.

    Returns:
        "viridis" on light, "magma" on dark.
    """
    return "magma" if normalize_theme(theme) == "dark" else "viridis"


def heatmap_colorscale(theme: str) -> str:
    """Return plotly's spelling of the ramp :func:`heatmap_cmap` gives matplotlib.

    Scripts that draw one heatmap twice — imshow for the PDF, go.Heatmap for the
    HTML — used to carry their own capitalised literal next to the shared call, so
    the two halves of one figure could drift apart. Derived from one constant so
    they cannot. The mapping is an explicit table, not ``.capitalize()``: that is
    right for single-word ramps and silently wrong for mixed-case plotly names
    ("YlGnBu".capitalize() is "Ylgnbu", which plotly does not know).

    Args:
        theme: Any spelling accepted by :func:`normalize_theme`.

    Returns:
        A plotly colorscale name; unknown ramps pass through unchanged.
    """
    name = heatmap_cmap(theme)
    return _PLOTLY_COLORSCALES.get(name.lower(), name)


_NEUTRALS = {
    "light": {
        "paper": "#FCFCFB", "plot_bg": "white",
        "ink": "#2B2B2B", "muted": "#6B6B6B", "faint": "#9A9A9A",
        "title_ink": "#1A1A1A", "axis_tick": "#8A8A8A",
        "grid": "#F0F0F0", "baseline": "#D8D8D8",
        "today": "#A6202C", "milestone": "#2B2B2B", "marker_edge": "white",
        "hover_bg": "rgba(255,255,255,0.96)", "hover_border": "#D8D8D8",
        "slider_bg": "#F7F3EF", "slider_border": "#E4DAD2",
        "workflow_bg": "#FAFAF8", "workflow_title": "#1A1A1A",
        "accent_tick": "#C9C9C9", "node_fill": "#FFFFFF", "node_border": "#9AA3A8",
        "node_text": "#2B2B2B", "node_sub": "#6B6B6B", "phase_text": "#8A8A8A",
        "phase_rule": "#E2E2E2", "phase_guide": "#EEEEEE", "title_text": "#1A1A1A",
        "source_text": "#9A9A9A", "file_fill": "#FBF6E9", "file_border": "#9A8A55",
        "file_fold": "#EFE3C2",
    },
    "dark": {
        "paper": "#171A1F", "plot_bg": "#1B1F26",
        "ink": "#E6E4DF", "muted": "#A6A39C", "faint": "#6E6C68",
        "title_ink": "#F2F0EB", "axis_tick": "#8A8F98",
        "grid": "#2A2E36", "baseline": "#3A3F49",
        "today": "#FF6B5E", "milestone": "#E6E4DF", "marker_edge": "#1B1F26",
        "hover_bg": "rgba(30,34,42,0.97)", "hover_border": "#3A3F49",
        "slider_bg": "#22262E", "slider_border": "#343A44",
        "workflow_bg": "#1E222A", "workflow_title": "#F2F0EB",
        "accent_tick": "#3A3F49", "node_fill": "#232830", "node_border": "#444B55",
        "node_text": "#E6E4DF", "node_sub": "#A6A39C", "phase_text": "#8A8F98",
        "phase_rule": "#2E333C", "phase_guide": "#262B33", "title_text": "#F2F0EB",
        "source_text": "#6E6C68", "file_fill": "#2A2A20", "file_border": "#8A7A45",
        "file_fold": "#3A3522",
    },
}

# Backgrounds and panel fills only. Ink, strokes, grid and text are deliberately
# absent — that half must flip between the two variants of a figure.
_TRANSPARENT_KEYS = ("paper", "plot_bg", "node_fill", "workflow_bg",
                     "file_fill", "file_fold", "marker_edge")


def neutrals(theme: str, transparent: bool = True) -> dict[str, str]:
    """Return a fresh copy of the structural neutral colours for a theme.

    Args:
        theme: Any spelling accepted by :func:`normalize_theme`.
        transparent: House default. Zeroes the background and panel-fill colours
            so the figure blends onto any backdrop instead of baking a white or
            black box. Pass False for a solid background.

    Returns:
        Colour role to hex (or rgba) string.
    """
    colors = dict(_NEUTRALS[normalize_theme(theme)])
    if transparent:
        for key in _TRANSPARENT_KEYS:
            colors[key] = "rgba(0,0,0,0)"
    return colors
