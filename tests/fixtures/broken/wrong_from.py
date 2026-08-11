# /// codebase-meta
# name: wrong_from
# order: 60
# summary: Lint fixture claiming a path its named producer does not write.
# inputs:
#   - path: outputs/40_dangling_next/out_dangling_next_missing.parquet
#     from: dangling_next
# outputs:
#   - path: outputs/60_wrong_from/out_wrong_from_table.parquet
#     desc: Placeholder output.
# next: final
# ///
"""Lint fixture: `from:` naming a real step that does not produce the path.

dangling_next.py writes `out_dangling_next_table.parquet`, not `..._missing...`,
so this edge is dangling and the DAG lies. The block is schema-valid; only the
cross-file resolution pass catches it.
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
