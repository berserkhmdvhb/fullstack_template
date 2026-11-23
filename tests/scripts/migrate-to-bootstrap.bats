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
  # The live objects Helm records as a release's: "kind namespace name [release release-namespace]",
  # the release read by the last helm status unless the line names another.
  export LIVE="$BATS_TEST_TMPDIR/live" APPLIED="$BATS_TEST_TMPDIR/applied"
  echo "namespace - app-ns" > "$LIVE"
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
  "template bootstrap") ns_doc=$'kind: Namespace\nmetadata:\n  name: app-ns'; echo "${STUB_BOOT:-$ns_doc}"
    if [[ -n "${STUB_BOOT_SUBJECT-}" ]]; then # the render's RoleBinding subject: deployer.name of the merged -f files
      files=(); while [[ $# -gt 0 ]]; do [[ "$1" != -f ]] || files+=("$2"); shift; done
      echo "---"; echo "kind: RoleBinding"; echo "metadata: {name: d, namespace: app-ns}"
      echo "roleRef: {kind: Role}"; echo "subject: $(yq ea '. as $i ireduce ({}; . * $i) | .deployer.name' "${files[@]}")"
    fi ;;
  "template "*) # the bootstrap's renders of the chart (the app's Role, the ClusterRules), else the chart
    if [[ "$*" == *gmp-cluster-rules* ]]; then echo "$STUB_CLUSTER_RULES"
    elif [[ "$*" == *templates/rbac.yaml* ]]; then printf -- '---\nkind: Role\nmetadata:\n  name: app\n  namespace: app-ns\n'
    else echo "$STUB_NEW"; fi ;;
esac
STUB
  cat > "$B/kubectl" <<'STUB'
#!/usr/bin/env bash
echo "kubectl $*" >> "$LOG"
[[ "$1" != --context ]] || shift 2
case "$1 $2" in
  "config get-contexts") printf 'kind-x\nother\n' ;;
  "config view") echo "https://127.0.0.1:6443" ;;
  "auth can-i") # STUB_CAN answers every check; STUB_DENY (a fragment of one check) answers that one no
    can="${STUB_CAN:-yes}"; [[ -z "${STUB_DENY-}" || "$*" != *"$STUB_DENY"* ]] || can=no
    echo "$can"; [[ "$can" == yes ]] ;;
  "apply -f") cat >> "$APPLIED" ;;
  "annotate "*|"label "*) # VERB KIND NAME [-n NS] K=V... --overwrite: recorded as "kind ns name K=V..."
    verb="$1" kind="$2" name="$3" ns=-; shift 3
    [[ "${STUB_NOTFOUND-}" != "$name" || "$verb" != annotate ]] ||
      { echo "Error from server (NotFound): ${kind}s \"$name\" not found" >&2; exit 1; }
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
    elif [[ "$*" == *"jsonpath={range"* ]]; then # NS NAME RELEASE RELEASE-NS POLICY of each live object of the kind
      want=""; [[ "$*" != *" -n "* ]] || want="$(sed 's/.* -n \([^ ]*\).*/\1/' <<<"$*")"
      read -r _ _ rel _ relns _ <<<"$(grep '^helm status' "$LOG" | tail -n 1)"
      while read -r k ns name r rn; do
        [[ "$k" == "$kind" && ( -z "$want" || "$ns" == "$want" ) ]] || continue
        policy=""; ! grep -q "^$k $ns $name .*helm.sh/resource-policy=keep" "$ANNOT" || policy=keep
        printf '%s\t%s\t%s\t%s\t%s\n' "${ns/#-/}" "$name" "${r:-$rel}" "${rn:-$relns}" "$policy"
      done < "$LIVE"
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
  [[ "$output" == *"k8s-bootstrap.sh production --release svc --namespace svc-production --chart-values $BATS_TEST_TMPDIR/secret.yaml | kubectl --context kind-x apply -f -"* ]]
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
  printf '%s\n' "namespace - app-ns-logging" "secret app-ns-logging shipper" \
    "persistentvolumeclaim your-service-name-dev old-data" "secret your-service-name-dev kept" >> "$LIVE"
}

# The same manifests with CRLF line ends; a stored manifest the line reader cannot classify (yq reads
# the quoted key); and a stored Secret whose name it cannot read (flow style).
crlf() { export STUB_OLD="${STUB_OLD//$'\n'/$'\r\n'}" STUB_NEW="${STUB_NEW//$'\n'/$'\r\n'}"; }
quoted_key() { export STUB_OLD=$'"kind": Namespace\nmetadata:\n  name: app-ns'; }
flow_secret() { export STUB_OLD="$STUB_OLD"$'\n---\nkind: Secret\nmetadata: {name: flow}'; }

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

# A yq stub: the pinned version (from tools/pins.tsv) unless told otherwise. STUB_YQ_MODE is what it
# does with the real yq's output: fail (exit 1, the default), empty (print nothing, exit 0), drop
# (without its last line), dupdrop (its second line replaced by a copy of the first), rename (one
# object's name changed), phantom (for the render, one object more), or hide
# (without the logging namespace).
fake_yq() {
  local pin real
  pin="$(awk -F'\t' '$1 == "yq" {print $2}' "$(dirname "$SCRIPT")/../tools/pins.tsv")"
  real="$(command -v yq)"
  cat > "$B/yq" <<STUB
#!/usr/bin/env bash
[[ "\$1" != --version ]] || { echo "yq (https://github.com/mikefarah/yq/) version v${1:-$pin}"; exit 0; }
case "\${STUB_YQ_MODE:-fail}" in
  empty) exit 0 ;;
  drop) "$real" "\$@" | sed '\$d' ;;
  dupdrop) "$real" "\$@" | awk 'NR == 1 { first = \$0 } NR == 2 { \$0 = first } { print }' ;;
  rename) "$real" "\$@" | sed 's/app-ns-logging/app-ns-loggin/' ;;
  hide) "$real" "\$@" | sed '/^Namespace\/-\/app-ns-logging\$/d' ;;
  phantom)
    [[ "\$*" == *"/dev/stdin" ]] || exec "$real" "\$@" # a file read (the bootstrap's own), as it is
    in="\$(cat)"
    printf '%s\n' "\$in" | "$real" "\$@"
    [[ "\$in" != *"name: kept"* || "\$in" == *app-ns-logging* ]] || printf 'Namespace - app-ns-logging\nNamespace/-/app-ns-logging\n' ;;
  *) exit 1 ;;
esac
STUB
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

@test "a yq that drops one object and duplicates another stops the run before any upgrade" {
  orphans
  fake_yq
  STUB_YQ_MODE=dupdrop run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"Namespace/-/app-ns-logging"* ]]
  run ! grep -q -e "^helm upgrade" -e "annotate" "$LOG"
}

@test "a yq that renames one object stops the run before any upgrade" {
  orphans
  fake_yq
  STUB_YQ_MODE=rename run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  # Both lists have 5 objects: the message names the ones that differ.
  [[ "$output" == *"yq found 5 "*"the manifest has 5"* ]]
  [[ "$output" == *"only the line reader found: Namespace/-/app-ns-logging Secret/app-ns-logging/shipper; only yq found: Namespace/-/app-ns-loggin Secret/app-ns-loggin/shipper"* ]]
  run ! grep -q -e "^helm upgrade" -e "annotate" "$LOG"
}

@test "a yq that adds an object to the current chart's render stops the run before any upgrade" {
  # The phantom would be taken as still rendered, so the stored one would not be kept.
  orphans
  fake_yq
  STUB_YQ_MODE=phantom run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"the current chart's render"* ]]
  run ! grep -q -e "^helm upgrade" -e "annotate" "$LOG"
}

@test "an object the release owns that is not in the cluster stops the run before any upgrade" {
  orphans
  STUB_NOTFOUND=app-ns-logging run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"NotFound"* && "$output" == *"is not in the cluster"* ]]
  [[ "$output" == *"run this again with --allow-missing Namespace/-/app-ns-logging"* ]]
  [[ "$output" != *"already gone"* ]]
  run ! grep -q "^helm upgrade" "$LOG"
}

@test "a manifest with CRLF line ends is read as what it holds: its objects are kept before the first upgrade" {
  orphans
  crlf
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 0 ]
  [[ "$output" != *"nothing to do"* ]]
  first="$(grep -n "^helm upgrade" "$LOG" | head -n 1 | cut -d: -f1)"
  at="$(grep -n "^kubectl --context kind-x annotate namespace app-ns-logging helm.sh/resource-policy=keep --overwrite$" "$LOG" | cut -d: -f1)"
  [ -n "$at" ]
  (( at < first ))
  [ "$(grep -c "annotate" "$LOG")" -eq 3 ]
}

@test "a CRLF manifest that holds Namespaces is not 'nothing to do': a non-admin is refused before any upgrade" {
  orphans
  crlf
  STUB_CAN=no run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"cluster admin"* && "$output" != *"nothing to do"* ]]
  run ! grep -q "^helm upgrade" "$LOG"
}

@test "a manifest that cannot be classified is refused, not taken as holding nothing" {
  quoted_key
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"cannot classify"* && "$output" != *"nothing to do"* ]]
  run ! grep -q -e "^helm upgrade" -e "annotate" "$LOG"
}

@test "a Secret the line reader cannot name stops the run before any upgrade" {
  flow_secret
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"cannot classify"* ]]
  run ! grep -q -e "^helm upgrade" -e "annotate" "$LOG"
}

@test "an identity that cannot list Secrets is refused before any change" {
  orphans
  STUB_DENY="list secrets" run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"cannot 'list secrets --all-namespaces'"*"cluster admin"* ]]
  run ! grep -q -e "^helm upgrade" -e "annotate" -e "kind-x get secrets" "$LOG"
}

@test "the admin check asks for get, list and patch on the kinds it keeps and lists" {
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --dry-run
  [ "$status" -eq 0 ]
  for verb in get list patch; do
    grep -q "auth can-i $verb namespaces --all-namespaces$" "$LOG"
    grep -q "auth can-i $verb persistentvolumeclaims --all-namespaces$" "$LOG"
    grep -q "auth can-i $verb secrets --all-namespaces$" "$LOG"
  done
}

@test "a re-run of a migrated release by an identity that cannot list Secrets exits 0 and lists nothing" {
  echo switched > "$STATE"
  STUB_DENY="list secrets" run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 0 ]
  [[ "$output" == *"nothing to do"*"cluster admin"* ]]
  run ! grep -q "kind-x get secrets" "$LOG"
}

@test "an object deleted on purpose is passed with --allow-missing, and only that object" {
  orphans
  STUB_NOTFOUND=old-data run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes \
    --allow-missing Secret/app-ns-logging/shipper
  [ "$status" -eq 1 ]
  [[ "$output" == *"--allow-missing PersistentVolumeClaim/your-service-name-dev/old-data"* ]]
  run ! grep -q "^helm upgrade" "$LOG"
  sed -i '/old-data/d' "$LIVE" # deleted: the cluster does not list it either
  STUB_NOTFOUND=old-data run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes \
    --allow-missing PersistentVolumeClaim/your-service-name-dev/old-data
  [ "$status" -eq 0 ]
  [[ "$output" == *"PersistentVolumeClaim/your-service-name-dev/old-data is not in the cluster"*"migrated:"* ]]
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes --allow-missing Deployment/x/web
  [ "$status" -eq 2 ]
}

# A copy of the scripts whose line reader, like the yq stub's hide mode, leaves the logging namespace out.
both_readers_hide() {
  local root="$BATS_TEST_TMPDIR/root"
  mkdir -p "$root/scripts" "$root/tools"
  cp "$(dirname "$SCRIPT")"/*.sh "$root/scripts/"
  cp "$(dirname "$SCRIPT")/../tools/pins.tsv" "$root/tools/"
  cat >> "$root/scripts/_manifest.sh" <<'PATCH'
eval "$(declare -f manifest_objects | sed '1s/.*/real_manifest_objects ()/')"
manifest_objects() { real_manifest_objects "$@" | sed '/^Namespace\/-\/app-ns-logging$/d'; }
PATCH
  fake_yq
  SCRIPT="$root/scripts/migrate-to-bootstrap.sh"
}

@test "an object both readers leave out is caught from the cluster's own record, before any upgrade" {
  orphans
  both_readers_hide
  STUB_YQ_MODE=hide run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"Namespace/-/app-ns-logging is this release's in the cluster"* ]]
  run ! grep -q -e "^helm upgrade" -e "annotate" "$LOG"
}

@test "the live objects of the release that the render holds, or that are kept, pass; they are listed before the first upgrade" {
  orphans
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 0 ]
  first="$(grep -n "^helm upgrade" "$LOG" | head -n 1 | cut -d: -f1)"
  marked="$(grep -n " annotate " "$LOG" | tail -n 1 | cut -d: -f1)"
  # Once before any change, and once more after the keep annotations.
  mapfile -t lists < <(grep -n "^kubectl --context kind-x get namespaces -o jsonpath={range" "$LOG" | cut -d: -f1)
  [ "${#lists[@]}" -eq 2 ]
  (( lists[0] < marked && marked < lists[1] && lists[1] < first ))
  # Secrets and PVCs: in the release's namespaces only, and never Helm's own release Secrets.
  grep -q "^kubectl --context kind-x get secrets -n app-ns-logging --field-selector type!=helm.sh/release.v1 -o jsonpath={range" "$LOG"
  grep -q "^kubectl --context kind-x get persistentvolumeclaims -n your-service-name-dev -o jsonpath={range" "$LOG"
  run ! grep -E "get (secrets|persistentvolumeclaims) (-A|--all-namespaces) .*jsonpath=\{range" "$LOG"
}

@test "a live object of the release that is neither rendered, kept nor in the manifest stops the run; one already kept does not" {
  orphans
  echo "secret your-service-name-dev stray" >> "$LIVE"
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"Secret/your-service-name-dev/stray is this release's in the cluster"* ]]
  run ! grep -q -e "^helm upgrade" -e "annotate" "$LOG"
  echo "secret your-service-name-dev stray helm.sh/resource-policy=keep" >> "$ANNOT"
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 0 ]
}

@test "another release's live objects, and Helm's own release Secrets, are not this release's" {
  orphans
  printf '%s\n' "secret your-service-name-dev theirs other your-service-name-dev" \
    "namespace - elsewhere your-service-name another-ns" \
    "secret your-service-name-dev sh.helm.release.v1.your-service-name.v3" >> "$LIVE"
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 0 ]
}

@test "a manifest with objects and a cluster that records none as the release's stops the run before any change" {
  orphans
  : > "$LIVE"
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"records none of them as this release's"* ]]
  run ! grep -q -e "^helm upgrade" -e "annotate" "$LOG"
}

# The bootstrap writes RBAC in the app namespace and in the database's. (It ends a document: the
# stub's chart render follows it, as k8s-bootstrap.sh prints the two.)
boot_rbac() {
  export STUB_BOOT=$'kind: Role\nmetadata:\n  name: d\n  namespace: app-ns\n---\nkind: Role\nmetadata:\n  name: d\n  namespace: app-ns-db\n---\nkind: RoleBinding\nmetadata:\n  name: d\n  namespace: app-ns-db\nroleRef:\n  kind: Role\n  name: d\n---\nkind: ResourceQuota\nmetadata:\n  name: q\n  namespace: app-ns\n---'
}

@test "the admin check covers every namespace the bootstrap writes RBAC to, with escalate and bind" {
  boot_rbac
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --dry-run
  [ "$status" -eq 0 ]
  for check in "get roles -n app-ns" "create roles -n app-ns-db" "patch roles -n app-ns-db" "escalate roles -n app-ns" \
    "escalate roles -n app-ns-db" "create rolebindings -n app-ns-db" "bind roles -n app-ns-db" "create resourcequotas -n app-ns"; do
    grep -q "auth can-i $check$" "$LOG"
  done
  run ! grep -q "auth can-i bind roles -n app-ns$" "$LOG"
}

@test "an identity without RBAC rights in the database namespace is refused before any change" {
  boot_rbac
  for deny in "create roles -n app-ns-db" "bind roles -n app-ns-db" "escalate roles -n app-ns-db"; do
    : > "$LOG"
    STUB_DENY="$deny" run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
    [ "$status" -eq 1 ]
    [[ "$output" == *"cannot '$deny'"*"cluster admin"* ]]
    run ! grep -q -e "^helm upgrade" -e "annotate" -e "apply -f" "$LOG"
  done
}

# The switch is a bootstrap value and the migration passes the bootstrap none: run a copy of the repo
# whose bootstrap values turn gmpClusterRules on and whose chart flavour is gmp.
boot_cluster_rules() {
  local copy="$BATS_TEST_TMPDIR/repo"
  mkdir -p "$copy/infra/k8s" "$copy/tools"
  cp -r "$BATS_TEST_DIRNAME/../../scripts" "$copy/scripts"
  cp -r "$BATS_TEST_DIRNAME/../../infra/k8s/bootstrap" "$copy/infra/k8s/bootstrap"
  cp -r "$BATS_TEST_DIRNAME/../../charts" "$copy/charts"
  cp "$BATS_TEST_DIRNAME/../../tools/pins.tsv" "$copy/tools/pins.tsv"
  sed -i 's/^gmpClusterRules: false/gmpClusterRules: true/' "$copy/infra/k8s/bootstrap/values.yaml"
  sed -i 's/^  flavour: prometheus-operator/  flavour: gmp/' "$copy/charts/your-service-name/values.yaml"
  SCRIPT="$copy/scripts/migrate-to-bootstrap.sh"
  export STUB_BOOT=$'kind: Role\nmetadata:\n  name: d\n  namespace: app-ns'
  export STUB_CLUSTER_RULES=$'apiVersion: monitoring.googleapis.com/v1\nkind: ClusterRules\nmetadata:\n  name: kube-alerts\nspec:\n  groups: []'
}

@test "a ClusterRules in the bootstrap render needs get, create and patch on it, cluster-wide" {
  boot_cluster_rules
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --dry-run
  [ "$status" -eq 0 ]
  for verb in get create patch; do grep -q "auth can-i $verb clusterrules.monitoring.googleapis.com --all-namespaces$" "$LOG"; done
}

@test "an identity without a ClusterRules right is refused before any change, naming it" {
  boot_cluster_rules
  for verb in get create patch; do
    : > "$LOG"
    STUB_DENY="$verb clusterrules" run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
    [ "$status" -eq 1 ]
    [[ "$output" == *"cannot '$verb clusterrules.monitoring.googleapis.com --all-namespaces'"*"cluster admin"* ]]
    run ! grep -q -e "^helm upgrade" -e "annotate" -e "apply -f" "$LOG"
  done
}

@test "an identity with every right, ClusterRules included, goes past the rights check" {
  boot_cluster_rules
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 0 ]
  # Each ClusterRules right was asked, before the first upgrade.
  upgrade="$(grep -n "^helm upgrade" "$LOG" | head -n 1 | cut -d: -f1)"
  [ -n "$upgrade" ]
  for verb in get create patch; do
    can="$(grep -n "auth can-i $verb clusterrules.monitoring.googleapis.com --all-namespaces$" "$LOG" | head -n 1 | cut -d: -f1)"
    [ -n "$can" ]
    [ "$can" -lt "$upgrade" ]
  done
  grep -q "apply -f -" "$LOG"
}

@test "the cluster-scoped kinds are checked cluster-wide, never in the context's namespace" {
  boot_cluster_rules
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --dry-run
  [ "$status" -eq 0 ]
  grep -q "auth can-i create namespaces --all-namespaces$" "$LOG"
  grep -q "auth can-i patch clusterrules.monitoring.googleapis.com --all-namespaces$" "$LOG"
  run ! grep -E "auth can-i [a-z]+ (namespaces|clusterrules[^ ]*)( -n [^ ]+)?$" "$LOG"
}

@test "a kind in the bootstrap render whose rights the script does not check stops the run before any change" {
  export STUB_BOOT=$'kind: Role\nmetadata:\n  name: d\n  namespace: app-ns\n---\nkind: ClusterRole\nmetadata:\n  name: c'
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --yes
  [ "$status" -eq 1 ]
  [[ "$output" == *"renders a ClusterRole"*"stopping before any change"* ]]
  run ! grep -q -e "^helm upgrade" -e "annotate" -e "apply -f" "$LOG"
}

@test "the ClusterRules right names the API group of the render's apiVersion" {
  boot_cluster_rules
  export STUB_CLUSTER_RULES=$'apiVersion: monitoring.example.test/v2\nkind: ClusterRules\nmetadata:\n  name: kube-alerts'
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --dry-run
  [ "$status" -eq 0 ]
  grep -q "auth can-i create clusterrules.monitoring.example.test --all-namespaces$" "$LOG"
  run ! grep -q "clusterrules.monitoring.googleapis.com" "$LOG"
}

@test "without a ClusterRules in the render no ClusterRules right is checked" {
  boot_rbac
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --dry-run
  [ "$status" -eq 0 ]
  run ! grep -qi "can-i .*clusterrules" "$LOG"
}

@test "a bootstrap values file that turns gmpClusterRules on makes the migration check and apply the ClusterRules" {
  boot_cluster_rules
  sed -i 's/^gmpClusterRules: true/gmpClusterRules: false/' "$BATS_TEST_TMPDIR/repo/infra/k8s/bootstrap/values.yaml"
  printf 'gmpClusterRules: true\n' > "$BATS_TEST_TMPDIR/b.yaml"
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --bootstrap-values "$BATS_TEST_TMPDIR/b.yaml" --yes
  [ "$status" -eq 0 ]
  for verb in get create patch; do grep -q "auth can-i $verb clusterrules.monitoring.googleapis.com --all-namespaces$" "$LOG"; done
  grep -q "kind: ClusterRules" "$APPLIED"
}

@test "a bootstrap values file that renames the deployer reaches the step-2 apply, not the committed name" {
  export STUB_BOOT_SUBJECT=1
  printf 'deployer:\n  name: renamed-deployer\n' > "$BATS_TEST_TMPDIR/b.yaml"
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --bootstrap-values "$BATS_TEST_TMPDIR/b.yaml" --yes
  [ "$status" -eq 0 ]
  grep -q "subject: renamed-deployer" "$APPLIED"
  run ! grep -q "subject: your-deployer" "$APPLIED"
}

@test "each --values reaches the bootstrap render as --chart-values, in order, and a bootstrap file as --values" {
  printf 'a: 1\n' > "$BATS_TEST_TMPDIR/c2.yaml"
  printf 'b: 1\n' > "$BATS_TEST_TMPDIR/b.yaml"
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --values "$BATS_TEST_TMPDIR/c2.yaml" --bootstrap-values "$BATS_TEST_TMPDIR/b.yaml" --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"--chart-values $BATS_TEST_TMPDIR/secret.yaml --chart-values $BATS_TEST_TMPDIR/c2.yaml --values $BATS_TEST_TMPDIR/b.yaml"* ]]
  grep -q -e "-f $BATS_TEST_TMPDIR/b.yaml" "$LOG"
}

@test "a missing --bootstrap-values file is refused before any write" {
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --bootstrap-values "$BATS_TEST_TMPDIR/none.yaml" --yes
  [ "$status" -eq 2 ]
  [[ "$output" == *"--bootstrap-values needs an existing file"* ]]
  [ -z "$(cat "$LOG")" ]
}

@test "--set and --set-string reach the bootstrap render as --chart-set and --chart-set-string" {
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --set fullnameOverride=x --set-string rbac.x=y --dry-run
  [ "$status" -eq 0 ]
  [[ "$output" == *"--chart-set fullnameOverride=x --chart-set-string rbac.x=y | kubectl"* ]]
}

@test "--set images.* is still accepted and reaches the upgrade" {
  d="sha256:$(printf 'a%.0s' {1..64})"
  run bash "$SCRIPT" dev --context kind-x "${V[@]}" --set "images.backend.digest=$d" --yes
  [ "$status" -eq 0 ]
  grep -q "^helm upgrade .*--set images.backend.digest=$d" "$LOG"
}
