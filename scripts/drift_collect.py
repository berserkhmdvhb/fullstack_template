#!/usr/bin/env python3
"""Find what each environment's latest deployment applied, for the live-drift check.

Usage: drift_collect.py OUTDIR       (env: GH_REPO, GH_TOKEN; reads the GitHub API only)

The same lookup as the weekly re-scan (rescan_collect.py): for dev, qa and production, the newest
deployment whose latest status is `success`, made by the release or the ops workflow. A release
deployment gives its commit, its ref (main or the v* tag) and the digests it deployed (the release's
assets, else its run's artifact). A rollback (deploy-ops.yml), an expired digest artifact, no
deployment at all, or an unusable ref or commit is a reason not to compare, never a guess. A failing
API call fails the collection.

Writes OUTDIR/deployments.json ({"<env>": {"sha", "ref", "digests"} or {"skip": reason}}) and a
summary to stdout and GITHUB_STEP_SUMMARY.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from rescan_collect import (
    ENVIRONMENTS,
    OPS_WORKFLOW,
    Gh,
    NotFoundError,
    deployment_images,
    latest_deployment,
    run_gh,
)

__all__ = ["NotFoundError", "collect", "main"]

SHA = re.compile(r"^[0-9a-f]{40}$")
REF = re.compile(r"^(main|v[0-9][0-9A-Za-z.+-]*)$")
ROLLBACK = "the latest deployment is a rollback (deploy-ops.yml): not known from Git; redeploy"
UNUSABLE = "unusable deployment: its ref is not main or a v* tag, or its commit is not a full id"
ARGS = 2


def environment(gh: Gh, repo: str, env: str) -> dict[str, Any]:
    try:
        found = latest_deployment(gh, repo, env)
        if found is None:
            return {"skip": "no successful deployment"}
        dep, path, run_id, job = found
        if path == OPS_WORKFLOW:
            return {"skip": ROLLBACK}
        sha, ref = str(dep.get("sha", "")), str(dep.get("ref", ""))
        if not SHA.match(sha) or not REF.match(ref):
            return {"skip": UNUSABLE}
        images = deployment_images(gh, repo, dep, run_id, job)
    except (LookupError, ValueError) as err:
        return {"skip": str(err)}
    digests = {
        f"{name.upper()}_IMAGE_DIGEST": digest for name, (digest, _) in sorted(images.items())
    }
    return {"sha": sha, "ref": ref, "digests": digests}


def collect(gh: Gh, repo: str) -> dict[str, dict[str, Any]]:
    return {env: environment(gh, repo, env) for env in ENVIRONMENTS}


def main(argv: list[str], gh: Gh = run_gh) -> int:
    if len(argv) != ARGS:
        sys.stderr.write(str(__doc__))
        return 2
    repo = os.environ.get("GH_REPO", "")
    if not repo:
        sys.stderr.write("GH_REPO is required\n")
        return 2
    found = collect(gh, repo)
    out = Path(argv[1])
    out.mkdir(parents=True, exist_ok=True)
    (out / "deployments.json").write_text(json.dumps(found, indent=2) + "\n")
    lines = ["### Live drift: what each environment's latest deployment applied", ""]
    for env, d in found.items():
        lines.append(
            f"- {env}: not compared: {d['skip']}"
            if "skip" in d
            else f"- {env}: `{d['ref']}` at `{d['sha'][:12]}`"
        )
    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
