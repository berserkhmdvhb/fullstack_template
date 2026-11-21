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
   waf.enabled swap in one place (ingress.yaml calls this for every API path).
*/}}
{{- define "chart.apiBackendService" -}}
{{- if .Values.waf.enabled -}}
{{- printf "%s-waf" (include "chart.fullname" .) -}}
{{- else -}}
{{- printf "%s-backend" (include "chart.fullname" .) -}}
{{- end -}}
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
   The Host values the backend answers (its ALLOWED_HOSTS): the ingress hosts, plus the backend
   Service's in-cluster names, because the frontend's server-side calls reach the backend by
   Service name. Ports are not listed: the backend strips the port before it matches.
*/}}
{{- define "chart.allowedHosts" -}}
{{- $svc := printf "%s-backend" (include "chart.fullname" .) -}}
{{- $ns := .Release.Namespace -}}
{{- $hosts := list .Values.ingress.hosts.api .Values.ingress.hosts.app .Values.ingress.hosts.root $svc (printf "%s.%s" $svc $ns) (printf "%s.%s.svc" $svc $ns) (printf "%s.%s.svc.cluster.local" $svc $ns) -}}
{{- if .Values.backend.extraAllowedHosts -}}
{{- $hosts = concat $hosts (list (trim .Values.backend.extraAllowedHosts)) -}}
{{- end -}}
{{- join "," $hosts -}}
{{- end -}}
