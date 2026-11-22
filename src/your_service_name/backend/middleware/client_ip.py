# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Trusted client-IP resolution: one resolver, one trust list, outermost in the stack.

Behind the gateway (and, in the Tunnel edge mode, ``cloudflared``) the TCP peer is a proxy Pod, so
without this every client shares the proxy's address: one rate-limit bucket, and logs that name the
proxy. This middleware rewrites ``scope["client"]`` to the real client, so everything downstream
(``request.client``, the rate limiter, the request logs) sees a single value.

Uvicorn's own proxy-header handling is deliberately OFF (``--no-proxy-headers`` in the launcher):
it cannot read ``CF-Connecting-IP``, and two resolvers would be two trust lists. This is the only
one, driven by ``TRUSTED_PROXY_CIDRS`` and ``TRUST_CF_CONNECTING_IP``.

Algorithm (``resolve_client_ip``)
---------------------------------
1. The peer is missing, is not an IP address (a unix socket), or is NOT inside
   ``TRUSTED_PROXY_CIDRS``: use the peer as-is and ignore EVERY forwarding header.
2. Otherwise, when ``TRUST_CF_CONNECTING_IP`` is on and ``CF-Connecting-IP`` holds exactly one
   valid IP: use it.
3. Otherwise, when ``X-Forwarded-For`` is present, walk its entries from the RIGHT, skipping those
   inside ``TRUSTED_PROXY_CIDRS``; the first untrusted valid IP is the client. An entry that is
   not a valid IP stops the walk and the last trusted hop seen (the peer to begin with) is used,
   never a value left of garbage. When every entry is trusted, the leftmost is used.
4. Otherwise the peer.

Addresses are normalised: an IPv4-mapped IPv6 address becomes IPv4, and a port or the brackets of
``[v6]:port`` are stripped. A repeated header is joined in order, as a proxy would have sent it.

The rewrite keeps the direct TCP peer in the scope (read it with ``direct_peer``): the auth
layer trusts the identity headers by that peer (``AUTH_PROXY_CIDRS``), never by a forwarded
address.

A trusted peer's ``X-Forwarded-Proto`` (``http`` or ``https`` only; the last, nearest value) also
sets ``scope["scheme"]``, replacing what Uvicorn's proxy headers did.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network

    from starlette.types import ASGIApp, Receive, Scope, Send

    Network = IPv4Network | IPv6Network
    Address = IPv4Address | IPv6Address

_HTTP_SCHEMES = {
    "http": {"http": "http", "https": "https"},
    "websocket": {"http": "ws", "https": "wss"},
}


def _parse_ip(raw: str) -> Address | None:
    """An IP from ``1.2.3.4``, ``1.2.3.4:80``, ``::1``, ``[::1]`` or ``[::1]:80``; else ``None``."""
    text = raw.strip()
    if text.startswith("["):
        text, _, _ = text[1:].partition("]")
    elif text.count(":") == 1:
        text = text.partition(":")[0]
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    return ip.ipv4_mapped or ip if isinstance(ip, ipaddress.IPv6Address) else ip


def _is_trusted(ip: Address, trusted: Iterable[Network]) -> bool:
    return any(ip in net for net in trusted)


# Namespaced by this module's import path, so no server or extension key can collide with it.
_DIRECT_PEER = f"{__name__}.direct_peer"


def direct_peer(scope: Scope) -> str | None:
    """The request's TCP peer, before any rewrite of ``scope["client"]`` by this middleware."""
    if _DIRECT_PEER in scope:
        peer: str | None = scope[_DIRECT_PEER]
        return peer
    client = scope.get("client")
    return client[0] if client else None


def peer_in(peer: str | None, networks: Iterable[Network]) -> bool:
    """Whether ``peer`` is an IP inside ``networks`` (a missing or non-IP peer never is)."""
    peer_ip = _parse_ip(peer) if peer else None
    return peer_ip is not None and _is_trusted(peer_ip, networks)


def resolve_client_ip(
    peer: str | None,
    forwarded_for: str | None,
    cf_connecting_ip: str | None,
    trusted: Sequence[Network],
    *,
    trust_cf: bool,
) -> str | None:
    """The client address for a request (see the module docstring); ``peer`` when not trusted."""
    peer_ip = _parse_ip(peer) if peer else None
    if peer_ip is None or not _is_trusted(peer_ip, trusted):
        return peer
    # Exactly one value: a repeated header joins to "a, b", and "a:1, b" must not resolve to "a".
    single = (
        cf_connecting_ip if trust_cf and cf_connecting_ip and "," not in cf_connecting_ip else None
    )
    if single and (cf := _parse_ip(single)):
        return str(cf)
    if forwarded_for:
        hop = peer_ip
        entries = [_parse_ip(e) for e in forwarded_for.split(",")]
        for entry in reversed(entries):
            if entry is None:
                return str(hop)
            if not _is_trusted(entry, trusted):
                return str(entry)
            hop = entry
        return str(hop)  # every entry is trusted: the leftmost
    return str(peer_ip)


class ClientIpMiddleware:
    """Pure ASGI: rewrite ``scope["client"]`` (and ``scheme``) from a trusted proxy's headers."""

    def __init__(self, app: ASGIApp, *, trusted: Sequence[Network], trust_cf: bool = False) -> None:
        self.app = app
        self._trusted = tuple(trusted)
        self._trust_cf = trust_cf

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Resolve the client for an http/websocket request, then call the app."""
        if scope["type"] not in _HTTP_SCHEMES or not self._trusted:
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        peer = client[0] if client else None
        if not peer_in(peer, self._trusted):
            await self.app(scope, receive, send)  # untrusted: no forwarding header is read
            return

        headers: dict[str, list[str]] = {}
        for name, value in scope.get("headers", []):
            headers.setdefault(name.decode("latin-1").lower(), []).append(value.decode("latin-1"))

        def joined(name: str) -> str | None:
            return ", ".join(headers[name]) if name in headers else None

        ip = resolve_client_ip(
            peer,
            joined("x-forwarded-for"),
            joined("cf-connecting-ip"),
            self._trusted,
            trust_cf=self._trust_cf,
        )
        scope.setdefault("state", {})  # shared by reference: outer layers read what inner ones set
        new_scope = {**scope, "client": (ip, client[1] if client else 0), _DIRECT_PEER: peer}
        proto = (joined("x-forwarded-proto") or "").split(",")[-1].strip().lower()
        if proto in ("http", "https"):
            new_scope["scheme"] = _HTTP_SCHEMES[scope["type"]][proto]
        await self.app(new_scope, receive, send)
