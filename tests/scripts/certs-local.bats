#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# certs-local.bats — scripts/certs-local.sh against a stub kubectl: it applies its self-signed
# ClusterIssuer only when no ClusterIssuer of that name exists or the existing one carries the
# script's own managed-by label, and never overwrites another (an ACME issuer, say). No cluster
# is contacted.
# -----------------------------------------------------------------------------

bats_require_minimum_version 1.5.0

setup() {
  SCRIPT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)/scripts/certs-local.sh"
  B="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$B"
  export LOG="$BATS_TEST_TMPDIR/calls.log"
  : > "$LOG"
  # STUB_ISSUER is what `kubectl get clusterissuer` prints: empty (absent) or "<name>/<managed-by>".
  cat > "$B/kubectl" <<'STUB'
#!/usr/bin/env bash
echo "kubectl $*" >> "$LOG"
case "$1 $2" in
  "get clusterissuer") printf '%s' "${STUB_ISSUER-}" ;;
  "apply -f") cat > /dev/null ;;
esac
exit 0
STUB
  chmod +x "$B/kubectl"
  export PATH="$B:$PATH"
}

@test "an absent issuer is applied" {
  run "$SCRIPT" --env dev --issuer selfsigned --timeout 1
  [ "$status" -eq 0 ]
  grep -q "^kubectl get clusterissuer selfsigned " "$LOG"
  [ "$(grep -c '^kubectl apply -f -' "$LOG")" -eq 2 ]   # the issuer, then the Certificate
}

@test "an issuer the script created is applied again" {
  STUB_ISSUER="selfsigned/certs-local.sh" run "$SCRIPT" --env dev --timeout 1
  [ "$status" -eq 0 ]
  [ "$(grep -c '^kubectl apply -f -' "$LOG")" -eq 2 ]
}

@test "an existing issuer the script did not create is refused, and nothing is applied" {
  STUB_ISSUER="letsencrypt-prod/" run "$SCRIPT" --env dev --issuer letsencrypt-prod
  [ "$status" -ne 0 ]
  [[ "$output" == *"ClusterIssuer letsencrypt-prod exists and is not managed by certs-local.sh"* ]]
  run ! grep -q '^kubectl apply' "$LOG"
}

@test "an existing issuer is used as is with --no-ca" {
  STUB_ISSUER="letsencrypt-prod/" run "$SCRIPT" --env dev --issuer letsencrypt-prod --no-ca --timeout 1
  [ "$status" -eq 0 ]
  run ! grep -q '^kubectl get clusterissuer' "$LOG"
  [ "$(grep -c '^kubectl apply -f -' "$LOG")" -eq 1 ]   # the Certificate only
}
