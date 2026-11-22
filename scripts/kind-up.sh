#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# ===========================================================================
# kind-up.sh — create (or reuse, or delete) the local kind cluster `fst`
# ===========================================================================
# The cluster is always named fst and its kubeconfig is always .local/kind/kubeconfig: every kind call
# passes --name fst and --kubeconfig, so nothing writes ~/.kube/config, and no call lists or touches
# another cluster. The node image is pinned by digest in tools/kind-nodes.tsv, one row per leg.
# `make kind-up` and `make kind-down` run it; scripts/kind-deploy.sh deploys into it.
#
# Usage: kind-up.sh [--node floor|newest] [--delete]
#   --node LEG   the node image of that leg (default floor: the chart's kubeVersion floor)
#   --delete     delete fst and .local/kind instead
# Files: .local/kind (scripts/_kind.sh; only FST_KIND_DIR, under .local/, overrides it). The pinned
#      kind comes first on PATH (make and CI put .tools/bin there).
# ===========================================================================
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/scripts/_kind.sh"
KUBECONFIG_FILE="$KIND_DIR/kubeconfig"

die() { echo "kind-up: $*" >&2; exit "${2:-1}"; }
usage() { sed -n '/^# Usage:/,/^# Files:/s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; }

leg=floor delete=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --node) [[ $# -ge 2 ]] || die "--node needs a leg" 2; leg="$2"; shift 2 ;;
    --delete) delete=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option '$1' (try --help)" 2 ;;
  esac
done

if [[ "$delete" == true ]]; then
  kind delete cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG_FILE"
  # Only the files the scripts create, then the directory if nothing else is in it.
  for file in "${KIND_FILES[@]}"; do rm -f "$KIND_DIR/$file"; done
  if [[ -d "$KIND_DIR" ]] && ! rmdir "$KIND_DIR" 2>/dev/null; then
    echo "kind-up: left $KIND_DIR: it holds files the kind scripts did not create"
  fi
  echo "kind-up: deleted $CLUSTER"
  exit 0
fi

image="$(awk -F'\t' -v leg="$leg" '!/^#/ && $1 == leg {print $3}' "$ROOT/tools/kind-nodes.tsv")"
[[ -n "$image" ]] || die "no leg '$leg' in tools/kind-nodes.tsv (floor or newest)" 2
mkdir -p "$KIND_DIR"
# The node container of fst, by its exact name: is the cluster there, and on which image?
if current="$(docker inspect --format '{{.Config.Image}}' "$CLUSTER-control-plane" 2>/dev/null)"; then
  [[ "$current" == "$image" ]] ||
    die "$CLUSTER already runs $current, not the $leg image $image: run make kind-down first"
  echo "kind-up: reusing $CLUSTER ($leg, $image)"
  kind get kubeconfig --name "$CLUSTER" > "$KUBECONFIG_FILE.part"
  mv "$KUBECONFIG_FILE.part" "$KUBECONFIG_FILE"
else
  kind create cluster --name "$CLUSTER" --kubeconfig "$KUBECONFIG_FILE" --image "$image" --wait 300s
fi
chmod 600 "$KUBECONFIG_FILE"
echo "kind-up: $CLUSTER is up; KUBECONFIG=$KUBECONFIG_FILE"
