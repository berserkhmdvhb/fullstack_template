"""pnpm's settings for the frontend and the docs site (each a project of its own, configured in its
pnpm-workspace.yaml), and the review of every dependency build script.

pnpm runs a dependency's build script (preinstall, install, postinstall) only when `allowBuilds`
approves it, and with `strictDepBuilds` an install fails on a package that declares one and is not
listed. So `allowBuilds` is the reviewed policy, and here:
- a denial (`false`) is keyed by the bare package name, so a version bump stays denied, and names a
  package the lockfile holds (a stale entry is a failure);
- an approval (`true`) is `name@<exact locked version>` with its own row in the Exceptions section
  of wiki/frontend-deployment.md, so a bump of an approved package forces a re-review;
- a range, a `||` list or an empty name is rejected: only a bare name or one exact version.
Installs run without `--ignore-scripts`: that flag also turns the strict check off (an unreviewed
build then passes silently), while the policy above already runs none.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from tests.supply_chain._pins import pin

ROOT = Path(__file__).resolve().parent.parent.parent
PROJECTS = ("frontend", "docs-site")
DOC = (ROOT / "wiki/frontend-deployment.md").read_text(encoding="utf-8")
NAME = r"(?:@[a-z0-9~._-]+/)?[a-z0-9~._-]+"
EXACT = r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"
BARE = re.compile(NAME)
PINNED = re.compile(rf"({NAME})@({EXACT})")
# Reviewed trust-policy exceptions, each with its reason in the project's pnpm-workspace.yaml and in
# wiki/frontend-deployment.md. A new entry is a review: add it here too.
REVIEWED_TRUST = {"frontend": ["chromium-bidi@157.0.8090-0"], "docs-site": []}
# An exclusion from the 7-day floor: "- name@x.y.z  # published YYYY-MM-DD; <reason>".
AGE_EXCLUDE = re.compile(
    rf"^\s+- '?({NAME}@{EXACT})'?\s+# published (\d{{4}}-\d{{2}}-\d{{2}}); (\S.*)$"
)


def settings(project: str) -> dict[str, Any]:
    text = (ROOT / project / "pnpm-workspace.yaml").read_text(encoding="utf-8")
    return cast("dict[str, Any]", yaml.safe_load(text))


def package(project: str) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads((ROOT / project / "package.json").read_text()))


def locked(project: str) -> set[tuple[str, str]]:
    """Every name@version in the project's pnpm-lock.yaml (one YAML document; a lock that also
    records pnpm itself has the dependencies in the last one)."""
    docs = list(yaml.safe_load_all((ROOT / project / "pnpm-lock.yaml").read_text()))
    keys = docs[-1].get("packages", {})
    return {cast("tuple[str, str]", tuple(k.rsplit("@", 1))) for k in keys}


def exceptions_section(doc: str) -> str:
    """The text under the `Exceptions` heading, up to the next heading."""
    match = re.search(r"^#{2,6} Exceptions\n(.*?)(?=^#{1,6} |\Z)", doc, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else ""


def problems(lock: set[tuple[str, str]], allow: dict[str, bool], doc: str) -> list[str]:
    found: list[str] = []
    names = {n for n, _ in lock}
    section = exceptions_section(doc)
    for key, value in allow.items():
        if value is False and BARE.fullmatch(key):
            if key not in names:
                found.append(f"{key}: denied, but the lockfile has no such package")
        elif value is True and (match := PINNED.fullmatch(key)):
            if f"`{key}`" not in section:
                found.append(f"{key}: approved without a row in the Exceptions section")
            if (match[1], match[2]) not in lock:
                found.append(f"{key}: approved, but the lockfile has no such version")
        else:
            found.append(f"{key}: a denial needs a bare name, an approval name@exact-version")
    return found


def age_exclusions(text: str) -> list[tuple[str, date, str]]:
    """The minimumReleaseAgeExclude entries: (selector, publish date, reason); a malformed entry is
    returned with date.min so it fails."""
    block = re.search(r"^minimumReleaseAgeExclude:\n((?:[ \t]+.*\n|\n)*)", text, re.MULTILINE)
    out: list[tuple[str, date, str]] = []
    for line in (block[1] if block else "").splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        m = AGE_EXCLUDE.match(line)
        out.append(
            (m[1], date.fromisoformat(m[2]), m[3])
            if m
            else (re.sub(r"^-\s*", "", line.strip()), date.min, "")
        )
    return out


def stale_exclusions(entries: list[tuple[str, date, str]], today: date) -> list[str]:
    """An entry is kept only while its version is younger than 7 days: then it must go. A publish
    date in the future is a wrong date (it would keep the entry alive), so it fails too."""
    found = [
        f"{sel}: published {day}, at least 7 days ago: remove it from minimumReleaseAgeExclude"
        for sel, day, _ in entries
        if day + timedelta(days=7) <= today
    ]
    found += [f"{sel}: published {day}, in the future" for sel, day, _ in entries if day > today]
    return found


LOCK = {("old", "1.2.3"), ("fresh", "2.0.0"), ("plain", "1.0.0")}
ROW = "#### Exceptions\n\n| `old@1.2.3` | why |\n\n## Next\n"


def test_a_denial_of_a_package_the_lockfile_lacks_fails() -> None:
    assert problems(LOCK, {"old": False}, "") == []
    assert problems(LOCK, {"gone": False}, "") == [
        "gone: denied, but the lockfile has no such package"
    ]


def test_an_unpinned_approval_fails() -> None:
    assert problems(LOCK, {"old": True}, ROW)


def test_a_stale_pinned_approval_fails() -> None:
    out = problems(LOCK, {"old@1.2.2": True}, "#### Exceptions\n`old@1.2.2`\n")
    assert out == ["old@1.2.2: approved, but the lockfile has no such version"]


def test_an_approval_without_an_exceptions_row_fails() -> None:
    out = problems(LOCK, {"old@1.2.3": True}, "#### Exceptions\n\nNone.\n")
    assert out == ["old@1.2.3: approved without a row in the Exceptions section"]


def test_an_approval_documented_only_outside_the_section_fails() -> None:
    doc = "see `old@1.2.3` here\n\n#### Exceptions\n\nNone.\n\n## Other\n`old@1.2.3`\n"
    assert problems(LOCK, {"old@1.2.3": True}, doc)


def test_a_pinned_documented_approval_passes() -> None:
    assert problems(LOCK, {"old@1.2.3": True}, ROW) == []


def test_keys_that_are_not_a_bare_name_or_one_exact_version_fail() -> None:
    for key in (
        "old@^1.2.0",
        "old@~1.2.0",
        "old@>=1",
        "old@latest",
        "old@*",
        "old@1.2.3 || 1.2.4",
        "",
        "@",
        "old@",
    ):
        assert problems(LOCK, {key: False}, ROW), key
        assert problems(LOCK, {key: True}, ROW), key


@pytest.mark.parametrize("project", PROJECTS)
def test_every_build_script_decision_is_reviewed(project: str) -> None:
    allow = settings(project).get("allowBuilds", {})
    assert allow, project
    assert problems(locked(project), allow, DOC) == []


@pytest.mark.parametrize("project", PROJECTS)
def test_the_settings_hold_the_policy(project: str) -> None:
    s = settings(project)
    assert s["minimumReleaseAge"] == 7 * 24 * 60
    assert s["strictDepBuilds"] is True
    assert s["blockExoticSubdeps"] is True
    assert s["trustPolicy"] == "no-downgrade"
    # Never download another pnpm, and write no pnpm record into the lock (the lockfile test
    # below): check-pnpm-version.mjs enforces the version in CI and in the image.
    assert s["pmOnFail"] == "ignore"
    # The store and the cache in the repository's gitignored .tools/, never under $HOME.
    assert s["storeDir"] == "../.tools/pnpm/store"
    assert s["cacheDir"] == "../.tools/pnpm/cache"
    for weaker in (
        "dangerouslyAllowAllBuilds",
        "trustLockfile",
        "trustPolicyIgnoreAfter",
        "ignoreScripts",
    ):
        assert weaker not in s, (project, weaker)
    assert s.get("minimumReleaseAgeStrict", True) is True
    assert "packages" not in s, "each project is its own root, not a workspace of several"


@pytest.mark.parametrize("project", PROJECTS)
def test_the_trust_policy_exceptions_are_the_reviewed_ones(project: str) -> None:
    assert settings(project).get("trustPolicyExclude", []) == REVIEWED_TRUST[project]
    for entry in REVIEWED_TRUST[project]:
        assert f"`{entry}`" in exceptions_section(DOC), entry


def test_an_exclusion_from_the_age_floor_lasts_only_while_the_version_is_young() -> None:
    text = (
        "minimumReleaseAge: 10080\nminimumReleaseAgeExclude:\n"
        "  # a comment\n"
        "  - 'young@1.0.1'  # published 2026-11-20; GHSA-xxxx-xxxx-xxxx fixed in 1.0.1\n"
        "  - old@2.0.0  # published 2026-11-10; the build fails on 1.9.9\n"
        "  - bare\n"
        "trustPolicy: no-downgrade\n"
    )
    entries = age_exclusions(text)
    assert [e[0] for e in entries] == ["young@1.0.1", "old@2.0.0", "bare"]
    remove = "at least 7 days ago: remove it from minimumReleaseAgeExclude"
    assert stale_exclusions(entries, date(2026, 11, 25)) == [
        f"old@2.0.0: published 2026-11-10, {remove}",
        f"bare: published 0001-01-01, {remove}",
    ]
    assert stale_exclusions(entries[:1], date(2026, 11, 26)) == []
    assert stale_exclusions(entries[:1], date(2026, 11, 19)) == [
        "young@1.0.1: published 2026-11-20, in the future"
    ]
    assert stale_exclusions(entries[:1], date(2026, 11, 27)) != []


@pytest.mark.parametrize("project", PROJECTS)
def test_no_exclusion_from_the_age_floor_outlives_its_version_being_young(project: str) -> None:
    text = (ROOT / project / "pnpm-workspace.yaml").read_text(encoding="utf-8")
    entries = age_exclusions(text)
    assert len(entries) == len(settings(project).get("minimumReleaseAgeExclude") or [])
    assert stale_exclusions(entries, datetime.now(UTC).date()) == []
    assert all(reason for _, _, reason in entries)


@pytest.mark.parametrize("project", PROJECTS)
def test_package_json_names_the_pinned_pnpm_and_no_npm_policy(project: str) -> None:
    pkg = package(project)
    assert pkg["packageManager"] == f"pnpm@{pin('pnpm')}"
    # npm's fields would be dead here: pnpm reads overrides and build decisions from its own file.
    for field in ("allowScripts", "overrides", "pnpm"):
        assert field not in pkg, (project, field)
    # The project's own install-time hooks would run on every install: there are none.
    assert not {"preinstall", "install", "postinstall", "prepare"} & set(pkg["scripts"]), project


@pytest.mark.parametrize("project", PROJECTS)
def test_pnpm_is_the_only_package_manager(project: str) -> None:
    for name in ("package-lock.json", "npm-shrinkwrap.json", ".npmrc", "yarn.lock"):
        assert not (ROOT / project / name).exists(), (project, name)
    assert (ROOT / project / "pnpm-lock.yaml").is_file()


@pytest.mark.parametrize("project", PROJECTS)
def test_the_lockfile_is_one_yaml_document(project: str) -> None:
    """GitHub's dependency graph, and so Dependabot's alerts and security updates, cannot read a
    pnpm-lock.yaml written as two documents (the pnpm record pnpm 12 adds unless pmOnFail is
    ignore)."""
    text = (ROOT / project / "pnpm-lock.yaml").read_text()
    docs = list(yaml.safe_load_all(text))
    assert len(docs) == 1, project
    assert not text.startswith("---"), project
    assert "packageManagerDependencies" not in text, project


def _check_pnpm(tmp_path: Path, version: str) -> subprocess.CompletedProcess[str]:
    """Run check-pnpm-version.mjs from frontend/ with a fake pnpm that prints `version`."""
    node = ROOT / ".tools/bin/node"
    if not node.is_file():
        pytest.skip("the pinned Node is not installed in .tools (make tools)")
    fake = tmp_path / "pnpm"
    fake.write_text(f"#!/bin/sh\necho {version}\n", encoding="utf-8")
    fake.chmod(0o755)
    return subprocess.run(
        [str(node), "scripts/check-pnpm-version.mjs"],
        cwd=ROOT / "frontend",
        env={"PATH": f"{tmp_path}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_version_check_fails_on_another_pnpm_and_passes_on_the_pinned_one(
    tmp_path: Path,
) -> None:
    """With pmOnFail: ignore, the check is what refuses another pnpm (CI and the image run it)."""
    other = _check_pnpm(tmp_path, "12.0.0")
    assert other.returncode == 1
    assert f"pnpm 12.0.0 is running but packageManager names pnpm@{pin('pnpm')}" in other.stderr
    assert _check_pnpm(tmp_path, pin("pnpm")).returncode == 0


def test_the_frontend_typescript_has_one_source_for_its_override() -> None:
    """openapi-typescript's peer range predates TypeScript 6; the override takes the catalog's."""
    s = settings("frontend")
    assert package("frontend")["devDependencies"]["typescript"] == "catalog:"
    assert s["overrides"]["openapi-typescript>typescript"] == "catalog:"
    assert re.fullmatch(r"~\d+\.\d+\.\d+", s["catalog"]["typescript"])


PNPM_RUN = re.compile(r"(?<![\w/-])pnpm (?!-)")


def _installs_pnpm(step: dict[str, Any]) -> bool:
    if str(step.get("uses", "")) == "./.github/actions/setup-frontend":
        return True
    run = str(step.get("run", ""))
    return any(
        "pnpm" in line.split("scripts/tools.sh install", 1)[1].split()
        for line in run.splitlines()
        if "scripts/tools.sh install" in line
    )


def test_every_job_that_runs_pnpm_installs_the_pinned_one_first() -> None:
    """The pinned, checksum-verified pnpm (mise.lock): scripts/tools.sh install, never a setup
    action or corepack."""
    files = [
        *sorted((ROOT / ".github/workflows").glob("*.yml")),
        ROOT / ".github/actions/setup-frontend/action.yml",
    ]
    seen = 0
    for path in files:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        groups = [job.get("steps", []) for job in doc.get("jobs", {}).values()] or [
            doc["runs"]["steps"]
        ]
        for steps in groups:
            for i, step in enumerate(steps):
                if PNPM_RUN.search(str(step.get("run", ""))) and not _installs_pnpm(step):
                    seen += 1
                    assert any(_installs_pnpm(s) for s in steps[:i]), (path.name, step.get("name"))
        text = path.read_text(encoding="utf-8")
        assert "corepack" not in text, path.name
        assert "pnpm/action-setup" not in text, path.name
    assert seen


def test_every_ci_install_runs_the_version_check_first() -> None:
    """pnpm itself does not refuse another version (pmOnFail: ignore): each job checks it first."""
    files = [
        *sorted((ROOT / ".github/workflows").glob("*.yml")),
        ROOT / ".github/actions/setup-frontend/action.yml",
    ]
    seen = 0
    for path in files:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        groups = [job.get("steps", []) for job in doc.get("jobs", {}).values()]
        for steps in groups or [doc["runs"]["steps"]]:
            checked = False
            for step in steps:
                checked = checked or step.get("uses") == "./.github/actions/setup-frontend"
                for line in str(step.get("run", "")).splitlines():
                    checked = checked or "scripts/check-pnpm-version.mjs" in line
                    if "pnpm install" in line:
                        seen += 1
                        assert checked, (path.name, step.get("name"))
    assert seen
    builder = (ROOT / "Dockerfile.frontend").read_text(encoding="utf-8")
    assert builder.index("node scripts/check-pnpm-version.mjs") < builder.index("pnpm install")
