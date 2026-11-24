"""release-please.yml: the release pull request and the v* tag, made with the release App's token.

Static and offline; the notice step runs locally.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING, Any

from tests.supply_chain._workflows import REPO, WORKFLOWS, unpinned_uses, workflow

if TYPE_CHECKING:
    from pathlib import Path

FILE = "release-please.yml"
BASH: str = shutil.which("bash") or "/usr/bin/bash"
MINT_ACTION = "actions/create-github-app-token@64ab729a6294000f5aa4a4bc0b73d1f32bc45510"
RP_ACTION = "googleapis/release-please-action@45996ed1f6d02564a971a2fa1b5860e934307cf7"
APP_OUTPUT = "${{ steps.app.outputs.token }}"


def _wf() -> dict[str, Any]:
    return workflow(FILE)


def _text() -> str:
    return (WORKFLOWS / FILE).read_text(encoding="utf-8")


def test_runs_on_push_to_main_only() -> None:
    assert _wf()["on"] == {"push": {"branches": ["main"]}}
    assert "pull_request" not in _text()  # never pull_request, never pull_request_target


def test_each_job_has_the_read_only_floor_and_runs_are_never_cancelled() -> None:
    wf = _wf()
    assert wf["permissions"] == {}
    assert wf["concurrency"] == {"group": "release-please", "cancel-in-progress": False}
    assert set(wf["jobs"]) == {"notice", "release-please"}
    for job in wf["jobs"].values():
        assert job["permissions"] == {"contents": "read"}  # the App token does every API call
        assert isinstance(job["timeout-minutes"], int)


def test_the_release_job_runs_in_the_release_environment_only_when_the_app_is_configured() -> None:
    jobs = _wf()["jobs"]
    assert jobs["release-please"]["environment"] == "release"
    assert jobs["release-please"]["if"] == "vars.RELEASE_APP_ID != ''"
    # The notice is the exact complement, and touches neither the environment nor a secret.
    assert jobs["notice"]["if"] == "vars.RELEASE_APP_ID == ''"
    assert "environment" not in jobs["notice"]
    assert "secrets." not in str(jobs["notice"])


def test_without_the_app_the_run_is_a_green_notice(tmp_path: Path) -> None:
    (step,) = _wf()["jobs"]["notice"]["steps"]
    summary = tmp_path / "summary"
    done = subprocess.run(
        [BASH, "-e", "-c", step["run"]],
        env={"PATH": "/usr/bin:/bin", "GITHUB_STEP_SUMMARY": str(summary)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0
    assert done.stdout.startswith("::notice::vars.RELEASE_APP_ID is not set")
    assert "release-please was skipped" in summary.read_text(encoding="utf-8")


def test_release_please_gets_the_app_token_and_nothing_else_reads_it() -> None:
    mint, release = _wf()["jobs"]["release-please"]["steps"]
    assert mint["uses"] == MINT_ACTION
    assert mint["with"] == {
        "client-id": "${{ vars.RELEASE_APP_ID }}",
        "private-key": "${{ secrets.RELEASE_APP_PRIVATE_KEY }}",
        "permission-contents": "write",
        "permission-pull-requests": "write",
        "permission-issues": "write",
    }
    assert release["uses"] == RP_ACTION
    assert release["with"] == {
        "token": APP_OUTPUT,
        "config-file": "release-please-config.json",
        "manifest-file": ".release-please-manifest.json",
    }
    for name in release["with"].values():
        if name != APP_OUTPUT:
            assert (REPO / name).is_file(), name
    text = _text()
    assert text.count("steps.app.outputs") == 1
    assert "github.token" not in text
    assert "GITHUB_TOKEN }}" not in text
    assert "secrets.RELEASE_APP_PRIVATE_KEY" in text
    assert text.count("secrets.") == 1


def test_both_actions_are_pinned_to_the_reviewed_shas_with_a_version_renovate_reads() -> None:
    assert unpinned_uses(WORKFLOWS / FILE) == []
    uses = [line.split("uses:", 1)[1].strip() for line in _text().splitlines() if "uses:" in line]
    assert uses == [f"{MINT_ACTION} # v3.3.0", f"{RP_ACTION} # v5.0.0"]
