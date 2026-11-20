#!/usr/bin/env python3
"""Wait for an HTTP service to return 200 before a deadline.

Usage:
    wait_for_service.py URL TIMEOUT [INTERVAL] [NAME]

Args:
    URL      - Full URL to poll (e.g. http://127.0.0.1:8000/api/v1/health)
    TIMEOUT  - Maximum seconds to wait (float or int)
    INTERVAL - Seconds between retries (default: 0.5)
    NAME     - Human-readable service name for log messages (default: "service")

Exits 0 when the URL returns HTTP 200, or 1 on timeout.
Uses only the standard library (urllib).
"""

import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

HTTP_OK = 200
MIN_ARGS = 2
INTERVAL_ARG_INDEX = 2
NAME_ARG_INDEX = 3

# Poll a loopback service started in THIS container. CI runners (especially
# enterprise/self-hosted runners) frequently export http_proxy/https_proxy;
# urllib honours those — and can also pick up system proxy config — whereas the
# entrypoint's curl is typically arranged to bypass loopback. Routing
# 127.0.0.1 through a central proxy would make every poll fail here while curl
# succeeds (the exact symptom we guard against), so force direct connections.
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Per-request socket timeout. Generous on purpose: a health poll that is merely
# slow to respond under load (e.g. a busy DB tier, or a slow shared filesystem)
# must be tolerated, not misread as "unreachable" — and it must not be shorter
# than the entrypoint's `curl --max-time 3`, or urllib would time out where curl
# still succeeds. The overall `timeout` argument bounds the total wait.
URL_OPEN_TIMEOUT = 10


def wait_for_service(
    url: str,
    timeout: float,
    interval: float = 0.5,
    name: str = "service",
) -> bool:
    """Poll ``url`` until it returns HTTP 200 or ``timeout`` seconds elapse.

    Returns True on success, False on timeout.
    """
    deadline = time.time() + float(timeout)
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            with _NO_PROXY_OPENER.open(url, timeout=URL_OPEN_TIMEOUT) as response:
                if response.status == HTTP_OK:
                    print(f"{name} healthy (attempt {attempt}).")
                    return True
        except urllib.error.HTTPError as exc:
            # Non-2xx (e.g. 503 while the DB tier is still initializing). The
            # status code IS the readiness contract, so keep polling until 200.
            print(
                f"{name} not ready (attempt {attempt}): HTTP {exc.code}",
                file=sys.stderr,
            )
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            # Connection refused/reset or socket timeout — service not up yet.
            print(
                f"{name} unreachable (attempt {attempt}): {exc}",
                file=sys.stderr,
            )
        time.sleep(float(interval))
    print(f"{name} did not become healthy within {timeout}s.", file=sys.stderr)
    return False


def _malformed_reason(url: str) -> str | None:
    """Return why ``url`` can't be polled, or None if it looks pollable.

    A health poll targets a *specific* service port, so a missing port is
    treated as malformed: urllib would silently fall back to port 80 and spend
    the entire timeout getting "Connection refused" against the wrong endpoint
    while the real service is healthy on its own port. Fail loud and immediate
    instead. (This is the failure mode the guard exists to catch.)
    """
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname:
        return "no scheme or host"
    if parts.port is None:
        return "no explicit port (would default to 80)"
    return None


def main(argv: list[str] | None = None) -> None:
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) < MIN_ARGS:
        print(
            "Usage: wait_for_service.py URL TIMEOUT [INTERVAL] [NAME]",
            file=sys.stderr,
        )
        sys.exit(2)

    url = argv[0]
    reason = _malformed_reason(url)
    if reason:
        print(
            f"Refusing to poll malformed health URL ({reason}): {url!r}",
            file=sys.stderr,
        )
        sys.exit(2)
    timeout = float(argv[1])
    interval = float(argv[INTERVAL_ARG_INDEX]) if len(argv) > INTERVAL_ARG_INDEX else 0.5
    name = argv[NAME_ARG_INDEX] if len(argv) > NAME_ARG_INDEX else "service"

    if not wait_for_service(url, timeout, interval=interval, name=name):
        sys.exit(1)


if __name__ == "__main__":
    main()
