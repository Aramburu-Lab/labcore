"""Conformance levels: what a repo promises, and what it actually delivers.

Build plan §9.1 makes the standard ratcheted rather than all-or-nothing. A repo
declares a level in ``.labtemplate.yml`` and ``labdocs lint`` enforces exactly
that level and nothing above it, so a half-migrated repo gets a green CI instead
of a permanently-red one nobody looks at. A lint that fails a level-1 repo for
missing metadata has defeated the entire design.

Levels are cumulative: level N activates every code from levels 1..N.

| Level | Name | Codes it activates |
|---|---|---|
| 0 | Unmanaged | none — the repo has opted out |
| 1 | Adopted | LD008 (the five adoption files) |
| 2 | Documented | LD000, LD001, LD002, LD005, LD006, LD009 as a *warning* |
| 3 | Structured | LD003, LD004, and LD009 promoted to an *error* |
| 4 | Reproducible | LD007 |
| 5 | Verified | none here — ADR-12 style limits are ruff's and prek's job |

:func:`assess` answers the other half of the question: not what the repo claims,
but the highest level it genuinely meets. ``labdocs audit`` prints both, because
a repo declaring 3 while satisfying 2 is exactly the silent drift §9.4 asks to
be noticed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .walk import CONFIG_NAME, Project, load_config, walk_project

MAX_LEVEL = 5

# A repo with no `.labtemplate.yml` has not declared level 0 — it has declared
# nothing. Silence must not buy a green lint, so an undeclared tree is held to
# the full standard; only an explicit `conformance: 0` switches the linter off.
UNDECLARED_LEVEL = MAX_LEVEL

# Copier writes this, and `labdocs audit` keys on it. Without it there is no
# adoption to verify, so LD008 stays quiet rather than nagging an unmanaged tree.
ANSWERS_FILE = ".copier-answers.yml"

LEVEL1_FILES = (
    ANSWERS_FILE,
    CONFIG_NAME,
    "knowledge/overview.md",
    "AGENTS.md",
    "CLAUDE.md",
)

LOCKFILE = "pixi.lock"
WORKFLOW_DIR = ".github/workflows"
DIGEST_MARKER = "@sha256:"
DRAFT_CODE = "LD009"

# Build plan §9.3: a draft is a warning while a repo is still documenting itself,
# and an error once it claims structure.
DRAFT_ERROR_LEVEL = 3

# ADR-12's decomposition ceiling, ratcheted per D-005b. Mirrors the template's
# `.labtemplate.yml`; a repo that omits the key gets these.
DEFAULT_FILE_LENGTH = {"warn": 200, "fail": 600}

# Neither a git tree nor a static walk can see a green CI run or a job that
# landed on Kebnekaise, so assess() says so instead of guessing.
NOTES = (
    "level 4: execution on HPC2N via the launcher is not statically checkable",
    "level 5: CI being green is not statically checkable; only the workflow file "
    "and the ADR-12 ratchet are",
)


@dataclass(frozen=True)
class Level:
    """One rung of the conformance ladder."""

    number: int
    name: str
    requires: str
    codes: tuple[str, ...]


LEVELS: tuple[Level, ...] = (
    Level(0, "Unmanaged", "nothing", ()),
    Level(
        1,
        "Adopted",
        ".copier-answers.yml, .labtemplate.yml, knowledge/overview.md, AGENTS.md, "
        "CLAUDE.md. No code touched.",
        ("LD008",),
    ),
    Level(
        2,
        "Documented",
        "every script carries a codebase-meta block; explain_codebase.html and "
        "api_index.md render. Nothing moved or renamed.",
        ("LD000", "LD001", "LD002", "LD005", "LD006", DRAFT_CODE),
    ),
    Level(
        3,
        "Structured",
        "scripts/<lang>/ and outputs/NN_step/ layout; the ADR-11 naming linter "
        "passes; no draft metadata left.",
        ("LD003", "LD004", "LD010"),
    ),
    Level(
        4,
        "Reproducible",
        "pixi.lock committed; containers digest-pinned; runs on HPC2N via the standard launcher.",
        ("LD007",),
    ),
    Level(
        5,
        "Verified",
        "labcore used for viz/IO; ADR-12 style limits enforced; CI green.",
        (),
    ),
)

LEVELS_BY_NUMBER = {level.number: level for level in LEVELS}


@dataclass(frozen=True)
class LevelReport:
    """What a repo declares against what it can be shown to satisfy."""

    root: Path
    declared: int
    assessed: int
    drafts: int
    unmet: tuple[str, ...]
    notes: tuple[str, ...] = NOTES

    @property
    def drifted(self) -> bool:
        """True when the repo claims more than it delivers."""
        return self.assessed < self.declared


def read_config(root: Path) -> dict:
    """Read the template-management settings from ``.labtemplate.yml``.

    Args:
        root: Project root.

    Returns:
        A mapping with four keys, always present: ``conformance`` (int or None
        when undeclared), ``frozen`` (bool), ``naming_exempt`` (list of globs)
        and ``file_length`` (the ADR-12 ratchet, defaulted per key).
    """
    raw = load_config(root)
    declared = raw.get("conformance")
    # bool is an int subclass, and `conformance: true` is a typo, not a level.
    level = declared if isinstance(declared, int) and not isinstance(declared, bool) else None
    ratchet = raw.get("file_length")
    ratchet = ratchet if isinstance(ratchet, dict) else {}
    return {
        "conformance": level,
        "frozen": bool(raw.get("frozen", False)),
        "naming_exempt": [str(glob) for glob in (raw.get("naming_exempt") or [])],
        "file_length": {
            key: ratchet.get(key) if isinstance(ratchet.get(key), int) else default
            for key, default in DEFAULT_FILE_LENGTH.items()
        },
    }


def read_level(root: Path) -> int:
    """Read the declared conformance level.

    Args:
        root: Project root.

    Returns:
        The level from ``.labtemplate.yml``, clamped to 0..5. An absent file or
        an absent/invalid ``conformance:`` key declares nothing, which is 0.
    """
    declared = read_config(root)["conformance"]
    return 0 if declared is None else max(0, min(MAX_LEVEL, declared))


def has_declaration(root: Path) -> bool:
    """True when the repo carries a ``.labtemplate.yml`` at all.

    Args:
        root: Project root.

    Returns:
        Whether the declaration file exists, which is what distinguishes an
        explicit opt-out at level 0 from a tree that never declared anything.
    """
    return (Path(root) / CONFIG_NAME).is_file()


def resolve_level(root: Path, explicit: int | None = None) -> int:
    """Decide which level a lint run must enforce.

    Args:
        root: Project root.
        explicit: Level supplied by the caller, overriding the declaration.

    Returns:
        The explicit level when given, the declared level when the repo carries
        a ``.labtemplate.yml``, otherwise :data:`UNDECLARED_LEVEL`.
    """
    if explicit is not None:
        return max(0, min(MAX_LEVEL, explicit))
    return read_level(root) if has_declaration(root) else UNDECLARED_LEVEL


def requirements(level: int) -> set[str]:
    """List the lint codes active at a level.

    Args:
        level: Conformance level, 0..5.

    Returns:
        Every code from levels 1..N. Level 0 activates nothing.
    """
    return {code for rung in LEVELS if 0 < rung.number <= level for code in rung.codes}


def draft_severity(level: int) -> str:
    """Severity of `draft: true` metadata at a level.

    Build plan §9.3: a warning at level 2 and an error at level 3, which is what
    stops `labdocs adopt` drafts quietly becoming permanent.

    Args:
        level: Conformance level being enforced.

    Returns:
        ``"warning"`` at level 2 or below, ``"error"`` from level 3 up.
    """
    return "error" if level >= DRAFT_ERROR_LEVEL else "warning"


def checks_adoption_files(root: Path) -> bool:
    """Whether LD008 applies to this tree.

    Args:
        root: Project root.

    Returns:
        True once Copier has written ``.copier-answers.yml``. :func:`assess`
        checks the full manifest unconditionally, so a repo that declares a
        level without ever being adopted still shows up as drifted in the audit.
    """
    return (Path(root) / ANSWERS_FILE).is_file()


def assess(root: Path) -> LevelReport:
    """Find the highest level a repo can be shown to satisfy.

    Args:
        root: Project root.

    Returns:
        A report carrying the declared level, the assessed level, the number of
        draft blocks in the lint domain (``scripts/``, which is what gates level
        3), why the next rung failed, and what could not be checked statically.
    """
    # Imported here because lint.py imports this module for its gating table.
    from .lint import lint_project

    root = Path(root)
    project = walk_project(root)
    drafts = sum(1 for block in project.blocks if block.data.get("draft") is True)

    checks = {
        1: lambda: _missing_files(root, LEVEL1_FILES),
        2: lambda: _lint_blockers(lint_project(root, level=2)),
        3: lambda: _lint_blockers(lint_project(root, level=3)),
        4: lambda: _reproducible_blockers(root, project, lint_project(root, level=4)),
        5: lambda: _verified_blockers(root),
    }

    assessed, unmet = 0, ()
    for number in range(1, MAX_LEVEL + 1):
        failures = checks[number]()
        if failures:
            unmet = tuple(failures)
            break
        assessed = number

    return LevelReport(
        root=root,
        declared=read_level(root),
        assessed=assessed,
        drafts=drafts,
        unmet=unmet,
    )


# The template repo is the only place the level-1 file CONTENTS exist, and it must stay that way:
# three separate bugs in this project came from one definition living in two places. So labdocs
# never scaffolds them — it names the command that does.
TEMPLATE_REF = "gh:Aramburu-Lab/codebase_template"


def adoption_hint(root: Path) -> str | None:
    """The command to run when a repo has not been adopted yet, or None if it has.

    `labdocs adopt` on an un-adopted tree drafts metadata into a repo that has no
    `.copier-answers.yml` to hang an update channel off, which is the wrong order and
    silently produces a repo that can never take a template update. Worse, the migrator's
    natural next move is a bare `copier copy`, which **blocks on stdin forever** on any
    colliding file — `--defaults` answers the template's questions, not its conflict
    prompts (template issue #1).

    Returns:
        A multi-line remedy naming the missing files and the exact command, or None when every
        level-1 file is present.
    """
    missing = _missing_files(root, LEVEL1_FILES)
    if not missing:
        return None
    return "\n".join(
        [
            f"not adopted yet — {len(missing)} of {len(LEVEL1_FILES)} level-1 files are missing:",
            *(f"    {item}" for item in missing),
            "run this FIRST, from the repo root:",
            f"    copier copy --trust --overwrite --skip-tasks {TEMPLATE_REF} .",
            "--overwrite is REQUIRED: without it copier prompts per conflicting file",
            "and blocks on stdin with no terminal. It also REPLACES colliding files,",
            "so commit before running it.",
        ]
    )


def _missing_files(root: Path, names: tuple[str, ...]) -> list[str]:
    """Which of the required paths are absent."""
    return [f"missing {name}" for name in names if not (root / name).is_file()]


def _lint_blockers(findings: list) -> list[str]:
    """Error-severity findings, phrased for a level report."""
    return [
        f"{f.code} {f.path.name}:{f.line} {f.message}" for f in findings if f.severity == "error"
    ]


def _reproducible_blockers(root: Path, project: Project, findings: list) -> list[str]:
    """Level 4: a committed lockfile and containers pinned by digest."""
    blockers = _missing_files(root, (LOCKFILE,))
    blockers += [f"{f.code} {f.path.name}:{f.line} {f.message}" for f in findings]
    for block in project.blocks:
        image = str(block.data.get("container", ""))
        if image and DIGEST_MARKER not in image:
            blockers.append(f"{block.name}: container '{image}' is not digest-pinned")
    return blockers


def _verified_blockers(root: Path) -> list[str]:
    """Level 5: the statically visible half — a CI workflow and the ratchet."""
    workflows = root / WORKFLOW_DIR
    found = sorted(workflows.glob("*.y*ml")) if workflows.is_dir() else []
    blockers = [] if found else [f"no workflow under {WORKFLOW_DIR}/"]
    if "file_length" not in load_config(root):
        blockers.append("no file_length ratchet declared in .labtemplate.yml")
    return blockers
