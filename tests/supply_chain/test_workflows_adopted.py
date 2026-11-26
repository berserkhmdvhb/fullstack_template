"""The `Adopted copy` job: an adopted copy of the tree runs its own suites, read-only and uncached,
in the template only."""

from __future__ import annotations

import json

from tests.supply_chain._workflows import workflow

JOB = workflow("ci.yml")["jobs"]["adopted"]
STEPS = JOB["steps"]
TEXT = json.dumps(JOB)
TEMPLATE = "steps.detect.outputs.template == 'true'"
OFF_PULL_REQUESTS = f"{TEMPLATE} && github.event_name != 'pull_request'"


def _runs() -> str:
    return "\n".join(s.get("run", "") for s in STEPS)


def test_it_always_runs_behind_the_gate_with_a_read_only_token_and_its_own_budget() -> None:
    assert "if" not in JOB
    assert "adopted" in workflow("ci.yml")["jobs"]["ci-ok"]["needs"]
    assert JOB["permissions"] == {"contents": "read"}
    assert 0 < JOB["timeout-minutes"] <= 40  # noqa: PLR2004 (the measured run, plus the system-deps step's room)


def test_it_reads_no_secret_and_uses_no_cache() -> None:
    assert "secrets." not in TEXT
    assert "github.token" not in TEXT
    assert "actions/cache" not in TEXT
    setup_node = next(s for s in STEPS if s.get("uses", "").startswith("actions/setup-node@"))
    assert setup_node["with"]["package-manager-cache"] is False
    assert not any(s.get("uses", "").startswith("./.github/actions/") for s in STEPS)


def test_the_template_is_detected_first_and_every_later_step_runs_only_there() -> None:
    """In an adopted repository the job does nothing and passes: `CI OK` is never left waiting."""
    assert STEPS[0]["uses"].startswith("actions/checkout@")
    detect = STEPS[1]
    assert detect["id"] == "detect"
    assert "if" not in detect
    assert 'compgen -G "src/your_""service_name/*.py"' in detect["run"]
    assert "::notice::not the template" in detect["run"]
    assert all(s["if"] in (TEMPLATE, OFF_PULL_REQUESTS) for s in STEPS[2:]), [
        s.get("name") or s.get("uses")
        for s in STEPS[2:]
        if s.get("if") not in (TEMPLATE, OFF_PULL_REQUESTS)
    ]


def test_the_copy_is_adopted_with_the_tools_and_a_ruff_and_runs_its_suites() -> None:
    names = [s.get("name", "") for s in STEPS]
    adopt = names.index("Adopt a copy")
    assert names.index("Install the pinned tools") < adopt
    # The adoption sorts and formats the renamed Python: this tree's backend (its ruff) is installed
    # first, in the workspace, from the lock (scripts/ci/adopted-copy.sh refuses to run without it).
    before = [s for s in STEPS[:adopt] if "run" in s and "working-directory" not in s]
    assert any(s["run"].startswith(".tools/bin/uv sync --locked") for s in before), before
    assert all(
        s.get("env", {}).get("UV_PYTHON_DOWNLOADS") == "never"
        for s in STEPS
        if "uv sync" in s.get("run", "")
    )
    runs = _runs()
    assert 'bash scripts/ci/adopted-copy.sh "$RUNNER_TEMP/adopted"' in runs
    lint = "make ruff-format-check ruff-lint mypy-check env-docs-check"
    for command in (
        "bats tests/scripts/adopt-e2e.bats",
        lint,
        "make test",
        "make chart-unittest",
        "pnpm run lint",
        "pnpm run typecheck",
    ):
        assert command in runs, command


def test_the_copy_runs_the_shell_and_hardening_lints_on_every_event() -> None:
    step = next(s for s in STEPS if s.get("name") == "The copy's shell lint and CI hardening lint")
    assert step["if"] == TEMPLATE
    assert step["working-directory"] == "${{ runner.temp }}/adopted"
    assert "bash scripts/ci/shell-lint.sh" in step["run"]
    assert "bash infra/security/check-ci-hardening.sh" in step["run"]


def test_the_copy_runs_its_bats_suites_and_doc_links_off_pull_requests() -> None:
    step = next(s for s in STEPS if s.get("name") == "The copy's bats suites and doc links")
    assert step["if"] == OFF_PULL_REQUESTS
    assert step["working-directory"] == "${{ runner.temp }}/adopted"
    assert "bats tests/scripts/ infra/security/local/tests/" in step["run"]
    assert "make docs-links" in step["run"]


def test_the_tree_is_adopted_once_and_the_end_to_end_checks_reuse_it() -> None:
    runs = _runs()
    assert runs.count("scripts/ci/adopted-copy.sh") == 1
    e2e = next(s for s in STEPS if "adopt-e2e.bats" in s.get("run", ""))
    assert e2e["env"]["ADOPTED_COPY"] == "${{ runner.temp }}/adopted"
    adopt = next(s for s in STEPS if s.get("name") == "Adopt a copy")
    assert '| tee "$RUNNER_TEMP/adopted.log"' in adopt["run"]


def test_the_docs_site_runs_off_pull_requests_only() -> None:
    site = [s for s in STEPS if "/adopted/docs-site" in s.get("working-directory", "")]
    assert site
    assert all(s["if"] == OFF_PULL_REQUESTS for s in site)
    assert any("pnpm run build" in s["run"] for s in site)
    off = [s for s in STEPS if s.get("if") == OFF_PULL_REQUESTS]
    assert [s for s in off if s not in site] == [
        s
        for s in STEPS
        if s.get("name")
        in (
            "Install the tools for the copy's bats suites and doc links",
            "The copy's bats suites and doc links",
        )
    ]
    rest = [s for s in STEPS[2:] if s not in off]
    assert all(s["if"] == TEMPLATE for s in rest)


def test_a_step_that_pipes_fails_on_the_first_failing_command() -> None:
    """`shell: bash` runs `bash -eo pipefail`, so `script | tee` fails when the script does."""
    for step in STEPS:
        run = step.get("run", "")
        if "|" in run.replace("||", ""):
            assert step.get("shell") == "bash" or run.lstrip().startswith("set -o pipefail"), step
