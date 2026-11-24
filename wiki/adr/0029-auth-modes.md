# ADR-0029: OIDC and Cloudflare Access are the first-class auth modes

## Status

Accepted.

## Date

2026-11-20

## Context

The template cannot know an adopter's identity provider. It must make the secure choices easy, keep the modes it
already implements and tests, and make plain which ones are fit for end-user identity.

## Decision

`AUTH_MODE` selects one of five modes:
- `oidc` and `cloudflare-access` are the production modes: the service verifies every token itself (the provider's
  JWKS, or the Access team's keys and the application's AUD tag);
- `gateway` (an identity header from an auth proxy, trusted only from `AUTH_PROXY_CIDRS`) and `bearer` (a shared-secret
  HMAC token) stay, implemented and tested, labelled **reference modes**;
- `none` is the default for local use; with `ENV=production` it is refused unless `ALLOW_ANONYMOUS=true`, and the
  production chart render refuses it on the same terms.

## Consequences

- Good: an adopter plugs in an IdP by configuration, and production cannot start open by accident.
- Bad: five modes to maintain and test; the reference modes can be misused (a shared secret mints any identity).

## Alternatives considered

- Drop `gateway` and `bearer`: rejected, they are implemented, tested and useful behind an auth proxy or in tests.
- Ship an IdP: rejected, it is the adopter's choice.

## Links

- [Security: authentication and authorization](../security.md#authentication--authorization)
- [`src/your_service_name/backend/auth/dependencies.py`](../../src/your_service_name/backend/auth/dependencies.py),
  [`src/your_service_name/backend/settings.py`](../../src/your_service_name/backend/settings.py)
