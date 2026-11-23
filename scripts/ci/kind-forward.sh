#!/usr/bin/env bash
# kind-forward.sh — run one command against the release `make kind-deploy` leaves in fst, through
# port-forwards, the way an operator reaches it.
#
# As the bootstrap's deployer (its deploy-ops rights include port-forward; kind-deploy.sh step 6 checks
# them), `kubectl port-forward` to the backend and the frontend Services, each on a loopback port kubectl
# picks at run time. It waits until each answers its health route, exports the URLs below, runs the
# command and stops the forwards on every exit path (EXIT trap; INT and TERM stop the command too).
#
# Public on kind is the frontend Service. kind has no Gateway controller; the frontend is the edge the
# chart renders there: it serves the pages and proxies /api, /docs and /openapi.json to BACKEND_URL,
# the WAF when it is on (kind-deploy.sh leaves it on). So the public forward goes frontend, WAF, backend.
#
# Exports: API_VERSION (the release's ConfigMap), BACKEND_HOST/BACKEND_PORT (the backend forward),
#   PUBLIC_HOST/PUBLIC_PORT/PUBLIC_URL and FRONTEND_HOST/FRONTEND_PORT (the frontend forward),
#   SMOKE_BASE_URL and DAST_TARGET_URL (= PUBLIC_URL), NEXT_DEPLOYMENT_ID (derived, as Dockerfile.frontend
#   does, from the version and revision labels of the image the frontend Pod runs, read on its kind node)
#   and LIVE_TARGET=deployed (the live suites skip what needs a local build).
# Logs: each run writes one log per forward, <name>-<pid>.log, to $RUNNER_TEMP/kind-forward when RUNNER_TEMP
#   is set (the kind job's failure dump prints every run's), else to a temporary directory; on a failure
#   this run's are printed here too.
# Root on the node: the deployment id is read with `docker exec` and `crictl inspecti` on the kind node,
#   outside the deployer's identity; it is read-only, kind nodes only (fst-*), and reads the image that runs.
# Env: KIND_FORWARD_TIMEOUT (seconds a forward has to start and answer, default 60).
#
# Usage: kind-forward.sh [--] COMMAND [ARG...]
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/scripts/_kind.sh"
RELEASE=your-service-name
NS="$RELEASE-local"
GROUP=system:authenticated
TIMEOUT="${KIND_FORWARD_TIMEOUT:-60}"

die() { echo "kind-forward: $*" >&2; exit "${2:-1}"; }
[[ "${1:-}" != -- ]] || shift
[[ $# -gt 0 ]] || die "usage: kind-forward.sh [--] COMMAND [ARG...]" 2
[[ "$TIMEOUT" =~ ^[1-9][0-9]*$ ]] || die "KIND_FORWARD_TIMEOUT must be a positive number of seconds" 2

# ---- fst's kubeconfig only, its one context, the pinned kubectl for the server's minor --------
[[ -f "$KIND_DIR/kubeconfig" ]] || die "no $KIND_DIR/kubeconfig: run make kind-up and make kind-deploy first"
export KUBECONFIG="$KIND_DIR/kubeconfig"
[[ "$(kubectl config get-contexts -o name)" == "$CONTEXT" ]] || die "refusing: $KUBECONFIG holds other contexts than $CONTEXT"
server="$(kubectl version -o json 2>/dev/null | jq -r '.serverVersion.minor' | tr -cd '0-9')"
pin="$(kind_kubectl_pin "$server")"
[[ -n "$pin" ]] || die "no pinned kubectl for a 1.$server server (tools/kind-nodes.tsv)"
# shellcheck disable=SC2016 # yq's own variables, not the shell's
deployer="$(yq ea '. as $i ireduce ({}; . * $i) | .deployer.name' "$ROOT/infra/k8s/bootstrap/values.yaml" \
  "$ROOT/infra/k8s/bootstrap/values.local.yaml")"
as_deployer=(--context "$CONTEXT" -n "$NS" --as "$deployer" --as-group "$GROUP")
kc() { "$pin" "${as_deployer[@]}" "$@"; }

if [[ -n "${RUNNER_TEMP:-}" ]]; then LOGS="$RUNNER_TEMP/kind-forward"; mkdir -p "$LOGS"; else LOGS="$(mktemp -d)"; fi
pids=()
cmd_pid=""
rc=0
# shellcheck disable=SC2329 # invoked by the EXIT trap below
cleanup() {
  rc=$?
  trap - EXIT INT TERM
  [[ -z "$cmd_pid" ]] || kill "$cmd_pid" 2>/dev/null || true
  local pid
  for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done
  for pid in "${pids[@]}" ${cmd_pid:+"$cmd_pid"}; do wait "$pid" 2>/dev/null || true; done
  if (( rc != 0 )); then
    for log in "$LOGS"/*-"$$".log; do [[ ! -f "$log" ]] || { echo "--- $log" >&2; cat "$log" >&2; }; done
  fi
  [[ -n "${RUNNER_TEMP:-}" ]] || rm -rf "$LOGS"
  exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

forward() { # name, health path: starts the forward and sets the port variable <name>_port
  local name="$1" path="$2" svc_port log port="" end=$((SECONDS + TIMEOUT))
  svc_port="$(kc get "service/$RELEASE-$name" -o jsonpath='{.spec.ports[?(@.name=="http")].port}')"
  [[ -n "$svc_port" ]] || die "the $RELEASE-$name Service has no http port"
  log="$LOGS/$name-$$.log"
  echo "== $(date -u +%FT%TZ) $name: $*" >> "$log"
  # The binary itself in the background, not kc: a backgrounded function is a subshell, and killing
  # that would leave kubectl running.
  "$pin" "${as_deployer[@]}" port-forward --address 127.0.0.1 "service/$RELEASE-$name" ":$svc_port" >> "$log" 2>&1 &
  pids+=($!)
  until [[ -n "$port" ]]; do
    port="$(sed -n 's/^Forwarding from 127\.0\.0\.1:\([0-9]*\) .*/\1/p' "$log" | tail -n 1)"
    [[ -n "$port" ]] && break
    kill -0 "${pids[-1]}" 2>/dev/null || die "the $name port-forward exited: $(tail -n 5 "$log")"
    (( SECONDS < end )) || die "the $name port-forward did not start in ${TIMEOUT} s"
    sleep 0.2
  done
  until curl -fsS -o /dev/null --max-time 5 "http://127.0.0.1:$port$path" 2>>"$log"; do
    (( SECONDS < end )) || die "$name did not answer http://127.0.0.1:$port$path in ${TIMEOUT} s"
    sleep 0.5
  done
  printf -v "${name}_port" '%s' "$port"
}

API_VERSION="$(kc get "configmap/$RELEASE-config" -o jsonpath='{.data.API_VERSION}')"
[[ -n "$API_VERSION" ]] || die "the $RELEASE-config ConfigMap has no API_VERSION"
backend_port="" frontend_port=""
forward backend "/api/$API_VERSION/health"
forward frontend /api/healthz

# The deployment id of the running frontend image: its labels, on the kind node the Pod runs on.
image="$(kc get "deployment/$RELEASE-frontend" -o jsonpath='{.spec.template.spec.containers[?(@.name=="frontend")].image}')"
node="$(kc get pods -l app.kubernetes.io/component=frontend -o jsonpath='{.items[0].spec.nodeName}')"
[[ "$node" == "$CLUSTER"-* ]] || die "the frontend Pod's node '$node' is not a node of $CLUSTER"
labels="$(docker exec "$node" crictl inspecti -o json "$image" | jq -c '.info.imageSpec.config.Labels // {}')"
version="$(jq -r '."org.opencontainers.image.version" // ""' <<<"$labels")"
revision="$(jq -r '."org.opencontainers.image.revision" // ""' <<<"$labels")"
[[ -n "$version$revision" ]] || die "the image $image has no version or revision label"
NEXT_DEPLOYMENT_ID="$(node "$ROOT/frontend/scripts/deployment-id.mjs" "$version" "$revision")"

export API_VERSION NEXT_DEPLOYMENT_ID LIVE_TARGET=deployed
export BACKEND_HOST=127.0.0.1 BACKEND_PORT="$backend_port"
export PUBLIC_HOST=127.0.0.1 PUBLIC_PORT="$frontend_port" FRONTEND_HOST=127.0.0.1 FRONTEND_PORT="$frontend_port"
export PUBLIC_URL="http://127.0.0.1:$frontend_port"
export SMOKE_BASE_URL="$PUBLIC_URL" DAST_TARGET_URL="$PUBLIC_URL"
echo "kind-forward: backend http://127.0.0.1:$backend_port, public $PUBLIC_URL, deployment id $NEXT_DEPLOYMENT_ID"
"$@" &
cmd_pid=$!
wait "$cmd_pid" || rc=$?
cmd_pid=""
exit "$rc"
