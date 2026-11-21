"""The client-IP section of the security guide tells adopters what their proxies must do."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _section() -> str:
    text = (ROOT / "wiki" / "security.md").read_text(encoding="utf-8")
    match = re.search(r"^## Client IP\n(.*?)(?=^## )", text, re.MULTILINE | re.DOTALL)
    assert match, "wiki/security.md has no '## Client IP' section"
    return match.group(1)


def test_the_section_says_what_the_trusted_proxies_must_do() -> None:
    section = _section()
    assert "### What the trusted proxies must do" in section
    for phrase in ("overwrite or append", "NetworkPolicy", "only way in", "X-Forwarded-Proto"):
        assert phrase in section, phrase
    assert "(#networkpolicy)" in section


def test_the_section_covers_each_edge_mode() -> None:
    section = _section()
    for mode in ("Ingress", "Tunnel", "Google"):
        assert re.search(rf"^\| \*\*{mode}", section, re.MULTILINE), mode


def test_every_proxy_behaviour_claim_cites_its_vendor_documentation() -> None:
    section = _section()
    for url in (
        "https://kubernetes.github.io/ingress-nginx/user-guide/nginx-configuration/configmap/",
        "https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_conn_man/headers",
        "https://cloud.google.com/load-balancing/docs/https",
    ):
        assert url in section, url


def test_the_section_documents_the_host_bits_rule() -> None:
    assert "host bits" in _section()


def test_the_keep_alive_docs_name_the_ingress_nginx_setting() -> None:
    for path in ("wiki/environment-variables.md", "charts/your-service-name/values.yaml"):
        assert "upstream-keepalive-timeout" in (ROOT / path).read_text(encoding="utf-8"), path
