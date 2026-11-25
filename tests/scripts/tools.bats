#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# tools.bats — scripts/tools.sh runs mise from the repository root with repo-local directories, ignoring the
# caller's environment and the user's global config. Offline: the installed mise is a stub; the real one
# (when .tools/bin/mise exists) must also see nothing of a hostile config in the home directory.
# -----------------------------------------------------------------------------

setup() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  export HOME="$BATS_TEST_TMPDIR/home"
  PROJ="$HOME/proj"
  mkdir -p "$PROJ/scripts" "$PROJ/tools" "$HOME/.config/mise"
  cp "$ROOT/scripts/tools.sh" "$ROOT/scripts/safe_extract.py" "$PROJ/scripts/"
  cp "$ROOT/mise.toml" "$PROJ/"
  TOOLS="$PROJ/.tools"
}

# A mise release archive whose mise prints where it runs and the environment it got.
stub_mise_archive() {
  mkdir -p "$BATS_TEST_TMPDIR/art/mise/bin"
  # shellcheck disable=SC2016 # the stub expands $(pwd) itself
  printf '#!/bin/sh\necho "cwd=$(pwd)"\nenv | grep -E "^(MISE_|GITHUB_|GH_)" | sort\n' > "$BATS_TEST_TMPDIR/art/mise/bin/mise"
  chmod +x "$BATS_TEST_TMPDIR/art/mise/bin/mise"
  tar -czf "$BATS_TEST_TMPDIR/mise.tar.gz" -C "$BATS_TEST_TMPDIR/art" mise
  SHA="$(sha256sum "$BATS_TEST_TMPDIR/mise.tar.gz" | cut -d' ' -f1)"
  printf 'mise\t9.9.9\tlinux-amd64\tfile://%s\t%s\nmise\t9.9.9\tdarwin-arm64\tfile://%s\t%s\n' \
    "$BATS_TEST_TMPDIR/mise.tar.gz" "$SHA" "$BATS_TEST_TMPDIR/mise.tar.gz" "$SHA" > "$PROJ/tools/pins.tsv"
}

tools() {
  run env PINS="$PROJ/tools/pins.tsv" TOOLS_DIR="$TOOLS" TOOLS_PROTO=file "$@"
}

@test "mise runs from the repository root with repo-local dirs, whatever the caller exports" {
  stub_mise_archive
  tools bash "$PROJ/scripts/tools.sh" install mise
  [ "$status" -eq 0 ]
  cd /
  run env PINS="$PROJ/tools/pins.tsv" TOOLS_DIR="$TOOLS" \
    MISE_DATA_DIR=/elsewhere MISE_CONFIG_DIR="$HOME/.config/mise" MISE_GLOBAL_CONFIG_FILE="$HOME/.config/mise/config.toml" \
    GITHUB_TOKEN=secret MISE_GITHUB_TOKEN=secret GH_TOKEN=secret \
    bash "$PROJ/scripts/tools.sh" mise
  [ "$status" -eq 0 ]
  [[ "$output" == *"cwd=$PROJ"$'\n'* ]]
  for d in DATA CACHE STATE CONFIG; do [[ "$output" == *"MISE_${d}_DIR=$TOOLS/mise/"* ]]; done
  [[ "$output" == *"MISE_GLOBAL_CONFIG_FILE=$TOOLS/mise/"* ]]
  [[ "$output" == *"MISE_SYSTEM_CONFIG_FILE=$TOOLS/mise/"* ]]
  [[ "$output" == *"MISE_CEILING_PATHS=$HOME"$'\n'* ]]
  [[ "$output" == *"MISE_OVERRIDE_CONFIG_FILENAMES=mise.toml"* ]]
  [[ "$output" != *secret* ]]
  [[ "$output" != *"/elsewhere"* ]]
}

@test "nothing is written to the home directory" {
  stub_mise_archive
  rmdir "$HOME/.config/mise" "$HOME/.config"
  tools bash "$PROJ/scripts/tools.sh" install mise
  [ "$status" -eq 0 ]
  tools bash "$PROJ/scripts/tools.sh" mise
  [ "$status" -eq 0 ]
  run find "$HOME" -path "$PROJ" -prune -o -mindepth 1 -print
  [ -z "$output" ]
}

@test "a hostile config in the home directory changes nothing (the real mise)" {
  if [ ! -x "$ROOT/.tools/bin/mise" ]; then
    [ -z "${CI:-}" ] || { echo "CI must install the pinned mise first: scripts/tools.sh install mise" >&2; return 1; }
    skip "needs the installed mise (scripts/tools.sh install mise)"
  fi
  mkdir -p "$TOOLS/bin"
  ln -s "$ROOT/.tools/bin/mise" "$TOOLS/bin/mise"
  printf '[settings]\ndisable_backends = []\nuse_versions_host = true\nlockfile = false\n[tools]\nnode = "1.2.3"\n[env]\nEVIL = "1"\n' \
    | tee "$HOME/.config/mise/config.toml" "$HOME/mise.toml" > "$HOME/.mise.toml"
  printf 'ignored_config_paths = ["%s"]\noverride_config_filenames = [".mise.toml"]\n' "$PROJ" > "$HOME/.config/mise/miserc.toml"
  before="$(find "$HOME" -path "$PROJ" -prune -o -print | sort)"

  tools bash "$PROJ/scripts/tools.sh" mise config ls --json
  [ "$status" -eq 0 ]
  [ "$(printf '%s' "$output" | grep -c '"path"')" -eq 1 ]
  [[ "$output" == *"\"path\": \"$PROJ/mise.toml\""* ]]

  tools bash "$PROJ/scripts/tools.sh" mise settings get use_versions_host
  [ "$output" = "false" ]
  tools bash "$PROJ/scripts/tools.sh" mise settings get lockfile
  [ "$output" = "true" ]
  tools bash "$PROJ/scripts/tools.sh" mise ls --json
  [ "$output" = "{}" ]
  tools bash "$PROJ/scripts/tools.sh" mise env --json
  [[ "$output" != *EVIL* ]]

  [ "$(find "$HOME" -path "$PROJ" -prune -o -print | sort)" = "$before" ]
}

@test "mise gets its own flags, --dry-run included" {
  stub_mise_archive
  printf '#!/bin/sh\nprintf "args=%%s\\n" "$*"\n' > "$BATS_TEST_TMPDIR/art/mise/bin/mise"
  tar -czf "$BATS_TEST_TMPDIR/mise.tar.gz" -C "$BATS_TEST_TMPDIR/art" mise
  sed -i "s/$SHA/$(sha256sum "$BATS_TEST_TMPDIR/mise.tar.gz" | cut -d' ' -f1)/" "$PROJ/tools/pins.tsv"
  tools bash "$PROJ/scripts/tools.sh" install mise
  [ "$status" -eq 0 ]
  tools bash "$PROJ/scripts/tools.sh" mise install --dry-run
  [ "$status" -eq 0 ]
  [ "$output" = "args=install --dry-run" ]
}

@test "mise before it is installed says so" {
  tools bash "$PROJ/scripts/tools.sh" mise --version
  [ "$status" -ne 0 ]
  [[ "$output" == *"mise is not installed"*"install mise"* ]]
}
