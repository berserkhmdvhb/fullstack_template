"""`make rules-test` checks the chart's rules with the pinned promtool, and is not vacuous.

scripts/ci/rules-test.sh renders both monitoring flavours, runs `promtool check rules` on the
extracted groups and `promtool test rules` on charts/your-service-name/tests/rules. A broken rule
(a syntax error, or a burn-rate threshold that no longer matches the unit tests) must fail it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
CHART = ROOT / "charts" / "your-service-name"
WORKFLOW = ROOT / ".github" / "workflows" / "reusable-chart.yml"
TOOLS = ROOT / ".tools" / "bin"
MAKE: str = shutil.which("make") or "/usr/bin/make"


@pytest.fixture(scope="module")
def tools() -> None:
    missing = [t for t in ("helm", "promtool", "yq") if not (TOOLS / t).exists()]
    if missing:
        msg = f"{', '.join(missing)} not installed (scripts/tools.sh install helm4 promtool yq)"
        if os.environ.get("CI"):
            pytest.fail(msg)
        pytest.skip(msg)


def _rules_test(chart: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [MAKE, "--no-print-directory", "rules-test"],
        cwd=ROOT,
        env={**os.environ, "CHART": str(chart)},
        capture_output=True,
        text=True,
        check=False,
    )


def _broken_copy(tmp: Path, old: str, new: str) -> Path:
    chart = tmp / "chart"
    shutil.copytree(CHART, chart)
    helpers = chart / "templates" / "_helpers.tpl"
    text = helpers.read_text(encoding="utf-8")
    assert old in text
    helpers.write_text(text.replace(old, new, 1), encoding="utf-8")
    return chart


@pytest.mark.usefixtures("tools")
def test_a_rule_with_a_syntax_error_fails_promtool_check(tmp_path: Path) -> None:
    chart = _broken_copy(
        tmp_path, "        > 0.05\n      for: 10m", "        > 0.05 +\n      for: 10m"
    )
    result = _rules_test(chart)
    assert result.returncode != 0
    assert "promtool check rules" in result.stdout
    assert "test rules" not in result.stdout


@pytest.mark.usefixtures("tools")
def test_a_wrong_burn_rate_threshold_fails_the_unit_tests(tmp_path: Path) -> None:
    chart = _broken_copy(tmp_path, '(list "1h" "5m" "14.4")', '(list "1h" "5m" "144")')
    result = _rules_test(chart)
    assert result.returncode != 0
    assert "SLOAvailabilityBurnFast" in result.stdout + result.stderr


def test_the_make_target_runs_the_script() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert re.search(r"^rules-test:\n\tscripts/ci/rules-test\.sh$", makefile, re.MULTILINE)


def test_the_chart_workflow_installs_promtool_then_runs_the_rules_offline() -> None:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["chart"]["steps"]
    runs = [str(s.get("run", "")) for s in steps]
    install = next(i for i, r in enumerate(runs) if "scripts/tools.sh install helm4" in r)
    assert "promtool" in runs[install].split()
    check = next(i for i, r in enumerate(runs) if "scripts/ci/rules-test.sh" in r)
    assert install < check
    assert "install" not in runs[check]


def test_the_unit_job_installs_promtool_for_these_tests() -> None:
    backend = ROOT / ".github" / "workflows" / "reusable-backend.yml"
    steps = yaml.safe_load(backend.read_text(encoding="utf-8"))["jobs"]["unit"]["steps"]
    runs = [str(s.get("run", "")) for s in steps]
    install = next(r for r in runs if "scripts/tools.sh install helm4" in r)
    assert {"promtool", "yq"} <= set(install.split())
