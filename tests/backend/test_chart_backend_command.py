# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The backend Pod runs the Python launcher directly: the image has no shell to wrap it in."""

from __future__ import annotations

import os
import subprocess
from typing import Any

import yaml

from tests.backend.test_chart_production_guard import CHART, find_helm, require_helm


def _backend_container() -> dict[str, Any]:
    helm = require_helm(find_helm(), ci=bool(os.environ.get("CI")))
    out = subprocess.run(
        [
            helm,
            "template",
            "svc",
            str(CHART),
            "-f",
            str(CHART / "values.dev.yaml"),
            "-s",
            "templates/backend-deployment.yaml",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    containers: list[dict[str, Any]] = yaml.safe_load(out)["spec"]["template"]["spec"]["containers"]
    return next(c for c in containers if c["name"] == "backend")


def test_the_command_is_tini_and_the_python_launcher() -> None:
    assert _backend_container()["command"] == [
        "/usr/bin/tini",
        "-s",
        "--",
        "/app/.venv/bin/python",
        "-m",
        "your_service_name.backend.serve",
    ]


def test_no_hook_needs_a_shell_in_the_backend_image() -> None:
    pre_stop = _backend_container()["lifecycle"]["preStop"]["exec"]["command"]
    assert pre_stop[0] == "/app/.venv/bin/python"
    assert "sh" not in {c.rsplit("/", 1)[-1] for c in pre_stop}
