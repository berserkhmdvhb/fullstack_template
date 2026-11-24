# ADR-0022: Repository settings ship as a script, with rules for a single owner

## Status

Accepted.

## Date

2026-11-20

## Context

Much of a public repository's security lives in settings, not files: who may run workflows, the default token's
rights, action pinning, rulesets, environments, push protection. Settings clicked by hand drift and cannot be
reviewed. The template's first owner works alone, so a rule that needs a second approver would block every merge.

## Decision

[`scripts/github-settings.sh`](../../scripts/github-settings.sh) applies the settings (`--diff` first, then
`--apply`), with a checklist doc. It sets, among others: approval for outside contributors' workflow runs; a
read-only default token; full-SHA pinning and an actions allowlist; immutable releases; push protection; squash-only
merges with the pull-request title; the `main` ruleset (a pull request with **0 approvals** and the required status
check, no force-push or deletion) and the `release-tags` ruleset (only the release GitHub App may create, update or
delete `v*` tags); the environments and their branch policies. It refuses to apply when self-hosted runners exist.
The admin role may bypass the `main` ruleset in pull-request mode only (GitHub cannot scope a bypass to the release
pull request). A CODEOWNERS file, where present, routes reviews and enforces nothing for a single owner.

## Consequences

- Good: settings are code, reviewed and re-applied after a change; the changelog says when to re-run.
- Good: a single owner can merge with the checks green, and nobody can push to `main` directly.
- Bad: with 0 approvals, the required checks are the only gate on `main`; a reviewer is advisory.
- Bad: the admin bypass covers every pull request, not only the release one.
- Bad: some settings have no API on every plan; the script reports those as soft failures.

## Alternatives considered

- A checklist only: rejected, it drifts.
- Require one approval: rejected for a single owner (self-approval is not possible).

## Links

- [`scripts/github-settings.sh`](../../scripts/github-settings.sh)
- [GitHub repository settings](../github-repository-settings.md)
