"""The final add_header set of every location in infra/nginx/nginx.conf.

nginx inherits a level's ``add_header`` only while the inner level declares none, so the final
set of a location is its own ``add_header`` lines when it has any, else the server's.
"""

import re
from pathlib import Path

CONF = (Path(__file__).resolve().parent.parent.parent / "infra/nginx/nginx.conf").read_text(
    encoding="utf-8"
)
SECURITY = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'",
}
ADD_HEADER = re.compile(r'^\s*add_header\s+(\S+)\s+"([^"]*)"(?:\s+(always))?\s*;', re.MULTILINE)
LOCATION = re.compile(r"^\s*location\s+([^{]+?)\s*\{(.*?)^\s{4}\}", re.MULTILINE | re.DOTALL)


def _headers(block: str) -> list[tuple[str, str, bool]]:
    return [(m[1], m[2], m[3] == "always") for m in ADD_HEADER.finditer(block)]


def _server_block() -> str:
    start = CONF.index("  server {")
    return CONF[start:]


def _locations() -> dict[str, list[tuple[str, str, bool]]]:
    return {m[1]: _headers(m[2]) for m in LOCATION.finditer(_server_block())}


def _server_level() -> list[tuple[str, str, bool]]:
    server = _server_block()
    return _headers(LOCATION.sub("", server))


def _final(location: str) -> list[tuple[str, str, bool]]:
    return _locations()[location] or _server_level()


def test_the_server_level_set_is_the_three_security_headers() -> None:
    assert {k: v for k, v, _ in _server_level()} == SECURITY
    assert len(_server_level()) == len(SECURITY)
    assert all(always for _, _, always in _server_level()), "error responses must carry them too"


def test_every_location_is_known() -> None:
    assert set(_locations()) == {
        "/",
        "/_next/static/",
        "= /logo.png",
        "= /favicon.ico",
        "= /robots.txt",
        "= /sitemap.xml",
        "= /health.txt",
        "= /healthz",
    }


def test_the_locations_without_add_header_inherit_the_security_set() -> None:
    for location in ("/", "= /healthz"):
        assert _locations()[location] == []
        assert {k: v for k, v, _ in _final(location)} == SECURITY


def test_cache_control_locations_redeclare_all_three_security_headers() -> None:
    caching = {
        "/_next/static/": "public, immutable",
        "= /logo.png": "public, max-age=604800",
        "= /favicon.ico": "public, max-age=604800",
        "= /robots.txt": "no-cache",
        "= /sitemap.xml": "no-cache",
        "= /health.txt": "no-cache",
    }
    for location, cache in caching.items():
        final = _final(location)
        names = [k for k, _, _ in final]
        assert len(names) == len(set(names)), f"{location}: a header is declared twice"
        assert {k: v for k, v, _ in final} == {"Cache-Control": cache, **SECURITY}, location
        assert all(always for k, _, always in final if k in SECURITY), location


def test_no_location_sets_hsts_or_cross_origin_headers() -> None:
    """HSTS is the edge's; the cross-origin pair is the frontend layer's."""
    for location in _locations():
        names = {k for k, _, _ in _final(location)}
        assert not names & {"Strict-Transport-Security", "Cross-Origin-Opener-Policy"}, location
