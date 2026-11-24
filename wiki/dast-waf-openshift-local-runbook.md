# DAST + WAF — OpenShift Local Deploy/Test Runbook

> **When to use:** to go beyond the advisory OWASP ZAP baseline that
> [`dast.yml`](../.github/workflows/dast.yml) runs after each release.
> This runbook stands up the service on a local OpenShift (via `openshift-local`
> / CRC, or minikube/kind), then exercises the **full DAST** and the **WAF**.
>
> It is operational ("do this, expect that"), not a plan. The three ways to run
> each step — **① manual / ② `make` / ③ bash script** — follow the convention in
> `wiki/local-development-minikube.md`; use whichever suits you.

---

## 0. Prerequisites

- An **OpenShift VM** with nested virtualization enabled (kind/minikube/
  `openshift-local` all need it). `openshift-local` (CRC) wants ~9 GB RAM.
- Tooling on the VM: `openshift-local` (or `minikube` / `kind`), `helm`,
  `kubectl`/`oc`, `docker` (or `podman`).
- The service-template repo cloned on the VM. The WAF (`templates/waf-proxy.yaml`, the
  `waf:` values block) is part of the chart; the full-DAST tier (§3) is not built in.

> **Cluster choice matters** (below): **minikube/kind**
> validate the chart *as written* (vanilla K8s: Ingress, NetworkPolicy).
> **openshift-local** validates OpenShift-isms (**SCC**, **Route**, internal
> registry) that vanilla K8s will NOT catch. Since the target is OpenShift, do at
> least one `openshift-local` pass before calling it done.

---

## 1. Bring up a local cluster

**openshift-local (real OpenShift — recommended for final validation):**
```bash
openshift-local setup --preset development     # first time only
openshift-local start                            # needs nested virt + ~9GB RAM
eval "$(openshift-local oc-env)"                 # put `oc` on PATH
oc login -u kubeadmin -p "$(openshift-local console --credentials | ...)"
```

**minikube (fastest functional validation; vanilla K8s):**
```bash
minikube start --cpus=4 --memory=6g --disk-size=40g   # or --driver=docker
minikube addons enable ingress                          # for the Ingress-based WAF
```

Verify: `kubectl cluster-info` / `oc whoami` responds.

---

## 2. Deploy the service via Helm

Use the chart's env values so namespace/image/secret resolve from one source
(see `wiki/local-development-minikube.md` for the `make`/script equivalents):

```bash
scripts/k8s-bootstrap.sh qa --release your-service-name | kubectl apply -f -   # the namespaces (admin, once)
helm install your-service-name ./charts/your-service-name \
  --namespace your-service-name-qa \
  -f charts/your-service-name/values.qa.yaml
helm status your-service-name -n your-service-name-qa          # wait for READY (migration Job + workloads)
```

Get a reachable URL:
- **minikube/kind (Ingress):** `minikube service your-service-name-backend -n your-service-name-qa --url`
  or `minikube tunnel` + the ingress host; or `kubectl port-forward
  svc/your-service-name-backend -n your-service-name-qa 8000:8000` for API-only.
- **openshift-local (Route):** `oc create route edge --service=your-service-name-backend
  -n your-service-name-qa` then `oc get route` for the host.

Smoke-check the target first:
```bash
curl -fsS "$DAST_TARGET_URL/api/v1/health"    # or /openapi.json — must return 200
```

---

## 3. Full DAST (OWASP ZAP) against the live deploy

Run the single-source scanner against the reachable deploy (same script CI uses,
so local ↔ CI cannot drift):

```bash
export DAST_TARGET_URL="<route/ingress host from step 2>"
export DAST_TARGET_RUNNING=true   # scan the running deploy; start no app container
bash infra/security/local/scan-dast.sh "$SEC_OUTPUT_DIR" "$(pwd)"
# baseline: zap-baseline.py  →  report.json + dast.sarif (advisory)
```

**Escalate to the "full" tier** (the point of this runbook). `scan-dast.sh` runs the baseline only; each step
below means running ZAP yourself (its `zap-full-scan.py` or `zap-api-scan.py`, from the same pinned image):
- **Active scan:** `zap-full-scan.py` (slower, deeper).
- **OpenAPI-driven (critical for a FastAPI JSON API):** point ZAP at
  `/openapi.json` / import the OpenAPI definition so it exercises real endpoints
  instead of crawling HTML.
- **Authenticated:** supply a ZAP context (login + session) so behind-login
  endpoints are covered.
- Inspect findings: `make security-report` / `infra/security/local/report.sh` over `$SEC_OUTPUT_DIR/dast/`.

Expect: baseline = advisory reds, pipeline-equivalent exit 0; active = more
findings, still advisory unless you flip the release path to blocking.

---

## 4. WAF — deploy and prove it blocks

The WAF is an in-cluster **Coraza** proxy (Go, CNCF) running as an Envoy wasm
plugin with the **OWASP Core Rule Set** (`templates/waf-proxy.yaml`). It fronts the
**API surface** (`/api`, `/docs`, `/openapi.json`); the frontend bypasses it.
It is **off by default** — enable it and confirm it protects without breaking legit
traffic:

```bash
helm upgrade your-service-name ./charts/your-service-name -n your-service-name-qa \
  -f charts/your-service-name/values.qa.yaml --set waf.enabled=true
helm status your-service-name -n your-service-name-qa        # wait for the WAF pod to be READY
```

> The WAF runs this repository's own image (`images.waf`, built from `Dockerfile.waf`:
> the distroless Envoy image and the Coraza Wasm, both pinned), deployed by digest like
> the app images. The Pod is `READY` once Envoy has loaded the Wasm and its health
> listener (`waf.health`) answers; if it never does, read the Envoy log (a Wasm that
> fails to load stops Envoy at start-up). See
> [Containerization, Dockerfile.waf](./containerization.md#dockerfilewaf).

**Prove it (the CI-able "does the WAF work" check):**
```bash
# ① malicious → expect blocked (403 / WAF response)
curl -s -o /dev/null -w "%{http_code}\n" \
  "$DAST_TARGET_URL/api/v1/items?id=1' OR '1'='1"        # SQLi probe
curl -s -o /dev/null -w "%{http_code}\n" \
  --data "q=<script>alert(1)</script>" "$DAST_TARGET_URL/api/search"  # XSS probe

# ② legitimate → expect normal (200)
curl -s -o /dev/null -w "%{http_code}\n" "$DAST_TARGET_URL/api/v1/health"
```
Malicious → `403` (blocked); legit → `200` (passed). If malicious slips through
(200), the rule-set isn't engaged — check the WAF pod logs and that traffic
routes *through* it (the Ingress API paths point at `<name>-waf` when `waf.enabled`,
via the `chart.apiBackendService` swap).

> **OpenShift:** the Coraza proxy is cluster-internal (a plain Deployment +
> Service), so it sits *behind* the OpenShift Route/Router edge and validates on
> `openshift-local` regardless — unlike an nginx-ingress-controller ModSecurity
> snippet, which needs vanilla-K8s nginx. The Route is just the TLS/edge terminus;
> the WAF does the filtering. (The minikube pass won't reveal that the Route itself
> provides no WAF.)

---

## 5. (Optional) Runtime security add-ons

Only once DAST + WAF are stable:
- **Kyverno** (admission): `helm install kyverno kyverno/kyverno` — enforce
  no-`latest`, allowed registries, `runAsNonRoot`, drop caps. The repository's example image
  policies and their test: [Admission control](./admission-control.md).
- **Falco / Cilium Tetragon** (runtime detection): DaemonSet; watch for a
  compromised container spawning a shell / writing to sensitive paths.
- **Trivy Operator** (continuous image scan): reconciles deployed workloads
  against fresh CVEs — the runtime continuation of grype/syft.

Validate each with `kubectl get pol` / `helm test` and a deliberate violation.

---

## 6. Teardown

```bash
helm uninstall your-service-name -n your-service-name-qa
openshift-local stop        # or: minikube delete / kind delete cluster
```

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `dast` job never runs (CI) | it runs only after a successful Release push run of this repository, or by hand: see the header of [`dast.yml`](../.github/workflows/dast.yml) |
| ZAP finds almost nothing | It crawled HTML, not the API → feed `/openapi.json`; enable active scan |
| Behind-login surface untested | add a ZAP auth context (login + session) |
| WAF malicious request returns 200 | WAF pod not READY, or traffic bypasses it (check ingress/route path routing) |
| Pods `CrashLoopBackOff` on OpenShift only | SCC rejects the securityContext → bind a SCC / adjust `runAsNonRoot` (vanilla K8s won't show this) |
| Image pull fails on OpenShift | needs a pull secret for the container registry (`ghcr.io`) (or use the OCP internal registry) |
