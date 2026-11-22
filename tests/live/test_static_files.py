# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Live tests: the headers on the static files the Next.js server serves itself, on real responses.

Starts its own copy of the built standalone server (``npm run build`` first) with temporary files in
its ``public/`` directory, so the logo, favicon, robots, sitemap and an SVG are all real files and
nothing is added to the repository. Static files must get no framing and no content policy that
would let anything run; the proxied text files keep the page policy and the no-framing header.
"""

from __future__ import annotations

import itertools
import os
import random
import re
import shutil
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
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
_BACKEND_CSP = "default-src 'self'; script-src 'self'"


class _StubBackend(BaseHTTPRequestHandler):
    """Answers every request like the backend would: its own policy header and a marker."""

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Security-Policy", _BACKEND_CSP)
        self.send_header("X-Backend", "stub")
        self.end_headers()
        self.wfile.write(b"stub")

    def log_message(self, *args: object) -> None:
        """Stay quiet."""


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
    backend = HTTPServer(("127.0.0.1", 0), _StubBackend)
    threading.Thread(target=backend.serve_forever, daemon=True).start()
    env = {
        **os.environ,
        "PORT": str(port),
        "HOSTNAME": "127.0.0.1",
        "BACKEND_URL": f"http://127.0.0.1:{backend.server_port}",
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
        backend.shutdown()
        backend.server_close()
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
# Look-alike paths that are not static fall to the page policy.
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
    # A last segment that is only an extension has no stem: not a file, so not static.
    ("/.svg", _MISSING, _PAGE),
    ("/a/.svg", _MISSING, _PAGE),
    ("/.png", _MISSING, _PAGE),
    ("/.SVG", _MISSING, _PAGE),
    ("/x/.webp", _MISSING, _PAGE),
    # A case variant of a backend prefix is not the backend's: a frontend path with the page policy.
    ("/API/v1/x.svg", _MISSING, _PAGE),
    ("/Docs/a.png", _MISSING, _PAGE),
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


@pytest.mark.parametrize("path", ["/api/v1/x.SVG", "/api/v1/x.png", "/api/v1/a/b.svg"])
def test_a_backend_path_ending_in_an_image_extension_still_reaches_the_backend(
    server: httpx.Client, path: str
) -> None:
    """The static set never applies under the backend prefixes (/api/v1, /docs, /openapi.json)."""
    res = server.get(path)
    assert res.headers.get("x-backend") == "stub", "the request did not reach the backend"
    assert len(res.headers.get_list("content-security-policy")) == 1


@pytest.mark.parametrize("path", ["/docs/x.PNG", "/docs/a/b.svg"])
def test_a_docs_path_ending_in_an_image_extension_keeps_the_backends_one_policy(
    server: httpx.Client, path: str
) -> None:
    res = server.get(path)
    assert res.headers.get("x-backend") == "stub"
    assert res.headers.get_list("content-security-policy") == [_BACKEND_CSP]


# Next.js's own 308 normalisation redirects are exempt from the headers above; the reason is in
# frontend/static-paths.ts. This pins that they stay nothing but a redirect.
@pytest.mark.parametrize("path", ["//t.svg", "/a.svg/", "/_next/image/", "//favicon.ico"])
def test_next_builtin_redirects_stay_bare_redirects(server: httpx.Client, path: str) -> None:
    # The raw path, as sent: httpx would read a leading `//` as a host.
    request = server.build_request("GET", "/")
    request.url = request.url.copy_with(raw_path=path.encode())
    res = server.send(request)
    assert res.status_code == status.HTTP_308_PERMANENT_REDIRECT
    assert res.headers["location"]
    assert "text/html" not in res.headers.get("content-type", "")
    assert "<" not in res.text


def _raw_get(client: httpx.Client, path: str) -> httpx.Response:
    """GET the path exactly as written (httpx would read a leading `//` as a host)."""
    request = client.build_request("GET", "/")
    request.url = request.url.copy_with(raw_path=path.encode())
    return client.send(request)


def _backend_paths() -> list[str]:
    """`BACKEND_PATHS` read from its one definition in static-paths.ts."""
    source = (_ROOT / "static-paths.ts").read_text(encoding="utf-8")
    docs = re.search(r"DOCS_PATH = '([^']+)'", source)
    listed = re.search(r"BACKEND_PATHS[^=]*= \[([^\]]+)\]", source)
    assert docs, "static-paths.ts no longer defines DOCS_PATH"
    assert listed, "static-paths.ts no longer defines BACKEND_PATHS"
    items = [item.strip() for item in listed.group(1).split(",")]
    return [docs.group(1) if item == "DOCS_PATH" else item.strip("'") for item in items]


def _mixed(text: str) -> str:
    return "".join(c.upper() if i % 2 == 0 else c.lower() for i, c in enumerate(text))


_IMAGE_EXTENSIONS = ["svg", "png", "jpg", "jpeg", "gif", "webp"]


def _generated_paths(backend: list[str]) -> list[str]:
    """A deterministic sample of prefix x letter case x stem x extension x trailing segment."""
    prefixes = [
        "",
        *(p.lstrip("/") for p in backend),
        "apix",
        "api.svg",
        "docs.png",
        "openapi.json.svg",
        "_next/static",
        "_next/staticfoo",
        "_next/image",
        "_next/image-x",
        "a/b",
        "api/v1/a/b",
        "_next/static/chunks",
    ]
    cases = [str.lower, str.upper, _mixed]
    stems = ["", "..", "photo", "a.b.c"]
    extensions = [
        *(f".{case(e)}" for e in _IMAGE_EXTENSIONS for case in (str.upper, _mixed)),
        *(f".{e}" for e in _IMAGE_EXTENSIONS),
        ".json",
        ".webmanifest",
        ".html",
        ".txt",
        ".xml",
        "",
    ]
    trailing = ["", "/tail"]
    every = sorted(
        {
            "/" + "/".join(filter(None, [case(prefix), stem + ext])) + tail
            for prefix, case, stem, ext, tail in itertools.product(
                prefixes, cases, stems, extensions, trailing
            )
        }
    )
    pinned = ["/API/v1/x.svg", "/Api/v1/x.png", "/DOCS/x.svg", "/Docs/a.png", "/OpenAPI.json/x.svg"]
    return pinned + random.Random(20261122).sample(every, 400)  # noqa: S311 - a fixed sample


def _exempt_redirect(res: httpx.Response) -> bool:
    """Next.js's own 308 normalisation redirect (see frontend/static-paths.ts)."""
    return (
        res.status_code == status.HTTP_308_PERMANENT_REDIRECT
        and bool(res.headers.get("location"))
        and "text/html" not in res.headers.get("content-type", "")
        and "<" not in res.text
    )


def _is_exact_backend(path: str, backend: list[str]) -> bool:
    segments = path.split("/")
    if "." in segments or ".." in segments or path.endswith("/"):
        return False  # normalised or redirected before the proxy sees it
    return any(path == p or path.startswith(f"{p}/") for p in backend)


def test_generated_paths_all_carry_one_policy_and_backend_paths_reach_the_backend(
    server: httpx.Client,
) -> None:
    """The invariant over a generated cross product, one server, every failure listed."""
    backend = _backend_paths()
    failures = []
    paths = _generated_paths(backend)
    for path in paths:
        res = _raw_get(server, path)
        if _exempt_redirect(res):
            continue
        problems = []
        if len(res.headers.get_list("content-security-policy")) != 1:
            problems.append(f"{len(res.headers.get_list('content-security-policy'))} CSP")
        if res.headers.get("x-frame-options") != "DENY":
            problems.append("no XFO DENY")
        if res.headers.get("x-content-type-options") != "nosniff":
            problems.append("no nosniff")
        if _is_exact_backend(path, backend) and res.headers.get("x-backend") != "stub":
            problems.append("did not reach the backend")
        if problems:
            failures.append(f"{path} -> {res.status_code}: {', '.join(problems)}")
    assert not failures, f"{len(failures)} of {len(paths)} paths:\n" + "\n".join(failures)
