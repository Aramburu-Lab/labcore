# /// codebase-meta
# exempt: shared path and FASTQ-header helpers, no standalone outputs
# ///
"""Small helpers shared by the python steps.

Kept project-local rather than in labcore: the tag convention below belongs to
this demultiplexer, not to every project in the lab.
"""

from __future__ import annotations

from pathlib import Path


def ensure_parent(path: Path) -> Path:
    """Create the parent directory of a file that is about to be written.

    Args:
        path: File path whose parent must exist.

    Returns:
        The unchanged path, so it can be used inline in a write call.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def tag_values(header: str, keys: tuple[str, ...]) -> dict[str, str]:
    """Pull `KEY:value` tags out of a FASTQ header line.

    Args:
        header: One `@`-prefixed FASTQ header.
        keys: Tag names to look for.

    Returns:
        Found tags only — a caller checking `len()` can reject short reads.
    """
    found = {}
    for field in header.strip().split():
        key, _, value = field.partition(":")
        if key in keys and value:
            found[key] = value
    return found
