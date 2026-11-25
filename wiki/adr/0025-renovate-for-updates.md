# ADR-0025: Renovate proposes every version update, after a seven-day wait

## Status

Accepted.

## Date

2026-11-20

## Context

The template pins everything: actions by SHA, images by digest, tools by version and checksum, Python and npm
packages in their locks. Pins that nobody moves rot into known CVEs. Dependabot cannot update a version that lives in
a TSV file, a Dockerfile `ARG` or a workflow `env`, and a freshly published release is the most likely to be
malicious or broken.

## Decision

Renovate owns every version update ([`renovate.json5`](../../renovate.json5)), with regex managers for the
checksum-pinned tools, the image digests in values files and Dockerfiles, and the tool versions in workflows. It
proposes a release only once it is seven days old (`minimumReleaseAge: "7 days"`). Dependabot keeps its security
alerts and security-update pull requests; its version updates are off (`open-pull-requests-limit: 0`).
Its mise manager moves the tool versions in `mise.toml` and runs `mise lock` for `mise.lock`; `make pins-refresh`
recomputes the other checksums and locks after a bump, and a CI drift check fails when a pinned version has no
matching checksum, or `mise.lock` lags `mise.toml`. Renovate and Dependabot titles are `fix(deps)`, so they pass the title check. The Renovate
GitHub App is installed by the owner; until then the file is inert.

## Consequences

- Good: one bot, one config, every pin reachable; a version and its checksum move in one pull request.
- Good: the wait gives registries and the community time to pull a malicious release.
- Bad: a non-security fix waits seven days; a security fix arrives through Dependabot or by hand.
- Bad: regex managers must be kept in step with the files they parse; tests pin them.

## Alternatives considered

- Dependabot for versions too: rejected, it cannot see the regex-pinned versions.
- No bot, manual bumps: rejected, pins rot.

## Links

- [`renovate.json5`](../../renovate.json5), [`.github/dependabot.yml`](../../.github/dependabot.yml),
  [`.github/workflows/pins-drift.yml`](../../.github/workflows/pins-drift.yml)
- [Security: dependency updates and pinned checksums](../security.md#dependency-updates-and-pinned-checksums)
- [`tests/supply_chain/test_renovate_config.py`](../../tests/supply_chain/test_renovate_config.py)
