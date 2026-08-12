"""Shared lab logic, pinned by tag rather than copied into projects (ADR-4).

Submodules are imported lazily: `labcore.viz` needs matplotlib, `labcore.io` needs
pyarrow, and `labcore.stats` needs scipy, but a project that only wants the
metadata parser should not have to install any of them.
"""

from __future__ import annotations

__version__ = "0.3.5"

__all__ = ["__version__", "cli", "frames", "io", "meta", "paths", "repro", "stats", "viz"]
