# shellcheck shell=bash
# _yq.sh — sourced by migrate-to-bootstrap.sh and k8s-env.sh: the one check that the yq on PATH is the
# version pinned in mise.toml. Their guards parse yq's output, so another yq could miscompute.
# pinned_yq ROOT: returns 0 for the pinned yq; otherwise prints why on stderr and returns 1.
pinned_yq() {
  local root="$1" pin version
  pin="$(awk -F'"' '$2 == "aqua:mikefarah/yq" {print $4}' "$root/mise.toml")"
  [[ -n "$pin" ]] || { echo "no yq pin in $root/mise.toml" >&2; return 1; }
  command -v yq >/dev/null 2>&1 || { echo "yq is required: run scripts/tools.sh install yq" >&2; return 1; }
  version="$(yq --version 2>&1)" || { echo "yq --version failed: $version" >&2; return 1; }
  [[ "$version" == *" version v$pin" ]] ||
    { echo "yq is '$version', not the pinned $pin (mise.toml): run scripts/tools.sh install yq" >&2; return 1; }
}
