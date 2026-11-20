---
name: add-supply-chain-scanner
description: Add or extend a supply-chain security scanner in this GitHub service-template repo the established way — a single-source script under `infra/security/local/` shared by a CI job (`.github/workflows/security.yml`), the `devsec.sh` subcommand, and a bats test, driven by the pinned `infra/security/config.yaml`. Use it whenever you add a scanner (SCA / malicious / typosquat / SBOM / license / image / provenance) or scan dependencies, lockfiles, or container images for vulnerabilities, secrets, or misconfiguration; wire a scanner into CI; add a `devsec.sh` subcommand; pin a version; or harden the `infra/security` scripts — even phrased as a missing scan (e.g. "our deps/images aren't scanned — add that check", "why isn't there a CI job for X", or just "add Trivy/grype/gitleaks"). Encodes the frozen-eight/local-adaptation invariant (new scanners under `local/`, never edit the eight), advisory-vs-gate contract, checksum-verified installs (never curl|bash), honest SKIPPED degradation, and TDD with bats.
compatibility: bash, bats, yq, jq, python3, shellcheck, GitHub Actions, actionlint, zizmor; the repo layout this skill targets is `infra/security/` + `.github/workflows/security.yml` + `infra/security/local/`.
---

# Add / extend a supply-chain scanner

The goal is that **CI and a developer laptop never drift**: one script under
`infra/security/local/` is the single source of truth, and a new CI job, a
`devsec.sh` subcommand, and a bats test all call that same script with the same
args. This skill is the repeatable path to add one.

Use it end to end, in order. Each step names the file(s) it touches and the
reference with the exact detail. Do not skip the TDD or the CI-contract test —
they are the quality gate that makes "production-grade" real, not a claim.

## Two invariants up front (do not violate)

1. **Frozen eight / local adaptation.** The shared `infra/security/*.sh` scripts
   and runtime (`_helpers.sh`, `tools-install.sh`, `config.yaml`) are the stable
   contract. A new scanner lives under **`infra/security/local/`** — never edit
   the eight to add scanner logic. The only edits to shared files are the ones
   this skill calls for: a pin in `config.yaml`, a `cmd_*` + module-var seam in
   `devsec.sh`, and (if the tool is checksum-verified) a case in `tools-install.sh`.
2. **Advisory vs gate — decide it explicitly.** A *report-only* (advisory) scan
   writes its verdict to `report.json` and **always `exit 0`** — a finding is a
   red in the report, never a pipeline block (its CI job is a leg of the advisory matrix: a `::warning`, never a required check).
   A *gate* fails the job on a finding. Most supply-chain discovery scanners are
   advisory; SBOM/provenance/signing correctness is usually a gate. Get this
   wrong and you either block merges on noise or silently report green.

## Classify the scanner first (one decision drives the whole path)

Answer these before writing code; they select the branches below:

- **Delivery:** checksum-verified release binary (goes in `config.yaml` → `tools:`
  and installs via `tools-install.sh`) **or** pip-only (→ `tools_pip:`, `pip install`)?
- **Contract:** advisory (report + `exit 0`, a leg of the advisory matrix) or gate (a blocking job)?
- **Input:** source/lockfile (per-dep, offline snapshot) or container image?
- **Language scope:** single-language lockfile or multi-language repo tree?

## The path (10 steps)

> The two references carry the exact detail — read the one each step points to.
> `references/script-side.md` → steps 3–4. `references/ci-side.md` → steps 5–6.
> `references/docs-and-verify.md` → steps 7–9.

0. **Read the pattern, not just this file.** Skim `references/script-side.md`
   (anatomy of a scanner script + its bats test) so you mirror the real shape
   instead of inventing one. If a plan/ledger exists for this work
   (`.superpowers/sdd/PLAN-*/progress.md`), resume from it — record every ruling
   there, not in your own memory.

1. **Pin the version in `infra/security/config.yaml`.** Checksum binary → add a
   flat key under `tools:` (`.tools.<name>`). pip-only → under `tools_pip:`
   (`.tools_pip.<name>`). This pin is the single source of truth read by the
   script, the CI job, and `tools-install.sh` — one version, everywhere.

2. **Write `infra/security/local/scan-<name>.sh`** — the single source. Follow
   the anatomy checklist in `references/script-side.md` (§ "anatomy of a scanner
   script"): shebang + docstring, `set -Eeuo pipefail`, source `_helpers.sh` +
   `trap 'trap_err' ERR`, `--help`, defaults (the `*_BIN` test seam,
   `resolve_sec_output_dir`, `OUT_DIR`), pin + install-if-missing, enumerate
   inputs (missing manifest = skip cleanly, never fatal), run the tool with its
   non-zero exit captured into a verdict var (so `set -e`/ERR can't abort before
   reports are written), write a SARIF (`<name>.sarif`) for
   the code scanning upload, write `report.json` with a `.status` (GREEN/RED/SKIPPED),
   then exit per the contract. **Installs are checksum-verified only** — never
   `curl | bash`; a failed install must degrade to an honest `SKIPPED`, never
   hard-die (`trap_err`) and never falsely report green.

3. **TDD the script with bats** (`references/script-side.md`, § "anatomy of its
   bats test"): create `infra/security/local/tests/devsec-<name>.bats`, stub the
   real tool on `PATH` (preserving `BASE_PATH` so `yq`/`python3`/`jq` still
   resolve), and cover clean / finding / install-fail(-or-no-toolchain) → SKIPPED.
   Watch the failing test first (RED), then implement (GREEN). Assert `.status`
   with `yq`, argv with `grep -qF` on the stub's argv log, and "no hard-die" with
   `[[ "$output" != *"ERROR @"* ]]`.

4. **Wire the CI job in `.github/workflows/security.yml`** (`references/ci-side.md`): an
   advisory scanner is a new leg of the `advisory` matrix (`id`, `category`, `script`,
   `sarif`) plus a line in the install step's `case`; a gate scanner is a standalone
   blocking job. Either way the job calls
   `bash infra/security/local/scan-<name>.sh "$SEC_OUTPUT_DIR" "$PWD"`, uploads the SARIF
   to code scanning, and matches the contract from step 0. The tool version comes from
   `config.yaml` (installed with `tools-install.sh`), never typed twice. Every `uses:`
   stays pinned to a full SHA.

5. **Wire the local runner.** Add a `cmd_<name>()` to
   `infra/security/local/devsec.sh` and a module-var seam
   (`<NAME>_SCAN_SH="${<NAME>_SCAN_SH:-$_SEC_DIR/local/scan-<name>.sh}"`) so
   `devsec.sh <name>` invokes the same script with the same two positional args
   (`$SEC_OUTPUT_DIR` `$REPO_ROOT`). Add the subcommand to the help/usage table.

6. **Document it** (`references/docs-and-verify.md`): update the layered-scanning
   section of `wiki/security.md`, record the tool decision / adoption outcome in
   `wiki/supply-chain-tooling-assessment.md`, and add the scanner's row to the
   `local/README.md` subcommand table + CI↔local map.

7. **Verify the whole thing** (`references/docs-and-verify.md`, the exact commands):
   - `bats infra/security/local/tests/` (all) and your new file — green.
   - `pytest tests/supply_chain/test_ci_security_jobs.py` — the CI-contract test
     for your new job passes (matrix leg / calls the local script / advisory label).
   - `actionlint`, `zizmor .github/` and `infra/security/check-ci-hardening.sh` — clean.
   - `shellcheck` on every script you touched — clean.
   - a real `devsec.sh <name>` run (or `--dry-run`) — ends with the right verdict.

8. **Commit** (this repo's convention): one focused message per logical change,
   conventional-commit prefix, **no `Co-Authored-By` line**. If the work spans
   script + CI + docs, say so in one message or split — match the surrounding
   history. Write the message in chat for the user to commit (do not commit
   yourself unless explicitly told to).

## Definition of done

A scanner is "added the right way" only when all hold:

- [ ] ONE script under `infra/security/local/`; CI job + `devsec.sh` + bats call it.
- [ ] Pin lives in `config.yaml` and is read by script **and** CI (one version).
- [ ] The job is a matrix leg (advisory) or a blocking job; permissions, timeout and SHA-pinned `uses:` set.
- [ ] Install is checksum-verified (binary) or pinned-wheel (pip); no `curl|bash`.
- [ ] Non-zero tool exit is captured as a verdict — reports are written *first*.
- [ ] `report.json` has `.status`; SARIF written for code scanning.
- [ ] Advisory↔gate and the job form (matrix leg vs blocking job) agree with step 0's classification.
- [ ] bats covers clean / finding / SKIPPED; `pytest` CI-contract test passes.
- [ ] `shellcheck` clean; real `devsec.sh <name>` run verified.
- [ ] `wiki/security.md`, tooling-assessment, and `local/README.md` updated.

## Scaffolder (optional but recommended)

`scripts/new-scanner.sh` stamps out the three boilerplate files (the scanner
script, its bats file, and the ready-to-paste `security.yml` wiring) pre-wired
to this anatomy, so you only fill in the scanner-specific logic. Run it, then work
the 10 steps — it removes the copy-paste, not the judgment.
