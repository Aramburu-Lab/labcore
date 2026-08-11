"""Discover scripts, parse their blocks, and build the labdocs project model.

The walk never aborts. A file whose block is unparseable becomes an entry in
``Project.errors`` rather than an exception, because one malformed script must
not hide the other twenty (build plan §5.3).

Two flavours, two carriers. An ``analysis`` project carries `codebase-meta` in
every script. A ``pipeline`` project is whatever ``nf-core pipelines create``
emitted, so its components are described by nf-core ``meta.yml`` sidecars, which
are read as-is: they have no ``order:`` or ``next:``, and inventing them would
fabricate a run order Nextflow decides at runtime.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from labcore.meta import COMMENT_TOKENS, MetaBlock, MetaError, extract_block

# Generated, vendored or transient trees. `outputs/` and `results/` hold data,
# never scripts; `work/` is Nextflow scratch and can contain thousands of files.
SKIP_DIRS = frozenset(
    {".git", "node_modules", "work", "results", ".venv", "__pycache__", "outputs"}
)

SIDECAR_ROOTS = ("modules", "subworkflows")
SIDECAR_NAME = "meta.yml"
CONFIG_NAME = ".labtemplate.yml"


@dataclass
class Component:
    """One nf-core ``meta.yml`` sidecar, parsed as-is."""

    path: Path
    data: dict

    @property
    def name(self) -> str:
        """Declared component name, or the enclosing directory name."""
        return self.data.get("name") or self.path.parent.name


@dataclass
class Project:
    """Everything a report or a linter needs to know about one repository."""

    root: Path
    blocks: list[MetaBlock] = field(default_factory=list)
    missing: list[Path] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)
    flavour: str = "analysis"
    components: list[Component] = field(default_factory=list)

    @property
    def script_paths(self) -> list[Path]:
        """Every script the walk found, whether or not it carried a block."""
        return [block.path for block in self.blocks] + list(self.missing)

    @property
    def steps(self) -> list[MetaBlock]:
        """Non-exempt blocks in run order."""
        return sorted(
            (b for b in self.blocks if not b.is_exempt),
            key=lambda b: (b.order if b.order is not None else 0, b.name),
        )

    @property
    def by_name(self) -> dict[str, MetaBlock]:
        """Step name to block. Exempt helpers are not steps and are excluded."""
        return {b.name: b for b in self.blocks if not b.is_exempt}


def detect_flavour(root: Path) -> str:
    """Classify a project as an nf-core pipeline or a plain analysis repo.

    Args:
        root: Project root.

    Returns:
        ``"pipeline"`` when both ``main.nf`` and ``nextflow.config`` sit at the
        root, otherwise ``"analysis"``.
    """
    is_pipeline = (root / "main.nf").is_file() and (root / "nextflow.config").is_file()
    return "pipeline" if is_pipeline else "analysis"


def load_config(root: Path) -> dict:
    """Read ``.labtemplate.yml``.

    Args:
        root: Project root.

    Returns:
        The parsed mapping, or an empty dict when the file is absent or blank.
    """
    config_path = root / CONFIG_NAME
    if not config_path.is_file():
        return {}
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _is_skipped(name: str) -> bool:
    """True for directories the walk must not descend into."""
    return name in SKIP_DIRS or name.startswith(".")


def iter_scripts(base: Path) -> Iterator[Path]:
    """Yield every script file under a directory, depth-first and sorted.

    Args:
        base: Directory to walk. A missing directory yields nothing.

    Yields:
        Files whose extension has a known comment token, in a stable order so
        two runs on the same tree produce byte-identical reports.
    """
    if not base.is_dir():
        return
    for entry in sorted(base.iterdir()):
        if entry.is_dir():
            if not _is_skipped(entry.name):
                yield from iter_scripts(entry)
        elif entry.suffix in COMMENT_TOKENS:
            yield entry


def _iter_sidecars(root: Path) -> Iterator[Path]:
    """Yield nf-core meta.yml sidecars under modules/ and subworkflows/."""
    for top in SIDECAR_ROOTS:
        base = root / top
        if not base.is_dir():
            continue
        for candidate in sorted(base.rglob(SIDECAR_NAME)):
            if any(_is_skipped(part) for part in candidate.relative_to(root).parts):
                continue
            yield candidate


def _read_sidecars(root: Path, project: Project) -> None:
    """Parse each sidecar into a Component, recording unreadable ones."""
    for path in _iter_sidecars(root):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError) as exc:
            project.errors.append((path, f"meta.yml is unreadable: {exc}"))
            continue
        if isinstance(data, dict):
            project.components.append(Component(path=path, data=data))
        else:
            project.errors.append((path, "meta.yml is not a mapping"))


def project_scripts(root: Path) -> Iterator[Path]:
    """Yield every script the linter is responsible for.

    Scripts normally live under ``scripts/``, but conformance level 2 requires a
    metadata block on every script while explicitly forbidding any file to be
    moved (build plan §9.1). A brownfield repo therefore has them at the root,
    and looking only under ``scripts/`` made `labdocs lint` exit 0 on a tree full
    of undocumented scripts — a vacuous pass on exactly the repo shape level 2
    exists for, and the state `labdocs adopt` already drafts blocks into.

    Args:
        root: Project root.

    Yields:
        Scripts under ``scripts/``, then any at the root itself.
    """
    seen: set[Path] = set()
    for path in iter_scripts(root / "scripts"):
        seen.add(path)
        yield path

    # Root level only. Recursing would sweep in vendored trees and per-analysis
    # output directories that no repo intends as source.
    if not root.is_dir():
        return
    for entry in sorted(root.iterdir()):
        if entry.is_file() and entry.suffix in COMMENT_TOKENS and entry not in seen:
            yield entry


def walk_project(root: Path) -> Project:
    """Build the project model for one repository.

    Args:
        root: Project root. Scripts are taken from ``scripts/`` and from the root
            itself, so an unrestructured repo is still graded.

    Returns:
        A Project whose ``blocks``, ``missing`` and ``errors`` together account
        for every script found. Pipeline projects also carry an nf-core component
        inventory in ``components``.
    """
    project = Project(root=root, flavour=detect_flavour(root))

    for path in project_scripts(root):
        try:
            block = extract_block(path)
        except MetaError as exc:
            project.errors.append((path, str(exc)))
            continue
        if block is None:
            project.missing.append(path)
        else:
            project.blocks.append(block)

    if project.flavour == "pipeline":
        _read_sidecars(root, project)

    return project
