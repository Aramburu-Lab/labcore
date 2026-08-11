# /// codebase-meta
# name: bare_input
# order: 50
# summary: Lint fixture whose input declares neither from nor external.
# inputs:
#   - path: settings/samples.tsv
# outputs:
#   - path: outputs/50_bare_input/out_bare_input_table.parquet
#     desc: Placeholder output.
# next: final
# ///
"""Lint fixture: an input with neither `from:` nor `external: true`.

Ambiguous provenance — a genuine outside input is declared, never inferred. The
schema's oneOf on inputs is what rejects this.
"""

from __future__ import annotations

from pathlib import Path


def write_table(out: Path) -> None:
    """Write the placeholder output.

    Args:
        out: Destination parquet path.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    out.touch()
