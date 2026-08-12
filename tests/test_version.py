"""The declared version must not fall behind the newest shipped tag.

`labcore.__version__` sat at 0.2.0 while v0.3.3 was tagged and pushed, so anyone
pinning v0.3.3 imported the package and was told 0.2.0. Four releases bumped the
tag without bumping the version and nothing noticed, which is the same
hand-maintained-duplicate drift the lint-contract test guards in the template.

Being *ahead* of the newest tag is fine and normal: that is an unreleased bump.
Being *behind* one is the bug.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

import labcore

REPO = Path(__file__).resolve().parents[1]


def _parts(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.lstrip("v").split(".") if x.isdigit())


def _newest_tag() -> str | None:
    """Highest semver tag in this repo, or None outside a git checkout."""
    out = subprocess.run(
        ["git", "-C", str(REPO), "tag", "-l", "v*"],
        capture_output=True, text=True, check=False,
    )
    tags = [t for t in out.stdout.split() if _parts(t)]
    return max(tags, key=_parts) if tags else None


def test_pyproject_and_package_agree():
    """One version, declared twice — they must not disagree."""
    declared = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    assert declared == labcore.__version__, (
        f"pyproject says {declared}, labcore.__version__ says {labcore.__version__}"
    )


def test_version_is_not_behind_the_newest_tag():
    """A released tag whose package reports an older version is a lie to consumers."""
    tag = _newest_tag()
    if tag is None:
        pytest.skip("no git tags; nothing has been released from this checkout")
    assert _parts(labcore.__version__) >= _parts(tag), (
        f"__version__ is {labcore.__version__} but {tag} is already tagged — anyone "
        f"pinning {tag} imports a package that reports the older number"
    )
