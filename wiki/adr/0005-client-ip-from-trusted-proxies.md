# ADR-0005: The client IP comes from one resolver that trusts listed proxies only

## Status

Accepted.

## Date

2026-11-20

## Context

Behind the gateway, and behind `cloudflared` in tunnel mode, the TCP peer of every request is a proxy Pod. Without a
resolver every client shares the proxy's address: one rate-limit bucket and logs that name the proxy. Forwarding
headers are client-controlled unless a trusted proxy set them, and Uvicorn's own proxy-header support cannot read
Cloudflare's `CF-Connecting-IP`.

## Decision

One ASGI middleware, outermost in the stack, resolves the client IP. It trusts forwarding headers only when the TCP
peer is inside `TRUSTED_PROXY_CIDRS` (empty by default): then `CF-Connecting-IP` when `TRUST_CF_CONNECTING_IP` is on
and it holds one valid IP, else `X-Forwarded-For` walked from the right past trusted hops; otherwise the peer itself.
The rate limiter and the logs use the resolved address. Uvicorn's proxy headers are off, so there is one trust list.
The auth layer trusts identity headers by the direct peer (`AUTH_PROXY_CIDRS`), never by a forwarded address.

## Consequences

- Good: per-client rate limits and accurate logs behind any of the edge modes.
- Good: a spoofed header from an untrusted peer is ignored; tests cover the spoofing cases.
- Bad: the operator must set `TRUSTED_PROXY_CIDRS` to the gateway and `cloudflared` Pod ranges; left empty, every
  client is the proxy (safe, but one bucket).
- Bad: a trusted proxy that passes a client's header through unchanged makes that header trusted; the proxies must
  overwrite it ([what the trusted proxies must do](../security.md#what-the-trusted-proxies-must-do)).

## Alternatives considered

- Uvicorn `--proxy-headers` with `--forwarded-allow-ips`: rejected, it cannot read `CF-Connecting-IP`, and a second
  resolver would be a second trust list.
- Trust `X-Forwarded-For` from anyone: rejected, any client could pick its own rate-limit bucket.

## Links

- [`src/your_service_name/backend/middleware/client_ip.py`](../../src/your_service_name/backend/middleware/client_ip.py)
- [Security: client IP](../security.md#client-ip), [Infrastructure: client IP behind the edge](../infrastructure.md#client-ip-behind-the-edge)
