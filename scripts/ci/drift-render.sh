#!/usr/bin/env bash
# drift-render.sh — render what an environment's latest deployment applied, for the live-drift check.
#
# The deployed commit's own deploy path, with that deploy's inputs, so the render is the upgrade's:
#   - its scripts/ci/deploy-env.sh with DEPLOY_RENDER_ONLY (the upgrade's values files and flags);
#   - its deploy.yml's job constants (CHART_DIR, DEPLOY_IMAGES, the ports and API_VERSION);
#   - the deployment's ref and commit as the GitHub ref the release ran on, so its image matrix and
#     image tag (.github/scripts/container_matrix.py) come out as the release computed them;
#   - the digests the release deployed (scripts/drift_collect.py) and the release name SERVICE_NAME.
# Nothing here reads the cluster; run it before the cluster login.
#
# Env: DRIFT_ENV (dev|qa|production) DEPLOYED_DIR (a checkout of the deployed commit) DEPLOYED_REF
# (main or the v* tag) DEPLOYED_SHA DIGESTS (JSON of <ID>_IMAGE_DIGEST) SERVICE_NAME PYTHON (with
# PyYAML) OUT (the render file); WORK_DIR (RUNNER_TEMP).
# A deployed commit whose deploy-env.sh predates DEPLOY_RENDER_ONLY is not rendered: the script says so
# and writes `rendered=false` to GITHUB_OUTPUT (`rendered=true` otherwise).
set -Eeuo pipefail

fail() { echo "::error::$*" >&2; exit 1; }
for v in DRIFT_ENV DEPLOYED_DIR DEPLOYED_REF DEPLOYED_SHA DIGESTS SERVICE_NAME PYTHON OUT; do
  [[ -n "${!v:-}" ]] || fail "$v is required"
done
[[ "$DRIFT_ENV" =~ ^(dev|qa|production)$ ]] || fail "DRIFT_ENV must be dev, qa or production"
[[ "$DEPLOYED_SHA" =~ ^[0-9a-f]{40}$ ]] || fail "DEPLOYED_SHA must be a full commit id"
case "$DEPLOYED_REF" in
  main) ref_type=branch ;;
  v[0-9]*) ref_type=tag ;;
  *) fail "DEPLOYED_REF must be main or a v* tag" ;;
esac
WORK_DIR="${WORK_DIR:-${RUNNER_TEMP:-}}"
[[ -n "$WORK_DIR" ]] || fail "WORK_DIR or RUNNER_TEMP is required"
deployed="$(cd "$DEPLOYED_DIR" && pwd)"
[[ "$(git -C "$deployed" rev-parse HEAD)" == "$DEPLOYED_SHA" ]] || fail "DEPLOYED_DIR is not a checkout of $DEPLOYED_SHA"

if ! grep -q DEPLOY_RENDER_ONLY "$deployed/scripts/ci/deploy-env.sh"; then
  msg="${DRIFT_ENV}: not compared: the deployed commit's deploy script predates the drift render; the next deploy makes it comparable"
  echo "::notice::$msg"
  echo "- $msg" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
  echo "rendered=false" >> "${GITHUB_OUTPUT:-/dev/null}"
  exit 0
fi

# The deploy job's constants, from the deployed commit's deploy.yml.
constant() {
  "$PYTHON" -c 'import sys, yaml; print(yaml.safe_load(open(sys.argv[1]))["env"][sys.argv[2]])' \
    "$deployed/.github/workflows/deploy.yml" "$1"
}
# The ref the release ran on: the matrix helper reads it for the repositories and the image tag.
export GITHUB_REF_NAME="$DEPLOYED_REF" GITHUB_REF_TYPE="$ref_type" GITHUB_SHA="$DEPLOYED_SHA"
plan="$WORK_DIR/drift-plan-${DRIFT_ENV}.out"
: > "$plan"
(cd "$deployed" && GITHUB_OUTPUT="$plan" "$PYTHON" .github/scripts/container_matrix.py --matrix >/dev/null)
image_tag="$(sed -n 's/^image_tag=//p' "$plan")"
[[ -n "$image_tag" ]] || fail "cannot compute the image tag of ${DEPLOYED_REF}"

run="$WORK_DIR/drift-${DRIFT_ENV}"
mkdir -p "$run"
(
  cd "$deployed"
  DEPLOY_ENV="$DRIFT_ENV" CHART_DIR="$(constant CHART_DIR)" DEPLOY_IMAGES="$(constant DEPLOY_IMAGES)" \
    BACKEND_PORT="$(constant BACKEND_PORT)" FRONTEND_PORT="$(constant FRONTEND_PORT)" \
    API_VERSION="$(constant API_VERSION)" IMAGE_TAG="$image_tag" HELM_TIMEOUT=5m WORK_DIR="$run" \
    MATRIX_CMD="$PYTHON $deployed/.github/scripts/container_matrix.py" DEPLOY_RENDER_ONLY="$OUT" \
    bash scripts/ci/deploy-env.sh
)
echo "rendered=true" >> "${GITHUB_OUTPUT:-/dev/null}"
