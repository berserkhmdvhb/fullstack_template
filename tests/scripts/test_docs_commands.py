# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Docs must not carry the invalid `kubectl create job --from=job/...` (CronJob only)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

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


# A code span or a fenced line holding `make <target>` (any flags and VAR=value before the target).
# A placeholder target (`make security-<step>`, `make kind-*-test`) is not one.
_MAKE = re.compile(r"(?:^|[\s;&|(])make\s+(?:-\S+\s+)*(?:[A-Z_]+=\S*\s+)*([a-z][\w-]*)(?![\w<*-])")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
# A path under a scripts/ directory: the repo's, the frontend's or a skill's (the docs name each
# relative to where it lives).
_SCRIPT_PATH = re.compile(r"(?<![\w./-])scripts/[\w./-]*[\w/]")
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


def make_targets_named(text: str) -> set[str]:
    return {m[1] for bit in _code(text) for m in _MAKE.finditer(bit)}


def _makefile_targets() -> set[str]:
    text = (ROOT / "Makefile").read_text("utf-8")
    return set(re.findall(r"^([A-Za-z0-9][\w.%-]*)\s*::?(?!=)", text, re.MULTILINE))


def missing_targets(named: set[str], targets: set[str]) -> set[str]:
    patterns = [re.compile(t.replace("%", ".+")) for t in targets if "%" in t]
    return {t for t in named if t not in targets and not any(p.fullmatch(t) for p in patterns)}


def test_every_make_target_the_docs_name_exists() -> None:
    targets = _makefile_targets()
    offenders = [
        f"{p.relative_to(ROOT)}: make {t}"
        for p in _docs()
        for t in sorted(missing_targets(make_targets_named(p.read_text("utf-8")), targets))
    ]
    assert offenders == []


def test_every_scripts_path_the_docs_name_exists() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    # "/<path>" and "/<path>/" for every tracked file and each directory above it.
    known = {f"/{n}" for n in tracked} | {
        f"/{n[: i + 1]}" for n in tracked for i, c in enumerate(n) if c == "/"
    }
    offenders = [
        f"{p.relative_to(ROOT)}: {m[0]}"
        for p in _docs()
        if str(p.relative_to(ROOT)) not in _HISTORY
        for m in _SCRIPT_PATH.finditer(p.read_text("utf-8"))
        if not any(k.endswith(f"/{m[0]}") for k in known)
    ]
    assert offenders == []


def test_the_make_target_scan_reads_code_only_and_skips_placeholders() -> None:
    text = "make sure of it\n`make test` and `HELM_ENV=dev make k8s-apply`\n"
    text += "`make security-<step>` `make kind-*-test`\n```bash\nmake -s lock-check\n```\n"
    assert make_targets_named(text) == {"test", "k8s-apply", "lock-check"}
    assert missing_targets({"test", "version-check-ruff", "nope"}, {"test", "version-check-%"}) == {
        "nope"
    }
