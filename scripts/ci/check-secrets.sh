#!/usr/bin/env bash
# =============================================================================
# CI Check: Validate Secret Templates
# =============================================================================
# Ensures that secret template files contain only placeholder values and not
# real secrets. Fails the CI job if a real value is detected.
# =============================================================================

set -euo pipefail

echo "== Checking secret templates for real values =="

FOUND=0
FAILED=0

# Scan the repo root (secret templates live under charts/, not k8s/), pruning
# VCS and build caches. `-print0` + `while read -d ''` is whitespace-safe: it
# handles paths containing spaces or newlines (P-L4). The outer loop runs in the
# current shell (process substitution, not a pipe) so FAILED/FOUND persist.
while IFS= read -r -d '' secret_file; do
  FOUND=1
  echo "Checking: $secret_file"

  # Extract stringData values (lines between stringData: and the next top-level key)
  in_stringdata=0
  while IFS= read -r line; do
    if [[ "$line" =~ ^stringData: ]]; then
      in_stringdata=1
      continue
    fi

    if [[ $in_stringdata -eq 1 ]]; then
      # Stop at next top-level key (no leading whitespace)
      if [[ "$line" =~ ^[a-zA-Z] && ! "$line" =~ ^[[:space:]] ]]; then
        in_stringdata=0
        continue
      fi

      # Skip comment lines
      if [[ "$line" =~ ^[[:space:]]*# ]]; then
        continue
      fi

      # Check if value is a placeholder
      if [[ "$line" =~ :[[:space:]]*\"?REPLACE_WITH_ ]]; then
        # Placeholder found - OK
        :
      elif [[ "$line" =~ :[[:space:]]*$ ]]; then
        # Empty value - OK (will be filled by sealed-secrets/ESO)
        :
      elif [[ "$line" =~ :[[:space:]]*\"?[A-Za-z0-9] ]]; then
        # Real value detected!
        echo "❌ ERROR: Real value detected in $secret_file:"
        echo "   $line"
        echo "   Secrets must use placeholder pattern: REPLACE_WITH_*"
        echo "   Or use sealed-secrets/External Secrets Operator."
        FAILED=1
      fi
    fi
  done < "$secret_file"
done < <(
  find . \
    \( \
       -path ./.git -o \
       -path ./node_modules -o \
       -path ./.venv -o \
       -path ./.ruff_cache -o \
       -path ./.pytest_cache -o \
       -path ./.mypy_cache \
    \) -prune -o \
    -type f \( -name "secret.yaml" -o -name "secret.yml" \) -print0
)

if [[ "$FOUND" -eq 0 ]]; then
  echo "No secret files found. Skipping check."
  exit 0
fi

if [[ $FAILED -eq 1 ]]; then
  echo ""
  echo "❌ Secret validation FAILED. Real values detected in secret templates."
  echo "   Please use placeholder values (REPLACE_WITH_*) or sealed-secrets/ESO."
  exit 1
fi

echo "✅ All secret templates contain only placeholder values."
exit 0
