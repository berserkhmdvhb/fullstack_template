#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# devsec-osv.bats — unit tests for the OSV-Scanner source-level scan
# (infra/security/local/scan-osv.sh), the single source shared by the CI
# `osv_scan` job and `devsec.sh osv`. A stub osv-scanner goes on PATH: it records
# its argv, writes the requested format to --output-file, and exits ${OSV_EXIT:-0}.
# So the scan logic — the invocation, the offline snapshot flags, the SARIF merge,
# and the advisory (exit-0-on-finding) contract — is tested without the ~200MB DB
# or a network. Each case runs in its own shell, so no state bleeds.
# -----------------------------------------------------------------------------

setup() {
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/scan-osv.sh"
  # Preserve the real PATH (it carries yq, which the script's cfg() reads the pin
  # from) and just prepend the stub dir, so the stub resolves first but yq/python3/jq
  # are still found.
  BASE_PATH="$PATH"
}

# _stub_osv EXIT — a stub osv-scanner: append its argv to $OSV_ARGV, write a
# minimal SARIF/JSON record to the --output-file path (parsed from -f/--output-file),
# and exit $1. Lets a test drive the "finding" path with OSV_EXIT=1.
_stub_osv() {
  local bindir="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$bindir"
  cat > "$bindir/osv-scanner" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$OSV_ARGV"
fmt=""; out=""
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
  case "${args[$i]}" in
    -f) fmt="${args[$((i+1))]}" ;;
    --output-file) out="${args[$((i+1))]}" ;;
  esac
done
case "$fmt" in
  sarif) printf '%s' '{"version":"2.1.0","runs":[{"results":[]}]}' > "$out" ;;
  json)  printf '%s' '{"results":[],"experimental_config":{}}' > "$out" ;;
esac
exit "${OSV_EXIT:-0}"
EOF
  chmod +x "$bindir/osv-scanner"
  export OSV_BIN="$bindir/osv-scanner"
  export OSV_EXIT="${1:-0}"           # the finding path: _stub_osv 1 makes the scan exit 1
  export OSV_ARGV="$BATS_TEST_TMPDIR/osv.argv"; : > "$OSV_ARGV"
  export PATH="$bindir:$BASE_PATH"
}

# _stub_install_fail — simulate "osv-scanner absent AND its install fails": point
# OSV_BIN at a path that does not exist (so `command -v` misses even if a real
# osv-scanner is on the host PATH) and TOOLS_INSTALLER at a stub that exits 1. The
# advisory script must then degrade to an honest SKIPPED verdict — never hard-die
# (trap_err) and never falsely report a clean pass.
_stub_install_fail() {
  local bindir="$BATS_TEST_TMPDIR/noinstall"
  mkdir -p "$bindir"
  cat > "$bindir/tools-install.sh" <<'EOF'
#!/usr/bin/env bash
echo "fake: cannot install osv-scanner" >&2
exit 1
EOF
  chmod +x "$bindir/tools-install.sh"
  export OSV_BIN="$bindir/osv-scanner-absent"
  export TOOLS_INSTALLER="$bindir/tools-install.sh"
}

# _repo — a fixture repo root with a pnpm lockfile (something OSV can read).
_repo() {
  local root="$BATS_TEST_TMPDIR/repo"
  mkdir -p "$root/frontend" "$BATS_TEST_TMPDIR/out"
  cat > "$root/frontend/pnpm-lock.yaml" <<'EOF'
lockfileVersion: '9.0'
importers:
  .:
    dependencies:
      lodash:
        specifier: ^4.0.0
        version: 4.17.21
packages:
  lodash@4.17.21:
    resolution: {integrity: sha512-v2kDEe57lecTulaDIuNTPy3Ry4gLGJ6Z1O3vE1krgXZNrsQ+LFTGHVxVjcXPs17LhbZVGedAJv8XZ1tvj5FvSg==}
snapshots:
  lodash@4.17.21: {}
EOF
  printf '%s' "$root"
}

@test "osv scans source in offline-snapshot mode, writes a merged SARIF + JSON record, exits 0" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stub_osv 0
  root="$(_repo)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]
  # V2 CLI, offline snapshot over the repo root, node_modules excluded.
  grep -qF 'scan source' "$OSV_ARGV"
  grep -qF -- '--offline-vulnerabilities' "$OSV_ARGV"
  grep -qF -- '--allow-no-lockfiles' "$OSV_ARGV"
  grep -qF 'g:**/node_modules/**' "$OSV_ARGV"
  # both records written; the merged SARIF is a 2.1.0 SARIF doc.
  [ -s "$SEC_OUTPUT_DIR/osv/report.json" ]
  [ "$(yq -r '.version' "$SEC_OUTPUT_DIR/osv/osv.sarif")" = "2.1.0" ]
  [[ "$output" == *"osv-scan clean"* ]]
}

@test "a finding (scan exits non-zero) still writes the reports and exits 0 — advisory, not a gate" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stub_osv 1
  root="$(_repo)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]                       # advisory: a finding never blocks
  [ -s "$SEC_OUTPUT_DIR/osv/report.json" ]  # report written despite the finding
  [ -s "$SEC_OUTPUT_DIR/osv/osv.sarif" ]
  [[ "$output" == *"advisory"* ]]
}

@test "tool unavailable (install fails) degrades to an honest SKIPPED verdict — exits 0, no hard-die, no false clean" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stub_install_fail
  root="$(_repo)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]                                            # advisory: never gates
  [ "$(jq -r '.status' "$SEC_OUTPUT_DIR/osv/report.json")" = "SKIPPED" ]
  # a skip still writes the empty Merged SARIF, so the code scanning upload has a file in CI
  [ -s "$SEC_OUTPUT_DIR/osv/osv.sarif" ]
  [ "$(yq -r '.version' "$SEC_OUTPUT_DIR/osv/osv.sarif")" = "2.1.0" ]
  [[ "$output" == *"SKIPPED"* ]]                                 # the coverage gap is visible
  [[ "$output" != *"ERROR @"* ]]                                 # did not hard-die via trap_err
}
