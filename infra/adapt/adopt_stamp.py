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
import json
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
# The GitHub repository in the SOURCE_URL default of every Dockerfile (the OCI `source` label).
OLD_REPOSITORY: Final[str] = "your-org/your-repo"
# The CODEOWNERS owner placeholder. GitHub accepts a user, a team or an email there, never a bare
# organisation, so it takes CODEOWNER (adopt.conf), not the repository's owner.
OLD_OWNER: Final[str] = "@" + OLD_REPOSITORY.split("/", maxsplit=1)[0]
CODEOWNERS: Final[str] = ".github/CODEOWNERS"
# A user @name, a team @org/team, or an email (no backslash: the value is a re.sub replacement).
_CODEOWNER: Final = re.compile(r"@[A-Za-z0-9-]+(/[A-Za-z0-9_.-]+)?|[^@\s\\]+@[^@\s\\]+\.[^@\s\\]+")
# README.md is excluded from the rewrites (it explains the placeholders), but its badges carry
# the repository.
REPOSITORY_ALSO: Final[str] = "README.md"
# The whole placeholder URL; scripts/ci/image-checks.sh reads it to refuse the template source.
OLD_SOURCE_URL: Final[str] = f"https://github.com/{OLD_REPOSITORY}"
# The organisation name in the image labels and Chart.yaml, stamped with the repository's owner.
OLD_VENDOR: Final[str] = "YourOrg"
_REPOSITORY: Final = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
# The template release an adopted copy was made from, written once at adoption. Its source is
# release-please's manifest (the last release the copy contains; tags are v<version>), read here
# because the adopter's own releases take that manifest over (wiki/template-upgrades.md).
TEMPLATE_VERSION: Final[str] = ".template-version"
RELEASE_MANIFEST: Final[str] = ".release-please-manifest.json"
RELEASE_CONFIG: Final[str] = "release-please-config.json"
# The adopter's own version line starts here once the template version is recorded.
# With the manifest at 0.0.0 release-please sees no previous release and uses `initial-version` from
# release-please-config.json (0.1.0): the first release is v0.1.0 whatever the commit types.
FIRST_LINE: Final[str] = "0.0.0"


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


def build_patterns(
    domain: str, registry: str, repository: str = "", codeowner: str = ""
) -> list[tuple[re.Pattern[str], str]]:
    """The fixed old->new substitutions, registry first (see module docstring).

    The repository (``owner/name``) and the code owner are optional: unset, the placeholder stays.
    """
    patterns = [
        (re.compile(rf"\b{re.escape(OLD_REGISTRY)}\b"), registry),
        (re.compile(rf"\b{re.escape(OLD_DOMAIN)}\b"), domain),
    ]
    # The hosts with their dots escaped, as a test's regex names them (api\.your-domain\.example).
    patterns += [
        (re.compile(rf"\b{re.escape(old.replace('.', r'\.'))}\b"), new.replace(".", r"\."))
        for old, new in ((OLD_REGISTRY, registry), (OLD_DOMAIN, domain))
    ]
    if repository:
        patterns += repository_patterns(repository)
    if codeowner:
        patterns.append((re.compile(rf"(?<![\w.-]){re.escape(OLD_OWNER)}(?![\w./-])"), codeowner))
    return patterns


def repository_patterns(repository: str) -> list[tuple[re.Pattern[str], str]]:
    """``your-org/your-repo`` -> the repository; ``YourOrg`` (the image vendor and author labels,
    the chart maintainer) -> its owner."""
    return [
        (re.compile(rf"(?<![\w.-]){re.escape(OLD_REPOSITORY)}(?![\w.-])"), repository),
        (re.compile(rf"\b{OLD_VENDOR}\b"), repository.split("/", maxsplit=1)[0]),
    ]


def stamp_template_version(root: Path, *, dry: bool) -> None:
    """Write ``v<manifest version>`` to .template-version once; an existing record is kept."""
    target = root / TEMPLATE_VERSION
    if target.exists():
        recorded = target.read_text(encoding="utf-8").strip()
        print(f"[adopt] {TEMPLATE_VERSION} already records {recorded}")
        return
    try:
        version = json.loads((root / RELEASE_MANIFEST).read_text(encoding="utf-8"))["."]
    except (OSError, ValueError, KeyError, TypeError):
        print(
            f"[adopt] NOTE: no version in {RELEASE_MANIFEST}, so {TEMPLATE_VERSION} is not written."
        )
        return
    if dry:
        print(f"[adopt] Would write {TEMPLATE_VERSION}: v{version}")
        print(f"[adopt] Would reset {RELEASE_MANIFEST} to {FIRST_LINE}")
        return
    target.write_text(f"v{version}\n", encoding="utf-8")
    print(f"[adopt] Wrote {TEMPLATE_VERSION}: v{version}")
    reset_release_line(root)


def reset_release_line(root: Path) -> None:
    """Start the adopter's own releases: the manifest, and the version file if one exists, go to
    FIRST_LINE. Only ever called right after the record is first written, so it never runs twice."""
    manifest = root / RELEASE_MANIFEST
    manifest.write_text(json.dumps({".": FIRST_LINE}, indent=2) + "\n", encoding="utf-8")
    print(f"[adopt] Reset {RELEASE_MANIFEST} to {FIRST_LINE}: your releases start their own line")
    try:
        name = json.loads((root / RELEASE_CONFIG).read_text(encoding="utf-8"))["version-file"]
    except (OSError, ValueError, KeyError, TypeError):
        return
    version_file = root / str(name)
    if version_file.is_file():
        version_file.write_text(f"{FIRST_LINE}\n", encoding="utf-8")
        print(f"[adopt] Reset {name} to {FIRST_LINE}")


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
    parser.add_argument(
        "--repository",
        default="",
        help="GitHub repository as owner/name (replaces your-org/your-repo in the image "
        "source label, the README badges and the governance files); unset keeps the placeholder",
    )
    parser.add_argument(
        "--codeowner",
        default="",
        help="The CODEOWNERS owner: a user @name, a team @org/team or an email (replaces "
        "@your-org); unset keeps the placeholder",
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
    if args.repository and not _REPOSITORY.fullmatch(args.repository):
        print(
            f"[adopt] ERROR: --repository must be owner/name (got {args.repository!r})",
            file=sys.stderr,
        )
        return 2
    if args.codeowner and not _CODEOWNER.fullmatch(args.codeowner):
        print(
            "[adopt] ERROR: --codeowner must be a user @name, a team @org/team or an email "
            f"(got {args.codeowner!r})",
            file=sys.stderr,
        )
        return 2

    include_exts: set[str] = {e.strip().lower() for e in args.include_exts.split(",") if e.strip()}
    excludes: set[str] = (
        {e.strip() for e in args.exclude_dirs.split(",") if e.strip()}
        if args.exclude_dirs
        else load_excludes()
    )
    config = _Config(include_exts, excludes, Path(args.backup_dir), dry=args.dry_run)
    root: Path = Path(args.root) if args.root else Path.cwd()

    patterns = build_patterns(args.domain, args.registry, args.repository, args.codeowner)
    changed = rewrite_tree(root=root, patterns=patterns, config=config)
    if args.repository:
        config.excludes = excludes - {REPOSITORY_ALSO}
        changed += [
            f
            for f in rewrite_tree(root, repository_patterns(args.repository), config)
            if f not in changed
        ]

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
    stamp_template_version(root, dry=args.dry_run)
    owners = root / CODEOWNERS
    if not args.codeowner and owners.is_file() and OLD_OWNER in owners.read_text(encoding="utf-8"):
        print(
            f"[adopt] NOTE: CODEOWNER is unset, so {CODEOWNERS} keeps the placeholder {OLD_OWNER}. "
            "Set CODEOWNER in adopt.conf (a user @name, a team @org/team or an email) and re-run."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
