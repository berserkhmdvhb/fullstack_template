"""Doc guards over the published docs (wiki/, docs-site/src/content/, README.md, CONTRIBUTING.md).

- No `kubectl exec ... -- bash|sh|curl|env|nslookup|/bin/sh|/bin/bash` into an app pod: the
  images are distroless, so the command fails; the operations runbook's `kubectl debug` form
  is the one that works.
- No numbered list skips a number (1, 2, 4): a lost step reads as a complete list. A list that a
  code fence or a heading interrupts starts again, and so does one that restarts at 1.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCOPE = ("wiki/", "docs-site/src/content/", "README.md", "CONTRIBUTING.md")
# The database Pod runs PostgreSQL's own image, which has a shell.
EXEC = re.compile(
    r"\bkubectl\b.*\bexec\b.*\s--\s+(?:bash|sh|curl|env|nslookup|/bin/sh|/bin/bash)\b"
)
ITEM = re.compile(r"^( *)(\d+)\. ")
FENCE = re.compile(r"^\s*(```|~~~)")


def docs() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "--", *SCOPE],  # noqa: S607
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout  # fmt: skip
    return [ROOT / rel for rel in out.split() if rel.endswith((".md", ".mdx"))]


def exec_into_app_pods(text: str) -> list[int]:
    lines = text.replace("\\\n", " ").splitlines()
    return [
        n
        for n, line in enumerate(lines, 1)
        if EXEC.search(line) and "postgres" not in re.split(r"\s--\s", line)[0]
    ]


def list_gaps(text: str) -> list[str]:
    gaps: list[str] = []
    last: dict[int, int] = {}  # the last number seen at each indent
    fenced = False
    for n, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            last.clear()
            continue
        if fenced:
            continue
        if line.startswith("#"):
            last.clear()
            continue
        item = ITEM.match(line)
        if not item:
            continue
        indent, number = len(item.group(1)), int(item.group(2))
        for deeper in [i for i in last if i > indent]:
            del last[deeper]
        prev = last.get(indent)
        if prev is not None and number not in (prev + 1, 1):
            gaps.append(f"line {n}: {number} after {prev}")
        last[indent] = number
    return gaps


DOCS = docs()


def test_the_scope_holds_the_docs() -> None:
    names = {p.relative_to(ROOT).as_posix() for p in DOCS}
    assert {"README.md", "CONTRIBUTING.md", "wiki/ci-cd.md"} <= names
    assert any(n.startswith("docs-site/src/content/") for n in names)


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_no_doc_execs_a_shell_or_a_tool_into_an_app_pod(doc: Path) -> None:
    assert exec_into_app_pods(doc.read_text("utf-8")) == [], (
        f"{doc.name}: the images are distroless; use `kubectl debug` (wiki/operations-runbook.md)"
    )


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_no_numbered_list_skips_a_number(doc: Path) -> None:
    assert list_gaps(doc.read_text("utf-8")) == [], doc.name


def test_the_exec_matcher_flags_app_pods_only() -> None:
    assert exec_into_app_pods("kubectl -n ns exec -it <pod> -- bash\n") == [1]
    assert exec_into_app_pods("x\nkubectl exec -n ns <pod> -- env | grep DB_\n") == [2]
    assert exec_into_app_pods("kubectl exec <pod> \\\n  -- curl -s localhost\n") == [1]
    assert exec_into_app_pods("kubectl -n db exec <n>-postgresql-0 -- sh -c 'psql'\n") == []
    assert exec_into_app_pods("kubectl debug -it <pod> --image=x --target=backend -- sh\n") == []
    assert exec_into_app_pods("kubectl exec <pod> -- python -m x.healthcheck\n") == []
    assert exec_into_app_pods("kubectl -n ns exec <pod> -- nslookup host\n") == [1]
    assert exec_into_app_pods("kubectl -n ns exec -it <pod> -- /bin/sh\n") == [1]
    assert exec_into_app_pods("kubectl exec <pod> -- /bin/bash -c x\n") == [1]
    assert exec_into_app_pods("kubectl debug -it <pod> --image=x -- nslookup host\n") == []
    assert exec_into_app_pods("kubectl exec <pod> -- nslookup db-postgresql.svc\n") == [1]


def test_the_list_matcher_flags_a_gap_and_nothing_else() -> None:
    assert list_gaps("1. a\n2. b\n\n4. d\n") == ["line 4: 4 after 2"]
    assert list_gaps("1. a\n   1. nested\n   3. gap\n2. b\n") == ["line 3: 3 after 1"]
    assert list_gaps("1. a\n2. b\n\n```\nx\n```\n\n4. d\n") == []  # a fence interrupts
    assert list_gaps("1. a\n## Next\n3. c\n") == []  # a heading interrupts
    assert list_gaps("1. a\n2. b\n\ntext\n\n1. again\n2. b\n") == []  # a new list
    assert list_gaps("```\n1. a\n3. c\n```\n") == []  # inside a fence
