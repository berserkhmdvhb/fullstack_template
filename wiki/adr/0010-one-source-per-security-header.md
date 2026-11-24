# ADR-0010: One source per security header, and HSTS from one place per deployment

## Status

Accepted. The HSTS part was refined on 2026-11-24 (one chart value, `edge.hsts`).

## Date

2026-11-20

## Context

The original stack set `Strict-Transport-Security` in three places with three different `max-age` values, and other
headers in more than one layer. A browser keeps whichever it saw last, so the effective policy was accidental. The
edge differs per deployment: in tunnel mode Cloudflare is the outer edge; in gateway and ingress mode the operator's
Gateway or controller may or may not set headers.

## Decision

Each header has one source per layer. The backend sets its own API headers (`backend/middleware/security.py`); the
frontend sets its CSP with a per-request nonce and its other page headers (`next.config.ts`, `proxy.ts`); the chart
renders no header filter or controller snippet. HSTS has exactly one source per deployment, picked by the chart's
`edge.hsts`:
- `auto` (the default): `edge` in tunnel mode, where Cloudflare sets HSTS; `app` in gateway and ingress mode;
- `app`: the backend sends HSTS (`HSTS_ENABLED`), only on a request it sees as https;
- `edge`: the edge sets it and the chart sets the backend's `HSTS_ENABLED=false`.

`edge.hsts` is the only knob: the chart refuses `config.HSTS_ENABLED`. The frontend never sends HSTS. A test per
layer pins the final header set.

## Consequences

- Good: one `max-age` per host, chosen on purpose; a second knob fails the render instead of drifting.
- Good: tunnel mode leaves HSTS to Cloudflare, where its staged rollout is documented.
- Bad: `app` sends nothing until the backend sees https, which needs the edge's Pod range in `TRUSTED_PROXY_CIDRS`
  and the WAF off; with the shipped values nothing sends HSTS until the operator chooses a source.
- Bad: the app host gets HSTS from the backend only through a same-origin API call; an edge source covers every host
  directly.

## Alternatives considered

- HSTS at the edge only, always: the first plan; rejected for gateway and ingress mode, because the template cannot
  know whether the operator's Gateway sets it.
- Keep the headers in every layer "for defence in depth": rejected, conflicting values are worse than one.

## Links

- [Security: HSTS](../security.md#hsts), [edge headers](../security.md#edge-headers)
- [`charts/your-service-name/values.yaml`](../../charts/your-service-name/values.yaml) (`edge.hsts`),
  [`tests/hsts_test.yaml`](../../charts/your-service-name/tests/hsts_test.yaml)
- [`src/your_service_name/backend/middleware/security.py`](../../src/your_service_name/backend/middleware/security.py),
  [`frontend/next.config.ts`](../../frontend/next.config.ts)
- [Cloudflare setup: HSTS](../cloudflare-setup.md#11-hsts-once-everything-is-https)
