#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# ===========================================================================
# db-backup.sh — trigger the pg-backup CronJob and fetch the dump
# ===========================================================================
# Purpose
#   Take an on-demand PostgreSQL backup the same way the scheduled one does, then
#   copy the resulting dump off the backup PersistentVolume to your laptop:
#     1. create a one-shot Job from the pg-backup CronJob
#     2. wait for that Job to finish (condition=complete)
#     3. mount the backup PVC in a short-lived "fetch" pod and copy the dump out
#     4. delete the fetch pod and the one-shot Job (unless --no-cleanup)
#
# Why the fetch pod?
#   The backup Job writes to a PVC, then exits — and `kubectl cp` cannot reach a
#   completed pod. So we attach a tiny running pod to the SAME PVC, copy the file,
#   and throw the pod away. The PVC name and mount path are configurable.
#
# Inputs (flags)
#   -e, --env ENV          dev|qa|production — DB namespace from the chart convention
#   -n, --namespace NS     target namespace (default: <release>-<env>-db / services.yaml)
#       --cronjob NAME     CronJob to trigger (default: your-service-name-pg-backup)
#       --pvc NAME         backup PVC (default: your-service-name-pg-backup-pvc)
#       --mount PATH        PVC mount path inside the fetch pod (default: /backup)
#       --dest DIR          where to write the dump (default: ./backups)
#       --fetch-image IMG   image for the fetch pod (default: busybox:1.36)
#       --no-cleanup        keep the one-shot Job + fetch pod for debugging
#       --timeout SECS      wait timeout (default: 300)
#       --dry-run          print each command, execute nothing
#   -h, --help
# ===========================================================================
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -Eeuo pipefail
set -o errtrace

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
trap 'lib_err_trap "db-backup" "${LINENO}" "${BASH_COMMAND}" "$?"' ERR
trap 'echo; log_err "interrupted — aborting"; exit 130' INT TERM

# ---- Defaults ---------------------------------------------------------------
ENV_NAME=""
NAMESPACE=""
CRONJOB="your-service-name-pg-backup"
PVC="your-service-name-pg-backup-pvc"
MOUNT="/backup"
DEST="./backups"
FETCH_IMAGE="busybox:1.36"
CLEANUP="true"
TIMEOUT="300"
DRY_RUN="${DRY_RUN:-false}"
JOB_NAME=""
FETCH_POD=""

print_usage() {
  cat <<'EOF'
Usage: db-backup.sh [options]

Options:
  -e, --env ENV         dev|qa|production — DB namespace from the chart convention
  -n, --namespace NS    target namespace (default: <release>-<env>-db / services.yaml)
      --cronjob NAME    CronJob to trigger (default: your-service-name-pg-backup)
      --pvc NAME        backup PVC (default: your-service-name-pg-backup-pvc)
      --mount PATH      PVC mount path in the fetch pod (default: /backup)
      --dest DIR        where to write the dump (default: ./backups)
      --fetch-image IMG image for the fetch pod (default: busybox:1.36)
      --no-cleanup      keep the one-shot Job + fetch pod for debugging
      --timeout SECS    wait timeout (default: 300)
      --dry-run         print each command, execute nothing
  -h, --help            show this help
EOF
}

# ---- Flags ------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    -e|--env)        ENV_NAME="$2"; shift 2 ;;
    -n|--namespace)  NAMESPACE="$2"; shift 2 ;;
    --cronjob)       CRONJOB="$2"; shift 2 ;;
    --pvc)           PVC="$2"; shift 2 ;;
    --mount)         MOUNT="$2"; shift 2 ;;
    --dest)          DEST="$2"; shift 2 ;;
    --fetch-image)   FETCH_IMAGE="$2"; shift 2 ;;
    --no-cleanup)    CLEANUP="false"; shift ;;
    --timeout)       TIMEOUT="$2"; shift 2 ;;
    --dry-run)       DRY_RUN="true"; shift ;;
    -h|--help)       print_usage; exit 0 ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
done

# ---- Resolve namespace (override > chart convention > services.yaml) ---------
# The pg-backup CronJob lives in the DB namespace (chart.dbNamespace =
# <release>-<env>-db), so resolve THAT — not the app namespace.
resolve_namespace() {
  [ -n "${NAMESPACE}" ] && return 0
  if [ -n "${ENV_NAME}" ]; then
    NAMESPACE="$(lib_helm_ns db "${ENV_NAME}")"
    [ -n "${NAMESPACE}" ] || die "could not resolve db namespace for env '${ENV_NAME}'"
    return 0
  fi
  # No --env: derive the db namespace from services.yaml (append -db if missing).
  need_cmd python3
  local base
  base="$(python3 - "${LIB_SERVICES_YAML}" <<'PY'
import sys, yaml
print((yaml.safe_load(open(sys.argv[1])) or {}).get("namespace", "") or "")
PY
)"
  [ -n "${base}" ] || die "no namespace: pass --namespace or --env"
  [[ "${base}" == *-db ]] || base="${base}-db"
  NAMESPACE="${base}"
}

# ---- Cleanup of whatever we created (best-effort, on any exit) ----------------
cleanup_backup() {
  [ "${CLEANUP}" = "true" ] || return 0
  if [ -n "${FETCH_POD}" ]; then
    run kubectl delete pod -n "${NAMESPACE}" "${FETCH_POD}" >/dev/null 2>&1 || true
  fi
  if [ -n "${JOB_NAME}" ]; then
    run kubectl delete job -n "${NAMESPACE}" "${JOB_NAME}" >/dev/null 2>&1 || true
  fi
}
# Only auto-cleanup on success; on error we keep things for debugging unless the
# trap path calls this. We gate with BACKUP_OK so a failure retains evidence.
BACKUP_OK="false"
trap '[ "${BACKUP_OK}" = "true" ] && cleanup_backup || log_warn "kept Job/fetch pod for debugging: ${JOB_NAME:-?}/${FETCH_POD:-?}"' EXIT

# ---- Preflight --------------------------------------------------------------
need_cmd kubectl
resolve_namespace
# A unique, timestamped name for the one-shot Job + fetch pod.
STAMP="$(date +%Y%m%d-%H%M%S)"
JOB_NAME="${CRONJOB}-ondemand-${STAMP}"
FETCH_POD="backup-fetch-${STAMP}"

# ---- 1. trigger -------------------------------------------------------------
lib_banner "db-backup" "ns=${NAMESPACE}  cronjob=${CRONJOB}  job=${JOB_NAME}"
log_step "kubectl create job -n ${NAMESPACE} ${JOB_NAME} --from=cronjob/${CRONJOB}"
run kubectl create job -n "${NAMESPACE}" "${JOB_NAME}" --from="cronjob/${CRONJOB}"

# ---- 2. wait for completion -------------------------------------------------
log_step "waiting up to ${TIMEOUT}s for ${JOB_NAME} to complete"
if ! run kubectl wait -n "${NAMESPACE}" --for=condition=complete \
        "job/${JOB_NAME}" --timeout="${TIMEOUT}s"; then
  log_err "backup job ${JOB_NAME} did not complete — inspect with:"
  log_meta "  kubectl -n ${NAMESPACE} logs job/${JOB_NAME}"
  die "backup job failed"
fi
log_ok "backup job complete"

# Under --dry-run the commands above were only printed; stop before the fetch
# (which needs real cluster output to locate the dump).
if [ "${DRY_RUN}" = "true" ]; then
  log_warn "dry-run: would mount ${PVC} in ${FETCH_POD}, copy the newest dump to ${DEST}, then clean up"
  exit 0
fi

# ---- 3. fetch the dump off the PVC via a short-lived pod ----------------------
mkdir -p "${DEST}"
lib_sep "fetch from PVC ${PVC} @ ${MOUNT}"
log_step "starting fetch pod ${FETCH_POD} (image ${FETCH_IMAGE})"
run kubectl run "${FETCH_POD}" -n "${NAMESPACE}" --image="${FETCH_IMAGE}" \
    --restart=Never --overrides="{
      \"spec\":{
        \"securityContext\":{\"runAsNonRoot\":true,\"runAsUser\":10001,\"runAsGroup\":999,\"fsGroup\":999},
        \"containers\":[{\"name\":\"fetch\",\"image\":\"${FETCH_IMAGE}\",
          \"command\":[\"sleep\",\"3600\"],
          \"securityContext\":{\"allowPrivilegeEscalation\":false,\"capabilities\":{\"drop\":[\"ALL\"]}},
          \"volumeMounts\":[{\"name\":\"backup\",\"mountPath\":\"${MOUNT}\"}]}],
        \"volumes\":[{\"name\":\"backup\",\"persistentVolumeClaim\":{\"claimName\":\"${PVC}\"}}]
      }}" >/dev/null
log_step "waiting for fetch pod to be Ready"
run kubectl wait -n "${NAMESPACE}" --for=condition=ready "pod/${FETCH_POD}" --timeout="${TIMEOUT}s"

log_step "listing backups in ${MOUNT}"
run kubectl exec -n "${NAMESPACE}" "${FETCH_POD}" -- ls -1 "${MOUNT}" || true

# Copy the newest .sql.gz (or .dump) out of the volume.
dump="$(run kubectl exec -n "${NAMESPACE}" "${FETCH_POD}" -- \
          sh -c 'ls -1t '"${MOUNT}"'/*.sql.gz '"${MOUNT}"'/*.dump 2>/dev/null | head -n1' || true)"
[ -n "${dump}" ] || die "no backup file found in ${MOUNT} (check the job ran)"
log_info "fetching ${dump}"
run kubectl cp -n "${NAMESPACE}" "${FETCH_POD}:${dump}" "${DEST}/"
DEST_FILE="${DEST}/$(basename "${dump}")"
if [ -f "${DEST_FILE}" ]; then
  log_ok "backup saved to ${DEST_FILE}"
else
  die "copy failed"
fi

# ---- 4. done ---------------------------------------------------------------
BACKUP_OK="true"   # arms the EXIT trap to clean up the Job + fetch pod
log_ok "db-backup complete → ${DEST_FILE}"
[ "${CLEANUP}" = "true" ] && log_meta "Job + fetch pod removed (--no-cleanup to keep them)"
