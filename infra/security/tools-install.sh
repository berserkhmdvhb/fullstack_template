#!/usr/bin/env bash
# Install a pinned CLI tool from its versioned GitHub release, verifying the
# project-published SHA-256 checksum before use.
#
# This replaces the old "download a remote installer and pipe it straight into a
# shell" pattern (finding P-M3). That pattern executes an UNPINNED remote script
# with NO integrity check — a supply-chain RCE vector, in the very CI stage meant
# to assure image integrity. Here we instead download a PINNED release artifact,
# verify its checksum against the release-published checksums file, then extract
# the single static binary. The only hardcoded input is the version; checksums
# are fetched from the release at runtime and verified, so nothing is transcribed
# and nothing remote is ever executed.
#
# Usage: tools-install.sh <tool> <version> [dest]
#   tool    one of: syft | grype | grant | cosign | trivy | osv-scanner
#                 | sbomqs | sbom-utility | kubescape
#   version the release tag, e.g. v2.4.0 (a leading "v" is accepted)
#   dest    install directory (default /usr/local/bin)
set -Eeuo pipefail

# Shared security runtime: CI-clean logging/banners, the ERR trap, and the
# DRY_RUN-aware `run` runner (⇢ prints the argv and executes nothing). Sourcing
# it keeps this installer on the same house style as the scanner scripts.
# shellcheck source=/dev/null
source "$(dirname "$0")/_helpers.sh"
trap 'trap_err' ERR

# Usage guard up front — name the problem via the shared `error`, not the bare
# `${1:?}` expansion (which would also trip the ERR trap and double the message).
if [ "$#" -lt 2 ]; then
  error "usage: tools-install.sh <tool> <version> [dest]"
  exit 2
fi
TOOL="$1"
VERSION="$2"
DEST="${3:-/usr/local/bin}"

case "$(uname -m)" in
  x86_64|amd64) ARCH="amd64" ;;
  aarch64|arm64) ARCH="arm64" ;;
  *) echo "tools-install: unsupported architecture: $(uname -m)" >&2; exit 1 ;;
esac
OS="linux"

# Sanity-check command after install. Most tools expose a bare `version` subcommand;
# OSV-Scanner (V2 CLI) overrides this to --version in its case above.
VCMD="version"

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# Per-tool release coordinates. BASE/ASSET/CHECKSUMS name the download; the
# extraction step pulls the binary out of whatever the release ships: a bare
# binary for cosign, a tarball with bin/<tool> for trivy, a top-level binary for
# the Anchore tools. VERSION may carry a leading "v"; the asset/checksum names
# never do, so it is stripped where the vendor omits it.
case "$TOOL" in
  syft|grype|grant)
    # Anchore: <tool>_<ver>_linux_<arch>.tar.gz + <tool>_<ver>_checksums.txt
    # (the release asset drops the leading "v": asset is syft_1.51.1_…, not
    # syft_v1.51.1_…).
    VER="${VERSION#v}"
    BASE="https://github.com/anchore/${TOOL}/releases/download/${VERSION}"
    ASSET="${TOOL}_${VER}_${OS}_${ARCH}.tar.gz"
    CHECKSUMS="${TOOL}_${VER}_checksums.txt"
    kind="anchore"
    ;;
  cosign)
    # Sigstore: bare binary cosign-linux-<arch> + cosign_checksums.txt
    BASE="https://github.com/sigstore/cosign/releases/download/${VERSION}"
    ASSET="cosign-linux-${ARCH}"
    CHECKSUMS="cosign_checksums.txt"
    kind="bare"
    ;;
  trivy)
    # Aqua: trivy_<ver>_Linux-64bit.tar.gz + trivy_<ver>_checksums.txt; the
    # binary ships under bin/trivy inside the tarball. (Note the vendor's exact
    # casing: "Linux-64bit", not "${OS}-64bit".)
    VER="${VERSION#v}"
    BASE="https://github.com/aquasecurity/trivy/releases/download/${VERSION}"
    ASSET="trivy_${VER}_Linux-64bit.tar.gz"
    CHECKSUMS="trivy_${VER}_checksums.txt"
    kind="trivy"
    ;;
  osv-scanner)
    # OSV-Scanner: bare per-arch binary osv-scanner_linux_<arch> + osv-scanner_SHA256SUMS
    # (release names are version-less: osv-scanner_linux_amd64, not osv-scanner_<ver>_…).
    BASE="https://github.com/google/osv-scanner/releases/download/${VERSION}"
    ASSET="osv-scanner_linux_${ARCH}"
    CHECKSUMS="osv-scanner_SHA256SUMS"
    kind="bare"
    # V2 CLI reads "version" as a scan path, so its version flag is --version (not the
    # bare `version` subcommand the other tools expose).
    VCMD="--version"
    ;;
  sbomqs)
    # interlynk-io/sbomqs: sbomqs_<ver>_Linux_<arch>.tar.gz + checksums.txt (SHA-256).
    # The release asset drops the leading "v" (asset is sbomqs_2.1.3_…, not sbomqs_v2.1.3_…),
    # so VER strips it for the asset/checksum names while BASE keeps the tag. Its arch token
    # is x86_64/arm64 (not the "amd64" $ARCH uses), so map it here.
    VER="${VERSION#v}"
    case "$ARCH" in amd64) sq_arch=x86_64 ;; arm64) sq_arch=arm64 ;; esac
    BASE="https://github.com/interlynk-io/sbomqs/releases/download/${VERSION}"
    ASSET="sbomqs_${VER}_Linux_${sq_arch}.tar.gz"
    CHECKSUMS="checksums.txt"
    kind="tarball"
    ;;
  sbom-utility)
    # CycloneDX/sbom-utility: sbom-utility-<ver>-linux-<arch>.tar.gz. The release tag KEEPS
    # the leading "v" in the asset name (sbom-utility-v0.19.2-…), so VERSION is used verbatim.
    # The project publishes only a per-asset .md5 (the hash alone, no filename) — verified
    # with md5sum below. md5 is weaker than the SHA-256 the other tools publish, but it is
    # the vendor's only published digest, so this stays a pinned, integrity-checked download
    # (never curl|bash); kind="md5" selects that verification path.
    BASE="https://github.com/CycloneDX/sbom-utility/releases/download/${VERSION}"
    ASSET="sbom-utility-${VERSION}-linux-${ARCH}.tar.gz"
    CHECKSUMS="sbom-utility-${VERSION}-linux-${ARCH}.tar.gz.md5"
    kind="md5"
    ;;
  kubescape)
    # Kubescape (kubescape/kubescape): bare per-arch binary kubescape_<ver>_linux_<arch>
    # + checksums.sha256 (standard "<sha256>  <asset>" lines — the same SHA-256 verify
    # path the other tools use). The release tag KEEPS the leading "v" in the download
    # URL, but the asset/checksum names drop it (kubescape_4.0.20_…, not
    # kubescape_v4.0.20_…), so VER strips it for the asset/checksum names while BASE
    # keeps the tag. kind="bare": the downloaded asset IS the binary (no tarball).
    VER="${VERSION#v}"
    BASE="https://github.com/kubescape/kubescape/releases/download/${VERSION}"
    ASSET="kubescape_${VER}_linux_${ARCH}"
    CHECKSUMS="checksums.sha256"
    kind="bare"
    ;;
  *)
    error "tools-install: unsupported tool '${TOOL}' (want syft|grype|grant|cosign|trivy|osv-scanner|sbomqs|sbom-utility|kubescape)"
    exit 1
    ;;
esac

log "==> ${TOOL} ${VERSION} (asset ${ASSET})"
# Downloads go to a file, never piped straight into a shell — the whole point of
# "pin + verify". `run` makes each one a no-op under --dry-run.
run curl -fsSL --retry 3 --retry-connrefused --connect-timeout 15 --speed-limit 1024 --speed-time 30 -o "${tmp}/${ASSET}" "${BASE}/${ASSET}"
run curl -fsSL --retry 3 --retry-connrefused --connect-timeout 15 --speed-limit 1024 --speed-time 30 -o "${tmp}/checksums.txt" "${BASE}/${CHECKSUMS}"

log "==> verifying checksum …"
if [ "$kind" = "md5" ]; then
  # sbom-utility publishes only a per-asset .md5 (the hash alone, no filename), so build
  # the "<md5>  <asset>" line md5sum -c expects from the downloaded checksums.txt content.
  run bash -c 'cd "$1" && printf "%s  %s\n" "$(cat checksums.txt)" "$2" | md5sum -c -' _ "$tmp" "$ASSET"
else
  # The vendor checksums file lists "<sha256>  <asset>"; verify the one we pulled.
  # Pass $tmp/$ASSET as positionals so the inner script stays single-quoted.
  run bash -c 'cd "$1" && grep "  $2$" checksums.txt | sha256sum -c -' _ "$tmp" "$ASSET"
fi

run install -d -m 755 "$DEST"

case "$kind" in
  bare)
    # cosign: the downloaded asset IS the binary.
    bin="${tmp}/${ASSET}"
    ;;
  *)
    # Anchore tools and trivy ship a tarball with the binary at top level
    # (e.g. ./syft, ./trivy); fall back to a shallow search in case a future
    # release wraps it in a directory.
    run tar -xzf "${tmp}/${ASSET}" -C "$tmp"
    if [ -x "${tmp}/${TOOL}" ]; then
      bin="${tmp}/${TOOL}"
    else
      bin="$(find "$tmp" -type f -name "${TOOL}" -maxdepth 2 | head -n1)"
    fi
    ;;
esac

# The existence guard is only meaningful once the artifact was actually written;
# under --dry-run nothing is downloaded, so skip it (the ⇢ lines above stand in).
if [[ "$DRY_RUN" != "true" && ! -f "${bin}" ]]; then
  error "binary '${TOOL}' not found in artifact"
  exit 1
fi
run chmod +x "${bin}"
run install -m 755 "${bin}" "${DEST}/${TOOL}"
run "${DEST}/${TOOL}" ${VCMD}
success "installed ${TOOL} ${VERSION} -> ${DEST}/${TOOL}"
