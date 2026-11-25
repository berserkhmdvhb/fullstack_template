#!/usr/bin/env bash
# Install the pinned dev toolchain into ./.tools (never system-wide, never via a piped installer).
#
# Usage: scripts/tools.sh [--dry-run] install [name...]   install all pins, or just the named ones
#                                                         (crd-sources: the pinned CRD sources)
#        scripts/tools.sh list                            show the pins for this platform
#        scripts/tools.sh mise [args...]                  run the pinned mise (below) from the repository root
#
# Most tools are installed by mise: `mise install --locked` from mise.toml and mise.lock (a sha256 per platform,
# and the vendor's signature or attestation where its recipe has one), then checked as a tree (scripts/safe_extract.py
# --check) and linked into .tools/bin. A mismatch or a failed check links nothing and exits non-zero. The rows left in
# tools/pins.tsv (mise itself, Python, the uv tools, two data files) download to a temp file, are checked against the
# sha256 in that file, and only then extracted. Python tools install from hash-pinned requirements
# (tools/requirements/<name>.txt) into their own venv.
# mise (the first pin) is repo-local and hermetic: its data, cache, state and config live under $TOOLS_DIR/mise, the
# user's global and system config and every parent directory's config are out of reach, and no GitHub token or usage
# report is used. The project settings are in mise.toml.
# Sibling of infra/security/tools-install.sh (security scanners); this one is for developer tools.
# uv keeps its cache and its Pythons under $TOOLS_DIR/uv, so nothing is written outside the repository.
# Env overrides (used by the tests): PINS, TOOLS_DIR, TOOLS_PROTO (curl --proto, default =https).
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PINS="${PINS:-$ROOT/tools/pins.tsv}"
REQS="${REQS:-$ROOT/tools/requirements}"
TOOLS_DIR="${TOOLS_DIR:-$ROOT/.tools}"
DRY_RUN=false # only --dry-run sets it
# The tools mise installs, in install order (uv before the Python tools that use it).
MISE_TOOLS="node uv zizmor prek helm3 helm4 kubectl kubectl-floor kind kubeconform promtool ct helm-unittest actionlint hadolint
  gitleaks squawk oasdiff cosign kyverno yq bats shellcheck lychee"
TMP="" PART=""

# The environment every mise run gets; it overrides whatever the caller has set. No other MISE_ variable gets
# through (MISE_NODE_VERSION, MISE_<TOOL>_VERSION and the like would change what is installed).
for _v in $(compgen -e | grep '^MISE_' || true); do unset "$_v"; done
export MISE_DATA_DIR="$TOOLS_DIR/mise/data" MISE_CACHE_DIR="$TOOLS_DIR/mise/cache" MISE_STATE_DIR="$TOOLS_DIR/mise/state"
export MISE_CONFIG_DIR="$TOOLS_DIR/mise/config" MISE_TMP_DIR="$TOOLS_DIR/mise/tmp" MISE_SYSTEM_DATA_DIR="$TOOLS_DIR/mise/system"
export MISE_SYSTEM_CONFIG_DIR="$TOOLS_DIR/mise/system-config"
INSTALLS="$MISE_DATA_DIR/installs" DOWNLOADS="$MISE_DATA_DIR/downloads"
# Global and system config are single files that are never created; mise.toml in the repository is the only config.
export MISE_GLOBAL_CONFIG_FILE="$TOOLS_DIR/mise/no-global.toml" MISE_SYSTEM_CONFIG_FILE="$TOOLS_DIR/mise/no-system.toml"
ABOVE_ROOT="$(dirname "$ROOT")"
export MISE_CEILING_PATHS="$ABOVE_ROOT" MISE_OVERRIDE_CONFIG_FILENAMES=mise.toml
export MISE_TRUSTED_CONFIG_PATHS="$ROOT" MISE_GITHUB_GH_CLI_TOKENS=false
export UV_CACHE_DIR="$TOOLS_DIR/uv/cache" UV_PYTHON_INSTALL_DIR="$TOOLS_DIR/uv/python"
# The Makefile sets uv up for the project venv (a Python request, managed-only, downloads off): not for the installer, which
# installs the pinned Python by an explicit `uv python install` and builds each tool venv on it by path.
export UV_PYTHON_DOWNLOADS=manual
unset UV_PYTHON UV_PYTHON_PREFERENCE
unset GITHUB_TOKEN GITHUB_API_TOKEN GH_TOKEN MISE_GITHUB_TOKEN MISE_GITHUB_ENTERPRISE_TOKEN

die() { echo "tools: $*" >&2; exit 1; }
cleanup() { rm -rf "${TMP:-}" "${PART:-}"; }
trap cleanup EXIT

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) PLATFORM=linux-amd64 ;;
  Darwin-arm64) PLATFORM=darwin-arm64 ;;
  *) die "unsupported platform $(uname -s)-$(uname -m)" ;;
esac

# run: print the argv and execute nothing under --dry-run.
run() {
  if [[ "$DRY_RUN" == "true" ]]; then echo "  -> $*"; else "$@"; fi
}

# Executable path(s) inside the unpacked artifact of a pins.tsv row: "<relpath>:<link name>".
exes_for() {
  case "$1" in
    mise) echo "mise:mise" ;; # the release asset is the bare executable
    k8s-openapi|kyverno-chart|external-secrets-chart) echo "" ;; # data (kubeconform.sh and the kind tests read them)
    *) echo "$1:$1" ;;
  esac
}

# What mise installs, per name: "<tool in mise.toml> <executable>[:<link name>]...". The name is what workflows pass to
# `install` and what lands in .tools/bin (helm3, helm4 and kubectl-floor are links to the one `helm` and `kubectl` each
# provides); "-" is a Helm plugin directory, linked into the plugin directory.
mise_spec() {
  case "$1" in
    node) echo "core:node node npm npx" ;;
    uv) echo "aqua:astral-sh/uv uv uvx" ;;
    zizmor) echo "aqua:zizmorcore/zizmor zizmor" ;;
    prek) echo "aqua:j178/prek prek" ;;
    helm3) echo "helm3 helm:helm3" ;;
    helm4) echo "helm4 helm:helm4" ;;
    kubectl) echo "aqua:kubernetes/kubernetes/kubectl kubectl" ;;
    kubectl-floor) echo "kubectl-floor kubectl:kubectl-floor" ;;
    kind) echo "aqua:kubernetes-sigs/kind kind" ;;
    kubeconform) echo "aqua:yannh/kubeconform kubeconform" ;;
    promtool) echo "github:prometheus/prometheus promtool" ;;
    ct) echo "aqua:helm/chart-testing ct" ;;
    helm-unittest) echo "github:helm-unittest/helm-unittest -" ;;
    actionlint) echo "aqua:rhysd/actionlint actionlint" ;;
    hadolint) echo "aqua:hadolint/hadolint hadolint" ;;
    gitleaks) echo "aqua:gitleaks/gitleaks gitleaks" ;;
    squawk) echo "github:sbdchd/squawk squawk" ;;
    oasdiff) echo "github:oasdiff/oasdiff oasdiff" ;;
    cosign) echo "aqua:sigstore/cosign cosign" ;;
    kyverno) echo "aqua:kyverno/kyverno kyverno" ;;
    yq) echo "aqua:mikefarah/yq yq" ;;
    bats) echo "aqua:bats-core/bats-core bats" ;;
    shellcheck) echo "aqua:koalaman/shellcheck shellcheck" ;;
    lychee) echo "aqua:lycheeverse/lychee lychee" ;;
    *) return 1 ;;
  esac
}

is_mise_tool() { [[ " ${MISE_TOOLS//$'\n'/ } " == *" $1 "* ]]; }

mise_version() { # tool key -> its version in mise.toml [tools]
  awk -v k="$1" '/^\[/ {t = ($0 == "[tools]"); next}
    t && !/^#/ && /=/ { key=$0; sub(/ *=.*/, "", key); gsub(/"/, "", key)
      if (key != k) next
      v=$0; sub(/^[^=]*= */, "", v)
      if (v ~ /^\{/) { match(v, /version *= *"[^"]*"/); v=substr(v, RSTART, RLENGTH); sub(/^version *= *"/, "", v); sub(/"$/, "", v) }
      else gsub(/[" ]/, "", v)
      print v; exit }' "$ROOT/mise.toml"
}

mise_run() { (cd "$ROOT" && "$TOOLS_DIR/bin/mise" "$@"); }

# mise.lock's tables for a tool (core:node is locked as node, any other name by its quoted key), then the
# tool's lines in mise.toml: together they say what is installed and how, so a change in either must reinstall.
lock_entry() { # tool key
  local k="${1#core:}"
  [[ "$k" == *:* ]] && k="\"$k\""
  awk -v k="$k" '/^\[/ { on = (index($0, "[[tools." k "]]") == 1 || index($0, "[tools." k ".") == 1) } on' "$ROOT/mise.lock"
}
config_entry() { # tool key
  awk -v k="$1" '/^\[/ { t = ($0 == "[tools]" || $0 == "[tool_alias]"); next }
    t && !/^#/ && /=/ { key=$0; sub(/ *=.*/, "", key); gsub(/"/, "", key); if (key == k) print }' "$ROOT/mise.toml"
}
# sha256 of stdin or of the file given: GNU sha256sum, or shasum -a 256 where that is what there is (macOS).
sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$@"; else shasum -a 256 "$@"; fi | cut -d' ' -f1
}
stamp_of() { { lock_entry "$1"; config_entry "$1"; } | sha256; }

# Unlink a mise tool and remove its install (and the download mise kept), inside mise's own directories only.
unlink_and_remove() { # name key exes install-dir
  local name="$1" exes="$3" dir="$4" pair slug
  for pair in $exes; do rm -f "$TOOLS_DIR/bin/${pair##*:}"; done
  [[ "$exes" != "-" ]] || rm -f "$TOOLS_DIR/helm-plugins/$name"
  rm -f "$TOOLS_DIR/mise/stamps/$name"
  slug="${dir#"$INSTALLS"/}"
  "$(tree_python)" -I "$ROOT/scripts/safe_extract.py" --remove "$INSTALLS" "$dir" || die "$name: could not remove its install"
  "$(tree_python)" -I "$ROOT/scripts/safe_extract.py" --remove "$DOWNLOADS" "$DOWNLOADS/$slug" || die "$name: could not remove its download"
}

# Install the named tools with the pinned mise, locked; check each installed tree; only then link anything.
# A tool whose lock entry or mise.toml entry differs from the one it was installed with is removed first.
install_mise_tools() { # names...
  local name key exes pair exe link bp found dir keys=() dirs=()
  for name in "$@"; do
    read -r key exes <<<"$(mise_spec "$name")"
    keys+=("$key")
    echo "==> $name $(mise_version "$key") (mise, locked)"
  done
  if [[ "$DRY_RUN" == "true" ]]; then
    echo "  -> mise install --locked ${keys[*]}"
    echo "  -> check each installed tree, then link into $TOOLS_DIR/bin"
    return
  fi
  [[ -x "$TOOLS_DIR/bin/mise" ]] || die "mise is not installed; run: scripts/tools.sh install mise"
  NAME=mise-tools
  for name in "$@"; do
    read -r key exes <<<"$(mise_spec "$name")"
    dir="$(mise_run where "$key" 2>/dev/null || true)"
    [[ -n "$dir" && -d "$dir" ]] || continue
    [[ "$(cat "$TOOLS_DIR/mise/stamps/$name" 2>/dev/null || true)" != "$(stamp_of "$key")" ]] || continue
    echo "==> $name: its lock entry or mise.toml entry changed, reinstalling"
    unlink_and_remove "$name" "$key" "$exes" "$dir"
  done
  mise_run install --locked "${keys[@]}" || die "mise install failed; nothing was linked"
  for key in "${keys[@]}"; do
    dir="$(mise_run where "$key")" || die "mise where $key failed; nothing was linked"
    [[ -n "$dir" ]] || die "mise where $key printed no directory; nothing was linked"
    dirs+=("$dir")
  done
  "$(tree_python)" -I "$ROOT/scripts/safe_extract.py" --check "$INSTALLS" "${dirs[@]}" || die "an installed tool failed the tree check; nothing was linked"
  mkdir -p "$TOOLS_DIR/bin" "$TOOLS_DIR/mise/stamps"
  for name in "$@"; do
    read -r key exes <<<"$(mise_spec "$name")"
    if [[ "$exes" == "-" ]]; then
      mkdir -p "$TOOLS_DIR/helm-plugins"
      ln -sfn "$(mise_run where "$key")" "$TOOLS_DIR/helm-plugins/$name"
    else
      for pair in $exes; do
        exe="${pair%%:*}"; link="${pair##*:}"; found=""
        for bp in $(mise_run bin-paths "$key"); do
          [[ -x "$bp/$exe" ]] && { found="$bp/$exe"; break; }
        done
        [[ -n "$found" ]] || die "$name: '$exe' not found in what mise installed"
        ln -sfn "$found" "$TOOLS_DIR/bin/$link"
      done
    fi
    stamp_of "$key" > "$TOOLS_DIR/mise/stamps/$name"
  done
}

# Entry points a Python tool's venv exposes in .tools/bin.
uv_exes_for() {
  case "$1" in
    schemathesis) echo "schemathesis st" ;;
    *) echo "$1" ;;
  esac
}

verify() { # file sha256
  local got
  got="$(sha256 "$1")"
  [[ "$got" == "$2" ]] || die "checksum mismatch for $NAME $VER: expected $2, got $got; refusing to install"
}

# Interpreter for extracting an archive (tarfile's "data" filter, which needs Python 3.12): a system python3 >= 3.12, else
# the uv-managed one (installed before any archive is extracted), else a message that says how to get it.
safe_python() {
  if python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' 2>/dev/null; then
    command -v python3
  elif uv python find --managed-python "$(pin_version python)" 2>/dev/null; then
    :
  else
    die "$NAME: extracting an archive needs python 3.12 or newer (the system python3 is older); run: scripts/tools.sh install python"
  fi
}

# Interpreter for the tree check and the removal (safe_extract.py --check, --remove): they only walk and remove, so
# the 3.9 that macOS ships is enough; with anything older, as safe_python.
tree_python() {
  if python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then
    command -v python3
  else
    safe_python
  fi
}

# Extract with tarfile's "data" filter (refuses escaping names, links, devices; strips setuid).
safe_extract() { # archive dest
  local py
  py="$(safe_python)"
  "$py" -I "$ROOT/scripts/safe_extract.py" "$1" "$2" || die "$NAME: unsafe archive; refusing to install"
}

link_exes() { # dir of the unpacked artifact
  local pair rel name
  for pair in $(exes_for "$NAME"); do
    rel="${pair%%:*}"; name="${pair##*:}"
    [[ "$DRY_RUN" == "true" || -f "$1/$rel" ]] || die "$NAME: '$rel' not found in the artifact"
    run chmod +x "$1/$rel"
    run ln -sfn "$1/$rel" "$TOOLS_DIR/bin/$name"
  done
}

# The pinned uv, once installed, is the one every uv: row uses.
use_pinned_uv() { [[ -x "$TOOLS_DIR/bin/uv" ]] && PATH="$TOOLS_DIR/bin:$PATH"; return 0; }

pin_version() { # name -> version of that pin (platform "any")
  awk -F'\t' -v n="$1" '!/^#/ && $1==n && $3=="any" {print $2; exit}' "$PINS"
}

install_uv() { # pkg
  local pkg="$1" req venv py exe pybin
  req="$REQS/$NAME.txt"
  [[ -f "$req" ]] || die "$NAME: missing hash-pinned requirements $req; refusing to install"
  grep -qiE "^${pkg//./\\.}==${VER//./\\.}([[:space:]\\]|\$)" "$req" || die "$NAME: $req does not pin $pkg==$VER; refusing to install"
  grep -q -- '--hash=sha256:' "$req" || die "$NAME: $req has no hashes; refusing to install"
  py="$(pin_version python)"
  venv="$TOOLS_DIR/uv-tools/$NAME"
  echo "==> $NAME $VER (uv, --require-hashes, python ${py:-default})"
  # The uv-managed interpreter by path, never a system python that happens to match 3.13.
  if [[ "$DRY_RUN" == "true" ]]; then
    pybin="<uv-managed python ${py:-3.13}>"
  else
    pybin="$(uv python find --managed-python "${py:-3.13}" 2>/dev/null || true)"
    if [[ -z "$pybin" ]]; then
      UV_PYTHON_BIN_DIR="$TOOLS_DIR/bin" uv python install "${py:-3.13}"
      pybin="$(uv python find --managed-python "${py:-3.13}")" || die "$NAME: no uv-managed python ${py:-3.13}"
    fi
  fi
  run rm -rf "$venv"
  run uv venv --quiet --python "$pybin" "$venv"
  run uv pip install --quiet --python "$venv/bin/python" --require-hashes --no-deps -r "$req"
  run mkdir -p "$TOOLS_DIR/bin"
  for exe in $(uv_exes_for "$NAME"); do
    run ln -sfn "$venv/bin/$exe" "$TOOLS_DIR/bin/$exe"
  done
}

install_pin() { # uses NAME VER URL SHA
  local dest file
  case "$URL" in
    uv:python)
      run env UV_PYTHON_BIN_DIR="$TOOLS_DIR/bin" uv python install "$VER"; return ;;
    uv:*) install_uv "${URL#uv:}"; return ;;
  esac
  dest="$TOOLS_DIR/opt/$NAME-$VER"
  # (mise was once an unpacked archive: a directory where its bare executable is now, which is reinstalled.)
  if [[ -d "$dest" && ( "$NAME" != mise || -f "$dest/mise" ) && "$DRY_RUN" != "true" ]]; then
    echo "$NAME $VER already installed"
  else
    echo "==> $NAME $VER ($URL)"
    if [[ "$DRY_RUN" == "true" ]]; then
      echo "  -> download, verify sha256 $SHA, extract to $dest"
    else
      TMP="$(mktemp -d)"
      file="$TMP/${URL##*/}"
      curl -fsSL --retry 3 --retry-connrefused --connect-timeout 15 --speed-limit 1024 --speed-time 30 \
        --proto "${TOOLS_PROTO:-=https}" --proto-redir =https -o "$file" "$URL"
      verify "$file" "$SHA"
      PART="$dest.part"
      rm -rf "$PART"
      mkdir -p "$PART"
      if [[ "$URL" == *.tar.* || "$URL" == *.tgz ]]; then
        safe_extract "$file" "$PART"
      else
        cp "$file" "$PART/$NAME"
      fi
      rm -rf "$dest"
      mv "$PART" "$dest"
      PART=""
      rm -rf "$TMP"
      TMP=""
    fi
  fi
  run mkdir -p "$TOOLS_DIR/bin"
  link_exes "$dest"
}

# split_row LINE: set NAME VER PLAT URL SHA from exactly five tab-separated, non-empty fields.
split_row() {
  local line="$1" rest tabs
  tabs="${line//[^$'\t']/}"
  [[ ${#tabs} -eq 4 ]] || die "malformed pin (need exactly 5 tab-separated fields): $line"
  NAME="${line%%$'\t'*}"; rest="${line#*$'\t'}"
  VER="${rest%%$'\t'*}"; rest="${rest#*$'\t'}"
  PLAT="${rest%%$'\t'*}"; rest="${rest#*$'\t'}"
  URL="${rest%%$'\t'*}"; SHA="${rest#*$'\t'}"
  [[ -n "$NAME" && -n "$VER" && -n "$PLAT" && -n "$URL" && -n "$SHA" ]] || die "malformed pin (empty field): $line"
}

cmd_install() {
  local wanted=" $* " found=" " line helm_default n pass NAME VER PLAT URL SHA tools=()
  [[ -r "$PINS" ]] || die "cannot read $PINS"
  use_pinned_uv
  # A named Python tool needs the pinned uv: install it first when it is not there yet.
  if [[ $# -gt 0 && ! -x "$TOOLS_DIR/bin/uv" ]] \
    && awk -F'\t' -v w="$wanted" '!/^#/ && index(w, " " $1 " ") && $4 ~ /^uv:/ {f=1} END {exit !f}' "$PINS"; then
    wanted+="uv "
  fi
  for n in $MISE_TOOLS; do
    [[ $# -eq 0 || "$wanted" == *" $n "* ]] && tools+=("$n")
  done
  # Installing a mise tool installs mise first, from its row.
  [[ ${#tools[@]} -eq 0 ]] || wanted+="mise "
  found+="${tools[*]} "
  # The mise row (a bare executable: nothing to extract), then the mise tools (uv among them), then Python, then the other
  # rows: an archive is extracted only with Python 3.12, which a stock macOS gets from uv here, and the uv tools need uv.
  for pass in mise python rest; do
    if [[ "$pass" == python && ${#tools[@]} -gt 0 ]]; then
      install_mise_tools "${tools[@]}"
      use_pinned_uv
    fi
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ -z "$line" || "$line" == \#* ]] && continue
      split_row "$line"
      [[ "$PLAT" == any || "$PLAT" == "$PLATFORM" ]] || continue
      case "$NAME" in mise) [[ "$pass" == mise ]] ;; python) [[ "$pass" == python ]] ;; *) [[ "$pass" == rest ]] ;; esac || continue
      [[ $# -eq 0 || "$wanted" == *" $NAME "* ]] || continue
      found+="$NAME "
      [[ "$URL" == uv:* || "$SHA" =~ ^[0-9a-f]{64}$ ]] || die "$NAME: pin has no sha256"
      install_pin
    done < "$PINS"
  done
  # crd-sources: the upstream CRDs pinned with their sha256 in charts/your-service-name/ci/crds/sources.tsv,
  # cached in $TOOLS_DIR/crd-sources (scripts/ci/kube_schemas.py checks each digest; a mismatch is refused).
  # The CRD schema check and its test read only that cache. Named only (`make tools` names it), so a
  # bare install fetches nothing but the pins.
  if [[ "$wanted" == *" crd-sources "* ]]; then
    NAME=crd-sources
    found+="crd-sources "
    echo "==> crd-sources (charts/your-service-name/ci/crds/sources.tsv)"
    run env CRD_CACHE="$TOOLS_DIR/crd-sources" "$(safe_python)" -I "$ROOT/scripts/ci/kube_schemas.py" crds fetch
  fi
  for n in "$@"; do [[ "$found" == *" $n "* ]] || die "no pin named '$n' for $PLATFORM"; done
  helm_default="$(sed -n 's/^#default-helm: *//p' "$PINS")"
  if [[ -n "$helm_default" && ( $# -eq 0 || "$wanted" == *" helm$helm_default "* ) ]]; then
    run ln -sfn "helm$helm_default" "$TOOLS_DIR/bin/helm"
  fi
  if [[ "$DRY_RUN" == "true" ]]; then echo "dry-run: nothing was installed"; else echo "done: tools in $TOOLS_DIR/bin"; fi
}

cmd_list() { # name version [mise tool]
  local n key
  awk -F'\t' -v p="$PLATFORM" '!/^#/ && NF && ($3=="any" || $3==p) {print $1, $2}' "$PINS"
  for n in $MISE_TOOLS; do
    read -r key _ <<<"$(mise_spec "$n")"
    echo "$n $(mise_version "$key") $key"
  done
}

# mise gets every argument as given (its own --dry-run included), so it is handled before the filter below.
if [[ "${1:-}" == mise ]]; then
  shift
  [[ -x "$TOOLS_DIR/bin/mise" ]] || die "mise is not installed; run: scripts/tools.sh install mise"
  cd "$ROOT"
  exec "$TOOLS_DIR/bin/mise" "$@"
fi
args=()
for a in "$@"; do
  if [[ "$a" == --dry-run ]]; then DRY_RUN=true; else args+=("$a"); fi
done
set -- "${args[@]:-}"
case "${1:-}" in
  install) shift; cmd_install "$@" ;;
  list) cmd_list ;;
  *) die "usage: tools.sh [--dry-run] install [name...] | list | mise [args...]" ;;
esac
