"""The frontend workflow, the OpenAPI drift gate and the path filter (Task 1.2)."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.supply_chain._workflows import ACTIONS, WORKFLOWS, failure_uploads, workflow

GATES = {"eslint", "prettier", "type-check", "openapi-drift", "build", "unit", "integration", "playwright-list"}


def _jobs() -> dict[str, Any]:
    return workflow("reusable-frontend.yml")["jobs"]


def _runs(job: dict[str, Any]) -> str:
    return "\n".join(str(st.get("run", "")) for st in job["steps"])


def _needs(job: dict[str, Any]) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def test_jobs_exist_and_are_blocking() -> None:
    wf = workflow("reusable-frontend.yml")
    assert "workflow_call" in wf["on"]
    assert GATES <= set(wf["jobs"])
    for name, job in wf["jobs"].items():
        assert "continue-on-error" not in job, name
        for step in job["steps"]:
            assert "continue-on-error" not in step, (name, step.get("name"))


def test_build_runs_before_the_unit_tests() -> None:
    jobs = _jobs()
    assert {"eslint", "prettier", "type-check"} <= set(_needs(jobs["build"]))
    assert "build" in _needs(jobs["unit"])
    assert "unit" in _needs(jobs["integration"])
    assert "integration" in _needs(jobs["playwright-list"])


def test_the_jobs_call_the_npm_scripts() -> None:
    jobs = _jobs()
    expected = {
        "eslint": "npm run lint",
        "prettier": "npm run format-check",
        "type-check": "npm run typecheck",
        "build": "npm run build",
        "unit": "npm run test:unit",
        "integration": "npm run test:integration",
        "playwright-list": "npx playwright test --list",
    }
    for name, command in expected.items():
        # A whole command per line, so a shorter script name never matches a longer one.
        lines = [ln.strip() for ln in _runs(jobs[name]).splitlines()]
        assert command in lines, name
    scripts = (Path(__file__).parents[2] / "frontend" / "package.json").read_text(encoding="utf-8")
    assert '"lint": "eslint . --max-warnings=0"' in scripts


def test_eslint_keeps_zero_warnings() -> None:
    """The flag lives in the npm script, so the job must call the script rather than bare eslint."""
    text = (WORKFLOWS / "reusable-frontend.yml").read_text(encoding="utf-8")
    assert "npx eslint" not in text
    scripts = (Path(__file__).parents[2] / "frontend" / "package.json").read_text(encoding="utf-8")
    assert "--max-warnings=0" in next(ln for ln in scripts.splitlines() if '"lint":' in ln)


def test_playwright_only_lists() -> None:
    runs = _runs(_jobs()["playwright-list"])
    assert "playwright install" not in runs
    assert "--list" in runs


def test_cache_is_keyed_on_the_lockfile() -> None:
    action = (ACTIONS / "setup-frontend" / "action.yml").read_text(encoding="utf-8")
    assert "hashFiles('frontend/package-lock.json')" in action
    for name, job in _jobs().items():
        uses = [str(s.get("uses", "")) for s in job["steps"]]
        assert "./.github/actions/setup-frontend" in uses, name


def test_coverage_gate_is_not_restated() -> None:
    text = (WORKFLOWS / "reusable-frontend.yml").read_text(encoding="utf-8")
    for word in ("threshold", "--coverage.", "fail_under", "fail-under"):
        assert word not in text, word
    # The gates live in the vitest configs, so they must still be there.
    for config in ("vitest.config.ts", "vitest.integration.config.ts"):
        src = (Path(__file__).parents[2] / "frontend" / config).read_text(encoding="utf-8")
        block = re.search(r"thresholds:\s*\{(.*?)\}", src, re.DOTALL)
        assert block, config
        found = dict(re.findall(r"(\w+):\s*(\d+)", block[1]))
        assert found == {"lines": "80", "functions": "80", "statements": "80", "branches": "70"}, config


def test_drift_check_is_the_make_target_and_runs_once() -> None:
    jobs = _jobs()
    assert "make openapi-drift-check" in _runs(jobs["openapi-drift"])
    assert "if" not in jobs["openapi-drift"], "it must run when only the backend changed"
    uses = [str(s.get("uses", "")) for s in jobs["openapi-drift"]["steps"]]
    assert "./.github/actions/setup-backend" in uses
    assert "./.github/actions/setup-frontend" in uses
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        assert "backend.openapi dump" not in text, path.name


def test_drift_check_runs_on_backend_or_frontend_changes() -> None:
    job = workflow("ci.yml")["jobs"]["frontend"]
    assert job["uses"] == "./.github/workflows/reusable-frontend.yml"
    condition = job["if"]
    assert "outputs.backend == 'true'" in condition
    assert "outputs.frontend == 'true'" in condition
    assert " || " in condition
    # Everything but the drift check waits for a frontend change.
    for name, gate in _jobs().items():
        assert (name == "openapi-drift") == ("if" not in gate), name
        if "if" in gate:
            assert gate["if"] == "inputs.frontend-changed", name


# --- the path filter, executed against a throwaway repository ---------------------------------


def _filter_script() -> str:
    steps = workflow("ci.yml")["jobs"]["changes"]["steps"]
    return str(next(s for s in steps if s.get("id") == "filter")["run"])


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def _run_filter(tmp_path: Path, event: str, changed: list[str]) -> dict[str, str]:
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    for key, value in (("user.email", "t@example.org"), ("user.name", "t"), ("commit.gpgsign", "false")):
        _git(origin, "config", key, value)
    (origin / "README.md").write_text("base\n")
    _git(origin, "add", ".")
    _git(origin, "commit", "-q", "-m", "base")
    base = _git(origin, "rev-parse", "HEAD")
    for rel in changed:
        target = origin / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("changed\n")
    _git(origin, "add", ".")
    _git(origin, "commit", "-q", "--allow-empty", "-m", "head")
    head = _git(origin, "rev-parse", "HEAD")
    work = tmp_path / "work"
    _git(tmp_path, "clone", "-q", "--no-checkout", "--depth=1", f"file://{origin}", str(work))
    out = tmp_path / "output"
    out.write_text("")
    env = {
        **os.environ,
        "EVENT_NAME": event,
        "BASE_SHA": base,
        "HEAD_SHA": head,
        "GITHUB_OUTPUT": str(out),
    }
    subprocess.run(["bash", "-e", "-c", _filter_script()], cwd=work, env=env, check=True, capture_output=True)
    return dict(line.split("=", 1) for line in out.read_text().splitlines())


@pytest.mark.parametrize(
    ("path", "backend", "frontend"),
    [
        ("src/your_service_name/backend/main.py", True, False),
        ("tests/unit/test_x.py", True, False),
        ("pyproject.toml", True, False),
        ("frontend/lib/api/openapi.json", True, True),
        ("frontend/app/page.tsx", False, True),
        ("frontend/package-lock.json", False, True),
        ("Makefile", True, True),
        (".github/workflows/ci.yml", True, True),
        ("scripts/tools.sh", True, True),
        ("tools/pins.tsv", True, True),
        ("docs/readme.md", False, False),
        ("ci/old.yml", False, False),
    ],
)
def test_pull_request_paths_map_to_outputs(tmp_path: Path, path: str, backend: bool, frontend: bool) -> None:
    result = _run_filter(tmp_path, "pull_request", [path])
    assert result == {"backend": str(backend).lower(), "frontend": str(frontend).lower()}


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_main_tags_and_dispatch_force_every_output(tmp_path: Path, event: str) -> None:
    """A push covers both main and v* tags; neither is filtered."""
    assert _run_filter(tmp_path, event, ["docs/readme.md"]) == {"backend": "true", "frontend": "true"}


def test_filter_outputs_match_the_declared_outputs() -> None:
    declared = set(workflow("ci.yml")["jobs"]["changes"]["outputs"])
    assert declared == {"backend", "frontend"}


def test_integration_job_uploads_its_report_only_on_failure() -> None:
    ups = failure_uploads(_jobs()["integration"])
    assert len(ups) == 1
    assert "junit-integration.xml" in ups[0]["with"]["path"]


def test_unit_job_uploads_its_report_only_on_failure() -> None:
    ups = failure_uploads(_jobs()["unit"])
    assert len(ups) == 1
    assert "test-results/junit.xml" in ups[0]["with"]["path"]
