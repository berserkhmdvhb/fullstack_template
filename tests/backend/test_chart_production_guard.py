# your-service-name — adaptable service template (see wiki/getting-started.md)
"""A production release must not render with ``ENV`` left at its dev default (validators off)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "your-service-name"


def find_helm(root: Path = ROOT) -> str | None:
    """The pinned helm (``.tools/bin/helm``) when installed, else the one on PATH."""
    pinned = root / ".tools" / "bin" / "helm"
    return str(pinned) if pinned.exists() else shutil.which("helm")


def require_helm(helm: str | None, *, ci: bool) -> str:
    """The helm binary; a missing one skips locally but must fail loudly in CI."""
    if helm:
        return helm
    msg = "helm is not installed (scripts/tools.sh install helm4); the production guard is untested"
    if ci:
        pytest.fail(msg)
    pytest.skip(msg)


@pytest.fixture(scope="module")
def helm() -> str:
    return require_helm(find_helm(), ci=bool(os.environ.get("CI")))


def test_a_missing_helm_fails_in_ci_and_skips_locally() -> None:
    with pytest.raises(pytest.fail.Exception, match="not installed"):
        require_helm(None, ci=True)
    with pytest.raises(pytest.skip.Exception):
        require_helm(None, ci=False)
    assert require_helm("/x/helm", ci=True) == "/x/helm"


def _template(helm: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [helm, "template", "svc", str(CHART), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_production_environment_with_dev_env_is_refused(helm: str) -> None:
    """`environment: production` without `values.production.yaml` leaves ENV=dev."""
    result = _template(helm, "--set", "environment=production")
    assert result.returncode != 0
    assert "config.ENV" in result.stderr


def test_production_values_render(helm: str) -> None:
    result = _template(helm, "-f", str(CHART / "values.production.yaml"))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("env", ["production", "prod"])
def test_production_environment_with_a_production_env_renders(helm: str, env: str) -> None:
    result = _template(helm, "--set", "environment=production", "--set", f"config.ENV={env}")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("values", ["values.dev.yaml", "values.qa.yaml", "values.local.yaml"])
def test_other_environments_render(helm: str, values: str) -> None:
    assert _template(helm, "-f", str(CHART / values)).returncode == 0
