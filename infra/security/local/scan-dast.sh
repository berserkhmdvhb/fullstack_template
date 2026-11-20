#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# scan-dast.sh — Dynamic Application Security Test (DAST) with OWASP ZAP.
#
# This is the one scanner that departs from the static-binary / pip pattern: ZAP
# is consumed as a PINNED DOCKER IMAGE (tag plus digest, config.yaml dast.zap_image,
# overridable by DAST_ZAP_IMAGE), run via its zap-baseline.py — there is no
# tools-install.sh case and no config.yaml .tools pin for it. And, unlike the
# source/image scanners, it needs a RUNNING APP to scan: this script starts the
# target in an ephemeral container, health-checks it (bounded, so it never
# hangs), runs the baseline scan, and ALWAYS tears the app container down — even
# on a skip or a mid-scan failure (an EXIT trap), so nothing is leaked.
#
# This is the SINGLE SOURCE OF TRUTH for the DAST scan: the dast workflow and
# the local `devsec.sh dast` command both call it.
#
# Advisory (exit 0): a finding (ZAP exit 1/2) is reported, not a hard gate. Any other
# ZAP/docker exit, or a missing report, is a tool error: status ERROR, exit 1.
#
# Usage: scan-dast.sh [SEC_OUTPUT_DIR] [PROJECT_ROOT]
# -----------------------------------------------------------------------------
set -Eeuo pipefail

# ---------- Shared runtime ----------
# This script lives in infra/security/local/, so _helpers.sh is one level up.
# shellcheck source=/dev/null
source "$(dirname "$(dirname "${BASH_SOURCE[0]}")")/_helpers.sh"
trap 'trap_err' ERR

# --help short-circuit (usage() is defined above).
usage() {
  cat <<'USAGE'
Usage: scan-dast.sh [SEC_OUTPUT_DIR] [PROJECT_ROOT]

Dynamic app scan (OWASP ZAP baseline). Needs a running app: starts the target,
health-checks it, scans, and always tears the app container down. Advisory: a
finding is reported, not a hard gate (exit 0).

  SEC_OUTPUT_DIR  security output dir  (default: config.yaml output.base)
  PROJECT_ROOT    repo root            (default: this script's ../..)
USAGE
  exit 0
}
case "${1:-}" in -h|--help) usage ;; esac

# ---------- Defaults ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SEC_DIR="$(dirname "$SCRIPT_DIR")"                       # infra/security
# CONFIG_FILE defaults to a *relative* path in _helpers.sh; pin it to this repo's
# config.yaml absolutely so cfg() resolves from any cwd — the bats suite runs this
# script from a temp dir, where the relative default ("infra/security/config.yaml")
# would miss and the script would hard-die on an advisory scan. An explicit
# absolute CONFIG_FILE from the environment is still honoured.
case "$CONFIG_FILE" in /*) ;; *) CONFIG_FILE="$SEC_DIR/config.yaml" ;; esac
SEC_OUTPUT_DIR="${1:-${SEC_OUTPUT_DIR:-}}"
resolve_sec_output_dir                                   # env → config → output/security
OUT_DIR="${SEC_OUTPUT_DIR%/}/dast"
# Merged SARIF — written empty here so it exists on EVERY path, including the
# SKIPPED exits below (a skip writes no scan SARIF of its own). The scan overwrites
# it with real content when it runs, so the code scanning upload always has a file in CI.
mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"                        # absolute: docker -v treats a relative source as a named volume
merged_sarif="${OUT_DIR}/dast.sarif"
echo '{"version":"2.1.0","runs":[]}' > "$merged_sarif"
# $2 (PROJECT_ROOT) is accepted for signature parity with the other scanners but
# is not used: a DAST scan targets a running app (a URL), not the repo tree.

DOCKER_BIN="${DOCKER_BIN:-docker}"                       # overridable: the test seam
CURL_BIN="${CURL_BIN:-curl}"                             # overridable: the test seam
# The ZAP image (the "version" pin for this scanner) and the app to scan. The app
# image defaults to the built backend; the target URL to localhost on the mapped
# port — override any of these at runtime (CI sets them via job variables).
DAST_ZAP_IMAGE="${DAST_ZAP_IMAGE:-$(cfg '.dast.zap_image')}"
DAST_TARGET_IMAGE="${DAST_TARGET_IMAGE:-}"               # empty → the script says what to set
DAST_TARGET_URL="${DAST_TARGET_URL:-http://localhost:8000}"
# The readiness probe path (config.yaml dast.health_path: the endpoint the backend serves without the gateway).
DAST_HEALTH_PATH="${DAST_HEALTH_PATH:-$(cfg '.dast.health_path')}"
DAST_PORT="${DAST_PORT:-8000}"
DAST_CONTAINER="${DAST_CONTAINER:-dast-target}"
# Bounded health-poll: never hang on a not-ready app. Overridable (small in tests).
DAST_HEALTH_RETRIES="${DAST_HEALTH_RETRIES:-30}"
DAST_HEALTH_DELAY="${DAST_HEALTH_DELAY:-2}"

lib_banner "scan-dast" "Dynamic app scan (OWASP ZAP baseline)"

need_cmd jq
[[ "$DAST_HEALTH_PATH" == /* ]] || die "no health path: set dast.health_path in ${CONFIG_FILE} (needs yq) or DAST_HEALTH_PATH"
[[ -n "$DAST_ZAP_IMAGE" ]] || die "no ZAP image: set dast.zap_image in ${CONFIG_FILE} (needs yq) or DAST_ZAP_IMAGE"

# ---------- Always tear the app container down (EXIT trap) -------------------
# Registered before the app starts, so EVERY exit path — clean, finding, skip, or
# a mid-scan failure — removes the container. Guarded on DAST_STARTED so a run
# that never got as far as starting the app has nothing to remove.
DAST_STARTED=0
# shellcheck disable=SC2329  # invoked indirectly via the EXIT trap below
cleanup() {
  # Reached only via the EXIT trap below: tear the started app container down on
  # every exit path (clean, finding, skip, or a mid-scan failure). An `if` with no
  # matching branch returns 0, so a run that never started the app stays green.
  # shellcheck disable=SC2317  # reachable via the EXIT trap below (the older shellcheck in CI can't see the trap)
  if [[ "${DAST_STARTED}" == "1" ]]; then
    # shellcheck disable=SC2317  # reachable via the EXIT trap below (the teardown command)
    run "$DOCKER_BIN" rm -f "$DAST_CONTAINER" >/dev/null 2>&1 || warn "failed to remove container $DAST_CONTAINER"
  fi
}
trap 'cleanup' EXIT

# ---------- Pre-flight: Docker daemon reachable? -----------------------------
# No daemon → nothing to start the app on. Degrade to an honest SKIPPED (advisory:
# never gate, never falsely report clean, never hard-die). Under --dry-run the
# daemon is not touched (the ⇢ lines stand in).
if [[ "$DRY_RUN" != "true" ]] && ! "$DOCKER_BIN" info >/dev/null 2>&1; then
  _skipped_scan "$OUT_DIR" "OWASP ZAP" "Docker daemon unavailable — cannot start the target app for the DAST scan"
  exit 0
fi

# A real run needs the app image to scan. Without it there is nothing to start —
# say so as an actionable SKIPPED rather than failing obscurely.
if [[ "$DRY_RUN" != "true" && -z "$DAST_TARGET_IMAGE" ]]; then
  _skipped_scan "$OUT_DIR" "OWASP ZAP" \
    "no DAST_TARGET_IMAGE set — point it at the built app image (CI sets it; locally: DAST_TARGET_IMAGE=<repo>:<tag> devsec.sh dast)"
  exit 0
fi

# ---------- Start the target app (ephemeral container) -----------------------
DAST_STARTED=1
_show_run "$DOCKER_BIN" run -d --name "$DAST_CONTAINER" \
  -p "${DAST_PORT}:8000" "$DAST_TARGET_IMAGE"
if ! run "$DOCKER_BIN" run -d --name "$DAST_CONTAINER" \
            -p "${DAST_PORT}:8000" "$DAST_TARGET_IMAGE" >/dev/null; then
  warn "target app failed to start (container $DAST_CONTAINER) — the scan degrades to SKIPPED"
fi

# ---------- Bounded health-poll (never hang on a not-ready app) --------------
# Poll the app's /health until it answers, capped at DAST_HEALTH_RETRIES. `run`
# makes each probe a no-op under --dry-run (returns 0 → "ready" immediately, so a
# preview never spins). A never-ready app falls through to SKIPPED below, where
# the EXIT trap tears the started container down.
ready=0
for ((i = 0; i < DAST_HEALTH_RETRIES; i++)); do
  if run "$CURL_BIN" -fsS "${DAST_TARGET_URL%/}${DAST_HEALTH_PATH}" >/dev/null 2>&1; then
    ready=1
    break
  fi
  run sleep "$DAST_HEALTH_DELAY" || true           # wait a beat before retrying
done
if [[ "$ready" -ne 1 ]]; then
  warn "target app not ready at ${DAST_TARGET_URL} after ${DAST_HEALTH_RETRIES} attempt(s)"
  _skipped_scan "$OUT_DIR" "OWASP ZAP" "target app not ready at ${DAST_TARGET_URL} (health check failed)"
  exit 0
fi

# ---------- Scan (ZAP baseline over the running app) -------------------------
# ZAP writes its reports into the mounted /zap/wrk (→ the host OUT_DIR). A finding
# exits non-zero — capture it as the verdict, still write both reports, via an
# `if` so `set -e`/ERR does not abort before the teardown trap has a chance.
mkdir -p "$OUT_DIR"
chmod a+rwx "$OUT_DIR"                                   # ZAP runs as a non-root user inside its image
zap_json="${OUT_DIR}/zap.json"
report="${OUT_DIR}/report.json"
# ZAP must reach the app it just started: --network host puts ZAP on the same
# network as the app's published port, so the target URL resolves the same way
# on a native daemon and on the CI docker:dind runner (Linux; --network host is
# not portable to macOS/Windows Docker Desktop — a documented local caveat).
_show_run "$DOCKER_BIN" run --rm --network host -v "$OUT_DIR":/zap/wrk:rw "$DAST_ZAP_IMAGE" \
  zap-baseline.py -t "$DAST_TARGET_URL" \
  -J "$(basename "$zap_json")"
if run "$DOCKER_BIN" run --rm --network host -v "$OUT_DIR":/zap/wrk:rw "$DAST_ZAP_IMAGE" \
      zap-baseline.py -t "$DAST_TARGET_URL" \
      -J "$(basename "$zap_json")"; then
  zap_rc=0
else
  zap_rc=$?
fi

# ---------- Convert ZAP's JSON report to SARIF 2.1.0 (jq) --------------------
# Each alert is one result: ruleId = pluginid-<slug of the alert name>; riskcode
# 3 (High) -> error, 2 (Medium) -> warning, else (Low, Informational) -> note; each
# instance URI is a location (capped at 10 per alert; an alert with none gets the target URL). The JSON report is kept.
if [ "$zap_rc" -le 2 ] && [ -s "$zap_json" ]; then
  jq --arg target "$DAST_TARGET_URL" '
    def slug: ascii_downcase | gsub("[^a-z0-9]+"; "-") | sub("^-"; "") | sub("-$"; "");
    def lvl: if (.riskcode | tostring) == "3" then "error" elif (.riskcode | tostring) == "2" then "warning" else "note" end;
    def rid: "\(.pluginid // .alertRef // "0")-\((.alert // .name // "alert") | slug)";
    [.site[]?.alerts[]?] as $alerts
    | {version: "2.1.0",
       "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
       runs: [{
         tool: {driver: {name: "OWASP ZAP baseline",
                         rules: ($alerts | unique_by(rid) | map({id: rid, name: (.alert // .name // "alert"),
                                 shortDescription: {text: (.alert // .name // "alert")}}))}},
         results: ($alerts | map({ruleId: rid, level: lvl,
                 message: {text: (.alert // .name // "alert")},
                 locations: (([.instances[]?.uri] | unique | .[:10]) as $u
                             | (if ($u | length) == 0 then [$target] else $u end)
                             | map({physicalLocation: {artifactLocation: {uri: .}}}))}))
       }]}' "$zap_json" > "$merged_sarif" || echo '{"version":"2.1.0","runs":[]}' > "$merged_sarif"
fi

# ---------- Verdict ------------------------------------------------------------
# rc 0 clean, rc 1/2 findings (advisory), anything else (ZAP 3, docker 125-127, ...) or
# a missing zap.json is a TOOL ERROR: status ERROR and a non-zero exit, so the job fails.
alerts=0
if [[ "$DRY_RUN" == "true" ]]; then
  status="GREEN"                       # dry-run: nothing ran, so there is no report to classify
  success "dast-scan dry-run: nothing executed"
elif [ "$zap_rc" -gt 2 ] || [ ! -s "$zap_json" ]; then
  status="ERROR"
  error "ZAP baseline tool error (exit ${zap_rc}, report $([ -s "$zap_json" ] && echo present || echo missing))"
elif [ "$zap_rc" -ne 0 ]; then
  status="RED"
  warn "ZAP baseline reported findings (exit ${zap_rc}) — advisory, not blocking"
else
  status="GREEN"
  success "dast-scan clean (exit 0)"
fi
[ -s "$zap_json" ] && alerts="$(jq '[.site[]?.alerts[]?] | length' "$zap_json" 2>/dev/null || echo 0)"
jq -n --arg tool "OWASP ZAP baseline" --arg target "$DAST_TARGET_URL" \
      --arg status "$status" --argjson rc "$zap_rc" --argjson alerts "$alerts" \
  '{tool:$tool, target:$target, exit_code:$rc, status:$status, alerts:$alerts}' > "$report"
success "dast reports written: ${report}, ${merged_sarif}"

# Advisory: a finding is never a gate (exit 0); only a tool error exits non-zero.
[ "$status" != "ERROR" ] || exit 1
exit 0
