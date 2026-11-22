#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# manifest-reader.bats — scripts/_manifest.sh, the one reader of a Helm manifest for
# migrate-to-bootstrap.sh and k8s-env.sh: the kind/namespace/name of each top-level document, and a
# refusal of whatever it cannot classify.
# -----------------------------------------------------------------------------

bats_require_minimum_version 1.5.0

setup() {
  # shellcheck source=/dev/null
  source "$(cd "$BATS_TEST_DIRNAME/../.." && pwd)/scripts/_manifest.sh"
}

objects() { printf '%s\n' "$1" | manifest_lf | manifest_objects rel-ns; }

@test "each top-level document is one kind/namespace/name line; nested kinds and comments are not" {
  run objects $'# Source: a.yaml\nkind: Namespace\nmetadata:\n  name: a\n  labels:\n    name: no\n---\n# only a comment\n---\nkind: Secret # c\nmetadata:\n  namespace: "other"\n  name: \'s\'\ndata:\n  kind: Namespace\n  name: no\n---\nkind: PersistentVolumeClaim\nmetadata:\n    name: p\nspec:\n  name: no\n---\nkind: ClusterRole\nmetadata:\n  name: sys:reader'
  [ "$status" -eq 0 ]
  [ "$output" = $'Namespace/-/a\nSecret/other/s\nPersistentVolumeClaim/rel-ns/p\nClusterRole/rel-ns/sys:reader' ]
}

@test "CRLF line ends read the same" {
  run objects $'kind: Namespace\r\nmetadata:\r\n  name: a\r\n---\r\nkind: Role\r'
  [ "$status" -eq 0 ]
  [ "$output" = $'Namespace/-/a\nRole/rel-ns/' ]
}

@test "an empty manifest holds nothing" {
  run objects ""
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "a document it cannot classify is refused" {
  for doc in $'"kind": Secret\nmetadata:\n  name: s' '{"kind": "Secret", "metadata": {"name": "s"}}' \
    $'kind: Secret\nkind: Role\nmetadata:\n  name: s' $'kind: Secret\nmetadata: {name: s}' \
    $'kind: Secret\nmetadata:\n  name: a b' $'kind: List\nitems:\n  - kind: Namespace' $'kind:\n  Secret' \
    $'kind: Secret\nmetadata:\n  name: s\nmetadata:\n  name: t' $'kind: Secret\nmetadata:\n  name: s\n  name: t'; do
    run objects $'kind: Role\n---\n'"$doc"
    [ "$status" -ne 0 ]
    [[ "$output" == *"manifest: document 2"* ]]
  done
}

@test "manifest_holds is true only for a Namespace, Role or RoleBinding" {
  manifest_holds $'Deployment/n/web\nRoleBinding/n/x'
  manifest_holds "Namespace/-/a"
  run ! manifest_holds $'ClusterRole/n/x\nSecret/n/Role'
  run ! manifest_holds ""
}
