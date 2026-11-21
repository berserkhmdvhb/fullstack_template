"""The architecture guide lists the middleware in the order main.py builds them."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ORDER = [
    "ClientIpMiddleware",
    "CorrelationIdMiddleware",
    "RequestLoggingMiddleware",
    "security headers",
    "RateLimitMiddleware",
    "CORSMiddleware",
]


def _section() -> str:
    text = (ROOT / "wiki" / "backend-architecture.md").read_text(encoding="utf-8")
    match = re.search(r"^## Middleware Stack\n(.*?)(?=^---$)", text, re.MULTILINE | re.DOTALL)
    assert match, "wiki/backend-architecture.md has no '## Middleware Stack' section"
    return match.group(1)


def test_the_order_list_starts_with_the_client_ip_middleware_and_matches_the_code() -> None:
    items = re.findall(r"^\d+\. (.*)$", _section(), re.MULTILINE)
    assert len(items) == len(ORDER), items
    for item, name in zip(items, ORDER, strict=True):
        assert name in item, (name, item)


def test_the_file_list_names_client_ip_and_links_the_trust_rules() -> None:
    section = _section()
    assert "`middleware/client_ip.py`" in section
    assert "security.md#client-ip" in section
