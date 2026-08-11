"""Draft `codebase-meta` blocks for an existing repo by static inspection.

Build plan §9.3: this is why conformance level 2 costs half a day rather than a
week. Each script is read statically — `ast` for Python, line regexes for the
rest, all of it in `adopt_inspect` — and is **never imported or executed**. An
adoption pass has to be safe on a repo whose imports do not resolve and whose
`__main__` would start a job.

Two things it refuses to invent:

* ``order:`` and ``next:`` encode run *intent*, not syntax. Every draft gets the
  same obviously-unset ``order: 0``; the derived sequence lives in
  :class:`AdoptReport` as a proposal a human confirms.
* ``from:`` on an input, for the same reason. Every input is ``external: true``
  until someone says otherwise, which keeps LD005 quiet without asserting a
  producer the parser cannot actually know.

Every block carries ``draft: true`` — a warning at level 2 and an error at level
3, so drafts cannot quietly become permanent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from labcore.meta import MetaError, comment_token, extract_block

from .adopt_inspect import ADOPT_SUFFIXES, MIN_TEXT, AdoptError, inspect_source
from .walk import SKIP_DIRS

DERIVED, PLACEHOLDER, ABSENT = "derived", "placeholder", "absent"

PLACEHOLDER_ORDER = 0
PLACEHOLDER_PATH = "unknown"
PLACEHOLDER_SUMMARY = "undocumented; drafted by labdocs adopt from static inspection, needs review"
PLACEHOLDER_OUTPUT_DESC = "static inspection found a write here; describe what the file contains"
PLACEHOLDER_MISSING_OUTPUT = "no output detected in the source; replace this with the real one"

# `step4_`, `01a_`, `RUN_2_`. The bare word `run`/`step` is only stripped when a
# number follows it, or `run_pipeline.py` would be adopted as `pipeline`.
ORDINAL_PREFIX = re.compile(r"^(?:(?:run|step|part|phase|s)[_-]?)?\d+[a-z]?[_-]", re.IGNORECASE)
NON_NAME = re.compile(r"[^a-z0-9]+")
CODING_LINE = re.compile(r"^#.*coding[:=]")


@dataclass
class Draft:
    """One script's statically inferred metadata, before a human touches it."""

    path: Path
    name: str
    summary: str
    inputs: list[dict] = field(default_factory=list)
    outputs: list[dict] = field(default_factory=list)
    options: list[dict] = field(default_factory=list)
    provenance: dict[str, str] = field(default_factory=dict)

    def as_mapping(self) -> dict:
        """Render the draft as the mapping that becomes block YAML.

        Returns:
            Block keys in declaration order, omitting ``inputs`` and ``options``
            when the inspection found none.
        """
        data: dict = {
            "name": self.name,
            "order": PLACEHOLDER_ORDER,
            "summary": self.summary,
            "draft": True,
        }
        if self.inputs:
            data["inputs"] = self.inputs
        data["outputs"] = self.outputs
        if self.options:
            data["options"] = self.options
        return data


@dataclass(frozen=True)
class ScriptStatus:
    """What happened to one candidate file during an adoption pass."""

    path: Path
    status: str
    name: str | None = None
    reason: str = ""
    provenance: dict[str, str] = field(default_factory=dict)
    block: str | None = None


@dataclass
class AdoptReport:
    """The outcome of an adoption pass over one repository.

    The derived-versus-placeholder ratio is the honest measure of how much work
    `adopt` saved; a run that drafts every script out of placeholders alone has
    saved nobody anything.
    """

    root: Path
    scripts: list[ScriptStatus] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)
    proposal: list[str] = field(default_factory=list)
    cycles: list[str] = field(default_factory=list)
    name_collisions: dict[str, list[str]] = field(default_factory=dict)
    written: list[Path] = field(default_factory=list)

    @property
    def totals(self) -> dict[str, int]:
        """Counts by status, plus field-level provenance totals."""
        counts = {"considered": len(self.scripts), "drafted": 0, "skipped": 0, "failed": 0}
        for status in self.scripts:
            counts[status.status] = counts.get(status.status, 0) + 1
        counts["derived_fields"] = self._count(DERIVED)
        counts["placeholder_fields"] = self._count(PLACEHOLDER)
        return counts

    @property
    def coverage(self) -> float:
        """Fraction of candidate files that ended up with a draft block."""
        return 0.0 if not self.scripts else self.totals["drafted"] / len(self.scripts)

    @property
    def derived_ratio(self) -> float:
        """Fraction of drafted fields that came from the source, not a placeholder."""
        derived, held = self._count(DERIVED), self._count(PLACEHOLDER)
        return 0.0 if not (derived + held) else derived / (derived + held)

    def _count(self, kind: str) -> int:
        """Number of drafted fields carrying one provenance value."""
        return sum(list(s.provenance.values()).count(kind) for s in self.scripts)


def normalise_name(stem: str) -> str:
    """Reduce a filename stem to the ADR-11 name regime.

    Strips leading ordinal prefixes (``01a_``, ``step4_``, ``RUN_2_``), lowercases,
    and collapses runs of non-alphanumerics into single underscores.

    Args:
        stem: Filename stem, without the extension.

    Returns:
        A string matching ``^[a-z0-9]+(_[a-z0-9]+)*$``.
    """
    trimmed = stem
    for _ in range(3):
        stripped = ORDINAL_PREFIX.sub("", trimmed, count=1)
        if stripped == trimmed or not stripped.strip("_-"):
            break
        trimmed = stripped
    cleaned = NON_NAME.sub("_", trimmed.lower()).strip("_")
    return cleaned or NON_NAME.sub("_", stem.lower()).strip("_") or "script"


def iter_candidates(root: Path) -> list[Path]:
    """Every file under a tree that could carry a `codebase-meta` block.

    Args:
        root: Directory to walk. Generated, vendored and dot-directories are skipped.

    Returns:
        Sorted paths, so two runs on the same tree report identically.
    """
    found: list[Path] = []
    if not root.is_dir():
        return found
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            if entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                found += iter_candidates(entry)
        elif entry.suffix in ADOPT_SUFFIXES:
            found.append(entry)
    return found


def inspect_script(path: Path) -> Draft:
    """Statically infer one script's metadata.

    Args:
        path: Script to read. Never imported, never executed.

    Returns:
        A Draft whose ``provenance`` records, per field, whether the value came
        from the source (``derived``), was substituted (``placeholder``), or was
        left out entirely (``absent``).

    Raises:
        AdoptError: The extension is unsupported, the file is unreadable, or it
            is a ``.py`` that does not parse.
    """
    if path.suffix not in ADOPT_SUFFIXES:
        raise AdoptError(f"{path}: no inspector for '{path.suffix}'")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise AdoptError(f"{path}: unreadable: {exc}") from exc

    summary, reads, writes, options = inspect_source(path.suffix, text, str(path))
    return _assemble(path, normalise_name(path.stem), summary, reads, writes, options)


def render_block(draft: Draft) -> str:
    """Render a draft as a comment-delimited `codebase-meta` block.

    Args:
        draft: Draft to serialise. Its path's extension selects the comment token.

    Returns:
        The block text, delimiters included, without a trailing newline.

    Raises:
        AdoptError: The extension has no known comment token.
    """
    token = comment_token(draft.path)
    if token is None:
        raise AdoptError(f"{draft.path}: no comment token known for '{draft.path.suffix}'")
    body = yaml.safe_dump(
        draft.as_mapping(),
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=10**6,
    )
    lines = [f"{token} /// codebase-meta"]
    lines += [f"{token} {line}" if line else token for line in body.splitlines()]
    lines.append(f"{token} ///")
    return "\n".join(lines)


def draft_block(path: Path) -> str | None:
    """Render the draft block for one script.

    Args:
        path: Script to inspect.

    Returns:
        The block text, or None when the file already carries a block, cannot be
        parsed, or has no inspector.
    """
    try:
        if extract_block(path) is not None:
            return None
    except (MetaError, OSError, UnicodeDecodeError):
        return None
    try:
        return render_block(inspect_script(path))
    except AdoptError:
        return None


def adopt_project(root: Path, *, write: bool = False) -> AdoptReport:
    """Draft `codebase-meta` blocks for every script in a repository.

    Args:
        root: Repository root to walk.
        write: Insert each block into its file. False renders into the report
            only, which is the mode to run first.

    Returns:
        A report carrying per-script status, field-level provenance, the derived
        run-order proposal, and the totals.
    """
    root = Path(root)
    report = AdoptReport(root=root)
    drafts: list[Draft] = []

    for path in iter_candidates(root):
        status, draft = _adopt_one(path, write=write)
        report.scripts.append(status)
        if draft is not None:
            drafts.append(draft)
        if status.status == "drafted" and write:
            report.written.append(path)

    report.edges = _overlap_edges(drafts)
    report.proposal, report.cycles = _topological(sorted({d.name for d in drafts}), report.edges)
    report.name_collisions = _collisions(drafts)
    return report


def render_report(report: AdoptReport) -> str:
    """Render an adoption report as markdown for the console.

    Args:
        report: Report from :func:`adopt_project`.

    Returns:
        The summary line, the per-script table, and the order proposal.
    """
    totals = report.totals
    lines = [
        f"# labdocs adopt — {report.root}",
        "",
        f"{totals['drafted']}/{totals['considered']} drafted ({report.coverage:.0%}), "
        f"{totals['skipped']} skipped, {totals['failed']} failed.",
        f"Fields: {totals['derived_fields']} derived, {totals['placeholder_fields']} "
        f"placeholder ({report.derived_ratio:.0%} derived).",
        "",
        "| Script | Status | Name | Derived | Placeholder | Note |",
        "|---|---|---|---|---|---|",
    ]
    for status in report.scripts:
        derived = sorted(k for k, v in status.provenance.items() if v == DERIVED)
        held = sorted(k for k, v in status.provenance.items() if v == PLACEHOLDER)
        lines.append(
            f"| {_relative(report.root, status.path)} | {status.status} | {status.name or '-'} "
            f"| {', '.join(derived) or '-'} | {', '.join(held) or '-'} | {status.reason} |"
        )
    lines += ["", "## Proposed run order — confirm before applying", ""]
    # Saying "proposed order" over an empty edge set would dress an alphabetical
    # list up as evidence. A repo that passes every path through argv gives the
    # overlap heuristic nothing to work with, and the report has to admit that.
    lines.append(
        f"{len(report.edges)} path overlap(s) found."
        + ("" if report.edges else " The sequence below is alphabetical, not derived.")
    )
    lines.append(" -> ".join(report.proposal) if report.proposal else "no drafts")
    if report.cycles:
        lines.append(f"Not ordered (cyclic path overlap): {', '.join(report.cycles)}")
    lines += [
        f"Name collision on '{name}': " + ", ".join(_relative(report.root, Path(p)) for p in paths)
        for name, paths in report.name_collisions.items()
    ]
    return "\n".join(lines)


def _relative(root: Path, path: Path) -> str:
    """Posix path relative to the root, falling back to the bare filename."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _adopt_one(path: Path, *, write: bool) -> tuple[ScriptStatus, Draft | None]:
    """Inspect, render and optionally insert a block for one file."""
    try:
        if extract_block(path) is not None:
            return ScriptStatus(path, "skipped", reason="already carries a block"), None
    except (MetaError, OSError, UnicodeDecodeError) as exc:
        return ScriptStatus(path, "failed", reason=str(exc)), None
    try:
        draft = inspect_script(path)
        block = render_block(draft)
    except AdoptError as exc:
        return ScriptStatus(path, "failed", reason=str(exc)), None
    if write:
        try:
            _insert_block(path, block)
        except OSError as exc:
            return ScriptStatus(path, "failed", reason=f"could not write: {exc}"), None
    return ScriptStatus(path, "drafted", draft.name, "", dict(draft.provenance), block), draft


def _insert_block(path: Path, block: str) -> None:
    """Insert a block after the shebang and encoding lines, ahead of the docstring."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    at = 1 if lines and lines[0].startswith("#!") else 0
    if at < len(lines) and CODING_LINE.match(lines[at]):
        at += 1
    merged = lines[:at] + block.splitlines() + lines[at:]
    path.write_text("\n".join(merged) + ("\n" if text else ""), encoding="utf-8")


def _assemble(
    path: Path,
    name: str,
    summary: str | None,
    reads: list[str],
    writes: list[str],
    options: list[dict],
) -> Draft:
    """Turn raw findings into a Draft, substituting placeholders where nothing was found."""
    provenance = {"order": PLACEHOLDER, "next": ABSENT}

    clean = (summary or "").strip()
    provenance["summary"] = DERIVED if len(clean) >= MIN_TEXT else PLACEHOLDER
    if provenance["summary"] == PLACEHOLDER:
        clean = PLACEHOLDER_SUMMARY

    inputs = [{"path": p, "external": True} for p in _dedupe(reads)]
    outputs = [{"path": p, "desc": PLACEHOLDER_OUTPUT_DESC} for p in _dedupe(writes)]
    provenance["inputs"] = DERIVED if inputs else ABSENT
    provenance["outputs"] = DERIVED if outputs else PLACEHOLDER
    provenance["options"] = DERIVED if options else ABSENT
    if not outputs:
        # The schema demands at least one output, so a script whose writes are
        # invisible to static inspection gets a flagged stand-in rather than a
        # block that fails LD000 the moment anyone lints it.
        outputs = [{"path": PLACEHOLDER_PATH, "desc": PLACEHOLDER_MISSING_OUTPUT}]
    return Draft(path, name, clean, inputs, outputs, options, provenance)


def _dedupe(values: list[str]) -> list[str]:
    """Order-preserving de-duplication of non-empty strings."""
    seen: dict[str, None] = {}
    for value in values:
        if value.strip():
            seen.setdefault(value.strip(), None)
    return list(seen)


def _path_keys(path: str) -> set[str]:
    """Keys a declared path can be matched on across scripts."""
    if not path or path == PLACEHOLDER_PATH:
        return set()
    normalised = path.strip().strip("\"'").lstrip("./")
    base = normalised.rsplit("/", 1)[-1]
    keys = {normalised}
    if "." in base and len(base) > MIN_TEXT:
        keys.add(base)
    return keys


def _overlap_edges(drafts: list[Draft]) -> list[tuple[str, str]]:
    """Producer-to-consumer edges implied by one script writing what another reads."""
    writers: dict[str, set[str]] = {}
    readers: dict[str, set[str]] = {}
    for draft in drafts:
        for side, specs in ((writers, draft.outputs), (readers, draft.inputs)):
            for spec in specs:
                for key in _path_keys(str(spec.get("path", ""))):
                    side.setdefault(key, set()).add(draft.name)
    return sorted(
        {
            (writer, reader)
            for key, producers in writers.items()
            for writer in producers
            for reader in readers.get(key, set())
            if writer != reader
        }
    )


def _topological(names: list[str], edges: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    """Kahn sort over the overlap graph, returning (ordered, unordered)."""
    incoming = dict.fromkeys(names, 0)
    outgoing: dict[str, list[str]] = {name: [] for name in names}
    for source, target in edges:
        if source in outgoing and target in incoming:
            outgoing[source].append(target)
            incoming[target] += 1

    ready = sorted(name for name, count in incoming.items() if count == 0)
    ordered: list[str] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for target in outgoing[current]:
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
        ready.sort()
    # A cycle means the overlap heuristic contradicted itself; say so rather than
    # emitting a sequence that silently drops the scripts involved.
    return ordered, sorted(set(names) - set(ordered))


def _collisions(drafts: list[Draft]) -> dict[str, list[str]]:
    """Normalised names claimed by more than one file."""
    by_name: dict[str, list[str]] = {}
    for draft in drafts:
        by_name.setdefault(draft.name, []).append(str(draft.path))
    return {name: sorted(paths) for name, paths in by_name.items() if len(paths) > 1}
