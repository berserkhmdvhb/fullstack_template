#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# ===========================================================================
# migrate-to-bootstrap.sh — move a release made by an older chart onto the admin bootstrap
# ===========================================================================
# An older chart rendered the namespaces and the app's Role and RoleBinding itself. The deployer
# has no rights on them, and the current chart stops rendering them, so Helm would delete them
# (the namespaces with everything in them). A cluster admin runs this once per environment, with
# the chart checkout the deploy uses and the values its deploy passes:
#   1. one upgrade that still renders them (namespaces.create=true, rbac.create=true): they gain
#      helm.sh/resource-policy: keep;
#   2. the bootstrap (scripts/k8s-bootstrap.sh <env> | kubectl apply -f -), applied over them;
#   3. one upgrade with both off: the release stops recording them, and keep leaves them in place;
#   4. a check that the release no longer holds a Namespace, Role or RoleBinding.
# The upgrades pass the current chart's values (values.<env>.yaml, then --values and --set), never
# the release's stored ones: an older release stored keys the current schema refuses. It is
# idempotent: a release that holds none of those objects is left alone, and a run stopped half-way
# can run again. See wiki/infrastructure.md (namespace bootstrap).
#
# Usage: migrate-to-bootstrap.sh <dev|qa|production|local> [--release NAME] [--namespace NS]
#                                [--values FILE]... [--set K=V]... [--set-string K=V]...
#                                [--timeout DURATION] [--dry-run] [--yes]
#   --release NAME   the Helm release (default your-service-name)
#   --namespace NS   the app namespace (default <release>-<env>)
#   --values, --set, --set-string  the deploy's own values after values.<env>.yaml: the secret
#                    values (or secrets.existingSecret) and the images it deploys (repeatable)
#   --timeout        each upgrade's --wait (default 10m)
#   --dry-run        print every step, contact no cluster
#   --yes            no confirmation (needed without a terminal)
# Needs helm, kubectl and yq, and cluster-admin rights in the current kube context.
# ===========================================================================
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART="$ROOT/charts/your-service-name"
HELM_HISTORY_MAX=5

usage() { sed -n '/^# Usage:/,/^# Needs/s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; }
die() { echo "migrate-to-bootstrap: $*" >&2; exit "${2:-1}"; }
label() { [[ "$2" =~ ^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$ ]] || die "$1 must be a DNS-1123 label, got '$2'" 2; }

[[ $# -ge 1 ]] || { usage >&2; exit 2; }
case "$1" in
  -h|--help) usage; exit 0 ;;
  dev|qa|production|local) ENV_NAME="$1"; shift ;;
  *) die "unknown environment '$1' (dev, qa, production or local)" 2 ;;
esac
release=your-service-name namespace="" timeout=10m dry_run=false yes=false
values=(-f "$CHART/values.$ENV_NAME.yaml")
while [[ $# -gt 0 ]]; do
  case "$1" in
    --release|--namespace|--timeout|--values|--set|--set-string)
      [[ $# -ge 2 ]] || die "$1 needs a value" 2
      case "$1" in
        --release) label release "$2"; release="$2" ;;
        --namespace) label namespace "$2"; namespace="$2" ;;
        --timeout) timeout="$2" ;;
        --values) [[ -f "$2" ]] || die "--values needs an existing file" 2; values+=(-f "$2") ;;
        *) values+=("$1" "$2") ;;
      esac
      shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    --yes) yes=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option '$1' (try --help)" 2 ;;
  esac
done
namespace="${namespace:-$release-$ENV_NAME}"
upgrade=(helm upgrade "$release" "$CHART" -n "$namespace" "${values[@]}" --history-max "$HELM_HISTORY_MAX" --wait --timeout "$timeout")
boot=("$ROOT/scripts/k8s-bootstrap.sh" "$ENV_NAME" --release "$release" --namespace "$namespace")

if [[ "$dry_run" == true ]]; then
  echo "dry run: if $release in $namespace holds a Namespace, Role or RoleBinding, as a cluster admin:"
  echo "  1. ${upgrade[*]} --set namespaces.create=true --set rbac.create=true"
  echo "  2. ${boot[*]#"$ROOT/"} | kubectl apply -f -"
  echo "  3. ${upgrade[*]} --set namespaces.create=false --set rbac.create=false"
  echo "  4. helm get manifest $release -n $namespace: no Namespace, Role or RoleBinding left"
  exit 0
fi

for tool in helm kubectl yq; do command -v "$tool" >/dev/null 2>&1 || die "$tool is required"; done
holds() { # the release's stored manifest holds a Namespace, Role or RoleBinding
  local manifest rc=0
  manifest="$(helm get manifest "$release" -n "$namespace" 2>&1)" || rc=$?
  if [[ "$rc" -ne 0 ]]; then
    grep -q "release: not found" <<<"$manifest" && die "no release $release in $namespace: nothing to migrate"
    die "could not read release $release in $namespace: $manifest"
  fi
  grep -Eqx 'kind: (Namespace|Role|RoleBinding)' <<<"$manifest"
}
if ! holds; then
  echo "migrate-to-bootstrap: $release in $namespace holds no Namespace, Role or RoleBinding: nothing to do"
  exit 0
fi
# The steps create namespaces, quotas and RBAC: a cluster admin's rights, checked before any change.
for check in "create namespaces" "create resourcequotas -n $namespace" "create roles -n $namespace" \
  "create rolebindings -n $namespace" "escalate roles -n $namespace"; do
  # shellcheck disable=SC2086 # the words are kubectl's arguments
  [[ "$(kubectl auth can-i $check 2>/dev/null || true)" == yes ]] ||
    die "this identity cannot '$check': run it as a cluster admin"
done
if [[ "$yes" != true ]]; then
  [[ -t 0 ]] || die "no terminal to confirm on: pass --yes to migrate $release in $namespace" 2
  read -r -p "Migrate release $release in $namespace onto the bootstrap? Type the release name: " answer
  [[ "$answer" == "$release" ]] || die "not confirmed" 2
fi

echo "==> 1. keep: an upgrade that still renders them, so they gain helm.sh/resource-policy: keep"
"${upgrade[@]}" --set namespaces.create=true --set rbac.create=true
echo "==> 2. the bootstrap"
"${boot[@]}" | kubectl apply -f -
echo "==> 3. switch: an upgrade with both off; keep leaves them in place"
"${upgrade[@]}" --set namespaces.create=false --set rbac.create=false
! holds || die "the release $release still holds a Namespace, Role or RoleBinding"
echo "migrate-to-bootstrap: migrated: $release in $namespace; the deployer's upgrades touch none of them"
