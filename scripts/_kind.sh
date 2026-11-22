# shellcheck shell=bash disable=SC2034  # the variables are for the scripts that source this file
# _kind.sh — sourced by kind-up.sh and kind-deploy.sh: the one place the local kind cluster's name and
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
