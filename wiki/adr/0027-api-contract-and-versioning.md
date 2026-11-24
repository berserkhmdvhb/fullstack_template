# ADR-0027: The API contract is checked on every pull request, under a versioning policy

## Status

Accepted.

## Date

2026-11-20

## Context

The frontend and any external client depend on the backend's OpenAPI contract. A removed field or a tightened
request breaks them silently, and nothing in a unit test notices.

## Decision

- `/api/v1` is stable: changes within a version are additive. A breaking change needs `/api/v2` beside it, or a
  deprecation with notice: the `Deprecation` (RFC 9745) and `Sunset` (RFC 8594) headers and a changelog entry, and
  removal only after the sunset date.
- The `api-compat` job runs oasdiff on every pull request against the committed snapshot
  `frontend/lib/api/openapi.json` and fails on an error-level breaking change, unless the pull request carries the
  `breaking-change` label, which only triage permission or above can add.
- The label also requires a major version bump: with it, `pr-title.yml` refuses a title without the Conventional
  Commit breaking marker (`type(scope)!: subject`), so the squash commit on `main` carries it and release-please
  proposes the next major version ([ADR-0023](./0023-release-flow.md)). Adding or removing the label re-runs that
  check.
- Database migrations are linted by squawk ([ADR-0006](./0006-migrations-as-a-release-job.md)).

## Consequences

- Good: a breaking change is a deliberate, labelled decision, never an accident, and it always moves the major
  version.
- Bad: the gate depends on the committed snapshot being regenerated (`make openapi-drift-check` fails a stale one).
- Bad: the title check is not a required check ([Releasing](../releasing.md#pull-request-titles)), so an owner who
  merges over a red title lands a breaking change without the marker; the release pull request is the last place to
  catch it.

## Alternatives considered

- Contract tests against a running server only: kept as `make test-contract` (schemathesis), but they test
  behaviour, not compatibility with the previous version.

## Links

- [API lifecycle](../api-lifecycle.md), [`scripts/ci/oasdiff.sh`](../../scripts/ci/oasdiff.sh),
  [`.github/workflows/pr-title.yml`](../../.github/workflows/pr-title.yml)
- [`src/your_service_name/backend/api/deprecation.py`](../../src/your_service_name/backend/api/deprecation.py)
