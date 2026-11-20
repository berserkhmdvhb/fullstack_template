#!/usr/bin/env bash
# infra/security/local/_ui.sh
# =============================================================================
# The local runner's ONE presentation layer (devsec.sh, report.sh).
#
# The shared runtime (_helpers.sh) already provides colour + logging helpers and
# degrades them cleanly (NO_COLOR / non-TTY / dumb TERM -> empty colour vars, so
# piped CI logs stay clean text). This layer sits *on top* of that contract and
# is the single place the local runner decides how its output looks, so every
# command and every view shares one vocabulary instead of each site inventing an
# ad-hoc printf. That is the "centralized" goal: change the look in one file.
#
# It only affects the LOCAL process. The seven CI scripts run as their own
# subprocesses (invoked verbatim) and keep the frozen styling byte-for-byte —
# the CI log must not change just because the laptop experience is prettier.
#
# Design rules (production-grade CLI UX, restrained not decorative):
#   - Colour is borrowed from the frozen palette, so it degrades in lock-step
#     with the frozen helpers — never a stray escape on CI or in a pipe.
#   - Meaning never relies on colour alone: every status carries a glyph, so a
#     colourblind reader — or a grepped log with the colour stripped — still
#     sees pass vs warn vs fail. (The one thing the runner is asked to make
#     easy to find is "which command actually ran", so that line gets its own
#     colour + bold + a glyph and jumps out of a long run.)
#   - No new dependencies: pure bash + printf, like the rest of the runner, so
#     it runs on any laptop out of the box (Linux / macOS, any UTF-8 terminal).
# =============================================================================

# ---------- palette (self-contained, so the layer works standalone) ----------
# Borrow the shared runtime's palette when it is already active (devsec.sh
# sources _helpers.sh first, which sets these from the same rule), otherwise
# compute it here — so the layer also works from a script that does not source
# the runtime, e.g. the read-only report.sh. Either way the rule is identical to
# the frozen one: no colour under NO_COLOR, when stdout is not a TTY (piped /
# CI), or on a "dumb" terminal — so piped logs stay clean text everywhere.
if [[ -z "${RESET:-}" ]]; then
  if [[ -n "${NO_COLOR:-}" || ! -t 1 || "${TERM:-dumb}" == "dumb" ]]; then
    RED=""; GREEN=""; YELLOW=""; BLUE=""; DIM=""; RESET=""
  else
    RED="$(printf '\033[31m')";  GREEN="$(printf '\033[32m')"
    YELLOW="$(printf '\033[33m')"; BLUE="$(printf '\033[34m')"
    DIM="$(printf '\033[2m')";    RESET="$(printf '\033[0m')"
  fi
fi

# Extra emphasis, derived from the palette above: when colour is off, RESET is
# "" and these become "" too (clean text on CI).
if [[ -n "$RESET" ]]; then
  BOLD="$(printf '\033[1m')"
  CYAN="$(printf '\033[36m')"
else
  BOLD=""; CYAN=""
fi

# Glyphs. UTF-8 (the runner already emits box-drawing + ⇢ / ⚠ elsewhere), kept
# as variables so the set lives in one place. Plain by construction (rendered
# via %s, never colourized) so each is legible with the colour stripped.
G_INFO="•"; G_OK="✓"; G_WARN="!"; G_FAIL="✗"; G_RUN="▶"

# _ui_ts — the frozen runtime's "[HH:MM:SS] " prefix when it was sourced before
# us (devsec.sh has it); nothing otherwise (a standalone sourcer such as
# report.sh may not), so this layer degrades gracefully either way.
_ui_ts() { if command -v _ts >/dev/null 2>&1; then _ts; fi; }

# ---------- semantic status helpers (override the frozen log/success/warn/error)
# Each mirrors the frozen helper's format and stream exactly, adding one glyph
# so the status is visible without colour. warn/error go to stderr (as before).

# ui_info MSG — context line (blue, no glyph: it is background, not a verdict).
ui_info() { printf '%b%s%s%b\n' "$BLUE"  "$(_ui_ts)" "$*" "$RESET"; }

# ui_ok MSG — a positive status / verdict.
ui_ok()   { printf '%b%s%s %s%b\n' "$GREEN" "$(_ui_ts)" "$G_OK" "$*" "$RESET"; }

# ui_warn MSG — a warning or advisory gap (SKIPPED, no-SBOM, validation error).
ui_warn() { printf '%b%s%s %s%b\n' "$YELLOW" "$(_ui_ts)" "$G_WARN" "$*" "$RESET" >&2; }

# ui_err MSG — an error.
ui_err()  { printf '%b%s%s %s%b\n' "$RED"   "$(_ui_ts)" "$G_FAIL" "$*" "$RESET" >&2; }

# ui_run CMD [ARG...] — the command about to execute (the frozen _show_run,
# restyled). Bold + distinct colour + glyph so it is the easiest line to find in
# a long run. Suppressed under --dry-run, where `run` already echoes the argv.
ui_run()  { printf '%b%s%s %s%b\n' "$BOLD$CYAN" "$(_ui_ts)" "$G_RUN" "$*" "$RESET"; }

# ---------- overrides (local process only) ------------------------------------
# Sourced after _helpers.sh, so these win for the rest of this process. The CI
# subprocesses have their own copy of the frozen helpers and are unaffected.

_show_run() { [[ "$DRY_RUN" == "true" ]] && return 0; ui_run "$@"; }  # was: blue "$ ..."
success()   { ui_ok   "$*"; }
warn()      { ui_warn "$*"; }
error()     { ui_err  "$*"; }
log()       { ui_info "$*"; }
# die() is left as the frozen `error "$*"; exit 2` — it now calls our ui_err, so
# a fatal error prints the ✗ glyph and still exits 2.

# ui_hdr LABEL — a section header for the read-only views (report.sh). Reuses the
# runner's colour + degradation so the two surfaces share one vocabulary.
ui_hdr() { printf '\n%s%s== %s ==%s\n' "$BOLD$CYAN" "$RESET" "$1" "$RESET"; }

# ui_absent MSG — a muted placeholder for an output the pipeline has not produced
# yet (report.sh). Dim, so "not run yet" is visibly quieter than a verdict.
ui_absent() { printf '  %s%s %s%s\n' "$DIM" "$G_INFO" "$1" "$RESET"; }
