# ADR-0021: Keyless deploys through Workload Identity Federation, one deployer per environment

## Status

Accepted.

## Date

2026-11-20

## Context

A deploy job needs cluster credentials. A long-lived key stored as a secret can be copied out by any job that reads
it, and a shared deployer for every environment lets a dev deploy touch production. A cluster API on a public IP
needs an IP allowlist, which GitHub's hosted runners cannot satisfy.

## Decision

Deploy jobs exchange the GitHub OIDC token for short-lived Google credentials through Workload Identity Federation.
The provider's condition pins the numeric `repository_owner_id` and `repository_id` and the running workflow file
(`job_workflow_ref`: `deploy.yml` or `deploy-ops.yml` on `main` or a `v*` tag; previews have a second provider for
`preview.yml`). There is one deployer service account per environment, bound to the subject
`repo:<owner>/<repo>:environment:<env>`, with namespace-scoped RBAC only
([ADR-0011](./0011-namespace-bootstrap-scoped-deployer.md)). Jobs reach the cluster over GKE's DNS-based control-plane
endpoint with IAM (`use_dns_based_endpoint: true`), so no IP allowlist is needed. The hardening lint fails a job that
uses a secret or an OIDC token without declaring its `environment:`. A kubeconfig mode remains for other clusters.

## Consequences

- Good: no stored cloud key; a token lives minutes and only a job of the right environment, repository and workflow
  gets one.
- Good: environment protection rules (required reviewers on `production`) gate the credential itself.
- Bad: setup is a one-time chain of cloud steps (pool, provider, service accounts, bindings) that the GKE guide walks
  through.
- Bad: kubeconfig mode reintroduces a long-lived credential; the docs say to prefer `gke` mode.

## Alternatives considered

- A service-account key as a repository secret: rejected, long-lived and copyable.
- One deployer for all environments: rejected, no separation between dev and production.
- A public endpoint with an allowlist: rejected, hosted runners have no stable addresses.

## Links

- [`.github/actions/cluster-access/action.yml`](../../.github/actions/cluster-access/action.yml)
- [GKE cluster setup: keyless access for GitHub Actions](../gke-cluster-setup.md#12-keyless-access-for-github-actions)
- [Platform setup: kubeconfig mode](../platform-setup.md#kubeconfig-mode)
