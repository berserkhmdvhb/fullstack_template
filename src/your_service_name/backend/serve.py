# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Backend launcher: serve the FastAPI app with Uvicorn, in this one process.

Run as ``python -m your_service_name.backend.serve`` (the container's command). One process per
container: Kubernetes scales Pods, not processes, and Uvicorn handles SIGTERM itself.

Configuration (environment; defaults are the safe baseline):
- ``BACKEND_ENTRY``: ASGI import string (``your_service_name.backend.main:app``).
- ``BACKEND_BIND`` / ``BACKEND_PORT``: listen address (``0.0.0.0`` / ``8000``).
- ``METRICS_PORT``: the port of the Prometheus metrics server (``9102``), bound on ``BACKEND_BIND``.
  The API app serves no ``/metrics``: the scrape reaches this port only, so the network policy can
  admit a collector to it without admitting it to the API.
- ``BACKEND_RELOAD``: ``true`` restarts the app when its code changes (a developer option; refused
  when ``ENV`` is production). Uvicorn then runs the app in a child process, so the child starts
  the metrics server (``reloading_app`` is the app factory) and this process does not.
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
from prometheus_client import start_http_server

if TYPE_CHECKING:
    from collections.abc import Mapping

_POSITIVE_INT = re.compile(r"[1-9][0-9]*", re.ASCII)


def _positive_int(environ: Mapping[str, str], name: str, default: int) -> int:
    raw = environ.get(name) or str(default)
    if not _POSITIVE_INT.fullmatch(raw):
        msg = f"{name} must be a positive integer (got '{raw}')"
        raise ValueError(msg)
    return int(raw)


def metrics_port(environ: Mapping[str, str]) -> int:
    """The metrics server's port; ``ValueError`` when it is invalid or the API's own port."""
    port = _positive_int(environ, "METRICS_PORT", 9102)
    if port == _positive_int(environ, "BACKEND_PORT", 8000):
        msg = f"METRICS_PORT must differ from BACKEND_PORT (both {port})"
        raise ValueError(msg)
    return port


_ENTRY = "your_service_name.backend.main:app"
_RELOAD_ENTRY = "your_service_name.backend.serve:reloading_app"


def _reload(environ: Mapping[str, str]) -> bool:
    """``BACKEND_RELOAD`` is on; ``ValueError`` when ``ENV`` is production."""
    if (environ.get("BACKEND_RELOAD") or "").strip().lower() not in {"true", "1", "yes"}:
        return False
    from your_service_name.backend.settings import PRODUCTION_ENVS, _EnvName  # noqa: PLC0415 - lazy

    # ENV as the app reads it: the environment, then .env (the one given here, if it has ENV).
    env = environ.get("ENV") or _EnvName().ENV
    if env.strip().lower() in PRODUCTION_ENVS:
        msg = "BACKEND_RELOAD is a developer option and is refused when ENV is production"
        raise ValueError(msg)
    return True


def build_options(environ: Mapping[str, str]) -> dict[str, Any]:
    """The keyword arguments for ``uvicorn.run``; ``ValueError`` names a bad variable."""
    options = _build_options(environ)
    if _reload(environ):
        # The reload child imports the factory, which starts the metrics server and returns the app.
        options.update(app=_RELOAD_ENTRY, factory=True, reload=True)
    return options


def _start_metrics(environ: Mapping[str, str]) -> None:
    """Start the metrics server; ``ValueError`` names ``METRICS_PORT`` when it cannot bind."""
    port = metrics_port(environ)
    try:
        start_http_server(port, addr=environ.get("BACKEND_BIND") or "0.0.0.0")  # noqa: S104
    except OSError as exc:
        msg = f"METRICS_PORT {port} cannot be bound ({exc.strerror or exc}); pick a free port"
        raise ValueError(msg) from exc


def reloading_app() -> Any:  # noqa: ANN401 - the ASGI app of BACKEND_ENTRY
    """The app factory under ``BACKEND_RELOAD``: runs in each reload child.

    The child owns the app's metrics registry, so it starts the metrics server (the previous child
    is gone before the next starts, which frees the port), then returns the app.
    """
    try:
        _start_metrics(os.environ)
    except ValueError as exc:
        sys.exit(f"ERROR: {exc}")
    from uvicorn.importer import import_from_string  # noqa: PLC0415

    return import_from_string(os.environ.get("BACKEND_ENTRY") or _ENTRY)


def _build_options(environ: Mapping[str, str]) -> dict[str, Any]:
    return {
        "app": environ.get("BACKEND_ENTRY") or _ENTRY,
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
        metrics_port(os.environ)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)  # noqa: T201
        return 1
    from your_service_name.backend.settings import ApiVersionSetting  # noqa: PLC0415 - after validation

    print(_banner(options, ApiVersionSetting().API_VERSION), flush=True)  # noqa: T201
    if not options.get("reload"):
        # The default registry: the app's instrumentation registers there when Uvicorn imports it,
        # in this same process (one process, no workers). Started once, before the app.
        try:
            _start_metrics(os.environ)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)  # noqa: T201
            return 1
    uvicorn.run(**options)
    return 0


if __name__ == "__main__":
    sys.exit(main())
