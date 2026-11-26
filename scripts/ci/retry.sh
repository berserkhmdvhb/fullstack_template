#!/usr/bin/env bash
# retry.sh — run one command with a bounded retry, for the network-bound steps of the workflows.
#
# Each attempt is cut off after TIMEOUT seconds, then killed 10 s later if it ignores the signal (a stalled download then retries instead of using up
# the step's budget); the command runs again after DELAY seconds, up to ATTEMPTS runs in all. Every
# failed attempt prints its number and exit status; the last failure's status is the script's.
# Retry only an idempotent command.
# Hazard: a retried command that starts children through sudo can leave them running past the timeout (sudo gives them
# their own session); scripts/ci/playwright-deps.sh is the pattern: make the child wait or bound itself.
#
# Usage: retry.sh ATTEMPTS DELAY TIMEOUT COMMAND [ARG...]
set -uo pipefail

(( $# >= 4 )) || { echo "usage: retry.sh ATTEMPTS DELAY TIMEOUT COMMAND [ARG...]" >&2; exit 2; }
attempts="$1" delay="$2" limit="$3"
for v in "${attempts}" "${delay}" "${limit}"; do
  [[ "${v}" =~ ^[0-9]+$ ]] || { echo "retry.sh: ATTEMPTS, DELAY and TIMEOUT must be whole numbers, got '${v}'" >&2; exit 2; }
done
(( attempts >= 1 )) || { echo "retry.sh: ATTEMPTS must be at least 1" >&2; exit 2; }
shift 3

for (( n = 1; n <= attempts; n++ )); do
  timeout -k 10 "${limit}" "$@" && exit 0
  rc=$?
  echo "::warning::attempt ${n}/${attempts} of '$*' failed with status ${rc}" >&2
  (( n < attempts )) && sleep "${delay}"
done
echo "::error::'$*' failed after ${attempts} attempts" >&2
exit "${rc}"
