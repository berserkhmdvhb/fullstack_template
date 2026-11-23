#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# preview-cleanup.bats — scripts/ci/preview-cleanup.sh against stub helm and kubectl.
# tests/k8s/test_previews.py runs it on the kind cluster as the preview deployer.
# -----------------------------------------------------------------------------

setup() {
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/../scripts/ci/preview-cleanup.sh"
  B="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$B"
  export LOG="$BATS_TEST_TMPDIR/calls.log"
  : > "$LOG"
  cat > "$B/helm" <<'STUB'
#!/usr/bin/env bash
echo "helm $*" >> "$LOG"
case "$1" in
  list) echo "${STUB_LIST-[]}" ;;
  status)
    [[ -z "${STUB_STATUS_ERR-}" ]] || { echo "Error: $STUB_STATUS_ERR" >&2; exit 1; }
    [[ " ${STUB_GONE-} " != *" $2 "* ]] || { echo "Error: release: not found" >&2; exit 1; }
    echo "STATUS: deployed" ;;
  uninstall) exit "${STUB_UNINSTALL_RC:-0}" ;;
esac
STUB
  cat > "$B/kubectl" <<'STUB'
#!/usr/bin/env bash
echo "kubectl $*" >> "$LOG"
STUB
  chmod +x "$B"/*
  export PATH="$B:$PATH"
  export SERVICE_NAME=svc
  unset NAMESPACE OPEN_PRS PREVIEW_TTL_DAYS
  DAY=86400
  NOW="$(date -u +%s)"
  fresh="$(date -u -d "@$((NOW - DAY / 2))" +%Y-%m-%dT%H:%M:%SZ)"
  old="$(date -u -d "@$((NOW - 8 * DAY))" +%Y-%m-%dT%H:%M:%SZ)"
  export fresh old
}

rel() { printf '{"name":"%s","updated":"%s"}' "$1" "$2"; }

@test "--pr N uninstalls <service>-pr-N in <service>-preview and deletes its kept Jobs and its volume" {
  run bash "$SCRIPT" --pr 12
  [ "$status" -eq 0 ]
  grep -qx "helm status svc-pr-12 -n svc-preview" "$LOG"
  grep -qx "helm uninstall svc-pr-12 -n svc-preview --wait --timeout 5m" "$LOG"
  grep -qx "kubectl -n svc-preview delete jobs -l app.kubernetes.io/instance=svc-pr-12 --ignore-not-found" "$LOG"
  grep -qx "kubectl -n svc-preview-db delete pvc -l app.kubernetes.io/instance=svc-pr-12 --ignore-not-found" "$LOG"
  [[ "$output" == *"Removed the preview svc-pr-12"* ]]
}

@test "--pr N of a preview already gone still deletes what Helm kept, and succeeds" {
  STUB_GONE=svc-pr-12 run bash "$SCRIPT" --pr 12
  [ "$status" -eq 0 ]
  [[ "$output" == *"svc-pr-12: no release in svc-preview"* ]]
  run grep -q "helm uninstall" "$LOG"
  [ "$status" -ne 0 ]
  grep -q "delete jobs -l app.kubernetes.io/instance=svc-pr-12" "$LOG"
}

@test "a release it cannot read (forbidden, unreachable) fails, and nothing is deleted" {
  STUB_STATUS_ERR="secrets is forbidden" run bash "$SCRIPT" --pr 12
  [ "$status" -eq 1 ]
  [[ "$output" == *"cannot read the release svc-pr-12"* ]]
  run grep -q -e uninstall -e delete "$LOG"
  [ "$status" -ne 0 ]
}

@test "a failed uninstall fails the run before anything else is deleted" {
  STUB_UNINSTALL_RC=1 run bash "$SCRIPT" --pr 12
  [ "$status" -ne 0 ]
  run grep -q "^kubectl" "$LOG"
  [ "$status" -ne 0 ]
}

@test "HELM_TIMEOUT and DB_SUFFIX are taken" {
  HELM_TIMEOUT=3m DB_SUFFIX=data run bash "$SCRIPT" --pr 1
  [ "$status" -eq 0 ]
  grep -q -- "--wait --timeout 3m" "$LOG"
  grep -q "kubectl -n svc-preview-data delete pvc" "$LOG"
}

@test "any namespace but <service>-preview is refused before any call" {
  for ns in svc-dev svc-production kube-system svc-preview-db; do
    NAMESPACE="$ns" run bash "$SCRIPT" --pr 1
    [ "$status" -eq 1 ]
    [[ "$output" == *"refusing namespace ${ns}"* ]]
  done
  NAMESPACE=svc-preview run bash "$SCRIPT" --pr 1
  [ "$status" -eq 0 ]
  [ "$(grep -c '^helm uninstall' "$LOG")" -eq 1 ]
}

@test "--pr refuses anything but a pull request number, before any call" {
  for pr in 0 007 -1 abc "1 2" "1,2" ""; do
    run bash "$SCRIPT" --pr "$pr"
    [ "$status" -eq 1 ]
  done
  run bash "$SCRIPT" --pr
  [ "$status" -eq 1 ]
  [ ! -s "$LOG" ]
}

@test "missing SERVICE_NAME, a bad TTL and unknown arguments are refused" {
  SERVICE_NAME="" run bash "$SCRIPT" --pr 1
  [ "$status" -eq 1 ]
  PREVIEW_TTL_DAYS=1.5 OPEN_PRS="" run bash "$SCRIPT" --sweep
  [ "$status" -eq 1 ]
  run bash "$SCRIPT"
  [ "$status" -eq 2 ]
  run bash "$SCRIPT" --all
  [ "$status" -eq 2 ]
  [ ! -s "$LOG" ]
}

@test "--sweep needs OPEN_PRS set (empty means none is open)" {
  run bash "$SCRIPT" --sweep
  [ "$status" -eq 1 ]
  [[ "$output" == *"needs OPEN_PRS"* ]]
  [ ! -s "$LOG" ]
}

@test "--sweep removes the previews of closed pull requests and keeps the open, fresh ones" {
  STUB_LIST="[$(rel svc-pr-1 "$fresh"),$(rel svc-pr-2 "$fresh"),$(rel svc-pr-3 "$fresh")]" \
    OPEN_PRS="2,3" run bash "$SCRIPT" --sweep
  [ "$status" -eq 0 ]
  [[ "$output" == *"svc-pr-1: pull request 1 is not open"* ]]
  [[ "$output" == *"svc-pr-2: kept (pull request 2 is open, last deployed 0 days ago)"* ]]
  grep -qx "helm uninstall svc-pr-1 -n svc-preview --wait --timeout 5m" "$LOG"
  [ "$(grep -c '^helm uninstall' "$LOG")" -eq 1 ]
  grep -qx "helm list -n svc-preview --deployed --failed --pending --uninstalling --time-format 2006-01-02T15:04:05Z07:00 -o json" "$LOG"
}

@test "--sweep removes an open pull request's preview older than PREVIEW_TTL_DAYS (default 7)" {
  STUB_LIST="[$(rel svc-pr-2 "$old"),$(rel svc-pr-3 "$fresh")]" OPEN_PRS="2 3" run bash "$SCRIPT" --sweep
  [ "$status" -eq 0 ]
  [[ "$output" == *"svc-pr-2: last deployed 8 days ago (PREVIEW_TTL_DAYS 7)"* ]]
  grep -qx "helm uninstall svc-pr-2 -n svc-preview --wait --timeout 5m" "$LOG"
  [ "$(grep -c '^helm uninstall' "$LOG")" -eq 1 ]
  : > "$LOG"
  STUB_LIST="[$(rel svc-pr-2 "$old")]" OPEN_PRS="2" PREVIEW_TTL_DAYS=9 run bash "$SCRIPT" --sweep
  [ "$status" -eq 0 ]
  run grep -q "helm uninstall" "$LOG"
  [ "$status" -ne 0 ]
}

@test "--sweep with no open pull request removes every preview" {
  STUB_LIST="[$(rel svc-pr-1 "$fresh"),$(rel svc-pr-2 "$fresh")]" OPEN_PRS="" run bash "$SCRIPT" --sweep
  [ "$status" -eq 0 ]
  [ "$(grep -c '^helm uninstall' "$LOG")" -eq 2 ]
}

@test "--sweep leaves alone every release that is not <service>-pr-<N>" {
  STUB_LIST="[$(rel svc "$old"),$(rel svc-pr-x "$old"),$(rel other-pr-1 "$old"),$(rel svc-pr-1-db "$old"),$(rel svcXpr-1 "$old")]" \
    OPEN_PRS="" run bash "$SCRIPT" --sweep
  [ "$status" -eq 0 ]
  for r in svc svc-pr-x other-pr-1 svc-pr-1-db svcXpr-1; do
    [[ "$output" == *"${r}: not a preview release, left alone"* ]]
  done
  run grep -q -e "helm uninstall" -e "^kubectl" "$LOG"
  [ "$status" -ne 0 ]
}

@test "a dot in the service name is literal in the release pattern" {
  STUB_LIST="[$(rel aXb-pr-1 "$old")]" SERVICE_NAME=a.b OPEN_PRS="" run bash "$SCRIPT" --sweep
  [ "$status" -eq 0 ]
  [[ "$output" == *"aXb-pr-1: not a preview release, left alone"* ]]
}
