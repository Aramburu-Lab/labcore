"""Tests for `labdocs adopt` — the static drafter that gets a repo to level 2.

The fixtures are written to disk rather than mocked, because the whole risk in
this command is real-world mess: a script with no docstring, no parser and bare
`open()` calls has to produce a block rather than a traceback.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest

from labcore.labdocs.adopt import (
    PLACEHOLDER,
    PLACEHOLDER_SUMMARY,
    AdoptError,
    adopt_project,
    draft_block,
    inspect_script,
    normalise_name,
    render_report,
)
from labcore.meta import MetaError, extract_block, validate_block

AWFUL = """\
import os
import sys

d = sys.argv[1]
f = open(os.path.join(d, "raw.txt"))
rows = [line.strip() for line in f]
out = open("dump.txt", "w")
out.write("\\n".join(rows))
"""

ARGPARSED = '''\
#!/usr/bin/env python3
"""Score candidate ORFs against the reference.

Longer prose that must not end up in the summary.
"""

import argparse

import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-i", "--input", required=True, help="Input Ribo-TISH TSV")
    ap.add_argument("--workers", type=int, default=6, help="Worker processes")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("positional")
    args = ap.parse_args()
    frame = pd.read_csv("dump.txt", sep="\\t")
    frame.to_csv("scored.tsv", sep="\\t")
'''

DOCOPTED = '''\
"""Merge the per-step tables.

Usage:
  merge.py [--threads=N] [--outdir=DIR]

Options:
  --threads=N  Parallel merges [default: 4]
  --outdir=DIR  Where the merged table lands
"""

from docopt import docopt

args = docopt(__doc__)
'''

BROKEN = "def main(:\n    pass\n"

SHELL = """\
#!/usr/bin/env bash
# Drive the whole pipeline end to end.
set -euo pipefail

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --outdir) OUT=$2 ;;
  esac
  shift
done

python3 merge.py < scored.tsv > merged.tsv 2>/dev/null
echo done >> run.log
"""

ALREADY = """\
# /// codebase-meta
# name: helpers
# exempt: shared helpers, no standalone outputs
# ///
def helper():
    return 1
"""

RSCRIPT = """\
# Plot the merged table.
df <- read.csv("merged.tsv")
ggsave("figure.pdf", plot(df))
"""


@pytest.fixture
def messy(tmp_path: Path) -> Path:
    """A deliberately awful repo: no docstrings, a broken file, mixed languages."""
    files = {
        "step0_prefilter.py": AWFUL,
        "step1_score.py": ARGPARSED,
        "merge.py": DOCOPTED,
        "half_written.py": BROKEN,
        "run_pipeline.sh": SHELL,
        "helpers.py": ALREADY,
        "sub/plot_figures.R": RSCRIPT,
    }
    for name, body in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("step4_merge", "merge"),
        ("01a_prefilter", "prefilter"),
        ("RUN_2_score", "score"),
        ("step0_prefilter", "prefilter"),
        ("run_pipeline", "run_pipeline"),
        ("match_ribotish_with_D", "match_ribotish_with_d"),
        ("plot-aalen full", "plot_aalen_full"),
        ("42", "42"),
    ],
)
def test_normalise_name(stem: str, expected: str):
    assert normalise_name(stem) == expected


def test_awful_script_drafts_rather_than_crashing(messy: Path):
    draft = inspect_script(messy / "step0_prefilter.py")

    assert draft.name == "prefilter"
    assert draft.summary == PLACEHOLDER_SUMMARY
    assert draft.provenance["summary"] == PLACEHOLDER
    # Bare open() is the only IO signal in the file and it must still be read.
    assert [i["path"] for i in draft.inputs] == ["os.path.join(d, 'raw.txt')"]
    assert [o["path"] for o in draft.outputs] == ["dump.txt"]


def test_argparse_options_carry_flag_desc_and_default(messy: Path):
    draft = inspect_script(messy / "step1_score.py")

    by_flag = {o["flag"]: o for o in draft.options}
    assert by_flag["--input"]["desc"] == "Input Ribo-TISH TSV"
    assert by_flag["--workers"]["default"] == 6
    # No help= in the source: a placeholder desc, never a missing key (LD000).
    assert len(by_flag["--quiet"]["desc"]) >= 3
    # A positional cannot be expressed as a flag, so it is dropped, not faked.
    assert "positional" not in by_flag


def test_summary_is_first_docstring_line_only(messy: Path):
    draft = inspect_script(messy / "step1_score.py")

    assert draft.summary == "Score candidate ORFs against the reference."
    assert draft.provenance["summary"] == "derived"


def test_docopt_usage_block_yields_options(messy: Path):
    draft = inspect_script(messy / "merge.py")

    by_flag = {o["flag"]: o for o in draft.options}
    assert by_flag["--threads"]["default"] == "4"
    assert by_flag["--threads"]["desc"] == "Parallel merges"
    assert by_flag["--outdir"]["desc"] == "Where the merged table lands"


def test_shell_redirections_classify_by_direction(messy: Path):
    draft = inspect_script(messy / "run_pipeline.sh")

    assert [i["path"] for i in draft.inputs] == ["scored.tsv"]
    assert [o["path"] for o in draft.outputs] == ["merged.tsv", "run.log"]
    assert {o["flag"] for o in draft.options} == {"--dry-run", "--outdir"}


def test_unparseable_python_is_reported_not_raised(messy: Path):
    with pytest.raises(AdoptError):
        inspect_script(messy / "half_written.py")

    report = adopt_project(messy)
    failed = [s for s in report.scripts if s.status == "failed"]
    assert [s.path.name for s in failed] == ["half_written.py"]
    assert "does not parse" in failed[0].reason


def test_every_emitted_block_is_draft_and_parses(messy: Path):
    report = adopt_project(messy)
    drafted = [s for s in report.scripts if s.status == "drafted"]
    assert drafted

    for status in drafted:
        block = extract_block(status.path, text=status.block)
        assert block is not None, status.path
        assert block.data["draft"] is True
        assert validate_block(block) == [], f"{status.path}: {validate_block(block)}"


def test_blocks_never_guess_order_or_next(messy: Path):
    report = adopt_project(messy)

    for status in (s for s in report.scripts if s.status == "drafted"):
        data = extract_block(status.path, text=status.block).data
        assert data["order"] == 0
        assert "next" not in data
        # from: is intent too — every input is external until a human says so.
        assert all(i.get("external") is True for i in data.get("inputs", []))
        assert all("from" not in i for i in data.get("inputs", []))


def test_order_proposal_comes_from_path_overlaps(messy: Path):
    report = adopt_project(messy)

    assert ("prefilter", "score") in report.edges
    assert ("score", "run_pipeline") in report.edges
    assert report.proposal.index("prefilter") < report.proposal.index("score")
    assert report.proposal.index("score") < report.proposal.index("run_pipeline")
    assert not report.cycles


def test_file_with_an_existing_block_is_skipped(messy: Path):
    report = adopt_project(messy)

    skipped = {s.path.name: s for s in report.scripts if s.status == "skipped"}
    assert "helpers.py" in skipped
    assert "already carries a block" in skipped["helpers.py"].reason


def test_r_script_uses_the_generic_inspector(messy: Path):
    draft = inspect_script(messy / "sub" / "plot_figures.R")

    assert draft.summary == "Plot the merged table."
    assert [i["path"] for i in draft.inputs] == ["merged.tsv"]
    assert [o["path"] for o in draft.outputs] == ["figure.pdf"]


def test_report_carries_provenance_and_totals(messy: Path):
    report = adopt_project(messy)
    totals = report.totals

    assert totals["considered"] == 7
    assert totals["drafted"] == 5
    assert totals["skipped"] == 1
    assert totals["failed"] == 1
    assert totals["derived_fields"] > totals["placeholder_fields"]
    assert 0.0 < report.derived_ratio <= 1.0
    # The acceptance bar from build plan §9: >=80% drafted, zero crashes.
    assert (
        report.coverage >= 0.8 * (totals["considered"] - totals["skipped"]) / totals["considered"]
    )
    drafted = [s for s in report.scripts if s.status == "drafted"]
    assert all(s.provenance["order"] == PLACEHOLDER for s in drafted)
    assert all(s.provenance["next"] == "absent" for s in drafted)


def test_write_inserts_a_block_that_reparses_and_is_idempotent(messy: Path):
    first = adopt_project(messy, write=True)
    assert first.written

    for path in first.written:
        block = extract_block(path)
        assert block is not None
        assert block.data["draft"] is True

    # A shebang must stay on line 1 or the script stops being executable.
    assert (messy / "run_pipeline.sh").read_text().splitlines()[0] == "#!/usr/bin/env bash"
    # An adoption pass that breaks the scripts it documents is worse than none.
    for path in (p for p in first.written if p.suffix == ".py"):
        ast.parse(path.read_text(encoding="utf-8"))
    # Re-running must not stack a second block; meta.py rejects two.
    second = adopt_project(messy, write=True)
    assert second.totals["drafted"] == 0
    assert second.totals["skipped"] == 6


def test_draft_block_returns_none_for_documented_and_broken_files(messy: Path):
    assert draft_block(messy / "helpers.py") is None
    assert draft_block(messy / "half_written.py") is None
    assert draft_block(messy / "step1_score.py") is not None


def test_name_collisions_are_reported(tmp_path: Path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    for sub in ("a", "b"):
        (tmp_path / sub / "step1_motifs.py").write_text("x = 1\n", encoding="utf-8")

    report = adopt_project(tmp_path)
    assert list(report.name_collisions) == ["motifs"]
    assert len(report.name_collisions["motifs"]) == 2


def test_nothing_is_executed_on_import(tmp_path: Path):
    landmine = tmp_path / "landmine.py"
    landmine.write_text(
        textwrap.dedent(
            """\
            import sys

            sys.exit("adopt imported project code")
            raise SystemExit(1)
            """
        ),
        encoding="utf-8",
    )

    draft = inspect_script(landmine)
    assert draft.name == "landmine"


def test_render_report_is_markdown_and_names_the_proposal(messy: Path):
    text = render_report(adopt_project(messy))

    assert text.startswith("# labdocs adopt")
    assert "| Script | Status |" in text
    assert "Proposed run order" in text
    assert "prefilter -> " in text
    assert "path overlap(s) found" in text


def test_report_refuses_to_dress_an_alphabetical_list_as_a_proposal(tmp_path: Path):
    # Two unrelated scripts: nothing links them, so the order is not evidence.
    (tmp_path / "alpha.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "beta.py").write_text("y = 2\n", encoding="utf-8")

    report = adopt_project(tmp_path)
    assert report.edges == []
    assert "alphabetical, not derived" in render_report(report)


def test_unsupported_extension_raises_rather_than_drafting(tmp_path: Path):
    odd = tmp_path / "notes.md"
    odd.write_text("# not a script\n", encoding="utf-8")

    with pytest.raises(AdoptError):
        inspect_script(odd)
    # ...and the walker never offers it in the first place.
    assert adopt_project(tmp_path).totals["considered"] == 0


def test_double_block_file_fails_instead_of_aborting_the_run(tmp_path: Path):
    doubled = tmp_path / "twice.py"
    doubled.write_text(ALREADY + ALREADY, encoding="utf-8")
    (tmp_path / "fine.py").write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(MetaError):
        extract_block(doubled)

    report = adopt_project(tmp_path)
    assert report.totals["failed"] == 1
    assert report.totals["drafted"] == 1
