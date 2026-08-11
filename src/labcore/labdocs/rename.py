"""Rename outputs onto the ADR-11 regime without breaking their references.

Build plan §9.3: reaching conformance level 3 means renaming outputs to
``out_<script>_<descriptor>``, and every such rename breaks every downstream
reference to it. Doing that by hand across scripts, configs and notebooks is how
a migration stalls or, worse, silently corrupts a pipeline.

So the move and the reference rewrite are one operation. :func:`apply_renames`
does ``git mv`` **and** rewrites every reference across the whole tree, then
stages both halves for a single commit. The build plan's advice is to review the
*map*, not the diff, which is why :func:`write_rename_map` emits an editable TSV
carrying the reason for every row. Nothing moves unless ``dry_run=False`` is
passed explicitly.

Matching is deliberately conservative — whole paths, and bare basenames only
where the surrounding text is not itself a literal path. That is what keeps a
rename of ``counts.csv`` from corrupting ``raw_counts.csv`` or ``raw/counts.csv``.

Scope: proposals come from `codebase-meta` output declarations, which is what
LD003 judges too, so an output no block declares is not proposed — declare it at
level 2 first. ``deliverables/`` names need a date and a version no static reader
can invent, so those stay LD003 findings for a human.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from .lint import _is_naming_exempt, _name_violation, _split_name, _strip_theme
from .walk import load_config, walk_project

# Every text carrier that can name a path. A missed reference is a broken
# pipeline, so this errs wide: workflow YAML under .github, a README under
# outputs/, and nextflow.config all count.
SCAN_SUFFIXES = frozenset({
    ".py", ".r", ".R", ".sh", ".bash", ".nf", ".rs", ".jl", ".pl", ".smk", ".ipynb",
    ".toml", ".yml", ".yaml", ".cfg", ".ini", ".config", ".json", ".md", ".txt",
})  # fmt: skip
SCAN_NAMES = frozenset({"Makefile", "Dockerfile", "Snakefile", "justfile"})

# Generated, vendored or transient trees. Hidden directories are NOT skipped
# wholesale: .github/workflows names output paths and must be rewritten too.
SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", "work", ".nextflow",
    ".pixi", "target", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".ipynb_checkpoints", ".copier",
})  # fmt: skip

# A path reference is never megabytes long; anything this big is a data blob that
# happens to carry a scannable extension.
MAX_SCAN_BYTES = 4 * 1024 * 1024

MAP_COLUMNS = ("old", "new", "reason")
MAP_PREAMBLE = (
    "# labdocs rename map. Edit the `new` column; delete any row you do not want.",
    "# Tab-separated: old<TAB>new<TAB>reason. Blank lines and # lines are ignored.",
    "# Apply with: labdocs adopt --apply-renames <this file>",
)

# What makes a hit part of a longer name rather than a reference to the file. On
# the left, `_`/`-`/`.` stop `counts.csv` matching inside `raw_counts.csv`. On the
# right, `/` stops a file name matching a directory, and a dot only disqualifies
# when another extension follows it — otherwise the full stop ending the sentence
# "reads counts.csv." would hide a real reference.
_LEFT_BAD = r"(?<![A-Za-z0-9_.\-])"
_RIGHT_BAD = r"(?![A-Za-z0-9_\-/])(?!\.[A-Za-z0-9])"

# A `/` before a bare basename means the text already spells out a directory.
# Rewriting is only safe when that directory is interpolated at runtime
# (`f"{outdir}/counts.csv"`, `$OUT/counts.csv`) — a literal prefix like
# `raw/counts.csv` names a different file and must be left alone.
_INTERPOLATED_TAIL = re.compile(r"(?:[})\]]|\$[A-Za-z_]\w*)$")


class RenameError(RuntimeError):
    """A rename set is unsafe or unusable, so nothing was done.

    Carries every problem found, not the first: fixing a map one refusal at a
    time is its own kind of migration stall.
    """


@dataclass(frozen=True)
class Rename:
    """One proposed move, as repository-relative POSIX paths."""

    old: str
    new: str
    reason: str = ""


@dataclass(frozen=True)
class Move:
    """What happened, or would happen, to one file on disk."""

    rename: Rename
    method: str
    performed: bool


@dataclass(frozen=True)
class Rewrite:
    """One reference rewritten, located precisely enough to review."""

    rel: str
    line: int
    before: str
    after: str
    cell: int | None = None

    @property
    def where(self) -> str:
        """``path:line``, or ``path:cellN:line`` inside a notebook."""
        if self.cell is None:
            return f"{self.rel}:{self.line}"
        return f"{self.rel}:cell{self.cell}:{self.line}"


@dataclass
class RenameReport:
    """Everything one codemod run moved, rewrote, or declined to touch."""

    root: Path
    dry_run: bool
    moves: list[Move] = field(default_factory=list)
    rewrites: list[Rewrite] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def files_rewritten(self) -> list[str]:
        """Distinct files carrying at least one rewritten reference."""
        return sorted({r.rel for r in self.rewrites})


# --- Matching ---------------------------------------------------------------


def _base(rel: str) -> str:
    return rel.rpartition("/")[2]


def _needle_re(needle: str) -> re.Pattern[str]:
    """Compile a boundary-guarded literal matcher; never a substring match."""
    return re.compile(_LEFT_BAD + re.escape(needle) + _RIGHT_BAD)


def _basename_ok(text: str, start: int) -> bool:
    """True when a bare-basename hit at `start` is safe to rewrite."""
    if start == 0 or text[start - 1] != "/":
        return True
    return bool(_INTERPOLATED_TAIL.search(text[: start - 1]))


@dataclass(frozen=True)
class _Rule:
    """One search-and-replace, plus whether bare-basename guarding applies."""

    pattern: re.Pattern[str]
    replacement: str
    bare: bool


def _rules_for(renames: Iterable[Rename]) -> list[_Rule]:
    """Build the whole-path and basename rules for a rename set."""
    rules: list[_Rule] = []
    for rename in renames:
        rules.append(_Rule(_needle_re(rename.old), rename.new, bare=False))
        if _base(rename.old) != _base(rename.new):
            rules.append(_Rule(_needle_re(_base(rename.old)), _base(rename.new), bare=True))
    return rules


def _rewrite(text: str, rules: list[_Rule]) -> str:
    """Apply every rule in one pass, longest match first, skipping overlaps.

    One pass, not one per rule: that is what stops a rename's own output being
    re-matched by the next rule in the list.
    """
    spans: list[tuple[int, int, str]] = []
    for rule in rules:
        for match in rule.pattern.finditer(text):
            if rule.bare and not _basename_ok(text, match.start()):
                continue
            spans.append((match.start(), match.end(), rule.replacement))
    if not spans:
        return text

    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    out: list[str] = []
    cursor = 0
    for start, end, replacement in spans:
        if start < cursor:
            continue
        out.append(text[cursor:start])
        out.append(replacement)
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


# --- Proposal and map -------------------------------------------------------


def _proposed(rel: str, script: str) -> str:
    """Propose ``out_<script>_<descriptor>`` for one output path."""
    parent, _, name = rel.rpartition("/")
    stem, ext = _split_name(name)
    base = _strip_theme(stem)
    theme = stem[len(base) :]

    slug = re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_")
    if slug.startswith("out_"):
        slug = slug[len("out_") :]
    if slug.startswith(f"{script}_"):
        slug = slug[len(script) + 1 :]
    elif slug == script:
        slug = ""
    # ADR-11 wants a descriptor *after* the script name, so an output whose whole
    # name was the script name still needs one.
    new_name = f"out_{script}_{slug or 'result'}{theme}" + (f".{ext}" if ext else "")
    return f"{parent}/{new_name}" if parent else new_name


def propose_renames(root: Path) -> list[Rename]:
    """Propose an ADR-11 name for every misnamed output a script declares.

    Args:
        root: Project root, the directory holding ``scripts/``.

    Returns:
        One :class:`Rename` per declared output whose path violates the naming
        regime, each carrying the violation and the declaring script as its
        reason. Conforming outputs are absent, so this returns an empty list on
        a repo already at level 3.
    """
    root = Path(root)
    project = walk_project(root)
    exempt = list(load_config(root).get("naming_exempt") or [])

    renames: list[Rename] = []
    seen: set[str] = set()
    for block in sorted(project.blocks, key=lambda b: str(b.path)):
        for output in block.outputs:
            rel = str(output.get("path") or "").strip()
            if not rel.startswith("outputs/") or rel in seen or _is_naming_exempt(rel, exempt):
                continue
            violation = _name_violation(rel, block.name)
            new_rel = _proposed(rel, block.name)
            if violation is None or new_rel == rel:
                continue
            seen.add(rel)
            renames.append(Rename(rel, new_rel, f"declared by {block.name}; {violation}"))
    return renames


def write_rename_map(root: Path, dest: Path) -> Path:
    """Write an editable rename map for review.

    The map is the review surface (build plan §9.3, "review the map, not the
    diff"), so every row carries the reason it was proposed rather than a bare
    old-to-new pair a reviewer would have to reconstruct.

    Args:
        root: Project root to inspect.
        dest: File to write; parent directories are created.

    Returns:
        The path written.
    """
    lines = [*MAP_PREAMBLE, "\t".join(MAP_COLUMNS)]
    lines += [f"{r.old}\t{r.new}\t{r.reason}" for r in propose_renames(root)]

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dest


def read_rename_map(path: Path) -> list[Rename]:
    """Read a rename map back after a human has edited it.

    Args:
        path: TSV written by :func:`write_rename_map`, possibly edited.

    Returns:
        The parsed renames, in file order.

    Raises:
        RenameError: On any row that is not three tab-separated fields, or whose
            old and new are not both set. A half-parsed map is more dangerous
            than no map, so one bad row rejects the whole file.
    """
    problems: list[str] = []
    renames: list[Rename] = []
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.rstrip("\r")
        fields = line.split("\t")
        if not line.strip() or line.lstrip().startswith("#") or fields[:2] == ["old", "new"]:
            continue
        if len(fields) != len(MAP_COLUMNS):
            problems.append(f"{path}:{number}: expected {len(MAP_COLUMNS)} tab-separated fields")
            continue
        old, new, reason = (f.strip() for f in fields)
        if not old or not new:
            problems.append(f"{path}:{number}: old and new must both be set")
            continue
        renames.append(Rename(old, new, reason))

    if problems:
        raise RenameError("rename map is malformed:\n  " + "\n  ".join(problems))
    return renames


# --- Documents --------------------------------------------------------------


@dataclass
class _Doc:
    """A scannable file split into rewritable segments.

    A notebook is JSON, so its segments are the strings inside each cell's
    ``source`` array — never the raw file text, which would corrupt the JSON.
    """

    rel: str
    segments: list[str]
    slots: list[tuple[int, int | None]] | None = None
    notebook: dict | None = None

    def location(self, index: int) -> tuple[int | None, int]:
        """Cell number and 1-based line for one segment."""
        if self.slots is None:
            return None, index + 1
        cell, offset = self.slots[index]
        return cell, (offset or 0) + 1


def _iter_files(root: Path) -> Iterator[Path]:
    """Yield every scannable file in the tree, in a stable order."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix in SCAN_SUFFIXES or name in SCAN_NAMES:
                yield path


def _dump_notebook(doc: dict) -> str:
    """Serialise a notebook the way Jupyter writes one."""
    return json.dumps(doc, indent=1, ensure_ascii=False) + "\n"


def _load_notebook(rel: str, text: str, warnings: list[str]) -> _Doc | None:
    """Split a notebook's cell sources into segments, or warn and skip it."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        warnings.append(f"{rel}: not valid JSON, left untouched ({exc})")
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("cells"), list):
        warnings.append(f"{rel}: no cells array, left untouched")
        return None

    segments: list[str] = []
    slots: list[tuple[int, int | None]] = []
    for cell_index, cell in enumerate(doc["cells"]):
        source = cell.get("source") if isinstance(cell, dict) else None
        if isinstance(source, str):
            segments.append(source)
            slots.append((cell_index, None))
        elif isinstance(source, list):
            for line_index, line in enumerate(source):
                if isinstance(line, str):
                    segments.append(line)
                    slots.append((cell_index, line_index))
    if _dump_notebook(doc) != text:
        warnings.append(f"{rel}: notebook JSON will be reformatted by the round-trip")
    return _Doc(rel=rel, segments=segments, slots=slots, notebook=doc)


def _load_doc(path: Path, root: Path, warnings: list[str]) -> _Doc | None:
    """Read one file into segments, warning on anything unreadable or huge."""
    rel = path.relative_to(root).as_posix()
    try:
        if path.stat().st_size > MAX_SCAN_BYTES:
            warnings.append(f"{rel}: larger than {MAX_SCAN_BYTES} bytes, not scanned")
            return None
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        warnings.append(f"{rel}: unreadable as UTF-8, left untouched ({exc})")
        return None
    if path.suffix == ".ipynb":
        return _load_notebook(rel, text, warnings)
    # split, not splitlines: joining on "\n" then round-trips byte for byte.
    return _Doc(rel=rel, segments=text.split("\n"))


def _render(doc: _Doc) -> str:
    """Rebuild a document's on-disk text from its segments."""
    if doc.notebook is None or doc.slots is None:
        return "\n".join(doc.segments)
    for index, segment in enumerate(doc.segments):
        cell, offset = doc.slots[index]
        if offset is None:
            doc.notebook["cells"][cell]["source"] = segment
        else:
            doc.notebook["cells"][cell]["source"][offset] = segment
    return _dump_notebook(doc.notebook)


def _rewrite_doc(doc: _Doc, rules: list[_Rule], rewrites: list[Rewrite]) -> bool:
    """Rewrite every reference in one document, recording each hit."""
    changed = False
    for index, segment in enumerate(doc.segments):
        updated = _rewrite(segment, rules)
        if updated == segment:
            continue
        cell, line = doc.location(index)
        rewrites.append(Rewrite(doc.rel, line, segment.rstrip("\n"), updated.rstrip("\n"), cell))
        doc.segments[index] = updated
        changed = True
    return changed


# --- Git and validation -----------------------------------------------------


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)


def _status(root: Path) -> list[tuple[str, str]]:
    """Porcelain status as (code, path) pairs, untracked listed individually."""
    entries = []
    for line in _git(root, "status", "--porcelain", "-uall").stdout.splitlines():
        # A rename entry reads "old -> new"; the new path is the one on disk.
        entries.append((line[:2].strip() or "??", line[3:].split(" -> ")[-1].strip('"')))
    return entries


def _require_clean(root: Path) -> None:
    """Refuse unless `git reset --hard` could undo everything this run does.

    Only *tracked* changes block: an untracked file is not in git's history to
    begin with, and refusing on those would let a freshly written
    ``rename_map.tsv`` veto its own use. They are warned about instead.
    """
    if _git(root, "rev-parse", "--git-dir").returncode != 0:
        raise RenameError(
            f"{root} is not a git repository; refusing to move files with no way to undo it"
        )
    dirty = [f"{code} {path}" for code, path in _status(root) if code != "??"]
    if dirty:
        raise RenameError(
            "worktree is not clean, so `git reset --hard` could not undo this run:\n  "
            + "\n  ".join(sorted(dirty))
            + "\ncommit or stash first."
        )


def _untracked_warnings(root: Path, touched: set[str]) -> list[str]:
    """Name every untracked file the run will change; git cannot undo those."""
    return [
        f"{path}: untracked by git, so this change cannot be undone with git reset --hard"
        for code, path in _status(root)
        if code == "??" and path in touched
    ]


def _path_problem(root: Path, rel: str) -> str | None:
    """Why a map path is unusable, or None when it is fine."""
    if not rel or rel.startswith("/") or ".." in rel.split("/"):
        return f"'{rel}' must be a relative path inside the project"
    if (root / rel).is_dir():
        return f"'{rel}' is a directory; the codemod renames files only"
    return None


def _validate(root: Path, renames: list[Rename]) -> tuple[list[Rename], list[str]]:
    """Reject an unsafe rename set whole; drop the harmless no-op rows.

    Raises:
        RenameError: On bad paths, collisions, rename chains or cycles, or a
            destination that already exists.
    """
    kept = [r for r in renames if r.old != r.new]
    notes = [f"{r.old}: old and new are identical, row ignored" for r in renames if r.old == r.new]

    problems: list[str] = []
    for rename in kept:
        problems += [
            p for p in (_path_problem(root, rename.old), _path_problem(root, rename.new)) if p
        ]
    for attr, noun in (("old", "source"), ("new", "destination")):
        counts = Counter(getattr(r, attr) for r in kept)
        problems += [
            f"{noun} '{key}' appears {count} times; a rename map must be one-to-one"
            for key, count in sorted(counts.items())
            if count > 1
        ]

    sources = {r.old for r in kept}
    for rename in kept:
        if rename.new in sources:
            problems.append(
                f"'{rename.old}' -> '{rename.new}' collides with another row's source; "
                "rename chains and cycles are not supported"
            )
        elif (root / rename.new).exists():
            problems.append(f"destination '{rename.new}' already exists")

    if problems:
        raise RenameError("refusing to rename:\n  " + "\n  ".join(sorted(set(problems))))
    return kept, notes


# --- Apply ------------------------------------------------------------------


def _move_one(root: Path, rename: Rename, warnings: list[str]) -> Move:
    """Move one file, preferring `git mv` so history follows it."""
    source = root / rename.old
    if not source.exists():
        warnings.append(
            f"{rename.old}: not on disk, so nothing was moved; references were still rewritten"
        )
        return Move(rename, method="absent", performed=False)

    (root / rename.new).parent.mkdir(parents=True, exist_ok=True)
    if _git(root, "mv", "--", rename.old, rename.new).returncode == 0:
        return Move(rename, method="git mv", performed=True)

    shutil.move(str(source), str(root / rename.new))
    warnings.append(f"{rename.old}: untracked by git, moved with a plain filesystem move")
    return Move(rename, method="move", performed=True)


def apply_renames(root: Path, renames: Iterable[Rename], *, dry_run: bool = True) -> RenameReport:
    """Move files and rewrite every reference to them, as one operation.

    The two halves are inseparable: a ``git mv`` without the rewrite leaves a
    pipeline that no longer runs, and the rewrite without the move leaves one
    pointing at nothing. Both land in a single staged change set, ready for one
    commit and one ``git reset --hard`` if it was wrong.

    Args:
        root: Project root; must be a clean git worktree to actually apply.
        renames: Rows from a reviewed rename map.
        dry_run: When True (the default) nothing is moved or written; the report
            still describes exactly what a real run would do.

    Returns:
        A :class:`RenameReport` listing every move, every rewritten reference
        with its file and line, and every file the codemod declined to read.

    Raises:
        RenameError: On a malformed or colliding rename set, or — when applying —
            on a dirty worktree or a missing repository.
    """
    root = Path(root).resolve()
    kept, notes = _validate(root, list(renames))
    report = RenameReport(root=root, dry_run=dry_run, warnings=list(notes))
    if not kept:
        return report

    rules = _rules_for(kept)
    edited: list[_Doc] = []
    for path in _iter_files(root):
        doc = _load_doc(path, root, report.warnings)
        if doc is not None and _rewrite_doc(doc, rules, report.rewrites):
            edited.append(doc)

    if dry_run:
        report.moves = [
            Move(r, method="git mv" if (root / r.old).exists() else "absent", performed=False)
            for r in kept
        ]
        return report

    _require_clean(root)
    report.warnings += _untracked_warnings(root, {d.rel for d in edited} | {r.old for r in kept})

    moved = {r.old: r.new for r in kept}
    report.moves = [_move_one(root, r, report.warnings) for r in kept]
    for doc in edited:
        (root / moved.get(doc.rel, doc.rel)).write_text(_render(doc), encoding="utf-8")

    # Stage the rewrites next to the moves git mv already staged, so the whole
    # codemod is one `git commit` — and one `git reset --hard` — away from undone.
    _git(root, "add", "-u")
    return report
