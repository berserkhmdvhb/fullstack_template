#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# k8s-env.bats — `scripts/k8s-env.sh apply` against stub kubectl and helm. The chart creates no
# namespace (namespaces.create=false), so apply first applies the admin bootstrap, then installs.
# No cluster is contacted.
# -----------------------------------------------------------------------------

bats_require_minimum_version 1.5.0

setup() {
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/../scripts/k8s-env.sh"
  B="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$B"
  export LOG="$BATS_TEST_TMPDIR/calls.log"
  : > "$LOG"
  cat > "$B/helm" <<'STUB'
#!/usr/bin/env bash
echo "helm $*" >> "$LOG"
[[ "$1" != template ]] || echo "kind: Namespace # rendered"
if [[ "$1 $2" == "get manifest" ]]; then
  [[ -z "${STUB_GET_FAIL-}" ]] || { echo "Error: Kubernetes cluster unreachable" >&2; exit 1; }
  [[ -n "${STUB_MANIFEST-}" ]] || { echo "Error: release: not found" >&2; exit 1; }
  printf '%s\n' "$STUB_MANIFEST"
  # A large release: far more than one pipe buffer (64 KiB) after the Namespace.
  [[ -z "${STUB_BIG-}" ]] || yes "# a long stored manifest line, written after the Namespace" | head -n 20000
fi
STUB
  cat > "$B/kubectl" <<'STUB'
#!/usr/bin/env bash
echo "kubectl $*" >> "$LOG"
[[ "$*" != "apply -f -" ]] || sed 's/^/stdin: /' >> "$LOG"
STUB
  chmod +x "$B"/*
  export PATH="$B:$PATH" LIB_TS=0 NO_COLOR=1
}

@test "apply pipes the bootstrap into kubectl before the chart, and the chart creates no namespace" {
  run bash "$SCRIPT" apply --env qa --release svc --no-wait
  [ "$status" -eq 0 ]
  boot="$(grep -n "^helm template bootstrap" "$LOG" | cut -d: -f1)"
  applied="$(grep -n "^kubectl apply -f -$" "$LOG" | cut -d: -f1)"
  upgrade="$(grep -n "^helm upgrade --install svc" "$LOG" | cut -d: -f1)"
  [ -n "$boot" ]
  [ -n "$applied" ]
  [ -n "$upgrade" ]
  # The render and the apply are one pipeline, so only the install has to come after both.
  (( boot < upgrade && applied < upgrade ))
  grep -qx "stdin: kind: Namespace # rendered" "$LOG"
  grep "^helm template bootstrap" "$LOG" | grep -q "values.qa.yaml --set-string release=svc --set-string namespace=svc-qa"
  run ! grep -q -- "--create-namespace" "$LOG"
}

@test "an explicit namespace reaches the bootstrap and the chart alike" {
  run bash "$SCRIPT" apply --env dev --namespace team-a --no-wait
  [ "$status" -eq 0 ]
  grep "^helm template bootstrap" "$LOG" | grep -q -- "--set-string namespace=team-a"
  grep "^helm upgrade" "$LOG" | grep -q -- "-n team-a"
}

@test "a failed bootstrap apply stops before the chart is installed" {
  # shellcheck disable=SC2016 # expanded by the stub, not here
  printf '#!/usr/bin/env bash\necho "kubectl $*" >> "$LOG"\n[[ "$1" != apply ]] || exit 1\n' > "$B/kubectl"
  run bash "$SCRIPT" apply --env dev --no-wait
  [ "$status" -ne 0 ]
  run ! grep -q "^helm upgrade" "$LOG"
}

@test "dry-run prints the bootstrap and the install, and runs neither" {
  run bash "$SCRIPT" apply --env dev --dry-run --no-wait
  [ "$status" -eq 0 ]
  [[ "$output" == *"k8s-bootstrap.sh dev --release your-service-name --namespace your-service-name-dev | kubectl apply -f -"* ]]
  [[ "$output" == *"helm upgrade --install"* ]]
  run ! grep -q "apply\|upgrade" "$LOG"
}

@test "a release that still owns its Namespaces is first upgraded once to keep them" {
  STUB_MANIFEST=$'---\nkind: Namespace\nmetadata:\n  name: svc-qa\n---\nkind: Role' run bash "$SCRIPT" apply --env qa --release svc --no-wait
  [ "$status" -eq 0 ]
  [[ "$output" == *"still owns its Namespace"* ]]
  once="$(grep -n "^helm upgrade .*--set namespaces.create=true --set rbac.create=true" "$LOG" | cut -d: -f1)"
  applied="$(grep -n "^kubectl apply -f -$" "$LOG" | cut -d: -f1)"
  last="$(grep -n "^helm upgrade" "$LOG" | tail -n 1 | cut -d: -f1)"
  [ -n "$once" ]
  [ "$(grep -c "^helm upgrade" "$LOG")" -eq 2 ]
  (( once < applied && applied < last ))
  run grep -q "namespaces.create" <<<"$(sed -n "${last}p" "$LOG")"
  [ "$status" -ne 0 ]
}

@test "a new or already switched release gets only the normal upgrade" {
  for manifest in "" $'---\nkind: Deployment'; do
    : > "$LOG"
    STUB_MANIFEST="$manifest" run bash "$SCRIPT" apply --env dev --release svc --no-wait
    [ "$status" -eq 0 ]
    [ "$(grep -c "^helm upgrade" "$LOG")" -eq 1 ]
  done
}

@test "every upgrade keeps 5 revisions of Helm history, as the deploy does" {
  STUB_MANIFEST="kind: Namespace" run bash "$SCRIPT" apply --env dev --release svc --no-wait
  [ "$status" -eq 0 ]
  [ "$(grep "^helm upgrade" "$LOG" | grep -c -- "--history-max 5")" -eq 2 ]
}

@test "a stored manifest larger than a pipe buffer still triggers the keep-upgrade" {
  STUB_BIG=1 STUB_MANIFEST=$'---\nkind: Namespace' run bash "$SCRIPT" apply --env dev --release svc --no-wait
  [ "$status" -eq 0 ]
  grep -q "^helm upgrade .*--set namespaces.create=true --set rbac.create=true" "$LOG"
}

@test "a failed release lookup is an error, not a release without namespaces" {
  STUB_GET_FAIL=1 run bash "$SCRIPT" apply --env dev --release svc --no-wait
  [ "$status" -ne 0 ]
  [[ "$output" == *"cluster unreachable"* ]]
  run grep -q "^helm upgrade" "$LOG"
  [ "$status" -ne 0 ]
}

@test "the keep-upgrade is printed as the exact command it runs" {
  STUB_MANIFEST="kind: Namespace" run bash "$SCRIPT" apply --env dev --release svc --no-wait
  [ "$status" -eq 0 ]
  cmd="$(grep "^helm upgrade .*rbac.create=true" "$LOG" | sed 's/^helm //')"
  [[ "$output" == *"helm $cmd"* ]]
}

@test "a dry run shows the keep-upgrade it would run, and runs nothing" {
  STUB_MANIFEST="kind: Namespace" run bash "$SCRIPT" apply --env dev --release svc --dry-run --no-wait
  [ "$status" -eq 0 ]
  [[ "$output" == *"would run"* ]]
  [[ "$output" == *"⇢ helm upgrade --install svc"*"--set namespaces.create=true --set rbac.create=true"* ]]
  run grep -q "^helm upgrade" "$LOG"
  [ "$status" -ne 0 ]
}

@test "a release that still owns only the app Role or RoleBinding gets the keep-upgrade too" {
  for kind in Role RoleBinding; do
    : > "$LOG"
    STUB_MANIFEST=$'---\nkind: Deployment\n---\nkind: '"$kind" run bash "$SCRIPT" apply --env dev --release svc --no-wait
    [ "$status" -eq 0 ]
    [ "$(grep -c "^helm upgrade" "$LOG")" -eq 2 ]
    [[ "$output" == *"still owns"* ]]
  done
}

@test "a dry run needs no cluster: it skips the release lookup and prints the commands" {
  STUB_GET_FAIL=1 run bash "$SCRIPT" apply --env dev --release svc --dry-run --no-wait
  [ "$status" -eq 0 ]
  [[ "$output" == *"would run if the release holds Namespace objects"* ]]
  [[ "$output" == *"⇢ helm upgrade --install svc"*"--set namespaces.create=true --set rbac.create=true"* ]]
  [[ "$output" == *"k8s-bootstrap.sh dev --release svc"*"| kubectl apply -f -"* ]]
  run grep -q "get manifest" "$LOG"
  [ "$status" -ne 0 ]
}
