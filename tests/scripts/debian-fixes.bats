#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# debian-fixes.bats — scripts/docker/debian-fixes.sh: a listed fix replaces the package's files and its dpkg
# record and changes nothing else of the base; an empty list fetches nothing. Refused: an entry the base
# already has (that version or newer), a package the base does not ship, a malformed or repeated entry, a
# record for another architecture or package or with a repeated field, a version dpkg cannot compare, a
# version the archive no longer serves, and a fixed package that would change the base beyond its own
# files (a path under a base symlink, a changed type, a directory's mode, a changed conffile, a dropped
# file) or whose files fail their md5sums. apt-get, dpkg and dpkg-deb are stubs (the script runs for real
# in the Debian stage of each Dockerfile); the stub dpkg orders versions with sort -V, enough for the
# versions used here. The stub package ships <prefix>/stub/<package>.so holding its version.
# -----------------------------------------------------------------------------

setup() {
  umask 022
  ROOT="$(dirname "$BATS_TEST_DIRNAME")/.."
  SCRIPT="$ROOT/scripts/docker/debian-fixes.sh"
  STUBS="$BATS_TEST_TMPDIR/bin"
  BASE="$BATS_TEST_TMPDIR/base-root"
  OUT="$BATS_TEST_TMPDIR/out"
  export CALLS="$BATS_TEST_TMPDIR/calls"
  export STUB_ARCH=amd64
  mkdir -p "$STUBS" "$BASE/var/lib/dpkg/status.d" "$BASE/usr/lib/stub" "$BASE/etc"
  ln -s usr/lib "$BASE/lib"
  printf 'old\n' > "$BASE/usr/lib/stub/libc6.so"
  : > "$CALLS"

  cat > "$STUBS/apt-get" <<'SH'
#!/usr/bin/env bash
echo "apt-get $*" >> "$CALLS"
[[ "$1" != update || -z "${STUB_UPDATE_FAILS:-}" ]] || { echo "E: Failed to fetch" >&2; exit 100; }
[[ "$1" == download ]] || exit 0
[[ -z "${STUB_GONE:-}" ]] || { echo "E: Version not found" >&2; exit 100; }
for spec in "$@"; do
  [[ "$spec" == *=* ]] || continue
  printf '%s %s\n' "${spec%%=*}" "${spec#*=}" > "${spec%%=*}_${spec#*=}_${STUB_ARCH}.deb"
done
SH
  cat > "$STUBS/dpkg" <<'SH'
#!/usr/bin/env bash
case "$1" in
  --print-architecture) echo "$STUB_ARCH" ;;
  --compare-versions)
    # Like dpkg: an embedded space or nothing after the epoch colon is an error (exit 2).
    [[ "$3" == lt && "$2$4" != *" "* && "$2" != *: && "$4" != *: ]] || exit 2
    mapfile -t order < <(printf '%s\n%s\n' "$2" "$4" | sort -V)
    [[ "$2" != "$4" && "${order[0]}" == "$2" ]] ;;
  *) exit 2 ;;
esac
SH
  cat > "$STUBS/dpkg-deb" <<'SH'
#!/usr/bin/env bash
umask 022
prefix="${STUB_PREFIX:-usr/lib}"
# payload DEB DIR — the fixed package's files, as the STUB_* variables say.
payload() {
  local pkg ver
  read -r pkg ver < "$1"
  mkdir -p "$2/$prefix/stub"
  printf '%s\n' "$ver" > "$2/$prefix/stub/$pkg.so"
  if [[ -n "${STUB_CONFFILE:-}" ]]; then
    mkdir -p "$2/etc/stub"
    printf '%s\n' "$STUB_CONFFILE" > "$2/etc/stub/$pkg.conf"
  fi
  if [[ -n "${STUB_NEW_PATH:-}" ]]; then
    mkdir -p "$2/$(dirname "$STUB_NEW_PATH")"
    : > "$2/$STUB_NEW_PATH"
  fi
}
case "$1" in
  --fsys-tarfile)
    d="$(mktemp -d)"
    chmod 755 "$d"
    payload "$2" "$d"
    if [[ -n "${STUB_FILES_ONLY:-}" ]]; then
      # A tar that lists only the files, no directory entries.
      (cd "$d" && find . -type f -print0 | tar --null -T - --no-recursion -cf -)
    else
      tar -C "$d" -cf - .
    fi
    rm -rf "$d" ;;
  -x) "$0" --fsys-tarfile "$2" | tar -xf - -C "$3" ;;
  -e)
    read -r pkg ver < "$2"
    mkdir -p "$3"
    printf 'Package: %s\nVersion: %s\nArchitecture: %s\n' "$pkg" "$ver" "$STUB_ARCH" > "$3/control"
    if [[ -z "${STUB_NO_MD5:-}" ]]; then
      sum="$(printf '%s\n' "$ver" | md5sum)"
      [[ -z "${STUB_BAD_SUM:-}" ]] || sum="00000000000000000000000000000000"
      printf '%s  %s/stub/%s.so\n' "${sum%% *}" "$prefix" "$pkg" > "$3/md5sums"
    fi
    [[ -z "${STUB_CONFFILE:-}" ]] || printf '/etc/stub/%s.conf\n' "$pkg" > "$3/conffiles" ;;
  *) exit 2 ;;
esac
SH
  chmod +x "$STUBS"/*
  export PATH="$STUBS:$PATH"
  base_record libc6 2.41-12+deb13u4
}

# base_record <package> <version> [arch]: the base's dpkg record and md5sums for <package>.
base_record() {
  printf 'Package: %s\nVersion: %s\nArchitecture: %s\n' "$1" "$2" "${3:-amd64}" > "$BASE/var/lib/dpkg/status.d/$1"
  printf '%s  usr/lib/stub/%s.so\n' 0123456789abcdef0123456789abcdef "$1" > "$BASE/var/lib/dpkg/status.d/$1.md5sums"
}

fix() {
  DEBIAN_FIXES="$1" run bash "$SCRIPT" "$BASE" "$OUT"
}

@test "an empty list writes an empty out dir and fetches nothing" {
  fix ""
  [ "$status" -eq 0 ]
  [ -d "$OUT" ]
  [ -z "$(ls -A "$OUT")" ]
  [ ! -s "$CALLS" ]
}

@test "a listed fix replaces the package's files and its dpkg record, and adds nothing the base lacks" {
  fix "libc6=2.41-12+deb13u5"
  [ "$status" -eq 0 ]
  [[ "$output" == *"libc6 2.41-12+deb13u4 -> 2.41-12+deb13u5"* ]]
  [[ "$output" != *" adds "* ]]
  grep -qx 'apt-get download -qq libc6=2.41-12+deb13u5' "$CALLS"
  [ "$(cat "$OUT/usr/lib/stub/libc6.so")" = "2.41-12+deb13u5" ]
  grep -qx 'Version: 2.41-12+deb13u5' "$OUT/var/lib/dpkg/status.d/libc6"
  grep -q ' usr/lib/stub/libc6.so$' "$OUT/var/lib/dpkg/status.d/libc6.md5sums"
}

@test "several fixes are fetched in one download" {
  base_record libc-bin 2.41-12+deb13u4
  fix "libc6=2.41-12+deb13u5 libc-bin=2.41-12+deb13u5"
  [ "$status" -eq 0 ]
  grep -qx 'apt-get download -qq libc6=2.41-12+deb13u5 libc-bin=2.41-12+deb13u5' "$CALLS"
  grep -qx 'Version: 2.41-12+deb13u5' "$OUT/var/lib/dpkg/status.d/libc-bin"
}

@test "entries on several lines are all read" {
  base_record libc-bin 2.41-12+deb13u4
  fix $'libc6=2.41-12+deb13u5\nlibc-bin=2.41-12+deb13u5\n'
  [ "$status" -eq 0 ]
  grep -qx 'apt-get download -qq libc6=2.41-12+deb13u5 libc-bin=2.41-12+deb13u5' "$CALLS"
}

@test "an entry the base already ships at that version is refused and named for removal" {
  fix "libc6=2.41-12+deb13u4"
  [ "$status" -ne 0 ]
  [[ "$output" == *"the base already ships libc6 2.41-12+deb13u4"* ]]
  [[ "$output" == *"remove 'libc6=2.41-12+deb13u4' from DEBIAN_FIXES"* ]]
  [ ! -s "$CALLS" ]
}

@test "an entry older than the base's version is refused: a fix never downgrades the base" {
  base_record libc6 2.41-12+deb13u6
  fix "libc6=2.41-12+deb13u5"
  [ "$status" -ne 0 ]
  [[ "$output" == *"the base already ships libc6 2.41-12+deb13u6"* ]]
  [ ! -s "$CALLS" ]
}

@test "a package the base does not ship is refused" {
  fix "libssl3t64=3.5.4-1~deb13u2"
  [ "$status" -ne 0 ]
  [[ "$output" == *"the base does not ship libssl3t64"* ]]
  [ ! -s "$CALLS" ]
}

@test "an entry that is not package=version is refused" {
  for bad in "libc6" "libc6=" "=2.41" "Libc6=2.41-12" "libc6=2.41-12+deb13u5,libc-bin" "libc6==2.41"; do
    fix "$bad"
    [ "$status" -ne 0 ]
    [[ "$output" == *"is not <package>=<version>"* ]]
  done
  [ ! -s "$CALLS" ]
}

@test "a package listed twice is refused" {
  fix "libc6=2.41-12+deb13u5 libc6=2.41-12+deb13u6"
  [ "$status" -ne 0 ]
  [[ "$output" == *"libc6 is listed twice"* ]]
}

@test "a base record for another architecture is refused" {
  base_record libc6 2.41-12+deb13u4 arm64
  fix "libc6=2.41-12+deb13u5"
  [ "$status" -ne 0 ]
  [[ "$output" == *"the base's libc6 is for arm64, this stage is amd64"* ]]
}

@test "a base record for every architecture (all) is accepted" {
  base_record libc6 2.41-12+deb13u4 all
  fix "libc6=2.41-12+deb13u5"
  [ "$status" -eq 0 ]
}

@test "a base record that repeats its Version field is refused" {
  echo "Version: 2.41-12+deb13u9" >> "$BASE/var/lib/dpkg/status.d/libc6"
  fix "libc6=2.41-12+deb13u5"
  [ "$status" -ne 0 ]
  [[ "$output" == *"must hold exactly one Version field"* ]]
  [ ! -s "$CALLS" ]
}

@test "a base record that names another package is refused" {
  sed -i 's/^Package: libc6$/Package: libc6-dev/' "$BASE/var/lib/dpkg/status.d/libc6"
  fix "libc6=2.41-12+deb13u5"
  [ "$status" -ne 0 ]
  [[ "$output" == *"is for libc6-dev, not libc6"* ]]
}

@test "a base version dpkg cannot compare is refused, not taken as older" {
  base_record libc6 "1:"
  fix "libc6=2.41-12+deb13u5"
  [ "$status" -ne 0 ]
  [[ "$output" == *"dpkg cannot compare the base's libc6 version '1:'"* ]]
  [ ! -s "$CALLS" ]
}

@test "a version the archive no longer serves fails and says what to change" {
  STUB_GONE=1 fix "libc6=2.41-12+deb13u5"
  [ "$status" -ne 0 ]
  [[ "$output" == *"could not download libc6=2.41-12+deb13u5"* ]]
  [[ "$output" == *"apt-cache policy"*"wiki/containerization.md#debian-fixes-the-base-has-not-shipped-yet"* ]]
}

@test "a failing apt-get update stops the build and names the fixes" {
  STUB_UPDATE_FAILS=1 fix "libc6=2.41-12+deb13u5"
  [ "$status" -ne 0 ]
  [[ "$output" == *"apt-get update failed"*"libc6=2.41-12+deb13u5 cannot be fetched"* ]]
  [ "$(grep -c 'apt-get download' "$CALLS")" -eq 0 ]
}

@test "a file under a directory the base has as a symlink is refused when the package lists no directories" {
  STUB_PREFIX=lib STUB_FILES_ONLY=1 fix "libc6=2.41-12+deb13u5"
  [ "$status" -ne 0 ]
  [[ "$output" == *"ships /lib/stub/libc6.so under /lib, a symlink in the base"* ]]
}

@test "a fixed package path under a directory the base has as a symlink is refused" {
  STUB_PREFIX=lib fix "libc6=2.41-12+deb13u5"
  [ "$status" -ne 0 ]
  [[ "$output" == *"ships the directory /lib, a symlink in the base: the copy would replace the link"* ]]
}

@test "a fixed package path whose type differs from the base's is refused" {
  : > "$BASE/etc/stub"
  STUB_CONFFILE=x fix "libc6=2.41-12+deb13u5"
  [ "$status" -ne 0 ]
  [[ "$output" == *"ships /etc/stub as a dir, the base has a file"* ]]
}

@test "a fixed package directory whose mode differs from the base's is refused" {
  chmod 700 "$BASE/usr/lib/stub"
  fix "libc6=2.41-12+deb13u5"
  [ "$status" -ne 0 ]
  [[ "$output" == *"ships /usr/lib/stub with another mode or owner than the base's (mode uid gid: 755 "* ]]
}

@test "a conffile that differs from the base's is refused" {
  mkdir -p "$BASE/etc/stub"
  printf 'the base changed this\n' > "$BASE/etc/stub/libc6.conf"
  STUB_CONFFILE=upstream fix "libc6=2.41-12+deb13u5"
  [ "$status" -ne 0 ]
  [[ "$output" == *"conffile /etc/stub/libc6.conf differs from the base's"* ]]
}

@test "a conffile equal to the base's passes" {
  mkdir -p "$BASE/etc/stub"
  printf 'upstream\n' > "$BASE/etc/stub/libc6.conf"
  STUB_CONFFILE=upstream fix "libc6=2.41-12+deb13u5"
  [ "$status" -eq 0 ]
  [[ "$output" != *" adds "* ]]
}

@test "a path the base does not have is added and logged" {
  STUB_NEW_PATH=usr/share/doc/libc6/NEWS fix "libc6=2.41-12+deb13u5"
  [ "$status" -eq 0 ]
  [[ "$output" == *"the fixed libc6 adds /usr/share/doc/libc6/NEWS, which the base does not have"* ]]
  [ -f "$OUT/usr/share/doc/libc6/NEWS" ]
}

@test "a fixed package that no longer ships a file of the base's is refused" {
  printf '%s  usr/lib/stub/%s\n' 0123456789abcdef0123456789abcdef gone.so >> "$BASE/var/lib/dpkg/status.d/libc6.md5sums"
  fix "libc6=2.41-12+deb13u5"
  [ "$status" -ne 0 ]
  [[ "$output" == *"no longer ships files the base has"*"usr/lib/stub/gone.so"* ]]
}

@test "unpacked files that do not match the package's md5sums are refused" {
  STUB_BAD_SUM=1 fix "libc6=2.41-12+deb13u5"
  [ "$status" -ne 0 ]
  [[ "$output" == *"the unpacked libc6 files do not match its md5sums"* ]]
}

@test "a fixed package without md5sums is refused when the base's record has them" {
  STUB_NO_MD5=1 fix "libc6=2.41-12+deb13u5"
  [ "$status" -ne 0 ]
  [[ "$output" == *"the fixed libc6 has no md5sums, the base's record has"* ]]
}

@test "a base without dpkg records is refused" {
  DEBIAN_FIXES="libc6=2.41-12+deb13u5" run bash "$SCRIPT" "$BATS_TEST_TMPDIR/nowhere" "$OUT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"no dpkg records at"* ]]
}
