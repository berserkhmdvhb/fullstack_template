# Supply-chain tooling assessment: a layered (defense-in-depth) approach

> Date: 2026-11-02 · Scope: the `ci/security.yml` + `infra/security/` supply-chain
> surface. **Verdict:** adopt **OSV-Scanner** (consolidated, offline, multi-language
> source-level SCA + known-malicious) **and** **GuardDog** (proactive heuristic
> typosquat). Overlap is intentional where it covers a different *target* or *detection
> model*; to keep it honest, retire the narrow tools each new tool subsumes.
>
> Sources: [Safeguard — OSS vuln-management tools compared](https://safeguard.sh/resources/blog/open-source-vulnerability-management-tools-compared);
> [awesome-security-pipeline — GitHub Actions security tools](https://github.com/rezmoss/awesome-security-pipeline/blob/main/comparisons/github-actions-security-tools.md).

## 0. The framing: overlap is expected; *unconsolidated* overlap is the anti-pattern

Both sources reject "pick one scanner" and endorse **layered, complementary tooling**:

- *"There is no single scanner"* — tools cover different control points and are
  complementary, not interchangeable (awesome-security-pipeline).
- *"For polyglot stacks you'll likely end up running two of these tools together… for
  containers, many teams run both and diff the results"* (Safeguard).
- But: run **"the smallest complementary set"**, and beware the failure mode — *"broad
  scope makes it easy to combine unrelated findings and thresholds into an opaque
  job."* The real cost of overlap is *"correlating findings across four different JSON
  output schemas."* (Safeguard)

**The discipline this assessment applies:** an overlap is *justified* when it covers a
different **target** (image vs lockfile vs source), a different **data source**
(OSV.dev vs Grype DB vs PyPI advisory DB), or a different **detection model**
(known-bad list vs heuristic). An overlap is *wasteful* when two tools do the identical
thing on the identical target from the identical feed — and then the narrower tool
should be **retired**, not stacked. The sections below mark each overlap accordingly.

## 1. The existing stack (what each piece already covers)

| Concern | Tool / job | Target | Data source | Model |
|---|---|---|---|---|
| SBOM inventory | **Syft** (`sbom` job) | image | — | inventory |
| CVE / vuln (SCA) | **Grype** (`grype` job) | **image SBOM** | Grype DB / NVD / GHSA | known-CVE |
| CVE audit — Python | **pip-audit** job (advisory) | installed venv | PyPI advisory DB + OSV | known-CVE |
| CVE audit — JS | **npm audit** job (advisory) | locked graph | npm advisory DB | known-CVE |
| SCA (built-in) | the old platform-built-in **Dependency-Scanning** (now GitHub dependency review) | source | platform advisory DB / NVD | known-CVE |
| License | **grant** (`grant_license_scan`) | image SBOM | license DB | policy |
| Signing / provenance | **cosign** + GitHub attestations *(delivered: `reusable-build.yml`)* | image | Sigstore | verification |
| **Malicious / typosquat** | **OSV-Scanner** (`osv` leg) | manifests | OSV `MAL-*` | **known-bad list** |

Two observations drive everything below:
1. **Known-CVE SCA is covered four ways** (Grype, pip-audit, npm-audit, the built-in dependency scanning),
   split by ecosystem and target, with the fragmented-schema cost the sources warn
   about. That is the main place to *consolidate*.
2. The **malicious / typosquat axis** splits in two: the *known-bad* half (OSV `MAL-*`)
   is covered by **OSV-Scanner**; the *proactive heuristic* half (typosquat-before-any-advisory)
   is covered by **GuardDog**. Both now have a tool — the one capability that had none.

> Verified 2026-11-02: OSV.dev serves the malicious corpus — querying `0requests`
> (PyPI) returns `MAL-2026-15859` "Malicious code in 0requests". (Source:
> `api.osv.dev/v1/query`.) So the feed the matcher relies on is real and live.

## 2. Target architecture — four intentional layers

| Layer | Tool(s) | Role | Target | Why it's not redundant | Gate / advisory |
|---|---|---|---|---|---|
| 0 Inventory | Syft | SBOM | image | produces the component list | — |
| 1 Known-CVE SCA | **Grype** (image) **+ OSV-Scanner (lockfile/source)** | CVE matching | **image** vs **lockfile** | **different target + different data source (OSV.dev)** → complementary (the sources' "run both, diff the results") | Grype: tag-gated; OSV-Scanner: advisory |
| 2 Malicious / typosquat | **OSV-Scanner (known `MAL-*`)** **+ GuardDog (heuristic)** | malicious/typosquat | manifests / direct deps | **different model**: known-bad list (OSV `MAL-*`) vs proactive heuristic | OSV-Scanner: advisory; GuardDog: advisory |
| 3 Provenance | cosign + GitHub attestations *(delivered: `reusable-build.yml`)* | signing / digest-pin | image | integrity | hard |

The two *intentional* overlaps are both justified:
- **Grype ↔ OSV-Scanner** — same concern (CVE), but Grype scans the *built image*
  (runtime truth) while OSV-Scanner scans *lockfiles/source* (pre-build, pull request feedback)
  from OSV.dev. Different target + data source = complementary, per both sources.
- **matcher/OSV-Scanner ↔ GuardDog** — same concern (malicious), but one is a
  *known-bad list* and the other is *proactive heuristic*. Different model = complementary.

## 3. Tool assessments

### 3.1 OSV-Scanner (`google/osv-scanner`, Apache-2.0, Google/OpenSSF) — **ADOPT**

Go CLI; official frontend to OSV.dev. 11+ ecosystems / 19+ lockfile types; reads SBOMs
(CycloneDX/SPDX) and container images; **offline-capable** (download the OSV DB once,
then no network); JSON/SARIF/JUnit output; **single static binary
with release checksums**.

**Why it earns its place (this reverses the prior "not adopted" call):**
- **Consolidation.** It is *one* multi-language lockfile scanner (npm, PyPI, Go,
  crates, Ruby…) — it can **absorb pip-audit + npm-audit (+ optionally the built-in
  dependency scanning)** into a single deterministic pass, directly addressing the
  "four JSON schemas" fragmentation the sources flag. That is a *reduction* in tool
  count, not an addition.
- **Different data source.** OSV.dev aggregates NVD + GHSA + RustSec + the
  malicious-packages corpus — redundancy vs Grype's feed closes blind spots in both.
- **Offline + deterministic.** Matches this repo's stated determinism goal better than
  the live, floating, advisory pip-audit/npm-audit.
- **Fits the install standard.** A checksum-verified static binary slots straight into
  `tools-install.sh` (unlike a pip package).
- **Covers the malicious (`MAL-*`) corpus** — the same known-bad coverage the
  hand-rolled matcher provides, but maintained and multi-language.

**Caveats:** its *headline* mode is still CVE/SCA, so on the CVE axis it overlaps
Grype/pip-audit — justified only because the target/data-source differs (see §2); if
you pointed it at the *image* too, that overlap would become wasteful. **Verify on
adopt** that it surfaces `MAL-*` entries and the exact exit-code behavior before
retiring the hand-rolled matcher (see §6).

### 3.2 GuardDog (`DataDog/guarddog`, Apache-2.0) — **ADOPT** (advisory)

Python tool; static analysis (YARA) + metadata heuristics over package source.
Ecosystems: PyPI, npm, Go, Rust, RubyGems, GitHub Actions, VSCode. `pip`/`uv`/Docker.
CLI: `guarddog pypi scan <name>[==<ver>] [--rules typosquatting] [--output-format=json|sarif]`.

**The genuine, un-filled gap:** a dedicated **`typosquatting`** rule (PyPI/npm/go/
RubyGems) plus code-capability heuristics (env-var exfiltration, obfuscation,
`risky_new_dependency`, …) that flag packages **before they are ever reported** to any
database. No SCA tool here (Grype, OSV-Scanner, pip-audit) does this. **Zero CVE
overlap** — it does no vulnerability matching at all.

**Caveats (scope drivers):** pip package → pin via inline `pip install` in the job
(the `pip_audit` pattern), *not* the `.tools` checksum map; **per-package** and
downloads the package to analyse it → network-dependent, so scope to **direct deps**;
**heuristic → false positives expected** → advisory only.

### 3.3 Trivy (Aqua, Apache-2.0) — **note as the maximal-consolidation alternative**

Already wired in `tools-install.sh` (unused). One CLI: SBOM + vuln (NVD/GHSA/OSV) +
malware (OSV) + secrets + IaC/misconfig + image + filesystem. It is the *one-tool*
consolidation candidate — it could subsume Grype **and** OSV-Scanner **and** add
secrets/IaC. **Trade-off:** it overlaps **Grype most heavily** (both are image-vuln
scanners), so "Trivy-for-everything" means retiring Grype — a bigger architectural
decision than the OSV-Scanner + GuardDog path. Listed here so the choice is explicit;
not the recommended path for this template (see §5).

## 4. Overlap ledger

| Overlap | Verdict | Action |
|---|---|---|
| Grype (image) ↔ OSV-Scanner (lockfile) | ✅ intentional | keep both — different target + data source |
| matcher (`MAL-*`) ↔ OSV-Scanner (`MAL-*`) | ⚠️ justified-but-duplicative | **retire the hand-rolled matcher** once OSV-Scanner's `MAL-*` surfacing is verified on adopt |
| OSV-Scanner (known `MAL-*`) ↔ GuardDog (heuristic) | ✅ intentional | keep both — different detection model (known-bad list vs proactive) |
| OSV-Scanner ↔ pip-audit / npm-audit / built-in dependency scanning (known-CVE) | ⚠️ justified-but-duplicative | **retire pip-audit + npm-audit** (and optionally the built-in scanning) — OSV-Scanner consolidates them — *resolved on adopt: npm-audit retired → `osv`, pip-audit kept (see Adoption outcome)* |
| Trivy ↔ Grype (both image-vuln) | ❌ wasteful *if both kept* | do not add Trivy unless you retire Grype (option C) |

## 5. Adoption options

| Option | Tools | Pros | Cons |
|---|---|---|---|
| **A — recommended** | **OSV-Scanner + GuardDog** (+ retire matcher + npm-audit; pip-audit kept) | Consolidates the 4-way SCA split into one deterministic multi-language pass; adds proactive typosquat; both fit the repo's install/scope conventions; smallest *complementary* set | Two new tools to wire; must verify OSV-Scanner `MAL-*` behavior before retiring the matcher |
| B — minimal | **GuardDog only** | Smallest change; fills the one true gap (proactive typosquat); zero CVE overlap | Leaves the 4-way SCA fragmentation; keeps the hand-rolled matcher as the only malicious coverage |
| C — maximal | **Trivy for everything** (+ retire Grype, pip-audit, npm-audit, matcher) | One CLI, one schema, widest coverage (adds secrets/IaC) | Biggest change; retires Grype; heaviest to tune; most behavior change for a template |

**Recommendation: Option A.** It is the "smallest complementary set" the sources
advise: OSV-Scanner consolidates the fragmented known-CVE SCA *and* carries the
known-malicious coverage; GuardDog adds the proactive layer nothing else covers. The
overlap each introduces is the *justified* kind (different target / model), and the
wasteful overlaps are retired rather than stacked.

> **Adoption outcome (resolved).** Option A was adopted and the §6 verify-on-adopt
> flags were closed. The retire list resolved **asymmetrically**:
> - **npm-audit → retired.** OSV-Scanner reads this repo's `frontend/package-lock.json`,
>   so npm known-CVE SCA is now the `osv` leg (`scan-osv.sh`, single source for
>   CI + `devsec.sh osv`).
> - **pip-audit → kept.** This repo declares Python deps in PEP 621
>   `pyproject.toml` `[project].dependencies`, which OSV-Scanner does **not** parse
>   (it reads *resolved* lockfiles: `requirements.txt`, `poetry.lock`, …). pip-audit
>   therefore stays the Python known-CVE SCA (the `pip-audit` job / `devsec.sh audit`).
> - **hand-rolled `MAL-*` matcher → retired** onto OSV-Scanner's maintained `MAL-*` corpus.
>
> So the delivered "smallest complementary set" is: **Grype** (image) + **pip-audit**
> (Python SCA) + **OSV-Scanner** (npm SCA + known-malicious) + **GuardDog** (typosquat
> heuristic) — each a distinct target / data source, no wasteful overlap.

## 6. Verify-on-adopt flags (resolved)

1. **OSV-Scanner `MAL-*` surfacing + exit code** — confirm it reports malicious
   packages (not just CVEs) and what exit code a `MAL-*` hit produces, before retiring
   the deterministic `malware_match.py`. Its offline/snapshot story must match the
   matcher's deterministic, offline-by-default behavior. **→ resolved:** OSV-Scanner
   reports `MAL-*` findings (advisory) and the hand-rolled `malware_scan`
   matcher was retired onto it — one `MAL-*` source, not two.
2. **OSV-Scanner lockfile coverage** — confirm it reads this repo's `pyproject.toml`
   (PEP 508) and `frontend/package-lock.json` (and/or the Syft SBOM) with parity to
   pip-audit/npm-audit before retiring them. **→ resolved:** it reads
   `frontend/package-lock.json` (npm-audit retired → `osv`) but does **not**
   parse PEP 621 `pyproject.toml` `[project].dependencies` (pip-audit kept).
3. **GuardDog positive-control** — confirm it flags a known typosquat (e.g. `0requests`)
   and stays non-blocking (advisory) on a finding.

## 7. Scope decisions (per adopted tool)

- **OSV-Scanner:** source/lockfile level (pull requests, `main` and tags), offline by default,
  advisory to start; multi-language (PyPI + npm first). Static binary
  via `tools-install.sh`.
- **GuardDog:** direct deps only, PyPI + npm, pull requests, `main` and tags, `--rules
  typosquatting`, SARIF → code scanning, **advisory** (a `::warning`, never a required check).
- **Left as-is by this assessment:** Syft, grant, cosign/SLSA (cosign signing + SLSA provenance
  were later **parked** — removed from the pipeline), and the eight `infra/security/*.sh` scripts.
  Grype stays (image layer). Trivy remains available but unadopted (option C).
