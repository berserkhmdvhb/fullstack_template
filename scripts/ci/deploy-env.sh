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
# Env (all required unless a default is shown): SERVICE_NAME DEPLOY_ENV (dev|qa|production|preview) CHART_DIR
# DEPLOY_IMAGES (comma list of image ids) DIGESTS (JSON of <ID>_IMAGE_DIGEST) IMAGE_TAG HELM_TIMEOUT
# BACKEND_PORT API_VERSION; FRONTEND_PORT (3000); WORK_DIR (RUNNER_TEMP); SMOKE_WAIT_TRIES (30), SMOKE_WAIT_SLEEP (2).
# MATRIX_CMD (the matrix helper command; deploy.yml points it at a PyYAML venv) and, for tests only,
# VERIFY_SCRIPT and SMOKE_SCRIPT are overridable.
# Test only: DEPLOY_ENV=kind deploys to the local kind cluster fst (tests/k8s/test_deploy_env.py), and
# only with DEPLOY_ENV_KIND_TEST=1 and a kubeconfig whose one context is kind-fst; it is refused
# otherwise, and no workflow sets it. It takes values.local.yaml, then each file of KIND_VALUES
# (a colon list), instead of values.<env>.yaml; the namespace is <SERVICE_NAME>-kind.
# Pull request previews (.github/workflows/preview.yml): DEPLOY_ENV=preview with PREVIEW_PR=<N> deploys
# the release <SERVICE_NAME>-pr-<N> into the one preview namespace <SERVICE_NAME>-preview, with
# values.preview.yaml and per-release hosts: <release>.<PREVIEW_DOMAIN>, <release>-api.<..> and
# <release>-app.<..>, or the never-resolving domain `invalid` when PREVIEW_DOMAIN is unset. With
# PREVIEW_DOMAIN the smoke test also runs through the public URL https://<release>.<PREVIEW_DOMAIN>,
# with the Cloudflare Access service token (CF_ACCESS_CLIENT_ID, CF_ACCESS_CLIENT_SECRET, both required
# then) as headers from a file, never on a command line; the URL goes to GITHUB_OUTPUT as `url`.
# PREVIEW_PR is refused with dev, qa and production; the test-only kind seam takes it too (the same
# release and namespace names, the kind values instead of values.preview.yaml).
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/scripts/ci/migration-job-lib.sh"
fail() { echo "::error::$*" >&2; exit 1; }

for v in SERVICE_NAME DEPLOY_ENV CHART_DIR DEPLOY_IMAGES DIGESTS IMAGE_TAG HELM_TIMEOUT BACKEND_PORT API_VERSION; do
  [[ -n "${!v:-}" ]] || fail "$v is required"
done
env_values=(-f "$CHART_DIR/values.${DEPLOY_ENV}.yaml")
PREVIEW_PR="${PREVIEW_PR:-}"
case "$DEPLOY_ENV" in
  dev|qa|production) [[ -z "$PREVIEW_PR" ]] || fail "PREVIEW_PR is for DEPLOY_ENV=preview only" ;;
  preview) [[ -n "$PREVIEW_PR" ]] || fail "DEPLOY_ENV=preview needs PREVIEW_PR (the pull request number)" ;;
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
  *) fail "DEPLOY_ENV must be dev, qa, production or preview" ;;
esac
WORK_DIR="${WORK_DIR:-${RUNNER_TEMP:-}}"
[[ -n "$WORK_DIR" ]] || fail "WORK_DIR or RUNNER_TEMP is required"
MATRIX_CMD="${MATRIX_CMD:-python3 $ROOT/.github/scripts/container_matrix.py}"
VERIFY_SCRIPT="${VERIFY_SCRIPT:-$ROOT/scripts/ci/deploy-verify.sh}"
SMOKE_SCRIPT="${SMOKE_SCRIPT:-$ROOT/scripts/smoke-test.sh}"
RELEASE="$SERVICE_NAME" NAMESPACE="${SERVICE_NAME}-${DEPLOY_ENV}" PUBLIC_URL=""
if [[ -n "$PREVIEW_PR" ]]; then
  [[ "$PREVIEW_PR" =~ ^[1-9][0-9]{0,9}$ ]] || fail "PREVIEW_PR must be a pull request number"
  RELEASE="${SERVICE_NAME}-pr-${PREVIEW_PR}" NAMESPACE="${SERVICE_NAME}-preview"
  domain="${PREVIEW_DOMAIN:-invalid}"
  [[ "$domain" =~ ^([a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?\.)*[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$ ]] ||
    fail "PREVIEW_DOMAIN must be a lower-case DNS name"
  jq -n --arg r "$RELEASE" --arg d "$domain" \
    '{edge: {hosts: {root: "\($r).\($d)", api: "\($r)-api.\($d)", app: "\($r)-app.\($d)"}}}' > "$WORK_DIR/preview-hosts.json"
  env_values+=(-f "$WORK_DIR/preview-hosts.json")
  if [[ -n "${PREVIEW_DOMAIN:-}" ]]; then
    [[ -n "${CF_ACCESS_CLIENT_ID:-}" && -n "${CF_ACCESS_CLIENT_SECRET:-}" ]] ||
      fail "PREVIEW_DOMAIN needs CF_ACCESS_CLIENT_ID and CF_ACCESS_CLIENT_SECRET (the Access service token)"
    PUBLIC_URL="https://${RELEASE}.${domain}"
  fi
fi
# The backend Service. The port-forwarded checks send its name as Host, as the frontend does
# in-cluster: the backend refuses a Host that is not in the ALLOWED_HOSTS the chart renders.
BACKEND_SERVICE="${RELEASE}-backend"
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
manifest="$(helm template "$RELEASE" "$CHART_DIR" "${discovery_values[@]}" --namespace "$NAMESPACE")" || fail "cannot render ${RELEASE} for ${DEPLOY_ENV}"
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
app_name="$(awk -v rel="$RELEASE" '/^[[:space:]]*app\.kubernetes\.io\/name:/ { n = $2; next }
  /^[[:space:]]*app\.kubernetes\.io\/instance:/ && n != "" && $2 == rel { print n } { n = "" }' <<<"$manifest" | tr -d '"' | sort -u)"
[[ -n "$app_name" && "$app_name" != *$'\n'* ]] || fail "cannot tell the app.kubernetes.io/name of ${RELEASE} from its render"
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
history="$(helm history "$RELEASE" -n "$NAMESPACE" -o json 2>/dev/null || echo '[]')"
recorded="$(jq -r '[.[] | select(.status == "deployed")] | last | .revision // empty' <<<"$history")"
echo "last deployed revision before this deploy: ${recorded:-none (first install)}"

# 2b. A failed FIRST install is uninstalled and the retry is revision 1 again, with the same Job name;
# Helm would adopt the kept, Failed Job and fail at once. Remove this release's Failed migration Jobs.
if [[ -z "$recorded" ]]; then
  migration_job_purge_failed "$NAMESPACE" "$RELEASE" || echo "warning: could not clear old migration Jobs" >&2
fi

# 3. deploy. The migration Job carries helm.sh/resource-policy keep, so Helm's own rollback leaves
# a failed Job (and its Pods) in place: print its status and logs before failing.
helm_rc=0
helm upgrade --install "$RELEASE" "$CHART_DIR" "${release_values[@]}" \
  --namespace "$NAMESPACE" \
  --rollback-on-failure --wait --wait-for-jobs --timeout "$HELM_TIMEOUT" \
  --history-max "$HELM_HISTORY_MAX" || helm_rc=$?
if (( helm_rc != 0 )); then
  echo "::group::migration Job of ${RELEASE} in ${DEPLOY_ENV} (the deploy failed)"
  migration_job_report_newest "$NAMESPACE" "$RELEASE" || true
  echo "::endgroup::"
  exit "$helm_rc"
fi
helm history "$RELEASE" -n "$NAMESPACE" --max 5
# A preview's public URL, for the deployment record (preview.yml's environment url).
[[ -z "$PUBLIC_URL" ]] || echo "url=${PUBLIC_URL}" >> "${GITHUB_OUTPUT:-/dev/null}"

# 4. verify and smoke
checks() {
  RELEASE="$RELEASE" NAMESPACE="$NAMESPACE" COMPONENTS="${components[*]}" bash "$VERIFY_SCRIPT" --name "$app_name" || return 1
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
  (( rc == 0 )) || return 1
  [[ -z "$PUBLIC_URL" ]] || public_smoke
}

# A preview with PREVIEW_DOMAIN: the smoke test again, through Cloudflare (the tunnel, Access and the
# Gateway) to the release's own hostname. The Access service token goes in a header file that only
# this user can read, removed on return; it is never on a command line or in the output.
public_smoke() {
  local hdr rc=1
  hdr="$(umask 077 && mktemp "$WORK_DIR/access-headers.XXXXXX")"
  printf 'CF-Access-Client-Id: %s\nCF-Access-Client-Secret: %s\n' "$CF_ACCESS_CLIENT_ID" "$CF_ACCESS_CLIENT_SECRET" > "$hdr"
  for _ in $(seq 1 "${SMOKE_WAIT_TRIES:-30}"); do
    curl -fsS --max-time 5 -H "@${hdr}" "${PUBLIC_URL}/api/${API_VERSION}/health" >/dev/null 2>&1 && break
    sleep "${SMOKE_WAIT_SLEEP:-2}"
  done
  bash "$SMOKE_SCRIPT" --base-url "$PUBLIC_URL" --header-file "$hdr" --path "/api/${API_VERSION}" \
    --format junit --out "$WORK_DIR/smoke-${DEPLOY_ENV}-public.xml" && rc=0
  rm -f "$hdr"
  return "$rc"
}

# 5. roll back to the revision recorded in step 2, only when a check failed
if ! checks; then
  if [[ -z "$recorded" ]]; then
    fail "verify or smoke failed in ${DEPLOY_ENV}, and there is no earlier deployed revision to roll back to"
  fi
  helm rollback "$RELEASE" "$recorded" -n "$NAMESPACE" --wait --timeout "$HELM_TIMEOUT" \
    --history-max "$HELM_HISTORY_MAX"
  echo "- Rolled $RELEASE back to revision $recorded in ${DEPLOY_ENV} after a failed verify or smoke" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
  fail "verify or smoke failed; rolled ${RELEASE} back to revision ${recorded} in ${DEPLOY_ENV}"
fi
echo "Deployed, verified and smoke tested: ${RELEASE} in ${DEPLOY_ENV}"
