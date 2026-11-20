#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# origin.bats — unit tests for the ORIGIN classifier (_origin.sh) and its two
# report.sh surfaces: the "by origin" line in the vuln view, and `report.sh
# origin`.
#
# Origin is DERIVED from each finding's purl at render time and never stored
# (route #3, wiki/handling-vulnerabilities.md), so the classifier logic is the
# thing under test: a wrong bucket is a *systematic* error across every report,
# so it is pinned here rather than left silent-wrong. Like report.bats, these are
# read-only views over JSON — no scanner, no docker, no stubbed PATH — just tiny
# inline grype fixtures pointed at via SEC_OUTPUT_DIR.
# -----------------------------------------------------------------------------

setup() {
  DIR="$(dirname "$BATS_TEST_DIRNAME")"            # infra/security/local
  ORIGIN="$DIR/_origin.sh"
  SCRIPT="$DIR/report.sh"
  OUT="$BATS_TEST_TMPDIR/output/security"
  mkdir -p "$OUT/grype"
  export SEC_OUTPUT_DIR="$OUT"
}

# One finding per origin bucket in a single grype JSON, so the classifier's whole
# map is exercised at once. purls chosen to hit each branch:
#   perl-base  deb, /var/lib/dpkg/status      -> os-base
#   libc6      deb, /usr/lib                  -> os-base   (2nd, lower sev -> order)
#   python     generic, runtime name          -> interpreter
#   flask      pypi, site-packages            -> app-dep
#   acme-lib   pypi, /app/vendor/...          -> vendored  (location overrides purl)
# Built with `jq -n` (not a hand-written heredoc) so the fixture is guaranteed
# valid JSON — a prior version silently dropped the match object's closing brace
# and every test downstream failed on a parse error.
_write_grype() {
  jq -n '{matches:[
    {artifact:{name:"perl-base",version:"5.40.1",type:"deb",purl:"pkg:deb/debian/perl-base@5.40.1?distro=debian-13",locations:[{path:"/var/lib/dpkg/status"}]},vulnerability:{id:"CVE-2026-82560",severity:"high",fix:{}}},
    {artifact:{name:"libc6",version:"2.41",type:"deb",purl:"pkg:deb/debian/libc6@2.41",locations:[{path:"/usr/lib/x86_64-linux-gnu/libc.so.6"}]},vulnerability:{id:"CVE-2026-19499",severity:"low",fix:{versions:["2.42"]}}},
    {artifact:{name:"python",version:"3.13.18",type:"generic",purl:"pkg:generic/python@3.13.18",locations:[{path:"/usr/local/bin/python3"}]},vulnerability:{id:"CVE-2026-103995",severity:"low",fix:{versions:["3.16.0"]}}},
    {artifact:{name:"flask",version:"3.0",type:"python",purl:"pkg:pypi/flask@3.0",locations:[{path:"/usr/local/lib/python3.13/site-packages/flask/__init__.py"}]},vulnerability:{id:"CVE-2025-0001",severity:"medium",fix:{versions:["3.0.1"]}}},
    {artifact:{name:"acme-lib",version:"1.2.3",type:"python",purl:"pkg:pypi/acme-lib@1.2.3",locations:[{path:"/app/vendor/acme-lib/acme.py"}]},vulnerability:{id:"CVE-2025-9999",severity:"critical",fix:{}}}
  ]}' > "$OUT/grype/backend.image.vuln.grype.json"
}

# -----------------------------------------------------------------------------
# classifier: purl TYPE -> bucket (the deterministic core)
# -----------------------------------------------------------------------------
@test "os distribution packages bucket as os-base" {
  run bash "$ORIGIN" of "pkg:deb/debian/apt@3.0.3" "/var/lib/dpkg/status"
  [ "$status" -eq 0 ]; [ "$output" = "os-base" ]
  run bash "$ORIGIN" of "pkg:apk/alpine/libressl@3.9.1" "/lib/apk/db/installed"
  [ "$output" = "os-base" ]
  run bash "$ORIGIN" of "pkg:rpm/cyphalinux/openssl@3.1.0" "/var/lib/rpm/Packages"
  [ "$output" = "os-base" ]
}

@test "the base image and language runtimes bucket as interpreter" {
  run bash "$ORIGIN" of "pkg:oci/python@sha256:09a1?tag=3.13-slim" ""
  [ "$output" = "interpreter" ]
  # runtime matched by NAME, even when delivered as an OS package:
  run bash "$ORIGIN" of "pkg:deb/debian/python3@3.13.18" "/usr/bin/python3.13"
  [ "$output" = "interpreter" ]
  run bash "$ORIGIN" of "pkg:generic/python@3.13.18" "/usr/local/bin/python3"
  [ "$output" = "interpreter" ]
}

@test "registry language libraries bucket as app-dep" {
  run bash "$ORIGIN" of "pkg:pypi/flask@3.0" "/usr/local/lib/python3.13/site-packages"
  [ "$output" = "app-dep" ]
  run bash "$ORIGIN" of "pkg:npm/express@4.18.2" "/app/node_modules/express"
  [ "$output" = "app-dep" ]
  run bash "$ORIGIN" of "pkg:cargo/serde@1.0" "/root/registry"
  [ "$output" = "app-dep" ]
}

@test "an app-tree location overrides the purl and buckets as vendored" {
  # flask is a pypi app-dep by type, but this copy was vendored into the tree.
  run bash "$ORIGIN" of "pkg:pypi/flask@3.0" "/app/vendor/flask/__init__.py"
  [ "$output" = "vendored" ]
  run bash "$ORIGIN" of "pkg:npm/lodash@4.17.0" "/src/third_party/js/lodash.js"
  [ "$output" = "vendored" ]
}

@test "an unrecognised ecosystem is named, not mis-bucketed" {
  run bash "$ORIGIN" of "pkg:swid/example.com/foo@1.0" "/opt/foo"
  [ "$output" = "unknown" ]
}

@test "a missing purl degrades to unknown rather than erroring" {
  run bash "$ORIGIN" of "" "/some/path"
  [ "$status" -eq 0 ]; [ "$output" = "unknown" ]
}

# -----------------------------------------------------------------------------
# summary: counts per bucket, derived from the grype JSON
# -----------------------------------------------------------------------------
@test "origin summary counts findings per bucket (os-base leads, count-desc)" {
  _write_grype
  run bash "$ORIGIN" summary "$OUT/grype/backend.image.vuln.grype.json"
  [ "$status" -eq 0 ]
  [[ "$output" == *"os-base=2"* ]]     # perl-base + libc6
  [[ "$output" == *"interpreter=1"* ]]
  [[ "$output" == *"app-dep=1"* ]]
  [[ "$output" == *"vendored=1"* ]]
  # the highest count (os-base=2) sorts first
  [[ "$(head -1 <<<"$output")" == "os-base=2" ]]
}

# -----------------------------------------------------------------------------
# report.sh vuln view carries the "by origin" breakdown
# -----------------------------------------------------------------------------
@test "vuln view shows a by-origin breakdown over the raw grype JSON" {
  _write_grype
  run bash "$SCRIPT" vuln
  [ "$status" -eq 0 ]
  [[ "$output" == *"by origin:"* ]]
  [[ "$output" == *"os-base=2"* ]]
  [[ "$output" == *"app-dep=1"* ]]
  [[ "$output" == *"vendored=1"* ]]
}

# -----------------------------------------------------------------------------
# report.sh origin: human grouping, fix-ownership order + severity order
# -----------------------------------------------------------------------------
@test "origin view groups by bucket in fix-ownership order (interpreter, os-base, app-dep, vendored)" {
  _write_grype
  run bash "$SCRIPT" origin
  [ "$status" -eq 0 ]
  [[ "$output" == *"VULNERABILITY BY ORIGIN"* ]]
  # interpreter leads (base-bump), vendored last — not alphabetical.
  local pi po pa pv
  pi="$(grep -n 'interpreter —' <<<"$output" | head -1 | cut -d: -f1)"
  po="$(grep -n 'os-base —'        <<<"$output" | head -1 | cut -d: -f1)"
  pa="$(grep -n 'app-dep —'        <<<"$output" | head -1 | cut -d: -f1)"
  pv="$(grep -n 'vendored —'       <<<"$output" | head -1 | cut -d: -f1)"
  [ -n "$pi" ] && [ -n "$po" ] && [ -n "$pa" ] && [ -n "$pv" ]
  [ "$pi" -lt "$po" ]; [ "$po" -lt "$pa" ]; [ "$pa" -lt "$pv" ]
}

@test "origin view sorts each bucket by severity (high before low within os-base)" {
  _write_grype
  run bash "$SCRIPT" origin
  [ "$status" -eq 0 ]
  # perl-base is HIGH, libc6 is LOW; both os-base, so perl-base must lead.
  [[ "$output" == *perl-base*libc6* ]]
}

# -----------------------------------------------------------------------------
# report.sh origin tsv: the scriptable surface for the triage/SLA loop
# -----------------------------------------------------------------------------
@test "origin tsv emits one raw row per match, bucket-leading" {
  _write_grype
  run bash "$SCRIPT" origin tsv
  [ "$status" -eq 0 ]
  # 5 matches -> 5 rows; each row starts with its bucket (tab-separated).
  [ "$(grep -c '' <<<"$output")" -eq 5 ]
  [[ "$(grep -c '^os-base	' <<<"$output")" -eq 2 ]]
  [[ "$(grep -c '^interpreter	' <<<"$output")" -eq 1 ]]
  [[ "$(grep -c '^app-dep	' <<<"$output")" -eq 1 ]]
  [[ "$(grep -c '^vendored	' <<<"$output")" -eq 1 ]]
  # the vendored row (acme-lib, critical) is the one starting with the bucket
  [[ "$(grep $'^vendored\t' <<<"$output")" == *acme-lib@1.2.3* ]]
  [[ "$(grep $'^vendored\t' <<<"$output")" == *CRITICAL* ]]
}

# -----------------------------------------------------------------------------
# graceful degradation: no scan -> "not run yet", not an error
# -----------------------------------------------------------------------------
@test "origin view degrades when no scan has run" {
  run bash "$SCRIPT" origin
  [ "$status" -eq 0 ]
  [[ "$output" == *"not run yet"* ]]
}
