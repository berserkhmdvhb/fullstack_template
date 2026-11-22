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
#   - per namespace, the deployer's Role and RoleBinding (least privilege, namespace-scoped, no RBAC);
#   - the app's own Role and RoleBinding, rendered from the chart's templates/rbac.yaml (one source);
#   - with gmpClusterRules: true, the chart's ClusterRules (Google Managed Prometheus; cluster-scoped).
# The app chart then runs with namespaces.create=false and rbac.create=false (its defaults).
# See wiki/infrastructure.md.
#
# Usage: k8s-bootstrap.sh <dev|qa|production|local> [--release NAME] [--namespace NS] [--db|--no-db]
#                         [--values FILE]... [--chart-values FILE]...
#   --release NAME   the Helm release name (SERVICE_NAME; default your-service-name)
#   --namespace NS   the app namespace (default <release>-<env>)
#   --db / --no-db   create the database namespace or not (default: the env config)
#   --values FILE    an extra bootstrap config file, layered last (repeatable)
#   --chart-values FILE  an extra chart values file the deploy layers over values.<env>.yaml
#                    (repeatable); read for the chart's monitoring.flavour and the app's names
# Needs helm and yq (scripts/tools.sh install helm4 yq), used only to render and read the config.
# ===========================================================================
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIR="$ROOT/infra/k8s/bootstrap"
CHART="$ROOT/charts/your-service-name"

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

args=() files=("$DIR/values.yaml" "$DIR/values.${ENV_NAME}.yaml") release="" namespace=""
chart_files=("$CHART/values.yaml" "$CHART/values.${ENV_NAME}.yaml")
while [[ $# -gt 0 ]]; do
  case "$1" in
    --release|--namespace)
      [[ $# -ge 2 ]] || die "$1 needs a value"
      label "$1" "$2"
      if [[ "$1" == --release ]]; then release="$2"; else namespace="$2"; fi
      shift 2 ;;
    --chart-values)
      [[ $# -ge 2 && -f "$2" ]] || die "--chart-values needs an existing file"
      chart_files+=("$2"); shift 2 ;;
    --db) args+=(--set database.inCluster=true); shift ;;
    --no-db) args+=(--set database.inCluster=false); shift ;;
    --values)
      [[ $# -ge 2 && -f "$2" ]] || die "--values needs an existing file"
      files+=("$2"); args+=(-f "$2"); shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option '$1' (try --help)" ;;
  esac
done

command -v helm >/dev/null 2>&1 || die "helm is required to render (scripts/tools.sh install helm4)"
command -v yq >/dev/null 2>&1 || die "yq is required to read the config (scripts/tools.sh install yq)"
# One config value: the bootstrap files merged in order (values.yaml, values.<env>.yaml, --values).
conf() { yq ea ". as \$i ireduce ({}; . * \$i) | .$1 // \"\"" "${files[@]}"; }

release="${release:-$(conf release)}"
label release "$release"
namespace="${namespace:-$(conf namespace)}"
namespace="${namespace:-${release}-$(conf environment)}"
label namespace "$namespace"
chart_args=(-f "$CHART/values.${ENV_NAME}.yaml" -n "$namespace" --set rbac.create=true)
for f in "${chart_files[@]:2}"; do chart_args+=(-f "$f"); done
for key in fullname:fullnameOverride nameOverride:nameOverride serviceAccountName:serviceAccount.name; do
  value="$(conf "${key%%:*}")"
  [[ -z "$value" ]] || { label "${key%%:*}" "$value"; chart_args+=(--set-string "${key##*:}=$value"); }
done

# Render both before printing either, so a failure prints nothing.
boot="$(helm template bootstrap "$DIR" -f "$DIR/values.${ENV_NAME}.yaml" "${args[@]}" \
  --set-string "release=$release" --set-string "namespace=$namespace")"
app_rbac="$(helm template "$release" "$CHART" "${chart_args[@]}" --show-only templates/rbac.yaml)"
# gmpClusterRules: the chart's cluster-scoped ClusterRules (Google Managed Prometheus), which the
# deployer cannot apply. Same pattern as the app's Role: rendered from the chart, applied here.
cluster_rules=""
if [[ "$(conf gmpClusterRules)" == "true" ]]; then
  # Only for an environment whose chart flavour is gmp: with the operator flavour the PrometheusRule
  # already holds these alerts, and forcing gmp here would run them twice.
  flavour="$(yq ea ". as \$i ireduce ({}; . * \$i) | .monitoring.flavour // \"prometheus-operator\"" "${chart_files[@]}")"
  [[ "$flavour" == "gmp" ]] ||
    die "gmpClusterRules is true but the chart's monitoring.flavour for ${ENV_NAME} is '${flavour}', not gmp: set monitoring.flavour: gmp in values.${ENV_NAME}.yaml (or --chart-values), or turn gmpClusterRules off"
  cluster_rules="$(helm template "$release" "$CHART" "${chart_args[@]}" \
    --set monitoring.gmp.clusterRules.enabled=true --show-only templates/gmp-cluster-rules.yaml)"
fi
printf '%s\n%s\n' "$boot" "$app_rbac"
[[ -z "$cluster_rules" ]] || printf '%s\n' "$cluster_rules"
