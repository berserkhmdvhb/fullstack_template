#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# devsec-iac.bats — unit tests for the IaC misconfiguration scan
# (infra/security/local/scan-iac.sh), the single source shared by the CI
# `iac_scan` job and `devsec.sh iac`. A stub checkov goes on PATH (via the
# CHECKOV_BIN seam): it records its argv and writes a minimal SARIF to the first
# --output-file-path and a JSON record to the second; the JSON summary is driven
# by CHECKOV_RESOURCES / CHECKOV_FAILED / CHECKOV_EXIT, so a test can drive "0
# resources parsed" (the false-clean case) or "N resources / a finding". So the
# scan logic — input enumeration, the multi-framework directory invocation, the
# SARIF merge, the honest-SKIPPED guard against a false clean, and the advisory
# (exit-0-on-finding) contract — is tested without installing checkov or a real
# IaC tree. Each case runs in its own shell, so no state bleeds.
#
# Why no Helm-render test: Checkov does not render Helm templates here (its helm
# framework no-ops, and scanning the rendered manifests aborts — Checkov 3.2.x
# segfaults on the Deployment/StatefulSet manifests a chart emits), so the script
# does not render; a Helm-only repo is reported as an honest SKIPPED (see the
# "0 resources" case below) and the workload is covered by Kubescape (devsec-k8s).
# -----------------------------------------------------------------------------

setup() {
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/scan-iac.sh"
  # Preserve the real PATH (it carries yq, which the script's cfg() reads the pin
  # from) and just prepend the stub dir, so the stub resolves first but
  # yq/jq/python3 are still found.
  BASE_PATH="$PATH"
}

# _stub_checkov EXIT — a stub checkov: append its argv to $CHECKOV_ARGV, write a
# minimal SARIF to the first --output-file-path and a JSON record to the second
# (paired positionally), and exit $1. The JSON summary is driven by the env the
# test sets (CHECKOV_RESOURCES / CHECKOV_FAILED / CHECKOV_PASSED), so a test can
# drive "0 resources parsed" (the false-clean case) or "N resources / a finding".
_stub_checkov() {
  local bindir="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$bindir"
  cat > "$bindir/checkov" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CHECKOV_ARGV"
# Collect the --output-file-path targets in order: SARIF first, JSON second.
paths=(); prev=""
for a in "$@"; do
  [ "$prev" = "--output-file-path" ] && paths+=("$a")
  prev="$a"
done
[ -n "${paths[0]:-}" ] && printf '%s' '{"version":"2.1.0","runs":[{"results":[]}]}' > "${paths[0]}"
[ -n "${paths[1]:-}" ] && printf '{"summary":{"passed":%s,"failed":%s,"skipped":0,"parsing_errors":0,"resource_count":%s,"checkov_version":"0.0.0"},"results":{"passed_checks":[],"failed_checks":[],"skipped_checks":[],"parsing_errors":[]}}' "${CHECKOV_PASSED:-1}" "${CHECKOV_FAILED:-0}" "${CHECKOV_RESOURCES:-1}" > "${paths[1]}"
exit "${CHECKOV_EXIT:-0}"
EOF
  chmod +x "$bindir/checkov"
  export CHECKOV_BIN="$bindir/checkov"
  export CHECKOV_EXIT="${1:-0}"          # the finding path: _stub_checkov 2 makes the pass exit 2
  export CHECKOV_ARGV="$BATS_TEST_TMPDIR/checkov.argv"; : > "$CHECKOV_ARGV"
  export PATH="$bindir:$BASE_PATH"
}

# _stub_install_fail — simulate "checkov absent AND its pip install fails": point
# CHECKOV_BIN at a path that does not exist (so `command -v` misses even if a real
# checkov is on the host PATH) and put a stub `pip` that exits 1 on PATH. The
# advisory script must then degrade to an honest SKIPPED verdict — never hard-die
# (trap_err) and never falsely report a clean pass.
_stub_install_fail() {
  local bindir="$BATS_TEST_TMPDIR/noinstall"
  mkdir -p "$bindir"
  cat > "$bindir/pip" <<'EOF'
#!/usr/bin/env bash
echo "fake: cannot install checkov" >&2
exit 1
EOF
  chmod +x "$bindir/pip"
  export CHECKOV_BIN="$bindir/checkov-absent"
  # Prepend the stub bin so the script's `pip install` resolves the failing stub.
  export PATH="$bindir:$BASE_PATH"
}

# _stub_pip EXIT — a stub `pip` that records its argv (so a test can assert which
# install flags scan-iac.sh chose) and exits ${PIP_EXIT:-0}. CHECKOV_BIN is left
# pointing at a path that does not exist, so after the (stubbed) install the tool
# is still "absent" and the script degrades to SKIPPED — these cases exercise the
# install *branch* selection (wheels-only vs source build), not a successful scan.
_stub_pip() {
  local bindir="$BATS_TEST_TMPDIR/pipbin"
  mkdir -p "$bindir"
  cat > "$bindir/pip" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$PIP_ARGV"
exit "${PIP_EXIT:-0}"
EOF
  chmod +x "$bindir/pip"
  export CHECKOV_BIN="$bindir/checkov-absent"
  export PIP_ARGV="$BATS_TEST_TMPDIR/pip.argv"; : > "$PIP_ARGV"
  export PATH="$bindir:$BASE_PATH"
}

# _stub_rust — point the CARGO_BIN seam at a no-op stub (deterministic even on a
# host with a real cargo on PATH), prepended so it composes with a prior _stub_pip,
# so scan-iac.sh's "Rust toolchain present" check is true and it takes the
# source-build branch instead of the wheels-only one.
_stub_rust() {
  local bindir="$BATS_TEST_TMPDIR/rustbin"
  mkdir -p "$bindir"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$bindir/cargo"
  chmod +x "$bindir/cargo"
  export CARGO_BIN="$bindir/cargo"
  export PATH="$bindir:$BASE_PATH"
}

# _repo — a fixture repo root WITH an IaC surface the stub Checkov reports one
# resource for (a Helm Chart.yaml, which the has_iac detection accepts).
_repo() {
  local root="$BATS_TEST_TMPDIR/repo"
  mkdir -p "$root" "$BATS_TEST_TMPDIR/out"
  {
    printf '%s\n' "apiVersion: v2"
    printf '%s\n' "name: your-service-name"
    printf '%s\n' "version: 0.1.0"
  } > "$root/Chart.yaml"
  printf '%s' "$root"
}

# _repo_none — a fixture repo root with NO IaC/Helm/K8s manifest (the bare-template
# case): only a README, so input enumeration finds nothing.
_repo_none() {
  local root="$BATS_TEST_TMPDIR/repo-none"
  mkdir -p "$root" "$BATS_TEST_TMPDIR/out"
  printf '%s\n' "just a readme" > "$root/README.md"
  printf '%s' "$root"
}

@test "iac scans the repo tree multi-framework, writes a merged SARIF + JSON, exits 0 GREEN" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stub_checkov 0
  root="$(_repo)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]
  # directory scan (-d) over the repo root, all four frameworks.
  grep -qF -- '-d' "$CHECKOV_ARGV"
  grep -qF 'terraform' "$CHECKOV_ARGV"
  grep -qF 'cloudformation' "$CHECKOV_ARGV"
  grep -qF 'kubernetes' "$CHECKOV_ARGV"
  grep -qF 'helm' "$CHECKOV_ARGV"
  # vendored deps + VCS metadata are skipped (no third-party noise), mirroring the
  # has_iac detection's node_modules/.git exclusion.
  grep -qF -- '--skip-path node_modules' "$CHECKOV_ARGV"
  grep -qF -- '--skip-path .git' "$CHECKOV_ARGV"
  # both records written; the merged SARIF is a 2.1.0 SARIF doc.
  [ -s "$SEC_OUTPUT_DIR/iac/report.json" ]
  [ "$(yq -r '.version' "$SEC_OUTPUT_DIR/iac/iac.sarif")" = "2.1.0" ]
  [ "$(yq -r '.status' "$SEC_OUTPUT_DIR/iac/report.json")" = "GREEN" ]
  [ "$(yq -r '.resources_scanned' "$SEC_OUTPUT_DIR/iac/report.json")" -ge 1 ]
  [[ "$output" == *"iac-scan clean"* ]]
}

@test "a finding (checkov exits non-zero) still writes the reports and exits 0 — advisory, not a gate" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stub_checkov 2
  root="$(_repo)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]                       # advisory: a finding never blocks
  [ -s "$SEC_OUTPUT_DIR/iac/report.json" ]  # report written despite the finding
  [ -s "$SEC_OUTPUT_DIR/iac/iac.sarif" ]
  [ "$(yq -r '.status' "$SEC_OUTPUT_DIR/iac/report.json")" = "RED" ]
  [ "$(yq -r '.resources_scanned' "$SEC_OUTPUT_DIR/iac/report.json")" -ge 1 ]
  [[ "$output" == *"advisory"* ]]
}

@test "no IaC present degrades to an honest SKIPPED verdict — exits 0, no hard-die, no false clean" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stub_checkov 0
  root="$(_repo_none)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]
  [ "$(yq -r '.status' "$SEC_OUTPUT_DIR/iac/report.json")" = "SKIPPED" ]
  [[ "$output" == *"SKIPPED"* ]]
  # a skip still writes the empty Merged SARIF, so the code scanning upload has a file in CI
  [ -s "$SEC_OUTPUT_DIR/iac/iac.sarif" ]
  [ "$(yq -r '.version' "$SEC_OUTPUT_DIR/iac/iac.sarif")" = "2.1.0" ]
  # checkov was never invoked (nothing to scan) — the stub's argv log stays empty.
  [ ! -s "$CHECKOV_ARGV" ]
}

@test "IaC present but Checkov extracts 0 resources (a Helm-only repo) degrades to an honest SKIPPED — not a false GREEN" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stub_checkov 0
  # Checkov reports 0 resources (it does not render the chart's templates): the
  # script must NOT call this GREEN — this is the false-clean the fix guards against.
  export CHECKOV_RESOURCES=0
  root="$(_repo)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]
  [ "$(yq -r '.status' "$SEC_OUTPUT_DIR/iac/report.json")" = "SKIPPED" ]
  [ "$(yq -r '.resources_scanned' "$SEC_OUTPUT_DIR/iac/report.json")" = "0" ]
  [[ "$output" == *"SKIPPED"* ]]
  [[ "$output" == *"Kubescape"* ]]                          # points at the tool that covers it
  [[ "$output" != *"ERROR @"* ]]                            # did not hard-die via trap_err
  # the directory scan ran once (has_iac was true), it just found nothing to check.
  [ "$(grep -c . "$CHECKOV_ARGV")" -eq 1 ]
}

@test "tool unavailable (pip install fails) degrades to an honest SKIPPED verdict — exits 0, no hard-die, no false clean" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stub_install_fail
  root="$(_repo)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]                                            # advisory: never gates
  [ "$(yq -r '.status' "$SEC_OUTPUT_DIR/iac/report.json")" = "SKIPPED" ]
  [[ "$output" == *"SKIPPED"* ]]
  [[ "$output" != *"ERROR @"* ]]                                 # did not hard-die via trap_err
}

@test "with a Rust toolchain present, installs checkov allowing a source build (pip WITHOUT --only-binary)" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stub_pip 0
  _stub_rust
  root="$(_repo)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]
  [ "$(yq -r '.status' "$SEC_OUTPUT_DIR/iac/report.json")" = "SKIPPED" ]
  [[ "$output" != *"ERROR @"* ]]
  # source-build branch: the recorded pip argv must NOT carry the wheels-only flag.
  [[ "$(<"$PIP_ARGV")" != *"--only-binary"* ]]
}

@test "without a Rust toolchain, installs checkov wheels-only (pip WITH --only-binary=:all:) — skips the doomed source compile" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stub_pip 0
  # Force the wheels-only branch deterministically, even on a host with a real
  # cargo/rustc on PATH: point the seams at paths that do not exist.
  export CARGO_BIN="$BATS_TEST_TMPDIR/no-cargo"
  export RUSTC_BIN="$BATS_TEST_TMPDIR/no-rustc"
  root="$(_repo)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]
  [ "$(yq -r '.status' "$SEC_OUTPUT_DIR/iac/report.json")" = "SKIPPED" ]
  [[ "$output" != *"ERROR @"* ]]
  # wheels-only branch: the recorded pip argv carries the flag that avoids building
  # rustworkx from source (needs Rust) when no wheel matches this Python.
  grep -qF -- '--only-binary=:all:' "$PIP_ARGV"
}
