"""The `Adopted copy` job: an adopted copy of the tree runs its own suites, read-only, uncached."""

from __future__ import annotations

import json

from tests.supply_chain._workflows import workflow

JOB = workflow("ci.yml")["jobs"]["adopted"]
STEPS = JOB["steps"]
TEXT = json.dumps(JOB)


def _runs() -> str:
    return "\n".join(s.get("run", "") for s in STEPS)


def test_it_always_runs_behind_the_gate_with_a_read_only_token_and_its_own_budget() -> None:
    assert "if" not in JOB
    assert "adopted" in workflow("ci.yml")["jobs"]["ci-ok"]["needs"]
    assert JOB["permissions"] == {"contents": "read"}
    assert 0 < JOB["timeout-minutes"] <= 30  # noqa: PLR2004 (the measured run, with headroom)


def test_it_reads_no_secret_and_uses_no_cache() -> None:
    assert "secrets." not in TEXT
    assert "github.token" not in TEXT
    assert "actions/cache" not in TEXT
    setup_node = next(s for s in STEPS if s.get("uses", "").startswith("actions/setup-node@"))
    assert setup_node["with"]["package-manager-cache"] is False
    assert not any(s.get("uses", "").startswith("./.github/actions/") for s in STEPS)


def test_the_copy_is_adopted_after_the_tools_and_runs_its_suites() -> None:
    names = [s.get("name", "") for s in STEPS]
    assert names.index("Install the pinned tools") < names.index("Adopt a copy")
    runs = _runs()
    assert 'bash scripts/ci/adopted-copy.sh "$RUNNER_TEMP/adopted"' in runs
    assert "uv sync --locked" in runs
    lint = "make ruff-format-check ruff-lint mypy-check env-docs-check"
    for command in (lint, "make test", "make chart-unittest", "npm run lint", "npm run typecheck"):
        assert command in runs, command


def test_the_docs_site_runs_off_pull_requests_only() -> None:
    site = [s for s in STEPS if "/adopted/docs-site" in s.get("working-directory", "")]
    assert site
    assert all(s["if"] == "github.event_name != 'pull_request'" for s in site)
    assert any("npm run build" in s["run"] for s in site)
    rest = [s for s in STEPS if s not in site]
    assert all("if" not in s for s in rest)
