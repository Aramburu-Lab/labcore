"""Where ADR-11 says a file belongs, worked out from the manifest.

The regime is ``scripts/<lang>/<name>.<ext>`` for code and
``outputs/<order>_<step>/out_<step>_<descriptor>.<ext>`` for data, and both
targets are fully determined by the declaring `codebase-meta` block — the
``order`` and ``name`` it carries — never by where the file happens to sit
today. That distinction is the whole point of this module. Deriving the target
from the current location only tidies a repo already living under ``outputs/``,
which is not the migration anyone needs: a brownfield repo writes to ``output/``,
``tmp/``, ``plots/`` and ``csv_outputs/``, and relocating exactly those paths is
what the level-2 to level-3 move *is*.

One walk yields three kinds of proposal:

* script moves — every script not already under ``scripts/<lang>/``;
* output renames — every declared output path, wherever it currently lives;
* unmappable paths — a path built at runtime (``plots/barplot_<theme>.pdf``) or
  a bare directory. Those are proposed but held back from the applyable set,
  because no static rewrite can reach a name that does not exist as a literal.
  They are still reported: an incomplete map that looks complete is the worse
  failure, so the map carries them commented out for a human to act on.

Scope: outputs come from `codebase-meta` declarations, which is what LD003
judges too, so an output no block declares is not proposed — declare it at level
2 first. ``results/`` belongs to nf-core and ``deliverables/`` names need a date
and a version no static reader can invent, so both are left alone.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from labcore.meta import MetaBlock

from .graph import LANGUAGES
from .lint import (
    EXEMPT_ROOTS,
    _is_naming_exempt,
    _name_violation,
    _relative,
    _split_name,
    _strip_theme,
)
from .walk import Project, load_config, walk_project

# Roots the naming regime does not govern; see the module docstring.
KEEP_ROOTS = frozenset(EXEMPT_ROOTS) | {"deliverables"}

# bin/ is a script root both the walker and the template's launcher rely on, so
# moving bin/lib_slurm.sh under scripts/ would break whatever sources it.
KEEP_SCRIPT_ROOT = "bin"

# A path carrying any of these is assembled at runtime — an interpolation, a
# glob, or a documented placeholder like <theme>. The literal names no file, so
# renaming it would move nothing and rewrite nothing.
RUNTIME_MARKERS = "<>${}*?"


@dataclass(frozen=True)
class Rename:
    """One proposed move, as repository-relative POSIX paths."""

    old: str
    new: str
    reason: str = ""


@dataclass(frozen=True)
class Proposals:
    """Every move ADR-11 implies, split by whether the codemod can perform it."""

    renames: list[Rename] = field(default_factory=list)
    unmappable: list[Rename] = field(default_factory=list)


def _slug(text: str) -> str:
    """Lowercase underscore string — ADR-11's one rule for every name.

    Angle brackets survive: a ``<theme>`` placeholder has to stay legible in the
    row a human is being asked to fix, and flattening it to ``theme`` would
    quietly propose a literal filename nobody wants.
    """
    return re.sub(r"[^a-z0-9<>]+", "_", text.lower()).strip("_")


def _output_name(rel: str, script: str) -> str:
    """Name one declared output ``out_<script>_<descriptor>.<ext>``."""
    stem, ext = _split_name(rel.rpartition("/")[2])
    base = _strip_theme(stem)
    theme = stem[len(base) :]

    slug = _slug(base)
    if slug.startswith("out_"):
        slug = slug[len("out_") :]
    if slug.startswith(f"{script}_"):
        slug = slug[len(script) + 1 :]
    elif slug == script:
        slug = ""
    # ADR-11 wants a descriptor *after* the script name, so an output whose whole
    # name was the script name still needs one.
    return f"out_{script}_{slug or 'result'}{theme}" + (f".{ext}" if ext else "")


def _script_moves(project: Project, exempt: list[str]) -> Iterator[Rename]:
    """Propose ``scripts/<lang>/`` for every script not already living there."""
    for path in sorted(project.script_paths):
        rel = _relative(project.root, path)
        lang = LANGUAGES.get(path.suffix)
        if lang is None or rel.split("/", 1)[0] == KEEP_SCRIPT_ROOT:
            continue
        if _is_naming_exempt(rel, exempt):
            continue

        parent, _, name = rel.rpartition("/")
        stem, ext = _split_name(name)
        home = f"scripts/{lang}"
        # A script already under its language directory keeps whatever sub-tree
        # it was filed into; only an illegal name is corrected.
        settled = parent == home or parent.startswith(f"{home}/")
        target = f"{parent if settled else home}/{_slug(stem)}" + (f".{ext}" if ext else "")
        if target == rel:
            continue
        why = "not a lowercase underscore string" if settled else f"{lang} scripts live in {home}/"
        yield Rename(rel, target, f"script; {why}")


def _output_moves(
    block: MetaBlock, exempt: list[str], seen: set[str]
) -> Iterator[tuple[Rename, bool]]:
    """Propose a target for one block's outputs, flagging the unreachable ones.

    Yields ``(rename, unmappable)``; an unmappable proposal is a statement of
    where the output belongs, not something the codemod can carry out.
    """
    step_dir = f"outputs/{block.order or 0:02d}_{block.name}"
    for output in block.outputs:
        rel = str(output.get("path") or "").strip()
        key = rel.rstrip("/")
        if not key or key in seen or _is_naming_exempt(rel, exempt):
            continue
        seen.add(key)
        if key.split("/", 1)[0] in KEEP_ROOTS:
            continue

        origin = f"declared by {block.name};"
        named = f"{step_dir}/{_output_name(rel, block.name)}"
        if any(marker in rel for marker in RUNTIME_MARKERS):
            why = "path is built at runtime; the script that writes it has to change"
            yield Rename(rel, named, f"{origin} {why}"), True
        # No extension: a directory or a prefix, never a file ADR-11 can name.
        elif not _split_name(key.rpartition("/")[2])[1]:
            why = "names a directory, not a file; its contents belong under the step directory"
            yield Rename(rel, f"{step_dir}/", f"{origin} {why}"), True
        elif named != rel:
            why = _name_violation(rel, block.name) or f"belongs in {step_dir}/"
            yield Rename(rel, named, f"{origin} {why}"), False


def propose(root: Path) -> Proposals:
    """Work out where every script and declared output belongs.

    Args:
        root: Project root, the directory holding the scripts.

    Returns:
        :class:`Proposals`, whose ``renames`` are safe to hand to
        :func:`labcore.labdocs.rename.apply_renames`, and whose ``unmappable``
        rows each need the script that builds the path changed first. A repo
        already at level 3 yields both lists empty.
    """
    root = Path(root)
    project = walk_project(root)
    exempt = list(load_config(root).get("naming_exempt") or [])

    renames = list(_script_moves(project, exempt))
    unmappable: list[Rename] = []
    seen: set[str] = set()
    for block in project.steps:
        for rename, blocked in _output_moves(block, exempt, seen):
            (unmappable if blocked else renames).append(rename)
    return Proposals(renames, unmappable)


def proposed_target(block: MetaBlock, rel: str) -> str | None:
    """Where one declared output belongs, or None when it is already right.

    Exposed so the linter and the rename map share a single answer. They used to
    disagree: the map only inspected paths already under ``outputs/`` and the
    linter's ADR-11 check did too, so a step still writing to ``output/`` passed
    level 3 untouched — which is how a repo reached that level with one step
    never migrated.

    Args:
        block: The declaring step.
        rel: The declared output path, relative to the project root.

    Returns:
        The target path, or None when the output is already there, is a runtime
        path no rename can reach, is a directory, or lives under a root the
        regime does not govern.
    """
    key = rel.rstrip("/")
    if not key or key.split("/", 1)[0] in KEEP_ROOTS:
        return None
    if any(marker in rel for marker in RUNTIME_MARKERS):
        return None
    if not _split_name(key.rpartition("/")[2])[1]:
        return None
    target = f"outputs/{block.order or 0:02d}_{block.name}/{_output_name(rel, block.name)}"
    return None if target == rel else target
