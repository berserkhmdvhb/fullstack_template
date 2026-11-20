#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# devsec-ui.bats — unit tests for the local presentation layer (_ui.sh): every
# status carries a glyph (so a verdict is legible with the colour stripped), the
# command line is rendered distinctly, colour degrades to clean text, and the
# layer is safe to source standalone (report.sh sources it without the runtime —
# a regression guard against `set -u` dying on an unbound colour variable).
#
# Each case runs the layer in a fresh `bash -c`, so its overrides of
# success/warn/error/log do not leak into the bats shell. Colour-on is verified
# by hand (it needs a TTY); here we assert the colour-independent contracts.
# -----------------------------------------------------------------------------

setup() {
  LOCAL_DIR="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"   # …/infra/security/local
  HELPERS="$LOCAL_DIR/../_helpers.sh"                # …/infra/security/_helpers.sh
  UI="$LOCAL_DIR/_ui.sh"
  REPORT="$LOCAL_DIR/report.sh"
}

# _ui CALL — run one layer call in a fresh shell that has the runtime + layer.
_ui() {
  bash -c "source \"$HELPERS\"; source \"$UI\"; $*"
}

@test "status helpers carry a glyph (legible with colour stripped)" {
  [[ "$(_ui 'ui_ok done')"     == *"✓"* ]]
  [[ "$(_ui 'ui_warn careful' 2>&1)" == *"!"* ]]
  [[ "$(_ui 'ui_err bad'      2>&1)" == *"✗"* ]]
}

@test "the overrides are wired: success/warn/error emit the same glyphs" {
  [[ "$(_ui 'success ok')"    == *"✓"* ]]
  [[ "$(_ui 'warn meh'        2>&1)" == *"!"* ]]
  [[ "$(_ui 'error no'        2>&1)" == *"✗"* ]]
}

@test "the command line names its argv (the runner's most-wanted line)" {
  local out; out="$(_ui 'ui_run syft image -o x')"
  [[ "$out" == *"syft image -o x"* ]]
  [[ "$out" == *"▶"* ]]
}

@test "colour degrades to clean text (no CSI escape sequences in the output)" {
  local out
  out="$(NO_COLOR=1 bash -c "source \"$HELPERS\"; source \"$UI\"; ui_ok hi")"
  [[ "$out" != *$'\033['* ]]
}

@test "report.sh sources the layer standalone and summary exits 0 with no outputs" {
  export SEC_OUTPUT_DIR="$BATS_TEST_TMPDIR/none"
  run bash "$REPORT" summary
  [ "$status" -eq 0 ]
}
