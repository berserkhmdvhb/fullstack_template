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
Exits non-zero on any problem. Python 3.12 or newer uses the data filter; an older Python (3.9 is
the one macOS ships) has none and gets the same rules from legacy_filter() below.
"""

from __future__ import annotations

import os
import posixpath
import shutil
import stat
import sys
import tarfile
from pathlib import Path, PurePosixPath

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


def inside(base: str, name: str) -> bool:
    """Whether `name`, joined to `base` and normalised, stays under `base` (names only)."""
    joined = posixpath.normpath(posixpath.join(base, name))
    return not PurePosixPath(name).is_absolute() and joined.split("/")[0] != ".."


def legacy_filter(tf: tarfile.TarFile) -> list[tarfile.TarInfo]:
    """The rules of tarfile's data filter, for a Python that does not have it: no absolute name, no
    `..`, only files, directories and links, a link that stays inside the archive's tree, no setuid,
    setgid or sticky bit and nothing group- or world-writable."""
    members = tf.getmembers()
    for member in members:
        name = PurePosixPath(member.name)
        if name.is_absolute() or ".." in name.parts:
            msg = f"{member.name}: absolute or .. name"
            raise UnsafeTreeError(msg)
        if not (member.isreg() or member.isdir() or member.issym() or member.islnk()):
            msg = f"{member.name}: a device or special file"
            raise UnsafeTreeError(msg)
        # A symlink's target is relative to its directory; a hard link's, to the archive's root.
        base = str(name.parent) if member.issym() else ""
        if (member.issym() or member.islnk()) and not inside(base, member.linkname):
            msg = f"{member.name}: a link that leads outside the tree"
            raise UnsafeTreeError(msg)
        member.mode &= 0o755
    return members


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
    if must_exist and not Path(root).is_dir():
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


def remove(path: Path) -> str:
    """Remove a tree; the first error, or an empty string."""
    if not path.exists():
        return ""
    errors: list[str] = []
    if sys.version_info >= (3, 12):  # noqa: UP036 - older Pythons name the callback onerror
        shutil.rmtree(path, onexc=lambda _func, name, exc: errors.append(f"{name}: {exc}"))
    else:
        shutil.rmtree(path, onerror=lambda _func, name, exc: errors.append(f"{name}: {exc[1]}"))
    return errors[0] if errors else ""


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
            elif error := remove(path):
                sys.stderr.write(f"safe_extract: could not remove {path}: {error}\n")
                failed = 1
        except (OSError, UnsafeTreeError) as exc:
            sys.stderr.write(f"safe_extract: refusing {path}: {exc}; removing it\n")
            if error := remove(path):
                sys.stderr.write(f"safe_extract: could not remove {path}: {error}\n")
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
            if hasattr(tarfile, "data_filter"):
                tf.extractall(dest, filter="data")
            else:
                tf.extractall(dest, members=legacy_filter(tf))  # noqa: S202 - checked by legacy_filter
        check_tree(dest)
    except (tarfile.TarError, OSError, UnsafeTreeError) as exc:
        sys.stderr.write(f"safe_extract: refusing {archive.name}: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
