# ADR-0008: Three edge modes, with Gateway API as the default

## Status

Accepted.

## Date

2026-11-20

## Context

The original chart assumed ingress-nginx, with nginx annotations and a NetworkPolicy that hard-coded its namespace.
The ingress-nginx controller was retired in March 2026. Adopters run Gateway API implementations, other Ingress
controllers, or a Cloudflare Tunnel with no public load balancer at all.

## Decision

The chart's `edge.mode` selects one of three edges, each rendered from the same routes list
([ADR-0009](./0009-retire-nginx-one-routes-list.md)):
- `gateway` (the default in `values.yaml`): Gateway API `HTTPRoute`s attached to `edge.gateway.parentRefs`, with
  optional GKE Gateway policies;
- `tunnel`: no in-cluster edge object; the Cloudflare Tunnel's `cloudflared` reaches the Services, and the
  NetworkPolicy admits the `cloudflared` namespace;
- `ingress`: a legacy opt-in, controller-agnostic (no controller annotations by default).

The app NetworkPolicy admits the edge's namespace through a configurable selector, per mode: the Gateway's
namespaces, the `cloudflared` namespace, or a selector the `ingress` mode requires the operator to set.

## Consequences

- Good: Gateway API is the portable Kubernetes standard, so a generic public template defaults to it; an adopter
  whose platform uses the tunnel sets `tunnel` in their own values.
- Good: no controller-specific behaviour hides in annotations.
- Bad: three modes to test; helm-unittest snapshots and kubeconform cover each.
- Bad: tunnel mode depends on Cloudflare for TLS, HSTS and rate limits at the edge
  ([ADR-0010](./0010-one-source-per-security-header.md)).

## Alternatives considered

- `tunnel` as the template's default: rejected, it ties a generic template to one vendor.
- Drop Ingress: rejected, the Ingress API is not deprecated, only one controller was retired, and many clusters still
  run other controllers.

## Links

- [`charts/your-service-name/values.yaml`](../../charts/your-service-name/values.yaml) (`edge`),
  [`templates/httproute.yaml`](../../charts/your-service-name/templates/httproute.yaml),
  [`templates/ingress.yaml`](../../charts/your-service-name/templates/ingress.yaml),
  [`templates/network-policy.yaml`](../../charts/your-service-name/templates/network-policy.yaml)
- [Infrastructure: the three modes](../infrastructure.md#the-three-modes),
  [NetworkPolicy per mode](../infrastructure.md#networkpolicy-per-mode)
