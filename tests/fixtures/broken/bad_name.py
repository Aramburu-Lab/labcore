# /// codebase-meta
# name: bad_name
# order: 30
# summary: Lint fixture whose output filename breaks the naming regime.
# outputs:
#   - path: outputs/30_bad_name/results-FINAL v2.csv
#     desc: Placeholder output with a space, a hyphen and no out_ prefix.
# next: final
# ///
"""Lint fixture: an output filename violating ADR-11.

The step-output regime is `out_<script>_<descriptor>.<ext>` with underscores and
no spaces. This block is schema-valid; only `labdocs lint --naming` rejects it.
"""

from __future__ import annotations

from pathlib import Path


def write_table(out: Path) -> None:
    """Write the placeholder output.

    Args:
        out: Destination path.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    out.touch()
