"""Every Helm upgrade and rollback the template runs applies server-side and forces conflicts.

Helm 4 applies server-side; a hand edit (kubectl scale, set image, set env, patch) makes its field
manager own the field, and an upgrade or rollback without --force-conflicts then fails on the
conflict, the automatic rollback too. --force-conflicts is refused on a client-side apply, which
Helm 4's default (`--server-side auto`) picks for a release last applied client-side (one Helm 3
made), so each call passes --server-side=true with it. Git is the source of truth: the deploy always
wins. Static, offline: shell continuation lines are joined, Python calls are read with ast.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

# It reads every tracked script, workflow and kind test.
pytestmark = pytest.mark.tree_wide

REPO = Path(__file__).resolve().parents[2]
FLAGS = ("--server-side=true", "--force-conflicts")
# A shell invocation, not prose: a command line or a bash array.
SHELL_CALL = re.compile(
    r"^\s*(helm|helm_as|hc)\s[^#]*\b(upgrade|rollback)\b"
    r"|=\(helm (upgrade|rollback)\b|helm=\((upgrade|rollback)\b"
)
SKIP = ("wiki/", "tests/supply_chain/", "tests/scripts/", "tests/backend/")


def _tracked(*patterns: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--", *patterns],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return [n for n in out if not n.startswith(SKIP) and (REPO / n).is_file()]


def shell_calls(text: str) -> list[str]:
    """Each helm upgrade/rollback command line, its continuation lines joined."""
    joined = re.sub(r"\\\n\s*", " ", text)
    return [ln.strip() for ln in joined.splitlines() if SHELL_CALL.search(ln)]


def _strings(node: ast.AST) -> list[str]:
    return [
        n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def python_calls(text: str) -> list[list[str]]:
    """The string items of every list literal that runs `helm ... upgrade|rollback`."""
    out = []
    for node in ast.walk(ast.parse(text)):
        if not isinstance(node, ast.List):
            continue
        items = [
            e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
        ]
        starred = [ast.unparse(e) for e in node.elts if isinstance(e, ast.Starred)]
        if items and items[0] in {"upgrade", "rollback"} and any("helm" in s for s in starred):
            out.append(_strings(node))
    return out


def _calls() -> list[tuple[str, str]]:
    found = []
    for name in _tracked("*.sh", "*.yml", "Makefile"):
        found += [(name, c) for c in shell_calls((REPO / name).read_text(encoding="utf-8"))]
    for name in _tracked("*.py"):
        text = (REPO / name).read_text(encoding="utf-8")
        found += [(name, " ".join(c)) for c in python_calls(text)]
    return found


def test_every_upgrade_and_rollback_applies_server_side_and_forces_conflicts() -> None:
    calls = _calls()
    names = {n for n, _ in calls}
    assert {
        "scripts/ci/deploy-env.sh",
        ".github/workflows/deploy-ops.yml",
        "scripts/k8s-env.sh",
        "scripts/kind-deploy.sh",
        "scripts/migrate-to-bootstrap.sh",
        "tests/k8s/_kube.py",
        "tests/k8s/test_migrations.py",
        "tests/k8s/test_external_secrets.py",
        "tests/k8s/test_tracing_redis.py",
        "tests/k8s/test_drift.py",
    } <= names, names
    for name, call in calls:
        for flag in FLAGS:
            assert flag in re.split(r"[\s()\[\]]+", call), (name, call)


def test_the_printed_rollback_hint_carries_both_flags() -> None:
    text = (REPO / "scripts" / "migrate-to-bootstrap.sh").read_text(encoding="utf-8")
    hint = next(ln for ln in text.splitlines() if "(helm rollback" in ln)
    for flag in FLAGS:
        assert flag in hint


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("helm upgrade --install r c \\\n  --wait \\\n  --server-side=true --force-conflicts\n", 1),
        ("  helm_as upgrade --install r c -f x\n", 1),
        ("# helm upgrade is mentioned here\n", 0),
    ],
)
def test_shell_calls_join_continuation_lines(text: str, found: int) -> None:
    calls = shell_calls(text)
    assert len(calls) == found
    if found and "--wait" in text:
        assert "--force-conflicts" in calls[0]


def test_python_calls_are_read_across_lines() -> None:
    text = (
        'cmd = [\n    *self.helm,\n    "upgrade",\n    "--install",\n    "r",\n]\nx = ["upgrade"]\n'
    )
    assert python_calls(text) == [["upgrade", "--install", "r"]]


# A documented command: `helm` (or a path to it) with upgrade or rollback, in a fenced code block.
DOC_CALL = re.compile(r"^\s*(?:\$\s+)?(?:\S*/)?helm\s+(?:[^#\n]*?\s)?(upgrade|rollback)\b")


def doc_calls(text: str) -> list[str]:
    out = []
    for block in re.findall(r"```[^\n]*\n(.*?)```", text, re.DOTALL):
        joined = re.sub(r"\\\n\s*", " ", block)
        out += [
            ln.strip()
            for ln in joined.splitlines()
            if DOC_CALL.search(ln) and not ln.lstrip().startswith("#")
        ]
    return out


def test_every_documented_upgrade_and_rollback_carries_both_flags() -> None:
    """A hand-run command copied from the docs behaves as the deploy does."""
    files = subprocess.run(
        ["git", "ls-files", "--", "*.md"],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    seen = 0
    for name in files:
        if name == "wiki/changelog.md":
            continue
        for call in doc_calls((REPO / name).read_text(encoding="utf-8")):
            seen += 1
            for flag in FLAGS:
                assert flag in call.split(), (name, call)
    assert seen >= 10  # noqa: PLR2004 - the README, the runbook, the guides


def test_doc_calls_join_continuations_and_skip_prose() -> None:
    text = "x `helm upgrade` y\n```bash\n# helm rollback a 1\nhelm upgrade r c \\\n  --wait\n```\n"
    assert [" ".join(c.split()) for c in doc_calls(text)] == ["helm upgrade r c --wait"]


# A command in a comment: a `#` line that starts with it (continuations joined, an inline comment
# after the backslash dropped), or one in backticks. A bare `helm upgrade`, or one mid-sentence, is
# prose.
COMMENT_CALL = re.compile(r"^(?:\S*/)?helm\s+(?:upgrade|rollback)\s+\S")
INLINE_CALL = re.compile(r"`((?:\S*/)?helm\s+(?:upgrade|rollback)\s+[^`]+)`")


def comment_calls(text: str) -> list[str]:
    body = "\n".join(
        re.sub(r"\\\s+#.*$", r"\\", m[1])
        for m in re.finditer(r"^[ \t]*#[ \t]?(.*)$", text, re.MULTILINE)
    )
    joined = re.sub(r"\\\n\s*", " ", body)
    lines = [" ".join(ln.split()) for ln in joined.splitlines()]
    return [ln for ln in lines if COMMENT_CALL.match(ln)] + INLINE_CALL.findall(joined)


def test_every_upgrade_in_an_example_or_a_chart_comment_carries_both_flags() -> None:
    """The commands a values file's comments give (copied by hand) behave as the deploy does."""
    files = _tracked("*.example", "charts/**")
    seen = 0
    for name in files:
        for call in comment_calls((REPO / name).read_text(encoding="utf-8")):
            seen += 1
            for flag in FLAGS:
                assert flag in call.split(), (name, call)
    assert seen >= 3  # noqa: PLR2004 - values.yaml, values.secret and values.cluster examples


def test_comment_calls_join_continuations_and_skip_prose() -> None:
    text = (
        "#   helm upgrade --install r c \\\n"
        "#     -f a.yaml \\            # or qa\n"
        "#     -n ns\n"
        "# a later helm upgrade must not delete it; `helm rollback` is safe\n"
        "# (`helm rollback r 1 --wait`)\n"
        "x: 1  # not a comment line: helm upgrade r c\n"
    )
    assert comment_calls(text) == [
        "helm upgrade --install r c -f a.yaml -n ns",
        "helm rollback r 1 --wait",
    ]
