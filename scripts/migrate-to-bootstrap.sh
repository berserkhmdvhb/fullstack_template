#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# ===========================================================================
# migrate-to-bootstrap.sh — move a release made by an older chart onto the admin bootstrap
# ===========================================================================
# An older chart rendered the namespaces and the app's Role and RoleBinding itself. The deployer
# has no rights on them, and the current chart stops rendering them, so Helm would delete them
# (the namespaces with everything in them). A cluster admin runs this once per environment, with
# the chart checkout the deploy uses and the values its deploy passes:
#   0. every Namespace, PersistentVolumeClaim and Secret the release's deployed revision holds and
#      the current chart no longer renders (an older chart's logging namespace, for one) is
#      annotated helm.sh/resource-policy=keep and migrate-to-bootstrap/kept-by=<namespace>/<release>
#      on the live object, then read back: no upgrade runs unless each carries both. Every stage
#      of that computation is checked, so a failure stops the run rather than empty the set. The
#      objects that carry the marker are listed at the end (on a re-run too) as kept and no longer
#      managed, for a person to decide on;
#   1. one upgrade that still renders the namespaces and the app's RBAC (namespaces.create=true,
#      rbac.create=true): they gain helm.sh/resource-policy: keep;
#   2. the bootstrap (scripts/k8s-bootstrap.sh <env> | kubectl apply -f -), applied over them;
#   3. one upgrade with both off: the release stops recording them, and keep leaves them in place;
#   4. a check that the release no longer holds a Namespace, Role or RoleBinding.
# It deletes nothing itself. The upgrades pass the current chart's values (values.<env>.yaml, then
# --values and --set), never the release's stored ones: an older release stored keys the current
# schema refuses. It reads the release at its deployed revision and refuses a release in any other
# status (failed, pending-*): roll that back first. A release that holds none of those objects is
# left alone, so a re-run is safe. See wiki/infrastructure.md (namespace bootstrap).
#
# Usage: migrate-to-bootstrap.sh <dev|qa|production|local> --context NAME [--release NAME]
#                                [--namespace NS] [--values FILE]... [--set K=V]...
#                                [--set-string K=V]... [--env-values-only]
#                                [--timeout DURATION] [--dry-run] [--yes]
#   --context NAME   the kube context to act on (required; every helm and kubectl call gets it)
#   --release NAME   the Helm release (default your-service-name)
#   --namespace NS   the app namespace (default <release>-<env>; not one ending in another env)
#   --values, --set, --set-string  the deploy's own values after values.<env>.yaml: the secret
#                    values (or secrets.existingSecret) and the images it deploys (repeatable)
#   --env-values-only  the env's values file is all the deploy passes (else one of the above is
#                    required: an upgrade without the secret values would empty the app's Secrets)
#   --timeout        each upgrade's --wait (default 10m)
#   --dry-run        check everything and print the steps and the objects it would keep; change nothing
#   --yes            no confirmation (needed without a terminal)
# Needs helm, kubectl, jq and the pinned yq (tools/pins.tsv), and cluster-admin rights in that context.
# ===========================================================================
set -Eeuo pipefail
shopt -s inherit_errexit

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
release=your-service-name namespace="" context="" timeout=10m dry_run=false yes=false env_only=false
values=(-f "$CHART/values.$ENV_NAME.yaml")
while [[ $# -gt 0 ]]; do
  case "$1" in
    --context|--release|--namespace|--timeout|--values|--set|--set-string)
      [[ $# -ge 2 ]] || die "$1 needs a value" 2
      case "$1" in
        --context) context="$2" ;;
        --release) label release "$2"; release="$2" ;;
        --namespace) label namespace "$2"; namespace="$2" ;;
        --timeout) timeout="$2" ;;
        --values) [[ -f "$2" ]] || die "--values needs an existing file" 2; values+=(-f "$2") ;;
        *) values+=("$1" "$2") ;;
      esac
      shift 2 ;;
    --env-values-only) env_only=true; shift ;;
    --dry-run) dry_run=true; shift ;;
    --yes) yes=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option '$1' (try --help)" 2 ;;
  esac
done
[[ -n "$context" ]] || die "--context NAME is required: the kube context to migrate in" 2
namespace="${namespace:-$release-$ENV_NAME}"
for other in dev qa production local; do
  [[ "$other" == "$ENV_NAME" || "$namespace" != *"-$other" ]] ||
    die "namespace $namespace is $other's, not $ENV_NAME's: pass the env it belongs to" 2
done
[[ "${#values[@]}" -gt 2 || "$env_only" == true ]] ||
  die "pass the values the deploy passes (--values, --set), or --env-values-only if values.$ENV_NAME.yaml is all of them" 2

for tool in helm kubectl jq yq; do command -v "$tool" >/dev/null 2>&1 || die "$tool is required"; done
# The keep set below is computed with yq: only the pinned one (tools/pins.tsv), whose output it knows.
yq_pin="$(awk -F'\t' '$1 == "yq" {print $2}' "$ROOT/tools/pins.tsv")"
yq_version="$(yq --version 2>&1)" || die "yq --version failed: $yq_version"
[[ -n "$yq_pin" && "$yq_version" == *" version v$yq_pin" ]] ||
  die "yq is '$yq_version', not the pinned $yq_pin (tools/pins.tsv): run scripts/tools.sh install yq"
kc() { kubectl --context "$context" "$@"; }
hc() { helm "$@" --kube-context "$context"; }
grep -qxF -- "$context" <<<"$(kubectl config get-contexts -o name 2>/dev/null || true)" ||
  die "context $context is not in the kubeconfig" 2
server="$(kc config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
where="context $context ($server)"
upgrade=(helm upgrade "$release" "$CHART" -n "$namespace" "${values[@]}" --history-max "$HELM_HISTORY_MAX" --wait --timeout "$timeout" --kube-context "$context")
boot=("$ROOT/scripts/k8s-bootstrap.sh" "$ENV_NAME" --release "$release" --namespace "$namespace")

deployed() { # the release's status must be deployed; prints its revision
  local out rc=0 status rev last
  out="$(hc status "$release" -n "$namespace" -o json 2>&1)" || rc=$?
  if [[ "$rc" -ne 0 ]]; then
    grep -q "release: not found" <<<"$out" && die "no release $release in $namespace ($where): nothing to migrate"
    die "could not read release $release in $namespace ($where): $out"
  fi
  status="$(jq -r '.info.status' <<<"$out")" rev="$(jq -r '.version' <<<"$out")"
  if [[ "$status" != deployed ]]; then
    last="$(hc history "$release" -n "$namespace" -o json | jq -r '[.[] | select(.status == "deployed" or .status == "superseded")] | last | .revision // empty')"
    local fix="roll it back to its last good revision first (helm rollback $release ${last:-<revision>} -n $namespace --kube-context $context; helm history lists them), then run this again"
    case "$status" in
      pending-*) die "release $release in $namespace is $status: an operation is in progress or was interrupted. Wait for it; if none is running, $fix" ;;
      *) die "release $release in $namespace is $status, not deployed: $fix" ;;
    esac
  fi
  echo "$rev"
}
holds() { grep -Eqx 'kind: (Namespace|Role|RoleBinding)' <<<"$1"; }
MARKER=migrate-to-bootstrap/kept-by
mark="$namespace/$release"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
objects() { # manifest file, out file: its Namespace, PersistentVolumeClaim and Secret objects, sorted
  yq -N 'select(.kind == "Namespace" or .kind == "PersistentVolumeClaim" or .kind == "Secret")
    | select(.metadata.name != null)
    | .kind + " " + (.metadata.namespace // "'"$namespace"'") + " " + .metadata.name
    | sub("^Namespace [^ ]+ ", "Namespace - ")' "$1" > "$2.unsorted" || die "yq could not read $(basename "$1")"
  LC_ALL=C sort -u "$2.unsorted" > "$2" || die "could not sort the objects of $(basename "$1")"
}
marked() { # what an earlier or this run kept for this release, from the marker on the live objects
  local kind kinds=(Namespace:namespaces PersistentVolumeClaim:persistentvolumeclaims Secret:secrets)
  : > "$work/marked"
  for kind in "${kinds[@]}"; do
    local -a all=(-A); [[ "${kind%%:*}" != Namespace ]] || all=()
    kc get "${kind#*:}" "${all[@]}" --no-headers \
      -o "custom-columns=NS:.metadata.namespace,N:.metadata.name,M:.metadata.annotations.migrate-to-bootstrap/kept-by" \
      > "$work/list" || die "could not list ${kind#*:} in $where"
    awk -v k="${kind%%:*}" -v m="$mark" '$3 == m { print k " " (k == "Namespace" ? $2 : $1 "/" $2) }' \
      "$work/list" >> "$work/marked" || die "could not read the ${kind#*:} list"
  done
  if [[ -s "$work/marked" ]]; then
    echo "migrate-to-bootstrap: kept, now unmanaged: decide by hand (delete or keep each; no release owns it):"
    sed 's/^/    /' "$work/marked"
  fi
}

# The deployed revision, read once its status is checked (every read below is at that revision).
rev="$(deployed)"
stored="$(hc get manifest "$release" -n "$namespace" --revision "$rev")"
if ! holds "$stored"; then
  echo "migrate-to-bootstrap: $release in $namespace ($where) holds no Namespace, Role or RoleBinding: nothing to do"
  marked
  exit 0
fi
# The steps create namespaces, quotas and RBAC: a cluster admin's rights, checked before any change.
for check in "create namespaces" "create resourcequotas -n $namespace" "create roles -n $namespace" \
  "create rolebindings -n $namespace" "escalate roles -n $namespace"; do
  # shellcheck disable=SC2086 # the words are kubectl's arguments
  [[ "$(kc auth can-i $check 2>/dev/null || true)" == yes ]] ||
    die "this identity cannot '$check' in $where: run it as a cluster admin"
done
# What the release holds and the first upgrade's render does not, Helm deletes in that upgrade. Each
# stage is a file with its own status check, so a failure stops the run rather than empty the set.
# (The manifests stay in memory: they hold the release's Secrets.)
printf '%s\n' "$stored" | objects /dev/stdin "$work/old"
render="$(helm template "$release" "$CHART" -n "$namespace" "${values[@]}" --set namespaces.create=true --set rbac.create=true)" ||
  die "the current chart does not render with these values"
printf '%s\n' "$render" | objects /dev/stdin "$work/new"
LC_ALL=C comm -23 "$work/old" "$work/new" > "$work/keep" || die "could not compare the release's objects with the chart's"
mapfile -t keep < "$work/keep"
show() { # kind namespace/name, or kind name for a Namespace
  local obj k n name
  for obj in "${keep[@]}"; do
    read -r k n name <<<"$obj"
    if [[ "$n" == - ]]; then echo "    $k $name"; else echo "    $k $n/$name"; fi
  done
}

echo "migrate-to-bootstrap: $release in $namespace, in $where"
echo "  0. keep (annotate helm.sh/resource-policy=keep and $MARKER=$mark) what the current chart no longer renders:"
if [[ "${#keep[@]}" -gt 0 ]]; then show; else echo "    nothing"; fi
echo "  1. ${upgrade[*]} --set namespaces.create=true --set rbac.create=true"
echo "  2. ${boot[*]#"$ROOT/"} | kubectl --context $context apply -f -"
echo "  3. ${upgrade[*]} --set namespaces.create=false --set rbac.create=false"
echo "  4. the deployed revision holds no Namespace, Role or RoleBinding"
if [[ "$dry_run" == true ]]; then echo "dry run: nothing changed"; exit 0; fi
if [[ "$yes" != true ]]; then
  [[ -t 0 ]] || die "no terminal to confirm on: pass --yes to migrate $release in $namespace ($where)" 2
  read -r -p "Migrate release $release in $namespace, in $where? Type the release name: " answer
  [[ "$answer" == "$release" ]] || die "not confirmed" 2
fi

echo "==> 0. keep what the current chart no longer renders"
for obj in "${keep[@]}"; do
  read -r k n name <<<"$obj"
  ns_arg=(); [[ "$n" == - ]] || ns_arg=(-n "$n")
  kind_lc="$(tr '[:upper:]' '[:lower:]' <<<"$k")"
  if ! out="$(kc annotate "$kind_lc" "$name" "${ns_arg[@]}" helm.sh/resource-policy=keep "$MARKER=$mark" --overwrite 2>&1)"; then
    grep -q NotFound <<<"$out" || die "could not annotate $k $name: $out"
    echo "  $k $name is already gone"
    continue
  fi
  # Read it back: no upgrade runs unless the live object carries both annotations.
  got="$(kc get "$kind_lc" "$name" "${ns_arg[@]}" \
    -o 'jsonpath={.metadata.annotations.helm\.sh/resource-policy}|{.metadata.annotations.migrate-to-bootstrap/kept-by}')" ||
    die "could not read back $k $name"
  [[ "$got" == "keep|$mark" ]] || die "$k $name is not marked keep after the annotate (read back '$got'): stopping before any upgrade"
done
echo "==> 1. keep: an upgrade that still renders them, so they gain helm.sh/resource-policy: keep"
"${upgrade[@]}" --set namespaces.create=true --set rbac.create=true
echo "==> 2. the bootstrap"
"${boot[@]}" | kc apply -f -
echo "==> 3. switch: an upgrade with both off; keep leaves them in place"
"${upgrade[@]}" --set namespaces.create=false --set rbac.create=false
rev="$(deployed)"
after="$(hc get manifest "$release" -n "$namespace" --revision "$rev")"
! holds "$after" || die "the release $release still holds a Namespace, Role or RoleBinding"
echo "migrate-to-bootstrap: migrated: $release in $namespace ($where); the deployer's upgrades touch none of them"
marked
