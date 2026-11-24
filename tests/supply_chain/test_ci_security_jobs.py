"""Source-level supply-chain scanning in GitHub Actions (security.yml, called from ci.yml).

The source-level jobs run on pull requests for fast feedback AND on protected
refs. The split is fixed:

- blocking: CodeQL, dependency-review (pull requests), gitleaks; grype (release.yml)
- advisory: pip-audit, and the `advisory` matrix (osv, guarddog, checkov, kubescape).
  A finding is a `::warning`, never a failure; only a tool error fails the job.

Each scanner's logic lives in one script under `infra/security/local/` (the
single source shared with `devsec.sh`), so the job only prepares the runtime and
delegates. Static and offline: these tests parse the committed workflows.

Retired from the old pipeline: the `allow_failure: true` flag (advisory is now
"no continue-on-error, ::warning on findings, never a required check"), and the
`security` stage (GitHub Actions has no stages).
"""

from __future__ import annotations

from typing import Any

from tests.supply_chain._workflows import every_step, workflow

BLOCKING = {"codeql", "dependency-review", "gitleaks"}
ADVISORY = {"pip-audit", "advisory"}
OSV_SCRIPT = "infra/security/local/scan-osv.sh"
MALWARE_HEURISTIC_SCRIPT = "infra/security/local/scan-malware-heuristic.sh"


def jobs() -> dict[str, Any]:
    return dict(workflow("security.yml")["jobs"])


def _runs(job: dict[str, Any]) -> str:
    return "\n".join(str(s.get("run", "")) for s in job["steps"])


def _matrix(id_: str) -> dict[str, Any]:
    (entry,) = [m for m in jobs()["advisory"]["strategy"]["matrix"]["include"] if m["id"] == id_]
    return dict(entry)


# --- wiring ------------------------------------------------------------------


def test_security_yml_is_wired_into_the_pipeline() -> None:
    # The jobs live in security.yml; they only run if ci.yml calls it (on every run, no paths gate).
    ci = workflow("ci.yml")
    caller = ci["jobs"]["security"]
    assert caller["uses"] == "./.github/workflows/security.yml"
    assert "needs" not in caller, "no gate may skip the security jobs"
    assert "if" not in caller, "no condition may skip the security jobs"
    assert "pull_request" in ci["on"], "the security jobs must run on pull requests"
    assert ci["on"]["push"]["branches"] == ["main"]
    assert "workflow_call" in workflow("security.yml")["on"]


# --- the advisory/blocking split ---------------------------------------------


def test_blocking_jobs_cannot_be_softened() -> None:
    for name in BLOCKING | {"grype"}:
        job = jobs().get(name) or workflow("reusable-build.yml")["jobs"][name]
        assert "continue-on-error" not in job, name
        assert all(
            "continue-on-error" not in s
            for s in job["steps"]
            if "upload-sarif" not in s.get("uses", "")
        ), name


def test_advisory_jobs_are_report_only() -> None:
    for name in ADVISORY:
        job = jobs()[name]
        assert "(advisory)" in job["name"], f"{name} must be labelled advisory"
        assert "continue-on-error" not in job, name
        assert "::warning" in _runs(job), f"{name} reports findings as a warning"
        assert "::error" in _runs(job), f"{name} still fails on a tool error"


# --- pip-audit ---------------------------------------------------------------


def test_pip_audit_installs_a_pinned_auditor_before_running_it() -> None:
    # Pin + verify, never assume: the auditor comes from a hash-pinned file BEFORE it runs.
    runs = _runs(jobs()["pip-audit"])
    install_at = runs.find('/bin/pip" install')
    audit_at = runs.find('/bin/pip-audit" --path')
    assert install_at != -1
    assert audit_at != -1
    assert install_at < audit_at, "pip-audit must be installed before it is run"
    assert "--require-hashes" in runs
    assert "tools/requirements/pip-audit.txt" in runs


# --- osv and guarddog (legs of the advisory matrix) --------------------------


def test_osv_scan_delegates_to_its_script() -> None:
    # One script, shared with `devsec.sh osv`; the job only prepares the runtime and hands off.
    assert f"infra/security/local/{_matrix('osv')['script']}" == OSV_SCRIPT
    assert 'bash "infra/security/local/${SCRIPT}"' in _runs(jobs()["advisory"])


def test_malware_heuristic_scan_delegates_to_its_script() -> None:
    assert f"infra/security/local/{_matrix('guarddog')['script']}" == MALWARE_HEURISTIC_SCRIPT
    assert 'bash "infra/security/local/${SCRIPT}"' in _runs(jobs()["advisory"])


def test_source_level_scans_have_no_ref_gate() -> None:
    # Source-level: they run on pull requests and protected refs, so no job condition narrows them.
    for name in ADVISORY:
        assert "if" not in jobs()[name], name


# --- image operations stay on protected refs ---------------------------------


def test_image_operations_never_run_on_pull_requests() -> None:
    # Build, push and signing exist only in reusable-build.yml (and the chart's in
    # reusable-chart-release.yml), called by release.yml, which triggers on main and v* only;
    # the one other registry login (dast.yml) is reached by workflow_run/dispatch, never by a
    # pull request.
    release = workflow("release.yml")
    assert set(release["on"]) == {"push"}
    assert release["on"]["push"] == {"branches": ["main"], "tags": ["v*"]}
    assert "pull_request" not in workflow("dast.yml")["on"]
    for name, step in every_step():
        uses = str(step.get("uses", ""))
        if uses.startswith("docker/build-push-action@"):
            assert name == "reusable-build.yml", (name, uses)
        if uses.startswith("docker/login-action@"):
            assert name in {"reusable-build.yml", "reusable-chart-release.yml", "dast.yml"}, (
                name,
                uses,
            )
