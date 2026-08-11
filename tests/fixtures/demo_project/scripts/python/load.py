#!/usr/bin/env python3
# /// codebase-meta
# name: load
# order: 10
# summary: Build an unfiltered count matrix from the fetched reads.
# inputs:
#   - path: outputs/00_fetch/out_fetch_reads.fq.gz
#     from: fetch
# outputs:
#   - path: outputs/10_load/out_load_raw.h5ad
#     desc: Unfiltered count matrix, one row per barcode.
# options:
#   - flag: --min-genes
#     desc: Initial floor on genes detected per barcode.
#     default: 200
# next: [qc_filter]
# container: ghcr.io/aramburu-lab/analysis-py@sha256:...
# ///
"""Count UMIs per barcode and write the raw AnnData matrix.

Read headers carry `BC:<barcode> UB:<umi> GN:<gene>` tags from the demultiplexer,
so counting is a single streaming pass and never needs the reads in memory.
"""

from __future__ import annotations

import argparse
import gzip
from collections import defaultdict
from pathlib import Path

import anndata as ad
import numpy as np
import scipy.sparse as sp

from helpers import ensure_parent, tag_values

DEFAULT_READS = Path("outputs/00_fetch/out_fetch_reads.fq.gz")
DEFAULT_OUT = Path("outputs/10_load/out_load_raw.h5ad")
REQUIRED_TAGS = ("BC", "UB", "GN")


def count_umis(reads: Path) -> dict[str, dict[str, int]]:
    """Tally distinct UMIs per barcode and gene.

    Args:
        reads: Gzipped FASTQ whose headers carry BC/UB/GN tags.

    Returns:
        Barcode -> gene -> UMI count.
    """
    seen: set[tuple[str, str, str]] = set()
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    with gzip.open(reads, "rt") as handle:
        for line_no, line in enumerate(handle):
            if line_no % 4:
                continue
            tags = tag_values(line, REQUIRED_TAGS)
            if len(tags) < len(REQUIRED_TAGS):
                continue
            barcode, umi, gene = tags["BC"], tags["UB"], tags["GN"]
            # Duplicate UMIs are PCR copies of one molecule, not two molecules.
            if (barcode, umi, gene) in seen:
                continue
            seen.add((barcode, umi, gene))
            counts[barcode][gene] += 1
    return counts


def to_anndata(counts: dict[str, dict[str, int]], min_genes: int) -> ad.AnnData:
    """Assemble a sparse AnnData from per-barcode counts.

    Args:
        counts: Barcode -> gene -> UMI count.
        min_genes: Drop barcodes detecting fewer genes than this.

    Returns:
        AnnData with barcodes as obs and genes as var.
    """
    barcodes = sorted(bc for bc, genes in counts.items() if len(genes) >= min_genes)
    gene_names = sorted({gene for bc in barcodes for gene in counts[bc]})
    gene_index = {gene: j for j, gene in enumerate(gene_names)}

    matrix = sp.lil_matrix((len(barcodes), len(gene_names)), dtype=np.int32)
    for i, barcode in enumerate(barcodes):
        for gene, n in counts[barcode].items():
            matrix[i, gene_index[gene]] = n

    adata = ad.AnnData(X=matrix.tocsr())
    adata.obs_names = barcodes
    adata.var_names = gene_names
    return adata


def main() -> None:
    """Parse arguments and write the raw matrix."""
    parser = argparse.ArgumentParser(description="Build the raw count matrix from fetched reads.")
    parser.add_argument("--reads", type=Path, default=DEFAULT_READS)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--min-genes", type=int, default=200, help="Genes detected per barcode.")
    args = parser.parse_args()

    adata = to_anndata(count_umis(args.reads), args.min_genes)
    ensure_parent(args.out)
    adata.write_h5ad(args.out, compression="gzip")
    print(f"load: {adata.n_obs} barcodes x {adata.n_vars} genes -> {args.out}")


if __name__ == "__main__":
    main()
