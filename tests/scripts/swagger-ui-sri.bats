#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# swagger-ui-sri.bats — scripts/swagger-ui-sri.sh against a stub curl (no network).
# -----------------------------------------------------------------------------
# shellcheck disable=SC2016  # the stub bodies are written verbatim into the stub script

setup() {
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/../scripts/swagger-ui-sri.sh"
  B="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$B"
  export PATH="$B:$PATH"
}

# The stub writes its body to the -o file, as curl does.
stub_curl() {
  cat > "$B/curl" <<STUB
#!/usr/bin/env bash
out=""
while [ \$# -gt 0 ]; do [ "\$1" = "-o" ] && out="\$2"; shift; done
$1
STUB
  chmod +x "$B/curl"
}

@test "prints the version and both hashes for a good download" {
  stub_curl 'printf "body" > "$out"'
  run bash "$SCRIPT" 5.1.2
  [ "$status" -eq 0 ]
  [[ "$output" == *'SWAGGER_UI_VERSION = "5.1.2"'* ]]
  [[ "$output" == *'SWAGGER_UI_CSS_SRI = "sha384-'* ]]
  [[ "$output" == *'SWAGGER_UI_JS_SRI  = "sha384-'* ]]
}

@test "a failed download exits non-zero and prints no hash" {
  stub_curl 'echo "curl: (22) 404" >&2; exit 22'
  run bash "$SCRIPT" 5.9.9
  [ "$status" -ne 0 ]
  [[ "$output" != *"sha384-"* ]]
}

@test "a partial body with a curl failure exits non-zero and prints no hash" {
  stub_curl 'printf "half a bun" > "$out"; echo "curl: (18) transfer closed" >&2; exit 18'
  run bash "$SCRIPT" 5.9.9
  [ "$status" -ne 0 ]
  [[ "$output" != *"sha384-"* ]]
  [[ "$output" != *"SWAGGER_UI_VERSION"* ]]
}

@test "an empty download exits non-zero" {
  stub_curl ': > "$out"'
  run bash "$SCRIPT" 5.9.9
  [ "$status" -ne 0 ]
  [[ "$output" != *"sha384-"* ]]
}

@test "a version that is not an exact 5.x.y is refused before any download" {
  stub_curl 'echo called > "$BATS_TEST_TMPDIR/called"'
  run bash "$SCRIPT" 5
  [ "$status" -eq 2 ]
  [ ! -e "$BATS_TEST_TMPDIR/called" ]
}
