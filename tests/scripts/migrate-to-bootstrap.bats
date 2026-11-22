#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# migrate-to-bootstrap.bats — scripts/migrate-to-bootstrap.sh against stub helm and kubectl: its
# arguments, its preconditions and refusals, the order of the steps, and that a re-run is a no-op.
# The stub release holds Namespace objects until the switch upgrade runs. No cluster is contacted.
# -----------------------------------------------------------------------------

bats_require_minimum_version 1.5.0

setup() {
  SCRIPT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)/scripts/migrate-to-bootstrap.sh"
  B="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$B"
  export LOG="$BATS_TEST_TMPDIR/calls.log" STATE="$BATS_TEST_TMPDIR/state"
  : > "$LOG"
  echo old > "$STATE"
  cat > "$B/helm" <<'STUB'
#!/usr/bin/env bash
echo "helm $*" >> "$LOG"
case "$1 $2" in
  "get manifest")
    [[ -z "${STUB_NO_RELEASE-}" ]] || { echo "Error: release: not found" >&2; exit 1; }
    if [[ "$(cat "$STATE")" == old || -n "${STUB_STAYS_OLD-}" ]]; then printf 'kind: Namespace\nkind: Role\n'; fi
    echo "kind: Deployment" ;;
  "upgrade "*)
    [[ "$*" != *"namespaces.create=false"* ]] || echo switched > "$STATE" ;;
esac
[[ "$1" != template ]] || echo "kind: Rendered"
STUB
  cat > "$B/kubectl" <<'STUB'
#!/usr/bin/env bash
echo "kubectl $*" >> "$LOG"
case "$1 $2" in
  "auth can-i") echo "${STUB_CAN:-yes}"; [[ "${STUB_CAN:-yes}" == yes ]] ;;
  "apply -f") cat > /dev/null ;;
esac
STUB
  chmod +x "$B"/*
  export PATH="$B:$PATH"
}

@test "no environment, an unknown one or an unknown option is refused before anything runs" {
  run bash "$SCRIPT"
  [ "$status" -eq 2 ]
  run bash "$SCRIPT" staging
  [ "$status" -eq 2 ]
  run bash "$SCRIPT" dev --reuse-values
  [ "$status" -eq 2 ]
  run bash "$SCRIPT" dev --release 'a,b=c' --yes
  [ "$status" -eq 2 ]
  [ -z "$(cat "$LOG")" ]
}

@test "the migration runs the keep upgrade, the bootstrap and the switch upgrade, in that order" {
  printf 'secrets: {}\n' > "$BATS_TEST_TMPDIR/secret.yaml"
  run bash "$SCRIPT" qa --release svc --values "$BATS_TEST_TMPDIR/secret.yaml" --set images.backend.digest=sha256:x --yes
  [ "$status" -eq 0 ]
  keep="$(grep -n '^helm upgrade svc .*namespaces.create=true --set rbac.create=true' "$LOG" | cut -d: -f1)"
  boot="$(grep -n '^kubectl apply -f -$' "$LOG" | cut -d: -f1)"
  switch="$(grep -n '^helm upgrade svc .*namespaces.create=false --set rbac.create=false' "$LOG" | cut -d: -f1)"
  [ -n "$keep" ] && [ -n "$boot" ] && [ -n "$switch" ]
  (( keep < boot && boot < switch ))
  for line in "$(sed -n "${keep}p" "$LOG")" "$(sed -n "${switch}p" "$LOG")"; do
    [[ "$line" == *"-n svc-qa -f "*"/charts/your-service-name/values.qa.yaml -f $BATS_TEST_TMPDIR/secret.yaml --set images.backend.digest=sha256:x"* ]]
    [[ "$line" == *"--history-max 5"* && "$line" == *"--wait"* ]]
    [[ "$line" != *"reuse-values"* ]]
  done
  grep "^helm template bootstrap" "$LOG" | grep -q -- "--set-string release=svc --set-string namespace=svc-qa"
  [[ "$output" == *"migrated: svc in svc-qa"* ]]
}

@test "a re-run after a migration does nothing" {
  echo switched > "$STATE"
  run bash "$SCRIPT" dev --yes
  [ "$status" -eq 0 ]
  [[ "$output" == *"nothing to do"* ]]
  run ! grep -q "^helm upgrade" "$LOG"
  run ! grep -q "^kubectl apply" "$LOG"
}

@test "no release is refused" {
  STUB_NO_RELEASE=1 run bash "$SCRIPT" dev --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"no release"* ]]
  run ! grep -q "^helm upgrade" "$LOG"
}

@test "an identity that is not a cluster admin is refused before any change" {
  STUB_CAN=no run bash "$SCRIPT" dev --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"cluster admin"* ]]
  run ! grep -q "^helm upgrade" "$LOG"
}

@test "without --yes and without a terminal it refuses rather than ask" {
  run bash "$SCRIPT" dev < /dev/null
  [ "$status" -eq 2 ]
  [[ "$output" == *"--yes"* ]]
  run ! grep -q "^helm upgrade" "$LOG"
}

@test "a dry run prints every step and touches no cluster" {
  run bash "$SCRIPT" production --release svc --dry-run
  [ "$status" -eq 0 ]
  [ -z "$(cat "$LOG")" ]
  [[ "$output" == *"helm upgrade svc "*"namespaces.create=true"* ]]
  [[ "$output" == *"k8s-bootstrap.sh production --release svc --namespace svc-production | kubectl apply -f -"* ]]
  [[ "$output" == *"helm upgrade svc "*"namespaces.create=false"* ]]
}

@test "a release that still holds them after the switch fails the run" {
  STUB_STAYS_OLD=1 run bash "$SCRIPT" dev --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"still holds"* ]]
}
