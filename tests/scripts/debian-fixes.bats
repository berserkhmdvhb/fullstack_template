#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# debian-fixes.bats — scripts/docker/debian-fixes.sh: a listed fix replaces the package's files and its dpkg
# record; an empty list fetches nothing; an entry the base already has (that version or newer), a package
# the base does not ship, a malformed or repeated entry, another architecture, a fixed package that drops a
# file of the base's, and unpacked files that fail their md5sums are refused. apt-get, dpkg and dpkg-deb
# are stubs (the script runs for real in the Debian stage of each Dockerfile); the stub dpkg orders
# versions with sort -V, enough for the versions used here.
# -----------------------------------------------------------------------------

setup() {
  ROOT="$(dirname "$BATS_TEST_DIRNAME")/.."
  SCRIPT="$ROOT/scripts/docker/debian-fixes.sh"
  STUBS="$BATS_TEST_TMPDIR/bin"
  BASE="$BATS_TEST_TMPDIR/base-status.d"
  OUT="$BATS_TEST_TMPDIR/out"
  export CALLS="$BATS_TEST_TMPDIR/calls"
  export STUB_ARCH=amd64
  mkdir -p "$STUBS" "$BASE"
  : > "$CALLS"

  cat > "$STUBS/apt-get" <<'SH'
#!/usr/bin/env bash
echo "apt-get $*" >> "$CALLS"
[[ "$1" == download ]] || exit 0
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
    [[ "$3" == ge ]] || exit 2
    mapfile -t order < <(printf '%s\n%s\n' "$2" "$4" | sort -V)
    [[ "$2" == "$4" || "${order[1]}" == "$2" ]] ;;
  *) exit 2 ;;
esac
SH
  # The fixed package ships one file, usr/lib/stub/<package>.so, holding its version.
  cat > "$STUBS/dpkg-deb" <<'SH'
#!/usr/bin/env bash
read -r pkg ver < "$2"
case "$1" in
  -x) mkdir -p "$3/usr/lib/stub" && printf '%s\n' "$ver" > "$3/usr/lib/stub/$pkg.so" ;;
  -e)
    mkdir -p "$3"
    printf 'Package: %s\nVersion: %s\nArchitecture: %s\n' "$pkg" "$ver" "$STUB_ARCH" > "$3/control"
    sum="$(printf '%s\n' "$ver" | md5sum)"
    [[ -z "${STUB_BAD_SUM:-}" ]] || sum="00000000000000000000000000000000"
    printf '%s  usr/lib/stub/%s.so\n' "${sum%% *}" "$pkg" > "$3/md5sums" ;;
  *) exit 2 ;;
esac
SH
  chmod +x "$STUBS"/*
  export PATH="$STUBS:$PATH"
  base_record libc6 2.41-12+deb13u4
}

# base_record <package> <version> [arch]: the base's dpkg record and md5sums for <package>.
base_record() {
  printf 'Package: %s\nVersion: %s\nArchitecture: %s\n' "$1" "$2" "${3:-amd64}" > "$BASE/$1"
  printf '%s  usr/lib/stub/%s.so\n' 0123456789abcdef0123456789abcdef "$1" > "$BASE/$1.md5sums"
}

@test "an empty list writes an empty out dir and fetches nothing" {
  DEBIAN_FIXES="" run bash "$SCRIPT" "$BASE" "$OUT"
  [ "$status" -eq 0 ]
  [ -d "$OUT" ]
  [ -z "$(ls -A "$OUT")" ]
  [ ! -s "$CALLS" ]
}

@test "a listed fix replaces the package's files and its dpkg record" {
  DEBIAN_FIXES="libc6=2.41-12+deb13u5" run bash "$SCRIPT" "$BASE" "$OUT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"libc6 2.41-12+deb13u4 -> 2.41-12+deb13u5"* ]]
  grep -qx 'apt-get download -qq libc6=2.41-12+deb13u5' "$CALLS"
  [ "$(cat "$OUT/usr/lib/stub/libc6.so")" = "2.41-12+deb13u5" ]
  grep -qx 'Version: 2.41-12+deb13u5' "$OUT/var/lib/dpkg/status.d/libc6"
  grep -q ' usr/lib/stub/libc6.so$' "$OUT/var/lib/dpkg/status.d/libc6.md5sums"
}

@test "several fixes are fetched in one download" {
  base_record libc-bin 2.41-12+deb13u4
  DEBIAN_FIXES="libc6=2.41-12+deb13u5 libc-bin=2.41-12+deb13u5" run bash "$SCRIPT" "$BASE" "$OUT"
  [ "$status" -eq 0 ]
  grep -qx 'apt-get download -qq libc6=2.41-12+deb13u5 libc-bin=2.41-12+deb13u5' "$CALLS"
  grep -qx 'Version: 2.41-12+deb13u5' "$OUT/var/lib/dpkg/status.d/libc-bin"
}

@test "an entry the base already ships at that version is refused and named for removal" {
  DEBIAN_FIXES="libc6=2.41-12+deb13u4" run bash "$SCRIPT" "$BASE" "$OUT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"the base already ships libc6 2.41-12+deb13u4"* ]]
  [[ "$output" == *"remove 'libc6=2.41-12+deb13u4' from DEBIAN_FIXES"* ]]
  [ ! -s "$CALLS" ]
}

@test "an entry older than the base's version is refused: a fix never downgrades the base" {
  base_record libc6 2.41-12+deb13u6
  DEBIAN_FIXES="libc6=2.41-12+deb13u5" run bash "$SCRIPT" "$BASE" "$OUT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"the base already ships libc6 2.41-12+deb13u6"* ]]
  [ ! -s "$CALLS" ]
}

@test "a package the base does not ship is refused" {
  DEBIAN_FIXES="libssl3t64=3.5.4-1~deb13u2" run bash "$SCRIPT" "$BASE" "$OUT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"the base does not ship libssl3t64"* ]]
  [ ! -s "$CALLS" ]
}

@test "an entry that is not package=version is refused" {
  for bad in "libc6" "libc6=" "=2.41" "Libc6=2.41-12" "libc6=2.41-12+deb13u5,libc-bin" "libc6==2.41"; do
    DEBIAN_FIXES="$bad" run bash "$SCRIPT" "$BASE" "$OUT"
    [ "$status" -ne 0 ]
    [[ "$output" == *"is not <package>=<version>"* ]]
  done
  [ ! -s "$CALLS" ]
}

@test "a package listed twice is refused" {
  DEBIAN_FIXES="libc6=2.41-12+deb13u5 libc6=2.41-12+deb13u6" run bash "$SCRIPT" "$BASE" "$OUT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"libc6 is listed twice"* ]]
}

@test "a base record for another architecture is refused" {
  base_record libc6 2.41-12+deb13u4 arm64
  DEBIAN_FIXES="libc6=2.41-12+deb13u5" run bash "$SCRIPT" "$BASE" "$OUT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"the base's libc6 is for arm64, this stage is amd64"* ]]
}

@test "a fixed package that no longer ships a file of the base's is refused" {
  printf '%s  usr/lib/stub/%s\n' 0123456789abcdef0123456789abcdef gone.so >> "$BASE/libc6.md5sums"
  DEBIAN_FIXES="libc6=2.41-12+deb13u5" run bash "$SCRIPT" "$BASE" "$OUT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"no longer ships files the base has"*"usr/lib/stub/gone.so"* ]]
}

@test "unpacked files that do not match the package's md5sums are refused" {
  STUB_BAD_SUM=1 DEBIAN_FIXES="libc6=2.41-12+deb13u5" run bash "$SCRIPT" "$BASE" "$OUT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"the unpacked libc6 files do not match its md5sums"* ]]
}

@test "a missing dpkg record directory is refused" {
  DEBIAN_FIXES="libc6=2.41-12+deb13u5" run bash "$SCRIPT" "$BATS_TEST_TMPDIR/nowhere" "$OUT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"no dpkg records at"* ]]
}
