# ADR-0046: A read-only weekly comparison finds live drift

## Status

Accepted.

## Date

2026-11-25

## Context

Deploys are pushed from GitHub Actions; nothing compares the cluster with Git afterwards. A hand edit (`kubectl
edit`, `kubectl scale`, a manual `helm upgrade`, a deleted or an extra object) goes unnoticed until the next
deploy overwrites it, or keeps it. Any check needs an identity on the cluster, and Helm's release Secrets hold the
rendered manifests and values, so reading them, or holding `patch`, would widen what that identity could see or do.

## Decision

[`drift.yml`](../../.github/workflows/drift.yml) runs weekly and on demand, and compares, for dev, qa and
production, the chart rendered from the commit of the environment's latest successful deployment (the lookup the
weekly re-scan uses) with the live objects:

- The render is the deploy's own: the deployed commit's `scripts/ci/deploy-env.sh` with `DEPLOY_RENDER_ONLY`
  (the upgrade's values files and flags), that commit's deploy constants, its ref as the release ran on it, and the
  digests it deployed. Everything is reconstructed from Git and the deployment record, so the deploy records nothing
  extra.
- [`scripts/drift.py`](../../scripts/drift.py) compares only the fields the render sets, lists by name where
  Kubernetes merges by name, and reports a changed field, a missing object, an extra object carrying the release's
  instance label, an `ExternalSecret` that is not ready, and an autoscaled workload outside its HPA's range, each by
  kind, namespace, name and field path, never a value. Jobs and Helm hooks are not compared, with the reason.
- The drift jobs declare their environment with `deployment: false`: its variables and branch policy, no
  deployment record, so a drift run never looks like a deploy to the lookups that read those records.
- The identity is read-only: per environment, a GitHub environment `drift-<env>` with no required reviewers, a
  Workload Identity provider that admits only `drift.yml` on `main` in that environment, a service account with only
  the cluster-connect role, and the bootstrap's `bootstrap-drift` Role in each namespace with `get` and `list` on
  exactly the kinds the chart renders there. It has no Secrets right at all (a `list` returns their data), so it
  never reads Helm's release records.
- A report job with `issues: write` and no cluster access opens or updates one issue per environment, labelled
  `drift`, and closes nothing; findings keep the run green, a failed collection or read fails it. Without the
  `drift-<env>` variables, each environment's job skips with a summary line.

## Consequences

- Good: a hand edit is reported within a week, with where it is, by an identity that cannot change or read a secret.
- Good: no controller runs in the cluster, and the deploy path stays the one that applies.
- Good: the deploy always wins. Every Helm upgrade and rollback passes `--force-conflicts`, so a hand edit (whose
  kubectl field manager now owns the field) is overwritten by the next deploy or rollback instead of failing it, and
  the check reports it before that. The chart never sets a field another controller owns at runtime (no `replicas`
  with the HPA on, no Secret External Secrets writes), so the deploy never fights one.
- Bad: a fix made by hand during an incident lasts only until the next deploy; it must be made permanent in the
  values or the chart first.
- Bad: a field the chart does not set (a label added by hand, a container a webhook injects into a list it does not
  name) is not compared, and a Secret's content is not checked at all.
- Bad: after a `deploy-ops.yml` rollback, or once a dev deploy's digest artifact has expired, the environment is not
  compared until the next deploy, and it says so.

## Alternatives considered

- A GitOps controller (Argo CD or Flux) that reconciles or reports drift continuously: cut. It replaces the
  push-based deploy and its checks, and runs a privileged controller in the cluster.
- `kubectl diff` or `helm diff` from the workflow: cut. Both run a server-side dry run, which needs `patch` (write
  rights), and `helm diff` reads the release Secrets.
- This read-only comparison of the render with `get` and `list`: chosen.

## Links

- [CI/CD: Live drift](../ci-cd.md#live-drift)
- [GKE cluster setup: the live-drift identity](../gke-cluster-setup.md#126-optional-the-live-drift-identity)
- [`scripts/ci/drift-render.sh`](../../scripts/ci/drift-render.sh), [`scripts/drift_collect.py`](../../scripts/drift_collect.py),
  [`scripts/drift_report.py`](../../scripts/drift_report.py), [`infra/k8s/bootstrap/templates/drift-rbac.yaml`](../../infra/k8s/bootstrap/templates/drift-rbac.yaml)
