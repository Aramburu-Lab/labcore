"""The rename codemod: does it move the file AND fix every reference to it.

Every fixture here is a real git repository, because the safety promise the
codemod makes ("you can always `git reset --hard`") is only testable against
real git state. Effects are asserted alongside exit codes throughout — a command
that never ran also changes nothing (lessons L-006).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from labcore.labdocs.rename import (
    Rename,
    RenameError,
    apply_renames,
    propose_renames,
    read_rename_map,
    write_rename_map,
)

TABLE = "outputs/20_qc_filter/results.csv"
RENAMED = "outputs/20_qc_filter/out_qc_filter_results.csv"

QC_FILTER = '''#!/usr/bin/env python3
# /// codebase-meta
# name: qc_filter
# order: 20
# summary: Filter cells and write the QC table.
# outputs:
#   - path: outputs/20_qc_filter/results.csv
#     desc: Cells passing every QC threshold.
# next: [report]
# ///
"""Write the QC table."""

from pathlib import Path

OUT = Path("outputs/20_qc_filter")


def main() -> None:
    (OUT / "results.csv").write_text("sample,pass\\n")


if __name__ == "__main__":
    main()
'''

REPORT = '''#!/usr/bin/env python3
# /// codebase-meta
# name: report
# order: 30
# summary: Summarise the QC table.
# inputs:
#   - path: outputs/20_qc_filter/results.csv
#     from: qc_filter
# outputs:
#   - path: outputs/30_report/out_report_summary.md
#     desc: One-line summary of the QC table.
# next: final
# ///
"""Read the QC table."""

from pathlib import Path

TABLE = Path("outputs/20_qc_filter/results.csv")


def main() -> None:
    print(TABLE.read_text())
'''

PARAMS = "qc_table: outputs/20_qc_filter/results.csv\nthreshold: 5\n"

NOTEBOOK = {
    "cells": [
        {
            "cell_type": "markdown",
            "metadata": {},
            "source": ["# Explore\n", "Reads outputs/20_qc_filter/results.csv.\n"],
        },
        {
            "cell_type": "code",
            "execution_count": 1,
            "metadata": {},
            "outputs": [{"output_type": "stream", "text": ["read results.csv\n"]}],
            "source": [
                "import pandas as pd\n",
                'df = pd.read_csv("outputs/20_qc_filter/results.csv")\n',
            ],
        },
    ],
    "metadata": {},
    "nbformat": 4,
    "nbformat_minor": 5,
}


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in the fixture repo and fail loudly if it did not work."""
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def commit_all(root: Path) -> None:
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "fixture")


def init_repo(root: Path) -> Path:
    git(root, "init", "-q", "-b", "main")
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A four-reference fixture: a producer, a consumer, a config and a notebook."""
    root = init_repo(tmp_path)
    write(root, ".labtemplate.yml", "conformance: 3\nfrozen: false\n")
    write(root, "scripts/python/qc_filter.py", QC_FILTER)
    write(root, "scripts/python/report.py", REPORT)
    write(root, "settings/params.yml", PARAMS)
    write(root, "notebooks/explore.ipynb", json.dumps(NOTEBOOK, indent=1) + "\n")
    write(root, TABLE, "sample,pass\ns1,true\n")
    commit_all(root)
    return root


def test_propose_names_the_output_after_its_declaring_script(repo: Path) -> None:
    proposals = propose_renames(repo)

    assert [(r.old, r.new) for r in proposals] == [(TABLE, RENAMED)]
    assert "qc_filter" in proposals[0].reason


def test_propose_is_idempotent_on_a_conforming_repo(repo: Path) -> None:
    """A level-3 repo has nothing to rename, so the codemod is a no-op on it."""
    apply_renames(repo, propose_renames(repo), dry_run=False)

    assert propose_renames(repo) == []


def test_rename_map_round_trips_through_the_editable_tsv(repo: Path, tmp_path: Path) -> None:
    dest = write_rename_map(repo, tmp_path / "maps" / "rename_map.tsv")
    text = dest.read_text(encoding="utf-8")

    assert dest.is_file()
    assert text.splitlines()[0].startswith("#")
    assert read_rename_map(dest) == propose_renames(repo)
    # The reason column is the review surface; a bare old->new pair is not enough.
    assert f"{TABLE}\t{RENAMED}\t" in text


def test_rename_map_rejects_a_malformed_row(tmp_path: Path) -> None:
    bad = tmp_path / "rename_map.tsv"
    bad.write_text("old\tnew\treason\na.csv\tb.csv\tfine\nonly_two\tfields\n", encoding="utf-8")

    with pytest.raises(RenameError, match="malformed"):
        read_rename_map(bad)


def test_apply_moves_the_file_and_rewrites_every_reference(repo: Path) -> None:
    report = apply_renames(repo, propose_renames(repo), dry_run=False)

    assert not (repo / TABLE).exists()
    assert (repo / RENAMED).read_text(encoding="utf-8") == "sample,pass\ns1,true\n"

    assert report.files_rewritten == [
        "notebooks/explore.ipynb",
        "scripts/python/qc_filter.py",
        "scripts/python/report.py",
        "settings/params.yml",
    ]
    # The producer names the path twice: once in its block, once as a basename.
    assert RENAMED in (repo / "scripts/python/qc_filter.py").read_text(encoding="utf-8")
    assert 'OUT / "out_qc_filter_results.csv"' in (repo / "scripts/python/qc_filter.py").read_text(
        encoding="utf-8"
    )
    assert RENAMED in (repo / "scripts/python/report.py").read_text(encoding="utf-8")
    assert (repo / "settings/params.yml").read_text(encoding="utf-8") == (
        f"qc_table: {RENAMED}\nthreshold: 5\n"
    )
    assert TABLE not in (repo / "notebooks/explore.ipynb").read_text(encoding="utf-8")

    # No reference to the old path survives anywhere in the tree.
    grep = subprocess.run(
        ["grep", "-rIn", "--exclude-dir=.git", TABLE, "."],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert grep.returncode == 1, f"stale references remain:\n{grep.stdout}"


def test_apply_leaves_every_rewritten_file_parseable(repo: Path) -> None:
    apply_renames(repo, propose_renames(repo), dry_run=False)

    compiled = subprocess.run(
        ["python", "-m", "py_compile", "scripts/python/qc_filter.py", "scripts/python/report.py"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert compiled.returncode == 0, compiled.stderr

    notebook = json.loads((repo / "notebooks/explore.ipynb").read_text(encoding="utf-8"))
    assert notebook["nbformat"] == 4
    assert notebook["cells"][1]["source"][1] == f'df = pd.read_csv("{RENAMED}")\n'
    # Cell outputs are a record of a past run, not a reference; leave them alone.
    assert notebook["cells"][1]["outputs"][0]["text"] == ["read results.csv\n"]


def test_report_locates_every_rewrite_by_file_and_line(repo: Path) -> None:
    report = apply_renames(repo, propose_renames(repo), dry_run=False)

    wheres = {r.where for r in report.rewrites}
    assert "settings/params.yml:1" in wheres
    assert any(w.startswith("notebooks/explore.ipynb:cell") for w in wheres)
    assert all(r.before != r.after for r in report.rewrites)


def test_apply_stages_moves_and_rewrites_as_one_reversible_change(repo: Path) -> None:
    apply_renames(repo, propose_renames(repo), dry_run=False)

    staged = git(repo, "diff", "--cached", "--name-status").stdout
    assert RENAMED in staged
    assert "settings/params.yml" in staged

    git(repo, "reset", "--hard", "HEAD")
    assert (repo / TABLE).exists()
    assert not (repo / RENAMED).exists()


def test_dry_run_is_the_default_and_touches_nothing(repo: Path) -> None:
    before = {p: p.read_bytes() for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts}

    report = apply_renames(repo, propose_renames(repo))

    assert report.dry_run is True
    assert report.rewrites, "a dry run must still say what it would rewrite"
    assert [m.performed for m in report.moves] == [False]
    assert {p: p.read_bytes() for p in before} == before
    assert (repo / TABLE).exists()
    assert not (repo / RENAMED).exists()


def test_a_dry_run_still_works_on_a_dirty_worktree(repo: Path) -> None:
    """Previewing a map is how you decide to commit; requiring a clean tree first
    would make the safe mode the awkward one."""
    (repo / "settings/params.yml").write_text("qc_table: edited\n", encoding="utf-8")

    report = apply_renames(repo, propose_renames(repo))

    assert report.rewrites
    assert (repo / TABLE).exists()


def test_dirty_worktree_is_refused_and_nothing_moves(repo: Path) -> None:
    (repo / "settings/params.yml").write_text("qc_table: edited\n", encoding="utf-8")

    with pytest.raises(RenameError, match="worktree is not clean"):
        apply_renames(repo, propose_renames(repo), dry_run=False)

    assert (repo / TABLE).exists()
    assert not (repo / RENAMED).exists()


def test_two_sources_onto_one_destination_are_refused(repo: Path) -> None:
    write(repo, "outputs/20_qc_filter/other.csv", "x\n")
    commit_all(repo)
    collide = [
        Rename(TABLE, RENAMED, "first"),
        Rename("outputs/20_qc_filter/other.csv", RENAMED, "second"),
    ]

    with pytest.raises(RenameError, match="one-to-one"):
        apply_renames(repo, collide, dry_run=False)

    assert (repo / TABLE).exists()


def test_a_rename_chain_is_refused(repo: Path) -> None:
    """a -> b -> c would silently destroy b; the chain must be broken up by hand."""
    write(repo, "outputs/20_qc_filter/b.csv", "b\n")
    commit_all(repo)
    chain = [
        Rename(TABLE, "outputs/20_qc_filter/b.csv", "first"),
        Rename("outputs/20_qc_filter/b.csv", "outputs/20_qc_filter/c.csv", "second"),
    ]

    with pytest.raises(RenameError, match="chains and cycles"):
        apply_renames(repo, chain, dry_run=False)


def test_an_existing_destination_is_refused(repo: Path) -> None:
    write(repo, RENAMED, "already here\n")
    commit_all(repo)

    with pytest.raises(RenameError, match="already exists"):
        apply_renames(repo, [Rename(TABLE, RENAMED, "clash")], dry_run=False)

    assert (repo / RENAMED).read_text(encoding="utf-8") == "already here\n"


def test_renaming_a_directory_is_refused(repo: Path) -> None:
    with pytest.raises(RenameError, match="renames files only"):
        apply_renames(repo, [Rename("outputs/20_qc_filter", "outputs/20_qc", "dir")], dry_run=False)


def test_a_no_op_row_is_dropped_and_reported(repo: Path) -> None:
    report = apply_renames(repo, [Rename(TABLE, TABLE, "unchanged")], dry_run=False)

    assert report.moves == []
    assert any("identical" in w for w in report.warnings)


# --------------------------------------------------------------------------- #
# Matching precision                                                            #
# --------------------------------------------------------------------------- #

SIMILAR = '''#!/usr/bin/env python3
# /// codebase-meta
# name: load
# order: 10
# summary: Load counts.
# outputs:
#   - path: outputs/10_load/counts.csv
#     desc: Filtered count matrix.
# next: final
# ///
"""Load counts."""

import os
from pathlib import Path

OUT = Path("outputs/10_load")
COUNTS = OUT / "counts.csv"
RAW = OUT / "raw_counts.csv"
ELSEWHERE = Path("reference/counts.csv")
JOINED = os.path.join(str(OUT), "counts.csv")
INTERPOLATED = f"{OUT}/counts.csv"
ARCHIVE = OUT / "counts.csv.gz"
'''


@pytest.fixture
def similar_repo(tmp_path: Path) -> Path:
    """A repo where the target basename is a prefix of, and inside, other names."""
    root = init_repo(tmp_path)
    write(root, "scripts/python/load.py", SIMILAR)
    write(root, "outputs/10_load/counts.csv", "a\n")
    write(root, "outputs/10_load/raw_counts.csv", "b\n")
    write(root, "reference/counts.csv", "c\n")
    write(root, "run.sh", 'cat "$OUT/counts.csv" reference/counts.csv raw_counts.csv\n')
    commit_all(root)
    return root


def test_a_basename_rename_does_not_corrupt_a_longer_name(similar_repo: Path) -> None:
    apply_renames(
        similar_repo,
        [Rename("outputs/10_load/counts.csv", "outputs/10_load/out_load_counts.csv", "adr-11")],
        dry_run=False,
    )
    source = (similar_repo / "scripts/python/load.py").read_text(encoding="utf-8")

    assert 'RAW = OUT / "raw_counts.csv"' in source
    assert 'ARCHIVE = OUT / "counts.csv.gz"' in source
    assert (similar_repo / "outputs/10_load/raw_counts.csv").exists()


def test_a_literal_path_to_a_different_file_is_left_alone(similar_repo: Path) -> None:
    apply_renames(
        similar_repo,
        [Rename("outputs/10_load/counts.csv", "outputs/10_load/out_load_counts.csv", "adr-11")],
        dry_run=False,
    )
    source = (similar_repo / "scripts/python/load.py").read_text(encoding="utf-8")

    assert 'ELSEWHERE = Path("reference/counts.csv")' in source
    assert "reference/counts.csv" in (similar_repo / "run.sh").read_text(encoding="utf-8")
    assert (similar_repo / "reference/counts.csv").exists()


def test_interpolated_and_quoted_forms_are_rewritten(similar_repo: Path) -> None:
    apply_renames(
        similar_repo,
        [Rename("outputs/10_load/counts.csv", "outputs/10_load/out_load_counts.csv", "adr-11")],
        dry_run=False,
    )
    source = (similar_repo / "scripts/python/load.py").read_text(encoding="utf-8")
    shell = (similar_repo / "run.sh").read_text(encoding="utf-8")

    assert 'COUNTS = OUT / "out_load_counts.csv"' in source
    assert 'JOINED = os.path.join(str(OUT), "out_load_counts.csv")' in source
    assert 'INTERPOLATED = f"{OUT}/out_load_counts.csv"' in source
    assert '"$OUT/out_load_counts.csv"' in shell
    # A bare basename in shell argv position is still a reference.
    assert "raw_counts.csv" in shell


# --------------------------------------------------------------------------- #
# Untracked files                                                               #
# --------------------------------------------------------------------------- #


def test_an_untracked_source_falls_back_to_a_plain_move_and_says_so(repo: Path) -> None:
    write(repo, "outputs/20_qc_filter/scratch.csv", "s\n")

    scratch = Rename(
        "outputs/20_qc_filter/scratch.csv",
        "outputs/20_qc_filter/out_qc_filter_scratch.csv",
        "adr-11",
    )

    report = apply_renames(repo, [scratch], dry_run=False)

    assert [m.method for m in report.moves] == ["move"]
    assert (repo / "outputs/20_qc_filter/out_qc_filter_scratch.csv").exists()
    assert any("plain filesystem move" in w for w in report.warnings)
    assert any("cannot be undone" in w for w in report.warnings)


def test_a_renamed_file_keeps_its_own_rewrites(repo: Path) -> None:
    """A map may move a file that also references another moved path. The rewrite
    must land at the new path — writing it to the old one recreates the file the
    move just deleted, and loses the edit."""
    both = [
        Rename(TABLE, RENAMED, "adr-11"),
        Rename("settings/params.yml", "settings/out_params.yml", "moved too"),
    ]

    apply_renames(repo, both, dry_run=False)

    assert not (repo / "settings/params.yml").exists()
    assert (repo / "settings/out_params.yml").read_text(encoding="utf-8") == (
        f"qc_table: {RENAMED}\nthreshold: 5\n"
    )


def test_a_missing_source_still_gets_its_references_rewritten(repo: Path) -> None:
    """outputs/ is usually gitignored, so the artifact is often simply not there."""
    git(repo, "rm", "-q", "--", TABLE)
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "drop output")

    report = apply_renames(repo, [Rename(TABLE, RENAMED, "adr-11")], dry_run=False)

    assert [m.method for m in report.moves] == ["absent"]
    assert RENAMED in (repo / "settings/params.yml").read_text(encoding="utf-8")
    assert any("not on disk" in w for w in report.warnings)


def test_a_non_repository_is_refused(tmp_path: Path) -> None:
    write(tmp_path, "outputs/10_load/counts.csv", "a\n")

    with pytest.raises(RenameError, match="not a git repository"):
        apply_renames(
            tmp_path,
            [Rename("outputs/10_load/counts.csv", "outputs/10_load/out_load_counts.csv", "x")],
            dry_run=False,
        )

    assert (tmp_path / "outputs/10_load/counts.csv").exists()


def test_an_undeclared_output_is_not_proposed(repo: Path, tmp_path: Path) -> None:
    """Attribution comes from `codebase-meta`; a file no block declares is left be.

    LD003 only judges declared output paths, so renaming an undeclared file buys
    no conformance and would have to guess which script produced it.
    """
    write(repo, "outputs/20_qc_filter/orphan.csv", "o\n")
    commit_all(repo)

    dest = write_rename_map(repo, tmp_path / "rename_map.tsv")

    assert "orphan.csv" not in dest.read_text(encoding="utf-8")
    assert [r.old for r in read_rename_map(dest)] == [TABLE]
