#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# new-scanner.sh — scaffold a new supply-chain scanner the repo's single-source
# way. Given a lowercase slug (e.g. `semgrep`), it writes:
#
#   infra/security/local/scan-<name>.sh           (the single source of truth)
#   infra/security/local/tests/devsec-<name>.bats (TDD, stub-on-PATH)
#
# and prints the GitHub Actions wiring for `.github/workflows/security.yml`: an
# entry for the `advisory` matrix plus its install-step case (advisory), or a
# standalone blocking job (--gate). The boilerplate is
# fully wired to the established pattern — shared runtime, the `*_BIN` test seam,
# install-if-missing with honest SKIPPED degradation, SARIF + report.json, and the
# advisory/gate exit contract — so you only fill the scanner-specific run logic
# (the TODO markers). It does NOT touch the frozen eight scripts, config.yaml,
# security.yml, or devsec.sh; those are the follow-up steps in SKILL.md.
#
# Usage: new-scanner.sh <name> [options]
#   <name>               lowercase slug: the scanner id (scan-<name>.sh, the matrix `id`)
#   --tool <bin>         the tool binary name (default: <name>)
#   --delivery binary|pip  how it installs (default: binary)
#   --gate               this scanner GATES the pipeline on a finding (default: advisory)
#   --deps "<pkgs>"      runtime deps the script needs (default: "bash jq")
#   --pin <ver>          the config.yaml pin (default: v0.0.0 binary / 0.0.0 pip)
#   --display "<text>"   one-line what-it-does (for the banner / CI comment)
#   --repo <dir>         the repo root to write into (default: the current directory)
# -----------------------------------------------------------------------------
set -euo pipefail

usage() { sed -n '2,27p' "$0" | sed 's/^# \{0,1\}//'; }

NAME=""; TOOL=""; DELIVERY="binary"; CONTRACT="advisory"; DEPS="bash jq"
PIN=""; DISPLAY=""; REPO_ROOT=""; JOBNAME=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --tool) TOOL="$2"; shift 2 ;;
    --delivery) DELIVERY="$2"; shift 2 ;;
    --gate) CONTRACT="gate"; shift ;;
    --contract) CONTRACT="$2"; shift 2 ;;
    --deps) DEPS="$2"; shift 2 ;;
    --pin) PIN="$2"; shift 2 ;;
    --display) DISPLAY="$2"; shift 2 ;;
    --repo) REPO_ROOT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) if [[ -z "$NAME" ]]; then NAME="$1"; shift; else echo "unknown arg: $1" >&2; usage; exit 2; fi ;;
  esac
done

[[ -n "$NAME" ]] || { echo "error: a <name> slug is required" >&2; usage; exit 2; }
[[ "$NAME" =~ ^[a-z][a-z0-9-]*$ ]] || { echo "error: <name> must be a lowercase slug (e.g. semgrep, sbom-tools)" >&2; exit 2; }
case "$DELIVERY" in binary|pip) ;; *) echo "error: --delivery must be binary|pip" >&2; exit 2 ;; esac
[[ "$CONTRACT" == advisory || "$CONTRACT" == gate ]] || { echo "error: --gate/--contract must be advisory|gate" >&2; exit 2; }

TOOL="${TOOL:-$NAME}"
NAMEU="$(printf '%s' "$NAME" | tr '[:lower:]-' '[:upper:]_')"
JOBNAME="${NAME//-/_}_scan"   # names the scanner in the banner and the docs
PINVAR="${NAMEU}_VERSION"
case "$DELIVERY" in
  binary) PINKEY=".tools.${NAME}"; PIN="${PIN:-v0.0.0}" ;;
  pip)    PINKEY=".tools_pip.${NAME}"; PIN="${PIN:-0.0.0}" ;;
esac
DISPLAY="${DISPLAY:-<what the ${TOOL} scan does>}"
REPO_ROOT="${REPO_ROOT:-$(pwd)}"
# Refuse a name the security workflow already uses (a leg id, a category, an artifact or a job key).
SEC_WF="${REPO_ROOT}/.github/workflows/security.yml"
if [[ -f "$SEC_WF" ]] && grep -Eq "^[[:space:]]*(- )?(id|category|name): ${NAME}[[:space:]]*$|^  ${NAME}:[[:space:]]*$" "$SEC_WF"; then
  echo "error: '${NAME}' is already used in .github/workflows/security.yml (an id, category, artifact name or job key); pick another name" >&2
  exit 2
fi
SCAN_SH="${REPO_ROOT}/infra/security/local/scan-${NAME}.sh"
BATS_FILE="${REPO_ROOT}/infra/security/local/tests/devsec-${NAME}.bats"

work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT

# _splice FILE MARKER BLOCKFILE — replace the single line in FILE that exactly
# equals MARKER with the contents of BLOCKFILE; leave FILE untouched if MARKER is
# absent or BLOCKFILE is missing. Pure bash (no sed `r`/`d` program) so shellcheck
# can parse the scaffolder itself, and so the spliced block is never silently lost.
_splice() {
  local file="$1" marker="$2" block="$3"
  [[ -f "$file" && -f "$block" ]] || return 0
  local tmp="$work/splice.tmp" line found=0
  : > "$tmp"
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" == "$marker" ]]; then cat "$block" >> "$tmp"; found=1
    else printf '%s\n' "$line" >> "$tmp"; fi
  done < "$file"
  if [[ "$found" -eq 1 ]]; then mv "$tmp" "$file"; else rm -f "$tmp"; fi
}

# _gsub STRING TOKEN REPL — print STRING with every occurrence of TOKEN replaced
# by REPL. TOKEN and REPL are taken literally (TOKEN is quoted in the pattern), so
# this is immune to the bash ${var//pat/} quirk where a token sitting inside "..."
# on a line that also contains '&' (e.g. `command -v "$X" >/dev/null 2>&1`, which
# the install block uses) is silently left un-replaced — and to sed/awk choking on
# the same sed-hostile text. Pure bash, no external tool.
_gsub(){ local s="$1"; while [[ "$s" == *"$2"* ]]; do s="${s%%"$2"*}$3${s#*"$2"}"; done; printf '%s' "$s"; }

# _subst FILE — replace every @@PLACEHOLDER@@ token in FILE with its value, in
# place, using _gsub (see why above).
_subst() {
  local file="$1" tmp="$work/subst.tmp" line
  : > "$tmp"
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="$(_gsub "$line" '@@NAME@@'     "$NAME")"
    line="$(_gsub "$line" '@@NAMEU@@'    "$NAMEU")"
    line="$(_gsub "$line" '@@TOOL@@'     "$TOOL")"
    line="$(_gsub "$line" '@@BINENV@@'   "${NAMEU}_BIN")"
    line="$(_gsub "$line" '@@PINVAR@@'   "$PINVAR")"
    line="$(_gsub "$line" '@@PINKEY@@'   "$PINKEY")"
    line="$(_gsub "$line" '@@PIN@@'      "$PIN")"
    line="$(_gsub "$line" '@@DEPS@@'     "$DEPS")"
    line="$(_gsub "$line" '@@DISPLAY@@'  "$DISPLAY")"
    line="$(_gsub "$line" '@@JOBNAME@@'  "$JOBNAME")"
    line="$(_gsub "$line" '@@CONTRACT@@' "$CONTRACT")"
    printf '%s\n' "$line" >> "$tmp"
  done < "$file"
  mv "$tmp" "$file"
}

# ---------------------------------------------------------------------------
# Build the install block (binary vs pip). Written to $work/install.block.
# ---------------------------------------------------------------------------
if [[ "$DELIVERY" == binary ]]; then
  cat > "$work/install.block" <<'BLK'
@@PINVAR@@="$(cfg '@@PINKEY@@')"
[[ -n "$@@PINVAR@@" ]] || die "no pinned @@TOOL@@ version (add @@PINKEY@@ to ${CONFIG_FILE##*/})"
if ! command -v "$@@BINENV@@" >/dev/null 2>&1; then
  warn "@@TOOL@@ missing — installing ${@@PINVAR@@} via tools-install.sh"
  run bash "$TOOLS_INSTALLER" @@TOOL@@ "$@@PINVAR@@" /usr/local/bin \
    || warn "@@TOOL@@ install failed — the scan degrades to SKIPPED if the tool is still absent"
fi
if [[ "$DRY_RUN" != "true" ]] && ! command -v "$@@BINENV@@" >/dev/null 2>&1; then
  _skipped_scan "$OUT_DIR" "@@TOOL@@" "@@TOOL@@ ${@@PINVAR@@} unavailable (not present and install failed)"
  exit 0
fi
BLK
else
  cat > "$work/install.block" <<'BLK'
@@PINVAR@@="$(cfg '@@PINKEY@@')"
[[ -n "$@@PINVAR@@" ]] || die "no pinned @@TOOL@@ version (add @@PINKEY@@ to ${CONFIG_FILE##*/})"
if ! command -v "$@@BINENV@@" >/dev/null 2>&1; then
  # GuardDog-style pip tools compile C extensions (need a C compiler + Python.h,
  # linking libyara/libgit2). Detect a missing toolchain up front and short-circuit
  # to an actionable SKIPPED WITHOUT invoking pip — one clean line, no compile dump.
  if [[ "$DRY_RUN" != "true" ]] && ! _@@NAME@@_toolchain_ok; then
    _skipped_scan "$OUT_DIR" "@@TOOL@@" "@@TOOL@@ ${@@PINVAR@@} unavailable — no C toolchain to build it (install: sudo apt-get install build-essential python3-dev libyara-dev libgit2-dev)"
    exit 0
  fi
  warn "@@TOOL@@ missing — installing ${@@PINVAR@@} via pip"
  run pip install --no-cache-dir --disable-pip-version-check "@@TOOL@@==${@@PINVAR@@}" \
    || warn "@@TOOL@@ install failed — the scan degrades to SKIPPED if the tool is still absent"
fi
if [[ "$DRY_RUN" != "true" ]] && ! command -v "$@@BINENV@@" >/dev/null 2>&1; then
  _skipped_scan "$OUT_DIR" "@@TOOL@@" "@@TOOL@@ ${@@PINVAR@@} unavailable (not present and install failed)"
  exit 0
fi
BLK
fi

# ---------------------------------------------------------------------------
# Toolchain probe (pip only) -> $work/toolchain.block (empty for binary).
# ---------------------------------------------------------------------------
: > "$work/toolchain.block"
if [[ "$DELIVERY" == pip ]]; then
  cat > "$work/toolchain.block" <<'BLK'

# _@@NAME@@_toolchain_ok — can pip build @@TOOL@@ here? Needs a C compiler + the
# Python dev headers (Python.h). Both probes are overridable for tests
# (@@NAMEU@@_CC / @@NAMEU@@_PYTHON_INCLUDE); an unset override probes the real box.
_@@NAME@@_toolchain_ok() {
  command -v "${@@NAMEU@@_CC:-cc}" >/dev/null 2>&1 || return 1
  local inc="${@@NAMEU@@_PYTHON_INCLUDE:-}"
  if [ -z "$inc" ]; then
    inc="$(python3 -c 'import sysconfig;print(sysconfig.get_path("include"),end="")' 2>/dev/null)" || return 1
  fi
  [ -n "$inc" ] && [ -f "$inc/Python.h" ]
}
BLK
fi

# ---------------------------------------------------------------------------
# Exit block (advisory vs gate) -> $work/exit.block
# ---------------------------------------------------------------------------
if [[ "$CONTRACT" == gate ]]; then
  cat > "$work/exit.block" <<'BLK'
# Gate: a finding fails the job. The TODO run section sets rc to the tool's exit.
if (( rc != 0 )); then
  error "@@TOOL@@ reported findings (exit ${rc}) — this scan gates the pipeline"
  exit "$rc"
fi
exit 0
BLK
else
  cat > "$work/exit.block" <<'BLK'
# Advisory: a finding is a red in the report, never a pipeline block. Always exit 0.
exit 0
BLK
fi

# ---------------------------------------------------------------------------
# Write scan-<name>.sh : skeleton (quoted heredoc, placeholders) + spliced blocks.
# ---------------------------------------------------------------------------
cat > "$work/scan.skel" <<'SKEL'
#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# scan-@@NAME@@.sh — @@DISPLAY@@
#
# @@TOOL@@ is the SINGLE SOURCE OF TRUTH for the @@NAME@@ scan: the CI `@@JOBNAME@@`
# job and the local `devsec.sh @@NAME@@` command both call it with the same args
# ($SEC_OUTPUT_DIR $PROJECT_ROOT), so CI and a laptop never drift.
#
# Contract: @@CONTRACT@@ — advisory scans always `exit 0` (the verdict lives in
# report.json, not the exit code); a gate fails the job on a finding.
#
# Usage: scan-@@NAME@@.sh [SEC_OUTPUT_DIR] [PROJECT_ROOT]
# -----------------------------------------------------------------------------
set -Eeuo pipefail

# ---------- Shared runtime ----------
# shellcheck source=/dev/null
source "$(dirname "$(dirname "${BASH_SOURCE[0]}")")/_helpers.sh"
trap 'trap_err' ERR

# --help short-circuit (usage() is defined above).
usage() {
  cat <<'USAGE'
Usage: scan-@@NAME@@.sh [SEC_OUTPUT_DIR] [PROJECT_ROOT]

@@DISPLAY@@

  SEC_OUTPUT_DIR  security output dir  (default: config.yaml output.base)
  PROJECT_ROOT    repo root            (default: this script's ../..)
USAGE
  exit 0
}
case "${1:-}" in -h|--help) usage ;; esac

# ---------- Defaults ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEC_DIR="$(dirname "$SCRIPT_DIR")"                       # infra/security
SEC_OUTPUT_DIR="${1:-${SEC_OUTPUT_DIR:-}}"
resolve_sec_output_dir                                   # env -> config -> output/security
OUT_DIR="${SEC_OUTPUT_DIR%/}/@@NAME@@"
# shellcheck disable=SC2034  # ROOT is consumed by the TODO run section below.
ROOT="${2:-$(cd "${SEC_DIR}/../.." && pwd)}"
@@BINENV@@="${@@BINENV@@:-@@TOOL@@}"                     # overridable: the test seam
TOOLS_INSTALLER="${TOOLS_INSTALLER:-${SEC_DIR}/tools-install.sh}"

lib_banner "scan-@@NAME@@" "@@DISPLAY@@"
@@TOOLCHAIN_BLOCK_MARKER@@
# ---------- Tool: pin + install if missing ----------
@@INSTALL_BLOCK_MARKER@@
need_cmd @@DEPS@@

# verdict: the TODO run section below sets rc to the tool's exit code (0 = clean).
# shellcheck disable=SC2034  # rc is the verdict accumulator, written by the TODO run section.
rc=0

# ---------- TODO: enumerate inputs (lockfiles / manifests / image list) -------
# @@TOOL@@ reads: REPLACE with what this scanner consumes. A missing input must
# skip that ecosystem cleanly — never fatal (the other ecosystems still run).

# ---------- TODO: run @@TOOL@@ (capture a non-zero exit as the verdict) --------
# Run the tool through `run` so --dry-run prints argv and executes nothing. A
# finding exits non-zero — capture it via `if run ...; then rc=0; else rc=$?; fi`
# so `set -e`/ERR cannot abort before the reports are written. Example:
#   mkdir -p "$OUT_DIR"
#   sarif="${OUT_DIR}/@@NAME@@.sarif.json"
#   if run "$@@BINENV@@" <flags> -f sarif --output-file "$sarif" "$ROOT"; then rc=0; else rc=$?; fi

# ---------- SARIF for code scanning ---------------------------------------------
mkdir -p "$OUT_DIR"
merged_sarif="${OUT_DIR}/@@NAME@@.sarif"
# REPLACE: map @@TOOL@@'s findings into a 2.1.0 SARIF (one result per finding).
# Until then, an empty report keeps code scanning resolvable:
echo '{"version":"2.1.0","runs":[]}' > "$merged_sarif"

# ---------- JSON summary record (MUST carry a .status: GREEN/RED/SKIPPED) -----
report="${OUT_DIR}/report.json"
# REPLACE: emit report.json with a .status field the chain / bats assert on.
echo '{"tool":"@@TOOL@@","scanned":0,"findings":[],"status":"GREEN"}' > "$report"

status="$(python3 -c "import json;print(json.load(open('$report'))['status'])" 2>/dev/null || echo UNKNOWN)"
log "@@NAME@@: status ${status}"
success "@@NAME@@: reports written: ${report}, ${merged_sarif}"

@@EXIT_BLOCK_MARKER@@
SKEL

# Splice the three blocks into their markers, then resolve the small placeholders.
_splice "$work/scan.skel" "@@INSTALL_BLOCK_MARKER@@"   "$work/install.block"
_splice "$work/scan.skel" "@@TOOLCHAIN_BLOCK_MARKER@@" "$work/toolchain.block"
_splice "$work/scan.skel" "@@EXIT_BLOCK_MARKER@@"      "$work/exit.block"
_subst "$work/scan.skel"
mkdir -p "$(dirname "$SCAN_SH")"
mv "$work/scan.skel" "$SCAN_SH"
chmod +x "$SCAN_SH"

# ---------------------------------------------------------------------------
# Write devsec-<name>.bats
# ---------------------------------------------------------------------------
# install-fail helper: binary -> failing TOOLS_INSTALLER stub; pip -> failing pip
# stub + forced-missing toolchain (so it short-circuits to SKIPPED without pip).
if [[ "$DELIVERY" == binary ]]; then
  cat > "$work/instfail.block" <<'BLK'
# _stub_install_fail — "@@TOOL@@ absent AND install fails": point @@BINENV@@ at a
# nonexistent path and put a failing tools-install.sh on PATH. The advisory script
# must degrade to an honest SKIPPED — never hard-die (trap_err), never false clean.
_stub_install_fail() {
  local bindir="$BATS_TEST_TMPDIR/noinstall"
  mkdir -p "$bindir"
  cat > "$bindir/tools-install.sh" <<'EOF'
#!/usr/bin/env bash
echo "fake: cannot install @@TOOL@@ (checksum verify / network failed)" >&2
exit 1
EOF
  chmod +x "$bindir/tools-install.sh"
  export @@BINENV@@="$bindir/@@TOOL@@-absent"
  export TOOLS_INSTALLER="$bindir/tools-install.sh"
  export PATH="$bindir:$BASE_PATH"
}
BLK
else
  cat > "$work/instfail.block" <<'BLK'
# _stub_install_fail — "@@TOOL@@ absent AND pip cannot build it": point @@BINENV@@ at
# a nonexistent path, force the toolchain probe to miss (@@NAMEU@@_CC -> absent), and
# put a failing pip on PATH. The advisory script short-circuits to an honest SKIPPED
# without ever invoking pip — never hard-die, never false clean.
_stub_install_fail() {
  local bindir="$BATS_TEST_TMPDIR/noinstall"
  mkdir -p "$bindir"
  cat > "$bindir/pip" <<'EOF'
#!/usr/bin/env bash
echo "fake: cannot build @@TOOL@@ (missing Python headers / build deps)" >&2
exit 1
EOF
  chmod +x "$bindir/pip"
  export @@BINENV@@="$bindir/@@TOOL@@-absent"
  export @@NAMEU@@_CC="$bindir/no-such-cc"
  export PATH="$bindir:$BASE_PATH"
}
BLK
fi

cat > "$work/bats.skel" <<'SKEL'
#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# devsec-@@NAME@@.bats — unit tests for scan-@@NAME@@.sh (the single source shared
# by the CI @@JOBNAME@@ job and devsec.sh @@NAME@@). A stub @@TOOL@@ goes on PATH: it
# records its argv and emits JSON whose `issues` count turns on the package name (a
# name containing "typo" is a hit). Each @test runs in its own shell.
# -----------------------------------------------------------------------------

setup() {
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/scan-@@NAME@@.sh"
  # Preserve the real PATH (carries yq/python3/jq) and just prepend the stub dir,
  # so the stub resolves first but yq/python3/jq are still found.
  BASE_PATH="$PATH"
}

# _stub_@@NAME@@ — a stub @@TOOL@@: append argv to $@@NAMEU@@_ARGV and emit a JSON
# record whose issues count turns on the package name; always exit 0.
_stub_@@NAME@@() {
  local bindir="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$bindir"
  cat > "$bindir/@@TOOL@@" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$@@NAMEU@@_ARGV"
name="$3"   # <tool> <eco> scan <name> --rules ... --output-format json
if [[ "$name" == *typo* ]]; then issues=1; else issues=0; fi
printf '{"package":"%s","package_version":"1.0.0","issues":%s,"errors":{},"risks":[],"path":"%s.json"}' "$name" "$issues" "$name"
exit 0
EOF
  chmod +x "$bindir/@@TOOL@@"
  export @@BINENV@@="$bindir/@@TOOL@@"
  export @@NAMEU@@_ARGV="$BATS_TEST_TMPDIR/@@NAME@@.argv"; : > "$@@NAMEU@@_ARGV"
  export PATH="$bindir:$BASE_PATH"
}

@@INSTFAIL_BLOCK_MARKER@@

# _manifests PYPI NPM — write a pyproject.toml + frontend/package-lock.json fixture.
_manifests() {
  local root="$BATS_TEST_TMPDIR/repo"
  mkdir -p "$root/frontend" "$BATS_TEST_TMPDIR/out"
  { echo "[project]"; echo 'dependencies = ['; local IFS=','; for d in $1; do printf '  "%s",\n' "$d"; done; echo ']'; } > "$root/pyproject.toml"
  cat > "$root/frontend/package-lock.json" <<EOF
{ "name": "root", "lockfileVersion": 3, "packages": { "": { "dependencies": { $2 } } } }
EOF
  printf '%s' "$root"
}

@test "clean run: reports written, GREEN, exits 0" {
  # shellcheck disable=SC2030,SC2031  # bats runs each @test in its own subshell; the export still reaches the `run` child inside it
  export SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stub_@@NAME@@
  root="$(_manifests 'requests==2.31.0,flask' '"lodash":"^4.0.0"')"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]                                            # plumbing: advisory exits
  [ "$(yq -r '.status' "$SEC_OUTPUT_DIR/@@NAME@@/report.json")" = "GREEN" ]
  [[ "$output" != *"ERROR @"* ]]                                 # plumbing: no hard-die
  # TODO(strengthen): once the run logic is wired, assert the scan count and the
  # per-dep argv here, e.g. [ "$(yq -r '.scanned' …/report.json)" -eq 1 ] and
  # grep -qF '<the-tool-flags>' "$@@NAMEU@@_ARGV".
}

@test "a finding: report RED, still exits 0 (advisory)" {
  # shellcheck disable=SC2030,SC2031  # bats runs each @test in its own subshell; the export still reaches the `run` child inside it
  export SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stub_@@NAME@@
  root="$(_manifests 'typopkg>=1.0' '"lodash":"^4.0.0"')"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]
  [ "$(yq -r '.status' "$SEC_OUTPUT_DIR/@@NAME@@/report.json")" = "RED" ]
  [[ "$output" != *"ERROR @"* ]]
}

@test "tool unavailable: degrades to honest SKIPPED, exits 0, no hard-die" {
  # shellcheck disable=SC2030,SC2031  # bats runs each @test in its own subshell; the export still reaches the `run` child inside it
  export SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stub_install_fail
  root="$BATS_TEST_TMPDIR/repo"; mkdir -p "$root"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]
  [ "$(yq -r '.status' "$SEC_OUTPUT_DIR/@@NAME@@/report.json")" = "SKIPPED" ]
  [[ "$output" == *"SKIPPED"* ]]
  [[ "$output" != *"ERROR @"* ]]
}
SKEL

_splice "$work/bats.skel" "@@INSTFAIL_BLOCK_MARKER@@" "$work/instfail.block"
_subst "$work/bats.skel"
mkdir -p "$(dirname "$BATS_FILE")"
mv "$work/bats.skel" "$BATS_FILE"

# ---------------------------------------------------------------------------
# Print the GitHub Actions wiring for .github/workflows/security.yml.
# ---------------------------------------------------------------------------
if [[ "$DELIVERY" == binary ]]; then
  INSTALL_NOTE=""
  INSTALL_CMD="bash infra/security/tools-install.sh ${TOOL} \"\$(yq -r '${PINKEY}' infra/security/config.yaml)\" \"\${RUNNER_TEMP}/bin\""
else
  INSTALL_CMD="sudo apt-get update -qq && sudo apt-get install -y -qq --no-install-recommends build-essential"
  INSTALL_NOTE="# REPLACE: the OS packages the pip build needs"
fi

if [[ "$CONTRACT" == advisory ]]; then
  cat <<JOB
# ---------------------------------------------------------------------------
# ${NAME} - ${DISPLAY}  (contract: advisory)
# Edit .github/workflows/security.yml, job \`advisory\`:
#  1. under strategy.matrix.include add this leg:
# ---------------------------------------------------------------------------
          - id: ${NAME}
            category: ${NAME}
            script: scan-${NAME}.sh
            sarif: ${NAME}/${NAME}.sarif
# 2. in the step "Install the pinned tools", add this line to the case "\$TOOL_ID" in:
${INSTALL_NOTE:+            ${INSTALL_NOTE}
}            ${NAME}) ${INSTALL_CMD} ;;
JOB
  if [[ "$DELIVERY" == pip ]]; then
    echo "# 3. add \`|| matrix.id == '${NAME}'\` to the \`if:\` of the setup-python step (the wrapper pip-installs ${TOOL})."
  fi
else
  _pin_of() { grep -ho "$1@[0-9a-f]\{40\} # v[0-9][0-9.]*" "$REPO_ROOT/.github/workflows/security.yml" 2>/dev/null | head -n 1 || true; }
  CHECKOUT_USES="$(_pin_of actions/checkout)"; CHECKOUT_USES="${CHECKOUT_USES:-actions/checkout@<40-char sha> # vX.Y.Z}"
  PYTHON_USES="$(_pin_of actions/setup-python)"; PYTHON_USES="${PYTHON_USES:-actions/setup-python@<40-char sha> # vX.Y.Z}"
  if [[ "$DELIVERY" == pip ]]; then
    SETUP_PY="      - uses: ${PYTHON_USES}
        with:
          python-version: \"3.13\"
"
  else
    SETUP_PY=""
  fi
  cat <<JOB
# ---------------------------------------------------------------------------
# ${NAME} - ${DISPLAY}  (contract: gate)
# Add this job to .github/workflows/security.yml. A failure fails the \`security\` job
# of ci.yml, so it blocks through the \`CI OK\` gate; list it in wiki/ci-cd.md.
# ---------------------------------------------------------------------------
  ${NAME}:
    name: ${NAME}
    runs-on: ubuntu-24.04
    timeout-minutes: 30
    permissions:
      contents: read
    steps:
      - uses: ${CHECKOUT_USES}
        with:
          persist-credentials: false
${SETUP_PY}      - name: Install the pinned tool
        run: |
          # Outside the workspace, so the scanner never reads the tool archives.
          export TOOLS_DIR="\${RUNNER_TEMP}/tools"
          scripts/tools.sh install yq
          mkdir -p "\${RUNNER_TEMP}/bin"
          echo "\${TOOLS_DIR}/bin" >> "\$GITHUB_PATH"
          echo "\${RUNNER_TEMP}/bin" >> "\$GITHUB_PATH"
          export PATH="\${TOOLS_DIR}/bin:\$PATH"
          ${INSTALL_NOTE:+${INSTALL_NOTE}
          }${INSTALL_CMD}
      - name: Scan
        run: bash infra/security/local/scan-${NAME}.sh "\$SEC_OUTPUT_DIR" "\$PWD"
JOB
fi

# ---------------------------------------------------------------------------
# Follow-up (printed to stderr so stdout stays the YAML block).
# ---------------------------------------------------------------------------
{
  echo
  echo "Generated:"
  echo "  $SCAN_SH"
  echo "  $BATS_FILE"
  echo
  echo "Next steps (see the skill's 10-step path):"
  echo "  1. Fill the two TODO sections in scan-${NAME}.sh (inputs + run logic + real SARIF/report.json)."
  echo "  2. Pin the version: add '${PINKEY}: ${PIN}' to infra/security/config.yaml."
  echo "  3. Run: bats infra/security/local/tests/devsec-${NAME}.bats  &&  shellcheck ${SCAN_SH} ${BATS_FILE}"
  echo "  4. Paste the printed wiring into .github/workflows/security.yml; add its case to tests/supply_chain/test_ci_security_jobs.py."
  echo "  5. Add cmd_${NAME}() + \${${NAMEU}_SCAN_SH} seam to infra/security/local/devsec.sh."
  echo "  6. Update wiki/security.md, wiki/supply-chain-tooling-assessment.md, infra/security/local/README.md."
} >&2
