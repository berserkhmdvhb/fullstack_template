# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Backend launcher: serve the FastAPI app with Uvicorn, in this one process.

Run as ``python -m your_service_name.backend.serve`` (the container's command). One process per
container: Kubernetes scales Pods, not processes, and Uvicorn handles SIGTERM itself.

Configuration (environment; defaults are the safe baseline):
- ``BACKEND_ENTRY``: ASGI import string (``your_service_name.backend.main:app``).
- ``BACKEND_BIND`` / ``BACKEND_PORT``: listen address (``0.0.0.0`` / ``8000``).
- ``BACKEND_GRACEFUL_TIMEOUT``: seconds to drain in-flight requests after SIGTERM (``30``).
- ``BACKEND_KEEPALIVE_TIMEOUT``: seconds an idle connection stays open (``75``). Keep it above the
  gateway's upstream idle timeout (``waf.upstreamIdleTimeoutSeconds`` in the chart), or the proxy
  can reuse a connection Uvicorn just closed and return a 502.
- ``API_VERSION``: only for the banner's URLs, read like the app reads it (environment over
  ``.env``, stripped). The app validates all its settings when Uvicorn imports it; the launcher
  does not build ``Settings`` twice.

Fixed options, each on purpose:
- No proxy headers: the app resolves the client IP itself (``TRUSTED_PROXY_CIDRS``), and two
  resolvers would be two trust lists.
- No ``server`` header: do not advertise the server software.
- No access log: the app writes its own JSON request log (``LOG_REQUESTS``).

The launcher reads the environment directly (not through ``Settings``) so that it stays a thin
process starter; the values are not app settings.
"""

from __future__ import annotations

import os
import re
import sys
from typing import TYPE_CHECKING, Any

import uvicorn

if TYPE_CHECKING:
    from collections.abc import Mapping

_POSITIVE_INT = re.compile(r"[1-9][0-9]*", re.ASCII)


def _positive_int(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = environ.get(name) or str(default)
    if not _POSITIVE_INT.fullmatch(raw):
        msg = f"{name} must be a positive integer (got '{raw}')"
        raise ValueError(msg)
    return int(raw)


def build_options(environ: Mapping[str, str]) -> dict[str, Any]:
    """The keyword arguments for ``uvicorn.run``; ``ValueError`` names a bad variable."""
    return {
        "app": environ.get("BACKEND_ENTRY") or "your_service_name.backend.main:app",
        "host": environ.get("BACKEND_BIND") or "0.0.0.0",  # noqa: S104 - a container listens everywhere
        "port": _positive_int(environ, "BACKEND_PORT", 8000),
        "log_level": "info",
        "proxy_headers": False,
        "server_header": False,
        "access_log": False,
        "timeout_keep_alive": _positive_int(environ, "BACKEND_KEEPALIVE_TIMEOUT", 75),
        "timeout_graceful_shutdown": _positive_int(environ, "BACKEND_GRACEFUL_TIMEOUT", 30),
    }


def _banner(options: Mapping[str, Any], api_version: str) -> str:
    base = f"http://{options['host']}:{options['port']}"
    return "\n".join(
        [
            "",
            "▶️  Backend — FastAPI / Uvicorn",
            f"   entry    : {options['app']}",
            f"   bind     : {options['host']}:{options['port']}",
            f"   graceful : {options['timeout_graceful_shutdown']}s",
            f"   keepalive: {options['timeout_keep_alive']}s",
            f"   api      : {base}/api/{api_version}",
            f"   docs     : {base}/docs",
            f"   health   : {base}/api/{api_version}/health",
            "",
        ]
    )


def main() -> int:
    """Validate the environment, print the banner, serve. Non-zero on a bad value."""
    try:
        options = build_options(os.environ)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)  # noqa: T201
        return 1
    from your_service_name.backend.settings import ApiVersionSetting  # noqa: PLC0415 - after validation

    print(_banner(options, ApiVersionSetting().API_VERSION), flush=True)  # noqa: T201
    uvicorn.run(**options)
    return 0


if __name__ == "__main__":
    sys.exit(main())
