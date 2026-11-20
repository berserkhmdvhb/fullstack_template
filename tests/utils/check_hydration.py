#!/usr/bin/env python3
"""Fail-fast check that a running Next.js frontend is serving its client bundles.

Why this exists
---------------
A Next.js App-Router page renders its shell as SSR HTML, then *hydrates* on the
client using the JS bundles under ``/_next/static/chunks/*.js``. If those bundles
404 (a partial/broken build artifact, or a runner where the standalone server
cannot read ``.next/static``) the page never hydrates: the client-only components
never mount, so every E2E assertion on them fails with "element(s) not found"
after ~200s of browser tests — a white-screen that looks like an app bug but is
really a broken build/deploy artifact.

This check fetches the page, extracts a shared chunk URL, and asserts it is
served as ``application/javascript`` BEFORE the expensive Playwright run starts,
so that failure mode fails fast and loud instead of after the full suite.

Usage:
    check_hydration.py BASE_URL [TIMEOUT] [INTERVAL] [NAME]

Args:
    BASE_URL - Frontend origin (e.g. http://127.0.0.1:3000)
    TIMEOUT  - Maximum seconds to wait (default: 30)
    INTERVAL - Seconds between retries (default: 1)
    NAME     - Human-readable name for log messages (default: "hydration")

Exits 0 when a shared chunk is served as application/javascript, else 1
(2 on a usage error). Standard library only (urllib, re).
"""

import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin

HTTP_OK = 200
# Next serves the compiled chunks as JavaScript; accept both the historical and
# the modern MIME spelling so a server default change can't false-trip this.
JS_CONTENT_TYPES = ("application/javascript", "text/javascript")

# CLI argument positions and minimum arity. Named constants so ruff's PLR2004
# "magic value in comparison" rule doesn't fire on the positional-arg checks in
# main() (mirrors the constants in wait_for_service.py).
MIN_ARGS = 1
TIMEOUT_ARG_INDEX = 1
INTERVAL_ARG_INDEX = 2
NAME_ARG_INDEX = 3

# Shared app chunks: main-app-<hash>.js plus the other shared chunks
# (255-*, 4bd1b696-*, webpack-*, ...). App-Router chunk filenames are word
# characters, '.', '/' and '-' only.
_CHUNK_RE = re.compile(r"/_next/static/chunks/[\w./-]+\.js")
_MAIN_APP_RE = re.compile(r"/_next/static/chunks/main-app-[\w./-]+\.js")

# Poll a loopback service started in THIS container. CI runners (especially
# enterprise/self-hosted runners) frequently export http_proxy/https_proxy;
# urllib honours those — and can also pick up system proxy config — whereas the
# entrypoint's curl is arranged to bypass loopback. Routing 127.0.0.1 through a
# central proxy would make every fetch fail here while curl succeeds, so force
# direct connections (mirrors wait_for_service.py).
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Per-request socket timeout. Generous on purpose: a fetch that is merely slow
# under load (busy runner, slow shared filesystem) must be tolerated, not
# misread as "not serving"; the overall `timeout` argument bounds the total wait.
REQUEST_TIMEOUT = 10


def _get(url: str) -> tuple[int | None, str, str]:
    """GET ``url`` directly (bypassing any proxy).

    Returns ``(status, content_type, body_text)``. ``status`` is None and
    ``body_text`` carries the error if the connection itself failed.
    """
    try:
        with _NO_PROXY_OPENER.open(url, timeout=REQUEST_TIMEOUT) as response:
            body = response.read().decode("utf-8", "replace")
            return response.status, response.headers.get("Content-Type", ""), body
    except urllib.error.HTTPError as exc:
        headers = exc.headers if exc.headers is not None else {}
        return exc.code, headers.get("Content-Type", ""), ""
    except (urllib.error.URLError, OSError) as exc:
        return None, "", str(exc)


def _extract_chunk(html: str) -> str | None:
    """Return a shared chunk path from page HTML, preferring the app entry.

    The App-Router entry chunk (``main-app-<hash>.js``) is the one that actually
    hydrates the app, so prefer it; otherwise fall back to the first shared chunk
    found — any of them 404ing breaks hydration.
    """
    main_app = _MAIN_APP_RE.search(html)
    if main_app:
        return main_app.group(0)
    first = _CHUNK_RE.search(html)
    return first.group(0) if first else None


def _content_type_ok(content_type: str) -> bool:
    main = content_type.split(";", 1)[0].strip().lower()
    return main in JS_CONTENT_TYPES


def check_hydration(
    base_url: str,
    timeout: float,
    interval: float = 1.0,
    name: str = "hydration",
) -> bool:
    """Poll ``base_url`` until a shared chunk serves as JS, or ``timeout`` elapses.

    Returns True on success, False on timeout.
    """
    page_url = urljoin(base_url, "/")
    deadline = time.time() + float(timeout)
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        status, _ct, body = _get(page_url)
        if status != HTTP_OK:
            print(
                f"{name} page not ready (attempt {attempt}): {page_url} -> HTTP {status}",
                file=sys.stderr,
            )
            time.sleep(float(interval))
            continue

        chunk = _extract_chunk(body)
        if chunk is None:
            print(
                f"{name} no /_next/static/chunks/*.js found in {page_url} (attempt {attempt})",
                file=sys.stderr,
            )
            time.sleep(float(interval))
            continue

        chunk_url = urljoin(base_url, chunk)
        cstatus, ccontent_type, _ = _get(chunk_url)
        if cstatus == HTTP_OK and _content_type_ok(ccontent_type):
            print(
                f"{name} healthy: {chunk} served as "
                f"{ccontent_type.split(';', 1)[0].strip()} (attempt {attempt}).",
            )
            return True

        detail = f"HTTP {cstatus}" if cstatus is not None else f"unreachable ({_ct})"
        print(
            f"{name} chunk not ready (attempt {attempt}): {chunk} -> {detail}, "
            f"Content-Type {ccontent_type!r}",
            file=sys.stderr,
        )
        time.sleep(float(interval))

    print(f"{name} did not become ready within {timeout}s.", file=sys.stderr)
    return False


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) < MIN_ARGS:
        print(
            "Usage: check_hydration.py BASE_URL [TIMEOUT] [INTERVAL] [NAME]",
            file=sys.stderr,
        )
        return 2

    base_url = argv[0]
    timeout = float(argv[TIMEOUT_ARG_INDEX]) if len(argv) > TIMEOUT_ARG_INDEX else 30.0
    interval = float(argv[INTERVAL_ARG_INDEX]) if len(argv) > INTERVAL_ARG_INDEX else 1.0
    name = argv[NAME_ARG_INDEX] if len(argv) > NAME_ARG_INDEX else "hydration"

    return 0 if check_hydration(base_url, timeout, interval, name) else 1


if __name__ == "__main__":
    sys.exit(main())
