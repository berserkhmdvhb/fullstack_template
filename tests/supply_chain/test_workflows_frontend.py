"""The frontend workflow, the OpenAPI drift gate and the path filter."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from tests.supply_chain._workflows import ACTIONS, WORKFLOWS, failure_uploads, workflow

GATES = {
    "eslint",
    "prettier",
    "type-check",
    "openapi-drift",
    "build",
    "unit",
    "integration",
    "playwright-list",
    "a11y",
}
ADVISORY = {"lighthouse"}


def _jobs() -> dict[str, Any]:
    return cast("dict[str, Any]", workflow("reusable-frontend.yml")["jobs"])


def _runs(job: dict[str, Any]) -> str:
    return "\n".join(str(st.get("run", "")) for st in job["steps"])


def _needs(job: dict[str, Any]) -> list[str]:
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def test_jobs_exist_and_are_blocking() -> None:
    wf = workflow("reusable-frontend.yml")
    assert "workflow_call" in wf["on"]
    assert set(wf["jobs"]) >= GATES
    for name, job in wf["jobs"].items():
        if name in ADVISORY:
            continue
        assert "continue-on-error" not in job, name
        for step in job["steps"]:
            assert "continue-on-error" not in step, (name, step.get("name"))


def test_build_runs_before_the_unit_tests() -> None:
    jobs = _jobs()
    assert {"eslint", "prettier", "type-check"} <= set(_needs(jobs["build"]))
    assert "build" in _needs(jobs["unit"])
    assert "unit" in _needs(jobs["integration"])
    assert "integration" in _needs(jobs["playwright-list"])


def test_the_jobs_call_the_pnpm_scripts() -> None:
    jobs = _jobs()
    expected = {
        "eslint": "pnpm run lint",
        "prettier": "pnpm run format-check",
        "type-check": "pnpm run typecheck",
        "build": "pnpm run build",
        "unit": "pnpm run test:unit",
        "integration": "pnpm run test:coverage",
        "playwright-list": "pnpm exec playwright test --list",
    }
    for name, command in expected.items():
        # A whole command per line, so a shorter script name never matches a longer one.
        lines = [ln.strip() for ln in _runs(jobs[name]).splitlines()]
        assert command in lines, name
    scripts = (Path(__file__).parents[2] / "frontend" / "package.json").read_text(encoding="utf-8")
    assert '"lint": "eslint . --max-warnings=0"' in scripts


def test_eslint_keeps_zero_warnings() -> None:
    """The flag lives in the package.json script, so the job calls the script, not bare eslint."""
    text = (WORKFLOWS / "reusable-frontend.yml").read_text(encoding="utf-8")
    assert "pnpm exec eslint" not in text
    scripts = (Path(__file__).parents[2] / "frontend" / "package.json").read_text(encoding="utf-8")
    assert "--max-warnings=0" in next(ln for ln in scripts.splitlines() if '"lint":' in ln)


def test_playwright_only_lists() -> None:
    runs = _runs(_jobs()["playwright-list"])
    assert "playwright install" not in runs
    assert "--list" in runs


def test_cache_is_keyed_on_the_lockfile() -> None:
    action = (ACTIONS / "setup-frontend" / "action.yml").read_text(encoding="utf-8")
    assert "hashFiles('frontend/pnpm-lock.yaml')" in action
    for name, job in _jobs().items():
        uses = [str(s.get("uses", "")) for s in job["steps"]]
        assert "./.github/actions/setup-frontend" in uses, name


def test_coverage_gate_is_not_restated() -> None:
    text = (WORKFLOWS / "reusable-frontend.yml").read_text(encoding="utf-8")
    for word in ("threshold", "--coverage.", "fail_under", "fail-under"):
        assert word not in text, word
    # The gates live in the vitest configs, so they must still be there.
    frontend = Path(__file__).parents[2] / "frontend"
    src = (frontend / "vitest.config.ts").read_text(encoding="utf-8")
    block = re.search(r"thresholds:\s*\{(.*?)\}", src, re.DOTALL)
    assert block
    found = dict(re.findall(r"(\w+):\s*(\d+)", block[1]))
    assert found == {"lines": "80", "functions": "80", "statements": "80", "branches": "70"}
    # One measurement: no second threshold block, and no test file hidden from it.
    for other in ("vitest.integration.config.ts", "vitest.unit.config.ts"):
        text = (frontend / other).read_text(encoding="utf-8")
        assert "thresholds:" not in text, other
        assert "coverage:" not in text, other
    assert re.search(r"exclude:\s*\[[^\]]*\.test\.ts", src) is None, "the whole suite must run"


def test_the_coverage_run_is_blocking_and_covers_the_whole_suite() -> None:
    """The integration job runs `pnpm run test:coverage` over every test file, with no `|| true`."""
    job = _jobs()["integration"]
    runs = _runs(job)
    assert "pnpm run test:coverage" in runs
    assert "|| true" not in runs
    assert "continue-on-error" not in job
    scripts = (Path(__file__).parents[2] / "frontend" / "package.json").read_text(encoding="utf-8")
    line = next(ln for ln in scripts.splitlines() if '"test:coverage":' in ln)
    assert "--coverage" in line
    assert "--config" not in line, "it must use the default config, which runs every test"
    assert "@vitest/coverage-v8" in scripts
    assert "integration" in _needs(_jobs()["playwright-list"])


def test_drift_check_is_the_make_target_and_runs_once() -> None:
    jobs = _jobs()
    assert "make openapi-drift-check" in _runs(jobs["openapi-drift"])
    assert "if" not in jobs["openapi-drift"], "it must run when only the backend changed"
    uses = [str(s.get("uses", "")) for s in jobs["openapi-drift"]["steps"]]
    assert "./.github/actions/setup-backend" in uses
    assert "./.github/actions/setup-frontend" in uses
    # The install-scripts guard runs on a frontend-only change too (the backend filter skips it).
    assert "tests/supply_chain/test_pnpm_config.py" in _runs(jobs["openapi-drift"])
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


GIT: str = shutil.which("git") or "/usr/bin/git"
BASH: str = shutil.which("bash") or "/usr/bin/bash"


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run([GIT, *args], cwd=cwd, check=True, capture_output=True, text=True)
    return done.stdout.strip()


def _run_filter(
    tmp_path: Path, event: str, changed: list[str], removed: tuple[str, ...] = ()
) -> dict[str, str]:
    """The filter's outputs for a head that changes `changed` and deletes `removed`."""
    origin = tmp_path / "origin"
    origin.mkdir()
    _git(origin, "init", "-q", "-b", "main")
    for key, value in (
        ("user.email", "t@example.org"),
        ("user.name", "t"),
        ("commit.gpgsign", "false"),
    ):
        _git(origin, "config", key, value)
    for rel in ("README.md", *removed):
        (origin / rel).parent.mkdir(parents=True, exist_ok=True)
        (origin / rel).write_text("base\n")
    _git(origin, "add", ".")
    _git(origin, "commit", "-q", "-m", "base")
    base = _git(origin, "rev-parse", "HEAD")
    for rel in removed:
        (origin / rel).unlink()
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
    subprocess.run(
        [BASH, "-e", "-c", _filter_script()], cwd=work, env=env, check=True, capture_output=True
    )
    return dict(line.split("=", 1) for line in out.read_text().splitlines())


@pytest.mark.parametrize(
    ("path", "backend", "frontend"),
    [
        ("src/your_service_name/backend/main.py", True, False),
        ("tests/unit/test_x.py", True, False),
        ("pyproject.toml", True, False),
        ("frontend/lib/api/openapi.json", True, True),
        ("frontend/app/page.tsx", False, True),
        ("frontend/pnpm-lock.yaml", True, True),
        ("frontend/pnpm-workspace.yaml", True, True),
        ("docs-site/pnpm-workspace.yaml", True, False),
        ("docs-site/pnpm-lock.yaml", True, False),
        ("frontend/package.json", True, True),
        (".devcontainer/devcontainer.json", True, False),
        ("renovate.json5", True, False),
        (".pr_agent.toml", True, False),
        ("lychee.toml", True, False),
        ("release-please-config.json", True, False),
        (".release-please-manifest.json", True, False),
        ("docker-compose.yml", True, False),
        ("README.md", True, False),
        (".env.example", True, False),
        ("charts/your-service-name/templates/pdb.yaml", True, False),
        ("agent/skills/quality-gates/SKILL.md", True, False),
        ("docs-site/site.json", True, False),
        ("docs-site/src/content/docs/architecture.md", True, False),
        ("frontend/.node-version", True, True),
        ("frontend/.nvmrc", True, True),
        ("frontend/static-paths.ts", True, True),
        ("frontend/lighthouserc.json", True, True),
        ("frontend/playwright.config.ts", True, True),
        ("frontend/vitest.unit.config.ts", True, True),
        ("frontend/e2e/a11y.spec.ts", True, True),
        ("Makefile", True, True),
        (".github/workflows/ci.yml", True, True),
        ("scripts/tools.sh", True, True),
        ("tools/pins.tsv", True, True),
        ("mise.toml", True, True),
        ("mise.lock", True, True),
        ("docs/readme.md", False, False),
        ("ci/old.yml", False, False),
    ],
)
def test_pull_request_paths_map_to_outputs(
    tmp_path: Path, path: str, *, backend: bool, frontend: bool
) -> None:
    result = _run_filter(tmp_path, "pull_request", [path])
    assert {k: result[k] for k in ("backend", "frontend")} == {
        "backend": str(backend).lower(),
        "frontend": str(frontend).lower(),
    }


@pytest.mark.parametrize(
    ("path", "images"),
    [
        ("Dockerfile.backend", True),
        ("src/your_service_name/main.py", True),
        ("frontend/app/page.tsx", True),
        ("infra/runtime/entrypoint.sh", False),
        ("scripts/ci/image-checks.sh", True),
        ("uv.lock", True),
        ("mise.toml", True),
        ("mise.lock", True),
        ("wiki/ci-cd.md", False),
        ("tests/scripts/image-checks.bats", False),
        ("charts/values.yaml", False),
    ],
)
def test_pull_request_paths_map_to_the_images_output(
    tmp_path: Path, path: str, *, images: bool
) -> None:
    assert _run_filter(tmp_path, "pull_request", [path])["images"] == str(images).lower()


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("charts/your-service-name/values.yaml", True),
        ("infra/k8s/bootstrap/values.yaml", True),
        ("infra/security/config.yaml", False),
        ("Dockerfile.waf", True),
        ("src/your_service_name/main.py", True),
        ("scripts/kind-deploy.sh", True),
        ("tools/kind-nodes.tsv", True),
        ("mise.toml", True),
        ("mise.lock", True),
        ("Makefile", True),
        ("deploy/policies/test/kyverno-values.yaml", True),
        ("tests/k8s/test_admission.py", True),
        ("tests/live/test_full_stack.py", True),
        ("tests/conftest.py", True),
        ("wiki/infrastructure.md", False),
        ("tests/scripts/kind-up.bats", False),
    ],
)
def test_pull_request_paths_map_to_the_kind_output(
    tmp_path: Path, path: str, *, kind: bool
) -> None:
    assert _run_filter(tmp_path, "pull_request", [path])["kind"] == str(kind).lower()


@pytest.mark.parametrize("event", ["push", "workflow_dispatch"])
def test_main_tags_and_dispatch_force_every_output(tmp_path: Path, event: str) -> None:
    """A push covers both main and v* tags; neither is filtered."""
    assert _run_filter(tmp_path, event, ["docs/readme.md"]) == {
        "backend": "true",
        "frontend": "true",
        "pins": "true",
        "images": "true",
        "kind": "true",
        "docs": "true",
    }


def test_filter_outputs_match_the_declared_outputs() -> None:
    declared = set(workflow("ci.yml")["jobs"]["changes"]["outputs"])
    assert declared == {"backend", "frontend", "pins", "images", "kind", "docs"}


def test_integration_job_uploads_its_report_only_on_failure() -> None:
    ups = failure_uploads(_jobs()["integration"])
    assert len(ups) == 1
    assert "junit-coverage.xml" in ups[0]["with"]["path"]
    assert "frontend/coverage/" in ups[0]["with"]["path"]


def test_unit_job_uploads_its_report_only_on_failure() -> None:
    ups = failure_uploads(_jobs()["unit"])
    assert len(ups) == 1
    assert "test-results/junit.xml" in ups[0]["with"]["path"]


# --- accessibility (axe, blocking) and Lighthouse CI (advisory) ---------------------------------


def _frontend_json() -> dict[str, Any]:
    path = Path(__file__).parents[2] / "frontend" / "package.json"
    return cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))


def test_a11y_job_is_blocking_scoped_and_reaches_ci_ok() -> None:
    job = _jobs()["a11y"]
    assert "continue-on-error" not in job
    assert job["timeout-minutes"] <= 15  # noqa: PLR2004
    assert job["permissions"] == {"contents": "read"}
    assert job["if"] == "inputs.frontend-changed"
    assert "build" in _needs(job)
    runs = _runs(job).splitlines()
    assert "pnpm run test:a11y" in [ln.strip() for ln in runs]
    # The browser comes from the runner install, with its system packages, like the live job.
    assert (
        'bash "$GITHUB_WORKSPACE/scripts/ci/retry.sh" 2 10 130 '
        "pnpm exec playwright install chromium" in [ln.strip() for ln in runs]
    )
    # Reached through the `frontend` job, which CI OK requires on every event.
    assert "frontend" in workflow("ci.yml")["jobs"]["ci-ok"]["needs"]
    assert workflow("ci.yml")["jobs"]["frontend"]["uses"].endswith("reusable-frontend.yml")


def test_a11y_script_runs_the_spec_against_the_built_app() -> None:
    root = Path(__file__).parents[2] / "frontend"
    assert _frontend_json()["scripts"]["test:a11y"] == (
        "E2E_START_SERVER=1 playwright test e2e/a11y.spec.ts"
    )
    config = (root / "playwright.config.ts").read_text(encoding="utf-8")
    assert "webServer" in config
    assert "scripts/serve-standalone.mjs" in config, "the server the image ships, not next start"
    assert "next start" not in config
    spec = (root / "e2e" / "a11y.spec.ts").read_text(encoding="utf-8")
    for tag in ("wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"):
        assert f"'{tag}'" in spec, tag
    assert "disableRules" not in spec, "no rule may be switched off"
    assert ".exclude(" not in spec
    assert "retries: 0" in spec, "no retry may hide an intermittent violation"


def test_the_live_job_runs_every_e2e_spec_including_axe() -> None:
    """`pnpm run test:e2e` has no spec filter, so the a11y spec runs against the full stack."""
    job = workflow("reusable-live.yml")["jobs"]["live"]
    assert "pnpm run test:e2e" in _runs(job)
    scripts = _frontend_json()["scripts"]
    assert scripts["test:e2e"] == "playwright test"


def test_lighthouse_job_is_advisory_and_not_a_blocking_gate() -> None:
    job = _jobs()["lighthouse"]
    assert job["continue-on-error"] is True
    assert job["name"].endswith("(advisory)")
    assert job["timeout-minutes"] <= 20  # noqa: PLR2004
    assert job["permissions"] == {"contents": "read"}
    assert job["if"] == "inputs.frontend-changed"
    runs = _runs(job)
    # The script turns budget findings into warnings and a summary line (test_lighthouse_assert.py);
    # only a tool error fails the job, and that warns too.
    assert "../scripts/ci/lighthouse-assert.sh" in runs
    assert "CHROME_PATH" in runs, "it must reuse the Playwright Chromium"
    last = job["steps"][-1]
    assert last["if"] == "failure()"
    assert "::warning title=Lighthouse (advisory)::" in last["run"]
    assert "GITHUB_STEP_SUMMARY" in last["run"]
    upload = next(st for st in job["steps"] if "upload-artifact" in str(st.get("uses", "")))
    assert upload["with"]["include-hidden-files"] is True, ".lighthouseci is a dot directory"
    # Nothing waits on it, and CI OK sees only the `frontend` job.
    for name, other in _jobs().items():
        assert "lighthouse" not in _needs(other), name
    assert "lighthouse" not in workflow("ci.yml")["jobs"]["ci-ok"]["needs"]
    # No required-check entry names it.
    doc = (Path(__file__).parents[2] / "wiki" / "github-repository-settings.md").read_text(
        encoding="utf-8"
    )
    assert "Lighthouse" not in doc.split("## Required status checks")[1].split("\n## ")[0]


def test_lighthouse_budgets_cover_categories_and_bytes() -> None:
    path = Path(__file__).parents[2] / "frontend" / "lighthouserc.json"
    config = json.loads(path.read_text(encoding="utf-8"))["ci"]
    assertions = config["assert"]["assertions"]
    for category in ("performance", "accessibility", "best-practices", "seo"):
        assert 0 < assertions[f"categories:{category}"][1]["minScore"] <= 1
    assert assertions["resource-summary:script:size"][1]["maxNumericValue"] > 0
    assert assertions["resource-summary:stylesheet:size"][1]["maxNumericValue"] > 0
    assert config["collect"]["settings"]["chromeFlags"]


def test_the_quality_gate_tools_are_pinned_exactly() -> None:
    dev = _frontend_json()["devDependencies"]
    for name in ("@axe-core/playwright", "@lhci/cli"):
        assert re.fullmatch(r"\d+\.\d+\.\d+", dev[name]), name


@pytest.mark.parametrize(
    ("path", "pins"),
    [
        # PNPM_VERSION and the two PNPM_SHA256 ARGs: make pins-refresh --check holds them to the
        # lock and the release digests.
        ("Dockerfile.frontend", True),
        ("Dockerfile.backend", False),
        ("mise.toml", True),
        ("frontend/app/page.tsx", False),
    ],
)
def test_pull_request_paths_map_to_the_pins_output(
    tmp_path: Path, path: str, *, pins: bool
) -> None:
    assert _run_filter(tmp_path, "pull_request", [path])["pins"] == str(pins).lower()
