#!/usr/bin/env bash
# Stub docker for the image-checks bats tests: log the call to $STUB_LOG, answer from STUB_* variables.
# STUB_FAIL is a space-separated list of calls that fail: user hctest hctimes labels create export run state.
echo "$*" >> "${STUB_LOG:-/dev/null}"
fails() { [[ " ${STUB_FAIL-} " == *" $1 "* ]] && { echo "stub docker: $1 failed" >&2; return 0; } || return 1; }
case "$1 $2" in
  "image inspect")
    case "$*" in
      *Config.User*) fails user && exit 1; printf '%s\n' "${STUB_USER-10001}" ;;
      *Healthcheck.Test*) fails hctest && exit 1; printf '%s\n' "${STUB_HC_TEST-[CMD /probe]}" ;;
      *StartPeriod*) fails hctimes && exit 1; printf '%s\n' "${STUB_HC_TIMES-0 1000000000 1000000000 1}" ;;
      *Labels*)
        fails labels && exit 1
        key="$(sed -n 's/.*Labels "org.opencontainers.image.\([a-z.]*\)".*/\1/p' <<<"$*")"
        var="STUB_LABEL_${key^^}"
        default="x-$key"
        printf '%s\n' "${!var-$default}" ;;
      *) echo "stub docker: unexpected: $*" >&2; exit 99 ;;
    esac ;;
  "create --name") fails create && exit 1; echo "created" ;;
  "export "*)
    fails export && exit 1
    if [[ -n "${STUB_TAR-}" ]]; then cat "$STUB_TAR"; else tar -cT /dev/null; fi ;;
  "rm -f") ;;
  "rmi "*) ;;
  "buildx build") ;;
  "run -d") fails run && exit 1; echo "started" ;;
  "inspect --format")
    case "$*" in
      *Health.Log*) echo "1 probe refused" ;;
      *)
        fails state && exit 1
        if [[ -z "${STUB_STATES-}" ]]; then echo "running healthy"; exit 0; fi
        read -r line < "$STUB_STATES"
        # consume one line per poll while more remain
        if [ "$(wc -l < "$STUB_STATES")" -gt 1 ]; then sed -i 1d "$STUB_STATES"; fi
        echo "$line" ;;
    esac ;;
  "logs --tail") echo "app log line" ;;
  *) echo "stub docker: unexpected: $*" >&2; exit 99 ;;
esac
