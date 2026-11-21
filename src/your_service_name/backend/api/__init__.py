# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
API assembly.

Responsibilities:
- Provide a single entry-point to assemble and mount all routers under a common prefix and version.

Public API:
- `build_api_router`: Return an APIRouter with feature routers attached under a versioned prefix.

Operational:
- Concurrency: Stateless assembly; routers manage their own dependencies.
- Logging: None.

Usage
-----
from your_service_name.backend.api import build_api_router

app.include_router(build_api_router(prefix="/api", version="v1"))

Notes
-----
- Keep feature router registration here to centralize HTTP wiring.
- Future versions (v2, etc.) can be added by importing their routers and adjusting this function.
"""

from __future__ import annotations

from importlib import import_module
from typing import Protocol, cast

from fastapi import APIRouter

from your_service_name.utils.versioning_api import (
    DEFAULT_API_VERSION,
    MSG_ERROR_UNSUPPORTED_VERSION,
    SUPPORTED_VERSIONS,
)


class RouterModule(Protocol):
    """
    Protocol for router modules that expose a FastAPI `router`.

    Attributes
    ----------
    router : fastapi.APIRouter
        The router to be included in the application.
    """

    router: APIRouter


def _load_router(version: str, name: str) -> RouterModule:
    """
    Lazily load a router module by version and name.

    Parameters
    ----------
    version : str
        API version segment (e.g., "v1").
    name : str
        Router module name (e.g., "echo", "health").

    Returns
    -------
    RouterModule
        Imported module object (e.g., `your_service_name.backend.api.routers.v1.echo`).

    Raises
    ------
    ValueError
        If the requested version is unsupported.
    """
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(MSG_ERROR_UNSUPPORTED_VERSION.format(version=version))

    module = import_module(f".routers.{version}.{name}", package=__name__)
    return cast("RouterModule", module)


def build_api_router(prefix: str = "/api", version: str = DEFAULT_API_VERSION) -> APIRouter:
    """
    Build a composite API router under a versioned prefix.

    Parameters
    ----------
    prefix : str, optional
        Base API prefix (e.g., "/api").
    version : str, optional
        API version (the `API_VERSION` setting); defaults to the code default.

    Returns
    -------
    fastapi.APIRouter
        Router with feature endpoints assembled under `prefix/version`.
    """

    resolved: str = version
    if resolved not in SUPPORTED_VERSIONS:
        raise ValueError(MSG_ERROR_UNSUPPORTED_VERSION.format(version=resolved))

    api = APIRouter(prefix=f"{prefix}/{resolved}")

    for name in SUPPORTED_VERSIONS[resolved]:
        mod = _load_router(resolved, name)
        api.include_router(mod.router)

    return api
