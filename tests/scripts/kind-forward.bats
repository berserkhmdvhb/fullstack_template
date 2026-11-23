#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# kind-forward.bats — scripts/ci/kind-forward.sh against stubs: it forwards to the release's Services
# as the deployer on ports kubectl picks at run time, waits until each answers (bounded), exports the
# URLs and the deployment id to the command, and stops its forwards on success, on failure and on a
# signal. The real forwards are proven by the kind job (a real cluster).
# -----------------------------------------------------------------------------

bats_require_minimum_version 1.5.0

setup() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  SCRIPT="$ROOT/scripts/ci/kind-forward.sh"
  B="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$B"
  export LOG="$BATS_TEST_TMPDIR/calls.log" PIDS="$BATS_TEST_TMPDIR/forward.pids"
  : > "$LOG"
  : > "$PIDS"
  mkdir -p "$ROOT/.local"
  KIND_DIR="$(mktemp -d "$ROOT/.local/bats-kind.XXXXXX")"
  export FST_KIND_DIR="$KIND_DIR"
  : > "$KIND_DIR/kubeconfig"
  unset RUNNER_TEMP
  # kubectl: the context and version questions, the Services' ports, the ConfigMap, the frontend's
  # image and node; a port-forward prints kubectl's line with a port of its own (40000 + n) and
  # stays up, its pid recorded. STUB_PF_SILENT: the forward never prints its line.
  cat > "$B/kubectl" <<'STUB'
#!/usr/bin/env bash
echo "kubectl $*" >> "$LOG"
case "$*" in
  "config get-contexts -o name") printf '%s\n' "${STUB_CONTEXTS:-kind-fst}"; exit ;;
  "version -o json") echo '{"serverVersion":{"major":"1","minor":"37"}}'; exit ;;
esac
case "$*" in
  *" port-forward "*)
    echo "$$" >> "$PIDS"
    [[ -n "${STUB_PF_SILENT:-}" ]] || echo "Forwarding from 127.0.0.1:$((40000 + $(wc -l < "$PIDS"))) -> 8000"
    exec sleep 300 ;;
  *"get service/"*) echo 8000 ;;
  *"get configmap/"*) echo v1 ;;
  *"get deployment/"*) echo kind-fst/frontend:kind-1 ;;
  *"get pods "*) echo fst-control-plane ;;
  *) exit 99 ;;
esac
STUB
  # curl: the health probe answers unless STUB_CURL_FAIL is set.
  # shellcheck disable=SC2016 # expanded by the stub, not here
  printf '#!/usr/bin/env bash\necho "curl $*" >> "$LOG"\n[[ -z "${STUB_CURL_FAIL:-}" ]]\n' > "$B/curl"
  # docker exec … crictl inspecti: the image's labels.
  cat > "$B/docker" <<'STUB'
#!/usr/bin/env bash
echo "docker $*" >> "$LOG"
echo '{"info":{"imageSpec":{"config":{"Labels":{"org.opencontainers.image.version":"1.2.3","org.opencontainers.image.revision":"0123456789abcdef"}}}}}'
STUB
  # node scripts/deployment-id.mjs VERSION REVISION
  # shellcheck disable=SC2016 # expanded by the stub, not here
  printf '#!/usr/bin/env bash\necho "node $*" >> "$LOG"\nprintf "dpl-%%s-%%s" "$2" "$3"\n' > "$B/node"
  chmod +x "$B"/*
  export PATH="$B:$PATH"
  # The command run under the forwards: prints what it was given.
  SHOW="$BATS_TEST_TMPDIR/show.sh"
  # shellcheck disable=SC2016 # expanded by the stub, not here
  printf '#!/usr/bin/env bash\nenv | grep -E "^(API_VERSION|BACKEND_|PUBLIC_|FRONTEND_|SMOKE_|DAST_|NEXT_|LIVE_)" | sort\nexit "${1:-0}"\n' > "$SHOW"
  chmod +x "$SHOW"
}

teardown() {
  while read -r pid; do kill "$pid" 2>/dev/null || true; done < "$PIDS"
  rm -rf "$KIND_DIR"
  rmdir "$ROOT/.local" 2>/dev/null || true
}

all_stopped() {
  [ "$(wc -l < "$PIDS")" -gt 0 ]
  while read -r pid; do
    if kill -0 "$pid" 2>/dev/null; then echo "forward $pid still runs"; return 1; fi
  done < "$PIDS"
}

@test "it forwards both Services on run-time ports as the deployer and exports the URLs" {
  run bash "$SCRIPT" -- "$SHOW"
  [ "$status" -eq 0 ]
  [[ "$output" == *"BACKEND_PORT=40001"* ]]
  [[ "$output" == *"PUBLIC_PORT=40002"* && "$output" == *"FRONTEND_PORT=40002"* ]]
  [[ "$output" == *"PUBLIC_URL=http://127.0.0.1:40002"* ]]
  [[ "$output" == *"SMOKE_BASE_URL=http://127.0.0.1:40002"* && "$output" == *"DAST_TARGET_URL=http://127.0.0.1:40002"* ]]
  [[ "$output" == *"API_VERSION=v1"* && "$output" == *"LIVE_TARGET=deployed"* ]]
  [[ "$output" == *"NEXT_DEPLOYMENT_ID=dpl-1.2.3-0123456789abcdef"* ]]
  grep -q -- "port-forward --address 127.0.0.1 service/your-service-name-backend :8000" "$LOG"
  grep -q -- "port-forward --address 127.0.0.1 service/your-service-name-frontend :8000" "$LOG"
  while read -r line; do
    [[ "$line" == *"--as your-deployer-local --as-group system:authenticated"* ]]
  done < <(grep "^kubectl --context" "$LOG")
  grep -q "curl .*http://127.0.0.1:40001/api/v1/health" "$LOG"
  grep -q "curl .*http://127.0.0.1:40002/api/healthz" "$LOG"
  grep -q "docker exec fst-control-plane crictl inspecti -o json kind-fst/frontend:kind-1" "$LOG"
  all_stopped
}

@test "a failing command keeps its exit code, the forwards stop and their logs are printed" {
  run bash "$SCRIPT" "$SHOW" 3
  [ "$status" -eq 3 ]
  [[ "$output" == *"Forwarding from 127.0.0.1:40001"* ]]
  all_stopped
}

@test "a forward that never answers fails within the timeout and stops every forward" {
  STUB_CURL_FAIL=1 KIND_FORWARD_TIMEOUT=2 run bash "$SCRIPT" "$SHOW"
  [ "$status" -eq 1 ]
  [[ "$output" == *"backend did not answer"* ]]
  [[ "$output" != *"BACKEND_PORT="* ]]
  all_stopped
}

@test "a forward that never starts fails within the timeout" {
  STUB_PF_SILENT=1 KIND_FORWARD_TIMEOUT=1 run bash "$SCRIPT" "$SHOW"
  [ "$status" -eq 1 ]
  [[ "$output" == *"did not start in 1 s"* ]]
  all_stopped
}

@test "a TERM while the command runs stops the command and the forwards" {
  printf '#!/usr/bin/env bash\necho "$$" > "%s"\nexec sleep 300\n' "$BATS_TEST_TMPDIR/cmd.pid" > "$SHOW"
  bash "$SCRIPT" "$SHOW" > "$BATS_TEST_TMPDIR/out" 2>&1 &
  helper=$!
  for _ in $(seq 100); do grep -q "deployment id" "$BATS_TEST_TMPDIR/out" && break; sleep 0.1; done
  grep -q "deployment id" "$BATS_TEST_TMPDIR/out"
  kill -TERM "$helper"
  rc=0
  wait "$helper" || rc=$?
  [ "$rc" -eq 143 ]
  all_stopped
  run ! kill -0 "$(cat "$BATS_TEST_TMPDIR/cmd.pid")"
}

@test "another context, a missing kubeconfig or no command is refused before any forward" {
  STUB_CONTEXTS=$'kind-fst\nkind-other' run bash "$SCRIPT" "$SHOW"
  [ "$status" -eq 1 ]
  [[ "$output" == *"refusing"* ]]
  run bash "$SCRIPT"
  [ "$status" -eq 2 ]
  rm "$KIND_DIR/kubeconfig"
  run bash "$SCRIPT" "$SHOW"
  [ "$status" -eq 1 ]
  [[ "$output" == *"make kind-up"* ]]
  run ! grep -q "port-forward" "$LOG"
}
