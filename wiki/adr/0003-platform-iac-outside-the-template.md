# ADR-0003: Platform infrastructure-as-code lives outside the template

## Status

Accepted.

## Date

2026-11-20

## Context

A service built from the template runs on a cluster and behind a DNS zone that other services share: the cloud
network, the cluster, the registry, Workload Identity Federation, the Cloudflare zone, the tunnel and Access. The
setup guides do these steps by hand. Code for them has to live somewhere, with its state, reviews and apply gate.

## Decision

The template ships no infrastructure-as-code. The shared platform (cloud network and NAT, node identity, cluster,
registry, Workload Identity Federation and the deployers; the zone settings, tunnel, Access apps, tokens and storage)
and each service's own edge objects (its tunnel routes, Access apps and DNS records) belong in the adopter's separate
infrastructure-as-code repository. The setup guides keep every manual step as the fallback, mark each step that code
may own, and keep their verification steps, which hold however the step was done.

## Consequences

- Good: the template stays generic; a shared cluster or zone is not owned by whichever service came first.
- Good: platform changes get their own review, state and apply approval, away from application pull requests.
- Bad: an adopter without such a repository does the setup by hand, or writes the code themselves.
- Bad: a step done by hand and later brought under code needs an import or a rebuild (the guides say which).

## Alternatives considered

- Everything inside the template repository: rejected, because the cluster and the zone are shared and would then
  belong to one service's template.
- A service-level module inside the template: kept out too, so the template holds no provider state or cloud IDs;
  the service's routes and Access apps live beside the platform code that owns the tunnel and the zone.

## Links

- [Platform setup](../platform-setup.md)
- [GKE cluster setup](../gke-cluster-setup.md), [Cloudflare setup](../cloudflare-setup.md)
