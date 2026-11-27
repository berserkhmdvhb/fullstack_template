#!/usr/bin/env python3
# your-service-name — adaptable service template (see wiki/getting-started.md)
"""docs_shell_lint.py — the shell blocks of the docs must paste safely into zsh and bash.

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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUIDES = ("cloudflare-setup.md", "gke-cluster-setup.md", "platform-setup.md")
PENDING = {"gke-cluster-setup.md"}
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})\s*([A-Za-z0-9_+-]*)")
_SHELLS = {"bash", "sh", "zsh", "shell"}
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")
_ECHO = re.compile(r"(?:^|[;&|(]|\bthen\b|\bdo\b|\$\()\s*echo\b")
_BRACE = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*:")
_PLACEHOLDER = re.compile(r"(?<![<\w])<[A-Za-z][\w.-]*>")


def _scan(lines: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """The problems of one block: (line number, rule and message)."""
    found: list[tuple[int, str]] = []
    quote = ""  # "", "'" or '"'
    ansi = False  # inside $'...', where a backslash escapes
    opened = 0
    terminator = ""
    for n, line in lines:
        if terminator:
            if line.strip() == terminator:
                terminator = ""
            continue
        code: list[str] = []  # the line with every quote's content blanked
        dq: list[str] = []  # the line with the single quotes' content blanked
        i = 0
        while i < len(line):
            c = line[i]
            if quote == "'":
                if c == "\\" and ansi:
                    i += 1
                elif c == "'":
                    quote, ansi = "", False
                    code.append(c)
                    dq.append(c)
                else:
                    code.append("_")
                    dq.append("_")
            elif quote == '"':
                if c == "\\":
                    i += 1
                    code.append("__")
                    dq.append("__")
                elif c == '"':
                    quote = ""
                    code.append(c)
                    dq.append(c)
                else:
                    code.append("_")
                    dq.append(c)
            elif c == "\\":
                i += 1
                code.append("__")
                dq.append("__")
            elif c in "'\"":
                quote, opened = c, n
                ansi = c == "'" and i > 0 and line[i - 1] == "$"
                code.append(c)
                dq.append(c)
            elif c == "#" and (i == 0 or line[i - 1].isspace()):
                found.append((n, "comment: no comment in a code block (put the expected result in prose)"))
                break
            else:
                code.append(c)
                dq.append(c)
            i += 1
        text, with_dq = "".join(code), "".join(dq)
        if _ECHO.search(text):
            found.append((n, "echo: use printf"))
        if _BRACE.search(with_dq):
            found.append((n, "brace: write ${NAME} before a colon (zsh reads $NAME:e as a modifier)"))
        if _PLACEHOLDER.search(text):
            found.append((n, "placeholder: a bare <name> is a redirect; quote it or use a variable"))
        if not quote and "<<" in text and (m := _HEREDOC.search(line)):
            terminator = m[2]
    if quote:
        found.append((opened, f"quote: the {quote} opened here never closes in the block"))
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
                fence, shell = m[1], m[2] in _SHELLS
                block = []
            continue
        if m and m[1][0] == fence[0] and len(m[1]) >= len(fence) and not line.strip(" `~"):
            if shell:
                out += [f"{path}:{k}: {msg}" for k, msg in _scan(block)]
            fence = ""
            continue
        block.append((n, line))
    return out


def main(argv: list[str]) -> int:
    files = [Path(a) for a in argv] or [ROOT / "wiki" / g for g in GUIDES]
    blocking = 0
    for f in files:
        problems = lint(f)
        pending = not argv and f.name in PENDING
        for p in problems:
            print(f"{'pending ' if pending else ''}{p}")
        blocking += 0 if pending else len(problems)
    if blocking:
        print(f"docs-shell-lint: {blocking} problem(s) in the docs' shell blocks", file=sys.stderr)
        return 1
    print(f"docs-shell-lint: {len(files)} file(s) checked, no blocking problem")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
