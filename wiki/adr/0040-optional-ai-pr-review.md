# ADR-0040: An optional, advisory AI reviewer for pull requests

## Status

Accepted.

## Date

2026-11-23

## Context

A single owner reviews their own pull requests ([ADR-0022](./0022-repository-settings-as-a-script.md)). An automated
reviewer can catch what a self-review misses. But a reviewer bot reads untrusted pull-request text (prompt
injection), sends code to a model provider, and costs money per run.

## Decision

[`pr-agent.yml`](../../.github/workflows/pr-agent.yml) runs PR-Agent as an optional reviewer, **off by default** (a repository variable turns it on) and
**advisory only**: never a required check, never approving or resolving threads. Its trust model:
- `pull_request` for same-repository pull requests only (forks skipped with a notice), never `pull_request_target`;
  slash commands only from owners, members and collaborators, bots ignored, and only on same-repository pull
  requests; a `gate` job with no secret decides, and only then does the job with the key run;
- one job with `contents: read`, `pull-requests: write` and `issues: write`; the model key a secret of its own
  environment, and no other secret; no pull-request code checked out or run;
- the image pinned by tag and digest and run directly (`docker://`), visible to Renovate. The action at its pinned
  commit only builds `FROM pragent/pr-agent:github_action`, a rolling tag, so pinning the action by SHA alone would
  still run a mutable image; the workflow records the action commit the digest belongs to.

Its settings, [`.pr_agent.toml`](../../.pr_agent.toml), pick Claude, keep it to comments, and turn auto-approval and thread resolving off.
[CI/CD](../ci-cd.md#ai-pull-request-review) says what is sent to the provider;
[PR-Agent setup](../pr-agent-setup.md) holds the cost, how to cap it, and the owner's steps.

## Consequences

- Good: when turned on, a second opinion on same-repository pull requests (on the triggers `pr-agent.yml` listens for), at no risk to the gate.
- Bad: code and diffs go to a third party; an adopter must accept that before turning it on.
- Bad: a prompt-injected comment can mislead a reader; the bot's rights are minimal so it cannot do more.

## Alternatives considered

- Anthropic's `claude-code-action`: the documented alternative ([CI/CD](../ci-cd.md#ai-pull-request-review)).
- No AI reviewer: the default stays off.

## Links

- [AI pull request review](../ci-cd.md#ai-pull-request-review)
- [`pr-agent.yml`](../../.github/workflows/pr-agent.yml)
- [ADR-0022: repository settings](./0022-repository-settings-as-a-script.md)
