# ADR-0035: kind is the default local cluster, matching CI

## Status

Accepted.

## Date

2026-11-20

## Context

A local cluster that differs from CI's finds different bugs. The original template documented minikube, while CI
needs a cluster it can create in a runner in seconds.

## Decision

kind is the default: `make kind-up` and `make kind-deploy` run the same sequence locally and in CI, on the pinned node
images, in a cluster named `fst` with its own kubeconfig, so nothing touches another cluster. minikube stays
documented as an option.

## Consequences

- Good: a red kind job in CI reproduces locally with the same commands.
- Bad: kind needs docker; minikube users follow a separate guide.

## Alternatives considered

- minikube as the default: rejected, CI does not run it.

## Links

- [`scripts/kind-up.sh`](../../scripts/kind-up.sh), [`scripts/kind-deploy.sh`](../../scripts/kind-deploy.sh)
- [Infrastructure: local cluster (kind)](../infrastructure.md#local-cluster-kind),
  [Local development on minikube](../local-development-minikube.md)
