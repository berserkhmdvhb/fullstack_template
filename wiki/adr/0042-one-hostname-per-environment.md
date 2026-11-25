# ADR-0042: One hostname per environment

## Status

Accepted.

## Date

2026-11-25

## Context

The chart asked for three hostnames per environment (`root`, `app` and `api`): nine across dev, qa and
production, plus three per pull request preview. Each is a DNS record, the tunnel routes NOTES prints for it and, while the
environment is private, a Cloudflare Access application.

The browser needs one. It calls `/api` on the page's own host, and the Next.js server forwards the frontend's
own calls to the backend by Service name, so the browser needs nothing from the extra hosts. The backend's own
defaults read no host to build CORS rules or `ALLOWED_HOSTS`; the chart sets `ALLOWED_HOSTS` from the edge hosts
and the backend's Service names. The `root` host already served every route, so `app` duplicated it.

The free Cloudflare Universal SSL certificate covers `<zone>` and `*.<zone>`, never a second level such as
`*.dev.<zone>`, so every host must be one label under the zone. Many services may share one domain.

## Decision

`edge.hosts.root` serves the whole app: one origin, so no CORS, host-locked cookies, `connect-src 'self'`, and
one certificate, one DNS record and one Access application per environment (the tunnel routes for a host are the NOTES table's lines). `edge.hosts.api` is optional: a
backend-only host, serving the `backend` routes, for clients that are not a browser and need their own rules (a
rate limit, an Access policy, a service token). Empty, nothing renders for it. There is no `app` host: the
schema refuses it, and refuses a missing or empty `root`.

The naming scheme is `<service>[-<env>].<domain>`, always one label under the zone: the service name in every
host lets many services share one domain, and previews already follow it (`<service>-pr-<N>.<preview-zone>`).
Production is `<service>.<domain>`, dev and qa `<service>-dev.<domain>` and `<service>-qa.<domain>`. An api
host, when set, is `<service>-api[-<env>].<domain>`. A flagship service may take the apex
(`edge.hosts.root: <domain>`); Cloudflare flattens the apex CNAME, so a tunnel route works there.

The template has no release yet, so this is not a migration: adopters configure hosts when they adopt.

## Consequences

- Good: a third of the DNS records and Access applications (and of the tunnel routes' hostnames); a preview needs one hostname.
- Good: one origin means no CORS configuration and no cross-host cookie or CSP rules.
- Good: a platform that hosts many services can adopt the same scheme under one domain.
- Bad: a non-browser client that needs different edge rules needs `edge.hosts.api` set, and its own DNS record,
  routes and Access application.
- Bad: a values file that still sets an `app` host fails the render.

## Alternatives considered

- **A, chosen:** one `root` host, an optional `api` host, no `app`.
- **B, keep all three and document them:** rejected; it triples the setup for no behaviour a browser needs.
- **C, rename the keys (`edge.host`, `edge.apiHost`):** rejected; churn in every values file and test for no
  behaviour.

## Links

- [Infrastructure: edge hostnames](../infrastructure.md#edge-hostnames)
- [Cloudflare setup: the hostname plan](../cloudflare-setup.md#22-the-hostname-plan)
