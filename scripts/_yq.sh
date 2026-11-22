# shellcheck shell=bash
# _yq.sh — sourced by migrate-to-bootstrap.sh and k8s-env.sh: the one check that the yq on PATH is the
# version pinned in tools/pins.tsv. Their guards parse yq's output, so another yq could miscompute.
# pinned_yq ROOT: returns 0 for the pinned yq; otherwise prints why on stderr and returns 1.
pinned_yq() {
  local root="$1" pin version
  pin="$(awk -F'\t' '$1 == "yq" {print $2}' "$root/tools/pins.tsv")"
  [[ -n "$pin" ]] || { echo "no yq pin in $root/tools/pins.tsv" >&2; return 1; }
  command -v yq >/dev/null 2>&1 || { echo "yq is required: run scripts/tools.sh install yq" >&2; return 1; }
  version="$(yq --version 2>&1)" || { echo "yq --version failed: $version" >&2; return 1; }
  [[ "$version" == *" version v$pin" ]] ||
    { echo "yq is '$version', not the pinned $pin (tools/pins.tsv): run scripts/tools.sh install yq" >&2; return 1; }
}
