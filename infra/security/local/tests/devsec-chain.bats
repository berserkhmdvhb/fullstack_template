#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# devsec-chain.bats — unit tests for devsec.sh's scan chain + fail policy
# (Task 5): cmd_sbom/vuln/license/vex/audit/ci_hardening + cmd_all, and
# the CI-mirroring gate — STOP steps (sbom/vuln/vex/ci-hardening) halt
# the chain and fail the gate; REPORT steps (license/audit) warn and continue.
#
# TDD: written first and run RED (`sbom`..`all` dispatch to _not_implemented, so
# the chain never runs and the exit code / order is wrong), then GREEN once the
# scan chain lands. The chain mutates the system and shells out to the eight CI
# scripts, so — like Task-3's cmd_tools — we drive it via `bash "$SCRIPT" all`
# in a subprocess with a controlled PATH: the six scanners are stubbed on PATH
# to RECORD their name (so the test never runs a real scan) and to exit with a
# per-step code the test controls via <NAME>_EXIT. Each case gets a fresh temp
# dir, so module state and PATH never bleed between cases.
# -----------------------------------------------------------------------------

setup() {
  # $BATS_TEST_DIRNAME is this file's dir (.../tests); devsec.sh is its parent.
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/devsec.sh"
  # CI runners carry tools (yq, curl, docker) in /usr/bin that dev machines may not.
  CORE_PATH="${DEVSEC_TEST_CORE_PATH:-/usr/bin:/bin}"
}

# _setup_chain — stub every scanner cmd_all runs. The five gating/report CI scripts
# (sbom, vuln, license, vex, ci-hardening) append their name to run.log and exit
# ${<NAME>_EXIT:-0}; the five advisory scanners (osv, malware-heuristic, iac, k8s,
# dast) write a GREEN report.json and exit 0 without logging. Failing curl, wget,
# docker, sudo, pip, pip3 and uv stubs make any unstubbed step fail loudly instead of
# touching the network or the system. It points devsec.sh's *_SH vars at the stubs, give it a ready scan.env (so cmd_all skips the Docker build) and an
# empty REPO_ROOT (so the report-only audit skips: no tool / no lockfile). The
# five <NAME>_EXIT vars default to 0, so a case that sets none gets an all-green
# chain; a case overrides the ones it needs.
_setup_chain() {
  local bindir="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$bindir" "$BATS_TEST_TMPDIR/repo"

  # scan.env lives at $SEC_OUTPUT_DIR/scan.env, which cmd_all requires.
  printf 'IMAGE_TAG=%s\n' "local-test" > "$BATS_TEST_TMPDIR/scan.env"

  export SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR"
  export REPO_ROOT="$BATS_TEST_TMPDIR/repo"
  export CHAIN_RUN_LOG="$BATS_TEST_TMPDIR/run.log"
  : > "$CHAIN_RUN_LOG"

  # One stub per CI script: record the step name, exit per <NAME>_EXIT.
  cat > "$bindir/sbom-generate.sh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "sbom" >> "$CHAIN_RUN_LOG"
exit ${SBOM_EXIT:-0}
EOF
  cat > "$bindir/scan-vulnerabilities.sh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "vuln" >> "$CHAIN_RUN_LOG"
exit ${VULN_EXIT:-0}
EOF
  cat > "$bindir/scan-licenses.sh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "license" >> "$CHAIN_RUN_LOG"
exit ${LICENSE_EXIT:-0}
EOF
  cat > "$bindir/generate-vex.sh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "vex" >> "$CHAIN_RUN_LOG"
exit ${VEX_EXIT:-0}
EOF
  cat > "$bindir/check-ci-hardening.sh" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "ci-hardening" >> "$CHAIN_RUN_LOG"
exit ${CIH_EXIT:-0}
EOF

  # Advisory scanners: a GREEN report in <SEC_OUTPUT_DIR>/<name>/, no run.log entry.
  local name var
  for name in osv:OSV_SCAN_SH malware-heuristic:MALWARE_HEURISTIC_SH iac:IAC_SCAN_SH \
              k8s:KUBESCAPE_SCAN_SH dast:DAST_SCAN_SH; do
    var="${name#*:}"; name="${name%%:*}"
    cat > "$bindir/scan-$name.sh" <<EOF
#!/usr/bin/env bash
mkdir -p "\$SEC_OUTPUT_DIR/$name"
printf '%s' '{"tool":"$name","status":"GREEN","findings":[]}' > "\$SEC_OUTPUT_DIR/$name/report.json"
exit 0
EOF
    export "$var=$bindir/scan-$name.sh"
  done

  # Tools no chain test may reach: fail loudly.
  for name in curl wget docker sudo pip pip3 uv; do
    printf '#!/usr/bin/env bash\necho "stub %s: network/system access is not allowed in chain tests" >&2\nexit 1\n' \
      "$name" > "$bindir/$name"
    chmod +x "$bindir/$name"
  done
  chmod +x "$bindir"/*.sh

  export SBOM_GEN_SH="$bindir/sbom-generate.sh"
  export VULN_SCAN_SH="$bindir/scan-vulnerabilities.sh"
  export LICENSE_SCAN_SH="$bindir/scan-licenses.sh"
  export VEX_GEN_SH="$bindir/generate-vex.sh"
  export CI_HARDEN_SH="$bindir/check-ci-hardening.sh"

  export PATH="$bindir:$CORE_PATH"
}

# RED anchor: with the scan chain not yet implemented, `all` dispatches to
# _not_implemented and exits 2, so run.log stays empty and the exit code is
# wrong — both assertions below fail until cmd_all lands.

FIVE_STEPS='sbom
vuln
license
vex
ci-hardening'

@test "all runs the scan chain in CI order" {
  _setup_chain
  run bash "$SCRIPT" all
  # Every step ran, exactly once, in CI order; the gate is green.
  [ "$status" -eq 0 ]
  [ "$(cat "$CHAIN_RUN_LOG")" = "$FIVE_STEPS" ]
}

@test "license RED only warns, chain continues (report-only)" {
  _setup_chain
  export LICENSE_EXIT=1
  run bash "$SCRIPT" all
  # license is report-only: it fails, but the chain keeps going (vex,
  # ci-hardening still run) and the gate stays green.
  [ "$status" -eq 0 ]
  [ "$(cat "$CHAIN_RUN_LOG")" = "$FIVE_STEPS" ]
}

@test "vex with 0 statements does not fail the chain" {
  _setup_chain
  # generate-vex exits 0 with 0 statements (the stub's default exit) — a
  # must-succeed step that succeeded, so the chain treats it as a pass.
  run bash "$SCRIPT" all
  [ "$status" -eq 0 ]
  [ "$(cat "$CHAIN_RUN_LOG")" = "$FIVE_STEPS" ]
}

@test "a SKIPPED source scan (tool unavailable) is rendered yellow and does not gate" {
  _setup_chain
  # osv stub: the advisory layer could not run — it writes a SKIPPED verdict and
  # exits 0 (advisory never gates). malware-heuristic stub: ran clean (GREEN).
  cat > "$BATS_TEST_TMPDIR/bin/scan-osv.sh" <<'EOF'
#!/usr/bin/env bash
mkdir -p "$SEC_OUTPUT_DIR/osv"
printf '%s' '{"tool":"OSV-Scanner","status":"SKIPPED","reason":"osv-scanner unavailable"}' > "$SEC_OUTPUT_DIR/osv/report.json"
exit 0
EOF
  cat > "$BATS_TEST_TMPDIR/bin/scan-malware-heuristic.sh" <<'EOF'
#!/usr/bin/env bash
mkdir -p "$SEC_OUTPUT_DIR/malware-heuristic"
printf '%s' '{"tool":"GuardDog","status":"GREEN","scanned":0,"findings":[]}' > "$SEC_OUTPUT_DIR/malware-heuristic/report.json"
exit 0
EOF
  chmod +x "$BATS_TEST_TMPDIR/bin/scan-osv.sh" "$BATS_TEST_TMPDIR/bin/scan-malware-heuristic.sh"
  export OSV_SCAN_SH="$BATS_TEST_TMPDIR/bin/scan-osv.sh"
  export MALWARE_HEURISTIC_SH="$BATS_TEST_TMPDIR/bin/scan-malware-heuristic.sh"
  run bash "$SCRIPT" all
  [ "$status" -eq 0 ]                          # a skipped advisory layer never fails the gate
  [[ "$output" == *"skipped: osv"* ]]          # the gap is listed, not buried as a green pass
  [[ "$output" != *"ERROR @"* ]]               # no hard-die
}

@test "every scanner the chain runs is stubbed by _setup_chain" {
  _setup_chain
  local vars var
  vars="$(sed -n '/^cmd_all()/,/^}/p' "$SCRIPT" | grep -o '"\$[A-Z_]*_SH"' | tr -d '"$' | sort -u)"
  [ -n "$vars" ]
  for var in $vars; do
    [[ "${!var}" == "$BATS_TEST_TMPDIR/bin/"* ]] || { echo "$var is not stubbed: ${!var:-unset}"; return 1; }
  done
}
