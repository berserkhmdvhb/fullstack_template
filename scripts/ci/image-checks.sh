#!/usr/bin/env bash
# image-checks.sh — structure checks for one built image (backs the `images` job in ci.yml and the
# `image-checks` job in reusable-build.yml; `make image-checks` runs it locally).
#
# Usage: image-checks.sh [--strict] <image-id> <image-ref>
#   <image-id>   an `id` in .github/container-images.yml, the one source of the image's settings
#                (`distroless`, and the optional `run_env` and `run_args` of the health-check container)
#   <image-ref>  what docker can resolve: a local tag, or <repository>@sha256:<digest>
#   --strict     labels must not be `unknown`, and `source` must not be the template placeholder
#
# Checks (every failure prints an ::error:: line; all checks run, then the exit status is 1 on any):
#   user         Config.User is numeric and not 0
#   no shell     distroless images only: no shell, package manager or fetcher in the filesystem, read
#                from `docker export` (nothing in the image is executed to find out)
#   healthcheck  the image declares one, and a throwaway container becomes healthy within the bound
#                start period + interval x retries + timeout, read from the image
#   labels       the OCI source, version, revision, created and title labels are present and non-empty
#
# Environment: IMAGE_CHECKS_MANIFEST (manifest path), IMAGE_CHECKS_POLL (seconds between health polls).
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PATH="$root/.tools/bin:$PATH"

USER_RE='^[1-9][0-9]*(:[0-9]+)?$'
FORBIDDEN='sh|bash|ash|dash|zsh|busybox|apt|apt-get|dpkg|apk|pip|pip3|npm|npx|curl|wget'
BIN_DIRS='bin|sbin|usr/bin|usr/sbin|usr/local/bin|usr/local/sbin'
LABELS=(source version revision created title)
NS=1000000000

failed=0
containers=()

fail() {
  echo "::error::image-checks (${id:-?}): $*"
  failed=1
}

cleanup() {
  local name
  for name in "${containers[@]}"; do
    docker rm -f "$name" > /dev/null 2>&1 || true
  done
}

# wait_bound START INTERVAL TIMEOUT RETRIES — the longest a container may take to become healthy, in
# whole seconds (the first three in nanoseconds, as docker inspect prints them). A zero value takes
# Docker's default: interval 30s, timeout 30s, retries 3.
wait_bound() {
  local start=$1 interval=$2 timeout=$3 retries=$4
  ((interval > 0)) || interval=$((30 * NS))
  ((timeout > 0)) || timeout=$((30 * NS))
  ((retries > 0)) || retries=3
  echo $(((start + interval * retries + timeout + NS - 1) / NS))
}

manifest_get() { # manifest_get JQ-EXPRESSION — evaluated on the image entry with id $id
  ID="$id" yq -r ".images[] | select(.id == strenv(ID)) | $1" "$manifest"
}

check_user() {
  local user
  user="$(docker image inspect --format '{{.Config.User}}' "$ref")" || { fail "docker image inspect failed reading the user of ${ref}"; return; }
  if [[ "$user" =~ $USER_RE ]]; then
    echo "user: ok (${user})"
  else
    fail "user is '${user}'; the image must run as a numeric, non-zero user (USER 10001)"
  fi
}

check_no_shell() {
  if [[ "$(manifest_get '.distroless')" != "true" ]]; then
    echo "no shell: skipped (not a distroless image)"
    return
  fi
  local name="image-checks-fs-${id}-$$-${RANDOM}" found listing
  containers+=("$name")
  docker create --name "$name" "$ref" > /dev/null || { fail "docker create failed for ${ref}"; return; }
  listing="$(docker export "$name" | tar -t)" || { fail "docker export failed for ${ref}"; return; }
  docker rm -f "$name" > /dev/null || true
  found="$(sed 's|^\./||; s|/$||' <<<"$listing" | grep -E "^(${BIN_DIRS})/(${FORBIDDEN})$" || true)"
  if [[ -n "$found" ]]; then
    fail "a distroless image must have no shell, package manager or fetcher; found: /${found//$'\n'/ /}"
  else
    echo "no shell: ok"
  fi
}

show_diagnostics() {
  echo "--- last health log entries"
  docker inspect --format '{{range .State.Health.Log}}{{.ExitCode}} {{.Output}}{{"\n"}}{{end}}' "$1" | tail -n 5 || true
  echo "--- last container log lines"
  docker logs --tail 20 "$1" 2>&1 || true
}

check_healthcheck() {
  local test times start interval timeout retries bound
  test="$(docker image inspect --format '{{if .Config.Healthcheck}}{{.Config.Healthcheck.Test}}{{end}}' "$ref")" \
    || { fail "docker image inspect failed reading the HEALTHCHECK of ${ref}"; return; }
  if [[ -z "$test" || "$test" == "[NONE]" ]]; then
    fail "the image declares no HEALTHCHECK"
    return
  fi
  times="$(docker image inspect --format '{{json .Config.Healthcheck.StartPeriod}} {{json .Config.Healthcheck.Interval}} {{json .Config.Healthcheck.Timeout}} {{json .Config.Healthcheck.Retries}}' "$ref")" \
    || { fail "docker image inspect failed reading the HEALTHCHECK timings of ${ref}"; return; }
  read -r start interval timeout retries <<<"$times"
  bound="$(wait_bound "$start" "$interval" "$timeout" "$retries")"

  local name="image-checks-${id}-$$-${RANDOM}" args=() line state
  containers+=("$name")
  while IFS= read -r line; do [[ -z "$line" ]] || args+=(-e "$line"); done \
    < <(manifest_get '(.run_env // {}) | to_entries | .[] | .key + "=" + .value')
  while IFS= read -r line; do [[ -z "$line" ]] || args+=("$line"); done \
    < <(manifest_get '(.run_args // []) | .[]')
  docker run -d --name "$name" "${args[@]}" "$ref" > /dev/null || { fail "docker run failed for ${ref}"; return; }

  SECONDS=0
  while true; do
    state="$(docker inspect --format '{{.State.Status}} {{.State.Health.Status}}' "$name")" \
      || { fail "docker inspect failed for the health-check container of ${ref}"; return; }
    case "$state" in
      *" healthy")
        echo "healthcheck: ok (healthy after ${SECONDS}s, bound ${bound}s)"
        return ;;
      exited* | dead*)
        fail "the container exited before it became healthy (${state%% *})"
        show_diagnostics "$name"
        return ;;
      *" unhealthy")
        fail "the container became unhealthy"
        show_diagnostics "$name"
        return ;;
    esac
    if ((SECONDS >= bound)); then
      fail "the container was not healthy within ${bound}s (start period + interval x retries + timeout); state: ${state}"
      show_diagnostics "$name"
      return
    fi
    sleep "${IMAGE_CHECKS_POLL:-1}"
  done
}

check_labels() {
  local placeholder="" label value bad=0
  if ((strict)); then
    placeholder="$(python3 -I -c 'import sys; sys.path.insert(0, sys.argv[1]); import adopt_stamp; print(adopt_stamp.OLD_SOURCE_URL)' "$root/infra/adapt")" \
      || { fail "cannot read the placeholder source URL (OLD_SOURCE_URL) from infra/adapt/adopt_stamp.py"; return; }
  fi
  for label in "${LABELS[@]}"; do
    value="$(docker image inspect --format "{{index .Config.Labels \"org.opencontainers.image.${label}\"}}" "$ref")" \
      || { fail "docker image inspect failed reading the labels of ${ref}"; return; }
    if [[ -z "$value" ]]; then
      fail "label org.opencontainers.image.${label} is missing or empty"
      bad=1
    elif ((strict)) && [[ "$value" == "unknown" ]]; then
      fail "label org.opencontainers.image.${label} is 'unknown' (strict): pass the build argument"
      bad=1
    elif ((strict)) && [[ "$label" == "source" && "$value" == "$placeholder" ]]; then
      fail "label org.opencontainers.image.source is the template placeholder (strict): pass SOURCE_URL"
      bad=1
    fi
  done
  ((bad)) || echo "labels: ok"
}

main() {
  strict=0
  if [[ "${1:-}" == "--strict" ]]; then
    strict=1
    shift
  fi
  if (($# != 2)); then
    echo "usage: image-checks.sh [--strict] <image-id> <image-ref>" >&2
    exit 2
  fi
  id="$1"
  ref="$2"
  manifest="${IMAGE_CHECKS_MANIFEST:-$root/.github/container-images.yml}"
  if [[ "$(ID="$id" yq -r '[.images[] | select(.id == strenv(ID))] | length' "$manifest")" != "1" ]]; then
    fail "no image '${id}' in ${manifest}"
    exit 1
  fi
  trap cleanup EXIT
  echo "image-checks: ${id} (${ref})$( ((strict)) && echo ' [strict]')"
  check_user
  check_no_shell
  check_healthcheck
  check_labels
  exit "$failed"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
