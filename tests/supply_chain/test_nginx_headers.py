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
VALUE = r"\"[^\"]*\"|'[^']*'|[^\s;]+"
ADD_HEADER = re.compile(rf"\badd_header\s+(\S+)\s+({VALUE})(?:\s+(always))?\s*;")
LOCATION = re.compile(r"^\s*location\s+([^{]+?)\s*\{(.*?)^\s{4}\}", re.MULTILINE | re.DOTALL)


def _unquote(value: str) -> str:
    return value[1:-1] if value[0] == value[-1] and value[0] in "\"'" else value


def _headers(block: str) -> list[tuple[str, str, bool]]:
    block = re.sub(r"#.*", "", block)  # comments may mention add_header
    found = [(m[1], _unquote(m[2]), m[3] == "always") for m in ADD_HEADER.finditer(block)]
    tokens = len(re.findall(r"\badd_header\b", block))
    assert tokens == len(found), "an add_header line this parser does not understand"
    return found


def _server_block() -> str:
    assert len(re.findall(r"^\s*server\s*\{", CONF, re.MULTILINE)) == 1, "exactly one server block"
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
        "/_next/static/": "public, max-age=31536000, immutable",
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


def test_no_location_sets_hsts() -> None:
    """HSTS is the edge's."""
    for location in _locations():
        assert "Strict-Transport-Security" not in {k for k, _, _ in _final(location)}, location


def test_cache_control_is_set_once_by_add_header_not_by_expires() -> None:
    """`expires` emits a Cache-Control of its own, so next to add_header it made two."""
    assert not re.search(r"^\s*expires\s", re.sub(r"#.*", "", CONF), re.MULTILINE)


def test_next_internals_are_hidden_from_proxied_responses() -> None:
    """x-middleware-rewrite carries the backend URL; http-level, so proxy locations inherit it."""
    http = CONF[: CONF.index("  server {")]
    hidden = set(
        re.findall(r"^\s*proxy_hide_header\s+(\S+);", re.sub(r"#.*", "", http), re.MULTILINE)
    )
    assert hidden == {"X-Middleware-Rewrite", "X-Powered-By"}
