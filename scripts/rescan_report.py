#!/usr/bin/env python3
"""Turn the re-scan findings into GitHub issues: one per (vulnerability id, package), label `vuln`.

Usage: rescan_report.py DIGESTS_JSON FINDINGS_DIR     (env: GH_REPO, GH_TOKEN)

DIGESTS_JSON is written by rescan_collect.py; FINDINGS_DIR holds one grype JSON report per
digest, named <short>.json. Only critical and high findings become issues; the rest go to the
run summary. An open issue carries `<!-- vuln-key: ID|PACKAGE -->` and `<!-- vuln-state: SHA -->`
(severity plus the digests and environments affected). A later run does nothing when the state is
unchanged; when it changed it rewrites the body and adds a comment. Issues are never closed here.
Untrusted text is sanitised, and bodies go to gh as files, never through a shell.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from rescan_collect import Gh, run_gh

LABEL = "vuln"
APP = "github-actions"  # only issues the workflow token made count as ours
MAX_NEW = 20
SEVERITIES = ("Critical", "High")
SLA_DAYS = {"Critical": 7, "High": 30}
KEY = re.compile(r"<!-- vuln-key: (.+?) -->")
STATE = re.compile(r"<!-- vuln-state: ([0-9a-f]{64}) -->")
ARGS = 3


def clean(text: object) -> str:
    """Keep only characters that are safe in a title, a marker and a code span."""
    return re.sub(r"[^\w.+:@/~=-]", "_", str(text))[:120]


def aggregate(digests: dict[str, Any], findings_dir: Path) -> tuple[dict[str, Any], Counter[str]]:
    """Critical/high findings by `ID|package`, and a count of every other severity."""
    found: dict[str, Any] = {}
    other: Counter[str] = Counter()
    for img in digests["images"]:
        report = findings_dir / f"{img['short']}.json"
        if not report.is_file():
            continue
        for m in json.loads(report.read_text())["matches"]:
            sev = str(m["vulnerability"]["severity"]).capitalize()
            if sev not in SEVERITIES:
                other[sev] += 1
                continue
            vid, pkg = clean(m["vulnerability"]["id"]), clean(m["artifact"]["name"])
            entry = found.setdefault(
                f"{vid}|{pkg}", {"id": vid, "package": pkg, "severity": sev, "affected": {}}
            )
            if SEVERITIES.index(sev) < SEVERITIES.index(entry["severity"]):
                entry["severity"] = sev
            aff = entry["affected"].setdefault(
                img["digest"],
                {"image": clean(img["id"]), "where": sorted(img["where"]), "versions": set()},
            )
            aff["versions"].add(clean(m["artifact"].get("version", "")))
            aff.setdefault("fixed", set()).update(
                clean(v) for v in m["vulnerability"].get("fix", {}).get("versions", [])
            )
    return found, other


def span(values: set[str]) -> str:
    """Values as code spans, so `@org/team` cannot ping anyone and a URL is not autolinked."""
    return ", ".join(f"`{v}`" for v in sorted(values) if v)


def state_of(entry: dict[str, Any]) -> str:
    basis = [
        entry["severity"],
        sorted((d, sorted(a["where"])) for d, a in entry["affected"].items()),
    ]
    return hashlib.sha256(json.dumps(basis).encode()).hexdigest()


def body_of(key: str, entry: dict[str, Any]) -> str:
    sev = entry["severity"]
    lines = [
        f"<!-- vuln-key: {key} -->",
        f"<!-- vuln-state: {state_of(entry)} -->",
        f"`{entry['id']}` in package `{entry['package']}`: **{sev}**.",
        "",
        (
            f"Target: fix within {SLA_DAYS[sev]} days of this issue being opened (see the "
            "vulnerability management guide). This issue is never closed automatically."
        ),
        "",
        "| Image | Digest | Runs in | Installed | Fixed in |",
        "|---|---|---|---|---|",
    ]
    for digest, a in sorted(entry["affected"].items()):
        cells = [
            f"`{a['image']}`",
            f"`{digest}`",
            ", ".join(f"`{w}`" for w in a["where"]),
            span(a["versions"]) or "-",
            span(a["fixed"]) or "none yet",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


@contextmanager
def body_file(text: str) -> Iterator[str]:
    """A temporary file holding `text`, for gh --body-file (no shell ever sees the text)."""
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(text)
    try:
        yield f.name
    finally:
        Path(f.name).unlink()


def ensure_label(gh: Gh) -> None:
    names = {
        x["name"] for x in json.loads(gh(["label", "list", "--limit", "1000", "--json", "name"]))
    }
    if LABEL not in names:
        gh(["label", "create", LABEL, "--color", "B60205", "--description", "Vulnerability"])


def open_issues(gh: Gh) -> dict[str, dict[str, Any]]:
    """Open `vuln` issues by key; the oldest wins when a key is duplicated."""
    args = [
        "issue",
        "list",
        "--label",
        LABEL,
        "--app",
        APP,
        "--state",
        "open",
        "--limit",
        "1000",
    ]
    rows = json.loads(gh([*args, "--json", "number,body"]))
    out: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda r: r["number"]):
        k, s = KEY.search(row["body"] or ""), STATE.search(row["body"] or "")
        if k:
            out.setdefault(k.group(1), {"number": row["number"], "state": s.group(1) if s else ""})
    return out


def report(gh: Gh, digests: dict[str, Any], findings_dir: Path) -> tuple[list[str], Counter[str]]:
    found, other = aggregate(digests, findings_dir)
    notes: list[str] = []
    if not found:
        return notes, other
    ensure_label(gh)
    existing = open_issues(gh)
    created = 0
    # Critical before high, then by key, so the cap never defers a critical finding.
    for key, entry in sorted(
        found.items(), key=lambda kv: (kv[1]["severity"] != "Critical", kv[0])
    ):
        body = body_of(key, entry)
        title = f"{entry['id']} in {entry['package']}"
        old = existing.get(key)
        if old is None and created >= MAX_NEW:
            notes.append(f"not opened (limit {MAX_NEW} per run): {key} ({entry['severity']})")
        elif old is None:
            created += 1
            with body_file(body) as f:
                gh(["issue", "create", "--title", title, "--label", LABEL, "--body-file", f])
            notes.append(f"opened: {key} ({entry['severity']})")
        elif old["state"] != state_of(entry):
            n = str(old["number"])
            comment = (
                f"Re-scan: the severity or the affected digests changed ({entry['severity']}).\n"
            )
            with body_file(body) as f:
                gh(["issue", "edit", n, "--body-file", f])
            with body_file(comment) as f:
                gh(["issue", "comment", n, "--body-file", f])
            notes.append(f"updated: #{n} {key} ({entry['severity']})")
        else:
            notes.append(f"unchanged: #{old['number']} {key}")
    return notes, other


def main(argv: list[str], gh: Gh = run_gh) -> int:
    if len(argv) != ARGS:
        sys.stderr.write(str(__doc__))
        return 2
    digests = json.loads(Path(argv[1]).read_text())
    notes, other = report(gh, digests, Path(argv[2]))
    lines = ["### Re-scan: findings", ""]
    lines += [f"- {n}" for n in notes] or ["- no new critical or high findings"]
    lines += [f"- {sev}: {n} finding(s), not opened as issues" for sev, n in sorted(other.items())]
    lines += [f"- {b['where']}: {b['reason']}" for b in digests.get("unscannable", [])]
    text = "\n".join(lines) + "\n"
    sys.stdout.write(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
