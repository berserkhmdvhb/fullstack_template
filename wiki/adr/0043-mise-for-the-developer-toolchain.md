# ADR-0043: mise for the developer toolchain

## Status

Accepted. The bootstrap, the settings and the mapping below are in place; the tools move from `tools/pins.tsv` to `mise.toml` and `mise.lock` one step at a time.

## Date

2026-11-25

## Context

`scripts/tools.sh` installs about forty pinned tools into `.tools/bin` from `tools/pins.tsv`, each download checked against a sha256. It works, and it has two costs. A Renovate bump of a row turns the `pins` check red until someone refreshes the checksum by hand (`make pins-refresh`). And macOS needs every row copied by hand, with its own asset and checksum.

[mise](https://mise.jdx.dev) reads a `mise.toml` (the tools and their versions) and a committed `mise.lock` (the exact version, URL and checksum of every tool, for every platform), and installs with `--locked` from the lock alone. Its `aqua` backend also verifies a cosign signature, a SLSA provenance or a GitHub artifact attestation where the vendor publishes one.

What mise does not do is resolve application dependencies. The backend's packages are locked by `uv.lock`, the frontend's by `package-lock.json`, and the images build from those (`uv sync --frozen`, `npm ci`); pip-audit, npm audit, the SBOMs and Renovate read them. Pods never run mise either: the cluster's configuration is Helm values and the GitHub environments.

## Decision

mise owns the developer toolchain: the command-line tools, the Node runtime and uv, and the shell's PATH for local work. uv and npm keep the application's packages. Helm values and the GitHub environments keep the cluster and deploy configuration. `make` stays the front door and `pre-commit` the git-hook runner.

**The bootstrap.** The first row of `tools/pins.tsv` is mise itself. `scripts/tools.sh` installs it first, through the verified path every other row uses: the download is checked against the row's sha256 before it is extracted, the archive goes through `scripts/safe_extract.py`, and a changed byte installs nothing. The sha256 is the one in the release's `SHASUMS256.txt` (the GitHub release-asset digest of the same file agrees). The release also ships `SHASUMS256.txt.minisig` (minisign) and `SHASUMS256.asc` (OpenPGP). Neither is checked: both need a minisign or gpg binary, and the toolchain has neither until it has installed something. Adding a verifier and a committed key is a separate change; the sha256 in the row is reviewed in the diff and `make pins-refresh --check` fetches the vendor's file again. The row also covers macOS (`darwin-arm64`); the other rows stay linux-only until their tools move.

**Repo-local and hermetic.** `.tools/bin/mise` runs only through `scripts/tools.sh`, which sets, whatever the caller exported:

- the data, cache, state, config, temporary and system directories under `.tools/mise/` (`MISE_DATA_DIR`, `MISE_CACHE_DIR`, `MISE_STATE_DIR`, `MISE_CONFIG_DIR`, `MISE_TMP_DIR`, `MISE_SYSTEM_DATA_DIR`, `MISE_SYSTEM_CONFIG_DIR`);
- the global and the system config as single files that are never created (`MISE_GLOBAL_CONFIG_FILE`, `MISE_SYSTEM_CONFIG_FILE`; both are environment-only settings);
- the search for config files stopped above the repository (`MISE_CEILING_PATHS` is the repository's parent, so a `mise.toml` in the home directory is out of reach) and limited to `mise.toml` (`MISE_OVERRIDE_CONFIG_FILENAMES`);
- the repository's own config trusted (`MISE_TRUSTED_CONFIG_PATHS`), which a project file cannot do for itself;
- no GitHub token: `GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_API_TOKEN` and mise's own token variables are unset, and the `gh` login is not read (`MISE_GITHUB_GH_CLI_TOKENS=false`).

`scripts/tools.sh mise <args>` runs the pinned mise from the repository root with this environment. A test puts a hostile global `config.toml`, a `mise.toml` and a `miserc.toml` in a fake home directory and shows that none is loaded and that nothing is written outside the repository.

**The settings.** `mise.toml` holds settings only, no tools yet, no tasks and no hooks. Each name was checked against the settings reference of the pinned release and against `mise settings ls`:

- `lockfile = true` and `locked = true`: mise.lock is read and kept, and installs are locked to it (a locked install makes no GitHub API call and refuses any download whose checksum differs from the lock; both were tried against a rate-limited, token-less address and against a changed lock byte). `mise lock` still writes new entries in this mode, with a warning per tool that has no entry yet.
- `lockfile_platforms = ["linux-x64", "macos-arm64"]`: CI and the devcontainer, and macOS on Apple silicon.
- `use_versions_host = false` and `use_versions_host_track = false`: release data comes from the forge and the vendor, not from a mirror run by mise's author, and no usage report is sent. With the mirror on, a lock entry records the mirror's copy of a GitHub digest and marks it (`checksum_source = "mise-versions"`); with it off, the same value comes from GitHub's own API and carries no mark. The values were identical in the mapping run.
- `github_attestations`, `slsa` and `aqua.cosign`, `aqua.minisign`, `aqua.slsa`, `aqua.github_attestations` stay on, so a recipe that declares a signature or provenance has it verified.
- `disable_backends` turns off every backend but `aqua`, `github` and `core`: the `asdf` and `vfox` plugins run arbitrary scripts, and `pipx`/`pypi`, `npm`, `cargo`, `go`, `gem`, `ubi`, `http` and the rest are not needed. A test keeps this list equal to "every backend mise knows, less the three".

## The mapping

Every row of `tools/pins.tsv`, mapped by running the pinned mise in a scratch directory: a `mise.toml` listing each candidate at its pinned version, then `mise lock` for `linux-x64` and `macos-arm64`, then reading what each lock entry records. "Checksum" is the sha256 of the downloaded artifact that `mise install --locked` compares before it extracts. A checksum from GitHub's release-asset digest was compared with the vendor's own checksum file named in `scripts/pins_refresh.py`, for both platforms: all forty entries that have a vendor file were equal; the four entries without one (shellcheck and squawk, as today) are the digest alone. A tool whose recipe declares no signature has the checksum only, which is what its row has today. Linux and macOS record the same kind of check for every tool.

Where a column says "alias", the tool name is a `[tool_alias]` of the same backend, so one backend serves two names with two versions and each has its own primary version for Renovate.

| Row | mise tool | Verification the lock records (both platforms) |
|---|---|---|
| `mise` | stays | The bootstrap: it installs the rest; sha256 as above. |
| `uv` | `aqua:astral-sh/uv` | Checksum, GitHub attestation. |
| `helm3`, `helm4` | alias of `aqua:helm/helm` | Checksum from the vendor's `.sha256sum` beside each archive. |
| `kubectl`, `kubectl-floor` | `aqua:kubernetes/kubernetes/kubectl`, alias for the floor | Checksum from the vendor's `.sha256` beside each binary (dl.k8s.io). |
| `kind` | `aqua:kubernetes-sigs/kind` | Checksum (the release's digest, equal to its `.sha256sum`). |
| `kubeconform` | `aqua:yannh/kubeconform` | Checksum (digest, equal to `CHECKSUMS`). |
| `promtool` | `github:prometheus/prometheus` (the aqua registry has no recipe for it) | Checksum (digest, equal to `sha256sums.txt`), GitHub attestation. The whole archive installs; only `promtool` is linked. |
| `ct` | `aqua:helm/chart-testing` | Checksum (digest, equal to `checksums.txt`). |
| `helm-unittest` | `github:helm-unittest/helm-unittest` | Checksum (digest, equal to `helm-unittest-checksum.sha`). A Helm plugin directory (`plugin.yaml`, `untt`), linked into the plugin directory like today. |
| `actionlint` | `aqua:rhysd/actionlint` | Checksum, GitHub attestation. |
| `hadolint` | `aqua:hadolint/hadolint` | Checksum, GitHub attestation. |
| `gitleaks` | `aqua:gitleaks/gitleaks` | Checksum (digest, equal to the release checksums file). |
| `squawk` | `github:sbdchd/squawk` | Checksum: the GitHub release-asset digest, which is also the only source today (no vendor file). |
| `oasdiff` | `github:oasdiff/oasdiff` | Checksum (digest, equal to `checksums.txt`). The macOS asset is the universal `darwin_all` archive. |
| `cosign` | `aqua:sigstore/cosign` | Checksum, cosign signature. |
| `kyverno` | `aqua:kyverno/kyverno` | Checksum, cosign signature. |
| `yq` | `aqua:mikefarah/yq` | Checksum, cosign signature. The asset is the bare binary, not the archive the row pins today; its sha256 equals the vendor's `checksums`. |
| `bats` | `aqua:bats-core/bats-core` | Checksum: the same GitHub tag archive as today, hashed when locked; no vendor checksum exists, as today. |
| `shellcheck` | `aqua:koalaman/shellcheck` | Checksum (digest); no vendor file, as today. The asset is the `.tar.xz`. |
| `lychee` | `aqua:lycheeverse/lychee` | Checksum (digest, equal to the `.sha256` beside the asset). |
| `node` | `core:node` | Checksum from nodejs.org's `SHASUMS256.txt` (equal to the vendor's file); the asset is the `.tar.gz`. |
| `python` | stays | See the Python runtime, below. |
| `zizmor`, `schemathesis`, `pre-commit`, `yamale`, `yamllint`, `pip-audit` | stay | Python tools in per-tool virtual environments from hash-pinned requirements; mise's `pipx` backend does not hash-pin transitive dependencies. |
| `k8s-openapi`, `kyverno-chart` | stay | Data, not tools. |

That is 32 rows: 22 move (17 `aqua`, 4 `github`, 1 `core`), 10 stay. Of the 22, four carry a GitHub attestation, three a cosign signature and fifteen a checksum only. No tool is verified less than today, so no tool stays for that reason.

**Extra effects for the next steps.**

- The lock records the newest release at the time it is written. The 7-day rule is applied when a version is written to `mise.toml`, and `mise lock` locks that version only.
- A locked install can make no GitHub API call, but `mise lock` can: about fifty calls for the whole set against sixty an hour for an unauthenticated address. Lock all platforms in one run, and wait for the limit to reset if it is hit; never borrow a token.
- `helm3` and `helm4` (and the two kubectls) each expose one executable name (`helm`, `kubectl`); `.tools/bin/helm3`, `helm4` and `kubectl-floor` stay links made by `scripts/tools.sh`.

## The Python runtime

The runtime stays `uv python install`, as the `python` row says today. mise's `core:python` does lock it (python-build-standalone, a checksum and a GitHub attestation per platform), and uv can use any interpreter by path, but moving it would give two download paths for the one interpreter: `uv sync` and `uv venv` in the Makefile, the images' build and the adopter's machine all fetch their own managed Python, and `scripts/tools.sh` finds its interpreter with `uv python find --managed-python`. The checksum uv checks for its own download is built into the pinned uv. A lock entry would also pin a patch release chosen when the lock is written, where the row pins the minor.

## How mise extracts archives

Researched from the source at the pinned release (`crates/mise-util/src/file.rs` and the `jdx-tar` and `zip` crates locked in its `Cargo.lock`). The aqua and github backends and `core:node` call the same `untar` and `unzip`; a bare binary is copied and made executable.

| Archive content | tar (`.tar.gz`, `.xz`, `.zst`, `.bz2`: `jdx-tar`) | zip (`zip` crate `extract`) |
|---|---|---|
| a `..` component | refused; the whole extraction fails | refused (`Invalid file path`); the extraction fails |
| an absolute name | refused | its root is dropped (the name is made relative) or the entry is refused |
| a link that points out of the tree | **created**: a symlink's target is written as is (absolute or `..`), only a later entry that writes *through* a symlinked directory is refused; a hard link needs an existing regular file in the tree | symlinks are created only if the target resolves inside the destination |
| a device or a fifo | skipped | not created |
| a setuid or setgid bit | **kept**: the mode is restored as `mode & 0o7777` (`preserve_permissions` is on by default and mise does not turn it off) | **kept**: the unix mode is applied as given |

So `..` and absolute names are covered by mise, and an out-of-tree symlink and a setuid bit are not (a zip is covered for the first). The check `scripts/safe_extract.py` makes today (Python's `data` filter: no escaping names, no links out, no devices, no setuid) therefore has to run again over the installed tree after `mise install`: walk it, refuse any symlink or hard link that resolves outside the install directory, any special file and any setuid or setgid bit, remove that install and exit non-zero. No zip is in the mapping (every asset is a tar archive or a bare binary).

## Consequences

- Good: a Renovate bump changes `mise.toml` and `mise.lock` together, and the checksum comes from `mise lock`, not by hand; macOS is a second platform in the same lock, not a second copy of every row.
- Good: more tools than today are verified by more than a checksum: GitHub attestations and cosign signatures where a vendor publishes them.
- Good: a locked install needs no GitHub API call, so no token and no rate limit in CI.
- Bad: a second place describes the toolchain while the move is under way: `tools/pins.tsv` for the rows that have not moved and the ones that stay, `mise.toml` and `mise.lock` for the rest.
- Bad: a 61 MB archive (about 180 MB unpacked) joins the download, and a mise settings or backend change is a new thing to review in each mise release.
- Bad: the extraction guard is ours, not mise's, so it is one more check to keep; the next change adds it with a test that plants each bad archive.
- Bad: the minisign and OpenPGP signatures over the mise release's checksum file are not checked yet.

## Alternatives considered

- **A, chosen:** mise owns the developer toolchain (the tools, Node, uv, the shell's PATH); uv and npm keep the application's packages; Helm values and GitHub environments keep the cluster and deploy configuration.
- **B, mise for everything (packages, configuration, tasks, hooks):** rejected; it would drop the hash-pinned locks that the scanners, the SBOMs and the image builds read, and add a second configuration source that Kubernetes never reads.
- **C, keep `tools.sh` as it is:** rejected; the red `pins` check on every tool bump and no macOS.

## Links

- [`tools/pins.tsv`](../../tools/pins.tsv) and [`scripts/tools.sh`](../../scripts/tools.sh): the pins and the installer
- [`mise.toml`](../../mise.toml): the settings
- [ADR-0015: uv for Python](./0015-uv-for-python.md)
