# ADR-0012: CPU requests without CPU limits; memory limit equals the request

## Status

Accepted.

## Date

2026-11-20

## Context

A CPU limit enforces a CFS quota: a container that uses its share early in a period is throttled for the rest, which
shows up as latency spikes even on an idle node. Memory is not compressible: without a limit one Pod can push its
neighbours out of memory.

## Decision

Every container has a CPU request and no CPU limit by default (an operator may set `limits.cpu`). Memory has a
request and a limit, and the limit must equal the request: a different value fails the render. The bootstrap's
LimitRange and ResourceQuota bound each namespace.

## Consequences

- Good: no throttling; a container may use idle CPU on its node, and requests still drive scheduling.
- Good: memory equal to request gives predictable eviction and no overcommit for memory.
- Bad: a busy container can take spare CPU that a neighbour later wants; requests guarantee only the floor.
- Bad: memory headroom must be sized up front; a spike above the request is an OOM kill.

## Alternatives considered

- CPU limit equal to request (Guaranteed QoS): rejected, it throttles for no gain on shared nodes.
- Memory limit above the request: rejected, it overcommits the node's memory.

## Scanners

Kubescape's C-0270 ("Ensure CPU limits are set") contradicts this decision, so
[`infra/security/local/kubescape-exceptions.json`](../../infra/security/local/kubescape-exceptions.json) disables that
one control (C-0271, the memory limits, stays). `scan-k8s.sh` leaves the results the exception disabled out of the SARIF
it uploads and counts them in its log; Kubescape's JSON report keeps them. Code scanning closes the open C-0270 alerts
on the next scan of the branch, as no longer found. An adopter who sets CPU limits removes the file's policy, or points
`KUBESCAPE_EXCEPTIONS` at another file (empty: none).

## Links

- [`charts/your-service-name/values.yaml`](../../charts/your-service-name/values.yaml) (`resources`)
- [Infrastructure: workloads, resources and shutdown](../infrastructure.md#workloads-rollout-resources-and-shutdown)
