# ADR-0011: An admin bootstrap creates the namespaces; the CI deployer is namespace-scoped

## Status

Accepted.

## Date

2026-11-20

## Context

If the app chart creates its namespaces, its RBAC and its quotas, the CI identity that runs `helm upgrade` needs
cluster-scoped rights: create namespaces, bind roles. A stolen or misused deploy token is then a cluster-admin token.

## Decision

The chart's `namespaces.create` and `rbac.create` are `false` by default. A one-time admin bootstrap
(`scripts/k8s-bootstrap.sh <env>`, rendering [`infra/k8s/bootstrap`](../../infra/k8s/bootstrap/Chart.yaml)) creates
the app and database namespaces with the restricted Pod Security labels, the ResourceQuota and LimitRange, the app's
Role and RoleBinding, and the deployer's namespace-scoped Role. The CI deployer then holds Helm's verbs in its two
namespaces and nothing else. `true` keeps a single-tenant path where the chart renders them.

## Consequences

- Good: the deploy identity cannot create namespaces, change RBAC or quotas, or reach another environment.
- Good: quotas and Pod Security labels are set by the admin and cannot be loosened by a chart change in a pull
  request.
- Bad: an extra admin step per environment, and a re-run when the bootstrap changes (the changelog's upgrade notes
  say when).

## Alternatives considered

- Let the chart create everything (`--create-namespace`): rejected for the cluster-scoped deployer it needs.

## Links

- [`scripts/k8s-bootstrap.sh`](../../scripts/k8s-bootstrap.sh),
  [`infra/k8s/bootstrap/templates/deployer-rbac.yaml`](../../infra/k8s/bootstrap/templates/deployer-rbac.yaml),
  [`infra/k8s/bootstrap/templates/quota.yaml`](../../infra/k8s/bootstrap/templates/quota.yaml)
- [Infrastructure: namespace bootstrap](../infrastructure.md#namespace-bootstrap-one-time-admin-step)
- [ADR-0021: keyless deploys](./0021-keyless-deploys-per-environment.md)
