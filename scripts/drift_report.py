#!/usr/bin/env python3
"""Turn the live-drift findings into GitHub issues: one per environment, label `drift`.

Usage: drift_report.py DEPLOYMENTS_JSON FINDINGS_DIR     (env: GH_REPO, GH_TOKEN)

DEPLOYMENTS_JSON is written by drift_collect.py; FINDINGS_DIR holds <env>.json from drift.py.
An open issue carries `<!-- drift-env: ENV -->` and `<!-- drift-state: SHA -->` (the findings).
A later run does nothing when the state is unchanged; when it changed it rewrites the body and adds
a comment. Issues are never closed here, not even when the drift is gone. Only kinds, names and
field paths are written, never a value; untrusted text is sanitised, and bodies go to gh as files.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from rescan_collect import Gh, run_gh
from rescan_report import APP, body_file, clean

LABEL = "drift"
ENV = re.compile(r"<!-- drift-env: ([a-z]+) -->")
STATE = re.compile(r"<!-- drift-state: ([0-9a-f]{64}) -->")
ARGS = 3


def state_of(findings: list[dict[str, str]]) -> str:
    rows = sorted([f["change"], f["kind"], f["namespace"], f["name"], f["path"]] for f in findings)
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def body_of(env: str, findings: list[dict[str, str]]) -> str:
    lines = [
        f"<!-- drift-env: {env} -->",
        f"<!-- drift-state: {state_of(findings)} -->",
        (
            f"The live objects of **{env}** differ from the render of its latest deployment "
            "(the weekly live-drift check). The next deploy overwrites them: bring a change you "
            "keep into Git first, then redeploy and close this issue. It is never closed "
            "automatically."
        ),
        "",
        "| Change | Kind | Namespace | Name | Field |",
        "|---|---|---|---|---|",
    ]
    for f in sorted(findings, key=lambda f: (f["kind"], f["namespace"], f["name"], f["path"])):
        field = f"`{clean(f['path'])}`" if f["path"] else "the object"
        cells = [
            clean(f["change"]).replace("_", " "),
            clean(f["kind"]),
            f"`{clean(f['namespace'])}`",
        ]
        lines.append("| " + " | ".join([*cells, f"`{clean(f['name'])}`", field]) + " |")
    return "\n".join(lines) + "\n"


def open_issues(gh: Gh) -> dict[str, dict[str, Any]]:
    """Open `drift` issues by environment; the oldest wins when one is duplicated."""
    args = ["issue", "list", "--label", LABEL, "--app", APP, "--state", "open", "--limit", "1000"]
    out: dict[str, dict[str, Any]] = {}
    for row in sorted(json.loads(gh([*args, "--json", "number,body"])), key=lambda r: r["number"]):
        e, s = ENV.search(row["body"] or ""), STATE.search(row["body"] or "")
        if e:
            out.setdefault(e.group(1), {"number": row["number"], "state": s.group(1) if s else ""})
    return out


def report(gh: Gh, deployments: dict[str, Any], findings_dir: Path) -> list[str]:
    found: dict[str, list[dict[str, str]]] = {}
    for env in deployments:
        path = findings_dir / f"{env}.json"
        if path.is_file():
            found[env] = json.loads(path.read_text())["findings"]
    if not any(found.values()):
        existing = open_issues(gh) if found else {}
    else:
        names = {
            x["name"]
            for x in json.loads(gh(["label", "list", "--limit", "1000", "--json", "name"]))
        }
        if LABEL not in names:
            gh(["label", "create", LABEL, "--color", "D93F0B", "--description", "Live drift"])
        existing = open_issues(gh)
    notes = []
    for env, findings in found.items():
        old, n = existing.get(env), len(findings)
        if not findings:
            if old:
                notes.append(
                    f"{env}: no drift now; #{old['number']} stays open until someone closes it"
                )
            continue
        body = body_of(env, findings)
        if old is None:
            with body_file(body) as f:
                gh(
                    [
                        "issue",
                        "create",
                        "--title",
                        f"Live drift in {env}",
                        "--label",
                        LABEL,
                        "--body-file",
                        f,
                    ]
                )
            notes.append(f"opened: {env} ({n} findings)")
        elif old["state"] != state_of(findings):
            with body_file(body) as f:
                gh(["issue", "edit", str(old["number"]), "--body-file", f])
            with body_file(f"The live drift of {env} changed: {n} findings now.\n") as f:
                gh(["issue", "comment", str(old["number"]), "--body-file", f])
            notes.append(f"updated: #{old['number']} {env} ({n} findings)")
        else:
            notes.append(f"unchanged: #{old['number']} {env}")
    return notes


def main(argv: list[str], gh: Gh = run_gh) -> int:
    if len(argv) != ARGS:
        sys.stderr.write(str(__doc__))
        return 2
    deployments = json.loads(Path(argv[1]).read_text())
    findings_dir = Path(argv[2])
    notes = report(gh, deployments, findings_dir)
    lines = ["### Live drift: issues", ""]
    for env, d in deployments.items():
        if "skip" in d:
            lines.append(f"- {env}: not compared: {d['skip']}")
        elif not (findings_dir / f"{env}.json").is_file():
            lines.append(f"- {env}: no report (the drift job skipped or failed; see its summary)")
    lines += [f"- {n}" for n in notes]
    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
