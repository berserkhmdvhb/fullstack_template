# Operations Runbook

Kubernetes operational procedures for deploying, scaling, debugging, and maintaining the service.

---

## Table of Contents

- [Overview](#overview)
- [Deployment Procedures](#deployment-procedures)
- [Scaling](#scaling)
- [Health Checks & Monitoring](#health-checks--monitoring)
- [Log Correlation & Tracing](#log-correlation--tracing)
- [Debugging](#debugging)
- [Backup & Recovery](#backup--recovery)
- [Alerts](#alerts)
- [Incident Response](#incident-response)
- [Maintenance](#maintenance)
- [Reference Commands](#reference-commands)

---

## Overview

This runbook covers day-to-day operational tasks for running the service on Kubernetes. It assumes familiarity with `kubectl` and the Helm chart (`charts/your-service-name/`); per-env config lives in `values.<env>.yaml`.

### Key Contacts

| Role | Responsibility |
|------|---------------|
| **Service Owner** | Deployment decisions, incident escalation |
| **Platform Team** | Cluster management, ingress, monitoring stack |
| **DBA** | Database migrations, backup verification |

### Cluster Information

| Item | Value |
|------|-------|
| **Cluster** | `<cluster-name>` |
| **Namespaces** | `your-service-name`, `your-service-name-db` |
| **Edge** | Per `edge.mode`: a Gateway (`edge.gateway.parentRefs`), an Ingress controller, or a tunnel ([Infrastructure, Edge](./infrastructure.md#edge)) |
| **Monitoring** | Prometheus + Grafana |

---

## Deployment Procedures

### Standard Deployment

> **Deployed with the Helm chart** (`charts/your-service-name/`), not `kubectl apply -k`. Namespaces
> are per-env: app `your-service-name-<environment>`, db `<app-ns>-db`. The
> [admin bootstrap](./infrastructure.md#namespace-bootstrap-one-time-admin-step) creates both, once per environment; the chart creates none.

```bash
# 1. Verify the image is in the registry (or, for local/minikube, built into the node daemon).
docker pull <registry>/your-service-name:<tag>

# 2. Deploy + verify the whole stack (secrets → apply → wait → in-cluster checks) in one command.
#    Reads charts/your-service-name/values.<environment>.yaml; the migration runs as a per-revision Job.
./scripts/e2e.sh --env <environment>                 # deploy (helm upgrade --install) then verify
./scripts/e2e.sh verify --env <environment>          # verify only — read-only, safe to re-run

# 3. Verify the rollout (app ns + db ns). The migration Job (one per revision) is kept for 24 hours.
kubectl -n your-service-name-<environment> rollout status deployment/your-service-name-backend
kubectl -n your-service-name-<environment> rollout status deployment/your-service-name-frontend
kubectl -n your-service-name-<environment> get pods
kubectl -n your-service-name-<environment>-db get pods        # Postgres
```

### Deployment with CI/CD

The release workflow handles deployment automatically — **Helm-native** (see [`wiki/ci-cd.md`](./ci-cd.md)), when the repository variable `DEPLOY_ENABLED` is `true`:

1. A push to `main` or a `v*` tag triggers `release.yml`
2. Images are built and pushed to `ghcr.io`, and their digests are verified
3. `helm-checks` lints and renders the chart (validation gate)
4. `deploy-dev`, `deploy-qa` and (on a `v*` tag, after approval) `deploy-production` run `helm upgrade --install` by image digest, with migrations through the per-revision Job (`--wait-for-jobs`)
5. Each deploy job then verifies the rollout and runs the smoke test, and **rolls back to the recorded revision** if either fails

Monitor the run in the repository's **Actions** tab.

### Rolling Back a Deployment

Use the [`deploy-ops`](../.github/workflows/deploy-ops.yml) workflow (Actions tab, **Run workflow**): pick the environment, the operation `history` to see the revisions, then `rollback` with an optional numeric `revision` (empty means the previous one) and `ROLLBACK` in the `confirm` input. The same by hand, with Helm:

```bash
# Check the release history
helm history your-service-name -n your-service-name-<environment>

# Roll back to a revision (omit the number for the previous one). --server-side=true --force-conflicts take back the
# fields a hand edit changed, as the workflow's rollback does; without it the rollback fails on them.
helm rollback your-service-name <N> -n your-service-name-<environment> --wait --server-side=true --force-conflicts

# Verify rollback
kubectl -n your-service-name-<environment> rollout status deployment/your-service-name-backend
```

### Database Migrations

Migrations run automatically as a Kubernetes Job on each deployment: one Job per release revision,
named by the chart's `chart.migrationJobName` helper (`<fullname>-migrate-r<revision>`), kept for 24 hours, including after a failed deploy's automatic rollback (`helm.sh/resource-policy: keep`), so its logs survive. Design and rules (never a hook,
expand/contract, a database ahead of the code is ready): [Database operations](./database.md#database-operations).

```bash
# Check migration job status
kubectl -n your-service-name-<environment> get jobs -l app.kubernetes.io/component=migration

# View migration logs
kubectl -n your-service-name-<environment> logs job/<migration-job-name>

# See why the newest one failed (a failed Job and its Pods are kept)
kubectl -n your-service-name-<environment> describe job <migration-job-name>
```

To re-run a failed migration, **deploy again**: each `helm upgrade` creates a new revision and so a new Job
(`kubectl create job --from=` accepts only a CronJob, see the
[kubectl reference](https://kubernetes.io/docs/reference/kubectl/generated/kubectl_create/kubectl_create_job/)).
Or, for the same revision, delete the failed Job and re-apply that revision's stored Job
(`<revision>` is the number in the Job's name; `-l` keeps only the migration Job from the manifest):

```bash
kubectl -n your-service-name-<environment> delete job <migration-job-name>
helm get manifest <release> -n your-service-name-<environment> --revision <revision> \
  | kubectl -n your-service-name-<environment> apply -l app.kubernetes.io/component=migration -f -
```



**First install failed?** Helm uninstalls a failed first install, so the retry is revision 1 again and renders
the same Job name; Helm would adopt the kept, Failed Job and the retry would fail at once. `deploy-env.sh` deletes
this release's Failed migration Jobs before a first install. By hand:

```bash
kubectl -n your-service-name-<environment> get jobs \
  -l app.kubernetes.io/component=migration,app.kubernetes.io/instance=<release>
kubectl -n your-service-name-<environment> delete job <failed-migration-job-name>
```

A Pod that stays unready with `reason: schema_behind` or `schema_missing` is waiting for that Job.

---

### Hand edits and the next deploy

**The deploy always wins over a hand edit of a field the chart sets.** Every upgrade and rollback the template runs passes `--server-side=true --force-conflicts`, so a `kubectl scale`, `kubectl set image`, `kubectl set env`, `kubectl edit` or `kubectl patch` that changes or removes such a field, or deletes an object of the release, is restored by the next deploy or rollback, never a reason for it to fail. The weekly [live-drift check](./ci-cd.md#live-drift) reports the edit before that: one `drift` issue per environment, with each field's path.

**A deploy does not remove an addition.** A field the chart doesn't set stays as you added it: an extra object, an added env var, an added ConfigMap key (which the app reads, through `envFrom`). The drift check reports each (`added`, `extra`) on every run until you remove it by hand:

```bash
kubectl -n your-service-name-<environment> set env deployment/your-service-name-backend NAME-   # an added env var
kubectl -n your-service-name-<environment> patch configmap your-service-name-config --type json \
  -p '[{"op": "remove", "path": "/data/KEY"}]'                                                  # an added key
kubectl -n your-service-name-<environment> delete configmap <name>                              # an extra object
```

So a fix made by hand during an incident is temporary, or lingers where you didn't mean it to. To keep it, make it permanent in Git (the environment's `values.<env>.yaml`, or the chart) before the next deploy; otherwise that deploy reverts it, and an addition you no longer need must still be removed by hand.

## Scaling

### Manual Scaling

A manual scale lasts until the next deploy when the HPA is off (the chart sets `replicas`; [Hand edits and the next deploy](#hand-edits-and-the-next-deploy)). With the HPA on, the chart sets no `replicas`, so the HPA owns the count and the deploy leaves it.

```bash
# Scale backend to 5 replicas
kubectl -n your-service-name-<environment> scale deployment/your-service-name-backend --replicas=5

# Scale frontend to 3 replicas
kubectl -n your-service-name-<environment> scale deployment/your-service-name-frontend --replicas=3
```

### HPA (Horizontal Pod Autoscaler)

The HPA automatically scales based on CPU and memory usage:

```bash
# Check HPA status
kubectl -n your-service-name-<environment> get hpa

# View HPA details
kubectl -n your-service-name-<environment> describe hpa your-service-name-backend

# HPA configuration (from the Helm chart, values.<env>.yaml):
# Backend: 2-10 replicas (CPU 70%, memory 80%)
# Frontend: 2-5 replicas (CPU 70%, memory 80%)
```

### PDB (Pod Disruption Budget)

PDB ensures minimum availability during voluntary disruptions:

```bash
# Check PDB status
kubectl -n your-service-name-<environment> get pdb

# PDB configuration:
# minAvailable: 1 for both backend and frontend
```

### When to Override HPA

Override HPA in these scenarios:

- **Planned maintenance** — Scale down before node drains
- **Load testing** — Temporarily increase max replicas
- **Cost optimization** — Reduce min replicas during off-hours

```bash
# Temporarily disable HPA (set min=max)
kubectl -n your-service-name-<environment> scale deployment/your-service-name-backend --replicas=2

# Re-enable HPA
kubectl -n your-service-name-<environment> autoscale deployment/your-service-name-backend \
  --min=2 --max=10 --cpu-percent=70
```

---

## Health Checks & Monitoring

### Pod Health

```bash
# Check all pods
kubectl -n your-service-name-<environment> get pods

# Check pod details (including events)
kubectl -n your-service-name-<environment> describe pod <pod-name>

# Check pod logs
kubectl -n your-service-name-<environment> logs <pod-name>

# Follow logs in real-time
kubectl -n your-service-name-<environment> logs -f <pod-name>
```

### Service Health

```bash
# Check services
kubectl -n your-service-name-<environment> get svc

# Check endpoints (pods backing services)
kubectl -n your-service-name-<environment> get endpoints

# The images are distroless (no shell, no curl): each one's own HEALTHCHECK probe
# Test backend health (calls /api/v1/healthz)
kubectl -n your-service-name-<environment> exec <backend-pod> -- python -m your_service_name.backend.healthcheck

# Test frontend health
kubectl -n your-service-name-<environment> exec <frontend-pod> -- /nodejs/bin/node -e \
  "fetch('http://localhost:3000/api/healthz').then(r => process.exit(r.ok ? 0 : 1))"
```

### Ingress Health

```bash
# Check the edge object (HTTPRoutes in gateway mode, the Ingress in ingress mode; none for a tunnel)
kubectl -n your-service-name-<environment> get httproute,ingress

# Check the edge Pods (the Gateway implementation, the controller, or cloudflared) in their namespace
kubectl -n <edge-namespace> get pods

# Test via the edge
curl -fsS https://your-service-name.your-domain.example/api/v1/health
curl -fsS https://your-service-name.your-domain.example/
```

### Prometheus Metrics

The backend serves these on the metrics port (`METRICS_PORT`, default registry, one process per Pod):

- `http_requests_total{handler,method,status}` — requests by route, method and status.
- `http_request_duration_seconds{handler,method}` — latency histogram, buckets 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5 s (`LATENCY_BUCKETS` in `main.py`; no `status` label). The latency SLO's threshold is one of these bounds.
- `http_request_duration_highr_seconds` — latency histogram, no labels, 21 buckets (0.01 s to 60 s), for percentiles.
- `http_request_size_bytes{handler}` / `http_response_size_bytes{handler}` — Summaries of the `Content-Length` header values (count and sum only).
- `process_*` and `python_*` — the Prometheus client's default process and runtime series (CPU, memory, file descriptors, GC).
- `your_service_name_build_info{version}` — constant 1 from start; `version` is the package version (the Git tag). The dashboards' variables list its labels.
- `rate_limit_rejections_total` — requests answered 429 by the default per-IP limit (the CSP report limit is not counted).
- `rate_limit_storage_failures_total{reason}` — limiter storage failures let through (fail open; `error|timeout`).
- `db_pool_size` — configured pool size.
- `db_pool_checked_out` — pool connections in use.
- `db_pool_checked_in` — idle connections in the pool.
- `db_pool_overflow` — SQLAlchemy's signed overflow: open connections minus the pool size. It starts at minus the pool's size (`DB_POOL_SIZE` on PostgreSQL), which an idle Pod shows; above 0, overflow connections are open.
- `db_pool_max_overflow` — configured maximum overflow (`DB_MAX_OVERFLOW`; -1 is unlimited).

The `db_pool_*` gauges come from the live pool at scrape time; the file-SQLite fallback and PostgreSQL use a queue pool and report all five, a `:memory:` or null pool has none of them and all five are absent.

```bash
# Access Prometheus UI (if port-forwarded)
kubectl -n monitoring port-forward svc/prometheus 9090:9090

# Query backend request rate
# Rate: http_requests_total{service="your-service-name-backend"}

# Query error rate
# Rate: http_requests_total{service="your-service-name-backend",status=~"5.."}
```

### Service Level Objectives

The chart records two SLOs from the backend's own series and alerts on how fast each spends its error
budget (`monitoring.slo` in `values.yaml`; on by default, rendered whenever `monitoring.prometheusRule`
renders the rules, in both flavours):

| SLO | Measures | Objective (default) |
|---|---|---|
| `availability` | the share of requests answered 5xx (`http_requests_total`, `status=~"5.."`) | `monitoring.slo.availability.objective: 99.9` (percent not 5xx) |
| `latency` | the share of requests slower than `thresholdSeconds` (`http_request_duration_seconds_bucket`) | `monitoring.slo.latency.objective: 95` under `thresholdSeconds: 0.5` |

Both leave out the handlers matching `monitoring.slo.excludeHandlers` (default
`.*/(health|healthz|readyz|livez|metrics)$`: the probes and the scrape are not user traffic). The latency
histogram has no `status` label, so the latency SLI covers every response, errors included: a fast 500
counts as fast. To change an objective, set it in `values.<env>.yaml`; the render fails unless it is a
number strictly between 0 and 100, and unless `thresholdSeconds` is one of the histogram's bucket bounds
(the list in [Prometheus Metrics](#prometheus-metrics); a new bound means changing `LATENCY_BUCKETS` in
`main.py` and `chart.latencyBuckets` in the chart together, which a test keeps equal).
`monitoring.slo.enabled: false` drops the SLO groups.

**Recording rules** (group `slo-recording`): `slo:sli_error:ratio_rate<w>{namespace, slo}`, the error
ratio (bad requests over all requests) for `slo` `availability` or `latency` over each window `w` in
5m, 30m, 1h, 2h, 6h, 1d, 3d. A window without traffic records nothing, so no traffic never alerts.

**Burn-rate alerts** (group `slo-alerts`; the Google SRE workbook's multiwindow, multi-burn-rate
alerts). The burn rate is the error ratio divided by the error budget (1 − objective/100); 1 spends
exactly a 30-day budget in 30 days. Each alert needs a long and a short window over the burn rate
together: the long one proves the burn is significant, the short one that it is still happening, so
an alert clears soon after the problem stops.

| Alert | Fires when | Severity |
|---|---|---|
| `SLOAvailabilityBurnFast`, `SLOLatencyBurnFast` | burn rate > 14.4 over 1h and 5m, or > 6 over 6h and 30m (at 14.4 the budget is gone in about 2 days) | `critical`: page, act now |
| `SLOAvailabilityBurnSlow`, `SLOLatencyBurnSlow` | burn rate > 3 over 1d and 2h (the budget is gone in about 10 days), or > 1 over 3d and 6h (gone by the end of the 30 days) | `warning`: a ticket, fix within working hours |

`HighErrorRate` (5xx above 5% for 10 minutes) is the older single-threshold alert and stays as a
backstop: when it fires with an SLO alert, act on the SLO alert first. `make rules-test` checks every
rule with the pinned `promtool` and runs the unit tests in `charts/your-service-name/tests/rules`
(each alert fires just over its thresholds and stays quiet just under them).

### Grafana Dashboards

The chart ships four dashboards as JSON files in `charts/your-service-name/dashboards/`, delivered by one
ConfigMap for Grafana's dashboard sidecar (kube-prometheus-stack's `grafana.sidecar.dashboards`). Off by
default; turn it on in `values.<env>.yaml`:

```yaml
monitoring:
  dashboards:
    enabled: true
```

The sidecar label, the folder annotation (`grafana_folder`, read when the sidecar sets
`folderAnnotation: grafana_folder`) and the target namespace are the other `monitoring.dashboards`
keys, documented with their defaults in [`values.yaml`](../charts/your-service-name/values.yaml).

- **One release per Grafana.** Each ConfigMap key is `<chart>-<file>` (`your-service-name-slo.json`), so two
  services never collide, but two environments of one service do: the sidecar writes every key into one
  folder, and uninstalling one release removes the files the other's dashboards were loaded from. Enable
  the dashboards in one release per Grafana (the `namespace` variable reaches the others), or give each
  environment its own `folder` with the sidecar's `enableUniqueFilenames`.
- **Another namespace needs a Role.** The deployer's Role is namespace-scoped (the admin bootstrap): with
  `monitoring.dashboards.namespace` set to Grafana's namespace, grant the deployer `configmaps` there, or
  the deploy fails with "configmaps is forbidden".
- **Variables.** `datasource` (a Prometheus data source), `cluster` (All by default; a series without a
  `cluster` label matches All, so GMP's multi-cluster data is not summed when you pick one) and
  `namespace` (the namespaces where the backend exports `your_service_name_build_info`, a gauge it
  serves at 1 from start, so the list does not depend on the release's or the scrape job's name). The
  metric is named with the service name, so two services on one Prometheus each list only their own
  namespaces: a series every service built from the template exports would list another service's
  namespaces under this one's title.
- **What the chart fills in.** The probe and scrape handlers are left out as the SLOs leave them out: the
  render writes `monitoring.slo.excludeHandlers` into the queries. The SLO dashboard divides by the
  release's own objective, recorded as `slo:objective:ratio{namespace, slo}`, so it is right in every
  namespace; it needs the SLO rules (`monitoring.prometheusRule`, either flavour, with
  `monitoring.slo.enabled`). The render fails if the dashboards outgrow a ConfigMap (1 MiB).

| Dashboard (uid) | Shows | Explains |
|---|---|---|
| Service RED (`your-service-name-red`) | request rate, 5xx error ratio and p50/p95/p99 latency (from `http_request_duration_seconds` buckets), overall and per handler | [HighErrorRate](#higherrorrate), [SLOAvailabilityBurnFast](#sloavailabilityburnfast), [SLOAvailabilityBurnSlow](#sloavailabilityburnslow), [SLOLatencyBurnFast](#slolatencyburnfast), [SLOLatencyBurnSlow](#slolatencyburnslow): which handler fails or is slow |
| Database pool (`your-service-name-db`) | connections in use per Pod against the pool size and size + max overflow, saturation (in use ÷ (size + max overflow); none when the overflow is unlimited), idle connections, overflow in use | [SLOLatencyBurnFast](#slolatencyburnfast), [SLOLatencyBurnSlow](#slolatencyburnslow): requests waiting for a connection; [PostgreSQLDown](#postgresqldown) |
| Rate limiting (`your-service-name-rate`) | 429 rejections per second, limiter storage failures by reason (the limiter fails open: requests pass unlimited while they grow) | no alert yet; read it when clients report 429s or the limiter's storage is unhealthy |
| SLOs (`your-service-name-slo`) | each SLO's error ratio over 5m/1h/6h/1d/3d against its budget, the burn rate of each alert's long window against its threshold ([Service Level Objectives](#service-level-objectives)), the 30-day error budget left | the four SLO alerts above |

The 30-day budget left averages the 5-minute error ratio over 30 days: time-weighted, not
request-weighted, so it is an estimate, and it needs 30 days of Prometheus retention (kube-prometheus-stack
keeps 10 days by default; with less, it covers what is kept). Every query is checked against the series the
backend serves ([Prometheus Metrics](#prometheus-metrics)) and the chart's recording rules, and by the
pinned `promtool` (`tests/backend/test_chart_dashboards.py`).

---

## Log Correlation & Tracing

Every request carries a **correlation id** so its logs can be stitched together across the service
(and across downstream services that forward it). The template ships the lightweight minimum — an
`X-Request-Id` header plus a `request_id` field on every log record — rather than a full
OpenTelemetry/trace pipeline (that is opt-in: see [Tracing](backend-architecture.md#tracing)).

### How it works

- The inbound `X-Request-Id` header becomes the correlation id for the request's context, but only
  when it is at most 64 characters of `[A-Za-z0-9._-]`. Anything else is replaced (the rejected
  value is never logged, since it could be a log-injection attempt). The id in use is returned in
  the `X-Request-Id` response header and in every error body.
- A valid W3C `traceparent` header (`<version>-<32 hex>-<16 hex>-<2 hex>`, lowercase, no all-zero ids,
  version `00` exactly, a newer version read by its first 55 characters, `ff` invalid, sent once) puts its trace id on every log line as `trace_id` (and on error bodies). An invalid or
  absent one is ignored, never a 400, and no trace id is generated here, unless [tracing](backend-architecture.md#tracing) is on: then `trace_id` and `span_id` come from the active span, which continues a valid `traceparent`.
- If the caller sends no valid id, the middleware **generates** one — a UUID4 hex
  string (32 chars, e.g. `2f1c9a7b…`). It is never blank and never the sentinel.
- With the WAF on (`waf.enabled`), its Envoy sees the request first. It passes a client's `X-Request-Id`
  through unchanged (the backend's check above still applies), and gives a request without one a dashed
  UUID (36 chars, e.g. `4de3c846-2c9a-…`), so on that path a generated id is Envoy's, not the 32-hex one.
  The WAF's connection manager keeps Envoy's defaults here (`generate_request_id` on, `use_remote_address`
  off); with `use_remote_address` on, Envoy would replace a client's id at the edge.
- The id is injected onto every log record emitted while handling the request (application, request
  and audit logs) via a `logging.Filter` on the root logger's handler — no call site has to pass it.
- Outside a request the field renders as the sentinel `-` (constant `NO_REQUEST_ID`).

The middleware is a **pure ASGI** middleware (not `BaseHTTPMiddleware`) and is the outermost
request-scoped middleware, precisely so the id is set in the same context as everything downstream —
including the request-logging middleware, which runs the endpoint in a separate AnyIO task. A
`BaseHTTPMiddleware` wrapper would copy the context *before* the id is set and lose it across that
task boundary.

### Reading the id in logs

Text format (`LOG_FORMAT` unset, non-production; `trace_id` appears only when the request carried a `traceparent`):

```
2026-11-11 10:02:14,123 INFO your_service_name.backend.api.echo [request_id=req-abc trace_id=4bf92f3577b34da6a3ce929d0e0e4736]: echo requested
```

Structured JSON (production, or `LOG_FORMAT=json`):

```json
{"@timestamp": "2026-11-11T10:02:14.123456+00:00", "level": "INFO", "logger": "your_service_name.backend.api.echo", "message": "echo requested", "request_id": "req-abc", "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736"}
```

Request-logging is opt-in (`LOG_REQUESTS=true`; the Helm chart sets it, the app default stays off
for local runs) and emits one line per request (method, path, status, duration, resolved client IP)
carrying the same id. It never holds a query string, header, cookie or body.

### Tracing a request in practice

```bash
# A request's id comes from the client response header, an inbound log line, or a trace parent.
REQUEST_ID="req-abc"

# Grep every backend pod's logs for that id (last 2000 lines each).
kubectl -n your-service-name-<environment> logs -l app.kubernetes.io/component=backend --tail=2000 \
  | grep "request_id=$REQUEST_ID"

# In a central log system (Loki / ELK / Datadog) just filter on request_id = "$REQUEST_ID".
```

Forward the ids to downstream services: whatever calls this service should copy its inbound
`X-Request-Id` onto any outbound calls, so a single id spans the whole request path. For the trace,
`outgoing_trace_headers()` (in `backend/logging/correlation.py`) returns the `traceparent` (and the
inbound `tracestate`, when at most 512 characters) to merge into every outbound HTTP call; the template makes none today. Span creation is left to the opt-in
OpenTelemetry tracing.

---

## Debugging

### Pod CrashLoopBackOff

```bash
# Check pod status
kubectl -n your-service-name-<environment> get pods

# Describe the pod (check events)
kubectl -n your-service-name-<environment> describe pod <pod-name>

# Check logs
kubectl -n your-service-name-<environment> logs <pod-name>

# Check previous container logs (if restarted)
kubectl -n your-service-name-<environment> logs <pod-name> --previous

# Common causes:
# - Missing ConfigMap/Secret
# - Database connection failure
# - Image pull error
# - Resource limits exceeded
```

### Database Connection Issues

```bash
# Check database pod (if in-cluster)
kubectl -n your-service-name-db get pods

# Test database connectivity from backend pod
kubectl -n your-service-name-<environment> exec <backend-pod> -- \
  python -c "import asyncpg; print('OK')"

# Check database logs
kubectl -n your-service-name-db logs <postgres-pod>

# Verify secret exists
kubectl -n your-service-name-<environment> get secret your-service-name-db-credentials
```

### Ingress Issues

```bash
# Check the edge's logs (the access log lives there: the Gateway implementation, the controller, or Cloudflare)
kubectl -n <edge-namespace> logs <edge-pod>

# Check the edge object
kubectl -n your-service-name-<environment> describe httproute,ingress

# Test DNS resolution
nslookup your-service-name.your-domain.example

# Check TLS certificate
echo | openssl s_client -connect your-service-name.your-domain.example:443 2>/dev/null | openssl x509 -noout -dates
```

### Resource Issues

```bash
# Check resource usage
kubectl -n your-service-name-<environment> top pods

# Check node resources
kubectl top nodes

# Check for OOMKilled
kubectl -n your-service-name-<environment> get pods -o jsonpath='{.items[*].status.containerStatuses[*].lastState.terminated.reason}'

# Check for resource quota violations
kubectl -n your-service-name-<environment> describe resourcequota
```

### Network Issues

```bash
# Check NetworkPolicy
kubectl -n your-service-name-<environment> get networkpolicy

# Test connectivity from backend to database
kubectl -n your-service-name-<environment> debug -it <backend-pod> --image=busybox:stable --target=backend -- \
  nc -zv -w 3 your-service-name-postgresql.your-service-name-db.svc.cluster.local 5432

# Check DNS resolution
kubectl -n your-service-name-<environment> debug -it <pod-name> --image=busybox:stable --target=backend -- nslookup your-service-name-postgresql.your-service-name-db.svc.cluster.local
```

---

## Backup & Recovery

### PostgreSQL Backup

Backups run daily at 2 AM UTC via CronJob (gated by `postgresql.enabled` + `postgresql.backup.enabled`):

```bash
# Check backup CronJob
kubectl -n your-service-name-db get cronjob your-service-name-pg-backup

# Check backup PVC
kubectl -n your-service-name-db get pvc your-service-name-pg-backup-pvc

# List backup files
kubectl -n your-service-name-db exec <backup-pod> -- ls -lh /backup

# Manual backup
kubectl -n your-service-name-db create job your-service-name-manual-backup \
  --from=cronjob/your-service-name-pg-backup
```

### Backup Verification

A weekly CronJob restores the newest dump into a temporary database, checks it and drops it, so a stale,
corrupt or empty backup is caught before you need it. It shares the backup PVC and is gated by the same
`postgresql.backup.enabled`. What it checks, and what it does not prove:
[Disaster Recovery, Drills](./disaster-recovery.md#drills).

A run drops its temporary database on every exit, also when the Job's deadline ends it: the Pod gets SIGTERM,
and the script stops waiting for the restore and drops the database (`WITH (FORCE)`) within the Pod's
termination grace period. Only a run killed (SIGKILL: the grace period ran out, or the node was lost) before
that drop finished leaves its database. Each run first drops such leftovers (`verify_backup_*`). A drop that
fails is only a warning in the verify Job's log (`WARNING: could not drop ...`), not a failure and not an alert,
so a database that keeps failing to drop leaks unnoticed: look for that line in the logs below, and drop it by
hand once no verify Job is active.

```bash
# Check the verification CronJob (postgresql.backup.verifySchedule)
kubectl -n your-service-name-db get cronjob your-service-name-pg-backup-verify

# Watch a verification run's logs
kubectl -n your-service-name-db logs -l app.kubernetes.io/component=backup-verification -f
```

### Restoring from Backup

The restore of a dump, Cloud SQL point-in-time recovery, Backup for GKE, the recovery targets and the drills:
[Disaster Recovery](./disaster-recovery.md).

### Velero Cluster Backup

For full cluster disaster recovery, use Velero:

```bash
# Create a backup
velero backup create your-service-name-manual \
  --include-namespaces your-service-name,your-service-name-db \
  --wait

# Restore from backup
velero restore create --from-backup your-service-name-manual

# Check backup status
velero backup describe your-service-name-manual
```

---

## Alerts

One section per alert the chart renders (`templates/_helpers.tpl`, groups in this order). Every alert's
`runbook_url` annotation links its section here: `monitoring.runbookUrl` + `#<alertname in lower case>`
(set `monitoring.runbookUrl` to your repository's copy of this file; production refuses the placeholder).
A test fails when an alert has no section or a section names no alert. Severity: `critical` pages,
`warning` is a ticket for working hours ([Severity Levels](#severity-levels)). `<ns>` is the release
namespace and `<db-ns>` the database namespace (`<ns>-db`).

### PodRestartingFrequently

- **Means:** a container of the namespace restarted more than 3 times in an hour (held 15 minutes).
- **Impact:** requests to that Pod fail while it restarts; a crash loop is next.
- **First checks:** `kubectl -n <ns> describe pod <pod>` (last state, exit code, `OOMKilled`) and
  `kubectl -n <ns> logs <pod> --previous`.
- **Fix:** [Pod CrashLoopBackOff](#pod-crashloopbackoff); an `OOMKilled` is [Resource Issues](#resource-issues).

### PodPendingTooLong

- **Means:** a Pod has stayed `Pending` for over 10 minutes.
- **Impact:** less capacity than the replica count says; a rollout that waits on it is stuck.
- **First checks:** the Pod's events (`kubectl -n <ns> describe pod <pod>`): unschedulable (CPU, memory,
  node selector, PVC) or an image pull.
- **Fix:** [Resource Issues](#resource-issues) for capacity; an image pull error means a wrong digest or
  pull secret (`imagePullSecrets`).

### PodFailed

- **Means:** a Pod of the namespace that no Job owns is in phase `Failed` (held 5 minutes), such as an
  evicted Pod. A Job's Pods are left out: the Job alerts cover them
  ([MigrationJobFailed](#migrationjobfailed), [BackupJobFailed](#backupjobfailed),
  [BackupVerifyFailed](#backupverifyfailed)), so a failed migration does not page through this alert.
- **Impact:** that Pod no longer serves; its controller replaces it, but the cause (eviction, node
  pressure) may hit the others.
- **First checks:** `kubectl -n <ns> get pods --field-selector=status.phase=Failed` and its logs.
- **Fix:** [Debugging](#debugging); delete an evicted Pod once its cause is fixed.

### HighCPUUsage

- **Means:** a Pod has used over 85% of its CPU limit for 15 minutes.
- **Impact:** CPU throttling, so latency rises (watch the latency SLO).
- **First checks:** `kubectl -n <ns> top pods` and the HPA (`kubectl -n <ns> get hpa`): at its maximum?
- **Fix:** [Scaling](#scaling), or raise the limit; [Resource Issues](#resource-issues).

### HighMemoryUsage

- **Means:** a Pod's working set has been over 90% of its memory limit for 15 minutes.
- **Impact:** none yet; an OOM kill is close (`MemoryApproachingOOM`).
- **First checks:** `kubectl -n <ns> top pods`; whether it grows steadily (a leak) or with load.
- **Fix:** [Resource Issues](#resource-issues): raise the limit or fix the leak.

### MemoryApproachingOOM

- **Means:** a Pod's working set has been over 95% of its memory limit for 5 minutes.
- **Impact:** the kernel kills the container at the limit; in-flight requests fail.
- **First checks:** as for `HighMemoryUsage`; `kubectl -n <ns> describe pod <pod>` for earlier `OOMKilled`.
- **Fix:** raise the memory limit now (`values.<env>.yaml`, redeploy), then find the cause
  ([Resource Issues](#resource-issues)).

### BackendEndpointsDown

- **Means:** the backend Service has had no ready endpoint for 5 minutes.
- **Impact:** the API is down for every user.
- **First checks:** `kubectl -n <ns> get pods,endpoints`; an unready Pod's reason (`schema_behind` or
  `schema_missing` waits on the migration: `MigrationJobFailed`).
- **Fix:** [P1 — Service Down](#p1--service-down); [Database Connection Issues](#database-connection-issues);
  [Rolling Back a Deployment](#rolling-back-a-deployment).

### FrontendEndpointsDown

- **Means:** the frontend Service has had no ready endpoint for 5 minutes.
- **Impact:** the web app is down for every user.
- **First checks:** `kubectl -n <ns> get pods,endpoints` and the frontend Pods' logs.
- **Fix:** [P1 — Service Down](#p1--service-down); [Rolling Back a Deployment](#rolling-back-a-deployment).

### MigrationJobFailed

- **Means:** this release's migration Job (`<fullname>-migrate-r<revision>`) has failed (held 5 minutes).
- **Impact:** that revision's rollout is blocked; the previous version keeps serving, so it is not a page.
  The failed Job is kept for 24 hours, so the alert stays until it is deleted or expires.
- **First checks:** `kubectl -n <ns> describe job <migration-job-name>` and its logs.
- **Fix:** [Database Migrations](#database-migrations): fix the migration and deploy again.

### PostgreSQLDown

- **Means:** the in-cluster PostgreSQL Service has had no ready endpoint for 5 minutes.
- **Impact:** every request that reads the database fails; the backend turns unready.
- **First checks:** `kubectl -n <db-ns> get pods,pvc` and the PostgreSQL Pod's logs and events.
- **Fix:** [Database Connection Issues](#database-connection-issues); [Restoring from Backup](#restoring-from-backup)
  for lost data.

### BackupJobFailed

- **Means:** a Job of the daily backup CronJob (`<fullname>-pg-backup`) has failed (held 1 hour).
- **Impact:** no fresh backup: a restore would lose more data.
- **First checks:** `kubectl -n <db-ns> get jobs` and the failed Job's logs (credentials, disk space on the
  backup PVC); `kubectl -n <db-ns> describe job <failed-job-name>` shows the reason.
- **Fix:** [PostgreSQL Backup](#postgresql-backup). A `DeadlineExceeded` reason means the dump took longer than
  the Job may run: raise `postgresql.backup.deadlineSeconds`
  ([Database Backup CronJob](./database.md#database-backup-cronjob)) and deploy. Once fixed, run a backup under a name this alert
  watches: `kubectl -n <db-ns> create job <fullname>-pg-backup-$(date +%s) --from=cronjob/<fullname>-pg-backup`.
  The failed Job is kept (the CronJob keeps its last 3 failed Jobs, with no TTL), so the alert keeps
  firing until you delete it: `kubectl -n <db-ns> delete job <failed-job-name>`.

### BackupVerifyFailed

- **Means:** a Job of the weekly restore check (`<fullname>-pg-backup-verify`) has failed (held 1 hour).
- **Impact:** the newest backup may not restore; treat the backups as unproven until a check passes.
- **First checks:** the verify Job's logs (`kubectl -n <db-ns> logs -l app.kubernetes.io/component=backup-verification`).
- **Fix:** [Backup Verification](#backup-verification); fix the backup (or the check), take a backup and
  run the check again under a name this alert watches, once `kubectl -n <db-ns> get jobs` shows no verify Job
  active (two runs restore into the same server at once):
  `kubectl -n <db-ns> create job <fullname>-pg-backup-verify-$(date +%s) --from=cronjob/<fullname>-pg-backup-verify`.
  A finished verify Job is deleted after 24 hours (`ttlSecondsAfterFinished`), so the alert clears by
  then; delete the failed Job to clear it sooner (`kubectl -n <db-ns> delete job <failed-job-name>`).

### BackupTooOld

- **Means:** the backup CronJob has not succeeded for longer than `monitoring.alerts.backupMaxAgeHours`
  (default 26), or never has and is older than that. Rendered with the backup CronJobs only.
- **Impact:** the newest backup is stale or missing; a restore would lose that much data.
- **First checks:** `kubectl -n <db-ns> get cronjob <fullname>-pg-backup` (suspended? last schedule?) and
  its Jobs; a failed Job also fires `BackupJobFailed`.
- **Fix:** [PostgreSQL Backup](#postgresql-backup); run a manual backup.

### BackupVerifyTooOld

- **Means:** the restore check has not succeeded for longer than
  `monitoring.alerts.backupVerifyMaxAgeHours` (default 192, eight days), or never has and is older.
- **Impact:** the backups have not been proven restorable for over a week. A `postgresql.backup.verifySchedule`
  that runs less often than this threshold keeps it firing.
- **First checks:** `kubectl -n <db-ns> get cronjob <fullname>-pg-backup-verify` and its Jobs.
- **Fix:** [Backup Verification](#backup-verification); run the check by hand (as in
  [BackupVerifyFailed](#backupverifyfailed)), once `kubectl -n <db-ns> get jobs` shows no verify Job active.

### CertificateExpiringSoon

- **Means:** a cert-manager Certificate in a namespace matching
  `monitoring.alerts.certificateExpiry.namespaces` expires in under `warningDays` (default 21) but not
  yet under `criticalDays`, where `CertificateExpiryCritical` takes over (held 1 hour). Off by default
  (`monitoring.alerts.certificateExpiry.enabled`). A Certificate never issued (cert-manager exports 0)
  does not fire. cert-manager renews well before this, so the renewal is failing.
- **Impact:** none yet; browsers and clients refuse the endpoint once it expires.
- **First checks:** `kubectl -n <namespace> describe certificate <name>` and its CertificateRequest and
  Order (a failed ACME challenge, a missing issuer).
- **Fix:** [Certificate Renewal](#certificate-renewal).

### CertificateExpiryCritical

- **Means:** as `CertificateExpiringSoon`, under `criticalDays` (default 7; held 1 hour).
- **Impact:** an outage at expiry, days away.
- **First checks:** as for `CertificateExpiringSoon`.
- **Fix:** [Certificate Renewal](#certificate-renewal) now. Once the issuer works, trigger a renewal with
  `cmctl renew -n <namespace> <name>`: it keeps the current Secret (so the endpoint keeps serving) and
  skips cert-manager's backoff after failed issuances. Do not delete the Secret: that removes the
  serving certificate before a new one exists.

### HighErrorRate

- **Means:** over 5% of requests answered 5xx for 10 minutes (the older backstop to the SLO alerts).
  Like the SLOs, it leaves out the probe and scrape handlers (`monitoring.slo.excludeHandlers`).
- **Impact:** users see errors.
- **First checks:** act on a firing SLO alert first; the backend logs for the failing handler.
- **Fix:** [Debugging](#debugging); [Rolling Back a Deployment](#rolling-back-a-deployment) if a deploy
  caused it.

### SLOAvailabilityBurnFast

- **Means:** the availability error budget burns at 14.4x or 6x ([Service Level Objectives](#service-level-objectives)).
- **Impact:** at this rate the 30-day budget is gone in about 2 days.
- **First checks:** the 5xx by handler (`http_requests_total{status=~"5.."}`) and recent deploys.
- **Fix:** [P1 — Service Down](#p1--service-down); roll back a bad deploy.

### SLOAvailabilityBurnSlow

- **Means:** the availability error budget burns at 3x or 1x ([Service Level Objectives](#service-level-objectives)).
- **Impact:** the budget runs out within the period unless fixed.
- **First checks:** the 5xx by handler over the last day.
- **Fix:** [Debugging](#debugging) within working hours.

### SLOLatencyBurnFast

- **Means:** the latency error budget (requests over `thresholdSeconds`) burns at 14.4x or 6x
  ([Service Level Objectives](#service-level-objectives)).
- **Impact:** many requests are slow; the budget is gone in about 2 days.
- **First checks:** the slow handlers (`http_request_duration_seconds_bucket`), CPU throttling
  (`HighCPUUsage`), the database.
- **Fix:** [P2 — Degraded Performance](#p2--degraded-performance); [Scaling](#scaling).

### SLOLatencyBurnSlow

- **Means:** the latency error budget burns at 3x or 1x ([Service Level Objectives](#service-level-objectives)).
- **Impact:** the budget runs out within the period unless fixed.
- **First checks:** the slow handlers over the last day.
- **Fix:** [P2 — Degraded Performance](#p2--degraded-performance) within working hours.

---

## Incident Response

### Severity Levels

| Severity | Description | Response Time |
|----------|-------------|---------------|
| **P1 — Critical** | Service down, data loss | Immediate |
| **P2 — High** | Degraded performance, partial outage | < 1 hour |
| **P3 — Medium** | Non-critical bug, minor degradation | < 4 hours |
| **P4 — Low** | Cosmetic issue, feature request | Next sprint |

### P1 — Service Down

**Symptoms**: All health checks failing, 5xx errors, pods in CrashLoopBackOff

**Immediate Actions**:

1. **Assess scope** — Is it one pod or all pods?
2. **Check recent deployments** — Did a deployment cause this?
3. **Rollback if needed** — `helm rollback` ([Rolling Back a Deployment](#rolling-back-a-deployment))
4. **Scale up if possible** — `kubectl scale --replicas=<N>`
5. **Notify stakeholders** — Update status page

**Investigation**:

```bash
# Check pod status
kubectl -n your-service-name-<environment> get pods

# Check events
kubectl -n your-service-name-<environment> get events --sort-by='.lastTimestamp'

# Check logs
kubectl -n your-service-name-<environment> logs -l app.kubernetes.io/name=your-service-name --tail=100
```

### P2 — Degraded Performance

**Symptoms**: High latency, increased error rate, HPA scaling frequently

**Actions**:

1. **Check metrics** — Prometheus/Grafana dashboards
2. **Scale up** — Manual scaling or adjust HPA
3. **Check database** — Connection pool, slow queries
4. **Check external dependencies** — API timeouts, DNS issues

**Investigation**:

```bash
# Check HPA
kubectl -n your-service-name-<environment> get hpa

# Check pod resources
kubectl -n your-service-name-<environment> top pods

# Check database connections
kubectl -n your-service-name-<environment> exec <backend-pod> -- \
  python -c "from your_service_name.backend.db.engine import ...; print('OK')"
```

### P3 — Non-Critical Issue

**Symptoms**: Intermittent errors, specific endpoint failing

**Actions**:

1. **Document the issue** — Create incident ticket
2. **Investigate root cause** — Check logs, metrics
3. **Plan fix** — Schedule for next deployment
4. **Monitor** — Watch for escalation

---

## Maintenance

### Planned Maintenance

**Before maintenance**:

1. **Notify stakeholders** — Schedule maintenance window
2. **Scale down** — Reduce replicas if needed
3. **Drain nodes** — If node maintenance required

```bash
# Drain a node
kubectl drain <node-name> --ignore-daemonsets --delete-emptydir-data

# Uncordon after maintenance
kubectl uncordon <node-name>
```

**After maintenance**:

1. **Verify health** — Check all pods and services
2. **Scale up** — Restore replica counts
3. **Monitor** — Watch for issues

### Certificate Renewal

TLS certificates are managed by cert-manager and auto-renewed. To check:

```bash
# Check certificate status
kubectl -n your-service-name-<environment> get certificate

# Check cert-manager logs
kubectl -n cert-manager logs -l app=cert-manager
```

### Dependency Updates

**Security patches**:

1. **Review SBOM** — Check for vulnerable dependencies
2. **Update base images** — Rebuild with latest security patches
3. **Test** — Run full test suite
4. **Deploy** — Follow standard deployment procedure

### Log Rotation

There is nothing to rotate in the Pods: the backend and the frontend write logs to stdout only, and
the container runtime and the node's log agent keep them (see
[Infrastructure, Collecting logs](./infrastructure.md#collecting-logs)). Read them with
`kubectl -n your-service-name-<environment> logs <pod-name>`.

---

## Reference Commands

### Quick Diagnostics

```bash
# Check all resources in namespace
kubectl -n your-service-name-<environment> get all

# Check pod status
kubectl -n your-service-name-<environment> get pods -o wide

# Check services and endpoints
kubectl -n your-service-name-<environment> get svc,endpoints

# Check ingress
kubectl -n your-service-name-<environment> get ingress

# Check HPA and PDB
kubectl -n your-service-name-<environment> get hpa,pdb

# Check resource usage
kubectl -n your-service-name-<environment> top pods

# Check events (last 10)
kubectl -n your-service-name-<environment> get events --sort-by='.lastTimestamp' | tail -10
```

### Port Forwarding

```bash
# Backend
kubectl -n your-service-name-<environment> port-forward svc/your-service-name-backend 8000:8000

# Frontend
kubectl -n your-service-name-<environment> port-forward svc/your-service-name-frontend 3000:3000

# Prometheus
kubectl -n monitoring port-forward svc/prometheus 9090:9090

# Grafana
kubectl -n monitoring port-forward svc/grafana 3000:3000
```

### Exec into Pod

The images are distroless: no shell, no curl. Run a binary the image has, or attach a debug container:

```bash
# Run a command in the backend pod (its Python)
kubectl -n your-service-name-<environment> exec <backend-pod> -- python -m your_service_name.backend.healthcheck

# A shell beside the app container, sharing its process namespace (a throwaway debug image)
kubectl -n your-service-name-<environment> debug -it <backend-pod> --image=busybox:stable --target=backend -- sh
```

### Label Selector

```bash
# All backend pods
kubectl -n your-service-name-<environment> get pods -l app.kubernetes.io/component=backend

# All frontend pods
kubectl -n your-service-name-<environment> get pods -l app.kubernetes.io/component=frontend

# Pods by label
kubectl -n your-service-name-<environment> get pods -l environment=<environment>
```
