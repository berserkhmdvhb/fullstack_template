# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Client-IP resolution: every branch of the algorithm, and the ASGI wiring around it."""

from __future__ import annotations

import ipaddress
from collections.abc import MutableMapping
from typing import TYPE_CHECKING, Any

import pytest

from your_service_name.backend.middleware.client_ip import ClientIpMiddleware, resolve_client_ip

if TYPE_CHECKING:
    from starlette.types import Message, Receive, Scope, Send

TRUSTED = [ipaddress.ip_network(c) for c in ("10.0.0.0/8", "fd00::/8")]
PEER = "10.1.2.3"  # a trusted proxy
STRANGER = "198.51.100.9"  # not trusted (TEST-NET-2)
CLIENT = "203.0.113.5"  # the real client (TEST-NET-3)
FORGED = "192.0.2.66"


def resolve(
    peer: str | None = PEER,
    xff: str | None = None,
    cf: str | None = None,
    *,
    trust_cf: bool = False,
    trusted: list[Any] = TRUSTED,
) -> str | None:
    return resolve_client_ip(peer, xff, cf, trusted, trust_cf=trust_cf)


class TestUntrustedPeer:
    """Forged headers from a peer outside TRUSTED_PROXY_CIDRS are ignored."""

    def test_forged_forwarded_for_is_ignored(self) -> None:
        assert resolve(STRANGER, xff=FORGED) == STRANGER

    def test_forged_cf_connecting_ip_is_ignored_even_with_cf_trust(self) -> None:
        assert resolve(STRANGER, cf=FORGED, trust_cf=True) == STRANGER

    def test_both_forged_headers_are_ignored(self) -> None:
        assert resolve(STRANGER, xff=FORGED, cf=FORGED, trust_cf=True) == STRANGER

    def test_no_trusted_proxies_trusts_nothing(self) -> None:
        assert resolve(PEER, xff=FORGED, cf=FORGED, trust_cf=True, trusted=[]) == PEER

    @pytest.mark.parametrize("peer", [None, "", "/run/app.sock", "not-an-ip"])
    def test_a_missing_or_non_ip_peer_is_used_as_is(self, peer: str | None) -> None:
        assert resolve(peer, xff=FORGED, cf=FORGED, trust_cf=True) == peer


class TestCfConnectingIp:
    def test_trusted_peer_with_cf_trust_uses_the_cf_header(self) -> None:
        assert resolve(cf=CLIENT, trust_cf=True) == CLIENT

    def test_cf_wins_over_forwarded_for(self) -> None:
        assert resolve(xff=FORGED, cf=CLIENT, trust_cf=True) == CLIENT

    def test_cf_header_is_ignored_while_cf_trust_is_off(self) -> None:
        assert resolve(cf=FORGED, trust_cf=False) == PEER
        assert resolve(xff=CLIENT, cf=FORGED, trust_cf=False) == CLIENT

    @pytest.mark.parametrize(
        "cf",
        [
            f"{FORGED}, {CLIENT}",
            f"{FORGED}:1, {CLIENT}",  # the first value alone parses (with a port): still two values
            f"[2001:db8::1], {CLIENT}",
            "garbage",
            "",
        ],
    )
    def test_an_invalid_or_multiple_cf_value_falls_through(self, cf: str) -> None:
        assert resolve(xff=CLIENT, cf=cf, trust_cf=True) == CLIENT
        assert resolve(cf=cf, trust_cf=True) == PEER

    def test_cf_ipv6_and_port_forms(self) -> None:
        assert resolve(cf="2001:db8::7", trust_cf=True) == "2001:db8::7"
        assert resolve(cf="[2001:db8::7]:443", trust_cf=True) == "2001:db8::7"
        assert resolve(cf=f"{CLIENT}:5555", trust_cf=True) == CLIENT


class TestForwardedFor:
    def test_the_rightmost_untrusted_entry_is_the_client(self) -> None:
        assert resolve(xff=CLIENT) == CLIENT

    def test_spoofed_left_entries_are_ignored(self) -> None:
        assert resolve(xff=f"{FORGED}, {CLIENT}, 10.9.9.9") == CLIENT

    def test_a_spoofed_entry_added_by_the_client_does_not_win(self) -> None:
        # the client sent "FORGED"; the gateway appended the address it saw
        assert resolve(xff=f"{FORGED}, {CLIENT}") == CLIENT

    def test_all_trusted_uses_the_leftmost(self) -> None:
        assert resolve(xff="10.0.0.1, 10.0.0.2, 10.0.0.3") == "10.0.0.1"

    def test_garbage_stops_the_walk_at_the_last_trusted_hop(self) -> None:
        assert resolve(xff=f"{CLIENT}, garbage, 10.0.0.2") == "10.0.0.2"
        assert resolve(xff=f"{CLIENT}, garbage") == PEER
        assert resolve(xff=f"{CLIENT}, ,") == PEER

    def test_no_value_is_taken_from_left_of_garbage(self) -> None:
        assert resolve(xff=f"{FORGED}, garbage, {CLIENT}") == CLIENT
        assert resolve(xff=f"{FORGED}, garbage, 10.0.0.2") == "10.0.0.2"

    def test_without_the_header_the_peer_is_used(self) -> None:
        assert resolve() == PEER

    def test_ipv6_entries_and_trusted_ipv6_hops(self) -> None:
        assert resolve(xff="2001:db8::1, fd00::5") == "2001:db8::1"
        assert resolve("fd00::9", xff="2001:db8::1") == "2001:db8::1"

    def test_brackets_and_ports_are_stripped(self) -> None:
        assert resolve(xff=f"[2001:db8::2]:1234, {CLIENT}:99") == CLIENT
        assert resolve(xff="[2001:db8::2]:1234") == "2001:db8::2"

    def test_ipv4_mapped_ipv6_is_normalised_before_the_trust_check(self) -> None:
        assert resolve(xff=f"::ffff:{CLIENT}") == CLIENT
        assert resolve(xff=f"{CLIENT}, ::ffff:10.0.0.4") == CLIENT
        assert resolve("::ffff:10.1.2.3", xff=CLIENT) == CLIENT

    def test_a_forwarded_for_from_a_trusted_peer_only_is_not_a_cf_header(self) -> None:
        assert resolve(xff=CLIENT, trust_cf=True) == CLIENT


# --- ASGI wiring ----------------------------------------------------------------


async def _run(
    peer: tuple[str, int] | None,
    headers: list[tuple[str, str]],
    scope_type: str = "http",
    *,
    trust_cf: bool = False,
    trusted: list[Any] = TRUSTED,
) -> MutableMapping[str, Any]:
    """The scope the wrapped app receives for a request from ``peer`` with ``headers``."""
    seen: dict[str, Any] = {}

    async def app(scope: Scope, _receive: Receive, _send: Send) -> None:
        seen.update(scope)

    async def receive() -> Message:  # pragma: no cover - never awaited
        return {"type": "http.request"}

    async def send(_message: Message) -> None:  # pragma: no cover - never awaited
        return None

    scope: Scope = {
        "type": scope_type,
        "scheme": "http",
        "client": peer,
        "headers": [(k.encode(), v.encode()) for k, v in headers],
    }
    await ClientIpMiddleware(app, trusted=trusted, trust_cf=trust_cf)(scope, receive, send)
    return seen


class TestMiddleware:
    async def test_it_rewrites_the_client_and_keeps_the_port(self) -> None:
        seen = await _run((PEER, 4242), [("X-Forwarded-For", CLIENT)])
        assert seen["client"] == (CLIENT, 4242)

    async def test_repeated_headers_are_joined_in_order(self) -> None:
        headers = [("x-forwarded-for", f"{FORGED}, {CLIENT}"), ("x-forwarded-for", "10.0.0.9")]
        seen = await _run((PEER, 1), headers)
        assert seen["client"][0] == CLIENT

    async def test_repeated_cf_headers_are_not_exactly_one_ip(self) -> None:
        headers = [("cf-connecting-ip", FORGED), ("cf-connecting-ip", CLIENT)]
        seen = await _run((PEER, 1), headers, trust_cf=True)
        assert seen["client"][0] == PEER

    async def test_an_untrusted_peer_keeps_its_scope_untouched(self) -> None:
        headers = [("x-forwarded-for", FORGED), ("x-forwarded-proto", "https")]
        seen = await _run((STRANGER, 7), headers)
        assert seen["client"] == (STRANGER, 7)
        assert seen["scheme"] == "http"

    async def test_no_peer(self) -> None:
        seen = await _run(None, [("x-forwarded-for", FORGED)])
        assert seen["client"] is None

    async def test_empty_trust_list_is_a_pass_through(self) -> None:
        seen = await _run((PEER, 1), [("x-forwarded-for", FORGED)], trusted=[])
        assert seen["client"] == (PEER, 1)

    async def test_a_trusted_peer_sets_the_scheme_from_forwarded_proto(self) -> None:
        seen = await _run((PEER, 1), [("x-forwarded-proto", "https")])
        assert seen["scheme"] == "https"

    async def test_the_nearest_forwarded_proto_value_is_used(self) -> None:
        seen = await _run((PEER, 1), [("x-forwarded-proto", "http, https")])
        assert seen["scheme"] == "https"

    @pytest.mark.parametrize("proto", ["ftp", "javascript", "", "https://x"])
    async def test_only_http_and_https_are_accepted_as_the_scheme(self, proto: str) -> None:
        seen = await _run((PEER, 1), [("x-forwarded-proto", proto)])
        assert seen["scheme"] == "http"

    async def test_websocket_schemes(self) -> None:
        seen = await _run((PEER, 1), [("x-forwarded-proto", "https")], "websocket")
        assert seen["scheme"] == "wss"

    async def test_lifespan_scopes_pass_through(self) -> None:
        seen = await _run((PEER, 1), [("x-forwarded-for", FORGED)], "lifespan")
        assert seen["client"] == (PEER, 1)
