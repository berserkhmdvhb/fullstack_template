# shellcheck shell=bash
# _sha256.sh — sourced by scripts/tools.sh, scripts/ci/kubeconform.sh and scripts/kind-deploy.sh: the sha256 (hex only)
# of the files given or of stdin, with GNU sha256sum, or shasum -a 256 where that is what there is (macOS).
sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$@"; else shasum -a 256 "$@"; fi | cut -d' ' -f1
}
