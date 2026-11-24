# ADR-0007: Logs go to stdout, and the chart ships no log collector

## Status

Accepted.

## Date

2026-11-20

## Context

The original chart wrote file logs to an `/app/logs` emptyDir and shipped a log-collector DaemonSet. The DaemonSet
ran as root with a hostPath mount, which breaks the restricted Pod Security profile, and an application chart that
installs a cluster-wide agent collides with the platform's own.

## Decision

The app writes structured JSON logs to stdout only; there are no file logs and no log volume. The chart contains no
DaemonSet or other cluster-wide agent. Log collection is the platform's job, and the docs point at the usual
choices: GKE Cloud Logging, or an OpenTelemetry Collector or Fluent Bit chart installed by the platform.

## Consequences

- Good: every app workload can run under the restricted Pod Security profile.
- Good: the twelve-factor rule: Kubernetes keeps the logs, `kubectl logs` works, and the platform picks the
  collector once for every service.
- Bad: a cluster without a collector keeps only the container runtime's rotated logs.

## Alternatives considered

- Keep the DaemonSet, opt-in: rejected, a privileged agent does not belong in an application chart even when off.
- Keep file logs beside stdout: rejected, two sinks to size and rotate, and the file sink needs a writable volume.

## Links

- [Infrastructure: collecting logs](../infrastructure.md#collecting-logs)
- [`src/your_service_name/backend/logging/json_formatter.py`](../../src/your_service_name/backend/logging/json_formatter.py)
- [`tests/backend/test_chart_stdout_logging.py`](../../tests/backend/test_chart_stdout_logging.py)
