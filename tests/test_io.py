"""Tests for labcore.io: the description contract, the companion README, formats.toml."""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pandas as pd
import polars as pl
import pyarrow as pa
import pytest

from labcore.io import FORMAT_DEFAULTS, merge_files, read_formats, write_table

DESCRIPTIONS = {
    "gene_id": "Ensembl gene identifier.",
    "counts": "Raw fragment counts.",
}


def polars_frame() -> pl.DataFrame:
    return pl.DataFrame({"gene_id": ["ENSG1", "ENSG2"], "counts": [4, 17]})


def pandas_frame() -> pd.DataFrame:
    return pd.DataFrame({"gene_id": ["ENSG1", "ENSG2"], "counts": [4, 17]})


def arrow_table() -> pa.Table:
    return pa.table({"gene_id": ["ENSG1", "ENSG2"], "counts": [4, 17]})


def companion(path: Path) -> str:
    return path.with_name(f"{path.stem}_README.md").read_text()


def test_missing_description_raises_and_names_the_column(tmp_path):
    with pytest.raises(ValueError) as excinfo:
        write_table(polars_frame(), tmp_path / "out_x.parquet", descriptions={"gene_id": "id"})
    message = str(excinfo.value)
    assert "counts" in message
    assert "gene_id" not in message


def test_error_names_every_missing_column(tmp_path):
    with pytest.raises(ValueError) as excinfo:
        write_table(polars_frame(), tmp_path / "out_x.parquet", descriptions={})
    message = str(excinfo.value)
    assert "gene_id" in message and "counts" in message


def test_nothing_is_written_when_a_description_is_missing(tmp_path):
    target = tmp_path / "out_x.parquet"
    with pytest.raises(ValueError):
        write_table(polars_frame(), target, descriptions={})
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("frame", [polars_frame(), pandas_frame(), arrow_table()])
def test_every_backend_writes_table_and_companion(tmp_path, frame):
    target = write_table(frame, tmp_path / "out_counts.parquet", descriptions=DESCRIPTIONS)
    assert target.exists()
    assert pl.read_parquet(target).shape == (2, 2)

    readme = target.with_name("out_counts_README.md")
    assert readme.parent == target.parent
    text = readme.read_text()
    assert "Rows | 2" in text
    assert "Columns | 2" in text
    for description in DESCRIPTIONS.values():
        assert description in text


def test_companion_records_timestamp_size_and_dtypes(tmp_path):
    target = write_table(polars_frame(), tmp_path / "out_counts.parquet", descriptions=DESCRIPTIONS)
    text = companion(target)
    # ISO 8601 UTC, e.g. 2026-08-11T09:14:22Z — assert the shape, not the value.
    stamp = next(line for line in text.splitlines() if line.startswith("| Created (UTC)"))
    assert stamp.rstrip(" |").endswith("Z")
    assert "T" in stamp and stamp.count("-") >= 2
    assert "Estimated in-memory size" in text
    assert "Git commit" in text
    assert "`gene_id`" in text and "`counts`" in text


def test_pandas_dtypes_reach_the_data_dictionary(tmp_path):
    target = write_table(pandas_frame(), tmp_path / "out_counts.parquet", descriptions=DESCRIPTIONS)
    assert "int64" in companion(target)


def test_unknown_frame_type_is_rejected(tmp_path):
    with pytest.raises(TypeError, match="pyarrow Table"):
        write_table({"gene_id": [1]}, tmp_path / "out_x.parquet", descriptions={"gene_id": "id"})


def test_extension_contradicting_formats_is_refused_then_allowed(tmp_path):
    target = tmp_path / "out_counts.csv"
    with pytest.raises(ValueError, match="contradicts"):
        write_table(polars_frame(), target, descriptions=DESCRIPTIONS)
    assert not target.exists()

    written = write_table(polars_frame(), target, descriptions=DESCRIPTIONS, override=True)
    assert written.exists()
    assert companion(written).count("|")  # the companion still lands beside it


def test_interchange_extension_needs_no_override(tmp_path):
    written = write_table(polars_frame(), tmp_path / "out_counts.tsv", descriptions=DESCRIPTIONS)
    assert written.read_text().splitlines()[0] == "gene_id\tcounts"


def test_formats_toml_overrides_the_defaults(tmp_path):
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "formats.toml").write_text('tabular = "tsv"\ntabular_interchange = "csv"\n')
    outputs = tmp_path / "outputs" / "10_counts"

    assert read_formats(tmp_path)["tabular"] == "tsv"
    assert read_formats(tmp_path)["compression"] == FORMAT_DEFAULTS["compression"]

    with pytest.raises(ValueError, match="contradicts"):
        write_table(polars_frame(), outputs / "out_counts.parquet", descriptions=DESCRIPTIONS)
    written = write_table(polars_frame(), outputs / "out_counts.csv", descriptions=DESCRIPTIONS)
    assert written.exists()


def test_read_formats_falls_back_when_absent(tmp_path):
    assert read_formats(tmp_path) == FORMAT_DEFAULTS


def test_unwritable_extension_is_refused_even_with_override(tmp_path):
    with pytest.raises(ValueError, match="feather"):
        write_table(
            polars_frame(), tmp_path / "out_x.feather", descriptions=DESCRIPTIONS, override=True
        )


CALLER = textwrap.dedent(
    """
    import polars as pl
    from labcore.io import write_table

    write_table(
        pl.DataFrame({"gene_id": ["A"], "counts": [1]}),
        "out_counts.parquet",
        descriptions={"gene_id": "Ensembl gene identifier.", "counts": "Raw counts."},
    )
    """
)


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_companion_records_the_calling_script_and_dirty_git_sha(tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    script = tmp_path / "make_counts.py"
    script.write_text(CALLER)
    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "test")
    git("add", "make_counts.py")
    git("commit", "-qm", "add script")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True, capture_output=True, text=True
    ).stdout.strip()

    subprocess.run([sys.executable, str(script)], cwd=tmp_path, check=True)
    text = (tmp_path / "out_counts_README.md").read_text()
    assert str(script.resolve()) in text
    assert sha in text
    assert "DIRTY" not in text

    (tmp_path / "make_counts.py").write_text(CALLER + "\n")
    subprocess.run([sys.executable, str(script)], cwd=tmp_path, check=True)
    assert "DIRTY" in (tmp_path / "out_counts_README.md").read_text()


pytest.importorskip("duckdb")


def test_merge_files_joins_on_row_position(tmp_path):
    baseline = tmp_path / "baseline.tsv"
    baseline.write_text("Tid\tAALen\nT1\t40\nT2\t90\n")
    disorder = tmp_path / "disorder.tsv"
    disorder.write_text("RowID\tTid\tDisorder\n0\tT1\t0.2\n1\tT2\t0.9\n")

    merged = merge_files([baseline, disorder], tmp_path / "merged.parquet")
    frame = pl.read_parquet(merged)

    # Tid and RowID come from both inputs; the join must not duplicate them.
    assert frame.columns == ["Tid", "AALen", "Disorder"]
    assert frame["Disorder"].to_list() == [0.2, 0.9]
    assert not (tmp_path / "merge_tmp" / "processing.db").exists()


def test_merge_files_needs_two_inputs(tmp_path):
    with pytest.raises(ValueError, match="at least two"):
        merge_files([tmp_path / "only.tsv"], tmp_path / "merged.parquet")
