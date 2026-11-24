# PR-Agent setup: the optional AI reviewer

This guide turns on the optional, advisory AI reviewer: [PR-Agent](https://github.com/The-PR-Agent/pr-agent), run by
[`pr-agent.yml`](../.github/workflows/pr-agent.yml). It covers:
- a model-provider key with a spend limit;
- the `ai-review` environment and its secret;
- the variable that turns it on;
- the model and what it costs;
- two opt-ins: a review on each push, and ticket context;
- what to watch on the first run;
- how to turn it off.

What it does, who can start it, what it sends to the provider and why its rights are minimal are in
[CI/CD, AI pull request review](./ci-cd.md#ai-pull-request-review); why it exists in
[ADR-0040](./adr/0040-optional-ai-pr-review.md). Read those first: turn it on only if the code may leave your
organisation. [Platform Setup](./platform-setup.md) gives the overall order; this step is optional.

Every step ends with a **Check**. Don't move on until it passes.

| | |
|---|---|
| Time | 15 minutes |
| Cost | the model provider's per-token price for each run; nothing on GitHub's side beyond the runner minutes |
| You need | admin rights on the repository, `gh` signed in as that admin, and an Anthropic account |
| Written | 2026-11-24. Dashboard menus move around, so if a path has changed, search for the setting's name. |

## Contents

1. [What you will build](#1-what-you-will-build)
2. [A model key with a spend limit](#2-a-model-key-with-a-spend-limit)
3. [The ai-review environment and its secret](#3-the-ai-review-environment-and-its-secret)
4. [Turn it on](#4-turn-it-on)
5. [The model and its cost](#5-the-model-and-its-cost)
6. [Optional: a review on each push](#6-optional-a-review-on-each-push)
7. [Optional: ticket context](#7-optional-ticket-context)
8. [Watch on the first run: the actions policies](#8-watch-on-the-first-run-the-actions-policies)
9. [Turn it off, or retire it](#9-turn-it-off-or-retire-it)

---

## 1. What you will build

> **Do now.**

```text
 pull request opened, reopened or ready for review; or a /command comment
    │
    ▼
 pr-agent.yml
    gate    ── no environment, no secret, read rights only: decides run=true or run=false, with a notice
    review  ── the ai-review environment, its ANTHROPIC_KEY: review and description comments
    │
    ▼
 comments on the pull request (never a required check, never an approval)
```

**Why this design:**
- **The key sits behind the gate.** Only `review` holds it, and only after `gate` passed a same-repository pull
  request or a collaborator's command.
- **The spend is capped where it is billed.** A dedicated key with a monthly limit bounds the cost, whoever triggers
  a run.
- **Off is the default.** Without the variable, nothing runs.

## 2. A model key with a spend limit

> **Do now.**

1. In the Anthropic Console, create a **workspace** for this repository alone.
2. Set the workspace's **monthly spend limit** to what you are willing to lose if the key leaks or a run loops
   ([Rate limits and spend limits](https://docs.anthropic.com/en/api/rate-limits)).
3. Create an **API key** in that workspace. Copy it once, into the secret of section 3; keep no other copy.

Anyone with write access can change the workflow in their own pull request and read the key, as with every
same-repository secret ([CI/CD, Prompt injection](./ci-cd.md#ai-pull-request-review)): the spend limit bounds that too.

**Check:** the workspace shows the limit, and the key is listed in that workspace only.

## 3. The ai-review environment and its secret

> **Do now.**

The settings script doesn't create this environment; create it by hand, under **Settings → Environments → New
environment** ([Managing environments](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-deployments/managing-environments-for-deployment)):

1. Name it `ai-review`.
2. **Deployment branches and tags: No restriction.** A pull request's ref must reach it.
3. **No required reviewers.** They would hold every run.
4. Add the key as the environment secret `ANTHROPIC_KEY`:

```bash
gh secret set ANTHROPIC_KEY --env ai-review --repo <owner>/<repo>   # paste the key at the prompt; it is not echoed
```

**Check:**

```bash
gh secret list --env ai-review --repo <owner>/<repo>
# → ANTHROPIC_KEY  <date>
```

## 4. Turn it on

> **Do now.**

Set the repository variable `PR_AGENT_ENABLED` to `true`: by hand (**Settings → Secrets and variables → Actions →
Variables**), or in the vars-file of [`scripts/github-settings.sh`](../scripts/github-settings.sh)
([Platform Setup, Variables and secrets](./platform-setup.md#variables-and-secrets)).

```bash
gh variable set PR_AGENT_ENABLED --body true --repo <owner>/<repo>
```

**Check:** open a small pull request from a branch of this repository. `PR-Agent gate` passes, `PR-Agent review
(advisory)` runs, and a review comment and a description comment appear. Comment `/help` on it: PR-Agent answers.
A run that skips leaves a notice that says why (`PR-Agent skipped: …`).

## 5. The model and its cost

> **Do now.**

The model and its fallback are `config.model` and `config.fallback_models` in [`.pr_agent.toml`](../.pr_agent.toml).
Both must be in PR-Agent's model registry at the pinned release; the file's comment names the cheaper choice and how
to switch to it. PR-Agent reads the file from the default branch, so a change takes effect once it is merged.

Each run is a few model calls, billed per token: a large diff costs more, and each command runs it again. The job's
`timeout-minutes` in [`pr-agent.yml`](../.github/workflows/pr-agent.yml) bounds a hung run, not the spend; the spend
limit of section 2 does that.

**Check:** after a few pull requests, the workspace's usage page shows the cost per run you expected.

## 6. Optional: a review on each push

> **Do now**, if you want it.

Off by default: PR-Agent ignores a push (`synchronize`) unless its push trigger is on, so the workflow doesn't listen
for one. To turn it on:

1. Add `synchronize` to the `pull_request` `types` of [`pr-agent.yml`](../.github/workflows/pr-agent.yml).
2. In [`.pr_agent.toml`](../.pr_agent.toml), under `[github_action_config]`, set `handle_push_trigger = true` and
   `push_commands = ["/review"]`.

Every push to an open pull request then costs a review run. The workflow's settings are pinned by
[`tests/supply_chain/test_workflows_pr_agent.py`](../tests/supply_chain/test_workflows_pr_agent.py); change the test
with them.

**Check:** a push to an open pull request starts `PR-Agent review (advisory)` and updates the review.

## 7. Optional: ticket context

> **Do now**, if you want it.

Issue text stays out of the prompt by default: in a public repository anyone can write an issue, which would put an
outsider's text in it ([CI/CD, AI pull request review](./ci-cd.md#ai-pull-request-review)). To let PR-Agent read the
issue a branch name points to and the linked tickets, set both `config.extract_issue_from_branch` and
`pr_reviewer.require_ticket_analysis_review` to `true` in [`.pr_agent.toml`](../.pr_agent.toml), and change the test
that pins them with them. Accept that an issue's author can then steer the review.

**Check:** on a pull request whose branch names an issue, the review mentions the ticket.

## 8. Watch on the first run: the actions policies

> **Do now.**

The `review` job's one step is a `docker://` image pinned by digest. Two repository policies that the settings script
sets may refuse such a step; neither has been confirmed either way:

- **Allowed actions.** The script allows only the actions the `uses:` lines name, and it skips `docker://`
  references. If GitHub's "selected actions" policy also blocks a `docker://` step, `review` fails at its start,
  before PR-Agent runs, with an error that the action is not allowed, and no comment appears. Add `pragent/pr-agent@*`
  to the allowed patterns (**Settings → Actions → General**) and re-run the job. The script's `--apply` rewrites that
  list from the `uses:` lines, so it drops the entry again: re-add it after each `--apply` until the script lists
  `docker://` images.
- **SHA pinning.** The script sets `sha_pinning_required`, which requires actions to be pinned to a full commit SHA.
  If that policy refuses a `docker://` step pinned by digest, the symptom is the same. The policy is under
  **Settings → Actions → General**, and the script sets it, so changing it by hand lasts only until the next
  `--apply`.

([Managing GitHub Actions settings for a repository](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository).)

**Check:** the step starts and PR-Agent comments. Then nothing is needed.

## 9. Turn it off, or retire it

> **Do now.**

- **Off:** delete the variable (`gh variable delete PR_AGENT_ENABLED --repo <owner>/<repo>`). The gate's `if:` is
  then false, and no runner starts.
- **Retire:** also delete the environment's secret (or the `ai-review` environment) and revoke the key in the
  Anthropic Console.

**Check:** the next pull request shows no PR-Agent jobs, and the revoked key is gone from the workspace.
