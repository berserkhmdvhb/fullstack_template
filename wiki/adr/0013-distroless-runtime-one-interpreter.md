# ADR-0013: A distroless backend runtime with the builder's own interpreter

## Status

Accepted.

## Date

2026-11-20

## Context

The original backend runtime image carried `curl` and `bash` for its HEALTHCHECK and for debugging. Every binary in
the image is attack surface and a source of CVEs to triage, and Kubernetes probes need neither. A distroless runtime
has no shell or package manager, so the virtual environment has to be built elsewhere and copied in, and a copied
venv only works if its interpreter link points at the same interpreter in both stages.

## Decision

The backend runtime is `gcr.io/distroless/python3-debian13` (Python 3.13), pinned by digest: no shell, no package
manager, no `curl`. The builder stage is Debian 13 (`debian:13-slim`, by digest) using Debian's own `python3.13`,
the same interpreter as the runtime, so the venv's `/usr/bin/python3.13` link is valid in both. The HEALTHCHECK is a
Python probe. The frontend and WAF images follow the same distroless, digest-pinned rule.

## Consequences

- Good: a smaller image with fewer packages to scan and patch, and nothing for an attacker's shell to run.
- Good: the venv never silently breaks on an interpreter mismatch.
- Bad: no shell for `kubectl exec` debugging; use an ephemeral debug container.
- Bad: the base's patch cadence is the distroless publisher's; [ADR-0014](./0014-debian-fixes-over-the-base.md)
  covers the gap.

## Alternatives considered

- `python:3.x-slim` runtime: rejected, it ships a shell and apt.
- A builder from a different Python distribution (the official Python image or a uv-managed Python): rejected, the
  copied venv would point at an interpreter the runtime does not have.

## Links

- [`Dockerfile.backend`](../../Dockerfile.backend), [`src/your_service_name/backend/healthcheck.py`](../../src/your_service_name/backend/healthcheck.py)
- [Containerization](../containerization.md#dockerfilebackend), [Security: minimal base image](../security.md#minimal-base-image)
- [`tests/supply_chain/test_backend_image.py`](../../tests/supply_chain/test_backend_image.py)
