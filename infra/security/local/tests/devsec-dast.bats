#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# devsec-dast.bats — unit tests for the dynamic app scan (infra/security/local/
# scan-dast.sh), the single source shared by the CI `dast_scan` job and
# `devsec.sh dast`. Two stubs go on PATH: `docker` (records its argv; `run -d`
# echoes a container id, `--rm … zap-baseline.py` writes the SARIF/JSON and exits
# ${ZAP_EXIT:-0}, `info` exits ${DOCKER_INFO_EXIT:-0}, `rm -f` tears down) and
# `curl` (the health probe: succeeds only after ${HEALTH_OK_AFTER:-0} calls). So
# the scan logic — docker-availability guard, start-app → bounded health-poll →
# zap-baseline.py → ALWAYS tear down → SARIF merge + report.json, advisory
# exit-0 — is tested without Docker or a running app. Each case runs in its own
# shell, so no state bleeds.
# -----------------------------------------------------------------------------

setup() {
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/scan-dast.sh"
  # Preserve the real PATH (it carries yq, which the script's cfg() reads the pin
  # from) and just prepend the stub dir, so the stubs resolve first but
  # yq/python3/jq are still found.
  BASE_PATH="$PATH"
}

# _stubs HEALTH_OK_AFTER ZAP_EXIT — install the docker + curl stubs and their env.
# DOCKER_INFO_EXIT (optional 3rd arg) drives the "docker unavailable" case.
# HEALTH_RETRIES (optional 4th arg) bounds the health-poll (small for the
# never-ready case). The app image / target URL / health-delay are exported here
# (inside the helper) rather than in the @test bodies, so shellcheck does not
# flag them as test-local (SC2030/SC2031: a @test runs in a subshell).
_stubs() {
  local health_ok="${1:-0}" zap_exit="${2:-0}"
  export DOCKER_INFO_EXIT="${3:-0}"
  export HEALTH_OK_AFTER="$health_ok"
  export ZAP_EXIT="$zap_exit"
  export DAST_HEALTH_RETRIES="${4:-5}"
  export DAST_HEALTH_DELAY=0
  # The app to start + scan (same for every case).
  export DAST_TARGET_IMAGE="app:tag"
  export DAST_TARGET_URL="http://localhost:8000"
  # Where the ZAP stub writes its reports: the host OUT_DIR the script reads from.
  export DAST_STUB_OUT="${SEC_OUTPUT_DIR%/}/dast"
  mkdir -p "$DAST_STUB_OUT"

  local bindir="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$bindir"

  # docker stub: record argv; dispatch on the subcommand.
  cat > "$bindir/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$DOCKER_ARGV"
case "${1:-}" in
  info) exit "${DOCKER_INFO_EXIT:-0}" ;;
  run)
    is_start=0; is_zap=0
    for a in "$@"; do
      case "$a" in -d) is_start=1 ;; --rm) is_zap=1 ;; esac
    done
    if [ "$is_start" = 1 ]; then
      echo "container123"   # the container id a real `run -d` prints
      exit 0
    fi
    if [ "$is_zap" = 1 ]; then
      json_arg=""
      args=("$@")
      for ((i=0; i<${#args[@]}; i++)); do
        case "${args[$i]}" in
          -J) json_arg="${args[$((i+1))]:-}" ;;
        esac
      done
      # ZAP writes its JSON report into the mounted /zap/wrk; the stub writes the same
      # basename into the host OUT_DIR the test controls (DAST_STUB_OUT).
      [ -n "$json_arg" ] && [ -z "${ZAP_NO_JSON:-}" ] && cat > "${DAST_STUB_OUT:-/tmp}/$(basename "$json_arg")" <<'JSON'
{"site":[{"@name":"http://localhost:8000","alerts":[
 {"pluginid":"40012","alert":"Cross Site Scripting (Reflected)","riskcode":"3","instances":[{"uri":"http://localhost:8000/a"},{"uri":"http://localhost:8000/b"}]},
 {"pluginid":"10038","alert":"Content Security Policy (CSP) Header Not Set","riskcode":"2","instances":[{"uri":"http://localhost:8000/"}]},
 {"pluginid":"10036","alert":"Server Leaks Version Information","riskcode":"1","instances":[{"uri":"http://localhost:8000/p0"},{"uri":"http://localhost:8000/p1"},{"uri":"http://localhost:8000/p2"},{"uri":"http://localhost:8000/p3"},{"uri":"http://localhost:8000/p4"},{"uri":"http://localhost:8000/p5"},{"uri":"http://localhost:8000/p6"},{"uri":"http://localhost:8000/p7"},{"uri":"http://localhost:8000/p8"},{"uri":"http://localhost:8000/p9"},{"uri":"http://localhost:8000/p10"},{"uri":"http://localhost:8000/p11"}]},
 {"pluginid":"10049","alert":"Non-Storable Content","riskcode":"0","instances":[]}]}]}
JSON
      exit "${ZAP_EXIT:-0}"
    fi
    exit 0
    ;;
  rm) exit 0 ;;
  *)  exit 0 ;;
esac
EOF
  chmod +x "$bindir/docker"

  # curl stub: the health probe — succeeds only after HEALTH_OK_AFTER calls.
  cat > "$bindir/curl" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$CURL_ARGV"
n=$(cat "$CURL_CALLS" 2>/dev/null || echo 0)
n=$((n+1)); printf '%s' "$n" > "$CURL_CALLS"
[ "$n" -ge "${HEALTH_OK_AFTER:-0}" ] && exit 0 || exit 1
EOF
  chmod +x "$bindir/curl"

  export PATH="$bindir:$BASE_PATH"
  export DOCKER_BIN="$bindir/docker"
  export CURL_BIN="$bindir/curl"
  export DOCKER_ARGV="$BATS_TEST_TMPDIR/docker.argv"; : > "$DOCKER_ARGV"
  export CURL_ARGV="$BATS_TEST_TMPDIR/curl.argv"; : > "$CURL_ARGV"
  export CURL_CALLS="$BATS_TEST_TMPDIR/curl.calls"; : > "$CURL_CALLS"
}

# _repo — a fixture repo root (the scan takes PROJECT_ROOT; here it is only the
# ZAP target's provenance, so any dir stands in).
_repo() {
  local root="$BATS_TEST_TMPDIR/repo"
  mkdir -p "$root" "$BATS_TEST_TMPDIR/out"
  printf '%s\n' "app" > "$root/README.md"
  printf '%s' "$root"
}

@test "app up + clean scan: starts the app, health-checks, runs zap-baseline.py, writes SARIF+JSON, tears down, exits 0" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stubs 0 0                       # app healthy on call 0; ZAP clean (exit 0)
  root="$(_repo)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]
  # app started, health probed, ZAP invoked against the target.
  grep -qF -- '-d' "$DOCKER_ARGV"
  grep -qF 'http://localhost:8000/api/v1/healthz' "$CURL_ARGV"
  grep -qF 'zap-baseline.py' "$DOCKER_ARGV"
  grep -qF 'http://localhost:8000' "$DOCKER_ARGV"
  # both records written; the merged SARIF is a 2.1.0 SARIF doc.
  [ -s "$SEC_OUTPUT_DIR/dast/report.json" ]
  [ "$(yq -r '.version' "$SEC_OUTPUT_DIR/dast/dast.sarif")" = "2.1.0" ]
  [ "$(yq -r '.status' "$SEC_OUTPUT_DIR/dast/report.json")" = "GREEN" ]
  # teardown ran on the running container.
  grep -qF 'rm -f' "$DOCKER_ARGV"
  [[ "$output" == *"clean"* ]]
}

@test "a finding (ZAP exits non-zero) still writes the reports, tears down, and exits 0 — advisory, not a gate" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stubs 0 2                       # app healthy; ZAP reports findings (exit 2)
  root="$(_repo)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]              # advisory: a finding never blocks
  [ -s "$SEC_OUTPUT_DIR/dast/report.json" ]
  [ -s "$SEC_OUTPUT_DIR/dast/dast.sarif" ]
  [ "$(yq -r '.status' "$SEC_OUTPUT_DIR/dast/report.json")" = "RED" ]
  grep -qF 'rm -f' "$DOCKER_ARGV"  # teardown still ran
  [[ "$output" == *"advisory"* ]]
}

@test "app never-ready degrades to SKIPPED — exits 0, no hang, and the app is torn down" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stubs 999 0 0 3                 # health never succeeds within the bound
  root="$(_repo)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]
  [ "$(yq -r '.status' "$SEC_OUTPUT_DIR/dast/report.json")" = "SKIPPED" ]
  [[ "$output" == *"SKIPPED"* ]]
  grep -qF 'rm -f' "$DOCKER_ARGV"  # the started container is torn down even on skip
}

@test "docker unavailable degrades to SKIPPED — exits 0, no hard-die, nothing started" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stubs 0 0 1                     # DOCKER_INFO_EXIT=1 → daemon down
  root="$(_repo)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]
  [ "$(yq -r '.status' "$SEC_OUTPUT_DIR/dast/report.json")" = "SKIPPED" ]
  [[ "$output" == *"SKIPPED"* ]]
  [[ "$output" != *"ERROR @"* ]]   # did not hard-die via trap_err
  # a skip still writes the empty Merged SARIF, so the code scanning upload has a file in CI
  [ -s "$SEC_OUTPUT_DIR/dast/dast.sarif" ]
  [ "$(yq -r '.version' "$SEC_OUTPUT_DIR/dast/dast.sarif")" = "2.1.0" ]
  # nothing started, so no teardown was needed.
  grep -qF -- '-d' "$DOCKER_ARGV" && return 1 || true
}

@test "the ZAP image defaults to the digest pin in config.yaml" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stubs 0 0
  root="$(_repo)"
  pin="$(yq -r '.dast.zap_image' "$(dirname "$BATS_TEST_DIRNAME")/../config.yaml")"
  [[ "$pin" =~ @sha256:[0-9a-f]{64}$ ]]
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]
  grep -qF -- "$pin zap-baseline.py" "$DOCKER_ARGV"
}

@test "DAST_ZAP_IMAGE overrides the config pin" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stubs 0 0
  root="$(_repo)"
  DAST_ZAP_IMAGE="zap:override" run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]
  grep -qF -- 'zap:override zap-baseline.py' "$DOCKER_ARGV"
}

@test "the report dir is writable by ZAP's non-root user" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stubs 0 0
  root="$(_repo)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]
  [ "$(stat -c %a "$SEC_OUTPUT_DIR/dast")" = "777" ]
}

@test "the health probe uses the configured path, and DAST_HEALTH_PATH overrides it" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stubs 0 0
  root="$(_repo)"
  want="$(yq -r '.dast.health_path' "$(dirname "$BATS_TEST_DIRNAME")/../config.yaml")"
  [ "$want" = "/api/v1/healthz" ]
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  grep -qF "http://localhost:8000${want}" "$CURL_ARGV"
  : > "$CURL_ARGV"
  DAST_HEALTH_PATH=/ready run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  grep -qF 'http://localhost:8000/ready' "$CURL_ARGV"
}

@test "ZAP JSON becomes SARIF 2.1.0: one result per alert, risk mapped to level, URIs as locations; JSON kept" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stubs 0 2
  root="$(_repo)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]
  sarif="$SEC_OUTPUT_DIR/dast/dast.sarif"
  [ -s "$SEC_OUTPUT_DIR/dast/zap.json" ]
  [ "$(jq -r '.version' "$sarif")" = "2.1.0" ]
  [ "$(jq '.runs | length' "$sarif")" -eq 1 ]
  [ "$(jq '.runs[0].results | length' "$sarif")" -eq 4 ]
  [ "$(jq -r '.runs[0].results[0].ruleId' "$sarif")" = "40012-cross-site-scripting-reflected" ]
  [ "$(jq -r '[.runs[0].results[].level] | join(",")' "$sarif")" = "error,warning,note,note" ]
  [ "$(jq -r '.runs[0].results[0].locations[1].physicalLocation.artifactLocation.uri' "$sarif")" = "http://localhost:8000/b" ]
  [ "$(jq '.runs[0].tool.driver.rules | length' "$sarif")" -eq 4 ]
  # locations: capped at 10; an alert with no instances falls back to the target URL
  [ "$(jq '.runs[0].results[2].locations | length' "$sarif")" -eq 10 ]
  [ "$(jq -r '.runs[0].results[3].locations[0].physicalLocation.artifactLocation.uri' "$sarif")" = "http://localhost:8000" ]
  [ "$(jq -r '.alerts' "$SEC_OUTPUT_DIR/dast/report.json")" = "4" ]
}

@test "a relative SEC_OUTPUT_DIR from another cwd still gives docker an absolute -v source" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/work/rel/sec"   # where the ZAP stub writes (absolute)
  _stubs 0 0 0 5
  root="$(_repo)"
  cd "$BATS_TEST_TMPDIR/work"
  run bash "$SCRIPT" "rel/sec" "$root"
  [ "$status" -eq 0 ]
  grep -qE -- '-v /' "$DOCKER_ARGV"
  run grep -E -- '-v [^/]' "$DOCKER_ARGV"
  [ "$status" -ne 0 ]
}

@test "findings (ZAP exit 1 or 2) stay advisory: RED, exit 0" {
  for rc in 1 2; do
    SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out$rc"; mkdir -p "$SEC_OUTPUT_DIR"
    _stubs 0 "$rc"
    root="$(_repo)"
    run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
    [ "$status" -eq 0 ]
    [ "$(jq -r '.status' "$SEC_OUTPUT_DIR/dast/report.json")" = "RED" ]
  done
}

@test "a tool error (ZAP exit 3, docker exit 125) is ERROR with a non-zero exit" {
  for rc in 3 125; do
    SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out$rc"; mkdir -p "$SEC_OUTPUT_DIR"
    _stubs 0 "$rc"
    root="$(_repo)"
    run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
    [ "$status" -ne 0 ]
    [ "$(jq -r '.status' "$SEC_OUTPUT_DIR/dast/report.json")" = "ERROR" ]
    grep -qF 'rm -f' "$DOCKER_ARGV"      # teardown still ran
  done
}

@test "a missing zap.json after a clean ZAP exit is ERROR with a non-zero exit" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stubs 0 0
  export ZAP_NO_JSON=1
  root="$(_repo)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -ne 0 ]
  [ "$(jq -r '.status' "$SEC_OUTPUT_DIR/dast/report.json")" = "ERROR" ]
}

@test "a dry run exits 0, never reports ERROR, and runs no docker command" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stubs 0 0
  root="$(_repo)"
  DRY_RUN=true run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]
  [ "$(jq -r '.status' "$SEC_OUTPUT_DIR/dast/report.json")" = "GREEN" ]
  [ ! -s "$DOCKER_ARGV" ]
}

@test "DAST_TARGET_RUNNING=true scans the running target: no app container, no image needed, health-checked" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stubs 0 2
  root="$(_repo)"
  DAST_TARGET_RUNNING=true DAST_TARGET_IMAGE="" DAST_TARGET_URL="http://127.0.0.1:41234" \
    run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]
  grep -qF 'http://127.0.0.1:41234/api/v1/healthz' "$CURL_ARGV"
  grep -qF -- '-t http://127.0.0.1:41234' "$DOCKER_ARGV"
  grep -qE -- '^run -d|^rm -f' "$DOCKER_ARGV" && return 1 || true
  [ "$(yq -r '.status' "$SEC_OUTPUT_DIR/dast/report.json")" = "RED" ]
}

@test "DAST_ZAP_CONTAINER names the ZAP container and removes it by that name, also on a tool error" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stubs 0 3
  root="$(_repo)"
  DAST_ZAP_CONTAINER=fst-zap-bats DAST_TARGET_RUNNING=true run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 1 ]
  grep -qF -- 'run --rm --name fst-zap-bats --network host' "$DOCKER_ARGV"
  [ "$(tail -n 1 "$DOCKER_ARGV")" = "rm -f fst-zap-bats" ]
}

@test "without DAST_ZAP_CONTAINER the ZAP container stays unnamed (the dast workflow's run)" {
  SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  _stubs 0 0
  root="$(_repo)"
  run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"
  [ "$status" -eq 0 ]
  grep -qF -- 'run --rm --network host' "$DOCKER_ARGV"
}
