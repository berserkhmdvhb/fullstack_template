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
from typing import NoReturn

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
    "a/b.svg": _SVG,
    "PHOTO.SVG": _SVG,
    "data.json": "{}",
    "app.webmanifest": "{}",
    "page.html": "<script>alert(1)</script>",
    "noext": "x",
    "logo.png": "x",
    "favicon.ico": "x",
    "robots.txt": "User-agent: *",
    "sitemap.xml": "<urlset/>",
    "temp.svg": _SVG,
}
_WEEK = "public, max-age=604800"


def _unavailable(reason: str) -> NoReturn:
    """Skip locally; fail in CI, where the build must exist."""
    if os.getenv("CI"):
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[httpx.Client]:
    bundle = _ROOT / ".next" / "standalone"
    if not (bundle / "server.js").is_file():
        _unavailable(f"{bundle}/server.js is missing: run `npm run build` in the frontend first")
    node = shutil.which("node")
    if node is None:
        _unavailable("node is not on PATH")
    work = tmp_path_factory.mktemp("standalone")
    shutil.copytree(bundle, work, dirs_exist_ok=True, ignore_dangling_symlinks=True)
    shutil.rmtree(work / ".next" / "static", ignore_errors=True)
    (work / ".next" / "static").unlink(missing_ok=True)
    shutil.copytree(_ROOT / ".next" / "static", work / ".next" / "static")
    if (_ROOT / "public").is_dir():
        shutil.copytree(_ROOT / "public", work / "public", dirs_exist_ok=True)
    (work / "public").mkdir(exist_ok=True)
    for name, body in _FILES.items():
        (work / "public" / name).parent.mkdir(parents=True, exist_ok=True)
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


@pytest.mark.parametrize("path", ["/robots.txt", "/sitemap.xml", "/health.txt"])
def test_a_proxied_text_file_is_revalidated(server: httpx.Client, path: str) -> None:
    res = server.get(path)
    assert res.status_code == status.HTTP_200_OK
    page = {**_FRONTEND_LAYER, "content-security-policy": _NEXT_CSP}
    _assert_final_set(res, page, exact_transport={"cache-control": "no-cache"})


# Every response carries nosniff, X-Frame-Options DENY and exactly one Content-Security-Policy: the
# static set's `default-src 'none'` (set by the headers rule) or the page policy (set by the proxy).
# The two sets are complements, so look-alike paths that are not static fall to the page policy.
_STATIC = "static"
_PAGE = "page"
_OK, _MISSING = status.HTTP_200_OK, status.HTTP_404_NOT_FOUND
_EVERY_RESPONSE = [
    ("/", _OK, _PAGE),
    ("/items", _OK, _PAGE),
    ("/no-such-page-4f9a", _MISSING, _PAGE),
    ("/_next/static/chunks/no-such-file-4f9a.js", _MISSING, _STATIC),
    ("/_next/static/no-such-file-4f9a.js", _MISSING, _STATIC),
    ("/_next/staticfoo", _MISSING, _PAGE),
    ("/_next/image-x", _MISSING, _PAGE),
    ("/favicon.ico", _OK, _STATIC),
    ("/favicon.icox", _MISSING, _PAGE),
    ("/favicon.ico/x", _MISSING, _PAGE),
    ("/a/b.svg", _OK, _STATIC),
    ("/PHOTO.SVG", _OK, _STATIC),
    ("/data.json", _OK, _PAGE),
    ("/app.webmanifest", _OK, _PAGE),
    ("/page.html", _OK, _PAGE),
    ("/noext", _OK, _PAGE),
    ("/robots.txt", _OK, _PAGE),
]


@pytest.mark.parametrize(("path", "code", "kind"), _EVERY_RESPONSE)
def test_every_response_has_nosniff_no_framing_and_exactly_one_policy(
    server: httpx.Client, path: str, code: int, kind: str
) -> None:
    res = server.get(path)
    assert res.status_code == code
    policy = "default-src 'none'" if kind == _STATIC else _NEXT_CSP
    _assert_final_set(res, {**_FRONTEND_LAYER, "content-security-policy": policy})


def test_a_chunk_response_is_one_policy_too(server: httpx.Client) -> None:
    res = server.get(_chunk(server))
    assert len(res.headers.get_list("content-security-policy")) == 1
