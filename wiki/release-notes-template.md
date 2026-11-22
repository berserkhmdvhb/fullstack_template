# Release Notes — v3.0.0

> Fill this in at tag time. The category bodies are pre-filled from the `[3.0.0]` block of
> [`changelog.md`](./changelog.md) — copy them into the GitHub release, then set the tag, date, and
> image tag. Tag policy and the release pipeline are in [`versioning.md`](./versioning.md) and
> [`releasing.md`](./releasing.md).

**Tag:** `v3.0.0`
**Date:** YYYY-MM-DD *(set to the day you push the tag)*
**Channel:** main (production)
**Image tag:** `3.0.0-prod-<short-sha>` — the same tag for all three images
  (`your-service-name-backend`, `your-service-name-frontend`, `your-service-name-nginx`)

---

## Changes

### New Features
- AuthN/authZ on the sample write endpoints (gateway-trusted-header + HMAC-signed JWT, HS256/384/512)
- Request correlation ID (`X-Request-Id`) + structured JSON logs in production
- OpenAPI contract single-sourced via `python -m your_service_name.backend.openapi dump` (+ Make targets and CI drift gates)
- `values.schema.json` (draft-07) validates Helm values at `helm lint` / `helm template` time
- `useHealth` React Query hook drives the home page
- Apache-2.0 `LICENSE`

### Bug Fixes
- Rate limiting actually enforces per-IP (replaced the slowapi no-op on FastAPI 0.162)
- OpenAPI drift gate made stable (`API_CONTRACT_VERSION`) and git-free on the frontend image
- Empty SARIF emitted on skipped OSV/GuardDog scans so a report always exists
- distroless frontend probes and healthcheck use `/nodejs/bin/node`; port-forward scheme resolved by numeric index
- Docs accuracy: changelog Kustomize→Helm, getting-started fence, deploy example, `environment-variables.md` reconciled to `.env.example` (no `DEBUG=TRUE`), supply-chain doc, `doc-templates`
- Troubleshooting: stale-frontend-image `POST /items` "Service temporarily unavailable" (500) root cause + fix

### Breaking Changes
- Removed `/items/{id}/archive` and the dead `sort` / `order` query params (canonical soft-delete via `delete_item`)
- Write endpoints now require auth when authN is enabled; unauthenticated writes are rejected
- CORS defaults to same-origin (empty when `CORS_ORIGINS` unset)
- Frontend CSP no longer permits `unsafe-inline` / `unsafe-eval` (per-request nonce)
- UUID ids are runtime `uuid.UUID` (string coercion at the API boundary)

### Security
- AuthN/authZ (HMAC JWT) on sample write endpoints; CORS same-origin default; CSP without `unsafe-*`
- Defense-in-depth response headers (`Permissions-Policy`, `Cross-Origin-Resource-Policy`)
- Per-IP rate limiting on the sample routes
- SBOM (Syft), vulnerability scan (Grype, `fail-on: high`, `only-fixed: true`), license scan (Grant), VEX

### Kubernetes
- Pod Security Standard version pinned to the chart's `kubeVersion` floor (`v1.34`); nginx `topologySpreadConstraints` + podAntiAffinity
- Production render fails loud when the managed DB is used with an empty `egressCIDR`
- Removed the broken `podman-compose.yml`
- **Before upgrading:** review `charts/your-service-name/values.<env>.yaml` — `values.schema.json` now rejects unknown keys and bad values at `helm lint` / `helm template` time

### Supply Chain
- Container images are pushed / signed / attested / scanned on `main` and `v*` tags only — never in a pull request (a pull request only builds them into its local daemon to check them)
- SBOM + VEX are published automatically on the tag pipeline; images are signed keyless and carry SLSA provenance and SBOM attestations (public repositories; see wiki/security.md)
- No frozen `infra/security/*.sh` scripts edited; new supply-chain logic lives under `infra/security/local/`
