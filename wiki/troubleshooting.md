# Troubleshooting

Common issues and solutions for local development, Kubernetes, Docker, and CI/CD.

---

## Table of Contents

- [Backend Issues](#backend-issues)
- [Frontend Issues](#frontend-issues)
- [Kubernetes Issues](#kubernetes-issues)
- [Docker Compose Issues](#docker-compose-issues)
- [Database Issues](#database-issues)
- [CI/CD Issues](#cicd-issues)
- [General Tips](#general-tips)

---

## Backend Issues

### Backend Won't Start

**Symptom**: Backend process exits immediately

**Causes & Solutions**:

| Cause | Solution |
|-------|----------|
| Missing dependencies | `make develop` (uv sync --locked) |
| Database not available | Check `DATABASE_URL`; SQLite fallback works without PostgreSQL |
| Port already in use | Change `BACKEND_PORT` in `.env` |
| Migration failure | Run `python -m your_service_name.backend.db.run_migrations` (or `alembic upgrade head`) manually |

**Debug**:
```bash
# Check if backend process is running
curl -fsS http://127.0.0.1:${BACKEND_PORT}/api/v1/health

# Check logs
tail -f logs/DEV/*.log
```

### MyPy Import Errors

**Symptom**: `error: Cannot find implementation or library stub for module named 'xxx'`

**Solution**: Add to `pyproject.toml`:
```toml
[tool.mypy]
ignore_missing_imports = true
```

### Ruff Lint Errors in Tests

**Symptom**: `S101: use of assert detected` in test files

**Solution**: Already handled in config:
```toml
[tool.ruff.lint.per-file-ignores]
"tests/*" = ["S101", "S603", "SLF001", "T201"]
```

---

## Frontend Issues

### Build Fails: `Module not found`

**Symptom**: `Module not found: Can't resolve '@/lib/...'`

**Solution**:
```bash
cd frontend
npm ci
npm run openapi-gen
```

### TypeScript Errors

**Symptom**: `npm run typecheck` shows errors

**Common causes**:
- Missing type imports: use `import type { X } from '...'`
- `null` vs `undefined` mismatch: use `?? null` or `?? undefined`
- Union type narrowing: cast with `as Type` when needed

### ESLint Errors

**Symptom**: `npm run lint` shows errors (0 warnings allowed)

**Common fixes**:
- Import order: value imports before type imports
- Use `type` instead of `interface` (consistent-type-definitions)
- Add JSDoc comments for exported types/functions

### Search Icon Overlaps Input Placeholder

**Symptom**: Search icon renders on top of placeholder text

**Cause**: Tailwind shorthand `padding` overrides individual `padding-left` class

**Solution**: Use inline style instead of Tailwind class:
```tsx
<input style={{ paddingLeft: '2.75rem' }} />
```

### POST /items Fails: "Service temporarily unavailable" (stale frontend image)

**Symptom**: Creating an item through the UI returns `Failed to create item: Service temporarily unavailable` (HTTP 500 from `POST /items`), while reads work and a `POST` sent straight to the backend returns `201`.

**Cause**: The running frontend container is on an older image whose `frontend/middleware.ts` rewrote API calls to a hard-coded backend host, instead of reading `BACKEND_URL` per request like the current repo does. The image was never rebuilt because `scripts/rebuild-and-roll.sh` rebuilds and rolls out the service in a **Kubernetes/minikube** cluster (`minikube docker-env` → `kubectl rollout restart`) — not the local docker-compose stack — so newer code never reached the Compose frontend. The stale middleware proxied writes to the wrong host, which 500'd.

**Solution** (local docker-compose stack):
```bash
# Rebuild the frontend image from the current repo, then recreate only it
docker compose build frontend
docker compose up -d --no-deps frontend      # backend/DB stay untouched
```
`POST /items` then returns `201`.

> **Not `./scripts/rebuild-and-roll.sh --env local`.** That script targets the
> minikube cluster (see [`local-development-minikube.md`](./local-development-minikube.md)): it builds in the node
> daemon and `kubectl rollout restart`s Deployments. It does **not** rebuild the
> Compose images or restart the Compose containers you hit on `${FRONTEND_PORT}`.
> Use `docker compose` for the Compose stack; use `rebuild-and-roll.sh` only when
> testing against the local Kubernetes stack.

**Debug**:
```bash
# Is the running frontend image current? (compare image id / created date)
docker compose ps frontend
docker images | grep frontend

# The backend is fine on its own — a direct write returns 201:
curl -fsS -X POST "http://127.0.0.1:${BACKEND_PORT}/api/v1/items" \
  -H 'Content-Type: application/json' \
  -d '{"name":"probe","description":"direct-to-backend"}'

# If `docker compose up` fails on "network ... has active endpoints", an orphan
# container from a since-deleted Compose file is likely holding the network's
# endpoints. Remove that orphan (only if it's yours to remove), then recreate.
```
---

## Kubernetes Issues

### Pod CrashLoopBackOff

**Symptom**: Pod restarts repeatedly

**Causes & Solutions**:

| Cause | Solution |
|-------|----------|
| Missing ConfigMap/Secret | Check `kubectl describe pod` for events |
| Database connection failure | Verify `DATABASE_URL` and secret exists |
| Image pull error | Check image tag and registry credentials |
| Resource limits exceeded | Raise the requests/limits in `values.<env>.yaml` |
| Liveness probe failure | Check probe configuration and endpoint |

**Debug**:
```bash
# Check pod status and events
kubectl -n your-service-name-<env> describe pod <pod-name>

# Check logs
kubectl -n your-service-name-<env> logs <pod-name>

# Check previous container logs (if restarted)
kubectl -n your-service-name-<env> logs <pod-name> --previous
```

### ImagePullBackOff

**Symptom**: Pod cannot pull the image

**Solutions**:

| Issue | Solution |
|-------|----------|
| Image doesn't exist | Build and push the image first |
| Wrong image tag | Check the tag in `values.<env>.yaml` (or the `--set images.<svc>.tag` override) |
| Registry auth failure | Create the pull secret with `--docker-password "$GHCR_PAT"` — **not** `--password-stdin` (that flag is `docker login`'s, not `kubectl`'s); the token also needs `read:packages` for the image's package. See [Deploying to Kubernetes](getting-started.md#deploying-to-kubernetes-production) |
| Network issue | Check cluster DNS and registry connectivity |

**Debug**:
```bash
# Check pod events
kubectl -n your-service-name-<env> describe pod <pod-name>

# Test registry access
kubectl -n your-service-name-<env> exec <pod-name> -- \
  curl -fsS https://<registry>/v2/<image>/manifests/<tag>
```

### Ingress Not Working

**Symptom**: 404 or connection refused when accessing via Ingress

**Solutions**:

| Issue | Solution |
|-------|----------|
| Ingress controller not running | Install NGINX Ingress Controller |
| Wrong host/path | Check Ingress resource configuration |
| TLS certificate issue | Check cert-manager and Certificate resource |
| Service misconfiguration | Verify Service name and port match Ingress |

**Debug**:
```bash
# Check ingress controller
kubectl -n ingress-nginx get pods

# Check ingress resource
kubectl -n your-service-name-<env> describe ingress your-service-name

# Check services
kubectl -n your-service-name-<env> get svc

# Test backend directly
kubectl -n your-service-name-<env> exec <pod-name> -- curl -fsS http://localhost:8000/api/v1/health
```

### Database Connection Issues

**Symptom**: Backend cannot connect to PostgreSQL

**Solutions**:

| Issue | Solution |
|-------|----------|
| Secret missing | Create or update the database credentials secret |
| Wrong hostname | Use Kubernetes DNS: `your-service-name-postgresql.your-service-name-db.svc.cluster.local` |
| NetworkPolicy blocking | Check NetworkPolicy allows backend → database |
| PostgreSQL not ready | Check PostgreSQL pod status and logs |

**Debug**:
```bash
# Check database pod
kubectl -n your-service-name-db get pods

# Check secret
kubectl -n your-service-name-<env> get secret your-service-name-db-credentials -o yaml

# Test connectivity from backend
kubectl -n your-service-name-<env> exec <backend-pod> -- \
  python -c "import asyncpg; print('OK')"
```

### HPA Not Scaling

**Symptom**: HPA shows `<unknown>` or doesn't scale

**Solutions**:

| Issue | Solution |
|-------|----------|
| Metrics server not running | Install Kubernetes Metrics Server |
| Resource requests not set | Ensure resource requests are set in Deployment |
| HPA target too high | Adjust CPU/memory target percentages |

**Debug**:
```bash
# Check HPA
kubectl -n your-service-name-<env> describe hpa your-service-name-backend

# Check metrics server
kubectl -n kube-system get pods -l k8s-app=metrics-server

# Check pod metrics
kubectl -n your-service-name-<env> top pods
```

### Rollout Stuck

**Symptom**: `kubectl rollout status` hangs or times out

**Solutions**:

| Issue | Solution |
|-------|----------|
| New pod not ready | Check readiness probe and logs |
| Old pod not terminating | Check pod disruption budget and finalizers |
| Resource quota exceeded | Increase quota or reduce replicas |

**Debug**:
```bash
# Check rollout status
kubectl -n your-service-name-<env> rollout status deployment/your-service-name-backend

# Check pod events
kubectl -n your-service-name-<env> get events --sort-by='.lastTimestamp'

# Force rollback if stuck
kubectl -n your-service-name-<env> rollout undo deployment/your-service-name-backend
```

---

## Docker Compose Issues

### Services Won't Start

**Symptom**: `docker compose up` shows health check failures

**Solutions**:

| Issue | Solution |
|-------|----------|
| PostgreSQL not ready | Wait for `pg_isready` health check; check `DB_PASSWORD` matches |
| Backend can't connect to DB | Verify `DATABASE_URL` uses `postgres` as hostname (not `localhost`) |
| Backend answers `400` "The Host header is not allowed." | The `Host` is not in `ALLOWED_HOSTS` (the chart renders the ingress hosts and the backend Service names; add more with `backend.extraAllowedHosts`). Through a port-forward send `Host: <release>-backend`; the health and metrics paths accept any host |
| Frontend can't reach backend | Check `NEXT_PUBLIC_API_URL` points to the backend port (default: `http://127.0.0.1:8000`) |

**Debug**:
```bash
# Check service status
docker compose ps

# View logs for specific service
docker compose logs backend
docker compose logs frontend
docker compose logs nginx

# Restart a specific service
docker compose restart backend
```

### Port Conflicts

**Symptom**: `Bind for 0.0.0.0:8000 failed: port is already allocated` (or 3000, 8081)

**Solution**: Change the port in `.env`:
```bash
BACKEND_PORT=9000
# or
FRONTEND_PORT=3001
# or
NGINX_PORT=8082
```

### Volume Permission Issues

**Symptom**: PostgreSQL can't write to data directory

**Solution**:
```bash
# Remove old volume data
docker compose down -v
docker compose up -d
```

---

## Database Issues

### Migration Errors

**Symptom**: `alembic upgrade head` fails

**Solutions**:

| Issue | Solution |
|-------|----------|
| Table already exists | Check `alembic current`; may need `alembic stamp head` |
| `/health` returns 503 `schema_missing` or `schema_behind` | The migration has not run: see [Database operations](./database.md#readiness-and-liveness) |
| Column type mismatch | Fix model, create new migration: `alembic revision --autogenerate -m "fix"` |
| Foreign key error | Ensure referenced tables exist in migration order |

### SQLite Lock Issues

**Symptom**: `database is locked` (SQLite only)

**Solution**:
- SQLite is single-writer; concurrent writes will conflict
- Use PostgreSQL for production
- For development, ensure only one process writes at a time

---

## CI/CD Issues

### Release Fails at `plan`

**Symptom**: `container_matrix.py` exits 2 with a message (bad tag, bad `VERSION` file, or a bad manifest)

**Solution**: Check `.github/container-images.yml` exists and has a valid `images` list, that the tag matches `v<major>.<minor>.<patch>`, and that `VERSION` holds one version.

### Image Build Fails

**Symptom**: the `build` job of `release.yml` fails

**Common causes**:

| Issue | Solution |
|-------|----------|
| Missing image matrix or version | Ensure the `plan` job of `release.yml` succeeded |
| Registry auth failure | The `build` job logs in with `GITHUB_TOKEN` and needs `packages: write`; check the job's `permissions` |

### Frontend Tests Fail in CI

**Symptom**: the `unit` or `integration` job of `reusable-frontend.yml` fails

**Solutions**:
- Clear cache: delete `frontend/node_modules` and re-run `npm ci`
- Check `package-lock.json` is committed and up-to-date
- Verify MSW handlers match the actual API endpoints

### Grype Scan Fails (`main` and Tags)

**Symptom**: the `grype` job of `release.yml` fails (it is blocking)

**Solution**:
- Review vulnerabilities in the SBOM
- Update vulnerable dependencies
- If false positive, add to Grype ignore list in `infra/security/grype.yaml`

### Deploy Job Fails

**Symptom**: a `deploy-<env>` job of `deploy.yml` fails

**Common causes**:

| Issue | Solution |
|-------|----------|
| Cluster access fails | Verify the environment's variables (`DEPLOY_MODE`, `GKE_CLUSTER`, ...) and secret `KUBECONFIG_B64`; run `deploy-ops` with the operation `check` |
| Namespace doesn't exist | Create namespace or use `kubectl create namespace` |
| Image not in registry | Ensure the `build` job of `release.yml` succeeded |
| Migration job fails | Check migration job logs |

**Debug**:
```bash
# Check the deploy job logs in the repository's Actions tab
# Render the chart to inspect what will be applied (Helm-native)
helm template your-service-name charts/your-service-name -f charts/your-service-name/values.<environment>.yaml

# Test kubectl access
kubectl --context=<context> get namespaces
```

---

## General Tips

### Quick Diagnostics

```bash
# Check all services are healthy (local dev — each service on its own port)
curl -fsS http://127.0.0.1:${BACKEND_PORT}/api/v1/health
curl -fsS http://127.0.0.1:${FRONTEND_PORT}/
curl -fsS http://127.0.0.1:${NGINX_PORT}/healthz

# Check environment variables
env | grep -E 'BACKEND|FRONTEND|NGINX|PUBLIC|DATABASE|API_VERSION'

# Check Python version
python --version

# Check Node version
node --version
npm --version
```

### Kubernetes Quick Diagnostics

```bash
# Check all resources in namespace
kubectl -n your-service-name-<env> get all

# Check pod status
kubectl -n your-service-name-<env> get pods -o wide

# Check events (last 10)
kubectl -n your-service-name-<env> get events --sort-by='.lastTimestamp' | tail -10

# Check resource usage
kubectl -n your-service-name-<env> top pods
```

### Clean Start

```bash
# Backend
make clean-all
make develop
alembic upgrade head

# Frontend
cd frontend
rm -rf node_modules .next
npm ci
npm run openapi-gen

# Docker
docker compose down -v
docker compose build --no-cache
docker compose up -d

# Kubernetes
kubectl -n your-service-name-<env> delete pods --all
kubectl -n your-service-name-<env> rollout status deployment/your-service-name-backend
```

### Getting Help

- Check the [Developer Guide](./developer-guide.md) for Makefile commands
- Review the [Architecture](./architecture.md) for system understanding
- Check the [Operations Runbook](./operations-runbook.md) for Kubernetes procedures
- Check the repository's Actions tab for CI-specific issues
