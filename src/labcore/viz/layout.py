"""Figure layout helpers: panel harmonisation, sizing, legends, overlap guards.

matplotlib is never imported here — every function is handed the figure or axes it
operates on, so this module loads without it.
"""

from __future__ import annotations

import math


def figsize_for_panels(n_rows: int, n_cols: int,
                       panel_width_in: float = 5.5,
                       panel_height_in: float = 4.5) -> tuple[float, float]:
    """Return a figure size for an n_rows by n_cols panel grid.

    Args:
        n_rows: Panel rows; values below 1 are treated as 1.
        n_cols: Panel columns; values below 1 are treated as 1.
        panel_width_in: Width per panel in inches.
        panel_height_in: Height per panel in inches.

    Returns:
        (width, height) in inches.
    """
    return (panel_width_in * max(n_cols, 1), panel_height_in * max(n_rows, 1))


def panel_grid(fig, axes, n_used: int, n_cols: int) -> None:
    """Harmonise a panel grid so its panels are comparable at a glance.

    Blanks the unused axes, hides x tick labels on every row but the last, puts
    all used panels on one shared x and y range, and aligns their axis labels.
    Panels drawn from separate samples otherwise get separate autoscaled ranges,
    and a reader compares two bars that are not on the same scale.

    Args:
        fig: The Figure owning `axes`.
        axes: The axes array from ``plt.subplots`` (or any flat sequence).
        n_used: How many leading axes carry data.
        n_cols: Columns in the grid.
    """
    flat = list(axes.ravel()) if hasattr(axes, "ravel") else list(axes)
    n_rows = max(1, math.ceil(len(flat) / max(n_cols, 1)))

    for ax in flat[n_used:]:
        ax.axis("off")
    for i, ax in enumerate(flat):
        if i // max(n_cols, 1) != n_rows - 1:
            ax.tick_params(labelbottom=False)

    used = flat[:n_used]
    if not used:
        return
    y_lo = min(ax.get_ylim()[0] for ax in used)
    y_hi = max(ax.get_ylim()[1] for ax in used)
    x_lo = min(ax.get_xlim()[0] for ax in used)
    x_hi = max(ax.get_xlim()[1] for ax in used)
    for ax in used:
        ax.set_ylim(y_lo, y_hi)
        ax.set_xlim(x_lo, x_hi)
    fig.align_labels()


def overlap_safe_xticks(ax, labels: list[str] | None = None,
                        max_label_chars: int = 10) -> None:
    """Rotate dense x tick labels before they collide horizontally.

    Idempotent, so it is safe to call after every redraw.

    Args:
        ax: The axes to inspect.
        labels: Optional labels to set first; otherwise the current ones are used.
        max_label_chars: Longest label that stays horizontal.
    """
    if labels is not None:
        ax.set_xticks(range(len(labels)))
        ax.set_xticklabels(labels)
    ticks = ax.get_xticklabels()
    if not ticks:
        return
    if max((len(t.get_text()) for t in ticks), default=0) > max_label_chars:
        for tick in ticks:
            tick.set_rotation(30)
            # Anchor right so the rotation pivots at the label's right edge,
            # which lands it under its tick instead of beside it.
            tick.set_ha("right")
            tick.set_rotation_mode("anchor")


def bar_width_for_n(n: int, max_total: float = 0.85) -> float:
    """Return the per-bar width for n bars drawn at one x position.

    Args:
        n: Bars in the group.
        max_total: Fraction of the x step the whole group covers, so 0.85 leaves a
            15 percent gap between groups.

    Returns:
        Width for one bar. Offset bar i by ``(i - (n - 1) / 2) * width`` to centre
        the group.
    """
    return max_total / max(n, 1)


def legend_outside_right(ax, title: str | None = None,
                         fontsize: float | None = None,
                         frameon: bool = False) -> None:
    """Anchor the legend just outside the right edge of `ax`, top-aligned.

    Mirrors plotly's default placement. ``loc="best"`` is avoided on purpose:
    matplotlib picks the corner with fewest points, which on a volcano or a
    TE-vs-TE scatter is the upper or lower right — directly over the most
    informative quadrant. Reserve the space with
    ``apply_final_polish(fig, legend_outside=True)``.

    Args:
        ax: The axes whose handles are used.
        title: Optional legend title.
        fontsize: Point size; None uses the rcParam.
        frameon: Draw the legend box.
    """
    ax.legend(loc="upper left", bbox_to_anchor=(1.02, 1.0), borderaxespad=0.0,
              title=title, fontsize=fontsize, frameon=frameon)


def legend_above(fig, handles=None, labels=None, ax=None,
                 ncol: int | None = None, y_anchor: float = 0.99) -> None:
    """Place one horizontal legend strip above the data area.

    Better than a right-hand band for figures with few entries, where the band
    costs about 17 percent of the figure width and squeezes the panels. Pair with
    ``apply_final_polish(fig, legend_above=True)`` so the strip has room.

    Args:
        fig: The Figure to attach the legend to.
        handles: Legend handles; taken from `ax` when omitted.
        labels: Legend labels; taken from `ax` when omitted.
        ax: Axes to collect handles and labels from.
        ncol: Columns in the strip; defaults to at most four.
        y_anchor: Figure-fraction y of the strip's top.
    """
    if handles is None and ax is not None:
        handles, labels = ax.get_legend_handles_labels()
    if not handles:
        return
    fig.legend(handles=handles, labels=labels, loc="upper center",
               bbox_to_anchor=(0.5, y_anchor),
               ncol=ncol or min(len(handles), 4), frameon=False)


def apply_final_polish(fig, legend_outside: bool = False,
                       legend_top: bool = False) -> None:
    """Run a final tight_layout that reserves space for titles and legends.

    matplotlib's tight_layout does not see the suptitle, so a long figure title
    collides with the top panel's title without this guard. Idempotent.

    Args:
        fig: The Figure to lay out.
        legend_outside: Reserve a right band for :func:`legend_outside_right`.
        legend_top: Reserve a top band for :func:`legend_above`.
    """
    has_suptitle = getattr(fig, "_suptitle", None) is not None
    right = 0.83 if legend_outside else 1.0
    top = 0.88 if legend_top else (0.95 if has_suptitle else 1.0)
    fig.tight_layout(rect=(0, 0, right, top))
