# your-service-name — adaptable service template (see wiki/getting-started.md)
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import pytest

from your_service_name.backend.repositories.in_memory_echo import (
    MSG_ERROR_INVALID_LENGTH,
    InMemoryEchoRepository,
)


def test_save_and_list_returns_pairs() -> None:
    len_item = 2
    repo = InMemoryEchoRepository()
    repo.save("hi", 2)
    repo.save("hello", 5)

    items = list(repo.list())
    assert items == [("hi", 2), ("hello", 5)]
    # Each entry is a tuple (not the EchoRecord entity)
    assert all(isinstance(it, tuple) and len(it) == len_item for it in items)


def test_list_returns_defensive_copy() -> None:
    repo = InMemoryEchoRepository()
    repo.save("a", 1)

    first = list(repo.list())
    first.append(("extra", 5))  # Mutate the returned list

    second = list(repo.list())
    assert ("extra", 5) not in second  # Repo state is unchanged


def test_negative_length_raises_value_error_message() -> None:
    repo = InMemoryEchoRepository()
    with pytest.raises(ValueError, match=re.escape(MSG_ERROR_INVALID_LENGTH)):
        repo.save("oops", -1)


def test_basic_thread_safety_concurrent_saves() -> None:
    repo = InMemoryEchoRepository()
    texts = [f"t{i}" for i in range(100)]

    def _save(t: str) -> None:
        repo.save(t, len(t))

    with ThreadPoolExecutor(max_workers=16) as pool:
        for t in texts:
            pool.submit(_save, t)

    items = list(repo.list())
    # We expect one entry per submitted text; order is not guaranteed
    assert len(items) == len(texts)
    # C401: use a set comprehension, not a generator in set(...)
    assert {text for text, _ in items} == set(texts)
