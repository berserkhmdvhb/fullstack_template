# ADR-0040: An optional, advisory AI reviewer for pull requests

## Status

Accepted (not yet implemented).

## Date

2026-11-23

## Context

A single owner reviews their own pull requests ([ADR-0022](./0022-repository-settings-as-a-script.md)). An automated
reviewer can catch what a self-review misses. But a reviewer bot reads untrusted pull-request text (prompt
injection), sends code to a model provider, and costs money per run.

## Decision

The template will add PR-Agent as an optional reviewer, **off by default** (a repository variable turns it on) and
**advisory only**: never a required check, never approving or resolving threads. Its trust model:
- `pull_request` for same-repository pull requests only (forks skipped with a notice), never `pull_request_target`;
  slash commands only from owners, members and collaborators, bots ignored;
- one job with `contents: read`, `pull-requests: write` and `issues: write`; the model key a secret of its own
  environment, and no other secret; no pull-request code checked out or run;
- the action pinned by SHA and its image by digest, both visible to Renovate.

The docs will say what is sent to the provider, the cost and how to cap it, and name Anthropic's
`claude-code-action` as the alternative.

## Consequences

- Good: a second opinion on every pull request at no risk to the gate.
- Bad: code and diffs go to a third party; an adopter must accept that before turning it on.
- Bad: a prompt-injected comment can mislead a reader; the bot's rights are minimal so it cannot do more.

## Alternatives considered

- Anthropic's `claude-code-action`: to be documented as the alternative.
- No AI reviewer: the default stays off.

## Links

- [CI/CD pipeline](../ci-cd.md) (its section will be added with the workflow)
- [ADR-0022: repository settings](./0022-repository-settings-as-a-script.md)
