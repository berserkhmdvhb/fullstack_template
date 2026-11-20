"""Caches are restored everywhere and saved only on a push to main (static, offline)."""

from __future__ import annotations

import re

from tests.supply_chain._workflows import every_step

PUSH_TO_MAIN = "github.event_name == 'push' && github.ref == 'refs/heads/main'"
# The only allowed save conditions: a push to main, optionally only on a cache miss.
SAVE_IF = re.compile(
    re.escape(PUSH_TO_MAIN) + r"( && steps\.[\w-]+\.outputs\.cache-hit != 'true')?"
)


def test_setup_actions_use_no_builtin_cache() -> None:
    for name, step in every_step():
        uses = str(step.get("uses", ""))
        if uses.startswith(("actions/setup-node@", "actions/setup-python@")):
            assert "cache" not in step.get("with", {}), name
            assert "cache-dependency-path" not in step.get("with", {}), name


def test_plain_actions_cache_is_never_used() -> None:
    for name, step in every_step():
        assert not str(step.get("uses", "")).startswith("actions/cache@"), name


def test_every_cache_save_is_push_to_main_only() -> None:
    saves = [
        (n, s) for n, s in every_step() if str(s.get("uses", "")).startswith("actions/cache/save@")
    ]
    assert saves
    for name, step in saves:
        assert SAVE_IF.fullmatch(str(step.get("if", "")).strip()), name


def test_save_condition_is_an_exact_match() -> None:
    assert SAVE_IF.fullmatch(f"{PUSH_TO_MAIN} && steps.restore.outputs.cache-hit != 'true'")
    for bad in (
        f"{PUSH_TO_MAIN} || true",
        f"{PUSH_TO_MAIN} && true || true",
        f"always() && {PUSH_TO_MAIN}",
        f"{PUSH_TO_MAIN}x",
    ):
        assert not SAVE_IF.fullmatch(bad), bad
