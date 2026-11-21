# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The Host the deploy scripts send through a port-forward is one the rendered chart accepts."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.backend.test_chart_production_guard import ROOT
from tests.backend.test_chart_request_limits import ENVS, rendered_config

# script -> the variable that names the release, as the script defines it
SCRIPTS = {
    "scripts/ci/deploy-env.sh": "SERVICE_NAME",
    "scripts/e2e.sh": "HELM_RELEASE",
}
RELEASE = "your-service-name"


def _backend_service(script: str, release_var: str) -> str:
    """Evaluate the script's own ``BACKEND_SERVICE=...`` line (no copy of it lives here)."""
    text = (ROOT / script).read_text(encoding="utf-8")
    match = re.search(r"^\s*(?:local\s+)?BACKEND_SERVICE=(.+)$", text, re.MULTILINE)
    assert match, f"{script} no longer defines BACKEND_SERVICE"
    out = subprocess.run(
        [
            "/bin/bash",
            "-c",
            f'{release_var}={RELEASE}; BACKEND_SERVICE={match[1]}; echo "$BACKEND_SERVICE"',
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout.strip()


@pytest.mark.parametrize("script", SCRIPTS)
def test_the_script_sends_its_backend_service_name_as_the_host(script: str) -> None:
    text = (ROOT / script).read_text(encoding="utf-8")
    assert re.search(r'(--host|Host:) "?\$\{?BACKEND_SERVICE\}?', text), script


@pytest.mark.parametrize("env", ENVS)
@pytest.mark.parametrize("script", SCRIPTS)
def test_that_host_is_in_every_rendered_allowed_hosts(script: str, env: str) -> None:
    host = _backend_service(script, SCRIPTS[script])
    assert host.endswith("-backend")
    allowed = rendered_config(env, release=RELEASE)["ALLOWED_HOSTS"].split(",")
    assert host in allowed, (script, env, host, allowed)


def test_the_smoke_script_default_base_is_not_used_by_the_deploy_scripts() -> None:
    """A base URL host (127.0.0.1) is never allowed in-cluster; the scripts must override Host."""
    assert Path(ROOT / "scripts/smoke-test.sh").exists()
