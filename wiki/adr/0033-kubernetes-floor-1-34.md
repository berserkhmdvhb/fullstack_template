# ADR-0033: Kubernetes 1.34 is the tested floor

## Status

Accepted.

## Date

2026-11-22

## Context

The chart claimed Kubernetes 1.30, which went end-of-life upstream in mid-2025 and was never tested. The pinned kind
publishes node images only for recent minors. A floor nobody tests is a guess.

## Decision

"Support what you test." The chart's `kubeVersion` is `>=1.34.0-0`, and the kind job runs two legs: the floor
(1.34) and kind's newest (1.37), node images pinned by digest. kubeconform validates against the 1.34 OpenAPI spec,
and the namespaces' Pod Security version labels are `v1.34`. At 1.34 every feature the chart uses is GA (native
sidecars, the preStop `sleep` action).

## Consequences

- Good: the floor is a fact, re-proven on every pull request.
- Bad: a cluster on 1.31 to 1.33 (provider extended support) refuses the install; upgrade the cluster first.

## Alternatives considered

- An older leg on an older kind node image: rejected, fragile, and it would prove only an unsupported version.

## Links

- [`charts/your-service-name/Chart.yaml`](../../charts/your-service-name/Chart.yaml),
  [`tools/kind-nodes.tsv`](../../tools/kind-nodes.tsv),
  [`.github/workflows/reusable-kind.yml`](../../.github/workflows/reusable-kind.yml)
- [Infrastructure: local cluster (kind)](../infrastructure.md#local-cluster-kind)
