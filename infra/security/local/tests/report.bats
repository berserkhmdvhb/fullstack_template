#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# report.bats — unit tests for report.sh (the read-only view layer over the
# scan JSON in output/security).
#
# report.sh never runs a scanner, so — unlike the chain tests — these need no
# stubbed PATH and no docker. Each case gets a fresh temp dir that we populate
# with tiny fixture JSON (CycloneDX SBOM, grype summary, OSV-Scanner V2 report,
# OpenVEX doc) and point report.sh at via SEC_OUTPUT_DIR, then assert on the
# rendered text: the per-axis summaries, the PASS/FAIL verdict logic, the
# MAL-* flag, search, the jq passthrough, graceful "not run yet" degradation and
# the unknown-command exit code.
# -----------------------------------------------------------------------------

setup() {
  # report.sh is a sibling of this file's parent (.../local/report.sh).
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/report.sh"
  OUT="$BATS_TEST_TMPDIR/output/security"
  mkdir -p "$OUT/sbom" "$OUT/grype" "$OUT/osv" "$OUT/vex"
  export SEC_OUTPUT_DIR="$OUT"
}

# --- minimal fixtures (valid enough for the jq views) ------------------------
_write_sbom() {
  cat > "$OUT/sbom/backend.image.sbom.cdx.json" <<'JSON'
  {"bomFormat":"CycloneDX","specVersion":"1.6","serialNumber":"urn:uuid:x","version":1,
   "metadata":{"timestamp":"2026-01-01Z","component":{"name":"backend.image","type":"application"}},
   "components":[
     {"type":"library","name":"python","version":"3.13"},
     {"type":"library","name":"flask","version":"3.0"},
     {"type":"operating-system","name":"debian"}
   ]}
JSON
}
_write_grype() { # $1 = severityCounts object body
  cat > "$OUT/grype/summary.json" <<JSON
  {"generatedAt":"2026-01-01Z","failOn":"high",
   "components":[{"component":"backend.image","group":"CONTAINER IMAGE","total":1,"fixable":0,"severityCounts":{$1}}]}
JSON
}
# The raw grype JSON carries the per-match vulnerability ids (CVE-/GHSA-/…) that
# the consolidated summary.json does not — the fixture the findings listing reads.
_write_grype_raw() {
  cat > "$OUT/grype/backend.image.vuln.grype.json" <<'JSON'
  {"matches":[
    {"artifact":{"name":"apt","version":"3.0.3"},"vulnerability":{"id":"CVE-2025-1234","severity":"high","fixStates":["fixed"],"fix":{"versions":["3.0.4"]}}},
    {"artifact":{"name":"libapt-pkg7.0","version":"3.0.3"},"vulnerability":{"id":"CVE-2025-1234","severity":"high","fixStates":["fixed"],"fix":{"versions":["3.0.4"]}}},
    {"artifact":{"name":"python","version":"3.13.18"},"vulnerability":{"id":"GHSA-abcd-efgh","severity":"medium","fixStates":["fixed"],"fix":{"versions":["3.16.0"]}}},
    {"artifact":{"name":"openssl","version":"3.0.13"},"vulnerability":{"id":"CVE-2024-0001","severity":"low","fixStates":["not_fixed"]}}
  ]}
JSON
}
_write_osv() {
  cat > "$OUT/osv/report.json" <<'JSON'
  {"results":[
    {"packages":[{"package":{"name":"pandsa","version":"0.0.1"},"vulnerabilities":[{"id":"MAL-2023-1937"}]}],
     "source":{"name":"malicious-requirements.txt"}}
  ]}
JSON
}
_write_vex() {
  cat > "$OUT/vex/openvex.json" <<'JSON'
  {"@context":"https://openvex.dev/ns/v0.2.0","@id":"https://example.com/vex-1","author":"test","version":1,"timestamp":"2026-01-01Z",
   "statements":[{"@id":"x","vulnerability_id":"CVE-2026-1","status":"not_affected","justification":"fixed_version"}]}
JSON
}
_write_grant() { # $1 = denied count
  mkdir -p "$OUT/grant"
  cat > "$OUT/grant/summary.json" <<'JSON'
  {"generatedAt":"2026-01-01Z","mode":"list","policy":"null",
   "components":[{"component":"backend.image","status":"PASS","report":"backend.image.license.grant.json"}]}
JSON
  cat > "$OUT/grant/backend.image.license.grant.json" <<JSON
  {"tool":"grant","version":"0.6.8","run":{"targets":[{"evaluation":{"status":"list",
    "summary":{"packages":{"total":10,"allowed":0,"denied":$1,"ignored":0,"unlicensed":1},
    "licenses":{"unique":5,"allowed":0,"denied":$1,"nonSPDX":0}}}}]}}
JSON
}

# -----------------------------------------------------------------------------
# graceful degradation: an empty output dir reports "not run yet" everywhere
# -----------------------------------------------------------------------------
@test "summary with no outputs degrades, does not error" {
  run bash "$SCRIPT" summary
  [ "$status" -eq 0 ]
  [ "$(grep -c 'not run yet' <<<"$output")" -eq 5 ]
}

# -----------------------------------------------------------------------------
# per-axis summaries render the key fields
# -----------------------------------------------------------------------------
@test "sbom view reports component count and by-type breakdown" {
  _write_sbom
  run bash "$SCRIPT" sbom
  [ "$status" -eq 0 ]
  [[ "$output" == *"components: 3"* ]]
  [[ "$output" == *"library: 2"* ]]
  [[ "$output" == *"operating-system: 1"* ]]
}

@test "vex view reports the statement status" {
  _write_vex
  run bash "$SCRIPT" vex
  [ "$status" -eq 0 ]
  [[ "$output" == *"statements: 1"* ]]
  [[ "$output" == *"[not_affected] CVE-2026-1"* ]]
}

# -----------------------------------------------------------------------------
# grype verdict logic: PASS below threshold, FAIL at/above it
# -----------------------------------------------------------------------------
@test "vuln verdict is PASS when max severity is below fail-on" {
  _write_sbom; _write_grype '"LOW":1'
  run bash "$SCRIPT" vuln
  [ "$status" -eq 0 ]
  [[ "$output" == *"verdict: PASS"* ]]
}

@test "vuln verdict is FAIL when a severity reaches fail-on" {
  _write_sbom; _write_grype '"HIGH":1'
  run bash "$SCRIPT" vuln
  [ "$status" -eq 0 ]
  [[ "$output" == *"verdict: FAIL"* ]]
}

# -----------------------------------------------------------------------------
# findings listing: id-leading (the CVE/GHSA handle), package@version, severity,
# fix version — read from the raw grype JSON, capped so noisy pkgs stay scannable
# -----------------------------------------------------------------------------
@test "vuln view lists top findings id-leading (id, pkg@ver, severity, fix)" {
  _write_grype_raw
  run bash "$SCRIPT" vuln
  [ "$status" -eq 0 ]
  [[ "$output" == *"✗ CVE-2025-1234"* ]]     # id leads the line
  [[ "$output" == *"apt@3.0.3"* ]]
  [[ "$output" == *"GHSA-abcd-efgh"* ]]       # non-CVE id too
  [[ "$output" == *"HIGH"* ]]
  [[ "$output" == *"fix → 3.0.4"* ]]
}

@test "vuln view omits the fix hint when no fix version is available" {
  # openssl in the raw fixture has a low CVE and no fix.versions, so its line
  # ends at the severity with no trailing "fix → …".
  _write_grype_raw
  run bash "$SCRIPT" vuln
  [ "$status" -eq 0 ]
  [[ "$output" == *"✗ CVE-2024-0001  openssl@3.0.13  LOW" ]]
  [[ "$output" != *"openssl"*"fix →"* ]]
}

# -----------------------------------------------------------------------------
# osv flags MAL-* (the malicious typosquat) as YES
# -----------------------------------------------------------------------------
@test "osv view flags a MAL-* match" {
  _write_osv
  run bash "$SCRIPT" osv
  [ "$status" -eq 0 ]
  [[ "$output" == *"MALICIOUS (MAL-*): YES"* ]]
  [[ "$output" == *"pandsa@0.0.1"* ]]
}

# -----------------------------------------------------------------------------
# license (grant): per-component status + totals; denied => FAIL
# -----------------------------------------------------------------------------
@test "license view reports status and package totals (PASS)" {
  _write_grant 0
  run bash "$SCRIPT" license
  [ "$status" -eq 0 ]
  [[ "$output" == *"[PASS] backend.image"* ]]
  [[ "$output" == *"packages: total=10"* ]]
  [[ "$output" == *"verdict: PASS"* ]]
}

@test "license verdict is FAIL when licenses are denied" {
  _write_grant 3
  run bash "$SCRIPT" license
  [ "$status" -eq 0 ]
  [[ "$output" == *"denied=3"* ]]
  [[ "$output" == *"verdict: FAIL"* ]]
}

# -----------------------------------------------------------------------------
# summary aggregates every axis
# -----------------------------------------------------------------------------
@test "summary prints all five axis headers" {
  _write_sbom; _write_grype '"LOW":1'; _write_osv; _write_vex
  run bash "$SCRIPT" summary
  [ "$status" -eq 0 ]
  [[ "$output" == *"SBOM (syft)"* ]]
  [[ "$output" == *"VULNERABILITY (grype)"* ]]
  [[ "$output" == *"KNOWN-VULN SCA (osv)"* ]]
  [[ "$output" == *"VEX (openvex)"* ]]
  [[ "$output" == *"LICENSE (grant)"* ]]
}

# -----------------------------------------------------------------------------
# search greps the structured JSON (SARIF exchange files excluded)
# -----------------------------------------------------------------------------
@test "search finds a pattern in the structured JSON" {
  _write_osv
  run bash "$SCRIPT" search MAL-2023-1937
  [ "$status" -eq 0 ]
  [[ "$output" == *"report.json"* ]]
  [[ "$output" == *"MAL-2023-1937"* ]]
}

@test "search without a pattern is a usage error" {
  run bash "$SCRIPT" search
  [ "$status" -eq 2 ]
}

# -----------------------------------------------------------------------------
# jq passthrough (a path or bare name resolves under the output dir)
# -----------------------------------------------------------------------------
@test "jq passthrough evaluates against a resolved output file" {
  _write_grype '"LOW":1'
  run bash "$SCRIPT" jq grype/summary.json '.failOn'
  [ "$status" -eq 0 ]
  [[ "$output" == *high* ]]
}

# -----------------------------------------------------------------------------
# unknown command exits non-zero with usage
# -----------------------------------------------------------------------------
@test "unknown command exits 2" {
  run bash "$SCRIPT" bogus
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown command"* ]]
}
