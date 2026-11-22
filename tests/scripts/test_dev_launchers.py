# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The developer launchers keep the client-IP and header handling of the image launcher."""

from __future__ import annotations

from your_service_name.backend import serve


def test_the_serve_module_turns_uvicorns_proxy_and_server_headers_off() -> None:
    options = serve.build_options({})
    assert options["proxy_headers"] is False
    assert options["server_header"] is False
