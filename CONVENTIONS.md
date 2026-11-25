# Conventions

The shared standard for **every** contribution to this service template — this service and every
future one adopted from it. It is the depth behind the lean mandatory rules in
[`AGENTS.md`](./AGENTS.md); [`CONTRIBUTING.md`](./CONTRIBUTING.md) is the workflow that applies it.

> **The one principle that does the real work.** A service *template* is not one service — it is a
> machine for producing services. A change that hard-codes a name or port, duplicates a source of
> truth, or edits a frozen control **breaks every future adopter**, not just this checkout. When a
> change would make the template less adaptable or less single-sourced, it is wrong even if it makes
> the current tree pass. Prefer the change that keeps the template generic.

Each convention below is: the **rule**, the **smell** that tells you that you are violating it, and
the **fix**. Per-domain *depth* (the "why" and the full worked example) lives in the `wiki/` pages
named at the end — this file holds only the cross-cutting invariant, so it stays short enough to
actually read.

---

## 1. The service name is a placeholder, never a literal

**Rule.** `your_service_name` (Python package) and `your-service-name` (images, Helm chart,
directories) are the template's placeholder. A contribution must not introduce a real service name,
and must not assume the placeholder — it adapts per adopter via
[`infra/adapt/replace_service_name.sh`](infra/adapt/replace_service_name.sh) (dry-run first).

**Smell → fix.**
- You typed a concrete name (`acme`, `flinky`, …) in code, a path, or a doc → use `your_service_name`.
- You wrote a fixed port/URL (`:8000`, `http://localhost`) as if it were permanent → make it
  env-driven (see convention 4).
- You are adapting the template for a real service and edited files by hand → run the adaptation
  script instead, then verify (`python -c "import <name>"`, `make test`, `pnpm run build`).

**Why.** The placeholder is the seam the whole template flexes on. A hard-coded name is a landmine
for the next adopter, who must hunt it down. Depth: [`wiki/adaptation.md`](wiki/adaptation.md).

## 2. One concern per layer (backend)

**Rule.** The backend is layered — routers → schemas → services → models → repos/entities — and each
concern is defined **once**. A new behavior goes in the layer that owns it; other layers call that
layer's public entry point rather than reaching past it. This is the template's analog of
single-ownership: each behavior has exactly one home.

**Smell → fix.**
- A router contains business logic (queries, mutations, side effects) → move it to a service; the
  router only binds the HTTP shape to the service call.
- Two modules both implement the same thing → pick one owner, make the other call it.
- A handler reaches directly into a DB model / repo → go through the owning service layer.

**Why.** Layering is what lets an adopter swap the database, the transport, or the auth without
rewriting the whole service. Depth: [`wiki/backend-architecture.md`](wiki/backend-architecture.md).

## 3. One source of truth (single-source / DRY)

**Rule.** Every fact — a command, a version, a path, a policy — has **exactly one** home, in code and
in docs. Everything else references that home. When two places state the same fact, one is now a bug
waiting to drift.

**Smell → fix.**
- A Makefile target and a CI job both spell out the same ruff/mypy command → the command lives in one
  place (the Makefile *or* a shared `.base` CI template); the other calls it.
- A doc restates a `wiki/` page's content → the doc links to the page instead.
- A version number appears in two files → single-source it (see convention 6).

**Why.** Duplicated facts diverge the moment one is edited; the template then ships contradictory
instructions. The lean docs (`AGENTS.md`) deliberately push depth to the full docs rather than
repeating it.

## 4. Config-driven, not hard-coded

**Rule.** Ports, hosts, URLs, and environment-specific values come from the environment /
`.env` / Helm `values.<env>.yaml` — never as literals in code or CI. The template's defaults
(`BACKEND_PORT=8000`, `FRONTEND_PORT=3000`, `API_VERSION=v1`) live in
`.env.example` and `.github/workflows/deploy.yml` (its `env` block); an adopter overrides them without editing code.

**Smell → fix.**
- A bare `8000` / `3000` / `localhost` in a script or source → read it from the env var with an
  inline `${VAR:-default}` guard so an empty variable can't collapse the value.
- A per-environment difference baked into a shared file → move it to `values.<env>.yaml`.

**Why.** Hard-coded infra values are the single most common thing an adopter has to rip out. The
container ports are fixed (`backend :8000`, `frontend :3000`); only the *exposed*
ports vary, via the Ingress and the per-env values. Depth:
[`wiki/infrastructure.md`](wiki/infrastructure.md), [`wiki/environment-variables.md`](wiki/environment-variables.md).

## 5. Image-level ops run on main and release tags only

**Rule.** Pushing, signing, attesting, and digest-resolving container images are **gated to `main`
and `v*` tags**: they run in the release workflow, **never in a pull request**.
A PR author can never sign an image or resolve a production digest. A PR job may still build images into
the local daemon only, to check them: it holds a `contents: read` token and does no push, registry login,
secret or build cache. The rule covers the project's registries and its signing identity. A test job
may push to a throwaway registry on the runner's loopback and sign there with a key the job generates,
trusted only by a test policy in a cluster deleted when the job ends, with no `id-token`, no registry
login and no secret, so nothing leaves the runner: the kind tests do this (only the admission test signs,
[`wiki/admission-control.md`](wiki/admission-control.md#proven-on-kind)); which modules push is derived, not
listed, by the guard test `test_the_registry_tests_have_no_identity_login_or_secret` in
[`tests/supply_chain/test_workflows_kind.py`](tests/supply_chain/test_workflows_kind.py). New supply-chain logic goes
under `infra/security/local/` — the eight shared `infra/security/*.sh` scripts are **frozen** (`_helpers.sh`, `sbom-generate.sh`,
`scan-vulnerabilities.sh`, `scan-licenses.sh`, `generate-vex.sh`, `check-ci-hardening.sh`,
`tools-install.sh`, `verify-image-digest.sh`; see
[`wiki/security.md`](wiki/security.md)).

**Smell → fix.**
- A new job that pushes/signs/attests an image, or builds one with a login, a secret, a cache or a write token,
  runs on every PR → move it into `release.yml`, or gate
  it with an `if:` on a push to `main` / a `v*` tag. The one exception is the test-only case above
  (a loopback registry and a job-generated key, trusted only by a test policy in a throwaway cluster,
  as `make kind-admission-test` does): leave it on pull requests.
- You edited `infra/security/*.sh` to add a scanner → put the scanner under `infra/security/local/`
  and add a pin in `infra/security/config.yaml` instead.
- You want to add/extend a supply-chain scanner → use the `add-supply-chain-scanner` skill
  (`agent/skills/add-supply-chain-scanner`), which encodes this exact invariant.

**Why.** The supply-chain chain (signing → SLSA provenance → digest-pin, all delivered in
`reusable-build.yml`) only has integrity if a PR author cannot forge any link in it. Depth:
[`wiki/security.md`](wiki/security.md), [`wiki/containerization.md`](wiki/containerization.md).

## 6. Version comes from Git, never from a literal

**Rule.** The package version is derived from Git tags via `setuptools_scm`
(`[tool.setuptools_scm]` in `pyproject.toml`, generating `src/your_service_name/_version.py`). Do
not hard-code a `__version__`, and do not pin a version where the tag should flow.

**Smell → fix.**
- A `__version__ = "1.2.3"` committed in source → delete it; let `setuptools_scm` generate it.
- An image tag or OCI label spelled by hand → take it from the CI-computed `IMAGE_TAG` / `VERSION`.

**Why.** A hand-written version lies the moment the tag moves; the tag is the only source that cannot
drift. Depth: [`wiki/versioning.md`](wiki/versioning.md).

---

## Where depth lives

`CONVENTIONS.md` holds only the cross-cutting invariants above. The per-domain depth — the full
"why", the worked examples, the edge cases — lives where it belongs, and these docs **link** to it
rather than restating it:

| Concern | Depth |
|---|---|
| Naming / adaptation | [`wiki/adaptation.md`](wiki/adaptation.md) |
| Backend layering | [`wiki/backend-architecture.md`](wiki/backend-architecture.md) |
| Single-source / DRY | [`CONTRIBUTING.md`](./CONTRIBUTING.md) (workflow) |
| Config-driven values | [`wiki/infrastructure.md`](wiki/infrastructure.md), [`wiki/environment-variables.md`](wiki/environment-variables.md) |
| Protected-ref gating / supply chain | [`wiki/security.md`](wiki/security.md), [`wiki/containerization.md`](wiki/containerization.md), `agent/skills/add-supply-chain-scanner` |
| Version single-sourcing | [`wiki/versioning.md`](wiki/versioning.md) |
