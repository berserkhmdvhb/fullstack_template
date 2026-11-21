# your-service-name — adaptable service template (see wiki/getting-started.md)
"""`entrypoint.sh --mode backend` (the default mode) starts without a frontend build."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = ROOT / "infra" / "runtime" / "entrypoint.sh"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.skipif(Path("/app").exists(), reason="a /app tree would hide the missing-path case")
def test_backend_mode_starts_without_a_frontend_build(tmp_path: Path) -> None:
    port = _free_port()
    env = {
        **os.environ,
        "PATH": f"{Path(sys.executable).parent}:{os.environ['PATH']}",
        "PYTHONPATH": str(ROOT / "src"),
        "TEST_DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'e.db'}",
        "BACKEND_PORT": str(port),
        "BACKEND_HOST": "127.0.0.1",
        "LOG_DIR": str(tmp_path / "logs"),
    }
    env.pop("SERVICE_MODE", None)
    proc = subprocess.Popen(
        ["bash", str(ENTRYPOINT), "--mode", "backend", "--no-wait"],  # noqa: S607 - bash on PATH
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    url = f"http://127.0.0.1:{port}/api/v1/health"
    status = None
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline and proc.poll() is None:
            try:
                with urllib.request.urlopen(url, timeout=2) as resp:
                    status = resp.status
                    break
            except (urllib.error.URLError, OSError):
                time.sleep(0.3)
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
        out, _ = proc.communicate(timeout=20)
    assert status == 200, out  # noqa: PLR2004 - migrated, so ready
