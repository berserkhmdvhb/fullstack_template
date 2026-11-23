"""The CI orchestrator, the backend workflow and the setup actions."""

from __future__ import annotations

import re
from typing import Any

from tests.supply_chain._workflows import (
    ACTIONS,
    REPO,
    WORKFLOWS,
    all_files,
    failure_uploads,
    load,
    steps_of,
    unpinned_uses,
    workflow,
)

FAILURE_UPLOAD_JOBS = 3  # unit, integration and e2e each upload their own failure report
ADVISORY_PYTHON = "3.14"


def test_files_exist() -> None:
    for rel in ("ci.yml", "reusable-backend.yml"):
        assert (WORKFLOWS / rel).is_file(), rel
    for name in ("setup-backend", "setup-frontend"):
        assert (ACTIONS / name / "action.yml").is_file(), name


def test_ci_triggers_and_concurrency() -> None:
    ci = workflow("ci.yml")
    assert set(ci["on"]) == {"pull_request", "push", "workflow_dispatch"}
    assert ci["on"]["push"]["branches"] == ["main"]
    assert ci["on"]["push"]["tags"] == ["v*"]
    assert ci["concurrency"]["group"] == "${{ github.workflow }}-${{ github.ref }}"
    assert "pull_request" in str(ci["concurrency"]["cancel-in-progress"])


def test_ci_jobs_and_changes_outputs() -> None:
    jobs = workflow("ci.yml")["jobs"]
    assert {"changes", "backend", "repo"} <= set(jobs)
    assert "backend" in jobs["changes"]["outputs"]
    assert jobs["backend"]["needs"] == "changes"
    assert jobs["backend"]["uses"] == "./.github/workflows/reusable-backend.yml"
    assert "needs" not in jobs["repo"], "the repo job is not path-filtered"
    assert "if" not in jobs["repo"]


def test_reusable_backend_jobs() -> None:
    wf = workflow("reusable-backend.yml")
    assert "workflow_call" in wf["on"]
    assert {"lint", "unit", "integration", "e2e"} <= set(wf["jobs"])


def test_python_matrix_and_advisory_leg() -> None:
    unit = workflow("reusable-backend.yml")["jobs"]["unit"]
    assert unit["strategy"]["matrix"]["python-version"] == ["3.12", "3.13", ADVISORY_PYTHON]
    assert unit["strategy"]["fail-fast"] is False
    assert unit["continue-on-error"] == f"${{{{ matrix.python-version == '{ADVISORY_PYTHON}' }}}}"


def test_blocking_except_the_advisory_leg() -> None:
    for name in ("ci.yml", "reusable-backend.yml"):
        for job_name, job in workflow(name)["jobs"].items():
            if job_name == "unit":
                continue
            assert "continue-on-error" not in job, f"{name}:{job_name}"
        for step in steps_of(workflow(name)):
            assert "continue-on-error" not in step, step.get("name")


def test_coverage_floor_lives_in_pyproject() -> None:
    text = (WORKFLOWS / "reusable-backend.yml").read_text(encoding="utf-8")
    assert not re.search(r"fail[_-]under", text)
    assert "make test-unit" in text


def _runs(job: dict[str, Any]) -> str:
    return "\n".join(str(st.get("run", "")) for st in job["steps"])


def test_repo_job_runs_its_three_gates() -> None:
    runs = _runs(workflow("ci.yml")["jobs"]["repo"])
    assert "bash scripts/ci/shell-lint.sh" in runs
    assert "bash scripts/ci/check-secrets.sh" in runs
    assert "bats infra/security/local/tests/" in runs
    assert "scripts/tools.sh install shellcheck bats" in runs


def test_backend_jobs_call_the_make_targets() -> None:
    jobs = workflow("reusable-backend.yml")["jobs"]
    lint = _runs(jobs["lint"])
    for target in ("ruff-format-check", "ruff-lint", "mypy-check"):
        assert f"make {target}" in lint
    assert "dev-tools-outdated" not in lint
    assert "check-all-dry" not in lint, "it pulls in a PyPI call"
    assert "make test-unit" in _runs(jobs["unit"])
    assert "make test-integration" in _runs(jobs["integration"])
    assert "make test-e2e" in _runs(jobs["e2e"])


def test_advisory_leg_warns_on_failure() -> None:
    steps = workflow("reusable-backend.yml")["jobs"]["unit"]["steps"]
    warn = [s for s in steps if "failure()" in str(s.get("if", "")) and "run" in s]
    assert len(warn) == 1
    assert "3.14" in warn[0]["if"]
    assert "::warning" in warn[0]["run"]
    assert "GITHUB_STEP_SUMMARY" in warn[0]["run"]


def test_setup_backend_pins_no_floating_tool() -> None:
    text = (ACTIONS / "setup-backend" / "action.yml").read_text(encoding="utf-8")
    assert "--upgrade" not in text


def _setup_backend_steps() -> list[dict[str, Any]]:
    return steps_of(load(ACTIONS / "setup-backend" / "action.yml"))


def test_setup_backend_installs_the_pinned_uv_and_the_locked_set() -> None:
    steps = _setup_backend_steps()
    runs = [s.get("run", "") for s in steps]
    joined = "\n".join(runs)
    assert "scripts/tools.sh install uv" in joined
    assert "pip install" not in joined
    assert not any("setup-uv" in s.get("uses", "") for s in steps), "no third-party uv action"
    check = joined.index("uv lock --check")
    assert check < joined.index("uv sync --locked"), "a stale lock must fail before the install"
    sync = next(s for s in steps if "uv sync --locked" in s.get("run", ""))
    assert sync["env"]["UV_PYTHON_DOWNLOADS"] == "never"
    assert "--python" in sync["run"]
    assert "--no-dev" not in sync["run"], "the dev group is installed"


def test_setup_backend_caches_the_uv_cache_keyed_on_the_lock_and_python() -> None:
    steps = _setup_backend_steps()
    restore = next(s for s in steps if "actions/cache/restore" in s.get("uses", ""))
    assert "hashFiles('uv.lock')" in restore["with"]["key"]
    assert "inputs.python-version" in restore["with"]["key"]
    names = [s.get("run", "") or s.get("uses", "") for s in steps]
    prune = next(i for i, n in enumerate(names) if "uv cache prune --ci" in n)
    save = next(i for i, n in enumerate(names) if "actions/cache/save" in n)
    assert prune < save
    for step in (steps[prune], steps[save]):
        assert "github.ref == 'refs/heads/main'" in step["if"]
        assert "github.event_name == 'push'" in step["if"]


def test_the_backend_path_filter_includes_the_lock() -> None:
    steps = workflow("ci.yml")["jobs"]["changes"]["steps"]
    run = next(s["run"] for s in steps if s.get("id") == "filter")
    pattern = next(m for m in re.findall(r"grep -Eq '([^']+)'", run) if "pyproject" in m)
    assert re.search(pattern, "uv.lock")


def test_every_workflow_has_permissions_and_concurrency() -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        wf = load(path)
        assert wf["permissions"] in ({}, {"contents": "read"}), path.name
        if set(wf["on"]) != {"workflow_call"}:
            assert "concurrency" in wf, path.name


# The only write grants any workflow job may hold: (workflow, job, permission).
WRITE_ALLOWLIST = {
    ("reusable-build.yml", "build", "packages"),
    ("reusable-build.yml", "merge", "packages"),
    ("reusable-build.yml", "grype", "security-events"),
    # Keyless signing and attestations: the reusable build workflow and its caller (the union).
    ("reusable-build.yml", "sign-attest", "packages"),
    ("reusable-build.yml", "sign-attest", "id-token"),
    ("reusable-build.yml", "sign-attest", "attestations"),
    ("reusable-build.yml", "attest-sbom", "packages"),
    ("reusable-build.yml", "attest-sbom", "id-token"),
    ("reusable-build.yml", "attest-sbom", "attestations"),
    ("release.yml", "images", "packages"),
    ("release.yml", "images", "security-events"),
    ("release.yml", "images", "id-token"),
    ("release.yml", "images", "attestations"),
    ("security.yml", "codeql", "security-events"),
    ("security.yml", "pip-audit", "security-events"),
    ("security.yml", "advisory", "security-events"),
    ("ci.yml", "security", "security-events"),
    ("dast.yml", "dast", "security-events"),
    # OpenSSF Scorecard: SARIF upload and the OIDC token that authenticates published results.
    ("scorecard.yml", "analysis", "security-events"),
    ("scorecard.yml", "analysis", "id-token"),
    # Workload Identity Federation: only the jobs that log in to the cluster, each behind an
    # environment.
    ("release.yml", "deploy", "id-token"),
    ("deploy.yml", "deploy-dev", "id-token"),
    ("deploy.yml", "deploy-qa", "id-token"),
    ("deploy.yml", "deploy-production", "id-token"),
    ("deploy-ops.yml", "ops", "id-token"),
    ("preview.yml", "deploy", "id-token"),
    ("preview.yml", "cleanup", "id-token"),
    ("preview.yml", "sweep", "id-token"),
    # The weekly re-scan: only its report job writes, and only issues.
    ("rescan.yml", "report", "issues"),
}


# The only non-contents read grants: (workflow, job, permission, level).
READ_ALLOWLIST = {
    ("reusable-build.yml", "sbom", "packages", "read"),
    ("reusable-build.yml", "image-checks", "packages", "read"),
    ("security.yml", "codeql", "actions", "read"),
    ("ci.yml", "security", "actions", "read"),
    ("dast.yml", "dast", "actions", "read"),
    ("dast.yml", "dast", "packages", "read"),
    ("scorecard.yml", "analysis", "actions", "read"),
    ("rescan.yml", "collect", "actions", "read"),
    ("rescan.yml", "collect", "deployments", "read"),
    ("rescan.yml", "scan", "packages", "read"),
    # The previews: the digests dev runs (rescan_collect.py --deployed), and the open pull requests.
    ("preview.yml", "digests", "actions", "read"),
    ("preview.yml", "digests", "deployments", "read"),
    ("preview.yml", "open-prs", "pull-requests", "read"),
}


# The one job that holds contents: write: it creates the draft release and uploads its assets.
CONTENTS_WRITE = ("release.yml", "release-assets")


def test_no_job_grants_write() -> None:
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for name, job in load(path)["jobs"].items():
            perms = dict(job["permissions"])
            contents = "write" if (path.name, name) == CONTENTS_WRITE else "read"
            assert perms.pop("contents") == contents, (path.name, name)
            for perm, level in perms.items():
                if (path.name, name, perm, level) in READ_ALLOWLIST:
                    continue
                assert level == "write", (path.name, name, perm)
                assert (path.name, name, perm) in WRITE_ALLOWLIST, (path.name, name, perm)
            if "uses" not in job:
                assert isinstance(job["timeout-minutes"], int), (path.name, name)
                # The per-platform jobs take their runner from the manifest's runners map.
                native = (path.name, name) in {
                    ("ci.yml", "images"),
                    ("reusable-build.yml", "build"),
                    ("reusable-build.yml", "image-checks"),
                }
                expected = "${{ matrix.runner }}" if native else "ubuntu-24.04"
                assert job["runs-on"] == expected, (path.name, name)


def test_checkout_does_not_persist_credentials() -> None:
    for path in all_files():
        for step in steps_of(load(path)):
            if str(step.get("uses", "")).startswith("actions/checkout@"):
                assert step["with"]["persist-credentials"] is False, path.name


def test_every_third_party_action_is_sha_pinned() -> None:
    for path in all_files():
        assert unpinned_uses(path) == [], path.name


def test_fork_safety() -> None:
    for path in all_files():
        assert "pull_request_target" not in path.read_text(encoding="utf-8"), path.name
        for step in steps_of(load(path)):
            assert not re.search(r"\$\{\{\s*secrets\.", str(step.get("run", ""))), (
                path.name,
                step.get("name"),
            )


def test_setup_frontend_reads_the_node_pin() -> None:
    action = load(ACTIONS / "setup-frontend" / "action.yml")
    text = (ACTIONS / "setup-frontend" / "action.yml").read_text(encoding="utf-8")
    assert "tools/pins.tsv" in text
    assert "frontend/package-lock.json" in text
    assert "npm ci" in text
    assert action["description"]


def test_zizmor_disables_only_self_repository() -> None:
    rules = load(REPO / ".github" / "zizmor.yml")["rules"]
    disabled = {name for name, cfg in rules.items() if cfg.get("disable")}
    assert disabled == {"self-repository"}
    # The one scoped exception: dangerous-triggers is ignored for dast.yml alone, never disabled.
    assert rules["dangerous-triggers"] == {"ignore": ["dast.yml"]}
    assert set(rules) == {"self-repository", "dangerous-triggers"}


def test_test_jobs_upload_their_reports_only_on_failure() -> None:
    jobs = workflow("reusable-backend.yml")["jobs"]
    names = []
    for key in ("unit", "integration", "e2e"):
        ups = failure_uploads(jobs[key])
        assert len(ups) == 1, key
        names.append(ups[0]["with"]["name"])
        assert "junit-" in ups[0]["with"]["path"]
    assert "${{ matrix.python-version }}" in names[0]
    assert len(set(names)) == FAILURE_UPLOAD_JOBS
    assert "coverage.xml" in failure_uploads(jobs["unit"])[0]["with"]["path"]
