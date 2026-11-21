# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Live tests: the headers on the static files the Next.js server serves itself, on real responses.

Starts its own copy of the built standalone server (``npm run build`` first) with temporary files in
its ``public/`` directory, so the logo, favicon, robots, sitemap and an SVG are all real files and
nothing is added to the repository. Static files must get no framing and no content policy that
would let anything run; the proxied text files keep the page policy and the no-framing header.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import status

from tests.live.test_header_set import (
    _FRONTEND_LAYER,
    _IMMUTABLE,
    _NEXT_CSP,
    _STATIC_LAYER,
    _assert_final_set,
)

pytestmark = pytest.mark.live

_ROOT = Path(os.getenv("FRONTEND_ROOT", Path(__file__).resolve().parents[2] / "frontend"))
_SVG = '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
_FILES = {
    "logo.png": "x",
    "favicon.ico": "x",
    "robots.txt": "User-agent: *",
    "sitemap.xml": "<urlset/>",
    "temp.svg": _SVG,
}
_WEEK = "public, max-age=604800"


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[httpx.Client]:
    bundle = _ROOT / ".next" / "standalone"
    if not (bundle / "server.js").is_file():
        pytest.fail(f"{bundle}/server.js is missing: run `npm run build` in the frontend first")
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is not on PATH")
    work = tmp_path_factory.mktemp("standalone")
    shutil.copytree(bundle, work, dirs_exist_ok=True, ignore_dangling_symlinks=True)
    shutil.rmtree(work / ".next" / "static", ignore_errors=True)
    (work / ".next" / "static").unlink(missing_ok=True)
    shutil.copytree(_ROOT / ".next" / "static", work / ".next" / "static")
    if (_ROOT / "public").is_dir():
        shutil.copytree(_ROOT / "public", work / "public", dirs_exist_ok=True)
    (work / "public").mkdir(exist_ok=True)
    for name, body in _FILES.items():
        (work / "public" / name).write_text(body, encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        **os.environ,
        "PORT": str(port),
        "HOSTNAME": "127.0.0.1",
        "BACKEND_URL": "http://127.0.0.1:1",
        "NODE_ENV": "production",
    }
    proc = subprocess.Popen(
        [node, "server.js"],
        cwd=work,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10.0, trust_env=False)
    try:
        for _ in range(60):
            try:
                if client.get("/api/healthz").status_code == status.HTTP_200_OK:
                    break
            except httpx.TransportError:
                time.sleep(0.5)
        else:
            pytest.fail("the standalone server did not start")
        yield client
    finally:
        client.close()
        proc.terminate()
        proc.wait(timeout=10)


def _chunk(client: httpx.Client) -> str:
    match = re.search(r'/_next/static/[^"\\]+\.js', client.get("/").text)
    assert match, "the page references no static chunk"
    return match.group(0)


def test_a_build_chunk_is_immutable_and_locked_down(server: httpx.Client) -> None:
    res = server.get(_chunk(server))
    assert res.status_code == status.HTTP_200_OK
    _assert_final_set(res, _STATIC_LAYER, exact_transport={"cache-control": _IMMUTABLE})


def test_a_missing_static_file_is_a_404_with_the_same_headers(server: httpx.Client) -> None:
    res = server.get("/_next/static/chunks/no-such-file-4f9a.js")
    assert res.status_code == status.HTTP_404_NOT_FOUND
    _assert_final_set(res, _STATIC_LAYER)


@pytest.mark.parametrize(
    ("path", "cache_control"),
    [("/logo.png", _WEEK), ("/favicon.ico", _WEEK), ("/temp.svg", None)],
)
def test_a_public_file_the_proxy_skips_is_locked_down(
    server: httpx.Client, path: str, cache_control: str | None
) -> None:
    res = server.get(path)
    assert res.status_code == status.HTTP_200_OK
    pinned = {"cache-control": cache_control} if cache_control else {}
    _assert_final_set(res, _STATIC_LAYER, exact_transport=pinned)
    assert res.headers["content-security-policy"] == "default-src 'none'"


@pytest.mark.parametrize("path", ["/robots.txt", "/sitemap.xml", "/health.txt"])
def test_a_proxied_text_file_is_revalidated_and_cannot_be_framed(
    server: httpx.Client, path: str
) -> None:
    res = server.get(path)
    assert res.status_code == status.HTTP_200_OK
    expected = {**_FRONTEND_LAYER, "content-security-policy": _NEXT_CSP, "x-frame-options": "DENY"}
    _assert_final_set(res, expected, exact_transport={"cache-control": "no-cache"})
