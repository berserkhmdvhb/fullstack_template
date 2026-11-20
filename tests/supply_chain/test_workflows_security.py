"""The security workflow (Task 1.6): blocking vs advisory split, SARIF categories, triggers.

Static, offline.
"""

from __future__ import annotations

import re
from typing import Any, cast

import yaml

from tests.supply_chain._workflows import REPO, WORKFLOWS, load, workflow

IMAGES = REPO / ".github" / "container-images.yml"
BLOCKING = {"codeql", "dependency-review", "gitleaks"}
NOTICE = {"code-security-off"}
ADVISORY = {"pip-audit", "advisory"}
ARTIFACT_RETENTION_DAYS = 90
CATEGORIES = {"pip-audit", "osv-scanner", "guarddog", "checkov", "kubescape"}


def _wf() -> dict[str, Any]:
    return workflow("security.yml")


def _jobs() -> dict[str, Any]:
    return cast("dict[str, Any]", _wf()["jobs"])


def _runs(job: dict[str, Any]) -> str:
    return "\n".join(s.get("run", "") for s in job["steps"])


def _uploads(job: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        s
        for s in job["steps"]
        if str(s.get("uses", "")).startswith("github/codeql-action/upload-sarif@")
    ]


def test_triggers() -> None:
    on = _wf()["on"]
    assert set(on) == {"workflow_call", "schedule", "workflow_dispatch"}
    (entry,) = on["schedule"]
    minute, hour, *_ = entry["cron"].split()
    assert minute not in ("0", "*"), "an off-hour cron avoids the top-of-hour rush"
    assert hour != "*", "an off-hour cron avoids the top-of-hour rush"


def test_concurrency_never_uses_the_workflow_name() -> None:
    conc = _wf()["concurrency"]
    assert "github.workflow" not in conc["group"], (
        "a called workflow would resolve to the caller's name"
    )
    assert conc["group"].startswith("security-")
    assert conc["cancel-in-progress"] is False


def test_job_set_and_permissions() -> None:
    wf = _wf()
    assert wf["permissions"] == {}
    assert set(wf["jobs"]) == BLOCKING | ADVISORY | NOTICE
    codeql = {"contents": "read", "security-events": "write", "actions": "read"}
    sarif = {"contents": "read", "security-events": "write"}
    read = {"contents": "read"}
    expected = {
        "codeql": codeql,
        "dependency-review": read,
        "gitleaks": read,
        "code-security-off": read,
        "pip-audit": sarif,
        "advisory": sarif,
    }
    for name, job in wf["jobs"].items():
        assert job["permissions"] == expected[name], name
        assert isinstance(job["timeout-minutes"], int), name


def test_blocking_jobs_have_no_continue_on_error() -> None:
    for name in BLOCKING:
        job = _jobs()[name]
        assert "continue-on-error" not in job, name
        assert all("continue-on-error" not in s for s in job["steps"]), name


def test_advisory_jobs_fail_only_on_tool_errors() -> None:
    for name in ADVISORY:
        job = _jobs()[name]
        assert "continue-on-error" not in job, name
        for step in job["steps"]:
            if "continue-on-error" in step:
                assert step["uses"].startswith("github/codeql-action/upload-sarif@"), (name, step)
                assert step["continue-on-error"] is True
        runs = _runs(job)
        assert "::warning" in runs, name
        assert "GITHUB_STEP_SUMMARY" in runs, name
        assert "::error" in runs, f"{name} fails on a tool error"
        assert "SKIPPED" in runs or name == "pip-audit", name
        # The SARIF upload is followed by a failure-only warning; the artifact is always kept.
        (upload,) = _uploads(job)
        assert upload["continue-on-error"] is True
        assert upload["id"]
        assert "hashFiles(" in upload["if"]
        assert "!= ''" in upload["if"]
        warn = [
            s
            for s in job["steps"]
            if f"steps.{upload['id']}.outcome == 'failure'" in str(s.get("if", ""))
        ]
        assert len(warn) == 1, name
        assert "::warning" in warn[0]["run"], name
        arts = [
            s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact@")
        ]
        assert len(arts) == 1, name
        assert arts[0]["with"]["retention-days"] == ARTIFACT_RETENTION_DAYS, name
        assert "always()" in arts[0]["if"]


def test_advisory_wrappers_are_the_single_source() -> None:
    job = _jobs()["advisory"]
    ids = {m["id"]: m for m in job["strategy"]["matrix"]["include"]}
    assert set(ids) == {"osv", "guarddog", "checkov", "kubescape"}
    assert {m["script"] for m in ids.values()} == {
        "scan-osv.sh",
        "scan-malware-heuristic.sh",
        "scan-iac.sh",
        "scan-k8s.sh",
    }
    assert job["strategy"]["fail-fast"] is False
    assert "infra/security/local/" in _runs(job)
    pip_audit = _runs(_jobs()["pip-audit"])
    assert "--require-hashes" in pip_audit
    assert "tools/requirements/pip-audit.txt" in pip_audit
    assert not re.search(r"pip install (?!.*--require-hashes)\S*audit", pip_audit)


def _categories() -> list[str]:
    """Every resolved SARIF category across all workflows (matrix placeholders expanded)."""
    found: list[str] = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for job in load(path)["jobs"].values():
            matrix = job.get("strategy", {}).get("matrix", {})
            if isinstance(
                matrix, str
            ):  # fromJSON of the image plan: one leg per image in the manifest
                ids = [
                    i["id"] for i in yaml.safe_load(IMAGES.read_text(encoding="utf-8"))["images"]
                ]
                matrix = {"include": [{"id": i} for i in ids]}
            for step in job.get("steps", []):
                if not str(step.get("uses", "")).startswith("github/codeql-action/upload-sarif@"):
                    continue
                cat = str(step["with"]["category"])
                m = re.search(r"\$\{\{\s*matrix\.(\w+)\s*\}\}", cat)
                if not m:
                    found.append(cat)
                    continue
                values = list(matrix.get(m[1], [])) + [
                    i[m[1]] for i in matrix.get("include", []) if m[1] in i
                ]
                assert values, (path.name, cat)
                found += [cat.replace(m[0], str(v)) for v in values]
    return found


def test_sarif_categories_are_unique_across_all_workflows() -> None:
    cats = _categories()
    assert len(cats) == len(set(cats)), sorted(c for c in cats if cats.count(c) > 1)
    assert set(cats) >= CATEGORIES
    assert any(c.startswith("grype-") for c in cats), (
        "release.yml's categories are part of the check"
    )
    # Every advisory category is unique per tool, never a shared one.
    advisory = [m["category"] for m in _jobs()["advisory"]["strategy"]["matrix"]["include"]]
    assert set(advisory) == CATEGORIES - {"pip-audit"}
    assert _uploads(_jobs()["pip-audit"])[0]["with"]["category"] == "pip-audit"


def test_codeql_job() -> None:
    job = _jobs()["codeql"]
    assert job["strategy"]["matrix"]["language"] == ["python", "javascript-typescript"]
    steps = {s["uses"].split("@")[0]: s for s in job["steps"] if "uses" in s}
    init, analyze = steps["github/codeql-action/init"], steps["github/codeql-action/analyze"]
    assert init["with"]["build-mode"] == "none"
    assert init["with"]["queries"] == "security-extended"
    assert init["uses"].split("@")[1] == _uploads(_jobs()["pip-audit"])[0]["uses"].split("@")[1]
    assert analyze["uses"].split("@")[1] == init["uses"].split("@")[1]
    assert "category" not in analyze.get("with", {}), "the default /language:<lang> category"


def test_dependency_review_is_pr_only_and_has_no_literal_licence_list() -> None:
    job = _jobs()["dependency-review"]
    assert "github.event_name == 'pull_request'" in job["if"]
    (step,) = [
        s
        for s in job["steps"]
        if str(s.get("uses", "")).startswith("actions/dependency-review-action@")
    ]
    assert step["with"]["fail-on-severity"] == "high"
    allow = str(step["with"]["allow-licenses"])
    licences = next(s for s in job["steps"] if s.get("id") == "licences")
    assert allow == "${{ steps.licences.outputs.allow }}", "exactly the step output"
    assert "set -o pipefail" in licences["run"], "a yq failure must not yield an empty list"
    assert '[[ -n "$list" ]]' in licences["run"], "an empty list is refused"
    assert "exit 1" in licences["run"]
    assert "comment-summary-in-pr" not in step["with"] or step["with"]["comment-summary-in-pr"] in (
        "never",
        False,
    )
    runs = _runs(job)
    assert "scripts/tools.sh install yq" in runs
    assert "infra/security/grant.yaml" in runs


def test_gitleaks_scans_pr_range_or_full_history() -> None:
    job = _jobs()["gitleaks"]
    checkout = next(
        s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout["with"]["fetch-depth"] == 0
    assert checkout["with"]["persist-credentials"] is False
    runs = _runs(job)
    assert "scripts/tools.sh install gitleaks" in runs
    assert "gitleaks-action" not in (WORKFLOWS / "security.yml").read_text(encoding="utf-8")
    assert "--redact" in runs
    assert "--log-opts" in runs
    step = next(s for s in job["steps"] if "--log-opts" in s.get("run", ""))
    assert "${{" not in step["run"], "values come through env:"
    assert {"BASE_SHA", "HEAD_SHA"} <= set(step["env"])
    assert "BASE_SHA}..${HEAD_SHA" in step["run"].replace('"', "")
    assert "pull_request" in step["run"] or "pull_request" in str(step["env"]), (
        "the PR-range vs full-history split"
    )


def test_ci_calls_security_unconditionally_with_the_union_of_grants() -> None:
    job = workflow("ci.yml")["jobs"]["security"]
    assert job["uses"] == "./.github/workflows/security.yml"
    assert not {"if", "needs", "paths", "with"} & set(job), (
        "security always runs, with no paths gate"
    )
    union: dict[str, str] = {}
    for j in _jobs().values():
        for k, v in j["permissions"].items():
            union[k] = "write" if "write" in (v, union.get(k)) else v
    assert job["permissions"] == union
    assert (
        "paths" not in workflow("ci.yml")["on"].get("pull_request", {})
        if workflow("ci.yml")["on"].get("pull_request")
        else True
    )


def test_no_secrets_and_no_pr_write_beyond_sarif() -> None:
    text = (WORKFLOWS / "security.yml").read_text(encoding="utf-8")
    assert "secrets." not in text
    assert "pull-requests:" not in text
    assert "pull_request_target" not in text


def test_code_security_opt_out_gates_only_codeql_and_dependency_review() -> None:
    jobs = _jobs()
    assert "vars.CODE_SECURITY != 'off'" in jobs["codeql"]["if"]
    assert "vars.CODE_SECURITY != 'off'" in jobs["dependency-review"]["if"]
    for name in ("gitleaks", "pip-audit", "advisory"):
        assert "CODE_SECURITY" not in str(jobs[name].get("if", "")), name
    notice = jobs["code-security-off"]
    assert notice["if"] == "vars.CODE_SECURITY == 'off'"
    run = _runs(notice)
    assert "::notice" in run
    assert "GITHUB_STEP_SUMMARY" in run
    assert "${{" not in run
    assert "CODE_SECURITY" not in "\n".join(_runs(j) for j in jobs.values() if j is not notice)
