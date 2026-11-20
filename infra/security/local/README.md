# Local supply-chain security runner

Run this project's CI supply-chain security steps **on a laptop** instead
of in CI.

`devsec.sh` is a thin adapter: it derives the GitHub Actions variables (`GITHUB_SHA`,
`GITHUB_REF_NAME`, `GITHUB_REPOSITORY`, `GITHUB_SERVER_URL`) from the local
git repo, layers your per-developer registry/project/credentials on top, and then
invokes the seven CI step scripts under `infra/security/` **verbatim** (they share
the sourced `_helpers.sh` runtime) — none of the eight `infra/security/*.sh` scripts
are edited by the local layer. Every local adaptation lives here, under
`infra/security/local/`.

```
infra/security/ — eight shell scripts, the frozen surface (the runner never edits these):
                  _helpers.sh is the shared runtime; the other seven are the CI step scripts.
├── _helpers.sh                 shared runtime (sourced, never executed)
├── sbom-generate.sh            CI: release.yml `sbom`
├── scan-vulnerabilities.sh     CI: release.yml `grype`
├── scan-licenses.sh            CI: release.yml `grant`
├── generate-vex.sh             CI: release.yml `vex`
├── check-ci-hardening.sh       CI: ci.yml `workflows` (blocking)
├── tools-install.sh            pinned, checksum-verified tool installer
└── verify-image-digest.sh      CI: release.yml `build` / `digests`

infra/security/local/
├── devsec.sh                   the wrapper: derives CI env, sequences the chain, applies the fail policy
├── report.sh                   read-only views over the scan JSON (runs no scanner)
├── scan-osv.sh                 single source for the CI `osv` leg (OSV-Scanner)
├── scan-malware-heuristic.sh   single source for the CI `guarddog` leg (GuardDog)
├── scan-iac.sh                 single source for the CI `checkov` leg (Checkov, IaC policy)
├── scan-k8s.sh                 single source for the CI `kubescape` leg (Kubescape, workload posture)
├── scan-dast.sh                single source for the CI `dast` job (OWASP ZAP baseline)
├── _ui.sh                      shared presentation layer (colour, glyphs, banners)
├── .env.local.example          per-developer overrides (gitignored — never commit the real .env.local)
└── tests/                      bats unit tests (stub docker + the CI step scripts)
```

## Prerequisites

| Tool | Used for | Provisioned by |
|------|----------|----------------|
| `docker` | building the images to scan | you (daemon must be reachable) |
| `syft`   | SBOM generation            | `devsec.sh tools` (pinned, checksum-verified) |
| `grype`  | vulnerability scan         | `devsec.sh tools` (pinned, checksum-verified) |
| `grant`  | license scan               | `devsec.sh tools` (pinned, checksum-verified) |
| `sbomqs` | SBOM quality + NTIA scoring | `devsec.sh tools` (pinned, checksum-verified) |
| `sbom-utility` | SBOM schema validation | `devsec.sh tools` (pinned, checksum-verified) |
| `jq`, `yq`, `git`, `bash` | parsing / git derivation | you (developer-managed, check-only) |
| `python3` | source-level typosquat scan — `scan-malware-heuristic.sh` (direct-dep enumeration, JSON) | you (developer-managed, check-only) |

Check / provision the tooling any time:

```bash
./devsec.sh tools
```

`jq`/`yq`/`docker`/`git`/`python3` are only *checked* (reported if missing);
`syft`/`grype`/`grant` — and the report-only `sbomqs` / `sbom-utility` SBOM-analysis
tools — are installed at their pinned versions when absent, never via `curl | bash`.
(`cosign` sign/attest is **Roadmap** — see
[`wiki/supply-chain-security.md`](../../../wiki/supply-chain-security.md) — so it is
not provisioned here.)

## Quick start

```bash
./devsec.sh build          # build the images + emit scan.env
./devsec.sh all            # run the chain in CI order, apply the fail policy
```

Preview everything with **no side effects** (prints each command, runs nothing):

```bash
DRY_RUN=true ./devsec.sh all
```

Run a single step:

```bash
./devsec.sh sbom
./devsec.sh vuln
./devsec.sh osv
./devsec.sh ci-hardening
# etc. — see `./devsec.sh help` for the full list.
```

## `.env.local` overrides

Optional, per-developer, **gitignored** — copy `.env.local.example`, never commit
your real values:

```bash
cp .env.local.example .env.local
```

| Key | Purpose |
|-----|---------|
| `REGISTRY` | override the local image registry (default: `ghcr.io`) |
| `GITHUB_REPOSITORY` | override the `<owner>/<repo>` used for image tags (lowercased) |
| `SEC_OUTPUT_DIR` | where scan artifacts are written (default from `config.yaml`, else `output/security`) |

Values loaded from `.env.local` are **never sourced** (parsed, not executed).

## Subcommands

| Subcommand | What it does |
|------------|--------------|
| `bootstrap`  | Derive the GitHub env from git + load `.env.local` |
| `build`      | Build the images from `.github/container-images.yml`, emit `scan.env` |
| `tools`      | Check / provision the security tooling |
| `sbom`       | Generate the SBOM (syft, image-first) |
| `vuln`       | Scan for vulnerabilities (grype, gated at `config.yaml` `policy.fail_level`) |
| `osv`        | Source-level SCA + malicious (OSV-Scanner) — *report-only* |
| `malware-heuristic` | Proactive typosquat scan (GuardDog, direct deps) — *report-only* |
| `iac`        | IaC misconfiguration scan (Checkov) — *report-only* |
| `k8s`        | K8s workload/posture scan (Kubescape, NSA+MITRE) — *report-only* |
| `dast`       | Dynamic app scan (OWASP ZAP baseline) — *report-only* |
| `license`    | Scan for license compliance (grant) — *report-only* |
| `vex`        | Generate / emit VEX (OpenVEX) |
| `sbomqs`       | Score SBOM quality + NTIA compliance (sbomqs) — *report-only* |
| `sbom-utility` | Validate the SBOM schema (CycloneDX sbom-utility) — *report-only* |
| `audit`      | `pip-audit` over the repo's Python deps — *report-only* |
| `ci-hardening` | Check CI supply-chain hardening |
| `all`        | Run the whole chain in CI order + apply the fail policy |
| `clean`      | Remove local security artifacts (never source, `.env.local`, or images) |
| `help`       | Show usage |

## Fail policy (mirrors the CI gate)

`all` runs the steps in CI order — `build` (only when no `scan.env` yet) →
`sbom → vuln → osv → malware-heuristic → iac → k8s → dast → license → vex → audit → ci-hardening` —
and ends on the shared pass/fail `summary` gate.

| Class | Steps | A non-zero exit… |
|-------|-------|------------------|
| **STOP** (must-succeed / gate-stop) | `sbom`, `vuln`, `vex`, `ci-hardening` | stops the chain **and** fails the gate |
| **REPORT** (warn-continue / report-only) | `osv`, `malware-heuristic`, `iac`, `k8s`, `dast`, `license`, `audit` | is logged; the chain continues; the gate stays green |

A VEX document with 0 statements is a **successful** run (exit 0), not a failure.
Once a STOP step fires, the remaining steps are skipped ("chain already stopped").

## Signing and attestation

Image signing, SBOM attestation and provenance happen in the release workflow,
never from a laptop, so there are no `sign`/`attest`/`publish` subcommands.

## CI ↔ local job map

| Local command | CI step |
|---------------|---------|
| `devsec.sh sbom`         | `sbom` (release.yml) |
| `devsec.sh vuln`         | `grype` (release.yml) |
| `devsec.sh osv`          | `advisory` leg `osv` (security.yml) |
| `devsec.sh malware-heuristic` | `advisory` leg `guarddog` |
| `devsec.sh iac`        | `advisory` leg `checkov` |
| `devsec.sh k8s`        | `advisory` leg `kubescape` |
| `devsec.sh dast`       | `dast` (dast.yml, OWASP ZAP) |
| `devsec.sh license`      | `grant` (release.yml) |
| `devsec.sh vex`          | `vex` (release.yml) |
| `devsec.sh ci-hardening` | `workflows` job of ci.yml (via `check-ci-hardening.sh`) |
| `devsec.sh audit`        | `pip-audit` (Python SCA; advisory) |
| `devsec.sh build`        | release.yml `build` (from `.github/container-images.yml`) |

`devsec.sh sbomqs` and `devsec.sh sbom-utility` are **local-only** SBOM-analysis
tools (quality scoring + schema validation over the SBOM the chain produced) —
they have **no CI job**, keeping the pipeline surface minimal. They are the
report-only complement to `report.sh`'s per-axis views. See
[`wiki/supply-chain-tooling-assessment.md`](../../../wiki/supply-chain-tooling-assessment.md).

## Testing

The runner is unit-tested with [`bats`](https://github.com/bats-core/bats-core)
and linted with `shellcheck`; the seven CI scripts are untouched by the tests
(each is stubbed via an overridable module var, so a test never runs a real scan
or writes to a real registry):

```bash
shellcheck devsec.sh
bats tests/
```

## Output styling

All of the runner's output is styled by one shared presentation layer,
[`_ui.sh`](./_ui.sh), sourced by `devsec.sh` (and by `report.sh`). It is the
single place the *local* runner decides how output looks — glyphs on every
status, a highlighted command line (the easiest line to find in a long run), and
a shared colour palette — so the look is consistent across every subcommand
instead of each site inventing its own `printf`.

It sits **on top of** the shared runtime and only affects the local process: the
seven CI scripts run as their own subprocesses and keep the frozen styling
byte-for-byte, so the CI log never changes just because the laptop experience is
prettier. Colour degrades to clean text automatically under `NO_COLOR`, when the
output is piped (to a file or CI), or on a "dumb" terminal — and every status
carries a glyph, so a verdict stays legible with the colour stripped.

## Safety note (the malicious demo)

The `malicious` demo under `examples/` pins a known typosquat
(`pandsa`, OSV `MAL-2023-1937`) so the scanners have a real name to match. The
package is **never installed or executed** — `osv-scanner` matches it by name
from the manifest. That is the shift-left guarantee the `osv` /
`guarddog` legs provide: the name is caught before `pip install`
ever runs.
