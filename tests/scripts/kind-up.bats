#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# kind-up.bats — scripts/kind-up.sh against stub kind and docker that record their arguments: it creates,
# reuses or deletes the cluster fst only, always with --name fst and its own kubeconfig, and never lists
# clusters.
# -----------------------------------------------------------------------------

bats_require_minimum_version 1.5.0

setup() {
  ROOT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)"
  SCRIPT="$ROOT/scripts/kind-up.sh"
  B="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$B"
  export LOG="$BATS_TEST_TMPDIR/calls.log"
  : > "$LOG"
  # The only override the scripts accept, and only under the repository's .local/.
  mkdir -p "$ROOT/.local"
  KIND_DIR="$(mktemp -d "$ROOT/.local/bats-kind.XXXXXX")"
  export FST_KIND_DIR="$KIND_DIR"
  # docker inspect answers for fst-control-plane with STUB_NODE_IMAGE (empty: no such container).
  cat > "$B/docker" <<'STUB'
#!/usr/bin/env bash
echo "docker $*" >> "$LOG"
if [[ "$1" == inspect && -n "${STUB_NODE_IMAGE:-}" ]]; then echo "$STUB_NODE_IMAGE"; exit 0; fi
exit 1
STUB
  cat > "$B/kind" <<'STUB'
#!/usr/bin/env bash
echo "kind $*" >> "$LOG"
if [[ "$*" == "get kubeconfig --name fst" ]]; then echo "apiVersion: v1"; fi
if [[ "$1 $2" == "create cluster" ]]; then : > "$6"; fi
STUB
  chmod +x "$B"/*
  export PATH="$B:$PATH"
  FLOOR="$(awk -F'\t' '$1 == "floor" {print $3}' "$ROOT/tools/kind-nodes.tsv")"
  NEWEST="$(awk -F'\t' '$1 == "newest" {print $3}' "$ROOT/tools/kind-nodes.tsv")"
}

teardown() {
  rm -rf "$KIND_DIR"
  rmdir "$ROOT/.local" 2>/dev/null || true
}

@test "it creates fst with the floor node image and its own kubeconfig" {
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  grep -qx "kind create cluster --name fst --kubeconfig $KIND_DIR/kubeconfig --image $FLOOR --wait 300s" "$LOG"
}

@test "--node newest uses the newest pinned node image" {
  run bash "$SCRIPT" --node newest
  [ "$status" -eq 0 ]
  grep -qx "kind create cluster --name fst --kubeconfig $KIND_DIR/kubeconfig --image $NEWEST --wait 300s" "$LOG"
}

@test "an existing fst on the same node image is reused, and its kubeconfig rewritten" {
  STUB_NODE_IMAGE="$FLOOR" run bash "$SCRIPT"
  [ "$status" -eq 0 ]
  [[ "$output" == *"reusing"* ]]
  run ! grep -q "kind create" "$LOG"
  grep -qx "kind get kubeconfig --name fst" "$LOG"
  [ "$(cat "$KIND_DIR/kubeconfig")" = "apiVersion: v1" ]
}

@test "an existing fst on another node image is refused, not recreated" {
  STUB_NODE_IMAGE="kindest/node:v1.0.0" run bash "$SCRIPT"
  [ "$status" -ne 0 ]
  [[ "$output" == *"make kind-down"* ]]
  run ! grep -q "kind create" "$LOG"
}

@test "--delete deletes fst only, with its kubeconfig, and removes the files it knows" {
  : > "$KIND_DIR/kubeconfig"
  : > "$KIND_DIR/values.yaml"
  run bash "$SCRIPT" --delete
  [ "$status" -eq 0 ]
  grep -qx "kind delete cluster --name fst --kubeconfig $KIND_DIR/kubeconfig" "$LOG"
  [ ! -e "$KIND_DIR" ]
}

@test "--delete leaves any file it did not create, and says so" {
  : > "$KIND_DIR/kubeconfig"
  : > "$KIND_DIR/notes.txt"
  run bash "$SCRIPT" --delete
  [ "$status" -eq 0 ]
  [ ! -e "$KIND_DIR/kubeconfig" ]
  [ -f "$KIND_DIR/notes.txt" ]
  [[ "$output" == *"left $KIND_DIR"* ]]
}

@test "an exported KIND_DIR is ignored: nothing is written to or deleted from it" {
  other="$BATS_TEST_TMPDIR/someone-elses-kind"
  mkdir -p "$other" && : > "$other/kubeconfig" && : > "$other/values.yaml"
  KIND_DIR="$other" run bash "$SCRIPT"
  grep -qx "kind create cluster --name fst --kubeconfig $FST_KIND_DIR/kubeconfig --image $FLOOR --wait 300s" "$LOG"
  KIND_DIR="$other" run bash "$SCRIPT" --delete
  [ -f "$other/kubeconfig" ]
  [ -f "$other/values.yaml" ]
  run ! grep -q "$other" "$LOG"
}

@test "an override outside the repository's .local/ is refused before anything runs" {
  for dir in "$BATS_TEST_TMPDIR/kind" "$ROOT/.local/../outside" "$ROOT/.local"; do
    FST_KIND_DIR="$dir" run bash "$SCRIPT" --delete
    [ "$status" -eq 2 ]
    [[ "$output" == *".local/"* ]]
  done
  [ -z "$(cat "$LOG")" ]
}

@test "no call ever lists clusters or names another one" {
  for args in "" "--node newest" "--delete"; do
    # shellcheck disable=SC2086 # the words are the arguments
    run bash "$SCRIPT" $args
    # shellcheck disable=SC2086 # the words are the arguments
    STUB_NODE_IMAGE="$FLOOR" run bash "$SCRIPT" $args
  done
  run ! grep -q "get clusters" "$LOG"
  while read -r line; do
    [[ "$line" != kind\ create* && "$line" != kind\ delete* ]] || [[ "$line" == *"--name fst --kubeconfig $KIND_DIR/kubeconfig"* ]]
  done < "$LOG"
  run ! grep -Eq "^docker (ps|stop|rm|system|container prune|image prune)" "$LOG"
}

@test "an unknown leg or option is refused before anything runs" {
  run bash "$SCRIPT" --node oldest
  [ "$status" -eq 2 ]
  run bash "$SCRIPT" --name lab
  [ "$status" -eq 2 ]
  [ -z "$(cat "$LOG")" ]
}
