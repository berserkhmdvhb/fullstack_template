# Contributing

How to contribute to this service template — a FastAPI + Next.js 16 service with an NGINX gateway,
Helm packaging, GitHub Actions, and supply-chain hardening. This guide is organised by the **kind of
change** you are making; every kind ends at the same quality gates. The deep "how" for each area
lives in `wiki/` and in the scoped skills under `agent/skills/` — this file points at them rather
than restating them.

> Working on this template means working for **every future adopter**, not just this checkout. The
> cross-cutting invariants (never hard-code the service name or ports, single-source every fact,
> gate image ops to `main` and `v*` tags, no secrets) are in
> [`CONVENTIONS.md`](./CONVENTIONS.md). Read it before you start — the categories below assume it.

---

## Table of Contents

- [Repository layout](#repository-layout)
- [Non-negotiable principles](#non-negotiable-principles)
- [Set up a dev environment](#set-up-a-dev-environment)
- [The workflow: TDD → gates → PR](#the-workflow-tdd--gates--pr)
- [Contributions by kind of change](#contributions-by-kind-of-change)
  - [Add a backend feature](#add-a-backend-feature)
  - [Add a frontend feature](#add-a-frontend-feature)
  - [Add or modify a Helm chart](#add-or-modify-a-helm-chart)
  - [Change CI](#change-ci)
  - [Add or extend a supply-chain scanner](#add-or-extend-a-supply-chain-scanner)
  - [Write or adapt docs](#write-or-adapt-docs)
- [Quality gates](#quality-gates)
- [CI/CD](#cicd)
- [Appendix A — Pull-request checklist (copy-paste)](#appendix-a-pull-request-checklist-copy-paste)

---

## Repository layout

```
service-template-react/
├── src/your_service_name/     # FastAPI backend (placeholder name — adapts per service)
├── frontend/                  # Next.js 16 / React / TypeScript app
├── charts/your-service-name/  # Helm chart (per-env values.<env>.yaml)
├── .github/                   # GitHub Actions workflows, composite actions, helper scripts
├── scripts/ci/                # scripts the workflows call (deploy-env, deploy-verify, shell-lint, …)
├── infra/                     # runtime (entrypoint), adapt (rename script), security/
├── tests/                     # backend tests (unit / integration / e2e / live markers)
├── wiki/                      # per-domain depth (architecture, ci-cd, security, …)
├── agent/skills/              # scoped skills (add-supply-chain-scanner, quality-gates)
├── Makefile                   # quality-gate shortcuts (check-all-dry, test-*, …)
├── AGENTS.md / CLAUDE.md      # lean always-on agent instructions (see AGENTS.md)
└── CONTRIBUTING.md / CONVENTIONS.md  # this guide / the cross-cutting standard
```

The placeholder `your_service_name` / `your-service-name` is **not** this service's real name — it is
the seam every adopter renames. See [`wiki/adaptation.md`](wiki/adaptation.md).

## Non-negotiable principles

These are enforced by the gates and the pipeline, not by politeness:

1. **The template is the source of truth** — a change that only works for one concrete service
   (a hard-coded name, a fixed port, a real URL) is a regression. [`CONVENTIONS.md`](./CONVENTIONS.md#1-the-service-name-is-a-placeholder-never-a-literal).
2. **One source of truth per fact** — never duplicate a command, version, or path.
   [`CONVENTIONS.md`](./CONVENTIONS.md#3-one-source-of-truth-single-source--dry).
3. **Image ops are gated to `main` and `v*` tags** — build/sign/attest/digest-resolve never run in a pull request.
   [`CONVENTIONS.md`](./CONVENTIONS.md#5-image-level-ops-run-on-main-and-release-tags-only).
4. **No secrets** — not in code, tests, docs, examples, or CI YAML. Real internal URLs and project
   IDs are redacted. [`wiki/security.md`](wiki/security.md) (secret scanning runs in CI via
   `scripts/ci/check-secrets.sh`).

## Set up a dev environment

Copy-paste; the full story (Kubernetes, Compose, seeding) is
[`wiki/getting-started.md`](wiki/getting-started.md) — don't duplicate it here.

```bash
git clone <YOUR_REPO_URL> && cd $_
cp .env.example .env                 # then edit to taste
infra/adapt/replace_service_name.sh -n <your_service> --dry-run   # preview the rename
infra/adapt/replace_service_name.sh -n <your_service>             # apply it
scripts/tools.sh install uv && export PATH="$PWD/.tools/bin:$PATH"
make develop && source .venv/bin/activate   # locked set; the uv workflow: wiki/getting-started.md#lock-file-and-the-7-day-rule
cd frontend && npm ci && npm run dev # frontend at :3000
# backend:  ./run_backend.sh  (migrates, then serves the API at :8000)
```

Install the git hook once with `scripts/tools.sh install pre-commit` and then `.tools/bin/pre-commit install`; `make precommit` runs every hook on all files. The hooks call the same tools CI does (see `.pre-commit-config.yaml`) and name the install command for any that is missing.

Python ≥ 3.12, the Node version in `frontend/.nvmrc`. Without a `DATABASE_URL` the backend falls back to SQLite, so it comes up
green with zero infra.

## The workflow: TDD → gates → PR

1. **Test first.** RED → GREEN → REFACTOR. Pick the test layer that matches the change — unit for a
   function, integration for wired components, e2e for a public-endpoint behavior, live for the full
   stack. Test-layer model: [`wiki/testing.md`](wiki/testing.md).
2. **Run the gates** (below) until green — locally, before you push.
3. **Open the PR** and walk [Appendix A](#appendix-a-pull-request-checklist-copy-paste).

---

## Contributions by kind of change

### Add a backend feature

Follow the layering (routers → schemas → services → models → repos): put the concern in the layer
that owns it, don't reach past it ([`CONVENTIONS.md`](./CONVENTIONS.md#2-one-concern-per-layer-backend)).

- Fully type-annotate; `mypy` is **strict** (`disallow_untyped_defs`, `pyproject.toml`).
- Add or adjust the test at the right layer; keep tests hermetic (httpx `ASGITransport`).
- Keep the OpenAPI surface intentional (a new router = new `/api/<version>/…` routes).
- Depth: [`wiki/backend-architecture.md`](wiki/backend-architecture.md),
  [`wiki/developer-guide.md`](wiki/developer-guide.md).

### Add a frontend feature

Match the existing `frontend/` structure (components, `lib/api`, generated types).

- `prettier` + `eslint` (`--max-warnings=0`) + `npm run typecheck` (app and tests) all clean.
- Regenerate API types after a backend change (`npm run openapi-gen`) so the client stays in sync.
- Unit (Vitest) for logic; RTL+MSW for component behavior; Playwright for full-stack E2E.
- Depth: [`wiki/frontend-architecture.md`](wiki/frontend-architecture.md).

### Add or modify a Helm chart

Per-environment values only — no hard-coded ports/hosts/URLs
([`CONVENTIONS.md`](./CONVENTIONS.md#4-config-driven-not-hard-coded)).

- Every environment-specific value lives in `charts/your-service-name/values.<env>.yaml`.
- Validate before you commit: `helm lint charts/your-service-name && helm template … | …`.
- The image tag is taken from CI (git-derived) or `values.<env>.yaml` — never pinned by hand.
- Depth: [`wiki/infrastructure.md`](wiki/infrastructure.md),
  [`wiki/environment-variables.md`](wiki/environment-variables.md).

### Change CI

Edit the workflows in `.github/workflows/`; shared steps live in reusable workflows and the composite
actions under `.github/actions/`, so each concern stays in one file.

- Pin every third-party action by a full 40-character SHA with a `# vX.Y.Z` comment, and every tool
  by version and checksum; set `permissions`, `concurrency` and `timeout-minutes`.
- A blocking job fails on findings and joins the `CI OK` gate (`needs:` of the `ci-ok` job); an
  advisory job turns findings into `::warning` annotations and is never required.
- Lint your change: `infra/security/check-ci-hardening.sh`, `actionlint` and `zizmor .github/`.
- Depth: [`wiki/ci-cd.md`](wiki/ci-cd.md).

### Add or extend a supply-chain scanner

**Do not hand-roll it.** Invoke the scoped skill — it encodes the established path (pin in
`config.yaml` → `infra/security/local/scan-<name>.sh` → CI job → `devsec.sh` subcommand → bats test)
and the two invariants that keep the template sound:

- **Never edit the eight frozen `infra/security/*.sh` scripts** (incl. `_helpers.sh`) — new scanner logic goes under
  `infra/security/local/`.
- **Checksum-verified installs only** (never `curl | bash`); a failed install degrades to an honest
  `SKIPPED`, never a false green.

→ **Use the `add-supply-chain-scanner` skill** (`agent/skills/add-supply-chain-scanner`). Depth:
[`wiki/security.md`](wiki/security.md), [`wiki/containerization.md`](wiki/containerization.md).

### Write or adapt docs

Keep `wiki/` in sync with the code; one home per fact
([`CONVENTIONS.md`](./CONVENTIONS.md#3-one-source-of-truth-single-source--dry)).

- A doc **links** to a `wiki/` page; it does not restate it.
- Every internal link must resolve — verify with the doc-link checker before you open the PR.
- Examples use the placeholder name and env-driven values; no secrets or real internal URLs.

---

## Quality gates

Run these before every commit / PR. The exact commands (verified against this repo's Makefile + CI on
2026-11-02):

**Backend (from repo root):**

| Gate | Command | CI job |
|---|---|---|
| Pre-commit (dry, read-only) | `make check-all-dry` | — (local only) |
| Git hooks (all files) | `make precommit` | `ci` / `hadolint`, `repo`, `workflows` run the same tools |
| Format (ruff) | `make ruff-format-check` | `reusable-backend` / `lint` |
| Lint (ruff) | `make ruff-lint` | `reusable-backend` / `lint` |
| Type check (mypy) | `make mypy-check` | `reusable-backend` / `lint` |
| Unit tests | `make test-unit` | `reusable-backend` / `unit` (3.12, 3.13, advisory 3.14) |
| Integration | `make test-integration` | `reusable-backend` / `integration` |
| E2E | `make test-e2e` | `reusable-backend` / `e2e` |
| Live | `make test-live` | `reusable-live` / `live` |
| Coverage (≥85%) | `make test-coverage` | coverage in `reusable-backend` / `unit` |
| Shell lint | `make shellcheck` | `ci` / `repo` |

`make check-all-dry` is the pre-commit gate (read-only); `make check-all` is the same but
auto-formats. `mypy` runs with no path args (config-driven). Full table + raw commands + marker
model: [`wiki/code-quality.md`](wiki/code-quality.md).

**Frontend (from `frontend/`):**

| Gate | Command | CI job |
|---|---|---|
| Format (prettier) | `npm run format-check` | `reusable-frontend` / `prettier` |
| Lint (eslint) | `npm run lint` | `reusable-frontend` / `eslint` |
| Type check (tsc) | `npm run typecheck` | `reusable-frontend` / `type-check` |
| Unit (vitest) | `npm run test` | `reusable-frontend` / `unit` |
| Build (Next.js) | `npm run build` | `reusable-frontend` / `build` |

Real browser E2E runs in `reusable-live` / `live`; `playwright-list` only validates the Playwright config.

> The authoritative, copy-paste gate reference (with the raw commands and the pytest/Vitest marker
> model) is the **`quality-gates` skill** — `agent/skills/quality-gates`. Trigger it whenever you
> change backend (`src/`, `tests/`) or frontend (`frontend/`) code and want to verify it is clean.

## CI/CD

The workflows are `ci` (the quality gate, ending in the required `CI OK` check), `release`
(image build, scan and deploy), `dast`, `deploy` and `deploy-ops`.

- Blocking gates: ruff, mypy, the frontend lint, format and type checks, the tests, shellcheck, the
  secret-template check, the chart lint and the workflow hardening lint.
- Tests run per layer on pull requests, on pushes to `main` and on tags; `live` is advisory on
  pull requests and blocking on `main` and tags.
- Image build/sign/attest run only on pushes to `main` and `v*` tags — never in a pull request.
- Depth (workflows, jobs, gates, the job map): [`wiki/ci-cd.md`](wiki/ci-cd.md),
  [`wiki/security.md`](wiki/security.md).

---

## Appendix A: Pull-request checklist (copy-paste)

```
Testing
- [ ] Change is TDD-first: a failing test, then the minimal code, then refactor.
- [ ] Test layer matches the change (unit / integration / e2e / live).
- [ ] New behavior is pinned by a test that would fail without the code.

Correctness / conventions
- [ ] Backend follows the layering (routers → schemas → services → models → repos).
- [ ] Fully type-annotated; mypy strict passes.
- [ ] No hard-coded service name / port / URL — placeholder + env-driven (CONVENTIONS.md).
- [ ] No duplicated source of truth (command / version / path has one home).
- [ ] Helm/chart changes are per-env values only; no pinned image tags by hand.
- [ ] No image build/sign/attest job runs on this PR (`main` and `v*` tags only).
- [ ] Did NOT edit the eight frozen infra/security/*.sh scripts (incl. `_helpers.sh`; local/ only).

Gates
- [ ] Backend:  make check-all-dry && make test   → green
- [ ] Frontend: npm run format-check && npm run lint && npm run typecheck && npm run test → green
- [ ] shellcheck clean.

Safety / hygiene
- [ ] No secrets, real internal URLs, or project IDs in code, tests, docs, or examples.
- [ ] Docs updated and in sync; every internal link resolves.
- [ ] CHANGELOG / wiki updated where the change touches them.
```
