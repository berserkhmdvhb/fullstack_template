#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# deploy-env.bats — scripts/ci/deploy-env.sh against stub helm, kubectl and curl.
# Verify and smoke are stubbed too (deploy-verify.bats covers the real verify).
# -----------------------------------------------------------------------------

D="sha256:$(printf 'a%.0s' {1..64})"

setup() {
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/../scripts/ci/deploy-env.sh"
  B="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$B"
  export LOG="$BATS_TEST_TMPDIR/calls.log"
  : > "$LOG"
  cat > "$B/helm" <<'STUB'
#!/usr/bin/env bash
echo "helm $*" >> "$LOG"
case "$1" in
  history)
    [[ "$*" == *--max* ]] && exit 0
    [[ -n "${STUB_HISTORY-}" ]] || { echo "Error: release: not found" >&2; exit 1; }
    echo "$STUB_HISTORY" ;;
  upgrade) exit "${STUB_UPGRADE_RC:-0}" ;;
  get) [[ -z "${STUB_NO_RELEASE-}" ]] || { echo "Error: release: not found" >&2; exit 1; }
       printf -- '---\nkind: Job\nmetadata:\n  name: svc-migrate-r2\n  labels:\n    app.kubernetes.io/component: migration\n' ;;
esac
STUB
  cat > "$B/kubectl" <<'STUB'
#!/usr/bin/env bash
echo "kubectl $*" >> "$LOG"
case "$*" in
  *port-forward*) exec sleep 30 ;;
  *"get job -l"*"--sort-by"*) printf '%s\n' "${STUB_NEWEST-}" | awk -F'|' '$1=="svc" {print $2"|"$3"|"$4"|"$5}' ;;
  *"get job -l"*) printf '%s\n' "${STUB_LEFTOVER-}" | awk -F'|' '$1=="svc" {print $2"|"$3}' ;;
  *"get job svc-migrate-r2"*jsonpath*) echo "svc-migrate-r2||4|True" ;;
  *"get job"*) echo "job summary" ;;
  *logs*) echo "migration log" ;;
esac
STUB
  printf '#!/usr/bin/env bash\nexit 0\n' > "$B/curl"
  cat > "$BATS_TEST_TMPDIR/matrix.sh" <<'STUB'
#!/usr/bin/env bash
echo "matrix $*" >> "$LOG"
ids="${@: -1}"
out=""
for i in ${ids//,/ }; do out+="{\"id\":\"$i\",\"repository\":\"ghcr.io/o/r/$i\"},"; done
echo "{\"include\":[${out%,}]}"
STUB
  cat > "$BATS_TEST_TMPDIR/verify.sh" <<'STUB'
#!/usr/bin/env bash
echo "verify COMPONENTS=$COMPONENTS WEB_UI=${WEB_UI_IMAGE_DIGEST-} RELEASE=$RELEASE NAMESPACE=$NAMESPACE" >> "$LOG"
exit "${STUB_VERIFY_RC:-0}"
STUB
  cat > "$BATS_TEST_TMPDIR/smoke.sh" <<'STUB'
#!/usr/bin/env bash
echo "smoke $*" >> "$LOG"
exit "${STUB_SMOKE_RC:-0}"
STUB
  chmod +x "$B"/* "$BATS_TEST_TMPDIR"/*.sh
  export PATH="$B:$PATH"
  export MATRIX_CMD="$BATS_TEST_TMPDIR/matrix.sh" VERIFY_SCRIPT="$BATS_TEST_TMPDIR/verify.sh" SMOKE_SCRIPT="$BATS_TEST_TMPDIR/smoke.sh"
  export WORK_DIR="$BATS_TEST_TMPDIR" SMOKE_WAIT_TRIES=1 SMOKE_WAIT_SLEEP=0
  export SERVICE_NAME=svc DEPLOY_ENV=dev CHART_DIR=charts/x HELM_TIMEOUT=5m IMAGE_TAG=t1
  export DEPLOY_IMAGES=backend,frontend BACKEND_PORT=8000 API_VERSION=v1
  export DIGESTS="{\"BACKEND_IMAGE_DIGEST\":\"$D\",\"FRONTEND_IMAGE_DIGEST\":\"$D\"}"
  export STUB_HISTORY='[{"revision":1,"status":"superseded"},{"revision":2,"status":"deployed"}]'
}

@test "happy path: upgrade by digest, verify, smoke, no rollback" {
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q "helm upgrade --install svc charts/x" "$LOG"
  grep -q -- "--rollback-on-failure --wait --wait-for-jobs --timeout 5m" "$LOG"
  grep -q -- "-n svc-dev" "$LOG"
  grep -q "^verify COMPONENTS=backend frontend" "$LOG"
  grep -q "^smoke --base-url http://127.0.0.1:8000 --host svc-backend --path /api/v1" "$LOG"
  grep -q "port-forward svc/svc-backend 8000:8000" "$LOG"
  run grep -q "helm rollback" "$LOG"
  [ "$status" -ne 0 ]
  [ "$(jq -r .images.backend.digest "$WORK_DIR/image-values.json")" = "$D" ]
  [ "$(jq -r .images.backend.registry "$WORK_DIR/image-values.json")" = "ghcr.io" ]
  [ "$(jq -r .images.backend.repository "$WORK_DIR/image-values.json")" = "o/r/backend" ]
}

@test "a verify failure rolls back to the recorded deployed revision" {
  STUB_VERIFY_RC=1 run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  grep -q "helm rollback svc 2 -n svc-dev --wait --timeout 5m" "$LOG"
  run grep -q "^smoke" "$LOG"
  [ "$status" -ne 0 ]
}

@test "a smoke failure also rolls back to the recorded revision" {
  STUB_SMOKE_RC=1 run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  grep -q "helm rollback svc 2 -n svc-dev" "$LOG"
}

@test "the recorded revision is the last deployed one, not the last entry" {
  STUB_HISTORY='[{"revision":3,"status":"deployed"},{"revision":4,"status":"failed"}]' STUB_VERIFY_RC=1 run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  grep -q "helm rollback svc 3 " "$LOG"
}

@test "first install: a failed check fails clearly and rolls nothing back" {
  STUB_HISTORY="" STUB_VERIFY_RC=1 run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"no earlier deployed revision"* ]]
  run grep -q "helm rollback" "$LOG"
  [ "$status" -ne 0 ]
}

@test "a failed upgrade is left to helm's own rollback" {
  STUB_UPGRADE_RC=1 run bash "$SCRIPT"
  [ "$status" -ne 0 ]
  run grep -q "helm rollback" "$LOG"
  [ "$status" -ne 0 ]
  run grep -q "^verify" "$LOG"
  [ "$status" -ne 0 ]
}

@test "a failed upgrade prints the newest migration job's status and logs, then fails" {
  STUB_UPGRADE_RC=1 STUB_NEWEST=$'svc|svc-migrate-r2||4|True' run bash "$SCRIPT"
  [ "$status" -ne 0 ]
  grep -q "kubectl .*logs -l job-name=svc-migrate-r2 --tail" "$LOG"
}

@test "a failed first install (release gone) still prints the failed migration job and its logs" {
  STUB_HISTORY="" STUB_UPGRADE_RC=1 STUB_NO_RELEASE=1 STUB_NEWEST=$'svc|svc-migrate-r1||4|True' run bash "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"svc-migrate-r1"* ]]
  grep -q "kubectl .*logs -l job-name=svc-migrate-r1 --tail" "$LOG"
}

@test "a failed upgrade prints the newest (failed) job, not the one the rolled-back manifest names" {
  STUB_UPGRADE_RC=1 STUB_NEWEST=$'svc|svc-migrate-r2|1||\nsvc|svc-migrate-r3||4|True' run bash "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"svc-migrate-r3"* ]]
  grep -q "kubectl .*logs -l job-name=svc-migrate-r3 --tail" "$LOG"
}

@test "a first install deletes a leftover Failed migration job of this release before installing" {
  STUB_HISTORY="" STUB_LEFTOVER=$'svc|svc-migrate-r1|True' run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"svc-migrate-r1"* ]]
  grep -q "kubectl delete job svc-migrate-r1 -n svc-dev" "$LOG"
  [ "$(grep -n "delete job" "$LOG" | head -1 | cut -d: -f1)" -lt "$(grep -n "helm upgrade" "$LOG" | head -1 | cut -d: -f1)" ]
}

@test "a first install with no leftover job deletes nothing" {
  STUB_HISTORY="" STUB_LEFTOVER=$'svc|svc-migrate-r1|' run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  run grep -q "delete job" "$LOG"
  [ "$status" -ne 0 ]
}

@test "an upgrade deletes no migration job" {
  STUB_LEFTOVER=$'svc|svc-migrate-r1|True' run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  run grep -q "delete job" "$LOG"
  [ "$status" -ne 0 ]
}

@test "another release's failed job is never deleted on a first install" {
  STUB_HISTORY="" STUB_LEFTOVER=$'other|other-migrate-r1|True' run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  run grep -q "delete job" "$LOG"
  [ "$status" -ne 0 ]
}

@test "a non-sha256 digest is refused before any helm upgrade" {
  DIGESTS="{\"BACKEND_IMAGE_DIGEST\":\"latest\",\"FRONTEND_IMAGE_DIGEST\":\"$D\"}" run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"sha256"* ]]
  run grep -q "helm upgrade" "$LOG"
  [ "$status" -ne 0 ]
}

@test "a missing digest is refused" {
  DIGESTS="{\"BACKEND_IMAGE_DIGEST\":\"$D\"}" run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  run grep -q "helm upgrade" "$LOG"
  [ "$status" -ne 0 ]
}

@test "an environment outside the allowlist is refused" {
  DEPLOY_ENV="dev;x" run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [ ! -s "$LOG" ]
}

@test "a hyphenated image id gets a sanitised digest variable and a component" {
  DEPLOY_IMAGES=backend,web-ui DIGESTS="{\"BACKEND_IMAGE_DIGEST\":\"$D\",\"WEB_UI_IMAGE_DIGEST\":\"$D\"}" run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q "^verify COMPONENTS=backend web-ui WEB_UI=$D" "$LOG"
}

@test "missing inputs are refused" {
  unset SERVICE_NAME
  run bash "$SCRIPT"
  [ "$status" -eq 1 ]
}
