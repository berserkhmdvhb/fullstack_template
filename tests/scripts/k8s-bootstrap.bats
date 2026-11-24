#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# k8s-bootstrap.bats — scripts/k8s-bootstrap.sh against a stub helm that records its arguments
# (the pinned yq reads the config). It only renders; nothing contacts a cluster, but for preview --create-secrets, here
# against a stub kubectl. tests/backend/test_k8s_bootstrap.py checks the real render.
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

# ---- preview --create-secrets: the previews' database Secrets, once, through the admin's kubectl ----

# A kubectl that keeps Secrets as files under $SECRETS/<namespace>/<name>/<key>; KUBECTL_FAILS fails a get,
# NO_NAMESPACE=<ns> fails a create there (the namespace is not applied yet).
secrets_kubectl() {
  export SECRETS="$BATS_TEST_TMPDIR/secrets"
  mkdir -p "$SECRETS"
  cat > "$B/kubectl" <<'STUB'
#!/usr/bin/env bash
echo "kubectl $*" >> "$LOG"
ns="$2"; shift 2
case "$1 $2" in
  "get secret")
    [[ -z "${KUBECTL_FAILS:-}" ]] || { echo "Error: forbidden" >&2; exit 1; }
    [[ ! -d "$SECRETS/$ns/$3" ]] || echo "secret/$3" ;;
  "create secret")
    name="$4"; shift 4
    [[ "$ns" != "${NO_NAMESPACE:-}" ]] || { echo "Error: namespaces \"$ns\" not found" >&2; exit 1; }
    [[ ! -d "$SECRETS/$ns/$name" ]] || { echo "AlreadyExists" >&2; exit 1; }
    mkdir -p "$SECRETS/$ns/$name"
    for a in "$@"; do kv="${a#--from-file=}"; cp "${kv#*=}" "$SECRETS/$ns/$name/${kv%%=*}"; done ;;
  "delete secret") rm -r "$SECRETS/$ns/$3" ;;
esac
STUB
  chmod +x "$B/kubectl"
}

@test "--create-secrets creates the two Secrets values.preview.yaml names, with one password, never on a command line or the output" {
  secrets_kubectl
  run bash "$SCRIPT" preview --create-secrets
  [ "$status" -eq 0 ]
  [ "$output" = "k8s-bootstrap: created your-service-name-preview/preview-app-secret and your-service-name-preview-db/preview-db-password" ]
  app="$SECRETS/your-service-name-preview/preview-app-secret" db="$SECRETS/your-service-name-preview-db/preview-db-password"
  [ "$(cat "$app/DB_USER")" = app ]
  [[ "$(cat "$app/DB_PASSWORD")" =~ ^[0-9a-f]{48}$ ]]
  [[ "$(cat "$app/STATS_PASS")" =~ ^[0-9a-f]{48}$ ]]
  [ "$(cat "$db/password")" = "$(cat "$app/DB_PASSWORD")" ]
  [ "$(cat "$app/STATS_PASS")" != "$(cat "$app/DB_PASSWORD")" ]
  run ! grep -qF "$(cat "$app/DB_PASSWORD")" "$LOG"
  run ! grep -q "^== helm" "$LOG"   # nothing rendered or printed
}

@test "--create-secrets keeps both Secrets on a re-run, and refuses when only one exists" {
  secrets_kubectl
  bash "$SCRIPT" preview --release acme --create-secrets
  password="$(cat "$SECRETS/acme-preview/preview-app-secret/DB_PASSWORD")"
  run bash "$SCRIPT" preview --release acme --create-secrets
  [ "$status" -eq 0 ]
  [ "$output" = "k8s-bootstrap: kept acme-preview/preview-app-secret and acme-preview-db/preview-db-password" ]
  [ "$(cat "$SECRETS/acme-preview/preview-app-secret/DB_PASSWORD")" = "$password" ]
  rm -r "$SECRETS/acme-preview-db"
  : > "$LOG"
  run bash "$SCRIPT" preview --release acme --create-secrets
  [ "$status" -eq 2 ]
  [[ "$output" == *"only one of acme-preview/preview-app-secret and acme-preview-db/preview-db-password exists"* ]]
  run ! grep -q "create secret" "$LOG"
}

@test "--create-secrets is refused outside preview, and stops on a kubectl error" {
  secrets_kubectl
  run bash "$SCRIPT" dev --create-secrets
  [ "$status" -eq 2 ]
  [[ "$output" == *"--create-secrets is for preview only"* ]]
  KUBECTL_FAILS=1 run bash "$SCRIPT" preview --create-secrets
  [ "$status" -ne 0 ]
  [[ "$output" == *"cannot read secret preview-app-secret in your-service-name-preview"* ]]
  run ! grep -q "create secret" "$LOG"
}

@test "--create-secrets before the database namespace exists keeps neither Secret, and the re-run then creates both" {
  secrets_kubectl
  NO_NAMESPACE=your-service-name-preview-db run bash "$SCRIPT" preview --create-secrets
  [ "$status" -eq 2 ]
  [[ "$output" == *"cannot create your-service-name-preview-db/preview-db-password (is the bootstrap applied?)"* ]]
  [[ "$output" == *"removed your-service-name-preview/preview-app-secret again"* ]]
  [ ! -e "$SECRETS/your-service-name-preview/preview-app-secret" ]
  run bash "$SCRIPT" preview --create-secrets
  [ "$status" -eq 0 ]
  [[ "$output" == "k8s-bootstrap: created "* ]]
}
