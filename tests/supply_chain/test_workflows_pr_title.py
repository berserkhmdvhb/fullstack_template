"""The pull request title check (Conventional Commits). Static, offline; the step runs locally."""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING, Any

import pytest

from tests.supply_chain._workflows import WORKFLOWS, unpinned_uses, workflow

if TYPE_CHECKING:
    from pathlib import Path

BASH: str = shutil.which("bash") or "/usr/bin/bash"


def _wf() -> dict[str, Any]:
    return workflow("pr-title.yml")


def test_runs_on_pull_request_title_and_label_events_never_pull_request_target() -> None:
    on = _wf()["on"]
    assert set(on) == {"pull_request"}
    assert set(on["pull_request"]["types"]) == {
        "opened", "edited", "synchronize", "reopened", "labeled", "unlabeled",
    }  # fmt: skip


def test_one_read_only_job_with_no_secret_no_checkout_and_no_action() -> None:
    assert _wf()["permissions"] == {}
    (job,) = _wf()["jobs"].values()
    assert job["permissions"] == {"contents": "read"}
    assert isinstance(job["timeout-minutes"], int)
    text = (WORKFLOWS / "pr-title.yml").read_text(encoding="utf-8")
    assert "secrets." not in text
    assert "github.token" not in text
    assert not [s for s in job["steps"] if "uses" in s]
    assert unpinned_uses(WORKFLOWS / "pr-title.yml") == []


def test_the_title_and_the_label_reach_the_shell_through_env_only() -> None:
    (step,) = next(iter(_wf()["jobs"].values()))["steps"]
    assert step["env"] == {
        "TITLE": "${{ github.event.pull_request.title }}",
        "BREAKING": "${{ contains(github.event.pull_request.labels.*.name, 'breaking-change') }}",
    }
    assert "${{" not in step["run"]


def _check(title: str, tmp_path: Path, *, breaking: bool = False) -> int:
    (step,) = next(iter(_wf()["jobs"].values()))["steps"]
    env = {
        "PATH": "/usr/bin:/bin",
        "TITLE": title,
        "BREAKING": str(breaking).lower(),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "s"),
    }
    return subprocess.run(
        [BASH, "-e", "-c", step["run"]], env=env, capture_output=True, check=False
    ).returncode


@pytest.mark.parametrize(
    "title",
    [
        "feat: add items",
        "fix(preview): job-level concurrency",
        "feat(chart,bootstrap)!: a preview namespace",
        "docs,build: two types",  # not one type
        "Fix: capitalised type",
        "feat:no space",
        "feat(): empty scope",
        "release: unknown type",
        "fix(preview) missing colon",
    ],
)
def test_the_check_accepts_conventional_titles_and_refuses_the_rest(
    title: str, tmp_path: Path
) -> None:
    good = {
        "feat: add items",
        "fix(preview): job-level concurrency",
        "feat(chart,bootstrap)!: a preview namespace",
    }
    assert (_check(title, tmp_path) == 0) is (title in good), title


def test_a_title_cannot_inject_shell(tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    assert _check(f"fix: $(touch {marker})", tmp_path) == 0
    assert not marker.exists()


@pytest.mark.parametrize(
    ("title", "code"),
    [
        ("feat!: drop the v1 items route", 0),
        ("feat(api)!: drop the v1 items route", 0),
        ("feat: drop the v1 items route", 1),
        ("feat(api): drop the v1 items route", 1),
        ("fix(api): x!: y", 1),  # the marker belongs after the type or scope
    ],
)
def test_the_breaking_change_label_requires_the_breaking_marker(
    title: str, code: int, tmp_path: Path
) -> None:
    assert _check(title, tmp_path, breaking=True) == code, title


def test_without_the_label_the_marker_stays_optional(tmp_path: Path) -> None:
    assert _check("feat(api): add a field", tmp_path) == 0
    assert _check("feat(api)!: drop a field", tmp_path) == 0


@pytest.mark.parametrize(
    "title",
    [
        "chore(main): release 3.1.0",  # release-please's default title for this config
        "fix(deps): bump urllib3 from 2.5.0 to 2.6.0",  # Dependabot, prefix fix(deps)
        "fix(deps): bump the npm_and_yarn group across 2 directories with 3 updates",
        "fix(deps): update dependency fastapi to v0.130.0",  # Renovate, semantic commits
        "fix(deps): lock file maintenance",
        "fix(deps): update actions/checkout action to v7.0.2",
    ],
)
def test_the_bots_titles_pass_so_the_required_check_never_blocks_them(
    title: str, tmp_path: Path
) -> None:
    assert _check(title, tmp_path) == 0, title
