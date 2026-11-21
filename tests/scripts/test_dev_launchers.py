# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The developer launchers keep the client-IP and header handling of the image launcher."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("script", ["run_backend.sh", "infra/runtime/entrypoint.sh"])
def test_the_dev_launchers_turn_uvicorns_proxy_headers_off(script: str) -> None:
    """One resolver only: a developer launcher must not run Uvicorn's own either."""
    assert "--no-proxy-headers" in (ROOT / script).read_text(encoding="utf-8")


def test_the_entrypoint_hides_the_server_header() -> None:
    """The backend must not advertise its software in `server` (the live test pins it)."""
    assert "--no-server-header" in (ROOT / "infra/runtime/entrypoint.sh").read_text(
        encoding="utf-8"
    )
