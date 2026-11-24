#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# adopted-copy.bats — scripts/ci/adopted-copy.sh on a small stand-in tree: it copies what a fresh
# clone would see (a tracked file deleted from disk is left out), keeps modes, and refuses to run
# without the ruff and helm the adoption needs. adopt-e2e.bats runs it on this repository.
# -----------------------------------------------------------------------------

setup() {
  REPO="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  S="$BATS_TEST_TMPDIR/src"
  D="$BATS_TEST_TMPDIR/copy"
  mkdir -p "$S/infra/adapt" "$S/bin" "$S/.venv/bin" "$S/.tools/bin" "$S/.tools/helm-plugins"
  printf '/.venv/\n/.tools/\n.adapt-backup/\n' > "$S/.gitignore"
  # adopt.sh's stand-in: records the ruff it was given.
  # shellcheck disable=SC2016 # expanded by the stub, not here
  printf '#!/bin/sh\necho "RUFF=$RUFF" > adopt.ran\necho "Adoption complete"\n' > "$S/infra/adapt/adopt.sh"
  printf 'one\n' > "$S/kept.txt"
  # Listed last (git lists the untracked files, then the tracked ones in order), so the copy loop's
  # last test is on a file that is gone.
  printf 'two\n' > "$S/zz-gone.txt"
  printf '#!/bin/sh\n' > "$S/bin/run.sh"
  chmod 755 "$S/bin/run.sh"
  printf '#!/bin/sh\n' > "$S/.venv/bin/ruff"
  printf '#!/bin/sh\n' > "$S/.tools/bin/helm"
  chmod +x "$S/.venv/bin/ruff" "$S/.tools/bin/helm"
  git -C "$S" init -q
  git -C "$S" add -A
  git -C "$S" -c user.name=t -c user.email=t@example.test commit -qm t
  rm "$S/zz-gone.txt"
  printf 'three\n' > "$S/untracked.txt"
}

copy() { (cd "$S" && bash "$REPO/scripts/ci/adopted-copy.sh" "$D"); }

@test "the copy holds what a fresh clone would see, with modes, and leaves out a deleted tracked file" {
  run copy
  [ "$status" -eq 0 ] || { echo "$output"; false; }
  [ -f "$D/kept.txt" ] && [ -f "$D/untracked.txt" ]
  [ ! -e "$D/zz-gone.txt" ]
  [ -x "$D/bin/run.sh" ]
  [ "$(cat "$D/adopt.ran")" = "RUFF=$S/.venv/bin/ruff" ]
  [ "$(git -C "$D" log --format=%s | tr '\n' ' ')" = "adopted template " ]
}

@test "without this tree's ruff it refuses to run and copies nothing" {
  rm "$S/.venv/bin/ruff"
  run copy
  [ "$status" -eq 2 ]
  [[ "$output" == *"no ruff at $S/.venv/bin/ruff"* ]]
  [ ! -e "$D" ]
}

@test "without the pinned helm and plugin it refuses to run and copies nothing" {
  rm -r "$S/.tools/helm-plugins"
  run copy
  [ "$status" -eq 2 ]
  [[ "$output" == *"no pinned helm and plugin"* ]]
  [ ! -e "$D" ]
}
