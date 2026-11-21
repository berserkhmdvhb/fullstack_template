#!/usr/bin/env bash
# deploy-env.sh — deploy one environment by digest, verify, smoke test, and roll back on a failed check.
#
# 1. image values from the manifest plus the release digests (every digest must be sha256:<64 hex>)
# 2. record the last `deployed` Helm revision (none on a first install)
# 3. helm upgrade --install --rollback-on-failure --wait --wait-for-jobs (Helm rolls a failed upgrade back
#    itself; --wait-for-jobs makes a failed migration Job fail the release; a failed deploy prints the
#    newest migration Job's status and logs first)
# 4. deploy-verify.sh, then scripts/smoke-test.sh through a port-forward
# 5. when verify or smoke fails: helm rollback to the recorded revision, then exit 1
#
# Env (all required unless a default is shown): SERVICE_NAME DEPLOY_ENV (dev|qa|production) CHART_DIR
# DEPLOY_IMAGES (comma list of image ids) DIGESTS (JSON of <ID>_IMAGE_DIGEST) IMAGE_TAG HELM_TIMEOUT
# BACKEND_PORT API_VERSION; FRONTEND_PORT (3000); WORK_DIR (RUNNER_TEMP); SMOKE_WAIT_TRIES (30), SMOKE_WAIT_SLEEP (2).
# MATRIX_CMD (the matrix helper command; deploy.yml points it at a PyYAML venv) and, for tests only,
# VERIFY_SCRIPT and SMOKE_SCRIPT are overridable.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/scripts/ci/migration-job-lib.sh"
fail() { echo "::error::$*" >&2; exit 1; }

for v in SERVICE_NAME DEPLOY_ENV CHART_DIR DEPLOY_IMAGES DIGESTS IMAGE_TAG HELM_TIMEOUT BACKEND_PORT API_VERSION; do
  [[ -n "${!v:-}" ]] || fail "$v is required"
done
case "$DEPLOY_ENV" in dev|qa|production) ;; *) fail "DEPLOY_ENV must be dev, qa or production" ;; esac
WORK_DIR="${WORK_DIR:-${RUNNER_TEMP:-}}"
[[ -n "$WORK_DIR" ]] || fail "WORK_DIR or RUNNER_TEMP is required"
MATRIX_CMD="${MATRIX_CMD:-python3 $ROOT/.github/scripts/container_matrix.py}"
VERIFY_SCRIPT="${VERIFY_SCRIPT:-$ROOT/scripts/ci/deploy-verify.sh}"
SMOKE_SCRIPT="${SMOKE_SCRIPT:-$ROOT/scripts/smoke-test.sh}"
NAMESPACE="${SERVICE_NAME}-${DEPLOY_ENV}"
# The backend Service. The port-forwarded checks send its name as Host, as the frontend does
# in-cluster: the backend refuses a Host that is not in the ALLOWED_HOSTS the chart renders.
BACKEND_SERVICE="${SERVICE_NAME}-backend"
VALUES="$WORK_DIR/image-values.json"

# The digest variable of an image id: uppercase, non-alphanumerics to _ (as release.yml writes them).
digest_var() { local v="${1^^}"; echo "${v//[^A-Z0-9_]/_}_IMAGE_DIGEST"; }

# 1. image values
# shellcheck disable=SC2086 # MATRIX_CMD is a command plus optional arguments
matrix="$($MATRIX_CMD --deploy-matrix --select "$DEPLOY_IMAGES")"
jq -n --argjson m "$matrix" --argjson d "$DIGESTS" --arg tag "$IMAGE_TAG" '
  {images: ($m.include | map({key: .id, value: {
    registry: (.repository | split("/")[0]),
    repository: (.repository | split("/")[1:] | join("/")),
    tag: $tag}}) | from_entries)}' > "$VALUES"
components=()
for id in ${DEPLOY_IMAGES//,/ }; do
  var="$(digest_var "$id")"
  dg="$(jq -r --arg k "$var" '.[$k] // ""' <<<"$DIGESTS")"
  [[ "$dg" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "$var must be sha256:<64 hex>; refusing to deploy $id by tag"
  jq --arg id "$id" --arg dg "$dg" '.images[$id].digest = $dg' "$VALUES" > "$VALUES.tmp" && mv "$VALUES.tmp" "$VALUES"
  export "$var=$dg"
  components+=("$id")
  echo "- \`$id\`: $dg" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
done

# 2. the revision to return to
history="$(helm history "$SERVICE_NAME" -n "$NAMESPACE" -o json 2>/dev/null || echo '[]')"
recorded="$(jq -r '[.[] | select(.status == "deployed")] | last | .revision // empty' <<<"$history")"
echo "last deployed revision before this deploy: ${recorded:-none (first install)}"

# 3. deploy. The migration Job carries helm.sh/resource-policy keep, so Helm's own rollback leaves
# a failed Job (and its Pods) in place: print its status and logs before failing.
helm_rc=0
helm upgrade --install "$SERVICE_NAME" "$CHART_DIR" \
  -f "$CHART_DIR/values.${DEPLOY_ENV}.yaml" -f "$VALUES" \
  --namespace "$NAMESPACE" \
  --rollback-on-failure --wait --wait-for-jobs --timeout "$HELM_TIMEOUT" || helm_rc=$?
if (( helm_rc != 0 )); then
  echo "::group::migration Job of ${SERVICE_NAME} in ${DEPLOY_ENV} (the deploy failed)"
  migration_job_report "$NAMESPACE" "$SERVICE_NAME" || true
  echo "::endgroup::"
  exit "$helm_rc"
fi
helm history "$SERVICE_NAME" -n "$NAMESPACE" --max 5

# 4. verify and smoke
checks() {
  RELEASE="$SERVICE_NAME" NAMESPACE="$NAMESPACE" COMPONENTS="${components[*]}" bash "$VERIFY_SCRIPT" || return 1
  local pf rc=0
  kubectl -n "$NAMESPACE" port-forward "svc/${BACKEND_SERVICE}" "${BACKEND_PORT}:${BACKEND_PORT}" >"$WORK_DIR/pf.log" 2>&1 &
  pf=$!
  for _ in $(seq 1 "${SMOKE_WAIT_TRIES:-30}"); do
    curl -fsS --max-time 3 "http://127.0.0.1:${BACKEND_PORT}/api/${API_VERSION}/health" >/dev/null 2>&1 && break
    sleep "${SMOKE_WAIT_SLEEP:-2}"
  done
  bash "$SMOKE_SCRIPT" --base-url "http://127.0.0.1:${BACKEND_PORT}" --host "${BACKEND_SERVICE}" --path "/api/${API_VERSION}" \
    --format junit --out "$WORK_DIR/smoke-${DEPLOY_ENV}.xml" || rc=1
  kill "$pf" 2>/dev/null || true
  return "$rc"
}

# 5. roll back to the revision recorded in step 2, only when a check failed
if ! checks; then
  if [[ -z "$recorded" ]]; then
    fail "verify or smoke failed in ${DEPLOY_ENV}, and there is no earlier deployed revision to roll back to"
  fi
  helm rollback "$SERVICE_NAME" "$recorded" -n "$NAMESPACE" --wait --timeout "$HELM_TIMEOUT"
  echo "- Rolled $SERVICE_NAME back to revision $recorded in ${DEPLOY_ENV} after a failed verify or smoke" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
  fail "verify or smoke failed; rolled ${SERVICE_NAME} back to revision ${recorded} in ${DEPLOY_ENV}"
fi
echo "Deployed, verified and smoke tested: ${SERVICE_NAME} in ${DEPLOY_ENV}"
