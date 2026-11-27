#!/usr/bin/env python3
# your-service-name — adaptable service template (see wiki/getting-started.md)
"""docs_shell_lint.py: the shell blocks of the setup guides must paste safely into zsh and bash.

Checks every fenced ```bash, ```sh, ```zsh and ```shell block of the given Markdown files
(default: the setup guides an adopter pastes from, GUIDES below):

  comment      no comment in a code block: an apostrophe in one opens a quote in zsh, and a
               trailing one becomes arguments. Put the expected result in prose.
  echo         no `echo` as a command: printf behaves the same in every shell.
  brace        `${NAME}` is braced before a colon: zsh reads `$NAME:e` as a modifier.
  placeholder  no bare `<placeholder>` outside quotes: the shell reads it as a redirect.
  quote        every quote closes inside its block.

Heredoc bodies are data and are skipped. A guide in PENDING is reported but does not fail the
run; take its name out when it is clean, and it blocks like the others.
Usage: docs_shell_lint.py [FILE...]
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUIDES = ("cloudflare-setup.md", "gke-cluster-setup.md", "platform-setup.md")
PENDING: set[str] = set()
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*([A-Za-z0-9_+-]*)")
_SHELLS = {"bash", "sh", "zsh", "shell"}
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_ECHO = re.compile(r"(?:^|[;&|(]|\bthen\b|\bdo\b|\$\()\s*echo\b")
_BRACE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*:")
_PLACEHOLDER = re.compile(r"(?<![<\w])<[A-Za-z][\w.-]*>")


@dataclass
class Quote:
    """The open quote carried from line to line: "", "'" or '"'; ansi for $'...'."""

    char: str = ""
    ansi: bool = False
    opened: int = 0


def _mask(line: str, n: int, q: Quote) -> tuple[str, str, bool]:
    """The line with every quote's content blanked, the line with only the single quotes' blanked,
    and whether an unquoted comment starts on it (the rest of the line is dropped then)."""
    code: list[str] = []
    dq: list[str] = []
    i = 0
    while i < len(line):
        c = line[i]
        if q.char == "'":
            if c == "\\" and q.ansi:
                i += 1
            elif c == "'":
                q.char, q.ansi = "", False
            shown = c if not q.char else "_"
            code.append(shown)
            dq.append(shown)
        elif q.char == '"':
            if c == "\\":
                i += 1
                code.append("__")
                dq.append("__")
            else:
                q.char = "" if c == '"' else q.char
                code.append(c if not q.char else "_")
                dq.append(c)
        elif c == "\\":
            i += 1
            code.append("__")
            dq.append("__")
        elif c in "'\"":
            q.char, q.opened = c, n
            q.ansi = c == "'" and i > 0 and line[i - 1] == "$"
            code.append(c)
            dq.append(c)
        elif c == "#" and (i == 0 or line[i - 1].isspace()):
            return "".join(code), "".join(dq), True
        else:
            code.append(c)
            dq.append(c)
        i += 1
    return "".join(code), "".join(dq), False


def _scan(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """The problems of one block: (line number, rule and message)."""
    found: list[tuple[int, str]] = []
    q = Quote()
    terminator = ""
    for n, line in lines:
        if terminator:
            terminator = "" if line.strip() == terminator else terminator
            continue
        code, dq, comment = _mask(line, n, q)
        if comment:
            found.append((n, "comment: no comment in a code block (put the result in prose)"))
        if _ECHO.search(code):
            found.append((n, "echo: use printf"))
        if _BRACE.search(dq):
            found.append((n, "brace: write ${NAME} before a colon (zsh: $NAME:e is a modifier)"))
        if _PLACEHOLDER.search(code):
            found.append(
                (n, "placeholder: a bare <name> is a redirect; quote it or use a variable")
            )
        heredoc = _HEREDOC.search(line) if "<<" in code and not q.char else None
        terminator = heredoc[2] if heredoc else ""
    if q.char:
        found.append((q.opened, f"quote: the {q.char} opened here never closes in the block"))
    return found


def lint(path: Path) -> list[str]:
    """The problems of one Markdown file, as `file:line: message`."""
    out: list[str] = []
    block: list[tuple[int, str]] = []
    fence = ""
    shell = False
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        m = _FENCE.match(line)
        if not fence:
            if m:
                fence, shell, block = m[1], m[2] in _SHELLS, []
        elif m and m[1][0] == fence[0] and len(m[1]) >= len(fence) and not line.strip(" `~"):
            if shell:
                out += [f"{path}:{k}: {msg}" for k, msg in _scan(block)]
            fence = ""
        else:
            block.append((n, line))
    return out


def main(argv: list[str]) -> int:
    files = [Path(a) for a in argv] or [ROOT / "wiki" / g for g in GUIDES]
    blocking = 0
    for f in files:
        problems = lint(f)
        pending = not argv and f.name in PENDING
        for p in problems:
            sys.stdout.write(f"{'pending ' if pending else ''}{p}\n")
        blocking += 0 if pending else len(problems)
    if blocking:
        sys.stderr.write(f"docs-shell-lint: {blocking} problem(s) in the docs' shell blocks\n")
        return 1
    sys.stdout.write(f"docs-shell-lint: {len(files)} file(s) checked, no blocking problem\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
