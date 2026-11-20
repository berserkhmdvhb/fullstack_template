"""The DAST workflow: a ZAP baseline against the released backend image.

Static, offline.
"""

from __future__ import annotations

import re
from typing import Any, cast

import yaml

from tests.supply_chain._workflows import (
    ARTIFACT_RETENTION_DAYS,
    REPO,
    WORKFLOWS,
    load,
    steps_of,
    workflow,
)

ZAP_PIN = re.compile(r"^[a-z0-9./-]+:\d+\.\d+\.\d+@sha256:[0-9a-f]{64}$")


def _wf() -> dict[str, Any]:
    return workflow("dast.yml")


def _job() -> dict[str, Any]:
    return cast("dict[str, Any]", _wf()["jobs"]["dast"])


def _text() -> str:
    return (WORKFLOWS / "dast.yml").read_text(encoding="utf-8")


def _runs() -> str:
    return "\n".join(s.get("run", "") for s in _job()["steps"])


def test_triggers_are_release_completed_and_dispatch() -> None:
    on = _wf()["on"]
    assert set(on) == {"workflow_run", "workflow_dispatch"}
    assert on["workflow_run"] == {
        "workflows": [workflow("release.yml")["name"]],
        "types": ["completed"],
    }
    assert list(on["workflow_dispatch"]["inputs"]) == ["image"]
    assert on["workflow_dispatch"]["inputs"]["image"]["required"] is True


def test_job_guard_has_all_three_parts() -> None:
    cond = _job()["if"]
    assert "github.event_name == 'workflow_dispatch'" in cond
    for part in (
        "github.event.workflow_run.conclusion == 'success'",
        "github.event.workflow_run.event == 'push'",
        "github.event.workflow_run.head_repository.full_name == github.repository",
    ):
        assert part in cond


def test_permissions_are_minimal() -> None:
    assert _wf()["permissions"] == {}
    assert _job()["permissions"] == {
        "contents": "read",
        "actions": "read",
        "packages": "read",
        "security-events": "write",
    }


def test_concurrency_is_per_head_sha_and_never_cancels() -> None:
    conc = _wf()["concurrency"]
    assert conc["group"] == "dast-${{ github.event.workflow_run.head_sha || github.sha }}"
    assert conc["cancel-in-progress"] is False
    assert "github.workflow" not in str(conc)


def test_zap_image_is_pinned_by_digest_in_config_only() -> None:
    cfg = yaml.safe_load((REPO / "infra" / "security" / "config.yaml").read_text(encoding="utf-8"))
    assert ZAP_PIN.match(cfg["dast"]["zap_image"]), cfg["dast"]["zap_image"]
    assert "zap2docker" not in _text()
    assert not re.search(r"zap[\w./-]*:(stable|latest|weekly|nightly|live)\b", _text())
    assert "zap_image" in _text()
    for rel in ("scan-dast.sh", "devsec.sh"):
        script = (REPO / "infra" / "security" / "local" / rel).read_text(encoding="utf-8")
        assert "zap2docker" not in script, rel
        assert not re.search(r"zap[\w./-]*:(stable|latest|weekly|nightly|live)\b", script), rel
        assert "dast.zap_image" in script, rel


def test_dispatch_input_is_validated_through_env() -> None:
    steps = [s for s in _job()["steps"] if "inputs.image" in str(s.get("env", {}))]
    assert len(steps) == 1
    run = steps[0]["run"]
    assert r"^ghcr\.io/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$" in run
    assert "exit 1" in run
    assert "inputs.image" not in "\n".join(s.get("run", "") for s in _job()["steps"])


def test_artifact_is_parsed_as_data_never_sourced() -> None:
    runs = _runs()
    assert not re.search(r"(^|\s)(source|\.)\s+\S*(image-digests|scan)\.env", runs, re.MULTILINE)
    assert "source " not in runs
    assert "BACKEND_IMAGE_DIGEST" in runs
    assert re.search(r"\^sha256:\[0-9a-f\]\{64\}\$", runs)


def test_artifact_comes_from_the_triggering_run_by_data_only() -> None:
    (dl,) = [
        s
        for s in _job()["steps"]
        if str(s.get("uses", "")).startswith("actions/download-artifact@")
    ]
    assert dl["with"]["name"] == "image-digests"
    assert dl["with"]["run-id"] == "${{ github.event.workflow_run.id }}"
    assert dl["with"]["github-token"] == "${{ secrets.GITHUB_TOKEN }}"
    assert "if" in dl  # skipped on dispatch


def test_checkout_is_default_branch_without_credentials() -> None:
    checkouts = [
        s for s in steps_of(_wf()) if str(s.get("uses", "")).startswith("actions/checkout@")
    ]
    assert len(checkouts) == 1
    assert checkouts[0]["with"] == {"persist-credentials": False}
    assert "head_sha" not in str(checkouts[0])
    assert "ref" not in checkouts[0]["with"]


def test_wrapper_is_called_with_the_image_through_env() -> None:
    assert "infra/security/local/scan-dast.sh" in _runs()
    assert "docker run" not in _runs()
    assert "zap-baseline" not in _runs()
    assert "DAST_TARGET_IMAGE" in _text()


def test_advisory_split_and_sarif_upload() -> None:
    job = _job()
    runs = _runs()
    assert "::warning" in runs
    assert "GITHUB_STEP_SUMMARY" in runs
    assert "SKIPPED" in runs
    # a tool error (status ERROR) fails the job; findings and SKIPPED only warn
    err = runs.split("ERROR)")[1].split(";;")[0]
    assert "::error" in err
    assert "exit 1" in err
    assert "::warning" not in err
    (sarif,) = [
        s
        for s in job["steps"]
        if str(s.get("uses", "")).startswith("github/codeql-action/upload-sarif@")
    ]
    assert sarif["with"]["category"] == "zap"
    assert sarif["continue-on-error"] is True
    assert "hashFiles(" in sarif["if"]
    warn = [s for s in job["steps"] if "steps.sarif.outcome == 'failure'" in str(s.get("if", ""))]
    assert len(warn) == 1
    assert "::warning" in warn[0]["run"]
    (art,) = [
        s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert art["with"]["retention-days"] == ARTIFACT_RETENTION_DAYS
    assert art["if"] == "always()"


def test_zizmor_ignores_dangerous_triggers_for_dast_only() -> None:
    rules = load(REPO / ".github" / "zizmor.yml")["rules"]
    assert rules["dangerous-triggers"] == {"ignore": ["dast.yml"]}
    assert {n for n, c in rules.items() if c.get("disable")} == {"self-repository"}
    assert set(rules) == {"self-repository", "dangerous-triggers"}
