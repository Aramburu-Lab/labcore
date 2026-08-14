"""Which files labdocs is willing to call a script.

Found by migrating a real repo: the FragPipe launcher had 1,364 lines across seven scripts that
labdocs could not see, so it would have certified as "level 2 — documented" while documenting none
of its own logic. Three separate causes, one test each.

Across ~/Scripts at the time: 328 `.sbatch` files in nine repos, and `bin/select_account` alone
appeared extensionless in five. This was the lab's normal way of writing HPC code, not one repo's
quirk.
"""

from __future__ import annotations

from pathlib import Path

from labcore.labdocs.walk import project_scripts


def _seen(root: Path) -> set[str]:
    return {str(Path(p).resolve().relative_to(root.resolve())) for p in project_scripts(root)}


def test_sbatch_files_are_scripts(tmp_path: Path) -> None:
    """Slurm batch scripts are shell scripts with a different extension."""
    (tmp_path / "run_thing.sbatch").write_text("#!/usr/bin/env bash\necho hi\n", encoding="utf-8")
    assert "run_thing.sbatch" in _seen(tmp_path)


def test_extensionless_entry_point_with_a_shebang_is_a_script(tmp_path: Path) -> None:
    """The lab's entry points are conventionally extensionless — `fragpipe`, `select_account`."""
    (tmp_path / "fragpipe").write_text("#!/usr/bin/env bash\necho hi\n", encoding="utf-8")
    assert "fragpipe" in _seen(tmp_path)


def test_extensionless_file_without_a_shebang_is_not(tmp_path: Path) -> None:
    """The shebang is the whole signal. A LICENSE or a README must not become a lint target."""
    (tmp_path / "LICENSE").write_text("MIT\n", encoding="utf-8")
    assert "LICENSE" not in _seen(tmp_path)


def test_a_binary_with_no_extension_does_not_raise(tmp_path: Path) -> None:
    """Shebang detection reads bytes, so an undecodable file is rejected rather than fatal."""
    (tmp_path / "blob").write_bytes(b"\x00\x01\x02\xff\xfe")
    assert "blob" not in _seen(tmp_path)


def test_containers_directory_is_walked(tmp_path: Path) -> None:
    """Container build scripts are as load-bearing as anything under scripts/."""
    (tmp_path / "containers").mkdir()
    (tmp_path / "containers" / "build_image.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    assert "containers/build_image.sh" in _seen(tmp_path)


def test_config_at_the_root_is_still_not_a_script(tmp_path: Path) -> None:
    """The pre-existing rule the widening must not break: pixi.toml is configuration."""
    (tmp_path / "pixi.toml").write_text("[workspace]\n", encoding="utf-8")
    assert "pixi.toml" not in _seen(tmp_path)


def test_adopt_and_the_linter_agree_on_what_a_script_is(tmp_path: Path) -> None:
    """The two discovery paths must not drift.

    `labdocs lint` uses walk.project_scripts; `labdocs adopt` used its own ADOPT_SUFFIXES and its
    own directory walk. When the walker learned about .sbatch and extensionless entry points and
    adopt did not, lint demanded a block on four files adopt would not draft one for — a repo that
    could not reach level 2 by following its own tooling.
    """
    from labcore.labdocs.adopt import iter_candidates

    for name, body in {
        "fragpipe": "#!/usr/bin/env bash\necho hi\n",
        "run_thing.sbatch": "#!/usr/bin/env bash\necho hi\n",
        "plain.sh": "#!/bin/bash\n",
        "NOTES": "not a script\n",
    }.items():
        (tmp_path / name).write_text(body, encoding="utf-8")

    linted = _seen(tmp_path)
    root = tmp_path.resolve()
    drafted = {str(Path(p).resolve().relative_to(root)) for p in iter_candidates(tmp_path)}
    assert linted == drafted, f"lint sees {sorted(linted)}, adopt sees {sorted(drafted)}"


def test_adopt_actually_drafts_an_extensionless_entry_point(tmp_path: Path) -> None:
    """Discovery parity is not enough — the draft has to succeed.

    The first fix made adopt *find* `fragpipe`, and it still failed with "no inspector for ''"
    because the same suffix rule lived in a third place (adopt_inspect.inspect_source). Asserting
    only that the two walks agree let that through, so this asserts a usable draft comes out.
    """
    from labcore.labdocs.adopt import inspect_script

    script = tmp_path / "fragpipe"
    body = "#!/usr/bin/env bash\n# Launch the thing.\nout=results/x.tsv\n"
    script.write_text(body, encoding="utf-8")
    draft = inspect_script(script)
    assert draft is not None
    assert draft.name == "fragpipe"


def test_project_name_survives_being_cloned_elsewhere(tmp_path: Path) -> None:
    """The manifest must not depend on the checkout directory.

    GitHub checks a repo out under its repo name. When that differs from the local directory —
    command_line_launcher locally, fragpipe-launcher on the runner — a directory-derived name made
    the runner render a different manifest, and `labdocs render --check` reported permanent
    staleness. fragpipe-launcher's CI failed on every push for this reason alone.
    """
    from labcore.labdocs.render import project_name

    here = tmp_path / "local_dir_name"
    here.mkdir()
    (here / ".copier-answers.yml").write_text("project_slug: stable_slug\n", encoding="utf-8")
    elsewhere = tmp_path / "totally_different_checkout"
    elsewhere.mkdir()
    (elsewhere / ".copier-answers.yml").write_text("project_slug: stable_slug\n", encoding="utf-8")

    assert project_name(here) == project_name(elsewhere) == "stable_slug"


def test_project_name_is_not_empty_for_a_relative_root(tmp_path: Path) -> None:
    """`Path('.').name` is the empty string, so `--root .` used to write project: ''."""
    import os

    from labcore.labdocs.render import project_name

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert project_name(Path(".")) == tmp_path.resolve().name
    finally:
        os.chdir(cwd)


def test_gitignored_scripts_are_not_linted(tmp_path: Path) -> None:
    """A file git ignores is not part of the codebase.

    Linting it makes local and CI disagree with no way to reconcile: CI clones the repo and
    never sees the file. On Riboseq, a deliberately archived `bin/archive/` that had never been
    committed contributed 31 of the 139 scripts the linter demanded metadata for.
    """
    import subprocess

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("archive/\n", encoding="utf-8")
    (tmp_path / "archive").mkdir()
    (tmp_path / "archive" / "old_thing.py").write_text("print(1)\n", encoding="utf-8")
    (tmp_path / "live_thing.py").write_text("print(1)\n", encoding="utf-8")

    seen = _seen(tmp_path)
    assert "live_thing.py" in seen
    assert "archive/old_thing.py" not in seen, "a gitignored script must not be linted"


def test_a_non_git_directory_is_still_graded_in_full(tmp_path: Path) -> None:
    """The filter must not silently disable linting for an un-adopted tree."""
    (tmp_path / "thing.py").write_text("print(1)\n", encoding="utf-8")
    assert "thing.py" in _seen(tmp_path)
