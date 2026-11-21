# your-service-name — adaptable service template (see wiki/getting-started.md)
"""RFC 9457 problem details: one builder and the handlers that make every error use it.

Every error the API returns is ``application/problem+json`` with ``type``, ``title``, ``status``,
``detail`` and ``instance`` (the request path, never the query string, which can carry tokens),
plus the ``request_id`` extension (and ``trace_id`` when the caller sent a valid ``traceparent``).
Nothing from an unexpected exception reaches the body: a 500 is always the generic text.

``install_error_handling`` wires the handlers and the OpenAPI description of the error bodies.
The catch-all ``Exception`` handler is the one Starlette's ``ServerErrorMiddleware`` calls (the
outermost layer, so it also catches an exception raised inside a middleware).
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from your_service_name.backend.logging.correlation import (
    HEADER_NAME,
    get_request_id,
    get_trace_id,
    reset_request_id,
    reset_trace_context,
    set_request_id,
    set_trace_context,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from starlette.requests import Request
    from starlette.types import Scope

logger = logging.getLogger(__name__)

PROBLEM_MEDIA_TYPE = "application/problem+json"
_SERVER_ERROR = 500
_TOO_LARGE = 413
_GENERIC_500 = "The server could not process the request."
_VALIDATION_DETAIL = "The request did not pass validation."
#: Statuses described on every operation in the OpenAPI document (422 is added where it can occur).
_DEFAULT_PROBLEM_STATUSES = (400, 401, 403, 404, 413, 429, 500)


class ProblemDetails(BaseModel):
    """An RFC 9457 problem details body (``application/problem+json``)."""

    type: str = Field(default="about:blank", description="Problem type; `about:blank` for HTTP.")
    title: str = Field(description="The HTTP reason phrase.")
    status: int = Field(description="The HTTP status code.")
    detail: str = Field(description="A safe, human-readable explanation.")
    instance: str = Field(description="The request path (no query string).")
    request_id: str = Field(description="The `X-Request-Id` of the request, for log correlation.")
    trace_id: str | None = Field(
        default=None, description="The W3C trace id from a valid `traceparent` header."
    )


class FieldError(BaseModel):
    """One invalid field of a request; never carries the submitted value."""

    loc: list[str | int] = Field(description="Where the problem is: `body`, `query`, ... + field.")
    msg: str
    type: str


class ValidationProblem(ProblemDetails):
    """A 422 problem: ``ProblemDetails`` plus the invalid fields."""

    errors: list[FieldError]


def request_ids(scope: Scope) -> tuple[str, str | None]:
    """The request id and trace id of a request.

    The scope's ``state`` is read first: it survives to the outermost layer, where the context
    variables are already reset. Inside the correlation middleware the context variables answer.
    """
    state: Mapping[str, Any] = scope.get("state") or {}
    return (state.get("request_id") or get_request_id(), state.get("trace_id") or get_trace_id())


def problem_response(
    scope: Scope,
    status_code: int,
    detail: str | None = None,
    *,
    headers: Mapping[str, str] | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    """Build the one error response: a problem body with the right content type and headers."""
    phrase = HTTPStatus(status_code).phrase
    request_id, trace_id = request_ids(scope)
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": phrase,
        "status": status_code,
        "detail": detail or phrase,
        "instance": scope.get("path", ""),
        "request_id": request_id,
    }
    if trace_id:
        body["trace_id"] = trace_id
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(
        body,
        status_code=status_code,
        headers={"X-Content-Type-Options": "nosniff", **(headers or {})},
        media_type=PROBLEM_MEDIA_TYPE,
    )


def _http_exception(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101 — the registered type
    safe = isinstance(exc.detail, str) and exc.status_code < _SERVER_ERROR
    return problem_response(
        request.scope,
        exc.status_code,
        exc.detail if safe and isinstance(exc.detail, str) else None,
        headers=exc.headers,
    )


def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101 — the registered type
    # loc/msg/type only: `input` and `ctx` would echo what the client submitted (maybe a secret).
    errors = [
        {
            "loc": [p if isinstance(p, int) else str(p) for p in e["loc"]],
            "msg": e["msg"],
            "type": e["type"],
        }
        for e in exc.errors()
    ]
    return problem_response(request.scope, 422, _VALIDATION_DETAIL, errors=errors)


def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    # Outside the correlation middleware the context variable is reset: restore it for the log line.
    request_id, _ = request_ids(request.scope)
    token = set_request_id(request_id)
    trace_token = set_trace_context(request.scope.get("state", {}).get("trace_context"))
    try:
        logger.error("Unhandled exception", exc_info=exc)
    finally:
        reset_trace_context(trace_token)
        reset_request_id(token)
    return problem_response(
        request.scope, _SERVER_ERROR, _GENERIC_500, headers={HEADER_NAME: request_id}
    )


def _describe(code: int, ref: str) -> dict[str, Any]:
    return {
        "description": HTTPStatus(code).phrase,
        "content": {PROBLEM_MEDIA_TYPE: {"schema": {"$ref": f"#/components/schemas/{ref}"}}},
    }


def _problem_openapi(app: FastAPI) -> dict[str, Any]:
    """The OpenAPI document with every error response described as a problem body."""
    if app.openapi_schema:
        return app.openapi_schema
    schema = FastAPI.openapi(app)
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    for name in ("HTTPValidationError", "ValidationError"):
        components.pop(name, None)
    for model in (ProblemDetails, ValidationProblem, FieldError):
        definition = model.model_json_schema(ref_template="#/components/schemas/{model}")
        components.update(definition.pop("$defs", {}))
        components[model.__name__] = definition
    for item in schema.get("paths", {}).values():
        for operation in item.values():
            responses = operation.setdefault("responses", {})
            for code in _DEFAULT_PROBLEM_STATUSES:
                if code != _TOO_LARGE or "requestBody" in operation:
                    responses.setdefault(str(code), _describe(code, "ProblemDetails"))
            if "422" in responses:
                responses["422"] = _describe(422, "ValidationProblem")
    app.openapi_schema = schema
    return schema


def install_error_handling(app: FastAPI) -> None:
    """Register the problem handlers (HTTP errors, validation, catch-all 500) and OpenAPI."""
    app.add_exception_handler(StarletteHTTPException, _http_exception)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unhandled)
    app.openapi = lambda: _problem_openapi(app)  # type: ignore[method-assign]
