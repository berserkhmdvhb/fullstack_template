#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# devsec-sbomqs.bats — unit tests for devsec.sh's report-only SBOM-analysis
# subcommands: cmd_sbomqs (sbomqs quality + NTIA compliance scoring) and
# cmd_sbom_utility (CycloneDX sbom-utility schema validation).
#
# These subcommands invoke their own CLIs directly (no scan-*.sh wrapper), so a
# stub sbomqs / sbom-utility goes on PATH via the SBOMQS_BIN / SBOM_UTILITY_BIN
# module var: it records its argv and exits a per-case code. A stub
# tools-install.sh simulates an install failure. So the subcommand logic —
# resolve the SBOM, ensure the pinned tool (install-if-missing, honest SKIPPED on
# failure), run the CLI, advisory exit-0 — is tested without installing or
# running the real tools. Each case runs in its own shell, so no state bleeds.
# -----------------------------------------------------------------------------

setup() {
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/devsec.sh"
  BASE_PATH="$PATH"   # preserve the real PATH (yq, which reads the config pin) — prepend the stub dir
  # A ready consolidated SBOM for the subcommands to resolve.
  export SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  mkdir -p "$SEC_OUTPUT_DIR/sbom"
  printf '%s' '{"bomFormat":"CycloneDX","specVersion":"1.6","components":[]}' \
    > "$SEC_OUTPUT_DIR/sbom/backend.image.sbom.cdx.json"
  # The stub dir leads PATH so the stub tool resolves first.
  mkdir -p "$BATS_TEST_TMPDIR/bin"
  export PATH="$BATS_TEST_TMPDIR/bin:$BASE_PATH"
}

# _stub_sbomqs EXIT — stub sbomqs: append argv to $SBOMQS_ARGV, exit $1.
_stub_sbomqs() {
  cat > "$BATS_TEST_TMPDIR/bin/sbomqs" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$SBOMQS_ARGV"
exit "${SBOMQS_EXIT:-0}"
EOF
  chmod +x "$BATS_TEST_TMPDIR/bin/sbomqs"
  export SBOMQS_BIN="$BATS_TEST_TMPDIR/bin/sbomqs"
  export SBOMQS_ARGV="$BATS_TEST_TMPDIR/sbomqs.argv"; : > "$SBOMQS_ARGV"
  export SBOMQS_EXIT="${1:-0}"
}

# _stub_sbom_utility EXIT — stub sbom-utility: append argv to $SBOM_UTILITY_ARGV, exit $1.
_stub_sbom_utility() {
  cat > "$BATS_TEST_TMPDIR/bin/sbom-utility" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$SBOM_UTILITY_ARGV"
exit "${SBOM_UTILITY_EXIT:-0}"
EOF
  chmod +x "$BATS_TEST_TMPDIR/bin/sbom-utility"
  export SBOM_UTILITY_BIN="$BATS_TEST_TMPDIR/bin/sbom-utility"
  export SBOM_UTILITY_ARGV="$BATS_TEST_TMPDIR/sbom-utility.argv"; : > "$SBOM_UTILITY_ARGV"
  export SBOM_UTILITY_EXIT="${1:-0}"
}

# _stub_install_fail — simulate "<tool> absent AND its install fails": point the
# *_BIN var at a path that does not exist (so command -v misses even if a real
# tool is on the host PATH) and TOOLS_INSTALLER at a stub that exits 1. The
# report-only subcommand must then degrade to an honest SKIPPED — never hard-die
# (trap_err) and never falsely report a clean pass.
_stub_install_fail() {
  cat > "$BATS_TEST_TMPDIR/bin/tools-install.sh" <<'EOF'
#!/usr/bin/env bash
echo "fake: cannot install" >&2
exit 1
EOF
  chmod +x "$BATS_TEST_TMPDIR/bin/tools-install.sh"
  export TOOLS_INSTALLER="$BATS_TEST_TMPDIR/bin/tools-install.sh"
}

# ---------- sbomqs ----------

@test "sbomqs scores the existing SBOM (ntia profile) and exits 0 — report-only" {
  _stub_sbomqs 0
  run bash "$SCRIPT" sbomqs
  [ "$status" -eq 0 ]
  grep -qF 'score --profile ntia' "$SBOMQS_ARGV"                 # scored the SBOM, NTIA profile
  grep -qF "$SEC_OUTPUT_DIR/sbom/backend.image.sbom.cdx.json" "$SBOMQS_ARGV"
  [[ "$output" == *"SBOM: backend.image.sbom.cdx.json"* ]]
}

@test "sbomqs with no SBOM present skips cleanly (run sbom first) — exits 0" {
  _stub_sbomqs 0
  rm -f "$SEC_OUTPUT_DIR/sbom/backend.image.sbom.cdx.json"
  run bash "$SCRIPT" sbomqs
  [ "$status" -eq 0 ]
  [[ "$output" == *"no SBOM"* ]]                                # actionable, not a crash
  [[ ! -s "$SBOMQS_ARGV" ]]                                     # the CLI was never invoked
}

@test "sbomqs tool unavailable (install fails) degrades to an honest SKIPPED — exits 0, no hard-die" {
  _stub_install_fail
  export SBOMQS_BIN="$BATS_TEST_TMPDIR/bin/sbomqs-absent"
  run bash "$SCRIPT" sbomqs
  [ "$status" -eq 0 ]                                           # report-only: never gates
  [[ "$output" == *"SKIPPED"* ]]                                # the coverage gap is visible
  [[ "$output" != *"ERROR @"* ]]                                # did not hard-die via trap_err
}

# ---------- sbom-utility ----------

@test "sbom-utility validates the existing SBOM (inferred format) and reports valid — exits 0" {
  _stub_sbom_utility 0
  run bash "$SCRIPT" sbom-utility
  [ "$status" -eq 0 ]
  grep -qF 'validate -i' "$SBOM_UTILITY_ARGV"                   # validated the SBOM by path
  grep -qF "$SEC_OUTPUT_DIR/sbom/backend.image.sbom.cdx.json" "$SBOM_UTILITY_ARGV"
  [[ "$output" == *"schema-valid"* ]]
}

@test "a schema error (validate exits non-zero) is reported, not gated — still exits 0" {
  _stub_sbom_utility 1
  run bash "$SCRIPT" sbom-utility
  [ "$status" -eq 0 ]                                           # advisory: a bad SBOM never blocks
  [[ "$output" == *"validation errors"* ]]                      # the verdict is visible
  [[ "$output" != *"ERROR @"* ]]                                # the non-zero exit was captured, not trapped
}

@test "sbom-utility tool unavailable (install fails) degrades to an honest SKIPPED — exits 0, no hard-die" {
  _stub_install_fail
  export SBOM_UTILITY_BIN="$BATS_TEST_TMPDIR/bin/sbom-utility-absent"
  run bash "$SCRIPT" sbom-utility
  [ "$status" -eq 0 ]
  [[ "$output" == *"SKIPPED"* ]]
  [[ "$output" != *"ERROR @"* ]]
}

# ---------- pass-through (extra args reach the tool verbatim) ----------

@test "sbomqs passes extra args through (compliance --ntia), SBOM appended — default NOT used" {
  _stub_sbomqs 0
  run bash "$SCRIPT" sbomqs compliance --ntia
  [ "$status" -eq 0 ]
  grep -qF 'compliance --ntia' "$SBOMQS_ARGV"                  # the passthrough args reached the tool
  grep -qF "$SEC_OUTPUT_DIR/sbom/backend.image.sbom.cdx.json" "$SBOMQS_ARGV"
  [[ "$output" != *"score --profile ntia"* ]]                 # the canonical default was not substituted in
}

@test "sbom-utility passes extra args through (license list), SBOM appended — default NOT used" {
  _stub_sbom_utility 0
  run bash "$SCRIPT" sbom-utility license list
  [ "$status" -eq 0 ]
  grep -qF 'license list' "$SBOM_UTILITY_ARGV"                # the passthrough args reached the tool
  grep -qF "$SEC_OUTPUT_DIR/sbom/backend.image.sbom.cdx.json" "$SBOM_UTILITY_ARGV"
  [[ "$output" != *"validate -i"* ]]                          # the canonical default was not substituted in
}
