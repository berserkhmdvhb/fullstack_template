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
{{- define "chart.dbNamespace" -}}
{{- printf "%s-%s" .Release.Namespace .Values.namespaces.dbSuffix -}}
{{- end -}}

{{/* logging namespace = <app-namespace>-<suffix>. Derived so it tracks the release.
     The Fluentd DaemonSet (C-M2) needs root + hostPath + DAC_READ_SEARCH, which the
     app namespace's "restricted" PSS forbids — so it runs here under "baseline". */}}
{{- define "chart.loggingNamespace" -}}
{{- printf "%s-%s" .Release.Namespace .Values.namespaces.loggingSuffix -}}
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

{{/* The backend and frontend Service ports: one source (config.*_PORT) for the Services and the edge. */}}
{{- define "chart.backendPort" -}}{{ .Values.config.BACKEND_PORT }}{{- end -}}
{{- define "chart.frontendPort" -}}{{ .Values.config.FRONTEND_PORT }}{{- end -}}

{{/* The readiness probe paths, for the GKE load balancer health checks (a unit test pins them to the probes). */}}
{{- define "chart.backendReadyPath" -}}/api/v1/health{{- end -}}
{{- define "chart.frontendReadyPath" -}}/api/healthz{{- end -}}

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

{{/* The host roles, in render order, as a JSON array (read it back with fromJsonArray). */}}
{{- define "chart.edgeRoles" -}}
{{- toJson (list "api" "app" "root") -}}
{{- end -}}

{{/*
   The one routes list (edge.routes), resolved for a host role: root serves every route, api the
   `backend` routes, app the `frontend` routes. Emits a JSON array of {path, match, name, port}
   (read it back with fromJsonArray). `backend` is the WAF-aware Service and its port; `frontend`
   is the UI Service. Called with (dict "ctx" . "role" "api|app|root"); httproute.yaml, ingress.yaml
   and NOTES.txt all render from this, so a path is never written twice.
*/}}
{{- define "chart.edgeRoutes" -}}
{{- $role := .role -}}
{{- $ctx := .ctx -}}
{{- $out := list -}}
{{- range $ctx.Values.edge.routes -}}
{{- if or (eq $role "root") (and (eq $role "api") (eq .service "backend")) (and (eq $role "app") (eq .service "frontend")) -}}
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
   Name of the app DB secret the backend + migration consume (DB_USER / DB_PASSWORD
   / STATS_PASS). In dev/local the chart creates it (chart.fullname-secret) from the
   values; in production set secrets.existingSecret to the externally-managed secret
   (sealed-secrets / ESO / SOPS) that holds the same keys, and the chart stops
   creating its own — so the reference is always satisfied.
*/}}
{{- define "chart.secretName" -}}
{{- if .Values.secrets.existingSecret -}}
{{- .Values.secrets.existingSecret -}}
{{- else -}}
{{- include "chart.fullname" . }}-secret
{{- end -}}
{{- end -}}

{{/* Backend DB connection assembled from ConfigMap (host/port/name) + app Secret (user/pass). */}}
{{- define "chart.databaseURL" -}}
{{- printf "postgresql+asyncpg://$(DB_USER):$(DB_PASSWORD)@$(DB_HOST):$(DB_PORT)/$(DB_NAME)" -}}
{{- end -}}

{{/*
   The Host values the backend answers (its ALLOWED_HOSTS): the edge hosts, plus the backend
   Service's in-cluster names, because the frontend's server-side calls reach the backend by
   Service name. Ports are not listed: the backend strips the port before it matches.
*/}}
{{- define "chart.allowedHosts" -}}
{{- $svc := printf "%s-backend" (include "chart.fullname" .) -}}
{{- $ns := .Release.Namespace -}}
{{- $hosts := list .Values.edge.hosts.api .Values.edge.hosts.app .Values.edge.hosts.root -}}
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
   the Job and by NOTES.txt, so they cannot disagree.
*/}}
{{- define "chart.migrationJobName" -}}
{{- printf "%s-migrate-r%d" (include "chart.fullname" . | trunc 44 | trimSuffix "-") (.Release.Revision | int) -}}
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

{{/* backend.extraInitContainers, rendered first (before any chart init container). */}}
{{- define "chart.extraInitContainers" -}}
{{- with .Values.backend.extraInitContainers }}
{{ toYaml . }}
{{- end }}
{{- end -}}
