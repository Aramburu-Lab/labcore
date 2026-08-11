# /// codebase-meta
# name: no_desc
# order: 20
# summary: Lint fixture whose output carries no desc.
# outputs:
#   - path: outputs/20_no_desc/out_no_desc_table.parquet
# next: final
# ///
"""Lint fixture: an output declared without a `desc:`.

Undescribed outputs are how data dictionaries die, so `desc` is required by the
schema. Everything else in this block is valid.
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
