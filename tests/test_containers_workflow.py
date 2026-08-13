"""containers.yml must commit digests.lock back, not just upload it as an artifact.

v0.5.0, v0.5.1 and v0.5.2 all shipped with the placeholder digests.lock still
committed: the workflow generated the real file and handed it to
actions/upload-artifact, which expires after 90 days. D-004's premise is that a
manuscript cites digest-pinned references from the repo, so the file has to be in
the repo. This is a shape check on the workflow, not a run of it.
"""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/containers.yml"


def _lock_job() -> dict:
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    writers = [j for j in jobs.values() if "containers/digests.lock" in yaml.dump(j)]
    assert len(writers) == 1, "exactly one job should own containers/digests.lock"
    return writers[0]


def _live_lines(job: dict) -> str:
    """The job's YAML with comment-only lines dropped.

    `"git push" in yaml.dump(job)` also matches `# git push ...`, so the test passes on a workflow
    whose push has been commented out — which is how such a line actually dies: someone disables it
    while debugging and never puts it back. Verified by mutation; the naive version did not fail.
    """
    return "\n".join(
        line for line in yaml.dump(job).splitlines() if not line.strip().lstrip("-").strip().startswith("#")
    )


def test_the_lockfile_is_committed_not_just_uploaded() -> None:
    job = _lock_job()
    script = _live_lines(job)
    assert "git push" in script, "digests.lock is generated but never pushed (the v0.5.x bug)"
    assert "upload-artifact" not in yaml.dump(job), "an artifact expires; the commit is the deliverable"
    assert job.get("permissions", {}).get("contents") == "write", "the push needs contents: write"


def test_the_commit_lands_on_the_default_branch_not_the_tag() -> None:
    # push:tags means HEAD is detached at the tag; a commit made there is unreachable.
    checkout = next(s for s in _lock_job()["steps"] if "checkout" in str(s.get("uses", "")))
    assert checkout.get("with", {}).get("ref") == "${{ github.event.repository.default_branch }}"
