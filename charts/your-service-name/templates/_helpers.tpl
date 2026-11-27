{{/*
   _helpers.tpl — naming, labels, selectors, and cross-namespace helpers.

   THE ROOT-CAUSE FIX lives here: chart.selectorLabels is STABLE IDENTITY ONLY
   (name + release/instance). `environment`, region, cluster and image tag are
   deliberately excluded, so a Deployment/StatefulSet selector never changes
   between environments and env switches are idempotent. Volatile values appear
   only in metadata labels (chart.labels), never in a selector.
*/}}

{{- define "chart.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "chart.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "chart.chartLabel" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version -}}
{{- end -}}

{{/* Full labels: stable identity + managed-by + volatile metadata (environment). */}}
{{- define "chart.labels" -}}
app.kubernetes.io/name: {{ include "chart.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
helm.sh/chart: {{ include "chart.chartLabel" . }}
environment: {{ .Values.environment }}
{{- end -}}

{{/* Selector labels — STABLE IDENTITY ONLY. Never add environment/region/tag here. */}}
{{- define "chart.selectorLabels" -}}
app.kubernetes.io/name: {{ include "chart.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "chart.selector" -}}
matchLabels:
{{- include "chart.selectorLabels" . | nindent 2 }}
{{- end -}}

{{- define "chart.serviceAccountName" -}}
{{- .Values.serviceAccount.name -}}
{{- end -}}

{{/* db namespace = <app-namespace>-<suffix>. Derived so it tracks the release. */}}
{{/*
   The backup and verify jobs run on the PostgreSQL Pod's node: the backup PVC is ReadWriteOnce, and
   that Pod mounts it (read-only) from the install on. Emits at column 0 (use `| nindent`).
*/}}
{{/*
   The group of the backup volume: PostgreSQL's fsGroup, and the backup and verify jobs' too. Every Pod
   that mounts the volume has the same fsGroup (with OnRootMismatch), so the kubelet never flips its
   group between them, and the jobs (uid 10001) write the group-owned volume. The data volume is
   mounted by PostgreSQL alone.
*/}}
{{- define "chart.pgFsGroup" -}}999{{- end -}}

{{- define "chart.backupAffinity" -}}
affinity:
  podAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      - topologyKey: kubernetes.io/hostname
        labelSelector:
          matchLabels:
            app.kubernetes.io/component: postgresql
            app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "chart.dbNamespace" -}}
{{- printf "%s-%s" .Release.Namespace .Values.namespaces.dbSuffix -}}
{{- end -}}

{{/* In-cluster DNS name of the internal Postgres Service (in the db namespace). */}}
{{- define "chart.dbHost" -}}
{{- printf "%s-postgresql.%s.svc.cluster.local" (include "chart.fullname" .) (include "chart.dbNamespace" .) -}}
{{- end -}}

{{/*
   Container image ref.

   Digest wins over tag: when `.image.digest` is set the ref is rendered as
   `registry/repository@sha256:<digest>` (tag dropped) — an immutable, tamper-
   evident pin. When digest is empty the ref falls back to `repository:tag` (bare
   `repo:tag` when registry is empty — the local overlay trick). `digest` defaults
   to "" in values.yaml, so existing dev/qa/local renders are unchanged; only the
   production deploy sets it (.github/workflows/deploy.yml, via scripts/ci/deploy-env.sh).
*/}}
{{- define "chart.image" -}}
{{- $img := .image -}}
{{- $repo := $img.repository -}}
{{- if $img.registry -}}
{{- $repo = printf "%s/%s" $img.registry $img.repository -}}
{{- end -}}
{{- if $img.digest -}}
{{- printf "%s@%s" $repo $img.digest -}}
{{- else -}}
{{- printf "%s:%s" $repo $img.tag -}}
{{- end -}}
{{- end -}}

{{/*
   API-path backend Service name. When the WAF is enabled the API surface is
   fronted by the Coraza proxy (templates/waf-proxy.yaml), so API paths target
   `<name>-waf`; otherwise they go straight to `<name>-backend`. Keeps the
   waf.enabled swap in one place (chart.edgeRoutes calls this for every backend route).
*/}}
{{- define "chart.apiBackendService" -}}
{{- if .Values.waf.enabled -}}
{{- printf "%s-waf" (include "chart.fullname" .) -}}
{{- else -}}
{{- printf "%s-backend" (include "chart.fullname" .) -}}
{{- end -}}
{{- end -}}

{{/* The port of chart.apiBackendService: the WAF's when waf.enabled, else the backend's. */}}
{{- define "chart.apiBackendPort" -}}
{{- if .Values.waf.enabled }}{{ .Values.waf.service.port }}{{ else }}{{ include "chart.backendPort" . }}{{ end -}}
{{- end -}}

{{/* The URL the frontend server calls the API at (BACKEND_URL): through the WAF when it is on. */}}
{{- define "chart.backendURL" -}}
{{- printf "http://%s:%s" (include "chart.apiBackendService" .) (include "chart.apiBackendPort" .) -}}
{{- end -}}

{{/* The port the WAF forwards to: waf.upstream.port, else the backend port. */}}
{{- define "chart.wafUpstreamPort" -}}
{{- .Values.waf.upstream.port | default (include "chart.backendPort" .) -}}
{{- end -}}

{{/* The backend and frontend Service ports: one source (config.*_PORT) for the Services and the edge. */}}
{{- define "chart.backendPort" -}}{{ .Values.config.BACKEND_PORT }}{{- end -}}
{{- define "chart.frontendPort" -}}{{ .Values.config.FRONTEND_PORT }}{{- end -}}

{{/* The backend's metrics port (config.METRICS_PORT): its own container and Service port, never the API's. */}}
{{- define "chart.metricsPort" -}}{{ .Values.config.METRICS_PORT }}{{- end -}}

{{/* The database port: config.DB_PORT, for the server, its Service, the policies and the clients. */}}
{{- define "chart.dbPort" -}}{{ .Values.config.DB_PORT }}{{- end -}}

{{/*
   The probe paths, one place: the Deployments' probes and the GKE load balancer health checks read
   them. Readiness checks the schema; startup and liveness are process-only (a unit test pins
   them to the backend's routes).
*/}}
{{- define "chart.backendReadyPath" -}}/api/v1/health{{- end -}}
{{- define "chart.backendLivePath" -}}/api/v1/healthz{{- end -}}
{{- define "chart.frontendReadyPath" -}}/api/healthz{{- end -}}

{{/*
   A memory quantity in bytes (Kubernetes suffixes Ki Mi Gi Ti, K M G T, or plain bytes), so that
   1Gi and 1024Mi compare equal. Fails on anything else, naming the value.
*/}}
{{- define "chart.bytes" -}}
{{- $q := toString (ternary (int64 .) . (kindIs "float64" .)) -}}
{{- $number := regexFind "^[0-9]+" $q -}}
{{- $suffix := trimPrefix $number $q -}}
{{- $factors := dict "" 1 "K" 1000 "M" 1000000 "G" 1000000000 "T" 1000000000000 "Ki" 1024 "Mi" 1048576 "Gi" 1073741824 "Ti" 1099511627776 -}}
{{- if or (not $number) (not (hasKey $factors $suffix)) -}}
{{- fail (printf "resources: %q is not a memory quantity (use whole numbers with Ki, Mi, Gi, Ti, K, M, G, T or plain bytes)" $q) -}}
{{- end -}}
{{- mul (int64 $number) (int64 (get $factors $suffix)) -}}
{{- end -}}

{{/*
   A container's resources from a standard block {requests, limits}: both as set, plus a memory limit
   equal to the memory request when limits.memory is unset. No CPU limit appears unless limits.cpu is
   set; any other limit (ephemeral-storage, hugepages-*) passes through. A memory limit that differs
   from the request fails the render (the memory is guaranteed and never overcommitted). Emits at
   column 0 (use `| nindent`).
*/}}
{{- define "chart.resources" -}}
{{- $requests := required "resources: requests is required" .requests -}}
{{- $memory := required "resources: requests.memory is required" $requests.memory -}}
{{- $limits := deepCopy (default dict .limits) -}}
{{- if and (hasKey $limits "memory") (ne (include "chart.bytes" (index $limits "memory")) (include "chart.bytes" $memory)) -}}
{{- fail (printf "resources: limits.memory (%v) must equal requests.memory (%v); leave limits.memory unset to use the request" (index $limits "memory") $memory) -}}
{{- end -}}
{{- $_ := set $limits "memory" $memory -}}
requests:
{{- toYaml $requests | nindent 2 }}
limits:
{{- toYaml $limits | nindent 2 }}
{{- end -}}

{{/*
   terminationGracePeriodSeconds: the preStop sleep + the drain + the margin (values.yaml, shutdown).
   The backend's drain is Uvicorn's graceful timeout (config.BACKEND_GRACEFUL_TIMEOUT, read by
   backend/serve.py); the frontend's is shutdown.frontendDrainSeconds.
*/}}
{{- define "chart.backendTerminationGrace" -}}
{{- add (int .Values.shutdown.preStopSeconds) (int .Values.config.BACKEND_GRACEFUL_TIMEOUT) (int .Values.shutdown.marginSeconds) -}}
{{- end -}}
{{- define "chart.frontendTerminationGrace" -}}
{{- add (int .Values.shutdown.preStopSeconds) (int .Values.shutdown.frontendDrainSeconds) (int .Values.shutdown.marginSeconds) -}}
{{- end -}}

{{/*
   The edge peers of the NetworkPolicy, per edge.mode, as a JSON array (read it back with
   fromJsonArray): gateway, each parentRefs namespace once (none named: the release namespace) plus
   the Google load balancer ranges when edge.gateway.gke.enabled; tunnel, edge.namespaceSelector or
   the cloudflared namespace; ingress, edge.namespaceSelector (required). edge.extraPeers last.
*/}}
{{- define "chart.edgePeers" -}}
{{- $edge := .Values.edge -}}
{{- $peers := list -}}
{{- if eq $edge.mode "gateway" -}}
{{- $namespaces := list -}}
{{- range $edge.gateway.parentRefs -}}
{{- $namespaces = append $namespaces (default $.Release.Namespace .namespace) -}}
{{- end -}}
{{- range uniq $namespaces -}}
{{- $peers = append $peers (dict "namespaceSelector" (dict "matchLabels" (dict "kubernetes.io/metadata.name" .))) -}}
{{- end -}}
{{- if $edge.gateway.gke.enabled -}}
{{- /* Google's global external Application Load Balancer proxies and health checkers. */ -}}
{{- range list "35.191.0.0/16" "130.211.0.0/22" -}}
{{- $peers = append $peers (dict "ipBlock" (dict "cidr" .)) -}}
{{- end -}}
{{- end -}}
{{- else -}}
{{- if and (eq $edge.mode "ingress") (not $edge.namespaceSelector) -}}
{{- fail "edge.namespaceSelector is required when edge.mode is ingress: set it to the matchLabels of your Ingress controller's namespace, for example {kubernetes.io/metadata.name: <controller-namespace>}" -}}
{{- end -}}
{{- $selector := $edge.namespaceSelector | default (dict "kubernetes.io/metadata.name" "cloudflared") -}}
{{- $peers = append $peers (dict "namespaceSelector" (dict "matchLabels" $selector)) -}}
{{- end -}}
{{- toJson (concat $peers (default list $edge.extraPeers)) -}}
{{- end -}}

{{/*
   The one DNS egress rule: kube-dns (networkPolicy.dns selectors; an empty one means kube-dns in
   kube-system, a set one replaces that) plus optional resolver ipBlocks.
   Emits a list item at column 0; every egress policy includes it with `| nindent 4`.
*/}}
{{- define "chart.dnsEgress" -}}
{{- $dns := .Values.networkPolicy.dns -}}
- to:
    - namespaceSelector:
        matchLabels:
{{- toYaml ($dns.namespaceSelector | default (dict "kubernetes.io/metadata.name" "kube-system")) | nindent 10 }}
      podSelector:
        matchLabels:
{{- toYaml ($dns.podSelector | default (dict "k8s-app" "kube-dns")) | nindent 10 }}
{{- range $dns.ipBlocks }}
    - ipBlock:
        cidr: {{ . | quote }}
{{- end }}
  ports:
    - protocol: UDP
      port: 53
    - protocol: TCP
      port: 53
{{- end -}}

{{/*
   A route's path as cloudflared matches it. A published route becomes an ingress rule whose path is an
   UNANCHORED regular expression, so a bare `/api` would also match `/apix` and `/x/api`. PathPrefix p
   becomes `^p(/|$)`, Exact p becomes `^p$` (dots escaped), and the PathPrefix `/` is the catch-all:
   the empty path, which must be the host's last rule. Called with (dict "path" "/api" "match" "PathPrefix").
*/}}
{{- define "chart.edgeTunnelPath" -}}
{{- if and (eq .match "PathPrefix") (eq .path "/") -}}
{{- else if eq .match "Exact" -}}
{{- printf "^%s$" (regexQuoteMeta .path) -}}
{{- else -}}
{{- printf "^%s(/|$)" (regexQuoteMeta (trimSuffix "/" .path)) -}}
{{- end -}}
{{- end -}}

{{/* The host roles, in render order, as a JSON array (read it back with fromJsonArray): root, plus api when it is set. */}}
{{- define "chart.edgeRoles" -}}
{{- $roles := list "root" -}}
{{- if .Values.edge.hosts.api -}}{{- $roles = append $roles "api" -}}{{- end -}}
{{- toJson $roles -}}
{{- end -}}

{{/*
   The one routes list (edge.routes), resolved for a host role: root serves every route, api (when
   set) the `backend` routes. Emits a JSON array of {path, match, name, port}
   (read it back with fromJsonArray). `backend` is the WAF-aware Service and its port; `frontend`
   is the UI Service. Called with (dict "ctx" . "role" "root|api"); httproute.yaml, ingress.yaml
   and NOTES.txt all render from this, so a path is never written twice.
*/}}
{{- define "chart.edgeRoutes" -}}
{{- $role := .role -}}
{{- $ctx := .ctx -}}
{{- $out := list -}}
{{- range $ctx.Values.edge.routes -}}
{{- if or (eq $role "root") (and (eq $role "api") (eq .service "backend")) -}}
{{- $name := printf "%s-frontend" (include "chart.fullname" $ctx) -}}
{{- $port := include "chart.frontendPort" $ctx -}}
{{- if eq .service "backend" -}}
{{- $name = include "chart.apiBackendService" $ctx -}}
{{- $port = include "chart.apiBackendPort" $ctx -}}
{{- end -}}
{{- $out = append $out (dict "path" .path "match" .match "name" $name "port" (int $port)) -}}
{{- end -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{/*
   Optional imagePullSecrets block; empty => omitted. Emits at column 0 so the
   caller positions it with `| indent N` (6 under a pod spec).
*/}}
{{- define "chart.imagePullSecrets" -}}
{{- if .Values.global.imagePullSecrets }}
imagePullSecrets:
{{- range .Values.global.imagePullSecrets }}
  - name: {{ . | quote }}
{{- end }}
{{- end -}}
{{- end -}}

{{/*
   An ExternalSecret's remoteRef (key, and property when set) for one key name: (list
   secrets.externalSecret <name>). Emits lines at column 0 (use `| nindent`).
*/}}
{{- define "chart.remoteRef" -}}
{{- $es := index . 0 -}}
key: {{ $es.remoteKeyTemplate | replace "{key}" (index . 1) | quote }}
{{- with $es.remoteProperty }}
property: {{ . | quote }}
{{- end }}
{{- end -}}

{{/*
   Name of the app DB secret the backend + migration consume (DB_USER / DB_PASSWORD
   / STATS_PASS): chart.fullname-secret, which the chart creates from the values (local)
   or the ExternalSecret syncs (dev, qa, production), else secrets.existingSecret, a
   Secret managed outside the chart with the same keys (previews) — so the reference
   is always satisfied.
*/}}
{{- define "chart.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- include "chart.fullname" . }}-secret
{{- end -}}
{{- end -}}

{{/*
   The DB_HOST value, in one place (the ConfigMap and the migration Job both render it): loopback
   with the Cloud SQL Auth Proxy sidecar, else the in-cluster Postgres Service, else the managed host.
*/}}
{{- define "chart.dbHostValue" -}}
{{- if .Values.cloudSqlProxy.enabled -}}
127.0.0.1
{{- else if .Values.postgresql.enabled -}}
{{- include "chart.dbHost" . -}}
{{- else -}}
{{- .Values.externalDatabase.host -}}
{{- end -}}
{{- end -}}

{{/*
   The migration Job's database env: its own explicit list, never the app ConfigMap. DB_HOST, DB_PORT
   and DB_NAME come from the same values keys the ConfigMap renders (chart.dbHostValue, config.*);
   emits list items at column 0 (use `| nindent`).
*/}}
{{- define "chart.dbEnv" -}}
- name: DB_HOST
  value: {{ include "chart.dbHostValue" . | quote }}
- name: DB_PORT
  value: {{ .Values.config.DB_PORT | quote }}
- name: DB_NAME
  value: {{ .Values.config.DB_NAME | quote }}
{{- end -}}

{{/* DB_USER and DB_PASSWORD from the app Secret, whichever source holds it (chart.secretName). */}}
{{- define "chart.dbSecretEnv" -}}
{{- range $key := list "DB_USER" "DB_PASSWORD" }}
- name: {{ $key }}
  valueFrom:
    secretKeyRef:
      name: {{ include "chart.secretName" $ }}
      key: {{ $key }}
      {{- /* With IAM authentication the proxy supplies the password, so the key may be absent. */}}
      {{- if and (eq $key "DB_PASSWORD") $.Values.cloudSqlProxy.enabled $.Values.cloudSqlProxy.autoIamAuthn }}
      optional: true
      {{- end }}
{{- end }}
{{- end -}}

{{/*
   Name and key of the Secret holding the in-cluster Postgres password (postgresql.existingSecret,
   else the one the database's ExternalSecret syncs, else the chart's db-credentials). Emits two lines at column 0 (use `| nindent`). One place for
   the StatefulSet and both backup CronJobs.
*/}}
{{- define "chart.pgPasswordRef" -}}
{{- if .Values.postgresql.existingSecret -}}
name: {{ .Values.postgresql.existingSecret }}
key: {{ .Values.postgresql.existingSecretKey }}
{{- else if .Values.secrets.externalSecret.enabled -}}
name: {{ include "chart.fullname" . }}-db-password
key: password
{{- else -}}
name: {{ include "chart.fullname" . }}-db-credentials
key: password
{{- end -}}
{{- end -}}

{{/*
   The other settings the migration runner reads (db/run_migrations.py, db/connect.py): ENV (the
   runner keeps the production database checks), TLS mode and the two timeouts, each only when
   config sets it (the runner has defaults).
*/}}
{{- define "chart.dbOptionalEnv" -}}
{{- range $key := list "ENV" "DB_SSLMODE" "DB_CONNECT_TIMEOUT_SECONDS" "DB_MIGRATION_LOCK_TIMEOUT_MS" }}
{{- if hasKey $.Values.config $key }}
- name: {{ $key }}
  value: {{ get $.Values.config $key | quote }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
   The Cloud SQL Auth Proxy as a native sidecar (an init container with restartPolicy: Always): it
   starts before the containers after it, keeps running, and ends with the Pod, so the migration
   Job completes when the migrate container exits. Listens on loopback, on config.DB_PORT.
*/}}
{{- define "chart.cloudSqlProxyContainer" -}}
{{- $p := .Values.cloudSqlProxy }}
- name: cloud-sql-proxy
  image: {{ include "chart.image" (dict "image" $p.image) }}
  imagePullPolicy: {{ .Values.imagePullPolicy }}
  restartPolicy: Always
  args:
    - --structured-logs
    - {{ printf "--port=%v" .Values.config.DB_PORT }}
    - --health-check
    - {{ printf "--http-port=%v" $p.healthPort }}
    # The kubelet probes the Pod IP; the proxy answers on loopback only unless told otherwise.
    - --http-address=0.0.0.0
    {{- if $p.privateIp }}
    - --private-ip
    {{- end }}
    {{- if $p.autoIamAuthn }}
    - --auto-iam-authn
    {{- end }}
    - {{ $p.instanceConnectionName | quote }}
  startupProbe:
    httpGet:
      path: /startup
      port: {{ $p.healthPort }}
    periodSeconds: 2
    timeoutSeconds: 2
    failureThreshold: 30
  securityContext:
    runAsNonRoot: true
    runAsUser: {{ $p.runAsUser }}
    readOnlyRootFilesystem: true
    allowPrivilegeEscalation: false
    capabilities:
      drop: ["ALL"]
    seccompProfile:
      type: RuntimeDefault
  resources:
{{- include "chart.resources" $p.resources | nindent 4 }}
{{- end -}}

{{/*
   Egress rules for the Auth Proxy's token fetch from the GKE metadata server
   (cloudSqlProxy.metadataEgress): one rule per entry, at the egress list's indent (4); each
   NetworkPolicy that carries the proxy's 3307/443 rule includes it.
*/}}
{{- define "chart.cloudSqlProxyMetadataEgress" -}}
{{- range .Values.cloudSqlProxy.metadataEgress }}
    - to:
        - ipBlock:
            cidr: {{ .cidr | quote }}
      ports:
{{- range .ports }}
        - protocol: TCP
          port: {{ . }}
{{- end }}
{{- end }}
{{- end -}}

{{/* Backend DB connection assembled from ConfigMap (host/port/name) + app Secret (user/pass). */}}
{{- define "chart.databaseURL" -}}
{{- printf "postgresql+asyncpg://$(DB_USER):$(DB_PASSWORD)@$(DB_HOST):$(DB_PORT)/$(DB_NAME)" -}}
{{- end -}}

{{/*
   The Host values the backend answers (its ALLOWED_HOSTS): the edge hosts (root, and api when set), plus the backend
   Service's in-cluster names, because the frontend's server-side calls reach the backend by
   Service name. Ports are not listed: the backend strips the port before it matches.
*/}}
{{- define "chart.allowedHosts" -}}
{{- $svc := printf "%s-backend" (include "chart.fullname" .) -}}
{{- $ns := .Release.Namespace -}}
{{- $hosts := list .Values.edge.hosts.root -}}
{{- if .Values.edge.hosts.api -}}{{- $hosts = append $hosts .Values.edge.hosts.api -}}{{- end -}}
{{- $hosts = concat $hosts (list $svc (printf "%s.%s" $svc $ns) (printf "%s.%s.svc" $svc $ns) (printf "%s.%s.svc.cluster.local" $svc $ns)) -}}
{{- /* With the WAF on the frontend calls it by the short name in BACKEND_URL, and Envoy passes that
       Host on unchanged (waf-proxy.yaml sets no host rewrite), so only that form is added. */ -}}
{{- if .Values.waf.enabled -}}
{{- $hosts = append $hosts (include "chart.apiBackendService" .) -}}
{{- end -}}
{{- if .Values.backend.extraAllowedHosts -}}
{{- $hosts = concat $hosts (list (trim .Values.backend.extraAllowedHosts)) -}}
{{- end -}}
{{- join "," $hosts -}}
{{- end -}}

{{/*
   The migration Job's name: one per release revision (a Job's spec is immutable). The release
   fullname is cut to 44 characters so "-migrate-r<revision>" keeps the name within 63. Used by
   the Job, by NOTES.txt and (its prefix) by MigrationJobFailed, so they cannot disagree.
*/}}
{{- define "chart.migrationJobName" -}}
{{- printf "%s%d" (include "chart.migrationJobPrefix" .) (.Release.Revision | int) -}}
{{- end -}}
{{/* The migration Job's name without the revision; MigrationJobFailed selects it. */}}
{{- define "chart.migrationJobPrefix" -}}
{{- printf "%s-migrate-r" (include "chart.fullname" . | trunc 44 | trimSuffix "-") -}}
{{- end -}}

{{/*
   The backup CronJobs' names: "<fullname>-pg-backup" and "<fullname>-pg-backup-verify". A CronJob name
   may have 52 characters (the controller adds 11 to name each Job), so the release fullname is cut to
   35 and a trailing "-" is dropped, as the migration Job's name does; a short name is unchanged. Used by
   the CronJobs and by the alerts that select them, so they cannot disagree.
*/}}
{{- define "chart.backupCronJobName" -}}
{{- printf "%s-pg-backup" (include "chart.fullname" . | trunc 35 | trimSuffix "-") -}}
{{- end -}}
{{- define "chart.backupVerifyCronJobName" -}}
{{- printf "%s-verify" (include "chart.backupCronJobName" .) -}}
{{- end -}}

{{/* Where a database CA from database.tls.caSecret is mounted (read-only), and its file. */}}
{{- define "chart.dbCaDir" -}}/etc/db-ca{{- end -}}
{{- define "chart.dbCaPath" -}}{{ include "chart.dbCaDir" . }}/ca.pem{{- end -}}

{{/* Volume, mount and env for the database CA; each renders nothing unless the Secret is named. */}}
{{- define "chart.dbCaVolume" -}}
{{- with .Values.database.tls.caSecret }}{{ if .name }}
- name: db-ca
  secret:
    secretName: {{ .name }}
    items:
      - key: {{ required "database.tls.caSecret.key is required with .name" .key }}
        path: ca.pem
{{- end }}{{ end }}
{{- end -}}
{{- define "chart.dbCaVolumeMount" -}}
{{- if .Values.database.tls.caSecret.name }}
- name: db-ca
  mountPath: {{ include "chart.dbCaDir" . }}
  readOnly: true
{{- end }}
{{- end -}}
{{- define "chart.dbCaEnv" -}}
{{- if .Values.database.tls.caSecret.name }}
- name: DB_SSLROOTCERT
  value: {{ include "chart.dbCaPath" . | quote }}
{{- end }}
{{- end -}}

{{/*
   The init containers rendered ahead of the chart's own (wait-for-db): backend.extraInitContainers,
   then the Cloud SQL Auth Proxy when enabled, so the proxy is up before wait-for-db dials it.
*/}}
{{- define "chart.extraInitContainers" -}}
{{- with .Values.backend.extraInitContainers }}
{{ toYaml . }}
{{- end }}
{{- if .Values.cloudSqlProxy.enabled }}
{{ include "chart.cloudSqlProxyContainer" . }}
{{- end }}
{{- end -}}

{{/*
   The alert rule groups: ONE definition, in two sets, embedded by both flavours so they cannot
   drift. chart.alertGroupsApp (the backend's own metrics, in the app namespace) and
   chart.alertGroupsCluster (kube-state-metrics and cAdvisor series). The Prometheus Operator's
   PrometheusRule holds both. For Google Managed Prometheus the namespaced Rules object evaluates
   only series of its own namespace, so Rules holds the app set and a ClusterRules object
   (gmp-cluster-rules.yaml, applied by the admin bootstrap) holds the cluster set. In the gmp
   flavour the kube_* selectors use monitoring.gmp.objectNamespaceLabel for the object's namespace.
   Prometheus template expressions in annotations are escaped ({{ "{{" }} ... {{ "}}" }}) so Helm
   leaves them for the rule engine to render. The kube_* and container_* series need
   kube-state-metrics and cAdvisor in the metrics backend (see the monitoring docs).
*/}}
{{- define "chart.alertGroupsCluster" -}}
{{- $kns := ternary .Values.monitoring.gmp.objectNamespaceLabel "namespace" (eq .Values.monitoring.flavour "gmp") }}
- name: pod-health
  rules:
    - alert: PodRestartingFrequently
      expr: |
        increase(kube_pod_container_status_restarts_total{{ "{" }}{{ $kns }}="{{ .Release.Namespace }}"}[1h]) > 3
      for: 15m
      labels:
        severity: warning
      annotations:
        summary: "Pod {{ "{{" }} $labels.pod {{ "}}" }} is restarting frequently"
        description: "Pod {{ "{{" }} $labels.pod {{ "}}" }} has restarted {{ "{{" }} $value {{ "}}" }} times in the last hour."
        runbook_url: "{{ $.Values.monitoring.runbookUrl }}#podrestartingfrequently"
    - alert: PodPendingTooLong
      expr: |
        kube_pod_status_phase{{ "{" }}{{ $kns }}="{{ .Release.Namespace }}", phase="Pending"} == 1
      for: 10m
      labels:
        severity: warning
      annotations:
        summary: "Pod {{ "{{" }} $labels.pod {{ "}}" }} is pending for more than 10 minutes"
        description: "Pod {{ "{{" }} $labels.pod {{ "}}" }} has been in Pending state for over 10 minutes."
        runbook_url: "{{ $.Values.monitoring.runbookUrl }}#podpendingtoolong"
    - alert: PodFailed
      expr: |
        kube_pod_status_phase{{ "{" }}{{ $kns }}="{{ .Release.Namespace }}", phase="Failed"} == 1
        unless on (pod)
        kube_pod_owner{{ "{" }}{{ $kns }}="{{ .Release.Namespace }}", owner_kind="Job"}
      # A Job's Pods are left to the Job alerts (MigrationJobFailed, BackupJobFailed,
      # BackupVerifyFailed): a failed migration is not a page, and its Pods are kept for 24h.
      for: 5m
      labels:
        severity: critical
      annotations:
        summary: "Pod {{ "{{" }} $labels.pod {{ "}}" }} has failed"
        description: "Pod {{ "{{" }} $labels.pod {{ "}}" }} is in Failed state."
        runbook_url: "{{ $.Values.monitoring.runbookUrl }}#podfailed"
- name: resource-usage
  rules:
    - alert: HighCPUUsage
      expr: |
        sum(rate(container_cpu_usage_seconds_total{namespace="{{ .Release.Namespace }}"}[5m])) by (pod)
        /
        sum(kube_pod_container_resource_limits{{ "{" }}{{ $kns }}="{{ .Release.Namespace }}", resource="cpu"}) by (pod)
        > 0.85
      for: 15m
      labels:
        severity: warning
      annotations:
        summary: "Pod {{ "{{" }} $labels.pod {{ "}}" }} CPU usage is above 85%"
        description: "Pod {{ "{{" }} $labels.pod {{ "}}" }} is using {{ "{{" }} $value | humanizePercentage {{ "}}" }} of its CPU limit."
        runbook_url: "{{ $.Values.monitoring.runbookUrl }}#highcpuusage"
    - alert: HighMemoryUsage
      expr: |
        sum(container_memory_working_set_bytes{namespace="{{ .Release.Namespace }}"}) by (pod)
        /
        sum(kube_pod_container_resource_limits{{ "{" }}{{ $kns }}="{{ .Release.Namespace }}", resource="memory"}) by (pod)
        > 0.90
      for: 15m
      labels:
        severity: warning
      annotations:
        summary: "Pod {{ "{{" }} $labels.pod {{ "}}" }} memory usage is above 90%"
        description: "Pod {{ "{{" }} $labels.pod {{ "}}" }} is using {{ "{{" }} $value | humanizePercentage {{ "}}" }} of its memory limit."
        runbook_url: "{{ $.Values.monitoring.runbookUrl }}#highmemoryusage"
    - alert: MemoryApproachingOOM
      expr: |
        sum(container_memory_working_set_bytes{namespace="{{ .Release.Namespace }}"}) by (pod)
        /
        sum(kube_pod_container_resource_limits{{ "{" }}{{ $kns }}="{{ .Release.Namespace }}", resource="memory"}) by (pod)
        > 0.95
      for: 5m
      labels:
        severity: critical
      annotations:
        summary: "Pod {{ "{{" }} $labels.pod {{ "}}" }} memory usage is above 95% (OOM risk)"
        description: "Pod {{ "{{" }} $labels.pod {{ "}}" }} is at risk of OOM kill."
        runbook_url: "{{ $.Values.monitoring.runbookUrl }}#memoryapproachingoom"
- name: service-health
  rules:
    - alert: BackendEndpointsDown
      expr: |
        kube_endpoint_address_available{{ "{" }}{{ $kns }}="{{ .Release.Namespace }}", endpoint="{{ include "chart.fullname" . }}-backend"} == 0
      for: 5m
      labels:
        severity: critical
      annotations:
        summary: "Backend service has no ready endpoints"
        description: "Backend service {{ include "chart.fullname" . }}-backend has 0 ready endpoints."
        runbook_url: "{{ $.Values.monitoring.runbookUrl }}#backendendpointsdown"
    - alert: FrontendEndpointsDown
      expr: |
        kube_endpoint_address_available{{ "{" }}{{ $kns }}="{{ .Release.Namespace }}", endpoint="{{ include "chart.fullname" . }}-frontend"} == 0
      for: 5m
      labels:
        severity: critical
      annotations:
        summary: "Frontend service has no ready endpoints"
        description: "Frontend service {{ include "chart.fullname" . }}-frontend has 0 ready endpoints."
        runbook_url: "{{ $.Values.monitoring.runbookUrl }}#frontendendpointsdown"
    - alert: MigrationJobFailed
      # A failed migration blocks its revision's rollout; the old version keeps serving (not a page).
      expr: |
        kube_job_failed{{ "{" }}{{ $kns }}="{{ .Release.Namespace }}", job_name=~"{{ include "chart.migrationJobPrefix" . }}[0-9]+"} == 1
      for: 5m
      labels:
        severity: warning
      annotations:
        summary: "Migration job {{ "{{" }} $labels.job_name {{ "}}" }} failed"
        description: "Migration job {{ "{{" }} $labels.job_name {{ "}}" }} has failed: that revision's rollout is blocked and the previous version keeps serving."
        runbook_url: "{{ $.Values.monitoring.runbookUrl }}#migrationjobfailed"
- name: database-health
  rules:
    - alert: PostgreSQLDown
      expr: |
        kube_endpoint_address_available{{ "{" }}{{ $kns }}="{{ include "chart.dbNamespace" . }}", endpoint="{{ include "chart.fullname" . }}-postgresql"} == 0
      for: 5m
      labels:
        severity: critical
      annotations:
        summary: "PostgreSQL has no ready endpoints"
        description: "PostgreSQL service {{ include "chart.fullname" . }}-postgresql has 0 ready endpoints."
        runbook_url: "{{ $.Values.monitoring.runbookUrl }}#postgresqldown"
    - alert: BackupJobFailed
      # The backup CronJob's Jobs are "<name>-pg-backup-<timestamp>"; match the
      # trailing timestamp with [0-9]+ so this does NOT also fire on the weekly
      # "<name>-pg-backup-verify-<timestamp>" Jobs: BackupVerifyFailed covers those.
      expr: |
        kube_job_failed{{ "{" }}{{ $kns }}="{{ include "chart.dbNamespace" . }}", job_name=~"{{ include "chart.backupCronJobName" . }}-[0-9]+"} == 1
      for: 1h
      labels:
        severity: warning
      annotations:
        summary: "PostgreSQL backup job failed"
        description: "Backup job {{ "{{" }} $labels.job_name {{ "}}" }} has failed."
        runbook_url: "{{ $.Values.monitoring.runbookUrl }}#backupjobfailed"
    - alert: BackupVerifyFailed
      expr: |
        kube_job_failed{{ "{" }}{{ $kns }}="{{ include "chart.dbNamespace" . }}", job_name=~"{{ include "chart.backupVerifyCronJobName" . }}-[0-9]+"} == 1
      for: 1h
      labels:
        severity: warning
      annotations:
        summary: "PostgreSQL backup restore check failed"
        description: "Restore-check job {{ "{{" }} $labels.job_name {{ "}}" }} has failed: the newest backup may not restore."
        runbook_url: "{{ $.Values.monitoring.runbookUrl }}#backupverifyfailed"
{{- if and .Values.postgresql.enabled .Values.postgresql.backup.enabled }}
{{- /*
   The age of the CronJob's last success; a CronJob that never succeeded has no
   last_successful_time series, so its age since creation stands in once it passes the limit.
*/}}
{{- range $c := list
      (dict "alert" "BackupTooOld" "suffix" "" "hours" $.Values.monitoring.alerts.backupMaxAgeHours "what" "backup")
      (dict "alert" "BackupVerifyTooOld" "suffix" "-verify" "hours" $.Values.monitoring.alerts.backupVerifyMaxAgeHours "what" "restore check") }}
{{- $sel := printf "{%s=%q, cronjob=%q}" $kns (include "chart.dbNamespace" $) (printf "%s%s" (include "chart.backupCronJobName" $) $c.suffix) }}
    - alert: {{ $c.alert }}
      expr: |
        time() - kube_cronjob_status_last_successful_time{{ $sel }} > {{ $c.hours }} * 3600
        or
        (
          time() - kube_cronjob_created{{ $sel }} > {{ $c.hours }} * 3600
          unless on (cronjob)
          kube_cronjob_status_last_successful_time{{ $sel }}
        )
      labels:
        severity: warning
      annotations:
        summary: "No successful PostgreSQL {{ $c.what }} for {{ "{{" }} $value | humanizeDuration {{ "}}" }}"
        description: "CronJob {{ include "chart.backupCronJobName" $ }}{{ $c.suffix }} has not completed a {{ $c.what }} in the last {{ $c.hours }} hours."
        runbook_url: "{{ $.Values.monitoring.runbookUrl }}#{{ lower $c.alert }}"
{{- end }}
{{- end }}
{{- $cert := .Values.monitoring.alerts.certificateExpiry }}
{{- if $cert.enabled }}
{{- /*
   cert-manager's own series, so with the cluster alerts. They are scraped from cert-manager's
   namespace, so the Certificate's namespace is in exported_namespace (gmp: $kns) unless the
   scrape keeps the series' labels (honorLabels: true), when it is in `namespace`: select either.
   cert-manager exports 0 for a Certificate never issued (no notAfter), so > 0 leaves it out. The
   warning stops where the critical alert starts, so one Certificate fires one alert.
*/}}
{{- $nsx := ternary $kns "exported_namespace" (eq .Values.monitoring.flavour "gmp") }}
{{- $re := default .Release.Namespace $cert.namespaces }}
{{- $m := "certmanager_certificate_expiration_timestamp_seconds" }}
- name: certificates
  rules:
{{- range $a := list
      (dict "alert" "CertificateExpiringSoon" "severity" "warning" "days" $cert.warningDays "above" $cert.criticalDays "text" ": cert-manager has not renewed it.")
      (dict "alert" "CertificateExpiryCritical" "severity" "critical" "days" $cert.criticalDays "text" ". Renew it now.") }}
    - alert: {{ $a.alert }}
      expr: |
        (
          ({{ $m }}{namespace=~{{ $re | quote }}{{ "}" }} or {{ $m }}{{ "{" }}{{ $nsx }}=~{{ $re | quote }}{{ "}" }}) > 0
        ) - time() < {{ $a.days }} * 86400{{ if $a.above }} >= {{ $a.above }} * 86400{{ end }}
      for: 1h
      labels:
        severity: {{ $a.severity }}
      annotations:
        summary: "Certificate {{ "{{" }} $labels.name {{ "}}" }} expires in {{ "{{" }} $value | humanizeDuration {{ "}}" }}"
        description: "cert-manager Certificate {{ "{{" }} or $labels.{{ $nsx }} $labels.namespace {{ "}}" }}/{{ "{{" }} $labels.name {{ "}}" }} expires in less than {{ $a.days }} days{{ $a.text }}"
        runbook_url: "{{ $.Values.monitoring.runbookUrl }}#{{ lower $a.alert }}"
{{- end }}
{{- end }}
{{- end -}}

{{- define "chart.alertGroupsApp" -}}
{{- /* The release's requests without the probe and scrape handlers (monitoring.slo.excludeHandlers):
       HighErrorRate and the SLOs count the same requests. */}}
{{- $f := printf "namespace=%q, handler!~%q" .Release.Namespace .Values.monitoring.slo.excludeHandlers }}
- name: app-health
  rules:
    - alert: HighErrorRate
      # http_requests_total is the default metric of prometheus_fastapi_instrumentator
      # (main.py: Instrumentator().instrument(app)), so the app records this series by
      # default; the metrics server (serve.py) exposes it on METRICS_PORT.
      expr: |
        sum(rate(http_requests_total{{ "{" }}{{ $f }}, status=~"5.."}[5m]))
        /
        sum(rate(http_requests_total{{ "{" }}{{ $f }}}[5m]))
        > 0.05
      for: 10m
      labels:
        severity: critical
      annotations:
        summary: "High error rate detected ({{ "{{" }} $value | humanizePercentage {{ "}}" }})"
        description: "More than 5% of requests are returning 5xx errors."
        runbook_url: "{{ $.Values.monitoring.runbookUrl }}#higherrorrate"
{{- if .Values.monitoring.slo.enabled }}
{{- /*
   The SLOs (monitoring.slo; wiki/operations-runbook.md#service-level-objectives), SRE-workbook style.
   slo:sli_error:ratio_rate<w>{slo=availability|latency} is the error ratio over the window w; the
   alerts compare it to burn rate x error budget (1 - objective/100), a long and a short window
   together (multiwindow, multi-burn-rate). A window without traffic records nothing (the
   denominator is filtered to > 0), so no traffic fires no alert. The latency histogram has no
   status label: the latency SLI covers every response, errors included.
*/}}
{{- $slo := .Values.monitoring.slo }}
{{- $le := include "chart.sloLatencyLe" . }}
- name: slo-recording
  rules:
{{- range $w := list "5m" "30m" "1h" "2h" "6h" "1d" "3d" }}
    - record: slo:sli_error:ratio_rate{{ $w }}
      labels:
        slo: availability
      expr: |
        (
          sum by (namespace) (rate(http_requests_total{{ "{" }}{{ $f }}, status=~"5.."}[{{ $w }}]))
          or on (namespace)
          0 * sum by (namespace) (rate(http_requests_total{{ "{" }}{{ $f }}}[{{ $w }}]))
        )
        /
        (sum by (namespace) (rate(http_requests_total{{ "{" }}{{ $f }}}[{{ $w }}])) > 0)
{{- end }}
{{- range $w := list "5m" "30m" "1h" "2h" "6h" "1d" "3d" }}
    - record: slo:sli_error:ratio_rate{{ $w }}
      labels:
        slo: latency
      expr: |
        1 - (
          sum by (namespace) (rate(http_request_duration_seconds_bucket{{ "{" }}{{ $f }}, le="{{ $le }}"}[{{ $w }}]))
          /
          (sum by (namespace) (rate(http_request_duration_seconds_count{{ "{" }}{{ $f }}}[{{ $w }}])) > 0)
        )
{{- end }}
{{- /* Each objective as a ratio (Sloth's slo:objective:ratio), so a dashboard divides by the
       release's own budget: 1 - slo:objective:ratio. */}}
{{- range $s := list "availability" "latency" }}
    - record: slo:objective:ratio
      labels:
        namespace: {{ $.Release.Namespace | quote }}
        slo: {{ $s | quote }}
      expr: vector({{ (get $slo $s).objective }} / 100)
{{- end }}
- name: slo-alerts
  rules:
{{- $slis := list
      (dict "slo" "availability" "name" "Availability" "bad" "of requests answered 5xx")
      (dict "slo" "latency" "name" "Latency" "bad" (printf "of requests slower than %ss" $le)) }}
{{- $speeds := list
      (dict "name" "Fast" "severity" "critical" "pairs" (list (list "1h" "5m" "14.4") (list "6h" "30m" "6"))
        "text" "at least 14.4x the sustainable rate over 1h and 5m, or 6x over 6h and 30m: at 14.4x a 30-day budget is gone in about 2 days. Act now.")
      (dict "name" "Slow" "severity" "warning" "pairs" (list (list "1d" "2h" "3") (list "3d" "6h" "1"))
        "text" "at least 3x the sustainable rate over 1d and 2h (the 30-day budget is gone in about 10 days), or 1x over 3d and 6h (gone by the end of the period). Fix it within working hours.") }}
{{- range $s := $slis }}
{{- $o := (get $slo $s.slo).objective }}
{{- $r := printf "{namespace=%q, slo=%q}" $.Release.Namespace $s.slo }}
{{- range $sp := $speeds }}
    - alert: SLO{{ $s.name }}Burn{{ $sp.name }}
      expr: |
{{- range $i, $p := $sp.pairs }}
        {{ if $i }}or
        {{ end }}(
          slo:sli_error:ratio_rate{{ index $p 0 }}{{ $r }} > {{ index $p 2 }} * (1 - {{ $o }} / 100)
          and
          slo:sli_error:ratio_rate{{ index $p 1 }}{{ $r }} > {{ index $p 2 }} * (1 - {{ $o }} / 100)
        )
{{- end }}
      labels:
        severity: {{ $sp.severity }}
      annotations:
        summary: "{{ $s.name }} SLO error budget burning {{ lower $sp.name }} ({{ "{{" }} $value | humanizePercentage {{ "}}" }} {{ $s.bad }})"
        description: "The {{ $o }}% {{ $s.slo }} objective's error budget is burning {{ $sp.text }}"
        runbook_url: "{{ $.Values.monitoring.runbookUrl }}#{{ lower (printf "SLO%sBurn%s" $s.name $sp.name) }}"
{{- end }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
   The bounds of http_request_duration_seconds, written the way prometheus_client renders `le`
   (1.0, not 1). The one chart copy of the backend's LATENCY_BUCKETS (main.py); a test keeps the two
   equal. Change both together.
*/}}
{{- define "chart.latencyBuckets" -}}0.05 0.1 0.25 0.5 1.0 2.5 5.0{{- end -}}

{{/*
   The `le` label of the bucket at monitoring.slo.latency.thresholdSeconds; fails when the threshold
   is not a bound (the latency SLI reads that bucket, so any other value would match nothing).
*/}}
{{- define "chart.sloLatencyLe" -}}
{{- $t := .Values.monitoring.slo.latency.thresholdSeconds -}}
{{- $le := "" -}}
{{- range splitList " " (include "chart.latencyBuckets" .) -}}
{{- if eq (float64 .) (float64 $t) }}{{ $le = . }}{{ end -}}
{{- end -}}
{{- if not $le -}}
{{- fail (printf "%s: monitoring.slo.latency.thresholdSeconds (%v) is not a bound of http_request_duration_seconds (%s): the latency SLI reads the bucket at the threshold. Pick one of them, or change the backend's LATENCY_BUCKETS and chart.latencyBuckets together." (include "chart.fullname" .) $t (include "chart.latencyBuckets" .)) -}}
{{- end -}}
{{- $le -}}
{{- end -}}
