#!/usr/bin/env python3
"""Safely extract a tar archive: safe_extract.py <archive> <dest>.

Uses tarfile's "data" filter (PEP 706), which refuses absolute names, ".." escapes, links and
hardlinks pointing outside dest, and device or special files, and strips setuid/setgid bits.
After extraction it re-checks, as a second layer, that every symlink resolves inside dest and that
no regular file shares an inode with a path outside dest.
`safe_extract.py --check <installs> <tree>...` runs the same tree check (and refuses setuid and
setgid files) over trees that something else extracted (the tools mise installed); a failing
tree is removed. `--remove <installs> <tree>...` removes trees. Both touch only a <tool>/<version>
directory strictly inside <installs> (an absolute path, never / or a directory holding $HOME) and
refuse every other path before doing anything.
Exits non-zero on any problem. Needs Python 3.12 or newer (tools.sh picks the interpreter).
"""

from __future__ import annotations

import os
import shutil
import stat
import sys
import tarfile
from pathlib import Path

ARGC = 3


class UnsafeTreeError(Exception):
    """The extracted tree has a link that leads outside the destination."""


def check_tree(dest: Path) -> None:
    root = os.path.realpath(dest)
    inodes: dict[int, int] = {}
    links: dict[int, int] = {}
    for base, dirs, files in os.walk(dest):
        for name in (*dirs, *files):
            path = Path(base, name)
            if path.is_symlink():
                real = os.path.realpath(path)
                if real != root and not real.startswith(root + os.sep):
                    msg = f"symlink {path.relative_to(dest)} resolves outside {dest}"
                    raise UnsafeTreeError(msg)
            elif path.is_file():
                st = path.stat()
                if st.st_mode & (stat.S_ISUID | stat.S_ISGID):
                    msg = f"{path.relative_to(dest)} has a setuid or setgid bit"
                    raise UnsafeTreeError(msg)
                inodes[st.st_ino] = inodes.get(st.st_ino, 0) + 1
                links[st.st_ino] = st.st_nlink
    for ino, nlink in links.items():
        if nlink > inodes[ino]:
            msg = f"a file in {dest} is hard-linked to a path outside it (inode {ino})"
            raise UnsafeTreeError(msg)


MIN_DEPTH = 2  # <tool>/<version> below the installs directory


class RefusedPathError(Exception):
    """A path the bounded modes will not touch."""


def bounded(installs: str, trees: list[str], *, must_exist: bool) -> list[Path]:
    """The trees as real paths, each at least <tool>/<version> deep inside the installs directory.

    Refuses an empty or relative path, a `..` component, a link, the filesystem root, $HOME or a
    directory that holds it, and anything that is not strictly inside the installs directory, so a
    wrong argument can never make the removal reach the working tree or the home directory."""
    if not installs or not Path(installs).is_absolute():
        msg = f"the installs directory {installs!r} is not an absolute path"
        raise RefusedPathError(msg)
    root = os.path.realpath(installs)
    home = os.path.realpath(os.environ["HOME"]) if os.environ.get("HOME") else ""
    if root == os.sep or (home and (home == root or home.startswith(root + os.sep))):
        msg = f"the installs directory {installs!r} is the filesystem root or holds $HOME"
        raise RefusedPathError(msg)
    if not Path(root).is_dir():
        msg = f"the installs directory {installs!r} does not exist"
        raise RefusedPathError(msg)
    real: list[Path] = []
    for tree in trees:
        if (
            not tree
            or not Path(tree).is_absolute()
            or ".." in Path(tree).parts
            or Path(tree).is_symlink()
        ):
            msg = f"{tree!r} is not an absolute path to a directory, or is a link"
            raise RefusedPathError(msg)
        resolved = os.path.realpath(tree)
        parts = Path(resolved).relative_to(root).parts if resolved.startswith(root + os.sep) else ()
        if len(parts) < MIN_DEPTH:
            msg = f"{tree!r} is not a tool tree inside {installs!r}"
            raise RefusedPathError(msg)
        if must_exist and not Path(resolved).is_dir():
            msg = f"{tree!r} is not a directory"
            raise RefusedPathError(msg)
        real.append(Path(resolved))
    return real


def check_installed(installs: str, trees: list[str], *, remove_only: bool = False) -> int:
    """Check each tree and remove the ones that fail (--remove: remove them all). 1 on a refusal
    or a failed tree."""
    try:
        paths = bounded(installs, trees, must_exist=not remove_only)
    except RefusedPathError as exc:
        sys.stderr.write(f"safe_extract: refusing: {exc}\n")
        return 1
    failed = 0
    for path in paths:
        try:
            if not remove_only:
                check_tree(path)
            else:
                shutil.rmtree(path, ignore_errors=True)
        except (OSError, UnsafeTreeError) as exc:
            sys.stderr.write(f"safe_extract: refusing {path}: {exc}; removing it\n")
            shutil.rmtree(path, ignore_errors=True)
            failed = 1
    return failed


def main(argv: list[str]) -> int:
    if len(argv) > 2 and argv[1] in {"--check", "--remove"}:  # noqa: PLR2004
        return check_installed(argv[2], argv[3:], remove_only=argv[1] == "--remove")
    if len(argv) != ARGC:
        sys.stderr.write("usage: safe_extract.py <archive> <dest>\n")
        return 2
    archive, dest = Path(argv[1]), Path(argv[2])
    if archive.suffix == ".zip":
        sys.stderr.write("safe_extract: zip archives are not supported; refusing\n")
        return 1
    try:
        with tarfile.open(archive) as tf:
            tf.extractall(dest, filter="data")
        check_tree(dest)
    except (tarfile.TarError, OSError, UnsafeTreeError, TypeError) as exc:
        # TypeError: Python without the data filter (before 3.12): refuse rather than extract.
        sys.stderr.write(f"safe_extract: refusing {archive.name}: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
