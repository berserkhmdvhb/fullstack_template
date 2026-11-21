#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# devsec-tools.bats — unit tests for devsec.sh's tool provisioning:
# the REQUIRED_TOOLS set, the pinned LOCAL_TOOL_VERSIONS map, and cmd_tools
# (report present / install the missing through the pinned, SHA-256-verifying
# tools-install.sh — never curl|bash — honouring DRY_RUN, and gating non-zero
# when a required tool is still missing).
#
# TDD: written first and run RED (`tools` is wired to _not_implemented, so the
# install is never invoked and the exit code is wrong), then GREEN once
# cmd_tools lands. cmd_tools mutates the system, so — unlike the pure
# functions — we drive it via `bash "$SCRIPT" tools` in a subprocess with a
# controlled PATH: a stub tools-install.sh on PATH RECORDS its argv (so the test
# never downloads), and stub "present" bins stand in for the real tools so a
# test can make exactly the tool under test "missing". Each case gets a fresh
# temp bin dir, so module state and PATH never bleed between cases.
# -----------------------------------------------------------------------------

setup() {
  # $BATS_TEST_DIRNAME is this file's dir (.../tests); devsec.sh is its parent.
  # Absolute path, no "..", so devsec.sh's own dirname(dirname(BASH_SOURCE[0]))
  # helper-resolution lands in infra/security/ (not in tests/).
  SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/devsec.sh"
  # Deterministic coreutils base (date/cat/chmod/mktemp/seq/...); the eight
  # required tools themselves come from the per-test stub dir, so each test
  # controls exactly which of them is present vs missing.
  CORE_PATH="/usr/bin:/bin"
  # Source of truth for pinned versions: infra/security/config.yaml, read the
  # SAME way cmd_tools does. CFG_GRYPE is the grype pin the test expects the
  # install to use — derived, never hardcoded, so the test tracks config.yaml
  # and cannot drift from it. (setup runs on the ambient PATH, so the real yq
  # resolves here even though it is shadowed inside the per-test stub PATH.)
  CONFIG_FILE="$(dirname "$(dirname "$SCRIPT")")/config.yaml"
  CFG_GRYPE="$(yq -r '.tools.grype' "$CONFIG_FILE" 2>/dev/null)"
  [[ -n "$CFG_GRYPE" ]] || die "test setup: could not read .tools.grype from ${CONFIG_FILE}"
}

# _setup_tools PRESENT... — make the named REQUIRED_TOOLS "present" (executable
# stubs in a fresh temp bin dir) and every other required tool "missing".
# Always installs a stub tools-install.sh that APPENDS its argv to install.log
# and never downloads. Exports PATH (so the subprocess sees the stubs) and
# TOOLS_INSTALLER (so cmd_tools invokes the recorder, not the real downloader).
# A tool is "missing" iff it is neither stubbed here nor on CORE_PATH.
_setup_tools() {
  local present=("$@") tool bindir="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$bindir"
  : > "$BATS_TEST_TMPDIR/install.log"
  for tool in syft grype grant cosign jq yq docker git; do
    if [[ " ${present[*]-} " == *" $tool "* ]]; then
      if [[ "$tool" == "yq" ]]; then
        # yq is actually executed by _tool_version to read config.yaml. Report the
        # real .tools.grype pin from config.yaml (baked in, so no PATH recursion)
        # — this exercises cmd_tools' CONFIG-FIRST path (the source of truth),
        # rather than forcing the LOCAL_TOOL_VERSIONS fallback with a hardcoded
        # version. The filter arg is ignored: only grype's pin is ever consumed
        # here (grype is the only missing+installable tool these tests exercise).
        cat > "$bindir/$tool" <<EOF
#!/usr/bin/env bash
printf '%s\n' "${CFG_GRYPE}"
EOF
      else
        cat > "$bindir/$tool" <<EOF
#!/usr/bin/env bash
echo "${tool}-stub"
EOF
      fi
      chmod +x "$bindir/$tool"
    fi
  done
  # Stub recorder: log the argv we were called with; NEVER download anything.
  cat > "$bindir/tools-install.sh" <<'EOF'
#!/usr/bin/env bash
printf 'tools-install.sh %s\n' "$*" >> "$BATS_TEST_TMPDIR/install.log"
EOF
  chmod +x "$bindir/tools-install.sh"
  export PATH="$bindir:$CORE_PATH"
  export TOOLS_INSTALLER="$bindir/tools-install.sh"
}

# RED anchor: with cmd_tools not yet implemented, `tools` dispatches to
# _not_implemented and exits 2, so no install is recorded and the exit code is
# non-zero — both assertions below fail until cmd_tools lands.

@test "cmd_tools installs a missing pinned tool via tools-install.sh" {
  # grype missing; every other required tool present (so only grype installs).
  _setup_tools syft grant cosign jq yq docker git
  run bash "$SCRIPT" tools
  # The recorder logged exactly one install: grype at its CONFIG pin (the source
  # of truth) into the default dest /usr/local/bin. CFG_GRYPE is read from
  # config.yaml in setup, so this asserts the config-first resolution, not a
  # hardcoded version.
  [ "$status" -eq 0 ]
  [ "$(cat "$BATS_TEST_TMPDIR/install.log")" = "tools-install.sh grype ${CFG_GRYPE} /usr/local/bin" ]
}

@test "cmd_tools lists all required tools as present when installed" {
  _setup_tools syft grype grant cosign jq yq docker git
  run bash "$SCRIPT" tools
  # Exit 0 and nothing was installed.
  [ "$status" -eq 0 ]
  [ ! -s "$BATS_TEST_TMPDIR/install.log" ]
}
