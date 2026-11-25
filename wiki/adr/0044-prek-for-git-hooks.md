# ADR-0044: prek for the git hooks

## Status

Accepted. `aqua:j178/prek` is in `mise.toml` and `mise.lock`; `make precommit` and the git hook run it.

## Date

2026-11-25

## Context

The hooks in [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml) are all `repo: local` with `language: system`: each calls the tool CI uses, nothing is fetched and nothing needs the network. The runner was pre-commit, a Python program installed into its own virtual environment from hash-pinned requirements (a row in `tools/pins.tsv`). The [tooling assessment](../tooling-assessment.md#7-git-hooks-prek) left the choice to the maintainers.

prek is a single Rust binary that reads the same configuration file name and the same hook definitions. Its [aqua registry recipe](https://github.com/aquaproj/aqua-registry/blob/main/pkgs/j178/prek/registry.yaml) names the release assets, a `.sha256` file beside each, and a GitHub artifact attestation, so mise locks and verifies it like the other tools in [ADR-0043](./0043-mise-for-the-developer-toolchain.md): a sha256 per platform in `mise.lock`, the attestation verified when the lock is written, and the locked sha256 checked at every install.

## Decision

prek runs the git hooks. It is `aqua:j178/prek` in `mise.toml`, linked as `.tools/bin/prek` (`scripts/tools.sh install prek`). `.pre-commit-config.yaml` stays the configuration, unchanged but for a comment. `make precommit` runs `prek run --all-files` with its cache under `.tools/prek` (`PREK_HOME`), and `prek install` installs the git hook. The `pre-commit` row, its requirements file and its refresh entry are gone.

On the same tree, pre-commit 4.7.0 and the pinned prek gave the same pass or fail for each of the eleven hooks, clean and with one deliberately broken file per hook type; the placeholder check was shown in its adopted mode as well.

## Consequences

- Good: one verified binary from the same lock as the other tools, with a GitHub attestation, instead of a Python virtual environment; no Python needed to run a hook.
- Good: no per-hook environments to build (they matter little for local hooks); all eleven hooks took 10.2 s under prek and 12.0 s under pre-commit on this tree.
- Bad: prek is pre-1.0 (still 0.x at the pin, and moving quickly); a minor release may change behaviour, so a bump goes through the same review and the equivalence check (`make precommit` on a clean and a broken tree).
- Bad: one maintainer-led project instead of the long-established pre-commit (first released in 2014); the configuration format is shared, so going back is a change of the runner only.
- Neutral: an adopter who already ran `pre-commit install` runs `prek install` once; it replaces the old hook.

## Alternatives considered

- **Keep pre-commit:** works, and every hook is local, so it gains nothing from its hook environments; but a Python venv and hashed requirements to carry for a tool that runs shell commands.
- **prek, chosen:** the same configuration and hooks, a single binary, locked and attested through mise; pre-1.0.
- **hk:** configured in Pkl; a new configuration file and language to learn, and every hook rewritten.
- **lefthook:** YAML, a single binary; a new configuration format and every hook rewritten, with no gain over prek's compatibility.

## Links

- [ADR-0043: mise for the developer toolchain](./0043-mise-for-the-developer-toolchain.md)
- [Tooling assessment, git hooks](../tooling-assessment.md#7-git-hooks-prek)
- [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml) and [`mise.toml`](../../mise.toml)
