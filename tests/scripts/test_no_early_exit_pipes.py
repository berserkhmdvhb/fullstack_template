"""No shell script under `pipefail` pipes a producer into a reader that stops early.

`producer | grep -q`, `| head` or `| awk '... exit'` lets the reader exit at the first match; a
producer still writing then dies of SIGPIPE, `pipefail` makes the pipeline fail, and an `if` takes
the wrong branch (or `set -e` aborts an assignment). Capture the output first and match it with a
here-string, or let the producer stop itself (`find -print -quit`). The few that remain are listed
with the reason each is safe.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EARLY = re.compile(r"\|\s*(grep\s[^|]*-[a-zA-Z]*[qm][a-zA-Z0-9]*|head\b|awk\s.*\bexit\b)")
# file -> fragment of the line -> why it is safe.
REVIEWED = {
    "agent/skills/add-supply-chain-scanner/scripts/new-scanner.sh": {
        "| head -n 1 || true": "|| true: a failed status is ignored and head already has its line",
    },
    "infra/runtime/entrypoint.sh": {
        "| head -n1 || true": "|| true: a failed status is ignored and head already has its line",
    },
    "infra/security/local/report.sh": {
        '"$(printf': "the substitution is an argument; its status is never checked",
    },
    "infra/security/tools-install.sh": {
        "| head -n1": "frozen; find in a fresh 2-level extract of one archive: a line or two",
    },
    "infra/security/verify-image-digest.sh": {
        "| head -n1": "frozen; the input is one image reference, so sed prints at most one line",
    },
    "scripts/db-backup.sh": {
        "| head -n1' || true": "runs in the Pod's sh, without pipefail, and || true besides",
    },
}


def _scripts() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.sh"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [ROOT / n for n in out.splitlines()]


def early_exit_pipes(path: Path) -> list[str]:
    text = path.read_text("utf-8")
    if "pipefail" not in text:
        return []
    # Join continued lines, so a pipe at the start of a continuation counts.
    lines = text.replace("\\\n", " ").splitlines()
    return [ln.strip() for ln in lines if not ln.lstrip().startswith("#") and EARLY.search(ln)]


def test_no_unreviewed_early_exit_reader_under_pipefail() -> None:
    found = {}
    for path in _scripts():
        name = str(path.relative_to(ROOT))
        reviewed = REVIEWED.get(name, {})
        bad = [ln for ln in early_exit_pipes(path) if not any(f in ln for f in reviewed)]
        if bad:
            found[name] = bad
    assert found == {}


def test_every_reviewed_entry_still_exists() -> None:
    for name, fragments in REVIEWED.items():
        lines = early_exit_pipes(ROOT / name)
        for fragment in fragments:
            assert [ln for ln in lines if fragment in ln], (name, fragment)


def test_the_pattern_catches_the_shapes() -> None:
    for line in (
        "if helm get manifest r | grep -qx 'kind: Namespace'; then",
        'x="$(cmd | head -n 1)"',
        "pod=\"$(kubectl get pods | awk '{print $1; exit}')\"",
        "cmd | grep -m1 x",
        'if printf "%s" "$logs" | grep -qi x; then',
    ):
        assert EARLY.search(line), line
    assert not EARLY.search('grep -q x <<<"$out"')


def test_the_scanners_fallback_greps_files_in_batches() -> None:
    for name in ("infra/security/local/scan-iac.sh", "infra/security/local/scan-k8s.sh"):
        text = (ROOT / name).read_text("utf-8").replace("\\\n", " ")
        greps = [
            ln for ln in text.splitlines() if "-exec grep" in ln and not ln.lstrip().startswith("#")
        ]
        assert greps, name
        for line in greps:
            assert "{} +" in line, (name, line)
