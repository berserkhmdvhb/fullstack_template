#!/usr/bin/env python3
"""Find the image digests to re-scan: what runs in each environment, and the latest release.

Usage: rescan_collect.py OUTDIR       (env: GH_REPO, GH_TOKEN; reads the GitHub API only)

For each environment (dev, qa, production) take the latest deployment whose latest status is
`success`. If its ref is a release tag whose release carries `image-digests.env` and `scan.env`
assets, read the digests from there; otherwise map the deployment to the Release workflow run for
that commit and read them from that run's `image-digests` artifact. Then do the same for the latest
published release. An expired artifact is reported as not scannable, never skipped silently.

Writes OUTDIR/digests.json ({"images": [...unique by digest...], "unscannable": [...]}) and the
OpenVEX document of each build under OUTDIR/vex/. Appends a summary to GITHUB_STEP_SUMMARY.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

# A runner takes the gh arguments and returns stdout; a failing call raises. Tests pass a stub.
Gh = Callable[[list[str]], bytes]
Make = Callable[[], "Build"]

ENVIRONMENTS = ("dev", "qa", "production")
RELEASE_WORKFLOW = "release.yml"
DIGESTS_FILE = "image-digests.env"
REFS_FILE = "scan.env"
VEX_FILE = "openvex.json"
EXPIRED = "not scannable: digest artifact expired (redeploy)"
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
REPOSITORY = re.compile(r"^[a-z0-9][a-z0-9._/-]*$")
LIMIT = 30  # the newest deployments, runs and releases that are looked at
ARGS = 2


class Expired(Exception):  # noqa: N818 - a control-flow signal, not an error
    """The digest artifact exists but has expired."""


class Build:
    """The images one build produced: id -> (digest, repository), and its OpenVEX document."""

    def __init__(self, images: dict[str, tuple[str, str]], vex: bytes | None) -> None:
        self.images = images
        self.vex = vex


def run_gh(args: list[str]) -> bytes:
    """Run `gh` (the token comes from GH_TOKEN) and return stdout."""
    done = subprocess.run([shutil.which("gh") or "gh", *args], capture_output=True, check=False)  # noqa: S603
    if done.returncode != 0:
        msg = f"gh {' '.join(args[:3])} failed: {done.stderr.decode(errors='replace').strip()}"
        raise RuntimeError(msg)
    return done.stdout


def api(gh: Gh, path: str) -> Any:  # noqa: ANN401 - parsed JSON
    return json.loads(gh(["api", path]))


def parse_env(text: str) -> dict[str, str]:
    pairs = (ln.split("=", 1) for ln in text.splitlines() if "=" in ln and not ln.startswith("#"))
    return {k.strip(): v.strip() for k, v in pairs}


def build_from_env(digests: str, refs: str, vex: bytes | None) -> Build:
    """Join `<ID>_IMAGE_DIGEST` with `<ID>_IMAGE` (a reference with a tag) into a Build."""
    images: dict[str, tuple[str, str]] = {}
    ref_map = parse_env(refs)
    for key, digest in parse_env(digests).items():
        if not key.endswith("_IMAGE_DIGEST"):
            continue
        name = key.removesuffix("_DIGEST")
        ref = ref_map.get(name, "")
        repository = ref.rsplit(":", 1)[0] if ":" in ref.rsplit("/", 1)[-1] else ref
        if not DIGEST.match(digest) or not REPOSITORY.match(repository):
            msg = f"unusable digest or image reference for {name}"
            raise ValueError(msg)
        images[name.removesuffix("_IMAGE").lower()] = (digest, repository)
    return Build(images, vex)


def from_release(gh: Gh, repo: str, release: dict[str, Any]) -> Build | None:
    """The build a release shipped, from its assets, or None when it has no digest assets."""
    assets = {a["name"]: a["id"] for a in release.get("assets", [])}
    if DIGESTS_FILE not in assets or REFS_FILE not in assets:
        return None

    def asset(name: str) -> bytes:
        url = f"repos/{repo}/releases/assets/{assets[name]}"
        return gh(["api", "-H", "Accept: application/octet-stream", url])

    vex = asset(VEX_FILE) if VEX_FILE in assets else None
    return build_from_env(asset(DIGESTS_FILE).decode(), asset(REFS_FILE).decode(), vex)


def artifact_zip(gh: Gh, repo: str, run_id: int, name: str) -> dict[str, bytes] | None:
    """The files of one run artifact; None when there is none; Expired when it has expired."""
    found = api(gh, f"repos/{repo}/actions/runs/{run_id}/artifacts?name={name}&per_page=5")
    arts = found.get("artifacts", [])
    if not arts:
        return None
    if all(a.get("expired") for a in arts):
        raise Expired
    art = next(a for a in arts if not a.get("expired"))
    data = gh(["api", f"repos/{repo}/actions/artifacts/{art['id']}/zip"])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {Path(n).name: z.read(n) for n in z.namelist() if not n.endswith("/")}


def from_run(gh: Gh, repo: str, sha: str, ref: str) -> Build:
    """The build of the release workflow run for a commit (the run for the same ref first)."""
    path = f"repos/{repo}/actions/workflows/{RELEASE_WORKFLOW}/runs?head_sha={sha}&per_page={LIMIT}"
    runs = api(gh, path).get("workflow_runs", [])
    if not runs:
        msg = "not scannable: no release workflow run found for this commit"
        raise LookupError(msg)
    runs.sort(key=lambda r: r.get("head_branch") != ref)  # stable: newest first within each group
    expired = False
    for run in runs:
        try:
            files = artifact_zip(gh, repo, run["id"], "image-digests")
        except Expired:
            expired = True
            continue
        if files and DIGESTS_FILE in files and REFS_FILE in files:
            vex_files = _optional_vex(gh, repo, run["id"])
            return build_from_env(
                files[DIGESTS_FILE].decode(), files[REFS_FILE].decode(), vex_files
            )
    if expired:
        raise LookupError(EXPIRED)
    msg = "not scannable: the release workflow run has no image-digests artifact"
    raise LookupError(msg)


def _optional_vex(gh: Gh, repo: str, run_id: int) -> bytes | None:
    try:
        files = artifact_zip(gh, repo, run_id, "vex")
    except Expired:
        return None
    return files.get(VEX_FILE) if files else None


def latest_success(gh: Gh, repo: str, env: str) -> dict[str, Any] | None:
    deps = api(gh, f"repos/{repo}/deployments?environment={env}&per_page={LIMIT}")
    for dep in deps:
        statuses = api(gh, f"repos/{repo}/deployments/{dep['id']}/statuses?per_page=1")
        if statuses and statuses[0].get("state") == "success":
            return dict(dep)
    return None


def deployed_build(gh: Gh, repo: str, dep: dict[str, Any], by_tag: dict[str, Any]) -> Build:
    """The build a deployment ran: the release assets for a release tag, else the workflow run."""
    ref, sha = str(dep.get("ref", "")), str(dep["sha"])
    release = by_tag.get(ref) if ref.startswith("v") else None
    built = from_release(gh, repo, release) if release else None
    return built or from_run(gh, repo, sha, ref)


def release_build(gh: Gh, repo: str, release: dict[str, Any]) -> Build:
    """A published release's build: its assets, else the workflow run of its tag."""
    built = from_release(gh, repo, release)
    if built:
        return built
    tag = str(release["tag_name"])
    sha = str(api(gh, f"repos/{repo}/commits/{tag}")["sha"])
    return from_run(gh, repo, sha, tag)


def collect(gh: Gh, repo: str) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]], list[str]]:
    """(images by digest, unscannable, notes). Each image: id, repository, where, vex bytes."""
    releases = api(gh, f"repos/{repo}/releases?per_page={LIMIT}")
    by_tag = {r["tag_name"]: r for r in releases}
    images: dict[str, dict[str, Any]] = {}
    bad: list[dict[str, str]] = []
    notes: list[str] = []

    def add(where: str, build: Build) -> None:
        for name, (digest, repository) in build.images.items():
            img = images.setdefault(
                digest, {"id": name, "repository": repository, "where": [], "vex": None}
            )
            img["where"].append(where)
            img["vex"] = img["vex"] or build.vex

    def attempt(where: str, make: Make) -> None:
        try:
            build = make()
        except (LookupError, ValueError) as err:
            bad.append({"where": where, "reason": str(err)})
            return
        if build.vex is None:
            notes.append(f"{where}: no OpenVEX document found; scanned without VEX")
        add(where, build)

    for env in ENVIRONMENTS:
        dep = latest_success(gh, repo, env)
        if dep is None:
            notes.append(f"{env}: no successful deployment")
            continue
        attempt(env, partial(deployed_build, gh, repo, dep, by_tag))
    published = [r for r in releases if not r.get("draft") and str(r["tag_name"]).startswith("v")]
    if published:
        latest = published[0]
        tag = str(latest["tag_name"])
        attempt(f"release {tag}", partial(release_build, gh, repo, latest))
    else:
        notes.append("no published release")
    return images, bad, notes


def write(out: Path, images: dict[str, dict[str, Any]], bad: list[dict[str, str]]) -> None:
    (out / "vex").mkdir(parents=True, exist_ok=True)
    rows = []
    for digest, img in sorted(images.items()):
        short = digest.removeprefix("sha256:")[:12]
        vex = None
        if img["vex"]:
            vex = f"vex/{short}.openvex.json"
            (out / vex).write_bytes(img["vex"])
        rows.append(
            {
                "id": img["id"],
                "digest": digest,
                "short": short,
                "repository": img["repository"],
                "where": img["where"],
                "vex": vex,
            }
        )
    (out / "digests.json").write_text(json.dumps({"images": rows, "unscannable": bad}, indent=2))


def main(argv: list[str], gh: Gh = run_gh) -> int:
    if len(argv) != ARGS:
        sys.stderr.write(str(__doc__))
        return 2
    repo = os.environ.get("GH_REPO", "")
    if not repo:
        sys.stderr.write("GH_REPO is required\n")
        return 2
    images, bad, notes = collect(gh, repo)
    write(Path(argv[1]), images, bad)
    lines = ["### Re-scan: digests found", ""]
    lines += [f"- `{d}` ({i['id']}): {', '.join(i['where'])}" for d, i in sorted(images.items())]
    lines += [f"- {b['where']}: {b['reason']}" for b in bad]
    lines += [f"- {n}" for n in notes]
    for b in bad:
        sys.stdout.write(f"::warning::{b['where']}: {b['reason']}\n")
    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
