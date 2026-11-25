"""The check that runs after every `mise install`: `safe_extract.py --check <installs> <tree>...`
refuses a tree with a path or a link that leads outside it, or a setuid or setgid file, removes that
tree and exits non-zero. It removes only a tree strictly inside <installs> (the mise installs
directory) and refuses any other path, whatever it is. Offline, on fixture trees."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts/safe_extract.py"


def run(
    mode: str,
    installs: Path | str,
    *trees: Path | str,
    home: Path | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ["PATH"], **({"HOME": str(home)} if home else {})}
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), mode, str(installs), *map(str, trees)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=cwd,
    )


def check(installs: Path, *trees: Path) -> subprocess.CompletedProcess[str]:
    return run("--check", installs, *trees)


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
    r = check(tmp_path, root)
    assert r.returncode == 0, r.stderr
    assert (root / "bin/tool").is_file()


def test_a_link_out_of_the_tree_is_refused_and_the_tree_is_removed(tmp_path: Path) -> None:
    for i, target in enumerate(("/etc", "../..", "../../other")):
        root = tree(tmp_path / str(i))
        (root / "bin/out").symlink_to(target)
        r = check(tmp_path, root)
        assert r.returncode != 0
        assert "outside" in r.stderr, r.stderr
        assert not root.exists()


def test_a_link_through_a_link_to_the_outside_is_refused(tmp_path: Path) -> None:
    root = tree(tmp_path)
    (root / "a").symlink_to("../..")
    (root / "b").symlink_to("a/x")
    assert check(tmp_path, root).returncode != 0
    assert not root.exists()


def test_setuid_and_setgid_files_are_refused_and_the_tree_is_removed(tmp_path: Path) -> None:
    for i, mode in enumerate((0o4755, 0o2755)):
        root = tree(tmp_path / str(i))
        (root / "bin/tool").chmod(mode)
        r = check(tmp_path, root)
        assert r.returncode != 0
        assert "setuid" in r.stderr or "setgid" in r.stderr, r.stderr
        assert not root.exists()


def test_a_file_hard_linked_to_the_outside_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.write_text("x")
    root = tree(tmp_path)
    os.link(outside, root / "bin/hard")
    assert check(tmp_path, root).returncode != 0
    assert not root.exists()


def test_one_bad_tree_is_removed_and_the_good_one_is_kept(tmp_path: Path) -> None:
    good, bad = tree(tmp_path, "a/1"), tree(tmp_path, "b/1")
    (bad / "x").symlink_to("/etc")
    r = check(tmp_path, good, bad)
    assert r.returncode != 0
    assert good.exists()
    assert not bad.exists()


def test_a_path_not_strictly_inside_the_installs_dir_is_refused_and_nothing_is_removed(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    installs = repo / ".tools/mise/data/installs"
    inside = tree(installs)
    for d in (home, repo, repo / ".tools", tmp_path / "elsewhere"):
        d.mkdir(parents=True, exist_ok=True)
        (d / "keep").write_text("x")
    link = installs / "linked"
    link.symlink_to(tmp_path / "elsewhere")
    refused: list[Path | str] = [
        "",
        "tool/1.0.0",
        ".",
        "..",
        "/",
        home,
        repo,
        repo / ".tools",
        installs,
        installs / "tool",
        tmp_path / "elsewhere",
        link,
        installs / ".." / "..",
        installs / "tool/1.0.0/../../..",
    ]
    for mode in ("--check", "--remove"):
        for path in refused:
            r = run(mode, installs, path, home=home, cwd=repo)
            assert r.returncode != 0, (mode, path)
            assert "refusing" in r.stderr, (mode, path, r.stderr)
    assert (inside / "bin/tool").is_file()
    for d in (home, repo, repo / ".tools", tmp_path / "elsewhere"):
        assert (d / "keep").is_file(), d


def test_an_installs_dir_that_is_empty_relative_the_root_or_home_is_refused(tmp_path: Path) -> None:
    home = tmp_path / "home"
    victim = tree(home, "x/1")
    for installs in ("", "installs", "/", str(home), str(home) + "/", str(tmp_path)):
        for mode in ("--check", "--remove"):
            r = run(mode, installs, victim, home=home, cwd=tmp_path)
            assert r.returncode != 0, (mode, installs)
    assert victim.exists()


def test_one_refused_path_stops_the_whole_call_before_anything_is_removed(tmp_path: Path) -> None:
    bad = tree(tmp_path, "a/1")
    (bad / "x").symlink_to("/etc")
    r = check(tmp_path, bad, tmp_path / "..")
    assert r.returncode != 0
    assert bad.exists(), "a bad tree is only removed once every path has passed the bounds"


def test_a_missing_tree_is_refused_by_the_check_and_fine_for_remove(tmp_path: Path) -> None:
    gone = tmp_path / "a/1"
    assert check(tmp_path, gone).returncode != 0
    assert run("--remove", tmp_path, gone).returncode == 0


def test_remove_deletes_a_tree_inside_the_installs_dir_and_only_that(tmp_path: Path) -> None:
    gone, kept = tree(tmp_path, "a/1"), tree(tmp_path, "b/1")
    assert run("--remove", tmp_path, gone).returncode == 0
    assert not gone.exists()
    assert kept.exists()


def test_a_removal_that_fails_is_reported_with_the_path_and_a_non_zero_exit(tmp_path: Path) -> None:
    stuck = tree(tmp_path, "a/1")
    locked = stuck / "bin"
    locked.chmod(0o555)  # nothing inside can be unlinked
    try:
        r = run("--remove", tmp_path, stuck)
        assert r.returncode != 0
        assert str(stuck) in r.stderr, r.stderr
        assert (locked / "tool").exists()
        (stuck / "x").symlink_to("/etc")
        r = check(tmp_path, stuck)
        assert r.returncode != 0
        assert "could not remove" in r.stderr, r.stderr
    finally:
        locked.chmod(0o755)
