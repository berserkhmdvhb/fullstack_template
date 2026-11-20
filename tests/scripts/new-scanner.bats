#!/usr/bin/env bats
# The scanner scaffolder prints GitHub Actions wiring and refuses names the security workflow already uses.

setup() {
  REPO="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  SCRIPT="$REPO/agent/skills/add-supply-chain-scanner/scripts/new-scanner.sh"
  WORK="$BATS_TEST_TMPDIR/repo"
  mkdir -p "$WORK/.github/workflows"
  cp "$REPO/.github/workflows/security.yml" "$WORK/.github/workflows/"
}

@test "a new advisory scanner prints its matrix leg and a case line ending in ;;" {
  run bash "$SCRIPT" demo --repo "$WORK" --display "demo"
  [ "$status" -eq 0 ]
  [[ "$output" == *"- id: demo"* ]]
  [[ "$output" == *'demo) bash infra/security/tools-install.sh demo'*' ;;'* ]]
}

@test "a pip scanner keeps the REPLACE note off the case line" {
  run bash "$SCRIPT" demo --repo "$WORK" --delivery pip
  [ "$status" -eq 0 ]
  line="$(grep -E '^ +demo\) ' <<<"$output")"
  [[ "$line" == *' ;;' ]]
  [[ "$line" != *'#'* ]]
}

@test "a pip gate job installs yq outside the workspace" {
  run bash "$SCRIPT" demo --repo "$WORK" --delivery pip --gate
  [ "$status" -eq 0 ]
  # shellcheck disable=SC2016  # the single quotes are deliberate: the assertion matches literal text
  [[ "$output" == *'export TOOLS_DIR="${RUNNER_TEMP}/tools"'* ]]
}

@test "names already used in security.yml are refused" {
  for name in pip-audit gitleaks osv kubescape codeql advisory osv-scanner; do
    run bash "$SCRIPT" "$name" --repo "$WORK"
    [ "$status" -eq 2 ]
    [[ "$output" == *"already used"* ]]
  done
  [ ! -e "$WORK/infra/security/local/scan-pip-audit.sh" ]
}
