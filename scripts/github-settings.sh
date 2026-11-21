#!/usr/bin/env bash
# github-settings.sh — the repository settings as code (dry run by default).
#
# Usage: github-settings.sh --repo <owner>/<repo> [--vars-file <path>] [--template] [--backup-dir <dir>] [--actions-app-id <N>]
#                           [--diff] [--dry-run | --apply]
#   --dry-run    (default) print every call it would make, with method, path and JSON payload.
#   --diff       with --dry-run, also do the GET reads, so the output shows create, update and delete.
#   --apply      perform the calls. Run it after the first push (wiki/github-repository-settings.md).
#   --vars-file  an env file kept OUTSIDE the repo, one NAME=value per line. Variables are set only from
#                it, and only the names it contains. Secrets are never set; the script prints the list.
#   --template   also mark the repository as a template (off by default, for adopters' copies).
#   --backup-dir where --apply saves what it is about to overwrite (default: under ~/.local/state).
# Needs: gh (authenticated as a repository admin), jq, python3. Idempotent: it reads current state and updates in
# place (PUT/PATCH by id or name), creates only what is missing and deletes stale deployment rules.
# Exit codes: 0 done, 1 error, 3 done but some settings were skipped (the summary lists them).
#
# REST docs, by area:
#   rulesets      https://docs.github.com/en/rest/repos/rules
#   environments  https://docs.github.com/en/rest/deployments/environments
#   policies      https://docs.github.com/en/rest/deployments/branch-policies
#   variables     https://docs.github.com/en/rest/actions/variables
#   actions       https://docs.github.com/en/rest/actions/permissions
#   runners       https://docs.github.com/en/rest/actions/self-hosted-runners
#   repo          https://docs.github.com/en/rest/repos/repos
# Two values are not in those pages: the RepositoryRole id 5 for "admin" (the convention everyone uses), and
# the id of the GitHub Actions app (15368) that must produce the required check. The second is verified against
# GitHub in the pre-flight (and can be set with --actions-app-id); confirm the bypass actor in the first --diff.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
MODE=dry
DIFF=0
TEMPLATE=0
REPO=""
VARS_FILE=""
BACKUP_DIR=""
declare -A VARS=()
SKIPS=()
LAST_ERR=""
EMPTY="{}"
REVIEWER_ID='"<id of the reviewer, looked up at --apply>"'
REPO_PRIVATE=unknown
APP_SLUG=unverified

# The required status check, and the app that must produce it (integration_id; the REST field is documented,
# the value 15368 for GitHub Actions is the well-known id). The pre-flight checks it against GitHub.
REQUIRED_CHECK="CI OK"
ACTIONS_APP_ID=""
RESOLVED_ID=""
# Names the vars-file may carry. Repository-scoped, then environment-scoped (as ENV_<env>_<NAME>); kept in
# step with wiki/platform-setup.md by tests/supply_chain/test_required_checks.py.
REPO_VARS=" GCP_PROJECT_ID GKE_CLUSTER GKE_LOCATION DEPLOY_ENABLED DEPLOY_ENVS SERVICE_NAME HELM_TIMEOUT HISTORY_KEEP CODE_SECURITY SCORECARD_ENABLED BUILD_ARM64 CLOUDFLARE_ACCOUNT_ID RELEASE_APP_ID "
ENV_VARS=" GCP_WIF_PROVIDER GCP_DEPLOYER_SA DEPLOY_MODE GCP_PROJECT_ID GKE_CLUSTER GKE_LOCATION "
# Script inputs that are never sent as variables. REVIEWER_USER_ID: the numeric id of the production reviewer;
# ACTIONS_APP_ID: the app that must produce the required check (default 15368, GitHub Actions).
CONTROL_VARS=" REVIEWER_USER_ID ACTIONS_APP_ID "
ENVS=(dev qa production release)

die() { echo "error: $*" >&2; exit 1; }
note() { echo "# $*" >&2; }
skip() { SKIPS+=("$1"); note "skipped: $1"; }

while (($#)); do
  case "$1" in
    --dry-run) MODE=dry ;;
    --apply) MODE=apply ;;
    --diff) DIFF=1 ;;
    --template) TEMPLATE=1 ;;
    --repo) REPO="${2:-}"; shift ;;
    --vars-file) VARS_FILE="${2:-}"; shift ;;
    --backup-dir) BACKUP_DIR="${2:-}"; shift ;;
    --actions-app-id) ACTIONS_APP_ID="${2:-}"; shift ;;
    -h | --help) sed -n '2,15p' "$0"; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
  shift
done
[[ "$REPO" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "--repo <owner>/<repo> is required"
OWNER="${REPO%%/*}"
command -v jq >/dev/null || die "jq is required"
command -v gh >/dev/null || die "gh is required"
command -v python3 >/dev/null || die "python3 is required"
ERRF="$(mktemp)"
trap 'rm -f "$ERRF"' EXIT

# A path that resolves under the repository root would put the file where git can see it.
outside_repo() { # path what
  local real
  real="$(python3 -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$1")"
  [[ "$real/" != "$ROOT/"* ]] || die "$2 must be outside the repository ($real)"
}

# --- vars-file: NAME=value lines, # comments and blank lines allowed; anything else is refused. ---
parse_vars() {
  local line n=0 name
  while IFS= read -r line || [[ -n "$line" ]]; do
    n=$((n + 1))
    [[ -z "${line//[[:space:]]/}" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || die "$VARS_FILE:$n: not NAME=value"
    name="${BASH_REMATCH[1]}"
    VARS["$name"]="${BASH_REMATCH[2]}"
    if [[ "$name" =~ ^ENV_([a-z]+)_(.+)$ ]]; then
      [[ " ${ENVS[*]} " == *" ${BASH_REMATCH[1]} "* && "$ENV_VARS" == *" ${BASH_REMATCH[2]} "* ]] || die "$VARS_FILE:$n: unknown environment variable $name"
    else
      [[ "$REPO_VARS$CONTROL_VARS" == *" $name "* ]] || die "$VARS_FILE:$n: unknown variable $name"
    fi
  done <"$VARS_FILE"
}
if [[ -n "$VARS_FILE" ]]; then
  [[ -r "$VARS_FILE" ]] || die "cannot read $VARS_FILE"
  outside_repo "$VARS_FILE" "--vars-file"
  parse_vars
fi
ACTIONS_APP_ID="${ACTIONS_APP_ID:-${VARS[ACTIONS_APP_ID]:-15368}}"
for n in RELEASE_APP_ID REVIEWER_USER_ID ACTIONS_APP_ID; do
  v="${VARS[$n]:-}"; [[ "$n" != ACTIONS_APP_ID ]] || v="$ACTIONS_APP_ID"
  [[ -z "$v" || "$v" =~ ^[0-9]+$ ]] || die "$n must be a number"
done

# --- calls ---
reads() { [[ "$MODE" == apply || "$DIFF" == 1 ]]; }
GH_ARGS=(-H "X-GitHub-Api-Version: 2022-11-28")

# get PATH: the body on stdout. Empty for a 404 or when reads are off; any other error stops the run.
get() {
  reads || return 0
  local out
  if out="$(gh api "${GH_ARGS[@]}" "$1" 2>"$ERRF")"; then
    printf '%s' "$out"
  elif grep -q 'HTTP 404' "$ERRF"; then
    return 0
  else
    die "GET $1 failed: $(cat "$ERRF")"
  fi
}

# try METHOD PATH [JSON]: prints in a dry run, sends with --apply. Returns the status; the error is in LAST_ERR.
try() {
  local method="$1" path="$2" body="${3:-}"
  LAST_ERR=""
  if [[ "$MODE" == dry ]]; then
    echo "DRY-RUN $method $path"
    [[ -z "$body" ]] || jq -c . <<<"$body"
    return 0
  fi
  echo "APPLY $method $path"
  if [[ -n "$body" ]]; then
    gh api -X "$method" "${GH_ARGS[@]}" "$path" --input - < <(jq -c . <<<"$body") >/dev/null 2>"$ERRF" || { LAST_ERR="$(cat "$ERRF")"; return 1; }
  else
    gh api -X "$method" "${GH_ARGS[@]}" "$path" >/dev/null 2>"$ERRF" || { LAST_ERR="$(cat "$ERRF")"; return 1; }
  fi
}
api() { try "$@" || die "$1 $2 failed: $LAST_ERR"; }
# soft WHAT METHOD PATH [JSON]: a refusal (plan limit, private repo) is recorded and the run goes on.
soft() { local what="$1"; shift; try "$@" || skip "$what ($1 $2): ${LAST_ERR:-refused}"; }

# Under --apply, whatever a replacing PUT will overwrite is saved first.
backup() { # path [body]
  [[ "$MODE" == apply ]] || return 0
  local body=""
  if (($# >= 2)); then body="$2"; else body="$(get "$1")"; fi
  [[ -n "$body" ]] || return 0
  mkdir -p "$BACKUP_DIR"
  printf '%s\n' "$body" >"$BACKUP_DIR/$(tr '/?' '__' <<<"${1#/}").json"
}

# --- pre-flight: everything that can be checked before the first change ---
preflight() {
  [[ "$MODE" != apply || -n "${VARS[RELEASE_APP_ID]:-}" ]] || die "RELEASE_APP_ID is missing from the vars-file; refusing to apply the v* tag ruleset"
  if [[ "$MODE" == apply ]]; then
    BACKUP_DIR="${BACKUP_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/github-settings-backups/${REPO//\//-}/$(date -u +%Y%m%dT%H%M%SZ)}"
    outside_repo "$BACKUP_DIR" "the backup directory"
  fi
  reads || return 0
  local id login owner_json runners repo_json branch run app_id
  if [[ -n "${VARS[REVIEWER_USER_ID]:-}" ]]; then
    id="${VARS[REVIEWER_USER_ID]}"
    login="$(get "/user/$id" | jq -r '.login // empty')"
    [[ -n "$login" ]] || die "REVIEWER_USER_ID $id is not a GitHub user"
  else
    owner_json="$(get "/users/$OWNER")"
    [[ "$(jq -r '.type // empty' <<<"$owner_json")" == User ]] || die "$OWNER is not a user account; set REVIEWER_USER_ID in the vars-file"
    id="$(jq -r .id <<<"$owner_json")"
    login="$OWNER"
  fi
  REVIEWER_ID="$id"
  repo_json="$(get "/repos/$REPO")"
  REPO_PRIVATE="$(jq -r '.private | if . == null then "unknown" else tostring end' <<<"${repo_json:-$EMPTY}")"
  # The required check must come from the app we pin: look at a real run, else at the documented app.
  branch="$(jq -r '.default_branch // "main"' <<<"${repo_json:-$EMPTY}")"
  run="$(get "/repos/$REPO/commits/$branch/check-runs?check_name=${REQUIRED_CHECK// /%20}&per_page=1")"
  app_id="$(jq -r '.check_runs[0].app.id // empty' <<<"${run:-$EMPTY}")"
  APP_SLUG="$(jq -r '.check_runs[0].app.slug // empty' <<<"${run:-$EMPTY}")"
  if [[ -z "$app_id" ]]; then
    run="$(get "/apps/github-actions")"
    app_id="$(jq -r '.id // empty' <<<"${run:-$EMPTY}")"
    APP_SLUG="$(jq -r '.slug // empty' <<<"${run:-$EMPTY}")"
  fi
  RESOLVED_ID="$app_id"
  if [[ -z "$app_id" ]]; then
    [[ "$MODE" != apply ]] || die "could not resolve the app behind the \"$REQUIRED_CHECK\" check; refusing to pin integration_id $ACTIONS_APP_ID"
    note "WARNING: could not resolve the app behind the \"$REQUIRED_CHECK\" check; --apply would refuse"
  elif [[ "$app_id" != "$ACTIONS_APP_ID" ]]; then
    [[ "$MODE" != apply ]] || die "the \"$REQUIRED_CHECK\" check comes from ${APP_SLUG:-an app} (id $app_id), not app id $ACTIONS_APP_ID; use --actions-app-id $app_id if that is right"
    note "WARNING: the \"$REQUIRED_CHECK\" check comes from ${APP_SLUG:-an app} (id $app_id), not $ACTIONS_APP_ID; --apply would refuse"
  fi
  note "production reviewer: $login (id $id)"
  # Design section 11.4: no self-hosted runners.
  runners="$(get "/repos/$REPO/actions/runners" | jq -r '.total_count // 0')"
  if ((runners > 0)); then
    [[ "$MODE" != apply ]] || die "the repository has $runners self-hosted runner(s); the design allows none (remove them first)"
    note "WARNING: the repository has $runners self-hosted runner(s); --apply would refuse"
  fi
}

# --- rulesets: https://docs.github.com/en/rest/repos/rules ---
ruleset() {
  local name="$1" body="$2" list id
  list="$(get "/repos/$REPO/rulesets?per_page=100")"
  id="$(jq -r --arg n "$name" '[(if type == "array" then .[] else empty end) | select(.name == $n) | .id][0] // empty' <<<"$list")"
  if [[ -n "$id" ]]; then
    backup "/repos/$REPO/rulesets/$id"
    api PUT "/repos/$REPO/rulesets/$id" "$body"
  else
    api POST "/repos/$REPO/rulesets" "$body"
  fi
}

main_ruleset() {
  # bypass_mode pull_request: the admin may merge a pull request over the gate; there is no direct push.
  # GitHub can't scope this to the release PR, so it covers every pull request. No linear-history rule.
  ruleset "main" "$(jq -n --arg c "$REQUIRED_CHECK" --argjson app "$ACTIONS_APP_ID" '{
    name: "main", target: "branch", enforcement: "active",
    bypass_actors: [{actor_id: 5, actor_type: "RepositoryRole", bypass_mode: "pull_request"}],
    conditions: {ref_name: {include: ["~DEFAULT_BRANCH"], exclude: []}},
    rules: [
      {type: "pull_request", parameters: {required_approving_review_count: 0, dismiss_stale_reviews_on_push: false,
        require_code_owner_review: false, require_last_push_approval: false, required_review_thread_resolution: false}},
      {type: "required_status_checks", parameters: {strict_required_status_checks_policy: false,
        required_status_checks: [{context: $c, integration_id: $app}]}},
      {type: "non_fast_forward"},
      {type: "deletion"}
    ]}')"
}

tag_ruleset() {
  # Only the release GitHub App (an Integration) may create, update or delete v* tags.
  local app="${VARS[RELEASE_APP_ID]:-\"<RELEASE_APP_ID>\"}"
  ruleset "release-tags" "$(jq -n --argjson app "$app" '{
    name: "release-tags", target: "tag", enforcement: "active",
    bypass_actors: [{actor_id: $app, actor_type: "Integration", bypass_mode: "always"}],
    conditions: {ref_name: {include: ["refs/tags/v*"], exclude: []}},
    rules: [{type: "creation"}, {type: "update"}, {type: "deletion"}]}')"
}

# --- environments: https://docs.github.com/en/rest/deployments/environments and .../branch-policies ---
# The wanted deployment rules of an environment, as "type name" lines.
wanted_policies() {
  case "$1" in
    dev | qa) printf '%s\n' "branch main" "tag v*" ;;
    production) printf '%s\n' "tag v*" ;;
    release) printf '%s\n' "branch main" ;;
  esac
}

policies() { # env: create the missing rules, delete every rule that is not wanted
  local env="$1" base="/repos/$REPO/environments/$1/deployment-branch-policies" have line type name id
  have="$(get "$base?per_page=100")"
  while read -r type name; do
    [[ "$(jq -r --arg n "$name" --arg t "$type" '[.branch_policies[]? | select(.name == $n and .type == $t)] | length' <<<"${have:-$EMPTY}")" != 0 ]] ||
      api POST "$base" "$(jq -nc --arg n "$name" --arg t "$type" '{name: $n, type: $t}')"
  done < <(wanted_policies "$env")
  while IFS=$'\t' read -r id type name; do
    [[ -n "$id" ]] || continue
    line="$type $name"
    grep -qxF -- "$line" < <(wanted_policies "$env") && continue
    backup "$base" "$have"
    api DELETE "$base/$id"
  done < <(jq -r '.branch_policies[]? | [.id, (.type // "branch"), .name] | @tsv' <<<"${have:-$EMPTY}")
}

environment() { # env
  local env="$1" body with have names
  body='{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}'
  backup "/repos/$REPO/environments/$env"
  if [[ "$env" == production ]]; then
    # One owner, so self-approval stays allowed. Reviewers need a public repo or Enterprise.
    with="$(jq -n --argjson id "$REVIEWER_ID" '{prevent_self_review: false, reviewers: [{type: "User", id: $id}],
      deployment_branch_policy: {protected_branches: false, custom_branch_policies: true}}')"
    if ! try PUT "/repos/$REPO/environments/$env" "$with"; then
      # Only a 422 is a plan refusal, and only a private repo may go without the approval gate.
      [[ "$LAST_ERR" == *422* && "$REPO_PRIVATE" == true ]] || die "PUT environments/production with reviewers failed: $LAST_ERR"
      skip "required reviewers on production (PUT environments/production): $LAST_ERR; the plan does not allow them on a private repository"
      api PUT "/repos/$REPO/environments/$env" "$body"
    elif [[ "$MODE" == apply ]]; then
      have="$(get "/repos/$REPO/environments/$env")"
      names="$(jq -r '[.protection_rules[]? | select(.type == "required_reviewers") | .reviewers[]?.reviewer.login] | join(", ")' <<<"${have:-$EMPTY}")"
      note "production reviewers: ${names:-none}"
      if [[ -z "$names" ]]; then
        [[ "$REPO_PRIVATE" == true ]] || die "production has no required reviewers after the update; the approval gate is missing"
        skip "production has no required reviewers after the update (private repository); the approval gate is missing"
      fi
    fi
  else
    api PUT "/repos/$REPO/environments/$env" "$body"
  fi
  policies "$env"
}

# --- variables: https://docs.github.com/en/rest/actions/variables ---
upsert_var() { # base-path name value  (base: /repos/R/actions/variables or /repos/R/environments/E/variables)
  local base="$1" name="$2" value="$3" payload
  payload="$(jq -nc --arg n "$name" --arg v "$value" '{name: $n, value: $v}')"
  local have
  have="$(get "$base/$name")"
  if [[ -n "$have" ]]; then api PATCH "$base/$name" "$payload"; else api POST "$base" "$payload"; fi
}

variables() {
  local name env
  for name in "${!VARS[@]}"; do
    if [[ "$REPO_VARS" == *" $name "* ]]; then upsert_var "/repos/$REPO/actions/variables" "$name" "${VARS[$name]}"; fi
  done
  for env in "${ENVS[@]}"; do
    for name in "${!VARS[@]}"; do
      if [[ "$name" == "ENV_${env}_"* ]]; then upsert_var "/repos/$REPO/environments/$env/variables" "${name#"ENV_${env}_"}" "${VARS[$name]}"; fi
    done
  done
}

# --- the Actions policy: https://docs.github.com/en/rest/actions/permissions ---
# One source of truth: the allowlist is every action a `uses:` line names (local and docker:// skipped), as
# the full path (github/codeql-action/init@*) and as owner/repo@*, so it holds under either reading of the match.
allowlist() {
  grep -rhoE '^[[:space:]-]*uses:[[:space:]]*[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+(/[A-Za-z0-9_./-]+)?@' "$ROOT/.github/workflows" "$ROOT/.github/actions" 2>/dev/null |
    sed -E 's/^[[:space:]-]*uses:[[:space:]]*//; s/@$//' |
    awk -F/ '{print $0; print $1 "/" $2}' | LC_ALL=C sort -u | sed 's/$/@*/'
}

actions_policy() {
  local patterns have_sha perm
  patterns="$(allowlist | jq -R . | jq -sc .)"
  perm="$(get "/repos/$REPO/actions/permissions")"
  backup "/repos/$REPO/actions/permissions" "$perm"
  # The selected-actions list exists only while allowed_actions is "selected" (the default is "all").
  if [[ "$(jq -r '.allowed_actions // empty' <<<"${perm:-$EMPTY}")" == selected ]]; then
    backup "/repos/$REPO/actions/permissions/selected-actions"
  fi
  backup "/repos/$REPO/actions/permissions/workflow"
  if ! try PUT "/repos/$REPO/actions/permissions" '{"enabled": true, "allowed_actions": "selected", "sha_pinning_required": true}'; then
    [[ "$LAST_ERR" == *422* ]] || die "PUT actions/permissions failed: $LAST_ERR"
    skip "sha_pinning_required was refused (422): $LAST_ERR"
    api PUT "/repos/$REPO/actions/permissions" '{"enabled": true, "allowed_actions": "selected"}'
  fi
  api PUT "/repos/$REPO/actions/permissions/selected-actions" "$(jq -nc --argjson p "$patterns" '{github_owned_allowed: false, verified_allowed: false, patterns_allowed: $p}')"
  api PUT "/repos/$REPO/actions/permissions/workflow" '{"default_workflow_permissions": "read", "can_approve_pull_request_reviews": false}'
  soft "fork pull request approval" PUT "/repos/$REPO/actions/permissions/fork-pr-contributor-approval" '{"approval_policy": "all_external_contributors"}'
  if reads; then
    have_sha="$(get "/repos/$REPO/actions/permissions" | jq -r '.sha_pinning_required | if . == null then "unknown" else tostring end')"
    note "sha_pinning_required is now: $have_sha"
  fi
}

# --- repository security and the template flag: https://docs.github.com/en/rest/repos/repos ---
repo_security() {
  soft "secret scanning and push protection" PATCH "/repos/$REPO" '{"security_and_analysis": {"secret_scanning": {"status": "enabled"}, "secret_scanning_push_protection": {"status": "enabled"}}}'
  soft "private vulnerability reporting" PUT "/repos/$REPO/private-vulnerability-reporting"
  # Dependabot is limited to security updates (.github/dependabot.yml): these two switch them on. Nothing to back up (both are plain flags).
  soft "Dependabot alerts" PUT "/repos/$REPO/vulnerability-alerts"
  soft "Dependabot security updates" PUT "/repos/$REPO/automated-security-fixes"
  soft "immutable releases" PUT "/repos/$REPO/immutable-releases"
  if ((TEMPLATE)); then api PATCH "/repos/$REPO" '{"is_template": true}'; fi
}

# --- the breaking-change label (read by the api-compat job; wiki/api-lifecycle.md): https://docs.github.com/en/rest/issues/labels ---
LABEL_NAME="breaking-change"
labels() {
  local have payload
  payload="$(jq -nc --arg n "$LABEL_NAME" '{name: $n, color: "b60205", description: "A deliberate breaking change to the API contract; lets the oasdiff gate pass"}')"
  have="$(get "/repos/$REPO/labels/$LABEL_NAME")"
  if [[ -n "$have" ]]; then
    api PATCH "/repos/$REPO/labels/$LABEL_NAME" "$payload"
  else
    api POST "/repos/$REPO/labels" "$payload"
  fi
}

secrets_list() {
  cat >&2 <<'LIST'
# Secrets are never set by this script. Set by hand (Settings -> Environments, or gh secret set --env):
#   release:     RELEASE_APP_PRIVATE_KEY  (the release GitHub App's private key)
#   dev/qa/production, only with DEPLOY_MODE=kubeconfig: KUBECONFIG_B64
#   production, only if the site deploy is used: CLOUDFLARE_API_TOKEN (and the optional R2 keys)
LIST
}

echo "# github-settings: $REPO, mode=$MODE$([[ "$DIFF" == 1 ]] && echo ', diff')"
preflight
[[ "$MODE" != apply ]] || note "backups of what is overwritten: $BACKUP_DIR"
if [[ -n "$RESOLVED_ID" ]]; then
  note "$REQUIRED_CHECK: pinned app id $ACTIONS_APP_ID; resolved ${APP_SLUG:-unnamed} (id $RESOLVED_ID)"
else
  note "$REQUIRED_CHECK: pinned app id $ACTIONS_APP_ID; resolved: none"
fi
actions_policy
repo_security
main_ruleset
tag_ruleset
for e in "${ENVS[@]}"; do environment "$e"; done
variables
labels
secrets_list
if ((${#SKIPS[@]})); then
  echo "# SKIPPED (${#SKIPS[@]}):" >&2
  printf '#   %s\n' "${SKIPS[@]}" >&2
  exit 3
fi
