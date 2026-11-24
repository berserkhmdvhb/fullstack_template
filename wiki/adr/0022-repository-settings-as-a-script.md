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
The `main` ruleset has **no bypass actor**: everyone, the admin included, merges only through a pull request whose
required check has passed. With 0 approvals the single owner still merges their own pull requests, and the release
pull request is opened with the release App's token, so CI runs on it. In an emergency an admin can set the
ruleset's enforcement to Disabled in **Settings → Rules** and back afterwards: a visible, deliberate step, not a
silent merge. The production environment sets `prevent_self_review: false`, so the single owner, as its required
reviewer, approves their own production deploys. [`CODEOWNERS`](../../.github/CODEOWNERS) routes reviews and
enforces nothing for a single owner.

## Consequences

- Good: settings are code, reviewed and re-applied after a change; the changelog says when to re-run.
- Good: a single owner can merge with the checks green, and nobody can push to `main` directly.
- Good: no one, the admin included, merges over a pending or failing required check.
- Bad: with 0 approvals, the required checks are the only gate on `main`; a reviewer is advisory.
- Bad: a check that cannot pass (a broken runner, an outage) blocks every merge until it is fixed, or until an admin
  turns the ruleset off for the emergency and on again.
- Bad: the production approval is the owner approving themselves: a deliberate pause, not a second pair of eyes.
- Bad: some settings have no API on every plan; the script reports those as soft failures.

## Alternatives considered

- A checklist only: rejected, it drifts.
- Require one approval: rejected for a single owner (self-approval is not possible).
- An admin bypass on every pull request (the repository admin role, pull-request mode): rejected. GitHub cannot
  scope it to the release pull request, so an admin could merge any pull request over a pending or failing required
  check; turning the ruleset off when it is really needed is visible and deliberate instead.
- For a team with a second reviewer: raise the required approvals in the `main` ruleset of
  [`scripts/github-settings.sh`](../../scripts/github-settings.sh) (described in
  [What the script sets](../github-repository-settings.md#what-the-script-sets)) and set `prevent_self_review: true`
  on production; then re-run `--diff` and `--apply`.

## Links

- [`scripts/github-settings.sh`](../../scripts/github-settings.sh)
- [GitHub repository settings](../github-repository-settings.md)
