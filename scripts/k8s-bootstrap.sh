#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# ===========================================================================
# k8s-bootstrap.sh — render the one-time admin bootstrap of one environment
# ===========================================================================
# Prints YAML to stdout and never contacts a cluster. A cluster admin applies it once per
# environment, before the first deploy:
#
#   scripts/k8s-bootstrap.sh dev | kubectl apply -f -
#
# It holds (infra/k8s/bootstrap, values.<env>.yaml over values.yaml):
#   - the app namespace and, with the in-cluster database, the database namespace, both on the
#     restricted Pod Security profile;
#   - the app namespace's ResourceQuota and LimitRange;
#   - per namespace, the deployer's Role and RoleBinding (least privilege, namespace-scoped).
# The app chart then runs with namespaces.create=false (its default). See wiki/infrastructure.md.
#
# Usage: k8s-bootstrap.sh <dev|qa|production|local> [--release NAME] [--namespace NS] [--db|--no-db]
#   --release NAME   the Helm release name (SERVICE_NAME; default your-service-name)
#   --namespace NS   the app namespace (default <release>-<env>)
#   --db / --no-db   create the database namespace or not (default: the env config)
# Needs helm (scripts/tools.sh install helm4), used only to render.
# ===========================================================================
set -Eeuo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/infra/k8s/bootstrap"

usage() { sed -n '/^# Usage:/,/^# Needs/s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; }
die() { echo "k8s-bootstrap: $*" >&2; exit 2; }
# A DNS-1123 label: also keeps the value from smuggling extra --set keys (a comma) into helm.
label() { [[ "$2" =~ ^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$ ]] || die "$1 must be a DNS-1123 label, got '$2'"; }

[[ $# -ge 1 ]] || { usage >&2; exit 2; }
case "$1" in
  -h|--help) usage; exit 0 ;;
  dev|qa|production|local) ENV_NAME="$1"; shift ;;
  *) die "unknown environment '$1' (dev, qa, production or local)" ;;
esac

args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --release|--namespace)
      [[ $# -ge 2 ]] || die "$1 needs a value"
      label "$1" "$2"
      args+=(--set-string "${1#--}=$2"); shift 2 ;;
    --db) args+=(--set database.inCluster=true); shift ;;
    --no-db) args+=(--set database.inCluster=false); shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option '$1' (try --help)" ;;
  esac
done

command -v helm >/dev/null 2>&1 || die "helm is required to render (scripts/tools.sh install helm4)"
exec helm template bootstrap "$DIR" -f "$DIR/values.${ENV_NAME}.yaml" "${args[@]}"
