# ADR-0026: CodeQL and dependency review block pull requests

## Status

Accepted.

## Date

2026-11-20

## Context

Most supply-chain controls run on `main` and tags, after a merge. A vulnerable dependency, a disallowed licence or an
injectable code path should be stopped in review, before it reaches `main`.

## Decision

[`security.yml`](../../.github/workflows/security.yml) runs CodeQL (Python and JavaScript/TypeScript) and
`actions/dependency-review-action` on pull requests, both blocking. The dependency review fails on a High or
Critical advisory and on any licence outside the allowlist it derives from
[`infra/security/grant.yaml`](../../infra/security/grant.yaml), so the PR gate and the image licence scan agree. A
private copy without GitHub code security can turn both off with `CODE_SECURITY=off`, and a job says so.

## Consequences

- Good: problems are caught where they are cheapest to fix, with one licence policy for both gates.
- Bad: CodeQL adds minutes to every pull request; a false positive needs a dismissal in code scanning.

## Alternatives considered

- Run them on `main` only: rejected, the finding arrives after the merge.

## Links

- [`.github/workflows/security.yml`](../../.github/workflows/security.yml)
- [GitHub repository settings: required status checks](../github-repository-settings.md#required-status-checks)
- [ADR-0002: Apache-2.0](./0002-apache-2-licence.md)
