#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# kind-deploy.bats — scripts/kind-deploy.sh's guards, against stubs that record every call: it targets
# the cluster fst through its own kubeconfig only, refuses any other context, picks the pinned kubectl
# within one minor of the server, and refuses bad arguments before anything runs. The full sequence is
# proven by the kind job itself (a real cluster).
# -----------------------------------------------------------------------------

bats_require_minimum_version 1.5.0

setup() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  SCRIPT="$ROOT/scripts/kind-deploy.sh"
  B="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$B"
  export LOG="$BATS_TEST_TMPDIR/calls.log"
  : > "$LOG"
  mkdir -p "$ROOT/.local"
  KIND_DIR="$(mktemp -d "$ROOT/.local/bats-kind.XXXXXX")"
  export FST_KIND_DIR="$KIND_DIR"
  : > "$KIND_DIR/kubeconfig"
  # kubectl answers the context questions from STUB_CONTEXTS (one per line, the first is current) and
  # the version from STUB_SERVER_MINOR; it logs the kubeconfig it was given. Anything else fails, so
  # the run stops right after the guards.
  cat > "$B/kubectl" <<'STUB'
#!/usr/bin/env bash
echo "$(basename "$0") KUBECONFIG=${KUBECONFIG:-} $*" >> "$LOG"
case "$*" in
  "config current-context") sed -n 1p <<<"${STUB_CONTEXTS:-kind-fst}" ;;
  "config get-contexts -o name") printf '%s\n' "${STUB_CONTEXTS:-kind-fst}" ;;
  "version -o json") printf '{"clientVersion":{"major":"1","minor":"%s"},"serverVersion":{"major":"1","minor":"%s"}}\n' \
      "${STUB_CLIENT_MINOR:-34}" "${STUB_SERVER_MINOR:-34}" ;;
  *) exit 99 ;;
esac
STUB
  cp "$B/kubectl" "$B/kubectl-floor"
  for tool in helm kind docker ct; do
    # shellcheck disable=SC2016 # expanded by the stub, not here
    printf '#!/usr/bin/env bash\necho "%s $*" >> "$LOG"\nexit 99\n' "$tool" > "$B/$tool"
  done
  chmod +x "$B"/*
  export PATH="$B:$PATH"
}

teardown() {
  rm -rf "$KIND_DIR"
  rmdir "$ROOT/.local" 2>/dev/null || true
}

@test "an exported KIND_DIR is ignored, and an override outside .local/ is refused" {
  other="$BATS_TEST_TMPDIR/someone-elses-kind"
  mkdir -p "$other" && : > "$other/kubeconfig"
  KIND_DIR="$other" run bash "$SCRIPT"
  run ! grep -q "$other" "$LOG"
  : > "$LOG"
  FST_KIND_DIR="$other" run bash "$SCRIPT"
  [ "$status" -eq 2 ]
  [[ "$output" == *".local/"* ]]
  [ -z "$(cat "$LOG")" ]
}

@test "without the kubeconfig of fst it stops and says to run make kind-up" {
  rm "$KIND_DIR/kubeconfig"
  run bash "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"make kind-up"* ]]
  [ -z "$(cat "$LOG")" ]
}

@test "another current context is refused before anything else runs" {
  STUB_CONTEXTS="kind-lab" run bash "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"refusing"*"kind-lab"* ]]
  run ! grep -Eq "^(helm|kind|docker|ct) " "$LOG"
}

@test "a kubeconfig that holds any other context is refused too" {
  STUB_CONTEXTS=$'kind-fst\nkind-reconcile' run bash "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"refusing"* ]]
  run ! grep -Eq "^(helm|kind|docker|ct) " "$LOG"
}

@test "an inherited KUBECONFIG is ignored: every kubectl call gets fst's own" {
  KUBECONFIG="$BATS_TEST_TMPDIR/elsewhere" run bash "$SCRIPT"
  grep -q "^kubectl" "$LOG"
  while read -r line; do
    [[ "$line" == *"KUBECONFIG=$KIND_DIR/kubeconfig "* ]]
  done < <(grep "^kubectl" "$LOG")
}

@test "the floor leg's server gets the floor kubectl; the newest leg's the default one" {
  run bash "$SCRIPT"
  [[ "$output" == *"kubectl-floor"* ]]
  : > "$LOG"
  STUB_SERVER_MINOR=37 STUB_CLIENT_MINOR=37 run bash "$SCRIPT"
  [[ "$output" == *"kubectl (1.37 client"* ]]
}

@test "a server no pinned leg covers is refused, rather than run with a skewed kubectl" {
  STUB_SERVER_MINOR=30 run bash "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"no pinned kubectl"* ]]
  run ! grep -Eq "^(helm|kind|docker|ct) " "$LOG"
}

@test "a client more than one minor from the server is refused" {
  STUB_CLIENT_MINOR=37 run bash "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"skew"* ]]
}

@test "bad arguments are refused before anything runs" {
  run bash "$SCRIPT" --base
  [ "$status" -eq 2 ]
  run bash "$SCRIPT" --name lab
  [ "$status" -eq 2 ]
  run bash "$SCRIPT" --base 'x;y'
  [ "$status" -eq 2 ]
  [ -z "$(cat "$LOG")" ]
}

# deploy() on its own (the full sequence needs a cluster): extracted from the script, with its
# helpers stubbed. helm fails as a failed migration makes it fail; the newest migration Job's report
# (scripts/ci/migration-job-lib.sh, as deploy-env.sh prints it) must be printed, then the run dies.
deploy_fn() {
  sed -n '/^deploy() {/,/^}/p' "$SCRIPT"
}

@test "a failed deploy prints the newest migration Job's status and logs, then fails" {
  [[ "$(deploy_fn)" == *migration_job_report_newest* ]]
  run bash -c '
    set -Eeuo pipefail
    WORK="$1" NS=ns RELEASE=rel values=v
    die() { echo "die: $*" >&2; exit 1; }
    kind_args() { :; }
    helm_as() { echo "Error: UPGRADE FAILED: resource Job/ns/rel-migrate-r2 not ready. status: Failed"; return 1; }
    migration_job_report_newest() { echo "report $1 $2"; echo "RuntimeError: MIGRATION_TEST_FAIL"; }
    eval "$2"
    deploy chart
    echo "not reached"' _ "$BATS_TEST_TMPDIR" "$(deploy_fn)"
  [ "$status" -eq 1 ]
  [[ "$output" == *"report ns rel"* ]]
  [[ "$output" == *"RuntimeError: MIGRATION_TEST_FAIL"* ]]
  [[ "$output" == *"die: helm upgrade --install failed (1)"* ]]
  [[ "$output" != *"not reached"* ]]
}

@test "a deploy that succeeds prints no migration report" {
  run bash -c '
    set -Eeuo pipefail
    WORK="$1" NS=ns RELEASE=rel values=v
    die() { echo "die: $*" >&2; exit 1; }
    kind_args() { :; }
    helm_as() { echo "STATUS: deployed"; }
    migration_job_report_newest() { echo "report"; }
    eval "$2"
    deploy chart
    echo "done"' _ "$BATS_TEST_TMPDIR" "$(deploy_fn)"
  [ "$status" -eq 0 ]
  [[ "$output" == *"done"* ]]
  [[ "$output" != *"report"* ]]
}
