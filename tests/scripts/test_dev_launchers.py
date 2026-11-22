# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The developer launchers keep the client-IP and header handling of the image launcher."""

from __future__ import annotations

from pathlib import Path

import pytest

from your_service_name.backend import serve

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("script", ["run_backend.sh", "infra/runtime/entrypoint.sh"])
def test_the_dev_launchers_pass_the_options_to_the_serve_module_not_to_uvicorn(
    script: str,
) -> None:
    """The proxy-header, server-header and access-log options live in `serve.build_options`."""
    text = (ROOT / script).read_text(encoding="utf-8")
    assert "--no-proxy-headers" not in text
    assert "--no-server-header" not in text


def test_the_serve_module_turns_uvicorns_proxy_and_server_headers_off() -> None:
    options = serve.build_options({})
    assert options["proxy_headers"] is False
    assert options["server_header"] is False
