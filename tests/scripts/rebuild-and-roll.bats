#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# rebuild-and-roll.bats — the in-container commands of scripts/rebuild-and-roll.sh,
# against stub docker and kubectl that record their arguments (one per line).
# No image is built and no cluster is contacted.
# -----------------------------------------------------------------------------

setup() {
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/../scripts/rebuild-and-roll.sh"
  mkdir -p "$BATS_TEST_TMPDIR/bin"
  for tool in docker kubectl; do
    cat > "$BATS_TEST_TMPDIR/bin/$tool" <<'STUB'
#!/usr/bin/env bash
name="$(basename "$0")"
if [[ "$name $1 $2" == "kubectl get pod" ]]; then echo pod-x; exit 0; fi
{ echo "== $name"; printf '%s\n' "$@"; } >> "$STUB_LOG"
STUB
    chmod +x "$BATS_TEST_TMPDIR/bin/$tool"
  done
  export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
  export STUB_LOG="$BATS_TEST_TMPDIR/args.log"
}

@test "the backend health probe is the venv Python, passed to kubectl exec as separate words" {
  run bash "$SCRIPT" --no-docker-env --no-build --no-verify --no-roll --only backend
  [ "$status" -eq 0 ]
  exec_args="$(sed -n '/^== kubectl/,$p' "$STUB_LOG" | sed -n '/^--$/,$p' | tail -n +2)"
  [ "$(sed -n 1p <<<"$exec_args")" = "/app/.venv/bin/python" ]
  [ "$(sed -n 2p <<<"$exec_args")" = "-c" ]
  [ "$(sed -n 3p <<<"$exec_args")" = "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=5)" ]
  [ "$(wc -l <<<"$exec_args")" -eq 3 ]
}

@test "the backend image is verified with the venv Python (the image has no shell)" {
  run bash "$SCRIPT" --no-docker-env --no-build --no-roll --no-health --only backend
  [ "$status" -eq 0 ]
  grep -qx -- "--entrypoint" "$STUB_LOG"
  grep -qx "/app/.venv/bin/python" "$STUB_LOG"
  grep -qx -- "-c" "$STUB_LOG"
  run grep -qx "sh" "$STUB_LOG"
  [ "$status" -ne 0 ]
}

@test "the dry run shows the Python verify command for the backend" {
  run bash "$SCRIPT" --dry-run --no-docker-env --only backend
  [ "$status" -eq 0 ]
  [[ "$output" == *"--entrypoint /app/.venv/bin/python your-service-name-backend:dev -c import "* ]]
}

@test "the frontend image is still verified with its own interpreter, passed with -e" {
  run bash "$SCRIPT" --dry-run --no-docker-env --only frontend
  [[ "$output" == *"--entrypoint /nodejs/bin/node your-service-name-frontend:dev -e "* ]]
}
