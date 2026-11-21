# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Authentication layer for the backend.

Exposes the request-principal dependency used to gate write endpoints:

    from your_service_name.backend.auth import get_current_user, Principal
    ...
    async def create(..., current_user: Annotated[Principal, Depends(get_current_user)]): ...

Identity is config-driven (``AUTH_MODE``): ``none`` (default, anonymous),
``oidc`` (JWT checked against a JWKS; the production mode), ``gateway`` (trusted header) or
``bearer`` (HMAC JWT); the last two are reference modes. See
``your_service_name.backend.auth.dependencies`` and wiki/security.md.
"""

from your_service_name.backend.auth.dependencies import (
    AuthUnavailableError,
    InvalidTokenError,
    NotAuthenticatedError,
    get_current_user,
    require_roles,
)
from your_service_name.backend.auth.principal import Principal

__all__ = [
    "AuthUnavailableError",
    "InvalidTokenError",
    "NotAuthenticatedError",
    "Principal",
    "get_current_user",
    "require_roles",
]
