"""The optional PR-Agent reviewer: off by default, advisory, minimal rights, pinned. Offline."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
from typing import TYPE_CHECKING, Any, cast

import pytest

from tests.supply_chain._workflows import REPO, WORKFLOWS, unpinned_uses, workflow
from tests.supply_chain.test_renovate_config import _compile, renovate

if TYPE_CHECKING:
    from pathlib import Path

BASH: str = shutil.which("bash") or "/usr/bin/bash"
NAME = "pr-agent.yml"
IMAGE = re.compile(
    r"^docker://pragent/pr-agent:(?P<tag>\d+\.\d+\.\d+-github_action)@sha256:[0-9a-f]{64}$"
)


def _wf() -> dict[str, Any]:
    return workflow(NAME)


def _job(key: str) -> dict[str, Any]:
    return cast("dict[str, Any]", _wf()["jobs"][key])


def _if(key: str) -> str:
    return " ".join(str(_job(key)["if"]).split())


def test_triggers_are_pull_request_and_comments_never_pull_request_target() -> None:
    on = _wf()["on"]
    assert set(on) == {"pull_request", "issue_comment"}
    # No `synchronize`: PR-Agent ignores it while its push trigger is off (the default).
    assert set(on["pull_request"]["types"]) == {"opened", "reopened", "ready_for_review"}
    assert on["issue_comment"]["types"] == ["created"]
    assert "pull_request_target" not in on


def test_the_gate_is_off_unless_the_variable_is_true_and_the_review_needs_it() -> None:
    # The pre-filter: an ordinary comment starts no runner; the shell step stays the decision.
    assert _if("gate") == (
        "vars.PR_AGENT_ENABLED == 'true' && (github.event_name == 'pull_request' || "
        "(github.event.issue.pull_request && startsWith(github.event.comment.body, '/')))"
    )
    review = _job("review")
    assert review["needs"] == "gate"
    assert _if("review") == "needs.gate.outputs.run == 'true'"
    assert set(_wf()["jobs"]) == {"gate", "review"}


def test_the_gate_holds_no_secret_no_environment_and_read_rights_only() -> None:
    gate = _job("gate")
    assert "environment" not in gate
    assert gate["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert "secrets." not in str(gate)
    assert gate["outputs"] == {"run": "${{ steps.decide.outputs.run }}"}
    (step,) = gate["steps"]
    assert "${{" not in step["run"]  # the event reaches the shell through env only


def test_the_gate_reads_each_fact_from_the_right_field() -> None:
    """The commenter's association, not the issue author's; repository IDs, not names."""
    (step,) = _job("gate")["steps"]
    assert step["env"] == {
        "EVENT": "${{ github.event_name }}",
        "SENDER_TYPE": "${{ github.event.sender.type }}",
        "BASE_REPO_ID": "${{ github.repository_id }}",
        "HEAD_REPO_ID": "${{ github.event.pull_request.head.repo.id }}",
        "ON_PR": "${{ github.event.issue.pull_request && 'true' || 'false' }}",
        "ASSOCIATION": "${{ github.event.comment.author_association }}",
        "BODY": "${{ github.event.comment.body }}",
        "PR_NUMBER": "${{ github.event.issue.number }}",
        "GH_TOKEN": "${{ github.token }}",
        "GH_REPO": "${{ github.repository }}",
    }


def test_exactly_one_zizmor_ignore_on_the_comment_trigger_with_a_reason() -> None:
    lines = (WORKFLOWS / NAME).read_text(encoding="utf-8").splitlines()
    ignores = [n for n, line in enumerate(lines) if "zizmor: ignore" in line]
    assert len(ignores) == 1
    (n,) = ignores
    assert lines[n] == "  issue_comment: # zizmor: ignore[dangerous-triggers]"
    reason = " ".join(ln.strip("# ") for ln in lines[n - 2 : n])
    for word in (
        "no checkout",
        "owners, members and collaborators only",
        "forks skipped",
        "gated `review` job",
    ):
        assert word in reason, word


SAME, FORK, PR = "111", "222", "7"


def _run_gate(
    tmp_path: Path, api_head: str, event: dict[str, str], gh_rc: int = 0
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the gate's step locally, with a stub `gh` that answers the head repository id."""
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    gh = stub / "gh"
    gh.write_text(
        f'#!{BASH}\necho "$*" >> "{tmp_path}/gh.log"\necho "{api_head}"\nexit {gh_rc}\n',
        encoding="utf-8",
    )
    gh.chmod(0o755)
    out = tmp_path / "out"
    out.write_text("", encoding="utf-8")
    env = {
        "PATH": f"{stub}:{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(out),
        "EVENT": "issue_comment",
        "SENDER_TYPE": "User",
        "BASE_REPO_ID": SAME,
        "HEAD_REPO_ID": "",
        "ON_PR": "true",
        "ASSOCIATION": "COLLABORATOR",
        "BODY": "/review",
        "PR_NUMBER": PR,
        "GH_TOKEN": "t",
        "GH_REPO": "o/r",
        **event,
    }
    (step,) = _job("gate")["steps"]
    done = subprocess.run(
        [BASH, "-c", step["run"]], env=env, capture_output=True, text=True, check=False
    )
    return done, out.read_text(encoding="utf-8").strip()


def _gate(tmp_path: Path, api_head: str = SAME, **event: str) -> tuple[str, str]:
    done, run = _run_gate(tmp_path, api_head, event)
    assert done.returncode == 0, done.stderr
    return run, done.stdout


@pytest.mark.parametrize(
    ("event", "api_head"),
    [
        ({"BODY": "/review"}, SAME),
        ({"BODY": "/ask why this lock?\nmore"}, SAME),
        ({"ASSOCIATION": "OWNER", "BODY": "/improve"}, SAME),
        ({"BODY": "/ask what does a--b do here?"}, SAME),  # no option token: `--` mid-word
        ({"BODY": "/ask is -- a separator?"}, SAME),  # a bare `--` names no option
        ({"EVENT": "pull_request", "HEAD_REPO_ID": SAME, "ON_PR": "false", "BODY": ""}, FORK),
    ],
)
def test_the_gate_lets_a_same_repository_pull_request_or_command_through(
    tmp_path: Path, event: dict[str, str], api_head: str
) -> None:
    assert _gate(tmp_path, api_head, **event)[0] == "run=true"


@pytest.mark.parametrize(
    ("case", "event", "api_head"),
    [
        ("not a pull request", {"ON_PR": "false"}, SAME),
        ("an outsider", {"ASSOCIATION": "CONTRIBUTOR"}, SAME),
        ("no association", {"ASSOCIATION": "NONE"}, SAME),
        ("a bot", {"SENDER_TYPE": "Bot"}, SAME),
        (
            "a bot's pull request",
            {"EVENT": "pull_request", "HEAD_REPO_ID": SAME, "SENDER_TYPE": "Bot"},
            SAME,
        ),
        ("a fork's pull request, commented", {}, FORK),
        ("a deleted fork, commented", {}, ""),
        ("a fork's pull request", {"EVENT": "pull_request", "HEAD_REPO_ID": FORK}, SAME),
        ("an unknown command", {"BODY": "/approve"}, SAME),
        ("a command not at the start", {"BODY": "please /review"}, SAME),
        ("a prefix of a command", {"BODY": "/reviewer"}, SAME),
        ("another event", {"EVENT": "push"}, SAME),
        (
            "an option override",
            {"BODY": "/describe --pr_description.publish_description_as_comment=false"},
            SAME,
        ),
        ("an option on a later line", {"BODY": "/review\n--pr_reviewer.extra=x"}, SAME),
        ("an option after a tab", {"BODY": "/improve\t--config.model=other"}, SAME),
        (
            "an option in an ask",
            {"BODY": "/ask why? --pr_description.generate_ai_title=true"},
            SAME,
        ),
    ],
)
def test_the_gate_says_no(tmp_path: Path, case: str, event: dict[str, str], api_head: str) -> None:
    run, stdout = _gate(tmp_path, api_head, **event)
    assert run == "run=false", case
    assert "::notice::PR-Agent skipped: " in stdout, case


def test_the_gate_asks_the_api_only_for_an_accepted_command(tmp_path: Path) -> None:
    _gate(tmp_path, SAME, ASSOCIATION="NONE")
    assert not (tmp_path / "gh.log").exists()
    _gate(tmp_path, SAME)
    assert (
        (tmp_path / "gh.log").read_text(encoding="utf-8").startswith(f"api repos/o/r/pulls/{PR} ")
    )


def test_the_gate_fails_closed_when_the_api_call_fails(tmp_path: Path) -> None:
    done, run = _run_gate(tmp_path, SAME, {}, gh_rc=1)
    assert done.returncode != 0
    assert "run=true" not in run


def test_the_review_job_has_exactly_the_three_grants_and_its_own_environment() -> None:
    assert _wf()["permissions"] == {}
    job = _job("review")
    assert job["permissions"] == {
        "contents": "read",
        "pull-requests": "write",
        "issues": "write",
    }
    assert job["environment"] == "ai-review"
    assert isinstance(job["timeout-minutes"], int)


def test_advisory_by_name_and_never_a_required_check() -> None:
    assert _job("review")["name"].endswith("(advisory)")
    doc = (REPO / "wiki" / "github-repository-settings.md").read_text(encoding="utf-8")
    required = doc.split("## Required status checks", 1)[1].split("\n## ", 1)[0]
    assert "PR-Agent" not in required
    advisory = doc.split("## Advisory checks (never required)", 1)[1].split("\n## ", 1)[0]
    for key in ("gate", "review"):
        assert f"- `{_job(key)['name']}`" in advisory, key


def test_one_step_the_digest_pinned_image_no_checkout_and_only_the_model_key() -> None:
    (step,) = _job("review")["steps"]
    assert IMAGE.match(step["uses"]), step["uses"]
    assert unpinned_uses(WORKFLOWS / NAME) == []
    assert step["env"]["GITHUB_TOKEN"] == "${{ github.token }}"  # noqa: S105 - an expression
    secrets = re.findall(r"secrets\.(\w+)", (WORKFLOWS / NAME).read_text(encoding="utf-8"))
    assert secrets == ["ANTHROPIC_KEY"]
    assert "actions/checkout" not in (WORKFLOWS / NAME).read_text(encoding="utf-8")
    assert not [s for k in _wf()["jobs"] for s in _job(k)["steps"] if "with" in s]


def test_renovate_versions_only_the_github_action_tags_of_the_image() -> None:
    (step,) = _job("review")["steps"]
    found = IMAGE.match(step["uses"])
    assert found
    rule = next(
        r for r in renovate()["packageRules"] if r.get("matchPackageNames") == ["pragent/pr-agent"]
    )
    assert rule["pinDigests"] is True
    versioning = re.fullmatch(r"regex:(.+)", rule["versioning"])
    assert versioning
    pattern = _compile(versioning[1])
    assert pattern.fullmatch(found["tag"])
    for other in (
        "github_action",
        "0.50.0",
        "latest",
        "0.50.0-gitlab_webhook",
        "0.50-github_action",
    ):
        assert not pattern.fullmatch(other), other


def test_the_settings_use_claude_and_turn_approval_and_thread_resolving_off() -> None:
    cfg = tomllib.loads((REPO / ".pr_agent.toml").read_text(encoding="utf-8"))
    model = cfg["config"]["model"]
    # Both are in PR-Agent's model registry at the pinned release.
    assert model == "anthropic/claude-opus-5-5"
    assert cfg["config"]["fallback_models"] == ["anthropic/claude-sonnet-5-5"]
    assert cfg["config"]["enable_auto_approval"] is False
    assert cfg["pr_questions"]["resolve_threads"] is False
    assert cfg["gitlab"]["resolve_outdated_inline_threads"] is False
    assert cfg["gitlab"]["auto_resolve_fixed_inline_threads"] is False
    assert cfg["pr_update_changelog"]["push_changelog_changes"] is False
    assert cfg["config"]["restricted_mode"] is True


def test_the_settings_keep_pr_agent_to_comments_and_issue_text_out() -> None:
    """The token could rewrite the title, body (the squash commit's message) and labels."""
    cfg = tomllib.loads((REPO / ".pr_agent.toml").read_text(encoding="utf-8"))
    assert cfg["pr_description"] == {
        "publish_description_as_comment": True,
        "generate_ai_title": False,
        "publish_labels": False,
    }
    assert cfg["pr_reviewer"] == {
        "enable_review_labels_security": False,
        "enable_review_labels_effort": False,
        "require_ticket_analysis_review": False,
    }
    assert cfg["config"]["extract_issue_from_branch"] is False


def test_the_switch_and_the_secret_are_in_the_single_list() -> None:
    table = (REPO / "wiki" / "platform-setup.md").read_text(encoding="utf-8")
    assert "| `PR_AGENT_ENABLED` | variable | repository |" in table
    assert "| `ANTHROPIC_KEY` | **secret** | environment `ai-review`" in table


def test_renovate_moves_the_release_commit_line_with_the_image() -> None:
    (step,) = _job("review")["steps"]
    found = IMAGE.match(step["uses"])
    assert found
    (manager,) = [
        m
        for m in renovate()["customManagers"]
        if any("pr-agent" in f for f in m["managerFilePatterns"])
    ]
    assert manager["datasourceTemplate"] == "github-tags"
    assert manager["depNameTemplate"] == "The-PR-Agent/pr-agent"
    (pattern,) = manager["matchStrings"]
    line = _compile(pattern).search((WORKFLOWS / NAME).read_text(encoding="utf-8"))
    assert line
    assert line["currentValue"] == "v" + found["tag"].removesuffix("-github_action")
    assert re.fullmatch(r"[0-9a-f]{40}", line["currentDigest"])
    group = next(r for r in renovate()["packageRules"] if r.get("groupName") == "pr-agent")
    assert set(group["matchPackageNames"]) == {"pragent/pr-agent", "The-PR-Agent/pr-agent"}
