"""The in-Pod health probes of scripts/ci/deploy-verify.sh: 2xx is healthy, a 3xx is not."""

from __future__ import annotations

import http.server
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts/ci/deploy-verify.sh"
NODE = shutil.which("node") or str(SCRIPT.parent.parent.parent / ".tools/bin/node")
TEXT = SCRIPT.read_text(encoding="utf-8")


def _probe(name: str) -> str:
    start = TEXT.index(f"{name}='") + len(name) + 2
    return TEXT[start : TEXT.index("'\n", start)]


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.end_headers()
            return
        self.send_response({"/ok": 200, "/boom": 500}.get(self.path, 404))
        self.end_headers()

    def log_message(self, *_: object) -> None:
        return


@pytest.fixture(scope="module")
def base() -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def node_or_skip(node: str, ci: str | None) -> str:
    """The Node binary; a missing one fails on CI (a hosted runner ships Node) and skips locally."""
    if Path(node).exists():
        return node
    if ci:
        pytest.fail("node is missing on CI: the frontend probe would go untested")
    pytest.skip("no node on this machine")


def test_a_missing_node_fails_on_ci_and_skips_locally() -> None:
    with pytest.raises(pytest.fail.Exception):
        node_or_skip("/nonexistent/node", "true")
    with pytest.raises(pytest.skip.Exception):
        node_or_skip("/nonexistent/node", None)
    assert node_or_skip(sys.executable, "true") == sys.executable


CASES = [("/ok", 0), ("/boom", 1), ("/missing", 1), ("/redirect", 1)]


@pytest.mark.parametrize(("path", "code"), CASES)
def test_the_backend_probe_treats_only_a_2xx_as_healthy_and_does_not_follow_a_redirect(
    base: str, path: str, code: int
) -> None:
    run = subprocess.run(
        [sys.executable, "-I", "-c", _probe("PY_PROBE"), base + path], check=False, timeout=20
    )
    assert (run.returncode == 0) == (code == 0)


@pytest.mark.parametrize(("path", "code"), CASES)
def test_the_frontend_probe_treats_only_a_2xx_as_healthy_and_does_not_follow_a_redirect(
    base: str, path: str, code: int
) -> None:
    node = node_or_skip(NODE, os.environ.get("CI"))
    run = subprocess.run([node, "-e", _probe("NODE_PROBE"), base + path], check=False, timeout=20)
    assert (run.returncode == 0) == (code == 0)


def test_the_frontend_is_probed_with_its_own_node_not_curl() -> None:
    assert "/nodejs/bin/node -e" in TEXT
    assert "curl" not in [
        w for ln in TEXT.splitlines() if not ln.lstrip().startswith("#") for w in ln.split()
    ]
