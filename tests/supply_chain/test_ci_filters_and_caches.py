"""CI wiring that must stay consistent: the path filter, the tools caches, runners without uv."""

from __future__ import annotations

import re
from typing import Any

from tests.supply_chain._workflows import ACTIONS, REPO, WORKFLOWS, all_files, load, workflow

INSTALL = re.compile(r"scripts/tools\.sh install ([^\n|;&]+)")


def _uv_tools() -> set[str]:
    rows = (REPO / "tools" / "pins.tsv").read_text(encoding="utf-8").splitlines()
    return {r.split("\t")[0] for r in rows if r and not r.startswith("#") and "\tuv:" in r}


def _jobs(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return doc["jobs"] if "jobs" in doc else {"action": doc["runs"]}


def _installed(job: dict[str, Any]) -> set[str]:
    runs = "\n".join(s.get("run", "") for s in job.get("steps", []))
    return {name for m in INSTALL.findall(runs) for name in m.split()}


def test_no_job_installs_a_uv_tool_without_provisioning_uv() -> None:
    # tools.sh installs a `uv:` pin with the uv binary, which GitHub runners do not ship.
    uv_tools = _uv_tools()
    assert {"yamllint", "yamale", "zizmor"} <= uv_tools
    for path in all_files():
        for key, job in _jobs(load(path)).items():
            bad = _installed(job) & uv_tools
            provisioned = any("setup-uv" in s.get("uses", "") for s in job.get("steps", []))
            assert not bad or provisioned, f"{path.name}:{key} installs {sorted(bad)} without uv"


def test_the_backend_path_filter_covers_every_ruff_root() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    roots = re.search(r"^RUFF_PATHS := (.+)$", makefile, re.MULTILINE)
    assert roots
    steps = workflow("ci.yml")["jobs"]["changes"]["steps"]
    run = next(s["run"] for s in steps if s.get("id") == "filter")
    pattern = next(m for m in re.findall(r"grep -Eq '([^']+)'", run) if "pyproject" in m)
    for root in roots.group(1).split():
        probe = root.removeprefix("./") + "x.py"
        assert re.search(pattern, probe), f"the backend filter misses {root}"


def test_jobs_that_save_a_tools_cache_use_distinct_keys_per_tool_set() -> None:
    seen: dict[str, tuple[str, frozenset[str]]] = {}
    for path in WORKFLOWS.glob("*.yml"):
        for name, job in _jobs(load(path)).items():
            for step in job.get("steps", []):
                if "actions/cache/save" not in step.get("uses", ""):
                    continue
                key = step["with"]["key"]
                tools = frozenset(_installed(job))
                other = seen.setdefault(key, (f"{path.name}:{name}", tools))
                assert other[1] == tools, (
                    f"{path.name}:{name} shares cache key {key} with {other[0]}"
                )


def test_every_actions_dir_is_still_scanned() -> None:
    assert list(ACTIONS.glob("*/action.yml"))


def test_every_tool_cache_key_hashes_the_pins_the_mise_config_and_its_lock() -> None:
    """A tool cache is only as good as its key: a bump in any of the three files must miss it."""
    seen = 0
    for path in all_files():
        for key in re.findall(r"key: (tools-[^\n]+)", path.read_text(encoding="utf-8")):
            seen += 1
            files = re.search(r"hashFiles\(([^)]*)\)", key)
            assert files, f"{path.name}: {key}"
            for name in ("tools/pins.tsv", "mise.toml", "mise.lock"):
                assert f"'{name}'" in files[1], f"{path.name}: {key} lacks {name}"
    assert seen >= 4  # noqa: PLR2004 (ci.yml twice, reusable-backend twice)
