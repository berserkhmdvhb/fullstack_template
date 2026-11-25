#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# tools.bats — scripts/tools.sh runs mise from the repository root with repo-local directories, ignoring the
# caller's environment and the user's global config. Offline: the installed mise is a stub; the real one
# (when .tools/bin/mise exists) must also see nothing of a hostile config in the home directory, and
# installs a fixture tool from a local server, locked, or refuses it when a checksum has changed.
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
  # shellcheck disable=SC2016 # the stub expands $(pwd) itself
  printf '#!/bin/sh\necho "cwd=$(pwd)"\nenv | grep -E "^(MISE_|GITHUB_|GH_)" | sort\n' > "$BATS_TEST_TMPDIR/mise-linux-x64"
  chmod +x "$BATS_TEST_TMPDIR/mise-linux-x64"
  SHA="$(sha256sum "$BATS_TEST_TMPDIR/mise-linux-x64" | cut -d' ' -f1)"
  printf 'mise\t9.9.9\tlinux-amd64\tfile://%s\t%s\nmise\t9.9.9\tdarwin-arm64\tfile://%s\t%s\n' \
    "$BATS_TEST_TMPDIR/mise-linux-x64" "$SHA" "$BATS_TEST_TMPDIR/mise-linux-x64" "$SHA" > "$PROJ/tools/pins.tsv"
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
    MISE_NODE_VERSION=20.0.0 MISE_JOBS=1 MISE_EXPERIMENTAL=1 \
    bash "$PROJ/scripts/tools.sh" mise
  [ "$status" -eq 0 ]
  [[ "$output" == *"cwd=$PROJ"$'\n'* ]]
  for d in DATA CACHE STATE CONFIG; do [[ "$output" == *"MISE_${d}_DIR=$TOOLS/mise/"* ]]; done
  [[ "$output" == *"MISE_GLOBAL_CONFIG_FILE=$TOOLS/mise/"* ]]
  [[ "$output" == *"MISE_SYSTEM_CONFIG_FILE=$TOOLS/mise/"* ]]
  [[ "$output" == *"MISE_CEILING_PATHS=$HOME"$'\n'* ]]
  [[ "$output" == *"MISE_OVERRIDE_CONFIG_FILENAMES=mise.toml"* ]]
  [[ "$output" != *secret* ]]
  [[ "$output" != *MISE_NODE_VERSION* && "$output" != *MISE_JOBS* && "$output" != *MISE_EXPERIMENTAL* ]]
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
  [[ "$output" != *1.2.3* ]]
  tools bash "$PROJ/scripts/tools.sh" mise env --json
  [[ "$output" != *EVIL* ]]

  [ "$(find "$HOME" -path "$PROJ" -prune -o -print | sort)" = "$before" ]
}

@test "mise gets its own flags, --dry-run included" {
  stub_mise_archive
  printf '#!/bin/sh\nprintf "args=%%s\\n" "$*"\n' > "$BATS_TEST_TMPDIR/mise-linux-x64"
  sed -i "s/$SHA/$(sha256sum "$BATS_TEST_TMPDIR/mise-linux-x64" | cut -d' ' -f1)/" "$PROJ/tools/pins.tsv"
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

teardown() {
  [ -z "${SRV_PID:-}" ] || kill "$SRV_PID" 2>/dev/null || true
}

# The real mise, a project locking one tool (kind) to an artifact served from 127.0.0.1, and a mise "release"
# that is a wrapper around the real one, so tools.sh installs mise from its row first, as it does for real.
fixture_project() {
  if [ ! -x "$ROOT/.tools/bin/mise" ]; then
    [ -z "${CI:-}" ] || { echo "CI must install the pinned mise first: scripts/tools.sh install mise" >&2; return 1; }
    skip "needs the installed mise (scripts/tools.sh install mise)"
  fi
  SRV="$BATS_TEST_TMPDIR/srv"
  REAL_PYTHON3="$(command -v python3)"
  mkdir -p "$SRV"
  printf '#!/bin/sh\necho fake kind\n' > "$SRV/kind-linux-amd64"
  cp "$SRV/kind-linux-amd64" "$SRV/kind-darwin-arm64"
  PORT="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
  python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$SRV" > /dev/null 2>&1 &
  SRV_PID=$!
  ART_SHA="$(sha256sum "$SRV/kind-linux-amd64" | cut -d' ' -f1)"
  sed -n '/^\[settings\]/,/^\]$/p' "$ROOT/mise.toml" > "$PROJ/mise.toml"
  printf '\n[tools]\n"aqua:kubernetes-sigs/kind" = "0.33.0"\n' >> "$PROJ/mise.toml"
  write_lock "$ART_SHA"
  printf '#!/bin/sh\nexec "%s" "$@"\n' "$ROOT/.tools/bin/mise" > "$BATS_TEST_TMPDIR/mise-linux-x64"
  chmod +x "$BATS_TEST_TMPDIR/mise-linux-x64"
  SHA="$(sha256sum "$BATS_TEST_TMPDIR/mise-linux-x64" | cut -d' ' -f1)"
  printf 'mise\t9.9.9\tlinux-amd64\tfile://%s\t%s\n' "$BATS_TEST_TMPDIR/mise-linux-x64" "$SHA" > "$PROJ/tools/pins.tsv"
  sleep 1
}

write_lock() { # sha256 of the kind artifact the lock records
  local key='tools."aqua:kubernetes-sigs/kind"'
  {
    printf 'lockfile_version = 4\n\n[[tools."aqua:kubernetes-sigs/kind"]]\nversion = "0.33.0"\n'
    printf 'backend = "aqua:kubernetes-sigs/kind"\nspecifiers = ["0.33.0"]\n\n'
    printf '[%s."platforms.linux-x64"]\nchecksum = "sha256:%s"\nurl = "http://127.0.0.1:%s/kind-linux-amd64"\n\n' "$key" "$1" "$PORT"
    printf '[%s."platforms.macos-arm64"]\nchecksum = "sha256:%s"\nurl = "http://127.0.0.1:%s/kind-darwin-arm64"\n' "$key" "$1" "$PORT"
    if [ -n "${PY_SHA:-}" ]; then
      printf '\n[[tools.python]]\nversion = "3.13.18"\nbackend = "core:python"\nspecifiers = ["3.13.18"]\n\n'
      printf '[tools.python."platforms.linux-x64"]\nchecksum = "sha256:%s"\nurl = "http://127.0.0.1:%s/python-linux-x64.tar.gz"\n\n' "$PY_SHA" "$PORT"
      printf '[tools.python."platforms.macos-arm64"]\nchecksum = "sha256:%s"\nurl = "http://127.0.0.1:%s/python-linux-x64.tar.gz"\n' "$PY_SHA" "$PORT"
    fi
  } > "$PROJ/mise.lock"
}

# The fixture project also pins Python, served as a tarball whose python3 runs the real one and logs each
# safe_extract.py call it gets (a stand-in for the build mise downloads: same layout, python/bin/python3).
fixture_python() {
  mkdir -p "$BATS_TEST_TMPDIR/pyroot/python/bin" "$BATS_TEST_TMPDIR/pyroot/python/lib/python3.13"
  printf '# system configuration generated and used by the sysconfig module\nbuild_time_vars = {}\n' \
    > "$BATS_TEST_TMPDIR/pyroot/python/lib/python3.13/_sysconfigdata__linux_x86_64-linux-gnu.py"
  cat > "$BATS_TEST_TMPDIR/pyroot/python/bin/python3" <<PY
#!/bin/sh
case "\$*" in *safe_extract.py*) echo "\$*" >> "$BATS_TEST_TMPDIR/mise-python.log" ;; esac
exec "$REAL_PYTHON3" "\$@"
PY
  chmod +x "$BATS_TEST_TMPDIR/pyroot/python/bin/python3"
  ln -s python3 "$BATS_TEST_TMPDIR/pyroot/python/bin/python"
  tar -czf "$SRV/python-linux-x64.tar.gz" -C "$BATS_TEST_TMPDIR/pyroot" python
  PY_SHA="$(sha256sum "$SRV/python-linux-x64.tar.gz" | cut -d' ' -f1)"
  # The real build has a GitHub attestation, which mise checks at install; a local stand-in has none.
  sed -i 's/^\[settings\]$/[settings]\npython.github_attestations = false/' "$PROJ/mise.toml"
  printf '"core:python" = "3.13.18"\n' >> "$PROJ/mise.toml"
  write_lock "$ART_SHA"
}

@test "a locked install of a mise tool downloads it, checks the lock's checksum and links it" {
  fixture_project
  tools bash "$PROJ/scripts/tools.sh" install kind
  [ "$status" -eq 0 ]
  [ -L "$TOOLS/bin/kind" ]
  [ "$("$TOOLS/bin/kind")" = "fake kind" ]
}

@test "a changed byte in a lock checksum is refused: nothing is linked and the exit is non-zero" {
  fixture_project
  write_lock "${ART_SHA:0:63}$([ "${ART_SHA:63:1}" = 0 ] && echo 1 || echo 0)"
  tools bash "$PROJ/scripts/tools.sh" install kind
  [ "$status" -ne 0 ]
  [[ "$output" == *"nothing was linked"* ]]
  [ ! -e "$TOOLS/bin/kind" ]
  [ ! -e "$TOOLS/mise/data/installs/aqua-kubernetes-sigs-kind/0.33.0" ]
}

@test "a changed byte in the downloaded artifact is refused: nothing is linked and the exit is non-zero" {
  fixture_project
  printf '#!/bin/sh\necho changed kind\n' > "$SRV/kind-linux-amd64"
  tools bash "$PROJ/scripts/tools.sh" install kind
  [ "$status" -ne 0 ]
  [ ! -e "$TOOLS/bin/kind" ]
  [ ! -e "$TOOLS/mise/data/installs/aqua-kubernetes-sigs-kind/0.33.0" ]
}

@test "naming two mise tools installs only those two" {
  : > "$PROJ/tools/pins.tsv"
  tools bash "$PROJ/scripts/tools.sh" --dry-run install helm4 kubeconform
  [ "$status" -eq 0 ]
  [[ "$output" == *"mise install --locked helm4 aqua:yannh/kubeconform"$'\n'* ]]
  [[ "$output" != *"yq"* && "$output" != *"node"* && "$output" != *"kind"* ]]
}

INSTALL_DIR='mise/data/installs/aqua-kubernetes-sigs-kind/0.33.0'

# A safe_extract.py that first plants a link out of the first tree it is given, then runs the real one.
plant_a_bad_tree() {
  mv "$PROJ/scripts/safe_extract.py" "$PROJ/scripts/real_safe_extract.py"
  cat > "$PROJ/scripts/safe_extract.py" <<'PY'
import os, runpy, sys
if sys.argv[1] in ("--check", "--remove"):
    os.symlink("/etc", os.path.join(sys.argv[3], "evil"))
runpy.run_path(os.path.join(os.path.dirname(__file__), "real_safe_extract.py"), run_name="__main__")
PY
}

@test "a tree that fails the check: non-zero exit, nothing linked, the tool's install removed" {
  fixture_project
  tools bash "$PROJ/scripts/tools.sh" install mise
  [ "$status" -eq 0 ]
  plant_a_bad_tree
  tools bash "$PROJ/scripts/tools.sh" install kind
  [ "$status" -ne 0 ]
  [[ "$output" == *"outside"* ]]
  [[ "$output" == *"nothing was linked"* ]]
  [ ! -e "$TOOLS/bin/kind" ]
  [ ! -e "$TOOLS/$INSTALL_DIR" ]
  [ -d "$PROJ/scripts" ] && [ -f "$PROJ/mise.toml" ]
}

@test "mise where printing nothing stops tools.sh before the check runs or anything is removed" {
  fixture_project
  # shellcheck disable=SC2016 # the wrapper expands $1 and $@ itself
  printf '#!/bin/sh\n[ "$1" = where ] && exit 0\nexec "%s" "$@"\n' "$ROOT/.tools/bin/mise" > "$BATS_TEST_TMPDIR/mise-linux-x64"
  printf 'mise\t9.9.9\tlinux-amd64\tfile://%s\t%s\n' "$BATS_TEST_TMPDIR/mise-linux-x64" "$(sha256sum "$BATS_TEST_TMPDIR/mise-linux-x64" | cut -d' ' -f1)" > "$PROJ/tools/pins.tsv"
  tools bash "$PROJ/scripts/tools.sh" install mise
  [ "$status" -eq 0 ]
  mv "$PROJ/scripts/safe_extract.py" "$PROJ/scripts/real_safe_extract.py"
  printf 'import pathlib, sys\npathlib.Path(sys.argv[0]).with_name("called").write_text("yes")\n' > "$PROJ/scripts/safe_extract.py"
  tools bash "$PROJ/scripts/tools.sh" install kind
  [ "$status" -ne 0 ]
  [[ "$output" == *"where"* ]]
  [ ! -e "$PROJ/scripts/called" ]
  [ ! -e "$TOOLS/bin/kind" ]
  [ -d "$PROJ/scripts" ] && [ -f "$PROJ/mise.toml" ]
}

@test "a lock checksum changed after the install: the next install refuses and unlinks the tool" {
  fixture_project
  tools bash "$PROJ/scripts/tools.sh" install kind
  [ "$status" -eq 0 ]
  [ -L "$TOOLS/bin/kind" ]
  write_lock "${ART_SHA:0:63}$([ "${ART_SHA:63:1}" = 0 ] && echo 1 || echo 0)"
  tools bash "$PROJ/scripts/tools.sh" install kind
  [ "$status" -ne 0 ]
  [[ "$output" == *"reinstalling"* ]]
  [ ! -e "$TOOLS/bin/kind" ] && [ ! -L "$TOOLS/bin/kind" ]
  [ ! -e "$TOOLS/$INSTALL_DIR" ]
}

@test "only an option changed in mise.toml: the tool is reinstalled; unchanged, it is left alone" {
  fixture_project
  tools bash "$PROJ/scripts/tools.sh" install kind
  [ "$status" -eq 0 ]
  inode="$(stat -c %i "$TOOLS/$INSTALL_DIR/kind")"
  tools bash "$PROJ/scripts/tools.sh" install kind
  [ "$status" -eq 0 ]
  [[ "$output" != *"reinstalling"* ]]
  [ "$(stat -c %i "$TOOLS/$INSTALL_DIR/kind")" = "$inode" ]
  sed -i 's|^"aqua:kubernetes-sigs/kind" = "0.33.0"|"aqua:kubernetes-sigs/kind" = { version = "0.33.0", platforms = { linux-x64 = { rename_exe = "kind" } } }|' "$PROJ/mise.toml"
  tools bash "$PROJ/scripts/tools.sh" install kind
  [ "$status" -eq 0 ]
  [[ "$output" == *"reinstalling"* ]]
  [ "$("$TOOLS/bin/kind")" = "fake kind" ]
  tools bash "$PROJ/scripts/tools.sh" install kind
  [[ "$output" != *"reinstalling"* ]]
}

@test "a reinstall with no downloads directory has nothing to remove there and succeeds" {
  fixture_project
  tools bash "$PROJ/scripts/tools.sh" install kind
  [ "$status" -eq 0 ]
  rm -rf "$TOOLS/mise/data/downloads"
  sed -i 's|^"aqua:kubernetes-sigs/kind" = "0.33.0"|"aqua:kubernetes-sigs/kind" = { version = "0.33.0", platforms = { linux-x64 = { rename_exe = "kind" } } }|' "$PROJ/mise.toml"
  tools bash "$PROJ/scripts/tools.sh" install kind
  [ "$status" -eq 0 ]
  [[ "$output" == *"reinstalling"* ]]
  [ "$("$TOOLS/bin/kind")" = "fake kind" ]
}

# A stock macOS python3 (3.9) first on PATH and no uv yet. mise is installed from its bare executable (nothing to
# extract), uv comes through mise, and the tree check only walks the tree, so none of it needs a newer Python. The fake
# python3 reports an old version and runs the real one, logging each script it runs; PY39=<a real 3.9> replaces it.
old_python3() { # the version it reports: "3.9" or "3.8"
  mkdir -p "$BATS_TEST_TMPDIR/fakebin"
  if [ -n "${PY39:-}" ] && [ "$1" = 3.9 ]; then
    ln -sf "$PY39" "$BATS_TEST_TMPDIR/fakebin/python3"
    return
  fi
  cat > "$BATS_TEST_TMPDIR/fakebin/python3" <<PY
#!/bin/sh
case "\$*" in
  *"(3, 12)"*) exit 1 ;;
  *"(3, 9)"*) exit $([ "$1" = 3.8 ] && echo 1 || echo 0) ;;
esac
case "\$*" in *safe_extract.py*) echo "\$*" >> "$BATS_TEST_TMPDIR/python3.log" ;; esac
exec "$(command -v python3)" "\$@"
PY
  chmod +x "$BATS_TEST_TMPDIR/fakebin/python3"
}

@test "python3 older than 3.12 and no uv: mise installs from its bare executable and the tree check runs on that python3" {
  fixture_project
  old_python3 3.9
  tools env PATH="$BATS_TEST_TMPDIR/fakebin:$PATH" bash "$PROJ/scripts/tools.sh" install kind
  [ "$status" -eq 0 ]
  [ "$("$TOOLS/bin/kind")" = "fake kind" ]
  [ -f "$TOOLS/opt/mise-9.9.9/mise" ]
  if [ -f "$BATS_TEST_TMPDIR/python3.log" ]; then
    ! grep -v -- '--check' "$BATS_TEST_TMPDIR/python3.log"
  fi
}

@test "a real python 3.9 runs the tree check and the removal (PY39=<python3.9>)" {
  [ -n "${PY39:-}" ] || skip "set PY39 to a real Python 3.9"
  fixture_project
  old_python3 3.9
  tools env PATH="$BATS_TEST_TMPDIR/fakebin:$PATH" bash "$PROJ/scripts/tools.sh" install kind
  [ "$status" -eq 0 ]
  [ "$(python3 --version)" ] # the fake dir is first only inside tools
  run env PATH="$BATS_TEST_TMPDIR/fakebin:$PATH" python3 --version
  [[ "$output" == "Python 3.9"* ]]
  # a changed lock entry: the reinstall removes the tool with the 3.9 rmtree
  write_lock "${ART_SHA:0:63}$([ "${ART_SHA:63:1}" = 0 ] && echo 1 || echo 0)"
  tools env PATH="$BATS_TEST_TMPDIR/fakebin:$PATH" bash "$PROJ/scripts/tools.sh" install kind
  [ "$status" -ne 0 ]
  [[ "$output" == *"reinstalling"* ]]
  [ ! -e "$TOOLS/$INSTALL_DIR" ]
}

@test "a stock python3 3.9: Python comes from mise (checksum from the lock), is linked, and the tree check runs on the 3.9" {
  fixture_project
  fixture_python
  old_python3 3.9
  tools env PATH="$BATS_TEST_TMPDIR/fakebin:$PATH" bash "$PROJ/scripts/tools.sh" install python kind
  [ "$status" -eq 0 ]
  [ "$("$TOOLS/bin/kind")" = "fake kind" ]
  [ -L "$TOOLS/bin/python3" ] && [ -L "$TOOLS/bin/python" ]
  [ "$("$TOOLS/bin/python3" -c 'print(7)')" = 7 ]
  [ ! -e "$BATS_TEST_TMPDIR/mise-python.log" ]
  grep -q -- '--check' "$BATS_TEST_TMPDIR/python3.log"
}

# A PATH with every tool of /usr/bin and /bin but no python3 (what a machine without one looks like).
no_python3_path() {
  local d="$BATS_TEST_TMPDIR/nopy" f
  mkdir -p "$d"
  for f in /usr/bin/* /bin/*; do
    case "${f##*/}" in python*|pydoc*|idle*|pip*) ;; *) ln -sf "$f" "$d/" ;; esac
  done
  NOPY="$d"
}

@test "no python3 at all: the tree check runs on the mise Python installed in the same run, which is linked after it" {
  fixture_project
  fixture_python
  no_python3_path
  run -127 env PATH="$NOPY" python3 --version
  tools env PATH="$NOPY" bash "$PROJ/scripts/tools.sh" install python kind
  [ "$status" -eq 0 ]
  [ "$("$TOOLS/bin/kind")" = "fake kind" ]
  [ "$("$TOOLS/bin/python3" -c 'print(8)')" = 8 ]
  grep -q -- '--check' "$BATS_TEST_TMPDIR/mise-python.log"
}

@test "no python3 and Python not installed: refused before anything is linked, with the way out" {
  fixture_project
  fixture_python
  no_python3_path
  tools env PATH="$NOPY" bash "$PROJ/scripts/tools.sh" install kind
  [ "$status" -ne 0 ]
  [[ "$output" == *"install python"* ]]
  [ ! -e "$TOOLS/bin/kind" ]
}

@test "a python3 older than 3.9 and no uv: refused before anything is linked" {
  fixture_project
  old_python3 3.8
  tools env PATH="$BATS_TEST_TMPDIR/fakebin:$PATH" bash "$PROJ/scripts/tools.sh" install kind
  [ "$status" -ne 0 ]
  [[ "$output" == *"needs python"* ]]
  [ ! -e "$TOOLS/bin/kind" ]
}

@test "extracting an archive needs python 3.12 or newer: on an older one it is refused, with the way out" {
  fixture_project
  old_python3 3.9
  mkdir -p "$BATS_TEST_TMPDIR/data"
  echo x > "$BATS_TEST_TMPDIR/data/f"
  tar -czf "$BATS_TEST_TMPDIR/chart.tgz" -C "$BATS_TEST_TMPDIR" data
  printf 'kyverno-chart\t1.0.0\tany\tfile://%s\t%s\n' "$BATS_TEST_TMPDIR/chart.tgz" "$(sha256sum "$BATS_TEST_TMPDIR/chart.tgz" | cut -d' ' -f1)" >> "$PROJ/tools/pins.tsv"
  tools env PATH="$BATS_TEST_TMPDIR/fakebin:$PATH" bash "$PROJ/scripts/tools.sh" install kyverno-chart
  [ "$status" -ne 0 ]
  [[ "$output" == *"python 3.12"*"install python"* ]]
  [ ! -e "$TOOLS/opt/kyverno-chart-1.0.0" ]
}

# macOS has shasum, not GNU sha256sum: a PATH with every tool of /usr/bin but sha256sum, and a shasum that calls it.
no_sha256sum_path() {
  local d="$BATS_TEST_TMPDIR/nosha" f
  mkdir -p "$d"
  for f in /usr/bin/*; do [ "${f##*/}" = sha256sum ] || ln -sf "$f" "$d/"; done
  rm -f "$d/shasum"
  # shellcheck disable=SC2016 # the shim expands its own arguments
  printf '#!/bin/sh\n[ "$1" = -a ] && [ "$2" = 256 ] || exit 2\nshift 2\nexec /usr/bin/sha256sum "$@"\n' > "$d/shasum"
  chmod +x "$d/shasum"
  NOSHA="$d"
}

@test "without sha256sum on PATH the checksum of a download is checked with shasum -a 256" {
  stub_mise_archive
  no_sha256sum_path
  [ ! -e "$NOSHA/sha256sum" ]
  tools env PATH="$NOSHA" bash "$PROJ/scripts/tools.sh" install mise
  [ "$status" -eq 0 ]
  [ -x "$TOOLS/bin/mise" ]
}

@test "without sha256sum a changed byte in the download is still refused" {
  stub_mise_archive
  no_sha256sum_path
  printf 'x' >> "$BATS_TEST_TMPDIR/mise-linux-x64"
  tools env PATH="$NOSHA" bash "$PROJ/scripts/tools.sh" install mise
  [ "$status" -ne 0 ]
  [[ "$output" == *"checksum mismatch"* ]]
  [ ! -e "$TOOLS/bin/mise" ]
}
