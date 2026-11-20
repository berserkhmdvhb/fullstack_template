#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# devsec-vex-vuln.bats — unit tests for devsec.sh's local scan visibility +
# VEX digest capture:
#   - _capture_local_digests: write <SVC>_IMAGE_DIGEST=sha256:… for each built
#     image from the local docker daemon (the LOCAL fallback for the registry
#     digest env verify:registry captures only on a push), reducing the scan.env
#     <SVC>_IMAGE key to the service id and skipping cleanly when docker or an
#     image is absent (never falling back to a mutable tag).
#   - cmd_vex: hand the local digest env to generate-vex.sh when the registry
#     digest env is absent, and pass the registry one through unchanged when
#     present (no regression on the CI path).
#   - cmd_vuln: name the images about to be scanned and surface the per-image
#     summary the -q scan hides.
#
# Like devsec-clean/-tools.bats, these drive mutating commands via `run bash -c`
# (or `run bash "$SCRIPT"`) with a controlled PATH: a stub docker answers inspect
# (so capture never touches a real image), stub CI scripts RECORD their argv, and
# a stub git lets _bootstrap succeed. Each case runs in its own shell, so module
# state never bleeds.
# -----------------------------------------------------------------------------

setup() {
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/devsec.sh"
  CORE_PATH="/usr/bin:/bin"
}

# _stub_docker — put a stub `docker` on PATH: `docker inspect … REF` echoes a
# fixed sha256 for any REF, but exits 1 for a REF containing "missing" (an image
# not in the local daemon). So a test controls exactly which image is "present".
_stub_docker() {
  local bindir="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$bindir"
  cat > "$bindir/docker" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  inspect)
    case "${!#}" in
      *missing*) exit 1 ;;
      *) echo "sha256:0000111122223333444455556666777788889999aaaabbbbccccddddeeee" ;;
    esac ;;
esac
EOF
  chmod +x "$bindir/docker"
  export PATH="$bindir:$CORE_PATH"
}

# _setup_vex — stub generate-vex.sh (records its argv to VEX_ARGV, exit 0) + a
# stub git (so _bootstrap resolves), a scan.env at $SEC_OUTPUT_DIR/scan.env, and
# REPO_ROOT. No image-digests.env, so cmd_vex takes the local-capture path.
_setup_vex() {
  local bindir="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$bindir" "$BATS_TEST_TMPDIR/repo" "$BATS_TEST_TMPDIR/out"
  export SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  export REPO_ROOT="$BATS_TEST_TMPDIR/repo"
  export VEX_ARGV="$BATS_TEST_TMPDIR/vex.argv"; : > "$VEX_ARGV"
  cat > "$SEC_OUTPUT_DIR/scan.env" <<'EOF'
BACKEND_IMAGE=registry.example/backend:local-abc
BACKEND_IMAGE_DOCKER=docker:registry.example/backend:local-abc
EOF
  cat > "$bindir/generate-vex.sh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$VEX_ARGV"
exit 0
EOF
  chmod +x "$bindir/generate-vex.sh"
  export VEX_GEN_SH="$bindir/generate-vex.sh"
  cat > "$bindir/git" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  config) echo https://github.com/acme/service-template.git ;;
  *) echo abc123def456 ;;
esac
EOF
  chmod +x "$bindir/git"
  export PATH="$bindir:$CORE_PATH"
}

# _setup_vuln — stub scan-vulnerabilities.sh (writes grype/summary.txt so the
# local runner has something to summarize, exit 0) + stub git + scan.env.
_setup_vuln() {
  local bindir="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$bindir" "$BATS_TEST_TMPDIR/repo" "$BATS_TEST_TMPDIR/out/grype"
  export SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/out"
  export REPO_ROOT="$BATS_TEST_TMPDIR/repo"
  cat > "$SEC_OUTPUT_DIR/scan.env" <<'EOF'
BACKEND_IMAGE=registry.example/backend:local-abc
FRONTEND_IMAGE=registry.example/frontend:local-abc
EOF
  cat > "$bindir/scan-vulnerabilities.sh" <<'EOF'
#!/usr/bin/env bash
mkdir -p "$SEC_OUTPUT_DIR/grype"
cat > "$SEC_OUTPUT_DIR/grype/summary.txt" <<'SUM'
============================================================
 Grype Summary (per component)
============================================================
[CONTAINER IMAGE]
SBOM: backend.image.sbom.cdx.json
Histogram:
LOW: 1
SUM
  exit 0
EOF
  chmod +x "$bindir/scan-vulnerabilities.sh"
  export VULN_SCAN_SH="$bindir/scan-vulnerabilities.sh"
  cat > "$bindir/git" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  config) echo https://github.com/acme/service-template.git ;;
  *) echo abc123def456 ;;
esac
EOF
  chmod +x "$bindir/git"
  export PATH="$bindir:$CORE_PATH"
}

# --- _capture_local_digests --------------------------------------------------

@test "capture_local_digests writes <SVC>_IMAGE_DIGEST per image, reducing the key and skipping *_IMAGE_DOCKER" {
  _stub_docker
  cat > "$BATS_TEST_TMPDIR/scan.env" <<'EOF'
BACKEND_IMAGE=registry.example/backend:local-abc
FRONTEND_IMAGE=registry.example/frontend:local-abc
BACKEND_IMAGE_DOCKER=docker:registry.example/backend:local-abc
EOF
  run bash -c "
    source '$SCRIPT' >/dev/null 2>&1
    export SCAN_ENV='$BATS_TEST_TMPDIR/scan.env'
    _capture_local_digests '$BATS_TEST_TMPDIR/digests.env'
  "
  [ "$status" -eq 0 ]
  # key reduction: BACKEND_IMAGE -> BACKEND_IMAGE_DIGEST (NOT BACKEND_IMAGE_IMAGE_DIGEST)
  grep -q '^BACKEND_IMAGE_DIGEST=sha256:' "$BATS_TEST_TMPDIR/digests.env"
  grep -q '^FRONTEND_IMAGE_DIGEST=sha256:' "$BATS_TEST_TMPDIR/digests.env"
  # the *_IMAGE_DOCKER build ref must NOT yield a digest key
  run grep -q 'DOCKER' "$BATS_TEST_TMPDIR/digests.env"
  [ "$status" -ne 0 ]
}

@test "capture_local_digests skips an image absent from the local daemon" {
  _stub_docker
  printf 'BACKEND_IMAGE=registry.example/backend-missing:local-abc\n' > "$BATS_TEST_TMPDIR/scan.env"
  run bash -c "
    source '$SCRIPT' >/dev/null 2>&1
    export SCAN_ENV='$BATS_TEST_TMPDIR/scan.env'
    _capture_local_digests '$BATS_TEST_TMPDIR/digests.env'
  "
  [ "$status" -ne 0 ]                 # nothing captured -> non-zero
  [ ! -s "$BATS_TEST_TMPDIR/digests.env" ]   # no digest keys written
  [[ "$output" == *"not in local daemon"* ]]
}

# --- cmd_vex -----------------------------------------------------------------

@test "vex captures local digests and passes them to generate-vex.sh when no registry digest env" {
  _setup_vex
  _stub_docker
  run bash "$SCRIPT" vex
  [ "$status" -eq 0 ]
  # generate-vex.sh received the LOCAL digest env as its 3rd argument…
  grep -q "image-digests.local.env" "$VEX_ARGV"
  # …and that env was populated with the real captured digest.
  grep -q '^BACKEND_IMAGE_DIGEST=sha256:' "$SEC_OUTPUT_DIR/image-digests.local.env"
}

@test "vex passes the registry digest env through unchanged when present (no local capture)" {
  _setup_vex
  # Pre-existing registry digest env (as verify:registry would leave it).
  printf 'BACKEND_IMAGE_DIGEST=sha256:registrydigest\n' > "$SEC_OUTPUT_DIR/image-digests.env"
  _stub_docker                       # even with docker present, must NOT capture
  run bash "$SCRIPT" vex
  [ "$status" -eq 0 ]
  # The LOCAL env is not used; the registry one is passed through.
  run grep -q "image-digests.local.env" "$VEX_ARGV"
  [ "$status" -ne 0 ]
  grep -q "image-digests.env" "$VEX_ARGV"
}

# --- cmd_vuln ----------------------------------------------------------------

@test "vuln names the images it is about to scan" {
  _setup_vuln
  run bash "$SCRIPT" vuln
  [ "$status" -eq 0 ]
  [[ "$output" == *"scanning 2 image(s)"* ]]
  [[ "$output" == *"backend:local-abc"* ]]
}

@test "vuln surfaces the per-image summary the -q scan hides" {
  _setup_vuln
  run bash "$SCRIPT" vuln
  [ "$status" -eq 0 ]
  [[ "$output" == *"scan results"* ]]
  [[ "$output" == *"Grype Summary"* ]]
}

# --- _report_scan_results ----------------------------------------------------

@test "report_scan_results prints actionable findings only when the scan failed its threshold" {
  _setup_vuln
  # This case calls the helper directly (not via the full `vuln` command), so the
  # scan artifacts the stub would write must be created here.
  printf '%s\n' "Grype Summary (per component)" > "$SEC_OUTPUT_DIR/grype/summary.txt"
  printf 'PACKAGE: python (3.13.18)\n  Severity: LOW\n  Fix versions: 3.16.0\n' > "$SEC_OUTPUT_DIR/grype/vuln-by-package.txt"
  run bash -c "
    source '$SCRIPT' >/dev/null 2>&1
    export SEC_OUTPUT_DIR='$SEC_OUTPUT_DIR'
    _report_scan_results 1
  "
  [[ "$output" == *"scan results"* ]]
  [[ "$output" == *"actionable findings"* ]]
  [[ "$output" == *"python (3.13.18)"* ]]
}
