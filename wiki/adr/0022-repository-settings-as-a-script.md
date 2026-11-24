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
The admin role may bypass the `main` ruleset on every pull request (pull-request mode, never a direct push): GitHub
does not let a sole owner approve their own pull request, so without a bypass the owner could not merge their own
changes whenever the gate cannot pass on its own. The production environment sets `prevent_self_review: false`, so
the single owner, as its required reviewer, approves their own production deploys. A CODEOWNERS file, where present,
routes reviews and enforces nothing for a single owner.

## Consequences

- Good: settings are code, reviewed and re-applied after a change; the changelog says when to re-run.
- Good: a single owner can merge with the checks green, and nobody can push to `main` directly.
- Bad: with 0 approvals, the required checks are the only gate on `main`; a reviewer is advisory.
- Bad: the admin bypass covers every pull request, not only the release one, and the ruleset's rules with it, so an
  admin can merge over a pending or failing required check; for everyone else the check still applies.
- Bad: the production approval is the owner approving themselves: a deliberate pause, not a second pair of eyes.
- Bad: some settings have no API on every plan; the script reports those as soft failures.

## Alternatives considered

- A checklist only: rejected, it drifts.
- Require one approval: rejected for a single owner (self-approval is not possible).
- No bypass actor at all, with the release pull request opened by the App token so CI runs on it: the option for a
  team with a second reviewer. To switch, drop the admin entry from the `main` ruleset's `bypass_actors` in
  [`scripts/github-settings.sh`](../../scripts/github-settings.sh) (the `main` ruleset, described in
  [What the script sets](../github-repository-settings.md#what-the-script-sets)), raise the required approvals, and
  set `prevent_self_review: true` on production; then re-run `--diff` and `--apply`.

## Links

- [`scripts/github-settings.sh`](../../scripts/github-settings.sh)
- [GitHub repository settings](../github-repository-settings.md)
