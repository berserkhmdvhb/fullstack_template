"""The live workflow: full stack on the runner, advisory on PRs, blocking elsewhere."""

from __future__ import annotations

import re
from typing import Any, cast

from tests.supply_chain._workflows import WORKFLOWS, all_files, failure_uploads, steps_of, workflow

PROXY_VARS = re.compile(r"\b(NO_PROXY|HTTP_PROXY|HTTPS_PROXY|ALL_PROXY)\b", re.IGNORECASE)


def _live() -> dict[str, Any]:
    return workflow("reusable-live.yml")


def _live_job() -> dict[str, Any]:
    return cast("dict[str, Any]", _live()["jobs"]["live"])


def _runs(doc: dict[str, Any]) -> str:
    return "\n".join(s.get("run", "") for s in steps_of(doc))


def test_live_is_a_call_only_workflow() -> None:
    assert set(_live()["on"]) == {"workflow_call"}


def test_advisory_on_pull_requests_blocking_elsewhere() -> None:
    job = _live_job()
    assert job["continue-on-error"] == "${{ github.event_name == 'pull_request' }}"
    warn = [s for s in job["steps"] if "::warning" in s.get("run", "")]
    assert len(warn) == 1
    assert warn[0]["if"] == "failure() && github.event_name == 'pull_request'"
    assert "live suite failed (advisory on PRs)" in warn[0]["run"]
    assert "GITHUB_STEP_SUMMARY" in warn[0]["run"]


def test_live_is_gated_on_backend_or_frontend_changes() -> None:
    job = workflow("ci.yml")["jobs"]["live"]
    assert job["uses"] == "./.github/workflows/reusable-live.yml"
    assert "changes" in job["needs"]
    cond = job["if"]
    assert "needs.changes.outputs.backend == 'true'" in cond
    assert "needs.changes.outputs.frontend == 'true'" in cond
    assert "||" in cond
    # A skipped gate (its paths did not change) must not stop the live job, but a failed or
    # cancelled one must.
    assert cond.startswith("${{ !cancelled() && ")
    assert "!contains(needs.*.result, 'failure')" in cond
    assert set(job["needs"]) == {"changes", "backend", "frontend"}
    assert "success" not in cond, "requiring success would skip the job whenever a gate is skipped"


def test_live_calls_the_make_target_and_the_npm_script() -> None:
    runs = _runs(_live())
    assert "make test-live" in runs
    assert "npm run test:e2e" in runs
    assert "pytest" not in runs
    assert "playwright test" not in runs


def test_live_starts_the_stack_through_the_entrypoint_and_always_stops_it() -> None:
    job = _live_job()
    runs = _runs(_live())
    assert "infra/runtime/entrypoint.sh" in runs
    assert "wait_for_service.py" in runs
    stop = [s for s in job["steps"] if "kill" in s.get("run", "")]
    assert stop
    assert all(s.get("if") == "always()" for s in stop)
    names = [s.get("name") for s in job["steps"]]
    assert names.index(stop[0]["name"]) > names.index("Start the stack")


def test_live_uses_no_literal_ports() -> None:
    # A port may appear only as the default of an env expansion, ${NAME_PORT:-default}.
    bare = re.sub(r"\$\{\w+:-[^}]*\}", "", _runs(_live()))
    assert not re.search(r"\b(8000|3000)\b", bare)


def test_no_self_hosted_runner_anywhere() -> None:
    for path in all_files():
        assert "self-hosted" not in path.read_text(encoding="utf-8"), path.name


def test_no_proxy_workarounds_anywhere() -> None:
    for path in all_files():
        assert not PROXY_VARS.search(path.read_text(encoding="utf-8")), path.name


def test_every_workflow_job_runs_on_the_hosted_image() -> None:
    # The per-platform jobs take their runner from the manifest's platform-to-runner map.
    from_manifest = {
        ("ci.yml", "images"),
        ("reusable-build.yml", "build"),
        ("reusable-build.yml", "image-checks"),
    }
    for path in WORKFLOWS.glob("*.yml"):
        for name, job in workflow(path.name)["jobs"].items():
            if "uses" in job:
                continue
            if (path.name, name) in from_manifest:
                assert job["runs-on"] == "${{ matrix.runner }}", f"{path.name}:{name}"
            else:
                assert job["runs-on"] == "ubuntu-24.04", f"{path.name}:{name}"


def test_live_needs_no_web_server_and_stamps_the_build_version() -> None:
    """The stack is backend + frontend (no web server to install); one version on build and test."""
    job = _live_job()
    assert not re.search(r"nginx|envsubst|gettext-base", _runs(_live()), re.IGNORECASE)
    assert job["env"]["NEXT_DEPLOYMENT_ID"] == "live-test"


def test_playwright_reports_upload_only_on_failure() -> None:
    ups = failure_uploads(_live_job())
    assert len(ups) == 1
    path = ups[0]["with"]["path"]
    assert "frontend/test-results" in path
    assert "frontend/playwright-report" in path
    # No other upload may exist: every artifact step of the live job is failure-only.
    every = [
        s
        for s in _live_job()["steps"]
        if str(s.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert every == ups


def test_the_backend_live_suite_targets_the_frontend_as_the_public_port() -> None:
    """test_full_stack.py skips without PUBLIC_PORT; the job must not leave it skipped."""
    step = next(s for s in _live_job()["steps"] if s.get("name") == "Backend live suite")
    assert 'PUBLIC_PORT="${FRONTEND_PORT:-3000}"' in step["run"]
