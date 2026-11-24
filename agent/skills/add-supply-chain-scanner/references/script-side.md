# Script side — the single-source scanner

Repo root: `…/your-service-name/`. Security root: `infra/security/`.

A new scanner is **one** bash script under `infra/security/local/`. It is the
single source of truth: the CI job, `devsec.sh <name>`, and the bats test all
call this exact script with the same two positional args
(`$SEC_OUTPUT_DIR` `$ROOT`). CI and a laptop therefore cannot drift.

## Where things live

```
infra/security/
  _helpers.sh            # shared runtime — SOURCED by every script
  tools-install.sh       # checksum-verified binary installer
  config.yaml            # pinned versions / output / policy (single source of truth)
  grype.yaml             # tool config (grant.yaml referenced)
  <the seven shared step scripts: sbom-generate.sh, scan-vulnerabilities.sh,
   scan-licenses.sh, generate-vex.sh,
   sign-images.sh, attest-provenance.sh, check-ci-hardening.sh, …>   # FROZEN
  local/
    devsec.sh            # local runner — dispatches to the seven + the local scanners
    scan-osv.sh          # SINGLE SOURCE: OSV-Scanner (CI `osv` leg + devsec.sh osv)
    scan-malware-heuristic.sh  # SINGLE SOURCE: GuardDog typosquat (CI `guarddog` leg)
    README.md            # subcommand table + fail-policy chain + CI↔local map
    .env.local.example   # per-developer overrides (gitignored)
    tests/
      devsec-osv.bats
      devsec-malware-heuristic.bats
      devsec-chain.bats, devsec-clean.bats, devsec-env.bats, devsec-resolve.bats,
      devsec-registry.bats, devsec-scanenv.bats, devsec-tools.bats, devsec-vex-vuln.bats
```

New scanner logic goes in `local/`. The eight top-level scripts are the frozen
contract — do not complicate them to add a scanner.

## The single-source pattern (traced for OSV-Scanner)

- **CI** (`.github/workflows/security.yml`, job `advisory`, leg `osv`):
  `bash infra/security/local/scan-osv.sh "$SEC_OUTPUT_DIR" "$PWD"`
- **Local** (`devsec.sh` `cmd_osv`, with `OSV_SCAN_SH="${OSV_SCAN_SH:-$_SEC_DIR/local/scan-osv.sh}"`):
  `_invoke_ci bash "$OSV_SCAN_SH" "$SEC_OUTPUT_DIR" "$REPO_ROOT"`
- **bats** (`devsec-osv.bats`): `run bash "$SCRIPT" "$SEC_OUTPUT_DIR" "$root"`
  where `SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/scan-osv.sh"`.

Same script, same two positional args, three call sites. Reproduce this triple
for every new scanner.

## `_helpers.sh` — the shared runtime (exact signatures)

Source it one level up from a `local/` script:

```bash
source "$(dirname "$(dirname "${BASH_SOURCE[0]}")")/_helpers.sh"
trap 'trap_err' ERR
```

(Plain bash functions become available on `source` — no `export -f`.) Globals set
on source: `RED GREEN YELLOW BLUE DIM RESET`, `LIB_TS` (1), `DRY_RUN` ("false"),
`LIB_ASSUME_YES` ("false"), `CONFIG_FILE` (`infra/security/config.yaml`),
`_SEC_WIDTH` (68), arrays `_LIB_PASSED/_LIB_FAILED/_LIB_SKIPPED/_LIB_CLEANUP`.

- `log MSG` (stdout) · `warn MSG` (stderr) · `error MSG` (stderr) ·
  `success MSG` (**stderr** — note this) · `die MSG` → `error; exit 2`.
- `trap_err` — ERR-trap handler; prints
  `ERROR @ <file>:<line>: <cmd> (exit <rc>)` and `exit "$rc"`. This is the
  "scary" failure you must never let an advisory scan hit.
- `need_cmd CMD…` — dies listing ALL missing commands at once.
- `need_docker_daemon` · `require_env_vars NAME…` · `assert_non_root`.
- `lib_cleanup_register PATH` / `_lib_cleanup_run()` — EXIT-trap temp cleanup
  (signal-safe). Always register your `mktemp -d`.
- `mask VAL` · `mask_conn CONN` · `safe_load_env FILE ARRNAME` (parses KEY=VALUE
  into a nameref assoc array; never `source`s the file).
- `run CMD…` — DRY_RUN-aware: under `--dry-run` prints `⇢ argv` and runs nothing;
  otherwise `"$@"`. Every state-mutating external command goes through `run`.
  `run_ro CMD…` — read-only counterpart; always executes.
- `confirm PROMPT` (bypassed by `LIB_ASSUME_YES`) · `confirm_break_glass VAR PROMPT`
  (`--yes` does NOT satisfy; needs `VAR=1` or interactive `YES`).
- `lib_banner TITLE [SUBTITLE]` · `lib_sep LABEL`.
- `pass STEP` · `fail STEP` · `skipped STEP` (advisory — appends to
  `_LIB_SKIPPED`, never gates, return stays 0) · `summary` (prints lists; returns
  non-zero if anything failed — the CI gate).
- `cfg EXPR` — `yq -r EXPR config.yaml`, best-effort, always exits 0 (nothing if
  `yq` absent). Read pins with `cfg '.tools.<name>'` / `cfg '.tools_pip.<name>'`.
- `resolve_sec_output_dir` — sets `SEC_OUTPUT_DIR` from env override →
  `cfg '.output.base'` → `output/security`.

**Color/DRY_RUN:** `NO_COLOR` / non-TTY / `TERM=dumb` → all colors empty (clean
text on CI). `DRY_RUN=true` gates `run`/`confirm`; `run_ro` ignores it.

## `config.yaml` — where the pin goes

The shape (the real pins and policy are in `infra/security/config.yaml`; never copy versions from here):

```yaml
output:
  base: output/security          # .output.base — read by resolve_sec_output_dir()
tools:                            # checksum-verified release binaries (tools-install.sh)
  # renovate: datasource=github-releases depName=<org>/<repo>
  <name>: v<version>             # <-- new checksum binary: .tools.<name>
tools_pip:                        # pip-only (integrity via pinned wheel + PyPI hash)
  # renovate: datasource=pypi depName=<package>
  <name>: <version>              # <-- new pip tool: .tools_pip.<name>
policy: { fail_level: <level>, only_fixed: <bool> }
```

Every pin carries its `# renovate:` line directly above it (a test fails on one without it).

- **Checksum binary** → flat key under `tools:`; add a matching case in
  `tools-install.sh`.
- **pip-only** → flat key under `tools_pip:`; `pip install "<name>==<ver>"` inline.
- Scripts read it via `cfg`; CI reads the same flat key via
  `yq -r '.tools.<name>' config.yaml`. One version, read in both places.

## `tools-install.sh` — checksum-verified install (never curl|bash)

Usage: `tools-install.sh <tool> <version> [dest]`
- `tool` ∈ `syft | grype | grant | cosign | trivy | osv-scanner`
- `version` = release tag (leading `v` accepted); `dest` default `/usr/local/bin`.

It downloads the asset + `checksums.txt` to files (never pipes into a shell),
verifies `grep "  <asset>$" checksums.txt | sha256sum -c -`, extracts, and
`install -m 755` into `$dest`. Every external command is wrapped in `run`.

How a scanner script calls it:

```bash
osv_version="$(cfg '.tools.osv-scanner')"
[[ -n "$osv_version" ]] || die "no pinned OSV-Scanner version (add .tools.osv-scanner to ${CONFIG_FILE##*/})"
if ! command -v "$OSV_BIN" >/dev/null 2>&1; then
  warn "osv-scanner missing — installing ${osv_version} via tools-install.sh"
  run bash "$TOOLS_INSTALLER" osv-scanner "$osv_version" /usr/local/bin \
    || warn "osv-scanner install failed — the scan degrades to SKIPPED if the tool is still absent"
fi
# still absent (and not --dry-run)? degrade honestly:
if [[ "$DRY_RUN" != "true" ]] && ! command -v "$OSV_BIN" >/dev/null 2>&1; then
  _skipped_scan "$OUT_DIR" "OSV-Scanner" "osv-scanner ${osv_version} unavailable (not present and install failed)"
  exit 0
fi
```

`TOOLS_INSTALLER="${TOOLS_INSTALLER:-${SEC_DIR}/tools-install.sh}"` is the test
seam. Install is **best-effort** (caught so it can't trip `set -e`/ERR); a
still-absent tool degrades to `_skipped_scan` + `exit 0`.

**pip-only tools compile C extensions** (GuardDog's pygit2/yara-python need a C
compiler + `Python.h`, linking libyara/libgit2). Probe the toolchain up front and
short-circuit to an *actionable* SKIPPED (naming the apt fix) **without invoking
pip** — so a box that can't build it gets one clean line, not a 70-line compile
dump. Guard it behind `if ! command -v "$GUARDDOG_BIN"` and `[[ $DRY_RUN != true ]]`
so a present tool / dry-run is unaffected:

```bash
_gd_toolchain_ok() {                       # both probes overridable for tests
  command -v "${GD_CC:-cc}" >/dev/null 2>&1 || return 1
  local inc="${GD_PYTHON_INCLUDE:-}"
  if [ -z "$inc" ]; then
    inc="$(python3 -c 'import sysconfig;print(sysconfig.get_path("include"),end="")' 2>/dev/null)" || return 1
  fi
  [ -n "$inc" ] && [ -f "$inc/Python.h" ]
}
```

## bats conventions (`local/tests/`)

`#!/usr/bin/env bats`. Each `@test` runs in its own shell (no state bleed).

- **Stub on PATH + `BASE_PATH` preservation** (so only the tool is shadowed, while
  `yq`/`python3`/`jq` stay real):
  ```bash
  setup() {
    SCRIPT="$(dirname "$BATS_TEST_DIRNAME")/scan-<name>.sh"
    BASE_PATH="$PATH"
  }
  # in the stub helper:
  export PATH="$bindir:$BASE_PATH"
  ```
- **Test seams** (env vars the script reads, exported by the stub helper):
  `*_BIN` (the binary: `OSV_BIN`, `GUARDDOG_BIN`), `*_RULE` (`GUARDDOG_RULE`),
  `*_EXIT` (`OSV_EXIT`; GuardDog always exits 0 so its stub keys off the package
  name), `TOOLS_INSTALLER` (stub recorder / failing installer),
  `GD_CC`/`GD_PYTHON_INCLUDE` (toolchain overrides), `SEC_OUTPUT_DIR`, and the
  `devsec.sh` module vars (`<NAME>_SCAN_SH`, …).
- **The stub writes the requested output** so the script's report logic runs for
  real: parse `-f/--output-file` from argv, write a minimal SARIF/JSON to
  `--output-file`, then `exit "${*_EXIT:-0}"`.
- **Assertions:** `.status`/`.scanned`/`.runs[].results` via `yq`/`jq` on the
  written files; argv flags via `grep -qF '…' "$ARGV"`; "no hard-die" via
  `[[ "$output" != *"ERROR @"* ]]`.

### Cases every scanner test must cover
1. **Clean** — reports written, correct `.status` (GREEN), argv flags present.
2. **Finding** — reports still written, exit per contract (0 for advisory).
3. **Install-fail / no toolchain** — honest `SKIPPED` (`.status`=SKIPPED, `exit 0`,
   `[[ "$output" != *"ERROR @"* ]]`), never a false clean.

## Anatomy of a scanner script — `infra/security/local/scan-<name>.sh`

1. Shebang `#!/usr/bin/env bash` + docstring: tool, "SINGLE SOURCE OF TRUTH",
   advisory-vs-gate contract, usage line.
2. `set -Eeuo pipefail`.
3. Source `_helpers.sh` (one level up) + `trap 'trap_err' ERR`.
4. `usage()` + `case "${1:-}" in -h|--help) usage ;; esac`.
5. Defaults: `SCRIPT_DIR`, `SEC_DIR` (`infra/security`),
   `SEC_OUTPUT_DIR="${1:-${SEC_OUTPUT_DIR:-}}"; resolve_sec_output_dir`,
   `OUT_DIR="${SEC_OUTPUT_DIR%/}/<name>"`, `ROOT="${2:-…}"`, the `*_BIN` seam,
   `TOOLS_INSTALLER="${TOOLS_INSTALLER:-${SEC_DIR}/tools-install.sh}"`, `lib_banner`.
6. Pin + install-if-missing: `ver="$(cfg '.tools.<name>'|.tools_pip.<name>')"`;
   `die` if empty; install (checksum via `run bash "$TOOLS_INSTALLER" …` OR pip
   with toolchain probe); if still absent (and not `--dry-run`) →
   `_skipped_scan "$OUT_DIR" "<Tool>" "…"; exit 0`; then `need_cmd <deps>`.
7. Enumerate inputs (manifests/lockfiles) — a missing manifest skips that
   ecosystem cleanly, never fatal.
8. Run the tool, capturing non-zero exit into a verdict var via
   `if run "$BIN" …; then rc=0; else rc=$?; fi` — so `set -e`/ERR can't abort
   before the reports exist (a finding exits non-zero → it's the verdict).
9. Write SARIF `<name>.sarif` (`version:"2.1.0"`, empty-doc
   fallback) for the code scanning upload.
10. Write `report.json` with a `.status` (GREEN/RED/SKIPPED) — what
    `_report_status`/bats assert.
11. Verdict: `warn`/`success` on the finding signal; confirm reports written.
12. Exit: advisory → `exit 0`; gate → `exit <rc>` (or let `summary`/caller gate).

## Anatomy of its bats test — `infra/security/local/tests/devsec-<name>.bats`

1. `#!/usr/bin/env bats` + docstring naming the single-source script + its callers.
2. `setup()`: `SCRIPT=…`; `BASE_PATH="$PATH"`.
3. A `<tool>_stub` helper: create `"$BATS_TEST_TMPDIR/bin/<tool>"`, make it
   (a) append argv to `$<TOOL>_ARGV`, (b) write the requested format to
   `--output-file`, (c) `exit "${*_EXIT:-0}"` (or key off input); `chmod +x`,
   `export <TOOL>_BIN=…`, `export PATH="$bindir:$BASE_PATH"`.
4. A `_stub_install_fail` helper: point `<TOOL>_BIN` at a nonexistent path + a
   failing `TOOLS_INSTALLER`/`pip` stub → asserts degrade to SKIPPED.
5. A fixture helper writing a lockfile/`pyproject.toml` the scanner can read;
   `mkdir -p "$BATS_TEST_TMPDIR/out"`.
6. Cases: clean / finding / install-fail→SKIPPED (see above).
7. Assert via `yq`/`jq`; assert no-hard-die via `[[ "$output" != *"ERROR @"* ]]`.
8. Wire the CI job, the `cmd_*` in `devsec.sh`, and the module-var seam so all
   three call the same script.

Source-safe pure functions (e.g. `devsec.sh` resolvers) are tested by
`source "$SCRIPT"` + calling the function in `bash -c`; state-mutating commands
(the scanners, `cmd_tools`) run via `run bash "$SCRIPT" …` in a subprocess with a
controlled PATH + stub recorder.
