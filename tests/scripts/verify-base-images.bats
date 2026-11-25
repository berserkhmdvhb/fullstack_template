#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# verify-base-images.bats — infra/security/local/verify-base-images.sh: the FROM parser over fixture Dockerfiles,
# the classification of the real Dockerfiles, and a fake cosign that fails for a wrong identity and a missing
# signature. Offline: no registry, no Sigstore.
# -----------------------------------------------------------------------------

setup() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  SCRIPT="${VERIFY_SCRIPT:-$ROOT/infra/security/local/verify-base-images.sh}"
  D="sha256:$(printf 'a%.0s' {1..64})"
  E="sha256:$(printf 'b%.0s' {1..64})"
  BIN="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$BIN"
  # A fake cosign: logs argv; fails for another identity or issuer, and for a digest of b's (no signature).
  cat > "$BIN/cosign" <<'SH'
#!/usr/bin/env bash
echo "$*" >> "$COSIGN_LOG"
[[ "$*" == *"--certificate-identity keyless@distroless.iam.gserviceaccount.com "* ]] || { echo "none of the expected identities matched" >&2; exit 1; }
[[ "$*" == *"--certificate-oidc-issuer https://accounts.google.com "* ]] || { echo "issuer mismatch" >&2; exit 1; }
[[ "$*" != *bbbbbbbb* ]] || { echo "no signatures found" >&2; exit 1; }
SH
  chmod +x "$BIN/cosign"
  export COSIGN="$BIN/cosign" COSIGN_LOG="$BATS_TEST_TMPDIR/cosign.log"
  : > "$COSIGN_LOG"
  cd "$BATS_TEST_TMPDIR" || return 1
}

@test "parser: --list prints the refs, skips stage aliases and scratch, reads --platform and a lower-case from" {
  printf '%s\n' \
    "# FROM comment.example/ignored@$D" \
    "FROM --platform=linux/amd64 gcr.io/distroless/static-debian13@$D AS base" \
    "from debian:13-slim@$D as other" \
    "FROM base AS runtime" \
    "FROM scratch" > Dockerfile.one
  run bash "$SCRIPT" --list Dockerfile.one
  [ "$status" -eq 0 ]
  [ "${#lines[@]}" -eq 2 ]
  [[ "${lines[0]}" == "Dockerfile.one"$'\t'"gcr.io/distroless/static-debian13@$D" ]]
  [[ "${lines[1]}" == "Dockerfile.one"$'\t'"debian:13-slim@$D" ]]
}

@test "parser: an ARG-substituted ref is unparsable and fails" {
  printf 'ARG B=x\nFROM %s@%s\n' "\$B" "$D" > Dockerfile.arg
  run bash "$SCRIPT" Dockerfile.arg
  [ "$status" -eq 1 ]
  [[ "$output" == *"unparsable FROM"* ]]
}

@test "a distroless base is verified with the vendor identity and issuer" {
  echo "FROM gcr.io/distroless/static-debian13@$D" > Dockerfile.ok
  run bash "$SCRIPT" Dockerfile.ok
  [ "$status" -eq 0 ]
  grep -qF -- "--certificate-identity keyless@distroless.iam.gserviceaccount.com" "$COSIGN_LOG"
  grep -qF -- "--certificate-oidc-issuer https://accounts.google.com" "$COSIGN_LOG"
  grep -qF "gcr.io/distroless/static-debian13@$D" "$COSIGN_LOG"
}

@test "a missing signature fails and names the image" {
  echo "FROM gcr.io/distroless/static-debian13@$E" > Dockerfile.bad
  run bash "$SCRIPT" Dockerfile.bad
  [ "$status" -eq 1 ]
  [[ "$output" == *"gcr.io/distroless/static-debian13@$E"* ]]
  [[ "$output" == *"no signatures found"* ]]
}

@test "a wrong identity fails and names the image" {
  cat > "$BIN/cosign" <<'SH'
#!/usr/bin/env bash
echo "none of the expected identities matched" >&2
exit 1
SH
  echo "FROM gcr.io/distroless/static-debian13@$D" > Dockerfile.bad
  run bash "$SCRIPT" Dockerfile.bad
  [ "$status" -eq 1 ]
  [[ "$output" == *"gcr.io/distroless/static-debian13@$D"* ]]
  [[ "$output" == *"identities"* ]]
}

@test "an unsigned base listed in the data file is skipped by name; an unlisted one fails" {
  printf 'debian\tno signature published\n' > unsigned.tsv
  export BASE_IMAGES_UNSIGNED="$PWD/unsigned.tsv"
  echo "FROM debian:13-slim@$D" > Dockerfile.deb
  run bash "$SCRIPT" Dockerfile.deb
  [ "$status" -eq 0 ]
  [[ "$output" == *"skipped"*"debian:13-slim"* ]]
  [ ! -s "$COSIGN_LOG" ]
  echo "FROM example.com/other/img:1@$D" > Dockerfile.new
  run bash "$SCRIPT" Dockerfile.new
  [ "$status" -eq 1 ]
  [[ "$output" == *"example.com/other/img:1@$D"*"classify it"* ]]
}

@test "a FROM without a digest fails" {
  echo "FROM gcr.io/distroless/static-debian13:nonroot" > Dockerfile.tag
  run bash "$SCRIPT" Dockerfile.tag
  [ "$status" -eq 1 ]
  [[ "$output" == *"not pinned by digest"* ]]
}

@test "the real Dockerfiles: every FROM is classified, and cosign is asked for the distroless ones only" {
  cd "$ROOT" || return 1
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [ "$(grep -c '^verified: gcr.io/distroless/' <<<"$output")" -eq 2 ]
  [ "$(wc -l < "$COSIGN_LOG")" -eq 2 ]
}
