#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# smoke-test.bats — scripts/smoke-test.sh against a stub curl: --host sets the Host header.
# -----------------------------------------------------------------------------

setup() {
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/../scripts/smoke-test.sh"
  B="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$B"
  export LOG="$BATS_TEST_TMPDIR/curl.log"
  : > "$LOG"
  cat > "$B/curl" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$LOG"
printf '{}\n200'
STUB
  chmod +x "$B/curl"
  export PATH="$B:$PATH"
}

@test "--host sends that Host header on every request" {
  run bash "$SCRIPT" --base-url http://127.0.0.1:9 --host svc-backend
  calls="$(grep -c -- '%{http_code}' "$LOG")"
  [ "$calls" -ge 6 ]
  [ "$(grep -c -- '-H Host: svc-backend' "$LOG")" -eq "$calls" ]
}

@test "without --host no Host header is set" {
  run bash "$SCRIPT" --base-url http://127.0.0.1:9
  run grep -c -- 'Host:' "$LOG"
  [ "$output" = "0" ]
}

@test "the usage text documents --host" {
  run bash "$SCRIPT" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"--host NAME"* ]]
}

@test "--header-file sends the file's headers on every request, never their values on a command line" {
  printf 'CF-Access-Client-Id: an-id\nCF-Access-Client-Secret: a-secret\n' > "$BATS_TEST_TMPDIR/h"
  run bash "$SCRIPT" --base-url https://pr.example.org --header-file "$BATS_TEST_TMPDIR/h"
  calls="$(grep -c -- '%{http_code}' "$LOG")"
  [ "$calls" -ge 6 ]
  [ "$(grep -c -- "-H @$BATS_TEST_TMPDIR/h" "$LOG")" -eq "$calls" ]
  run grep -c -e a-secret -e an-id "$LOG"
  [ "$output" = "0" ]
  [[ "$(bash "$SCRIPT" --base-url https://pr.example.org --header-file "$BATS_TEST_TMPDIR/h" --dry-run 2>&1)" != *a-secret* ]]
}

@test "--header-file refuses a missing file before any request" {
  run bash "$SCRIPT" --base-url https://pr.example.org --header-file "$BATS_TEST_TMPDIR/none"
  [ "$status" -ne 0 ]
  [[ "$output" == *"not a readable file"* ]]
  [ ! -s "$LOG" ]
}

@test "the usage text documents --header-file" {
  run bash "$SCRIPT" --help
  [[ "$output" == *"--header-file FILE"* ]]
}
