#!/usr/bin/env python3
# /// codebase-meta
# name: figures
# order: 50
# summary: Render the volcano deliverables from the differential results.
# inputs:
#   - path: outputs/40_differential/out_differential_results.parquet
#     from: differential
# outputs:
#   - path: deliverables/2026-08-11_de_volcano_v1_white.pdf
#     desc: Volcano plot, light theme, vector PDF.
#   - path: deliverables/2026-08-11_de_volcano_v1_black.pdf
#     desc: Volcano plot, dark theme, vector PDF.
#   - path: deliverables/2026-08-11_de_explorer_v1.html
#     desc: Interactive Plotly explorer with hover on every variable; opt-in.
# options:
#   - flag: --top-n
#     desc: Number of labelled genes.
#     default: 20
# next: final
# container: ghcr.io/aramburu-lab/analysis-py@sha256:...
# ///
"""Render the differential-expression deliverables.

Static vector PDFs are always written, one per theme (D-005a: the file suffix is
`_white`/`_black`, the theme argument stays `light`/`dark`). The Plotly explorer
is opt-in (D-005d) — an interactive HTML is a convenience, never the record.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import polars as pl

from helpers import ensure_parent

DEFAULT_RESULTS = Path("outputs/40_differential/out_differential_results.parquet")
EXPLORER_NAME = "2026-08-11_de_explorer_v1.html"
THEME_SUFFIX = {"light": "white", "dark": "black"}
THEME_COLOURS = {
    "light": {"fg": "#1a1a1a", "bg": "#ffffff", "hit": "#c0392b"},
    "dark": {"fg": "#e8e8e8", "bg": "#12141a", "hit": "#ff6b57"},
}


def label_genes(results: pl.DataFrame, top_n: int) -> pl.DataFrame:
    """Pick the genes worth naming on the plot.

    Args:
        results: Differential results with log2fc and fdr columns.
        top_n: How many genes to label.

    Returns:
        The top rows by significance among the significant genes.
    """
    return (
        results.filter(pl.col("significant"))
        .sort(["fdr", pl.col("log2fc").abs()], descending=[False, True])
        .head(top_n)
    )


def volcano(results: pl.DataFrame, top_n: int, theme: str, out: Path) -> None:
    """Draw one volcano plot and save it as vector PDF.

    Args:
        results: Differential results.
        top_n: Number of labelled genes.
        theme: `light` or `dark`.
        out: Destination PDF.
    """
    colours = THEME_COLOURS[theme]
    neg_log_p = -np.log10(np.maximum(results["pvalue"].to_numpy(), 1e-300))

    fig, ax = plt.subplots(figsize=(5.5, 5.0))
    fig.patch.set_facecolor(colours["bg"])
    ax.set_facecolor(colours["bg"])
    ax.scatter(results["log2fc"], neg_log_p, s=6, c=colours["fg"], alpha=0.35, linewidths=0)

    hits = label_genes(results, top_n)
    hit_y = -np.log10(hits["pvalue"].to_numpy())
    ax.scatter(hits["log2fc"], hit_y, s=14, c=colours["hit"])
    for gene, x, y in zip(hits["gene"], hits["log2fc"], hit_y, strict=True):
        ax.annotate(
            gene,
            (x, y),
            fontsize=6,
            color=colours["fg"],
            xytext=(3, 3),
            textcoords="offset points",
        )

    ax.set_xlabel("log2 fold change", color=colours["fg"])
    ax.set_ylabel("-log10 p", color=colours["fg"])
    ax.tick_params(colors=colours["fg"])
    ensure_parent(out)
    fig.savefig(out, format="pdf", bbox_inches="tight", facecolor=colours["bg"])
    plt.close(fig)


def explorer(results: pl.DataFrame, out: Path) -> None:
    """Write the interactive Plotly explorer.

    Args:
        results: Differential results; every column becomes hover data.
        out: Destination HTML.
    """
    import plotly.express as px

    figure = px.scatter(
        results.to_pandas(),
        x="log2fc",
        y=-np.log10(results["pvalue"].to_numpy()),
        color="significant",
        hover_data=results.columns,
    )
    ensure_parent(out)
    figure.write_html(out, include_plotlyjs="cdn")


def main() -> None:
    """Parse arguments and render every requested deliverable."""
    parser = argparse.ArgumentParser(description="Render the DE volcano deliverables.")
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--outdir", type=Path, default=Path("deliverables"))
    parser.add_argument("--stem", default="2026-08-11_de_volcano_v1", help="Deliverable stem.")
    parser.add_argument("--top-n", type=int, default=20, help="Number of labelled genes.")
    parser.add_argument("--interactive", action="store_true", help="Also write the explorer.")
    args = parser.parse_args()

    results = pl.read_parquet(args.results)
    for theme, suffix in THEME_SUFFIX.items():
        volcano(results, args.top_n, theme, args.outdir / f"{args.stem}_{suffix}.pdf")
    if args.interactive:
        explorer(results, args.outdir / EXPLORER_NAME)
    print(f"figures: {len(THEME_SUFFIX)} PDFs in {args.outdir}")


if __name__ == "__main__":
    main()
