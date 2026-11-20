#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# devsec-k8s.bats — unit tests for the Kubernetes workload/posture scan
# (infra/security/local/scan-k8s.sh), the single source shared by the CI
# `k8s_scan` job and `devsec.sh k8s`. A stub kubescape goes on PATH (via the
# KUBESCAPE_BIN seam): it records its argv, writes the SARIF + JSON records to
# `<--output base>.sarif` / `.json`, and exits ${KUBESCAPE_EXIT:-0}. A stub
# tools-install.sh drives the "tool absent AND its checksum install fails" case.
# So the scan logic — the offline `scan framework nsa,mitre` invocation, the
# SARIF merge, and the advisory (exit-0-on-finding) contract — is tested without
# the Kubescape binary. Each case runs in its own shell, so no state bleeds.
# -----------------------------------------------------------------------------

setup() {
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/scan-k8s.sh"
  # Preserve the real PATH (it carries yq, which the script's cfg() reads the pin
  # from) and just prepend the stub dir, so the stubs resolve first but yq/jq
  # are still found.
  BASE_PATH="$PATH"
}

# _stub_kubescape EXIT — a stub kubescape: append its argv to $KUBESCAPE_ARGV,
# write a minimal SARIF/JSON to "<--output base>.sarif"/".json", and exit $1.
# Lets a test drive the "finding" path with _stub_kubescape 1.
_stub_kubescape() {
  local bindir="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$bindir"
  cat > "$bindir/kubescape" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$KUBESCAPE_ARGV"
base=""; prev=""
for a in "$@"; do
  if [ "$prev" = "--output" ]; then base="$a"; fi
  prev="$a"
done
[ -n "$base" ] && printf '%s' '{"version":"2.1.0","runs":[{"results":[]}]}' > "${base}.sarif"
[ -n "$base" ] && printf '%s' '{"risk_score":0,"results":[]}' > "${base}.json"
exit "${KUBESCAPE_EXIT:-0}"
EOF
  chmod +x "$bindir/kubescape"
  export KUBESCAPE_BIN="$bindir/kubescape"
  export KUBESCAPE_EXIT="${1:-0}"          # the finding path: _stub_kubescape 1 makes the scan exit 1
  export KUBESCAPE_ARGV="$BATS_TEST_TMPDIR/kubescape.argv"; : > "$KUBESCAPE_ARGV"
  export PATH="$bindir:$BASE_PATH"
}

# _stub_install_fail — simulate "kubescape absent AND its checksum install fails":
# point KUBESCAPE_BIN at a path that does not exist (so `command -v` misses even if
# a real kubescape is on the host PATH) and TOOLS_INSTALLER at a stub that exits 1.
# The advisory script must then degrade to an honest SKIPPED verdict — never
# hard-die (trap_err) and never falsely report a clean pass.
_stub_install_fail() {
  local bindir="$BATS_TEST_TMPDIR/noinstall"
  mkdir -p "$bindir"
  cat > "$bindir/tools-install.sh" <<'EOF'
#!/usr/bin/env bash
echo "fake: cannot install kubescape" >&2
exit 1
EOF
  chmod +x "$bindir/tools-install.sh"
  export KUBESCAPE_BIN="$bindir/kubescape-absent"
  export TOOLS_INSTALLER="$bindir/tools-install.sh"
}

# _repo — a fixture repo root with a K8s/Helm surface (a Chart.yaml) so the
# "has K8s" detection passes and the offline workload scan actually runs.
_repo() {
  local root="$BATS_TEST_TMPDIR/repo"
  mkdir -p "$root" "$BATS_TEST_TMPDIR/out"
  printf '%s\n' "apiVersion: v2" > "$root/Chart.yaml"
  printf '%s\n' "name: your-service-name" >> "$root/Chart.yaml"
  printf '%s' "$root"
}

@test "k8s scans the repo workload offline (NSA + MITRE), writes a merged SARIF + JSON, exits 0" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stub_kubescape 0
  root="$(_repo)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]
  # offline workload scan over the repo root, NSA-CISA + MITRE frameworks.
  grep -qF 'scan framework' "$KUBESCAPE_ARGV"
  grep -qF 'nsa,mitre' "$KUBESCAPE_ARGV"
  # both records written; the merged SARIF is a 2.1.0 SARIF doc.
  [ -s "$SEC_OUTPUT_DIR/k8s/report.json" ]
  [ "$(yq -r '.version' "$SEC_OUTPUT_DIR/k8s/k8s.sarif")" = "2.1.0" ]
  [ "$(yq -r '.status' "$SEC_OUTPUT_DIR/k8s/report.json")" = "GREEN" ]
  [[ "$output" == *"k8s-scan clean"* ]]
}

@test "a finding (kubescape exits non-zero) still writes the reports and exits 0 — advisory, not a gate" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stub_kubescape 1
  root="$(_repo)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]                       # advisory: a finding never blocks
  [ -s "$SEC_OUTPUT_DIR/k8s/report.json" ]  # report written despite the finding
  [ -s "$SEC_OUTPUT_DIR/k8s/k8s.sarif" ]
  [ "$(yq -r '.status' "$SEC_OUTPUT_DIR/k8s/report.json")" = "RED" ]
  [[ "$output" == *"advisory"* ]]
}

@test "tool unavailable (checksum install fails) degrades to an honest SKIPPED verdict — exits 0, no hard-die, no false clean" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stub_install_fail
  root="$(_repo)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]                                            # advisory: never gates
  [ "$(yq -r '.status' "$SEC_OUTPUT_DIR/k8s/report.json")" = "SKIPPED" ]
  # a skip still writes the empty Merged SARIF, so the code scanning upload has a file in CI
  [ -s "$SEC_OUTPUT_DIR/k8s/k8s.sarif" ]
  [ "$(yq -r '.version' "$SEC_OUTPUT_DIR/k8s/k8s.sarif")" = "2.1.0" ]
  [[ "$output" == *"SKIPPED"* ]]                                 # the coverage gap is visible
  [[ "$output" != *"ERROR @"* ]]                                 # did not hard-die via trap_err
}

@test "KUBESCAPE_VERBOSE=1 appends -v (per-resource control failures) to the invocation" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stub_kubescape 0
  root="$(_repo)"
  KUBESCAPE_VERBOSE=1 run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]
  # verbose is opt-in: with the flag set, Kubescape's -v lands on the argv (a token
  # match, so a "-v" inside a temp path can't false-positive).
  grep -qE '(^| )-v( |$)' "$KUBESCAPE_ARGV"
}

@test "verbose is off by default: no -v on the invocation unless KUBESCAPE_VERBOSE=1" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stub_kubescape 0
  root="$(_repo)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]
  # opt-in only: a plain run must not carry -v. Plain `grep` + `return 1` (not a bare
  # `!`, SC2314) so the negative guard can't be masked by a later passing assertion.
  if grep -qE '(^| )-v( |$)' "$KUBESCAPE_ARGV"; then
    echo "-v present with KUBESCAPE_VERBOSE unset: verbose must be opt-in" >&2
    return 1
  fi
}
