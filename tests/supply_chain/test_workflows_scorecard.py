"""The OpenSSF Scorecard workflow and its README badge. Static, offline."""

from __future__ import annotations

from typing import Any, cast

from tests.supply_chain._workflows import REPO, WORKFLOWS, unpinned_uses, workflow

APPROVED = {
    "actions/checkout",
    "actions/upload-artifact",
    "github/codeql-action/upload-sarif",
    "ossf/scorecard-action",
}


def _job() -> dict[str, Any]:
    return cast("dict[str, Any]", workflow("scorecard.yml")["jobs"]["analysis"])


def _step(prefix: str) -> dict[str, Any]:
    return next(s for s in _job()["steps"] if str(s.get("uses", "")).startswith(prefix))


def test_triggers_are_weekly_schedule_and_push_to_main_only() -> None:
    on = workflow("scorecard.yml")["on"]
    assert set(on) == {"schedule", "push"}
    assert on["push"] == {"branches": ["main"]}
    minute, hour, day, month, weekday = on["schedule"][0]["cron"].split()
    assert minute not in {"0", "*"}  # a fixed off-the-hour time
    assert hour.isdigit()
    assert (day, month) == ("*", "*")
    assert weekday.isdigit()  # weekly


def test_workflow_has_no_top_level_env_defaults_or_write_permission() -> None:
    wf = workflow("scorecard.yml")
    assert wf["permissions"] == {}
    assert not {"env", "defaults"} & set(wf)


def test_job_permissions_are_exactly_what_the_action_documents() -> None:
    assert _job()["permissions"] == {
        "contents": "read",
        "actions": "read",
        "security-events": "write",
        "id-token": "write",
    }
    text = (WORKFLOWS / "scorecard.yml").read_text(encoding="utf-8")
    for scope in ("contents", "actions", "security-events", "id-token"):
        line = next(ln for ln in text.splitlines() if ln.strip().startswith(f"{scope}:"))
        assert "#" in line, scope


def test_job_obeys_the_publishing_restrictions() -> None:
    job = _job()
    assert job["runs-on"] == "ubuntu-24.04"
    assert not {"env", "defaults", "container", "services"} & set(job)
    assert "timeout-minutes" in job
    assert {str(s["uses"]).split("@")[0] for s in job["steps"]} == APPROVED
    assert all("run" not in s for s in job["steps"])


def test_job_only_runs_on_public_repositories() -> None:
    assert _job()["if"] == "github.event.repository.visibility == 'public'"


def test_every_uses_is_sha_pinned_with_a_version_comment() -> None:
    assert unpinned_uses(WORKFLOWS / "scorecard.yml") == []


def test_checkout_does_not_persist_credentials() -> None:
    assert _step("actions/checkout@")["with"] == {"persist-credentials": False}


def test_scorecard_publishes_sarif_results() -> None:
    w = _step("ossf/scorecard-action@")["with"]
    assert w["publish_results"] is True
    assert w["results_format"] == "sarif"
    assert w["results_file"] == "results.sarif"


def test_sarif_goes_to_code_scanning_and_an_artifact() -> None:
    assert _step("github/codeql-action/upload-sarif@")["with"]["sarif_file"] == "results.sarif"
    art = _step("actions/upload-artifact@")["with"]
    assert art["path"] == "results.sarif"


def test_readme_has_the_scorecard_badge_with_placeholders() -> None:
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert (
        "[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/"
        "<owner>/<your-service-name>/badge)]"
        "(https://scorecard.dev/viewer/?uri=github.com/<owner>/<your-service-name>)"
    ) in readme
