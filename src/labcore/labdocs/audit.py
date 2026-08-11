"""Cross-repo template drift: which projects are stale, frozen, or still drafting.

Build plan §9 uses this table as the migration backlog, so a frozen repo is
reported *as frozen* rather than omitted — a row that disappears looks like a
repo that was fixed, and the whole point is noticing repos that quietly drift.

Every row carries two levels: the one the repo *declares* in ``.labtemplate.yml``
and the one :func:`labcore.labdocs.levels.assess` can show it *satisfies*. A repo
declaring 3 while satisfying 2 is the silent drift §9.4 asks to be caught, and it
is invisible if only one number is printed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from labcore.meta import COMMENT_TOKENS, MetaError, extract_block

from .levels import ANSWERS_FILE, assess

TEMPLATE_FILE = ".labtemplate.yml"
SEARCH_DEPTH = 2
SKIP_DIRS = {".git", ".venv", "node_modules", "work", "outputs", "results", "__pycache__"}


@dataclass(frozen=True)
class RepoStatus:
    """One project's standing against the template."""

    path: Path
    template_version: str | None
    conformance: int | None
    frozen: bool
    drafts: int
    assessed: int = 0

    @property
    def drifted(self) -> bool:
        """True when the repo satisfies less than it declares."""
        return self.assessed < (self.conformance or 0)


def audit_repos(paths: list[Path]) -> list[RepoStatus]:
    """Report template drift for every project under the given paths.

    Args:
        paths: Directories to search. Each is recursed to depth 2 looking for a
            ``.copier-answers.yml``; a path that is itself a project counts.

    Returns:
        One status per project found, sorted by path and de-duplicated across
        overlapping search roots.
    """
    found: dict[Path, RepoStatus] = {}
    for path in paths:
        for project in _find_projects(Path(path)):
            found.setdefault(project, _status(project))
    return [found[key] for key in sorted(found)]


def render_audit(rows: list[RepoStatus]) -> str:
    """Render statuses as a markdown table.

    Args:
        rows: Statuses from :func:`audit_repos`.

    Returns:
        A markdown table, or a one-line notice when nothing was found.
    """
    if not rows:
        return "No projects with a .copier-answers.yml found."
    lines = [
        "| Repo | Template | Declared | Assessed | Frozen | Drafts |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        declared = "-" if row.conformance is None else str(row.conformance)
        lines.append(
            f"| {row.path} | {row.template_version or 'unknown'} | {declared} | "
            f"{row.assessed} | {'yes' if row.frozen else 'no'} | {row.drafts} |"
        )
    drifted = [row for row in rows if row.drifted]
    if drifted:
        # Called out under the table because two adjacent numbers disagreeing is
        # exactly what a reader skims past.
        lines.append("")
        lines.append(
            "Drifted (assessed below declared): " + ", ".join(str(row.path) for row in drifted)
        )
    return "\n".join(lines)


def _find_projects(root: Path) -> list[Path]:
    """Directories carrying a .copier-answers.yml, at most SEARCH_DEPTH below root."""
    if not root.is_dir():
        return []
    found = []
    for depth in range(SEARCH_DEPTH + 1):
        pattern = "/".join(["*"] * depth + [ANSWERS_FILE]) if depth else ANSWERS_FILE
        found += [answers.parent for answers in root.glob(pattern)]
    return sorted(set(found))


def _status(project: Path) -> RepoStatus:
    answers = _load_yaml(project / ANSWERS_FILE)
    template = _load_yaml(project / TEMPLATE_FILE)
    commit = answers.get("_commit")
    conformance = template.get("conformance")
    return RepoStatus(
        path=project,
        template_version=str(commit) if commit is not None else None,
        conformance=conformance if isinstance(conformance, int) else None,
        frozen=bool(template.get("frozen", False)),
        drafts=_count_drafts(project),
        assessed=assess(project).assessed,
    )


def _load_yaml(path: Path) -> dict:
    """Parse a YAML mapping, treating a missing or malformed file as empty."""
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _count_drafts(project: Path) -> int:
    """Number of `draft: true` metadata blocks — the un-reviewed output of `labdocs adopt`."""
    count = 0
    for path in _sources(project):
        try:
            block = extract_block(path)
        except (MetaError, OSError, UnicodeDecodeError):
            continue
        if block is not None and block.data.get("draft") is True:
            count += 1
    return count


def _sources(project: Path) -> list[Path]:
    """Every file under the project that could carry a metadata block."""
    out = []
    for path in project.rglob("*"):
        if path.suffix not in COMMENT_TOKENS or not path.is_file():
            continue
        if SKIP_DIRS.intersection(path.parts):
            continue
        out.append(path)
    return out
