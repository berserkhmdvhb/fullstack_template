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
  template|get) [[ "$1" == template || -z "${STUB_NO_RELEASE-}" ]] || { echo "Error: release: not found" >&2; exit 1; }
       printf -- '---\nkind: Job\nmetadata:\n  name: svc-migrate-r2\n  labels:\n    app.kubernetes.io/component: migration\n'
       # Each entry is <component label>[=<image id>]: a Deployment with that label running that image,
       # by tag, or by digest with STUB_REF=digest (a values file that already pins one).
       ref=":t1"; [[ "${STUB_REF-}" != digest ]] || ref="@sha256:$(printf '0%.0s' {1..64})"
       for e in ${STUB_RENDERED-backend frontend web-ui}; do
         c="${e%%=*}" i="${e#*=}"
         printf -- '---\nkind: Deployment\nmetadata:\n  name: svc-%s\n  labels:\n    app.kubernetes.io/name: '"${STUB_NAME-svc-chart}"'\n    app.kubernetes.io/instance: svc\n    app.kubernetes.io/component: %s\nspec:\n  template:\n    spec:\n      containers:\n        - name: %s\n          image: ghcr.io/o/r/%s%s\n' "$c" "$c" "$c" "$i" "$ref"
       done
       # STUB_EXTRA_NAME: one more Deployment of this release under another app.kubernetes.io/name.
       [[ -z "${STUB_EXTRA_NAME-}" ]] || printf -- '---\nkind: Deployment\nmetadata:\n  name: svc-extra\n  labels:\n    app.kubernetes.io/name: %s\n    app.kubernetes.io/instance: svc\n' "$STUB_EXTRA_NAME" ;;
esac
STUB
  cat > "$B/kubectl" <<'STUB'
#!/usr/bin/env bash
echo "kubectl $*" >> "$LOG"
case "$*" in
  *port-forward*) exec sleep 30 ;;
  "config get-contexts -o name") printf '%s\n' "${STUB_CONTEXTS-kind-fst}" ;;
  "config current-context") echo "${STUB_CONTEXT-kind-fst}" ;;
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
echo "verify COMPONENTS=$COMPONENTS WEB_UI=${WEB_UI_IMAGE_DIGEST-} RELEASE=$RELEASE NAMESPACE=$NAMESPACE ARGS=$*" >> "$LOG"
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
  grep "helm upgrade --install svc charts/x" "$LOG" | grep -q -- "--set guards.rejectPlaceholders=true"
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

@test "an optional image the release does not run (the WAF, off) is deployed by digest but not verified" {
  DEPLOY_IMAGES=backend,frontend,waf DIGESTS="{\"BACKEND_IMAGE_DIGEST\":\"$D\",\"FRONTEND_IMAGE_DIGEST\":\"$D\",\"WAF_IMAGE_DIGEST\":\"$D\"}" run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [ "$(jq -r .images.waf.digest "$WORK_DIR/image-values.json")" = "$D" ]
  grep -q "^verify COMPONENTS=backend frontend " "$LOG"
  [[ "$output" == *"waf: not rendered by this release"* ]]
}

@test "an optional image the release runs (the WAF, on) is verified too" {
  STUB_RENDERED="backend frontend waf" DEPLOY_IMAGES=backend,frontend,waf DIGESTS="{\"BACKEND_IMAGE_DIGEST\":\"$D\",\"FRONTEND_IMAGE_DIGEST\":\"$D\",\"WAF_IMAGE_DIGEST\":\"$D\"}" run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q "^verify COMPONENTS=backend frontend waf " "$LOG"
}

@test "an optional image the release runs still needs its digest" {
  STUB_RENDERED="backend frontend waf" DEPLOY_IMAGES=backend,frontend,waf run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"WAF_IMAGE_DIGEST"* ]]
  run grep -q "helm upgrade" "$LOG"
  [ "$status" -ne 0 ]
}

@test "an optional image the release does not run deploys without its digest (an older release)" {
  DEPLOY_IMAGES=backend,frontend,waf run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [ "$(jq -r '.images.waf.digest // "none"' "$WORK_DIR/image-values.json")" = "none" ]
  grep -q "^verify COMPONENTS=backend frontend " "$LOG"
}

@test "the components are read from a render before the upgrade, with the deploy's own values" {
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep "^helm template svc charts/x" "$LOG" | grep -q -- "-f charts/x/values.dev.yaml -f $WORK_DIR/image-values.json"
  [ "$(grep -n "^helm template" "$LOG" | cut -d: -f1)" -lt "$(grep -n "^helm upgrade" "$LOG" | cut -d: -f1)" ]
}

@test "the discovery render runs without the placeholder guard, and the upgrade adds it to the same values" {
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  discovery="$(grep "^helm template svc charts/x" "$LOG")"
  [[ "$discovery" != *rejectPlaceholders* ]]
  args="${discovery#helm template svc charts/x }"
  args="${args% --namespace svc-dev}"
  grep "^helm upgrade --install svc charts/x" "$LOG" | grep -qF -- "charts/x $args --set guards.rejectPlaceholders=true --namespace svc-dev"
}

@test "an image the values file already runs by digest is still found, so it still needs its release digest" {
  STUB_REF=digest STUB_RENDERED="backend frontend jobs=worker" DEPLOY_IMAGES=backend,frontend,worker run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"WORKER_IMAGE_DIGEST"* ]]
  run grep -q "helm upgrade" "$LOG"
  [ "$status" -ne 0 ]
}

@test "an optional image is not found by digest when it does not render" {
  STUB_REF=digest DEPLOY_IMAGES=backend,frontend,waf run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"waf: not rendered by this release (feature off) and no WAF_IMAGE_DIGEST"* ]]
}

@test "a backend digest is still required, whatever renders" {
  DIGESTS="{\"FRONTEND_IMAGE_DIGEST\":\"$D\"}" run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"BACKEND_IMAGE_DIGEST"* ]]
}

@test "an image whose component label differs from its id, without a digest, fails closed" {
  STUB_RENDERED="backend frontend jobs=worker" DEPLOY_IMAGES=backend,frontend,worker run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"WORKER_IMAGE_DIGEST"* ]]
  run grep -q "helm upgrade" "$LOG"
  [ "$status" -ne 0 ]
}

@test "missing inputs are refused" {
  unset SERVICE_NAME
  run bash "$SCRIPT"
  [ "$status" -eq 1 ]
}

@test "verify finds the Pods by the chart's name label, not the release name" {
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -q "^verify .*RELEASE=svc NAMESPACE=svc-dev ARGS=--name svc-chart$" "$LOG"
}

@test "a render without the release's name label is refused before any upgrade" {
  STUB_NAME="" run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"app.kubernetes.io/name"* ]]
  run grep -q "helm upgrade" "$LOG"
  [ "$status" -ne 0 ]
}

@test "a render with more than one name label for the release is refused before any upgrade" {
  STUB_EXTRA_NAME=other-chart run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"app.kubernetes.io/name"* ]]
  run grep -q "helm upgrade" "$LOG"
  [ "$status" -ne 0 ]
}

# ---- DEPLOY_ENV=kind: test-only, refused without the flag and outside the kind context ----------

@test "kind is refused without DEPLOY_ENV_KIND_TEST" {
  DEPLOY_ENV=kind run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"test-only"* ]]
  [ ! -s "$LOG" ]
}

@test "kind is refused with the flag set to anything but 1" {
  DEPLOY_ENV=kind DEPLOY_ENV_KIND_TEST=true run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [ ! -s "$LOG" ]
}

@test "kind with the flag is refused outside the kind context" {
  DEPLOY_ENV=kind DEPLOY_ENV_KIND_TEST=1 STUB_CONTEXTS=prod STUB_CONTEXT=prod run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"kind-fst"* ]]
  run grep -q "^helm" "$LOG"
  [ "$status" -ne 0 ]
}

@test "kind with the flag is refused when the kubeconfig holds another context too" {
  DEPLOY_ENV=kind DEPLOY_ENV_KIND_TEST=1 STUB_CONTEXTS=$'kind-fst\nprod' run bash "$SCRIPT"
  [ "$status" -eq 1 ]
  run grep -q "^helm" "$LOG"
  [ "$status" -ne 0 ]
}

@test "kind with the flag is refused when helm is pointed at another context or server" {
  for v in HELM_KUBECONTEXT=prod HELM_KUBEAPISERVER=https://192.0.2.1 HELM_KUBETOKEN=t; do
    : > "$LOG"
    run env "$v" DEPLOY_ENV=kind DEPLOY_ENV_KIND_TEST=1 bash "$SCRIPT"
    [ "$status" -eq 1 ]
    run grep -q "^helm" "$LOG"
    [ "$status" -ne 0 ]
  done
}

@test "kind in the kind context: values.local.yaml and the KIND_VALUES files, its own namespace, the guard on" {
  DEPLOY_ENV=kind DEPLOY_ENV_KIND_TEST=1 KIND_VALUES="/k/a.yaml:/k/b.yaml" run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep "^helm upgrade --install svc charts/x" "$LOG" |
    grep -qF -- "-f charts/x/values.local.yaml -f /k/a.yaml -f /k/b.yaml -f $WORK_DIR/image-values.json --set guards.rejectPlaceholders=true --namespace svc-kind"
  run grep -q "values.kind.yaml" "$LOG"
  [ "$status" -ne 0 ]
  grep -q "^verify .*NAMESPACE=svc-kind" "$LOG"
}
