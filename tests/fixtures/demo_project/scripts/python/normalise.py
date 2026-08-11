#!/usr/bin/env python3
# /// codebase-meta
# name: normalise
# order: 30
# summary: Scale counts by size factors so libraries are comparable.
# inputs:
#   - path: outputs/20_qc_filter/out_qc_filter_clean.h5ad
#     from: qc_filter
# outputs:
#   - path: outputs/30_normalise/out_normalise_counts.parquet
#     desc: Size-factor-normalised counts, ready for modelling.
# options:
#   - flag: --method
#     desc: Size-factor method, one of {scran,cpm}.
#     default: scran
# next: [differential]
# container: ghcr.io/aramburu-lab/analysis-py@sha256:...
# ///
"""Normalise the clean matrix and emit a long-format table for modelling.

`scran` pooled factors are the default because CPM assumes every cell has the
same total RNA content, which is exactly what a mixed-cell-type sample violates.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import anndata as ad
import numpy as np
import polars as pl

from helpers import ensure_parent

DEFAULT_CLEAN = Path("outputs/20_qc_filter/out_qc_filter_clean.h5ad")
DEFAULT_OUT = Path("outputs/30_normalise/out_normalise_counts.parquet")
METHODS = ("scran", "cpm")


def cpm_factors(counts: np.ndarray) -> np.ndarray:
    """Library size factors scaled to counts per million.

    Args:
        counts: Cells x genes count matrix.

    Returns:
        One factor per cell.
    """
    library = counts.sum(axis=1)
    return library / 1e6


def scran_factors(counts: np.ndarray, pool_sizes: tuple[int, ...] = (20, 40, 60)) -> np.ndarray:
    """Deconvolved pooled size factors, Lun et al. style.

    Args:
        counts: Cells x genes count matrix.
        pool_sizes: Cell counts to pool over before deconvolution.

    Returns:
        One factor per cell, centred on 1.
    """
    reference = np.mean(counts / np.maximum(counts.sum(axis=1, keepdims=True), 1), axis=0)
    factors = np.ones(counts.shape[0])
    for size in pool_sizes:
        order = np.argsort(counts.sum(axis=1))
        for start in range(0, len(order) - size + 1, size):
            pool = order[start : start + size]
            ratio = counts[pool].sum(axis=0) / np.maximum(reference * size, 1e-9)
            factors[pool] = np.median(ratio[ratio > 0]) / size
    return factors / np.mean(factors)


def normalise(adata: ad.AnnData, method: str) -> pl.DataFrame:
    """Divide counts by size factors and melt to long format.

    Args:
        adata: Filtered count matrix.
        method: One of `scran` or `cpm`.

    Returns:
        Long frame of barcode, gene, normalised expression.

    Raises:
        ValueError: If method is not a known size-factor method.
    """
    if method not in METHODS:
        raise ValueError(f"unknown method '{method}', expected one of {METHODS}")
    counts = np.asarray(adata.X.todense(), dtype=float)
    factors = scran_factors(counts) if method == "scran" else cpm_factors(counts)
    scaled = np.log1p(counts / np.maximum(factors[:, None], 1e-9))
    frame = pl.DataFrame(scaled, schema=list(adata.var_names))
    return frame.with_columns(barcode=pl.Series(list(adata.obs_names))).unpivot(
        index="barcode", variable_name="gene", value_name="expression"
    )


def main() -> None:
    """Parse arguments and write the normalised table."""
    parser = argparse.ArgumentParser(description="Size-factor normalise the clean matrix.")
    parser.add_argument("--clean", type=Path, default=DEFAULT_CLEAN)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--method", choices=METHODS, default="scran", help="Size-factor method.")
    args = parser.parse_args()

    long_counts = normalise(ad.read_h5ad(args.clean), args.method)
    ensure_parent(args.out)
    long_counts.write_parquet(args.out, compression="zstd")
    print(f"normalise: {args.method} -> {long_counts.height} rows in {args.out}")


if __name__ == "__main__":
    main()
