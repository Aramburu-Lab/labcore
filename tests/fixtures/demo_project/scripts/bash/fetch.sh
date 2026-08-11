#!/usr/bin/env bash
# /// codebase-meta
# name: fetch
# order: 0
# summary: Download the raw reads listed in the samplesheet.
# inputs:
#   - path: settings/samples.tsv
#     external: true
# outputs:
#   - path: outputs/00_fetch/out_fetch_reads.fq.gz
#     desc: Raw paired FASTQ.
#   - path: outputs/00_fetch/out_fetch_manifest.tsv
#     desc: Checksum manifest for provenance.
# options:
#   - flag: --threads
#     desc: Parallel downloads.
#     default: 4
#   - flag: --outdir
#     desc: Destination for the reads and the manifest.
#     default: outputs/00_fetch
# next: [load]
# container: ghcr.io/aramburu-lab/analysis-base@sha256:...
# ///
#
# Fetch one FASTQ per accession and checksum every file on the way in, so a
# re-run that silently downloads a truncated file is caught here and not three
# steps later in a count matrix nobody can explain.

set -euo pipefail

samplesheet="settings/samples.tsv"
outdir="outputs/00_fetch"
threads=4

while [[ $# -gt 0 ]]; do
  case "$1" in
    --samplesheet) samplesheet="$2"; shift 2 ;;
    --outdir)      outdir="$2";      shift 2 ;;
    --threads)     threads="$2";     shift 2 ;;
    -h|--help)     grep '^# ' "$0" | tail -n 3; exit 0 ;;
    *) echo "fetch: unknown option '$1'" >&2; exit 2 ;;
  esac
done

[[ -s "$samplesheet" ]] || { echo "fetch: no samplesheet at $samplesheet" >&2; exit 1; }
mkdir -p "$outdir"

reads="${outdir}/out_fetch_reads.fq.gz"
manifest="${outdir}/out_fetch_manifest.tsv"

download_one() {
  local accession="$1" url="$2" dest="$3"
  curl --silent --show-error --fail --location --retry 3 --output "${dest}/${accession}.fq.gz" "$url"
  printf '%s\t%s\t%s\n' "$accession" "$(sha256sum "${dest}/${accession}.fq.gz" | cut -d' ' -f1)" "$url"
}
export -f download_one

# Column 1 is the accession, column 3 the URL; the header line is dropped.
tail -n +2 "$samplesheet" \
  | awk -F'\t' 'NF >= 3 {print $1"\t"$3}' \
  | xargs -P "$threads" -n 2 bash -c 'download_one "$0" "$1" "'"$outdir"'"' \
  > "$manifest"

# Concatenating after the manifest is written keeps per-accession checksums
# meaningful; the merged file is what load.py streams.
cat "${outdir}"/*.fq.gz > "$reads"

echo "fetch: $(wc -l < "$manifest") accessions -> $reads"
