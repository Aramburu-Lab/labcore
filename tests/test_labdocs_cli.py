from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from labcore.labdocs.api import build_api_index, extract_functions, write_api_index
from labcore.labdocs.audit import audit_repos, render_audit
from labcore.labdocs.cli import main

FIXTURES = Path(__file__).parent / "fixtures"
DEMO = FIXTURES / "demo_project"
BROKEN = FIXTURES / "broken"


def _needs(module: str, *names: str):
    """Skip until the sibling labdocs module lands with one of these entry points."""
    pytest.importorskip(f"labcore.labdocs.{module}")
    from importlib import import_module

    mod = import_module(f"labcore.labdocs.{module}")
    if not any(callable(getattr(mod, n, None)) for n in names):
        pytest.skip(f"{module}.py exposes none of {names}")


def test_schema_print_emits_valid_json(capsys):
    assert main(["schema", "--print"]) == 0
    assert "codebase-meta" in json.loads(capsys.readouterr().out)["$id"]


def test_schema_without_print_is_a_usage_error():
    assert main(["schema"]) == 2


def test_unknown_subcommand_is_a_usage_error():
    assert main(["nonesuch"]) == 2


def test_lint_clean_project_exits_zero():
    _needs("lint", "lint_project", "lint_tree", "lint")
    assert main(["lint", "--root", str(DEMO)]) == 0


def test_lint_broken_fixtures_exit_one(tmp_path, capsys):
    _needs("lint", "lint_project", "lint_tree", "lint")
    # The linter walks <root>/scripts/, so the fixtures are mounted as one.
    root = tmp_path / "broken_project"
    shutil.copytree(BROKEN, root / "scripts" / "python")

    assert main(["lint", "--naming", "--root", str(root)]) == 1
    printed = capsys.readouterr().out.strip().splitlines()
    assert printed == sorted(printed)
    codes = {line.split(": ")[1].split()[0] for line in printed}
    assert {"LD001", "LD002", "LD003", "LD004", "LD005", "LD006"} <= codes


def test_render_then_check_is_clean(tmp_path):
    _needs("render", "render", "render_all")
    root = tmp_path / "demo"
    shutil.copytree(DEMO, root)
    assert main(["render", "--root", str(root)]) == 0
    assert main(["render", "--check", "--root", str(root)]) == 0


def test_api_writes_index_with_a_known_fixture_row(tmp_path):
    root = tmp_path / "demo"
    shutil.copytree(DEMO, root)
    assert main(["api", "--root", str(root)]) == 0
    text = (root / "knowledge" / "api_index.md").read_text(encoding="utf-8")
    assert "### scripts/python/qc_filter.py" in text
    assert "| Function | Signature | Summary |" in text
    assert (
        "| `mad_threshold` | `(x: pl.Series, n: int = 5) -> tuple[float, float]` | "
        "Lower/upper MAD cutoffs for a metric. |"
    ) in text


def test_api_index_covers_labcore_and_every_language():
    text = build_api_index(DEMO)
    assert "### labcore/meta.py" in text
    assert "| `ensure_parent` |" in text
    assert "| `read_expression` | `(path)` | Widen the long normalised" in text


def test_bash_functions_are_found_without_a_marker():
    entries = extract_functions(DEMO / "scripts" / "bash" / "fetch.sh")
    assert [e.name for e in entries] == ["download_one"]


def test_private_python_helpers_are_excluded():
    names = [e.name for e in extract_functions(DEMO / "scripts" / "python" / "helpers.py")]
    assert names == ["ensure_parent", "tag_values"]


def test_write_api_index_returns_its_path(tmp_path):
    root = tmp_path / "demo"
    shutil.copytree(DEMO, root)
    assert write_api_index(root) == root / "knowledge" / "api_index.md"


def test_audit_reports_frozen_repos_rather_than_omitting_them(tmp_path):
    frozen = tmp_path / "frozen_repo"
    frozen.mkdir()
    (frozen / ".copier-answers.yml").write_text("_commit: v0.3.0\n", encoding="utf-8")
    (frozen / ".labtemplate.yml").write_text("conformance: 2\nfrozen: true\n", encoding="utf-8")

    rows = audit_repos([tmp_path])
    assert [r.path for r in rows] == [frozen]
    assert rows[0].frozen is True
    assert rows[0].template_version == "v0.3.0"
    assert "| yes |" in render_audit(rows)


def test_audit_finds_projects_at_depth_two_and_counts_drafts(tmp_path):
    nested = tmp_path / "lab" / "phase1"
    (nested / "scripts").mkdir(parents=True)
    (nested / ".copier-answers.yml").write_text("_commit: v1.0.0\n", encoding="utf-8")
    (nested / "scripts" / "step.py").write_text(
        "# /// codebase-meta\n# name: step\n# draft: true\n# ///\n", encoding="utf-8"
    )
    rows = audit_repos([tmp_path])
    assert [(r.path, r.drafts, r.conformance) for r in rows] == [(nested, 1, None)]


def test_audit_cli_on_empty_tree(tmp_path, capsys):
    assert main(["audit", str(tmp_path)]) == 0
    assert "No projects" in capsys.readouterr().out
