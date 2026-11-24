#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# adopt-e2e.bats — infra/adapt/adopt.sh on a copy of this working tree (scripts/ci/adopted-copy.sh), end to end: no placeholder
# form is left outside infra/adapt/excludes.txt, each listed path still holds one, the chart and the
# package are renamed, scripts stay executable, binary files are untouched and a second run is a no-op.
# The placeholders are spelled in pieces so the adapt tooling never rewrites them.
# -----------------------------------------------------------------------------

UNDER="your_""service_name"
DASH="your-""service-name"

# Every placeholder form the adapter stamps, as git grep -E patterns (word-bounded like the adapter).
forms() {
  local b='(^|[^[:alnum:]_.-])' e='($|[^[:alnum:]_-])'
  printf '%s\n' \
    "$UNDER" "$DASH" "Your Service ""Name" "Your""Org" \
    "${b}your-""domain\\.example${e}" "${b}your-""domain\\\\\\.example${e}" \
    "${b}your-""org/your-repo${e}" "${b}@your-""org${e}"
}

setup_file() {
  REPO="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  # The template only, detected as tests/utils/template_tree.py does: an adopted repository has renamed
  # the package, and re-adopting it under the test name is no check of its own service.
  compgen -G "$REPO/src/$UNDER/*.py" > /dev/null \
    || skip "not the template (src/$UNDER/ holds no Python): this check is the template's own"
  # adopted-copy.sh needs this tree's ruff and the pinned helm; the Adopted copy CI job installs both.
  [[ -x "$REPO/.venv/bin/ruff" && -x "$REPO/.tools/bin/helm" ]] \
    || skip "needs this tree's backend (.venv/bin/ruff) and the pinned helm (the Adopted copy CI job runs it)"
  # ADOPTED_COPY: a copy scripts/ci/adopted-copy.sh already made, with its output in "$ADOPTED_COPY.log" (the
  # Adopted copy CI job sets it, so the tree is adopted once). Unset, the file makes its own copy.
  C="${ADOPTED_COPY:-$BATS_FILE_TMPDIR/copy}"
  export REPO C
  if [[ -n "${ADOPTED_COPY:-}" ]]; then
    cp "$C.log" "$BATS_FILE_TMPDIR/adopt.log"
  elif ! (cd "$REPO" && bash scripts/ci/adopted-copy.sh "$C") > "$BATS_FILE_TMPDIR/adopt.log" 2>&1; then
    cat "$BATS_FILE_TMPDIR/adopt.log"
    return 1
  fi
  # The test identity, as scripts/ci/adopted-copy.sh wrote it into the copy's config.
  # shellcheck source=/dev/null
  NAME="$(source "$C/.adapt-backup/adopt.conf" && echo "$SERVICE_NAME")"
  export NAME SLUG="${NAME//_/-}"
}

# The listed paths, as git pathspecs that leave them out.
excluded() {
  while IFS= read -r line; do
    [[ -z "$line" || "$line" == \#* ]] || printf ':(exclude)%s\n' "$line"
  done < "$C/infra/adapt/excludes.txt"
}

hits() {
  local args=() f
  while IFS= read -r f; do args+=(-e "$f"); done < <(forms)
  git -C "$C" grep -IEn "${args[@]}" -- "$@"
}

@test "the adoption ran" {
  grep -q "Adoption complete" "$BATS_FILE_TMPDIR/adopt.log"
}

@test "no placeholder form is left outside the listed exclusions" {
  mapfile -t ex < <(excluded)
  run hits . "${ex[@]}"
  [ "$status" -eq 1 ] || { echo "$output"; false; }
  run bash -c 'git -C "$C" ls-files | grep -F -e "$1" -e "$2"' _ "$UNDER" "$DASH"
  [ -z "$output" ]
}

@test "each listed exclusion in the copy still holds a placeholder form (none is stale)" {
  stale=()
  while IFS= read -r line; do
    [[ -z "$line" || "$line" == \#* ]] && continue
    [[ -n "$(git -C "$C" ls-files -- "$line")" ]] || continue  # a cache or tool directory: not in the tree
    hits "$line" >/dev/null || stale+=("$line")
  done < "$C/infra/adapt/excludes.txt"
  [ "${#stale[@]}" -eq 0 ] || { printf 'stale: %s\n' "${stale[@]}"; false; }
}

@test "the chart directory and the package are renamed" {
  [ -f "$C/charts/$SLUG/Chart.yaml" ]
  [ -f "$C/src/$NAME/__init__.py" ]
  [ ! -e "$C/charts/$DASH" ]
  [ ! -e "$C/src/$UNDER" ]
  grep -qx "name: $SLUG" "$C/charts/$SLUG/Chart.yaml"
}

@test "every rewritten file keeps its mode, and no binary file changes" {
  run git -C "$C" diff --summary HEAD~ HEAD
  [[ "$output" != *"mode change"* ]] || { echo "$output"; false; }
  binaries="$(git -C "$C" diff --numstat HEAD~ HEAD | awk '$1 == "-"')"
  [ -z "$binaries" ] || { echo "$binaries"; false; }
}

@test "a second run is a no-op" {
  (cd "$C" && ADOPT_CONF="$C/.adapt-backup/adopt.conf" ADAPT_ROOT="$C" bash infra/adapt/adopt.sh) \
    > "$BATS_FILE_TMPDIR/again.log" 2>&1
  run git -C "$C" status --porcelain
  [ -z "$output" ] || { echo "$output"; false; }
  grep -q "Total: 0 file(s)" "$BATS_FILE_TMPDIR/again.log"
}

@test "the leftover check passes in the copy" {
  run bash -c 'cd "$C" && bash scripts/ci/check-placeholder-leftovers.sh'
  [ "$status" -eq 0 ] || { echo "$output"; false; }
}

@test "the copy records the template's release and starts its own version line" {
  template="$(jq -r '."."' "$REPO/.release-please-manifest.json")"
  [ "$(cat "$C/.template-version")" = "v$template" ]
  [ "$(jq -r '."."' "$C/.release-please-manifest.json")" = "0.0.0" ]
}
