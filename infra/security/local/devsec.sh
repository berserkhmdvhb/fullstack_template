#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# devsec.sh — Local runner for the project's CI supply-chain security
# steps, so a developer can run them on a laptop instead of in CI.
#
# Invariant: the CI scripts under infra/security/*.sh are never edited; every
# local adaptation lives under infra/security/local/ (this file included).
#
# This file holds the orchestrator skeleton, the local build coordinates, tool provisioning,
# the local build + scan.env, and the scan chain + fail policy. It boots and sources the
# shared runtime. It derives the GitHub Actions variables (GITHUB_*) from the local git
# repo, layers the local registry/project/tag on top (from the gitignored .env.local), and
# dispatches to subcommands.
# bootstrap/build/tools + the sbom/vuln/osv/malware-heuristic/license/vex/audit/
# ci-hardening scan chain and `all` are implemented here. `clean` removes the
# regenerable local security artifacts (the scan output tree + the stray
# repo-root scan.env), never source, .env.local, or the images themselves.
# (Image signing + SLSA attestation run only in CI, in reusable-build.yml — no cosign —
#  so the sign/attest subcommands are intentionally absent.)
#
# Public subcommands:
#   help bootstrap build tools sbom vuln osv malware-heuristic base-images license vex
#   sbomqs sbom-utility audit ci-hardening all clean
# Hidden (local-debug) subcommand:
#   _selftest_env   derive and print the GITHUB_* env (answers "what env did the
#                   runner derive?"); internal, not listed in usage().
#
# Sourced (never executed) by: bats tests, so they can unit-test derive_ci_env
# and the local-coordinate resolvers directly.
# Consumes: _helpers.sh (lib_banner, log/warn/error/die, colors, trap_err,
#           safe_load_env, mask).
# Produces: derive_ci_env; local coordinates — load_local_env, resolve_registry,
#           resolve_project_slug, local_image_tag; the scan chain +
#           fail policy (cmd_sbom..cmd_ci_hardening, cmd_all);
#           usage, main dispatch.
# -----------------------------------------------------------------------------

set -Eeuo pipefail
trap 'trap_err' ERR

# _helpers.sh lives two directory levels above this file:
#   infra/security/local/devsec.sh  ->  infra/security/_helpers.sh
# BASH_SOURCE[0] (not $0) is used so the path resolves whether this file is
# executed directly or sourced by a test. The sourced path is computed at
# runtime, so point shellcheck at /dev/null to keep it quiet (the helpers are
# linted in their own right).
# shellcheck source=/dev/null
source "$(dirname "$(dirname "${BASH_SOURCE[0]}")")/_helpers.sh"

# ---------- local presentation layer -----------------------------------------
# The one place the local runner decides how output looks (glyphs, the
# highlighted command line). Sourced after _helpers.sh so its overrides of
# success/warn/error/log/_show_run win for this process; the CI scripts run as
# their own subprocesses and keep the frozen styling (CI output unchanged).
# shellcheck source=/dev/null
source "$(dirname "${BASH_SOURCE[0]}")/_ui.sh"

# _remote_host URL — print the hostname from a git remote URL. Handles the
# https://[user@]host[:port]/path, ssh://..., and scp-like [user@]host:path
# forms. It extracts only the host; the caller decides the scheme, because
# the server URL is always https even when the remote is ssh.
_remote_host() {
  local u="${1:-}"
  [[ -n "$u" ]] || { printf 'localhost'; return 0; }
  if [[ "$u" == *"://"* ]]; then
    local rest="${u#*://}"        # [user@]host[:port]/path
    local authority="${rest%%/*}" # [user@]host[:port]
    authority="${authority##*@}"  # host[:port]
    printf '%s' "${authority%%:*}"
  elif [[ "$u" == *":"* ]]; then
    local hostpart="${u%%:*}"     # [user@]host
    printf '%s' "${hostpart##*@}"
  else
    printf '%s' "${u%%/*}"
  fi
}

# _remote_path URL — print the project path (owner/name) from a git remote URL,
# with any leading slash and trailing ".git" stripped. Empty when the remote
# carries no path component (so the caller can apply its own fallback).
_remote_path() {
  local u="${1:-}"
  [[ -n "$u" ]] || { printf ''; return 0; }
  local path=""
  if [[ "$u" == *"://"* ]]; then
    local rest="${u#*://}"        # [user@]host[:port]/path (or host if no '/')
    if [[ "$rest" == *"/"* ]]; then
      path="${rest#*/}"           # everything after the first '/'
    fi
  elif [[ "$u" == *":"* ]]; then
    path="${u#*:}"                # scp-like: everything after the ':'
  fi
  path="${path%.git}"
  path="${path#/}"
  printf '%s' "$path"
}

# derive_ci_env — set (and export) the GitHub Actions variables (GITHUB_SHA,
# GITHUB_REF_NAME, GITHUB_REPOSITORY, GITHUB_SERVER_URL) plus GIT_SHORT_SHA from
# the local git repo. Pure: no network, no state beyond the variables it sets, so
# it is safe to call from a test. Every git lookup is guarded, so a repo with no
# remote (or no git at all) still yields sane sentinel values instead of dying.
derive_ci_env() {
  local remote host path
  local sha ref

  sha="$(git rev-parse HEAD 2>/dev/null || true)"
  ref="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  remote="$(git config --get remote.origin.url 2>/dev/null || true)"

  GITHUB_SHA="${sha:-unknown}"
  GIT_SHORT_SHA="${GITHUB_SHA:0:7}"
  GITHUB_REF_NAME="${ref:-unknown}"

  host="$(_remote_host "$remote")"
  path="$(_remote_path "$remote")"

  GITHUB_REPOSITORY="${path:-unknown}"
  GITHUB_SERVER_URL="https://${host:-localhost}"

  export GITHUB_SHA GIT_SHORT_SHA GITHUB_REF_NAME GITHUB_REPOSITORY GITHUB_SERVER_URL
}

# ---------- local-debug aid (hidden) -----------------------------------------

# _selftest_env — derive the GitHub env and print it as KEY=VALUE lines, for local
# debugging ("what env did the runner derive?"). Internal: not in usage().
_selftest_env() {
  derive_ci_env
  local v
  for v in GITHUB_SHA GIT_SHORT_SHA GITHUB_REF_NAME GITHUB_REPOSITORY GITHUB_SERVER_URL; do
    printf '%s=%s\n' "$v" "${!v}"
  done
}

# ---------- local build coordinates ---------------------------------
# Where the local image registry / project / tag come from, so the build step
# knows what to tag. Two layers: derive_ci_env gives the git->CI
# coordinates; then .env.local (gitignored, per-developer, never committed) may
# override the registry and project. Everything here is source-safe and pure, so
# the bats tests can source this file and call the resolvers directly.

# _DEVSEC_LOCAL_DIR — absolute path to this script's directory
# (infra/security/local), resolved once at source time from BASH_SOURCE so it is
# correct whether the script is executed directly or sourced by a test.
_DEVSEC_LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

# _DEVSEC_CONFIG — absolute path to infra/security/config.yaml (one directory
# level above _DEVSEC_LOCAL_DIR), so cmd_tools can read pinned tool versions
# from it regardless of the caller's working directory.
_DEVSEC_CONFIG="$(dirname "$_DEVSEC_LOCAL_DIR")/config.yaml"

# LOCAL_ENV — the parsed contents of .env.local (empty until load_local_env
# runs). Recognised keys: REGISTRY, GITHUB_REPOSITORY (build coordinates). Stored, never sourced, so no
# code in the file ever executes.
declare -A LOCAL_ENV=()

# Resolved local build coordinates (set by resolve_*; empty until then).
LOCAL_REGISTRY=""
LOCAL_PROJECT=""
LOCAL_TAG=""

# LOCAL_IMAGE_REPO (ghcr.io/<owner>/<repo>, lowercased) is what the build step
# consumes; set and exported by _bootstrap once the registry and project are
# resolved. Image refs are "${LOCAL_IMAGE_REPO}/<id>:<tag>".
LOCAL_IMAGE_REPO=""

# load_local_env [FILE] — read the optional per-developer override file
# (.env.local beside this script by default) WITHOUT sourcing it: safe_load_env
# parses KEY=VALUE into LOCAL_ENV, so no shell code in the file ever runs (a
# value like TOKEN=$(evil) is stored literally). Absent file is fine — the
# resolve_* helpers then fall back to git-derived values. Resets LOCAL_ENV each
# call so re-invocation stays idempotent.
#
# Silent by design: it loads and does not report, so it can never leak a secret
# to the log. _bootstrap is the one place these values are printed, and there
# every secret-looking value is passed through mask.
# shellcheck disable=SC2119,SC2120  # FILE is an optional positional (${1:-default})
load_local_env() {
  local file="${1:-${_DEVSEC_LOCAL_DIR}/.env.local}"
  LOCAL_ENV=()
  [[ -f "$file" ]] || return 0
  safe_load_env "$file" LOCAL_ENV
}

# resolve_registry — set LOCAL_REGISTRY: the .env.local REGISTRY override when
# present, else ghcr.io (the registry the release workflow pushes to).
resolve_registry() {
  LOCAL_REGISTRY="${LOCAL_ENV[REGISTRY]:-ghcr.io}"
}

# resolve_project_slug — set LOCAL_PROJECT: the .env.local GITHUB_REPOSITORY
# override when present, else the git-derived GITHUB_REPOSITORY (set by
# derive_ci_env), lowercased because registry repositories must be lowercase.
resolve_project_slug() {
  local p="${LOCAL_ENV[GITHUB_REPOSITORY]:-${GITHUB_REPOSITORY:-unknown}}"
  LOCAL_PROJECT="${p,,}"
}

# local_image_tag — print the deterministic local image tag for the current
# commit: "local-<short sha>" (e.g. "local-abc123d"), so repeated local builds of
# one commit collide rather than accumulate. With GIT_SHORT_SHA unset or
# empty it falls back to "local-dev".
local_image_tag() {
  printf 'local-%s' "${GIT_SHORT_SHA:-dev}"
}

# _bootstrap — derive the GitHub env from git, layer the local build coordinates on
# top (registry / project / tag + the LOCAL_IMAGE_REPO the build step
# consumes), export them, and print them. Any secret-looking value loaded from
# .env.local is reported through mask, so a credential is never echoed in the
# clear.
_bootstrap() {
  lib_banner "bootstrap" "Local tooling + build coordinates"
  derive_ci_env
  load_local_env
  resolve_registry
  resolve_project_slug
  LOCAL_TAG="$(local_image_tag)"
  LOCAL_IMAGE_REPO="${LOCAL_REGISTRY}/${LOCAL_PROJECT}"
  export LOCAL_IMAGE_REPO LOCAL_REGISTRY LOCAL_PROJECT LOCAL_TAG

  log "repository:      ${GITHUB_REPOSITORY}"
  log "commit:          ${GIT_SHORT_SHA} (ref: ${GITHUB_REF_NAME})"
  log "registry:        ${LOCAL_REGISTRY}"
  log "project:         ${LOCAL_PROJECT}"
  log "tag:             ${LOCAL_TAG}"
  log "registry image:  ${LOCAL_IMAGE_REPO}"
  return 0
}

# ---------- tool provisioning ---------------------------------------
# The scan steps need a fixed set of tools. cmd_tools reports which are present
# and installs the missing ones through the repo's pinned, SHA-256-verifying
# tools-install.sh — NEVER curl|bash. docker/git/jq/yq are checked only
# (developer-managed, not provisioned here); syft/grype/grant are the ones we
# install. Versions are pinned, never floating, so a local run matches what CI
# installs. (Image signing/attestation via cosign runs only in CI, in reusable-build.yml,
# so cosign is not provisioned here.)

# REQUIRED_TOOLS — every tool the local security steps need, in check order.
REQUIRED_TOOLS=(syft grype grant jq yq docker git)

# TOOLS_DEST — default directory tools-install.sh installs into.
TOOLS_DEST="${TOOLS_DEST:-/usr/local/bin}"

# TOOLS_INSTALLER — path to the pinned, checksum-verifying installer. Defaults
# to the co-located infra/security/tools-install.sh (the brief's
# "$SCRIPT_DIR/../tools-install.sh" form); overridable so a test can point it at
# a stub recorder instead of the real downloader.
TOOLS_INSTALLER="${TOOLS_INSTALLER:-$_DEVSEC_LOCAL_DIR/../tools-install.sh}"

# LOCAL_TOOL_VERSIONS — fallback pins, never floating. The single source of
# truth is infra/security/config.yaml (`.tools.<tool>`, the SAME flat key the CI
# scripts read via cfg); _tool_version reads it first. These map values are used
# ONLY when config.yaml is absent or unreadable, so they mirror config.yaml's
# current values — a machine that cannot read the config still installs the same
# versions CI does, rather than silently drifting to older local pins.
declare -A LOCAL_TOOL_VERSIONS=(
  [syft]="v1.55.0"
  [grype]="v0.121.3"
  [grant]="v0.6.8"
  # SBOM-analysis tools (report-only quality/validation over the SBOM the chain produced).
  [sbomqs]="v2.1.3"
  [sbom-utility]="v0.19.2"
)

# _tool_is_installable TOOL — true for the subset of REQUIRED_TOOLS we provision
# via tools-install.sh (the rest are checked only).
_tool_is_installable() {
  case "$1" in
    syft|grype|grant) return 0 ;;
    *) return 1 ;;
  esac
}

# _tool_version TOOL — print the pinned version for TOOL: config.yaml wins (the
# single source of truth), reading the SAME flat `.tools.<tool>` key the CI
# scripts use via cfg(); else the LOCAL_TOOL_VERSIONS fallback map; die with a
# clear message if neither carries one (a pin is mandatory — never floating).
#
# Uses `// ""` (not `// empty`): both yield an empty string for an absent key —
# so the mandatory-pin fallback below still triggers — but `// ""` is honored by
# every mikefarah yq build, whereas `// empty` is rejected by some (a lexer error
# this box's yq v4.53.3 raises). A pin must resolve on the dev box, not only in
# CI, so the portable form keeps config-first actually working everywhere.
_tool_version() {
  local tool="$1" val
  if [[ -f "$_DEVSEC_CONFIG" ]] && command -v yq >/dev/null 2>&1; then
    val="$(yq -r ".tools.${tool} // \"\"" "$_DEVSEC_CONFIG" 2>/dev/null || true)"
    [[ -n "$val" ]] && { printf '%s' "$val"; return 0; }
  fi
  val="${LOCAL_TOOL_VERSIONS[$tool]:-}"
  [[ -n "$val" ]] && { printf '%s' "$val"; return 0; }
  die "no pinned version for tool '${tool}' (add it to ${_DEVSEC_CONFIG##*/} or LOCAL_TOOL_VERSIONS)"
}

# cmd_tools — check REQUIRED_TOOLS; install the missing installable ones through
# the pinned tools-install.sh (honouring DRY_RUN via the shared `run`), report
# the missing check-only ones, and gate non-zero if any required tool is still
# missing after the pass (so `all`/scan steps can fail fast).
cmd_tools() {
  lib_banner "tools" "Check / provision local security tooling"
  local tool version missing=()
  for tool in "${REQUIRED_TOOLS[@]}"; do
    if command -v "$tool" >/dev/null 2>&1; then
      log "present  ${tool} -> $(command -v "$tool")"
    elif _tool_is_installable "$tool"; then
      version="$(_tool_version "$tool")"
      warn "missing  ${tool} — installing ${version} via tools-install.sh"
      run bash "$TOOLS_INSTALLER" "$tool" "$version" "$TOOLS_DEST"
    else
      missing+=("$tool")
      warn "missing  ${tool} — install it manually (not provisioned here)"
    fi
  done
  if (( ${#missing[@]} > 0 )); then
    # In a dry run nothing is installed, so a still-missing check-only tool is
    # expected there — report it, do not gate on it.
    if [[ "$DRY_RUN" == "true" ]]; then
      warn "dry-run: ${missing[*]} would remain to install (nothing was installed)"
      return 0
    fi
    die "required tool(s) still missing: $(_join "${missing[@]}")"
  fi
  success "all required tools present"
  return 0
}

# ---------- local image build + scan.env ----------------------------
# The local stand-in for the CI build job: build the project's images from the
# single source .github/container-images.yml and emit scan.env so the scanner steps
# (sbom/vuln/...) resolve them by the <ID>_IMAGE / <ID>_IMAGE_DOCKER keys that
# config.yaml's image.services declares. The eight CI scripts stay untouched.

# _DEVSEC_REPO_ROOT — absolute path to the repository root: three directory
# levels above infra/security/local (…/infra/security/local -> up three -> root),
# so scan.env can be written to a stable place regardless of the caller's cwd.
_DEVSEC_REPO_ROOT="$(dirname "$(dirname "$(dirname "$_DEVSEC_LOCAL_DIR")")")"

# CONTAINER_IMAGES_YML — the single source of image build coordinates
# (id -> dockerfile/context). Overridable so tests point it at a fixture instead
# of the committed .github/container-images.yml.
CONTAINER_IMAGES_YML="${CONTAINER_IMAGES_YML:-$_DEVSEC_REPO_ROOT/.github/container-images.yml}"

# _BUILT_IMAGES — id -> "<repo>:<tag>" for the images build_images just built;
# write_scan_env (and cmd_build) consume it. Reset at the start of build_images,
# so repeated builds stay idempotent.
declare -A _BUILT_IMAGES=()

# build_images — build each image declared in container-images.yml and collect
# id -> "<repo>:<tag>" in _BUILT_IMAGES for the scan.env step. Refuses root
# (shared assert_non_root) and guards the Docker daemon (shared
# need_docker_daemon) up front. Reads id/dockerfile/context via yq; for each id,
# if <context>/<dockerfile> is absent it warns and SKIPS that image — the others
# still build, a missing Dockerfile is not fatal to the whole chain — otherwise it
# builds+tags through the shared run (so DRY_RUN prints the argv, builds nothing).
build_images() {
  assert_non_root
  need_docker_daemon

  local yml="$CONTAINER_IMAGES_YML"
  [[ -f "$yml" ]] || die "image manifest not found: $yml"
  command -v yq >/dev/null 2>&1 || die "yq is required to read $yml"

  local -a ids dockerfiles contexts
  mapfile -t ids         < <(yq '.images[].id'         "$yml")
  mapfile -t dockerfiles < <(yq '.images[].dockerfile' "$yml")
  mapfile -t contexts    < <(yq '.images[].context'    "$yml")

  _BUILT_IMAGES=()
  local i id dockerfile context df_path ref
  for i in "${!ids[@]}"; do
    id="${ids[i]}"
    dockerfile="${dockerfiles[i]}"
    context="${contexts[i]:-.}"
    df_path="${context%/}/${dockerfile}"
    if [[ ! -f "$df_path" ]]; then
      warn "skipping ${id}: ${dockerfile} not found at ${df_path} — continuing with the other images"
      continue
    fi
    ref="${LOCAL_IMAGE_REPO}/${id}:${LOCAL_TAG}"
    run docker build -f "$dockerfile" -t "$ref" "$context"
    _BUILT_IMAGES["$id"]="$ref"
  done
  return 0
}

# _scan_env_path — where scan.env is written (and the scanners read it):
# $SEC_OUTPUT_DIR/scan.env, resolving SEC_OUTPUT_DIR exactly as the scan chain
# does (_prep_scan_env -> resolve_sec_output_dir: env override, else config.yaml's
# output.base, else output/security). Resolving here — not falling back to the
# repo root when SEC_OUTPUT_DIR is unset — is what keeps `build` and the scan
# chain agreeing on scan.env's path when they run as SEPARATE commands: without
# it, an unset SEC_OUTPUT_DIR sent `build` to repo-root/scan.env while `sbom`
# read output/security/scan.env and found no *_IMAGE refs ("no runtime images
# resolved"). `devsec.sh all` never hit this, because _prep_scan_env ran first
# and pinned SEC_OUTPUT_DIR for the whole process.
_scan_env_path() {
  resolve_sec_output_dir
  printf '%s' "${SEC_OUTPUT_DIR%/}/scan.env"
}

# write_scan_env MAPNAME [OUTFILE] — turn the id -> "<repo>:<tag>" map into the
# dotenv text the scanners consume, and write it. Per id (uppercased, so it matches
# config.yaml image.services and what sbom-generate.sh reads):
#   <ID>_IMAGE=<repo>:<tag>
#   <ID>_IMAGE_DOCKER=docker:<repo>:<tag>
# plus IMAGE_TAG=<local tag>. Keys are emitted in a stable (sorted) order and the
# text is exactly what safe_load_env re-parses (KEY=VALUE, never sourced). OUTFILE
# defaults to _scan_env_path; the path is printed for the caller to log. Pure: the
# same map + same path always yield the same bytes.
write_scan_env() {
  local mapname="$1"
  local -n _wmap="$mapname"
  local out="${2:-$(_scan_env_path)}"
  local dir; dir="$(dirname "$out")"
  [[ -d "$dir" ]] || mkdir -p "$dir"

  local -a ids_sorted=()
  if (( ${#_wmap[@]} )); then
    mapfile -t ids_sorted < <(printf '%s\n' "${!_wmap[@]}" | LC_ALL=C sort)
  fi

  local id ref
  {
    for id in "${ids_sorted[@]}"; do
      ref="${_wmap[$id]}"
      printf '%s_IMAGE=%s\n' "${id^^}" "$ref"
      printf '%s_IMAGE_DOCKER=docker:%s\n' "${id^^}" "$ref"
    done
    printf 'IMAGE_TAG=%s\n' "${LOCAL_TAG:-}"
  } > "$out"

  printf '%s' "$out"
}

# cmd_build — local stand-in for the CI build job: bootstrap the CI env + local
# build coordinates, build every image from container-images.yml, then emit
# scan.env so the downstream scanners resolve them, and say where it landed.
cmd_build() {
  lib_banner "build" "Build local images + emit scan.env"
  _bootstrap
  build_images
  local out; out="$(write_scan_env _BUILT_IMAGES)"
  success "wrote scan.env -> ${out}"
  return 0
}

# ---------- scan chain ----------------------------------------------
# The eight CI scripts stay untouched; these commands are the local adaptation
# layer. Each bootstraps the CI env + local coordinates, takes the
# build's scan.env as the scanners' input, and invokes the matching CI script by
# absolute path with the local paths filled in. `all` sequences them in CI order
# and applies the fail policy, which mirrors CI's gate:
#   STOP    — must-succeed / gate-stop (sbom, vuln, vex, ci-hardening):
#             a non-zero exit stops the chain and fails the gate.
#   REPORT  — warn-continue / report-only (license, audit): a non-zero exit is
#             logged, the chain keeps going, and it never fails the gate.

# _SEC_DIR — absolute path to infra/security (one level above local/), the home
# of the eight CI scripts this runner invokes verbatim.
_SEC_DIR="$(dirname "$_DEVSEC_LOCAL_DIR")"

# CI script paths (absolute, so the invocation is cwd-independent). Each is
# overridable — the test seam: point it at a stub recorder instead of the real
# script, so the chain is unit-tested without running a scanner.
SBOM_GEN_SH="${SBOM_GEN_SH:-$_SEC_DIR/sbom-generate.sh}"
VULN_SCAN_SH="${VULN_SCAN_SH:-$_SEC_DIR/scan-vulnerabilities.sh}"
LICENSE_SCAN_SH="${LICENSE_SCAN_SH:-$_SEC_DIR/scan-licenses.sh}"
VEX_GEN_SH="${VEX_GEN_SH:-$_SEC_DIR/generate-vex.sh}"
CI_HARDEN_SH="${CI_HARDEN_SH:-$_SEC_DIR/check-ci-hardening.sh}"

# Source-level scanners (OSV-Scanner known-CVE SCA + GuardDog typosquat). Absolute,
# overridable — the same test seam as the scanner vars: point at a stub recorder in
# tests so the commands are unit-tested without running a scanner.
OSV_SCAN_SH="${OSV_SCAN_SH:-$_SEC_DIR/local/scan-osv.sh}"
MALWARE_HEURISTIC_SH="${MALWARE_HEURISTIC_SH:-$_SEC_DIR/local/scan-malware-heuristic.sh}"
BASE_IMAGES_SH="${BASE_IMAGES_SH:-$_SEC_DIR/local/verify-base-images.sh}"

# IaC misconfiguration scanner (Checkov). Absolute, overridable — the same test
# seam as the scanner vars: point at a stub recorder in tests so the command is
# unit-tested without running the scanner.
IAC_SCAN_SH="${IAC_SCAN_SH:-$_SEC_DIR/local/scan-iac.sh}"

# Kubernetes workload/posture scanner (Kubescape). Absolute, overridable — the same
# test seam.
KUBESCAPE_SCAN_SH="${KUBESCAPE_SCAN_SH:-$_SEC_DIR/local/scan-k8s.sh}"

# Dynamic app scanner (OWASP ZAP baseline). Absolute, overridable — the same test
# seam. Delivered as a pinned Docker image (DAST_ZAP_IMAGE), not a tools-install.sh
# binary; scan-dast.sh starts the target app, health-checks it, scans, and tears it
# down, so DAST_TARGET_IMAGE / DAST_TARGET_URL configure what it scans.
DAST_SCAN_SH="${DAST_SCAN_SH:-$_SEC_DIR/local/scan-dast.sh}"
# SBOM-analysis tools (sbomqs quality scoring + CycloneDX sbom-utility validation). These
# are their own CLIs, not scanners, so there is no scan-*.sh single source — the thin
# cmd_sbomqs / cmd_sbom_utility subcommands below invoke them directly. The binary names are
# overridable — the test seam: a bats stub puts its own sbomqs / sbom-utility on PATH so the
# subcommands are unit-tested without installing or running the real tools.
SBOMQS_BIN="${SBOMQS_BIN:-sbomqs}"
SBOM_UTILITY_BIN="${SBOM_UTILITY_BIN:-sbom-utility}"

# Config-file paths the scanners read. GRYPE_CONFIG is repo-relative (present);
# GRANT_CONFIG comes from config.yaml and is empty when the repo carries no
# license policy file (then scan-licenses.sh runs with its built-in defaults).
CONFIG_FILE="${CONFIG_FILE:-$_DEVSEC_CONFIG}"
GRYPE_CONFIG="${GRYPE_CONFIG:-$_DEVSEC_REPO_ROOT/infra/security/grype.yaml}"
GRANT_CONFIG="${GRANT_CONFIG:-$(cfg '.license.policy_file')}"
# The ZAP image itself (the one delivery deviation from the checksum-binary model):
# a pinned Docker image, not a tools-install.sh binary. Defaulted here so `cmd_dast`
# can name it in its canonical-command display without tripping `set -u`; the pin is
# config.yaml dast.zap_image (read by scan-dast.sh too), and tests point it at a stub.
DAST_ZAP_IMAGE="${DAST_ZAP_IMAGE:-$(cfg '.dast.zap_image')}"

# REPO_ROOT — the local stand-in for the CI workspace ($GITHUB_WORKSPACE), which the scanners
# and check-ci-hardening.sh take as their repo-root argument.
REPO_ROOT="$_DEVSEC_REPO_ROOT"

# DEVSEC_VERBOSE (opt-in, off by default): when =1 the local runner surfaces the
# scanners' own per-finding / per-component detail on the terminal AFTER the
# frozen CI script has run — grype's native per-vulnerability table and the syft
# SBOM summary. Both are rendered from the JSON artifacts the scan already wrote,
# so this is a VIEW over the artifact of record: it matches exactly what CI
# scanned, costs nothing (never a re-scan), and needs no edit to the eight (the
# frozen scripts send that native stdout to files/quiet). Default run stays the
# concise, actionable digest. Mirrors the KUBESCAPE_VERBOSE=1 seam in scan-k8s.sh.
DEVSEC_VERBOSE="${DEVSEC_VERBOSE:-0}"
_verbose() { [[ "${DEVSEC_VERBOSE:-0}" == "1" ]]; }

# _prep_scan_env — resolve the scan output dir + the scan.env path the build
# local build wrote, set GITHUB_WORKSPACE to the repo root, and export them, so the
# CI scanners (which read SEC_OUTPUT_DIR / scan.env / GITHUB_WORKSPACE) run against
# the local repo. Path resolution only — does not require scan.env to exist yet.
_prep_scan_env() {
  resolve_sec_output_dir
  SCAN_ENV="${SEC_OUTPUT_DIR%/}/scan.env"
  DIGEST_ENV="${SEC_OUTPUT_DIR%/}/image-digests.env"
  GITHUB_WORKSPACE="$REPO_ROOT"
  export SEC_OUTPUT_DIR SCAN_ENV DIGEST_ENV GITHUB_WORKSPACE
}

# _invoke_ci CMD... — run a CI script through the DRY_RUN-aware `run` and return
# its exit code, capturing the failure WITHOUT tripping `set -e` / the ERR trap
# (the `||` makes the failure a handled one). A dry run prints the argv and
# executes nothing, so every scan "passes" trivially under --dry-run.
_invoke_ci() {
  local rc=0
  run "$@" || rc=$?
  return "$rc"
}

# ---------- scan-chain fail policy -------------------------------------------
# _CHAIN_STOPPED is set by the first failing STOP step; once set, every later
# _chain_* call short-circuits (logs "skipped") so the remaining steps never run
# — a gate-stop hit, say, does not keep scanning after the gate has already fired.
_CHAIN_STOPPED=0

# _chain_stop NAME CMD... — run a STOP step; record pass/fail and stop the chain
# on failure. Always returns 0 (the gate is `summary`, not this return value).
_chain_stop() {
  local name="$1"; shift
  if [[ "$_CHAIN_STOPPED" == 1 ]]; then
    log "skipped  $name (chain already stopped by an earlier gate)"
    return 0
  fi
  local rc=0
  _invoke_ci "$@" || rc=$?
  if (( rc == 0 )); then
    pass "$name"
  else
    fail "$name"
    _CHAIN_STOPPED=1
  fi
  return 0
}

# _report_status NAME — the verdict status the step's scanner wrote to
# $SEC_OUTPUT_DIR/<name>/report.json (GREEN / RED / SKIPPED), or empty when the step
# wrote no such record. An advisory source scan always exits 0, so its real verdict is
# carried in the report, not the exit code — this lets the chain tell a SKIPPED layer
# (tool unavailable) apart from a clean pass.
_report_status() {
  local f="$SEC_OUTPUT_DIR/$1/report.json"
  [[ -f "$f" ]] || return 0
  jq -r '.status // ""' "$f" 2>/dev/null || true
}

# _chain_report NAME CMD... — run a REPORT step; a non-zero exit is recorded as a
# warning and the chain continues. Never fails the gate. A SKIPPED verdict (the scanner
# could not run) is rendered distinctly — yellow, listed as "skipped" — so a "no
# coverage" gap is never buried as a green pass.
_chain_report() {
  local name="$1"; shift
  if [[ "$_CHAIN_STOPPED" == 1 ]]; then
    log "skipped  $name (chain already stopped by an earlier gate)"
    return 0
  fi
  local rc=0
  _invoke_ci "$@" || rc=$?
  if [[ "$(_report_status "$name")" == "SKIPPED" ]]; then
    skipped "$name"
  elif (( rc == 0 )); then
    pass "$name"
  else
    warn "report-only: $name exited ${rc} — recorded, not gating"
    pass "$name (report-only)"
  fi
  return 0
}

# ---------- scan / audit subcommands -----------------------------------------

# cmd_sbom — local stand-in for the CI SBOM job: bootstrap the env, then invoke
# sbom-generate.sh verbatim against the build's scan.env (image-first).
cmd_sbom() {
  lib_banner "sbom" "Generate the SBOM (syft, image-first)"
  _bootstrap
  _prep_scan_env
  _show_run syft "${LOCAL_IMAGE_REPO}:${LOCAL_TAG}" \
    -o "cyclonedx-json=${SEC_OUTPUT_DIR%/}/sbom/backend.image.sbom.cdx.json"
  local rc=0
  _invoke_ci bash "$SBOM_GEN_SH" -O "$SEC_OUTPUT_DIR" -s "$SCAN_ENV" || rc=$?
  if _verbose; then _syft_summary; fi           # opt-in: syft summary from the artifacts
  return "$rc"
}

# _report_scan_results RC — local visibility over a grype scan: print the
# per-image breakdown the -q invocation suppresses (which image, severity counts,
# fixable) from the grype/ artifacts the CI script already writes, and — only
# when the scan failed its threshold — the actionable per-package CVE + fix list.
# Purely local readability; the scan's exit code (RC) is unchanged and still
# drives the command's result.
_report_scan_results() {
  local rc="$1"
  local base="$SEC_OUTPUT_DIR/grype"
  local summary="$base/summary.txt" bypkg="$base/vuln-by-package.txt"
  [[ -f "$summary" ]] || return 0
  log "scan results (per image, ${base}/summary.txt):"
  cat "$summary"
  if (( rc != 0 )); then
    [[ -f "$bypkg" ]] && { log "actionable findings (highest severity first):"; cat "$bypkg"; }
  fi
  return 0
}

# _grype_native_table — local VERBOSE view: reproduce grype's native per-vulnerability
# table from the <component>.vuln.grype.json artifacts the frozen scan-vulnerabilities.sh
# just wrote (one per SBOM/component). This is the detail the -q scan suppresses and
# sends to files, recovered here from the artifact of record — so it matches what CI
# scanned without a second scan. The JSON carries CVSS baseScore, not grype's EPSS/RISK,
# so CVSS stands in for those two columns (labeled honestly, not as EPSS/RISK).
_grype_native_table() {
  local dir="$SEC_OUTPUT_DIR/grype" file name count
  for file in "$dir"/*.vuln.grype.json; do
    [ -e "$file" ] || continue                 # nullglob-off safe: literal glob when no match
    name="$(basename "$file" .vuln.grype.json)"
    count="$(jq '.matches | length' "$file" 2>/dev/null || echo 0)"
    [[ "$count" =~ ^[0-9]+$ ]] || count=0        # empty/0-byte artifact -> 0
    log "grype findings — ${name} (${count})"
    if [[ "$count" -eq 0 ]]; then
      echo "  (no findings)"; echo; continue
    fi
    printf '%s\n' "NAME  INSTALLED  FIXED-IN  VULNERABILITY  SEVERITY  CVSS"
    jq -r '
      .matches[]
      | [ (.artifact.name // .package.name // "?"),
          (.artifact.version // .package.version // "?"),
          ((.fixVersions // []) | if length > 0 then join(",") else "-" end),
          (.vulnerability.id // "?"),
          (.vulnerability.severity // "Unknown"),
          (((.vulnerability.cvss // []) | map(.metrics.baseScore // empty))
             | (if length > 0 then max else null end) // "-")
        ]
      | @tsv
    ' "$file" \
      | if command -v column >/dev/null 2>&1; then column -t -s "$(printf '\t')" 2>/dev/null; else cat; fi \
      || true
    echo
  done
  return 0
}

# _syft_summary — local VERBOSE view over the image-first SBOM step: the syft run
# log the generator captured (non-empty only when syft emitted diagnostics — its
# progress is suppressed when stdout is not a TTY) plus a per-SBOM component count
# rendered from each CycloneDX JSON. syft's own stdout IS the SBOM, so there is no
# richer native report than these artifacts to surface.
_syft_summary() {
  local sbom_dir="$SEC_OUTPUT_DIR/sbom" logf="" f file name n
  # timestamp-named logs (syft-YYYYMMDD-HHMMSS) sort chronologically, so the last
  # glob match is the latest — no ls/find needed (portable across Linux + macOS).
  for f in "$SEC_OUTPUT_DIR"/logs/syft-*.log; do [ -e "$f" ] && logf="$f"; done
  [[ -s "$logf" ]] && { log "syft run log (${logf##*/}):"; cat "$logf"; }
  # The *.cdx.json glob already excludes index.json, so no explicit skip is needed.
  # Strip .sbom.cdx.json (parallel to the grype loop's .vuln.grype.json) so the
  # component id reads "backend.image", matching the grype artifacts.
  for file in "$sbom_dir"/*.cdx.json; do
    [ -e "$file" ] || continue
    name="$(basename "$file" .sbom.cdx.json)"
    n="$(jq '(.components // []) | length' "$file" 2>/dev/null || echo 0)"
    [[ "$n" =~ ^[0-9]+$ ]] || n=0                # empty/0-byte artifact -> 0
    log "SBOM ${name}: ${n} components (${file##*/})"
  done
  return 0
}

# cmd_vuln — local stand-in for the CI vulnerability job: invoke
# scan-vulnerabilities.sh verbatim (grype, gated at config.yaml policy.fail_level).
cmd_vuln() {
  lib_banner "vuln" "Scan for vulnerabilities (grype)"
  _bootstrap
  _prep_scan_env
  # The -q scan won't say which images it covers; name them up front (from the
  # scan.env the sbom/build step wrote) so the final verdict is legible.
  if [[ -f "$SCAN_ENV" ]]; then
    local -a imgs=()
    local k v
    while IFS='=' read -r k v; do
      [[ "$k" == *_IMAGE && "$k" != *_IMAGE_DOCKER && -n "$v" ]] && imgs+=("$v")
    done < "$SCAN_ENV"
    (( ${#imgs[@]} )) && log "scanning ${#imgs[@]} image(s): ${imgs[*]}"
  fi
  # Canonical gate command (assembled inside the frozen scan-vulnerabilities.sh):
  # grype over each <component>.cdx.json in the SBOM dir, fixable-only, failing at
  # the policy threshold. $GRYPE_CONFIG carries fail_level + only_fixed.
  _show_run grype "sbom:${SEC_OUTPUT_DIR%/}/sbom/*.cdx.json" \
    --fail-on high -o table --only-fixed -c "${GRYPE_CONFIG:-infra/security/grype.yaml}"
  local rc=0
  _invoke_ci bash "$VULN_SCAN_SH" -q -c "$GRYPE_CONFIG" \
    -s "$SEC_OUTPUT_DIR/sbom" -O "$SEC_OUTPUT_DIR" || rc=$?
  _report_scan_results "$rc"
  if _verbose; then _grype_native_table; fi   # opt-in: native per-vuln table from the artifacts
  return "$rc"
}

# cmd_license — local stand-in for the CI license job: invoke
# scan-licenses.sh verbatim. Report-only by policy — a non-zero result is
# recorded, it never gates the chain (see cmd_all / _chain_report).
cmd_license() {
  lib_banner "license" "Scan for license compliance (grant)"
  _bootstrap
  _prep_scan_env
  local -a args=( -s "$SEC_OUTPUT_DIR/sbom" -O "$SEC_OUTPUT_DIR" )
  if [[ -n "$GRANT_CONFIG" && -f "$GRANT_CONFIG" ]]; then
    args+=( -c "$GRANT_CONFIG" )
  else
    args+=( -L )
  fi
  # Canonical command (frozen scan-licenses.sh): `grant list` per <component>.cdx.json
  # in the SBOM dir, written to <component>.license.grant.json; -c enforces the policy
  # file, otherwise -L is list-only. This job stays report-only.
  _show_run grant list "${SEC_OUTPUT_DIR%/}/sbom/*.cdx.json" -o json \
    -f "<component>.license.grant.json" "(-c policy | -L)"
  local rc=0
  _invoke_ci bash "$LICENSE_SCAN_SH" "${args[@]}" || rc=$?
  return "$rc"
}

# cmd_osv — local stand-in for the CI osv job: invoke scan-osv.sh verbatim.
# Report-only (advisory): OSV-Scanner covers known-CVE SCA (npm) + OSV MAL-* from
# resolved lockfiles; a finding is a red, not a gate (see cmd_all / _chain_report).
cmd_osv() {
  lib_banner "osv" "Source-level SCA + malicious (OSV-Scanner)"
  _bootstrap
  _prep_scan_env
  # Canonical command (editable scan-osv.sh): osv-scanner over the repo tree as an
  # offline snapshot (--download-offline-databases --offline-vulnerabilities
  # --allow-no-lockfiles), node_modules + .git excluded, SARIF + JSON reports.
  _show_run osv-scanner scan source --download-offline-databases --offline-vulnerabilities \
    --allow-no-lockfiles -r "$REPO_ROOT" "(-f sarif | json)"
  local rc=0
  _invoke_ci bash "$OSV_SCAN_SH" "$SEC_OUTPUT_DIR" "$REPO_ROOT" || rc=$?
  return "$rc"
}

# cmd_base_images — verify the distroless bases' signatures (the PR `images` job and the release build run
# the same script). A read; needs the network (registry, Rekor, TUF) and cosign on PATH.
cmd_base_images() {
  lib_banner "base-images" "Base image signatures (cosign, distroless keyless identity)"
  (cd "$REPO_ROOT" && bash "$BASE_IMAGES_SH")
}

# cmd_malware_heuristic — local stand-in for the CI malware-heuristic job: invoke
# scan-malware-heuristic.sh verbatim (GuardDog typosquat on direct deps). Report-only
# (advisory): a heuristic finding is a red, not a gate (see cmd_all / _chain_report).
cmd_malware_heuristic() {
  lib_banner "malware-heuristic" "Typosquat heuristic (GuardDog, direct deps)"
  _bootstrap
  _prep_scan_env
  # Canonical command (editable scan-malware-heuristic.sh): one GuardDog scan per
  # DIRECT dep (pypi or npm), typosquatting rules, JSON verdict, per-package timeout.
  _show_run guarddog "{pypi,npm}" scan "<dep>" --rules typosquatting \
    --output-format json "(per direct dep)"
  local rc=0
  _invoke_ci bash "$MALWARE_HEURISTIC_SH" "$SEC_OUTPUT_DIR" "$REPO_ROOT" || rc=$?
  return "$rc"
}

# cmd_iac — local stand-in for the CI iac_scan job: invoke scan-iac.sh verbatim
# (Checkov over Terraform / CloudFormation / K8s / Helm). Report-only (advisory):
# an IaC misconfiguration is a red, not a gate (see cmd_all / _chain_report).
cmd_iac() {
  lib_banner "iac" "IaC misconfiguration scan (Checkov)"
  _bootstrap
  _prep_scan_env
  # Canonical command (editable scan-iac.sh): Checkov directory scan over the repo
  # tree, multi-framework (terraform / cloudformation / kubernetes / helm), SARIF +
  # JSON.
  _show_run checkov -d "$REPO_ROOT" --framework terraform cloudformation kubernetes helm \
    "(-o sarif | json)"
  local rc=0
  _invoke_ci bash "$IAC_SCAN_SH" "$SEC_OUTPUT_DIR" "$REPO_ROOT" || rc=$?
  return "$rc"
}

# cmd_k8s — local stand-in for the CI k8s_scan job: invoke scan-k8s.sh verbatim
# (Kubescape workload scan against NSA-CISA + MITRE). Report-only (advisory): a
# misconfiguration is a red, not a gate (see cmd_all / _chain_report).
cmd_k8s() {
  lib_banner "k8s" "Kubernetes workload/posture scan (Kubescape)"
  _bootstrap
  _prep_scan_env
  # Canonical command (editable scan-k8s.sh): Kubescape offline workload scan over
  # the repo tree, NSA-CISA + MITRE frameworks, SARIF + JSON.
  _show_run kubescape scan framework nsa,mitre --input "$REPO_ROOT" "(-f sarif | json)"
  local rc=0
  _invoke_ci bash "$KUBESCAPE_SCAN_SH" "$SEC_OUTPUT_DIR" "$REPO_ROOT" || rc=$?
  return "$rc"
}

# cmd_dast — local stand-in for the CI dast_scan job: invoke scan-dast.sh verbatim
# (OWASP ZAP baseline against a running app). Report-only (advisory): a finding is a
# red, not a gate (see cmd_all / _chain_report). Needs a running app: set
# DAST_TARGET_IMAGE (the app to start) + DAST_TARGET_URL; with neither, the scan
# honestly SKIPS.
cmd_dast() {
  lib_banner "dast" "Dynamic app scan (OWASP ZAP baseline)"
  _bootstrap
  _prep_scan_env
  # Canonical command (editable scan-dast.sh): start the target app, health-check it,
  # run zap-baseline.py against DAST_TARGET_URL, tear the app down, SARIF + JSON.
  _show_run "$DAST_ZAP_IMAGE" "zap-baseline.py -t ${DAST_TARGET_URL:-http://localhost:8000}" \
    "(-J json report, converted to SARIF)"
  local rc=0
  _invoke_ci bash "$DAST_SCAN_SH" "$SEC_OUTPUT_DIR" "$REPO_ROOT" || rc=$?
  return "$rc"
}

# _capture_local_digests OUTFILE — write <SVC>_IMAGE_DIGEST=sha256:… for each
# *_IMAGE ref in scan.env, using the image's immutable content digest from the
# local docker daemon (docker inspect --format '{{.Id}}'). The scan.env key
# (<SVC>_IMAGE) is reduced to the service id before appending _IMAGE_DIGEST, so
# it matches the <ID>_IMAGE_DIGEST keys generate-vex.sh reads from the grype
# report filename. Emits nothing (returns 1) if docker or an image is absent, so
# generate-vex.sh then skips that image exactly as without a digest — it never
# falls back to a mutable tag. This is the LOCAL fallback for the registry digest
# env that verify:registry captures only on a push: it lets VEX emit a faithful
# preview pinned to the local build (the shipped CI doc pins the registry digest).
_capture_local_digests() {
  local out="$1" env="$SCAN_ENV" id ref svc digest wrote=0
  command -v docker >/dev/null 2>&1 || {
    warn "docker unavailable — cannot capture local image digests (VEX needs an immutable digest)"; return 1; }
  [[ -f "$env" ]] || {
    warn "scan env not found: ${env} — cannot capture local image digests"; return 1; }
  : > "$out"
  while IFS='=' read -r id ref; do
    [[ "$id" == *_IMAGE && "$id" != *_IMAGE_DOCKER ]] || continue
    [[ -n "$ref" ]] || { warn "empty ref for ${id} — skip"; continue; }
    svc="${id%_IMAGE}"
    if digest="$(docker inspect --format '{{.Id}}' "$ref" 2>/dev/null)"; then
      printf '%s=%s\n' "${svc}_IMAGE_DIGEST" "$digest" >> "$out"
      log "captured ${svc} digest ${digest#sha256:}… (local build)"
      wrote=1
    else
      warn "image not in local daemon: ${ref} — skip (cannot pin by digest)"
    fi
  done < "$env"
  (( wrote )) || { warn "no local image digests captured — VEX will emit 0 statements"; return 1; }
  return 0
}

# cmd_vex — local stand-in for the CI VEX job: invoke generate-vex.sh verbatim.
# generate-vex.sh pins each statement to an immutable digest, captured in CI by
# verify:registry (the push) — absent on a laptop. A just-built image, though,
# carries its own immutable content digest, so locally we capture those into
# image-digests.local.env and hand that to generate-vex.sh in place of the
# registry file: the document is emitted now, pinned to the local build (a
# faithful preview; the shipped CI doc pins the registry manifest digest). When
# the registry digest file is present we pass it through unchanged. A 0-statement
# doc is a successful run (exit 0), not a failure.
cmd_vex() {
  lib_banner "vex" "Generate / emit VEX (OpenVEX)"
  _bootstrap
  _prep_scan_env
  local rc=0 digest_env="${DIGEST_ENV}"
  if [[ ! -f "$digest_env" ]] \
     && _capture_local_digests "${SEC_OUTPUT_DIR%/}/image-digests.local.env"; then
    digest_env="${SEC_OUTPUT_DIR%/}/image-digests.local.env"
    log "using local image digests: ${digest_env}"
  fi
  _invoke_ci bash "$VEX_GEN_SH" "$SEC_OUTPUT_DIR" "$SCAN_ENV" "$digest_env" || rc=$?
  return "$rc"
}

# _run_audit — report-only known-CVE audit over the repo's PYTHON dependencies:
# pip-audit over requirements.txt / pyproject.toml / Pipfile. Python SCA is the
# one supply-chain axis OSV-Scanner does NOT cover — it reads resolved JS/Go/Rust
# lockfiles, not PEP 621 pyproject.toml — so pip-audit stays the Python auditor
# (the local counterpart to the CI pip_audit job). npm SCA belongs to osv_scan /
# devsec.sh osv, so it is deliberately not duplicated here (smallest complementary
# set). pip-audit needs network, so when the tool or a manifest is absent it skips
# with a warning (exit 0). A non-zero result is the caller's to weigh — audit
# never gates the chain (report-only).
_run_audit() {
  local rc=0 ran=0
  if command -v pip-audit >/dev/null 2>&1; then
    if [[ -f "$REPO_ROOT/requirements.txt" || -f "$REPO_ROOT/pyproject.toml" || -f "$REPO_ROOT/Pipfile" ]]; then
      warn "running pip-audit (report-only)…"
      # Canonical command: pip-audit over the Python manifest in the repo root
      # (requirements.txt / pyproject.toml / Pipfile) — the one SCA axis OSV-Scanner
      # does not cover. Needs network; skips cleanly when no manifest is present.
      _show_run pip-audit "(repo root: requirements.txt / pyproject.toml / Pipfile)"
      ( cd "$REPO_ROOT" && pip-audit ) || rc=1
      ran=1
    fi
  fi
  [[ "$ran" == 1 ]] || { warn "audit skipped: no pip-audit or Python manifest found (report-only)"; return 0; }
  return "$rc"
}

# cmd_audit — run the report-only Python known-CVE audit (pip-audit).
cmd_audit() {
  lib_banner "audit" "Python known-CVE audit (pip-audit, report-only)"
  _bootstrap
  _prep_scan_env
  local rc=0
  _invoke_ci _run_audit || rc=$?
  return "$rc"
}

# ---------- SBOM analysis (report-only quality + validation) -----------------
# sbomqs and the CycloneDX sbom-utility are their OWN CLIs that ASSESS the SBOM the
# chain already produced — they do not discover vulnerabilities, so (unlike the pipeline
# scanners) they get no scan-*.sh wrapper, no SARIF and no report.json. They are thin
# report-only subcommands: ensure the pinned tool, resolve the SBOM, run the CLI. Both
# are advisory — a low/NTIA-failing quality score or a schema error is a red in the
# report, never a gate (they run as REPORT steps in cmd_all). Each takes a canonical
# command by default, and passes any extra args straight through to the tool
# (e.g. `devsec.sh sbomqs compliance --ntia`, `devsec.sh sbom-utility license list`),
# appending the resolved SBOM path — so the Makefile can expose the useful sub-commands
# without each one becoming its own subcommand.

# _resolve_sbom — print the consolidated CycloneDX SBOM the chain produced
# ($SEC_OUTPUT_DIR/sbom/*.cdx.json), or nothing when the chain has not run. Prefers the
# canonical backend.image SBOM, else the first cdx.json under the sbom dir. The SBOM-analysis
# subcommands read this artifact; they never generate it (that is cmd_sbom / the CI SBOM job).
_resolve_sbom() {
  local d="${SEC_OUTPUT_DIR%/}/sbom" f
  if [ -f "$d/backend.image.sbom.cdx.json" ]; then
    printf '%s' "$d/backend.image.sbom.cdx.json"
  elif f="$(find "$d" -maxdepth 1 -type f -name '*.cdx.json' -print -quit 2>/dev/null)" \
       && [ -n "$f" ]; then
    printf '%s' "$f"
  fi
}

# _ensure_sbom_tool TOOL BIN — make the pinned SBOM-analysis tool available: install it
# (checksum-verified, via tools-install.sh) when absent, else use it as-is. A still-absent
# tool on a real run degrades to an honest SKIPPED (report-only: never gate, never falsely
# report clean) — the same contract the advisory scanners keep. Returns 0 when the tool is
# usable, 1 otherwise; prints the SKIPPED warning itself so callers just `|| return 0`.
_ensure_sbom_tool() {
  local tool="$1" bin="$2" version
  version="$(_tool_version "$tool")"
  if ! command -v "$bin" >/dev/null 2>&1; then
    warn "${tool} missing — installing ${version} via tools-install.sh"
    run bash "$TOOLS_INSTALLER" "$tool" "$version" "$TOOLS_DEST" \
      || warn "${tool} install failed — the report degrades to SKIPPED if the tool is still absent"
  fi
  if [[ "$DRY_RUN" != "true" ]] && ! command -v "$bin" >/dev/null 2>&1; then
    warn "⚠ ${tool} unavailable — SKIPPED: ${tool} ${version} not present and install failed"
    return 1
  fi
  return 0
}

# _sbom_analysis_setup TOOL BIN — shared preamble for the report-only SBOM-analysis CLIs
# (sbomqs, sbom-utility): bootstrap the CI env, resolve the output dir, provision the pinned
# TOOL (install-if-missing, honest SKIPPED on failure), then resolve the chain's consolidated
# SBOM. On success sets _SBOM_ANALYSIS_BOM to its path and returns 0; with no SBOM yet or an
# unprovisionable tool it warns (SKIPPED) and returns 1, so callers just `|| return 0`. (The
# path is returned via a global rather than stdout, so _bootstrap's own output is never
# captured into it.)
_sbom_analysis_setup() {
  local tool="$1" bin="$2"
  _bootstrap
  _prep_scan_env
  _ensure_sbom_tool "$tool" "$bin" || return 1
  local bom; bom="$(_resolve_sbom)"
  if [[ -z "$bom" ]]; then
    warn "no SBOM under ${SEC_OUTPUT_DIR%/}/sbom — run 'devsec.sh sbom' first ($tool skipped)"
    return 1
  fi
  _SBOM_ANALYSIS_BOM="$bom"
  return 0
}

# cmd_sbomqs — report-only SBOM quality + NTIA compliance grade (sbomqs). Scores the SBOM
# the chain produced (completeness, NTIA minimum elements); it does NOT scan for CVEs.
# Advisory: a low / NTIA-failing score is a red in the report, never a gate. With no extra
# args it runs the canonical `score --profile ntia`; any extra args pass straight through to
# sbomqs (e.g. `sbomqs compliance --ntia`, `sbomqs list --feature comp_supplier --missing`),
# with the resolved SBOM path appended.
cmd_sbomqs() {
  lib_banner "sbom-quality" "SBOM quality + NTIA compliance (sbomqs)"
  _sbom_analysis_setup sbomqs "$SBOMQS_BIN" || return 0
  success "SBOM: ${_SBOM_ANALYSIS_BOM##*/}"
  local argv=( "$SBOMQS_BIN" score --profile ntia )
  (( $# )) && argv=( "$SBOMQS_BIN" "$@" )
  argv+=( "$_SBOM_ANALYSIS_BOM" )
  _show_run "${argv[@]}"
  run "${argv[@]}" || true   # report-only: never gate
  return 0
}

# cmd_sbom_utility — report-only SBOM schema validation (CycloneDX sbom-utility). Validates
# the SBOM the chain produced against its declared CycloneDX/SPDX format + version (format
# and schema are auto-inferred from the document); a schema error exits non-zero, captured
# as the verdict but never gating (report-only). Complements sbomqs: validation vs quality.
# With no extra args it runs the canonical `validate -i <SBOM>`; any extra args pass straight
# through to sbom-utility (e.g. `sbom-utility license list`, `sbom-utility component list`),
# with the resolved SBOM appended via `-i` (sbom-utility takes its input as a flag, unlike
# sbomqs, which takes a positional — so the two subcommands append differently).
cmd_sbom_utility() {
  lib_banner "sbom-validate" "SBOM schema validation (CycloneDX sbom-utility)"
  _sbom_analysis_setup sbom-utility "$SBOM_UTILITY_BIN" || return 0
  success "SBOM: ${_SBOM_ANALYSIS_BOM##*/}"
  local argv=( "$SBOM_UTILITY_BIN" validate )
  (( $# )) && argv=( "$SBOM_UTILITY_BIN" "$@" )
  argv+=( -i "$_SBOM_ANALYSIS_BOM" )
  _show_run "${argv[@]}"
  # Capture the non-zero exit as the verdict WITHOUT tripping set -e / the ERR trap (a schema
  # error is the verdict, and the report must still be readable), exactly as the advisory
  # scanners do. Report-only: never gate. The schema-valid / validation-errors verdict lines
  # are validate-specific; other sub-commands (license/component/…) just run.
  local rc=0
  if run "${argv[@]}"; then rc=0; else rc=$?; fi
  if (( rc != 0 )); then
    if [ "${argv[1]:-}" = "validate" ]; then
      warn "sbom-utility reported validation errors (exit ${rc}) — advisory, not blocking"
    else
      warn "sbom-utility exited ${rc} — advisory, not blocking"
    fi
  elif [ "${argv[1]:-}" = "validate" ]; then
    success "sbom-utility: SBOM is schema-valid"
  fi
  return 0
}

# cmd_ci_hardening — local stand-in for the CI hardening-check job: invoke
# check-ci-hardening.sh verbatim against the repo root + scan output dir.
cmd_ci_hardening() {
  lib_banner "ci-hardening" "Check CI supply-chain hardening"
  _bootstrap
  _prep_scan_env
  local rc=0
  _invoke_ci bash "$CI_HARDEN_SH" "$REPO_ROOT" "$SEC_OUTPUT_DIR" || rc=$?
  return "$rc"
}

# cmd_all — run the whole local DevSecOps chain in CI order and apply the fail
# policy, ending with the shared `summary` gate. Order: build (only when the
# chain has no scan.env input yet) → sbom → vuln → osv → malware-heuristic →
# license → vex → audit (report-only) → ci-hardening. A failing STOP
# step stops the chain; a failing REPORT step is logged and the chain continues.
cmd_all() {
  lib_banner "all" "Run the full local DevSecOps chain"
  _bootstrap
  _prep_scan_env

  # The scanners read the build's scan.env; if it does not exist yet, build the
  # images first so the chain has its input (needs the Docker daemon).
  if [[ ! -f "$SCAN_ENV" ]]; then
    warn "scan.env not found at ${SCAN_ENV} — building the images first"
    build_images
    write_scan_env _BUILT_IMAGES >/dev/null
  fi
  [[ -f "$SCAN_ENV" ]] || die "scan.env not found at ${SCAN_ENV} — run 'devsec.sh build' first"

  local -a license_args=( -s "$SEC_OUTPUT_DIR/sbom" -O "$SEC_OUTPUT_DIR" )
  if [[ -n "$GRANT_CONFIG" && -f "$GRANT_CONFIG" ]]; then
    license_args+=( -c "$GRANT_CONFIG" )
  else
    license_args+=( -L )
  fi

  _CHAIN_STOPPED=0
  _chain_stop  sbom         bash "$SBOM_GEN_SH"    -O "$SEC_OUTPUT_DIR" -s "$SCAN_ENV"
  _chain_stop  vuln         bash "$VULN_SCAN_SH"   -q -c "$GRYPE_CONFIG" -s "$SEC_OUTPUT_DIR/sbom" -O "$SEC_OUTPUT_DIR"
  # Source-level advisory scans (OSV-Scanner SCA + GuardDog typosquat): report-only,
  # they never gate the chain (see the fail-policy note above cmd_all).
  _chain_report osv              bash "$OSV_SCAN_SH" "$SEC_OUTPUT_DIR" "$REPO_ROOT"
  _chain_report malware-heuristic bash "$MALWARE_HEURISTIC_SH" "$SEC_OUTPUT_DIR" "$REPO_ROOT"
  # App/infra advisory scans (IaC + K8s workload-posture + dynamic ZAP): report-only,
  # they never gate the chain (see the fail-policy note above cmd_all).
  _chain_report iac               bash "$IAC_SCAN_SH"       "$SEC_OUTPUT_DIR" "$REPO_ROOT"
  _chain_report k8s               bash "$KUBESCAPE_SCAN_SH" "$SEC_OUTPUT_DIR" "$REPO_ROOT"
  _chain_report dast              bash "$DAST_SCAN_SH"      "$SEC_OUTPUT_DIR" "$REPO_ROOT"
  _chain_report license     bash "$LICENSE_SCAN_SH" "${license_args[@]}"
  _chain_stop  vex          bash "$VEX_GEN_SH"     "$SEC_OUTPUT_DIR" "$SCAN_ENV" "$DIGEST_ENV"
  _chain_report audit       _run_audit
  _chain_stop  ci-hardening bash "$CI_HARDEN_SH"   "$REPO_ROOT" "$SEC_OUTPUT_DIR"

  summary
}

# ---------- clean (remove local security artifacts) --------------------------

# cmd_clean — remove the regenerable local security artifacts this runner
# produces, so a developer can restart a scan chain from a clean slate. It
# deletes ONLY:
#   - the scan output dir (SEC_OUTPUT_DIR, default output/security): the whole
#     tree the chain writes — sbom/ grype/ logs/ vex/ license/ …
#   - the stray repo-root scan.env fallback (gitignored), when an older run
#     wrote there instead of inside SEC_OUTPUT_DIR
# and NEVER source, .env.local (per-developer coords/creds), or the Docker
# images themselves (deleting an image is a real side effect, out of scope).
#
# Idempotent (absent paths are fine), cwd-independent (the output dir is made
# absolute so it resolves against the repo, not the caller's cwd), dry-run-aware
# (DRY_RUN=true prints what would be removed and deletes nothing), and guarded:
# an empty, "/", or the repo-root path is refused rather than `rm -rf`'d.
cmd_clean() {
  lib_banner "clean" "Remove local security artifacts"
  _bootstrap
  _prep_scan_env

  # _prep_scan_env resolves SEC_OUTPUT_DIR via resolve_sec_output_dir, which may
  # leave it relative ("output/security"); make it absolute so the removal is
  # cwd-independent and resolves against the repo, not the caller's cwd.
  resolve_sec_output_dir
  local sec_dir
  if [[ "${SEC_OUTPUT_DIR}" == /* ]]; then
    sec_dir="${SEC_OUTPUT_DIR%/}"
  else
    sec_dir="${REPO_ROOT%/}/${SEC_OUTPUT_DIR%/}"
  fi

  local -a targets=()
  [[ -n "$sec_dir" ]] && targets+=("$sec_dir")
  [[ -f "${REPO_ROOT%/}/scan.env" ]] && targets+=("${REPO_ROOT%/}/scan.env")

  (( ${#targets[@]} > 0 )) || { success "nothing to clean (no local security artifacts found)"; return 0; }

  local removed=0 would=0 failed=0 t
  for t in "${targets[@]}"; do
    # Refuse empty / root / repo-root targets — defense against a mis-resolved
    # `rm -rf` taking something far bigger than the scan artifacts.
    if [[ -z "$t" || "$t" == "/" || "$t" == "${REPO_ROOT%/}" ]]; then
      warn "refusing to clean unsafe path: '${t:-<empty>}'"
      continue
    fi
    if [[ ! -e "$t" && ! -L "$t" ]]; then
      log "absent    ${t} (nothing to remove)"
      continue
    fi
    if [[ "$DRY_RUN" == "true" ]]; then
      log "would-remove ${t}"
      would=$((would + 1))
      continue
    fi
    if rm -rf -- "$t"; then
      log "removed    ${t}"
      removed=$((removed + 1))
    else
      error "failed to remove ${t}"
      failed=$((failed + 1))
    fi
  done

  if [[ "$DRY_RUN" == "true" ]]; then
    success "dry-run: would remove ${would} path(s) (nothing removed)"
    return 0
  fi
  (( failed > 0 )) && die "clean: ${failed} path(s) failed to remove"
  if (( removed > 0 )); then
    success "removed ${removed} path(s)"
  else
    # Every target was already absent — running `clean` on an empty slate.
    success "already clean (no local security artifacts present)"
  fi
  return 0
}

# ---------- unimplemented subcommands ----------------------------------------

# _not_implemented CMD — a latent fallback for any subcommand that has no body;
# say so clearly and stop non-zero so a caller (or CI) sees it as a real failure
# rather than a silent no-op. (Every subcommand this template ships is
# implemented; this guards against a future dispatcher entry with no body.)
_not_implemented() {
  die "'$1' is not implemented in this template."
}

# ---------- usage ------------------------------------------------------------

usage() {
  cat <<'USAGE'
Usage: devsec.sh <subcommand> [args...]

Local runner for the project's CI supply-chain security steps.
The CI scripts under infra/security/*.sh are never edited; all local
adaptation lives under infra/security/local/ (this file included).

Subcommands:
  bootstrap      Bootstrap local tooling / credentials
  build          Build the project (local stand-in for the CI build job)
  tools          Install / check security tooling
  sbom           Generate the SBOM
  vuln           Scan for vulnerabilities
  osv            Scan for known-CVE SCA + malicious (OSV-Scanner)
  malware-heuristic  Scan direct deps for typosquats (GuardDog)
  base-images    Verify the distroless bases' signatures (cosign)
  iac            Scan IaC for misconfiguration (Checkov)
  k8s            Scan K8s workloads for posture (Kubescape)
  dast           Dynamic application scan (OWASP ZAP baseline)
  license        Scan for license compliance
  vex            Generate / emit VEX
  sbomqs         Score SBOM quality + NTIA compliance (sbomqs) — report-only (extra args pass through)
  sbom-utility   Validate the SBOM schema (CycloneDX sbom-utility) — report-only (extra args pass through)
  audit          Run the security audit
  ci-hardening   Check CI supply-chain hardening
  all            Run every step
  clean          Remove local security artifacts
  help           Show this help

Environment (derived from git when unset):
  GITHUB_SHA GIT_SHORT_SHA GITHUB_REF_NAME GITHUB_REPOSITORY GITHUB_SERVER_URL

Local build coordinates (.env.local may override REGISTRY / GITHUB_REPOSITORY):
  LOCAL_REGISTRY LOCAL_PROJECT LOCAL_TAG LOCAL_IMAGE_REPO

Verbose (local, opt-in):
  DEVSEC_VERBOSE=1   After sbom/vuln, surface the scanners' native per-finding /
                     per-component detail — rendered from the artifacts the scan
                     just wrote (never a re-scan, so it matches CI exactly).

USAGE
}

# ---------- dispatch ---------------------------------------------------------

main() {
  local cmd="${1:-help}"
  case "$cmd" in
    help|-h|--help) lib_banner "devsec" "Local supply-chain security runner (the CI scans on your laptop)"
                    usage ;;
    bootstrap)      _bootstrap ;;
    build)          cmd_build ;;
    tools)          cmd_tools ;;
    sbom)           cmd_sbom         || exit $? ;;
    vuln)           cmd_vuln         || exit $? ;;
    osv)            cmd_osv          || exit $? ;;
    malware-heuristic) cmd_malware_heuristic || exit $? ;;
    base-images)    cmd_base_images  || exit $? ;;
    iac)            cmd_iac          || exit $? ;;
    k8s)            cmd_k8s          || exit $? ;;
    dast)           cmd_dast         || exit $? ;;
    license)        cmd_license      || exit $? ;;
    vex)            cmd_vex          || exit $? ;;
    sbomqs)         cmd_sbomqs       "${@:2}" || exit $? ;;
    sbom-utility)   cmd_sbom_utility "${@:2}" || exit $? ;;
    audit)          cmd_audit        || exit $? ;;
    ci-hardening)   cmd_ci_hardening || exit $? ;;
    all)            cmd_all || exit $? ;;
    clean)          cmd_clean || exit $? ;;
    _selftest_env)  _selftest_env ;;
    *)              die "unknown subcommand: $cmd (try 'devsec.sh help')" ;;
  esac
}

# Source-safe: only dispatch when executed directly, so bats can `source` this
# file to unit-test derive_ci_env without triggering the dispatcher.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
