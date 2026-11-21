#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# =============================================
# Rebuild & Roll — config-driven rebuild + rollout
# =============================================
# Purpose
#   For every component listed in scripts/services.yaml, run the same four-step
#   loop that is otherwise done by hand:
#     1. build the image in the minikube node daemon
#     2. verify the fix is baked into the image (optional per-image check)
#     3. kubectl rollout restart the workload
#     4. wait for the rollout, then health-check a fresh pod from inside
#   One command does all three services (backend, frontend, nginx); a failure on
#   one service is recorded and reported, not fatal to the others.
#
# Key behaviors
#   - Services come from scripts/services.yaml — add a row to add a service.
#   - Builds in the node daemon via `eval $(minikube docker-env)` (disable with
#     --no-docker-env for a registry-based flow).
#   - Per-service health probe tool is configurable (curl / wget / node / python) because
#     curl is not present in every base image.
#   - Sequential by default: a controlled blast radius — we never restart every
#     workload at once. --dry-run prints the commands without running them.
#
# Inputs (flags)
#   -c, --config FILE        services.yaml path   (default: alongside this script)
#   -e, --env ENV            local|dev|qa|production — resolve namespace + image tag from
#                           the chart's values.<env>.yaml; overrides the YAML
#                           namespace and each image's tag. services.yaml stays
#                           pure structure — no env-specific values in it.
#   -n, --namespace NS       k8s namespace         (default: from the YAML)
#   -T, --tag TAG            override the image tag of every service
#       --only svc1,svc2     restrict to these service keys
#       --no-build           skip image builds (roll + health only)
#       --no-verify          skip the per-image verification step
#       --verify-warn        verification failure warns instead of aborting
#       --no-roll            skip the rollout (build + verify + health)
#       --no-health          skip the in-pod health check
#       --no-docker-env      do not eval `minikube docker-env`
#       --dry-run            print each command, execute nothing
#   -h, --help
#
# Exit status
#   0 if every targeted service completed its loop, 1 if any step failed.
# =============================================
# If not running under bash (e.g. /bin/sh), re-exec with bash.
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -euo pipefail
set -o errtrace

# Shared runtime: logging, banners, dry-run runners, safe env loader, …
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
trap 'lib_err_trap "rebuild-and-roll" "${LINENO}" "${BASH_COMMAND}" "$?"' ERR
trap 'echo; log_err "interrupted — aborting"; exit 130' INT TERM

# ---- Logging shim — thin wrappers over _lib.sh so the loop body reads cleanly -
# (die() and need_cmd() are provided by _lib.sh.)
sep()   { lib_sep "$*"; }
step()  { log_step "$*"; }
info()  { log_info "$*"; }
ok()    { log_ok "$*"; }
warn()  { log_warn "$*"; }
err()   { log_err "$*"; }

# Command runners that honour --dry-run -----------------------------------------
run_cmd() {                       # simple argv command
  if [ "${DRY_RUN}" = "true" ]; then printf '    ↪  %s\n' "$*"; return 0; fi
  "$@"
}
run_shell() {                     # shell snippet run on the HOST (kubectl exec probes)
  if [ "${DRY_RUN}" = "true" ]; then printf '    ↪  %s\n' "$*"; return 0; fi
  bash -c "$*"
}

# run_in_image — run a verify snippet INSIDE the just-built image (throwaway
# container, entrypoint overridden), so it inspects the image's own filesystem /
# Python, not the host's. This is what services.yaml documents and what the
# by-hand step does; `run_shell` here had it running on the host, so every verify
# checked the laptop and failed. `docker run` honours `minikube docker-env`, so
# it sees the image built into the node daemon in the normal flow.
#
# The interpreter defaults to sh but is overridable per service (services.yaml
# `verify_entrypoint`): a shell-less runtime image (e.g. the frontend's distroless
# base) has no /bin/sh to exec, so its snippet runs via the image's own entrypoint
# instead of `-c` (`docker run --entrypoint <ep> <image> -e '<snippet>'`).
run_in_image() {                  # $1 image  $2 snippet  $3 entrypoint (default sh)
  local image="$1" snippet="$2" entrypoint="${3:-sh}"
  if [ "${DRY_RUN}" = "true" ]; then
    if [ "${entrypoint}" = "sh" ]; then
      printf '    ↪  docker run --rm --entrypoint sh %s -c %s\n' "${image}" "${snippet}"
    else
      printf '    ↪  docker run --rm --entrypoint %s %s -e %s\n' "${entrypoint}" "${image}" "${snippet}"
    fi
    return 0
  fi
  if [ "${entrypoint}" = "sh" ]; then
    docker run --rm --entrypoint sh "${image}" -c "${snippet}"
  else
    docker run --rm --entrypoint "${entrypoint}" "${image}" -e "${snippet}"
  fi
}

# ---- Defaults ---------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG="${SCRIPT_DIR}/services.yaml"
ENV_NAME=""           # --env: local|dev|qa|production
NAMESPACE=""          # empty → use the YAML default (or, with --env, the values file)
TAG_OVERRIDE=""       # empty → keep each image's tag (or, with --env, the values file's)
ONLY=""
DO_BUILD="true"; DO_VERIFY="true"; DO_ROLL="true"; DO_HEALTH="true"
DOCKER_ENV="true"; DRY_RUN="false"
VERIFY_WARN="false"
TIMEOUT="${ROLLOUT_TIMEOUT:-240}"
HEALTH_RETRIES="${HEALTH_RETRIES:-6}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-3}"

PASSED=(); FAILED=()

# ---- Usage -------------------------------------------------------------------
print_usage() {
  cat <<'EOF'
Usage: rebuild-and-roll.sh [options]
  -c, --config FILE     services.yaml path (default: alongside this script)
  -e, --env ENV         local|dev|qa|production — namespace + image tag from values.<env>.yaml
  -n, --namespace NS    k8s namespace (default: from the YAML)
  -T, --tag TAG         override the image tag of every service
      --only svc1,svc2  restrict to these service keys (e.g. backend,nginx)
      --no-build        skip image builds (roll + health only)
      --no-verify       skip the per-image verification step
      --verify-warn     verification failure warns instead of aborting
      --no-roll         skip the rollout (build + verify + health)
      --no-health       skip the in-pod health check
      --no-docker-env   do not eval `minikube docker-env` (registry flow)
      --dry-run         print each command, execute nothing
  -h, --help            show this help
Examples:
  ./scripts/rebuild-and-roll.sh                     # build+verify+roll+health, all services
  ./scripts/rebuild-and-roll.sh --env qa            # namespace + tag taken from values.qa.yaml
  ./scripts/rebuild-and-roll.sh --env local         # laptop cluster: bare :local tags, images off the node daemon
  ./scripts/rebuild-and-roll.sh --only backend,nginx
  ./scripts/rebuild-and-roll.sh --no-health --tag "$(date +%Y%m%d)"
  ./scripts/rebuild-and-roll.sh --dry-run           # see what it would do
EOF
}

# ---- Flags -------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    -c|--config)      CONFIG="$2"; shift 2 ;;
    -e|--env)         ENV_NAME="$2"; shift 2 ;;
    -n|--namespace)   NAMESPACE="$2"; shift 2 ;;
    -T|--tag)         TAG_OVERRIDE="$2"; shift 2 ;;
    --only)           ONLY="$2"; shift 2 ;;
    --no-build)       DO_BUILD="false"; shift 1 ;;
    --no-verify)      DO_VERIFY="false"; shift 1 ;;
    --verify-warn)    VERIFY_WARN="true"; shift 1 ;;
    --no-roll)        DO_ROLL="false"; shift 1 ;;
    --no-health)      DO_HEALTH="false"; shift 1 ;;
    --no-docker-env)  DOCKER_ENV="false"; shift 1 ;;
    --dry-run)        DRY_RUN="true"; shift 1 ;;
    -h|--help)        print_usage; exit 0 ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
done

# ---- Pre-flight -------------------------------------------------------------
need_cmd kubectl
need_cmd docker
[ -f "${CONFIG}" ] || die "config not found: ${CONFIG}"
[ -d "${REPO_ROOT}" ] || die "cannot resolve repo root from ${SCRIPT_DIR}"

# Build a set for --only filtering
declare -A ONLY_SET=()
if [ -n "${ONLY}" ]; then
  IFS=',' read -ra _only_arr <<< "${ONLY}"
  for s in "${_only_arr[@]}"; do ONLY_SET["${s// /}"]="1"; done
fi

# ---- Load + normalise services from YAML (python3 + PyYAML) --------------------
# Emits one TAB-separated record per service; the verify/probe fields contain no
# tabs, so a plain `IFS=tab read` round-trips them intact.
mapfile -t SERVICE_RECORDS < <(python3 - "${CONFIG}" <<'PY'
import sys, yaml
with open(sys.argv[1]) as fh:
    data = yaml.safe_load(fh) or {}
for s in (data.get("services") or []):
    rec = "\t".join(str(s.get(k, "")) for k in (
        "key", "label", "dockerfile", "image", "workload", "container",
        "port", "health_path", "probe_tool", "verify", "verify_entrypoint",
    ))
    print(rec)
PY
)
[ "${#SERVICE_RECORDS[@]}" -gt 0 ] || die "no services defined in ${CONFIG}"

# Point the build context at the repo root (Dockerfiles are relative to it).
cd "${REPO_ROOT}"

# Build in the node daemon so the kubelet picks the new image up directly
# (`minikube image load` can leave a stale imageID). Skipped under
# --dry-run (nothing is executed) and --no-docker-env (registry-based flow).
if [ "${DOCKER_ENV}" = "true" ] && [ "${DRY_RUN}" != "true" ] && command -v minikube >/dev/null 2>&1; then
  eval "$(minikube docker-env)"
  info "building inside the minikube node daemon (eval \$(minikube docker-env))"
fi

# ---- Health probe builder ----------------------------------------------------
build_probe() {                       # $1 tool  $2 port  $3 path  → echo snippet
  local tool="$1" port="$2" path="$3"
  local url="http://127.0.0.1:${port}${path}"
  case "${tool}" in
    wget) echo "wget -q -T 5 -O - '${url}'" ;;
    # distroless runtime has no PATH entry for node — use the absolute path the
    # Dockerfile HEALTHCHECK relies on (Dockerfile.frontend line 94).
    node) echo "/nodejs/bin/node -e \"fetch('${url}').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))\"" ;;
    curl) echo "curl -fsS --max-time 5 '${url}'" ;;
    # the backend image has no curl and starts no shell: its own interpreter (a non-2xx raises)
    python) echo "/app/.venv/bin/python -c \"import urllib.request; urllib.request.urlopen('${url}', timeout=5)\"" ;;
    *)    die "unknown probe_tool '${tool}' (use curl | wget | node | python)" ;;
  esac
}

# ---- Per-pod health check (retries while the fresh pod warms up) ---------------
health_check() {                      # $1 ns  $2 key  $3 container  $4 probe
  local ns="$1" key="$2" container="$3" probe="$4" pod attempt
  # Under --dry-run we must not touch the cluster: we can't know a fresh pod's
  # name, so print the probe we *would* exec and return success.
  if [ "${DRY_RUN}" = "true" ]; then
    printf '    ↪  kubectl exec -n %s <fresh %s pod> -c %s -- %s\n' \
      "${ns}" "${key}" "${container}" "${probe}"
    return 0
  fi
  for ((attempt = 1; attempt <= HEALTH_RETRIES; attempt++)); do
    pod="$(kubectl get pod -n "${ns}" -l "app.kubernetes.io/component=${key}" \
            -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null | head -n1)"
    [ -n "${pod}" ] || { sleep "${HEALTH_INTERVAL}"; continue; }
    if run_shell "kubectl exec -n '${ns}' '${pod}' -c '${container}' -- ${probe}"; then
      return 0
    fi
    sleep "${HEALTH_INTERVAL}"
  done
  return 1
}

# ---- One service through the whole loop --------------------------------------
process_service() {
  local key="$1" label="$2" dockerfile="$3" image="$4" workload="$5" \
        container="$6" port="$7" health_path="$8" probe_tool="$9" \
        verify="${10}" verify_entrypoint="${11:-sh}"
  local ns="${NAMESPACE:-${DEFAULT_NS}}"
  [ -n "${ns}" ] || die "no namespace: set it in ${CONFIG} or pass --namespace"
  [ -n "${probe_tool}" ] || probe_tool="curl"
  if [ -n "${TAG_OVERRIDE}" ]; then image="${image%:*}:${TAG_OVERRIDE}"; fi
  local probe; probe="$(build_probe "${probe_tool}" "${port}" "${health_path}")"

  sep "[${key}] ${label}"
  info "image    : ${image}"
  info "workload : ${workload}  (ns: ${ns})"

  # 1. build
  if [ "${DO_BUILD}" = "true" ]; then
    step "build: docker build -f ${dockerfile} -t ${image} ."
    if run_cmd docker build -t "${image}" -f "${dockerfile}" .; then
      ok "image built: ${image}"
    else
      err "build failed — skipping rollout for [${key}]"; FAILED+=("${key} (build)"); return
    fi
  else
    info "build skipped"
  fi

  # 2. verify the fix is baked in
  if [ "${DO_VERIFY}" = "true" ]; then
    if [ -n "${verify}" ]; then
      step "verify image: ${verify}"
      if run_in_image "${image}" "${verify}" "${verify_entrypoint}"; then
        ok "image verification passed"
      elif [ "${VERIFY_WARN}" = "true" ]; then
        warn "image verification failed — proceeding (--verify-warn)"
      else
        err "image verification FAILED — skipping rollout for [${key}]"; FAILED+=("${key} (verify)"); return
      fi
    else
      info "verify skipped (no check defined for this service)"
    fi
  else
    info "verify disabled"
  fi

  # 3. roll out
  if [ "${DO_ROLL}" = "true" ]; then
    # kubectl rollout wants a resource type; the registry stores the bare name.
    local deployment="deployment/${workload}"
    step "rollout: kubectl rollout restart ${deployment} -n ${ns}"
    if run_cmd kubectl rollout restart "${deployment}" -n "${ns}"; then
      step "wait for rollout (timeout ${TIMEOUT}s)"
      if run_cmd kubectl rollout status "${deployment}" -n "${ns}" --timeout="${TIMEOUT}s"; then
        ok "rollout complete"
      else
        err "rollout did not complete in ${TIMEOUT}s — [${key}]"; FAILED+=("${key} (rollout)"); return
      fi
    else
      err "rollout restart failed — [${key}]"; FAILED+=("${key} (restart)"); return
    fi
  else
    info "rollout skipped"
  fi

  # 4. health-check a fresh pod
  if [ "${DO_HEALTH}" = "true" ]; then
    step "health: ${probe_tool} ${health_path} on 127.0.0.1:${port}"
    if health_check "${ns}" "${key}" "${container}" "${probe}"; then
      ok "healthy (${health_path} → ok)"
    else
      err "health check failed after ${HEALTH_RETRIES} attempts — [${key}]"; FAILED+=("${key} (health)"); return
    fi
  else
    info "health skipped"
  fi

  PASSED+=("${key}")
}

# ---- --env resolution: namespace + image tag from the Helm values file -------
# With --env, env-specific values come from the chart's values.<env>.yaml: the
# namespace follows the chart convention (lib_helm_ns), and the image tag is read
# from images.<svc>.tag. Explicit --namespace / --tag still win. services.yaml
# stays pure structure — no env-specific values in it.
resolve_env() {
  [ -n "${ENV_NAME}" ] || return 0
  need_cmd yq
  local vf; vf="$(lib_helm_values_file "${ENV_NAME}")"

  if [ -z "${NAMESPACE}" ]; then
    NAMESPACE="$(lib_helm_ns app "${ENV_NAME}")"
    [ -n "${NAMESPACE}" ] || die "could not resolve namespace for env '${ENV_NAME}'"
  fi

  if [ -z "${TAG_OVERRIDE}" ]; then
    # Every service shares one tag per env in this chart (local→local, dev→dev,
    # qa/production→latest); read it from the backend image as the rebuild tag.
    local tag
    tag="$(yq eval '.images.backend.tag // ""' "${vf}" 2>/dev/null)"
    [ -n "${tag}" ] && TAG_OVERRIDE="${tag}"
  fi
}

# ---- Drive the loop ----------------------------------------------------------
DEFAULT_NS="$(python3 - "${CONFIG}" <<'PY'
import sys, yaml
print((yaml.safe_load(open(sys.argv[1])) or {}).get("namespace", "") or "")
PY
)"

resolve_env

SUBTITLE="build → verify → roll → health for every service in ${CONFIG##*/}"
if [ -n "${ENV_NAME}" ]; then
  SUBTITLE+="  |  env=${ENV_NAME}  ns=${NAMESPACE}  tag=${TAG_OVERRIDE:-<services.yaml>}"
fi
lib_banner "rebuild & roll" "${SUBTITLE}"

for rec in "${SERVICE_RECORDS[@]}"; do
  IFS=$'\t' read -r f_key f_label f_df f_img f_wl f_cont f_port f_path f_tool f_verify f_verify_ep <<< "${rec}"
  [ "${#ONLY_SET[@]}" -eq 0 ] || [ -n "${ONLY_SET[$f_key]:-}" ] || { info "skip ${f_key} (not in --only)"; continue; }
  process_service "${f_key}" "${f_label}" "${f_df}" "${f_img}" "${f_wl}" "${f_cont}" \
                  "${f_port}" "${f_path}" "${f_tool}" "${f_verify}" "${f_verify_ep}"
done

# ---- Summary -----------------------------------------------------------------
echo
sep "summary"
if [ "${#PASSED[@]}" -gt 0 ]; then ok "passed: ${PASSED[*]}"; fi
if [ "${#FAILED[@]}" -gt 0 ]; then err "failed: ${FAILED[*]}"; fi
[ "${#FAILED[@]}" -eq 0 ] || exit 1
echo
[ "${DRY_RUN}" = "true" ] && warn "dry-run: nothing was executed"
exit 0
