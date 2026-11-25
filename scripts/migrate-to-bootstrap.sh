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
#      annotated helm.sh/resource-policy=keep and labelled migrate-to-bootstrap/release=<release>
#      and migrate-to-bootstrap/namespace=<namespace> on the live object, then read back: no
#      upgrade runs unless each carries all three, and one the cluster does not have stops the run.
#      Two readers list those objects, a line reader (scripts/_manifest.sh) and yq, and the run stops
#      before any change unless both give the same set, for the release and for the render. A
#      third source needs neither: every live Namespace, and every PersistentVolumeClaim and Secret
#      in the release's namespaces, that Helm's annotations (meta.helm.sh/release-name and
#      release-namespace) record as this release's must be in the render or kept, or the run stops.
#      Every stage of that computation is checked, so a failure stops the run rather than empty the set.
#      The objects that carry the labels are listed at the end (on a re-run too, for a cluster
#      admin) as kept and no longer managed, for a person to decide on;
#   1. one upgrade that still renders the namespaces and the app's RBAC (namespaces.create=true,
#      rbac.create=true): they gain helm.sh/resource-policy: keep;
#   2. the bootstrap (scripts/k8s-bootstrap.sh <env> | kubectl apply -f -), applied over them;
#   3. one upgrade with both off: the release stops recording them, and keep leaves them in place;
#   4. a check that the release no longer holds a Namespace, Role or RoleBinding.
# It deletes nothing itself. The upgrades pass the current chart's values (values.<env>.yaml, then
# --values and --set), never the release's stored ones: an older release stored keys the current
# schema refuses. It reads the release at its deployed revision and refuses a release in any other
# status (failed, pending-*): roll that back first. A release that holds none of those objects is
# left alone, so a re-run is safe; a manifest it cannot classify is refused, not taken as holding
# none. See wiki/infrastructure.md (namespace bootstrap).
#
# Usage: migrate-to-bootstrap.sh <dev|qa|production|local> --context NAME [--release NAME]
#                                [--namespace NS] [--values FILE]... [--bootstrap-values FILE]...
#                                [--set K=V]...
#                                [--set-string K=V]... [--env-values-only]
#                                [--allow-missing KIND/NAMESPACE/NAME]...
#                                [--timeout DURATION] [--dry-run] [--yes]
#   --context NAME   the kube context to act on (required; every helm and kubectl call gets it)
#   --release NAME   the Helm release (default your-service-name)
#   --namespace NS   the app namespace (default <release>-<env>; not one ending in another env)
#   --values, --set, --set-string  the deploy's own values after values.<env>.yaml: the secret
#                    values (or secrets.existingSecret) and the images it deploys (repeatable).
#                    The bootstrap render gets them too (--chart-values, --chart-set, --chart-set-string)
#                    (--set-file and --set-json are refused: put such a value in a --values file)
#   --bootstrap-values  a bootstrap config file, as scripts/k8s-bootstrap.sh --values (repeatable): the
#                    one the admin's own bootstrap run passes. Each --values is also passed to it as
#                    --chart-values, so its render and the upgrades read the same chart values
#   --env-values-only  the env's values file is all the deploy passes (else one of the above is
#                    required: an upgrade without the secret values would empty the app's Secrets)
#   --allow-missing  an object the release holds that was deleted on purpose, as the refusal names
#                    it (Namespace/-/NAME, Secret/NS/NAME): go on without it (repeatable)
#   --timeout        each upgrade's --wait (default 9m)
#   --dry-run        check everything and print the steps and the objects it would keep; change nothing
#   --yes            no confirmation (needed without a terminal)
# Needs helm, kubectl, jq and the pinned yq (mise.toml), and cluster-admin rights in that context.
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
release=your-service-name namespace="" context="" timeout=9m dry_run=false yes=false env_only=false
values=(-f "$CHART/values.$ENV_NAME.yaml")
allow_missing=() boot_extra=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --context|--release|--namespace|--timeout|--values|--bootstrap-values|--set|--set-string|--allow-missing)
      [[ $# -ge 2 ]] || die "$1 needs a value" 2
      case "$1" in
        --context) context="$2" ;;
        --release) label release "$2"; release="$2" ;;
        --namespace) label namespace "$2"; namespace="$2" ;;
        --timeout) timeout="$2" ;;
        --allow-missing)
          [[ "$2" =~ ^(Namespace|PersistentVolumeClaim|Secret)/[^/\ ]+/[^/\ ]+$ ]] ||
            die "--allow-missing takes KIND/NAMESPACE/NAME, as the refusal prints it, got '$2'" 2
          allow_missing+=("$2") ;;
        --values) [[ -f "$2" ]] || die "--values needs an existing file" 2; values+=(-f "$2"); boot_extra+=(--chart-values "$2") ;;
        --bootstrap-values) [[ -f "$2" ]] || die "--bootstrap-values needs an existing file" 2; boot_extra+=(--values "$2") ;;
        *) values+=("$1" "$2"); boot_extra+=("--chart-${1#--}" "$2") ;;
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
# The keep set below is computed with yq: only the pinned one (mise.toml), whose output it knows.
# shellcheck source=/dev/null
source "$ROOT/scripts/_yq.sh"
pinned_yq "$ROOT" || exit 1
# shellcheck source=/dev/null
source "$ROOT/scripts/_manifest.sh"
kc() { kubectl --context "$context" "$@"; }
hc() { helm "$@" --kube-context "$context"; }
grep -qxF -- "$context" <<<"$(kubectl config get-contexts -o name 2>/dev/null || true)" ||
  die "context $context is not in the kubeconfig" 2
server="$(kc config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
where="context $context ($server)"
upgrade=(helm upgrade "$release" "$CHART" -n "$namespace" "${values[@]}" --history-max "$HELM_HISTORY_MAX" --server-side=true --force-conflicts --wait --timeout "$timeout" --kube-context "$context")
boot=("$ROOT/scripts/k8s-bootstrap.sh" "$ENV_NAME" --release "$release" --namespace "$namespace" "${boot_extra[@]}")

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
    local fix="roll it back to its last good revision first (helm rollback $release $last -n $namespace --server-side=true --force-conflicts --kube-context $context; helm history lists them), then run this again"
    [[ -n "$last" ]] || fix="it has never been deployed, so there is no revision to roll back to: fix the cause and upgrade it with the chart it was installed with until it is deployed, then run this again (an uninstall would delete its namespaces)"
    case "$status" in
      pending-*) die "release $release in $namespace is $status: an operation is in progress or was interrupted. Wait for it; if none is running, $fix" ;;
      *) die "release $release in $namespace is $status, not deployed: $fix" ;;
    esac
  fi
  echo "$rev"
}
# Every manifest is read through manifest_lf, and classified by manifest_objects (scripts/_manifest.sh).
stored_manifest() { hc get manifest "$release" -n "$namespace" --revision "$1" | manifest_lf; }
classify() { # what it is, the manifest: a kind/namespace/name line per document, or the run stops
  printf '%s\n' "$2" | manifest_objects "$namespace" ||
    die "cannot classify $1 (above): refusing to take it as holding nothing"
}
# The kept objects' marker: two labels (selectable; each value a DNS label, so within 63 characters).
selector="migrate-to-bootstrap/release=$release,migrate-to-bootstrap/namespace=$namespace"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
# objects WHAT MANIFEST DOCUMENTS OUT: the manifest's Namespace, PersistentVolumeClaim and Secret
# objects into OUT, sorted, as kind/namespace/name. Two readers list them: DOCUMENTS (classify's
# lines) and yq. They must give the same set, in both directions, or a reader that printed too
# little, too much or another name (and exited 0) would change what is kept.
objects() {
  local what="$1" out="$4"
  printf '%s\n' "$3" > "$out.documents" || die "could not write the documents of $what"
  awk -F/ '$1 ~ /^(Namespace|PersistentVolumeClaim|Secret)$/' "$out.documents" > "$out.lines" ||
    die "could not select the objects of $what"
  LC_ALL=C sort -u "$out.lines" > "$out" || die "could not sort the objects of $what"
  printf '%s\n' "$2" | yq -N 'select(.kind == "Namespace" or .kind == "PersistentVolumeClaim" or .kind == "Secret")
    | select(.metadata.name != null)
    | .kind + "/" + (.metadata.namespace // "'"$namespace"'") + "/" + .metadata.name
    | sub("^Namespace/[^/]+/", "Namespace/-/")' /dev/stdin > "$out.yq.unsorted" || die "yq could not read $what"
  LC_ALL=C sort -u "$out.yq.unsorted" > "$out.yq" || die "could not sort yq's objects of $what"
  LC_ALL=C comm -3 "$out" "$out.yq" > "$out.differ" || die "could not compare the two lists of $what"
  if [[ -s "$out.differ" || ( -s "$out" && ! -s "$out.yq" ) ]]; then
    local ours theirs
    LC_ALL=C comm -23 "$out" "$out.yq" > "$out.only" || die "could not compare the two lists of $what"
    LC_ALL=C comm -13 "$out" "$out.yq" > "$out.yq.only" || die "could not compare the two lists of $what"
    ours="$(paste -sd' ' "$out.only")" theirs="$(paste -sd' ' "$out.yq.only")"
    die "the two readers of $what disagree: yq found $(wc -l < "$out.yq") Namespace, PersistentVolumeClaim and Secret objects; the manifest has $(wc -l < "$out"); only the line reader found: ${ours:-none}; only yq found: ${theirs:-none}: stopping before any change"
  fi
}
marked() { # what an earlier or this run kept for this release: only the objects with its labels
  local kind kinds=(Namespace:namespaces PersistentVolumeClaim:persistentvolumeclaims Secret:secrets)
  : > "$work/marked"
  for kind in "${kinds[@]}"; do
    local -a all=(-A); [[ "${kind%%:*}" != Namespace ]] || all=()
    kc get "${kind#*:}" "${all[@]}" -l "$selector" --no-headers \
      -o "custom-columns=NS:.metadata.namespace,N:.metadata.name" > "$work/list" ||
      die "could not list the kept ${kind#*:} in $where"
    awk -v k="${kind%%:*}" '{ print k " " (k == "Namespace" ? $2 : $1 "/" $2) }' "$work/list" >> "$work/marked" ||
      die "could not read the ${kind#*:} list"
  done
  if [[ -s "$work/marked" ]]; then
    echo "migrate-to-bootstrap: kept, now unmanaged: decide by hand (delete or keep each; no release owns it):"
    sed 's/^/    /' "$work/marked"
  fi
}
lacks() { # checks (kubectl auth can-i arguments): prints the first the identity lacks
  local check
  for check in "$@"; do
    # shellcheck disable=SC2086 # the words are kubectl's arguments
    [[ "$(kc auth can-i $check 2>/dev/null || true)" == yes ]] || { echo "$check"; return 1; }
  done
}
# Keeping, reading back and listing: the Namespaces, and the PersistentVolumeClaims and Secrets of any namespace.
kept_checks=()
for verb in get list patch; do
  # A cluster-scoped kind is checked cluster-wide: in the context's namespace a Role could answer yes.
  kept_checks+=("$verb namespaces --all-namespaces" "$verb persistentvolumeclaims --all-namespaces" "$verb secrets --all-namespaces")
done
boot_checks() { # the bootstrap's apply, from its own render: every namespace it writes a quota, a LimitRange or RBAC to, and any ClusterRules
  local render kind ns ref api resource verb
  render="$("${boot[@]}")" || die "the bootstrap does not render for $ENV_NAME"
  printf '%s\n' "$render" | yq -N 'select(.kind != null and .kind != "Namespace")
    | .kind + " " + (.metadata.namespace // "'"$namespace"'") + " " + (.roleRef.kind // "-") + " " + (.apiVersion // "-")' /dev/stdin > "$work/boot" ||
    die "yq could not read the bootstrap's render"
  echo "create namespaces --all-namespaces"
  while read -r kind ns ref api; do
    case "$kind" in
      ResourceQuota|LimitRange|Role|RoleBinding) ;;
      ClusterRules) # cluster-scoped: its group from the render's apiVersion, checked cluster-wide
        [[ "$api" == */* ]] || die "the bootstrap's ClusterRules has no API group (apiVersion '$api')"
        for verb in get create patch; do echo "$verb clusterrules.${api%/*} --all-namespaces"; done
        continue ;;
      *) die "the bootstrap renders a $kind, a kind this script does not check the rights for: stopping before any change" ;;
    esac
    resource="$(tr '[:upper:]' '[:lower:]' <<<"$kind")s"
    for verb in get create patch; do echo "$verb $resource -n $ns"; done
    # A Role may grant what its author lacks only with escalate; a RoleBinding to it only with bind.
    [[ "$kind" != Role ]] || echo "escalate roles -n $ns"
    [[ "$kind" != RoleBinding ]] || echo "bind $(tr '[:upper:]' '[:lower:]' <<<"$ref")s -n $ns"
  done < "$work/boot"
}
# The third source for what the upgrade must not delete, read by neither reader: the live objects
# that Helm's own annotations record as this release's (Helm 3.2 and later set them on every object
# it creates or updates). Namespaces cluster-wide; PersistentVolumeClaims and Secrets in the
# release's namespaces (its own, the live ones it owns, and every one either reader named), without
# Helm's release Secrets. kubectl prints only names and those annotations; it does fetch the
# objects it prints from, so these namespaces' Secrets pass through it, not through this script.
owned() { # out file: "kind/namespace/name<TAB>resource policy" per object
  local out="$1" ns
  local cols='{range .items[*]}{.metadata.namespace}{"\t"}{.metadata.name}{"\t"}{.metadata.annotations.meta\.helm\.sh/release-name}{"\t"}{.metadata.annotations.meta\.helm\.sh/release-namespace}{"\t"}{.metadata.annotations.helm\.sh/resource-policy}{"\n"}{end}'
  mine() { # kind: the listed rows that are this release's
    awk -F'\t' -v k="$1" -v r="$release" -v n="$namespace" '$3 == r && $4 == n && !(k == "Secret" && $2 ~ /^sh\.helm\.release\.v1\./) {
      print k "/" (k == "Namespace" ? "-" : $1) "/" $2 "\t" $5 }' "$out.rows" >> "$out" || die "could not read the live $1 list"
  }
  : > "$out"
  kc get namespaces -o "jsonpath=$cols" > "$out.rows" || die "could not list the namespaces in $where"
  mine Namespace
  { echo "$namespace" && awk -F'[/\t]' '{ print $3 }' "$out" &&
    awk -F/ '{ print ($1 == "Namespace" ? $3 : $2) }' "$work/old" "$work/new"; } > "$out.ns.unsorted" ||
    die "could not list the release's namespaces"
  LC_ALL=C sort -u "$out.ns.unsorted" > "$out.ns" || die "could not sort the release's namespaces"
  while read -r ns; do
    kc get persistentvolumeclaims -n "$ns" -o "jsonpath=$cols" > "$out.rows" ||
      die "could not list the PersistentVolumeClaims of $ns in $where"
    mine PersistentVolumeClaim
    kc get secrets -n "$ns" --field-selector 'type!=helm.sh/release.v1' -o "jsonpath=$cols" > "$out.rows" ||
      die "could not list the Secrets of $ns in $where"
    mine Secret
  done < "$out.ns"
}
# protected before|after: every live object of the release is in the render, or carries keep, or
# (before the keep annotations are made) is in the keep set. Anything else the upgrade could remove.
protected() {
  local obj policy
  owned "$work/live"
  [[ -s "$work/live" || ! -s "$work/old" ]] ||
    die "the manifest of release $release lists $(wc -l < "$work/old") Namespace, PersistentVolumeClaim and Secret objects, and the cluster ($where) records none of them as this release's (no meta.helm.sh/release-name=$release annotation; a Helm before 3.2 set none), so the keep set cannot be checked against the cluster: upgrade the release once with the chart it was installed with and a current Helm, then run this again. Stopping before any upgrade"
  while IFS=$'\t' read -r obj policy; do
    if grep -qxF -- "$obj" "$work/new" || [[ "$policy" == keep ]]; then continue; fi
    if [[ "$1" == before ]] && grep -qxF -- "$obj" "$work/keep"; then continue; fi
    die "$obj is this release's in the cluster (its meta.helm.sh/release-name annotation), and it is neither in the current chart's render nor kept: the release's manifest, as read here, does not list it, and an upgrade may remove it. Stopping before any upgrade"
  done < "$work/live"
}

# The deployed revision, read once its status is checked (every read below is at that revision).
rev="$(deployed)"
stored="$(stored_manifest "$rev")"
documents="$(classify "the release's manifest" "$stored")"
if ! manifest_holds "$documents"; then
  echo "migrate-to-bootstrap: $release in $namespace ($where) holds no Namespace, Role or RoleBinding: nothing to do"
  # The kept list reads labelled objects cluster-wide: only for a cluster admin.
  if lacks "${kept_checks[@]}" > /dev/null; then marked; else echo "migrate-to-bootstrap: run it as a cluster admin to list what an earlier run kept"; fi
  exit 0
fi
# The steps create namespaces, quotas and RBAC: a cluster admin's rights, checked before any change.
boot_checks > "$work/checks" # not in a process substitution: its die must stop the run
mapfile -t checks < "$work/checks"
[[ "${checks[0]-}" == "create namespaces --all-namespaces" ]] || die "could not work out the rights the bootstrap needs"
missing="$(lacks "${checks[@]}" "${kept_checks[@]}")" || die "this identity cannot '$missing' in $where: run it as a cluster admin"
# What the release holds and the first upgrade's render does not, Helm deletes in that upgrade. Each
# stage is a file with its own status check, so a failure stops the run rather than empty the set.
# (The manifests stay in memory: they hold the release's Secrets.)
objects "the release's manifest" "$stored" "$documents" "$work/old"
render="$(helm template "$release" "$CHART" -n "$namespace" "${values[@]}" --set namespaces.create=true --set rbac.create=true | manifest_lf)" ||
  die "the current chart does not render with these values"
documents="$(classify "the current chart's render" "$render")"
objects "the current chart's render" "$render" "$documents" "$work/new"
LC_ALL=C comm -23 "$work/old" "$work/new" > "$work/keep" || die "could not compare the release's objects with the chart's"
mapfile -t keep < "$work/keep"
protected before
show() { # kind namespace/name, or kind name for a Namespace
  local obj k n name
  for obj in "${keep[@]}"; do
    IFS=/ read -r k n name <<<"$obj"
    if [[ "$n" == - ]]; then echo "    $k $name"; else echo "    $k $n/$name"; fi
  done
}

echo "migrate-to-bootstrap: $release in $namespace, in $where"
echo "  0. keep (annotate helm.sh/resource-policy=keep, label $selector) what the current chart no longer renders:"
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
  IFS=/ read -r k n name <<<"$obj"
  ns_arg=(); [[ "$n" == - ]] || ns_arg=(-n "$n")
  kind_lc="$(tr '[:upper:]' '[:lower:]' <<<"$k")"
  if ! out="$(kc annotate "$kind_lc" "$name" "${ns_arg[@]}" helm.sh/resource-policy=keep --overwrite 2>&1)"; then
    # The release owns it and the cluster does not have it: not "already gone", but a state to look into.
    grep -q NotFound <<<"$out" || die "could not annotate $k $name: $out"
    if [[ " ${allow_missing[*]} " == *" $obj "* ]]; then
      echo "  $obj is not in the cluster: going on without it (--allow-missing)"
      continue
    fi
    die "$k $name is in the manifest of release $release and is not in the cluster ($where): $out. Check that the context and the namespace are the release's. If it was deleted on purpose, nothing is left of it to keep: run this again with --allow-missing $obj. Otherwise restore it first. No upgrade ran"
  fi
  kc label "$kind_lc" "$name" "${ns_arg[@]}" "migrate-to-bootstrap/release=$release" \
    "migrate-to-bootstrap/namespace=$namespace" --overwrite > /dev/null || die "could not label $k $name"
  # Read it back: no upgrade runs unless the live object carries keep and both labels.
  got="$(kc get "$kind_lc" "$name" "${ns_arg[@]}" -o 'jsonpath={.metadata.annotations.helm\.sh/resource-policy}|{.metadata.labels.migrate-to-bootstrap/release}|{.metadata.labels.migrate-to-bootstrap/namespace}')" ||
    die "could not read back $k $name"
  [[ "$got" == "keep|$release|$namespace" ]] || die "$k $name is not marked keep after the annotate (read back '$got'): stopping before any upgrade"
done
protected after
echo "==> 1. keep: an upgrade that still renders them, so they gain helm.sh/resource-policy: keep"
"${upgrade[@]}" --set namespaces.create=true --set rbac.create=true
echo "==> 2. the bootstrap"
"${boot[@]}" | kc apply -f -
echo "==> 3. switch: an upgrade with both off; keep leaves them in place"
"${upgrade[@]}" --set namespaces.create=false --set rbac.create=false
rev="$(deployed)"
after="$(stored_manifest "$rev")"
documents="$(classify "the release's manifest after the switch" "$after")"
! manifest_holds "$documents" || die "the release $release still holds a Namespace, Role or RoleBinding"
echo "migrate-to-bootstrap: migrated: $release in $namespace ($where); the deployer's upgrades touch none of them"
marked
