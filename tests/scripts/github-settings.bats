#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# github-settings.bats — scripts/github-settings.sh against a stub gh that records argv and stdin.
# No real GitHub API is ever called. GET answers come from files in $STUB_DIR named after the path.
# -----------------------------------------------------------------------------

setup() {
  SCRIPT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)/scripts/github-settings.sh"
  B="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$B"
  export STUB_DIR="$BATS_TEST_TMPDIR/state"
  mkdir -p "$STUB_DIR"
  export LOG="$BATS_TEST_TMPDIR/gh.log"
  : > "$LOG"
  cat > "$B/gh" <<'STUB'
#!/usr/bin/env bash
# One log line per call: argv, then the stdin body (if any) on the next line.
echo "gh $*" >> "$LOG"
args=("$@")
body=""
if [[ "$*" == *"--input -"* ]]; then body="$(cat)"; echo "BODY $body" >> "$LOG"; fi
method=GET
while (($#)); do [[ "$1" == "-X" ]] && method="$2"; shift; done
for path in "${args[@]}"; do [[ "$path" == /* ]] && break; done
path="${path%%\?*}"
# $STUB_DIR/FAIL: an ERE tested against "METHOD PATH BODY"; a match is refused with a 422.
fail=""; [[ -f "$STUB_DIR/FAIL" ]] && fail="$(cat "$STUB_DIR/FAIL")"
if [[ -n "$fail" && "$method $path${body:+ $body}" =~ $fail ]]; then
  if [[ -f "$STUB_DIR/FAIL_MSG" ]]; then cat "$STUB_DIR/FAIL_MSG" >&2; else echo "gh: Validation Failed (HTTP 422)" >&2; fi
  exit 1
fi
if [[ "$method" == GET ]]; then
  f="$STUB_DIR/$(tr '/' '_' <<<"$path")"
  if [[ -f "$f" ]]; then cat "$f"
  elif [[ "$path" == /users/* ]]; then echo '{"id":4242,"login":"o","type":"User"}'
  elif [[ "$path" == /user/* ]]; then echo '{"id":7,"login":"alice"}'
  elif [[ "$path" == /apps/github-actions ]]; then echo '{"id":15368,"slug":"github-actions"}'
  elif [[ "$path" == /repos/o/r ]]; then echo '{"private":false,"default_branch":"main"}'
  elif [[ "$path" == */environments/production ]]; then echo '{"protection_rules":[{"type":"required_reviewers","reviewers":[{"reviewer":{"login":"o"}}]}]}'
  elif [[ "$path" == */actions/runners ]]; then echo '{"total_count":0,"runners":[]}'
  elif [[ "$path" == */variables/* ]]; then echo "gh: Not Found (HTTP 404)" >&2; exit 1
  else echo '{}'; fi
fi
STUB
  chmod +x "$B/gh"
  export PATH="$B:$PATH"
  export HOME="$BATS_TEST_TMPDIR/home"
  export XDG_STATE_HOME="$BATS_TEST_TMPDIR/xdg"
  mkdir -p "$HOME"
  VARS="$BATS_TEST_TMPDIR/vars.env"
  cat > "$VARS" <<'ENVF'
# comment
RELEASE_APP_ID=12345
DEPLOY_ENABLED=true
SERVICE_NAME=demo
ENV_dev_GCP_DEPLOYER_SA=dev-sa
ENV_production_GCP_DEPLOYER_SA=prod-sa
ENVF
}

count() { grep -c -- "$1" "$LOG" || true; }
mutating() { grep -cE '^gh (api )?-X (POST|PUT|PATCH|DELETE)|^gh api -X (POST|PUT|PATCH|DELETE)' "$LOG" || true; }

@test "--repo is required" {
  run "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"--repo"* ]]
}

@test "dry run is the default and makes no call at all" {
  run "$SCRIPT" --repo o/r --vars-file "$VARS"
  [ "$status" -eq 0 ]
  [[ "$output" == *"DRY-RUN POST /repos/o/r/rulesets"* ]]
  [[ "$output" == *"CI OK"* ]]
  [ ! -s "$LOG" ]
}

@test "dry run with --diff reads but never mutates" {
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --diff
  [ "$status" -eq 0 ]
  [ -s "$LOG" ]
  [ "$(mutating)" -eq 0 ]
}

@test "--apply issues the expected calls" {
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 0 ]
  grep -q 'gh api -X POST .*/repos/o/r/rulesets' "$LOG"
  grep -q 'BODY .*"context":"CI OK"' "$LOG"
  grep -q 'BODY .*"actor_type":"Integration".*"actor_id":12345\|BODY .*"actor_id":12345.*"actor_type":"Integration"' "$LOG"
  grep -q 'gh api -X PUT .*/repos/o/r/environments/production' "$LOG"
  grep -q 'BODY .*"prevent_self_review":false' "$LOG"
  grep -q 'BODY .*"id":4242' "$LOG"
  grep -q 'gh api -X POST .*/environments/dev/deployment-branch-policies' "$LOG"
  grep -q 'BODY {"name":"v\*","type":"tag"}' "$LOG"
  grep -q 'gh api -X POST .*/repos/o/r/actions/variables' "$LOG"
  grep -q 'BODY {"name":"GCP_DEPLOYER_SA","value":"prod-sa"}' "$LOG"
  grep -q 'gh api -X PUT .*/actions/permissions/workflow' "$LOG"
  grep -q 'BODY .*"secret_scanning_push_protection"' "$LOG"
  [ "$(count '"is_template"')" -eq 0 ]
}

@test "--template is opt-in" {
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply --template
  [ "$status" -eq 0 ]
  grep -q 'BODY {"is_template":true}' "$LOG"
}

@test "the rulesets, Actions policy and environments carry the designed bodies" {
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 0 ]
  main="$(grep '^BODY {"name":"main"' "$LOG")"
  [[ "$main" == *'"bypass_mode":"pull_request"'* ]]
  [[ "$main" == *'"required_approving_review_count":0'* ]]
  [[ "$main" == *'{"type":"non_fast_forward"}'* ]]
  [[ "$main" == *'{"type":"deletion"}'* ]]
  [[ "$main" == *'"required_status_checks":[{"context":"CI OK","integration_id":15368}]'* ]]
  [[ "$main" != *'"bypass_mode":"always"'* ]]
  tag="$(grep '^BODY {"name":"release-tags"' "$LOG")"
  [[ "$tag" == *'"refs/tags/v*"'* && "$tag" == *'{"type":"creation"}'* && "$tag" == *'{"type":"update"}'* ]]
  grep -q 'BODY {"enabled":true,"allowed_actions":"selected","sha_pinning_required":true}' "$LOG"
  grep -q 'BODY {"github_owned_allowed":false,"verified_allowed":false,"patterns_allowed":\[' "$LOG"
  grep -q 'BODY {"default_workflow_permissions":"read","can_approve_pull_request_reviews":false}' "$LOG"
  grep -q 'BODY {"approval_policy":"all_external_contributors"}' "$LOG"
  grep -q 'environments/qa/deployment-branch-policies' "$LOG"
  grep -q 'BODY {"name":"main","type":"branch"}' "$LOG"
  grep -q 'environments/release/deployment-branch-policies' "$LOG"
  [ "$(grep -c 'environments/release/deployment-branch-policies' "$LOG")" -ge 2 ]  # the GET and one POST
}

@test "apply saves what it overwrites outside the repo, and says where" {
  applied_state
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"$XDG_STATE_HOME/github-settings-backups"* ]]
  [ -n "$(find "$XDG_STATE_HOME/github-settings-backups" -name '*rulesets_7.json')" ]
}

@test "a backup directory inside the repo is refused" {
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply --backup-dir "$BATS_TEST_DIRNAME/b"
  [ "$status" -ne 0 ]
  [[ "$output" == *"outside the repository"* ]]
}

@test "a vars-file inside the repo is refused" {
  cp "$VARS" "$BATS_TEST_DIRNAME/../../.vars-test.env"
  run "$SCRIPT" --repo o/r --vars-file "$BATS_TEST_DIRNAME/../../.vars-test.env"
  rm -f "$BATS_TEST_DIRNAME/../../.vars-test.env"
  [ "$status" -ne 0 ]
  [[ "$output" == *"outside the repository"* ]]
}

@test "the ids must be numbers" {
  echo 'REVIEWER_USER_ID=abc' >> "$VARS"
  run "$SCRIPT" --repo o/r --vars-file "$VARS"
  [ "$status" -ne 0 ]
  [[ "$output" == *"REVIEWER_USER_ID must be a number"* ]]
}

@test "the reviewer is resolved and printed; an organisation owner needs REVIEWER_USER_ID" {
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --diff
  [[ "$output" == *"production reviewer: o (id 4242)"* ]]
  echo '{"id":1,"login":"o","type":"Organization"}' > "$STUB_DIR/_users_o"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -ne 0 ]
  [[ "$output" == *"REVIEWER_USER_ID"* ]]
  [ "$(mutating)" -eq 0 ]
  echo 'REVIEWER_USER_ID=7' >> "$VARS"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"production reviewer: alice (id 7)"* ]]
}

@test "self-hosted runners fail --apply and warn otherwise" {
  echo '{"total_count":2,"runners":[]}' > "$STUB_DIR/_repos_o_r_actions_runners"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --diff
  [ "$status" -eq 0 ]
  [[ "$output" == *"self-hosted runner"* ]]
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -ne 0 ]
  [ "$(mutating)" -eq 0 ]
}

@test "a GET error other than 404 fails the run" {
  echo '^GET /repos/o/r/rulesets' > "$STUB_DIR/FAIL"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 1 ]
  [[ "$output" == *"GET /repos/o/r/rulesets"* ]]
}

@test "refused immutable releases and fork approval are skipped, summarised, and exit 3" {
  echo 'PUT /repos/o/r/(immutable-releases|actions/permissions/fork-pr-contributor-approval)' > "$STUB_DIR/FAIL"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 3 ]
  [[ "$output" == *"SKIPPED (2)"* ]]
  [[ "$output" == *"immutable releases"* && "$output" == *"fork pull request approval"* ]]
  grep -q 'rulesets' "$LOG"
}

@test "a 422 on sha_pinning_required retries without it, then reports it" {
  echo 'PUT /repos/o/r/actions/permissions .*sha_pinning_required' > "$STUB_DIR/FAIL"
  echo '{"sha_pinning_required":false}' > "$STUB_DIR/_repos_o_r_actions_permissions"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 3 ]
  [[ "$output" == *"sha_pinning_required was refused"* ]]
  [[ "$output" == *"sha_pinning_required is now: false"* ]]
  grep -q 'BODY {"enabled":true,"allowed_actions":"selected"}' "$LOG"
}

@test "a 422 on the production reviewers degrades to a skip on a private repo" {
  echo '{"private":true,"default_branch":"main"}' > "$STUB_DIR/_repos_o_r"
  echo 'PUT /repos/o/r/environments/production .*reviewers' > "$STUB_DIR/FAIL"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 3 ]
  [[ "$output" == *"required reviewers on production"* ]]
}

@test "a 422 on the production reviewers is an error on a public repo" {
  echo 'PUT /repos/o/r/environments/production .*reviewers' > "$STUB_DIR/FAIL"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 1 ]
  [ "$(count '"reviewers"')" -eq 1 ]
}

@test "a 5xx on the production reviewers never falls back to a reviewer-less PUT" {
  echo '{"private":true,"default_branch":"main"}' > "$STUB_DIR/_repos_o_r"
  echo "gh: Server Error (HTTP 502)" > "$STUB_DIR/FAIL_MSG"
  echo 'PUT /repos/o/r/environments/production .*reviewers' > "$STUB_DIR/FAIL"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 1 ]
  [ "$(count 'gh api -X PUT .*environments/production')" -eq 1 ]  # the one PUT, with reviewers
  [ "$(count 'environments/release')" -eq 0 ]
}

@test "the reviewers are read back and printed" {
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 0 ]
  [[ "$output" == *"production reviewers: o"* ]]
}

@test "no reviewers on read-back: an error on a public repo, a skip on a private one" {
  echo '{"protection_rules":[]}' > "$STUB_DIR/_repos_o_r_environments_production"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 1 ]
  [[ "$output" == *"no required reviewers"* ]]
  echo '{"private":true,"default_branch":"main"}' > "$STUB_DIR/_repos_o_r"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 3 ]
  [[ "$output" == *"SKIPPED"*"no required reviewers"* ]]
}

@test "only HTTP 404 means not found; another 404 mention is an error" {
  echo '^GET /repos/o/r/rulesets' > "$STUB_DIR/FAIL"
  echo "gh: Server Error (HTTP 500): upstream said 404 somewhere" > "$STUB_DIR/FAIL_MSG"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 1 ]
  echo "gh: Not Found (HTTP 404)" > "$STUB_DIR/FAIL_MSG"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 0 ]
}

@test "ACTIONS_APP_ID in the vars-file is used, validated, and overridden by the flag" {
  echo '{"check_runs":[{"app":{"id":99,"slug":"other-app"}}]}' > "$STUB_DIR/_repos_o_r_commits_main_check-runs"
  echo 'ACTIONS_APP_ID=99' >> "$VARS"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 0 ]
  grep -q '"integration_id":99' "$LOG"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply --actions-app-id 15368
  [ "$status" -ne 0 ]
  [[ "$output" == *"other-app"* ]]
  sed -i.bak 's/ACTIONS_APP_ID=99/ACTIONS_APP_ID=abc/' "$VARS"
  : > "$LOG"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -ne 0 ]
  [[ "$output" == *"ACTIONS_APP_ID must be a number"* ]]
  [ ! -s "$LOG" ]
}

@test "a different app behind CI OK refuses --apply with no change; --diff warns" {
  echo '{"check_runs":[{"app":{"id":99,"slug":"other-app"}}]}' > "$STUB_DIR/_repos_o_r_commits_main_check-runs"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -ne 0 ]
  [[ "$output" == *"other-app"* ]]
  [ "$(mutating)" -eq 0 ]
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --diff
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARNING"*"other-app"* ]]
}

@test "the required check and its app are printed; --actions-app-id overrides" {
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --diff
  [[ "$output" == *"CI OK: pinned app id 15368; resolved github-actions (id 15368)"* ]]
  echo '{"check_runs":[{"app":{"id":99,"slug":"other-app"}}]}' > "$STUB_DIR/_repos_o_r_commits_main_check-runs"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply --actions-app-id 99
  [ "$status" -eq 0 ]
  grep -q '"integration_id":99' "$LOG"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --actions-app-id x
  [ "$status" -ne 0 ]
}

@test "nothing resolvable behind CI OK refuses --apply" {
  echo '{}' > "$STUB_DIR/_apps_github-actions"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -ne 0 ]
  [[ "$output" == *"could not resolve"* ]]
  [ "$(mutating)" -eq 0 ]
}

@test "a failed backup read stops the run before the PUT" {
  echo '^GET /repos/o/r/actions/permissions$' > "$STUB_DIR/FAIL"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 1 ]
  [ "$(mutating)" -eq 0 ]
}

@test "a failed variable read is not treated as missing" {
  echo '^GET .*/variables/SERVICE_NAME' > "$STUB_DIR/FAIL"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 1 ]
  [ "$(count 'gh api -X POST .*/variables')" -eq 0 ]
  [ "$(count 'gh api -X PATCH .*/variables')" -eq 0 ]
  [ "$(count '"name":"SERVICE_NAME"')" -eq 0 ]
}

@test "selected-actions is backed up only while allowed_actions is selected" {
  echo '{"allowed_actions":"all"}' > "$STUB_DIR/_repos_o_r_actions_permissions"
  echo 'GET /repos/o/r/actions/permissions/selected-actions' > "$STUB_DIR/FAIL"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 0 ]
  echo '{"allowed_actions":"selected"}' > "$STUB_DIR/_repos_o_r_actions_permissions"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 1 ]
}

@test "each environment gets exactly its deployment rules" {
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 0 ]
  rules() { # env: the bodies sent right after a POST to that environment's policies
    grep -A1 "^gh api -X POST .*/environments/$1/deployment-branch-policies" "$LOG" | grep '^BODY' | sort
  }
  [ "$(rules release)" = 'BODY {"name":"main","type":"branch"}' ]
  [ "$(rules production)" = 'BODY {"name":"v*","type":"tag"}' ]
  want="$(printf '%s\n' 'BODY {"name":"main","type":"branch"}' 'BODY {"name":"v*","type":"tag"}' | sort)"
  [ "$(rules qa)" = "$want" ]
  [ "$(rules dev)" = "$want" ]
}

@test "environments and stale rules are backed up before they are overwritten or deleted" {
  applied_state
  echo '{"protection_rules":[{"type":"required_reviewers","reviewers":[{"reviewer":{"login":"o"}}]}]}' > "$STUB_DIR/_repos_o_r_environments_production"
  echo '{"branch_policies":[{"id":2,"name":"v*","type":"tag"},{"id":9,"name":"v*","type":"branch"}]}' \
    > "$STUB_DIR/_repos_o_r_environments_production_deployment-branch-policies"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 0 ]
  [ -n "$(find "$XDG_STATE_HOME/github-settings-backups" -name 'repos_o_r_environments_production.json')" ]
  [ -n "$(find "$XDG_STATE_HOME/github-settings-backups" -name '*production_deployment-branch-policies.json')" ]
}


applied_state() {
  echo '[{"id":7,"name":"main"},{"id":8,"name":"release-tags"}]' > "$STUB_DIR/_repos_o_r_rulesets"
  echo '{"id":7}' > "$STUB_DIR/_repos_o_r_rulesets_7"
  for v in DEPLOY_ENABLED SERVICE_NAME RELEASE_APP_ID; do echo "{\"name\":\"$v\"}" > "$STUB_DIR/_repos_o_r_actions_variables_$v"; done
  echo '{"name":"GCP_DEPLOYER_SA"}' > "$STUB_DIR/_repos_o_r_environments_dev_variables_GCP_DEPLOYER_SA"
  echo '{"name":"GCP_DEPLOYER_SA"}' > "$STUB_DIR/_repos_o_r_environments_production_variables_GCP_DEPLOYER_SA"
  for e in dev qa production release; do
    case "$e" in
      dev | qa) p='[{"id":1,"name":"main","type":"branch"},{"id":2,"name":"v*","type":"tag"}]' ;;
      production) p='[{"id":2,"name":"v*","type":"tag"}]' ;;
      release) p='[{"id":1,"name":"main","type":"branch"}]' ;;
    esac
    echo "{\"branch_policies\":$p}" > "$STUB_DIR/_repos_o_r_environments_${e}_deployment-branch-policies"
  done
}

@test "a second apply against the applied state updates in place and creates or deletes nothing" {
  applied_state
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 0 ]
  grep -q 'gh api -X PUT .*/rulesets/7' "$LOG"
  grep -q 'gh api -X PUT .*/rulesets/8' "$LOG"
  grep -q 'gh api -X PATCH .*/actions/variables/SERVICE_NAME' "$LOG"
  grep -q 'gh api -X PATCH .*/environments/dev/variables/GCP_DEPLOYER_SA' "$LOG"
  [ "$(count 'gh api -X POST')" -eq 0 ]
  [ "$(count 'gh api -X DELETE')" -eq 0 ]
}

@test "stale deployment rules are deleted, and shown as DRY-RUN DELETE under --diff" {
  applied_state
  echo '{"branch_policies":[{"id":2,"name":"v*","type":"tag"},{"id":9,"name":"v*","type":"branch"},{"id":10,"name":"*","type":"branch"}]}' \
    > "$STUB_DIR/_repos_o_r_environments_production_deployment-branch-policies"
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --diff
  [ "$status" -eq 0 ]
  [[ "$output" == *"DRY-RUN DELETE /repos/o/r/environments/production/deployment-branch-policies/9"* ]]
  [[ "$output" == *"DRY-RUN DELETE /repos/o/r/environments/production/deployment-branch-policies/10"* ]]
  [[ "$output" != *"deployment-branch-policies/2"* ]]
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  grep -q 'gh api -X DELETE .*/production/deployment-branch-policies/9' "$LOG"
  grep -q 'gh api -X DELETE .*/production/deployment-branch-policies/10' "$LOG"
}

@test "the allowlist is derived from the workflows' uses lines" {
  run "$SCRIPT" --repo o/r --vars-file "$VARS"
  [ "$status" -eq 0 ]
  [[ "$output" == *'"actions/checkout@*"'* ]]
  [[ "$output" == *'"github/codeql-action@*"'* ]]
  [[ "$output" == *'"github/codeql-action/init@*"'* ]]
  [[ "$output" == *'"actions/cache/restore@*"'* ]]
  [[ "$output" != *'"./.github'* ]]
}

@test "secrets are never set and are listed for the owner" {
  run "$SCRIPT" --repo o/r --vars-file "$VARS" --apply
  [ "$status" -eq 0 ]
  [ "$(count '/secrets')" -eq 0 ]
  [[ "$output" == *"RELEASE_APP_PRIVATE_KEY"* ]]
}

@test "a missing RELEASE_APP_ID blocks --apply, before any change" {
  grep -v RELEASE_APP_ID "$VARS" > "$VARS.new"
  run "$SCRIPT" --repo o/r --vars-file "$VARS.new" --apply
  [ "$status" -ne 0 ]
  [[ "$output" == *"RELEASE_APP_ID"* ]]
  [ "$(mutating)" -eq 0 ]
}

@test "the vars-file refuses lines that are not NAME=value" {
  echo 'not a var line' >> "$VARS"
  run "$SCRIPT" --repo o/r --vars-file "$VARS"
  [ "$status" -ne 0 ]
  [[ "$output" == *"not NAME=value"* ]]
}

@test "the vars-file refuses unknown names" {
  echo 'EVIL=1' >> "$VARS"
  run "$SCRIPT" --repo o/r --vars-file "$VARS"
  [ "$status" -ne 0 ]
  [[ "$output" == *"unknown variable EVIL"* ]]
}
