# ADR-0004: One Uvicorn process per Pod, no Gunicorn

## Status

Accepted.

## Date

2026-11-20

## Context

The original backend image started Gunicorn with Uvicorn workers, though Gunicorn was not a declared dependency.
Kubernetes already supervises the process: it restarts a crashed container, scales by Pods and sends SIGTERM on a
rollout. A second supervisor inside the container hides crashes from Kubernetes, splits one memory limit across
workers, and complicates graceful shutdown.

## Decision

Every launch path runs one Uvicorn process with no `--workers`, through one launcher,
`python -m your_service_name.backend.serve`: the image, the local scripts and CI. The launcher fixes the options:
no `Server` header, the access log off (the app writes its own JSON request logs), a keep-alive longer than a
proxy's idle timeout (`BACKEND_KEEPALIVE_TIMEOUT`, 75 s), a graceful shutdown timeout, and Uvicorn's own
proxy-header handling off ([ADR-0005](./0005-client-ip-from-trusted-proxies.md)). Hot reload is the launcher's own
`BACKEND_RELOAD` option, refused in production. Scale is horizontal, by replicas and the HPA.

## Consequences

- Good: a crash is a container restart Kubernetes sees and counts; the memory limit belongs to one process.
- Good: one shutdown path (SIGTERM, drain, exit) that the shutdown tests on kind exercise.
- Good: the identical options everywhere, and a test fails if any path runs bare `uvicorn` or Gunicorn.
- Bad: one Pod uses one core for Python code; more throughput means more Pods, each with its own memory overhead.

## Alternatives considered

- Gunicorn with Uvicorn workers: rejected as a second supervisor (above).
- `uvicorn --workers N`: the same problem, with Uvicorn as the supervisor.

## Links

- [`src/your_service_name/backend/serve.py`](../../src/your_service_name/backend/serve.py)
- [`tests/test_no_gunicorn.py`](../../tests/test_no_gunicorn.py)
- [Infrastructure: backend entry](../infrastructure.md#backend-entry),
  [Workloads: rollout, resources and shutdown](../infrastructure.md#workloads-rollout-resources-and-shutdown)
