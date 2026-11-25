#!/usr/bin/env python3
"""Safely extract a tar archive: safe_extract.py <archive> <dest>.

Uses tarfile's "data" filter (PEP 706), which refuses absolute names, ".." escapes, links and
hardlinks pointing outside dest, and device or special files, and strips setuid/setgid bits.
After extraction it re-checks, as a second layer, that every symlink resolves inside dest and that
no regular file shares an inode with a path outside dest.
`safe_extract.py --check <tree>...` runs the same tree check (and refuses setuid and setgid files)
over trees that something else extracted (the tools mise installed); a failing tree is removed.
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


def check_installed(trees: list[str]) -> int:
    """Check each tree; remove the ones that fail. 1 if any failed."""
    failed = 0
    for tree in trees:
        try:
            check_tree(Path(tree))
        except (OSError, UnsafeTreeError) as exc:
            sys.stderr.write(f"safe_extract: refusing {tree}: {exc}; removing it\n")
            shutil.rmtree(tree, ignore_errors=True)
            failed = 1
    return failed


def main(argv: list[str]) -> int:
    if len(argv) > 1 and argv[1] == "--check":
        return check_installed(argv[2:])
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
