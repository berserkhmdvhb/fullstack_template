# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Echo endpoint request/response schemas.

Responsibilities:
- Validate the incoming text to echo.
- Serialize the echoed output and its length.

Public API:
- `EchoRequest`: Request body schema.
- `EchoResponse`: Response payload schema.

Operational:
- Concurrency: Safe; no side effects.
- Logging: None.

Usage
-----
from your_service_name.backend.schemas.echo import EchoRequest, EchoResponse
req = EchoRequest(text="hello")
res = EchoResponse(text=req.text, length=len(req.text))
"""

from __future__ import annotations

from pydantic import BaseModel, Field, HttpUrl, StrictStr


class EchoRequest(BaseModel):
    """
    Request body for the echo endpoint.

    Attributes
    ----------
    text : str
        Arbitrary text to echo back.
    source_url : HttpUrl | None
        Optional URL associated with the text; reserved for future use.

    Examples
    --------
    >>> EchoRequest(text="abc").model_dump()
    {'text': 'abc', 'source_url': None}
    """

    text: StrictStr = Field(..., min_length=1, max_length=10_000, description="Text to echo.")
    source_url: HttpUrl | None = Field(default=None, description="Optional source URL.")

    model_config = {
        "extra": "forbid",  # Prevent unexpected fields in the request.
        # Field-level constraints already enforce length; keep config minimal.
    }


class EchoResponse(BaseModel):
    """
    Response payload for the echo endpoint.

    Attributes
    ----------
    text : str
        Echoed text.
    length : int
        Length of the provided text.

    Examples
    --------
    >>> EchoResponse(text="abc", length=3).model_dump()
    {'text': 'abc', 'length': 3}
    """

    text: StrictStr = Field(..., description="Echoed text.")
    length: int = Field(..., ge=0, description="Length of the text (non-negative).")

    model_config = {
        "extra": "forbid",  # Keep the response schema stable and explicit.
    }


# Ensure models are fully defined for Pydantic v2/OpenAPI generation.
EchoRequest.model_rebuild()
EchoResponse.model_rebuild()
