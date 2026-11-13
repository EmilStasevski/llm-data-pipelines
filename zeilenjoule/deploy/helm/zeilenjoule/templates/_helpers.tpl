{{/* Common labels; component selects the pod set. */}}
{{- define "zj.labels" -}}
app.kubernetes.io/name: zeilenjoule
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end }}

{{- define "zj.selector" -}}
app.kubernetes.io/name: zeilenjoule
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/* Own images: registry/name@digest, or :tag when no digest was recorded. */}}
{{- define "zj.image" -}}
{{- $d := index .root.Values.image (printf "%sDigest" .name) -}}
{{- if $d -}}
{{ .root.Values.image.registry }}/zeilenjoule/{{ .repo }}@{{ $d }}
{{- else -}}
{{ .root.Values.image.registry }}/zeilenjoule/{{ .repo }}:{{ .root.Values.image.tag }}
{{- end -}}
{{- end }}

{{/* PodSecurity "restricted" pod and container contexts. */}}
{{- define "zj.podSecurity" -}}
runAsNonRoot: true
seccompProfile: {type: RuntimeDefault}
{{- end }}

{{- define "zj.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities: {drop: [ALL]}
{{- end }}

{{- define "zj.dbHost" -}}
{{- if .Values.postgres.enabled }}postgres{{ else }}{{ required "externalDatabase.host is required when postgres.enabled=false" .Values.externalDatabase.host }}{{ end -}}
{{- end }}

{{- define "zj.dbQuery" -}}
{{- if .Values.postgres.enabled }}sslmode=disable{{ else }}sslmode={{ .Values.externalDatabase.sslmode }}{{ end -}}
{{- end }}

{{/* Environment shared by gateway, worker and the migration init container. Passwords come from the Secret
     and are expanded by the kubelet via $(VAR); they never appear in the rendered manifest. */}}
{{- define "zj.serviceEnv" -}}
- name: POSTGRES_PASSWORD
  valueFrom: {secretKeyRef: {name: {{ .Values.existingSecret }}, key: POSTGRES_PASSWORD}}
- name: ZJ_DATABASE_URL
  value: "postgres://{{ .Values.externalDatabase.user }}:$(POSTGRES_PASSWORD)@{{ include "zj.dbHost" . }}:{{ .Values.externalDatabase.port }}/{{ .Values.externalDatabase.database }}?{{ include "zj.dbQuery" . }}"
- name: ZJ_TEI_URL
  value: {{ if .Values.gpu.enabled }}http://tei:8081{{ else }}{{ required "modelServers.teiURL is required when gpu.enabled=false" .Values.modelServers.teiURL }}{{ end }}
- name: ZJ_LLM_URL
  value: {{ if .Values.gpu.enabled }}http://vllm:8000{{ else }}{{ required "modelServers.llmURL is required when gpu.enabled=false" .Values.modelServers.llmURL }}{{ end }}
- name: ZJ_LLM_MODEL
  value: model
- name: ZJ_LOG_LEVEL
  value: {{ .Values.logLevel | quote }}
- name: ZJ_UPLOAD_DIR
  value: /var/lib/zj/uploads
- name: GOMEMLIMIT
  valueFrom: {resourceFieldRef: {resource: limits.memory}}
{{- end }}

{{/* Migration as an init container (idempotent, advisory lock 7123, ADR 0023): no Helm hook ordering needed. */}}
{{- define "zj.migrateInit" -}}
- name: migrate
  image: {{ include "zj.image" (dict "root" . "name" "worker" "repo" "worker") }}
  imagePullPolicy: {{ .Values.image.pullPolicy }}
  args: [migrate]
  env:
    {{- include "zj.serviceEnv" . | nindent 4 }}
  securityContext:
    {{- include "zj.containerSecurity" . | nindent 4 }}
  resources:
    requests: {cpu: 50m, memory: 64Mi}
    limits: {memory: 256Mi}
{{- end }}

{{/* Model PVC name. */}}
{{- define "zj.modelsClaim" -}}
{{- if .Values.models.existingClaim }}{{ .Values.models.existingClaim }}{{ else }}zeilenjoule-models{{ end -}}
{{- end }}
