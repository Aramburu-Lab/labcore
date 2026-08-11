"""Provenance-carrying table writes and out-of-core file merges.

Every table written through :func:`write_table` lands next to a
``<name>_README.md`` recording who made it, from which commit, and what each
column means. The description argument is mandatory and unforgiving: an
undescribed column raises, because silence is how a data dictionary rots.

Frames are dispatched by duck-typing, never by ``isinstance`` against an
imported module — Polars is a hard dependency, pandas and Arrow are not, and
~70% of the lab's existing table code is pandas (prior_art.md §1 verdict 4).
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Build plan §4.1. These are the answer to "what format should this be?" so the
# question never gets re-decided per script.
FORMAT_DEFAULTS: dict[str, str] = {
    "tabular": "parquet",
    "tabular_interchange": "tsv",
    "matrix": "h5ad",
    "figure_static": "pdf",
    "figure_interactive": "html",
    "report": "html",
    "compression": "zstd",
}

WRITABLE_EXTENSIONS = frozenset({"parquet", "tsv", "csv"})

MIN_MERGE_INPUTS = 2
BYTES_PER_KIB = 1024


def read_formats(root: Path | str) -> dict[str, str]:
    """Load `settings/formats.toml`, filling anything absent from the defaults.

    Args:
        root: Project root containing a `settings/` directory.

    Returns:
        The canonical format map, with `FORMAT_DEFAULTS` under every key the
        file does not override.
    """
    formats = dict(FORMAT_DEFAULTS)
    toml_path = Path(root) / "settings" / "formats.toml"
    if toml_path.is_file():
        formats.update({k: str(v) for k, v in tomllib.loads(toml_path.read_text()).items()})
    return formats


def write_table(
    df: Any,
    path: Path | str,
    *,
    descriptions: dict[str, str],
    override: bool = False,
) -> Path:
    """Write a table plus its `<name>_README.md` provenance companion.

    The companion records the UTC creation timestamp, the calling script's
    path, the current git commit SHA (dirty-flagged), row and column counts,
    the estimated in-memory size, and a data dictionary combining each column's
    dtype with its supplied description.

    Args:
        df: A Polars DataFrame, pandas DataFrame, or pyarrow Table.
        path: Destination file. Its extension must agree with `formats.toml`.
        descriptions: One entry per column. Extra keys are ignored.
        override: Permit an extension that contradicts `formats.toml`.

    Returns:
        The path written.

    Raises:
        TypeError: If `df` is not a recognised frame type.
        ValueError: If any column lacks a description, or the extension
            contradicts `formats.toml` and `override` is False.
    """
    path = Path(path)
    facts = _frame_facts(df)
    missing = [name for name in facts.columns if name not in descriptions]
    if missing:
        raise ValueError(
            f"{path.name}: no description for column(s) {', '.join(missing)}. "
            f"Describe every column — an undocumented one is how a data dictionary rots."
        )

    formats = read_formats(_project_root(path))
    extension = _check_extension(path, formats, override=override)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_frame(df, path, facts.kind, extension, formats["compression"])

    readme = path.with_name(f"{path.stem}_README.md")
    readme.write_text(_readme_text(path, facts, descriptions, extension, formats["compression"]))
    return path


def merge_files(
    inputs: Sequence[Path | str],
    output: Path | str,
    *,
    memory_gb: int = 8,
    tmp_dir: Path | str | None = None,
    threads: int = 8,
) -> Path:
    """Join tabular files 1:1 on row position via DuckDB, out of core.

    Extracted from `Python/Nf_core_plots/step4_merge.py`, which merged a
    baseline plus 254 MB of annotation TSVs into a 54 MB Parquet on a laptop
    (`Nf_core_plots/output/merged_microproteins.parquet`). The anchor file is
    materialised as a table and the rest registered as views, so only the
    anchor is ever fully resident. Columns already contributed by an earlier
    input are excluded rather than suffixed — a 1:1 positional join repeats key
    columns by construction.

    Args:
        inputs: Two or more files. The first is the anchor; the rest are
            LEFT JOINed onto it. Files carrying a `RowID` column are joined on
            it, otherwise row order supplies one.
        output: Destination `.parquet`.
        memory_gb: DuckDB `memory_limit`. Spills to `tmp_dir` beyond it.
        tmp_dir: Spill and scratch-database directory. Defaults to
            `<output parent>/merge_tmp`.
        threads: DuckDB thread count. Tune to the node, not the laptop.

    Returns:
        The path written.

    Raises:
        ImportError: If duckdb is not installed.
        ValueError: If fewer than two inputs are given.
    """
    duckdb = _import_duckdb()
    paths = [Path(p) for p in inputs]
    if len(paths) < MIN_MERGE_INPUTS:
        raise ValueError(f"merge_files needs at least two inputs, got {len(paths)}.")

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tmp_dir) if tmp_dir else output.parent / "merge_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    scratch_db = tmp / "processing.db"

    connection = duckdb.connect(str(scratch_db))
    try:
        connection.execute(f"SET memory_limit='{memory_gb}GB'")
        # Row position is the join key, so insertion order is correctness here.
        connection.execute("SET preserve_insertion_order=true")
        connection.execute(f"SET temp_directory='{_sql_literal(str(tmp))}'")
        connection.execute(f"SET threads TO {int(threads)}")
        query = _build_join(connection, paths)
        connection.execute(
            f"COPY ({query}) TO '{_sql_literal(str(output))}' "
            f"(FORMAT PARQUET, COMPRESSION 'ZSTD')"
        )
    finally:
        connection.close()
        scratch_db.unlink(missing_ok=True)
    return output


@dataclass(frozen=True)
class _FrameFacts:
    """Backend-independent view of whatever frame was handed to write_table."""

    kind: str
    columns: dict[str, str]
    n_rows: int
    n_bytes: int


def _frame_facts(df: Any) -> _FrameFacts:
    """Extract columns, dtypes, shape and size by duck-typing the frame."""
    if hasattr(df, "write_parquet") and hasattr(df, "estimated_size"):
        dtypes = {name: str(dtype) for name, dtype in zip(df.columns, df.dtypes, strict=True)}
        return _FrameFacts("polars", dtypes, df.height, df.estimated_size())
    if hasattr(df, "to_parquet") and hasattr(df, "memory_usage"):
        dtypes = {str(name): str(dtype) for name, dtype in df.dtypes.items()}
        return _FrameFacts("pandas", dtypes, len(df.index), int(df.memory_usage(deep=True).sum()))
    if hasattr(df, "column_names") and hasattr(df, "nbytes"):
        types = [str(t) for t in df.schema.types]
        dtypes = dict(zip(df.column_names, types, strict=True))
        return _FrameFacts("arrow", dtypes, df.num_rows, df.nbytes)
    raise TypeError(
        f"write_table takes a Polars DataFrame, pandas DataFrame or pyarrow Table, "
        f"got {type(df).__name__}."
    )


def _check_extension(path: Path, formats: dict[str, str], *, override: bool) -> str:
    """Validate the destination extension against formats.toml."""
    extension = path.suffix.lstrip(".").lower()
    if extension not in WRITABLE_EXTENSIONS:
        raise ValueError(
            f"{path.name}: write_table can write {sorted(WRITABLE_EXTENSIONS)}, not '{extension}'."
        )
    canonical = {formats["tabular"], formats["tabular_interchange"]}
    if extension not in canonical and not override:
        raise ValueError(
            f"{path.name}: settings/formats.toml declares tabular='{formats['tabular']}' and "
            f"tabular_interchange='{formats['tabular_interchange']}'; '{extension}' contradicts "
            f"both. Pass override=True if this write is a deliberate exception."
        )
    return extension


def _write_frame(df: Any, path: Path, kind: str, extension: str, compression: str) -> None:
    """Write the frame with the backend it came from."""
    if extension == "parquet":
        if kind == "polars":
            df.write_parquet(path, compression=compression)
        elif kind == "pandas":
            df.to_parquet(path, compression=compression, index=False)
        else:
            from pyarrow import parquet

            parquet.write_table(df, path, compression=compression)
        return

    separator = "\t" if extension == "tsv" else ","
    if kind == "polars":
        df.write_csv(path, separator=separator)
    elif kind == "pandas":
        df.to_csv(path, sep=separator, index=False)
    else:
        from pyarrow import csv

        csv.write_csv(df, path, write_options=csv.WriteOptions(delimiter=separator))


def _readme_text(
    path: Path,
    facts: _FrameFacts,
    descriptions: dict[str, str],
    extension: str,
    compression: str,
) -> str:
    """Render the companion README markdown."""
    stamp = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    script = _caller_script()
    header = [
        f"# {path.name}",
        "",
        "Generated by `labcore.io.write_table`. Do not edit — rerun the script instead.",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Created (UTC) | {stamp} |",
        f"| Script | `{script}` |",
        f"| Git commit | {_git_sha(script)} |",
        f"| Rows | {facts.n_rows:,} |",
        f"| Columns | {len(facts.columns):,} |",
        f"| Estimated in-memory size | {_human_bytes(facts.n_bytes)} |",
        f"| Frame backend | {facts.kind} |",
        f"| Format | {extension} ({compression}) |",
        "",
        "## Data dictionary",
        "",
        "| Column | Dtype | Description |",
        "| --- | --- | --- |",
    ]
    rows = [
        f"| `{name}` | `{dtype}` | {descriptions[name]} |" for name, dtype in facts.columns.items()
    ]
    return "\n".join([*header, *rows, ""])


def _caller_script() -> str:
    """Resolve the script that called write_table, for the provenance record."""
    main = sys.modules.get("__main__")
    candidate = getattr(main, "__file__", None) or (sys.argv[0] if sys.argv else "")
    return str(Path(candidate).resolve()) if candidate else "<interactive>"


def _git_sha(script: str) -> str:
    """Read HEAD near the calling script, flagged DIRTY on uncommitted changes."""
    start = Path(script).parent if script != "<interactive>" else Path.cwd()
    if not start.is_dir():
        return "unknown (script path does not exist)"
    sha = _git(start, "rev-parse", "HEAD")
    if sha is None:
        return "unknown (no git repository or no commits yet)"
    # Untracked files are ignored on purpose: this very call is about to write a
    # table beside the script, and a flag that fires on every run tells nobody
    # anything. Modified *tracked* code is the thing that invalidates a commit.
    dirty = _git(start, "status", "--porcelain", "--untracked-files=no")
    return f"{sha} DIRTY (uncommitted changes)" if dirty else sha


def _git(cwd: Path, *args: str) -> str | None:
    """Run a git command, returning stripped stdout or None on any failure."""
    try:
        done = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _project_root(path: Path) -> Path:
    """Walk up from the output path for the directory holding settings/formats.toml."""
    start = path.resolve().parent
    for candidate in (start, *start.parents):
        if (candidate / "settings" / "formats.toml").is_file():
            return candidate
    return start


def _human_bytes(n: int) -> str:
    """Format a byte count for a human reading a README."""
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < BYTES_PER_KIB:
            return f"{size:,.1f} {unit}"
        size /= BYTES_PER_KIB
    return f"{size:,.1f} TiB"


def _import_duckdb() -> Any:
    """Import duckdb lazily; it is an optional extra, not a hard dependency."""
    try:
        import duckdb
    except ImportError as exc:
        raise ImportError(
            "merge_files needs duckdb. Install it with: pip install 'labcore[io]'"
        ) from exc
    return duckdb


def _sql_literal(text: str) -> str:
    """Escape a value for embedding in a single-quoted SQL literal."""
    return text.replace("'", "''")


def _scan_sql(path: Path) -> str:
    """SQL fragment reading one file, chosen by extension."""
    literal = _sql_literal(str(path))
    if path.suffix.lower() in {".parquet", ".pq"}:
        return f"read_parquet('{literal}')"
    delimiter = "," if path.suffix.lower() == ".csv" else "\\t"
    return f"read_csv_auto('{literal}', delim='{delimiter}', null_padding=true, parallel=true)"


def _describe(connection: Any, source: str) -> list[str]:
    """Column names of a DuckDB relation or scan expression."""
    return [row[0] for row in connection.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()]


def _register(connection: Any, name: str, path: Path, *, as_table: bool) -> list[str]:
    """Materialise or view one input, adding RowID when the file lacks one."""
    scan = _scan_sql(path)
    if "RowID" not in _describe(connection, scan):
        scan = f"(SELECT *, (row_number() OVER () - 1) AS RowID FROM {scan})"
    kind = "TABLE" if as_table else "VIEW"
    connection.execute(f"CREATE OR REPLACE {kind} {name} AS SELECT * FROM {scan}")
    return _describe(connection, name)


def _build_join(connection: Any, paths: list[Path]) -> str:
    """Register every input and assemble the LEFT JOIN over RowID."""
    anchor_columns = _register(connection, "t_anchor", paths[0], as_table=True)
    seen = {name.casefold() for name in anchor_columns}
    selects = ["a.* EXCLUDE (RowID)"]
    joins = []
    for index, path in enumerate(paths[1:]):
        alias = f"v{index}"
        columns = _register(connection, alias, path, as_table=False)
        # A positional join repeats every key column; excluding beats suffixing
        # because downstream code reads these names, not name_x / name_y.
        drop = sorted({"RowID", *(c for c in columns if c.casefold() in seen)})
        seen.update(c.casefold() for c in columns)
        selects.append(f"{alias}.* EXCLUDE ({', '.join(drop)})")
        joins.append(f"LEFT JOIN {alias} ON a.RowID = {alias}.RowID")
    return f"SELECT {', '.join(selects)} FROM t_anchor a {' '.join(joins)}"
