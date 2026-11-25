# ADR-0015: uv manages the Python project, its lock and its environments

## Status

Accepted.

## Date

2026-11-20

## Context

The backend needs one reproducible dependency set across laptops, CI and the image, with hashes, so a compromised
index or a re-published version cannot change what is installed. Mixing pip, a requirements compiler and a virtualenv
tool spreads that across three tools and several files.

## Decision

uv, at the version `mise.toml` pins (checksum-verified in `mise.lock`), is the one Python tool. `pyproject.toml` declares the
dependencies and the dev group; `uv.lock` pins every package with hashes; CI and the image install exactly the lock
(`uv sync --locked`, `uv sync --frozen`), and a stale lock fails (`make lock-check`, in CI and the git hook).
`[tool.uv] exclude-newer = "7 days"` keeps any release younger than seven days out of a resolve, the same rule
Renovate follows ([ADR-0025](./0025-renovate-for-updates.md)). A hashed `requirements.txt` is exported on demand for
tools that need one, and not tracked.

## Consequences

- Good: one lock, hash-checked everywhere; fast installs; the image and CI cannot drift from the laptop.
- Good: the seven-day rule gives the ecosystem time to catch a malicious release before it reaches the lock.
- Bad: contributors need uv (the pinned toolchain installs it); a fix released today waits seven days unless the
  lock is changed by hand on purpose.

## Alternatives considered

- pip with pip-tools: rejected, two tools and no project or environment management.
- Poetry: rejected, uv covers the same ground (project, lock, environments) in one faster tool.

## Links

- [`pyproject.toml`](../../pyproject.toml), [`uv.lock`](../../uv.lock), [`mise.toml`](../../mise.toml)
- [Getting started: lock file and the 7-day rule](../getting-started.md#lock-file-and-the-7-day-rule)
- [`.github/actions/setup-backend/action.yml`](../../.github/actions/setup-backend/action.yml)
