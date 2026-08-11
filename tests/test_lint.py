"""Every failure class the reference says the engine must reject, one test each."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from labcore.labdocs.lint import Finding, lint_project
from labcore.labdocs.walk import walk_project

FIXTURES = Path(__file__).parent / "fixtures"
DEMO = FIXTURES / "demo_project"
BROKEN = FIXTURES / "broken"


@pytest.fixture(scope="module")
def broken_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The broken fixtures assembled into a project the walker can see.

    They live flat in tests/fixtures/broken/ so each one reads as a standalone
    example; the linter only walks scripts/**, so they are copied there whole —
    wrong_from.py needs dangling_next.py present to resolve its `from:` edge.
    """
    root = tmp_path_factory.mktemp("broken_project")
    dest = root / "scripts" / "python"
    dest.mkdir(parents=True)
    for source in sorted(BROKEN.glob("*.py")):
        shutil.copy(source, dest / source.name)
    return root


def codes_at(findings: list[Finding], filename: str) -> set[str]:
    """Codes reported against one file."""
    return {f.code for f in findings if f.path.name == filename}


def test_ld001_script_without_a_block(broken_root: Path) -> None:
    assert "LD001" in codes_at(lint_project(broken_root), "missing_block.py")


def test_ld002_output_without_desc(broken_root: Path) -> None:
    assert "LD002" in codes_at(lint_project(broken_root), "no_desc.py")


def test_ld003_filename_violating_the_naming_regime(broken_root: Path) -> None:
    assert "LD003" in codes_at(lint_project(broken_root), "bad_name.py")


def test_ld004_next_pointing_at_a_missing_script(broken_root: Path) -> None:
    assert "LD004" in codes_at(lint_project(broken_root), "dangling_next.py")


def test_ld005_input_with_neither_from_nor_external(broken_root: Path) -> None:
    assert "LD005" in codes_at(lint_project(broken_root), "bare_input.py")


def test_ld006_from_naming_a_path_its_producer_never_writes(broken_root: Path) -> None:
    assert "LD006" in codes_at(lint_project(broken_root), "wrong_from.py")


def test_demo_project_lints_clean() -> None:
    """The exempt: helper and the external: true input must both pass."""
    errors = [f for f in lint_project(DEMO) if f.severity == "error"]
    assert errors == []


def test_demo_project_theme_suffixed_figures_are_legal() -> None:
    """D-005a's _white/_black tokens are reserved, not naming violations."""
    assert not [f for f in lint_project(DEMO) if f.code == "LD003"]


def test_findings_carry_a_line_inside_the_offending_block(broken_root: Path) -> None:
    findings = [f for f in lint_project(broken_root) if f.code == "LD004"]
    assert findings[0].line > 1


def test_ld007_docker_io_container_is_a_warning(tmp_path: Path) -> None:
    script = tmp_path / "scripts" / "python" / "devimage.py"
    script.parent.mkdir(parents=True)
    script.write_text(
        "# /// codebase-meta\n"
        "# name: devimage\n"
        "# order: 10\n"
        "# summary: Step pinned to a dev-only image.\n"
        "# outputs:\n"
        "#   - path: outputs/10_devimage/out_devimage_table.parquet\n"
        "#     desc: Placeholder output.\n"
        "# next: final\n"
        "# container: docker.io/library/python:3.13\n"
        "# ///\n",
        encoding="utf-8",
    )
    findings = lint_project(tmp_path)
    assert [(f.code, f.severity) for f in findings] == [("LD007", "warning")]


def test_walk_reports_flavour_and_every_script() -> None:
    project = walk_project(DEMO)
    assert project.flavour == "analysis"
    assert len(project.script_paths) == 7
    assert project.errors == []
    assert [b.name for b in project.steps] == [
        "fetch",
        "load",
        "qc_filter",
        "normalise",
        "differential",
        "figures",
    ]


def test_walk_detects_a_pipeline_and_its_components(tmp_path: Path) -> None:
    (tmp_path / "main.nf").write_text("workflow {}\n", encoding="utf-8")
    (tmp_path / "nextflow.config").write_text("manifest {}\n", encoding="utf-8")
    meta = tmp_path / "modules" / "local" / "star_align" / "meta.yml"
    meta.parent.mkdir(parents=True)
    meta.write_text("name: star_align\ntools:\n  - star:\n      version: 2.7.11b\n", "utf-8")

    project = walk_project(tmp_path)

    assert project.flavour == "pipeline"
    assert [c.name for c in project.components] == ["star_align"]
