# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The container's HEALTHCHECK probe: stdlib only, 0 on a 2xx and 1 on anything else."""

from __future__ import annotations

import http.server
import json
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from your_service_name.backend import healthcheck

SRC = Path(__file__).resolve().parents[2] / "src"
FORBIDDEN = (
    "fastapi",
    "starlette",
    "sqlalchemy",
    "pydantic",
    "uvicorn",
    "your_service_name.backend.main",
)


class _Handler(http.server.BaseHTTPRequestHandler):
    status = 200
    delay = 0.0

    def do_GET(self) -> None:
        time.sleep(self.delay)
        self.send_response(self.status)
        if self.status == 301:  # noqa: PLR2004
            self.send_header("Location", "/ok")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_: object) -> None:  # silence the test server
        pass


@contextmanager
def _server(status: int = 200, delay: float = 0.0) -> Iterator[int]:
    handler = type("H", (_Handler,), {"status": status, "delay": delay})
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_port
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HEALTHCHECK_SCHEME", "BACKEND_PORT", "API_VERSION"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(("status", "code"), [(200, 0), (204, 0), (404, 1), (500, 1), (301, 1)])
def test_the_exit_code_follows_the_status(
    monkeypatch: pytest.MonkeyPatch, status: int, code: int
) -> None:
    with _server(status) as port:
        monkeypatch.setenv("BACKEND_PORT", str(port))
        assert healthcheck.main() == code


def test_it_probes_the_liveness_path_of_the_configured_version() -> None:
    assert healthcheck.url({"BACKEND_PORT": "9", "API_VERSION": "v9"}) == (
        "http://localhost:9/api/v9/healthz"
    )


def test_the_defaults_are_http_port_8000_and_v1() -> None:
    assert healthcheck.url({}) == "http://localhost:8000/api/v1/healthz"
    assert healthcheck.url({"HEALTHCHECK_SCHEME": "https"}).startswith("https://localhost:8000/")


def test_a_redirect_is_not_followed(monkeypatch: pytest.MonkeyPatch) -> None:
    with _server(301) as port:
        monkeypatch.setenv("BACKEND_PORT", str(port))
        assert healthcheck.main() == 1


def test_nothing_listening_is_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    with _server() as port:
        pass  # closed again: the port is free
    monkeypatch.setenv("BACKEND_PORT", str(port))
    assert healthcheck.main() == 1


def test_a_slow_handler_times_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(healthcheck, "TIMEOUT_SECONDS", 0.3)
    with _server(delay=1.5) as port:
        monkeypatch.setenv("BACKEND_PORT", str(port))
        assert healthcheck.main() == 1


def test_a_tls_failure_is_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    """An https probe against a plain-HTTP server cannot complete a handshake."""
    with _server() as port:
        monkeypatch.setenv("BACKEND_PORT", str(port))
        monkeypatch.setenv("HEALTHCHECK_SCHEME", "https")
        assert healthcheck.main() == 1


def test_the_timeout_is_below_the_dockerfile_healthcheck_timeout() -> None:
    assert healthcheck.TIMEOUT_SECONDS == 4  # noqa: PLR2004 - the HEALTHCHECK allows 5 s


def test_importing_it_loads_no_framework() -> None:
    code = (
        f"import sys, json; sys.path.insert(0, {str(SRC)!r}); "
        "import your_service_name.backend.healthcheck; "
        "print(json.dumps(sorted(sys.modules)))"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        check=False,
        env={},
    )
    assert result.returncode == 0, result.stderr
    loaded = json.loads(result.stdout)
    assert not [m for m in loaded if m.split(".")[0] in FORBIDDEN or m in FORBIDDEN]
