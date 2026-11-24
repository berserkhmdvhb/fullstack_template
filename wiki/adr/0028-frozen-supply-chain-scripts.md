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

The eight scripts (`_helpers.sh`, `sbom-generate.sh`, `scan-vulnerabilities.sh`, `scan-licenses.sh`,
`generate-vex.sh`, `check-ci-hardening.sh`, `tools-install.sh`, `verify-image-digest.sh`) are frozen. New
supply-chain logic goes under `infra/security/local/`, through the `add-supply-chain-scanner` skill. An edit to a
frozen script is a one-time, owner-approved exception, recorded in [`AGENTS.md`](../../AGENTS.md) with its reason; so
far: one re-baseline to remove the old forge's coupling, a VEX correctness fix, and passing large scanner JSON to jq
through files.

## Consequences

- Good: the scripts' behaviour is stable and reviewable; a new scanner cannot break an old one.
- Bad: a fix to a frozen script needs an explicit exception, which slows it down on purpose.

## Alternatives considered

- Treat them like any other script: rejected, too easy to change a release claim in passing.

## Links

- [`AGENTS.md`](../../AGENTS.md) (rule 6), [`CONVENTIONS.md`](../../CONVENTIONS.md)
- [`infra/security/local/README.md`](../../infra/security/local/README.md)
- [`agent/skills/add-supply-chain-scanner/SKILL.md`](../../agent/skills/add-supply-chain-scanner/SKILL.md)
