"""Transient network failures: the Playwright installs and the index creation retry, bounded."""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING, Any

import pytest

from tests.supply_chain._workflows import REPO, WORKFLOWS, workflow

if TYPE_CHECKING:
    from pathlib import Path

BASH: str = shutil.which("bash") or "/usr/bin/bash"
RETRY = REPO / "scripts" / "ci" / "retry.sh"
DEPS = "Install the Playwright system deps"
BROWSER = "Download the Playwright browser"
OLD = "Install the Playwright browser"
SITES = [
    ("reusable-frontend.yml", "a11y"),
    ("reusable-frontend.yml", "lighthouse"),
    ("reusable-kind.yml", "kind"),
    ("reusable-live.yml", "live"),
    ("reusable-docs-site.yml", "build"),
    ("ci.yml", "adopted"),
]
# Each install runs where that job's Playwright is installed.
WORKDIR = {
    "reusable-docs-site.yml": "docs-site",
    "ci.yml": "${{ runner.temp }}/adopted/docs-site",
}
STEP_MINUTES = 5
ATTEMPT_SECONDS = 130
KILL_AFTER_SECONDS = 10
ATTEMPTS = 2
DELAY_SECONDS = 10


def _steps(file: str, job: str) -> list[dict[str, Any]]:
    return list(workflow(file)["jobs"][job]["steps"])


@pytest.mark.parametrize(("file", "job"), SITES)
def test_each_playwright_install_is_two_bounded_retried_steps(file: str, job: str) -> None:
    steps = _steps(file, job)
    names = [s.get("name") for s in steps]
    assert OLD not in names
    assert "--with-deps" not in "\n".join(s.get("run", "") for s in steps)
    deps, browser = (steps[names.index(n)] for n in (DEPS, BROWSER))
    assert names.index(DEPS) + 1 == names.index(BROWSER)
    for step, cmd in ((deps, "install-deps chromium"), (browser, "install chromium")):
        assert step["working-directory"] == WORKDIR.get(file, "frontend")
        assert step["timeout-minutes"] == STEP_MINUTES
        assert step["run"] == (
            f'bash "$GITHUB_WORKSPACE/scripts/ci/retry.sh" {ATTEMPTS} {DELAY_SECONDS} '
            f"{ATTEMPT_SECONDS} npx playwright {cmd}"
        )
        # Every attempt, with its pause, fits the step's own budget.
        worst = ATTEMPTS * (ATTEMPT_SECONDS + KILL_AFTER_SECONDS) + (ATTEMPTS - 1) * DELAY_SECONDS
        assert worst <= STEP_MINUTES * 60


def test_the_kind_install_stays_on_the_newest_leg_only() -> None:
    for name in (DEPS, BROWSER):
        step = next(s for s in _steps("reusable-kind.yml", "kind") if s.get("name") == name)
        assert step["if"] == "matrix.leg == 'newest'"


def test_job_budgets_fit_the_install_steps() -> None:
    # Two steps at their full budget must leave room in the job (the frontend jobs allow 15 and 20).
    for file, job in SITES:
        budget = workflow(file)["jobs"][job]["timeout-minutes"]
        assert budget >= 2 * STEP_MINUTES + 5, (file, job)


def test_lighthouse_still_uses_the_playwright_chromium() -> None:
    steps = _steps("reusable-frontend.yml", "lighthouse")
    names = [s.get("name") for s in steps]
    collect = steps[names.index("Collect the Lighthouse runs")]
    assert names.index(BROWSER) < names.index("Collect the Lighthouse runs")
    assert "chromium.executablePath()" in collect["run"]


def _stub(tmp_path: Path, name: str, body: str) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / name).write_text(f"#!/usr/bin/env bash\n{body}\n")
    (bin_dir / name).chmod(0o755)
    return bin_dir


def _flaky(tmp_path: Path, name: str, *, fail_first: int) -> Path:
    """A stub that fails its first `fail_first` calls, then succeeds; it counts calls in $COUNT."""
    return _stub(
        tmp_path,
        name,
        'n=$(( $(cat "$COUNT" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$COUNT"\n'
        f'echo "stub: connection timed out" >&2\n(( n > {fail_first} )) && exit 0\nexit 7',
    )


def _run(
    tmp_path: Path, args: list[str], bin_dir: Path
) -> tuple[subprocess.CompletedProcess[str], int]:
    _stub(tmp_path, "sleep", 'echo "$1" >> "$SLEEPS"')  # no real waiting
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "COUNT": str(tmp_path / "count"),
        "SLEEPS": str(tmp_path / "sleeps"),
    }
    done = subprocess.run(args, env=env, capture_output=True, text=True, check=False, cwd=tmp_path)
    count = tmp_path / "count"
    return done, int(count.read_text()) if count.exists() else 0


@pytest.mark.parametrize(("fail_first", "calls"), [(0, 1), (1, 2)])
def test_retry_helper_succeeds_once_the_command_does(
    tmp_path: Path, fail_first: int, calls: int
) -> None:
    bin_dir = _flaky(tmp_path, "tool", fail_first=fail_first)
    done, count = _run(tmp_path, [BASH, str(RETRY), "3", "4", "60", "tool", "x"], bin_dir)
    assert done.returncode == 0, done.stderr
    assert count == calls
    if fail_first:
        assert "attempt 1/3" in done.stderr
        assert "connection timed out" in done.stderr
        assert (tmp_path / "sleeps").read_text().split() == ["4"]


def test_retry_helper_fails_after_the_last_attempt(tmp_path: Path) -> None:
    bin_dir = _flaky(tmp_path, "tool", fail_first=99)
    done, count = _run(tmp_path, [BASH, str(RETRY), "3", "4", "60", "tool"], bin_dir)
    assert done.returncode == 7  # noqa: PLR2004 - the command's own status
    assert count == 3  # noqa: PLR2004
    assert "failed after 3 attempts" in done.stderr
    assert (tmp_path / "sleeps").read_text().split() == ["4", "4"]  # none after the last


@pytest.mark.parametrize(
    "args", [["0", "1", "1"], ["x", "1", "1"], ["2", "-1", "1"], ["2", "1", ""], ["2", "1.5", "1"]]
)
def test_retry_helper_refuses_bad_numbers(tmp_path: Path, args: list[str]) -> None:
    bin_dir = _flaky(tmp_path, "tool", fail_first=0)
    done, count = _run(tmp_path, [BASH, str(RETRY), *args, "tool"], bin_dir)
    assert done.returncode == 2  # noqa: PLR2004
    assert count == 0
    assert "retry.sh:" in done.stderr


def test_retry_helper_cuts_off_a_stalled_attempt(tmp_path: Path) -> None:
    bin_dir = _stub(
        tmp_path,
        "tool",
        'n=$(( $(cat "$COUNT" 2>/dev/null || echo 0) + 1 )); echo $n > "$COUNT"\n'
        "exec /bin/sleep 30",
    )
    done, count = _run(tmp_path, [BASH, str(RETRY), "2", "0", "1", "tool"], bin_dir)
    assert done.returncode == 124  # noqa: PLR2004 - timeout's status
    assert count == 2  # noqa: PLR2004


def _index_step() -> str:
    jobs = workflow("reusable-build.yml")["jobs"]
    return str(
        next(s for s in jobs["merge"]["steps"] if s.get("name") == "Create the index and tag it")[
            "run"
        ]
    )


def _run_index(tmp_path: Path, *, fail_first: int) -> tuple[subprocess.CompletedProcess[str], int]:
    work = tmp_path / "work"
    (work / "platform").mkdir(parents=True)
    (work / "platform" / "backend.linux-amd64.digest.env").write_text(
        f"BACKEND_IMAGE_DIGEST=sha256:{'b' * 64}\n"
    )
    bin_dir = _flaky(tmp_path, "docker", fail_first=fail_first)
    _stub(tmp_path, "sleep", 'echo "$1" >> "$SLEEPS"')
    env = {
        "PATH": f"{bin_dir}:/usr/bin:/bin",
        "COUNT": str(tmp_path / "count"),
        "SLEEPS": str(tmp_path / "sleeps"),
        "IMAGE_ID": "backend",
        "REPOSITORY": "ghcr.io/o/r/backend",
        "IMAGE_REF": "ghcr.io/o/r/backend:v1",
        "IMMUTABLE": "false",
        "PRERELEASE": "false",
    }
    done = subprocess.run(
        [BASH, "-e", "-c", _index_step()],
        cwd=work,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    return done, int((tmp_path / "count").read_text())


def test_index_creation_retries_with_backoff_then_succeeds(tmp_path: Path) -> None:
    done, count = _run_index(tmp_path, fail_first=2)
    assert done.returncode == 0, done.stdout
    assert count == 3  # noqa: PLR2004
    assert "attempt 1/3" in done.stdout
    assert "attempt 2/3" in done.stdout
    assert "connection timed out" in done.stdout
    assert (tmp_path / "sleeps").read_text().split() == ["10", "30"]


def test_index_creation_fails_after_three_attempts(tmp_path: Path) -> None:
    done, count = _run_index(tmp_path, fail_first=99)
    assert done.returncode != 0
    assert count == 3  # noqa: PLR2004
    assert "failed after 3 attempts" in done.stdout


def test_index_validation_stays_outside_the_retry() -> None:
    run = _index_step()
    loop = run.index("for attempt in")
    assert run.index("no platform digests") < loop
    assert run.index('tags=(--tag "${IMAGE_REF}")') < loop
    assert run.count('docker buildx imagetools create "${tags[@]}" "${sources[@]}"') == 1


def test_the_helper_is_a_tracked_executable_script() -> None:
    assert RETRY.stat().st_mode & 0o111
    assert WORKFLOWS.is_dir()


def test_every_tool_download_retries_and_times_out() -> None:
    # Every job installs through this script; a flaky mirror must not fail the run on one try.
    lines = [
        line
        for line in (REPO / "scripts" / "tools.sh").read_text().replace("\\\n", " ").splitlines()
        if line.lstrip().startswith("curl ")
    ]
    assert lines
    for line in lines:
        for flag in ("--retry ", "--retry-connrefused", "--connect-timeout ", "--speed-limit "):
            assert flag in line, (flag, line)
