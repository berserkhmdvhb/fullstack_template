# shellcheck shell=bash disable=SC2034  # the variables are for the scripts that source this file
# _kind.sh — sourced by kind-up.sh, kind-deploy.sh and kind-forward.sh: the one place the local kind cluster's name and
# files are fixed. The cluster is fst, its directory is the repository's .local/kind. The only override is
# FST_KIND_DIR (the bats tests set it), and it must resolve under the repository's .local/; an exported
# KIND_DIR, or anything else, is never read.
KIND_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
CLUSTER=fst
CONTEXT=kind-fst
KIND_LOCAL="$KIND_ROOT/.local"
KIND_DIR="$KIND_LOCAL/kind"
# Every file the two scripts create there; kind-down deletes these and nothing else.
KIND_FILES=(kubeconfig kubeconfig.part values.yaml)
if [[ -n "${FST_KIND_DIR:-}" ]]; then
  _kind_dir="$(realpath -m -- "$FST_KIND_DIR")"
  if [[ "$_kind_dir" != "$KIND_LOCAL"/?* ]]; then
    echo "kind: FST_KIND_DIR must be a directory under $KIND_LOCAL/ (.local/), got '$FST_KIND_DIR'" >&2
    exit 2
  fi
  KIND_DIR="$_kind_dir"
fi
# The pinned kubectl (tools/kind-nodes.tsv) of the leg whose node image has the server minor $1; empty if none.
kind_kubectl_pin() { awk -F'\t' -v m="$1" '!/^#/ && $3 ~ ("^kindest/node:v1\\." m "\\.") {print $2}' "$KIND_ROOT/tools/kind-nodes.tsv"; }
