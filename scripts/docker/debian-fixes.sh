#!/usr/bin/env bash
# debian-fixes.sh — lay Debian's fix for a package over a distroless base that has not shipped it yet.
#
# Usage (the debian-fixes stage of each Dockerfile, a Debian stage of the base's release):
#   DEBIAN_FIXES="<package>=<version> ..." debian-fixes.sh <base-status.d> <out-dir>
#
# A distroless base is rebuilt from Debian some days after Debian publishes a fix; until then the
# vulnerability gate fails on every image built from it. DEBIAN_FIXES lists the fixed packages. Each entry
# must name a package the base ships (its dpkg record <base-status.d>/<package>, the file distroless keeps
# per package), for this stage's architecture, at an older version than the entry. An entry the base
# already has, at that version or newer, is an error: the list is emptied when the base catches up instead
# of lingering, and a fix can never downgrade the base. apt downloads each <package>=<version> (checked
# against the archive's signed indices) and the package is unpacked into <out-dir> together with its
# control file and md5sums, which replace the base's record in <out-dir>/var/lib/dpkg/status.d/, so an
# SBOM scan lists the fixed version. The unpacked files are checked against those md5sums, and a fixed
# package that no longer ships a file the base has is refused, because copying over the base cannot
# remove it. The runtime stage copies <out-dir> over the base. An empty list writes an empty <out-dir>
# and fetches nothing.
set -euo pipefail

die() {
  echo "debian-fixes: $*" >&2
  exit 1
}

[[ $# -eq 2 ]] || die "usage: debian-fixes.sh <base-status.d> <out-dir>"
status_dir="$1"
out="$2"
mkdir -p "$out"

read -r -a fixes <<< "${DEBIAN_FIXES:-}"
if [[ ${#fixes[@]} -eq 0 ]]; then
  echo "debian-fixes: none listed, the base is used as published"
  exit 0
fi
[[ -d "$status_dir" ]] || die "no dpkg records at $status_dir"

arch="$(dpkg --print-architecture)"
declare -A listed=()
specs=()
for fix in "${fixes[@]}"; do
  [[ "$fix" =~ ^([a-z0-9][a-z0-9+.-]+)=([0-9][A-Za-z0-9.+~:-]*)$ ]] ||
    die "'$fix' is not <package>=<version>"
  pkg="${BASH_REMATCH[1]}"
  ver="${BASH_REMATCH[2]}"
  [[ -z "${listed[$pkg]:-}" ]] || die "$pkg is listed twice"
  listed[$pkg]=1
  record="$status_dir/$pkg"
  [[ -f "$record" ]] || die "the base does not ship $pkg: remove '$fix' from DEBIAN_FIXES"
  have="$(sed -n 's/^Version: //p' "$record")"
  base_arch="$(sed -n 's/^Architecture: //p' "$record")"
  [[ -n "$have" ]] || die "the base's record of $pkg has no Version"
  [[ "$base_arch" == "$arch" || "$base_arch" == all ]] ||
    die "the base's $pkg is for $base_arch, this stage is $arch"
  if dpkg --compare-versions "$have" ge "$ver"; then
    die "the base already ships $pkg $have (not older than $ver): remove '$fix' from DEBIAN_FIXES"
  fi
  echo "debian-fixes: $pkg $have -> $ver"
  specs+=("$pkg=$ver")
done

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
apt-get update -qq
# apt checks each download against the archive's signed indices before it writes the .deb.
(cd "$work" && apt-get download -qq "${specs[@]}")

records="$out/var/lib/dpkg/status.d"
mkdir -p "$records"
for spec in "${specs[@]}"; do
  pkg="${spec%%=*}"
  debs=("$work/${pkg}_"*.deb)
  [[ ${#debs[@]} -eq 1 && -f "${debs[0]}" ]] || die "apt did not download exactly one $pkg package"
  dpkg-deb -x "${debs[0]}" "$out"
  dpkg-deb -e "${debs[0]}" "$work/$pkg.control"
  cp "$work/$pkg.control/control" "$records/$pkg"
  if [[ -f "$work/$pkg.control/md5sums" ]]; then
    cp "$work/$pkg.control/md5sums" "$records/$pkg.md5sums"
    (cd "$out" && md5sum --check --quiet "var/lib/dpkg/status.d/$pkg.md5sums") ||
      die "the unpacked $pkg files do not match its md5sums"
  fi
  if [[ -f "$status_dir/$pkg.md5sums" ]]; then
    [[ -f "$records/$pkg.md5sums" ]] || die "the fixed $pkg has no md5sums, the base's record has"
    dropped="$(LC_ALL=C comm -23 \
      <(sed -E 's/^[0-9a-f]+ +//' "$status_dir/$pkg.md5sums" | LC_ALL=C sort) \
      <(sed -E 's/^[0-9a-f]+ +//' "$records/$pkg.md5sums" | LC_ALL=C sort))"
    [[ -z "$dropped" ]] ||
      die "the fixed $pkg no longer ships files the base has, and copying over the base cannot remove them: $dropped"
  fi
done
echo "debian-fixes: unpacked ${specs[*]}"
