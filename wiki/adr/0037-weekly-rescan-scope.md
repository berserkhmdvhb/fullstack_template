# ADR-0037: The weekly re-scan covers what runs and the latest release

## Status

Accepted.

## Date

2026-11-20

## Context

An image clean at release time collects CVEs as advisories are published. Re-scanning every release ever made costs
time and opens issues for versions nobody runs.

## Decision

[`rescan.yml`](../../.github/workflows/rescan.yml) runs weekly and on demand. It scans by digest the images each
environment last deployed (from GitHub's deployment records) and those of the latest published `v*` release, with
the build gate's Grype version and ignore list, and opens or updates an issue per new Critical or High finding.

## Consequences

- Good: the findings are about what is running or about to be adopted.
- Bad: an older release still run by someone outside these environments is not re-scanned.

## Alternatives considered

- The last three releases: cut, more noise for versions this repository does not run.

## Links

- [Vulnerability management: the weekly re-scan](../vulnerability-management.md#the-weekly-re-scan)
- [`scripts/rescan_collect.py`](../../scripts/rescan_collect.py), [`scripts/rescan_report.py`](../../scripts/rescan_report.py)
