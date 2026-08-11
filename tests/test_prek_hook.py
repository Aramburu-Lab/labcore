"""Prove the prek hook actually blocks a bad commit.

ADR-10 calls enforcement "the whole point" of the docs engine. Every other test
here checks that `labdocs lint` *returns* a finding; none of them prove that a
human typing `git commit` is stopped. An unproven hook is not enforcement — it is
a config file that everyone assumes works.

So this drives the real thing: a real git repo, a real `prek install`, a real
`git commit`, asserting a non-zero exit and no new commit.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "demo_project"
CONFIG = Path(__file__).parent.parent / ".pre-commit-config.yaml"

HOOKS_ONLY = """\
repos:
  - repo: local
    hooks:
      - id: labdocs-lint
        name: labdocs lint
        entry: labdocs lint
        language: system
        pass_filenames: false
"""


def _run(cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=300, check=False
    )


def _git_env() -> dict[str, str]:
    """Environment with the venv on PATH and git identity set.

    Returns:
        A copy of os.environ suitable for driving git and prek in a temp repo.
    """
    env = dict(os.environ)
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}"
    env.update(
        GIT_AUTHOR_NAME="test",
        GIT_AUTHOR_EMAIL="test@example.com",
        GIT_COMMITTER_NAME="test",
        GIT_COMMITTER_EMAIL="test@example.com",
        PREK_HOME=str(Path(env.get("TMPDIR", "/tmp")) / "prek-home"),
    )
    return env


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real git repo containing the demo project with the hook installed."""
    if shutil.which("prek") is None:
        pytest.skip("prek not installed")
    if not FIXTURE.is_dir():
        pytest.skip(f"fixture missing: {FIXTURE}")

    root = tmp_path / "proj"
    shutil.copytree(FIXTURE, root)
    (root / ".pre-commit-config.yaml").write_text(HOOKS_ONLY, encoding="utf-8")

    env = _git_env()
    _run(["git", "init", "-q", "-b", "main"], root, env)
    _run(["git", "add", "-A"], root, env)
    baseline = _run(["git", "commit", "-qm", "initial"], root, env)
    assert baseline.returncode == 0, f"baseline commit failed: {baseline.stderr}"

    installed = _run(["prek", "install"], root, env)
    assert installed.returncode == 0, f"prek install failed: {installed.stderr}"
    assert (root / ".git" / "hooks" / "pre-commit").exists(), "prek did not write the hook"
    return root


def _head(root: Path, env: dict[str, str]) -> str:
    return _run(["git", "rev-parse", "HEAD"], root, env).stdout.strip()


def test_clean_commit_is_allowed(repo: Path) -> None:
    """The hook must not block a commit that violates nothing."""
    env = _git_env()
    before = _head(repo, env)
    (repo / "knowledge" / "overview.md").write_text("touched\n", encoding="utf-8")
    _run(["git", "add", "-A"], repo, env)
    result = _run(["git", "commit", "-m", "clean change"], repo, env)

    assert result.returncode == 0, (
        f"hook rejected a clean commit.\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert _head(repo, env) != before, "commit reported success but HEAD did not move"


def test_metadata_violation_is_rejected(repo: Path) -> None:
    """Staging an output with no desc: must stop the commit."""
    env = _git_env()
    before = _head(repo, env)

    target = repo / "scripts" / "python" / "qc_filter.py"
    text = target.read_text(encoding="utf-8")
    lines = [ln for ln in text.splitlines() if not ln.strip().startswith("#     desc:")]
    assert len(lines) < len(text.splitlines()), "fixture changed: no '#     desc:' line to strip"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")

    _run(["git", "add", "-A"], repo, env)
    result = _run(["git", "commit", "-m", "remove an output description"], repo, env)

    assert result.returncode != 0, (
        "THE HOOK DID NOT FIRE — a metadata violation was committed.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert _head(repo, env) == before, "hook exited non-zero but the commit still landed"


def test_missing_metadata_block_is_rejected(repo: Path) -> None:
    """A new script with no codebase-meta block must stop the commit."""
    env = _git_env()
    before = _head(repo, env)

    (repo / "scripts" / "python" / "undocumented.py").write_text(
        '"""A new step nobody documented."""\n\nprint("hello")\n', encoding="utf-8"
    )
    _run(["git", "add", "-A"], repo, env)
    result = _run(["git", "commit", "-m", "add an undocumented script"], repo, env)

    assert result.returncode != 0, (
        "THE HOOK DID NOT FIRE — an undocumented script was committed.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert _head(repo, env) == before, "hook exited non-zero but the commit still landed"
