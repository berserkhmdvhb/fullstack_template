# shellcheck shell=bash
# _manifest.sh — sourced by migrate-to-bootstrap.sh and k8s-env.sh: the one reader of a Helm manifest
# (a release's stored one, or a render). Both decide from it what an upgrade would delete, so a
# manifest it cannot classify is an error, never one that holds nothing.
#
# manifest_lf: stdin to stdout with CRLF line ends made LF. Every manifest passes through it once,
#   where it is read, so no later parse sees a carriage return.
# manifest_objects NAMESPACE: stdin (a manifest) to one kind/namespace/name line per top-level
#   document, in document order: the namespace is "-" for a Namespace, and NAMESPACE where the
#   document has none. It reads lines, not YAML: a top-level "kind:" and the "name:" and "namespace:"
#   directly under a top-level "metadata:". Returns 1, with the reason on stderr (never a line of the
#   manifest, which may hold Secrets), for a document with content and no plain kind, two kinds,
#   a List, or a Namespace, PersistentVolumeClaim or Secret without a plain name.
# manifest_holds OBJECTS: true when that list has a Namespace, Role or RoleBinding (what an older
#   chart rendered and the admin bootstrap now owns).
manifest_lf() { sed $'s/\r$//'; }
manifest_objects() {
  awk -v dflt="${1-}" '
    function fail(why) {
      printf "manifest: document %d (ending at line %d): %s\n", doc, NR, why > "/dev/stderr"
      bad = 1; exit 1
    }
    function value(   v) { # the scalar after the key, without a comment or one pair of quotes
      v = $0; sub(/^ *[a-z]+:[ \t]*/, "", v); sub(/[ \t]+#.*$/, "", v); sub(/[ \t]+$/, "", v)
      if (v ~ /^".*"$/ || v ~ /^\047.*\047$/) v = substr(v, 2, length(v) - 2)
      return v
    }
    function flush(   plain) {
      if (!content) return
      plain = "^[A-Za-z0-9._-]+$"
      if (kind !~ /^[A-Za-z0-9]+$/) fail("no plain top-level kind")
      if (kind ~ /List$/) fail("a " kind ", whose items are not top-level documents")
      if (kind ~ /^(Namespace|PersistentVolumeClaim|Secret)$/ && (name !~ plain || (ns != "" && ns !~ plain)))
        fail("a " kind " without a plain metadata.name (and namespace)")
      print kind "/" (kind == "Namespace" ? "-" : (ns == "" ? dflt : ns)) "/" name
      content = nk = nm = nn = nns = inmeta = 0; kind = name = ns = ""
    }
    /^---[ \t]+[^# \t]/ { doc++; fail("content on a document start line") }
    /^---([ \t]|$)/ { flush(); next }
    /^[ \t]*(#|$)/ { next }
    !content { content = 1; doc++ }
    /^kind:([ \t]|$)/ { if (nk++) fail("two top-level kinds"); kind = value(); next }
    /^metadata:([ \t]|$)/ { if (nm++) fail("two top-level metadata keys"); inmeta = 1; indent = 0; next }
    /^[^ ]/ { inmeta = 0; next }
    inmeta && match($0, /^ +/) {
      if (!indent) indent = RLENGTH
      if (RLENGTH != indent) next
      if ($0 ~ /^ +name:([ \t]|$)/) { if (nn++) fail("two metadata.name keys"); name = value() }
      if ($0 ~ /^ +namespace:([ \t]|$)/) { if (nns++) fail("two metadata.namespace keys"); ns = value() }
    }
    END { if (!bad) flush() }
  '
}
manifest_holds() { grep -Eq '^(Namespace|Role|RoleBinding)/' <<<"$1"; }
