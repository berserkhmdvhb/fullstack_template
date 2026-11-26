"""Copy the Next.js standalone server into a work dir, keeping pnpm's relative links."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def copy_standalone(bundle: Path, work: Path, *, static: Path, public: Path) -> None:
    """Copy ``bundle`` into ``work``, put the build's ``static`` (and ``public``, when it exists) in
    place, then check that every symlink in the copy resolves inside the copy.

    The links are copied as links: ``ignore_dangling_symlinks`` checks a relative link against the
    process's cwd, so it would drop every pnpm ``node_modules/<pkg> -> .pnpm/...`` link. The check
    runs after the replacement because Next can bake ``.next/static`` as an absolute link to the
    build host; any other link out of the copy still fails.
    """
    shutil.copytree(bundle, work, symlinks=True, dirs_exist_ok=True)
    shutil.rmtree(work / ".next" / "static", ignore_errors=True)
    (work / ".next" / "static").unlink(missing_ok=True)
    shutil.copytree(static, work / ".next" / "static")
    if public.is_dir():
        shutil.copytree(public, work / "public", dirs_exist_ok=True)
    root = work.resolve()
    # os.walk does not descend into linked dirs, so each link is checked once.
    for parent, dirs, files in os.walk(work):
        for link in (
            Path(parent, name) for name in dirs + files if Path(parent, name).is_symlink()
        ):
            target = link.resolve()
            inside = target.exists() and target.is_relative_to(root)
            assert inside, f"{link} -> {link.readlink()} does not resolve inside the copy"
