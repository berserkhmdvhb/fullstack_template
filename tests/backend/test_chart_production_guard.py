# your-service-name — adaptable service template (see wiki/getting-started.md)
"""A production release must not render with ``ENV`` left at its dev default (validators off)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

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
    """Both spellings the backend reads as production pass the ENV guard (the other production
    rules take values.production.yaml, which meets them)."""
    result = _template(
        helm, "-f", str(CHART / "values.production.yaml"), "--set", f"config.ENV={env}"
    )
    assert result.returncode == 0, result.stderr


def test_production_without_an_auth_mode_is_refused(helm: str, tmp_path: Path) -> None:
    """An unset AUTH_MODE is the backend default, none: refused like an explicit none."""
    values = yaml.safe_load((CHART / "values.production.yaml").read_text(encoding="utf-8"))
    del values["config"]["AUTH_MODE"]
    path = tmp_path / "production-without-auth.yaml"
    path.write_text(yaml.safe_dump(values), encoding="utf-8")
    result = _template(helm, "-f", str(path))
    assert result.returncode != 0
    assert 'config.AUTH_MODE is "none"' in result.stderr


PRODUCTION_IMAGES = ("backend", "frontend", "waf", "clientImage", "cloudSqlProxy")
IMAGE_USE = re.compile(r'include "chart\.image" \(dict "image" \.Values\.([\w.]+)\)')


def test_the_digest_rule_lists_every_image_the_templates_render() -> None:
    """validate-values.yaml names each image it requires a digest for; a new image template that
    is not in that list would deploy by tag in production."""
    used = {
        m for f in (CHART / "templates").glob("*.yaml") for m in IMAGE_USE.findall(f.read_text())
    }
    assert used >= {"images.backend", "images.frontend", "images.waf", "postgresql.clientImage"}
    rules = (CHART / "templates" / "validate-values.yaml").read_text(encoding="utf-8")
    for path in sorted(used):
        assert f'"{path}" .Values.{path}' in rules, path


def test_every_container_of_a_production_render_runs_by_digest(helm: str) -> None:
    """values.production.yaml with every optional image on: no container runs by tag."""
    result = _template(
        helm,
        *("-f", str(CHART / "values.production.yaml")),
        *("--set", "waf.enabled=true", "--set", "cloudSqlProxy.enabled=true"),
        *(
            "--set",
            "cloudSqlProxy.egressCIDRs={199.36.153.8/30}",
            "--set",
            "config.DB_SSLMODE=disable",
        ),
    )
    assert result.returncode == 0, result.stderr
    images = re.findall(r"^\s*(?:- )?image: \"?([^\"\s]+)", result.stdout, re.MULTILINE)
    # backend, frontend, waf, the database client, the proxy
    assert len(set(images)) == len(PRODUCTION_IMAGES), images
    assert all(re.search(r"@sha256:[0-9a-f]{64}$", ref) for ref in images), images


@pytest.mark.parametrize("values", ["values.dev.yaml", "values.qa.yaml", "values.local.yaml"])
def test_other_environments_render(helm: str, values: str) -> None:
    assert _template(helm, "-f", str(CHART / values)).returncode == 0
