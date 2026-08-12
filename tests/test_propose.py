"""Where the manifest says a file belongs — especially before the repo agrees.

The fixtures here are plain trees, not git repositories: nothing is moved, so
there is no `git reset --hard` promise to test (that is test_rename.py's job).
What is asserted instead is the level-2 to level-3 transition, where no file is
under `outputs/` yet and every target has to come from the declaring block.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from labcore.labdocs.propose import propose
from labcore.labdocs.rename import (
    Rename,
    RenameError,
    apply_renames,
    propose_renames,
    read_rename_map,
    write_rename_map,
)

PREFILTER = '''#!/usr/bin/env python3
# /// codebase-meta
# name: step0_prefilter
# order: 0
# summary: Prefilter the microprotein table.
# outputs:
#   - path: tmp/filtered_microproteins.tsv
#     desc: Rows passing the prefilter.
# next: [step1_motifs]
# ///
"""Prefilter."""

from pathlib import Path

OUT = Path("tmp/filtered_microproteins.tsv")
'''

MOTIFS = '''#!/usr/bin/env python3
# /// codebase-meta
# name: step1_motifs
# order: 10
# summary: Scan the prefiltered table for motifs.
# inputs:
#   - path: tmp/filtered_microproteins.tsv
#     from: step0_prefilter
# outputs:
#   - path: output/motifs.tsv
#     desc: One row per motif hit.
# next: final
# ///
"""Motifs."""
'''

PLOTTER = '''#!/usr/bin/env python3
# /// codebase-meta
# name: plot_tis_type
# order: 130
# summary: Bar chart of the TIS-type split, one figure per theme.
# outputs:
#   - path: plots/tis_type_barplot_<theme>.pdf
#     desc: TIS types by percentage; <theme> is each entry of themes in the config.
#   - path: plots/
#     desc: Directory the figures are written to.
# next: final
# ///
"""Plot."""
'''

MATCHER = '''#!/usr/bin/env python3
# /// codebase-meta
# name: match_ribotish_with_d
# order: 230
# summary: Match the Ribo-TISH calls against dataset D.
# outputs:
#   - path: out/counts_barplot.png
#     desc: Match counts per category.
#   - path: results/nf_core_summary.tsv
#     desc: Summary written into the nf-core results tree.
# next: final
# ///
"""Match."""
'''

RUNNER = """#!/usr/bin/env bash
# /// codebase-meta
# name: run_pipeline
# order: 70
# summary: Run every step in order.
# outputs:
#   - path: output/
#     desc: Everything the run writes.
# next: final
# ///
python3 step0_prefilter.py
"""

HELPER = '''#!/usr/bin/env python3
# /// codebase-meta
# exempt: Pure helper module; writes nothing of its own.
# ///
"""Shared figure helpers."""
'''

LAUNCHER = """#!/usr/bin/env bash
# /// codebase-meta
# exempt: Sourced helper library, not a pipeline step.
# ///
echo sourced
"""

CONFORMING = '''#!/usr/bin/env python3
# /// codebase-meta
# name: qc_filter
# order: 20
# summary: Filter cells and write the QC table.
# outputs:
#   - path: outputs/20_qc_filter/out_qc_filter_results.csv
#     desc: Cells passing every QC threshold.
# next: final
# ///
"""Already where the manifest says it should be."""
'''

RUNTIME_OUT = "plots/tis_type_barplot_<theme>.pdf"


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def lvl2(tmp_path: Path) -> Path:
    """A brownfield repo: scripts at the root, outputs over four different roots.

    This is the shape the codemod exists for, and the shape it used to have
    nothing to say about, because none of these paths is under `outputs/` yet.
    """
    write(tmp_path, ".labtemplate.yml", "conformance: 2\nfrozen: false\nnaming_exempt: []\n")
    write(tmp_path, "step0_prefilter.py", PREFILTER)
    write(tmp_path, "step1_motifs.py", MOTIFS)
    write(tmp_path, "plot_tis_type.py", PLOTTER)
    write(tmp_path, "match_ribotish_with_D.py", MATCHER)
    write(tmp_path, "run_pipeline.sh", RUNNER)
    write(tmp_path, "bin/lib_slurm.sh", LAUNCHER)
    write(tmp_path, "scripts/python/plot_utils.py", HELPER)
    # The scattered roots really exist on disk, which is what makes a directory
    # row dangerous rather than merely useless.
    write(tmp_path, "tmp/filtered_microproteins.tsv", "id\n")
    write(tmp_path, "output/motifs.tsv", "id\tmotif\n")
    write(tmp_path, "plots/tis_type_barplot_light.pdf", "%PDF\n")
    return tmp_path


def test_an_output_is_placed_by_its_block_not_by_where_it_sits_today(lvl2: Path) -> None:
    """The level-2 to level-3 move: nothing is under outputs/ yet, so reading the
    target off the current location proposes nothing at all."""
    proposals = propose_renames(lvl2)
    targets = {r.old: r.new for r in proposals}

    assert targets["tmp/filtered_microproteins.tsv"] == (
        "outputs/00_step0_prefilter/out_step0_prefilter_filtered_microproteins.tsv"
    )
    assert targets["output/motifs.tsv"] == "outputs/10_step1_motifs/out_step1_motifs_motifs.tsv"
    assert targets["out/counts_barplot.png"] == (
        "outputs/230_match_ribotish_with_d/out_match_ribotish_with_d_counts_barplot.png"
    )
    assert "step1_motifs" in next(r for r in proposals if r.old == "output/motifs.tsv").reason


def test_a_script_moves_into_its_language_directory_and_loses_its_capital(lvl2: Path) -> None:
    targets = {r.old: r.new for r in propose_renames(lvl2)}

    assert targets["match_ribotish_with_D.py"] == "scripts/python/match_ribotish_with_d.py"
    assert targets["run_pipeline.sh"] == "scripts/bash/run_pipeline.sh"
    # Already home, and bin/ is a script root of its own; neither is busywork.
    assert "scripts/python/plot_utils.py" not in targets
    assert "bin/lib_slurm.sh" not in targets


def test_a_runtime_path_is_commented_rather_than_dropped(lvl2: Path, tmp_path: Path) -> None:
    """An omitted row makes an incomplete map look complete, which is worse than
    a row a human still has to finish by hand."""
    proposals = propose(lvl2)

    assert RUNTIME_OUT not in [r.old for r in proposals.renames]
    row = next(r for r in proposals.unmappable if r.old == RUNTIME_OUT)
    assert "runtime" in row.reason
    # The placeholder survives into the target, so the row states what the script
    # writing it has to be changed to.
    assert row.new == "outputs/130_plot_tis_type/out_plot_tis_type_tis_type_barplot_<theme>.pdf"

    dest = write_rename_map(lvl2, tmp_path / "maps" / "rename_map.tsv")

    assert f"# {RUNTIME_OUT}\t" in dest.read_text(encoding="utf-8")
    # Commented, so it is inert until a human uncomments and edits it.
    assert [r.old for r in read_rename_map(dest)] == [r.old for r in proposals.renames]


def test_a_directory_output_is_commented_so_the_rest_of_the_map_applies(lvl2: Path) -> None:
    proposals = propose(lvl2)

    assert "plots/" in [r.old for r in proposals.unmappable]
    assert apply_renames(lvl2, proposals.renames, dry_run=True).moves
    # Left live, that one row refuses the whole run — hence the comment.
    with pytest.raises(RenameError, match="renames files only"):
        apply_renames(
            lvl2,
            [*proposals.renames, Rename("plots/", "outputs/130_plot_tis_type/", "dir")],
            dry_run=True,
        )


def test_the_nf_core_results_tree_is_left_to_nf_core(lvl2: Path) -> None:
    """ADR-11 exempts results/; relocating it would fight the pipeline that owns it."""
    proposals = propose(lvl2)

    assert not [r for r in proposals.renames + proposals.unmappable if r.old.startswith("results/")]


def test_a_conforming_repo_proposes_nothing_at_all(tmp_path: Path) -> None:
    """Level 3 in, empty map out. Deriving targets from the manifest must not
    invent work for a repo already sitting where the manifest says it should."""
    write(tmp_path, ".labtemplate.yml", "conformance: 3\nfrozen: false\n")
    write(tmp_path, "scripts/python/qc_filter.py", CONFORMING)
    write(tmp_path, "outputs/20_qc_filter/out_qc_filter_results.csv", "sample,pass\n")

    proposals = propose(tmp_path)
    dest = write_rename_map(tmp_path, tmp_path / "rename_map.tsv")
    rows = [ln for ln in dest.read_text(encoding="utf-8").splitlines() if not ln.startswith("#")]

    assert (proposals.renames, proposals.unmappable) == ([], [])
    assert rows == ["\t".join(("old", "new", "reason"))]


def test_ld010_catches_a_step_never_migrated(tmp_path):
    """A step still writing outside outputs/ must fail level 3.

    LD003 only governs paths already under outputs/, so a repo was declared
    level 3 with one step never migrated and lint stayed green. LD010 asks
    propose() where the output belongs, so the linter and the rename map cannot
    disagree again.
    """
    from labcore.labdocs.lint import lint_project

    root = tmp_path / "p"
    (root / "scripts" / "python").mkdir(parents=True)
    (root / ".labtemplate.yml").write_text(
        "conformance: 3\nfrozen: false\nnaming_exempt: []\n", encoding="utf-8"
    )
    for name in (".copier-answers.yml", "AGENTS.md", "CLAUDE.md"):
        (root / name).write_text("x\n", encoding="utf-8")
    (root / "knowledge").mkdir()
    (root / "knowledge" / "overview.md").write_text("x\n", encoding="utf-8")
    (root / "scripts" / "python" / "matcher.py").write_text(
        '# /// codebase-meta\n'
        '# name: matcher\n'
        '# order: 230\n'
        '# summary: Still writes to the pre-migration location.\n'
        '# outputs:\n'
        '#   - path: out/matches_perfect.csv\n'
        '#     desc: Perfect matches.\n'
        '# next: final\n'
        '# ///\n',
        encoding="utf-8",
    )
    codes = [f.code for f in lint_project(root) if f.severity == "error"]
    assert "LD010" in codes, (
        "a step declaring out/ instead of outputs/230_matcher/ passed level 3 — "
        "the linter is blind to placement again"
    )
