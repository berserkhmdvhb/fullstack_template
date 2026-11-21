"""The chart job must exercise the production guard in validate-values.yaml (a negative render)."""

from __future__ import annotations

from typing import Any

from ._workflows import workflow


def _guard_step() -> dict[str, Any]:
    steps = workflow("reusable-chart.yml")["jobs"]["chart"]["steps"]
    found: list[dict[str, Any]] = [s for s in steps if "config.ENV=dev" in s.get("run", "")]
    assert len(found) == 1, "the chart job needs exactly one negative-render step for the guard"
    return found[0]


def test_the_step_renders_a_production_release_with_a_dev_env() -> None:
    run = _guard_step()["run"]
    assert "helm template" in run
    assert "--set environment=production" in run
    assert "--set config.ENV=dev" in run


def test_a_successful_render_fails_the_step() -> None:
    run = _guard_step()["run"]
    assert "if helm template" in run, "a render that succeeds must fail the step"
    assert "exit 1" in run


def test_a_failure_without_the_guard_message_fails_the_step() -> None:
    run = _guard_step()["run"]
    assert "grep -q" in run
    assert "environment is production but config.ENV" in run


def test_the_step_has_no_expression_in_run() -> None:
    assert "${{" not in _guard_step()["run"]


def test_the_step_comes_after_the_positive_renders() -> None:
    steps = workflow("reusable-chart.yml")["jobs"]["chart"]["steps"]
    runs = [s.get("run", "") for s in steps]
    positive = next(i for i, r in enumerate(runs) if 'helm template "${SERVICE_NAME}"' in r)
    assert runs.index(_guard_step()["run"]) > positive


def test_the_job_keeps_its_permissions_and_timeout() -> None:
    job = workflow("reusable-chart.yml")["jobs"]["chart"]
    assert job["permissions"] == {"contents": "read"}
    assert job["timeout-minutes"] <= 10  # noqa: PLR2004


def test_the_unit_job_installs_the_pinned_helm_before_the_tests() -> None:
    """test_chart_production_guard.py runs in the unit job and needs the pinned helm, not PATH's."""
    steps = workflow("reusable-backend.yml")["jobs"]["unit"]["steps"]
    runs = [s.get("run", "") for s in steps]
    install = next((i for i, r in enumerate(runs) if "scripts/tools.sh install helm4" in r), None)
    assert install is not None, "the unit job must install the pinned helm (helm4)"
    tests = next(i for i, r in enumerate(runs) if "make test-unit" in r)
    assert install < tests
    assert "${{" not in runs[install]
    assert "GITHUB_PATH" not in runs[install], "the tests find .tools/bin/helm; no PATH writes"
