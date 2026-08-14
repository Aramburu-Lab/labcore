"""The language-specific static readers behind `labdocs adopt`.

Split out of `adopt.py` so the adoption *pass* (walk, render, report, propose an
order) stays readable next to the per-language *reading* (ast for Python, line
regexes for everything else). Adding a language means adding one branch here and
nothing anywhere else.

Nothing in this module imports or executes the code it reads. `ast.parse` is the
only Python entry point used, and every other inspector works on raw text.
"""

from __future__ import annotations

import ast
import re

# Must stay in step with walk.ROOT_SCRIPT_SUFFIXES — tests/test_walk_coverage.py asserts the two
# discovery paths agree. They drifted once: the walker learned about .sbatch and extensionless
# entry points while adopt did not, so `labdocs lint` demanded a block on files `labdocs adopt`
# refused to draft one for.
ADOPT_SUFFIXES = frozenset(
    {".py", ".sh", ".bash", ".r", ".R", ".nf", ".rs", ".jl", ".pl", ".sbatch"}
)
# An extensionless entry point is shell; see walk._is_script.
SHELL_SUFFIXES = frozenset({".sh", ".bash", ".sbatch", ""})

PLACEHOLDER_OPTION_DESC = "no help text in the source; describe what this flag does"
PATH_LIMIT = 120
MIN_TEXT = 3

OPTION_CALLS = frozenset({"add_argument", "add_option"})
_READ_NAMES = """read_csv read_table read_tsv read_parquet read_excel read_json read_ndjson
    read_feather read_ipc read_hdf read_pickle read_fwf read_avro read_delta scan_csv scan_parquet
    scan_ndjson scan_ipc read_h5ad read_10x_h5 read_10x_mtx read_loom read_umi_tools"""
_WRITE_NAMES = """to_csv to_parquet to_excel to_json to_feather to_hdf to_pickle to_stata to_html
    to_latex write_csv write_parquet write_json write_ndjson write_ipc write_excel write_avro
    write_delta sink_csv sink_parquet sink_ndjson savefig write_h5ad write_loom write_csvs"""
READ_CALLS = frozenset(_READ_NAMES.split())
WRITE_CALLS = frozenset(_WRITE_NAMES.split())

# Bare `save`/`load` are far too common to trust unqualified, so they only count
# when the receiver is numpy itself.
_NUMPY_WRITE = "save savez savez_compressed savetxt"
_NUMPY_READ = "load loadtxt genfromtxt"
NUMPY_IO = dict.fromkeys(_NUMPY_WRITE.split(), "write") | dict.fromkeys(_NUMPY_READ.split(), "read")
NUMPY_MODULES = frozenset({"np", "numpy"})
H5_MODULES = frozenset({"h5py"})

DOCOPT_OPTION = re.compile(r"^\s*(-{1,2}[A-Za-z][^\s,]*(?:,\s*-{1,2}[^\s=]+)?)[\s=]{2,}(\S.*)$")
DOCOPT_DEFAULT = re.compile(r"\[default:\s*([^\]]*)\]", re.IGNORECASE)
FLAG_TOKEN = re.compile(r"-{1,2}[A-Za-z][\w-]*")

SHELL_OUT = re.compile(r"(?:^|[^<>&\d])\d?>>?\s*(\"[^\"]+\"|'[^']+'|[^\s;|&<>()]+)")
SHELL_IN = re.compile(r"(?:^|[^<&\d])<(?!<)\s*(\"[^\"]+\"|'[^']+'|[^\s;|&<>()]+)")
SHELL_CASE_FLAG = re.compile(r"^\s*\|?\s*(-{1,2}[A-Za-z][\w-]*)\)")
SHELL_NULL = frozenset({"/dev/null", "/dev/stderr", "/dev/stdout", "&1", "&2"})

GENERIC_FN = re.compile(r"\b([A-Za-z][A-Za-z0-9_.]*)\s*\(")
GENERIC_QUOTED = re.compile(r"(['\"])([^'\"]+)\1")
GENERIC_READ = ("read", "load", "scan", "fread", "import", "source")
GENERIC_WRITE = ("write", "save", "export", "ggsave", "sink", "png", "pdf", "svg")

# (summary, read paths, write paths, options)
Findings = tuple[str | None, list[str], list[str], list[dict]]


class AdoptError(Exception):
    """Raised when a file cannot be inspected at all.

    A caller walking a tree turns this into a reported failure rather than an
    abort: one unparseable script must not cost the other forty their drafts.
    """


def inspect_source(suffix: str, text: str, label: str) -> Findings:
    """Read one script's summary, IO paths and options out of its source.

    Args:
        suffix: File extension, which selects the inspector.
        text: Full file contents.
        label: Path shown in error messages.

    Returns:
        ``(summary, reads, writes, options)``. Any element may be empty; the
        caller is what substitutes placeholders.

    Raises:
        AdoptError: The extension has no inspector, or a ``.py`` does not parse.
    """
    # SHELL_SUFFIXES carries "" for extensionless entry points, which are not in ADOPT_SUFFIXES
    # because that set also drives discovery and would otherwise sweep in every LICENSE and README.
    # This was the THIRD place the same suffix rule lived; the first fix missed it and adopt kept
    # reporting "no inspector for ''" on files it had just agreed were scripts.
    if suffix not in ADOPT_SUFFIXES and suffix not in SHELL_SUFFIXES:
        raise AdoptError(f"{label}: no inspector for '{suffix}'")
    if suffix == ".py":
        return inspect_python(text, label)
    if suffix in SHELL_SUFFIXES:
        reads, writes = shell_io(text)
        return comment_summary(text), reads, writes, shell_options(text)
    reads, writes = generic_io(text)
    return comment_summary(text), reads, writes, []


def inspect_python(text: str, label: str) -> Findings:
    """Walk a Python AST for its docstring, IO calls and parser registrations.

    Args:
        text: Source to parse.
        label: Path shown in error messages.

    Returns:
        ``(summary, reads, writes, options)``.

    Raises:
        AdoptError: The source does not parse.
    """
    try:
        tree = ast.parse(text, filename=label)
    except (SyntaxError, ValueError) as exc:
        raise AdoptError(f"{label}: does not parse as Python: {exc}") from exc

    docstring = ast.get_docstring(tree)
    reads: list[str] = []
    writes: list[str] = []
    options: list[dict] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name, owner = _call_name(node)
        if name in OPTION_CALLS:
            option = _option_spec(node)
            if option is not None:
                options.append(option)
        elif name is not None:
            found = _io_target(name, owner, node)
            if found is not None:
                (writes if found[0] == "write" else reads).append(found[1])

    if not options and docstring:
        options = docopt_options(docstring)
    lines = (docstring or "").splitlines()
    summary = next((line.strip() for line in lines if line.strip()), None)
    return summary, reads, writes, dedupe_options(options)


def docopt_options(docstring: str) -> list[dict]:
    """Pull options out of a docopt usage block.

    Args:
        docstring: Module docstring, which is where docopt keeps its grammar.

    Returns:
        One entry per documented flag; empty when there is no usage block.
    """
    if "usage:" not in docstring.lower():
        return []
    options = []
    for line in docstring.splitlines():
        match = DOCOPT_OPTION.match(line)
        flags = FLAG_TOKEN.findall(match.group(1)) if match else []
        if not flags:
            continue
        desc = match.group(2).strip()
        option: dict = {"flag": max(flags, key=len)}
        default = DOCOPT_DEFAULT.search(desc)
        if default is not None:
            option["default"] = default.group(1).strip()
            desc = DOCOPT_DEFAULT.sub("", desc).strip()
        option["desc"] = desc if len(desc) >= MIN_TEXT else PLACEHOLDER_OPTION_DESC
        options.append(option)
    return options


def comment_summary(text: str) -> str | None:
    """First meaningful leading comment line of a non-Python script.

    Args:
        text: Full file contents.

    Returns:
        The comment text, or None when the file opens with code.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#!"):
            continue
        if not (stripped.startswith("#") or stripped.startswith("//")):
            return None
        body = stripped.lstrip("#/").strip()
        if body and not body.startswith("-*-"):
            return body
    return None


def shell_io(text: str) -> tuple[list[str], list[str]]:
    """Read and write paths from shell redirections.

    Args:
        text: Shell source.

    Returns:
        ``(reads, writes)``, with the standard streams dropped — they are not files.
    """
    reads, writes = [], []
    for line in text.splitlines():
        code = line.split("#", 1)[0]
        if code.strip():
            writes += [m.strip().strip("\"'") for m in SHELL_OUT.findall(code)]
            reads += [m.strip().strip("\"'") for m in SHELL_IN.findall(code)]
    real = [p for p in reads if p and p not in SHELL_NULL]
    return real, [p for p in writes if p and p not in SHELL_NULL]


def shell_options(text: str) -> list[dict]:
    """Long options a shell script dispatches on in a `case` branch.

    Args:
        text: Shell source.

    Returns:
        One entry per distinct flag, each with a placeholder description.
    """
    matches = (SHELL_CASE_FLAG.match(line.split("#", 1)[0]) for line in text.splitlines())
    found = [{"flag": m.group(1), "desc": PLACEHOLDER_OPTION_DESC} for m in matches if m]
    return dedupe_options(found)


def generic_io(text: str) -> tuple[list[str], list[str]]:
    """Read and write paths for languages with no dedicated inspector (R, Perl, Julia).

    Args:
        text: Source of any line-commented language.

    Returns:
        ``(reads, writes)`` from the first quoted literal after a read- or
        write-shaped function name.
    """
    reads, writes = [], []
    for line in text.splitlines():
        code = line.split("#", 1)[0]
        for call in GENERIC_FN.finditer(code):
            kind = _generic_kind(call.group(1))
            quoted = GENERIC_QUOTED.search(code, call.end()) if kind else None
            if quoted is not None:
                (writes if kind == "write" else reads).append(quoted.group(2))
    return reads, writes


def dedupe_options(options: list[dict]) -> list[dict]:
    """Keep the first entry per flag; a repeated flag is the same option twice.

    Args:
        options: Option entries in discovery order.

    Returns:
        The same entries, one per flag.
    """
    seen: dict[str, dict] = {}
    for option in options:
        seen.setdefault(option["flag"], option)
    return list(seen.values())


def _generic_kind(function: str) -> str | None:
    """Classify a bare function name as a read, a write, or neither."""
    # Both spellings matter: R writes `read.csv` (dotted verb) and Python writes
    # `pd.read_csv` (dotted namespace); only one of the two survives a split.
    names = (function.lower(), function.rsplit(".", 1)[-1].lower())
    if any(name.startswith(GENERIC_WRITE) for name in names):
        return "write"
    return "read" if any(name.startswith(GENERIC_READ) for name in names) else None


def _call_name(node: ast.Call) -> tuple[str | None, str | None]:
    """Return (called name, receiver name) for a call expression."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr, func.value.id if isinstance(func.value, ast.Name) else None
    if isinstance(func, ast.Name):
        return func.id, None
    return None, None


def _io_target(name: str, owner: str | None, node: ast.Call) -> tuple[str, str] | None:
    """Classify a call as a read or a write of a path, or return None."""
    path = _path_arg(node)
    if path is None:
        return None
    if name == "open" or (name == "File" and owner in H5_MODULES):
        mode = _mode_arg(node)
        return ("write" if any(flag in mode for flag in "wax") else "read", path)
    if name in NUMPY_IO and owner in NUMPY_MODULES:
        return (NUMPY_IO[name], path)
    if name in READ_CALLS:
        return ("read", path)
    return ("write", path) if name in WRITE_CALLS else None


def _path_arg(node: ast.Call) -> str | None:
    """Render the first positional argument as a path string."""
    if not node.args:
        return None
    argument = node.args[0]
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument.value.strip()[:PATH_LIMIT] or None
    try:
        return ast.unparse(argument)[:PATH_LIMIT]
    except (AttributeError, ValueError):
        return None


def _mode_arg(node: ast.Call) -> str:
    """Read the file mode from the second positional argument or `mode=`."""
    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
        return str(node.args[1].value)
    for keyword in node.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value)
    return "r"


def _option_spec(node: ast.Call) -> dict | None:
    """Build one option entry from an argparse or optparse registration."""
    flags = [
        arg.value
        for arg in node.args
        if isinstance(arg, ast.Constant) and str(arg.value).startswith("-")
    ]
    if not flags:
        return None
    keywords = {kw.arg: kw.value for kw in node.keywords if kw.arg}
    help_node = keywords.get("help")
    is_text = isinstance(help_node, ast.Constant) and isinstance(help_node.value, str)
    desc = help_node.value.strip() if is_text else ""
    option: dict = {
        "flag": max(flags, key=len),
        "desc": desc if len(desc) >= MIN_TEXT else PLACEHOLDER_OPTION_DESC,
    }
    if "default" in keywords:
        option["default"] = _literal(keywords["default"])
    return option


def _literal(node: ast.expr) -> object:
    """Evaluate a literal argument, falling back to its source text."""
    try:
        return _yamlable(ast.literal_eval(node))
    except (ValueError, TypeError, SyntaxError, MemoryError):
        try:
            return ast.unparse(node)
        except (AttributeError, ValueError):
            return None


def _yamlable(value: object) -> object:
    """Coerce a Python literal into something yaml.safe_dump will accept."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list | tuple | set):
        return [_yamlable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _yamlable(item) for key, item in value.items()}
    return str(value)
