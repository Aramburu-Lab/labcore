#!/usr/bin/env Rscript
# /// codebase-meta
# name: differential
# order: 40
# summary: Fit the design and test the requested contrast.
# inputs:
#   - path: outputs/30_normalise/out_normalise_counts.parquet
#     from: normalise
#   - path: settings/design.tsv
#     external: true
# outputs:
#   - path: outputs/40_differential/out_differential_results.parquet
#     desc: Per-gene log2FC, p-value and BH-adjusted FDR for the requested contrast.
# options:
#   - flag: --fdr
#     desc: Adjusted p-value cutoff for the significance flag.
#     default: 0.05
#   - flag: --contrast
#     desc: Contrast to test, as <factor>:<numerator>-<denominator>.
# next: [figures]
# container: ghcr.io/aramburu-lab/analysis-r@sha256:...
# ///
#
# Differential expression on the normalised table. limma-trend is used rather
# than a count-based model because the input has already been log-normalised
# upstream; refitting counts here would double-correct the library sizes.

suppressPackageStartupMessages({
  library(optparse)
  library(arrow)
  library(limma)
})

opts <- parse_args(OptionParser(option_list = list(
  make_option("--counts", default = "outputs/30_normalise/out_normalise_counts.parquet"),
  make_option("--design", default = "settings/design.tsv"),
  make_option("--out", default = "outputs/40_differential/out_differential_results.parquet"),
  make_option("--fdr", type = "double", default = 0.05),
  make_option("--contrast", default = "condition:treated-control")
)))

#' Widen the long normalised table into a genes x cells matrix.
#'
#' @param path Parquet file written by normalise.
#' @return Numeric matrix with genes as rows and barcodes as columns.
read_expression <- function(path) {
  long <- as.data.frame(read_parquet(path))
  wide <- reshape(long, idvar = "gene", timevar = "barcode", direction = "wide")
  matrix_out <- as.matrix(wide[, -1])
  rownames(matrix_out) <- wide$gene
  matrix_out
}

#' Build the contrast vector named on the command line.
#'
#' @param design Model matrix.
#' @param contrast Spec of the form <factor>:<numerator>-<denominator>.
#' @return Contrast vector aligned to the design columns.
make_contrast <- function(design, contrast) {
  parts <- strsplit(sub("^[^:]+:", "", contrast), "-", fixed = TRUE)[[1]]
  stopifnot(length(parts) == 2, all(parts %in% colnames(design)))
  makeContrasts(contrasts = paste(parts, collapse = " - "), levels = design)
}

expression <- read_expression(opts$counts)
samples <- read.delim(opts$design, stringsAsFactors = TRUE)
samples <- samples[match(colnames(expression), samples$barcode), ]

design <- model.matrix(~ 0 + condition, data = samples)
colnames(design) <- levels(samples$condition)

fit <- eBayes(contrasts.fit(lmFit(expression, design), make_contrast(design, opts$contrast)), trend = TRUE)
results <- topTable(fit, number = Inf, sort.by = "P")

out <- data.frame(
  gene = rownames(results),
  log2fc = results$logFC,
  pvalue = results$P.Value,
  fdr = results$adj.P.Val,
  significant = results$adj.P.Val < opts$fdr,
  contrast = opts$contrast
)

dir.create(dirname(opts$out), recursive = TRUE, showWarnings = FALSE)
write_parquet(out, opts$out, compression = "zstd")
message(sprintf("differential: %d of %d genes below FDR %.3g", sum(out$significant), nrow(out), opts$fdr))
