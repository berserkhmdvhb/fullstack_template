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
