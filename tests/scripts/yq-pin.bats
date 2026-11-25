#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# yq-pin.bats — scripts/_yq.sh's pinned_yq: the yq on PATH must be the version mise.toml pins.
# -----------------------------------------------------------------------------

bats_require_minimum_version 1.5.0

setup() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  B="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$B"
  PIN="$(awk -F'"' '$2 == "aqua:mikefarah/yq" {print $4}' "$ROOT/mise.toml")"
}

stub_yq() { # the --version line it prints, and its exit status
  printf '#!/usr/bin/env bash\necho "%s"\nexit %s\n' "$1" "${2:-0}" > "$B/yq"
  chmod +x "$B/yq"
}

# PATH holds only the stub dir (with awk and bash), so a yq installed on the machine is never found.
check() {
  ln -sf "$(command -v awk)" "$B/awk"
  ln -sf "$BASH" "$B/bash"
  run --separate-stderr "$BASH" -c "PATH='$B'; source '$ROOT/scripts/_yq.sh' && pinned_yq '$ROOT'"
}

@test "the pinned version passes" {
  stub_yq "yq (https://github.com/mikefarah/yq/) version v$PIN"
  check
  [ "$status" -eq 0 ]
}

@test "another version is refused, naming both" {
  stub_yq "yq (https://github.com/mikefarah/yq/) version v4.0.0"
  check
  [ "$status" -eq 1 ]
  [[ "$stderr" == *"v4.0.0"*"pinned $PIN"* ]]
}

@test "a yq whose --version fails is refused" {
  stub_yq "boom" 3
  check
  [ "$status" -eq 1 ]
  [[ "$stderr" == *"yq --version failed"* ]]
}

@test "no yq is refused" {
  check
  [ "$status" -eq 1 ]
  [[ "$stderr" == *"yq is required"* ]]
}
