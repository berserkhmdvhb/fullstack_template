"""The Lighthouse budget check turns a budget miss into warnings, and fails only on a tool error."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci" / "lighthouse-assert.sh"

TOOL_ERROR_CODE = 3

MISS = {
    "name": "maxNumericValue",
    "expected": 350000,
    "actual": 400000,
    "operator": "<=",
    "passed": False,
    "auditProperty": "script.size",
    "auditId": "resource-summary",
    "url": "http://127.0.0.1:3419/items",
}


def _run(
    tmp_path: Path, *, exit_code: int, results: list[dict[str, object]] | None
) -> tuple[subprocess.CompletedProcess[str], str]:
    """Run the script with a fake ``npx``: write ``results``, then exit with ``exit_code``."""
    work = tmp_path / "frontend"
    work.mkdir()
    fake = tmp_path / "npx"
    payload = json.dumps(results) if results is not None else ""
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "mkdir -p .lighthouseci\n"
        f"if [[ -n '{payload}' ]]; then\n"
        f"  printf '%s' '{payload}' > .lighthouseci/assertion-results.json\n"
        "fi\n"
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    summary = tmp_path / "summary.md"
    result = subprocess.run(
        ["bash", str(SCRIPT)],  # noqa: S607 - bash from PATH, as CI does
        cwd=work,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "GITHUB_STEP_SUMMARY": str(summary),
        },
    )
    return result, summary.read_text(encoding="utf-8") if summary.exists() else ""


def test_met_budgets_pass_with_a_summary_line(tmp_path: Path) -> None:
    result, summary = _run(tmp_path, exit_code=0, results=[])
    assert result.returncode == 0
    assert "all budgets met" in summary
    assert "::warning" not in result.stdout


def test_a_budget_miss_warns_and_does_not_fail(tmp_path: Path) -> None:
    result, summary = _run(tmp_path, exit_code=1, results=[MISS])
    assert result.returncode == 0, result.stderr
    warning = "::warning title=Lighthouse (advisory)::"
    assert f"{warning}http://127.0.0.1:3419/items resource-summary script.size" in result.stdout
    assert "found 400000" in result.stdout
    assert "1 budget finding(s)" in summary


@pytest.mark.parametrize("results", [None, [], [{**MISS, "passed": True}]])
def test_a_tool_error_fails_the_job(
    tmp_path: Path, results: list[dict[str, object]] | None
) -> None:
    result, summary = _run(tmp_path, exit_code=1, results=results)
    assert result.returncode == 1
    assert "tool error" in result.stderr
    assert "::warning" not in result.stdout
    assert "budget finding" not in summary


def test_the_exit_code_of_a_tool_error_is_kept(tmp_path: Path) -> None:
    result, _ = _run(tmp_path, exit_code=TOOL_ERROR_CODE, results=None)
    assert result.returncode == TOOL_ERROR_CODE
