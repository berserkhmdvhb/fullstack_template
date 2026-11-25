# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Docs must not carry the invalid `kubectl create job --from=job/...` (CronJob only)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.tree_wide

ROOT = Path(__file__).resolve().parents[2]


def _docs() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.md"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [ROOT / name for name in out.splitlines()]


def test_no_doc_creates_a_job_from_a_job() -> None:
    pattern = re.compile(r"create job[^\n]*--from=job/")
    offenders = [str(p.relative_to(ROOT)) for p in _docs() if pattern.search(p.read_text("utf-8"))]
    assert offenders == []


def test_no_doc_has_a_placeholder_create_job_line() -> None:
    pattern = re.compile(r"kubectl[^\n]*create job \.\.\.")
    offenders = [str(p.relative_to(ROOT)) for p in _docs() if pattern.search(p.read_text("utf-8"))]
    assert offenders == []


def test_no_doc_lets_the_app_chart_create_its_namespace() -> None:
    """The namespaces come from the admin bootstrap (scripts/k8s-bootstrap.sh), never from helm.

    Only tracked docs are read (git ls-files), so nothing outside the public tree is checked.
    """
    offenders = []
    for p in _docs():
        commands = p.read_text("utf-8").replace("\\\n", " ").splitlines()
        if [c for c in commands if "charts/your-service-name" in c and "--create-namespace" in c]:
            offenders.append(str(p.relative_to(ROOT)))
    assert offenders == []


def test_no_doc_says_the_chart_creates_the_namespaces() -> None:
    pattern = re.compile(r"[Tt]he chart creates (them|both|\*\*both\*\*)")
    offenders = [str(p.relative_to(ROOT)) for p in _docs() if pattern.search(p.read_text("utf-8"))]
    assert offenders == []


def test_the_migration_says_the_old_quota_is_recreated_by_the_bootstrap() -> None:
    text = (ROOT / "wiki" / "infrastructure.md").read_text("utf-8")
    section = text[text.index("**An existing release from an older chart**") :]
    section = " ".join(section[: section.index("\n---")].split())
    assert "deletes the old chart's ResourceQuota and LimitRange" in section
    assert "recreates them" in section


def test_the_migration_is_one_script_that_passes_the_current_charts_values() -> None:
    """One source: the doc calls scripts/migrate-to-bootstrap.sh and restates none of its upgrades.
    The script's upgrades pass the env's values file and the deploy's values, never the stored ones:
    an older release stored keys the current schema refuses (images.nginx, ingress)."""
    text = (ROOT / "wiki" / "infrastructure.md").read_text("utf-8")
    section = text[text.index("**An existing release from an older chart**") :]
    section = section[: section.index("\n---")]
    assert "scripts/migrate-to-bootstrap.sh" in section
    assert not re.search(r"^helm upgrade", section, re.MULTILINE), "the doc restates the script"
    script = (ROOT / "scripts" / "migrate-to-bootstrap.sh").read_text("utf-8")
    code = "\n".join(ln for ln in script.splitlines() if not ln.lstrip().startswith("#"))
    assert "reuse-values" not in code
    assert 'values=(-f "$CHART/values.$ENV_NAME.yaml")' in code


# A code span or a fenced line holding `make <targets>`: the words up to an operator or a comment.
_MAKE = re.compile(r"(?:^|[\s;&|(])make\s+([^;&|)#]*)")
_TARGET = re.compile(r"[a-z][\w.-]*")
# A placeholder target (`make security-<step>`, `make kind-*-test`) is not one.
_PLACEHOLDER = re.compile(r"[<*{]")
# make's options that take the next word as their argument.
_OPTION_ARG = {"-C", "-f", "-I", "-j", "-o", "-W"}
_ASSIGNMENT = re.compile(r"[A-Z_][A-Z0-9_]*=")
# A `/` between alternatives (`make test-e2e / test-live`) or an option.
_SKIPPED = re.compile(r"/$|-")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
# A path under a scripts/ directory: the repo's, the frontend's or a skill's (the docs name each
# relative to where it lives), with or without a leading `./`.
_SCRIPT_PATH = re.compile(r"(?<![\w./-])(?:\./)?(scripts/[\w./-]*[\w/])")
# The changelog records what was removed, by name.
_HISTORY = {"wiki/changelog.md"}


def _code(text: str) -> list[str]:
    """The fenced lines and the inline code spans of a Markdown text."""
    out: list[str] = []
    fenced = False
    for line in text.splitlines():
        if _FENCE.match(line):
            fenced = not fenced
        elif fenced:
            out.append(line)
        else:
            out += [m[2] for m in re.finditer(r"(`+)(.+?)\1", line)]
    return out


def _targets(words: list[str]) -> list[str]:
    """The targets of one make command's words: options, VAR=value and `/` separators skipped."""
    found: list[str] = []
    it = iter(words)
    for word in it:
        if word in {"--", "\\"}:
            break
        if word in _OPTION_ARG:
            next(it, None)
        elif _SKIPPED.match(word) or _ASSIGNMENT.match(word) or _PLACEHOLDER.search(word):
            continue
        elif _TARGET.fullmatch(word):
            found.append(word)
        else:
            break
    return found


def make_targets_named(text: str) -> set[str]:
    return {t for bit in _code(text) for m in _MAKE.finditer(bit) for t in _targets(m[1].split())}


def _makefile_targets() -> set[str]:
    text = (ROOT / "Makefile").read_text("utf-8")
    return set(re.findall(r"^([A-Za-z0-9][\w.%-]*)\s*::?(?!=)", text, re.MULTILINE))


def missing_targets(named: set[str], targets: set[str]) -> set[str]:
    patterns = [re.compile(t.replace("%", ".+")) for t in targets if "%" in t]
    return {t for t in named if t not in targets and not any(p.fullmatch(t) for p in patterns)}


def test_every_make_target_the_docs_name_exists() -> None:
    targets = _makefile_targets()
    named = {p: make_targets_named(p.read_text("utf-8")) for p in _docs()}
    # The scan finds real targets, among them the later ones of a multi-target command
    # (`make kind-up kind-deploy`, AGENTS.md's `make test-integration / test-e2e / …`).
    assert {"test", "check-all-dry", "kind-deploy", "test-contract", "test-live"} <= set().union(
        *named.values()
    )
    offenders = [
        f"{p.relative_to(ROOT)}: make {t}"
        for p, found in named.items()
        for t in sorted(missing_targets(found, targets))
    ]
    assert offenders == []


def _tracked() -> list[str]:
    return subprocess.run(
        ["git", "ls-files"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()


def _known(tracked: list[str]) -> set[str]:
    """Each tracked file and each directory above it, as "/<path>" and "/<path>/"."""
    return {f"/{n}" for n in tracked} | {
        f"/{n[: i + 1]}" for n in tracked for i, c in enumerate(n) if c == "/"
    }


def _exists(path: str, known: set[str]) -> bool:
    """A path exists when it is a tracked path or the tail of one (docs name paths relative to
    the directory they describe: `e2e/smoke.spec.ts` for frontend/e2e/smoke.spec.ts)."""
    return any(k.endswith((f"/{path}", f"/{path}/")) for k in known)


def test_every_scripts_path_the_docs_name_exists() -> None:
    known = _known(_tracked())
    found = [(p, m[1]) for p in _docs() for m in _SCRIPT_PATH.finditer(p.read_text("utf-8"))]
    assert any(path == "scripts/k8s-bootstrap.sh" for _, path in found)
    offenders = [
        f"{p.relative_to(ROOT)}: {path}"
        for p, path in found
        if str(p.relative_to(ROOT)) not in _HISTORY and not _exists(path, known)
    ]
    assert offenders == []


# A repo-relative path in a code span: words joined by "/", not part of a URL, a host or an
# expression, and not cut short by a placeholder (`values.<env>.yaml`, `scan-*.sh`).
_CODE_PATH = re.compile(r"(?<![\w./@:$\\-])((?:[\w.-]+/)+[\w.-]*\w)(?![\w<*{$/.\\-]|\.[\w<*{])")
_FILE = re.compile(r"\.(ya?ml|sh|py|md|toml|json|tsx?|mjs|js|bats|tpl|txt|cfg|ini)$")
# Paths the docs name on purpose that are not tracked, each with its reason.
_UNTRACKED = {
    r"src/your_service_name/_version\.py": "written by setuptools-scm at build time, ignored",
    r"(frontend/)?\.next(/.*)?": "the Next.js build output",
    r"\.local/.*": "written by make kind-up, ignored",
    r"frontend/node_modules": "installed by pnpm install",
    r"charts/your-service-name/values\.secret\.yaml": "a local secrets file, ignored",
    r"path/to/.*": "a placeholder path",
    r".*your-feature.*": "the developer guide's example of a feature to add",
    r"[\w-]+(\.[\w-]+)*\.example/.*": "a URL on a placeholder host, not a path",
    r"frontend/middleware\.ts": "a troubleshooting entry names an older image's file",
}


def code_paths(text: str, top: set[str]) -> set[str]:
    """The repo-relative paths in the code of a Markdown text: under a tracked top-level
    directory, or a file name with an extension."""
    found = set()
    for bit in _code(text):
        for m in _CODE_PATH.finditer(bit):
            path = m[1].removeprefix("./")
            if path.split("/")[0] in top or _FILE.search(path):
                found.add(path)
    return found


# The pages the adapter leaves alone (infra/adapt/excludes.txt) keep the template's placeholder
# paths, which an adopted copy has renamed. Spelt as a class so the adapter leaves this one alone.
_PLACEHOLDER_PATH = re.compile(r"(src/your[_]service[_]name|charts/your[-]service[-]name)(/.*)?")
_KEPT = [
    ln.strip()
    for ln in (ROOT / "infra/adapt/excludes.txt").read_text(encoding="utf-8").splitlines()
    if ln.strip() and not ln.startswith("#")
]


def keeps_the_placeholder(doc: str, path: str) -> bool:
    """A placeholder path in a page the adapter leaves alone, which an adopted copy has renamed."""
    kept = any(doc == k or doc.startswith(k.rstrip("/") + "/") for k in _KEPT)
    return kept and bool(_PLACEHOLDER_PATH.fullmatch(path))


def _chart_hosts() -> list[str]:
    """A URL on one of the chart's hosts (api.<domain>/openapi.json): the template's placeholder
    hosts, or the adopter's once infra/adapt/adopt.sh has stamped the domain."""
    values = yaml.safe_load(next(ROOT.glob("charts/*/values.yaml")).read_text(encoding="utf-8"))
    return [re.escape(host) + "/.*" for host in values["edge"]["hosts"].values()]


def missing_paths(named: set[str], known: set[str]) -> set[str]:
    allowed = [re.compile(p) for p in (*_UNTRACKED, *_chart_hosts())]
    return {p for p in named if not _exists(p, known) and not any(a.fullmatch(p) for a in allowed)}


def test_every_repo_path_the_docs_name_in_code_exists() -> None:
    tracked = _tracked()
    known = _known(tracked)
    top = {n.split("/")[0] for n in tracked if "/" in n}
    named = {p: code_paths(p.read_text("utf-8"), top) for p in _docs()}
    assert {"scripts/k8s-bootstrap.sh", ".github/workflows/ci.yml"} <= set().union(*named.values())
    offenders = [
        f"{p.relative_to(ROOT)}: {path}"
        for p, found in named.items()
        if str(p.relative_to(ROOT)) not in _HISTORY
        for path in sorted(missing_paths(found, known))
        if not keeps_the_placeholder(str(p.relative_to(ROOT)), path)
    ]
    assert offenders == []


def test_the_path_scan_skips_urls_placeholders_and_expressions() -> None:
    text = "`ci/security.yml` `./run_backend.sh` `charts/x/values.<env>.yaml` `scan-*.sh`\n"
    text += "`https://example.com/a/b.md` `ghcr.io/o/r/backend` `$HOME/.kube/c.yaml` `a\\.b/c`\n"
    assert code_paths(text, {"charts"}) == {"ci/security.yml", "run_backend.sh"}
    assert missing_paths({"ci/security.yml", "path/to/x.py"}, {"/run_backend.sh"}) == {
        "ci/security.yml"
    }
    host = _chart_hosts()[0].removesuffix("/.*").replace("\\", "")
    assert missing_paths({f"{host}/openapi.json", f"{host}.x/a.json"}, set()) == {
        f"{host}.x/a.json"
    }


def test_only_a_page_the_adapter_leaves_alone_may_keep_a_placeholder_path() -> None:
    package, chart = "src/your_" + "service_name/main.py", "charts/your-" + "service-name"
    assert keeps_the_placeholder("CONTRIBUTING.md", package)
    assert keeps_the_placeholder("wiki/adaptation.md", chart)
    assert not keeps_the_placeholder("wiki/operations-runbook.md", chart)
    assert not keeps_the_placeholder("CONTRIBUTING.md", "src/acme/main.py")


def test_the_make_target_scan_reads_code_only_and_skips_placeholders() -> None:
    text = "make sure of it\n`make test` and `HELM_ENV=dev make k8s-apply`\n"
    text += "`make security-<step>` `make kind-*-test`\n```bash\nmake -s lock-check\n```\n"
    text += "`make kind-up kind-deploy` `make test-e2e / test-live` `make -C x lint FOO=1 # c`\n"
    text += '`make smoke-test URL=u -- smoke-test --out a` `make k8s-logs ARGS="-f x" y`\n'
    assert make_targets_named(text) == {
        "test", "k8s-apply", "lock-check", "kind-up", "kind-deploy", "test-e2e", "test-live",
        "lint", "smoke-test", "k8s-logs",
    }  # fmt: skip
    assert missing_targets({"test", "version-check-ruff", "nope"}, {"test", "version-check-%"}) == {
        "nope"
    }
