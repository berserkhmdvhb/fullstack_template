# ADR-0036: Alert rules and scrapes in two flavours, Prometheus Operator and GKE Managed Prometheus

## Status

Accepted.

## Date

2026-11-20

## Context

The chart's alerts and scrape config were Prometheus Operator objects (`ServiceMonitor`, `PrometheusRule`). On GKE,
Managed Prometheus (GMP) reads its own kinds instead, and a namespaced GMP `Rules` object evaluates only its own
namespace's series.

## Decision

`monitoring.flavour` is `prometheus-operator` or `gmp`, rendering the same rule groups either as a `ServiceMonitor`
and `PrometheusRule`, or as a `PodMonitoring` and `Rules` (`monitoring.googleapis.com/v1`). With `gmp`, the app's own
alert is in `Rules` and the cluster-level alerts are a cluster-scoped `ClusterRules`, which the admin bootstrap
renders (`gmpClusterRules: true`), because the deployer has no cluster rights. kubeconform carries both sets of CRD
schemas, and promtool tests the rules.

## Consequences

- Good: one rule source for both stacks; GKE adopters need no Prometheus Operator.
- Bad: two renders to test, and the GMP cluster rules need a bootstrap re-run when they change.

## Alternatives considered

- Prometheus Operator only: rejected, it leaves GKE adopters to translate the rules.

## Links

- [Infrastructure: metrics port and monitoring flavours](../infrastructure.md#metrics-port-and-monitoring-flavours)
- [`charts/your-service-name/templates/gmp-rules.yaml`](../../charts/your-service-name/templates/gmp-rules.yaml),
  [`charts/your-service-name/templates/prometheusrule.yaml`](../../charts/your-service-name/templates/prometheusrule.yaml)
- [Operations runbook: alerts](../operations-runbook.md#alerts)
