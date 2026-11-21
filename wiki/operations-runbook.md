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
| **Ingress Controller** | `ingress-nginx` namespace |
| **Monitoring** | Prometheus + Grafana |

---

## Deployment Procedures

### Standard Deployment

> **Deployed with the Helm chart** (`charts/your-service-name/`), not `kubectl apply -k`. Namespaces
> are per-env: app `your-service-name-<environment>`, db `<app-ns>-db` (production keeps the bare
> `your-service-name`). The chart creates both; you no longer pre-create them.

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

# Roll back to a revision (omit the number for the previous one)
helm rollback your-service-name <N> -n your-service-name-<environment> --wait

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

## Scaling

### Manual Scaling

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

# Test backend health
kubectl -n your-service-name-<environment> exec <pod-name> -- curl -fsS http://localhost:8000/api/v1/health

# Test frontend health
kubectl -n your-service-name-<environment> exec <pod-name> -- curl -fsS http://localhost:3000/
```

### Ingress Health

```bash
# Check ingress
kubectl -n your-service-name-<environment> get ingress

# Check ingress controller
kubectl -n ingress-nginx get pods

# Test via Ingress
curl -fsS https://api.your-domain.example/api/v1/health
curl -fsS https://app.your-domain.example/
```

### Prometheus Metrics

```bash
# Access Prometheus UI (if port-forwarded)
kubectl -n monitoring port-forward svc/prometheus 9090:9090

# Query backend request rate
# Rate: http_requests_total{service="your-service-name-backend"}

# Query error rate
# Rate: http_requests_total{service="your-service-name-backend",status=~"5.."}
```

### Grafana Dashboards

Access Grafana for pre-built dashboards:

- **Backend Overview** — Request rate, error rate, latency
- **Frontend Overview** — Build status, bundle size
- **Database** — Connection pool, query latency
- **Pod Resources** — CPU/memory usage per pod

---

## Log Correlation & Tracing

Every request carries a **correlation id** so its logs can be stitched together across the service
(and across downstream services that forward it). The template ships the lightweight minimum — an
`X-Request-Id` header plus a `request_id` field on every log record — rather than a full
OpenTelemetry/trace pipeline.

### How it works

- The inbound `X-Request-Id` header becomes the correlation id for the request's context, but only
  when it is at most 64 characters of `[A-Za-z0-9._-]`. Anything else is replaced (the rejected
  value is never logged, since it could be a log-injection attempt). The id in use is returned in
  the `X-Request-Id` response header and in every error body.
- A valid W3C `traceparent` header (`<version>-<32 hex>-<16 hex>-<2 hex>`, lowercase, no all-zero ids,
  version `00` exactly, a newer version read by its first 55 characters, `ff` invalid, sent once) puts its trace id on every log line as `trace_id` (and on error bodies). An invalid or
  absent one is ignored, never a 400, and no trace id is generated here.
- If the caller sends no valid id, the middleware **generates** one — a UUID4 hex
  string (32 chars, e.g. `2f1c9a7b…`). It is never blank and never the sentinel.
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

Request-logging is opt-in (`LOG_REQUESTS=true`) and emits one line per request (method, path,
status, duration) carrying the same id.

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
# Check ingress controller logs
kubectl -n ingress-nginx logs -l app.kubernetes.io/name=ingress-nginx

# Check ingress resource
kubectl -n your-service-name-<environment> describe ingress your-service-name

# Test DNS resolution
nslookup api.your-domain.example

# Check TLS certificate
echo | openssl s_client -connect api.your-domain.example:443 2>/dev/null | openssl x509 -noout -dates
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
kubectl -n your-service-name-<environment> exec <backend-pod> -- \
  curl -v postgresql://user:pass@your-service-name-postgresql.your-service-name-db.svc.cluster.local:5432/db

# Check DNS resolution
kubectl -n your-service-name-<environment> exec <pod-name> -- nslookup your-service-name-postgresql.your-service-name-db.svc.cluster.local
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

A weekly CronJob restores the latest dump to a throwaway database, confirms the expected tables exist,
and drops it — so a corrupt or empty backup is caught before you need it (`ON_ERROR_STOP=1` aborts the
job loudly on any step). It shares the backup PVC and is gated by the same `postgresql.backup.enabled`.

```bash
# Check the verification CronJob (Sundays 03:00 UTC)
kubectl -n your-service-name-db get cronjob your-service-name-pg-backup-verify

# Watch a verification run's logs
kubectl -n your-service-name-db logs -l app.kubernetes.io/component=backup-verification -f
```

### Restoring from Backup

```bash
# 1. Stop the application
kubectl -n your-service-name-<environment> scale deployment/your-service-name-backend --replicas=0

# 2. Restore the database
kubectl -n your-service-name-db exec <postgres-pod> -- \
  gunzip -c /backup/your-service-name-<timestamp>.sql.gz | \
  psql -U <user> -d <database>

# 3. Restart the application
kubectl -n your-service-name-<environment> scale deployment/your-service-name-backend --replicas=2

```

4. Run migrations (if needed): see [Database Migrations](#database-migrations) for the valid re-run recipe (redeploy).

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
3. **Rollback if needed** — `kubectl rollout undo`
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

Logs are stored in emptyDir volumes and rotated automatically. To check log usage:

```bash
# Check pod disk usage
kubectl -n your-service-name-<environment> exec <pod-name> -- df -h /app/logs

# Clear old logs (if needed)
kubectl -n your-service-name-<environment> exec <pod-name> -- \
  find /app/logs -name "*.log" -mtime +7 -delete
```

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

```bash
# Exec into backend pod
kubectl -n your-service-name-<environment> exec -it <backend-pod> -- bash

# Exec into frontend pod
kubectl -n your-service-name-<environment> exec -it <frontend-pod> -- sh

# Run a command in pod
kubectl -n your-service-name-<environment> exec <pod-name> -- curl -fsS http://localhost:8000/api/v1/health
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
