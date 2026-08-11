r"""Parse and validate `codebase-meta` blocks carried by scripts.

The grammar is pinned exactly (build plan §5.2) rather than inferred, because a
parser that guesses will accept a block in one language and reject the identical
block in another.

A block opens on the first line matching ``^\\s*<C>\\s*///\\s*codebase-meta\\s*$``
and closes on the first subsequent ``^\\s*<C>\\s*///\\s*$``, where ``<C>`` is the
host language's line-comment token, inferred from the file extension. Every line
between is stripped of leading whitespace, the comment token, and *exactly one*
following space; the remainder is parsed as YAML.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

# Extension → line-comment token. Adding a language is one entry here plus a
# `languages` choice in copier.yml — deliberately cheap, per build plan §4.1.
COMMENT_TOKENS: dict[str, str] = {
    ".py": "#",
    ".r": "#",
    ".R": "#",
    ".sh": "#",
    ".bash": "#",
    ".nf": "//",
    ".rs": "//",
    ".toml": "#",
    ".jl": "#",
    ".pl": "#",
}

SCHEMA_PATH = Path(__file__).parent / "labdocs" / "schema" / "codebase-meta.schema.json"


class MetaError(Exception):
    """Raised when a block is structurally unparseable.

    Schema violations are *not* raised — they are collected as lint findings so
    one bad file does not hide the other twenty.
    """


@dataclass
class MetaBlock:
    """One parsed `codebase-meta` block and where it came from."""

    path: Path
    data: dict
    start_line: int

    @property
    def is_exempt(self) -> bool:
        """True if this file opted out of being a pipeline step."""
        return "exempt" in self.data

    @property
    def name(self) -> str:
        """Declared step name, or the filename stem for an exempt helper."""
        return self.data.get("name", self.path.stem)

    @property
    def order(self) -> int | None:
        """Run order, or None when exempt."""
        return self.data.get("order")

    @property
    def outputs(self) -> list[dict]:
        """Declared outputs (may be empty for an exempt helper)."""
        return self.data.get("outputs", []) or []

    @property
    def inputs(self) -> list[dict]:
        """Declared inputs (may be empty)."""
        return self.data.get("inputs", []) or []

    @property
    def next_steps(self) -> list[str]:
        """Downstream step names. A terminal step returns an empty list."""
        nxt = self.data.get("next")
        if nxt is None or nxt == "final":
            return []
        return list(nxt)

    @property
    def is_final(self) -> bool:
        """True when this step is explicitly declared terminal."""
        return self.data.get("next") == "final"


@dataclass
class ParseResult:
    """Outcome of walking a tree: what parsed, and what refused to."""

    blocks: list[MetaBlock] = field(default_factory=list)
    missing: list[Path] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)


def comment_token(path: Path) -> str | None:
    """Return the line-comment token for a file, or None if unsupported.

    Args:
        path: File whose extension determines the token.

    Returns:
        The comment token (``#`` or ``//``), or None when the extension is not
        one labdocs knows how to read.
    """
    return COMMENT_TOKENS.get(path.suffix)


def _open_re(token: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*{re.escape(token)}\s*///\s*codebase-meta\s*$")


def _close_re(token: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*{re.escape(token)}\s*///\s*$")


def _strip(line: str, token: str) -> str:
    """Remove indentation, the comment token, and exactly one following space."""
    stripped = line.lstrip()
    if not stripped.startswith(token):
        return ""
    rest = stripped[len(token) :]
    # Exactly one space, not .lstrip() — YAML nesting lives in the remaining
    # indentation, and eating it would flatten every nested list.
    return rest[1:] if rest.startswith(" ") else rest


def extract_block(path: Path, text: str | None = None) -> MetaBlock | None:
    """Extract the single `codebase-meta` block from a file.

    Args:
        path: File to read. Its extension selects the comment token.
        text: File contents, if already in hand. Read from disk when None.

    Returns:
        The parsed block, or None when the file carries no block.

    Raises:
        MetaError: On an unterminated block, a second block, unsupported
            extension, or YAML that does not parse.
    """
    token = comment_token(path)
    if token is None:
        raise MetaError(f"{path}: no comment token known for '{path.suffix}'")

    content = text if text is not None else path.read_text(encoding="utf-8")
    lines = content.splitlines()
    opener, closer = _open_re(token), _close_re(token)

    start = next((i for i, ln in enumerate(lines) if opener.match(ln)), None)
    if start is None:
        return None

    end = next((j for j in range(start + 1, len(lines)) if closer.match(lines[j])), None)
    if end is None:
        raise MetaError(f"{path}:{start + 1}: codebase-meta block is never closed")

    # A second block means two competing descriptions of one file.
    if any(opener.match(ln) for ln in lines[end + 1 :]):
        raise MetaError(f"{path}: more than one codebase-meta block; at most one is allowed")

    body = "\n".join(_strip(ln, token) for ln in lines[start + 1 : end])
    try:
        data = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        raise MetaError(f"{path}:{start + 1}: block is not valid YAML: {exc}") from exc

    if data is None:
        raise MetaError(f"{path}:{start + 1}: codebase-meta block is empty")
    if not isinstance(data, dict):
        raise MetaError(f"{path}:{start + 1}: block must be a mapping, got {type(data).__name__}")

    return MetaBlock(path=path, data=data, start_line=start + 1)


@lru_cache(maxsize=1)
def load_schema() -> dict:
    """Load the bundled JSON Schema.

    Returns:
        The parsed schema document.
    """
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_block(block: MetaBlock) -> list[str]:
    """Validate one block against the schema.

    Args:
        block: Parsed block to check.

    Returns:
        Human-readable violation messages; empty when the block is valid.
    """
    import jsonschema

    validator = jsonschema.Draft202012Validator(load_schema())
    out = []
    for err in sorted(validator.iter_errors(block.data), key=lambda e: list(e.path)):
        loc = "".join(f"[{p!r}]" for p in err.path) or "<root>"
        out.append(f"{loc}: {err.message}")
    return out
