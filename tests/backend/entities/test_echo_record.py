# your-service-name — adaptable service template (see wiki/getting-started.md)
from __future__ import annotations

import dataclasses
from typing import Any, cast

import pytest

from your_service_name.backend.entities.echo_record import EchoRecord


def test_echo_record_fields_and_values() -> None:
    rec = EchoRecord(text="hello", length=5)
    expected_len = len("hello")
    assert rec.text == "hello"
    assert rec.length == expected_len


def test_echo_record_is_dataclass_and_frozen() -> None:
    assert dataclasses.is_dataclass(EchoRecord)
    rec = EchoRecord(text="x", length=1)

    # Cast to Any to avoid mypy "read-only" error while still triggering runtime FrozenInstanceError
    with pytest.raises(dataclasses.FrozenInstanceError):
        cast("Any", rec).text = "y"


def test_echo_record_has_slots() -> None:
    assert hasattr(EchoRecord, "__slots__")
    rec = EchoRecord(text="a", length=1)
    assert not hasattr(rec, "__dict__")
