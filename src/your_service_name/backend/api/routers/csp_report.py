# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Opt-in CSP violation report endpoint: ``POST /api/v1/csp-report`` (``CSP_REPORT_ENABLED``).

A browser-defined format, not part of the API contract: it is kept out of the OpenAPI document
(``include_in_schema=False``), mounted only when enabled (a 404 otherwise) and takes no
authentication (browsers send reports without credentials). The checks run cheapest first:

1. the per-client limit (``CSP_REPORT_RATE_LIMIT``, its own counter in the shared limiter): 429
2. the content type, ``application/csp-report`` or ``application/reports+json``: 415
3. the size, ``CSP_REPORT_MAX_BYTES``, from ``Content-Length`` and again while the body streams
   in (never read unbounded): 413
4. the JSON and its shape: 400

An accepted report answers 204 and logs one warning per violation (see
``services/csp_report_service.py`` for exactly what is logged). Every error is a problem body.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException, Request, Response, status
from limits import parse

from your_service_name.backend import paths
from your_service_name.backend.rate_limiter import client_key
from your_service_name.backend.services import csp_report_service

router = APIRouter(include_in_schema=False)
logger = logging.getLogger(__name__)

NAMESPACE = "csp-report"
_MEDIA_TYPES = frozenset({"application/csp-report", "application/reports+json"})


@router.post(paths.CSP_REPORT_ROUTE)
async def receive_csp_report(request: Request) -> Response:
    """Log the violations in a CSP report; 204 on success."""
    settings = request.app.state.settings
    limit = parse(settings.CSP_REPORT_RATE_LIMIT)
    if not await request.app.state.rate_limiter.hit(limit, NAMESPACE, client_key(request)):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Rate limit exceeded. Please try again later.",
            headers={"Retry-After": str(limit.GRANULARITY.seconds * limit.multiples)},
        )

    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type not in _MEDIA_TYPES:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Unsupported report type.")

    body = await _read_capped(request, settings.CSP_REPORT_MAX_BYTES)
    try:
        found = csp_report_service.violations(json.loads(body), media_type)
    except (ValueError, RecursionError) as exc:  # bad JSON or encoding, or a wrong shape
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The report is not valid.") from exc

    for fields in found:
        logger.warning(
            "CSP violation: directive=%s blocked=%s document=%s",
            fields.get("effective-directive") or fields.get("violated-directive", "-"),
            fields.get("blocked-uri", "-"),
            fields.get("document-uri", "-"),
            extra={"csp_violation": fields},
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _read_capped(request: Request, max_bytes: int) -> bytes:
    """The body, refusing (413) as soon as it is known to be over ``max_bytes``."""
    declared = request.headers.get("content-length", "")
    if declared.isascii() and declared.isdigit() and int(declared) > max_bytes:
        raise _too_large()
    received = bytearray()
    async for chunk in request.stream():
        received += chunk
        if len(received) > max_bytes:
            raise _too_large()
    return bytes(received)


def _too_large() -> HTTPException:
    return HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "The report is too large.")
