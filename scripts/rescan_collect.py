#!/usr/bin/env python3
"""Find the image digests to re-scan: what runs in each environment, and the latest release.

Usage: rescan_collect.py OUTDIR       (env: GH_REPO, GH_TOKEN; reads the GitHub API only)
       rescan_collect.py --deployed ENV  print what ENV runs: the JSON object of <ID>_IMAGE_DIGEST
                                         keys the deploy takes (preview.yml deploys what dev runs)

For each environment (dev, qa, production) take the newest deployment whose latest status is
`success` and follow that status's job URL to the job, and so the workflow run, that created it.
  * a `release.yml` run: if the deployment's ref is a release tag whose release carries
    `image-digests.env` and `scan.env`, read them there; otherwise read the `image-digests`
    artifact of that run uploaded before the deploying job started (artifacts outlive re-runs);
  * a `deploy-ops.yml` run (it records a deployment only for a rollback): the running digest is
    unknown, so it is reported;
  * any other run: ignored.
Then do the same for the latest published release. An expired or missing artifact is reported as
not scannable, never skipped silently.

Writes OUTDIR/digests.json ({"images": [...unique by digest...], "unscannable": [...]}).
Appends a summary to GITHUB_STEP_SUMMARY.
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
Images = dict[str, tuple[str, str]]  # image id -> (digest, repository)

ENVIRONMENTS = ("dev", "qa", "production")
RELEASE_WORKFLOW = ".github/workflows/release.yml"
OPS_WORKFLOW = ".github/workflows/deploy-ops.yml"
DIGESTS_FILE = "image-digests.env"
REFS_FILE = "scan.env"
EXPIRED = "not scannable: digest artifact expired (redeploy)"
ROLLBACK = "after rollback: running digest unknown - redeploy or inspect the cluster"
MISMATCH = "not scannable: the deploying run's commit or ref is not the deployment's"
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
SLUG = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
LIMIT = 30  # the newest deployments and runs that are looked at
ARGS = 2


class NotFoundError(RuntimeError):
    """The GitHub API answered 404."""


class Expired(Exception):  # noqa: N818 - a control-flow signal, not an error
    """The digest artifact exists but has expired."""


def run_gh(args: list[str]) -> bytes:
    """Run `gh` (the token comes from GH_TOKEN) and return stdout."""
    done = subprocess.run([shutil.which("gh") or "gh", *args], capture_output=True, check=False)  # noqa: S603
    if done.returncode != 0:
        err = done.stderr.decode(errors="replace").strip()
        msg = f"gh {' '.join(args[:3])} failed: {err}"
        raise (NotFoundError if "HTTP 404" in err else RuntimeError)(msg)
    return done.stdout


def api(gh: Gh, path: str) -> Any:  # noqa: ANN401 - parsed JSON
    return json.loads(gh(["api", path]))


def parse_env(text: str) -> dict[str, str]:
    pairs = (ln.split("=", 1) for ln in text.splitlines() if "=" in ln and not ln.startswith("#"))
    return {k.strip(): v.strip() for k, v in pairs}


def registry_pattern(repo: str) -> re.Pattern[str]:
    """Images of this repository only: ghcr.io/<owner>/<repo>/<name>, lowercased as GHCR does."""
    if not SLUG.match(repo):
        msg = f"GH_REPO is not owner/repo: {repo!r}"
        raise ValueError(msg)
    return re.compile(rf"^ghcr\.io/{re.escape(repo.lower())}/[a-z0-9][a-z0-9._/-]*$")


def build_from_env(digests: str, refs: str, repo: str) -> Images:
    """Join `<ID>_IMAGE_DIGEST` with `<ID>_IMAGE` (a reference with a tag): id -> (digest, repo)."""
    pattern = registry_pattern(repo)
    images: Images = {}
    ref_map = parse_env(refs)
    for key, digest in parse_env(digests).items():
        if not key.endswith("_IMAGE_DIGEST"):
            continue
        name = key.removesuffix("_DIGEST")
        ref = ref_map.get(name, "")
        repository = ref.rsplit(":", 1)[0] if ":" in ref.rsplit("/", 1)[-1] else ref
        if not DIGEST.match(digest) or not pattern.match(repository):
            msg = f"unusable digest or image reference for {name}"
            raise ValueError(msg)
        images[name.removesuffix("_IMAGE").lower()] = (digest, repository)
    return images


def from_release(gh: Gh, repo: str, release: dict[str, Any]) -> Images | None:
    """The images a release shipped, from its assets, or None when it has no digest assets."""
    assets = {a["name"]: a["id"] for a in release.get("assets", [])}
    if DIGESTS_FILE not in assets or REFS_FILE not in assets:
        return None

    def asset(name: str) -> str:
        url = f"repos/{repo}/releases/assets/{assets[name]}"
        return gh(["api", "-H", "Accept: application/octet-stream", url]).decode()

    return build_from_env(asset(DIGESTS_FILE), asset(REFS_FILE), repo)


def parse_run_url(url: str, repo: str) -> tuple[int, int]:
    """(run id, job id) from a deployment status URL; anything else is reported."""
    pattern = rf"^https://github\.com/{re.escape(repo)}/actions/runs/(\d+)/job/(\d+)(?:[?#].*)?$"
    m = re.match(pattern, url)
    if not m:
        msg = "not scannable: unrecognised deployment status URL"
        raise LookupError(msg)
    return int(m[1]), int(m[2])


def run_artifact(gh: Gh, repo: str, run_id: int, before: str | None) -> dict[str, bytes]:
    """The newest `image-digests` artifact of a run, uploaded at or before `before` if given.

    Artifacts persist across attempts, so the deploying job's start time picks the one it used.
    """
    found = api(gh, f"repos/{repo}/actions/runs/{run_id}/artifacts?name=image-digests&per_page=100")
    arts = [a for a in found.get("artifacts", []) if before is None or a["created_at"] <= before]
    if not arts:
        msg = "not scannable: no image-digests artifact uploaded before the deploying job"
        raise LookupError(msg)
    art = max(arts, key=lambda a: a["created_at"])
    if art.get("expired"):
        raise Expired
    data = gh(["api", f"repos/{repo}/actions/artifacts/{art['id']}/zip"])
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {Path(n).name: z.read(n) for n in z.namelist() if not n.endswith("/")}


def from_run(gh: Gh, repo: str, run_id: int, before: str | None) -> Images:
    try:
        files = run_artifact(gh, repo, run_id, before)
    except Expired:
        raise LookupError(EXPIRED) from None
    if DIGESTS_FILE not in files or REFS_FILE not in files:
        msg = "not scannable: the artifact lacks image-digests.env or scan.env"
        raise LookupError(msg)
    return build_from_env(files[DIGESTS_FILE].decode(), files[REFS_FILE].decode(), repo)


def from_tag_run(gh: Gh, repo: str, tag: str) -> Images:
    """The release workflow run of a tag (newest first), for a release without digest assets."""
    sha = str(api(gh, f"repos/{repo}/commits/{tag}")["sha"])
    runs = api(
        gh, f"repos/{repo}/actions/workflows/release.yml/runs?head_sha={sha}&per_page={LIMIT}"
    )
    mine = [r for r in runs.get("workflow_runs", []) if r.get("head_branch") == tag]
    if not mine:
        msg = "not scannable: no release workflow run found for this tag"
        raise LookupError(msg)
    last: LookupError | None = None
    for run in mine:
        try:
            return from_run(gh, repo, run["id"], None)
        except LookupError as err:
            last = err
    raise last or LookupError(EXPIRED)


def release_by_tag(gh: Gh, repo: str, tag: str) -> dict[str, Any] | None:
    try:
        return dict(api(gh, f"repos/{repo}/releases/tags/{tag}"))
    except NotFoundError:
        return None


def latest_deployment(
    gh: Gh, repo: str, env: str
) -> tuple[dict[str, Any], str, int, dict[str, Any]] | None:
    """The newest successful deployment of an environment made by the release or the ops workflow:
    (deployment, workflow path, run id, job); None when there is none; LookupError to report."""
    deps = api(gh, f"repos/{repo}/deployments?environment={env}&per_page={LIMIT}")
    for dep in deps:
        statuses = api(gh, f"repos/{repo}/deployments/{dep['id']}/statuses?per_page=1")
        if not statuses or statuses[0].get("state") != "success":
            continue
        url = str(statuses[0].get("log_url") or statuses[0].get("target_url") or "")
        run_id, job_id = parse_run_url(url, repo)
        job = api(gh, f"repos/{repo}/actions/jobs/{job_id}")
        if job.get("run_id") != run_id:
            msg = "not scannable: the deployment's job does not belong to its run"
            raise LookupError(msg)
        run = api(gh, f"repos/{repo}/actions/runs/{run_id}")
        path = str(run.get("path", "")).split("@")[0]
        if path in (OPS_WORKFLOW, RELEASE_WORKFLOW):
            # The run that made it ran on the deployment's commit and ref, or it is not trusted.
            if (run.get("head_sha"), run.get("head_branch")) != (dep.get("sha"), dep.get("ref")):
                raise LookupError(MISMATCH)
            return dep, path, run_id, job
    return None


def deployment_images(
    gh: Gh, repo: str, dep: dict[str, Any], run_id: int, job: dict[str, Any]
) -> Images:
    """The images a release deployment ran: its release's assets, else its run's artifact."""
    ref = str(dep.get("ref", ""))
    release = release_by_tag(gh, repo, ref) if ref.startswith("v") else None
    built = from_release(gh, repo, release) if release else None
    return built or from_run(gh, repo, run_id, str(job["started_at"]))


def deployed(gh: Gh, repo: str, env: str) -> Images | None:
    """What an environment runs; None when no release deployment is found; LookupError to report."""
    found = latest_deployment(gh, repo, env)
    if found is None:
        return None
    dep, path, run_id, job = found
    if path == OPS_WORKFLOW:  # it records a deployment only for a rollback
        raise LookupError(ROLLBACK)
    return deployment_images(gh, repo, dep, run_id, job)


def release_images(gh: Gh, repo: str, release: dict[str, Any]) -> Images:
    """A published release's images: its assets, else the workflow run of its tag."""
    return from_release(gh, repo, release) or from_tag_run(gh, repo, str(release["tag_name"]))


def collect(gh: Gh, repo: str) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]], list[str]]:
    """(images by digest, unscannable, notes). Each image: id, repository, where."""
    images: dict[str, dict[str, Any]] = {}
    bad: list[dict[str, str]] = []
    notes: list[str] = []

    def attempt(where: str, make: Callable[[], Images | None]) -> None:
        try:
            found = make()
        except (LookupError, ValueError) as err:
            bad.append({"where": where, "reason": str(err)})
            return
        if found is None:
            notes.append(f"{where}: no successful release deployment")
            return
        for name, (digest, repository) in found.items():
            img = images.setdefault(digest, {"id": name, "repository": repository, "where": []})
            img["where"].append(where)

    for env in ENVIRONMENTS:
        attempt(env, partial(deployed, gh, repo, env))
    try:
        release = dict(api(gh, f"repos/{repo}/releases/latest"))
    except NotFoundError:
        notes.append("no published release")
    else:
        tag = str(release["tag_name"])
        if release.get("draft") or release.get("prerelease") or not tag.startswith("v"):
            notes.append(f"latest release {tag} is not a published v* release; skipped")
        else:
            attempt(f"release {tag}", partial(release_images, gh, repo, release))
    return images, bad, notes


def write(out: Path, images: dict[str, dict[str, Any]], bad: list[dict[str, str]]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "id": img["id"],
            "digest": digest,
            "short": digest.removeprefix("sha256:")[:12],
            "repository": img["repository"],
            "where": img["where"],
        }
        for digest, img in sorted(images.items())
    ]
    (out / "digests.json").write_text(json.dumps({"images": rows, "unscannable": bad}, indent=2))


def deployed_digests(gh: Gh, repo: str, env: str) -> dict[str, str]:
    """What an environment runs, as the deploy's DIGESTS object; LookupError when it is unknown."""
    try:
        found = deployed(gh, repo, env)
    except ValueError as err:
        raise LookupError(str(err)) from None
    if not found:
        msg = f"no successful release deployment of {env}"
        raise LookupError(msg)
    return {f"{name.upper()}_IMAGE_DIGEST": digest for name, (digest, _) in sorted(found.items())}


def main(argv: list[str], gh: Gh = run_gh) -> int:
    deployed_mode = len(argv) == ARGS + 1 and argv[1] == "--deployed"
    if len(argv) != ARGS and not deployed_mode:
        sys.stderr.write(str(__doc__))
        return 2
    repo = os.environ.get("GH_REPO", "")
    if not repo:
        sys.stderr.write("GH_REPO is required\n")
        return 2
    if deployed_mode:
        try:
            digests = deployed_digests(gh, repo, argv[2])
        except LookupError as err:
            sys.stderr.write(f"{argv[2]}: {err}\n")
            return 1
        sys.stdout.write(json.dumps(digests) + "\n")
        return 0
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
