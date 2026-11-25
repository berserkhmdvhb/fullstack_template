"""The `hadolint` job in ci.yml: a blocking lint of every tracked Dockerfile.

The discovery and the lint live in scripts/ci/hadolint.sh (shared with the pre-commit hook), run
with the pinned binary that scripts/tools.sh installs from mise.toml.
"""

from __future__ import annotations

from typing import Any

from tests.supply_chain._workflows import REPO, workflow


def job() -> dict[str, Any]:
    jobs = workflow("ci.yml")["jobs"]
    assert "hadolint" in jobs, f"ci.yml needs a hadolint job (got {sorted(jobs)})"
    return dict(jobs["hadolint"])


def test_hadolint_job_is_blocking_and_always_runs() -> None:
    j = job()
    assert "continue-on-error" not in j
    assert all("continue-on-error" not in s for s in j["steps"])
    assert "if" not in j
    assert "needs" not in j


def test_hadolint_job_is_behind_ci_ok() -> None:
    assert "hadolint" in workflow("ci.yml")["jobs"]["ci-ok"]["needs"]


def test_hadolint_job_is_least_privilege_and_bounded() -> None:
    j = job()
    assert j["permissions"] == {"contents": "read"}
    assert "timeout-minutes" in j
    assert j["steps"][0]["with"]["persist-credentials"] is False


def test_hadolint_job_installs_the_pinned_binary_and_runs_the_script() -> None:
    runs = "\n".join(s.get("run", "") for s in job()["steps"])
    assert "scripts/tools.sh install hadolint" in runs
    assert runs.count("bash scripts/ci/hadolint.sh") == 1


def test_hadolint_script_covers_every_tracked_dockerfile_pattern() -> None:
    text = (REPO / "scripts" / "ci" / "hadolint.sh").read_text(encoding="utf-8")
    assert text.startswith("#!/usr/bin/env bash")
    assert "'Dockerfile*'" in text
    assert "'*.Dockerfile'" in text
