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
#   - with sharedServiceAccount: true (preview), the app's ServiceAccount, rendered from the chart's
#     templates/serviceaccount.yaml: the preview namespace's releases share it;
#   - with gmpClusterRules: true, the chart's ClusterRules (Google Managed Prometheus; cluster-scoped).
# The app chart then runs with namespaces.create=false and rbac.create=false (its defaults).
# See wiki/infrastructure.md.
#
# Usage: k8s-bootstrap.sh <dev|qa|production|local|preview> [--release NAME] [--namespace NS] [--db|--no-db]
#                         [--values FILE]... [--chart-values FILE]...
#                         [--chart-set K=V]... [--chart-set-string K=V]...
#   --release NAME   the Helm release name (SERVICE_NAME; default your-service-name)
#   --namespace NS   the app namespace (default <release>-<env>)
#   --db / --no-db   create the database namespace or not (default: the env config)
#   --values FILE    an extra bootstrap config file, layered last (repeatable)
#   --chart-values FILE  an extra chart values file the deploy layers over values.<env>.yaml
#                    (repeatable); read for the chart's monitoring.flavour and the app's names
#   --chart-set K=V, --chart-set-string K=V  a --set / --set-string the deploy passes to the chart,
#                    rendered after the chart values files, as helm applies them (repeatable);
#                    with gmpClusterRules, monitoring.flavour is refused here: use --chart-values
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
  dev|qa|production|local|preview) ENV_NAME="$1"; shift ;;
  *) die "unknown environment '$1' (dev, qa, production, local or preview)" ;;
esac

args=() files=("$DIR/values.yaml" "$DIR/values.${ENV_NAME}.yaml") release="" namespace=""
chart_files=("$CHART/values.yaml" "$CHART/values.${ENV_NAME}.yaml") chart_sets=()
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
    --chart-set|--chart-set-string)
      [[ $# -ge 2 && "$2" == *=* ]] || die "$1 needs KEY=VALUE"
      chart_sets+=("--${1#--chart-}" "$2"); shift 2 ;;
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
chart_args=(-f "$CHART/values.${ENV_NAME}.yaml" -n "$namespace")
for f in "${chart_files[@]:2}"; do chart_args+=(-f "$f"); done
for key in fullname:fullnameOverride nameOverride:nameOverride serviceAccountName:serviceAccount.name; do
  value="$(conf "${key%%:*}")"
  [[ -z "$value" ]] || { label "${key%%:*}" "$value"; chart_args+=(--set-string "${key##*:}=$value"); }
done
# The bootstrap's own sets come last: helm applies the last --set of a key, so a forwarded
# rbac.create=false cannot drop the app's Role.
chart_args+=("${chart_sets[@]}" --set rbac.create=true)
gmp_rules="$(conf gmpClusterRules)"
# The flavour is read from the chart values files below; a --set of it is not, so refuse one.
if [[ "$gmp_rules" == "true" ]]; then
  for ((i = 1; i < ${#chart_sets[@]}; i += 2)); do
    [[ ! "${chart_sets[i]}" =~ (^|,)monitoring\.flavour= ]] ||
      die "monitoring.flavour cannot be a --chart-set: the bootstrap reads it from the chart values files, so pass it in one with --chart-values"
  done
fi

# Render both before printing either, so a failure prints nothing.
boot="$(helm template bootstrap "$DIR" -f "$DIR/values.${ENV_NAME}.yaml" "${args[@]}" \
  --set-string "release=$release" --set-string "namespace=$namespace")"
app_rbac="$(helm template "$release" "$CHART" "${chart_args[@]}" --show-only templates/rbac.yaml)"
# sharedServiceAccount (preview): the ServiceAccount the namespace's releases share; the app's
# RoleBinding above binds it. The chart there runs with serviceAccount.create false.
if [[ "$(conf sharedServiceAccount)" == "true" ]]; then
  app_rbac+=$'\n'"$(helm template "$release" "$CHART" "${chart_args[@]}" --set serviceAccount.create=true \
    --show-only templates/serviceaccount.yaml)"
fi
# gmpClusterRules: the chart's cluster-scoped ClusterRules (Google Managed Prometheus), which the
# deployer cannot apply. Same pattern as the app's Role: rendered from the chart, applied here.
cluster_rules=""
if [[ "$gmp_rules" == "true" ]]; then
  # Only for an environment whose chart flavour is gmp: with the operator flavour the PrometheusRule
  # already holds these alerts, and forcing gmp here would run them twice.
  flavour="$(yq ea ". as \$i ireduce ({}; . * \$i) | .monitoring.flavour // \"prometheus-operator\"" "${chart_files[@]}")"
  [[ "$flavour" == "gmp" ]] ||
    die "gmpClusterRules is true but the chart's monitoring.flavour for ${ENV_NAME} is '${flavour}', not gmp: set monitoring.flavour: gmp in values.${ENV_NAME}.yaml (or --chart-values), or turn gmpClusterRules off"
  cluster_rules="$(helm template "$release" "$CHART" "${chart_args[@]}" \
    --set monitoring.gmp.clusterRules.enabled=true --show-only templates/gmp-cluster-rules.yaml)"
  # The chart's deploy guard refuses the placeholder runbook link in production, but this render is
  # not the deploy (and runs without guards.rejectPlaceholders), so refuse it here too.
  if [[ "$ENV_NAME" == production ]] && grep -Eq 'runbook_url: "[^"]*github\.com/your-org/your-repo/' <<<"$cluster_rules"; then
    die "the ClusterRules' runbook links still use the placeholder monitoring.runbookUrl (github.com/your-org/your-repo/...): set it in values.production.yaml (or --chart-values / --chart-set)"
  fi
fi
printf '%s\n%s\n' "$boot" "$app_rbac"
[[ -z "$cluster_rules" ]] || printf '%s\n' "$cluster_rules"
