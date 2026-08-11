"""Figure writers: vector PDF per theme, opt-in HTML, and the CSV sidecar.

Every import of matplotlib and plotly is lazy and inside a function, so this
module loads in an environment that has neither. Figures are identified by their
class's module rather than by isinstance, which would force those imports.
"""

from __future__ import annotations

from pathlib import Path

from labcore.viz.palette import bg_name, neutrals


def _kind(fig) -> str:
    """Return "mpl", "plotly" or "unknown" without importing either library."""
    module = type(fig).__module__ or ""
    if module.startswith("matplotlib"):
        return "mpl"
    if module.startswith("plotly"):
        return "plotly"
    return "unknown"


def _split_figures(fig) -> tuple[object, object | None]:
    """Split the `fig` argument into (static source, interactive source).

    A pair is accepted because plotly cannot render a matplotlib figure and
    matplotlib cannot render a plotly one: a script that wants both a vector PDF
    and an interactive HTML builds both and passes ``(mpl_fig, plotly_fig)``.
    """
    if isinstance(fig, (tuple, list)):
        try:
            static, live = fig
        except ValueError as exc:
            raise ValueError(
                "figure pair must be (matplotlib_figure, plotly_figure)") from exc
        return static, live
    if _kind(fig) == "plotly":
        return fig, fig
    if _kind(fig) == "mpl":
        return fig, None
    raise TypeError(f"unsupported figure type {type(fig).__name__!r}")


def _reink_mpl(fig, theme: str) -> None:
    """Flip a built figure's structural ink to `theme`, in place."""
    ink = neutrals(theme, transparent=False)
    fg, bg, grid = ink["ink"], ink["plot_bg"], ink["grid"]
    fig.patch.set_facecolor(bg)
    for text in fig.texts:
        text.set_color(fg)
    for ax in fig.axes:
        ax.set_facecolor(bg)
        for spine in ax.spines.values():
            spine.set_color(fg)
        ax.tick_params(colors=fg, which="both")
        ax.title.set_color(fg)
        ax.xaxis.label.set_color(fg)
        ax.yaxis.label.set_color(fg)
        for line in ax.get_xgridlines() + ax.get_ygridlines():
            line.set_color(grid)
        legend = ax.get_legend()
        if legend is not None:
            for text in legend.get_texts():
                text.set_color(fg)


def save_mpl_pdf(fig, outdir: Path | str, stem: str, theme: str) -> Path:
    """Write a matplotlib figure to ``<stem>_<white|black>.pdf``.

    The figure's ink is re-coloured for the theme first, so one built figure can
    serve both variants. Only structural ink flips (spines, ticks, axis labels,
    titles, legend text); a data annotation drawn with an explicit colour keeps
    it, which is why the house palette is dual-safe.

    Args:
        fig: A matplotlib Figure.
        outdir: Directory to write into; created if absent.
        stem: File name without theme suffix or extension.
        theme: Any spelling accepted by ``normalize_theme``.

    Returns:
        The path written.
    """
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{stem}_{bg_name(theme)}.pdf"
    _reink_mpl(fig, theme)
    fig.savefig(path)
    return path


def save_plotly_pdf(fig, outdir: Path | str, stem: str, theme: str) -> Path:
    """Write a plotly figure to ``<stem>_<white|black>.pdf`` via kaleido.

    Args:
        fig: A plotly Figure.
        outdir: Directory to write into; created if absent.
        stem: File name without theme suffix or extension.
        theme: Any spelling accepted by ``normalize_theme``.

    Returns:
        The path written.

    Raises:
        RuntimeError: If kaleido is not installed. It is not a labcore dependency
            because the matplotlib path covers static output for every consumer
            in the lab; install it only if the static figure must come from plotly.
    """
    from labcore.viz.theme import plotly_template

    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{stem}_{bg_name(theme)}.pdf"
    fig.update_layout(template=plotly_template(theme))
    try:
        fig.write_image(path)
    except ValueError as exc:
        raise RuntimeError(
            "static PDF from a plotly figure needs kaleido (pip install kaleido); "
            "or pass a matplotlib figure, or the pair (mpl_fig, plotly_fig)"
        ) from exc
    return path


def save_plotly_html(fig, outdir: Path | str, stem: str, theme: str) -> Path:
    """Write a plotly figure to ``<stem>_<white|black>.html``.

    Args:
        fig: A plotly Figure.
        outdir: Directory to write into; created if absent.
        stem: File name without theme suffix or extension.
        theme: Any spelling accepted by ``normalize_theme``.

    Returns:
        The path written.
    """
    from labcore.viz.theme import plotly_template

    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{stem}_{bg_name(theme)}.html"
    fig.update_layout(template=plotly_template(theme))
    # include_plotlyjs="cdn" keeps the file a few KB but needs internet at view
    # time; switch to "inline" for an offline reader.
    fig.write_html(path, full_html=True, include_plotlyjs="cdn")
    return path


def save_csv_sidecar(df, outdir: Path | str, stem: str) -> Path:
    """Write the frame behind a figure to ``<stem>.csv``.

    The name carries NO theme suffix, unlike its siblings: the numbers are
    identical on both themes, so writing them twice puts two byte-identical files
    on disk and invites them to drift. Call this once, outside the theme loop —
    which is what ``save_figure(data=...)`` does.

    Args:
        df: A polars frame (``write_csv``) or a pandas frame (``to_csv``).
        outdir: Directory to write into; created if absent.
        stem: File name without extension.

    Returns:
        The path written.
    """
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{stem}.csv"
    if hasattr(df, "write_csv"):
        df.write_csv(path)
    else:
        df.to_csv(path, index=False)
    return path


def save_figure(fig, stem: str, outdir: Path | str, *,
                themes: tuple[str, ...] = ("light", "dark"),
                interactive: bool = False, data=None) -> list[Path]:
    """Write one figure as its full output set and return the paths written.

    Default is two files: ``<stem>_white.pdf`` and ``<stem>_black.pdf``, vector,
    always. Interactive HTML is opt-in (D-005d) — a mandatory HTML has no
    substrate for figures with broken axes or twin y-axes, and no frame in scope
    at save time to hover over.

    Args:
        fig: A matplotlib Figure, a plotly Figure, or the pair
            ``(matplotlib_figure, plotly_figure)`` when both outputs are wanted.
        stem: File name without theme suffix or extension.
        outdir: Directory to write into; created if absent.
        themes: Themes to emit, in order. Each becomes a ``_white`` / ``_black``
            file per D-005a.
        interactive: Also write ``<stem>_<theme>.html`` per theme, four files in
            the default two-theme case. Requires a plotly figure.
        data: Optional frame behind the figure; written once as ``<stem>.csv``
            with no theme suffix.

    Returns:
        Every path written, in write order.

    Raises:
        TypeError: If `fig` is neither figure type, or `interactive` is requested
            without a plotly figure to render.
    """
    static_fig, live_fig = _split_figures(fig)
    if interactive and _kind(live_fig) != "plotly":
        raise TypeError("interactive=True needs a plotly figure: pass one, or the "
                        "pair (matplotlib_figure, plotly_figure)")

    written: list[Path] = []
    for theme in themes:
        if _kind(static_fig) == "mpl":
            written.append(save_mpl_pdf(static_fig, outdir, stem, theme))
        else:
            written.append(save_plotly_pdf(static_fig, outdir, stem, theme))
        if interactive:
            written.append(save_plotly_html(live_fig, outdir, stem, theme))
    if data is not None:
        written.append(save_csv_sidecar(data, outdir, stem))

    # The figure is consumed: leaving matplotlib figures open across a loop of
    # panels is what trips its >20-open-figures warning and grows RSS on a node.
    if _kind(static_fig) == "mpl":
        import matplotlib.pyplot as plt
        plt.close(static_fig)
    return written
