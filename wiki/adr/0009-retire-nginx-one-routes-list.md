# ADR-0009: Retire the nginx tier; one routes list feeds every edge

## Status

Accepted.

## Date

2026-11-22

## Context

The original stack put an nginx tier in front of the frontend to serve static assets and route paths. That was a
fourth image to build, sign, scan and patch, a second frontend build with a risk of asset skew between the two, and a
second place where routing was defined.

## Decision

There is no nginx tier. The Next.js server serves its own pages and `/_next/static` assets, which are hashed and
immutable with a one-year cache; a CDN in front (Cloudflare or another) caches them at the edge. Next.js's
`deploymentId` is set from the build version, so a client caught mid-rollout reloads instead of breaking. Routing is
defined once, as `edge.routes` (the backend paths, and everything else to the frontend), and rendered into the
`HTTPRoute` (gateway mode) or the Ingress (ingress mode); in tunnel mode the tunnel's routes are the same list, which
`NOTES.txt` prints.

## Consequences

- Good: two app images instead of three, one frontend build, one routing table.
- Good: the static cache policy lives with the code that hashes the assets.
- Bad: static requests that miss the CDN reach Node instead of nginx; the CDN carries the load.
- Bad: the former nginx practices (headers, limits, timeouts) had to find new homes; each is listed in the docs.

## Alternatives considered

- Keep nginx for static assets: rejected for the image, build and skew costs above.
- Route in each edge's own template: rejected, three tables would drift.

## Links

- [`frontend/next.config.ts`](../../frontend/next.config.ts),
  [`frontend/scripts/deployment-id.mjs`](../../frontend/scripts/deployment-id.mjs)
- [Infrastructure: the routes list](../infrastructure.md#the-routes-list),
  [where the former nginx practices live now](../infrastructure.md#where-the-former-nginx-practices-live-now)
- [Frontend deployment: static assets](../frontend-deployment.md#static-assets)
- [ADR-0008: edge modes](./0008-edge-modes.md)
