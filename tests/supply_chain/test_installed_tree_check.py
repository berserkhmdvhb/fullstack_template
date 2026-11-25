"""The check that runs after every `mise install`: `safe_extract.py --check <tree>...` refuses a
tree with a path or a link that leads outside it, or a setuid or setgid file, removes that tree and
exits non-zero. Offline, on fixture trees."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts/safe_extract.py"


def check(*trees: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--check", *map(str, trees)],
        capture_output=True,
        text=True,
        check=False,
    )


def tree(base: Path, name: str = "tool/1.0.0") -> Path:
    root = base / name
    (root / "bin").mkdir(parents=True)
    exe = root / "bin/tool"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    return root


def test_a_clean_tree_with_in_tree_links_passes(tmp_path: Path) -> None:
    root = tree(tmp_path)
    (root / "bin/tool-link").symlink_to("tool")
    (root / "bin/up").symlink_to("../bin/tool")
    r = check(root)
    assert r.returncode == 0, r.stderr
    assert (root / "bin/tool").is_file()


def test_a_link_out_of_the_tree_is_refused_and_the_tree_is_removed(tmp_path: Path) -> None:
    for i, target in enumerate(("/etc", "../..", "../../other")):
        root = tree(tmp_path / str(i))
        (root / "bin/out").symlink_to(target)
        r = check(root)
        assert r.returncode != 0
        assert "outside" in r.stderr, r.stderr
        assert not root.exists()


def test_a_link_through_a_link_to_the_outside_is_refused(tmp_path: Path) -> None:
    root = tree(tmp_path)
    (root / "a").symlink_to("../..")
    (root / "b").symlink_to("a/x")
    assert check(root).returncode != 0
    assert not root.exists()


def test_setuid_and_setgid_files_are_refused_and_the_tree_is_removed(tmp_path: Path) -> None:
    for i, mode in enumerate((0o4755, 0o2755)):
        root = tree(tmp_path / str(i))
        (root / "bin/tool").chmod(mode)
        r = check(root)
        assert r.returncode != 0
        assert "setuid" in r.stderr or "setgid" in r.stderr, r.stderr
        assert not root.exists()


def test_a_file_hard_linked_to_the_outside_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_text("x")
    root = tree(tmp_path)
    os.link(outside, root / "bin/hard")
    assert check(root).returncode != 0
    assert not root.exists()


def test_one_bad_tree_is_removed_and_the_good_one_is_kept(tmp_path: Path) -> None:
    good, bad = tree(tmp_path, "a/1"), tree(tmp_path, "b/1")
    (bad / "x").symlink_to("/etc")
    r = check(good, bad)
    assert r.returncode != 0
    assert good.exists()
    assert not bad.exists()
