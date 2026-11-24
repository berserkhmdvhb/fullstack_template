# API lifecycle

How the HTTP API changes without surprising its clients. The checks that enforce it run in CI; how to run them
locally is in [Testing](./testing.md#postgresql-contract-tests-and-the-two-gates).

## The policy

- **`/api/v1` is stable.** Within a version, changes are additive: a new endpoint, a new optional request field, a new
  response field. Tightening a request (a new maximum, a pattern, a new required field), removing or renaming
  anything, or changing a type is a breaking change.
- **A breaking change needs a new version or a deprecation.** Either add `/api/v2` beside `/api/v1` (see
  [`build_api_router`](../src/your_service_name/backend/api/__init__.py)), or keep the old behaviour, mark it
  deprecated, give notice, and remove it only after the sunset date.
- **Notice.** Deprecate first, set a sunset date at least one release cycle later, and say so in the
  [changelog](./changelog.md). A deprecated route keeps working and keeps its behaviour; only headers change.
- **The gate.** The `api-compat` job runs [oasdiff](https://github.com/oasdiff/oasdiff) on every pull request,
  comparing the committed snapshot `frontend/lib/api/openapi.json` with the base's (the first parent of the merge
  commit the job checks out, which GitHub rebuilds on the current base branch), and fails on any
  error-level breaking change (`oasdiff breaking BASE REVISION --fail-on ERR`, see the
  [breaking-changes catalogue](https://github.com/oasdiff/oasdiff/blob/main/docs/BREAKING-CHANGES.md)).
- **The label.** A deliberate breaking change passes the gate when the pull request carries the `breaking-change`
  label (`scripts/github-settings.sh` creates it). Only users with triage permission or above can
  add or remove a label, so the author of a fork's pull request cannot wave their own change through. Adding or
  removing the label re-runs the whole workflow (every job), so the gate sees it.
- **The major version.** The label also requires the Conventional Commit breaking marker in the pull request title
  (`type(scope)!: subject`): [`pr-title.yml`](../.github/workflows/pr-title.yml) refuses a labelled pull request
  without it, and re-runs when the label is added or removed. The squash commit on `main` then carries the `!`, and
  release-please proposes the next major version ([Pull request titles](./releasing.md#pull-request-titles)).

## Deprecating an endpoint

Mark the route `deprecated=True` in its FastAPI decorator, so the OpenAPI document says so (add an `x-sunset`
date in RFC 3339 form with `openapi_extra={"x-sunset": "2026-07-01"}` if you set one). oasdiff reads only the OpenAPI
document, not the response headers: a deprecated endpoint can be removed without a breaking-change error (after its
`x-sunset` date, when there is one), while removing an endpoint that was never marked deprecated needs the label
([oasdiff on deprecation](https://github.com/oasdiff/oasdiff/blob/main/docs/DEPRECATION.md)). Then tell clients
with the headers:

[`api/deprecation.py`](../src/your_service_name/backend/api/deprecation.py) builds the headers; `mark_deprecated`
sets them on the FastAPI `Response`:

```python
mark_deprecated(
    response,
    deprecated_at=datetime(2026, 1, 1, tzinfo=UTC),
    sunset=datetime(2026, 7, 1, tzinfo=UTC),
    deprecation_link="https://docs.example.com/api/deprecations/old-endpoint",
)
```

| Header | Format | Standard |
|---|---|---|
| `Deprecation` | a structured-field Date: `@` and the Unix time in seconds, for example `Deprecation: @1688169599` | [RFC 9745, section 2](https://www.rfc-editor.org/rfc/rfc9745#section-2) (the Date type of [RFC 9651](https://www.rfc-editor.org/rfc/rfc9651#section-3.3.7)) |
| `Sunset` | an HTTP-date in GMT, for example `Sunset: Sun, 30 Jun 2024 23:59:59 GMT` | [RFC 8594, section 3](https://www.rfc-editor.org/rfc/rfc8594#section-3) |
| `Link` | `<url>; rel="deprecation"` and/or `<url>; rel="sunset"` | [RFC 9745, section 3](https://www.rfc-editor.org/rfc/rfc9745#section-3), [RFC 8594, section 6](https://www.rfc-editor.org/rfc/rfc8594#section-6) |

The sunset must not be earlier than the deprecation date (RFC 9745, section 4); the helper refuses it, and refuses a
datetime without a time zone. Mark a route deprecated only after the notice above has gone out; no route in this
template is deprecated, the tests use a throwaway route.
