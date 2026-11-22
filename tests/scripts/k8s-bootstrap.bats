#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# k8s-bootstrap.bats — scripts/k8s-bootstrap.sh against a stub helm that records its arguments.
# It only renders; nothing contacts a cluster. tests/backend/test_k8s_bootstrap.py checks the real render.
# -----------------------------------------------------------------------------

bats_require_minimum_version 1.5.0

setup() {
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/../scripts/k8s-bootstrap.sh"
  B="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$B"
  export LOG="$BATS_TEST_TMPDIR/calls.log"
  : > "$LOG"
  cat > "$B/helm" <<'STUB'
#!/usr/bin/env bash
printf '%s\n' "$@" >> "$LOG"
echo "kind: Namespace"
STUB
  # shellcheck disable=SC2016 # expanded by the stub, not here
  printf '#!/usr/bin/env bash\necho "kubectl $*" >> "$LOG"\n' > "$B/kubectl"
  chmod +x "$B"/*
  export PATH="$B:$PATH"
}

@test "an environment renders the bootstrap chart with its values file, to stdout" {
  run bash "$SCRIPT" qa
  [ "$status" -eq 0 ]
  [ "$output" = "kind: Namespace" ]
  [ "$(sed -n 1,2p "$LOG" | tr '\n' ' ')" = "template bootstrap " ]
  grep -qx -- "-f" "$LOG"
  grep -q "/infra/k8s/bootstrap/values.qa.yaml$" "$LOG"
  run ! grep -q "kubectl" "$LOG"
}

@test "the release, namespace and database flags become values" {
  run bash "$SCRIPT" production --release acme --namespace team-a --db
  [ "$status" -eq 0 ]
  grep -qx "release=acme" "$LOG"
  grep -qx "namespace=team-a" "$LOG"
  grep -qx "database.inCluster=true" "$LOG"
  run bash "$SCRIPT" dev --no-db
  grep -qx "database.inCluster=false" "$LOG"
}

@test "an unknown environment, option or a missing value is refused before helm runs" {
  for args in "" "staging" "dev --bogus" "dev --release"; do
    # shellcheck disable=SC2086 # split on purpose
    run bash "$SCRIPT" $args
    [ "$status" -ne 0 ]
    [ -z "$(cat "$LOG")" ]
  done
}

@test "a name that is not a DNS label cannot smuggle extra values into helm" {
  run bash "$SCRIPT" dev --release "acme,deployer.name=evil"
  [ "$status" -eq 2 ]
  [[ "$output" == *"DNS-1123 label"* ]]
  [ -z "$(cat "$LOG")" ]
}

@test "a missing helm is a clear error" {
  run env PATH="/usr/bin:/bin" bash "$SCRIPT" dev
  [ "$status" -eq 2 ]
  [[ "$output" == *"helm is required"* ]]
}
