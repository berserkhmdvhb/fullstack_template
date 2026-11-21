# CI side — wiring the scanner in GitHub Actions

Repo root: `…/your-service-name/`. The source-level scanners live in
`.github/workflows/security.yml`, which `ci.yml` calls on every run (no paths
gate) and which also runs weekly and by hand. The `security` job of `ci.yml`
is behind the required `CI OK` check, so a failing job in `security.yml` blocks
the merge and an advisory one never does.

## Two scanner archetypes

- **A. Source-level, advisory.** A leg of the `advisory` matrix in `security.yml`.
  It delegates to one wrapper in `infra/security/local/`. Examples: `osv`,
  `guarddog`, `checkov`, `kubescape` (and `pip-audit`, which is its own job).
- **B. Image-based, `main` and `v*` tags only.** A job in `release.yml` that works
  by the digest the `build` job verified (`sbom`, `grype`, `grant`, `vex`). Image
  operations (push, sign, attest, digest resolution) never run in a pull request.

A new supply-chain *scanner* is almost always archetype A. Use B only when the
scanner must consume a built image digest.

## Anatomy of the advisory matrix

```yaml
  advisory:
    name: ${{ matrix.id }} (advisory)
    strategy:
      fail-fast: false
      matrix:
        include:
          - id: osv                          # the leg name, also the artifact name
            category: osv-scanner            # the code scanning category
            script: scan-osv.sh              # the wrapper in infra/security/local/
            sarif: osv/osv.sarif             # relative to $SEC_OUTPUT_DIR
```

Each leg: installs the pinned tool (the `case "$TOOL_ID" in` in the step "Install
the pinned tools"), runs `bash infra/security/local/${SCRIPT} "$SEC_OUTPUT_DIR" "$PWD"`,
turns the verdict into a `::warning` plus a step-summary line, uploads the SARIF
to code scanning (`continue-on-error`, so a repository without code scanning
stays green) and uploads the whole output directory as an artifact named after
the `id`. A non-zero wrapper exit is a **tool error** and fails the leg;
findings never do.

The scaffolder (`scripts/new-scanner.sh`) prints the matrix leg and the `case`
line to paste. A pip tool also needs its id added to the `if:` of the
`setup-python` step.

### What a new advisory leg must set

| Key | Notes |
|---|---|
| `id` | lowercase slug; the leg and artifact name |
| `category` | unique code scanning category |
| `script` | the wrapper file name under `infra/security/local/` |
| `sarif` | `<name>/<name>.sarif`; `report.json` sits next to it |
| the `case` line | installs the pinned tool, or the OS deps for a pip tool |

Job-level keys (`permissions`, `timeout-minutes`, `concurrency`, the SHA-pinned
`uses:`) are already set on the shared `advisory` job; do not weaken them.

## A gate scanner

A scanner that must fail the run on a finding is a **standalone job** in
`security.yml` (not a matrix leg, because the matrix is advisory). The scaffolder
prints it with `--gate`. A failure fails the `security` job of `ci.yml`, so it
blocks through `CI OK`. List it in `wiki/ci-cd.md` as blocking. Set
`permissions`, `timeout-minutes` and a SHA-pinned `uses:` like every other job.

## Runtime and images

There is no scanner image map. The jobs run on `ubuntu-24.04` runners and install
what they need: tools through `scripts/tools.sh` (`tools/pins.tsv`) or
`infra/security/tools-install.sh` (`infra/security/config.yaml`), OS packages
through `apt-get`. `.github/container-images.yml` is a different thing: the map of
the application's own buildable images, read by `release.yml`.

## How `config.yaml` is consumed — CI side vs local side

`config.yaml` is the single source of truth for pinned tool versions (`.tools`
checksum CLIs; `.tools_pip` pip tools) and `.output.base`.

- **(a) CI reads `config.yaml` via `yq` and passes the version to `tools-install.sh`**
  — the binary tools, in the `case "$TOOL_ID" in` of the advisory job:
  ```bash
  osv) bash infra/security/tools-install.sh osv-scanner "$(yq -r '.tools.osv-scanner' infra/security/config.yaml)" "${RUNNER_TEMP}/bin" ;;
  ```
- **(b) CI installs only system dependencies; the wrapper reads the pin via `cfg()`**
  and pip-installs the tool (`guarddog`, `checkov`). The OS packages come from the
  `case` line; the Python comes from the `setup-python` step.
- **Exception — `pip-audit`** is hash-pinned in `tools/requirements/pip-audit.txt`
  and does not use `config.yaml`.
- **Local side:** `cfg()` in `infra/security/_helpers.sh` reads the same file, so
  CI and a laptop never drift.

## Ref scope and the advisory/blocking rule

- **Source-level jobs** run on pull requests (fast feedback), on `main` and tags,
  weekly, and by hand. No `if:` limits them to a ref.
- **Image jobs** are in `release.yml`, which triggers only on a push to `main` or a
  `v*` tag. A pull request never pushes, scans or signs an image (the `images` job only builds one into the local daemon to check it).
- **Advisory** = findings become `::warning` and a summary line; only a tool error
  fails; never a required check; the job name ends in `(advisory)`.
- **Blocking** = fails on findings; reachable from the `CI OK` gate.

## The CI contract test — `tests/supply_chain/test_ci_security_jobs.py`

This is the "definition of done" for CI wiring. It loads `security.yml` through
`tests/supply_chain/_workflows.py` and asserts the contract. Helpers: `jobs()`,
`_runs(job)` (the `run:` text of every step), `_matrix(id)` (one leg of the
advisory matrix).

For a new advisory leg add a case that asserts: the leg exists in
`advisory.strategy.matrix.include` with the right `script` and `sarif`; the script
path is the wrapper in `infra/security/local/`; the advisory job is labelled
`(advisory)`, has no `continue-on-error`, reports `::warning` and still fails on
`::error`. The test hardcodes script path constants (for example `OSV_SCRIPT`), so
**add a constant and assertions for your new wrapper.** For a gate job, add it to
`BLOCKING` and check that it has no `continue-on-error`.

## Verify the workflow change

```bash
actionlint
zizmor .github/
infra/security/check-ci-hardening.sh
.venv/bin/pytest tests/supply_chain/test_ci_security_jobs.py -q
```

## Reports / artifacts note

SARIF goes to code scanning through `github/codeql-action/upload-sarif` (a SHA-pinned
`uses:`, with a distinct `category`), and the whole `$SEC_OUTPUT_DIR` goes up as an
`actions/upload-artifact` artifact (retention 90 days). Files that pass between
jobs of the release workflow (`scan.env`, `image-digests.env`) travel as artifacts
(`digest-<id>`), not as job outputs of secrets.
