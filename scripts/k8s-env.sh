#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# ===========================================================================
# k8s-env.sh — Environment lifecycle for the 3-image stack (config-driven)
# ===========================================================================
# Purpose
#   Install (or remove) one environment via the Helm chart, then drive every
#   workload listed in scripts/services.yaml to a healthy, available state — one
#   shared command instead of a runbook's worth of kubectl.
#
#   apply    helm upgrade --install the chart (-f values.<env>.yaml)
#            → roll out + wait Ready for each service (health-gated)
#   delete   confirm → helm uninstall the release
#   status   one-glance rollup: deployments, pods, services, rollout status
#   wait     skip install; just wait for every service to be available/Ready
#
# Why this exists
#   `make k8s-apply` installs the chart but does not wait, and says nothing about
#   whether the rollout actually succeeded. This script closes both gaps. The
#   chart creates no namespace (namespaces.create=false): apply first applies the
#   admin bootstrap (scripts/k8s-bootstrap.sh: the namespaces, quota, LimitRange
#   and deployer RBAC), so it needs admin rights on the cluster. The chart makes a
#   per-revision migration Job (not a Helm hook), so there is no dev secret to
#   inject and no migration Job to delete-recreate by hand.
#
# Inputs (flags)
#   -e, --env local|dev|qa|production   environment      (default: dev)
#       --release NAME            Helm release name       (default: your-service-name)
#       --values FILE             extra values file, repeatable (added after values.<env>.yaml)
#       --set KEY=VALUE           override a single value, repeatable
#   -n, --namespace NS            target namespace (default: <release>-<env>, per the chart convention)
#   -t, --timeout SECONDS         per-service rollout/wait timeout   (default: 240)
#       --no-wait                 apply without waiting for readiness
#       --only svc1,svc2          restrict to these service keys
#       --dry-run                 print each helm command, execute nothing
#   -y, --yes                     assume yes (skip the delete confirmation)
#   -h, --help
# ===========================================================================
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -Eeuo pipefail
set -o errtrace

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
trap 'lib_err_trap "k8s-env" "${LINENO}" "${BASH_COMMAND}" "$?"' ERR

# ---- Defaults ---------------------------------------------------------------
ACTION="apply"
ENV_NAME="dev"
HELM_RELEASE="${HELM_RELEASE:-your-service-name}"
NAMESPACE=""
ONLY=""
TIMEOUT="${ROLLOUT_TIMEOUT:-240}"
WAIT_TRIES="${WAIT_TRIES:-1}"          # rollout status already blocks; 1 pass
NO_WAIT="false"
# Helm keeps this many release revisions, as the deploy does (scripts/ci/deploy-env.sh); the bootstrap's
# Secrets quota allows them.
HELM_HISTORY_MAX=5
DRY_RUN="${DRY_RUN:-false}"
LIB_ASSUME_YES="${LIB_ASSUME_YES:-false}"

declare -A ONLY_SET=()
declare -a EXTRA_VALUES=()
declare -a SET_ARGS=()

# Chart + per-env values, resolved from the repo root (set by _lib.sh).
LIB_CHART_DIR="${LIB_CHART_DIR:-${LIB_REPO_ROOT}/charts/your-service-name}"

print_usage() {
  cat <<'EOF'
Usage: k8s-env.sh [action] [options]

Actions (default: apply):
  apply                 install the chart (helm upgrade --install), wait for all services healthy
  delete                uninstall the release (asks to confirm)
  status                show deployments/pods/services + rollout status
  wait                  only wait for services to become available/Ready

Options:
  -e, --env ENV         environment: local | dev | qa | production (default: dev)
      --release NAME    Helm release name (default: your-service-name)
      --values FILE     extra values file, repeatable (added after values.<env>.yaml)
      --set KEY=VALUE   override a single value, repeatable (added after --values files)
  -n, --namespace NS    target namespace (default: <release>-<env>, per the chart convention)
  -t, --timeout SECS    per-service rollout timeout (default: 240)
      --no-wait         apply but do not wait for readiness
      --only svc1,svc2  restrict to these service keys (e.g. backend,frontend)
      --dry-run         print each helm command, execute nothing
  -y, --yes             assume yes (skip the delete confirmation)
  -h, --help            show this help

Examples:
  ./scripts/k8s-env.sh apply --env qa
  ./scripts/k8s-env.sh --env dev --only backend
  ./scripts/k8s-env.sh status --env production
  ./scripts/k8s-env.sh delete --env dev --yes
  ./scripts/k8s-env.sh apply --env dev --dry-run
EOF
}

# ---- Flags ------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    apply|delete|status|wait)  ACTION="$1"; shift ;;
    -e|--env)        ENV_NAME="$2"; shift 2 ;;
    --release)       HELM_RELEASE="$2"; shift 2 ;;
    --values)        EXTRA_VALUES+=("$2"); shift 2 ;;
    --set)           SET_ARGS+=("$2"); shift 2 ;;
    -n|--namespace)  NAMESPACE="$2"; shift 2 ;;
    -t|--timeout)    TIMEOUT="$2"; shift 2 ;;
    --only)          ONLY="$2"; shift 2 ;;
    --no-wait)       NO_WAIT="true"; shift ;;
    --dry-run)       DRY_RUN="true"; shift ;;
    -y|--yes)        LIB_ASSUME_YES="true"; shift ;;
    -h|--help)       print_usage; exit 0 ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
done

# ---- Resolve values file, chart dir + namespace -----------------------------
[ -d "${LIB_CHART_DIR}" ] || die "chart not found: ${LIB_CHART_DIR}"
VALUES_FILE="$(lib_helm_values_file "${ENV_NAME}")"
log_meta "chart  : ${VALUES_FILE%/*}"
log_meta "values : ${VALUES_FILE#"${LIB_REPO_ROOT}"/}"

# ---- Load + normalise services from services.yaml ---------------------------
declare -a SVC_KEYS=() SVC_WORKLOADS=()
load_services() {
  need_cmd python3
  local rec k wl
  mapfile -t SVC_RECORDS < <(python3 - "${LIB_SERVICES_YAML}" <<'PY'
import sys, yaml
data = yaml.safe_load(open(sys.argv[1])) or {}
for s in (data.get("services") or []):
    print("\t".join([str(s.get("key", "")), str(s.get("workload", ""))]))
PY
)
  for rec in "${SVC_RECORDS[@]}"; do
    IFS=$'\t' read -r k wl <<< "${rec}"
    [ -n "${k}" ] || continue
    SVC_KEYS+=("${k}"); SVC_WORKLOADS+=("${wl}")
  done
  [ "${#SVC_KEYS[@]}" -gt 0 ] || die "no services defined in ${LIB_SERVICES_YAML}"
}

# --only filter set
if [ -n "${ONLY}" ]; then
  IFS=',' read -ra _only_arr <<< "${ONLY}"
  for s in "${_only_arr[@]}"; do ONLY_SET["${s// /}"]="1"; done
fi

# ---- Resolve namespace (override > chart convention) ------------------------
resolve_namespace() {
  if [ -n "${NAMESPACE}" ]; then return 0; fi
  NAMESPACE="$(lib_helm_ns app "${ENV_NAME}")"
  [ -n "${NAMESPACE}" ] || die "could not resolve app namespace for env '${ENV_NAME}'; pass --namespace"
}

# ---- Preflight --------------------------------------------------------------
need_cmd kubectl helm yq
[ "${DRY_RUN}" = "true" ] || require_kube
load_services
resolve_namespace

# ===========================================================================
# wait_for_services — roll every (filtered) service out and gate on readiness.
# ===========================================================================
wait_for_services() {
  local ns="$1" i key wl
  for i in "${!SVC_KEYS[@]}"; do
    key="${SVC_KEYS[$i]}"; wl="${SVC_WORKLOADS[$i]}"
    if [ "${#ONLY_SET[@]}" -gt 0 ] && [ -z "${ONLY_SET[$key]:-}" ]; then
      log_info "skip ${key} (not in --only)"; continue
    fi
    lib_sep "wait: ${key}"
    log_step "kubectl -n ${ns} rollout status deployment/${wl} --timeout=${TIMEOUT}s"
    if ! run kubectl -n "${ns}" rollout status "deployment/${wl}" --timeout="${TIMEOUT}s"; then
      log_err "rollout for [${key}] did not complete in ${TIMEOUT}s"
      return 1
    fi
    log_step "kubectl -n ${ns} wait for Ready pods (app.kubernetes.io/component=${key})"
    if ! run kubectl -n "${ns}" wait --for=condition=ready pod \
            -l "app.kubernetes.io/component=${key}" --timeout="${TIMEOUT}s" --all; then
      log_warn "a Ready pod for [${key}] was not observed (check events with: kubectl -n ${ns} describe deploy/${wl})"
    fi
  done
  return 0
}

# NOTE: the retired Kustomize flow injected the local-dev app secret and
# delete-recreated the migration Job by hand. The chart supersedes both:
# secrets come from values.<env>.yaml, and every release revision creates its own
# migration Job (<fullname>-migrate-r<revision>, not a Helm hook).

# ===========================================================================
# Actions
# ===========================================================================
cmd_apply() {
  lib_banner "k8s-env apply" "env=${ENV_NAME}  release=${HELM_RELEASE}  ns=${NAMESPACE}"
  log_info "values   : ${VALUES_FILE#"$LIB_REPO_ROOT"/}"
  log_info "namespace: ${NAMESPACE}"

  # helm upgrade --install is idempotent: the first install and every later deploy
  # use the same command. The chart makes a new migration Job per release revision,
  # so there is no migration Job to delete-recreate. Extra -f files (from --values)
  # override values.<env>.yaml; --set KEY=VALUE (repeatable) overrides individual values.
  local -a helm=(upgrade --install "${HELM_RELEASE}" "${LIB_CHART_DIR}" -f "${VALUES_FILE}" --history-max "${HELM_HISTORY_MAX}")
  local vf s
  for vf in "${EXTRA_VALUES[@]}"; do helm+=(-f "${vf}"); done
  for s in "${SET_ARGS[@]}"; do helm+=(--set "${s}"); done
  helm+=(-n "${NAMESPACE}")

  # A release from an older chart owns its Namespace objects and the app's Role and RoleBinding.
  # Switching to the bootstrap would make Helm delete them (the namespaces with everything in them),
  # and a deployer could not. So upgrade it once with them still rendered: they gain
  # helm.sh/resource-policy: keep, and the normal upgrade below drops them from the release record
  # while leaving them in place. The manifest is captured first (a pipe into grep -q could die of
  # SIGPIPE under pipefail and skip this); a failed lookup is an error, except "not found".
  local manifest="" rc=0
  manifest="$(helm get manifest "${HELM_RELEASE}" -n "${NAMESPACE}" 2>&1)" || rc=$?
  if [ "${rc}" -ne 0 ]; then
    grep -q "release: not found" <<<"${manifest}" \
      || die "could not read release ${HELM_RELEASE} in ${NAMESPACE}: ${manifest}"
    manifest=""
  fi
  if grep -Eqx 'kind: (Namespace|Role|RoleBinding)' <<<"${manifest}"; then
    local -a keep=("${helm[@]}" --set namespaces.create=true --set rbac.create=true)
    log_warn "release ${HELM_RELEASE} still owns its Namespace, Role or RoleBinding objects: one upgrade with namespaces.create=true and rbac.create=true first, so they are kept when the chart stops rendering them"
    [ "${DRY_RUN}" != "true" ] || log_info "dry run: this is the upgrade that would run first"
    log_step "helm ${keep[*]}"
    run helm "${keep[@]}"
  fi

  # The chart creates no namespace (namespaces.create=false). The namespaces, with the
  # restricted Pod Security labels, the quota, the LimitRange and the deployer RBAC, come
  # from the admin bootstrap: render it and apply it first (idempotent; needs admin rights).
  local -a boot=("${LIB_DIR}/k8s-bootstrap.sh" "${ENV_NAME}" --release "${HELM_RELEASE}" --namespace "${NAMESPACE}")
  log_step "${boot[*]} | kubectl apply -f -"
  if [ "${DRY_RUN}" = "true" ]; then
    run "${boot[*]} | kubectl apply -f -"
  else
    "${boot[@]}" | kubectl apply -f -
  fi

  log_step "helm ${helm[*]}"
  run helm "${helm[@]}"

  if [ "${NO_WAIT}" = "true" ]; then
    log_info "--no-wait: not gating on readiness"
  else
    wait_for_services "${NAMESPACE}" || die "environment did not become ready"
  fi

  log_ok "apply complete for env=${ENV_NAME} (release ${HELM_RELEASE} in ${NAMESPACE})"
}

cmd_wait() {
  lib_banner "k8s-env wait" "env=${ENV_NAME}  ns=${NAMESPACE}"
  wait_for_services "${NAMESPACE}" || die "environment did not become ready"
  log_ok "all targeted services available"
}

cmd_status() {
  lib_banner "k8s-env status" "env=${ENV_NAME}  ns=${NAMESPACE}"
  log_info "namespace: ${NAMESPACE}"
  echo
  log_step "kubectl -n ${NAMESPACE} get deployments"
  run kubectl -n "${NAMESPACE}" get deployment
  echo
  log_step "kubectl -n ${NAMESPACE} get pods"
  run kubectl -n "${NAMESPACE}" get pod
  echo
  log_step "kubectl -n ${NAMESPACE} get services"
  run kubectl -n "${NAMESPACE}" get service
  echo
  local i key wl
  for i in "${!SVC_KEYS[@]}"; do
    key="${SVC_KEYS[$i]}"; wl="${SVC_WORKLOADS[$i]}"
    if [ "${#ONLY_SET[@]}" -gt 0 ] && [ -z "${ONLY_SET[$key]:-}" ]; then continue; fi
    lib_sep "rollout: ${key}"
    run kubectl -n "${NAMESPACE}" rollout status "deployment/${wl}" --timeout="${TIMEOUT}s"
  done
}

cmd_delete() {
  lib_banner "k8s-env delete" "env=${ENV_NAME}  release=${HELM_RELEASE}  ns=${NAMESPACE}"
  confirm "uninstall release ${HELM_RELEASE} (env=${ENV_NAME}) from ${NAMESPACE}?" || exit 0
  log_step "helm uninstall ${HELM_RELEASE} -n ${NAMESPACE}"
  run helm uninstall "${HELM_RELEASE}" -n "${NAMESPACE}"
  # The namespaces come from the bootstrap, not the release, so they persist; note how
  # to remove them so a re-install starts clean (and a lingering -db PVC is a choice).
  log_warn "release removed; namespaces ${NAMESPACE} and ${NAMESPACE}-db persist — delete them with: kubectl delete namespace ${NAMESPACE} ${NAMESPACE}-db"
  log_ok "delete complete for env=${ENV_NAME}"
}

# ---- Dispatch ---------------------------------------------------------------
case "${ACTION}" in
  apply)  cmd_apply ;;
  wait)   cmd_wait ;;
  status) cmd_status ;;
  delete) cmd_delete ;;
  *)      die "unknown action: ${ACTION} (use apply | delete | status | wait)" ;;
esac
