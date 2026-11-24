# ADR-0030: Three environments, dev, qa and production, with qa optional

## Status

Accepted.

## Date

2026-11-20

## Context

The original pipeline promoted through dev, qa and production. Some adopters want a staging step, others find it
pure overhead.

## Decision

The deploy chain keeps all three, on by default. `deploy-dev`, then `deploy-qa` (after dev), then `deploy-production`
(on a `v*` tag only, after dev and after qa succeeded or was skipped, behind the `production` environment's required
reviewer). The repository variable `DEPLOY_ENVS` can drop qa; it gates nothing else. Everything is skipped unless
`DEPLOY_ENABLED` is `true`, so a fresh fork stays green.

## Consequences

- Good: the original's promotion chain is preserved; an adopter drops qa with one variable.
- Bad: three environments to bootstrap and configure, each with its own deployer
  ([ADR-0021](./0021-keyless-deploys-per-environment.md)).

## Alternatives considered

- dev and production only: rejected as the default, it would drop a stage the original had.

## Links

- [`.github/workflows/deploy.yml`](../../.github/workflows/deploy.yml)
- [CI/CD: deploy chain](../ci-cd.md#deploy-chain)
