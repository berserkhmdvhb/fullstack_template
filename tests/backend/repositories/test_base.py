# your-service-name — adaptable service template (see wiki/getting-started.md)
from __future__ import annotations

from collections.abc import Sequence

from your_service_name.backend.repositories.base import EchoItem, EchoRepository


class _DummyRepo(EchoRepository):
    """Simple in-memory stub to validate the protocol at runtime."""

    def __init__(self) -> None:
        self._items: list[EchoItem] = []

    def save(self, text: str, length: int) -> None:
        self._items.append((text, length))

    def list(self) -> Sequence[EchoItem]:
        return list(self._items)


def test_echo_repository_contract_save_and_list() -> None:
    repo: EchoRepository = _DummyRepo()
    repo.save("hi", 2)
    repo.save("hello", 5)

    items = repo.list()
    assert isinstance(items, Sequence)
    assert ("hi", 2) in items
    assert ("hello", 5) in items


def test_echo_item_runtime_shape() -> None:
    item: EchoItem = ("abc", 3)
    assert isinstance(item, tuple)
    assert item == ("abc", 3)
