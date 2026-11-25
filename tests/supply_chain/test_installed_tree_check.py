"""The check that runs after every `mise install`: `safe_extract.py --check <installs> <tree>...`
refuses a tree with a path or a link that leads outside it, or a setuid or setgid file, removes that
tree and exits non-zero. It removes only a tree strictly inside <installs> (the mise installs
directory) and refuses any other path, whatever it is. Offline, on fixture trees."""

from __future__ import annotations

import importlib.util
import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path
from types import ModuleType

import pytest

EXECUTABLE = 0o755  # the mode a setuid 0o4777 file ends with
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


# --- Python without tarfile's data filter (macOS ships 3.9): the same rules, written out ---


def _module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("safe_extract_under_test", SCRIPT)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _archive(path: Path, *members: tarfile.TarInfo) -> Path:
    with tarfile.open(path, "w:gz") as tf:
        for member in members:
            tf.addfile(member, io.BytesIO(b"x") if member.isreg() else None)
    return path


def _member(name: str, kind: bytes = tarfile.REGTYPE, **attrs: object) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.size = 1 if kind == tarfile.REGTYPE else 0
    info.mode = 0o755
    for key, value in attrs.items():
        setattr(info, key, value)
    return info


@pytest.fixture
def legacy(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """safe_extract.py as an older Python runs it: no data_filter, and rmtree's old callback."""
    monkeypatch.delattr(tarfile, "data_filter", raising=False)
    monkeypatch.setattr(sys, "version_info", (3, 9, 6, "final", 0))
    return _module()


def _extract(module: ModuleType, archive: Path, dest: Path) -> int:
    return int(module.main(["safe_extract.py", str(archive), str(dest)]))


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_the_legacy_filter_extracts_a_clean_archive_and_strips_setuid_and_write_bits(
    legacy: ModuleType, tmp_path: Path
) -> None:
    archive = _archive(
        tmp_path / "a.tgz",
        _member("tool", tarfile.DIRTYPE),
        _member("tool/run", mode=0o4777),
        _member("tool/link", tarfile.SYMTYPE, linkname="run"),
        _member("tool/hard", tarfile.LNKTYPE, linkname="tool/run"),
    )
    dest = tmp_path / "out"
    assert _extract(legacy, archive, dest) == 0
    assert (dest / "tool/link").is_symlink()
    assert (dest / "tool/run").stat().st_mode & 0o7777 == EXECUTABLE


@pytest.mark.parametrize(
    "bad",
    [
        _member("../escape"),
        _member("a/../../escape"),
        _member("/etc/escape"),
        _member("tool/out", tarfile.SYMTYPE, linkname="/etc"),
        _member("tool/up", tarfile.SYMTYPE, linkname="../../.."),
        _member("tool/hard", tarfile.LNKTYPE, linkname="../outside"),
        _member("tool/fifo", tarfile.FIFOTYPE),
        _member("tool/dev", tarfile.CHRTYPE),
    ],
    ids=lambda m: f"{m.name}-{m.type!r}",
)
def test_the_legacy_filter_refuses_what_the_data_filter_refuses(
    legacy: ModuleType, tmp_path: Path, bad: tarfile.TarInfo
) -> None:
    dest = tmp_path / "out"
    assert _extract(legacy, _archive(tmp_path / "a.tgz", bad), dest) == 1
    assert not (tmp_path / "escape").exists()
    assert not dest.exists() or not any(dest.rglob("*"))


def test_the_checks_and_the_removal_work_without_the_new_rmtree_callback(
    legacy: ModuleType, tmp_path: Path
) -> None:
    root = tree(tmp_path)
    (root / "bin/out").symlink_to("/etc")
    assert legacy.check_installed(str(tmp_path), [str(root)]) == 1
    assert not root.exists()
    other = tree(tmp_path, "other/1.0.0")
    assert legacy.check_installed(str(tmp_path), [str(other)], remove_only=True) == 0
    assert not other.exists()
