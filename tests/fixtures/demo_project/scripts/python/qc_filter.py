#!/usr/bin/env python3
# /// codebase-meta
# name: qc_filter
# order: 20
# summary: Drop low-quality cells by MAD thresholds.
# inputs:
#   - path: outputs/10_load/out_load_raw.h5ad
#     from: load
# outputs:
#   - path: outputs/20_qc_filter/out_qc_filter_clean.h5ad
#     desc: Cells passing all QC thresholds.
#   - path: outputs/20_qc_filter/out_qc_filter_metrics.parquet
#     desc: Per-cell QC metrics.
# options:
#   - flag: --mad
#     desc: MAD multiplier for outlier calling.
#     default: 5
# next: [normalise]
# container: ghcr.io/aramburu-lab/analysis-py@sha256:...
# ///
"""Remove low-quality cells and keep the metrics that justified the call.

Thresholds are median-absolute-deviation based rather than fixed cutoffs: a fixed
counts floor tuned on one chemistry silently deletes half the cells on the next.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import anndata as ad
import numpy as np
import polars as pl

from helpers import ensure_parent

QC_METRICS = ("n_counts", "n_genes", "pct_mito")


def cell_metrics(adata: ad.AnnData) -> pl.DataFrame:
    """Compute per-cell QC metrics.

    Args:
        adata: Raw count matrix.

    Returns:
        One row per barcode with counts, detected genes and mitochondrial fraction.
    """
    counts = np.asarray(adata.X.sum(axis=1)).ravel()
    genes = np.asarray((adata.X > 0).sum(axis=1)).ravel()
    mito = adata.var_names.str.upper().str.startswith("MT-")
    mito_counts = np.asarray(adata[:, mito].X.sum(axis=1)).ravel()
    return pl.DataFrame(
        {
            "barcode": list(adata.obs_names),
            "n_counts": counts,
            "n_genes": genes,
            "pct_mito": 100.0 * mito_counts / np.maximum(counts, 1),
        }
    )


def mad_threshold(x: pl.Series, n: int = 5) -> tuple[float, float]:
    """Lower/upper MAD cutoffs for a metric.

    Args:
        x: Metric values, one per cell.
        n: MAD multiplier.

    Returns:
        Inclusive (lower, upper) bounds.
    """
    median = float(x.median())
    mad = float((x - median).abs().median())
    return median - n * mad, median + n * mad


def drop_low_quality_cells(adata: ad.AnnData, *, mad: int) -> tuple[ad.AnnData, pl.DataFrame]:
    """Remove cells outside MAD bounds on any QC metric.

    Args:
        adata: Raw count matrix.
        mad: MAD multiplier passed to every metric.

    Returns:
        The filtered matrix and the metrics frame with a `pass_qc` column.
    """
    metrics = cell_metrics(adata)
    keep = pl.Series("pass_qc", [True] * metrics.height)
    for metric in QC_METRICS:
        low, high = mad_threshold(metrics[metric], mad)
        keep = keep & metrics[metric].is_between(low, high)
    metrics = metrics.with_columns(keep)
    return adata[keep.to_numpy()].copy(), metrics


def main() -> None:
    """Parse arguments, filter cells and write both outputs."""
    parser = argparse.ArgumentParser(description="Drop low-quality cells by MAD thresholds.")
    parser.add_argument("--raw", type=Path, default=Path("outputs/10_load/out_load_raw.h5ad"))
    parser.add_argument("--outdir", type=Path, default=Path("outputs/20_qc_filter"))
    parser.add_argument("--mad", type=int, default=5, help="MAD multiplier for outlier calling.")
    args = parser.parse_args()

    clean, metrics = drop_low_quality_cells(ad.read_h5ad(args.raw), mad=args.mad)
    ensure_parent(args.outdir / "out_qc_filter_clean.h5ad")
    clean.write_h5ad(args.outdir / "out_qc_filter_clean.h5ad", compression="gzip")
    metrics.write_parquet(args.outdir / "out_qc_filter_metrics.parquet", compression="zstd")
    print(f"qc_filter: kept {clean.n_obs} of {metrics.height} cells")


if __name__ == "__main__":
    main()
