# AGENTS.md

Always-on instructions for coding agents in this repo. This is the **cross-tool source of truth**
(read by Claude Code, Codex, Cursor, Copilot, Gemini). It is deliberately lean — the depth lives in
[`CONTRIBUTING.md`](./CONTRIBUTING.md), [`CONVENTIONS.md`](./CONVENTIONS.md), `wiki/`, and
`agent/skills/`. When in doubt, this file points at them instead of restating them.

**What this is:** a *service template* — a FastAPI + Next.js 16 service (NGINX gateway, Helm, GitHub
Actions, supply-chain hardening) that adapts to each adopter. You are contributing for **every future
service built from it**, not just this checkout. See [`CONVENTIONS.md`](./CONVENTIONS.md).

## Commands (the ones you can't guess)

```bash
make check-all-dry        # pre-commit gate: drift + ruff format --check + ruff check + mypy (read-only)
make check-all            # same, but auto-formats (rewrites files)
make precommit            # every pre-commit hook on all files (install the git hook once: .tools/bin/pre-commit install)
make test                 # backend unit + integration + e2e (hermetic; excludes live)
make test-unit            # backend unit only
make test-integration / test-e2e / test-contract / test-live
make shellcheck           # lint every tracked *.sh + *.bats
make pins-refresh         # after bumping a pin: write the vendor checksums and regenerate the hashed locks
cd frontend && npm run lint && npm run typecheck && npm run test   # frontend gates
```

Adapt the placeholder before running the app for real:
`infra/adapt/replace_service_name.sh -n <name> --dry-run` first. Full setup:
[`wiki/getting-started.md`](wiki/getting-started.md).

## Mandatory rules (each with a why)

1. **Never hard-code the service name or ports** — use `your_service_name` and env/Helm values. A
   literal name or port is a landmine for the next adopter. [`CONVENTIONS.md`](./CONVENTIONS.md#1-the-service-name-is-a-placeholder-never-a-literal)
2. **Put each concern in its layer** (backend: routers → schemas → services → models → repos). Reach
   past a layer and you make the service un-swappable. [`CONVENTIONS.md`](./CONVENTIONS.md#2-one-concern-per-layer-backend)
3. **One source of truth per fact** — never duplicate a command, version, or path; a doc links, it
   doesn't restate. [`CONVENTIONS.md`](./CONVENTIONS.md#3-one-source-of-truth-single-source--dry)
4. **Config-driven, not hard-coded** — ports/hosts/URLs come from env / `.env` / `values.<env>.yaml`.
   [`CONVENTIONS.md`](./CONVENTIONS.md#4-config-driven-not-hard-coded)
5. **Image ops (build/sign/attest/digest-resolve) run on `main` and `v*` tags only, never in a pull request.** A PR
   author must not be able to forge the supply-chain chain. [`CONVENTIONS.md`](./CONVENTIONS.md#5-image-level-ops-run-on-main-and-release-tags-only)
6. **Never edit the eight frozen `infra/security/*.sh` scripts** — `_helpers.sh`, `sbom-generate.sh`,
   `scan-vulnerabilities.sh`, `scan-licenses.sh`, `generate-vex.sh`, `check-ci-hardening.sh`,
   `tools-install.sh`, `verify-image-digest.sh` — new supply-chain logic goes under
   `infra/security/local/`. (They were edited once, as a one-time re-baseline, to remove the old CI vendor coupling, and once more for a VEX correctness fix in `generate-vex.sh`; both were one-time, owner-approved edits and the rule is unchanged.) Add/extend a scanner via the `add-supply-chain-scanner` skill.
7. **No secrets in code, tests, docs, examples, or CI YAML** — and no real internal URLs / project
   IDs (redact them). CI's secret-template check and gitleaks fail the run on a leak.
8. **Version comes from Git tags (`setuptools_scm`) — never hard-code `__version__`.**
   [`CONVENTIONS.md`](./CONVENTIONS.md#6-version-comes-from-git-never-from-a-literal)

## Known pitfalls

- **Leftover `your_service_name`** in a new file/path/doc — the placeholder must stay a placeholder.
- **Hard-coded ports/hosts** (`:8000`, `localhost`) in code or CI — read them from the env var.
- **Editing the eight frozen `infra/security/*.sh` scripts** (incl. `_helpers.sh`) to add a scanner — use `infra/security/local/`.
- **Putting a build/sign/attest CI job in a PR run** — gate it to `main` and `v*` tags.
- **A secret or real internal URL in the diff** — redact; CI will block it (gitleaks, the secret-template check).
- **A doc cross-link that doesn't resolve** — verify links before opening the PR.
- **`make test` ≠ unit-only here** — it runs unit + integration + e2e; `make test-unit` is unit only.

## Where depth lives

| Need | Go to |
|---|---|
| Full guide, by kind of change | [`CONTRIBUTING.md`](./CONTRIBUTING.md) |
| Cross-cutting invariants (the "why") | [`CONVENTIONS.md`](./CONVENTIONS.md) |
| Per-domain depth (architecture, CI/CD, security, …) | `wiki/` |
| Scoped how-to skills (not auto-loaded — see `CLAUDE.md`) | `agent/skills/` |

**Scoped skills** (invoke by name, or install to `~/.claude/skills/`):
- `add-supply-chain-scanner` — add/extend a scanner the established way (pin → `local/` script → CI →
  `devsec.sh` → bats).
- `quality-gates` — run the full backend + frontend quality gates before any commit/PR.
