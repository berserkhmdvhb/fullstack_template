#!/usr/bin/env bats
# -----------------------------------------------------------------------------
# migrate-to-bootstrap.bats — scripts/migrate-to-bootstrap.sh against stub helm and kubectl: its
# arguments, its preconditions and refusals (context, release status, rights), the order of the
# steps, the objects it keeps from deletion, and that a re-run is a no-op. The stub release holds a
# Namespace and a Role until the switch upgrade runs. No cluster is contacted.
# -----------------------------------------------------------------------------

bats_require_minimum_version 1.5.0

setup() {
  SCRIPT="$(cd "$BATS_TEST_DIRNAME/../.." && pwd)/scripts/migrate-to-bootstrap.sh"
  B="$BATS_TEST_TMPDIR/bin"
  mkdir -p "$B"
  export LOG="$BATS_TEST_TMPDIR/calls.log" STATE="$BATS_TEST_TMPDIR/state" ANNOT="$BATS_TEST_TMPDIR/annotations"
  : > "$LOG"
  : > "$ANNOT"
  echo old > "$STATE"
  # The release's stored manifest before the switch, and the current chart's render.
  export STUB_OLD=$'kind: Namespace\nmetadata:\n  name: app-ns\n---\nkind: Role\nmetadata:\n  name: app-role\n  namespace: app-ns\n---\nkind: Deployment\nmetadata:\n  name: web'
  export STUB_NEW=$'kind: Namespace\nmetadata:\n  name: app-ns\n---\nkind: Deployment\nmetadata:\n  name: web'
  cat > "$B/helm" <<'STUB'
#!/usr/bin/env bash
echo "helm $*" >> "$LOG"
case "$1 $2" in
  "status "*)
    [[ -z "${STUB_NO_RELEASE-}" ]] || { echo "Error: release: not found" >&2; exit 1; }
    [[ -z "${STUB_READ_FAIL-}" ]] || { echo "Error: Kubernetes cluster unreachable" >&2; exit 1; }
    printf '{"version": 3, "info": {"status": "%s"}}\n' "${STUB_STATUS:-deployed}" ;;
  "history "*)
    if [[ -n "${STUB_NEVER-}" ]]; then echo '[{"revision": 1, "status": "failed"}]'
    else echo '[{"revision": 2, "status": "deployed"}, {"revision": 3, "status": "failed"}]'; fi ;;
  "get manifest")
    if [[ "$(cat "$STATE")" == old || -n "${STUB_STAYS_OLD-}" ]]; then echo "$STUB_OLD"; else echo "kind: Deployment"; fi ;;
  "upgrade "*)
    [[ "$*" != *"namespaces.create=false"* ]] || echo switched > "$STATE" ;;
  "template bootstrap") echo "kind: Rendered" ;;
  "template "*) echo "$STUB_NEW" ;;
esac
STUB
  cat > "$B/kubectl" <<'STUB'
#!/usr/bin/env bash
echo "kubectl $*" >> "$LOG"
[[ "$1" != --context ]] || shift 2
case "$1 $2" in
  "config get-contexts") printf 'kind-x\nother\n' ;;
  "config view") echo "https://127.0.0.1:6443" ;;
  "auth can-i") echo "${STUB_CAN:-yes}"; [[ "${STUB_CAN:-yes}" == yes ]] ;;
  "apply -f") cat > /dev/null ;;
  "annotate "*|"label "*) # VERB KIND NAME [-n NS] K=V... --overwrite: recorded as "kind ns name K=V..."
    verb="$1" kind="$2" name="$3" ns=-; shift 3
    [[ "${1-}" != -n ]] || { ns="$2"; shift 2; }
    [[ -n "${STUB_ANNOTATE_NOOP-}" && "$verb" == annotate ]] || echo "$kind $ns $name ${*/--overwrite/}" >> "$ANNOT" ;;
  "get "*)
    kind="${2%s}" # namespaces or namespace: the record keeps the singular
    if [[ "$*" == *" -l "* ]]; then # NS NAME of every object of the kind with all the selector's labels
      sel="$(sed 's/.* -l \([^ ]*\).*/\1/' <<<"$*")"
      while read -r k ns name rest; do
        [[ "$k" == "$kind" ]] || continue
        all="$(grep "^$k $ns $name " "$ANNOT" | cut -d' ' -f4- | tr '\n' ' ')"
        ok=1; for kv in ${sel//,/ }; do [[ " $all " == *" $kv "* ]] || ok=0; done
        [[ "$ok" == 1 ]] || continue
        [[ "$ns" != - ]] || ns="<none>"
        echo "$ns $name"
      done < <(sort -u -k1,3 "$ANNOT")
    else # one object: its keep policy and the two marker labels
      name="$3" ns=-; [[ "${4-}" != -n ]] || ns="$5"
      rec="$(grep "^$kind $ns $name " "$ANNOT" | tr '\n' ' ')"
      val() { grep -o "$1=[^ ]*" <<<"$rec" | tail -n 1 | cut -d= -f2; }
      echo "$(val helm.sh/resource-policy)|$(val migrate-to-bootstrap/release)|$(val migrate-to-bootstrap/namespace)"
    fi ;;
esac
STUB
  chmod +x "$B"/*
  export PATH="$B:$PATH"
  printf 'secrets: {}\n' > "$BATS_TEST_TMPDIR/secret.yaml"
  V=(--values "$BATS_TEST_TMPDIR/secret.yaml")
}

@test "no environment, an unknown one or an unknown option is refused before anything runs" {
  run bash "$SCRIPT"
  [ "$status" -eq 2 ]
  run bash "$SCRIPT" staging --context kind-x
  [ "$status" -eq 2 ]
  run bash "$SCRIPT" dev --context kind-x --reuse-values
  [ "$status" -eq 2 ]
  run bash "$SCRIPT" dev --context kind-x --release 'a,b=c' --yes
  [ "$status" -eq 2 ]
  [ -z "$(cat "$LOG")" ]
}

@test "without --context it is refused before anything runs" {
  run bash "$SCRIPT" dev "${V[@]}" --yes
  [ "$status" -eq 2 ]
  [[ "$output" == *"--context"* ]]
  [ -z "$(cat "$LOG")" ]
}

@test "a context the kubeconfig does not hold is refused before the cluster is read" {
  run bash "$SCRIPT" dev --context nowhere "${V[@]}" --yes
  [ "$status" -eq 2 ]
  [[ "$output" == *"nowhere"*"not in the kubeconfig"* ]]
  run ! grep -q "^helm" "$LOG"
}

@test "the context reaches every helm and kubectl call" {
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 0 ]
  run ! grep -v -e "--kube-context kind-x" -e "^kubectl --context kind-x " -e "^kubectl config get-contexts" \
    -e "^helm template" "$LOG"
  grep -q "^helm upgrade .*--kube-context kind-x" "$LOG"
  grep -q "^kubectl --context kind-x apply -f -$" "$LOG"
}

@test "a namespace of another environment is refused" {
  run bash "$SCRIPT" dev --context kind-x --namespace svc-qa "${V[@]}" --yes
  [ "$status" -eq 2 ]
  [[ "$output" == *"svc-qa"* ]]
  [ -z "$(cat "$LOG")" ]
}

@test "without the deploy's values it is refused, unless the env's values file is all it takes" {
  run bash "$SCRIPT" dev --context kind-x --yes
  [ "$status" -eq 2 ]
  [[ "$output" == *"--env-values-only"* ]]
  [ -z "$(cat "$LOG")" ]
  run bash "$SCRIPT" dev --context kind-x --env-values-only --yes
  [ "$status" -eq 0 ]
}

@test "the migration runs the keep upgrade, the bootstrap and the switch upgrade, in that order" {
  run bash "$SCRIPT" qa --context kind-x --release svc "${V[@]}" --set images.backend.digest=sha256:x \
    --set-string images.backend.tag=t1 --timeout 7m --yes
  [ "$status" -eq 0 ]
  keep="$(grep -n '^helm upgrade svc .*namespaces.create=true --set rbac.create=true' "$LOG" | cut -d: -f1)"
  boot="$(grep -n 'apply -f -$' "$LOG" | cut -d: -f1)"
  switch="$(grep -n '^helm upgrade svc .*namespaces.create=false --set rbac.create=false' "$LOG" | cut -d: -f1)"
  [ -n "$keep" ] && [ -n "$boot" ] && [ -n "$switch" ]
  (( keep < boot && boot < switch ))
  for line in "$(sed -n "${keep}p" "$LOG")" "$(sed -n "${switch}p" "$LOG")"; do
    [[ "$line" == *"-n svc-qa -f "*"/charts/your-service-name/values.qa.yaml -f $BATS_TEST_TMPDIR/secret.yaml --set images.backend.digest=sha256:x --set-string images.backend.tag=t1"* ]]
    [[ "$line" == *"--history-max 5"* && "$line" == *"--wait --timeout 7m"* ]]
    [[ "$line" != *"reuse-values"* ]]
  done
  grep "^helm template bootstrap" "$LOG" | grep -q -- "--set-string release=svc --set-string namespace=svc-qa"
  [[ "$output" == *"migrated: svc in svc-qa"* ]]
}

@test "the stored manifest is read at the deployed revision" {
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 0 ]
  grep -q "^helm get manifest your-service-name -n your-service-name-dev --revision 3 " "$LOG"
}

@test "a re-run after a migration does nothing" {
  echo switched > "$STATE"
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 0 ]
  [[ "$output" == *"nothing to do"* ]]
  run ! grep -q "^helm upgrade" "$LOG"
  run ! grep -q "apply -f" "$LOG"
}

@test "no release is refused" {
  STUB_NO_RELEASE=1 run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"no release"* ]]
  run ! grep -q "^helm upgrade" "$LOG"
}

@test "a release that cannot be read is an error, not a release without namespaces" {
  STUB_READ_FAIL=1 run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"cluster unreachable"* ]]
  run ! grep -q "^helm upgrade" "$LOG"
}

@test "a failed release is refused, with the rollback to its last deployed revision" {
  STUB_STATUS=failed run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"failed"*"helm rollback your-service-name 2 -n your-service-name-dev --kube-context kind-x"* ]]
  run ! grep -q "^helm upgrade" "$LOG"
  run ! grep -q "get manifest" "$LOG"
}

@test "a pending release is refused, with how to recover" {
  for s in pending-install pending-upgrade pending-rollback; do
    : > "$LOG"
    STUB_STATUS="$s" run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
    [ "$status" -eq 1 ]
    [[ "$output" == *"$s"*"in progress or was interrupted"*"helm rollback"* ]]
    run ! grep -q "^helm upgrade" "$LOG"
  done
}

@test "an identity that is not a cluster admin is refused before any change" {
  STUB_CAN=no run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"cluster admin"* ]]
  run ! grep -q "^helm upgrade" "$LOG"
}

@test "without --yes and without a terminal it refuses rather than ask" {
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" < /dev/null
  [ "$status" -eq 2 ]
  [[ "$output" == *"--yes"* ]]
  run ! grep -q "^helm upgrade" "$LOG"
}

@test "the confirmation names the context and its server, and a wrong answer changes nothing" {
  run script -qec "bash $SCRIPT dev --context kind-x ${V[*]}" /dev/null <<<"wrong"
  [[ "$output" == *"kind-x"*"https://127.0.0.1:6443"* ]]
  [[ "$output" == *"not confirmed"* ]]
  run ! grep -q "^helm upgrade" "$LOG"
}

@test "a dry run prints every step with the context, and changes nothing" {
  run bash "$SCRIPT" production --context kind-x --release svc "${V[@]}" --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"kind-x"*"https://127.0.0.1:6443"* ]]
  [[ "$output" == *"helm upgrade svc "*"namespaces.create=true"* ]]
  [[ "$output" == *"k8s-bootstrap.sh production --release svc --namespace svc-production | kubectl --context kind-x apply -f -"* ]]
  [[ "$output" == *"helm upgrade svc "*"namespaces.create=false"* ]]
  # Only read-only calls: an allowlist, so any other call fails it.
  run ! grep -Ev -e '^helm (status|history|get manifest|template) ' -e '^kubectl config (get-contexts|view) ' \
    -e '^kubectl --context kind-x (config view|auth can-i|get|version) ' "$LOG"
}

@test "a release that still holds them after the switch fails the run" {
  STUB_STAYS_OLD=1 run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"still holds"* ]]
}

# The older release owns a logging namespace with a Secret in it, and a PVC, that the current chart
# does not render, a ConfigMap it does not render either, and a Secret it still renders.
orphans() {
  export STUB_OLD="$STUB_OLD"$'\n---\nkind: Namespace\nmetadata:\n  name: app-ns-logging\n---\nkind: Secret\nmetadata:\n  name: shipper\n  namespace: app-ns-logging\n---\nkind: PersistentVolumeClaim\nmetadata:\n  name: old-data\n---\nkind: ConfigMap\nmetadata:\n  name: gone\n---\nkind: Secret\nmetadata:\n  name: kept'
  export STUB_NEW="$STUB_NEW"$'\n---\nkind: Secret\nmetadata:\n  name: kept'
}

@test "a Namespace, PVC or Secret the current chart no longer renders is annotated keep before the first upgrade" {
  orphans
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 0 ]
  first="$(grep -n "^helm upgrade" "$LOG" | head -n 1 | cut -d: -f1)"
  for obj in "namespace app-ns-logging" "secret shipper -n app-ns-logging" \
    "persistentvolumeclaim old-data -n your-service-name-dev"; do
    at="$(grep -n "^kubectl --context kind-x annotate $obj helm.sh/resource-policy=keep --overwrite$" "$LOG" | cut -d: -f1)"
    [ -n "$at" ]
    (( at < first ))
    at="$(grep -n "^kubectl --context kind-x label $obj migrate-to-bootstrap/release=your-service-name migrate-to-bootstrap/namespace=your-service-name-dev --overwrite$" "$LOG" | cut -d: -f1)"
    [ -n "$at" ]
    (( at < first ))
  done
  [ "$(grep -c "annotate" "$LOG")" -eq 3 ]
  [[ "$output" == *"kept, now unmanaged: decide by hand"*"Namespace app-ns-logging"*"PersistentVolumeClaim your-service-name-dev/old-data"*"Secret app-ns-logging/shipper"* ]]
  run ! grep -q "delete" "$LOG"
}

@test "a dry run lists the objects it would keep" {
  orphans
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"Namespace app-ns-logging"*"PersistentVolumeClaim your-service-name-dev/old-data"*"Secret app-ns-logging/shipper"* ]]
  [[ "$output" != *"ConfigMap"* && "$output" != *"Secret your-service-name-dev/kept"* ]]
  run ! grep -q "annotate" "$LOG"
}

@test "no path of the script deletes anything" {
  code="$(grep -v '^\s*#' "$SCRIPT")"
  run ! grep -Eq 'kubectl[^|]* delete|helm[^|]* (uninstall|delete)' <<<"$code"
}

@test "the kept objects are computed the same in any locale (sort and comm agree)" {
  export STUB_OLD="$STUB_OLD"$'\n---\nkind: Secret\nmetadata:\n  name: a-b\n---\nkind: Secret\nmetadata:\n  name: a_c\n---\nkind: Secret\nmetadata:\n  name: A-d\n---\nkind: Secret\nmetadata:\n  name: ab'
  export STUB_NEW="$STUB_NEW"$'\n---\nkind: Secret\nmetadata:\n  name: a_c\n---\nkind: Secret\nmetadata:\n  name: ab'
  LC_ALL=en_US.UTF-8 run --separate-stderr bash "$SCRIPT" dev --context kind-x "${V[@]}" --dry-run
  [ "$status" -eq 0 ]
  [[ "$stderr" != *"sorted order"* ]]
  [[ "$output" == *"Secret your-service-name-dev/A-d"* && "$output" == *"Secret your-service-name-dev/a-b"* ]]
  [[ "$output" != *"/a_c"* && "$output" != *"/ab"* ]]
}

# A yq stub: the pinned version (from tools/pins.tsv) unless told otherwise, failing unless told not to.
fake_yq() {
  local pin
  pin="$(awk -F'\t' '$1 == "yq" {print $2}' "$(dirname "$SCRIPT")/../tools/pins.tsv")"
  # STUB_YQ_MODE: fail (exit 1, the default), empty (print nothing, exit 0), or drop (the real yq's
  # output without its last line).
  local real; real="$(command -v yq)"
  # shellcheck disable=SC2016 # the stub expands $1 itself
  printf '#!/usr/bin/env bash\n[[ "$1" != --version ]] || { echo "yq (https://github.com/mikefarah/yq/) version v%s"; exit 0; }\ncase "${STUB_YQ_MODE:-fail}" in\n  empty) exit 0 ;;\n  drop) "%s" "$@" | sed "\\$d" ;;\n  *) exit 1 ;;\nesac\n' \
    "${1:-$pin}" "$real" > "$B/yq"
  chmod +x "$B/yq"
}

@test "a yq that fails while the kept objects are computed stops the run before any upgrade" {
  orphans
  fake_yq
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -ne 0 ]
  run ! grep -q -e "^helm upgrade" -e "annotate" "$LOG"
}

@test "a yq that is not the pinned version is refused before the cluster is read" {
  fake_yq 4.0.0
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"yq"*"4.0.0"*"pinned"* ]]
  run ! grep -q "^helm" "$LOG"
}

@test "an annotate that exits 0 but leaves no annotation stops the run before any upgrade" {
  orphans
  STUB_ANNOTATE_NOOP=1 run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"not marked keep"* ]]
  run ! grep -q "^helm upgrade" "$LOG"
}

@test "every kept object is read back from the cluster before the first upgrade" {
  orphans
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 0 ]
  first="$(grep -n "^helm upgrade" "$LOG" | head -n 1 | cut -d: -f1)"
  for obj in "namespace app-ns-logging" "secret shipper -n app-ns-logging" \
    "persistentvolumeclaim old-data -n your-service-name-dev"; do
    at="$(grep -n "^kubectl --context kind-x get $obj -o jsonpath=" "$LOG" | cut -d: -f1)"
    [ -n "$at" ]
    (( at < first ))
  done
}

@test "a re-run after a stop at step 1 lists the objects the first run kept, from their marker" {
  # Step 1 ran: the deployed revision no longer holds the logging namespace, which is marked.
  echo "namespace - app-ns-logging helm.sh/resource-policy=keep migrate-to-bootstrap/release=your-service-name migrate-to-bootstrap/namespace=your-service-name-dev" >> "$ANNOT"
  echo "namespace - other-ns helm.sh/resource-policy=keep migrate-to-bootstrap/release=other migrate-to-bootstrap/namespace=elsewhere" >> "$ANNOT"
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 0 ]
  [[ "$output" == *"kept, now unmanaged: decide by hand"*"Namespace app-ns-logging"* ]]
  [[ "$output" != *"other-ns"* ]]
  run ! grep -q "annotate" "$LOG"
}

@test "a release already migrated still lists what an earlier run kept" {
  echo switched > "$STATE"
  echo "secret app-ns-logging shipper helm.sh/resource-policy=keep migrate-to-bootstrap/release=your-service-name migrate-to-bootstrap/namespace=your-service-name-dev" >> "$ANNOT"
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 0 ]
  [[ "$output" == *"nothing to do"*"kept, now unmanaged"*"Secret app-ns-logging/shipper"* ]]
}

@test "a yq that prints nothing and exits 0 stops the run before any upgrade" {
  orphans
  fake_yq
  STUB_YQ_MODE=empty run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"yq found 0"*"the manifest has"* ]]
  run ! grep -q -e "^helm upgrade" -e "annotate" "$LOG"
}

@test "a yq that drops one of the objects stops the run before any upgrade" {
  orphans
  fake_yq
  STUB_YQ_MODE=drop run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"the manifest has 5"* ]]
  run ! grep -q -e "^helm upgrade" -e "annotate" "$LOG"
}

@test "the kept objects are listed by their marker labels, after the admin checks" {
  orphans
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 0 ]
  can="$(grep -n "auth can-i" "$LOG" | tail -n 1 | cut -d: -f1)"
  list="$(grep -n " get secrets -A -l migrate-to-bootstrap/release=your-service-name,migrate-to-bootstrap/namespace=your-service-name-dev " "$LOG" | head -n 1 | cut -d: -f1)"
  [ -n "$list" ]
  (( can < list ))
  run ! grep -Eq " get (secrets|namespaces|persistentvolumeclaims)( -A)? --no-headers" "$LOG"
}

@test "a re-run by a non-admin of a migrated release exits 0 and lists nothing" {
  echo switched > "$STATE"
  STUB_CAN=no run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 0 ]
  [[ "$output" == *"nothing to do"*"cluster admin"* ]]
  run ! grep -q " get secrets" "$LOG"
}

@test "a release that was never deployed is refused with how to recover, not a rollback" {
  STUB_STATUS=failed STUB_NEVER=1 run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"never been deployed"* ]]
  [[ "$output" != *"helm rollback"* ]]
  run ! grep -q "^helm upgrade" "$LOG"
}
