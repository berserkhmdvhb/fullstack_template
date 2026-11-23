#!/usr/bin/env bash
# deploy-env.sh — deploy one environment by digest, verify, smoke test, and roll back on a failed check.
#
# 1. image values from the manifest plus the release digests (every digest must be sha256:<64 hex>); a
#    render first tells which images run, and only an image the release does not render may lack one
# 2. record the last `deployed` Helm revision (none on a first install)
# 3. helm upgrade --install --rollback-on-failure --wait --wait-for-jobs (Helm rolls a failed upgrade back
#    itself; --wait-for-jobs makes a failed migration Job fail the release; a failed deploy prints the
#    migration Job's status and logs first; a first install first deletes a leftover Failed migration Job)
# 4. deploy-verify.sh (for each image the release renders a component for), then scripts/smoke-test.sh
#    through a port-forward
# 5. when verify or smoke fails: helm rollback to the recorded revision, then exit 1
#
# Env (all required unless a default is shown): SERVICE_NAME DEPLOY_ENV (dev|qa|production) CHART_DIR
# DEPLOY_IMAGES (comma list of image ids) DIGESTS (JSON of <ID>_IMAGE_DIGEST) IMAGE_TAG HELM_TIMEOUT
# BACKEND_PORT API_VERSION; FRONTEND_PORT (3000); WORK_DIR (RUNNER_TEMP); SMOKE_WAIT_TRIES (30), SMOKE_WAIT_SLEEP (2).
# MATRIX_CMD (the matrix helper command; deploy.yml points it at a PyYAML venv) and, for tests only,
# VERIFY_SCRIPT and SMOKE_SCRIPT are overridable.
# Test only: DEPLOY_ENV=kind deploys to the local kind cluster fst (tests/k8s/test_deploy_env.py), and
# only with DEPLOY_ENV_KIND_TEST=1 and a kubeconfig whose one context is kind-fst; it is refused
# otherwise, and no workflow sets it. It takes values.local.yaml, then each file of KIND_VALUES
# (a colon list), instead of values.<env>.yaml; the namespace is <SERVICE_NAME>-kind.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/scripts/ci/migration-job-lib.sh"
fail() { echo "::error::$*" >&2; exit 1; }

for v in SERVICE_NAME DEPLOY_ENV CHART_DIR DEPLOY_IMAGES DIGESTS IMAGE_TAG HELM_TIMEOUT BACKEND_PORT API_VERSION; do
  [[ -n "${!v:-}" ]] || fail "$v is required"
done
env_values=(-f "$CHART_DIR/values.${DEPLOY_ENV}.yaml")
case "$DEPLOY_ENV" in dev|qa|production) ;;
  kind)
    [[ "${DEPLOY_ENV_KIND_TEST:-}" == 1 ]] || fail "DEPLOY_ENV=kind is test-only: it needs DEPLOY_ENV_KIND_TEST=1"
    [[ -z "${HELM_KUBEAPISERVER:-}${HELM_KUBETOKEN:-}" && "${HELM_KUBECONTEXT:-kind-fst}" == kind-fst ]] ||
      fail "DEPLOY_ENV=kind refuses a HELM_KUBECONTEXT, HELM_KUBEAPISERVER or HELM_KUBETOKEN other than kind-fst's"
    [[ "$(kubectl config get-contexts -o name 2>/dev/null || true)" == kind-fst &&
      "$(kubectl config current-context 2>/dev/null || true)" == kind-fst ]] ||
      fail "DEPLOY_ENV=kind runs only with a kubeconfig whose one context is kind-fst"
    env_values=(-f "$CHART_DIR/values.local.yaml")
    IFS=: read -ra kind_files <<<"${KIND_VALUES:-}"
    for f in "${kind_files[@]}"; do env_values+=(-f "$f"); done ;;
  *) fail "DEPLOY_ENV must be dev, qa or production" ;;
esac
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
# Helm keeps this many release revisions (Secrets in the namespace). 5 is enough to roll back past a
# few bad deploys, and it bounds the Secrets the namespace quota must allow (infra/k8s/bootstrap,
# quota.hard.secrets; a test checks the two together). deploy-ops.yml's rollback passes the same.
HELM_HISTORY_MAX=5

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
# 1b. Which images this release runs: a render with the deploy's own values files, before anything
# changes. An optional image (the WAF, off unless waf.enabled) renders no Pod when its feature is off,
# so it needs no digest then (an older release's digest file may have none) and its Pods are not
# checked. This discovery render is not a deploy: it runs without guards.rejectPlaceholders, because
# the release digests are not set yet (values.production.yaml holds placeholder digests until then).
# The upgrade takes the same values plus the guard, which refuses the chart's placeholder hosts,
# Gateway, digests and auth settings.
discovery_values=("${env_values[@]}" -f "$VALUES")
release_values=("${discovery_values[@]}" --set guards.rejectPlaceholders=true)
manifest="$(helm template "$SERVICE_NAME" "$CHART_DIR" "${discovery_values[@]}" --namespace "$NAMESPACE")" || fail "cannot render ${SERVICE_NAME} for ${DEPLOY_ENV}"
# An image is used when a rendered container runs its repository, by tag or by digest (a values file
# may already hold one, such as the placeholder), whatever the component label; its component is
# checked when a Pod carries the id as label.
used() {
  local repo images
  repo="$(jq -r --arg id "$1" '.images[$id] | "\(.registry)/\(.repository)"' "$VALUES")"
  images="$(grep -E '^[[:space:]]*(- )?image:' <<<"$manifest" || true)"
  grep -qF -e "image: ${repo}:" -e "image: ${repo}@" -e "image: \"${repo}:" -e "image: \"${repo}@" <<<"$images"
}
# deploy-verify.sh finds the Pods by their app.kubernetes.io/name, the chart's name (the label above
# the release's app.kubernetes.io/instance), which differs from the release name when that only
# contains the chart's.
app_name="$(awk -v rel="$SERVICE_NAME" '/^[[:space:]]*app\.kubernetes\.io\/name:/ { n = $2; next }
  /^[[:space:]]*app\.kubernetes\.io\/instance:/ && n != "" && $2 == rel { print n } { n = "" }' <<<"$manifest" | tr -d '"' | sort -u)"
[[ -n "$app_name" && "$app_name" != *$'\n'* ]] || fail "cannot tell the app.kubernetes.io/name of ${SERVICE_NAME} from its render"
renders() { grep -qE "^[[:space:]]*app\.kubernetes\.io/component: \"?${1}\"?[[:space:]]*$" <<<"$manifest"; }
components=()
for id in ${DEPLOY_IMAGES//,/ }; do
  var="$(digest_var "$id")"
  dg="$(jq -r --arg k "$var" '.[$k] // ""' <<<"$DIGESTS")"
  if [[ -z "$dg" ]] && ! used "$id" && ! renders "$id"; then
    echo "${id}: not rendered by this release (feature off) and no ${var}; deployed without it, not checked"
    continue
  fi
  [[ "$dg" =~ ^sha256:[0-9a-f]{64}$ ]] || fail "$var must be sha256:<64 hex>; refusing to deploy $id by tag"
  jq --arg id "$id" --arg dg "$dg" '.images[$id].digest = $dg' "$VALUES" > "$VALUES.tmp" && mv "$VALUES.tmp" "$VALUES"
  export "$var=$dg"
  if renders "$id"; then
    components+=("$id")
  else
    echo "${id}: not rendered by this release (feature off); its digest is set, its Pods are not checked"
  fi
  echo "- \`$id\`: $dg" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
done

# 2. the revision to return to
history="$(helm history "$SERVICE_NAME" -n "$NAMESPACE" -o json 2>/dev/null || echo '[]')"
recorded="$(jq -r '[.[] | select(.status == "deployed")] | last | .revision // empty' <<<"$history")"
echo "last deployed revision before this deploy: ${recorded:-none (first install)}"

# 2b. A failed FIRST install is uninstalled and the retry is revision 1 again, with the same Job name;
# Helm would adopt the kept, Failed Job and fail at once. Remove this release's Failed migration Jobs.
if [[ -z "$recorded" ]]; then
  migration_job_purge_failed "$NAMESPACE" "$SERVICE_NAME" || echo "warning: could not clear old migration Jobs" >&2
fi

# 3. deploy. The migration Job carries helm.sh/resource-policy keep, so Helm's own rollback leaves
# a failed Job (and its Pods) in place: print its status and logs before failing.
helm_rc=0
helm upgrade --install "$SERVICE_NAME" "$CHART_DIR" "${release_values[@]}" \
  --namespace "$NAMESPACE" \
  --rollback-on-failure --wait --wait-for-jobs --timeout "$HELM_TIMEOUT" \
  --history-max "$HELM_HISTORY_MAX" || helm_rc=$?
if (( helm_rc != 0 )); then
  echo "::group::migration Job of ${SERVICE_NAME} in ${DEPLOY_ENV} (the deploy failed)"
  migration_job_report_newest "$NAMESPACE" "$SERVICE_NAME" || true
  echo "::endgroup::"
  exit "$helm_rc"
fi
helm history "$SERVICE_NAME" -n "$NAMESPACE" --max 5

# 4. verify and smoke
checks() {
  RELEASE="$SERVICE_NAME" NAMESPACE="$NAMESPACE" COMPONENTS="${components[*]}" bash "$VERIFY_SCRIPT" --name "$app_name" || return 1
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
  helm rollback "$SERVICE_NAME" "$recorded" -n "$NAMESPACE" --wait --timeout "$HELM_TIMEOUT" \
    --history-max "$HELM_HISTORY_MAX"
  echo "- Rolled $SERVICE_NAME back to revision $recorded in ${DEPLOY_ENV} after a failed verify or smoke" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
  fail "verify or smoke failed; rolled ${SERVICE_NAME} back to revision ${recorded} in ${DEPLOY_ENV}"
fi
echo "Deployed, verified and smoke tested: ${SERVICE_NAME} in ${DEPLOY_ENV}"
