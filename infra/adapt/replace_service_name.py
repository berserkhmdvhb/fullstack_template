#!/usr/bin/env python3
# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Template adaptation: replace service name

This script renames the src/<old> package directory to src/<new>, performs
regex-based replacements across the repository for entries referencing the
old package name, and writes backups + a manifest when changes occur.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

# Runnable both as a script (`python3 infra/adapt/replace_service_name.py`, which
# the .sh wrapper uses) and as a module (`python3 -m infra.adapt.replace_service_name`).
# `infra/` is not a package, so a plain relative import would fail when executed by
# path -- put this directory on sys.path before importing the sibling `_common`.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    DEFAULT_INCLUDE_EXTS,
    VALID_NAME_RE,
    backup_file,
    detect_images,
    load_excludes,
    os_walk_safe,
    rewrite_images_block,
    rewrite_services_block,
    rewrite_tree,
)


class AdaptConfig:
    """Configuration bundle to handle args in calls."""

    def __init__(
        self,
        include_exts: set[str],
        excludes: set[str],
        backup: Path,
        *,
        dry: bool,
    ) -> None:
        self.include_exts = include_exts
        self.excludes = excludes
        self.backup = backup
        self.dry = dry


class ServiceNameConfig:
    """Service-name replacement inputs."""

    def __init__(self, old: str, new: str) -> None:
        self.old = old
        self.new = new


# ----------------------------
# Filesystem helpers and guards
# ----------------------------


def detect_old_name(root: Path) -> str:
    """Try to infer the current package name from pyproject or src/."""
    # 1) project name in pyproject.toml
    pp = root / "pyproject.toml"
    if pp.is_file():
        try:
            with pp.open(encoding="utf-8") as f:
                for line in f:
                    m = re.search(r'^\s*name\s*=\s*"([^"]+)"', line)
                    if m and re.match(r"^[a-z][a-z0-9_]*$", m.group(1)):
                        return m.group(1)
        except OSError as exc:
            print(f"[adapt] WARNING: failed reading {pp}: {exc}", file=sys.stderr)

    # 2) first plausible src/<pkg> directory
    src = root / "src"
    if src.is_dir():
        try:
            for child in sorted(src.iterdir()):
                if child.is_dir() and re.match(r"^[a-z][a-z0-9_]*$", child.name):
                    return child.name
        except OSError as exc:
            print(f"[adapt] WARNING: failed listing {src}: {exc}", file=sys.stderr)

    return "your_service_name"


# ----------------------------
# Patterns builder
# ----------------------------


def _patterns_frontend_package_json(cfg: ServiceNameConfig) -> list[tuple[re.Pattern[str], str]]:
    """Handle frontend/package.json name field and related entries."""
    old, new = cfg.old, cfg.new
    pats: list[tuple[re.Pattern[str], str]] = []
    # "name": "your_service_name" in frontend/package.json
    pats.append(
        (
            re.compile(rf'("name"\s*:\s*"){re.escape(old)}(")'),
            r"\g<1>" + new + r"\g<2>",
        )
    )
    return pats


def _patterns_frontend_next_config(cfg: ServiceNameConfig) -> list[tuple[re.Pattern[str], str]]:
    """Handle next.config.ts references to service name."""
    old, new = cfg.old, cfg.new
    pats: list[tuple[re.Pattern[str], str]] = []
    # Any string literal referencing the old service name in next.config.ts
    pats.append(
        (
            re.compile(rf'(["\']){re.escape(old)}(["\'])'),
            r"\g<1>" + new + r"\g<2>",
        )
    )
    return pats


def _patterns_run_scripts_defaults(cfg: ServiceNameConfig) -> list[tuple[re.Pattern[str], str]]:
    """Match and update defaulted entries inside run_* scripts."""
    old, new = cfg.old, cfg.new
    pats: list[tuple[re.Pattern[str], str]] = []
    # BACKEND_ENTRY="${BACKEND_ENTRY:-<old>.backend.main:app}" -> <new>...
    pats.append(
        (
            re.compile(
                rf'BACKEND_ENTRY="\$\{{BACKEND_ENTRY:-{re.escape(old)}\.backend\.main:app\}}"'
            ),
            f'BACKEND_ENTRY="${{BACKEND_ENTRY:-{new}.backend.main:app}}"',
        )
    )
    return pats


def _patterns_entrypoint_defaults(cfg: ServiceNameConfig) -> list[tuple[re.Pattern[str], str]]:
    """Match and update defaulted entries inside infra/runtime/entrypoint.sh."""
    old, new = cfg.old, cfg.new
    pats: list[tuple[re.Pattern[str], str]] = []
    pats.append(
        (
            re.compile(
                rf"BACKEND_ENTRY=\$\{{BACKEND_ENTRY:-{re.escape(old)}\.backend\.main:app\}}"
            ),
            f"BACKEND_ENTRY=${{BACKEND_ENTRY:-{new}.backend.main:app}}",
        )
    )
    return pats


def _patterns_ci(cfg: ServiceNameConfig) -> list[tuple[re.Pattern[str], str]]:
    old, new = cfg.old, cfg.new
    pats: list[tuple[re.Pattern[str], str]] = []
    # CI variables (quoted + unquoted)
    pats.append(
        (
            re.compile(
                rf'(^\s*BACKEND_ENTRY\s*:\s*")({re.escape(old)}\.backend\.main:app)(".*$)',
                re.MULTILINE,
            ),
            r"\g<1>" + f"{new}.backend.main:app" + r"\g<3>",
        )
    )
    pats.append(
        (
            re.compile(rf"\bBACKEND_ENTRY:\s*{re.escape(old)}\.backend\.main:app\b"),
            f"BACKEND_ENTRY: {new}.backend.main:app",
        )
    )
    return pats


def _patterns_build_scripts(cfg: ServiceNameConfig) -> list[tuple[re.Pattern[str], str]]:
    """Patterns for build scripts (Docker/Podman/Apptainer) covering:
    - Variable defaults: IMAGE_NAME / CONTAINER_NAME using ${VAR:-...} style
    - Non-default assignments (legacy forms)
    - Usage help text lines (default values shown to users)
    - Apptainer SIF/IMAGE names (if present)
    """
    old, new = cfg.old, cfg.new
    pats: list[tuple[re.Pattern[str], str]] = []

    # --- Apptainer (keep legacy simple assignments if present) ---
    # SIF_NAME="<old>.sif"  --> "<new>.sif"
    pats.append((re.compile(rf'SIF_NAME="{re.escape(old)}\.sif"'), f'SIF_NAME="{new}.sif"'))
    # IMAGE_NAME="<old>"    --> "<new>"
    pats.append((re.compile(rf'IMAGE_NAME="{re.escape(old)}"'), f'IMAGE_NAME="{new}"'))
    # CONTAINER_NAME="<old>_container" --> "<new>_container"
    pats.append(
        (
            re.compile(rf'CONTAINER_NAME="{re.escape(old)}_container"'),
            f'CONTAINER_NAME="{new}_container"',
        )
    )

    # --- Docker/Podman: env-default style assignments ---
    # IMAGE_NAME="${IMAGE_NAME:-<old>}" --> "${IMAGE_NAME:-<new>}"
    pats.append(
        (
            re.compile(rf'IMAGE_NAME="\$\{{IMAGE_NAME:-{re.escape(old)}\}}"'),
            f'IMAGE_NAME="${{IMAGE_NAME:-{new}}}"',
        )
    )
    # CONTAINER_NAME="${CONTAINER_NAME:-<old>_container}" --> "${CONTAINER_NAME:-<new>_container}"
    pats.append(
        (
            re.compile(rf'CONTAINER_NAME="\$\{{CONTAINER_NAME:-{re.escape(old)}_container\}}"'),
            f'CONTAINER_NAME="${{CONTAINER_NAME:-{new}_container}}"',
        )
    )

    # --- Usage text in build scripts (Docker & Podman) ---
    # ... Image name (default: <old>) --> (default: <new>)
    pats.append(
        (
            re.compile(rf"Image name \(default: {re.escape(old)}\)"),
            f"Image name (default: {new})",
        )
    )
    # ... Container name (default: <old>_container) --> (default: <new>_container)
    pats.append(
        (
            re.compile(rf"Container name \(default: {re.escape(old)}_container\)"),
            f"Container name (default: {new}_container)",
        )
    )
    # Handle comments with default values
    pats.append(
        (
            re.compile(rf"#\s*-n/--name\s+IMAGE_NAME\s+\(default:\s+{re.escape(old)}\)"),
            f"#   -n/--name        IMAGE_NAME      (default: {new})",
        )
    )

    # --- SETUPTOOLS_SCM_PRETEND_VERSION_FOR_YOUR_SERVICE_NAME in Dockerfile ---
    old_upper = old.upper()
    new_upper = new.upper()
    pats.append(
        (
            re.compile(rf"SETUPTOOLS_SCM_PRETEND_VERSION_FOR_{re.escape(old_upper)}"),
            f"SETUPTOOLS_SCM_PRETEND_VERSION_FOR_{new_upper}",
        )
    )
    # Also handle the comment reference
    pats.append(
        (
            re.compile(rf"SETUPTOOLS_SCM_PRETEND_VERSION_FOR_{re.escape(old_upper)},"),
            f"SETUPTOOLS_SCM_PRETEND_VERSION_FOR_{new_upper},",
        )
    )

    return pats


def _patterns_python_paths(cfg: ServiceNameConfig) -> list[tuple[re.Pattern[str], str]]:
    old, new = cfg.old, cfg.new
    pats: list[tuple[re.Pattern[str], str]] = []
    pats.append((re.compile(rf"\b{re.escape(old)}\."), f"{new}."))
    pats.append((re.compile(rf"src/{re.escape(old)}/"), f"src/{new}/"))
    # Pattern for __version__ = _pkg_version("your_service_name")
    pats.append((re.compile(rf'_pkg_version\("{re.escape(old)}"\)'), f'_pkg_version("{new}")'))
    return pats


def _patterns_python_imports(cfg: ServiceNameConfig) -> list[tuple[re.Pattern[str], str]]:
    """Handle Python import statements like 'from your_service_name import ...'"""
    old, new = cfg.old, cfg.new
    pats: list[tuple[re.Pattern[str], str]] = []
    # Pattern for "from your_service_name import" statements
    pats.append((re.compile(rf"from\s+{re.escape(old)}\s+import"), f"from {new} import"))
    return pats


def _patterns_pyproject_and_sonar(cfg: ServiceNameConfig) -> list[tuple[re.Pattern[str], str]]:
    old, new = cfg.old, cfg.new
    pats: list[tuple[re.Pattern[str], str]] = []
    pats.append(
        (re.compile(rf'^\s*name\s*=\s*"{re.escape(old)}"', re.MULTILINE), f'name = "{new}"')
    )
    pats.append(
        (re.compile(rf'include\s*=\s*\[\s*"{re.escape(old)}\*"\s*\]'), f'include = ["{new}*"]')
    )
    pats.append((re.compile(rf"Description of {re.escape(old)}"), f"Description of {new}"))
    pats.append(
        (re.compile(r'^(\s*projectName\s*=\s*)"[^"]+"', re.MULTILINE), r"\g<1>" + f'"{new}"')
    )
    # Pattern for importlib.metadata.version("your_service_name")
    pats.append((re.compile(rf'im\.version\("{re.escape(old)}"\)'), f'im.version("{new}")'))
    # Pattern for package-data entry in pyproject.toml
    pats.append(
        (
            re.compile(rf'^\s*{re.escape(old)}\s*=\s*\["py\.typed"\]', re.MULTILINE),
            f'{new} = ["py.typed"]',
        )
    )
    return pats


def _patterns_makefile(cfg: ServiceNameConfig) -> list[tuple[re.Pattern[str], str]]:
    old, new = cfg.old, cfg.new
    pats: list[tuple[re.Pattern[str], str]] = []
    pats.append(
        (
            re.compile(rf"^(\s*SERVICE\s*:?\s*=\s*){re.escape(old)}\b", re.MULTILINE),
            r"\g<1>" + f"{new}",
        )
    )
    return pats


def _patterns_yaml_config_values(cfg: ServiceNameConfig) -> list[tuple[re.Pattern[str], str]]:
    """Handle generic YAML/TOML-style template config values.

    Covers:
    - .github/container-images.yml: package_name: your_service_name
    - infra/security/config.yaml: - src/your_service_name
    - infra/security/config.yaml: match: your-service-name
    """
    old, new = cfg.old, cfg.new
    old_slug = old.replace("_", "-")
    new_slug = new.replace("_", "-")

    pats: list[tuple[re.Pattern[str], str]] = []

    # package_name --> your_service_name
    pats.append(
        (
            re.compile(
                rf"^(\s*package_name\s*:\s*){re.escape(old)}\s*$",
                re.MULTILINE,
            ),
            r"\g<1>" + new,
        )
    )

    # YAML list/path value: - src/your_service_name
    pats.append(
        (
            re.compile(
                rf"^(\s*-\s*)src/{re.escape(old)}\s*$",
                re.MULTILINE,
            ),
            r"\g<1>" + f"src/{new}",
        )
    )

    # Generic src/your_service_name path with no trailing slash
    # Covers values like:
    #   path --> src/your_service_name
    #   - src/your_service_name
    pats.append(
        (
            re.compile(rf"\bsrc/{re.escape(old)}\b"),
            f"src/{new}",
        )
    )

    # Hyphenated service slug:
    # your-service-name -> awesome-solution
    pats.append(
        (
            re.compile(rf"\b{re.escape(old_slug)}\b"),
            new_slug,
        )
    )

    return pats


def build_patterns(old: str, new: str) -> list[tuple[re.Pattern[str], str]]:
    """Aggregate all pattern builders; low branch count and low complexity."""
    cfg = ServiceNameConfig(old=old, new=new)
    pats: list[tuple[re.Pattern[str], str]] = []
    pats += _patterns_run_scripts_defaults(cfg)
    pats += _patterns_entrypoint_defaults(cfg)
    pats += _patterns_ci(cfg)
    pats += _patterns_build_scripts(cfg)
    pats += _patterns_python_paths(cfg)
    pats += _patterns_python_imports(cfg)
    pats += _patterns_pyproject_and_sonar(cfg)
    pats += _patterns_makefile(cfg)
    pats += _patterns_yaml_config_values(cfg)
    pats += _patterns_frontend_package_json(cfg)
    pats += _patterns_frontend_next_config(cfg)
    return pats


def rename_package_dir(root: Path, old: str, new: str, backup: Path, *, dry: bool) -> None:
    """Rename src/old → src/new with backup (merge-safe)."""
    src_old = root / "src" / old
    src_new = root / "src" / new
    if not src_old.is_dir():
        return

    print(f"[adapt] Renaming directory src/{old} -> src/{new}")
    if dry:
        return

    backup_dir = backup / "src" / old
    try:
        shutil.copytree(src_old, backup_dir, dirs_exist_ok=True)
    except OSError as exc:
        print(f"[adapt:backup] WARNING: backup src/{old} failed: {exc}", file=sys.stderr)

    if src_new.exists():
        # merge into existing target
        for dirpath, _, files in os_walk_safe(src_old):
            rel = dirpath.relative_to(src_old)
            dst_dir = src_new / rel
            dst_dir.mkdir(parents=True, exist_ok=True)
            for fn in files:
                try:
                    shutil.move(str(dirpath / fn), str(dst_dir / fn))
                except (OSError, shutil.Error) as exc:
                    print(f"[adapt] WARNING: move failed for {fn}: {exc}", file=sys.stderr)
        shutil.rmtree(src_old)
    else:
        try:
            src_old.rename(src_new)
        except OSError as exc:
            print(f"[adapt] WARNING: rename src/{old} -> src/{new} failed: {exc}", file=sys.stderr)


def rename_chart_dir(root: Path, old: str, new: str, backup: Path, *, dry: bool) -> None:
    """Rename charts/<old-slug>/ -> charts/<new-slug>/ with backup (merge-safe).

    The Helm chart lives under the hyphenated slug, so the slug -- not the
    underscore package name -- is what moves here. The chart's internal ``name:``
    (Chart.yaml) is a text rewrite handled by the tree walk's slug pattern, so
    this step only moves the directory. Absent charts/ (already adapted, or a
    tree without the chart) is a no-op -- chart wiring is optional like images.
    """
    old_slug = old.replace("_", "-")
    new_slug = new.replace("_", "-")
    charts_old = root / "charts" / old_slug
    charts_new = root / "charts" / new_slug
    if not charts_old.is_dir():
        return

    print(f"[adapt] Renaming chart directory charts/{old_slug} -> charts/{new_slug}")
    if dry:
        return

    backup_dir = backup / "charts" / old_slug
    try:
        shutil.copytree(charts_old, backup_dir, dirs_exist_ok=True)
    except OSError as exc:
        print(f"[adapt:backup] WARNING: backup charts/{old_slug} failed: {exc}", file=sys.stderr)

    if charts_new.exists():
        # merge into an existing target of the same slug
        for dirpath, _, files in os_walk_safe(charts_old):
            rel = dirpath.relative_to(charts_old)
            dst_dir = charts_new / rel
            dst_dir.mkdir(parents=True, exist_ok=True)
            for fn in files:
                try:
                    shutil.move(str(dirpath / fn), str(dst_dir / fn))
                except (OSError, shutil.Error) as exc:
                    print(f"[adapt] WARNING: move failed for {fn}: {exc}", file=sys.stderr)
        shutil.rmtree(charts_old)
    else:
        try:
            charts_old.rename(charts_new)
        except OSError as exc:
            print(
                f"[adapt] WARNING: rename charts/{old_slug} -> {new_slug} failed: {exc}",
                file=sys.stderr,
            )


# ----------------------------
# Image manifest wiring
# ----------------------------


def rewire_images(root: Path, config: AdaptConfig, new_name: str) -> int:
    """Regenerate the two manifests' image blocks from the root Dockerfiles.

    Keeps the build manifest (``.github/container-images.yml`` ``images:``) and the scan
    manifest (``infra/security/config.yaml`` ``image.services:``) mirrored to the
    actual root ``Dockerfile*`` set, so adding or removing an image never leaves
    the manifests stale. The image id is the Dockerfile filename suffix (a bare
    ``Dockerfile`` takes ``new_name``), so this step is name-independent and
    commutes with the service-name rewrite -- it runs after it and picks up the
    new name for any bare Dockerfile. Returns the image count wired (0 only when
    the blocks are already canonical).
    """
    try:
        images = detect_images(root, new_name)
    except ValueError as exc:
        # No root Dockerfile: image wiring is optional, so skip it rather than
        # fail the whole rename.
        print(f"[adapt] Skipping image rewire: {exc}")
        return 0

    ids = ", ".join(image_id for image_id, _ in images)

    ci_path = root / ".github" / "container-images.yml"
    sec_path = root / "infra" / "security" / "config.yaml"
    ci_text = ci_path.read_text(encoding="utf-8")
    sec_text = sec_path.read_text(encoding="utf-8")
    new_ci = rewrite_images_block(ci_text, images)
    new_sec = rewrite_services_block(sec_text, images)

    if new_ci == ci_text and new_sec == sec_text:
        print(f"[adapt] Images already wired ({len(images)} image(s): {ids}); nothing to do.")
        return 0

    if config.dry:
        print(f"[adapt] Would rewire {len(images)} image(s) ({ids}) in:")
        print(f"  - {ci_path.relative_to(root)}")
        print(f"  - {sec_path.relative_to(root)}")
        return len(images)

    backup_file(config.backup, str(ci_path.relative_to(root)), ci_path)
    backup_file(config.backup, str(sec_path.relative_to(root)), sec_path)
    ci_path.write_text(new_ci, encoding="utf-8")
    sec_path.write_text(new_sec, encoding="utf-8")
    print(f"[adapt] Rewired {len(images)} image(s) ({ids}) in:")
    print(f"  - {ci_path.relative_to(root)}")
    print(f"  - {sec_path.relative_to(root)}")
    return len(images)


# ----------------------------
# CLI
# ----------------------------

# Valid service name: lowercase identifier (shared rule, imported from _common).


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Adapt template: replace service name")
    parser.add_argument(
        "--new", required=True, help="New package name (lowercase, [a-z][a-z0-9_]*)"
    )
    parser.add_argument("--old", default="", help="Old package name (auto-detected if empty)")
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

    new: str = args.new
    old: str | None = args.old or None
    dry: bool = args.dry_run

    include_exts: set[str] = {e.strip().lower() for e in args.include_exts.split(",") if e.strip()}
    exclude_dirs: set[str] = (
        {e.strip() for e in args.exclude_dirs.split(",") if e.strip()}
        if args.exclude_dirs
        else load_excludes()
    )
    backup: Path = Path(args.backup_dir)

    # Validate new name
    if not VALID_NAME_RE.match(new):
        print(
            f"❌ Invalid --new '{new}' "
            "(use lowercase letters, digits, underscores; start with a letter)",
            file=sys.stderr,
        )
        return 2

    # Detect old name if not provided
    root: Path = Path(args.root) if args.root else Path.cwd()
    if not old:
        old = detect_old_name(root)

    # No-op case
    if old == new:
        print(f"[adapt] OLD_NAME equals NEW_NAME ('{new}'); nothing to do.")
        return 0

    print(f"[adapt] OLD_NAME={old} -> NEW_NAME={new}")

    # Build config bundle (reduces arg count in downstream calls)
    config = AdaptConfig(
        include_exts=include_exts,
        excludes=exclude_dirs,
        backup=backup,
        dry=dry,
    )

    # 1) Directory renames with backup (Python package + Helm chart slug)
    rename_package_dir(root, old, new, backup, dry=dry)
    rename_chart_dir(root, old, new, backup, dry=dry)

    # 2) Regex-based rewrites
    # (build_patterns already aggregates helpers; keeping old/new call is fine)
    patterns = build_patterns(old, new)
    changed = rewrite_tree(root=root, patterns=patterns, config=config)

    # 3) Wire the image manifests to the root Dockerfiles (name-independent; runs
    #    after the rewrite so a bare Dockerfile takes the new name).
    rewire_images(root, config, new)

    # Report
    print("\n[adapt] Files changed (service name):")
    for f in changed:
        print(f" - {f}")
    print(f"\n[adapt] Total: {len(changed)} file(s)")

    # Manifest
    if not dry and changed:
        try:
            mf = backup / "manifest.txt"
            mf.write_text("\n".join(changed) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"[adapt:backup] WARNING: write manifest failed: {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
