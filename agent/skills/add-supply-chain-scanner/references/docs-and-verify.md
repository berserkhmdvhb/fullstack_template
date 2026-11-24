# Docs + verify — the "document it" and "verify it" steps

Repo root: `…/your-service-name/`.

## Document it

A scanner is not "added" until the docs reflect the new layer. Three places:

### 1. `wiki/supply-chain-security.md` and `wiki/ci-cd.md` — the control catalogue
`wiki/supply-chain-security.md` is the catalogue of every scanner: the tool, what it
targets, gate-vs-advisory and ref scope (pull requests vs `main` and tags). Add a row
for the new scanner and **fix any now-stale row** it supersedes. `wiki/security.md`
(section `## Supply-Chain Security (CI)`) only points at that page, so it needs no
edit. In `wiki/ci-cd.md` add the leg to the workflow map (`security.yml`) and, for a
gate, to the blocking row of "Advisory vs blocking".

### 2. `wiki/supply-chain-tooling-assessment.md` — the decision record
Records the tool-SELECTION decision (adopt X), the defense-in-depth "layered"
rationale, an overlap ledger, adoption options, and explicit "retire X" rulings.
Structure: §0 framing (overlap is expected; unconsolidated overlap is the
anti-pattern), §1 existing stack (concern/tool/target/data-source/model table),
§2 target architecture (layers 0 Inventory / 1 Known-CVE SCA / 2
Malicious-typosquat / 3 Provenance, with a "Gate / advisory" column), §3 tool
assessments, §4 overlap ledger, §5 adoption options, §6 verify-on-adopt flags,
§7 scope decisions.

Add the new tool to §1's table, justify its layer in §2 (which *distinct*
target / data-source / detection-model it covers — an overlap is justified only
when it differs; the narrower overlapping tool should be retired, not stacked),
state what it retires in §4, and, if resolved, extend the "Adoption outcome
(resolved)" callout. Key discipline line: an overlap is wasteful when two tools
do the identical thing on the identical target from the identical feed.

### 3. `infra/security/local/README.md` — the developer-facing map
- **Subcommand table** (`## Subcommands`): add a row `| <name> | what it does |`
  labeling it report-only / gate-stop / opt-in (existing rows: osv,
  malware-heuristic, license, malware, audit, ci-hardening, sign/attest/publish, all).
- **Fail-policy chain** (`## Fail policy (mirrors the CI gate)`): if the scanner
  changes chain position, update the chain-order line
  (`build → sbom → vuln → osv → malware-heuristic → license → malware → vex →
  audit → ci-hardening`) and place the step in the STOP row (sbom, vuln, malware,
  vex, ci-hardening — gate-stop) or REPORT row (osv, malware-heuristic, license,
  audit — warn-continue). A VEX doc with 0 statements is a successful run (exit 0).
- **CI↔local map** (`## CI ↔ local job map`): add `| devsec.sh <name> | <ci_job> |`.

The eight-frozen / local-adaptation invariant is documented in three sites
(`local/README.md`, the plan ledger, `supply-chain-tooling-assessment.md` §7):
`devsec.sh` invokes the eight `infra/security/*.sh` verbatim (never edited); all
local adaptation lives under `infra/security/local/`.

## Verify it

Run all four; a scanner is done only when all are green.

- **bats (runner behavior):**
  - all: `LIB_TS=0 bats infra/security/local/tests/` (the CI form, the `repo` job of `ci.yml`) — or locally `bats infra/security/local/tests/`.
  - one file: `bats infra/security/local/tests/devsec-<name>.bats`.
- **pytest (config/artifact contract):** `pytest tests/supply_chain/test_ci_security_jobs.py`
  (whole suite: `make test`, unit + integration + e2e; its exact command is in the Makefile;
  one test: `pytest tests/supply_chain/test_ci_security_jobs.py::test_<name>`).
  It parses the committed YAML and asserts the new job's contract — it does NOT
  run the scanner.
- **shellcheck (style gate):** `make shellcheck` (local) or `bash scripts/ci/shell-lint.sh`
  (CI) — lints every git-tracked `*.sh` (default dialect) + every `*.bats` (as
  bash). Your new `*.sh` AND its `*.bats` must be clean at default level.
- **Real end-to-end:** `./devsec.sh <name>` (single step) and
  `DRY_RUN=true ./devsec.sh all` (dry-run, no side effects). For an advisory
  scanner confirm the run writes `output/<name>/report.json` with `.status`
  GREEN/RED/SKIPPED and the process exits 0; for a gate scanner confirm a finding
  makes `./devsec.sh all` stop the chain and fail the `summary` gate.

Record delivered verification as `VERIFIED:` lines in your plan or
progress log, if you keep one: pytest N/N, bats N/N, shellcheck
clean, and a real `devsec.sh` run with the observed exit code / `report.json`
status — mirroring the existing ledgers.

## Advisory / report-only contract (the thing most often got wrong)

A report-only scanner's CI job is a leg of the `advisory` matrix in `security.yml`
(no `continue-on-error`; findings become a `::warning`), source-level (pull requests
and `main`), delegates to one
`infra/security/local/<tool>.sh`, and that script **always exits 0** — the verdict
is carried in `output/<name>/report.json` `.status`, never in the exit code. A
gate scanner is instead a blocking job whose script exits non-zero on
a finding. Documented in `.github/workflows/security.yml` (the header comment),
the scripts themselves (header docstring + explicit `exit 0`), `local/README.md`
(REPORT class), `supply-chain-tooling-assessment.md` (§2 "Gate / advisory"), and
and the supply-chain docs under `wiki/`.
