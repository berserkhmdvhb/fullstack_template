#!/usr/bin/env bash
# yamllint-values.sh — yamllint the chart's values files with the config `ct lint` uses in CI
# (.github/ct-lintconf.yaml), so prek and the chart job read one file.
set -euo pipefail

mapfile -t files < <(git ls-files 'charts/*/values*.yaml')
yamllint -c .github/ct-lintconf.yaml "${files[@]}"
