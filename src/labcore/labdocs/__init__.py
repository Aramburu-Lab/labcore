"""The docs engine: walk a project, validate its metadata, render its report.

`explain_codebase.html`, `codebase_manifest.json`, `knowledge/codebase_map.md` and
`knowledge/api_index.md` are all generated here and never hand-written (ADR-10).
A prek hook fails the commit when any of them goes stale.
"""

from __future__ import annotations

__all__ = ["api", "audit", "cli", "graph", "lint", "render", "walk"]
