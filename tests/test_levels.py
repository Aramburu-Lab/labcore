"""The conformance ladder: lint enforces the declared level and nothing above it.

Two tests carry the whole design (build plan §9.1, §9.3) and everything else here
is scaffolding around them:

* a repo with **no metadata at all** lints clean at level 1 and dirty at level 2 —
  if this fails, a half-migrated repo has a permanently-red CI and the ratchet is
  pointless;
* a repo with `draft: true` lints clean-with-warnings at level 2 and dirty at
  level 3 — if this fails, `labdocs adopt` drafts quietly become permanent.
"""

from __future__ import annotations

from pathlib import Path

from labcore.labdocs.audit import audit_repos, render_audit
from labcore.labdocs.cli import main
from labcore.labdocs.levels import (
    LEVEL1_FILES,
    MAX_LEVEL,
    assess,
    draft_severity,
    read_config,
    read_level,
    requirements,
    resolve_level,
)
from labcore.labdocs.lint import lint_project

NO_METADATA = "import sys\n\nprint(sys.version)\n"

DRAFT_BLOCK = """\
# /// codebase-meta
# name: qc_filter
# order: 10
# summary: Drop low-quality cells by MAD thresholds.
# draft: true
# outputs:
#   - path: outputs/10_qc_filter/out_qc_filter_clean.parquet
#     desc: Cells passing all QC thresholds.
# next: final
# ///
"""

REVIEWED_BLOCK = DRAFT_BLOCK.replace("# draft: true\n", "")


def make_repo(root: Path, level: int, script: str = "", *, name: str = "qc_filter") -> Path:
    """Build an adopted repo: the five level-1 files plus one script.

    Args:
        root: Directory to build in; created if absent.
        level: Value for ``conformance:`` in ``.labtemplate.yml``.
        script: Contents of the single script. Omitted means no script at all.
        name: Script stem, so a block's ``name:`` can match its filename.

    Returns:
        The repo root.
    """
    root.mkdir(parents=True, exist_ok=True)
    (root / "knowledge").mkdir(exist_ok=True)
    (root / ".copier-answers.yml").write_text("_commit: v0.1.0\n", encoding="utf-8")
    (root / ".labtemplate.yml").write_text(
        f"conformance: {level}\nfrozen: false\nnaming_exempt: []\n", encoding="utf-8"
    )
    (root / "knowledge" / "overview.md").write_text("# Overview\n", encoding="utf-8")
    (root / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    (root / "CLAUDE.md").write_text("# Claude\n", encoding="utf-8")
    if script:
        target = root / "scripts" / "python" / f"{name}.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(script, encoding="utf-8")
    return root


def errors(findings: list) -> list[str]:
    """Codes of the error-severity findings."""
    return [f.code for f in findings if f.severity == "error"]


# --- the central claim ------------------------------------------------------


def test_no_metadata_lints_clean_at_level_1(tmp_path: Path) -> None:
    """Level 1 is structural presence only: an undocumented repo is compliant."""
    repo = make_repo(tmp_path / "adopted", 1, NO_METADATA, name="legacy_step")

    assert lint_project(repo) == []


def test_no_metadata_lints_dirty_at_level_2(tmp_path: Path) -> None:
    """The same tree, one rung up, must fail on the missing block."""
    repo = make_repo(tmp_path / "documented", 2, NO_METADATA, name="legacy_step")

    findings = lint_project(repo)

    assert "LD001" in errors(findings)


def test_draft_is_a_warning_at_level_2(tmp_path: Path) -> None:
    """A draft block is reported at level 2, but does not fail the build."""
    repo = make_repo(tmp_path / "drafting", 2, DRAFT_BLOCK)

    findings = lint_project(repo)

    assert errors(findings) == []
    assert [(f.code, f.severity) for f in findings] == [("LD009", "warning")]


def test_draft_is_an_error_at_level_3(tmp_path: Path) -> None:
    """Level 3 is what stops adopt-generated drafts becoming permanent."""
    repo = make_repo(tmp_path / "structured", 3, DRAFT_BLOCK)

    findings = lint_project(repo)

    assert errors(findings) == ["LD009"]


def test_the_same_block_reviewed_lints_clean_at_level_3(tmp_path: Path) -> None:
    """Proves LD009 is what flipped, not some other defect in the fixture."""
    repo = make_repo(tmp_path / "reviewed", 3, REVIEWED_BLOCK)

    assert lint_project(repo) == []


# --- gating mechanics -------------------------------------------------------


def test_level_0_opts_out_entirely(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "unmanaged", 0, NO_METADATA, name="legacy_step")

    assert lint_project(repo) == []


def test_an_undeclared_tree_is_held_to_the_full_standard(tmp_path: Path) -> None:
    """Silence is not an opt-out; only an explicit `conformance: 0` is."""
    repo = make_repo(tmp_path / "silent", 2, NO_METADATA, name="legacy_step")
    (repo / ".labtemplate.yml").unlink()

    assert resolve_level(repo) == MAX_LEVEL
    assert "LD001" in errors(lint_project(repo))


def test_explicit_level_overrides_the_declaration(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "override", 1, NO_METADATA, name="legacy_step")

    assert lint_project(repo, level=1) == []
    assert "LD001" in errors(lint_project(repo, level=2))


def test_ld003_and_ld004_only_bite_from_level_3(tmp_path: Path) -> None:
    """A dangling `next:` is a level-3 concern, invisible to a documented repo."""
    block = DRAFT_BLOCK.replace("# draft: true\n", "").replace(
        "# next: final\n", "# next: [nowhere]\n"
    )
    repo = make_repo(tmp_path / "dangling", 2, block)

    assert lint_project(repo) == []
    assert errors(lint_project(repo, level=3)) == ["LD004"]


def test_missing_adoption_file_is_ld008_at_level_1(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "partial", 1, REVIEWED_BLOCK)
    (repo / "AGENTS.md").unlink()

    findings = lint_project(repo)

    assert [(f.code, f.path.name) for f in findings] == [("LD008", "AGENTS.md")]


def test_requirements_are_cumulative() -> None:
    assert requirements(0) == set()
    assert requirements(1) == {"LD008"}
    assert requirements(1) < requirements(2) < requirements(3) < requirements(4)
    assert {"LD001", "LD002", "LD005", "LD006"} <= requirements(2)
    assert {"LD003", "LD004"} <= requirements(3)
    assert "LD007" in requirements(4)


def test_draft_severity_flips_at_level_3() -> None:
    assert draft_severity(2) == "warning"
    assert draft_severity(3) == "error"


# --- configuration reading --------------------------------------------------


def test_read_level_defaults_to_zero(tmp_path: Path) -> None:
    assert read_level(tmp_path) == 0

    (tmp_path / ".labtemplate.yml").write_text("frozen: true\n", encoding="utf-8")
    assert read_level(tmp_path) == 0


def test_read_config_returns_every_key_with_defaults(tmp_path: Path) -> None:
    (tmp_path / ".labtemplate.yml").write_text(
        "conformance: 3\nfrozen: true\nnaming_exempt: ['legacy/**']\nfile_length:\n  warn: 150\n",
        encoding="utf-8",
    )

    config = read_config(tmp_path)

    assert config["conformance"] == 3
    assert config["frozen"] is True
    assert config["naming_exempt"] == ["legacy/**"]
    assert config["file_length"] == {"warn": 150, "fail": 600}


def test_read_config_rejects_a_boolean_conformance(tmp_path: Path) -> None:
    """`conformance: true` is a typo, not level 1."""
    (tmp_path / ".labtemplate.yml").write_text("conformance: true\n", encoding="utf-8")

    assert read_config(tmp_path)["conformance"] is None


# --- assessment and the audit table -----------------------------------------


def test_assess_reports_the_level_actually_met(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "drifted", 3, NO_METADATA, name="legacy_step")

    report = assess(repo)

    assert (report.declared, report.assessed) == (3, 1)
    assert report.drifted is True
    assert any("LD001" in reason for reason in report.unmet)


def test_assess_names_the_missing_level_1_files(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "bare", 1, REVIEWED_BLOCK)
    (repo / "CLAUDE.md").unlink()

    report = assess(repo)

    assert report.assessed == 0
    assert report.unmet == ("missing CLAUDE.md",)
    assert set(LEVEL1_FILES) >= {"CLAUDE.md", "AGENTS.md"}


def test_assess_counts_drafts_and_stops_below_level_3(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "drafts", 2, DRAFT_BLOCK)

    report = assess(repo)

    assert (report.assessed, report.drafts, report.drifted) == (2, 1, False)


def test_audit_prints_declared_and_assessed_and_flags_drift(tmp_path: Path) -> None:
    make_repo(tmp_path / "honest", 1, REVIEWED_BLOCK)
    make_repo(tmp_path / "lying", 3, NO_METADATA, name="legacy_step")

    rows = audit_repos([tmp_path])
    table = render_audit(rows)

    # `honest` declares 1 and satisfies 3 — assessment reports what is true, and
    # exceeding a declaration is not drift. `lying` is the row worth a PR.
    assert [(r.path.name, r.conformance, r.assessed, r.drifted) for r in rows] == [
        ("honest", 1, 3, False),
        ("lying", 3, 1, True),
    ]
    assert "| Repo | Template | Declared | Assessed | Frozen | Drafts |" in table
    assert "Drifted (assessed below declared):" in table
    assert "lying" in table.rsplit("\n", 1)[-1]


def test_frozen_repo_is_reported_not_omitted(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "frozen_repo", 1, REVIEWED_BLOCK)
    (repo / ".labtemplate.yml").write_text("conformance: 1\nfrozen: true\n", encoding="utf-8")

    rows = audit_repos([tmp_path])

    assert [(r.path.name, r.frozen) for r in rows] == [("frozen_repo", True)]
    assert "| yes |" in render_audit(rows)


# --- the exit code, not just the finding count ------------------------------


def test_cli_exit_code_follows_the_declared_level(tmp_path: Path, capsys) -> None:
    """L-006: assert the exit status, not only the findings the API returned."""
    adopted = make_repo(tmp_path / "cli_level1", 1, NO_METADATA, name="legacy_step")
    documented = make_repo(tmp_path / "cli_level2", 2, NO_METADATA, name="legacy_step")

    assert main(["lint", "--root", str(adopted)]) == 0
    assert capsys.readouterr().out == ""

    assert main(["lint", "--root", str(documented)]) == 1
    assert "LD001" in capsys.readouterr().out


def test_cli_exit_code_is_clean_for_a_draft_at_level_2(tmp_path: Path, capsys) -> None:
    """A warning is printed but must not fail the commit hook."""
    repo = make_repo(tmp_path / "cli_draft", 2, DRAFT_BLOCK)

    assert main(["lint", "--root", str(repo)]) == 0
    assert "LD009" in capsys.readouterr().out


class TestFlatRepoIsGraded:
    """Level 2 forbids moving files, so lint must see scripts where they are.

    `walk_project` used to look only under `scripts/`, so a brownfield repo — the
    exact shape level 2 exists for — lint-passed at level 2 with every script
    undocumented. `labdocs adopt` saw those scripts and drafted blocks for them,
    so the two halves of the migration path disagreed about what a project was.
    """

    @staticmethod
    def _flat(tmp_path, level: int):
        (tmp_path / "analysis.py").write_text('"""Do a thing."""\nprint(1)\n', encoding="utf-8")
        (tmp_path / ".labtemplate.yml").write_text(
            f"conformance: {level}\nfrozen: false\nnaming_exempt: []\n", encoding="utf-8"
        )
        for name in (".copier-answers.yml", "AGENTS.md", "CLAUDE.md"):
            (tmp_path / name).write_text("x\n", encoding="utf-8")
        (tmp_path / "knowledge").mkdir(exist_ok=True)
        (tmp_path / "knowledge" / "overview.md").write_text("x\n", encoding="utf-8")
        return tmp_path

    def test_undocumented_root_script_fails_level_2(self, tmp_path):
        from labcore.labdocs.lint import lint_project

        findings = lint_project(self._flat(tmp_path, 2))
        assert [f.code for f in findings if f.severity == "error"] == ["LD001"], (
            "a root-level script with no metadata block passed level 2 — lint is "
            "grading an empty set"
        )

    def test_the_same_repo_is_clean_at_level_1(self, tmp_path):
        from labcore.labdocs.lint import lint_project

        assert lint_project(self._flat(tmp_path, 1)) == [], (
            "level 1 must touch no code; a missing block cannot fail it"
        )
