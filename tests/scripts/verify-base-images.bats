#!/usr/bin/env bats
# shellcheck disable=SC2030,SC2031  # each test runs in its own subshell: the exports are per test
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
  export BASE_IMAGES_BACKOFF=0
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

@test "a per-architecture stage pick is a stage only when both architectures are stages" {
  # shellcheck disable=SC2016 # the Dockerfile text ${TARGETARCH}, written as it is
  printf 'FROM scratch AS tool-amd64\nFROM scratch AS tool-arm64\nFROM tool-${TARGETARCH} AS tool\n' > Dockerfile.arch
  run bash "$SCRIPT" Dockerfile.arch
  [ "$status" -eq 0 ]
  # shellcheck disable=SC2016 # as above
  printf 'FROM scratch AS tool-amd64\nFROM tool-${TARGETARCH} AS tool\n' > Dockerfile.half
  run bash "$SCRIPT" Dockerfile.half
  [ "$status" -eq 1 ]
  [[ "$output" == *"unparsable FROM"* ]]
  # shellcheck disable=SC2016 # as above
  printf 'FROM debian:${TAG}\n' > Dockerfile.var
  run bash "$SCRIPT" Dockerfile.var
  [ "$status" -eq 1 ]
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

@test "COPY --from, ADD --from and RUN --mount=from: an image is classified, a stage or index is not" {
  printf '%s\n' \
    "FROM gcr.io/distroless/static-debian13@$D AS base" \
    "FROM base AS two" \
    "COPY --from=base /a /b" \
    "COPY --from=0 /a /b" \
    "COPY --chown=1:1 --from=ghcr.io/example/tool:1@$D /t /t" \
    "ADD --from=other.example/x/y@$D /a /b" \
    "RUN --mount=type=bind,from=mount.example/m/n@$D,target=/m true" > Dockerfile.copy
  run bash "$SCRIPT" --list Dockerfile.copy
  [ "$status" -eq 0 ]
  [ "${#lines[@]}" -eq 4 ]
  [[ "$output" == *"ghcr.io/example/tool:1@$D"* ]]
  [[ "$output" == *"other.example/x/y@$D"* ]]
  [[ "$output" == *"mount.example/m/n@$D"* ]]
}

@test "an unlisted COPY --from image fails; so does an unlisted --mount from" {
  : > unsigned.tsv
  export BASE_IMAGES_UNSIGNED="$PWD/unsigned.tsv"
  echo "COPY --from=ghcr.io/example/tool:1@$D /t /t" > Dockerfile.copy
  run bash "$SCRIPT" Dockerfile.copy
  [ "$status" -eq 1 ]
  [[ "$output" == *"ghcr.io/example/tool:1@$D"*"classify it"* ]]
  echo "RUN --mount=type=bind,from=mount.example/m/n@$D,target=/m true" > Dockerfile.mount
  run bash "$SCRIPT" Dockerfile.mount
  [ "$status" -eq 1 ]
  [[ "$output" == *"mount.example/m/n@$D"*"classify it"* ]]
}

@test "a COPY --from distroless image is verified" {
  echo "COPY --from=gcr.io/distroless/static-debian13@$D /a /b" > Dockerfile.copy
  run bash "$SCRIPT" Dockerfile.copy
  [ "$status" -eq 0 ]
  grep -qF "gcr.io/distroless/static-debian13@$D" "$COSIGN_LOG"
}

@test "a transient network failure is retried and then passes" {
  cat > "$BIN/cosign" <<'SH'
#!/usr/bin/env bash
echo "$*" >> "$COSIGN_LOG"
if [[ "$(wc -l < "$COSIGN_LOG")" -eq 1 ]]; then echo "Get https://tuf-repo-cdn.sigstore.dev/root.json: dial tcp: i/o timeout" >&2; exit 1; fi
SH
  echo "FROM gcr.io/distroless/static-debian13@$D" > Dockerfile.ok
  run bash "$SCRIPT" Dockerfile.ok
  [ "$status" -eq 0 ]
  [ "$(wc -l < "$COSIGN_LOG")" -eq 2 ]
}

@test "a persistent mismatch fails after three attempts with the full output and a mismatch message" {
  cat > "$BIN/cosign" <<'SH'
#!/usr/bin/env bash
echo "$*" >> "$COSIGN_LOG"
printf 'line one\nerror: no matching signatures\nline three\n' >&2
exit 1
SH
  echo "FROM gcr.io/distroless/static-debian13@$D" > Dockerfile.bad
  run bash "$SCRIPT" Dockerfile.bad
  [ "$status" -eq 1 ]
  [ "$(wc -l < "$COSIGN_LOG")" -eq 3 ]
  [[ "$output" == *"did not verify"* ]]
  [[ "$output" == *"line one"* && "$output" == *"line three"* ]]
}

@test "a persistent network failure fails with a distinct message" {
  cat > "$BIN/cosign" <<'SH'
#!/usr/bin/env bash
echo "$*" >> "$COSIGN_LOG"
echo "error getting TUF root: dial tcp: lookup tuf-repo-cdn.sigstore.dev: no such host" >&2
exit 1
SH
  echo "FROM gcr.io/distroless/static-debian13@$D" > Dockerfile.bad
  run bash "$SCRIPT" Dockerfile.bad
  [ "$status" -eq 1 ]
  [ "$(wc -l < "$COSIGN_LOG")" -eq 3 ]
  [[ "$output" == *"could not be reached"* ]]
  [[ "$output" != *"did not verify"* ]]
}

@test "the default list is every tracked Dockerfile outside tests" {
  cd "$ROOT" || return 1
  run bash "$SCRIPT" --list
  [ "$status" -eq 0 ]
  got="$(cut -f1 <<<"$output" | sort -u)"
  want="$(git ls-files -- '*Dockerfile*' | grep -E '(^|/)(Dockerfile(\..*)?|[^/]*\.Dockerfile)$' | grep -Ev '(^|/)tests/' | sort -u)"
  [ -n "$want" ]
  [ "$got" = "$want" ]
}
