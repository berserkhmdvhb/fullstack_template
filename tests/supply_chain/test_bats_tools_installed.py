"""The `repo` job runs `bats tests/scripts/`: it must install every pinned tool those tests use."""

from __future__ import annotations

import re
from pathlib import Path

from tests.supply_chain._workflows import REPO, workflow

# Tools the bats tests mention but never run for real; each needs a reason.
STUBBED = {
    "kubectl": "the tests put a stub kubectl on PATH",
    "kind": "an awk variable in the migration helper, never the binary",
    "cosign": "named in an error message the scaffolder tests print",
    "python": "the backend virtualenv's interpreter, not a downloaded pin",
}
# A tool name in command position: line start, after a pipe, `;`, `&&`, `$(`, a backtick, or a
# keyword that runs its argument (`run`, `command -v`, `need_cmd`, ...).
_PREFIX = (
    r"(?:^[ \t]*|[|&;][ \t]*|\$\([ \t]*|`[ \t]*"
    r"|\b(?:run|exec|command -v|need_cmd|then|do|else)[ \t]+(?:![ \t]+)?)"
)
_SCRIPT_REF = re.compile(r"(?:scripts|infra)/[\w/.-]+\.sh")


def pinned_tools() -> set[str]:
    rows = (REPO / "tools" / "pins.tsv").read_text(encoding="utf-8").splitlines()
    return {r.split("\t")[0] for r in rows if r.strip() and not r.startswith("#")}


def _code(path: Path) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(ln for ln in lines if not ln.lstrip().startswith("#"))


def _scripts_of(text: str, root: Path) -> set[Path]:
    return {root / m for m in _SCRIPT_REF.findall(text) if (root / m).is_file()}


def tools_used(bats: Path, root: Path, tools: set[str]) -> set[str]:
    """Tools run by a bats file or the scripts it runs (directly or one script deep)."""
    text = _code(bats)
    scripts = _scripts_of(text, root)
    for script in set(scripts):
        scripts |= _scripts_of(_code(script), root)
    text += "\n".join(_code(s) for s in sorted(scripts) if s.name != "tools.sh")
    called = {
        t
        for t in tools
        if re.search(_PREFIX + re.escape(t) + r"(?=\s|$|[;|&])", text, re.MULTILINE)
    }
    # An install hint (`scripts/tools.sh install X`) names a tool even where it is not run.
    hinted = {
        t for t in tools if re.search(rf"scripts/tools\.sh install\b[^\n]*\b{re.escape(t)}\b", text)
    }
    return called | hinted


def not_installed(used: set[str], installed: set[str]) -> set[str]:
    """What the job must still install: stubbed tools are exempt."""
    return used - installed - set(STUBBED)


def repo_job_tools() -> set[str]:
    runs = "\n".join(str(s.get("run", "")) for s in workflow("ci.yml")["jobs"]["repo"]["steps"])
    return {
        t for m in re.finditer(r"scripts/tools\.sh install ([^\n]+)", runs) for t in m[1].split()
    }


def key_names(tool: str, key: str) -> bool:
    """The tool is one hyphen-delimited part of the cache key, not a substring of another word."""
    return f"-{tool}-" in f"-{key}-"


def test_every_bats_tool_is_installed_by_the_repo_job() -> None:
    installed = repo_job_tools()
    tools = pinned_tools()
    for bats in sorted((REPO / "tests" / "scripts").glob("*.bats")):
        missing = not_installed(tools_used(bats, REPO, tools), installed)
        assert not missing, (
            f"{bats.name} uses {sorted(missing)}; the repo job installs {sorted(installed)}"
        )


def test_the_found_tools_include_the_known_ones() -> None:
    tools = pinned_tools()
    assert "oasdiff" in tools_used(REPO / "tests/scripts/oasdiff.bats", REPO, tools)
    assert "squawk" in tools_used(REPO / "tests/scripts/squawk.bats", REPO, tools)


def test_every_stub_exemption_is_a_pinned_tool() -> None:
    assert set(STUBBED) <= pinned_tools() | {"python"}


def test_the_tools_cache_key_names_the_installed_tools() -> None:
    steps = workflow("ci.yml")["jobs"]["repo"]["steps"]
    keys = [s["with"]["key"] for s in steps if "actions/cache" in s.get("uses", "")]
    assert keys
    for tool in repo_job_tools():
        assert all(key_names(tool, k) for k in keys), (tool, keys)


# --- self-tests of the detection, on fixture text ---


def _fixture(tmp_path: Path, bats: str, script: str = "", other: str = "") -> Path:
    (tmp_path / "scripts" / "ci").mkdir(parents=True)
    (tmp_path / "scripts" / "ci" / "a.sh").write_text(script, encoding="utf-8")
    (tmp_path / "scripts" / "ci" / "b.sh").write_text(other, encoding="utf-8")
    path = tmp_path / "t.bats"
    path.write_text(bats, encoding="utf-8")
    return path


TOOLS = {"squawk", "yq", "oasdiff", "ct"}


def test_a_direct_call_without_an_install_hint_is_caught(tmp_path: Path) -> None:
    bats = _fixture(tmp_path, '@test "x" {\n  run yq -r . f.yaml\n}\n')
    assert tools_used(bats, tmp_path, TOOLS) == {"yq"}


def test_a_call_in_a_script_the_bats_runs_is_caught(tmp_path: Path) -> None:
    bats = _fixture(tmp_path, "run bash scripts/ci/a.sh\n", script="set -e\nsquawk --version\n")
    assert tools_used(bats, tmp_path, TOOLS) == {"squawk"}


def test_a_call_one_script_deeper_is_caught(tmp_path: Path) -> None:
    bats = _fixture(
        tmp_path,
        "run bash scripts/ci/a.sh\n",
        script='bash "$ROOT/scripts/ci/b.sh"\n',
        other="x=$(oasdiff breaking a b)\n",
    )
    assert tools_used(bats, tmp_path, TOOLS) == {"oasdiff"}


def test_prose_comments_and_strings_are_not_calls(tmp_path: Path) -> None:
    text = '# yq is great\n@test "squawk gate" {\n  echo "uses oasdiff here"\n}\n'
    assert tools_used(_fixture(tmp_path, text), tmp_path, TOOLS) == set()


def test_an_install_hint_counts(tmp_path: Path) -> None:
    bats = _fixture(
        tmp_path,
        "run bash scripts/ci/a.sh\n",
        script="echo 'run scripts/tools.sh install squawk'\n",
    )
    assert tools_used(bats, tmp_path, TOOLS) == {"squawk"}


def test_a_stubbed_tool_on_the_allowlist_passes() -> None:
    assert not_installed({"kubectl"}, {"yq"}) == set()
    assert not_installed({"kubectl", "oasdiff"}, {"yq"}) == {"oasdiff"}, "a real tool is not exempt"


def test_a_short_tool_name_does_not_match_inside_another_word() -> None:
    assert key_names("kyverno", "tools-repo-kyverno-squawk-Linux-abc")
    assert not key_names("ct", "tools-repo-kyverno-act-x-Linux")
    assert not key_names("yq", "tools-repo-yqx-Linux")
    assert key_names("pip-audit", "tools-pip-audit-Linux")
