# your-service-name — adaptable service template (see wiki/getting-started.md)
"""A production release must not render with ``ENV`` left at its dev default (validators off)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "your-service-name"
HELM = shutil.which("helm") or str(ROOT / ".tools" / "bin" / "helm")

pytestmark = pytest.mark.skipif(not Path(HELM).exists(), reason="helm is not installed")


def _template(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [HELM, "template", "svc", str(CHART), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_production_environment_with_dev_env_is_refused() -> None:
    """`environment: production` without `values.production.yaml` leaves ENV=dev."""
    result = _template("--set", "environment=production")
    assert result.returncode != 0
    assert "config.ENV" in result.stderr


def test_production_values_render() -> None:
    result = _template("-f", str(CHART / "values.production.yaml"))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("env", ["production", "prod"])
def test_production_environment_with_a_production_env_renders(env: str) -> None:
    result = _template("--set", "environment=production", "--set", f"config.ENV={env}")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("values", ["values.dev.yaml", "values.qa.yaml", "values.local.yaml"])
def test_other_environments_render(values: str) -> None:
    assert _template("-f", str(CHART / values)).returncode == 0
