#!/usr/bin/env bash
# infra/security/local/_origin.sh
# =============================================================================
# ORIGIN classification for supply-chain findings — DERIVED, never stored.
#
# The scan chain already carries everything needed to say WHERE a vulnerable
# component came from: its purl (pkg:deb/debian/…, pkg:pypi/…, pkg:oci/…) in the
# SBOM and grype JSON, and the location path(s) syft found it at. This reads that
# signal back out — a pure function of the purl (+, for the vendored case, the
# location path). Nothing here is written to a new artifact: report.sh sources it
# to render an origin axis, and the triage/SLA loop can call the same functions to
# group findings by who owns the fix. That is "route #3" (derived at render time)
# from wiki/handling-vulnerabilities.md — one source of truth (the purl), no
# stored duplicate, traded for a little classification logic that origin.bats pins
# (a wrong bucket is a systematic error, so it is tested rather than silent-wrong).
#
# Buckets (remediation ownership, most-specific first):
#   vendored    — shipped inside the app source tree (a /vendor/ or /third_party/
#                 path). You fix it by updating the vendored copy.   [location-driven]
#   interpreter — the language runtime you chose (python/node/go/jdk/ruby/php/…),
#                 whether it arrives as the base image (pkg:oci/…) or as a runtime
#                 package (pkg:generic/python, pkg:deb/.../python3). Fixed by
#                 bumping the base/runtime.
#   os-base     — an OS distribution package from the base image's package db
#                 (pkg:deb/*, pkg:apk/alpine, pkg:rpm/*, …). Fixed by the base-bump
#                 loop, like the interpreter, but tracked separately because it is
#                 the distro's package set, not the runtime version you picked.
#   app-dep     — a third-party library you pulled from a registry into your app
#                 (pkg:pypi/*, pkg:npm/*, pkg:golang/*, pkg:cargo/*, …). Fixed by
#                 upgrading the dependency in your lockfile / requirements.
#   unknown     — an ecosystem this map does not recognise. Named explicitly
#                 rather than mis-bucketed, so a gap is visible (see Review Focus).
#
# origin_of   "<purl>" "<space-separated location paths>"  ->  echoes <bucket>
# origin_rows "<grype-raw-json...>"                        ->  TSV, one row per match
# origin_summary "<grype-raw-json...>"                     ->  "<bucket>=<n>" lines
# (When executed directly, acts as a tiny CLI over those three: of | rows | summary.)
# =============================================================================
set -euo pipefail

# purl TYPE -> bucket vocabularies. Padded with surrounding spaces so the
# `case " $SET " in *" $tok "*)` membership test is exact (no prefix collisions,
# e.g. `gem` must not match `gempkg`).
# https://github.com/package-url/purl-spec/blob/master/PURL-TYPES.rst
_OS_PURL_TYPES=" deb apk alpine rpm ebuild ros pkgconf qpkg melpa "
_APP_PURL_TYPES=" pypi npm golang cargo maven composer gem nuget conan hackage swift pub cran hex mix melange github gitlab bitbucket gentoo "
# Language runtimes, matched on the purl NAME when the type alone is ambiguous
# (pkg:generic/python, or python3 delivered as an OS deb). Lower-cased, padded.
_RUNTIME_NAMES=" python python3 python2 pypy node nodejs deno bun openjdk temurin zulu corretto graalvm golang go ruby ruby-full php rust rustc lua julia "

_purl_type() {  # pkg:deb/debian/x@1 -> deb ; pkg:oci/img@sha256:.. -> oci ; "" -> ""
  case "${1:-}" in
    pkg:*) printf '%s' "${1#pkg:}" | cut -d/ -f1 ;;
    *)     printf '%s' "" ;;
  esac
}

_purl_name() {  # pkg:deb/debian/Python3@3.13 -> python3  (type + namespace stripped)
  local rest="${1#pkg:}"   # deb/debian/python3@3.13
  rest="${rest#*/}"        # debian/python3@3.13   (drop the type segment)
  rest="${rest%%@*}"       # debian/python3        (drop the version)
  printf '%s' "${rest##*/}" | tr '[:upper:]' '[:lower:]'   # python3
}

# origin_of <purl> <locations>  ->  echoes <bucket> on stdout (no newline).
# <locations> is the space-joined location-path list from the SBOM/grype match
# (origin_rows joins them); the vendored glob runs over that whole list, so any
# app-tree path flips the bucket.
origin_of() {
  local purl="${1:-}" locs="${2:-}"
  local type name
  type="$(_purl_type "$purl")"
  name="$(_purl_name "$purl")"

  # No purl type to read: nothing to classify. Name it rather than let an empty
  # token match the spaces *between* the padded set entries below.
  [ -n "$type" ] || { printf 'unknown'; return 0; }

  # 1) vendored wins: any location under the app tree (vendor/ third_party/ …),
  #    whatever the purl says. Slash-delimited so a component literally *named*
  #    "myvendor" is not matched, and conservative enough that a library sitting
  #    in site-packages is NOT mis-bucketed as vendored.
  case " $locs " in
    */vendor/* | */vendors/* | */third_party/* | */third-party/* | */vendored/*)
      printf 'vendored'; return 0 ;;
  esac

  # 2) interpreter: the base image itself, or a language runtime by name.
  #    (Guard the name test: an empty name would match the spaces *between* the
  #    padded runtime tokens, so skip it when there is no name to match.)
  [ "$type" = "oci" ] && { printf 'interpreter'; return 0; }
  if [ -n "$name" ]; then
    case " $_RUNTIME_NAMES " in *" $name "*) printf 'interpreter'; return 0 ;; esac
  fi

  # 3) OS distribution package (the base image's dpkg/apk/rpm database).
  case " $_OS_PURL_TYPES " in *" $type "*) printf 'os-base'; return 0 ;; esac

  # 4) a registry language library => your dependency.
  case " $_APP_PURL_TYPES " in *" $type "*) printf 'app-dep'; return 0 ;; esac

  # 5) unrecognised ecosystem: name it, don't guess.
  printf 'unknown'
}

# origin_rows <grype-raw-json...>  ->  TSV, one row per match:
#   bucket \t rank \t id \t pkg@ver \t SEV \t fix
# rank is the numeric severity rank (0..5) so callers can sort fix-ownership
# blocks by severity without re-parsing the word. Reads .artifact.locations for
# the path (that is where syft puts them) and .artifact.purl for the origin.
origin_rows() {
  [ "$#" -gt 0 ] || return 0
  jq -r '
    (.matches // [])[]
    | (.artifact.purl // "") as $p
    | ([ .artifact.locations[]?.path ] | join(" ")) as $l
    | (.vulnerability.id // "?") as $id
    | (((.artifact.name // "?")) + "@" + (.artifact.version // "?")) as $pv
    | ((.vulnerability.severity // "UNKNOWN") | ascii_upcase) as $sev
    | ([ .vulnerability.fix.versions[]? ] | unique | join(",")) as $fix
    | {UNKNOWN:0,NEGLIGIBLE:1,LOW:2,MEDIUM:3,HIGH:4,CRITICAL:5} as $rk
    | ($rk[$sev] // 0) as $rank
    | "\($p)\t\($l)\t\($rank)\t\($id)\t\($pv)\t\($sev)\t\($fix)"
  ' "$@" 2>/dev/null | while IFS=$'\t' read -r p l rank id pv sev fix; do
    # bucket computed on its own line: nesting "$(origin_of …)" inside the
    # printf arg list trips the parser here, so keep it out of the format call.
    bucket="$(origin_of "$p" "$l")"
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$bucket" "$rank" "$id" "$pv" "$sev" "$fix"
  done
}

# origin_summary <grype-raw-json...>  ->  "<bucket>=<n>" lines, count-desc.
origin_summary() {
  [ "$#" -gt 0 ] || return 0
  # `uniq -c` emits "  <count> <bucket>"; sort numerically on that leading count
  # (no -t needed), then flip to "bucket=count". Avoids `sort -t=`, which uutils
  # coreutils rejects ("separator must be exactly one character long").
  origin_rows "$@" | cut -f1 | LC_ALL=C sort | uniq -c \
    | sort -rn | awk '{ print $2 "=" $1 }'
}

# --- tiny CLI (only when executed directly, not when sourced by report.sh) -----
if [ "${BASH_SOURCE[0]:-$0}" = "${0}" ]; then
  case "${1:-}" in
    of)      shift; origin_of "${1:-}" "${2:-}"; echo ;;
    summary) shift; origin_summary "$@" ;;
    rows)    shift; origin_rows "$@" ;;
    *) echo "usage: _origin.sh {of <purl> <locs>|summary <grype.json...>|rows <grype.json...>}" >&2; exit 2 ;;
  esac
fi
