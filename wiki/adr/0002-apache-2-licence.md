# ADR-0002: The template is licensed Apache-2.0

## Status

Accepted.

## Date

2026-11-20

## Context

The repository started with two candidate licences: the template's own Apache-2.0 and the MIT licence a new GitHub
repository offers. Adopters copy the whole tree into their own services, often commercial ones, so the licence must
be permissive and clear about patents.

## Decision

The template is licensed under Apache-2.0 ([`LICENSE`](../../LICENSE)). The dependency licence policy
([`infra/security/grant.yaml`](../../infra/security/grant.yaml)) and the dependency review's allowlist admit only
licences compatible with it.

## Consequences

- Good: an explicit patent grant and a patent-retaliation clause protect adopters and contributors.
- Good: compatible with the permissive licences the dependencies use.
- Bad: longer than MIT, and a redistributor must keep the licence text and mark the files they changed.

## Alternatives considered

- MIT: shorter, but without a patent grant; rejected because the template was already Apache-2.0.

## Links

- [`LICENSE`](../../LICENSE)
- [`infra/security/grant.yaml`](../../infra/security/grant.yaml)
- [ADR-0026: blocking pull-request security gates](./0026-blocking-pr-security-gates.md)
