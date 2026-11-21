#!/usr/bin/env bash
# yamllint-values.sh — yamllint the chart's values files with chart-testing's own lint config,
# the config `ct lint` applies, so this and CI agree. The ct version comes from tools/pins.tsv.
set -euo pipefail

mapfile -t conf < <(compgen -G ".tools/opt/ct-*/etc/lintconf.yaml" || true)
if (( ${#conf[@]} != 1 )); then
  echo "yamllint-values: ct lintconf not found; run: scripts/tools.sh install ct" >&2
  exit 1
fi
mapfile -t files < <(git ls-files 'charts/*/values*.yaml')
yamllint -c "${conf[0]}" "${files[@]}"
