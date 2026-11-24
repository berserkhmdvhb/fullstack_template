# ADR-0017: Release builds use no build cache

## Status

Accepted.

## Date

2026-11-21

## Context

Any job on `main` can write GitHub Actions cache entries, including test jobs that run third-party packages and any
third-party action. A release build that restores such an entry could bake a poisoned layer into an image that the
pipeline then signs and attests as built from its commit; the signature and provenance would look valid. SLSA Build
L3 requires that one build cannot inject entries into a cache another build uses, and that the output does not depend
on the cache.

## Decision

The whole call graph of `release.yml` (every workflow and local composite action it reaches) restores and writes no
cache: no `cache-from` or `cache-to`, no `actions/cache`, no `setup-*` cache, and `setup-buildx-action` with
`cache-binary: false`. The hardening lint's `no-release-cache` check enforces this and fails closed on any action,
remote reusable workflow, `docker://` step or local JavaScript or Docker action it cannot verify. Test jobs keep
their caches.

## Consequences

- Good: it serves the [Build L3 aim](./0016-slsa-build-l3-for-release-images.md); each release is built from the
  commit, the pinned bases and the locked dependencies only.
- Bad: release builds are slower, every layer cold.
- Bad: a new action on the release path needs review and an entry on the lint's list of cache-free actions.

## Alternatives considered

- Keep the cache and document the risk: rejected, it would put the L3 aim out of reach.
- A registry cache that only the release build can write: rejected, one compromised release build could still
  poison the next.

## Links

- [Security: a release build starts cold](../security.md#a-release-build-starts-cold)
- [`infra/security/ci_hardening_lint.py`](../../infra/security/ci_hardening_lint.py) (`check_no_release_cache`)
- [`.github/workflows/release.yml`](../../.github/workflows/release.yml)
