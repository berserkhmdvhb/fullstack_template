#!/usr/bin/env bash
# deploy-verify.sh — read-only post-deploy sweep of one Helm release (kubectl only).
#
# Checks (the first failure exits 1):
#   1. the release is `deployed` (its newest Helm release Secret has status=deployed)
#   2. the newest migration Job (one per release revision), if any, completed
#   3. every pod of the service is Running (or Succeeded)
#   4. every pod of each component runs the expected image DIGEST (the pod's imageID)
#   5. the backend and frontend health routes answer (kubectl exec, no ingress needed)
# Pods being deleted (deletionTimestamp set) are skipped in checks 3 to 5: after `helm --wait` the old
# ReplicaSet's pods can still be draining with the old image.
#
# Inputs, flags win over env (a missing or malformed one exits 2):
#   --release R        RELEASE        Helm release name
#   --namespace N      NAMESPACE      app namespace of the release
#   --name L           SERVICE_NAME   value of the app.kubernetes.io/name label (default: RELEASE)
#   --components "a b" COMPONENTS     components to check (default: "backend frontend nginx")
#   <COMPONENT>_IMAGE_DIGEST          expected sha256:<64 hex> per component, env only; the name is the
#                                     component uppercased with non-alphanumerics as _ (web-ui: WEB_UI_...)
#   BACKEND_PORT (8000), FRONTEND_PORT (3000), API_VERSION (v1)
set -Eeuo pipefail

die_usage() { echo "deploy-verify: $*" >&2; exit 2; }
fail() { echo "ERROR: $*" >&2; exit 1; }

RELEASE="${RELEASE:-}" NAMESPACE="${NAMESPACE:-}" SERVICE_NAME="${SERVICE_NAME:-}"
COMPONENTS="${COMPONENTS:-backend frontend nginx}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --release) RELEASE="${2:?--release needs a value}"; shift 2 ;;
    --namespace) NAMESPACE="${2:?--namespace needs a value}"; shift 2 ;;
    --name) SERVICE_NAME="${2:?--name needs a value}"; shift 2 ;;
    --components) COMPONENTS="${2:?--components needs a value}"; shift 2 ;;
    *) die_usage "unknown argument: $1" ;;
  esac
done
[[ -n "$RELEASE" ]] || die_usage "RELEASE (or --release) is required"
[[ -n "$NAMESPACE" ]] || die_usage "NAMESPACE (or --namespace) is required"
SERVICE_NAME="${SERVICE_NAME:-$RELEASE}"

declare -A EXPECT=()
for c in $COMPONENTS; do
  var="${c^^}"; var="${var//[^A-Z0-9_]/_}_IMAGE_DIGEST"
  [[ -n "${!var:-}" ]] || die_usage "$var is required"
  [[ "${!var}" =~ ^sha256:[0-9a-f]{64}$ ]] || die_usage "$var must be sha256:<64 hex>"
  EXPECT[$c]="${!var}"
done

K=(kubectl -n "$NAMESPACE")
echo "Verifying release $RELEASE in $NAMESPACE"

echo "== Check 1: Helm release status =="
found="$("${K[@]}" get secret -l "owner=helm,name=${RELEASE},status=deployed" -o name)"
[[ -n "$found" ]] || fail "release $RELEASE is not deployed"
echo "release deployed"

echo "== Check 2: Migration Job =="
# One Job per release revision stays until its TTL; only the newest is this deploy's.
migs="$("${K[@]}" get job -l app.kubernetes.io/component=migration --sort-by=.metadata.creationTimestamp \
  -o jsonpath='{range .items[*]}{.status.succeeded}{"|"}{.status.failed}{"|"}{.status.conditions[?(@.type=="Failed")].status}{"\n"}{end}' | tail -n 1)"
if [[ -z "$migs" ]]; then
  echo "no migration job (cleaned up after its TTL): ok"
else
  while IFS='|' read -r ok failed cond; do
    [[ "$ok" == "1" ]] && continue
    if [[ "$cond" == "True" || "${failed:-0}" != "0" ]]; then fail "migration job failed"; fi
    fail "migration job did not complete"
  done <<<"$migs"
  echo "migration job completed"
fi

echo "== Check 3: Pod status =="
pods="$("${K[@]}" get pods -l "app.kubernetes.io/name=${SERVICE_NAME}" \
  -o jsonpath='{range .items[*]}{.metadata.name}{"|"}{.status.phase}{"|"}{.metadata.deletionTimestamp}{"\n"}{end}')"
live=0
while IFS='|' read -r name phase deleting; do
  [[ -n "$name" && -z "$deleting" ]] || continue
  live=$((live + 1))
  echo "$name $phase"
  [[ "$phase" == "Running" || "$phase" == "Succeeded" ]] || fail "not all pods are Running ($name is $phase)"
done <<<"$pods"
(( live > 0 )) || fail "not all pods are Running (none found)"
echo "all pods Running"

echo "== Check 4: Image digests =="
for c in $COMPONENTS; do
  lines="$("${K[@]}" get pods -l "app.kubernetes.io/component=${c}" \
    -o jsonpath='{range .items[*]}{.metadata.name}{"|"}{.metadata.deletionTimestamp}{"|"}{range .status.containerStatuses[*]}{.imageID}{" "}{end}{"\n"}{end}')"
  seen=0
  while IFS='|' read -r pod deleting ids; do
    [[ -n "$pod" && -z "$deleting" ]] || continue
    seen=$((seen + 1))
    [[ "$ids" == *"@${EXPECT[$c]} "* ]] || fail "$c pod $pod digest mismatch (expected ${EXPECT[$c]}, running ${ids% })"
    echo "  $c $pod: ${EXPECT[$c]}"
  done <<<"$lines"
  (( seen > 0 )) || fail "no $c pods found"
done
echo "all running images match the expected digests"

echo "== Check 5: Health endpoints =="
health() {
  local comp="$1" url="$2" pod
  pod="$("${K[@]}" get pods -l "app.kubernetes.io/component=${comp}" \
    -o jsonpath='{range .items[*]}{.metadata.name}{"|"}{.metadata.deletionTimestamp}{"\n"}{end}' \
    | awk -F'|' '$1 != "" && $2 == "" {print $1; exit}')"
  [[ -n "$pod" ]] || fail "no $comp pod to check"
  "${K[@]}" exec "$pod" -- curl -fsS "$url" >/dev/null || fail "$comp health failed"
  echo "$comp health: OK"
}
health backend "http://localhost:${BACKEND_PORT:-8000}/api/${API_VERSION:-v1}/health"
health frontend "http://localhost:${FRONTEND_PORT:-3000}/"

echo "== Verification complete: all checks passed =="
