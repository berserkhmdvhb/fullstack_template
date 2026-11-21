---
name: quality-gates
description: Run this service template's full code-quality gates before any commit, push, or pull request - backend ruff (format + lint), mypy type-check, and pytest unit/integration/e2e/live; frontend prettier, ESLint, TypeScript type-check (tsc), and Vitest. Trigger this skill whenever a change is made to the backend (src/, tests/) or frontend (frontend/) and it must be verified clean, or the user says "check / lint / test / format / type-check my code", "run the gates / quality checks / pre-commit checks", "make sure the tests pass", "does this pass ruff/mypy", or "before I commit / open a PR / push" - even when they name no specific tool or file. Do NOT trigger for writing new tests, debugging a crashing process, fixing a single lint error, answering ruff/mypy/config questions, viewing coverage, or setting up a venv / installing deps. Covers both backend and frontend; a green backend does not excuse a skipped frontend.
compatibility: bash, make, ruff, mypy, pytest, npm, tsc, vitest; GitHub Actions (`.github/workflows/reusable-backend.yml`, `reusable-frontend.yml`, `reusable-live.yml`).
---

# Quality Gates

The standard operating procedure after **any** change to the backend (`src/`, `tests/`) or frontend
(`frontend/`), and before every commit or pull request (PR). Green here means green in CI — these
are the same commands the GitHub Actions workflows run.

## Golden rules

- **Always run the full gate before committing.** A change is not "done" until every gate below is
  green.
- **Fix, don't bypass.** If a gate fails, fix the code (or the test). Never weaken a gate to pass it.
- **Match CI, don't improvise.** Use the exact commands below. Ad-hoc commands can pass locally while
  CI fails (marker filters, `-o addopts`, config files).
- **Backend and frontend are independent.** Run both; a green backend does not excuse a skipped
  frontend.
- **Order matters on the backend:** format → lint → mypy → tests. Formatting first avoids lint noise.

## TL;DR — full gate

```bash
# ---------- BACKEND (run from repo root) ----------
make check-all-dry                 # drift + ruff format --check + ruff check + mypy (read-only)
make test-unit                     # unit tests only (default marker filter)
make test-integration              # integration suite
make test-e2e                      # e2e suite (ASGI in-process)
make test-live                     # live suite (needs a running full stack)

# ---------- FRONTEND (run from frontend/) ----------
npm run format-check && npm run lint && npm run typecheck && npm run test
```

For auto-formatting instead of a dry check, use `make check-all` (runs `ruff format` and rewrites
files) instead of `make check-all-dry`.

## Backend gates

| Gate | Make target | Raw command | CI job |
|---|---|---|---|
| Format check | `make ruff-format-check` | `ruff format --check $(RUFF_PATHS)` | `reusable-backend` / `lint` |
| Lint | `make ruff-lint` | `ruff check --no-fix $(RUFF_PATHS)` | `reusable-backend` / `lint` |
| Type check | `make mypy-check` | `mypy` *(config-driven: no path args)* | `reusable-backend` / `lint` |
| Unit tests | `make test-unit` | `pytest tests/ -v` *(default addopts = unit only)* | `reusable-backend` / `unit` |
| Integration | `make test-integration` | `pytest tests/ -m integration -o addopts="" -v` | `reusable-backend` / `integration` |
| E2E | `make test-e2e` | `pytest tests/ -m e2e -o addopts="" -v` | `reusable-backend` / `e2e` |
| Live | `make test-live` | `pytest tests/ -m live -o addopts="" -v` | `reusable-live` / `live` |

Notes:
- `make check-all-dry` bundles drift-check + format-check + lint + mypy (read-only, safe for
  pre-commit). `make check-all` is the same but auto-formats.
- `mypy` runs with no path args because `pyproject.toml` sets `files = ["src", "tests"]` under
  `[tool.mypy]` (strict; `mypy_path = "src"`), so the config drives the scope — no path args needed.
- `make test` (no suffix) runs **unit + integration + e2e** (hermetic, `-m "not live"`) — it is
  **not** unit-only here. Unit-only is `make test-unit`, which relies on the default `addopts` marker
  filter.
- Integration/e2e/live pass `-o addopts=""` to **clear** the default marker filter, otherwise the
  `-m` flag would conflict with it.
- `make test-live` needs the full running stack (backend + frontend + NGINX); in CI it runs in the `live` job on a GitHub-hosted runner.

### Test marker model (backend)

pytest markers live in `pyproject.toml` → `[tool.pytest.ini_options].markers`:

| Marker | Meaning | Selection |
|---|---|---|
| *(none / default)* | unit tests | `-m "not integration and not e2e and not live"` (the default `addopts`, so `make test-unit` = unit only) |
| `integration` | hermetic, wires internal components, no real external IO | `-m integration` |
| `e2e` | end-to-end through the public API (ASGI in-process), network/LLM edges stubbed | `-m e2e` |
| `live` | needs a running external stack | `-m live` |

## Frontend gates

Next.js 15 / React / TypeScript. Run from the `frontend/` directory.

| Gate | npm script | Raw command | CI job |
|---|---|---|---|
| Format (prettier) | `npm run format-check` | `prettier . --check` | `reusable-frontend` / `prettier` |
| Lint (eslint) | `npm run lint` | `eslint . --max-warnings=0` | `reusable-frontend` / `eslint` |
| Type check (TS) | `npm run typecheck` | `tsc --noEmit` | `reusable-frontend` / `type-check` |
| Unit tests (vitest) | `npm run test` | `vitest run` | `reusable-frontend` / `unit` and `integration` (CI runs `npm run test:unit` and `npm run test:integration`) |
| Build (Next.js) | `npm run build` | `next build` | `reusable-frontend` / `build` — gates the unit tests |
| E2E (playwright) | `npm run test:e2e` | `playwright test` | `reusable-frontend` / `playwright-list` (config/discovery only); the real run is `reusable-live` / `live` |

Notes:
- In CI the frontend gates run in parallel and `build` waits for lint, format and type-check; the
  unit tests wait for `build` (a broken production build fails before tests run). Locally, run
  `npm run build` before the unit tests if you want to reproduce that gate.
- The full browser E2E runs in the `live` job against a started stack (advisory on pull requests,
  blocking on `main` and tags); `playwright-list` only validates the Playwright config and test
  discovery.

## What "green" means

- **ruff format --check**: no files need reformatting.
- **ruff check**: no lint violations.
- **mypy**: no type errors (strict mode).
- **pytest**: exit 0; CI adds `--maxfail=1`. Coverage is measured (`--cov-fail-under=85` via
  `make test-coverage`) but is not a hard gate in the default pipeline.
- **prettier / eslint / tsc**: exit 0, no complaints.
- **vitest**: all tests pass.

## Reporting a result

State each gate on its own line with pass/fail, and paste the failing output for anything red. Don't
summarize "tests pass" — name the suite (unit / integration / e2e / live) so it is unambiguous which
marker ran.

## Related

- Full orchestration: `.github/workflows/ci.yml` → `reusable-backend.yml`, `reusable-frontend.yml`,
  `reusable-live.yml`; shell lint runs in the `repo` job of `ci.yml`.
- Backend gates + marker model: [`wiki/code-quality.md`](../../../wiki/code-quality.md).
- CI workflows / jobs / gates: [`wiki/ci-cd.md`](../../../wiki/ci-cd.md).
- Adding a supply-chain scanner (a separate concern from code-quality): the `add-supply-chain-scanner`
  skill.
