# ADR-0028: The core supply-chain scripts are frozen; new logic goes under `local/`

## Status

Accepted.

## Date

2026-11-20

## Context

Eight scripts under `infra/security/` produce the SBOMs, the vulnerability and licence scans, the VEX, the CI
hardening check, the tool installs and the digest check. CI and the local security runner both rely on their exact
behaviour. Each edit risks a silent change to what the release claims.

## Decision

The eight scripts are frozen. New supply-chain logic goes under `infra/security/local/`, through the
`add-supply-chain-scanner` skill. An edit to a frozen script is a one-time, owner-approved exception, recorded with its
reason. The list of the eight and the record of the exceptions are [`AGENTS.md`](../../AGENTS.md#mandatory-rules-each-with-a-why),
rule 6.

## Consequences

- Good: the scripts' behaviour is stable and reviewable; a new scanner cannot break an old one.
- Bad: a fix to a frozen script needs an explicit exception, which slows it down on purpose.

## Alternatives considered

- Treat them like any other script: rejected, too easy to change a release claim in passing.

## Links

- [`AGENTS.md`](../../AGENTS.md) (rule 6), [`CONVENTIONS.md`](../../CONVENTIONS.md)
- [`infra/security/local/README.md`](../../infra/security/local/README.md)
- [`agent/skills/add-supply-chain-scanner/SKILL.md`](../../agent/skills/add-supply-chain-scanner/SKILL.md)
