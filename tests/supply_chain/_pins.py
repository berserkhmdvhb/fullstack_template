"""The pinned toolchain as the installer reads it: `scripts/tools.sh list` merges the mise tools
(mise.toml) with the rows left in tools/pins.tsv, so no test needs its own name-to-tool map."""

from __future__ import annotations

import functools
import shutil
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
LOCK = tomllib.loads((ROOT / "mise.lock").read_text(encoding="utf-8"))


@functools.cache
def listing() -> dict[str, tuple[str, str]]:
    """name -> (version, mise tool key; empty for a row left in tools/pins.tsv)."""
    out = subprocess.run(
        [shutil.which("bash") or "/usr/bin/bash", str(ROOT / "scripts/tools.sh"), "list"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    rows = [ln.split() for ln in out.splitlines()]
    return {r[0]: (r[1], r[2] if len(r) > 2 else "") for r in rows}  # noqa: PLR2004


def pin(name: str) -> str:
    """The pinned version of a tool, from mise.toml or tools/pins.tsv."""
    assert name in listing(), f"{name} is pinned in neither mise.toml nor tools/pins.tsv"
    return listing()[name][0]


def lock_entry(name: str, platform: str = "linux-x64") -> dict[str, str]:
    """The mise.lock entry (checksum, url, ...) of a mise tool for one platform."""
    key = listing()[name][1]
    assert key, f"{name} is not a mise tool"
    entry: dict[str, str] = LOCK["tools"][key.removeprefix("core:")][0][f"platforms.{platform}"]
    return entry
