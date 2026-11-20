#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# devsec-clean.bats — unit tests for devsec.sh's `clean` subcommand (local):
# cmd_clean removes ONLY the regenerable local security artifacts — the scan
# output dir (SEC_OUTPUT_DIR, made absolute so it is cwd-independent) and the
# stray repo-root scan.env — never source, .env.local, or the Docker images; it
# is idempotent, dry-run-aware, and REFUSES an empty / "/" / repo-root target
# rather than let a mis-resolved `rm -rf` widen.
#
# cmd_clean mutates the filesystem, so — like devsec-tools/-scanenv.bats — we
# drive it via `run bash -c` in a subprocess with a controlled PATH: a stub `git`
# on PATH lets _bootstrap's derive_ci_env/resolve_registry succeed without
# touching the real repo, and each case points REPO_ROOT + SEC_OUTPUT_DIR at a
# throwaway temp dir so the rm -rf can only ever hit scratch state. REPO_ROOT is
# set AFTER `source` (devsec.sh assigns it unconditionally from the script
# location), and SEC_OUTPUT_DIR is absolute, so the removal is cwd-independent
# and resolves against REPO_ROOT, not the caller's cwd. Each case runs in its own
# `bash -c`, so module state never bleeds.
# -----------------------------------------------------------------------------

setup() {
  # $BATS_TEST_DIRNAME is this file's dir (.../tests); devsec.sh is its parent.
  # Absolute path, no "..", so devsec.sh's own dirname(dirname(BASH_SOURCE[0]))
  # helper-resolution lands in infra/security/ (not in tests/).
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/devsec.sh"
}

# _setup_git — put a stub `git` on PATH (prepended, so coreutils stays reachable)
# returning fixed values, so cmd_clean's _bootstrap (derive_ci_env, resolve_*)
# succeeds without reading the real repo. cmd_clean asserts nothing about these
# values, so any deterministic answer is fine.
_setup_git() {
  mkdir -p "$BATS_TEST_TMPDIR/bin"
  cat > "$BATS_TEST_TMPDIR/bin/git" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  config) echo https://github.com/acme/service-template.git ;;
  *)      echo abc123def456 ;;
esac
EOF
  chmod +x "$BATS_TEST_TMPDIR/bin/git"
  export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
}

# _make_fake_repo — write a throwaway "repo" under the temp dir: a scan output
# tree (sec/{grype,sbom} with files) and a source-like dir whose contents must
# NEVER be deleted. Prints the repo root path.
_make_fake_repo() {
  local root="$BATS_TEST_TMPDIR/repo"
  mkdir -p "$root/sec/grype" "$root/sec/sbom" "$root/src"
  echo out  > "$root/sec/grype/summary.txt"
  echo json > "$root/sec/sbom/index.json"
  echo keep > "$root/src/keep.txt"
  printf '%s' "$root"
}

@test "clean removes the scan output dir and the stray repo-root scan.env" {
  _setup_git
  root="$(_make_fake_repo)"
  echo "IMAGE=x" > "$root/scan.env"          # stray repo-root scan.env
  run bash -c "
    source '$SCRIPT' >/dev/null 2>&1
    REPO_ROOT='$root' SEC_OUTPUT_DIR='$root/sec' DRY_RUN=false cmd_clean
  "
  [ "$status" -eq 0 ]
  [ ! -e "$root/sec" ]                        # scan output tree gone
  [ ! -e "$root/scan.env" ]                   # stray scan.env gone
  [ -e "$root/src/keep.txt" ]                 # source untouched
  [[ "$output" == *"removed 2 path(s)"* ]]    # both targets reported
}

@test "clean --dry-run previews removals and deletes nothing" {
  _setup_git
  root="$(_make_fake_repo)"
  echo "IMAGE=x" > "$root/scan.env"
  run bash -c "
    source '$SCRIPT' >/dev/null 2>&1
    REPO_ROOT='$root' SEC_OUTPUT_DIR='$root/sec' DRY_RUN=true cmd_clean
  "
  [ "$status" -eq 0 ]
  [ -e "$root/sec" ]                          # still present
  [ -e "$root/scan.env" ]                     # still present
  [[ "$output" == *"would-remove"* ]]
  [[ "$output" == *"dry-run: would remove 2 path(s)"* ]]
}

@test "clean on an empty slate reports already-clean and succeeds" {
  _setup_git
  root="$BATS_TEST_TMPDIR/repo"
  mkdir -p "$root/src"; echo keep > "$root/src/keep.txt"
  run bash -c "
    source '$SCRIPT' >/dev/null 2>&1
    REPO_ROOT='$root' SEC_OUTPUT_DIR='$root/sec' DRY_RUN=false cmd_clean
  "
  [ "$status" -eq 0 ]
  [[ "$output" == *"already clean"* ]]
  [ -e "$root/src/keep.txt" ]
}

@test "clean removes a stray repo-root scan.env even when the output dir is absent" {
  _setup_git
  root="$BATS_TEST_TMPDIR/repo"
  mkdir -p "$root/src"; echo keep > "$root/src/keep.txt"
  echo "IMAGE=x" > "$root/scan.env"
  run bash -c "
    source '$SCRIPT' >/dev/null 2>&1
    REPO_ROOT='$root' SEC_OUTPUT_DIR='$root/sec' DRY_RUN=false cmd_clean
  "
  [ "$status" -eq 0 ]
  [ ! -e "$root/scan.env" ]                   # stray removed
  [ -e "$root/src/keep.txt" ]
  [[ "$output" == *"removed 1 path(s)"* ]]    # only the scan.env target
}

@test "clean refuses when the output dir resolves to the repo root (guard)" {
  _setup_git
  root="$(_make_fake_repo)"
  # SEC_OUTPUT_DIR == REPO_ROOT: sec_dir becomes the repo root itself, which the
  # guard must refuse rather than `rm -rf` it — the defense against a mis-resolved
  # removal widening far past the scan artifacts.
  run bash -c "
    source '$SCRIPT' >/dev/null 2>&1
    REPO_ROOT='$root' SEC_OUTPUT_DIR='$root' DRY_RUN=false cmd_clean
  "
  [ "$status" -eq 0 ]
  [[ "$output" == *"refusing to clean unsafe path"* ]]
  [ -d "$root" ]                              # repo root NOT deleted
  [ -e "$root/src/keep.txt" ]                 # nor its contents
  [ -e "$root/sec/grype/summary.txt" ]        # scan artifacts never reached rm
}

@test "clean is cwd-independent: a same-named dir in the cwd is not touched" {
  _setup_git
  root="$(_make_fake_repo)"
  decoy="$BATS_TEST_TMPDIR/cwd"; mkdir -p "$decoy/sec"; echo decoy > "$decoy/sec/file"
  run bash -c "
    source '$SCRIPT' >/dev/null 2>&1
    cd '$decoy'
    REPO_ROOT='$root' SEC_OUTPUT_DIR='$root/sec' DRY_RUN=false cmd_clean
  "
  [ "$status" -eq 0 ]
  [ ! -e "$root/sec" ]                        # the absolute target removed
  [ -e "$decoy/sec/file" ]                    # the cwd decoy (same name) survives
}
