#!/usr/bin/env bash
# render-admission-policy.sh <Owner>/<Repo> — print the admission policies with the placeholders filled.
#
# <Owner>/<Repo> is the repository exactly as GitHub spells it. The certificate subject and the
# repository claim need that exact case; the image glob needs the lowercase registry path.
set -euo pipefail

usage() {
  echo "usage: $0 <Owner>/<Repo>   (the repository exactly as GitHub spells it)" >&2
  exit 2
}

[[ $# -eq 1 && "$1" =~ ^[A-Za-z0-9-]+/[A-Za-z0-9._-]+$ ]] || usage
slug="$1"
lower="${slug,,}"
# The subject is a regexp inside single-quoted YAML: escape the dots, and sed needs each backslash doubled.
escaped="${slug//./\\\\.}"

dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../deploy/policies" && pwd)"
out=""
for f in kyverno-verify-images.yaml kyverno-deny-image-aliases.yaml; do
  out+="$(sed \
    -e "s|github\\\\.com/<Owner>/<Repo>/|github\\\\.com/${escaped}/|g" \
    -e "s|<Owner>/<Repo>|${slug}|g" \
    -e "s|<owner>/<repo>|${lower}|g" "$dir/$f")"$'\n---\n'
done

if grep -Eq '<([Oo]wner|[Rr]epo)>' <<<"$out"; then
  echo "render-admission-policy: a placeholder is left unfilled" >&2
  exit 1
fi
printf '%s' "${out%---$'\n'}"
