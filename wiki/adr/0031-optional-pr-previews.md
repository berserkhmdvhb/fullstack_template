# ADR-0031: Optional pull-request previews of the chart, with main-built images, in one namespace

## Status

Accepted.

## Date

2026-11-23

## Context

A preview environment per pull request helps review chart and configuration changes on a real cluster. But a pull
request's jobs run the pull request's own code, image operations never run in a pull request
([`AGENTS.md`](../../AGENTS.md), rule 5), and Kubernetes RBAC cannot limit namespace creation by name. The kind job
on every pull request already proves the deploy.

## Decision

Previews are off by default (`PREVIEWS_ENABLED`). When on:
- only same-repository pull requests with the `preview` label, on `pull_request` (never `pull_request_target`, never
  forks), in the `preview` environment;
- they deploy **main-built, signed images only**, by digest, read from dev's latest deployment: a preview shows the
  pull request's chart, values and Helm-side changes, not its application code;
- **one preview namespace** (and its database namespace), with one Helm release `<service>-pr-<N>` per pull request,
  created once by the admin bootstrap with quotas, a LimitRange, a shared ServiceAccount, its own app tier and a
  namespace-scoped preview deployer;
- behind Cloudflare Access, with an Access service token for the smoke test; a cleanup on close or unlabel and a
  daily TTL sweep.

## Consequences

- Good: chart changes are seen live before merge, with no image built from a pull request.
- Good: the deployer needs no cluster-wide rights.
- Bad: a write-access pull request can run arbitrary code in the preview namespaces' Pods and read their Secrets,
  bounded by RBAC, the quotas, Pod Security `restricted` and the NetworkPolicies; the
  [trust boundary](../ci-cd.md#pull-request-previews) is documented in full.
- Bad: application code changes are not previewed until merged.

## Alternatives considered

- A namespace per pull request: rejected, the deployer would need cluster-wide `namespaces/create`.
- Build preview images from the pull request: rejected, it would put image operations in a pull request.

## Links

- [`.github/workflows/preview.yml`](../../.github/workflows/preview.yml),
  [`scripts/ci/preview-cleanup.sh`](../../scripts/ci/preview-cleanup.sh),
  [`charts/your-service-name/values.preview.yaml`](../../charts/your-service-name/values.preview.yaml)
- [CI/CD: pull request previews](../ci-cd.md#pull-request-previews)
- [`tests/k8s/test_previews.py`](../../tests/k8s/test_previews.py)
