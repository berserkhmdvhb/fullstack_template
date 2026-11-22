#!/usr/bin/env bash
# rules-test.sh — check the chart's alert and recording rules with the pinned promtool, offline.
#
# Renders the chart with the rules on in both monitoring flavours (prometheus-operator: the
# PrometheusRule; gmp: the Rules and the ClusterRules), extracts every spec.groups into one Prometheus
# rule file per flavour and runs `promtool check rules` on it. Then runs `promtool test rules` on each
# unit-test file in <chart>/tests/rules (*.test.yaml, whose rule_files is `rules.yaml`) once per
# flavour, against that flavour's file. The render is release `svc` in namespace `app`, which the
# test files' series use.
#
# Usage: scripts/ci/rules-test.sh. Needs helm, promtool and yq:
# scripts/tools.sh install helm4 promtool yq. Env: CHART (default charts/your-service-name).
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHART="${CHART:-$ROOT/charts/your-service-name}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

die() { echo "rules-test: $*" >&2; exit 1; }

for tool in helm promtool yq; do
  command -v "$tool" >/dev/null || die "$tool is required (scripts/tools.sh install helm4 promtool yq)"
done
shopt -s nullglob
tests=("$CHART"/tests/rules/*.test.yaml)
(( ${#tests[@]} )) || die "no rule unit tests in $CHART/tests/rules"

# extract FLAVOUR KINDS HELM-ARGS...: render, keep the spec.groups of the KINDS (a yq condition on .kind).
extract() {
  local flavour="$1" kinds="$2" dir="$WORK/$1"
  shift 2
  mkdir -p "$dir"
  helm template svc "$CHART" -n app --set monitoring.prometheusRule=true "$@" > "$dir/render.yaml" \
    || die "$flavour: the render failed"
  yq ea "[select($kinds) | .spec.groups[]] | {\"groups\": .}" "$dir/render.yaml" > "$dir/rules.yaml"
  [[ "$(yq '.groups | length' "$dir/rules.yaml")" -gt 0 ]] || die "$flavour: the render holds no rule group"
  echo "== $flavour: promtool check rules"
  promtool check rules "$dir/rules.yaml"
  cp "${tests[@]}" "$dir/"
  echo "== $flavour: promtool test rules"
  (cd "$dir" && promtool test rules ./*.test.yaml)
}

extract prometheus-operator '.kind == "PrometheusRule"'
extract gmp '.kind == "Rules" or .kind == "ClusterRules"' \
  --set monitoring.flavour=gmp --set monitoring.gmp.clusterRules.enabled=true
echo "rules-test: the rules of both flavours pass promtool check and ${#tests[@]} unit-test files"
