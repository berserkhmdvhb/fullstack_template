#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# ===========================================================================
# certs-local.sh — wire self-signed TLS for local HTTPS (cert-manager)
# ===========================================================================
# Purpose
#   Give the local stack real HTTPS on your laptop using cert-manager's bundled
#   `selfsigned` ClusterIssuer (k8s/local/selfsigned-issuer.yaml). It:
#     1. applies the ClusterIssuer (cluster-scoped)
#     2. reads the chart's values.<env>.yaml Ingress TLS secretName + hosts
#     3. creates a Certificate (in the app namespace) that fills that secret,
#        signed by `selfsigned`, for those hosts (+ 127.0.0.1)
#     4. waits for cert-manager to issue the secret
#
# LOCAL-DEV ONLY. Never point this at production — production uses letsencrypt-prod.
# After setup, map the hosts to 127.0.0.1 (e.g. in /etc/hosts) and trust the CA:
#   mkcert -install && mkcert api.local app.local 127.0.0.1
#
# Inputs (flags)
#   -e, --env ENV          dev|qa|production — namespace + Ingress from values.<env>.yaml
#   -n, --namespace NS     app namespace (default: <release>-<env>)
#       --issuer NAME      ClusterIssuer to use (default: selfsigned)
#       --ca               also create the selfsigned ClusterIssuer (default: on)
#       --no-ca            assume the ClusterIssuer already exists
#       --timeout SECS     wait timeout (default: 60)
#       --dry-run          print each command, execute nothing
#   -h, --help
# ===========================================================================
if [ -z "${BASH_VERSION:-}" ]; then exec bash "$0" "$@"; fi
set -Eeuo pipefail
set -o errtrace

# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_lib.sh"
trap 'lib_err_trap "certs-local" "${LINENO}" "${BASH_COMMAND}" "$?"' ERR

# ---- Defaults ---------------------------------------------------------------
ENV_NAME="dev"
NAMESPACE=""
ISSUER="selfsigned"
DO_CA="true"
TIMEOUT="60"
DRY_RUN="${DRY_RUN:-false}"
CERT_NAME=""
SECRET_NAME=""

print_usage() {
  cat <<'EOF'
Usage: certs-local.sh [options]

Options:
  -e, --env ENV         dev|qa|production — namespace + Ingress from values.<env>.yaml
  -n, --namespace NS    app namespace (default: <release>-<env>)
      --issuer NAME     ClusterIssuer to use (default: selfsigned)
      --no-ca           assume the ClusterIssuer already exists
      --timeout SECS    wait timeout (default: 60)
      --dry-run         print each command, execute nothing
  -h, --help            show this help

Examples:
  ./scripts/certs-local.sh --env dev
  ./scripts/certs-local.sh --env dev --no-ca      # issuer already applied
  ./scripts/certs-local.sh --env dev --dry-run
EOF
}

# ---- Flags ------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    -e|--env)        ENV_NAME="$2"; shift 2 ;;
    -n|--namespace)  NAMESPACE="$2"; shift 2 ;;
    --issuer)        ISSUER="$2"; shift 2 ;;
    --no-ca)         DO_CA="false"; shift ;;
    --timeout)       TIMEOUT="$2"; shift 2 ;;
    --dry-run)       DRY_RUN="true"; shift ;;
    -h|--help)       print_usage; exit 0 ;;
    *) die "Unknown option: $1 (try --help)" ;;
  esac
done

# ---- Resolve namespace (override > chart convention) ------------------------
resolve_target() {
  if [ -z "${NAMESPACE}" ]; then
    NAMESPACE="$(lib_helm_ns app "${ENV_NAME}")"
    [ -n "${NAMESPACE}" ] || die "could not resolve namespace for env '${ENV_NAME}'"
  fi
}

# ---- Read the chart's values.<env>.yaml Ingress TLS secretName + hosts -------
read_ingress_tls() {                    # sets SECRET_NAME, CERT_NAME, DNS_NAMES[]
  need_cmd yq
  local vf; vf="$(lib_helm_values_file "${ENV_NAME}")"
  SECRET_NAME="$(yq eval '.ingress.tlsSecretName // ""' "${vf}" 2>/dev/null)"
  [ -n "${SECRET_NAME}" ] || die "no ingress.tlsSecretName in ${vf}"
  # hosts is a map (api/app/root); take its values as the DNS names.
  mapfile -t DNS_NAMES < <(yq eval '.ingress.hosts | to_entries | .[].value' "${vf}" 2>/dev/null \
    | grep -vE '^-$|^null$|^$')
  [ "${#DNS_NAMES[@]}" -gt 0 ] || die "no ingress.hosts in ${vf}"
  CERT_NAME="${SECRET_NAME}-cert"
}

# ---- Preflight --------------------------------------------------------------
need_cmd kubectl yq
[ "${DRY_RUN}" = "true" ] || require_kube
resolve_target
read_ingress_tls

# ---- Apply the ClusterIssuer (unless --no-ca) --------------------------------
if [ "${DO_CA}" = "true" ]; then
  lib_banner "certs-local (setup)" "env=${ENV_NAME}  ns=${NAMESPACE}  issuer=${ISSUER}"
  log_step "applying selfsigned ClusterIssuer"
  run kubectl apply -f "${LIB_REPO_ROOT}/k8s/local/selfsigned-issuer.yaml"
else
  lib_banner "certs-local (setup)" "env=${ENV_NAME}  ns=${NAMESPACE}  issuer=${ISSUER} (existing)"
fi

# ---- Emit + apply the Certificate ------------------------------------------
lib_sep "certificate → secret ${SECRET_NAME}"
log_info "hosts: ${DNS_NAMES[*]} (+ 127.0.0.1)"

apply_certificate() {                    # renders the Certificate to stdout
  local d dns_yaml="      - 127.0.0.1"
  for d in "${DNS_NAMES[@]}"; do dns_yaml+=$'\n'"      - ${d}"; done
  cat <<EOF
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: ${CERT_NAME}
  namespace: ${NAMESPACE}
  labels:
    app.kubernetes.io/managed-by: certs-local.sh
spec:
  secretName: ${SECRET_NAME}
  duration: 8760h
  renewBefore: 360h
  isCA: false
  commonName: ${DNS_NAMES[0]}
  dnsNames:
${dns_yaml}
  ipAddresses:
      - 127.0.0.1
  privateKey:
    algorithm: ECDSA
    size: 256
  issuerRef:
    name: ${ISSUER}
    kind: ClusterIssuer
EOF
}

if [ "${DRY_RUN}" = "true" ]; then
  printf '%s%s⇢ would apply Certificate %s (secret %s) %s%s\n' \
    "$(_lib_ts)" "${C_YEL}" "${CERT_NAME}" "${SECRET_NAME}" "(see below)" "${C_RST}"
  apply_certificate | sed 's/^/      /'
  exit 0
fi

apply_certificate | run kubectl apply -f -

# ---- Wait for cert-manager to populate the secret ---------------------------
log_step "waiting up to ${TIMEOUT}s for secret/${SECRET_NAME}"
if run kubectl wait -n "${NAMESPACE}" --for=condition=Ready \
      "certificate/${CERT_NAME}" --timeout="${TIMEOUT}s" >/dev/null; then
  log_ok "certificate issued → TLS secret ${SECRET_NAME} ready in ${NAMESPACE}"
  log_meta "trust the CA for these hosts, then add them to /etc/hosts → 127.0.0.1:"
  for d in "${DNS_NAMES[@]}"; do log_meta "    ${d}"; done
else
  log_warn "certificate not Ready yet — check: kubectl -n ${NAMESPACE} describe certificate ${CERT_NAME}"
  exit 1
fi
