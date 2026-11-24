"""The offline link check: lychee.toml stays offline and uncached; CI runs it as a blocking gate.

The repo job in ci.yml runs `make docs-links`, which feeds lychee the tracked Markdown only.
"""

from __future__ import annotations

import re
import tomllib

from tests.supply_chain._workflows import REPO, workflow


def test_the_config_keeps_the_check_offline_and_uncached() -> None:
    config = tomllib.loads((REPO / "lychee.toml").read_text(encoding="utf-8"))
    assert config["offline"] is True
    assert config["cache"] is False
    assert config["include_fragments"] == "anchor-only"


def test_the_make_target_checks_the_tracked_markdown_with_the_config() -> None:
    text = (REPO / "Makefile").read_text(encoding="utf-8")
    recipe = re.search(r"^docs-links:\n((?:\t.*\n)+)", text, re.MULTILINE)
    assert recipe, "the Makefile has no docs-links recipe"
    body = recipe[1]
    assert 'git ls-files "*.md"' in body
    assert "lychee --config lychee.toml" in body
    assert "--cache" not in body
    assert "--no-offline" not in body


def test_the_repo_job_runs_the_link_check_behind_ci_ok() -> None:
    jobs = workflow("ci.yml")["jobs"]
    repo = jobs["repo"]
    assert "repo" in jobs["ci-ok"]["needs"]
    assert "if" not in repo
    assert "continue-on-error" not in repo
    steps = [s for s in repo["steps"] if s.get("run") == "make docs-links"]
    assert len(steps) == 1, "the repo job runs `make docs-links` once"
    assert "if" not in steps[0]
    assert "continue-on-error" not in steps[0]
