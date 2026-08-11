"""Enforce the labdocs contract on a project tree.

Each failure the reference calls out (§4, "What the engine must reject") gets its
own greppable code, so a CI log says *which* rule broke rather than "lint failed":

| Code | Failure |
|---|---|
| LD000 | block does not parse, or violates the schema |
| LD001 | script in ``scripts/`` with no `codebase-meta` block |
| LD002 | an output with no ``desc:`` |
| LD003 | a filename violating the ADR-11 naming regime |
| LD004 | ``next:`` pointing at a script that does not exist |
| LD005 | an input with neither ``from:`` nor ``external: true`` |
| LD006 | ``from: X`` where X does not list that path among its outputs |
| LD007 | a ``container:`` on docker.io — dev-only (D-004), warning not error |
| LD008 | a file the declared conformance level requires is absent |
| LD009 | ``draft: true`` metadata — warning at level 2, error at level 3 |

Every code is gated by the repo's conformance level (build plan §9.1); see
``levels.py`` for the table. A finding above the declared level is not reported
at all, because a permanently-red CI is a CI nobody reads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fnmatch import fnmatch
from functools import lru_cache
from pathlib import Path

from labcore.meta import MetaBlock, validate_block

from .levels import (
    DRAFT_CODE,
    LEVEL1_FILES,
    checks_adoption_files,
    draft_severity,
    read_config,
    requirements,
    resolve_level,
)
from .walk import Project, walk_project

SCRIPT_STEM = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")
DELIVERABLE_STEM = re.compile(r"^\d{4}-\d{2}-\d{2}_[a-z0-9]+(_[a-z0-9]+)*_v\d+$")
VERSION_SEGMENT = re.compile(r"^v\d+$")

# D-005a puts a theme token on every figure filename. Without reserving it here,
# every figure the template teaches you to write would fail the linter.
THEME_SUFFIXES = ("_white", "_black")

# nf-core owns results/ and names it its own way (ADR-11).
EXEMPT_ROOTS = ("results",)

DEV_REGISTRY = "docker.io/"


@dataclass(frozen=True)
class Finding:
    """One lint result, located precisely enough to jump to."""

    code: str
    path: Path
    line: int
    message: str
    severity: str = "error"


@lru_cache(maxsize=256)
def _file_lines(path: Path) -> tuple[str, ...]:
    """Cached file contents; a lint run is a one-shot process."""
    return tuple(path.read_text(encoding="utf-8").splitlines())


def _locate(path: Path, needle: str, default: int) -> int:
    """1-based line carrying `needle`, or `default` when it is not found."""
    for number, line in enumerate(_file_lines(path), start=1):
        if needle in line:
            return number
    return default


def _split_name(filename: str) -> tuple[str, str]:
    """Split on the first dot, so `out_fetch_reads.fq.gz` keeps its full stem."""
    stem, _, ext = filename.partition(".")
    return stem, ext


def _strip_theme(stem: str) -> str:
    """Drop a reserved trailing `_white`/`_black` theme token (D-005a)."""
    for suffix in THEME_SUFFIXES:
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def _relative(root: Path, path: Path) -> str:
    """Posix path relative to the project root, falling back to the name."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _is_naming_exempt(rel: str, globs: list[str]) -> bool:
    """True when a path matches an exemption glob from .labtemplate.yml."""
    return any(fnmatch(rel, glob) for glob in globs)


def _name_violation(rel: str, script: str) -> str | None:
    """Return why a declared output path breaks ADR-11, or None if it is fine."""
    if " " in rel:
        return f"'{rel}' contains a space; underscores only"
    top = rel.split("/", 1)[0]
    if top in EXEMPT_ROOTS:
        return None
    stem = _strip_theme(_split_name(Path(rel).name)[0])
    if top == "outputs" and not (stem.startswith(f"out_{script}_") and SCRIPT_STEM.match(stem)):
        return f"'{rel}' must be named out_{script}_<descriptor>.<ext>"
    if top == "deliverables" and not DELIVERABLE_STEM.match(stem):
        return f"'{rel}' must be named YYYY-MM-DD_description_vN.<ext>"
    return None


def _script_name_findings(project: Project, exempt: list[str]) -> list[Finding]:
    """LD003 over the script filenames themselves."""
    findings = []
    for path in sorted(project.script_paths):
        rel = _relative(project.root, path)
        if _is_naming_exempt(rel, exempt):
            continue
        stem = _split_name(path.name)[0]
        bad_version = any(VERSION_SEGMENT.match(part) for part in stem.split("_"))
        if not SCRIPT_STEM.match(stem) or bad_version:
            findings.append(
                Finding(
                    "LD003",
                    path,
                    1,
                    f"'{rel}' is not a single lowercase underscore string; "
                    "no hyphens, no dates, no version suffix",
                )
            )
    return findings


def _output_findings(block: MetaBlock, root: Path, exempt: list[str]) -> list[Finding]:
    """LD002 and LD003 over one block's declared outputs."""
    findings = []
    for output in block.outputs:
        path = output.get("path")
        needle = path or output.get("pipe", "")
        line = _locate(block.path, needle, block.start_line) if needle else block.start_line
        if not str(output.get("desc", "")).strip():
            findings.append(Finding("LD002", block.path, line, f"output '{needle}' has no desc:"))
        # A piped output has no filename, so there is nothing to name.
        if not path:
            continue
        rel = _relative(root, Path(path))
        if _is_naming_exempt(rel, exempt):
            continue
        violation = _name_violation(rel, block.name)
        if violation:
            findings.append(Finding("LD003", block.path, line, violation))
    return findings


def _input_findings(project: Project, block: MetaBlock) -> list[Finding]:
    """LD005 and LD006 over one block's declared inputs."""
    findings = []
    known = project.by_name
    for spec in block.inputs:
        path = str(spec.get("path", ""))
        line = _locate(block.path, path, block.start_line)
        producer = spec.get("from")
        if producer is None and spec.get("external") is not True:
            findings.append(
                Finding(
                    "LD005",
                    block.path,
                    line,
                    f"input '{path}' declares neither from: nor external: true",
                )
            )
            continue
        if producer is None:
            continue
        upstream = known.get(producer)
        if upstream is None:
            findings.append(
                Finding("LD006", block.path, line, f"from: '{producer}' is not a known script")
            )
        elif path not in {str(out.get("path")) for out in upstream.outputs}:
            findings.append(
                Finding("LD006", block.path, line, f"'{producer}' does not output '{path}'")
            )
    return findings


def _next_findings(project: Project, block: MetaBlock) -> list[Finding]:
    """LD004 over one block's downstream edges."""
    known = project.by_name
    return [
        Finding(
            "LD004",
            block.path,
            _locate(block.path, target, block.start_line),
            f"next: '{target}' names a script that does not exist",
        )
        for target in block.next_steps
        if target not in known
    ]


def _container_findings(block: MetaBlock) -> list[Finding]:
    """LD007: a docker.io reference is a dev convenience, never an archive."""
    image = str(block.data.get("container", ""))
    if not image.startswith(DEV_REGISTRY):
        return []
    return [
        Finding(
            "LD007",
            block.path,
            _locate(block.path, image, block.start_line),
            f"container '{image}' is a dev-only reference; pin a digest before archiving",
            severity="warning",
        )
    ]


def _block_findings(project: Project, block: MetaBlock, exempt: list[str]) -> list[Finding]:
    """Every check that reads a single block, plus the schema fallback."""
    findings = (
        _output_findings(block, project.root, exempt)
        + _input_findings(project, block)
        + _next_findings(project, block)
        + _container_findings(block)
    )
    schema_errors = validate_block(block)
    # The schema's top-level oneOf collapses to "not valid under any of the given
    # schemas" for a missing desc or a bare input — useless next to LD002/LD005,
    # so it is only surfaced when neither of those already explained the block.
    if schema_errors and not any(f.code in {"LD002", "LD005"} for f in findings):
        findings.append(Finding("LD000", block.path, block.start_line, "; ".join(schema_errors)))
    return findings


def _adoption_findings(root: Path) -> list[Finding]:
    """LD008 over the level-1 file manifest."""
    return [
        Finding("LD008", root / name, 1, f"conformance level 1 requires {name}")
        for name in LEVEL1_FILES
        if not (root / name).is_file()
    ]


def _draft_findings(project: Project, level: int) -> list[Finding]:
    """LD009 over blocks `labdocs adopt` wrote and nobody has reviewed."""
    return [
        Finding(
            DRAFT_CODE,
            block.path,
            _locate(block.path, "draft:", block.start_line),
            "draft: true metadata has not been reviewed",
            severity=draft_severity(level),
        )
        for block in project.blocks
        if block.data.get("draft") is True
    ]


def lint_project(root: Path, *, level: int | None = None) -> list[Finding]:
    """Lint one project tree at its conformance level.

    Args:
        root: Project root, the directory holding ``scripts/``.
        level: Level to enforce, overriding the declaration. When None the
            level comes from ``.labtemplate.yml``; a tree with no declaration
            is held to the full standard rather than let off.

    Returns:
        Findings sorted by path, then line, then code, filtered to the codes the
        level activates. A repo at level 0 has opted out and returns nothing.
        The caller decides the exit status; only ``severity == "error"`` should
        make it non-zero.
    """
    root = Path(root)
    resolved = resolve_level(root, level)
    if resolved < 1:
        return []

    project = walk_project(root)
    exempt = read_config(root)["naming_exempt"]

    findings = [Finding("LD000", path, 1, message) for path, message in project.errors]
    findings += [Finding("LD001", path, 1, "no codebase-meta block") for path in project.missing]
    findings += _script_name_findings(project, exempt)
    findings += _draft_findings(project, resolved)
    for block in project.blocks:
        findings += _block_findings(project, block, exempt)
    if checks_adoption_files(root):
        findings += _adoption_findings(root)

    active = requirements(resolved)
    return sorted(
        (f for f in findings if f.code in active),
        key=lambda f: (str(f.path), f.line, f.code),
    )
