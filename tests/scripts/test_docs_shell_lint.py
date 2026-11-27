# your-service-name — adaptable service template (see wiki/getting-started.md)
"""scripts/ci/docs_shell_lint.py: the rules, on small documents, and the real setup guides."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci" / "docs_shell_lint.py"
FENCE = "`" * 3


def run(*files: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-I", str(SCRIPT), *map(str, files)], capture_output=True, text=True, check=False
    )


def doc(tmp_path: Path, body: str, lang: str = "bash") -> Path:
    path = tmp_path / "doc.md"
    path.write_text(f"# t\n\n{FENCE}{lang}\n{body}\n{FENCE}\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("body", "rule"),
    [
        ("ls # what's this", "comment"),
        ("# a comment line", "comment"),
        ("echo hello", "echo"),
        ("true && echo hello", "echo"),
        ("curl http://$H:443/x", "brace"),
        ('curl "http://$H:443/x"', "brace"),
        ("kubectl get pod <name>", "placeholder"),
        ("printf 'open", "quote"),
    ],
)
def test_a_rule_fires(tmp_path: Path, body: str, rule: str) -> None:
    result = run(doc(tmp_path, body))
    assert result.returncode == 1
    assert f": {rule}:" in result.stdout


@pytest.mark.parametrize(
    "body",
    [
        "printf '%s\\n' \"${H}:443\"",
        "printf '%s\\n' '# not a comment' 'echo' '$H:443' '<name>'",
        'printf "%s\\n" "a # b" "${#}"',
        "kubectl apply -f - <<'EOF'\n# yaml comment, it's data\nkey: <value>\nEOF\nprintf ok",
        "printf 'a'\\\n  'b'",
    ],
)
def test_safe_blocks_pass(tmp_path: Path, body: str) -> None:
    result = run(doc(tmp_path, body))
    assert result.returncode == 0, result.stdout


def test_other_languages_are_not_checked(tmp_path: Path) -> None:
    assert run(doc(tmp_path, "# a comment, echo", lang="yaml")).returncode == 0
    assert run(doc(tmp_path, "# a comment, echo", lang="text")).returncode == 0

