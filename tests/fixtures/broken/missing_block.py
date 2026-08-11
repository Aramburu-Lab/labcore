"""Lint fixture: a script carrying no codebase-meta block at all.

Every other file here has a block with exactly one defect; this one has none,
which is the failure the whole system exists to catch. A helper that genuinely
is not a step opts out with `exempt:` instead — see demo_project/helpers.py.
"""

from __future__ import annotations

from pathlib import Path


def count_rows(table: Path) -> int:
    """Count data rows in a headed TSV.

    Args:
        table: TSV with one header line.

    Returns:
        Number of rows after the header.
    """
    with table.open(encoding="utf-8") as handle:
        return max(sum(1 for _ in handle) - 1, 0)
