#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# devsec-verbose.bats — unit tests for the opt-in DEVSEC_VERBOSE=1 local view:
# after the frozen CI script has run, the local runner surfaces the scanners'
# own detail rendered FROM the artifacts the scan wrote (never a re-scan):
#   - cmd_vuln: grype's native per-vulnerability table from *.vuln.grype.json.
#   - cmd_sbom: the syft run log (when non-empty) + a per-SBOM component count
#     from each CycloneDX JSON.
# The default run (no flag) must stay the concise, actionable digest — so each
# "verbose shows X" case is paired with a "default hides X" guard.
#
# Like devsec-vex-vuln/-chain.bats: mutating commands run via `run bash "$SCRIPT"`
# with a controlled PATH — a stub CI script WRITES the artifacts a real scan would
# (grype JSON / CycloneDX SBOM + a syft log), a stub git lets _bootstrap succeed.
# Each case runs in its own shell, so module state never bleeds.
# -----------------------------------------------------------------------------

setup() {
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/devsec.sh"
  CORE_PATH="/usr/bin:/bin"
}

# _stub_git — a stub git so _bootstrap resolves registry/project from a URL.
_stub_git() {
  local bindir="$BATS_TEST_TMPDIR/bin"
  cat > "$bindir/git" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  config) echo https://github.com/acme/service-template.git ;;
  *) echo abc123def456 ;;
esac
EOF
  chmod +x "$bindir/git"
}

# _setup_vuln_verbose — stub scan-vulnerabilities.sh so it writes what the real
# frozen script would: grype/summary.txt (the derived digest the runner already
# surfaces) AND a per-component *.vuln.grype.json (the artifact the VERBOSE view
# renders from). Two matches: one with a fix + CVSS, one without.
_setup_vuln_verbose() {
  local bindir="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$bindir" "$BATS_TEST_TMPDIR/repo" "$BATS_TEST_TMPDIR/out/grype"
  export SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  export REPO_ROOT="$BATS_TEST_TMPDIR/repo"
  printf 'BACKEND_IMAGE=registry.example/backend:local-abc\n' > "$SEC_OUTPUT_DIR/scan.env"
  cat > "$SEC_OUTPUT_DIR/grype/summary.txt" <<'SUM'
============================================================
 Grype Summary (per component)
============================================================
[CONTAINER IMAGE]
SBOM: backend.image.sbom.cdx.json
Status: FAIL
SUM
  cat > "$SEC_OUTPUT_DIR/grype/backend.image.vuln.grype.json" <<'JSON'
{
  "matches": [
    {"vulnerability":{"id":"CVE-2026-999001","severity":"High","cvss":[{"metrics":{"baseScore":8.1}}]},"artifact":{"name":"libssl3","version":"3.5.9-r0"},"fixVersions":["3.5.9-r1"]},
    {"vulnerability":{"id":"CVE-2026-999002","severity":"Medium"},"artifact":{"name":"python","version":"3.13.18"}}
  ]
}
JSON
  cat > "$bindir/scan-vulnerabilities.sh" <<'EOF'
#!/usr/bin/env bash
# The real frozen script writes these artifacts; the stub just leaves them in place.
exit 0
EOF
  chmod +x "$bindir/scan-vulnerabilities.sh"
  export VULN_SCAN_SH="$bindir/scan-vulnerabilities.sh"
  _stub_git
  export PATH="$bindir:$CORE_PATH"
}

# _setup_sbom_verbose — stub sbom-generate.sh so it writes two CycloneDX SBOMs
# (so the VERBOSE view can count components) and a non-empty syft run log.
_setup_sbom_verbose() {
  local bindir="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$bindir" "$BATS_TEST_TMPDIR/repo" "$BATS_TEST_TMPDIR/out/sbom" "$BATS_TEST_TMPDIR/out/logs"
  export SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  export REPO_ROOT="$BATS_TEST_TMPDIR/repo"
  cat > "$SEC_OUTPUT_DIR/sbom/backend.image.sbom.cdx.json" <<'JSON'
{"bomFormat":"CycloneDX","specVersion":"1.5","version":1,"components":[
  {"name":"pkg-a","version":"1.0","type":"library"},
  {"name":"pkg-b","version":"2.0","type":"library"}
]}
JSON
  printf '[12:00:00] Generating IMAGE SBOM (backend)\n' > "$SEC_OUTPUT_DIR/logs/syft-20260101-120000.log"
  cat > "$bindir/sbom-generate.sh" <<'EOF'
#!/usr/bin/env bash
# The real frozen script writes these artifacts; the stub just leaves them in place.
exit 0
EOF
  chmod +x "$bindir/sbom-generate.sh"
  export SBOM_GEN_SH="$bindir/sbom-generate.sh"
  _stub_git
  export PATH="$bindir:$CORE_PATH"
}

# --- cmd_vuln: grype native table (VERBOSE) ---------------------------------

@test "vuln: DEVSEC_VERBOSE=1 renders grype's native per-vulnerability table from the artifact" {
  _setup_vuln_verbose
  DEVSEC_VERBOSE=1 run bash "$SCRIPT" vuln
  [ "$status" -eq 0 ]
  # native columns + the two findings' data (name, CVE id, severity, fix, CVSS)
  [[ "$output" == *"VULNERABILITY"* ]]
  [[ "$output" == *"CVE-2026-999001"* ]]
  [[ "$output" == *"libssl3"* ]]
  [[ "$output" == *"3.5.9-r1"* ]]
  [[ "$output" == *"High"* ]]
}

@test "vuln: without DEVSEC_VERBOSE the native table stays hidden (concise digest only)" {
  _setup_vuln_verbose
  run bash "$SCRIPT" vuln
  [ "$status" -eq 0 ]
  # the derived digest the runner always surfaces…
  [[ "$output" == *"scan results"* ]]
  # …but NOT the native per-finding detail (that is opt-in).
  [[ "$output" != *"CVE-2026-999001"* ]]
  [[ "$output" != *"VULNERABILITY"* ]]
}

# --- cmd_sbom: syft summary (VERBOSE) ---------------------------------------

@test "sbom: DEVSEC_VERBOSE=1 shows the syft run log and per-SBOM component counts" {
  _setup_sbom_verbose
  DEVSEC_VERBOSE=1 run bash "$SCRIPT" sbom
  [ "$status" -eq 0 ]
  [[ "$output" == *"syft run log"* ]]
  [[ "$output" == *"SBOM backend.image: 2 components"* ]]
}

@test "sbom: without DEVSEC_VERBOSE the artifact-derived summary stays hidden" {
  _setup_sbom_verbose
  run bash "$SCRIPT" sbom
  [ "$status" -eq 0 ]
  [[ "$output" != *"SBOM backend.image: 2 components"* ]]
  [[ "$output" != *"syft run log"* ]]
}

# --- edge case: empty (0-byte) grype artifact -------------------------------

@test "vuln: an empty (0-byte) grype artifact renders '(no findings)' with a 0 count, no error" {
  _setup_vuln_verbose
  : > "$SEC_OUTPUT_DIR/grype/backend.image.vuln.grype.json"    # truncate to 0 bytes
  DEVSEC_VERBOSE=1 run bash "$SCRIPT" vuln
  [ "$status" -eq 0 ]                                          # no hard-die on the empty artifact
  # Empty artifact -> 0 count + honest "(no findings)". grep -F (fixed string), not a
  # [[ == glob ]]: the literal parentheses would otherwise be parsed by the matcher.
  printf '%s\n' "$output" | grep -qF "backend.image (0)"
  printf '%s\n' "$output" | grep -qF "(no findings)"
}
