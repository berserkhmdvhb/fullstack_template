#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# k8s-bootstrap.bats — scripts/k8s-bootstrap.sh against a stub helm that records its arguments
# (the pinned yq reads the config). It only renders; nothing contacts a cluster. tests/backend/test_k8s_bootstrap.py checks the real render.
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
{ echo "== helm"; printf '%s\n' "$@"; } >> "$LOG"
echo "kind: ${STUB_KIND:-Rendered} # $2"
STUB
  # shellcheck disable=SC2016 # expanded by the stub, not here
  printf '#!/usr/bin/env bash\necho "kubectl $*" >> "$LOG"\n' > "$B/kubectl"
  chmod +x "$B"/*
  export PATH="$B:$PATH"
}

@test "an environment renders the bootstrap chart, then the chart's own RBAC, to stdout" {
  run bash "$SCRIPT" qa
  [ "$status" -eq 0 ]
  [ "$output" = $'kind: Rendered # bootstrap\nkind: Rendered # your-service-name' ]
  grep -q "/infra/k8s/bootstrap/values.qa.yaml$" "$LOG"
  # The app's Role and RoleBinding: the chart's rbac.yaml, its qa values, in the qa namespace.
  app="$(awk '/^== helm$/ {n++} n == 2' "$LOG")"
  [ "$(sed -n 2,3p <<<"$app" | tr '\n' ' ')" = "template your-service-name " ]
  grep -q "/charts/your-service-name/values.qa.yaml$" <<<"$app"
  grep -qx "your-service-name-qa" <<<"$app"
  grep -qx "rbac.create=true" <<<"$app"
  grep -qx "templates/rbac.yaml" <<<"$app"
  run ! grep -q "kubectl" "$LOG"
}

@test "a fullname or nameOverride in the config reaches the chart's render" {
  printf 'fullname: acme-api\nnameOverride: api\n' > "$BATS_TEST_TMPDIR/extra.yaml"
  run bash "$SCRIPT" dev --values "$BATS_TEST_TMPDIR/extra.yaml"
  [ "$status" -eq 0 ]
  grep -qx "fullnameOverride=acme-api" "$LOG"
  grep -qx "nameOverride=api" "$LOG"
  printf 'fullname: "a,b=c"\n' > "$BATS_TEST_TMPDIR/bad.yaml"
  : > "$LOG"
  run bash "$SCRIPT" dev --values "$BATS_TEST_TMPDIR/bad.yaml"
  [ "$status" -eq 2 ]
  [ -z "$(cat "$LOG")" ]
}

@test "the release, namespace and database flags become values" {
  run bash "$SCRIPT" production --release acme --namespace team-a --db
  [ "$status" -eq 0 ]
  grep -qx "release=acme" "$LOG"
  grep -qx "namespace=team-a" "$LOG"
  grep -qx "database.inCluster=true" "$LOG"
  run bash "$SCRIPT" dev --no-db
  grep -qx "database.inCluster=false" "$LOG"
  grep -qx "namespace=your-service-name-dev" "$LOG"
}

@test "an unknown environment, option or a missing value is refused before helm runs" {
  for args in "" "staging" "dev --bogus" "dev --release" "dev --values /nonexistent"; do
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

@test "--chart-set and --chart-set-string reach the chart render, after the --chart-values files" {
  printf 'x: 1\n' > "$BATS_TEST_TMPDIR/c.yaml"
  run bash "$SCRIPT" qa --chart-set a=b --chart-values "$BATS_TEST_TMPDIR/c.yaml" --chart-set-string c=d
  [ "$status" -eq 0 ]
  app="$(awk '/^== helm$/ {n++} n == 2' "$LOG" | tr '\n' ' ')"
  [[ "$app" == *"-f $BATS_TEST_TMPDIR/c.yaml "*"--set a=b --set-string c=d"* ]]
  run ! bash "$SCRIPT" qa --chart-set nokey
}

@test "a forwarded rbac.create=false or clusterRules.enabled=false loses to the bootstrap's own sets" {
  printf 'gmpClusterRules: true\n' > "$BATS_TEST_TMPDIR/boot.yaml"
  printf 'monitoring:\n  flavour: gmp\n' > "$BATS_TEST_TMPDIR/c.yaml"
  run bash "$SCRIPT" qa --values "$BATS_TEST_TMPDIR/boot.yaml" --chart-values "$BATS_TEST_TMPDIR/c.yaml" \
    --chart-set rbac.create=false --chart-set monitoring.gmp.clusterRules.enabled=false
  [ "$status" -eq 0 ]
  # helm applies the last --set of a key: the bootstrap's own must come after the forwarded ones.
  app="$(awk '/^== helm$/ {n++} n == 2' "$LOG")"
  [ "$(grep -x 'rbac.create=.*' <<<"$app" | tail -1)" = "rbac.create=true" ]
  rules="$(awk '/^== helm$/ {n++} n == 3' "$LOG")"
  grep -qx "templates/gmp-cluster-rules.yaml" <<<"$rules"
  [ "$(grep -x 'rbac.create=.*' <<<"$rules" | tail -1)" = "rbac.create=true" ]
  [ "$(grep -x 'monitoring.gmp.clusterRules.enabled=.*' <<<"$rules" | tail -1)" = "monitoring.gmp.clusterRules.enabled=true" ]
}

@test "with gmpClusterRules, a monitoring.flavour --chart-set is refused and names --chart-values" {
  printf 'gmpClusterRules: true\n' > "$BATS_TEST_TMPDIR/boot.yaml"
  for set in monitoring.flavour=gmp "a=b,monitoring.flavour=gmp"; do
    : > "$LOG"
    run bash "$SCRIPT" qa --values "$BATS_TEST_TMPDIR/boot.yaml" --chart-set "$set"
    [ "$status" -eq 2 ]
    [[ "$output" == *"monitoring.flavour cannot be a --chart-set"*"--chart-values"* ]]
    [ -z "$(cat "$LOG")" ]
  done
}
