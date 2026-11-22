"""The health probe of scripts/ci/deploy-verify.sh: 2xx is healthy, a 3xx is not, nor is silence.

The check reaches each Pod over a port-forward and asks it with the runner's curl, so no command
runs inside a Pod (the deployer has no pods/exec). Here the probe function runs against a local
HTTP server.
"""

from __future__ import annotations

import http.server
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts/ci/deploy-verify.sh"
TEXT = SCRIPT.read_text(encoding="utf-8")


def _function(name: str) -> str:
    start = TEXT.index(f"{name}() {{")
    return TEXT[start : TEXT.index("\n}\n", start) + 3]


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/slow":
            time.sleep(8)
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.end_headers()
            return
        self.send_response({"/ok": 200, "/slow": 200, "/boom": 500}.get(self.path, 404))
        self.end_headers()

    def log_message(self, *_: object) -> None:
        return


@pytest.fixture(scope="module")
def base() -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _http_ok(url: str) -> int:
    assert shutil.which("curl"), "the runner's curl is required"
    return subprocess.run(
        ["bash", "-c", _function("http_ok") + 'http_ok "$1"', "probe", url],  # noqa: S607
        check=False,
        timeout=20,
    ).returncode


# The probe gives up after 5 s (curl --max-time 5); 7 s allows for process start.
GIVE_UP_SECONDS = 7
CASES = [("/ok", 0), ("/boom", 1), ("/missing", 1), ("/redirect", 1)]


@pytest.mark.parametrize(("path", "code"), CASES)
def test_only_a_2xx_is_healthy_and_a_redirect_is_not_followed(
    base: str, path: str, code: int
) -> None:
    assert (_http_ok(base + path) == 0) == (code == 0)


def test_no_answer_within_five_seconds_is_unhealthy(base: str) -> None:
    started = time.monotonic()
    assert _http_ok(base + "/slow") != 0
    assert time.monotonic() - started < GIVE_UP_SECONDS


def test_a_closed_port_is_unhealthy() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert _http_ok(f"http://127.0.0.1:{port}/") != 0


def test_no_command_runs_inside_a_pod() -> None:
    code = [ln for ln in TEXT.splitlines() if not ln.lstrip().startswith("#")]
    assert not [ln for ln in code if " exec " in f" {ln} "]
    assert "port-forward" in TEXT
