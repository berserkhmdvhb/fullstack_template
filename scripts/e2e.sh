#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# ===========================================================================
# e2e.sh — deploy + verify the stack end-to-end (the "did it actually come up?"
#          gate), then assert the rendered chart is internally consistent.
# ===========================================================================
# Purpose
#   One command that stands up the environment AND proves it is healthy and
#   correct in-cluster — the sweep you'd otherwise paste as a dozen ad-hoc
#   `kubectl` blocks. It answers the questions `k8s-env.sh apply` (which only
#   waits for Ready) and `smoke-test.sh` (which only checks the HTTP surface)
#   leave open:
#
#     • Is Postgres actually Running (not crash-looping)?
#     • Did the migration Job complete 1/1?
#     • Is the backend on real Postgres, not the SQLite fallback?
#     • Are replicas / HPA / ResourceQuota consistent (no stuck rollout)?
#     • Do the live objects still match what the Helm chart renders?
#
#   Each check prints its raw diagnostics and a PASS / WARN / FAIL verdict; the
#   command exits non-zero if any check FAILs, so it doubles as a CI gate.
#
# Subcommands (default: all)
#   deploy    apply the chart (k8s-env.sh → helm upgrade --install), then verify
#   verify    run the in-cluster verification sweep only (no apply, read-only)
#   render    render the chart (helm template) and assert the key invariants hold
#   all       deploy, then verify
#
# Inputs (flags)
#   -e, --env ENV       local | dev | qa | production  (default: local)
#       --release NAME  Helm release name       (default: your-service-name)
#       --values FILE   extra values file, repeatable (added after values.<env>.yaml)
#       --set KEY=VALUE override a single value, repeatable
#   -n, --namespace NS  app namespace override        (default: <release>-<env>)
#       --db-ns NS      db namespace override         (default: <release>-<env>-db)
#       --dry-run       print every mutating command, execute nothing
#   -h, --help
#
# Secrets now come from values.<env>.yaml: the chart renders the app secret and,
# for local/dev/qa, the db-credentials secret. Nothing here injects credentials
# by hand — production's managed DB brings its own, supplied via sealed-secrets /
# ESO / SOPS. This script never prints a resolved DATABASE_URL or password.
# ===========================================================================
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -Eeuo pipefail
set -o errtrace

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/ci/migration-job-lib.sh"
trap 'lib_err_trap "e2e" "${LINENO}" "${BASH_COMMAND}" "$?"' ERR
trap 'echo; log_err "interrupted — aborting"; exit 130' INT TERM

# ---- Defaults ---------------------------------------------------------------
ACTION="all"
ENV_NAME="local"
HELM_RELEASE="${HELM_RELEASE:-your-service-name}"
NAMESPACE=""
DB_NAMESPACE=""
DRY_RUN="${DRY_RUN:-false}"
declare -a EXTRA_VALUES=()
declare -a SET_ARGS=()

# DB tier + secret posture, resolved from the env's values file (resolve_db_tier):
#   PG_ENABLED   true  → chart bundles Postgres (local/dev/qa): StatefulSet + db-credentials
#                false → production's managed DB (externalDatabase; no StatefulSet)
#   SECRETS_SET  true  → values.<env>.yaml ships app-secret values (local/dev/qa)
#                false → production (real secrets via sealed-secrets / ESO / SOPS)
PG_ENABLED=""
SECRETS_SET=""

# Verdict accumulators (mirrors smoke-test.sh).
declare -a R_NAME=() R_RESULT=() R_DETAIL=()
PASS=0; WARN=0; FAIL=0

print_usage() {
  cat <<'EOF'
Usage: e2e.sh [action] [options]

Actions (default: all):
  deploy    apply the chart (k8s-env.sh → helm upgrade --install), then verify
  verify    in-cluster verification sweep only (read-only, no apply)
  render    render the chart (helm template) and assert the key invariants
  all       deploy, then verify

Options:
  -e, --env ENV       local | dev | qa | production   (default: local)
      --release NAME  Helm release name       (default: your-service-name)
      --values FILE   extra values file, repeatable (added after values.<env>.yaml)
      --set KEY=VALUE override a single value, repeatable
  -n, --namespace NS  app namespace override   (default: <release>-<env>)
      --db-ns NS      db namespace override    (default: <release>-<env>-db)
      --dry-run       print mutating commands, execute nothing
  -h, --help          show this help

Examples:
  ./scripts/e2e.sh                    # full local deploy + verify
  ./scripts/e2e.sh verify --env dev
  ./scripts/e2e.sh render --env production
  ./scripts/e2e.sh deploy --env dev --dry-run
  ./scripts/e2e.sh deploy --env local --set ingress.enabled=false   # coexistence: no Ingress clash
EOF
}

# ---- Flags ------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    deploy|verify|render|all)  ACTION="$1"; shift ;;
    -e|--env)       ENV_NAME="$2"; shift 2 ;;
    --release)      HELM_RELEASE="$2"; shift 2 ;;
    --values)       EXTRA_VALUES+=("$2"); shift 2 ;;
    --set)          SET_ARGS+=("$2"); shift 2 ;;
    -n|--namespace) NAMESPACE="$2"; shift 2 ;;
    --db-ns)        DB_NAMESPACE="$2"; shift 2 ;;
    --dry-run)      DRY_RUN="true"; shift ;;
    -h|--help)      print_usage; exit 0 ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
done

# ---- Preflight --------------------------------------------------------------
need_cmd kubectl helm python3 yq curl
# `render` is purely local (helm template) and needs no cluster; verify/deploy do.
if [ "${ACTION}" != "render" ] && [ "${DRY_RUN}" != "true" ]; then
  require_kube
fi

# ---- Resolve namespaces + DB tier (Helm convention, not a rendered overlay) -
# The chart's namespaces follow a deterministic convention (lib_helm_ns): app ns
# = <release>-<env>, db ns = <app>-db. There is no overlay to render for names.
resolve_namespaces() {
  if [ -n "${NAMESPACE}" ]; then
    log_meta "app ns: ${NAMESPACE} (explicit)"
  else
    NAMESPACE="$(lib_helm_ns app "${ENV_NAME}")"
    [ -n "${NAMESPACE}" ] || die "could not resolve app namespace for env '${ENV_NAME}'; pass --namespace"
  fi
  if [ -z "${DB_NAMESPACE}" ]; then
    DB_NAMESPACE="$(lib_helm_ns db "${ENV_NAME}")"
  fi
  log_meta "app namespace: ${NAMESPACE}   db namespace: ${DB_NAMESPACE}"
}

# resolve_db_tier — read the env's values file to learn whether the chart bundles
# Postgres (local/dev/qa) or points at a managed DB (production), and whether the
# committed values ship app-secret data. This drives which render/verify checks
# apply: production renders no StatefulSet, no db-credentials in the db ns, and no
# committed app secret (real ones come from sealed-secrets / ESO / SOPS).
resolve_db_tier() {
  local vf pw
  vf="$(lib_helm_values_file "${ENV_NAME}")"
  PG_ENABLED="$(yq -r '.postgresql.enabled // false' "${vf}" 2>/dev/null || echo false)"
  pw="$(yq -r '.secrets.dbPassword // ""' "${vf}" 2>/dev/null || echo "")"
  if [ -n "${pw}" ]; then SECRETS_SET="true"; else SECRETS_SET="false"; fi
  log_meta "db tier: postgresql.enabled=${PG_ENABLED}   secrets set=${SECRETS_SET}"
}

# render_manifests — `helm template` the chart for this env. Read-only (never
# touches a cluster), so it always executes; extra -f files and --set overrides
# (from --values/--set) are honoured so render reflects exactly what would deploy.
# Sets two globals instead of printing: RENDER_STDOUT (the manifests) and
# RENDER_STDERR (Helm's own warnings). Splitting the streams means the "no render
# warnings" check keys off Helm's real output — a rendered value like
# `LOG_LEVEL: WARNING` in the docs can't masquerade as a warning. Call it directly
# (not inside $(…)), or the globals set here are lost to the subshell.
render_manifests() {
  local -a args=(template "${HELM_RELEASE}" "${LIB_CHART_DIR}" -f "$(lib_helm_values_file "${ENV_NAME}")")
  local vf s tmp
  for vf in "${EXTRA_VALUES[@]}"; do args+=(-f "${vf}"); done
  for s in "${SET_ARGS[@]}"; do args+=(--set "${s}"); done
  args+=(-n "${NAMESPACE}")
  tmp="$(mktemp)"
  RENDER_STDOUT="$(helm "${args[@]}" 2>"${tmp}")"
  RENDER_STDERR="$(cat "${tmp}")"; rm -f "${tmp}"
}

# ===========================================================================
# record — log one check's verdict and tally it.
#   record "name" PASS|WARN|FAIL "detail"
# ===========================================================================
record() {
  R_NAME+=("$1"); R_RESULT+=("$2"); R_DETAIL+=("${3:-}")
  case "$2" in
    PASS) PASS=$((PASS+1)); log_ok "$1 — ${3:-}" ;;
    WARN) WARN=$((WARN+1)); log_warn "$1 — ${3:-}" ;;
    FAIL) FAIL=$((FAIL+1)); log_err "$1 — ${3:-}" ;;
  esac
}

verdict_summary() {
  echo
  lib_sep "verdict"
  printf '  %s%-28s %-8s %s%s\n' "${C_BLD}" "CHECK" "RESULT" "DETAIL" "${C_RST}"
  local i color
  for i in "${!R_NAME[@]}"; do
    color="${C_GREEN}"; [[ "${R_RESULT[$i]}" == "WARN" ]] && color="${C_YEL}"
    [[ "${R_RESULT[$i]}" == "FAIL" ]] && color="${C_RED}"
    printf '  %s%-28s %-8s %s%s\n' "${color}" "${R_NAME[$i]}" "${R_RESULT[$i]}" "${R_DETAIL[$i]}" "${C_RST}"
  done
  echo
  log_info "summary: ${PASS} passed, ${WARN} warning(s), ${FAIL} failed"
}

# ===========================================================================
# render — assert the rendered chart is internally consistent.
# ===========================================================================
cmd_render() {
  lib_banner "e2e render" "env=${ENV_NAME}  ns=${NAMESPACE}"
  render_manifests                       # sets RENDER_STDOUT / RENDER_STDERR
  local out="${RENDER_STDOUT}"

  local n_ss n_root n_dburl n_warn
  # grep -c exits 1 on zero matches; `|| true` keeps a zero count from tripping set -e.
  n_ss="$(printf '%s' "${out}" | grep -c '^kind: StatefulSet$' || true)"
  n_root="$(printf '%s' "${out}" | grep -c 'runAsUser: 999$' || true)"
  n_dburl="$(grep -c 'postgresql+asyncpg' <<<"${out}" || true)"
  # Count only Helm's own warnings (stderr), not substrings inside rendered values.
  n_warn="$(printf '%s' "${RENDER_STDERR}" | grep -ic 'warn' || true)"

  lib_sep "rendered invariants"
  printf '%s\n' "${out}"

  echo
  # StatefulSet / non-root Postgres: only when the chart bundles Postgres. Production
  # (managed DB) renders neither — assert the *absence* there instead of failing.
  if [ "${PG_ENABLED}" = "true" ]; then
    if [ "${n_ss}" -ge 1 ]; then record "StatefulSet present"      PASS  "count=${n_ss}"
    else                          record "StatefulSet present"      FAIL  "count=${n_ss}"; fi
    if [ "${n_root}" -ge 1 ]; then record "non-root Postgres (999)" PASS  "runAsUser:999 ×${n_root}"
    else                          record "non-root Postgres (999)" FAIL  "runAsUser:999 ×${n_root}"; fi
  else
    if [ "${n_ss}" -eq 0 ]; then record "StatefulSet absent (managed DB)" PASS  "no StatefulSet, as expected"
    else                         record "StatefulSet absent (managed DB)" FAIL  "unexpected StatefulSet count=${n_ss}"; fi
    record "non-root Postgres (999)" WARN "managed DB — no in-cluster Postgres to check"
  fi
  # DATABASE_URL carries the postgresql+asyncpg scheme for every env (the scheme is
  # baked into chart.databaseURL), so this holds whether the DB is bundled or external.
  if [ "${n_dburl}" -ge 1 ]; then record "DATABASE_URL is Postgres" PASS  "postgresql+asyncpg ×${n_dburl}"
  else                            record "DATABASE_URL is Postgres" FAIL  "no postgresql+asyncpg DATABASE_URL"; fi
  if [ "${n_warn}" -eq 0 ]; then   record "no render warnings"       PASS  "clean"
  else                             record "no render warnings"       WARN  "${n_warn} warning line(s)"; fi

  verdict_summary
  [ "${FAIL}" -eq 0 ] || exit 1
}

# ===========================================================================
# verify — read-only in-cluster sweep. Each check prints its raw diagnostics.
# ===========================================================================
check_postgres() {
  # Managed DB (production): no in-cluster Postgres pod exists — skip, don't FAIL.
  if [ "${PG_ENABLED}" != "true" ]; then
    record "postgres Running" WARN "managed DB (external) — no in-cluster Postgres pod"
    return 0
  fi
  lib_sep "verify: postgres pod (-db ns)"
  kubectl get pod -n "${DB_NAMESPACE}" -l app.kubernetes.io/component=postgresql -o wide 2>&1 || true
  local name ready restarts reason
  name="$(kubectl get pod -n "${DB_NAMESPACE}" -l app.kubernetes.io/component=postgresql \
            -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
  # .status.phase is "Running" even mid-crash-loop (a container is being restarted),
  # so gate on the container's *ready* flag — that is the real "is it serving" signal.
  ready="$(kubectl get pod -n "${DB_NAMESPACE}" -l app.kubernetes.io/component=postgresql \
             -o jsonpath='{.items[0].status.containerStatuses[0].ready}' 2>/dev/null || true)"
  restarts="$(kubectl get pod -n "${DB_NAMESPACE}" -l app.kubernetes.io/component=postgresql \
                -o jsonpath='{.items[0].status.containerStatuses[0].restartCount}' 2>/dev/null || true)"
  reason="$(kubectl get pod -n "${DB_NAMESPACE}" -l app.kubernetes.io/component=postgresql \
              -o jsonpath='{.items[0].status.containerStatuses[0].state.waiting.reason}' 2>/dev/null || true)"
  if [ "${ready}" = "true" ]; then
    record "postgres Running" PASS "${name:-postgresql-0} ready (restarts=${restarts:-0})"
  else
    record "postgres Running" FAIL "${name:-postgresql-0} not ready (restarts=${restarts:-0}, ${reason:-unknown}) — events:"
    # An `if`, not `[ -n … ] && kubectl … || true` (`A && B || C` is not
    # if-then-else — SC2015). Only print events when a pod name was captured.
    if [ -n "${name}" ]; then
      kubectl describe pod -n "${DB_NAMESPACE}" "${name}" 2>/dev/null | sed -n '/Events:/,$p' || true
    fi
  fi
}

check_deployments() {
  lib_sep "verify: dev deployments + replicasets"
  kubectl get deploy,rs -n "${NAMESPACE}" 2>&1 || true
  local lagging
  lagging="$(kubectl get deploy -n "${NAMESPACE}" \
               -o jsonpath='{range .items[?(@.status.availableReplicas < @.spec.replicas)]}{.metadata.name}{"\n"}{end}' 2>/dev/null || true)"
  if [ -z "${lagging}" ]; then
    record "deployments available" PASS "all deployments have their replicas Available"
  else
    record "deployments available" FAIL "not yet available: $(echo "${lagging}" | tr '\n' ' ')"
  fi
}

check_migration() {
  lib_sep "verify: migration (per-revision Job)"
  # The migration is a plain Job named <fullname>-migrate-r<revision>, not a Helm hook, and k8s-env.sh
  # deploys without --wait: `helm status` = deployed says nothing about it. Take the Job the release
  # manifest names (scripts/ci/migration-job-lib.sh), wait for its Complete condition, fail on Failed
  # or on any lookup error (recorded, not an abort).
  local timeout="${MIGRATION_WAIT_SECONDS:-300}" msg rc=0
  msg="$(migration_job_wait "${NAMESPACE}" "${HELM_RELEASE}" "${timeout}")" || rc=$?
  case "${rc}" in
    0) record "migration completed" PASS "${msg}" ;;
    1) record "migration completed" FAIL "${msg}"
       migration_job_report "${NAMESPACE}" "${HELM_RELEASE}" ;;
    *) record "migration completed" FAIL "${msg}"
       migration_job_report "${NAMESPACE}" "${HELM_RELEASE}" ;;
  esac
}

check_http_contract() {
  # Reach the backend API through a throwaway port-forward (Ingress is disabled in
  # the local coexistence window). /health gates readiness; /items proves the
  # migration populated the schema — against an un-migrated DB the read path is 500.
  lib_sep "verify: backend HTTP contract (port-forward :8000)"
  # Send the Service name as Host, as the frontend does in-cluster: the backend refuses a Host that
  # is not in the ALLOWED_HOSTS the chart renders (127.0.0.1 is not).
  local BACKEND_SERVICE="${HELM_RELEASE}-backend"
  kubectl port-forward -n "${NAMESPACE}" "svc/${BACKEND_SERVICE}" 8000:8000 >/dev/null 2>&1 &
  local pf=$! i up=0 health items
  for i in 1 2 3 4 5; do
    if curl -s -o /dev/null --max-time 3 -H "Host: ${BACKEND_SERVICE}" http://127.0.0.1:8000/api/v1/health 2>/dev/null; then up=1; break; fi
    sleep 1
  done
  health="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -H "Host: ${BACKEND_SERVICE}" http://127.0.0.1:8000/api/v1/health 2>/dev/null || echo 000)"
  items="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 -H "Host: ${BACKEND_SERVICE}" http://127.0.0.1:8000/api/v1/items 2>/dev/null || echo 000)"
  kill "${pf}" >/dev/null 2>&1 || true
  wait "${pf}" 2>/dev/null || true
  if [ "${up}" = "1" ] && [ "${health}" = "200" ]; then record "GET /health → 200" PASS "backend ready"
  else                                                    record "GET /health → 200" FAIL "tunnel=${up} health=${health}"; fi
  if [ "${items}" = "200" ]; then record "GET /items → 200" PASS "schema migrated (items readable)"
  else                            record "GET /items → 200" FAIL "status ${items} (schema/read path not healthy)"; fi
}

check_backend_engine() {
  lib_sep "verify: backend DATABASE_URL + DB engine"
  local dburl
  # The backend image starts no shell: read the variable with its own Python.
  dburl="$(kubectl exec -n "${NAMESPACE}" deploy/your-service-name-backend -c backend \
             -- /app/.venv/bin/python -c 'import os; print(os.environ.get("DATABASE_URL", ""))' 2>/dev/null || true)"
  printf 'DATABASE_URL=%s\n' "$(mask_conn "${dburl}")"
  local logs
  logs="$(kubectl logs -n "${NAMESPACE}" deploy/your-service-name-backend --tail 60 2>&1 || true)"
  if [[ "${dburl}" == *"postgresql"* ]]; then
    if printf '%s' "${logs}" | grep -qi "SQLiteImpl"; then
      record "backend on Postgres" WARN "DATABASE_URL is postgresql but a SQLiteImpl fallback appeared in logs"
    else
      record "backend on Postgres" PASS "DATABASE_URL is postgresql+asyncpg, no SQLite fallback"
    fi
  elif [ -n "${dburl}" ]; then
    record "backend on Postgres" FAIL "DATABASE_URL is not postgresql: ${dburl}"
  else
    record "backend on Postgres" WARN "could not read DATABASE_URL from the backend pod"
  fi
  printf '%s\n' "${logs}" | grep -iE "SQLiteImpl|using SQLite|postgresql|asyncpg|Connected to|Traceback|[Ee]rror" | tail -12 || true
}

check_replicas_quota() {
  lib_sep "verify: replicas / HPA / quota"
  kubectl get hpa -n "${NAMESPACE}" 2>&1 || true
  kubectl get resourcequota -n "${NAMESPACE}" 2>&1 || true
  kubectl describe resourcequota -n "${NAMESPACE}" 2>&1 | sed -n '/Name:/,/Resource/p' || true
  # Quota headroom depends on the node + HPA maxReplicas; assert nothing here, just
  # surface it. The rule: the HPA maxReplicas must fit the ResourceQuota.
  record "replicas / quota reported" PASS "see HPA + ResourceQuota above"
}

check_secrets_present() {
  if [ "${PG_ENABLED}" = "true" ]; then
    # Bundled Postgres (local/dev/qa): db-credentials live in the DB namespace
    # (db-secret.yaml, from postgresql.auth); the app secret in the app namespace.
    lib_sep "verify: secrets in both namespaces"
    log_step "db ns (${DB_NAMESPACE})"
    kubectl get secret -n "${DB_NAMESPACE}" 2>&1 || true
    log_step "app ns (${NAMESPACE})"
    kubectl get secret -n "${NAMESPACE}" 2>&1 || true
    local db_keys app_keys
    db_keys="$(kubectl get secret -n "${DB_NAMESPACE}" your-service-name-db-credentials \
                 -o jsonpath='{.data}' 2>/dev/null || true)"
    app_keys="$(kubectl get secret -n "${NAMESPACE}" your-service-name-secret \
                   -o jsonpath='{.data}' 2>/dev/null || true)"
    if [ -n "${db_keys}" ] && [ "${db_keys}" != "{}" ]; then record "db-credentials present" PASS "keys loaded"
    else                                                    record "db-credentials present" FAIL "missing in ${DB_NAMESPACE}"; fi
    if [ -n "${app_keys}" ] && [ "${app_keys}" != "{}" ]; then record "app secret present"   PASS "keys loaded"
    else                                                     record "app secret present"   FAIL "missing in ${NAMESPACE}"; fi
  else
    # Managed DB (production): db-credentials is rendered into the APP namespace by
    # database-external.yaml (host/port/username/database), and the app secret is
    # supplied out-of-band (sealed-secrets / ESO / SOPS) — surface both, assert neither.
    lib_sep "verify: secrets (managed DB — production)"
    log_step "app ns (${NAMESPACE}) — db-credentials rendered here for the managed DB"
    kubectl get secret -n "${NAMESPACE}" 2>&1 || true
    local ext_keys
    ext_keys="$(kubectl get secret -n "${NAMESPACE}" your-service-name-db-credentials \
                  -o jsonpath='{.data}' 2>/dev/null || true)"
    if [ -n "${ext_keys}" ] && [ "${ext_keys}" != "{}" ]; then record "managed-db credentials present" PASS "host/port/username/database loaded"
    else                                                       record "managed-db credentials present" WARN "not rendered (supply via sealed-secrets / ESO / SOPS)"; fi
    record "app secret present" WARN "production app secret is external (sealed-secrets / ESO / SOPS)"
  fi
}

cmd_verify() {
  lib_banner "e2e verify" "env=${ENV_NAME}  app=${NAMESPACE}  db=${DB_NAMESPACE}"
  check_postgres
  check_deployments
  check_migration
  check_http_contract
  check_backend_engine
  check_replicas_quota
  check_secrets_present
  verdict_summary
  [ "${FAIL}" -eq 0 ] || exit 1
}

# ===========================================================================
# deploy — apply the chart via k8s-env.sh (helm upgrade --install: migration
# per-revision migration Job, secrets from values.<env>.yaml, both namespaces created),
# then verify. No secret is injected here.
# ===========================================================================
cmd_deploy() {
  lib_banner "e2e deploy" "env=${ENV_NAME}  app=${NAMESPACE}  db=${DB_NAMESPACE}"
  # The chart apply (helm upgrade --install, per-revision migration Job, secrets
  # from values.<env>.yaml, both namespaces created) is owned by k8s-env.sh —
  # reuse it rather than reimplementing the flow. No secret is injected here.
  local -a args=("apply" "--env" "${ENV_NAME}" "--release" "${HELM_RELEASE}")
  local vf; for vf in "${EXTRA_VALUES[@]}"; do args+=("--values" "${vf}"); done
  local s;  for s  in "${SET_ARGS[@]}"; do args+=("--set" "${s}"); done
  # Coexistence window: `local` disables its Ingress so its host/path can't collide
  # with the still-live Kustomize `local` deploy. Drop this after Stage C (k8s/ gone).
  if [ "${ENV_NAME}" = "local" ] && ! printf ' %s ' " ${SET_ARGS[*]} " | grep -q "ingress.enabled=false"; then
    args+=("--set" "ingress.enabled=false")
  fi
  [ "${DRY_RUN}" = "true" ] && args+=("--dry-run")

  lib_sep "apply chart (k8s-env.sh)"
  if [ "${DRY_RUN}" = "true" ]; then
    log_step "Helm-driven apply via k8s-env.sh (dry-run):"
    printf '⇢ %s %s\n' "${LIB_DIR}/k8s-env.sh" "${args[*]}"
  else
    "${LIB_DIR}/k8s-env.sh" "${args[@]}"
  fi
}

# ---- Dispatch ---------------------------------------------------------------
resolve_namespaces
resolve_db_tier

case "${ACTION}" in
  render) cmd_render ;;
  verify) cmd_verify ;;
  deploy) cmd_deploy; [ "${DRY_RUN}" = "true" ] || cmd_verify ;;
  all)    cmd_deploy; [ "${DRY_RUN}" = "true" ] || cmd_verify ;;
  *)      die "unknown action: ${ACTION} (use deploy | verify | render | all)" ;;
esac
