# agent/

Project-specific agent assets for this service template — its **supply-chain / DevSecOps
hardening** and its **code-quality gates**. These are bundled inside the repo so the methodology
travels with it when the template is cloned / renamed / transferred to another namespace or registry.

Nothing here is auto-loaded by Claude Code — it is a **transfer bundle**. On the target
machine, install any skill into the user skills dir:

```bash
# from the transferred repo root — one per skill you want
cp -r agent/skills/add-supply-chain-scanner ~/.claude/skills/
cp -r agent/skills/quality-gates           ~/.claude/skills/
```

After that, each skill is available to any session in the repo:
`add-supply-chain-scanner` (its `devsec.sh` / CI / bats scaffolds stamp out live in `infra/security/`
+ `.github/workflows/security.yml`) and `quality-gates` (runs the backend + frontend gates before any commit / PR).
Scoped skills are invoked by name in a session too (`/skill quality-gates`) without installing.

## Relationship to the root docs

The root `AGENTS.md` / `CONTRIBUTING.md` / `CONVENTIONS.md` are the **always-on + contribution
layer** for *any* change to the template — adding a backend feature, a frontend feature, a Helm
chart, CI, or docs. They are generic and live at the repo root so every agent reads them.

`agent/` is the **transfer bundle of scoped skills** — the deep
"how" for the two concerns that warrant a repeatable, skill-encoded procedure (supply-chain scanning
and quality gates). The root docs *reference* these skills for that depth; the skills do not restate
the root docs. (General-purpose skills like `officecli` are **not** here — see Scope below.)

## What's in here

```
agent/
├── README.md                                    # this file
├── skills/
│   ├── add-supply-chain-scanner/                # the methodology, as a Claude Code skill
│   │   ├── SKILL.md                             # when to trigger + the 10-step path + DoD
│   │   ├── references/                          # script-side / ci-side / docs-and-verify
│   │   ├── scripts/new-scanner.sh               # scaffolder: stamps scan-<name>.sh + bats + CI job
│   │   └── evals/trigger-eval.json              # trigger-eval queries for the skill
│   └── quality-gates/                           # the methodology, as a Claude Code skill
│       └── SKILL.md                             # when to trigger + backend/frontend gate commands
```

## Scope

- **Included:** the two scoped skills (the deep, repeatable "how") — `add-supply-chain-scanner` and
  `quality-gates` (the backend + frontend gates, reconciled to this repo's Makefile targets and
  GitHub Actions).
- **Not here:** the project work records. The plans / review that recorded how *this* template was
  brought up to its supply-chain standard (digest-pin, OpenVEX, typosquat/malicious-dep detection, CI
  self-check; keyless signing + SLSA provenance since delivered in `reusable-build.yml`) and CI/CD standard (digest-pin / distribution
  integrity)
  were working artifacts, not part of the transfer bundle. The delivered docs live in `wiki/`
  (incl. `wiki/supply-chain-tooling-assessment.md`, the tooling decision record); the jobs in
  `.github/workflows/security.yml`, `.github/workflows/reusable-build.yml` and `infra/security/`.
- **Excluded on purpose:** general-purpose skills (e.g. `officecli`) — those are not specific to
  this project and belong in the user skills dir, not the repo.
- **Roadmap:** what is delivered and what stays outside the repository (admission enforcement on a
  real cluster, among others) — see `wiki/ROADMAP-supply-chain.md`.

## Provenance

Copied from a personal Claude skills directory on 2026-11-02. If the source skill changes,
re-copy to keep the bundle in sync.
