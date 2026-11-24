# GitHub repository settings

The repository's GitHub settings are code: [`scripts/github-settings.sh`](../scripts/github-settings.sh) sets them, and this page is the checklist of what it sets and why. Run it **after the first push**: a `main` ruleset that requires a pull request would refuse that push ([GKE §11.2](./gke-cluster-setup.md#112-create-and-harden-the-repository)). The variables it sets are the ones in [Platform Setup](./platform-setup.md#variables-and-secrets), which stays the single list.

## Required status checks

The `main` ruleset requires one check:

- `CI OK`

`CI OK` is the `ci-ok` job in [`ci.yml`](../.github/workflows/ci.yml); read its comment and `needs:` list for the exact rules. In short: it depends on every blocking job, runs even when one of them failed, and fails on any result that isn't a success. Only the path-filtered `backend`, `frontend` and `pins` (pin drift) jobs may be skipped, and only on a pull request; the image structure checks (`image-plan`, `images`) and the `kind` job run on pull requests only, so they may be skipped on any event, but a failure blocks; `live` is ignored on a pull request, where it is advisory. The check is pinned to the GitHub Actions app (`integration_id`), so no other app can report it.

Why one aggregate check and not a list: a path-filtered job that is skipped, and the matrix legs of a reusable workflow, report check names that aren't stable, so GitHub can't require them reliably. `tests/supply_chain/test_required_checks.py` pins the name against the real job names, checks that every blocking pull request job sits behind the gate, and runs the gate's script against sample results.

## Advisory checks (never required)

These warn and don't block. They are never listed as required:

- `pip-audit (advisory)`
- `Live suites (full stack)`
- `Unit (Python 3.14)`
- `Lighthouse CI (advisory)`
- `ZAP baseline (advisory)`
- `Licences (grant, advisory)`
- `VEX (advisory)`
- `PR-Agent review (advisory)`

The advisory scanner legs of `security.yml` (named `<id> (advisory)`) are the same. `Live suites (full stack)` is advisory on pull requests and blocking on `main` and tags. `Unit (Python 3.14)` is the one advisory leg of the unit matrix. DAST, grant and VEX run only on `main`, tags or by hand, so they never gate a pull request. `PR-Agent review (advisory)` is the optional AI reviewer, off by default ([CI/CD](./ci-cd.md#ai-pull-request-review)); it comments and never approves.

## What the script sets

| Area | Setting | Why |
|---|---|---|
| `main` ruleset | a pull request is required, 0 approvals; the check `CI OK`; no force pushes; no deletion | one owner, so no approval count, but nothing lands without a green gate. [`CODEOWNERS`](../.github/CODEOWNERS) routes reviews and enforces nothing: GitHub doesn't let the only owner approve their own pull request, so a required code-owner review would block every merge |
| `main` ruleset | **no bypass actor**: the admin, like everyone, merges only through a pull request with `CI OK` green | the gate holds for every merge. With 0 approvals the owner still merges their own pull requests, and the release pull request, opened by the release App, gets its CI run. In an emergency an admin can set the ruleset's enforcement to **Disabled** (**Settings → Rules**), a visible, deliberate step, and turn it back on after ([ADR-0022](./adr/0022-repository-settings-as-a-script.md)) |
| `release-tags` ruleset | only the release GitHub App may create, update or delete `v*` tags | a tag is a release; the App is the only identity that can make one |
| Environments | `dev` and `qa`: deployment rules **branch** `main` and **tag** `v*`; any other rule is deleted | the deploy chain runs from `main` and from release tags |
| Environments | `production`: **tag** `v*` only; you as the required reviewer; self-review allowed | one owner has to approve their own deploys (a single-owner repository) |
| Environments | `release`: **branch** `main` only | holds the release App's private key |
| Labels | the `breaking-change` label (created, or updated in place) | the `api-compat` job lets a pull request with this label pass a breaking OpenAPI change ([API lifecycle](./api-lifecycle.md)); only users with triage permission or above can add it |
| Variables | repository and environment variables from your vars-file | the scopes of [Platform Setup](./platform-setup.md#variables-and-secrets) |
| Actions policy | default token read-only; Actions can't approve pull requests | a job asks for anything more itself |
| Actions policy | allowed actions: selected, the list derived from the `uses:` lines | one source of truth: the workflows |
| Actions policy | full-length SHA pinning required; approval for all outside contributors | a fork can't run workflows unreviewed |
| Merges | squash only (merge commits and rebase merges off); the squash commit takes the pull request **title** and body | the title lands on `main` as the commit message, so `pr-title.yml` can hold it to Conventional Commits ([Pull request titles](./releasing.md#pull-request-titles)) |
| Security | immutable releases; secret scanning and push protection; private vulnerability reporting; Dependabot alerts and security updates | a published release can't be changed; a leaked secret is blocked at push; a vulnerable dependency gets a fix pull request while Renovate is off or between runs. [`SECURITY.md`](../SECURITY.md) and the issue forms send reporters to private vulnerability reporting |
| Template | marked as a template, only with `--template` | the upstream template repository sets it; an adopter's copy leaves it off |
| Runners | none self-hosted (checked, never set) | the repository settings script; `--apply` refuses while any exist |

Environment rules and reviewers need a public repository, or a private one on a paid plan: a private repository on Pro or Team gets environments and deployment rules but **not required reviewers** (Enterprise or public only), and the template's repository is public. On a **private** copy, secret scanning, private vulnerability reporting and the production reviewers may be refused by the plan: the script records each as a skip, carries on, prints a summary and exits with code 3. Details in [GKE §11.1](./gke-cluster-setup.md#111-public-or-private).

## The release GitHub App

The `release-please` job of [`release-please.yml`](../.github/workflows/release-please.yml) opens the release pull request and creates the `v*` tag. It can't use the workflow's `GITHUB_TOKEN`, because a pull request or tag made with that token triggers no workflows: the release PR would have no checks and the tag would build nothing.

Create a GitHub App under your account (**Settings → Developer settings → GitHub Apps**):

- **Permissions (repository):** Contents: read and write (the tag and the release); Pull requests: read and write (the release pull request); Issues: read and write (release-please labels its pull request through the issues API); Metadata: read. Nothing else. The workflow's token asks for exactly these ([`release-please.yml`](../.github/workflows/release-please.yml)).
- Webhooks off. Install it on this repository only.
- Its **App ID** goes in the vars-file as `RELEASE_APP_ID` (a variable, not a secret). The script uses it as the bypass actor of the `release-tags` ruleset and refuses `--apply` without it; the workflow passes it to `actions/create-github-app-token` as `client-id`, which GitHub accepts as the App ID.
- Generate a **private key** and store it as the `release` environment secret `RELEASE_APP_PRIVATE_KEY` (**Settings → Environments → release → Environment secrets**). The script never sets secrets.
- **Before the first run, on a repository with history** (not one fresh from **Use this template**, which starts at one commit): add `"bootstrap-sha": "<sha>"` to [`release-please-config.json`](../release-please-config.json), the newest commit the first release leaves out (it collects the commits after it). With no `v<manifest version>` tag to find, release-please otherwise reads up to its commit search depth (500 commits) into the first release pull request. It reads the key only until it finds a release. The template commits none: a SHA names a commit of one repository, and a copy has other SHAs.
- **Watch on the first run.** If GitHub refuses the numeric App ID through `client-id`, switch that input of the token step to `app-id` (deprecated, same variable). If release-please's force-push of its release branch is refused after a workflow file changed on `main`, the token probably needs `permission-workflows: write` in the token step and the App the Workflows: read and write permission; neither is confirmed.
- Until `RELEASE_APP_ID` is set, `release-please.yml` is a green run with a notice; once it is, every push to `main` updates the release pull request ([Creating a Release](./releasing.md#creating-a-release)).

## Run it

You need `gh` logged in as a repository admin, `jq` and `python3`. Keep the vars-file outside the repository.

```bash
# 1. Dry run: prints every call (method, path, payload) and changes nothing.
scripts/github-settings.sh --repo OWNER/REPO --vars-file ~/github-vars.env
# 2. Dry run with reads: also fetches current state, so it shows create versus update.
scripts/github-settings.sh --repo OWNER/REPO --vars-file ~/github-vars.env --diff
# 3. Apply, after the first push. Safe to repeat: it updates in place.
scripts/github-settings.sh --repo OWNER/REPO --vars-file ~/github-vars.env --apply
# The upstream template repository only: add --template
```

`--repo` is required; there is no default owner. The script lists the secrets you must set by hand, and the dry run prints that list too.

### The vars-file

Keep it **outside the repository**; the script refuses a path under the repo root. One `NAME=value` per line; blank lines and `#` comments are allowed; anything else is refused. Only the names in the file are set.

```text
# Repository variables
SERVICE_NAME=...
DEPLOY_ENABLED=true
# Optional: build native arm64 images too (see below)
BUILD_ARM64=true
# Environment variables: ENV_<environment>_<NAME>
ENV_dev_GCP_DEPLOYER_SA=...
# The release App id, and (optional) your numeric user id for the production reviewer
RELEASE_APP_ID=...
REVIEWER_USER_ID=...
```

`BUILD_ARM64=true` adds native arm64 images (merged with amd64 into one index per image); unset builds `linux/amd64` only. It costs one more build, scan and check leg per image on a `ubuntu-24.04-arm` runner. Those runners are free on public repositories; on a private repository the minutes are billed like other GitHub-hosted Linux minutes, so check your plan first. A pull request builds `linux/amd64` only whatever it says.

The accepted names are the `REPO_VARS` and `ENV_VARS` lists at the top of the script, kept in step with [Platform Setup's table](./platform-setup.md#variables-and-secrets) by a test. Environment names carry the prefix `ENV_dev_`, `ENV_qa_`, `ENV_production_` or `ENV_release_`. `RELEASE_APP_ID` and `REVIEWER_USER_ID` must be numbers. The script resolves the reviewer to a login and prints it; without `REVIEWER_USER_ID` it uses the owner, and stops if the owner is an organisation.

### What to expect

- **Before any change**, `--apply` validates the vars-file, checks that `RELEASE_APP_ID` is set, checks that the app behind `CI OK` is the pinned one, resolves the reviewer and checks for self-hosted runners.
- **Backups:** `--apply` first saves the rulesets, the Actions permissions, the allowlist, the environments and the deployment rules it is about to overwrite or delete, to a timestamped directory under `$XDG_STATE_HOME/github-settings-backups` (`~/.local/state` when unset) (or `--backup-dir`, which must also be outside the repo). The path is printed.
- **One value is not in GitHub's docs:** the GitHub Actions app id 15368 that the required check is pinned to. The script looks the app up (a real `CI OK` run on the default branch, else `GET /apps/github-actions`), prints `CI OK: pinned app id N; resolved <app> (id M)`, and `--apply` refuses on a mismatch; `--actions-app-id N` overrides. Confirm it in the first `--diff`, and check **Settings → Rules** after the first `--apply`.
- **Production reviewers:** a 422 on a private repository (plan limit) is a soft skip; any other failure, or a 422 on a public repository, stops the run, so the approval gate is never dropped silently. After the update the script reads the reviewers back and prints them; none on a public repository is an error, and none on a private one is a skip (exit 3).
- **Soft skips:** immutable releases, the fork approval policy, secret scanning, private vulnerability reporting and the production reviewers record a skip when GitHub refuses them. A 422 on `sha_pinning_required` is retried without it. The script then prints the value GitHub reports, and a summary of every skip, and exits 3.
- **Deployment rules** that aren't wanted are deleted (a stray `v*` branch rule would let a branch reach production's secrets). `--diff` shows each as `DRY-RUN DELETE`.
- The cluster-side variables and the deployer identities are in [GKE §13.1](./gke-cluster-setup.md#131-variables-and-secrets).
