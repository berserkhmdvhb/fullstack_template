"""The repo-wide shell style gate: the `Shellcheck` step of the `repo` job in ci.yml.

One gate that shellchecks every git-tracked shell script (*.sh + *.bats) at
shellcheck's DEFAULT level (error + warning + info + style): green only when
every script is clean. It is blocking (no `continue-on-error`, no `if:`), and
runs on pull requests, pushes to main and `v*` tags, like the other lint gates.

The discovery + lint logic lives in scripts/ci/shell-lint.sh, invoked as
`bash scripts/ci/shell-lint.sh`: it uses `mapfile`, which is bash-only, so it must run
as a script under bash and not inline in a `run:` block. These tests pin that
contract so the gate cannot silently regress.
"""

from __future__ import annotations

from typing import Any

from tests.supply_chain._workflows import REPO, workflow

LINT_SCRIPT = REPO / "scripts" / "ci" / "shell-lint.sh"


def job() -> dict[str, Any]:
    jobs = workflow("ci.yml")["jobs"]
    assert "repo" in jobs, f"the repo job must be defined in ci.yml (got {sorted(jobs)})"
    return dict(jobs["repo"])


def step() -> dict[str, Any]:
    found = [s for s in job()["steps"] if s.get("name") == "Shellcheck"]
    assert len(found) == 1, "exactly one Shellcheck step"
    return found[0]


# --- contract ----------------------------------------------------------------


def test_shellcheck_job_is_a_blocking_gate() -> None:
    j = job()
    # Blocking: nothing may soften a failure, on the job or on any of its steps.
    assert "continue-on-error" not in j
    assert all("continue-on-error" not in s for s in j["steps"])
    assert "continue-on-error" not in step()


def test_shellcheck_runs_on_prs_pushes_and_tags_unconditionally() -> None:
    on = workflow("ci.yml")["on"]
    assert "pull_request" in on, "shellcheck must run on pull requests"
    assert on["push"]["branches"] == ["main"], "shellcheck must run on pushes to main"
    assert on["push"]["tags"] == ["v*"], "shellcheck must run on tags"
    assert "if" not in job(), "the repo job has no condition: it runs on every event"
    assert "if" not in step(), "the Shellcheck step has no condition"


def test_shellcheck_lints_via_a_bash_script_not_inline() -> None:
    # The discovery uses `mapfile` (bash-only): it lives in a script file run as
    # `bash scripts/ci/shell-lint.sh`, never inline in the step.
    run = step()["run"]
    assert run.strip() == "bash scripts/ci/shell-lint.sh"
    assert "mapfile" in LINT_SCRIPT.read_text(), (
        "the bash-only discovery must live in scripts/ci/shell-lint.sh, not inline in the job"
    )


def test_shell_lint_script_exists_and_is_bash() -> None:
    # The gate's logic is a real, lintable script, itself a tracked *.sh, so it is
    # linted by the very gate it backs (and must be shellcheck-clean).
    assert LINT_SCRIPT.is_file(), f"{LINT_SCRIPT} must exist"
    assert LINT_SCRIPT.read_text().startswith("#!/usr/bin/env bash"), (
        "scripts/ci/shell-lint.sh must declare a bash shebang"
    )
