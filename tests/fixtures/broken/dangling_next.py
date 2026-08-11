# /// codebase-meta
# name: dangling_next
# order: 40
# summary: Lint fixture pointing at a downstream script that does not exist.
# outputs:
#   - path: outputs/40_dangling_next/out_dangling_next_table.parquet
#     desc: Placeholder output consumed by nothing.
# next: [does_not_exist]
# ///
"""Lint fixture: a `next:` target with no matching script.

A silently broken DAG edge. The block is schema-valid — only the cross-file
resolution pass can catch it. This step's outputs are also the producer that
wrong_from.py misquotes.
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
