{{/* The app namespace: values.namespace, else <release>-<environment> (the deploy's convention). */}}
{{- define "bootstrap.appNamespace" -}}
{{- .Values.namespace | default (printf "%s-%s" .Values.release .Values.environment) -}}
{{- end -}}

{{- define "bootstrap.dbNamespace" -}}
{{- printf "%s-%s" (include "bootstrap.appNamespace" .) .Values.dbSuffix -}}
{{- end -}}

{{/* The namespaces to create: the app one, plus the database one when the in-cluster database is used. */}}
{{- define "bootstrap.namespaces" -}}
{{- $list := list (dict "name" (include "bootstrap.appNamespace" .) "role" "app") -}}
{{- if .Values.database.inCluster -}}
{{- $list = append $list (dict "name" (include "bootstrap.dbNamespace" .) "role" "db") -}}
{{- end -}}
{{- toJson $list -}}
{{- end -}}

{{- define "bootstrap.labels" -}}
app.kubernetes.io/part-of: {{ .Values.release }}
app.kubernetes.io/component: bootstrap
environment: {{ .Values.environment }}
{{- end -}}
