"""scripts/ci/chart-predates-bootstrap.sh: does a chart (a pull request's base, in the kind job)
still render its namespaces or the app's Role itself? Decided from the chart's render, both ways."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.backend.test_chart_production_guard import find_helm, require_helm

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts" / "ci" / "chart-predates-bootstrap.sh"
CHART = ROOT / "charts" / "your-service-name"
BASH: str = shutil.which("bash") or "/usr/bin/bash"


@pytest.fixture(scope="module")
def helm() -> str:
    return require_helm(find_helm(), ci=bool(os.environ.get("CI")))


def run(helm: str, chart: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PATH": f"{Path(helm).parent}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run(
        [BASH, str(SCRIPT), str(chart)], capture_output=True, text=True, env=env, check=False
    )


def chart_copy(tmp_path: Path, key: str) -> Path:
    copy = tmp_path / "chart"
    shutil.copytree(CHART, copy)
    values = copy / "values.yaml"
    text = values.read_text(encoding="utf-8")
    pattern = rf"(^{key}:\n(?:  .*\n)*?)  create: false"
    new, count = re.subn(pattern, r"\1  create: true", text, count=1, flags=re.MULTILINE)
    assert count == 1, key
    values.write_text(new, encoding="utf-8")
    return copy


def test_the_current_chart_does_not_predate_the_bootstrap(helm: str) -> None:
    result = run(helm, CHART)
    assert result.returncode == 1, result.stderr
    assert "renders no Namespace, Role or RoleBinding" in result.stdout


@pytest.mark.parametrize("key", ["namespaces", "rbac"])
def test_a_chart_that_renders_namespaces_or_the_app_role_predates_it(
    helm: str, tmp_path: Path, key: str
) -> None:
    result = run(helm, chart_copy(tmp_path, key))
    assert result.returncode == 0, result.stderr
    assert "predates the bootstrap" in result.stdout


def test_a_chart_that_does_not_render_is_an_error(helm: str, tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    shutil.copytree(CHART, broken)
    (broken / "templates" / "broken.yaml").write_text('{{ fail "boom" }}\n', encoding="utf-8")
    result = run(helm, broken)
    assert result.returncode == 2  # noqa: PLR2004
    assert "boom" in result.stderr
