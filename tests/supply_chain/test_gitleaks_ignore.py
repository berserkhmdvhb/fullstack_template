"""`.gitleaksignore` may only silence one exact, explained finding, never a pattern.

gitleaks reads `.gitleaksignore` from the repository root. A line there is a finding's
fingerprint, `<commit sha>:<path>:<rule id>:<line>`, which matches that one finding in that one
commit and nothing else. A false positive already in the history cannot be fixed with an inline
`gitleaks:allow` comment, because the commit that added it is scanned as it was. The fingerprint
is the narrow fix.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
IGNORE = ROOT / ".gitleaksignore"
# A full 40-character commit, a literal path (no glob), a rule id, a line number.
FINGERPRINT = re.compile(r"^[0-9a-f]{40}:[^:*?\[\]\s]+:[a-z0-9-]+:[1-9][0-9]*$")
MIN_REASON = 10
SHA = "a" * 40


def problems(text: str) -> list[str]:
    """Every entry must be an exact fingerprint with a reason comment on the line above it."""
    found = []
    previous = ""
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if line and not line.startswith("#"):
            if not FINGERPRINT.match(line):
                found.append(f"line {number}: not an exact fingerprint: {line}")
            elif not previous.startswith("#") or len(previous.lstrip("# ")) < MIN_REASON:
                found.append(f"line {number}: needs a reason comment directly above it")
        previous = line
    return found


@pytest.mark.parametrize(
    ("text", "count"),
    [
        (f"# The value is a URL path, not a key.\n{SHA}:tests/a.py:generic-api-key:7\n", 0),
        (f"{SHA}:tests/a.py:generic-api-key:7\n", 1),
        (f"# short\n{SHA}:tests/a.py:generic-api-key:7\n", 1),
        (f"# The value is a URL path, not a key.\n\n{SHA}:tests/a.py:generic-api-key:7\n", 1),
        ("# The value is a URL path, not a key.\n5dd6650:tests/a.py:generic-api-key:7\n", 1),
        (f"# The value is a URL path, not a key.\n{SHA}:tests/*:generic-api-key:7\n", 1),
        ("# The value is a URL path, not a key.\ntests/a.py:generic-api-key:7\n", 1),
        ("# The value is a URL path, not a key.\ngeneric-api-key\n", 1),
    ],
)
def test_problems_rejects_patterns_and_unexplained_entries(text: str, count: int) -> None:
    assert len(problems(text)) == count


def test_the_ignore_file_holds_only_explained_exact_fingerprints() -> None:
    text = IGNORE.read_text(encoding="utf-8") if IGNORE.exists() else ""
    assert problems(text) == []
