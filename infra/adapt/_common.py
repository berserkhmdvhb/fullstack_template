"""Shared filesystem helpers and rewrite engine for the adapt scripts.

This module centralises the duplicated utilities used by
``replace_service_name.py`` so that any bug fix or behavioural change only
needs to be made once.  The public API is:

- :func:`VALID_NAME_RE`   -- lowercase-identifier rule (service name / image id)
- :func:`norm_rel`        -- normalise a ``Path`` to a POSIX relative string
- :func:`is_excluded`     -- check whether a relative path falls under an
  exclusion set
- :func:`os_walk_safe`    -- ``os.walk`` wrapper yielding ``Path`` objects
- :func:`iter_files`      -- iterate editable files honouring excludes
- :func:`should_edit`     -- decide whether a filename matches the include
  extensions (with special cases for ``Dockerfile`` and ``Makefile``)
- :func:`rewrite_tree`    -- apply a list of ``(regex, replacement)`` pairs
  across the tree, with backup + atomic replace semantics
- image-block helpers    -- detect the root Dockerfiles and rewire (keeping existing entries) the
  ``images:`` block (.github/container-images.yml) and the ``services:`` block
  (infra/security/config.yaml) surgically, so the build/scan manifests mirror
  the actual Dockerfile set without a load->dump round-trip
"""

from __future__ import annotations

import re
import shutil
import sys
from collections.abc import Iterable
from os import walk
from pathlib import Path
from typing import Final, Protocol

# A valid service name / image id: it becomes an env var, a Dockerfile name and a
# repository suffix, so it must be a lowercase identifier. Shared by the name and
# image adapters so the rule lives once (the callers import it).
VALID_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]*$")

# ---------------------------------------------------------------------------
# Filesystem helpers & guards
# ---------------------------------------------------------------------------


def norm_rel(p: Path) -> str:
    """Return a POSIX-style relative path string with trailing slashes stripped."""
    return str(p.as_posix().rstrip("/"))


# The file types the adapters rewrite (the default of every --include-exts flag). .lock is uv.lock,
# which names the project; .snap is a helm-unittest snapshot, which names the chart's objects.
DEFAULT_INCLUDE_EXTS: Final = (
    ".py,.sh,.yml,.yaml,.toml,.cfg,.ini,.env,.def,.json,.json5,.ts,.tsx,.bats,.md,.mdx,.lock,.snap"
)
EXCLUDES_FILE: Final = Path(__file__).with_name("excludes.txt")


def load_excludes() -> set[str]:
    """Paths the adapters never rewrite: the lines of ``excludes.txt`` (``#`` comments skipped)."""
    lines = (ln.strip() for ln in EXCLUDES_FILE.read_text(encoding="utf-8").splitlines())
    return {ln for ln in lines if ln and not ln.startswith("#")}


def is_excluded(rel_path: Path, excludes: set[str]) -> bool:
    """Return ``True`` when *rel_path* matches (or is nested under) any exclusion."""
    rp = norm_rel(rel_path)
    for ex in excludes:
        exn = ex.rstrip("/")
        if rp == exn or rp.startswith(exn + "/"):
            return True
    return False


def os_walk_safe(root: Path) -> Iterable[tuple[Path, list[str], list[str]]]:
    """Yield ``(dirpath, dirnames, filenames)`` using ``os.walk``, paths as ``Path``."""
    for dirpath, dirnames, filenames in walk(root):
        yield Path(dirpath), dirnames, filenames


def iter_files(root: Path, excludes: set[str]) -> Iterable[tuple[str, Path]]:
    """Yield ``(rel_path_str, absolute_path)`` for files not under *excludes*."""
    for dirpath, dirnames, filenames in os_walk_safe(root):
        rel_dir = dirpath.relative_to(root)

        # prune excluded subdirs in place
        keep_dirs: list[str] = []
        for d in dirnames:
            cand = rel_dir / d if rel_dir != Path() else Path(d)
            if not is_excluded(cand, excludes):
                keep_dirs.append(d)
        dirnames[:] = keep_dirs

        for fn in filenames:
            full = dirpath / fn
            relf = full.relative_to(root)
            if is_excluded(relf, excludes):
                continue
            yield (str(relf.as_posix()), full)


def should_edit(name: str, include_exts: set[str]) -> bool:
    """Return ``True`` if the file should be edited based on its name/extension."""
    # Always edit these regardless of extension: the Makefile, and any Dockerfile
    # variant. A multi-image Dockerfile (Dockerfile.backend) has a suffix that is
    # NOT a real extension, so the extension filter below would wrongly skip it --
    # which would leave the build-script patterns (SETUPTOOLS_SCM_PRETEND_VERSION
    # _FOR_<NAME>, image-name defaults, …) dead for the very files they target.
    # A ".example" template (.env.example, values.secret.yaml.example) is judged by the name
    # under it; the dotfiles (and CODEOWNERS) have no usable suffix, so they are matched by name.
    name = name.removesuffix(".example")
    by_name = {"Makefile", ".env", ".prettierignore", "CODEOWNERS"}
    if name in by_name or name.startswith(("Dockerfile", ".env.")):
        return True
    # Otherwise filter by extension
    ext = Path(name).suffix.lower()
    return ext in include_exts


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------


def backup_file(backup: Path, relf: str, full: Path) -> None:
    """Copy ``full`` to ``backup/<relf>`` (mkdir parents); warn, never raise."""
    try:
        backup_path = backup / relf
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(full, backup_path)
    except OSError as exc:
        print(f"[adapt:backup] WARNING: backup {relf} failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Rewrite engine
# ---------------------------------------------------------------------------


class _RewriteConfig(Protocol):
    """The attributes :func:`rewrite_tree` reads from its config object."""

    include_exts: set[str]
    excludes: set[str]
    backup: Path
    dry: bool


def rewrite_tree(
    root: Path,
    patterns: list[tuple[re.Pattern[str], str]],
    config: _RewriteConfig,  # AdaptConfig -- duck-typed to avoid circular import
) -> list[str]:
    """Apply regex replacements across files matching ``include_exts`` (respecting ``excludes``).

    The *config* object must expose ``include_exts``, ``excludes``, ``backup``,
    and ``dry`` attributes (see ``AdaptConfig`` in the calling scripts).
    """
    changed: list[str] = []
    for relf, full in iter_files(root, config.excludes):
        name = Path(full).name
        if not should_edit(name, config.include_exts):
            continue

        try:
            text = Path(full).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"[adapt] WARNING: skipped {relf}: {exc}", file=sys.stderr)
            continue

        new_text = text
        for rx, rep in patterns:
            new_text = rx.sub(rep, new_text)

        if new_text != text:
            if not config.dry:
                backup_file(config.backup, relf, full)
                tmp_path = Path(str(full) + ".adapt.tmp")
                tmp_path.write_text(new_text, encoding="utf-8")
                shutil.copymode(full, tmp_path)  # a script stays executable
                try:
                    tmp_path.replace(full)
                except OSError as exc:
                    print(
                        f"[adapt] WARNING: replace failed for {relf}: {exc}",
                        file=sys.stderr,
                    )
            changed.append(relf)
    return changed


# ---------------------------------------------------------------------------
# Image-block rewriting
# ---------------------------------------------------------------------------
# The two DevSecOps manifests each carry a data block derived from the root
# Dockerfiles, and both must mirror the actual Dockerfile set or the build/scan
# manifests drift:
#   - .github/container-images.yml  -> top-level `images:` block (the build manifest)
#   - infra/security/config.yaml -> `image.services:` block (the scan manifest)
# Both are rewritten by surgical, line-based block replacement so the surrounding
# formatting and comments are preserved byte-for-byte -- unlike a load->dump
# round-trip. That matters because the image ids / <ID>_IMAGE env vars are a
# stable convention the frozen CI scripts depend on, so the ids are derived from
# the real Dockerfile paths rather than guessed. The image id is the Dockerfile
# filename suffix (the bare `Dockerfile` takes the service name), so this step is
# name-independent: it commutes with the service-name rewrite.


def _dockerfile_id(name: str, service_name: str) -> str:
    """Image id from a root Dockerfile's filename.

    ``Dockerfile.api`` -> ``api``; the bare ``Dockerfile`` (no suffix) takes the
    service name. The leading dot of ``Dockerfile.<id>`` is stripped. The
    Dockerfile filename itself is never modified -- it is the developer's source
    of truth.
    """
    suffix = name[len("Dockerfile") :]
    return suffix.lstrip(".") or service_name


def detect_images(root: Path, service_name: str) -> list[tuple[str, str]]:
    """Detect the root Dockerfiles and derive sorted ``(id, dockerfile)`` pairs.

    Globs ``Dockerfile*`` at ``root`` only (non-recursive, so e.g. an
    ``examples/`` demo Dockerfile is ignored). Each id is validated against
    ``VALID_NAME_RE``, duplicates are rejected, and the result is sorted by id for
    deterministic output. Raises ``ValueError`` (message carries "nothing to
    build") when no root Dockerfile is found, so the caller can skip the image
    step cleanly.
    """
    images: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path in sorted(root.glob("Dockerfile*")):
        if not path.is_file():
            continue
        image_id = _dockerfile_id(path.name, service_name)
        if not VALID_NAME_RE.match(image_id):
            msg = f"invalid image id '{image_id}' from {path.name} (use lowercase [a-z][a-z0-9_]*)"
            raise ValueError(msg)
        if image_id in seen:
            msg = f"duplicate image id '{image_id}' ({path.name})"
            raise ValueError(msg)
        seen.add(image_id)
        images.append((image_id, path.name))
    if not images:
        msg = "nothing to build: no root Dockerfile found"
        raise ValueError(msg)
    images.sort(key=lambda pair: pair[0])
    return images


def render_images_block(images: list[tuple[str, str]]) -> list[str]:
    """Canonical ``images:`` block lines for .github/container-images.yml.

    Each image is two-space list-indented with the fields the frozen CI build
    (the container_matrix.py helper) and the local runner (devsec.sh build) read. Used
    for a Dockerfile that has no entry yet; an existing entry is kept as it is
    (rewrite_images_block).
    """
    lines: list[str] = []
    for image_id, dockerfile in images:
        lines += [
            f"  - id: {image_id}",
            f'    repository: "ghcr.io/${{OWNER}}/${{REPO}}/{image_id}"',
            f"    dockerfile: {dockerfile}",
            "    context: .",
            "    dockerfile_context: .",
            "    build_args:",
            '      VERSION: "${VERSION}"',
        ]
    return lines


def render_services_block(images: list[tuple[str, str]]) -> list[str]:
    """Canonical ``image.services:`` block lines for infra/security/config.yaml.

    One entry per image, mirroring the image ids so the scan manifest can never
    drift from the build manifest (.github/container-images.yml). The ``<ID>_IMAGE`` /
    ``<ID>_IMAGE_DOCKER`` env vars the frozen CI scripts read are the upper-cased
    id.
    """
    lines: list[str] = []
    for image_id, _ in images:
        upper = image_id.upper()
        lines += [
            f"    - name: {image_id}",
            f"      env_var: {upper}_IMAGE",
            f"      docker_env_var: {upper}_IMAGE_DOCKER",
            f"      match: {image_id}",
        ]
    return lines


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _replace_block(
    lines: list[str],
    key: str,
    rendered: list[str],
    expected_indent: int | None = None,
) -> list[str]:
    r"""Splice ``rendered`` in for the child block of a top-level ``KEY:``.

    Finds the ``KEY:`` line (regex ``^(\s*)KEY:\s*(#.*)?$``, optionally at a fixed
    indent), then its child block: the contiguous following lines that are
    non-blank and indented deeper than the key. The block ends at the first blank
    line, a line at the key's indentation or shallower, or EOF. Everything else in
    the file (comments, other keys, blank lines) is left byte-for-byte, so a
    hand-edited manifest keeps its formatting -- unlike a load->dump round-trip.
    """
    key_re = re.compile(r"^(\s*)" + re.escape(key) + r":\s*(#.*)?$")
    k = None
    for i, line in enumerate(lines):
        m = key_re.match(line)
        if m and (expected_indent is None or len(m.group(1)) == expected_indent):
            k = i
            break
    if k is None:
        msg = f"key {key!r} not found"
        raise ValueError(msg)
    key_indent = _indent(lines[k])
    j = k + 1
    while j < len(lines):
        line = lines[j]
        if line.strip() == "" or _indent(line) <= key_indent:
            break
        j += 1
    return lines[: k + 1] + rendered + lines[j:]


def _finalize(lines: list[str], *, had_trailing_newline: bool) -> str:
    """Join ``lines``, restoring the input's trailing-newline state.

    ``content.split("\n")`` drops the marker of whether the source ended in "\n"
    (a trailing "" element), and ``_replace_block`` can splice the block right up
    to the end, so the join can't tell on its own -- the caller passes the state
    back in. A newline is added only when the original had one, so a manifest that
    shipped WITHOUT a trailing newline is preserved byte-for-byte -- and the
    idempotent no-op guard can actually fire.
    """
    joined = "\n".join(lines)
    if had_trailing_newline:
        return joined if joined.endswith("\n") else joined + "\n"
    return joined


_ENTRY_START_RE = re.compile(r"^  - ")
_ENTRY_DOCKERFILE_RE = re.compile(r"^\s+(?:- )?dockerfile:\s*[\"']?([^\"'\s#]+)")
_ENTRY_ID_RE = re.compile(r"^(  - id:\s*)[\"']?([^\"'\s#]+)[\"']?(.*)$")


def check_images_block(content: str) -> None:
    """Refuse an ``images:`` block the line-based rewire cannot keep intact.

    Raises ``ValueError`` for a flow-style entry (``- {id: ...}``) or a blank line inside the block:
    either would otherwise make the rewire lose an entry's keys or splice half the block.
    """
    lines = content.split("\n")
    key = next((i for i, ln in enumerate(lines) if re.match(r"^images:\s*(#.*)?$", ln)), None)
    if key is None:
        return
    end = next(
        (i for i in range(key + 1, len(lines)) if lines[i].strip() and _indent(lines[i]) == 0),
        len(lines),
    )
    block = lines[key + 1 : end]
    while block and not block[-1].strip():
        block.pop()
    for n, line in enumerate(block, key + 2):
        if re.match(r"^\s*-\s*\{", line):
            entry = line.strip()[:40]
            msg = f"line {n}: a flow-style images entry ({entry}); write it in block style"
            raise ValueError(msg)
        if not line.strip():
            msg = f"line {n}: a blank line inside the images block; remove it"
            raise ValueError(msg)


def _existing_entries(lines: list[str]) -> dict[str, list[str]]:
    """The current ``images:`` entries (block style), keyed by their ``dockerfile`` value."""
    key = next((i for i, ln in enumerate(lines) if re.match(r"^images:\s*(#.*)?$", ln)), None)
    if key is None:
        return {}
    entries: list[list[str]] = []
    for line in lines[key + 1 :]:
        if line.strip() == "" or _indent(line) == 0:
            break
        if _ENTRY_START_RE.match(line):
            entries.append([line])
        elif entries:
            entries[-1].append(line)
    out: dict[str, list[str]] = {}
    for entry in entries:
        for line in entry:
            m = _ENTRY_DOCKERFILE_RE.match(line)
            if m:
                out[m.group(1)] = entry
                break
    return out


def _keep_entry(entry: list[str], image_id: str) -> list[str]:
    """An existing entry, verbatim, with its id (and repository suffix) renamed if it changed."""
    m = _ENTRY_ID_RE.match(entry[0])
    if not m or m.group(2) == image_id:
        return entry
    old_id = m.group(2)
    renamed = [f"{m.group(1)}{image_id}{m.group(3)}"]
    suffix = re.compile(rf"/{re.escape(old_id)}([\"']?\s*(#.*)?)$")
    renamed += [
        suffix.sub(rf"/{image_id}\1", ln) if re.match(r"^\s+repository:", ln) else ln
        for ln in entry[1:]
    ]
    return renamed


def rewrite_images_block(content: str, images: list[tuple[str, str]]) -> str:
    """Replace the top-level ``images:`` block in container-images.yml.

    An image whose Dockerfile already has an entry keeps that entry byte for byte (every key:
    ``distroless``, ``platforms``, ``healthcheck_exempt``, ...), renamed only when its derived id
    changed; a new Dockerfile gets the canonical entry; an entry without its Dockerfile is dropped.
    """
    check_images_block(content)
    had_nl = content.endswith("\n")
    lines = content.split("\n")
    existing = _existing_entries(lines)
    rendered: list[str] = []
    for image_id, dockerfile in images:
        if dockerfile in existing:
            rendered += _keep_entry(existing[dockerfile], image_id)
        else:
            rendered += render_images_block([(image_id, dockerfile)])
    lines = _replace_block(lines, "images", rendered, expected_indent=0)
    return _finalize(lines, had_trailing_newline=had_nl)


def rewrite_services_block(content: str, images: list[tuple[str, str]]) -> str:
    """Replace the ``services:`` block nested under the top-level ``image:`` key.

    ``services:`` is a unique key in this file (a ``# services:`` mention in a
    comment does not match the key regex), so the same ``_replace_block`` used for
    the top-level ``images:`` block reuses cleanly.
    """
    had_nl = content.endswith("\n")
    lines = content.split("\n")
    lines = _replace_block(lines, "services", render_services_block(images))
    return _finalize(lines, had_trailing_newline=had_nl)
