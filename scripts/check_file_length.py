#!/usr/bin/env python3
"""Fail a commit when a source file grows past the ADR-12 hard ceiling.

The target is 200 lines; the hard failure is 600. That gap is the D-005b ratchet:
64-83% of real files in this lab exceed 200 today, and a hook that fails most of
the codebase gets disabled in week one, taking the metadata and naming lints with
it. Tighten `FAIL` as the codebase catches up.
"""

from __future__ import annotations

import sys
from pathlib import Path

WARN = 200
FAIL = 600
SUFFIXES = {".py", ".r", ".R", ".sh", ".bash", ".nf", ".rs"}


def check(paths: list[str]) -> int:
    """Report over-long files.

    Args:
        paths: Candidate file paths, as passed by the hook runner.

    Returns:
        1 if any file exceeded the hard ceiling, else 0.
    """
    failed = False
    for raw in paths:
        path = Path(raw)
        if path.suffix not in SUFFIXES or not path.is_file():
            continue
        n = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        if n > FAIL:
            print(f"{path}:1: ERROR {n} lines > {FAIL}. Extract a helper (ADR-12).")
            failed = True
        elif n > WARN:
            print(f"{path}:1: warning {n} lines > {WARN} target. Consider extracting a helper.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(check(sys.argv[1:]))
