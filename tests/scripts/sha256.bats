#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# sha256.bats — scripts/_sha256.sh, the one sha256 helper of tools.sh, kubeconform.sh and kind-deploy.sh: GNU
# sha256sum where there is one, shasum -a 256 where there is not (macOS). No other script calls sha256sum itself.
# -----------------------------------------------------------------------------

setup() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  printf 'abc' > "$BATS_TEST_TMPDIR/f"
  ABC=ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad
  # macOS has shasum, not GNU sha256sum: every tool of /usr/bin but sha256sum, and a shasum that calls it.
  NOSHA="$BATS_TEST_TMPDIR/nosha"
  mkdir -p "$NOSHA"
  for f in /usr/bin/*; do [ "${f##*/}" = sha256sum ] || ln -sf "$f" "$NOSHA/"; done
  rm -f "$NOSHA/shasum"
  # shellcheck disable=SC2016 # the shim expands its own arguments
  printf '#!/bin/sh\n[ "$1" = -a ] && [ "$2" = 256 ] || exit 2\nshift 2\nexec /usr/bin/sha256sum "$@"\n' > "$NOSHA/shasum"
  chmod +x "$NOSHA/shasum"
}

# shellcheck disable=SC2016 # the inner bash expands its own arguments
@test "the helper hashes a file and stdin with sha256sum" {
  run bash -c 'source "$1"; sha256 "$2"; sha256 < "$2"' _ "$ROOT/scripts/_sha256.sh" "$BATS_TEST_TMPDIR/f"
  [ "$status" -eq 0 ]
  [ "$output" = "$ABC"$'\n'"$ABC" ]
}

# shellcheck disable=SC2016 # the inner bash expands its own arguments
@test "without sha256sum on PATH the helper uses shasum -a 256" {
  [ ! -e "$NOSHA/sha256sum" ]
  run env PATH="$NOSHA" bash -c 'source "$1"; sha256 "$2"; sha256 < "$2"' _ "$ROOT/scripts/_sha256.sh" "$BATS_TEST_TMPDIR/f"
  [ "$status" -eq 0 ]
  [ "$output" = "$ABC"$'\n'"$ABC" ]
}

# kubeconform.sh in a copy of the tree whose k8s-openapi pin is a small fixture: the pin check passes without
# sha256sum (the run stops after it, at the missing helm), and a changed byte is still refused.
kubeconform_copy() {
  P="$BATS_TEST_TMPDIR/proj"
  mkdir -p "$P/scripts/ci" "$P/tools" "$P/.tools/opt/k8s-openapi-1.0.0"
  cp "$ROOT/scripts/ci/kubeconform.sh" "$P/scripts/ci/"
  cp "$ROOT/scripts/_sha256.sh" "$P/scripts/"
  cp "$BATS_TEST_TMPDIR/f" "$P/.tools/opt/k8s-openapi-1.0.0/k8s-openapi"
  printf 'k8s-openapi\t1.0.0\tany\thttps://example.invalid/x\t%s\n' "$ABC" > "$P/tools/pins.tsv"
}

@test "without sha256sum kubeconform.sh checks its pinned spec with shasum" {
  kubeconform_copy
  run env PATH="$NOSHA" bash "$P/scripts/ci/kubeconform.sh"
  [ "$status" -ne 0 ]
  [[ "$output" == *"helm is required"* ]]
  [[ "$output" != *"does not match its pin"* ]]
}

@test "without sha256sum kubeconform.sh still refuses a changed spec" {
  kubeconform_copy
  printf 'x' >> "$P/.tools/opt/k8s-openapi-1.0.0/k8s-openapi"
  run env PATH="$NOSHA" bash "$P/scripts/ci/kubeconform.sh"
  [ "$status" -ne 0 ]
  [[ "$output" == *"does not match its pin"* ]]
}

@test "no script but the helper calls sha256sum itself (the frozen scanners aside)" {
  cd "$ROOT"
  run git grep -lF sha256sum -- '*.sh' ':!scripts/_sha256.sh' ':(exclude,glob)infra/security/*.sh'
  [ -z "$output" ] || { echo "$output"; false; }
}
