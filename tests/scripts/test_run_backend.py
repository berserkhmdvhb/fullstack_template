# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The image launcher hands Uvicorn the flags the client-IP and keep-alive design relies on."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "infra" / "backend" / "run_backend.sh"


def _launch(tmp_path: Path, **env: str) -> subprocess.CompletedProcess[str]:
    """Run the launcher with a fake ``uvicorn`` that prints its arguments one per line."""
    fake = tmp_path / "uvicorn"
    fake.write_text('#!/usr/bin/env bash\nprintf "ARG:%s\\n" "$@"\n', encoding="utf-8")
    fake.chmod(0o755)
    run_env = {"PATH": f"{tmp_path}:{os.environ['PATH']}", **env}
    return subprocess.run(
        ["bash", str(LAUNCHER)],  # noqa: S607 - bash from PATH, as the image does
        capture_output=True,
        text=True,
        check=False,
        env=run_env,
    )


def _args(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [line[4:] for line in result.stdout.splitlines() if line.startswith("ARG:")]


def test_the_launcher_passes_the_uvicorn_flags(tmp_path: Path) -> None:
    result = _launch(tmp_path)
    assert result.returncode == 0, result.stderr
    args = _args(result)
    assert "--workers" not in args
    for flag in ("--no-proxy-headers", "--no-server-header", "--no-access-log"):
        assert flag in args
    assert "--proxy-headers" not in args
    assert "--forwarded-allow-ips" not in args
    assert args[args.index("--timeout-keep-alive") + 1] == "75"
    assert args[args.index("--timeout-graceful-shutdown") + 1] == "30"


def test_the_keep_alive_is_configurable(tmp_path: Path) -> None:
    args = _args(_launch(tmp_path, BACKEND_KEEPALIVE_TIMEOUT="120"))
    assert args[args.index("--timeout-keep-alive") + 1] == "120"


@pytest.mark.parametrize("value", ["0", "-1", "soon", "1.5"])
def test_a_bad_keep_alive_stops_the_launcher(tmp_path: Path, value: str) -> None:
    result = _launch(tmp_path, BACKEND_KEEPALIVE_TIMEOUT=value)
    assert result.returncode != 0
    assert "BACKEND_KEEPALIVE_TIMEOUT" in result.stderr
    assert not _args(result)


@pytest.mark.parametrize("script", ["run_backend.sh", "infra/runtime/entrypoint.sh"])
def test_the_other_launchers_also_turn_uvicorns_proxy_headers_off(script: str) -> None:
    """One resolver only: the developer launchers must not run Uvicorn's own either."""
    assert "--no-proxy-headers" in (ROOT / script).read_text(encoding="utf-8")
