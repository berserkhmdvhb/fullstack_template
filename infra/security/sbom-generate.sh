#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Portable SBOM Generation (image-first, config-driven, language-agnostic)
#
# Generates SBOMs from:
#   - Container images (PRIMARY, production source of truth). Each image is
#     scanned as the IMMUTABLE artifact that ships: when an immutable digest is
#     available (image-digests.env, from verify:registry) the scan targets
#     `registry:repo@sha256:…`; else a local-daemon ref (`docker:…`); else the
#     registry tag. Precedence is digest > daemon > registry-tag.
#   - Project directories (optional, generic)
#   - Repository root (optional, debug only)
#
# scan.env's `*_IMAGE` is left as `repo:tag` (VEX / provenance / deploy parse it
# that way); the digest/daemon resolution below is assembled here and never
# written back.
#
# Behavior controlled via config.yaml + CLI overrides.
#
# Outputs under ${SEC_OUTPUT_DIR}:
#   sbom/*.sbom.<format>.json
#   logs/syft-*.log
#
# Requirements: syft, jq, yq
# -----------------------------------------------------------------------------
set -Eeuo pipefail

# ---------- Shared helpers ----------
# shellcheck source=/dev/null
source "$(dirname "$0")/_helpers.sh"
trap 'trap_err' ERR

# ---------- Defaults ----------
ROOT_DIR="${ROOT_DIR:-.}"

resolve_sec_output_dir

OUT_SBOM_DIR="${SEC_OUTPUT_DIR}/sbom"
OUT_LOG_DIR="${SEC_OUTPUT_DIR}/logs"

SBOM_FORMAT="${SBOM_FORMAT:-$(cfg '.sbom.format')}"
[[ -z "$SBOM_FORMAT" ]] && SBOM_FORMAT="cyclonedx-json"

if [[ -n "${SBOM_PROJECT_PATHS:-}" ]]; then
  IFS=', ' read -r -a PROJECTS <<< "$SBOM_PROJECT_PATHS"
else
  mapfile -t PROJECTS < <(cfg '.paths.projects[]')
fi

SBOM_ENABLE_ROOT="${SBOM_ENABLE_ROOT:-$(cfg '.sbom.enable.root')}"
SBOM_ENABLE_PROJECT="${SBOM_ENABLE_PROJECT:-$(cfg '.sbom.enable.project')}"
SBOM_ENABLE_IMAGE="${SBOM_ENABLE_IMAGE:-$(cfg '.sbom.enable.image')}"

[[ -z "$SBOM_ENABLE_ROOT" ]] && SBOM_ENABLE_ROOT="false"
[[ -z "$SBOM_ENABLE_PROJECT" ]] && SBOM_ENABLE_PROJECT="false"
[[ -z "$SBOM_ENABLE_IMAGE" ]] && SBOM_ENABLE_IMAGE="true"

SCAN_ENV_PATH="${SCAN_ENV_PATH:-$(cfg '.image.env_file')}"
[[ -z "$SCAN_ENV_PATH" ]] && SCAN_ENV_PATH="scan.env"

# Immutable image digests (`<ID>_IMAGE_DIGEST=sha256:…`), captured by
# verify:registry. Present as an artifact in CI; absent locally (then the scan
# falls back to the local daemon / registry tag). Config-driven, overridable.
DIGEST_ENV="${DIGEST_ENV:-$(cfg '.image.digest_file')}"
[[ -z "$DIGEST_ENV" ]] && DIGEST_ENV="image-digests.env"

QUIET=false
VERBOSE=false

export SYFT_CHECK_FOR_APP_UPDATE="${SYFT_CHECK_FOR_APP_UPDATE:-false}"

case "$SBOM_FORMAT" in
  cyclonedx-json) SBOM_EXT="cdx" ;;
  spdx-json)      SBOM_EXT="spdx" ;;
  syft-json)      SBOM_EXT="syft" ;;
  *) die "Unsupported SBOM format: $SBOM_FORMAT" ;;
esac

# ---------- Usage ----------
usage() {
  cat <<'USAGE'
Usage: sbom-generate.sh [-R ROOT] [-p PROJECT_PATHS] [-O OUTPUT_DIR]
                        [-F FORMAT] [-s scan.env] [-d DIGEST_ENV] [-q] [-v] [-h]

Description:
  Generate SBOMs from:
    - Container images (PRIMARY, production source of truth)
    - Project directories (optional, language-agnostic)
    - Repository root (optional, debug)

Options:
  -R  Root directory (default: .)
  -p  Project paths (CSV/space-separated)
  -O  Output directory (default: output/security)
  -F  SBOM format (cyclonedx-json|spdx-json|syft-json)
  -s  scan.env path (default: scan.env)
  -d  image-digests.env path (default: image-digests.env)
  -q  Quiet
  -v  Verbose
  -h  Help

Environment:
  CONFIG_FILE            YAML config file
  SBOM_ENABLE_IMAGE      true/false
  SBOM_ENABLE_PROJECT    true/false
  SBOM_ENABLE_ROOT       true/false
  SBOM_PROJECT_PATHS     project paths
  SEC_OUTPUT_DIR         output base

Outputs:
  sbom/*.image.sbom.*.json
  sbom/*.project.sbom.*.json
  sbom/root.sbom.*.json
  sbom/index.json

USAGE
  exit 0
}

lib_banner "sbom-generate" "Portable SBOM generation (image-first, config-driven)"

# ---------- CLI ----------
while getopts ":R:p:O:F:s:d:qvh" opt; do
  case "$opt" in
    R) ROOT_DIR="$OPTARG" ;;
    p) SBOM_PROJECT_PATHS="$OPTARG" ;;
    O) SEC_OUTPUT_DIR="$OPTARG"; OUT_SBOM_DIR="$SEC_OUTPUT_DIR/sbom"; OUT_LOG_DIR="$SEC_OUTPUT_DIR/logs" ;;
    F) SBOM_FORMAT="$OPTARG" ;;
    s) SCAN_ENV_PATH="$OPTARG" ;;
    d) DIGEST_ENV="$OPTARG" ;;
    q) QUIET=true ;;
    v) VERBOSE=true ;;
    h) usage ;;
    \?) die "Invalid option -$OPTARG" ;;
    :)  die "Option -$OPTARG requires an argument" ;;
  esac
done
$VERBOSE && set -x

# ---------- Pre-flight ----------
# Use the shared runtime's need_cmd (names every missing command at once)
# instead of an inline require_cmd — this script sources _helpers.sh.
need_cmd syft jq yq

mkdir -p "$OUT_SBOM_DIR" "$OUT_LOG_DIR"
LOG_FILE="${OUT_LOG_DIR}/syft-$(date +%Y%m%d-%H%M%S).log"
: > "$LOG_FILE"

$QUIET || log "Syft: $(syft version 2>/dev/null || echo unknown)"
$QUIET || log "Logging to: $LOG_FILE"

# Mirror stderr
exec 3>&2
exec 2> >(tee -a "$LOG_FILE" >&2)

# ---------- Helpers ----------
validate_sbom_json() {
  local file="$1" fmt="$2"

# shellcheck source=/dev/null
  jq -e . "$file" >/dev/null || die "Invalid JSON: $file"

  case "$fmt" in
    cyclonedx-json) jq -e '(.bomFormat=="CycloneDX")' "$file" >/dev/null || die "Not CycloneDX: $file" ;;
    spdx-json) jq -e 'has("spdxVersion") or has("SPDXID")' "$file" >/dev/null || die "Not SPDX: $file" ;;
    syft-json) jq -e 'has("artifacts") or has("schema")' "$file" >/dev/null || die "Not Syft: $file" ;;
  esac
}

run_syft_atomic() {
  local target="$1" out="$2" fmt="$3"
  local tmp="${out}.tmp"

  mkdir -p "$(dirname "$out")"

  local args=(
    "$target"
    --exclude "**/node_modules/**/yarn.lock"
    --exclude "**/node_modules/**/package-lock.json"
    --exclude "**/node_modules/**/pnpm-lock.yaml"
    --exclude "**/node_modules/**/.github/**"
    -o "$fmt"
  )

  if $QUIET; then
    SYFT_CHECK_FOR_APP_UPDATE=false syft "${args[@]}" >"$tmp" 2>>"$LOG_FILE"
  else
    SYFT_CHECK_FOR_APP_UPDATE=false syft "${args[@]}" | tee "$tmp" >/dev/null 2>>"$LOG_FILE"
  fi

  validate_sbom_json "$tmp" "$fmt"
  mv -f "$tmp" "$out"
}

sanitize() {
  local s="$1"
  [[ "$s" == "." || "$s" == "./" ]] && s="root"
  printf "%s" "$s" | sed -e 's#^\.\/##' -e 's#/#.#g' -e 's#[^A-Za-z0-9_.-]#-#g'
}

# resolve_image_target REF DIGEST DAEMON_REF -> the syft source target to scan.
#
# The scheme travels with the result, so the scanner never has to guess:
#   - digest present  -> registry:repo@sha256:…  (scan exactly what ships)
#   - daemon ref set  -> <daemon_ref>             (docker:… local daemon, as-is)
#   - registry tag    -> registry:repo:tag        (bare tag -> registry:)
#                        or the ref unchanged if it already carries docker:/registry:
#
# scan.env's *_IMAGE stays repo:tag for the downstream consumers (VEX / attested
# provenance / digest-pinned deploy); only this assembled target is qualified.
resolve_image_target() {
  local ref="$1" digest="$2" daemon_ref="$3"

  # 1) Immutable digest — scan the exact artifact the deploy pins (repo@sha256:…).
  if [[ -n "$digest" && "$digest" == sha256:* && -n "$ref" ]]; then
    printf 'registry:%s@%s' "${ref%:*}" "$digest"
    return 0
  fi
  # 2) Local Docker daemon — developer laptop, no registry push required.
  if [[ -n "$daemon_ref" ]]; then
    printf '%s' "$daemon_ref"
    return 0
  fi
  # 3) Registry tag (ref may already carry a docker:/registry: scheme).
  if [[ -n "$ref" ]]; then
    case "$ref" in
      docker:*|registry:*) printf '%s' "$ref" ;;
      *) printf 'registry:%s' "$ref" ;;
    esac
    return 0
  fi
  return 1
}

# Resolve each configured service to a syft target (see resolve_image_target),
# reading refs from scan.env, immutable digests from image-digests.env, and the
# optional local-daemon ref from config's docker_env_var. Falls back to
# image-ref.txt for any service whose *_IMAGE is empty.
resolve_images_from_env() {
  local envp="${SCAN_ENV_PATH}" digestenv="${DIGEST_ENV}"
  declare -gA IMAGE_MAP=()

  # Load image refs, then immutable digests. Both are CI-generated KEY=VALUE
  # dotenvs (no shell code); sourcing matches generate-vex.sh's handling of the
  # same files. image-digests.env is absent locally — the digest branch then
  # falls through to the daemon / registry-tag, which is the intended local path.
  # shellcheck source=/dev/null
  if [[ -f "$envp" ]]; then set -a; . "$envp"; set +a; fi
# shellcheck source=/dev/null
  if [[ -f "$digestenv" ]]; then set -a; . "$digestenv"; set +a; fi

  mapfile -t SERVICES < <(yq -r '.image.services[].name' "$CONFIG_FILE")

  # Primary resolution from env vars (digest > daemon > registry-tag).
  local svc env_var docker_var digest_var ref digest daemon_ref match target
  for svc in "${SERVICES[@]}"; do
    env_var="$(yq -r ".image.services[] | select(.name==\"$svc\") | .env_var" "$CONFIG_FILE")"
    docker_var="$(yq -r ".image.services[] | select(.name==\"$svc\") | .docker_env_var" "$CONFIG_FILE")"
    digest_var="${svc^^}_IMAGE_DIGEST"

    ref="${!env_var:-}"
    digest="${!digest_var:-}"
    daemon_ref="${!docker_var:-}"

    # `|| target=` keeps this set -e-safe: a service with nothing resolved yields
    # "" (skipped by generate_image_sbom) rather than aborting the whole run.
    target="$(resolve_image_target "$ref" "$digest" "$daemon_ref")" || target=""
    IMAGE_MAP["$svc"]="$target"
  done

  # Fallback: image-ref.txt for any service still unresolved.
  if [[ -f "image-ref.txt" ]]; then
    while IFS= read -r ref; do
      [[ -z "$ref" ]] && continue
      for svc in "${SERVICES[@]}"; do
        [[ -n "${IMAGE_MAP[$svc]}" ]] && continue

        match="$(yq -r ".image.services[] | select(.name==\"$svc\") | .match // \"\"" "$CONFIG_FILE")"

        if [[ -n "$match" && "$ref" == *"$match":* ]]; then
          IMAGE_MAP["$svc"]="$(resolve_image_target "$ref" "" "")"
        fi
      done
    done < <(sed '/^[[:space:]]*$/d' image-ref.txt)
  fi

  # Verify at least one image resolved
  shopt -s nullglob
  for svc in "${SERVICES[@]}"; do
    [[ -n "${IMAGE_MAP[$svc]}" ]] && return 0
  done

  return 1
}

# ---------- Main ----------
$QUIET || log "Starting SBOM generation (image-first mode)…"

# --------------------------
# 1) IMAGE SBOMs (PRIMARY)
# --------------------------
if [[ "${SBOM_ENABLE_IMAGE,,}" == "true" ]]; then
  if resolve_images_from_env; then

    generate_image_sbom() {
      local name="$1"
      local target="$2"

      if [[ -z "$target" ]]; then
        warn "No ${name} image resolved (skipped)"
        return 0
      fi

      local out="${OUT_SBOM_DIR}/${name}.image.sbom.${SBOM_EXT}.json"
      $QUIET || log "Generating IMAGE SBOM (${name}) -> $(basename "$out") [${target}]"

      # `target` already carries its syft scheme (registry: / docker:), resolved
      # by resolve_image_target — do NOT re-prefix, or a docker: daemon ref
      # becomes the unparseable registry:docker:…
      run_syft_atomic "$target" "$out" "$SBOM_FORMAT"
    }
    for svc in $(printf "%s\n" "${!IMAGE_MAP[@]}" | LC_ALL=C sort); do
      generate_image_sbom "$svc" "${IMAGE_MAP[$svc]}"
    done

  else
    if [[ "${SBOM_ENABLE_PROJECT,,}" == "true" ]]; then
      warn "No images found, continuing with project scan only"
    else
      die "No runtime images resolved from scan.env or image-ref.txt"
    fi
  fi
fi

# --------------------------
# 2) PROJECT SBOMs (OPTIONAL, GENERIC)
# --------------------------

if [[ "${SBOM_ENABLE_PROJECT,,}" == "true" && ${#PROJECTS[@]} -eq 0 ]]; then
  warn "No project paths configured"
fi

if [[ "${SBOM_ENABLE_PROJECT,,}" == "true" ]]; then
  for d in "${PROJECTS[@]}"; do
    [[ -z "$d" ]] && continue

    if [[ -d "$d" ]]; then
      base="$(sanitize "$d")"

      OUT="${OUT_SBOM_DIR}/${base}.project.sbom.${SBOM_EXT}.json"
      $QUIET || log "Generating PROJECT SBOM -> $(basename "$OUT")"

      run_syft_atomic "dir:${d}" "$OUT" "$SBOM_FORMAT"
    else
      warn "Project path not found: $d (skipped)"
    fi
  done
fi

# --------------------------
# 3) ROOT SBOM (OPTIONAL, DEBUG ONLY)
# --------------------------
if [[ "${SBOM_ENABLE_ROOT,,}" == "true" ]]; then
  if [[ -d "$ROOT_DIR" ]]; then
    ROOT_SBOM="${OUT_SBOM_DIR}/root.sbom.${SBOM_EXT}.json"
    $QUIET || log "Generating ROOT SBOM -> $(basename "$ROOT_SBOM")"

    run_syft_atomic "dir:${ROOT_DIR}" "$ROOT_SBOM" "$SBOM_FORMAT"
  else
    warn "Root path not found: $ROOT_DIR (skipped)"
  fi
fi

# --------------------------
# 4) SBOM INDEX (MANIFEST)
# --------------------------
INDEX_JSON="${OUT_SBOM_DIR}/index.json"
$QUIET || log "Generating SBOM index -> $(basename "$INDEX_JSON")"

{
  printf '{\n'
  printf '  "generatedAt": "%s",\n' "$(timestamp)"
  printf '  "format": "%s",\n' "$SBOM_FORMAT"
  printf '  "entries": [\n'

  first=true
  for f in "$OUT_SBOM_DIR"/*.json; do
    [[ "$(basename "$f")" == "index.json" ]] && continue

    $first || printf ',\n'
    first=false

    bn="$(basename "$f")"
    nm="${bn%.json}"

    printf '    {"name":"%s","path":"%s"}' "$nm" "$bn"
  done

  printf '\n  ]\n'
  printf '}\n'
} > "${INDEX_JSON}.tmp"

jq -e . "${INDEX_JSON}.tmp" >/dev/null 2>&1 || die "SBOM index validation failed"
mv -f "${INDEX_JSON}.tmp" "$INDEX_JSON"

count=$(find "$OUT_SBOM_DIR" -maxdepth 1 -type f -name '*.json' | wc -l)

success "SBOM generation complete ($count files)"

# Restore stderr
exec 2>&3