#!/usr/bin/env python3
"""Render the image manifest into a build or deploy matrix and the version outputs.

Version policy: the committed ``VERSION`` file (``0.0.0`` when absent) is the baseline; a ``v*``
Git tag overrides it (channel ``prod``). On ``main`` the channel is ``main``; on any other branch
(or a pull request or dispatch ref) it is the sanitised ref name, at most 20 characters. The image
tag is ``{version}-{channel}-{short_sha}``. ``immutable`` is ``true`` exactly for ``v*`` tags.

Ref name, ref type and SHA come from ``GITHUB_REF_NAME``, ``GITHUB_REF_TYPE`` and ``GITHUB_SHA``;
repositories are ``ghcr.io/<owner>/<repo>/<id>`` from ``GITHUB_REPOSITORY_OWNER`` and
``GITHUB_REPOSITORY``, lowercased. Bad input exits 2 with a message, never a traceback.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, NoReturn

import yaml

TOP_KEYS = {"schema_version", "version", "images"}
IMAGE_KEYS = {
    "id",
    "repository",
    "dockerfile",
    "context",
    "dockerfile_context",
    "build_args",
    # Read by scripts/ci/image-checks.sh, not rendered into the matrix.
    "distroless",
    "run_env",
    "run_args",
}
ID_RE = re.compile(r"[a-z0-9-]+")
VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")
TAG_RE = re.compile(r"v(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?)")
SHA_RE = re.compile(r"[0-9a-f]{7,64}")
NAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")
PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
MAX_CHANNEL = 20


class InputError(Exception):
    """Bad input; reported as a message and exit status 2."""


def env(name: str) -> str:
    """Return a required environment variable."""
    value = os.environ.get(name, "")
    if not value:
        msg = f"{name} is not set"
        raise InputError(msg)
    return value


def read_baseline(root: Path) -> str:
    """Return the baseline version from the VERSION file (0.0.0 when absent)."""
    path = root / "VERSION"
    if not path.is_file():
        return "0.0.0"
    text = path.read_text(encoding="utf-8")
    first = text.split("\n", 1)[0].strip()
    if text.strip() != first or not VERSION_RE.fullmatch(first):
        msg = f"VERSION must hold one version like 1.2.3 (got {text!r})"
        raise InputError(msg)
    return first


def compute_version(root: Path) -> dict[str, str]:
    """Apply the version and channel policy to the GitHub ref environment."""
    ref_name, ref_type, sha = env("GITHUB_REF_NAME"), env("GITHUB_REF_TYPE"), env("GITHUB_SHA")
    if ref_type not in ("branch", "tag"):
        msg = f"GITHUB_REF_TYPE must be branch or tag (got {ref_type!r})"
        raise InputError(msg)
    if not SHA_RE.fullmatch(sha):
        msg = "GITHUB_SHA must be a lowercase hex commit id"
        raise InputError(msg)
    baseline = read_baseline(root)
    immutable = ref_type == "tag"
    if immutable:
        match = TAG_RE.fullmatch(ref_name)
        if not match:
            msg = f"tag {ref_name!r} does not match v<major>.<minor>.<patch>[-pre][+build]"
            raise InputError(msg)
        version, channel = match[1], "prod"
    elif ref_name == "main":
        version, channel = baseline, "main"
    else:
        version = baseline
        channel = re.sub(r"[^A-Za-z0-9_.-]", "-", ref_name)[:MAX_CHANNEL] or "branch"
    version = version.replace("+", "-")  # Docker-tag safe
    return {
        "version": version,
        "channel": channel,
        "image_tag": f"{version}-{channel}-{sha[:7]}",
        "immutable": "true" if immutable else "false",
    }


def format_outputs(values: dict[str, str]) -> str:
    """Render ``name=value`` lines, refusing values that could inject extra outputs."""
    for name, value in values.items():
        if "\n" in value or "\r" in value:
            msg = f"output {name} contains a newline"
            raise InputError(msg)
    return "".join(f"{k}={v}\n" for k, v in values.items())


# docker run flags the health-check container may never get (ports, host access, privilege).
# The release job's host holds a registry login, so the manifest cannot widen the container.
FORBIDDEN_RUN_FLAGS = (
    "--publish",
    "--publish-all",
    "--privileged",
    "--volume",
    "--mount",
    "--network",
    "--net",
    "--pid",
    "--ipc",
    "--cap-add",
    "--device",
    "--security-opt",
)
FORBIDDEN_SHORT_FLAGS = ("-p", "-P", "-v")


def forbidden_run_arg(arg: str) -> bool:
    """Whether a run_args entry is a forbidden flag: bare, ``--flag=value`` or ``-pX``."""
    if arg.startswith("--"):
        return arg.split("=", 1)[0] in FORBIDDEN_RUN_FLAGS
    return arg.startswith(FORBIDDEN_SHORT_FLAGS)


def check_image_checks_settings(image: dict[str, Any]) -> None:
    """Validate the keys scripts/ci/image-checks.sh reads: distroless, run_env and run_args."""
    env_vars = image.get("run_env") or {}
    if not isinstance(env_vars, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env_vars.items()
    ):
        msg = f"manifest: run_env of {image['id']!r} must map strings to strings"
        raise InputError(msg)
    run_args = image.get("run_args") or []
    if not isinstance(run_args, list) or not all(isinstance(a, str) for a in run_args):
        msg = f"manifest: run_args of {image['id']!r} must be a list of strings"
        raise InputError(msg)
    if bad := [a for a in run_args if forbidden_run_arg(a)]:
        msg = f"manifest: run_args of {image['id']!r} may not hold {bad[0]!r} (ports, mounts, host)"
        raise InputError(msg)
    if not isinstance(image.get("distroless", False), bool):
        msg = f"manifest: distroless of {image['id']!r} must be true or false"
        raise InputError(msg)


def check_image(image: Any, seen: set[str]) -> None:  # noqa: ANN401
    """Validate one manifest image entry and record its id in ``seen``."""
    if not isinstance(image, dict):
        msg = "manifest: each image must be a mapping"
        raise InputError(msg)
    if unknown := set(image) - IMAGE_KEYS:
        msg = f"manifest: unknown image key {min(unknown)!r}"
        raise InputError(msg)
    for key in ("id", "repository", "dockerfile"):
        if not isinstance(image.get(key), str) or not image[key]:
            msg = f"manifest: image needs a non-empty string {key!r}"
            raise InputError(msg)
    if not ID_RE.fullmatch(image["id"]):
        msg = f"manifest: id {image['id']!r} must match [a-z0-9-]+"
        raise InputError(msg)
    if image["id"] in seen:
        msg = f"manifest: duplicate id {image['id']!r}"
        raise InputError(msg)
    seen.add(image["id"])
    args = image.get("build_args") or {}
    if not isinstance(args, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in args.items()
    ):
        msg = f"manifest: build_args of {image['id']!r} must map strings to strings"
        raise InputError(msg)
    check_image_checks_settings(image)


def load_manifest(path: Path) -> list[dict[str, Any]]:
    """Load and validate the manifest, returning its image entries."""
    if not path.is_file():
        msg = f"manifest {path} not found (container-images.yml)"
        raise InputError(msg)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        msg = f"manifest is not valid YAML: {exc}"
        raise InputError(msg) from exc
    if not isinstance(data, dict):
        msg = "manifest must be a mapping"
        raise InputError(msg)
    if unknown := set(data) - TOP_KEYS:
        msg = f"manifest: unknown key {min(unknown)!r}"
        raise InputError(msg)
    images = data.get("images")
    if not isinstance(images, list) or not images:
        msg = "manifest: images must be a non-empty list"
        raise InputError(msg)
    seen: set[str] = set()
    for image in images:
        check_image(image, seen)
    return images


def repo_parts() -> tuple[str, str]:
    """Return the lowercased owner and repository name for GHCR."""
    owner = env("GITHUB_REPOSITORY_OWNER").lower()
    full = env("GITHUB_REPOSITORY")
    repo = full.split("/", 1)[1].lower() if "/" in full else ""
    for label, value in (("GITHUB_REPOSITORY_OWNER", owner), ("GITHUB_REPOSITORY", repo)):
        if not NAME_RE.fullmatch(value):
            msg = f"{label} does not give a valid name (got {value!r})"
            raise InputError(msg)
    return owner, repo


def expand(value: str, ctx: dict[str, str]) -> str:
    """Substitute ``${NAME}`` placeholders; an unknown name is an error."""

    def sub(match: re.Match[str]) -> str:
        if match[1] not in ctx:
            msg = f"manifest: unknown placeholder ${{{match[1]}}}"
            raise InputError(msg)
        return ctx[match[1]]

    return PLACEHOLDER_RE.sub(sub, value)


def render(images: list[dict[str, Any]], ctx: dict[str, str]) -> list[dict[str, Any]]:
    """Return the matrix entries with placeholders expanded."""
    return [
        {
            "id": i["id"],
            "repository": expand(i["repository"], ctx),
            "dockerfile": i["dockerfile"],
            "context": i.get("context", "."),
            "build_args": {k: expand(v, ctx) for k, v in (i.get("build_args") or {}).items()},
        }
        for i in images
    ]


def select(entries: list[dict[str, Any]], raw: str) -> list[dict[str, Any]]:
    """Limit entries to the comma-separated ids in ``raw``."""
    wanted = [s.strip() for s in raw.split(",") if s.strip()]
    if not wanted:
        msg = "--select is empty"
        raise InputError(msg)
    by_id = {e["id"]: e for e in entries}
    if unknown := [w for w in wanted if w not in by_id]:
        msg = f"unknown image id {unknown[0]!r} (known: {', '.join(by_id)})"
        raise InputError(msg)
    return [by_id[w] for w in dict.fromkeys(wanted)]


def run(args: argparse.Namespace) -> None:
    """Execute the requested mode."""
    root = Path(args.root)
    images = load_manifest(root / args.config)
    owner, repo = repo_parts()
    values = compute_version(root)
    entries = render(images, {"OWNER": owner, "REPO": repo, "VERSION": values["version"]})
    if args.deploy_matrix:
        entries = select(entries, args.select)
    text = json.dumps({"include": entries}, separators=(",", ":")) + "\n"
    if args.matrix:
        out = format_outputs(values)
        target = os.environ.get("GITHUB_OUTPUT")
        if target:
            with Path(target).open("a", encoding="utf-8") as fh:
                fh.write(out)
        else:
            text += out
    sys.stdout.write(text)


def fail(message: str) -> NoReturn:
    """Report bad input and exit 2."""
    sys.stderr.write(f"container_matrix: error: {message}\n")
    sys.exit(2)


def main() -> None:
    """Parse arguments and run."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--matrix", action="store_true", help="build matrix and version outputs")
    mode.add_argument("--deploy-matrix", action="store_true", help="matrix limited by --select")
    parser.add_argument("--select", help="comma-separated image ids (with --deploy-matrix)")
    parser.add_argument("--config", default=".github/container-images.yml")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    if args.deploy_matrix and args.select is None:
        parser.error("--deploy-matrix requires --select")
    try:
        run(args)
    except InputError as exc:
        fail(str(exc))
    except OSError as exc:
        fail(str(exc))


if __name__ == "__main__":
    main()
