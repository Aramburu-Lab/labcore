"""Tests for labcore.paths."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from labcore.paths import (
    PathsError,
    env_var_for,
    load_paths,
    resolve_root,
    write_site_env,
)


def make_project(tmp_path: Path, toml_body: str) -> Path:
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "paths.toml").write_text(toml_body, encoding="utf-8")
    return tmp_path


def test_load_paths_normalises_string_and_table_entries(tmp_path):
    project = make_project(
        tmp_path,
        '[roots]\ndata = "/data/raw"\nscratch = { env = "SCRATCH_DIR" }\n',
    )
    entries = load_paths(project)
    assert entries["data"] == {"path": "/data/raw", "env": None}
    assert entries["scratch"] == {"path": None, "env": "SCRATCH_DIR"}


def test_resolve_root_prefers_toml_over_environment(tmp_path, monkeypatch):
    project = make_project(tmp_path, '[roots]\ndata = "/from/toml"\n')
    monkeypatch.setenv(env_var_for("data"), "/from/env")
    assert resolve_root("data", root=project) == Path("/from/toml")


def test_resolve_root_falls_back_to_named_env_var(tmp_path, monkeypatch):
    project = make_project(tmp_path, '[roots]\nscratch = { env = "SCRATCH_DIR" }\n')
    monkeypatch.setenv("SCRATCH_DIR", "/node/local")
    assert resolve_root("scratch", root=project) == Path("/node/local")


def test_resolve_root_raises_when_neither_toml_nor_env_supplies_it(tmp_path, monkeypatch):
    project = make_project(tmp_path, '[roots]\nscratch = { env = "SCRATCH_DIR" }\n')
    monkeypatch.delenv("SCRATCH_DIR", raising=False)
    with pytest.raises(PathsError) as excinfo:
        resolve_root("scratch", root=project)
    message = str(excinfo.value)
    assert "SCRATCH_DIR" in message
    assert "roots.scratch.path" in message


def test_resolve_root_never_guesses_a_default_for_unknown_name(tmp_path, monkeypatch):
    project = make_project(tmp_path, '[roots]\ndata = "/data/raw"\n')
    monkeypatch.delenv(env_var_for("nobackup"), raising=False)
    with pytest.raises(PathsError) as excinfo:
        resolve_root("nobackup", root=project)
    assert env_var_for("nobackup") in str(excinfo.value)


def test_resolve_root_raises_on_unset_interpolated_variable(tmp_path, monkeypatch):
    project = make_project(tmp_path, '[roots]\nwork = "$SNIC_NOBACKUP/work"\n')
    monkeypatch.delenv("SNIC_NOBACKUP", raising=False)
    with pytest.raises(PathsError, match=r"\$SNIC_NOBACKUP"):
        resolve_root("work", root=project)


def test_missing_settings_file_raises(tmp_path):
    with pytest.raises(PathsError, match="paths.toml"):
        load_paths(tmp_path)


def test_write_site_env_output_is_sourceable_bash(tmp_path):
    dest = tmp_path / "generated" / "site.env"
    write_site_env({"data": "/data/raw", "work": Path("/proj/with space/work")}, dest)

    text = dest.read_text(encoding="utf-8")
    assert "GENERATED" in text
    assert "DO NOT HAND-EDIT" in text

    var = env_var_for("work")
    result = subprocess.run(
        ["bash", "-c", f'set -euo pipefail; source "{dest}"; echo "${var}"'],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "/proj/with space/work"


def test_write_site_env_resolves_entry_dicts(tmp_path, monkeypatch):
    monkeypatch.setenv("SCRATCH_DIR", "/node/local")
    dest = tmp_path / "site.env"
    write_site_env({"scratch": {"path": None, "env": "SCRATCH_DIR"}}, dest)
    assert f"export {env_var_for('scratch')}=/node/local" in dest.read_text(encoding="utf-8")


def test_write_site_env_fails_on_unresolvable_root(tmp_path, monkeypatch):
    monkeypatch.delenv("SCRATCH_DIR", raising=False)
    with pytest.raises(PathsError):
        write_site_env({"scratch": {"path": None, "env": "SCRATCH_DIR"}}, tmp_path / "site.env")
