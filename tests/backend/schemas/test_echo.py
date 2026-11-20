# your-service-name — adaptable service template (see wiki/getting-started.md)
from __future__ import annotations

import pytest
from pydantic import ValidationError

from your_service_name.backend.schemas.echo import EchoRequest, EchoResponse


def test_echo_request_happy_path_and_defaults() -> None:
    req = EchoRequest(text="hello")
    assert req.text == "hello"
    assert req.source_url is None  # optional field defaults to None


def test_echo_request_validates_length_and_type() -> None:
    # empty string -> min_length=1
    with pytest.raises(ValidationError) as exc:
        EchoRequest.model_validate({"text": ""})
    err = exc.value.errors()
    # Single error on 'text' field
    assert err
    assert err[0]["loc"] == ("text",)
    assert err[0]["type"] == "string_too_short"

    # non-string rejected because StrictStr
    with pytest.raises(ValidationError) as exc:
        EchoRequest.model_validate({"text": 123})
    err = exc.value.errors()
    assert err
    assert err[0]["loc"] == ("text",)
    # The error type for wrong type will be one of the string type mismatches.abs
    # check it starts with 'string_'
    assert err[0]["type"].startswith("string_")

    # lower and upper bounds
    EchoRequest(text="a" * 1)  # ok
    EchoRequest(text="a" * 10_000)  # ok
    with pytest.raises(ValidationError) as exc:
        EchoRequest.model_validate({"text": "a" * 10_001})
    err = exc.value.errors()
    assert err
    assert err[0]["loc"] == ("text",)
    assert err[0]["type"] == "string_too_long"


def test_echo_request_httpurl_optional_and_validated() -> None:
    # Valid case: use model_validate to avoid mypy arg-type complaints while
    # still verifying that a valid string is accepted as HttpUrl
    req = EchoRequest.model_validate({"text": "hi", "source_url": "https://example.com"})
    assert req.source_url is not None
    # Pydantic normalizes a bare domain to include trailing slash
    assert str(req.source_url) == "https://example.com/"
    # Invalid case: bad URL should raise ValidationError
    with pytest.raises(ValidationError, match="source_url"):
        EchoRequest.model_validate({"text": "hi", "source_url": "not-a-url"})


def test_echo_request_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError, match="extra_field"):
        EchoRequest.model_validate({"text": "hello", "extra_field": "nope"})


def test_echo_response_happy_path_and_constraints() -> None:
    res = EchoResponse(text="abc", length=3)
    len_res = len("abc")
    assert res.text == "abc"
    assert res.length == len_res

    # ge=0: negative length should fail
    with pytest.raises(ValidationError) as exc:
        EchoResponse.model_validate({"text": "abc", "length": -1})
    err = exc.value.errors()
    assert err
    assert err[0]["loc"] == ("length",)
    assert err[0]["type"] == "greater_than_equal"

    # StrictStr violation on text
    with pytest.raises(ValidationError) as exc:
        EchoResponse.model_validate({"text": 123, "length": 3})
    err = exc.value.errors()
    assert err
    assert err[0]["loc"] == ("text",)
    assert err[0]["type"].startswith("string_")
