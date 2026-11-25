#!/usr/bin/env bash
# kubeconform.sh — validate every render of the chart and of the admin bootstrap, strictly and offline.
#
# The render matrix: each environment's values file (values.<env>.yaml) x each edge mode
# (ci/<mode>-values.yaml) x each monitoring flavour, with the monitors and the rules on (the gmp
# column also renders the cluster-scoped ClusterRules); then each environment with every optional
# object on (the WAF, the GKE Gateway policies, the ExternalSecret, the Namespaces, the RBAC, the
# external database stub, the Grafana dashboards), with the Cloud SQL Auth Proxy, and the bootstrap
# (scripts/k8s-bootstrap.sh <env>, and once with its GMP ClusterRules); then the External Secrets
# stores the docs apply (deploy/external-secrets/secretstore-*.yaml).
#
# kubeconform -strict reads local schemas only, never the network: the Kubernetes kinds from
# .tools/k8s-schemas (generated once from the pinned OpenAPI spec, tools/pins.tsv row k8s-openapi,
# by scripts/ci/kube_schemas.py core), the CRDs from charts/your-service-name/ci/crds (committed;
# `make crd-schemas` regenerates them). A kind with no schema fails (no -ignore-missing-schemas).
#
# Usage: scripts/ci/kubeconform.sh. Needs helm, kubeconform, yq (the bootstrap) and python3 >= 3.10:
# scripts/tools.sh install helm4 kubeconform yq k8s-openapi. Env: TOOLS_DIR (default ./.tools).
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHART="$ROOT/charts/your-service-name"
TOOLS_DIR="${TOOLS_DIR:-$ROOT/.tools}"
RELEASE=your-service-name
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

die() { echo "kubeconform: $*" >&2; exit 1; }
# shellcheck source=/dev/null
source "$ROOT/scripts/_sha256.sh"

# The pinned Kubernetes OpenAPI spec (the chart's kubeVersion floor), checked against its pin.
read -r KUBE_VERSION SPEC_SHA < <(awk -F'\t' '$1 == "k8s-openapi" {print $2, $5}' "$ROOT/tools/pins.tsv")
[[ -n "${KUBE_VERSION:-}" ]] || die "tools/pins.tsv has no k8s-openapi row"
SPEC="$TOOLS_DIR/opt/k8s-openapi-$KUBE_VERSION/k8s-openapi"
[[ -f "$SPEC" ]] || die "$SPEC is missing: run scripts/tools.sh install k8s-openapi"
[[ "$(sha256 "$SPEC")" == "$SPEC_SHA" ]] || die "$SPEC does not match its pin; reinstall it"
for tool in helm kubeconform yq python3; do
  command -v "$tool" >/dev/null || die "$tool is required (scripts/tools.sh install helm4 kubeconform yq)"
done

# The Kubernetes schemas, generated once per spec and generator (the stamp holds both hashes).
SCHEMAS="$TOOLS_DIR/k8s-schemas"
CORE="$SCHEMAS/v${KUBE_VERSION}-standalone-strict"
stamp="$(cat "$SPEC" "$ROOT/scripts/ci/kube_schemas.py" | sha256)"
if [[ "$(cat "$CORE/.stamp" 2>/dev/null || true)" != "$stamp" ]]; then
  rm -rf "$CORE" "$CORE.part"
  python3 -I "$ROOT/scripts/ci/kube_schemas.py" core "$SPEC" "$CORE.part"
  echo "$stamp" > "$CORE.part/.stamp"
  mv "$CORE.part" "$CORE"
fi

# The only schema locations: two local path templates. Never `default` or a URL.
KUBECONFORM_ARGS=(
  -strict -summary -output text -kubernetes-version "$KUBE_VERSION"
  -schema-location "$SCHEMAS/{{ .NormalizedKubernetesVersion }}-standalone{{ .StrictSuffix }}/{{ .ResourceKind }}{{ .KindSuffix }}.json"
  -schema-location "$CHART/ci/crds/{{ .Group }}/{{ .ResourceKind }}_{{ .ResourceAPIVersion }}.json"
)

renders=0 failed=()
# check NAME COMMAND...: render with COMMAND, then validate the output.
check() {
  local name="$1" out="$WORK/render.yaml" summary
  shift
  renders=$((renders + 1))
  if ! "$@" > "$out" 2> "$WORK/render.err"; then
    echo "FAIL $name: the render failed"; cat "$WORK/render.err"; failed+=("$name"); return 0
  fi
  if summary="$(kubeconform "${KUBECONFORM_ARGS[@]}" "$out" 2>&1)"; then
    echo "ok   $name: ${summary##*- }"
  else
    echo "FAIL $name"; echo "$summary"; failed+=("$name")
  fi
}

# The schemas must catch a mistake: an unknown field in a core kind and in a CRD, and a kind
# without a schema, each fail. Otherwise every pass below would mean nothing.
canary() {
  printf '%s\n' "$2" > "$WORK/canary.yaml"
  if kubeconform "${KUBECONFORM_ARGS[@]}" "$WORK/canary.yaml" > /dev/null 2>&1; then
    die "the $1 canary validated; the schemas are not strict"
  fi
}
canary "core kind" $'apiVersion: v1\nkind: ConfigMap\nmetadata: {name: c}\ndata: {a: b}\nunknownField: 1'
canary "CRD" $'apiVersion: gateway.networking.k8s.io/v1\nkind: HTTPRoute\nmetadata: {name: r}\nspec: {unknownField: 1}'
canary "missing schema" $'apiVersion: example.test/v1\nkind: Widget\nmetadata: {name: w}'

monitors=(--set monitoring.serviceMonitor=true --set monitoring.prometheusRule=true)
# The secret sources are exclusive: the ExternalSecret replaces the preview's existingSecret, and the
# in-cluster database takes its password from a named Secret here (the environments' own renders
# above sync it through a second ExternalSecret instead).
features=(
  --set waf.enabled=true --set edge.gateway.gke.enabled=true --set rbac.create=true
  --set namespaces.create=true --set externalDatabase.manageSecret=true
  --set secrets.externalSecret.enabled=true --set secrets.existingSecret=
  --set postgresql.existingSecret=pg-credentials --set observability.tracing.enabled=true
  --set observability.tracing.endpoint=http://collector.monitoring.svc:4318
  --set monitoring.dashboards.enabled=true --set monitoring.dashboards.folder=Services "${monitors[@]}"
)
proxy=(
  --set postgresql.enabled=false --set config.DB_SSLMODE=disable --set cloudSqlProxy.enabled=true
  --set "cloudSqlProxy.egressCIDRs[0]=199.36.153.8/30" "${monitors[@]}"
)
envs=()
for f in "$CHART"/values.*.yaml; do
  env="${f##*/values.}"; envs+=("${env%.yaml}")
done
modes=()
for f in "$CHART"/ci/*-values.yaml; do
  mode="${f##*/}"; modes+=("${mode%-values.yaml}")
done

for env in "${envs[@]}"; do
  t=(helm template "$RELEASE" "$CHART" -n "$RELEASE-$env" -f "$CHART/values.$env.yaml")
  for mode in "${modes[@]}"; do
    check "$env $mode prometheus-operator" "${t[@]}" -f "$CHART/ci/$mode-values.yaml" "${monitors[@]}"
    check "$env $mode gmp" "${t[@]}" -f "$CHART/ci/$mode-values.yaml" "${monitors[@]}" \
      --set monitoring.flavour=gmp --set monitoring.gmp.clusterRules.enabled=true
  done
  check "$env every optional object" "${t[@]}" -f "$CHART/ci/gateway-values.yaml" "${features[@]}"
  check "$env cloud sql proxy" "${t[@]}" "${proxy[@]}"
  check "$env bootstrap" "$ROOT/scripts/k8s-bootstrap.sh" "$env"
done
printf 'gmpClusterRules: true\n' > "$WORK/bootstrap-gmp.yaml"
# A stand-in runbook URL (no scheme: this script names no URL): the production bootstrap refuses
# ClusterRules with the placeholder one.
printf 'monitoring: {flavour: gmp, runbookUrl: "git.svc.test/ops/runbook.md"}\n' \
  > "$WORK/chart-gmp.yaml"
check "production bootstrap with the gmp ClusterRules" "$ROOT/scripts/k8s-bootstrap.sh" production \
  --values "$WORK/bootstrap-gmp.yaml" --chart-values "$WORK/chart-gmp.yaml"
for f in "$ROOT"/deploy/external-secrets/secretstore-*.yaml; do
  check "store ${f#"$ROOT"/}" cat "$f"
done

if (( ${#failed[@]} )); then
  die "${#failed[@]} of $renders renders failed: ${failed[*]}"
fi
echo "kubeconform: all $renders renders valid (strict, offline, Kubernetes $KUBE_VERSION)"
