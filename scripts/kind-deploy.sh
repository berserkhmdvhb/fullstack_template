#!/usr/bin/env bash
# your-service-name — adaptable service template (see wiki/getting-started.md)
# ===========================================================================
# kind-deploy.sh — deploy and test the chart in the local kind cluster `fst`
# ===========================================================================
# The kind job's one sequence: CI (.github/workflows/reusable-kind.yml) and `make kind-deploy` run
# this script. It targets fst only, through .local/kind/kubeconfig (an inherited KUBECONFIG is
# ignored, and any other context is refused), with a pinned kubectl within one minor of the server.
#
#   1. Build the three images through the images job's build path (scripts/ci/build-image.sh), load
#      them into fst and remove the local tags. No registry, no push.
#   2. As cluster admin: the bootstrap for `local` (scripts/k8s-bootstrap.sh) in the release
#      namespace and in a second one for ct, and the Gateway API CRDs from their pinned source.
#   3. ct lint, then ct install (each ci/ values file, with values.local.yaml) in the ct namespace;
#      ct's own helm calls carry the deployer's identity, which the run checks.
#   4. The release: with --base REF, the chart of REF first (with its own values files, and only the
#      overrides its values.yaml knows), a row written to the database, then the upgrade to this
#      checkout's chart; else this checkout's chart. Then helm test, and a backup and its verification.
#   5. The WAF on (an upgrade), its Pod checked against the restricted profile, helm test again.
#   6. The deploy-ops checks of the deployer identity.
# Every install, upgrade, test and status runs as the bootstrap's deployer (helm --kube-as-user /
# --kube-as-group), never as admin. Secrets are generated once into .local/kind (gitignored).
#
# Usage: kind-deploy.sh [--base REF]
#   --base REF   install the chart at REF first, then upgrade to this checkout's (the upgrade test)
# Env: PYTHON (a Python with PyYAML for the image matrix). Files: .local/kind (scripts/_kind.sh).
# ===========================================================================
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=/dev/null
source "$ROOT/scripts/_kind.sh"
CHART="$ROOT/charts/your-service-name"
ENV_NAME=local
RELEASE=your-service-name
NS="$RELEASE-$ENV_NAME"
CT_NS="$RELEASE-ct"
GROUP=system:authenticated

die() { echo "kind-deploy: $*" >&2; exit "${2:-1}"; }
step() { printf '\n==> %s\n' "$*"; }
ok() { echo "ok   $*"; }
usage() { sed -n '/^# Usage:/,/^# Env:/s/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; }

base=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --base)
      [[ $# -ge 2 && "$2" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ ]] || die "--base needs a commit or ref" 2
      base="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option '$1' (try --help)" 2 ;;
  esac
done

# ---- The guards: fst's kubeconfig only, its one context, a kubectl within one minor ----------
[[ -f "$KIND_DIR/kubeconfig" ]] || die "no $KIND_DIR/kubeconfig: run make kind-up first"
export KUBECONFIG="$KIND_DIR/kubeconfig"
unset HELM_KUBECONTEXT HELM_KUBEAPISERVER HELM_KUBETOKEN HELM_KUBEASUSER HELM_KUBEASGROUPS
current="$(kubectl config current-context)"
[[ "$current" == "$CONTEXT" ]] || die "refusing: the current context is '$current', not $CONTEXT"
contexts="$(kubectl config get-contexts -o name)"
[[ "$contexts" == "$CONTEXT" ]] || die "refusing: $KUBECONFIG holds other contexts than $CONTEXT"

minor() { jq -r ".$1.minor" <<<"$2" | tr -cd '0-9'; }
# Any kubectl reads the server's version (its skew warning is moot here: the leg's own is picked next).
server="$(minor serverVersion "$(kubectl version -o json 2>/dev/null)")"
pin="$(awk -F'\t' -v m="$server" '!/^#/ && $3 ~ ("^kindest/node:v1\\." m "\\.") {print $2}' "$ROOT/tools/kind-nodes.tsv")"
[[ -n "$pin" ]] || die "no pinned kubectl for a 1.$server server (tools/kind-nodes.tsv)"
WORK="$(mktemp -d)"
pf_pid=""
built=()
cleanup() {
  [[ -z "$pf_pid" ]] || kill "$pf_pid" 2>/dev/null || true
  local ref
  for ref in "${built[@]}"; do docker image rm "$ref" >/dev/null 2>&1 || true; done
  rm -rf "$WORK"
}
trap cleanup EXIT
mkdir -p "$WORK/bin"
ln -s "$(command -v "$pin")" "$WORK/bin/kubectl"
export PATH="$WORK/bin:$PATH"
client="$(minor clientVersion "$(kubectl version -o json)")"
(( client - server <= 1 && server - client <= 1 )) ||
  die "version skew: $pin is 1.$client, the server 1.$server (more than one minor apart)"
echo "kind-deploy: using $pin (1.$client client, 1.$server server)"

# ---- The identities ---------------------------------------------------------------------------
cd "$ROOT"
kc() { kubectl --context "$CONTEXT" "$@"; }
# shellcheck disable=SC2016 # yq's own variables, not the shell's
deployer="$(yq ea '. as $i ireduce ({}; . * $i) | .deployer.name' infra/k8s/bootstrap/values.yaml "infra/k8s/bootstrap/values.$ENV_NAME.yaml")"
as_deployer=(--as "$deployer" --as-group "$GROUP")
helm_as() { helm --kube-context "$CONTEXT" --kube-as-user "$deployer" --kube-as-group "$GROUP" "$@"; }
fullname="$RELEASE"
backend_port="$(yq '.config.BACKEND_PORT' "$CHART/values.yaml")"
api_version="$(yq '.config.API_VERSION' "$CHART/values.yaml")"

# The database password, generated once and kept with the cluster (the data outlives a run).
values="$KIND_DIR/values.yaml"
if [[ ! -f "$values" ]]; then
  ( umask 077
    printf 'secrets:\n  dbPassword: "%s"\n  statsPass: "%s"\n' \
      "$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')" "$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')" > "$values" )
fi
# The values every install gets on top of the chart's own values.local.yaml, one argument per line: the
# images built here and the scrape peers (a Pod of the release namespace, so helm test's collector Job
# proves them). Only what that chart's values.yaml knows, so the base commit's chart is installed with
# its own values and this run's overrides it understands.
kind_args() { # chart dir, namespace
  local chart="$1" ns="$2" id
  for id in "${image_ids[@]}"; do
    if yq -e ".images | has(\"$id\")" "$chart/values.yaml" >/dev/null 2>&1; then
      printf '%s\n' --set-string "images.$id.registry=" --set-string "images.$id.repository=kind-$CLUSTER/$id" \
        --set-string "images.$id.tag=$tag"
    fi
  done
  if yq -e '.networkPolicy | (has("monitoringNamespaceSelector") and has("monitoringPodSelector"))' "$chart/values.yaml" >/dev/null 2>&1; then
    printf '%s\n' --set-string "networkPolicy.monitoringNamespaceSelector.kubernetes\\.io/metadata\\.name=$ns" \
      --set-string "networkPolicy.monitoringPodSelector.metrics-collector=true"
  fi
}

# ---- 1. Images ------------------------------------------------------------------------------------
step "build the images (scripts/ci/build-image.sh) and load them into $CLUSTER"
python="${PYTHON:-}"
[[ -n "$python" ]] || { if [[ -x .venv/bin/python ]]; then python=.venv/bin/python; else python=python3; fi; }
head_sha="$(git rev-parse HEAD)"
matrix="$(GITHUB_REF_NAME="${GITHUB_REF_NAME:-local}" GITHUB_REF_TYPE="${GITHUB_REF_TYPE:-branch}" \
  GITHUB_SHA="${GITHUB_SHA:-$head_sha}" GITHUB_REPOSITORY_OWNER="${GITHUB_REPOSITORY_OWNER:-local}" \
  GITHUB_REPOSITORY="${GITHUB_REPOSITORY:-local/local}" BUILD_ARM64=false \
  "$python" .github/scripts/container_matrix.py --matrix | sed -n 1p)"
tag="kind-$(date +%s)"
image_ids=()
while read -r id; do
  entry="$(ID="$id" jq -c '.include[] | select(.id == env.ID)' <<<"$matrix")"
  ref="kind-$CLUSTER/$id:$tag"
  built+=("$ref")
  SOURCE_URL="${SOURCE_URL:-https://example.invalid/local/kind}" VCS_REF="${VCS_REF:-$head_sha}" \
    VCS_BRANCH="${VCS_BRANCH:-local}" bash scripts/ci/build-image.sh "$entry" "$ref"
  kind load docker-image --name "$CLUSTER" "$ref"
  docker image rm "$ref" >/dev/null
  image_ids+=("$id")
done < <(jq -r '.include[].id' <<<"$matrix")
ok "images built and loaded: $(jq -r '[.include[].id] | join(", ")' <<<"$matrix") ($tag)"

# ---- 2. The admin bootstrap -------------------------------------------------------------------
step "the admin bootstrap ($ENV_NAME) for $NS and $CT_NS, and the Gateway API CRDs"
for ns in "$NS" "$CT_NS"; do
  scripts/k8s-bootstrap.sh "$ENV_NAME" --release "$RELEASE" --namespace "$ns" | kc apply -f -
done
# The pinned source and sha256 of charts/your-service-name/ci/crds/sources.tsv, from the crd-sources cache.
crd_sha="$(awk -F'\t' '$1 == "gateway-api" {print $4}' "$CHART/ci/crds/sources.tsv")"
crd="$ROOT/.tools/crd-sources/$crd_sha"
[[ -f "$crd" ]] || die "$crd is missing: run scripts/tools.sh install crd-sources"
[[ "$(sha256sum "$crd" | cut -d' ' -f1)" == "$crd_sha" ]] || die "$crd does not match its pinned sha256"
kc apply --server-side -f "$crd" >/dev/null
ok "bootstrap applied; Gateway API CRDs applied"

# ---- 3. ct lint, ct install ---------------------------------------------------------------------
step "ct lint and ct install (the deployer, in $CT_NS)"
ct lint --config .github/ct.yaml --charts "$CHART"
# A release an earlier, interrupted run left behind would clash with ct's (the fixed ServiceAccount name).
leftovers="$(helm_as list -n "$CT_NS" -q --deployed --failed --pending)"
for leftover in $leftovers; do helm_as uninstall "$leftover" -n "$CT_NS" --wait; done
mapfile -t ct_set < <(kind_args "$CHART" "$CT_NS")
# --debug prints each helm call ct makes, so the run checks that ct's own helm install, test and
# uninstall carry the deployer's identity (--helm-extra-args reaches every one of them).
ct install --config .github/ct.yaml --charts "$CHART" --namespace "$CT_NS" --debug \
  --helm-extra-args "--timeout 600s --kube-as-user $deployer --kube-as-group $GROUP" \
  --helm-extra-set-args "--values=$CHART/values.local.yaml --values=$values ${ct_set[*]}" 2>&1 | tee "$WORK/ct.log"
ct_tests="$(grep -E "^(>>> )?helm test " "$WORK/ct.log" || true)"
[[ "$(wc -l <<<"$ct_tests")" -ge 3 && -n "$ct_tests" ]] || die "ct ran fewer helm tests than its three ci/ values files"
if grep -v -- "--kube-as-user $deployer --kube-as-group $GROUP" <<<"$ct_tests"; then
  die "ct ran a helm test without the deployer's identity (above)"
fi
ok "ct install passed for every ci/ values file; its $(wc -l <<<"$ct_tests") helm tests ran as $deployer"

# ---- 4. The release, and the upgrade test --------------------------------------------------------
deploy() { # chart dir, extra helm args...
  local chart="$1"; shift
  local -a args
  mapfile -t args < <(kind_args "$chart" "$NS")
  helm_as upgrade --install "$RELEASE" "$chart" -n "$NS" -f "$chart/values.local.yaml" -f "$values" \
    "${args[@]}" --history-max 5 --wait --wait-for-jobs --timeout 10m "$@" \
    2>&1 | tee "$WORK/helm.log"
  ! grep -qi forbidden "$WORK/helm.log" || die "the deployer was refused: $(grep -i forbidden "$WORK/helm.log")"
}
migrated() { # the migration Job of the current revision succeeded
  local rev job
  rev="$(helm_as history "$RELEASE" -n "$NS" --max 1 -o json | jq -r '.[0].revision')"
  job="$(kc -n "$NS" get jobs -l app.kubernetes.io/component=migration -o json |
    jq -r --arg r "-r$rev" '.items[] | select(.metadata.name | endswith($r)) | select(.status.succeeded == 1) | .metadata.name')"
  [[ -n "$job" ]] || die "no succeeded migration Job for revision $rev"
  ok "migration Job $job (revision $rev) succeeded"
}
psql_db() {
  kc -n "$NS-db" exec "$fullname-postgresql-0" -- psql -X -q -t -A -v ON_ERROR_STOP=1 \
    -U "$(yq '.postgresql.auth.user' "$CHART/values.yaml")" -d "$(yq '.postgresql.auth.database' "$CHART/values.yaml")" -c "$1"
}
uids() { # the namespaces and the database's volumes, by uid
  kc get namespace "$NS" "$NS-db" -o jsonpath='{range .items[*]}{.metadata.name}={.metadata.uid}{"\n"}{end}'
  local pv
  for pv in $(kc -n "$NS-db" get pvc -o jsonpath='{.items[*].spec.volumeName}'); do
    kc get pv "$pv" -o jsonpath='{.metadata.name}={.metadata.uid}{"\n"}'
  done
}

if [[ -n "$base" ]]; then
  commit="$(git rev-parse --verify --quiet "$base^{commit}")" || die "--base $base is not a commit here"
  step "install the chart of the base commit $base, as the deployer"
  mkdir -p "$WORK/base"
  git archive "$commit" charts/your-service-name | tar -x -C "$WORK/base"
  base_chart="$WORK/base/charts/your-service-name"
  # A base chart from before the PostgreSQL Pod mounted the backup PVC cannot bind that PVC before
  # the first backup on a WaitForFirstConsumer class, so its --wait would time out: install it
  # without backups (the upgrade below turns them on).
  base_extra=()
  if ! helm template "$RELEASE" "$base_chart" -n "$NS" -f "$base_chart/values.local.yaml" -f "$values" \
      --show-only templates/postgresql-statefulset.yaml 2>/dev/null | grep -q 'claimName: .*-pg-backup-pvc'; then
    echo "kind-deploy: the base chart does not bind its backup PVC at install: installing it without backups"
    base_extra=(--set postgresql.backup.enabled=false)
  fi
  deploy "$base_chart" "${base_extra[@]}"
  migrated
  marker="kind-$tag"
  psql_db "CREATE TABLE IF NOT EXISTS kind_upgrade_check (marker text); INSERT INTO kind_upgrade_check VALUES ('$marker')"
  uids > "$WORK/before"
  ok "row written; namespaces and volumes recorded: $(paste -sd' ' "$WORK/before")"
  step "upgrade to this checkout's chart, as the deployer"
  deploy "$CHART"
  migrated
  uids > "$WORK/after"
  # Every namespace and volume of before is still there, by uid (the upgrade may add the backup PVC).
  ! grep -vxFf "$WORK/after" "$WORK/before" || die "a namespace or volume (above) was replaced by the upgrade"
  ok "no namespace or volume was deleted ($(wc -l < "$WORK/before") kept, $(wc -l < "$WORK/after") after)"
  [[ "$(psql_db "SELECT count(*) FROM kind_upgrade_check WHERE marker = '$marker'")" == 1 ]] ||
    die "the row written before the upgrade is gone"
  ok "the database row survived the upgrade"
else
  step "install this checkout's chart, as the deployer"
  deploy "$CHART"
  migrated
fi
step "helm test, as the deployer"
helm_as test "$RELEASE" -n "$NS" --timeout 5m
ok "helm test passed"

step "a backup, then its verification (Jobs from the two CronJobs, as admin)"
# The backup PVC was bound at install (the PostgreSQL Pod mounts it read-only); the backup Job
# writes a dump on that node and the verify Job restores the newest one into a scratch database.
db_ns="$NS-db"
run_job() { # name, cronjob, the log line it must print
  kc -n "$db_ns" delete job "$1" --ignore-not-found >/dev/null
  kc -n "$db_ns" create job "$1" --from="cronjob/$2" >/dev/null
  if ! kc -n "$db_ns" wait --for=condition=complete "job/$1" --timeout=300s >/dev/null; then
    kc -n "$db_ns" logs "job/$1" --all-containers >&2 || true
    die "the Job $1 (from $2) did not complete"
  fi
  kc -n "$db_ns" logs "job/$1" > "$WORK/$1.log"
  grep -q "$3" "$WORK/$1.log" || die "the Job $1 did not print '$3': $(cat "$WORK/$1.log")"
}
run_job kind-backup "$fullname-pg-backup" "Backup created: /backup/"
dump="$(sed -n 's/^Backup created: //p' "$WORK/kind-backup.log")"
run_job kind-backup-verify "$fullname-pg-backup-verify" "Backup verification completed successfully."
grep -qx "Latest backup: $dump" "$WORK/kind-backup-verify.log" || die "the verify Job did not read $dump"
kc -n "$db_ns" delete job kind-backup kind-backup-verify >/dev/null
ok "backup Job wrote $dump; the verify Job restored it"

# ---- 5. The WAF ---------------------------------------------------------------------------------
step "the WAF on (an upgrade, as the deployer)"
deploy "$CHART" --set waf.enabled=true
[[ "$(kc get namespace "$NS" -o jsonpath='{.metadata.labels.pod-security\.kubernetes\.io/enforce}')" == restricted ]] ||
  die "$NS does not enforce the restricted profile"
waf="$(kc -n "$NS" get pods -l app.kubernetes.io/component=waf -o json | jq '.items[0]')"
[[ "$(jq -r '.spec.securityContext.runAsUser' <<<"$waf")" == 65532 ]] || die "the WAF Pod spec does not run as 65532"
running_uid="$(jq -r '.status.containerStatuses[0].user.linux.uid // empty' <<<"$waf")"
[[ "$running_uid" == 65532 ]] || die "the running WAF container reports uid '${running_uid}', not 65532"
[[ "$(jq -r '.status.conditions[] | select(.type == "Ready") | .status' <<<"$waf")" == True ]] || die "the WAF Pod is not Ready"
[[ "$(jq -r '.status.containerStatuses[0].restartCount' <<<"$waf")" == 0 ]] || die "the WAF container restarted"
ok "the WAF Pod was admitted under enforce=restricted, runs as uid 65532 (spec and running container), probes pass"
helm_as test "$RELEASE" -n "$NS" --timeout 5m
ok "helm test passed with the WAF: a benign request 200, an XSS probe 403"

# ---- 6. The deploy-ops checks of the deployer ----------------------------------------------------
step "the deployer's deploy-ops rights ($deployer)"
helm_as status "$RELEASE" -n "$NS" >/dev/null
ok "helm status"
kc -n "$NS" logs "deployment/$fullname-backend" --tail 1 "${as_deployer[@]}" >/dev/null
ok "pods/log"
kc -n "$NS" port-forward "svc/$fullname-backend" ":$backend_port" "${as_deployer[@]}" > "$WORK/pf.log" 2>&1 &
pf_pid=$!
port=""
for _ in $(seq 50); do
  port="$(sed -n 's/^Forwarding from 127\.0\.0\.1:\([0-9]*\) .*/\1/p' "$WORK/pf.log")"
  [[ -z "$port" ]] || break
  sleep 0.2
done
[[ -n "$port" ]] || die "the port-forward did not start: $(cat "$WORK/pf.log")"
code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "http://127.0.0.1:$port/api/$api_version/health")"
kill "$pf_pid"; wait "$pf_pid" 2>/dev/null || true; pf_pid=""
[[ "$code" == 200 ]] || die "the health check over the port-forward answered $code"
ok "port-forward (health 200)"
if kc -n "$NS" exec "deployment/$fullname-backend" "${as_deployer[@]}" -- true > "$WORK/exec.log" 2>&1; then
  die "the deployer could exec into a Pod"
fi
grep -qi forbidden "$WORK/exec.log" || die "exec failed, but not as Forbidden: $(cat "$WORK/exec.log")"
ok "exec refused (forbidden)"
# Secrets: readable only in the namespaces the bootstrap bound the deployer in, refused everywhere else.
bound="$(kc get rolebindings -A -o json | jq -r '.items[] | select(.metadata.name == "bootstrap-deployer") | .metadata.namespace')"
for ns in $(kc get namespaces -o jsonpath='{.items[*].metadata.name}'); do
  for verb in get list; do
    answer="$(kc auth can-i "$verb" secrets -n "$ns" "${as_deployer[@]}" || true)"
    if grep -qx "$ns" <<<"$bound"; then want=yes; else want=no; fi
    [[ "$answer" == "$want" ]] || die "$verb secrets in $ns: $answer, want $want"
  done
done
ok "secrets get/list: yes only in $(paste -sd' ' <<<"$bound"); no in every other namespace"
if kc get secrets --all-namespaces "${as_deployer[@]}" > "$WORK/secrets.log" 2>&1; then
  die "the deployer could list secrets cluster-wide"
fi
grep -qi forbidden "$WORK/secrets.log" || die "the cluster-wide list failed, but not as Forbidden"
for ns in kube-system default; do
  if kc -n "$ns" get secrets "${as_deployer[@]}" > "$WORK/secrets.log" 2>&1; then die "the deployer read secrets in $ns"; fi
  grep -qi forbidden "$WORK/secrets.log" || die "reading secrets in $ns failed, but not as Forbidden"
done
ok "secrets refused cluster-wide, in kube-system and in default"
step "done: $RELEASE in $NS on $CLUSTER (KUBECONFIG=$KUBECONFIG)"
