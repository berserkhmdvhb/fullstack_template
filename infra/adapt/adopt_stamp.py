"""Adopt a forked copy of the template — stamp the deployment coordinates.

Complements :mod:`replace_service_name`, which rewrites the *service name*.
Adoption has two independent axes:

  * identity    -- the service name (package, image-name suffix, k8s name
    prefix, ``BACKEND_ENTRY``, CI ``package_name``). Handled by
    ``replace_service_name.py``.
  * deployment  -- where the images live (the registry) and the ingress base
    domain. Handled here.

Both are driven from the same ``adopt.conf`` by the thin wrapper ``adopt.sh``,
so one edit applies everywhere. The stamping reuses the shared ``rewrite_tree``
engine (``_common.py``) -- the same backup + atomic-replace + dry-run semantics
as the name adapter -- so the two steps behave identically and a dry-run of one
covers both.

The placeholder tokens (``your-domain.example`` / ``registry.your-domain.example``) are the
template defaults: fixed strings, so the patterns are fixed old->new. The
registry pattern runs first because ``registry.your-domain.example`` contains
``your-domain.example`` (running the bare-domain pass first would leave
``registry.<DOMAIN>`` half-substituted).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Final

# The shared rewrite engine lives in this directory; ``infra/`` is not a package,
# so put this dir on sys.path before importing it (same trick as the name adapter).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import DEFAULT_INCLUDE_EXTS, load_excludes, rewrite_tree

OLD_DOMAIN: Final[str] = "your-domain.example"
OLD_REGISTRY: Final[str] = "registry.your-domain.example"


class _Config:
    """Duck-typed sibling of ``replace_service_name.AdaptConfig`` for ``rewrite_tree``.

    ``rewrite_tree`` only reads ``include_exts`` / ``excludes`` / ``backup`` /
    ``dry`` (see ``_common``), so this carries exactly those -- no shared import
    to keep the two adapters decoupled.
    """

    def __init__(
        self, include_exts: set[str], excludes: set[str], backup: Path, *, dry: bool
    ) -> None:
        self.include_exts = include_exts
        self.excludes = excludes
        self.backup = backup
        self.dry = dry


def build_patterns(domain: str, registry: str) -> list[tuple[re.Pattern[str], str]]:
    """The two fixed old->new substitutions, registry first (see module docstring)."""
    return [
        (re.compile(rf"\b{re.escape(OLD_REGISTRY)}\b"), registry),
        (re.compile(rf"\b{re.escape(OLD_DOMAIN)}\b"), domain),
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Adopt template: stamp domain + registry")
    parser.add_argument(
        "--domain", required=True, help="New base domain (replaces your-domain.example)"
    )
    parser.add_argument(
        "--registry",
        required=True,
        help="New image registry (replaces registry.your-domain.example)",
    )
    parser.add_argument("--root", default=None, help="Repo root (default: the current directory)")
    parser.add_argument("--dry-run", action="store_true", help="Show changes only")
    parser.add_argument(
        "--include-exts",
        default=DEFAULT_INCLUDE_EXTS,
        help="Comma-separated list of file extensions to edit",
    )
    parser.add_argument(
        "--exclude-dirs",
        default=None,
        help="Comma-separated paths (relative) to skip (default: infra/adapt/excludes.txt)",
    )
    parser.add_argument("--backup-dir", required=True, help="Backup base directory")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    include_exts: set[str] = {e.strip().lower() for e in args.include_exts.split(",") if e.strip()}
    excludes: set[str] = (
        {e.strip() for e in args.exclude_dirs.split(",") if e.strip()}
        if args.exclude_dirs
        else load_excludes()
    )
    config = _Config(include_exts, excludes, Path(args.backup_dir), dry=args.dry_run)
    root: Path = Path(args.root) if args.root else Path.cwd()

    patterns = build_patterns(args.domain, args.registry)
    changed = rewrite_tree(root=root, patterns=patterns, config=config)

    if not changed:
        print(
            "[adopt] Domain/registry already canonical "
            "(no your-domain.example / registry.your-domain.example left); nothing to do."
        )
    else:
        print("[adopt] Domain/registry stamped in:")
        for relf in changed:
            print(f" - {relf}")
    print(f"\n[adopt] Total: {len(changed)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
