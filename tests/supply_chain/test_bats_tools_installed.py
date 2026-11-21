"""The `repo` job runs `bats tests/scripts/`: it must install every pinned tool those tests need."""

from __future__ import annotations

import re

from tests.supply_chain._workflows import REPO, workflow


def _repo_job_tools() -> set[str]:
    runs = "\n".join(str(s.get("run", "")) for s in workflow("ci.yml")["jobs"]["repo"]["steps"])
    return {
        t for m in re.finditer(r"scripts/tools\.sh install ([^\n]+)", runs) for t in m[1].split()
    }


def _needed_by_bats() -> dict[str, set[str]]:
    """Per bats file: the tools its scripts name in a `scripts/tools.sh install <tool>` hint."""
    needed: dict[str, set[str]] = {}
    for bats in sorted((REPO / "tests" / "scripts").glob("*.bats")):
        text = bats.read_text(encoding="utf-8")
        tools: set[str] = set()
        for rel in set(re.findall(r"scripts/[\w/.-]+\.sh", text)):
            script = REPO / rel
            if script.is_file() and script.name != "tools.sh":
                for m in re.finditer(
                    r"scripts/tools\.sh install ([\w-]+)", script.read_text("utf-8")
                ):
                    tools.add(m[1])
        for m in re.finditer(r"scripts/tools\.sh install ([\w-]+)", text):
            tools.add(m[1])
        if tools:
            needed[bats.name] = tools
    return needed


def test_the_scripts_under_bats_are_found() -> None:
    needed = _needed_by_bats()
    assert needed["oasdiff.bats"] == {"oasdiff"}
    assert needed["squawk.bats"] == {"squawk"}


def test_the_repo_job_installs_every_tool_the_bats_tests_need() -> None:
    installed = _repo_job_tools()
    for bats, tools in _needed_by_bats().items():
        assert tools <= installed, (
            f"{bats} needs {sorted(tools - installed)}; the repo job installs {sorted(installed)}"
        )


def test_the_tools_cache_key_names_the_installed_tools() -> None:
    steps = workflow("ci.yml")["jobs"]["repo"]["steps"]
    keys = [s["with"]["key"] for s in steps if "actions/cache" in s.get("uses", "")]
    assert keys
    for tool in _repo_job_tools():
        assert all(tool in k for k in keys), (tool, keys)
