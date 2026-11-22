#!/usr/bin/env bash
# chart-predates-bootstrap.sh — does a chart still render its namespaces or the app's Role and
# RoleBinding itself (the model before the admin bootstrap)? Decided from the chart's own render
# (its values.yaml and, when present, its values.local.yaml), never from a commit id. The kind job asks
# it about a pull request's base chart: such a base is installed as cluster admin, its own model, and
# then moved with scripts/migrate-to-bootstrap.sh.
#
# Usage: chart-predates-bootstrap.sh <chart-dir> [helm template args...]
# Exit: 0 it predates the bootstrap; 1 it does not; 2 the chart does not render. Needs helm.
set -Eeuo pipefail

[[ $# -ge 1 && -f "$1/Chart.yaml" ]] || { echo "usage: chart-predates-bootstrap.sh <chart-dir> [helm args...]" >&2; exit 2; }
chart="$1"; shift
args=()
[[ ! -f "$chart/values.local.yaml" ]] || args+=(-f "$chart/values.local.yaml")
if ! render="$(helm template probe "$chart" -n probe "${args[@]}" "$@" 2>&1)"; then
  echo "$render" >&2
  exit 2
fi
if grep -Eqx 'kind: (Namespace|Role|RoleBinding)' <<<"$render"; then
  echo "$chart predates the bootstrap: it renders Namespace, Role or RoleBinding objects"
  exit 0
fi
echo "$chart renders no Namespace, Role or RoleBinding"
exit 1
