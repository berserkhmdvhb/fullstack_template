#!/usr/bin/env bash
# debian-fixes.sh — lay Debian's fix for a package over a distroless base that has not shipped it yet.
#
# Usage (the debian-fixes stage of each Dockerfile, a Debian stage of the base's release, run as root):
#   DEBIAN_FIXES="<package>=<version> ..." debian-fixes.sh <base-root> <out-dir>
# <base-root> is a copy of the runtime base's filesystem; the runtime stage copies <out-dir> over the base.
#
# A distroless base is rebuilt from Debian some days after Debian publishes a fix; until then the
# vulnerability gate fails on every image built from it. DEBIAN_FIXES lists the fixed packages,
# separated by spaces or newlines. Each entry must name a package the base ships (its dpkg record
# var/lib/dpkg/status.d/<package>, the file distroless keeps per package), for this stage's architecture,
# at an older version than the entry. An entry the base already has, at that version or newer, is an
# error: the list is emptied when the base catches up instead of lingering, and a fix can never
# downgrade the base.
#
# apt downloads each <package>=<version> (checked against the archive's signed indices), and the package
# is unpacked into <out-dir> with its control file and md5sums, which replace the base's record, so an
# SBOM scan lists the fixed version. Copying <out-dir> over the base must change nothing but the
# package's own files, so the build is refused when the fixed package
#   - ships a path under a directory that is a symlink in the base (the copy would replace the link),
#   - ships a path whose type (file, directory, symlink) differs from the base's,
#   - ships a directory whose mode or owner differs from the base's,
#   - ships a conffile whose contents differ from the base's (the base may have changed it),
#   - no longer ships a file the base's record lists (copying over the base cannot remove it), or
#   - unpacks files that do not match its md5sums.
# A path the base does not have is added and logged. (The base's record lists only the package's regular
# files, so a symlink or conffile a fixed version drops cannot be detected; a stable-release fix does
# not drop them.) An empty list writes an empty <out-dir> and fetches nothing.
set -euo pipefail

DOC="wiki/containerization.md#debian-fixes-the-base-has-not-shipped-yet"

die() {
  echo "debian-fixes: $*" >&2
  exit 1
}

note() {
  echo "debian-fixes: $*"
}

[[ $# -eq 2 ]] || die "usage: debian-fixes.sh <base-root> <out-dir>"
base="$1"
out="$2"
mkdir -p "$out"

fixes=()
read -r -d '' -a fixes <<< "${DEBIAN_FIXES:-}" || true
if [[ ${#fixes[@]} -eq 0 ]]; then
  note "none listed, the base is used as published"
  exit 0
fi
status_dir="$base/var/lib/dpkg/status.d"
[[ -d "$status_dir" ]] || die "no dpkg records at $status_dir"

# field RECORD NAME — the value of a field that the record holds exactly once.
field() {
  local value
  value="$(sed -n "s/^$2: //p" "$1")"
  [[ -n "$value" && "$value" != *$'\n'* ]] || die "the base's record $1 must hold exactly one $2 field"
  printf '%s' "$value"
}

# kind PATH — file, dir, link or none.
kind() {
  if [[ -L "$1" ]]; then echo link
  elif [[ -d "$1" ]]; then echo dir
  elif [[ -e "$1" ]]; then echo file
  else echo none
  fi
}

# check_path WHAT PATH — copying <out-dir>/PATH over the base changes nothing but that path's content:
# no symlink of the base on the way, the same type, and a directory keeps the base's mode and owner.
# WHAT names what ships the path, for the messages. PATH is relative; empty is the root.
check_path() {
  local what="$1" path="$2" prefix="" theirs ours i parts=()
  [[ -z "$path" ]] || IFS=/ read -r -a parts <<< "$path"
  for ((i = 0; i < ${#parts[@]} - 1; i++)); do
    prefix="${prefix:+$prefix/}${parts[i]}"
    [[ ! -L "$base/$prefix" ]] ||
      die "$what ships /$path under /$prefix, a symlink in the base: the copy would replace the link"
  done
  theirs="$(kind "$base/$path")"
  ours="$(kind "$out/$path")"
  if [[ "$theirs" == none ]]; then
    note "$what adds /$path, which the base does not have"
  elif [[ "$theirs" == link && "$ours" == dir ]]; then
    die "$what ships the directory /$path, a symlink in the base: the copy would replace the link"
  elif [[ "$theirs" != "$ours" ]]; then
    die "$what ships /$path as a $ours, the base has a $theirs"
  elif [[ "$ours" == dir && "$(stat -c '%a %u %g' "$out/$path")" != "$(stat -c '%a %u %g' "$base/$path")" ]]; then
    die "$what ships /$path with another mode or owner than the base's (mode uid gid:" \
      "$(stat -c '%a %u %g' "$out/$path") vs $(stat -c '%a %u %g' "$base/$path"))"
  fi
}

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
  name="$(field "$record" Package)"
  have="$(field "$record" Version)"
  base_arch="$(field "$record" Architecture)"
  [[ "$name" == "$pkg" ]] || die "the base's record $record is for $name, not $pkg"
  [[ "$base_arch" == "$arch" || "$base_arch" == all ]] ||
    die "the base's $pkg is for $base_arch, this stage is $arch"
  if dpkg --compare-versions "$have" lt "$ver"; then
    note "$pkg $have -> $ver"
  else
    case $? in
      1) die "the base already ships $pkg $have (not older than $ver): remove '$fix' from DEBIAN_FIXES" ;;
      *) die "dpkg cannot compare the base's $pkg version '$have' with '$ver'" ;;
    esac
  fi
  specs+=("$pkg=$ver")
done

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
# apt downloads as its unprivileged _apt user, which needs to write the directory.
[[ $EUID -ne 0 ]] || chown _apt "$work"
apt-get update -qq || die "apt-get update failed"
# apt checks each download against the archive's signed indices before it writes the .deb.
(cd "$work" && apt-get download -qq "${specs[@]}") ||
  die "the Debian archive does not serve ${specs[*]}: set each entry to the version that" \
    "'apt-cache policy <package>' names as the candidate, or remove it if the base ships the fix ($DOC)"

records="$out/var/lib/dpkg/status.d"
mkdir -p "$records"
# The directories this script makes are copied over the base too.
for path in "" var var/lib var/lib/dpkg var/lib/dpkg/status.d; do
  check_path "the record directory" "$path"
done
for spec in "${specs[@]}"; do
  pkg="${spec%%=*}"
  debs=("$work/${pkg}_"*.deb)
  [[ ${#debs[@]} -eq 1 && -f "${debs[0]}" ]] || die "apt did not download exactly one $pkg package"
  dpkg-deb --fsys-tarfile "${debs[0]}" > "$work/$pkg.tar"
  tar -tf "$work/$pkg.tar" > "$work/$pkg.paths"
  dpkg-deb -x "${debs[0]}" "$out"
  dpkg-deb -e "${debs[0]}" "$work/$pkg.control"

  # The copy over the base changes nothing but the package's own files.
  while IFS= read -r entry; do
    path="${entry#./}"
    path="${path%/}"
    [[ -z "$path" ]] || check_path "the fixed $pkg" "$path"
  done < "$work/$pkg.paths"
  if [[ -f "$work/$pkg.control/conffiles" ]]; then
    while read -r -a words; do
      [[ ${#words[@]} -gt 0 ]] || continue
      conffile="${words[-1]#/}"
      if [[ -e "$out/$conffile" && -e "$base/$conffile" ]] && ! cmp -s "$out/$conffile" "$base/$conffile"; then
        die "the fixed $pkg's conffile /$conffile differs from the base's, which the base may have changed:" \
          "this cannot be merged, wait for the base's rebuild ($DOC)"
      fi
    done < "$work/$pkg.control/conffiles"
  fi

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
note "unpacked ${specs[*]}"
