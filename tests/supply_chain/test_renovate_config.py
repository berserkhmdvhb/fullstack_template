"""renovate.json5 and dependabot.yml: who owns version updates; the regexes see every pin."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.supply_chain._pins import pin
from tests.supply_chain.test_release_versions import SCM_PACKAGES

pytestmark = pytest.mark.tree_wide

ROOT = Path(__file__).resolve().parent.parent.parent
PINS = (ROOT / "tools" / "pins.tsv").read_text(encoding="utf-8")
CONFIG = (ROOT / "infra" / "security" / "config.yaml").read_text(encoding="utf-8")
DEPENDABOT = ROOT / ".github" / "dependabot.yml"
MAX_PINS_MANAGERS = 6
GIT: str = shutil.which("git") or "/usr/bin/git"


def renovate() -> dict[str, Any]:
    """The file is comments plus plain JSON by design, so the standard library can read it."""
    text = (ROOT / "renovate.json5").read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE))
    return data


def _managers(file_pattern: str) -> list[dict[str, Any]]:
    return [
        m
        for m in renovate()["customManagers"]
        if any(file_pattern in p for p in m["managerFilePatterns"])
    ]


def _compile(match_string: str) -> re.Pattern[str]:
    """Renovate runs RE2 and writes named groups (?<name>...); Python writes (?P<name>...)."""
    return re.compile(re.sub(r"\(\?<([A-Za-z]+)>", r"(?P<\1>", match_string), re.MULTILINE)


def test_release_age_grouping_and_majors() -> None:
    cfg = renovate()
    assert cfg["minimumReleaseAge"] == "7 days"
    assert "helpers:pinGitHubActionDigests" in cfg["extends"]
    assert "docker:pinDigests" in cfg["extends"]
    rules = {r["description"]: r for r in cfg["packageRules"]}
    grouped = rules["Weekly group for minor and patch updates"]
    assert {"minor", "patch"} <= set(grouped["matchUpdateTypes"])
    assert "major" not in grouped["matchUpdateTypes"]
    assert grouped["schedule"]
    assert rules["Major updates: one pull request each, never grouped"]["groupName"] is None


def test_every_pin_row_is_seen_by_exactly_one_manager_and_the_url_follows_the_version() -> None:
    managers = _managers("pins")
    assert 0 < len(managers) <= MAX_PINS_MANAGERS
    table = [ln.split("\t") for ln in PINS.splitlines() if ln and not ln.startswith("#")]
    for name, version, plat, url, _sha in table:
        hits = [
            m
            for mgr in managers
            for ms in mgr["matchStrings"]
            for m in _compile(ms).finditer(PINS)
            if m.group("depName") == name
        ]
        # One match per row: a tool with a row per platform (mise) is matched once for each.
        rows_of_name = sum(1 for r in table if r[0] == name)
        assert len(hits) == rows_of_name, f"{name}: matched {len(hits)} times, {rows_of_name} rows"
        hit = next(h for h in hits if rows_of_name == 1 or f"\t{plat}\t" in h.group(0))
        matched = hit.group(0)
        assert hit.group("currentValue") == version
        assert url in matched or url.startswith("uv:"), f"{name}: the match stops short of the url"
        assert not re.search(r"[0-9a-f]{64}", matched), f"{name}: the sha256 must not be matched"
        bumped = matched.replace(version, "99.9.9")
        assert version not in bumped
        assert bumped.count("99.9.9") >= 1


def test_the_github_release_tags_extract_to_the_pinned_versions() -> None:
    """Every GitHub-hosted pin's tag form gives its version; lychee's library tags give none."""
    (manager,) = [m for m in _managers("pins") if m["datasourceTemplate"] == "github-releases"][:1]
    extract = _compile(manager["extractVersionTemplate"])
    for tag, version in (("v1.7.12", "1.7.12"), ("0.13.8", "0.13.8"), ("lychee-v0.24.2", "0.24.2")):
        found = extract.fullmatch(tag)
        assert found, tag
        assert found.group("version") == version
    for tag in ("lychee-lib-v0.24.2", "nightly"):
        assert not extract.fullmatch(tag), tag


def test_every_security_tool_version_is_annotated() -> None:
    (manager,) = _managers("config")[:1]
    found = {
        m.group("depName"): m.group("currentValue")
        for m in _compile(manager["matchStrings"][0]).finditer(CONFIG)
    }
    tools = yaml.safe_load(CONFIG)
    for section in ("tools", "tools_pip"):
        for name, version in tools[section].items():
            assert version in found.values(), f"{section}.{name} has no `# renovate:` annotation"


def test_the_image_digest_pattern_sees_the_dast_image() -> None:
    manager = next(m for m in _managers("values") if "docker" in m["datasourceTemplate"])
    match = _compile(manager["matchStrings"][0]).search(CONFIG)
    assert match
    assert match.group("depName") == "ghcr.io/zaproxy/zaproxy"
    assert match.group("currentDigest").startswith("sha256:")


def test_dependabot_does_security_updates_only() -> None:
    text = DEPENDABOT.read_text(encoding="utf-8")
    assert "Renovate" in text.split("version:", 1)[0], (
        "the file must say Renovate owns version updates"
    )
    updates = yaml.safe_load(text)["updates"]
    assert {u["package-ecosystem"] for u in updates} == {"uv", "npm", "docker", "github-actions"}
    for update in updates:
        assert update["open-pull-requests-limit"] == 0


def test_every_name_based_rule_is_scoped_to_the_pin_file() -> None:
    """`python`, `node` and friends also name base images and npm packages: scope the rules."""
    for rule in renovate()["packageRules"]:
        if "matchDepNames" in rule:
            assert rule.get("matchManagers") == ["custom.regex"], rule["description"]
            assert rule.get("matchFileNames") == ["tools/pins.tsv"], rule["description"]


def test_the_node_cap_covers_every_manager_that_tracks_node_and_follows_the_pin() -> None:
    rule = next(r for r in renovate()["packageRules"] if r.get("matchPackageNames") == ["node"])
    assert {"custom.regex", "nvm", "nodenv", "dockerfile"} <= set(rule["matchManagers"])
    assert rule["groupName"] == "node"
    major = pin("node").split(".")[0]
    cap = re.fullmatch(r"/(.+)/", rule["allowedVersions"])
    assert cap
    assert re.search(cap[1], f"{major}.1.2")
    assert re.search(cap[1], f"{major}.1.2-trixie-slim")
    assert not re.search(cap[1], f"{int(major) + 1}.0.0")


def test_the_split_values_format_is_matched_once_a_digest_is_set() -> None:
    values = (ROOT / "charts" / "your-service-name" / "values.yaml").read_text(encoding="utf-8")
    manager = next(
        m
        for m in _managers("values")
        if "(?<currentDigest>" in "".join(m["matchStrings"])
        and "tag:" in "".join(m["matchStrings"])
    )
    pattern = _compile(manager["matchStrings"][0])
    found = [(m.group("depName"), m.group("currentValue")) for m in pattern.finditer(values)]
    # The pinned third-party images only; the release's own (empty digest) are never looked up.
    assert found == [
        ("gcr.io/cloud-sql-connectors/cloud-sql-proxy", "2.28.0"),
        ("postgres", "16-alpine"),
        ("postgres", "16-alpine"),
    ]
    unpinned = re.sub(r'digest: "sha256:[0-9a-f]{64}"', 'digest: ""', values)
    assert not pattern.search(unpinned), "an empty digest is not matched"


def test_no_private_planning_reference_in_the_new_files() -> None:
    for rel in (
        "renovate.json5",
        "tools/pins.tsv",
        "scripts/pins_refresh.py",
        "scripts/github-settings.sh",
    ):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert not re.search(r"spec section|design doc", text, re.IGNORECASE), rel


def test_renovate_maintains_uv_lock_and_moves_the_uv_pin_with_its_image() -> None:
    cfg = renovate()
    assert cfg["lockFileMaintenance"]["enabled"] is True
    assert cfg["minimumReleaseAge"] == "7 days"
    rule = next(r for r in cfg["packageRules"] if r.get("groupName") == "uv")
    assert set(rule["matchPackageNames"]) == {"astral-sh/uv", "ghcr.io/astral-sh/uv"}


def test_one_pull_request_moves_setuptools_scm_in_the_dev_group_and_the_build_constraints() -> None:
    rules = renovate()["packageRules"]
    names = [r["description"] for r in rules]
    (rule,) = [r for r in rules if set(r.get("matchPackageNames", ())) & set(SCM_PACKAGES)]
    assert set(rule["matchPackageNames"]) == set(SCM_PACKAGES)
    assert rule["groupName"]
    assert not {"matchUpdateTypes", "matchManagers", "matchFileNames", "enabled"} & set(rule)
    # After the major rule (groupName null), so a major joins the group too.
    assert names.index(rule["description"]) > names.index(
        "Major updates: one pull request each, never grouped"
    )
    assert "build-constraint-dependencies" in " ".join(rule["prBodyNotes"])
    # The two places the rule moves (test_release_versions.py keeps their versions equal).
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for name in SCM_PACKAGES:
        assert pyproject.count(f'"{name}==') == 2, name  # noqa: PLR2004 - dev group and build constraint


def test_uv_lock_maintenance_is_not_blocked_by_the_release_age_check() -> None:
    # A lock refresh has no release timestamp: with minimumReleaseAge set, the default
    # timestamp-required behaviour would create no branch. uv's exclude-newer holds the 7 days.
    rules = renovate()["packageRules"]
    rule = next(r for r in rules if r.get("matchUpdateTypes") == ["lockFileMaintenance"])
    assert rule["matchManagers"] == ["pep621"]
    assert "minimumReleaseAge" in rule
    assert rule["minimumReleaseAge"] is None
    # An update with no release timestamp is held by the default timestamp-required behaviour.
    assert rule["minimumReleaseAgeBehaviour"] == "timestamp-optional"
    assert renovate()["minimumReleaseAge"] == "7 days"


def test_the_envoy_distroless_tag_is_versioned_so_new_releases_are_proposed() -> None:
    """`distroless-vX.Y.Z` is not a version Renovate's docker versioning can parse: without a rule
    only the digest of the same tag is refreshed and a new Envoy patch or minor never proposed."""
    dockerfile = (ROOT / "Dockerfile.waf").read_text(encoding="utf-8")
    tag = re.search(r"^FROM envoyproxy/envoy:(\S+)@sha256:", dockerfile, re.MULTILINE)
    assert tag
    rule = next(
        r for r in renovate()["packageRules"] if r.get("matchPackageNames") == ["envoyproxy/envoy"]
    )
    assert rule["pinDigests"] is True
    versioning = re.fullmatch(r"regex:(.+)", rule["versioning"])
    assert versioning
    pattern = _compile(versioning[1])
    found = pattern.fullmatch(tag[1])
    assert found
    assert found.group("major") + "." + found.group("minor") + "." + found.group("patch") == (
        tag[1].removeprefix("distroless-v")
    )
    for other in ("v1.39.6", "distroless-dev", "contrib-v1.39.6", "distroless-v1.39-latest"):
        assert not pattern.fullmatch(other), other


def test_every_other_new_pin_has_a_tag_renovate_versions_by_default() -> None:
    """The other new pins use plain semver or a version-suffix tag (docker versioning reads it)."""
    values = (ROOT / "charts" / "your-service-name" / "values.yaml").read_text(encoding="utf-8")
    dockerfile = (ROOT / "Dockerfile.waf").read_text(encoding="utf-8")
    tags = re.findall(r"^\s+tag: [\"']?(\S+?)[\"']?\n\s+digest: \"sha256:", values, re.MULTILINE)
    tags += re.findall(r"^FROM debian:(\S+)@", dockerfile, re.MULTILINE)
    assert tags
    for tag in tags:
        assert re.fullmatch(r"\d+(\.\d+)*(-[a-z]+)?", tag), tag


CRD_SOURCES = ROOT / "charts" / "your-service-name" / "ci" / "crds" / "sources.tsv"


def test_every_crd_source_row_is_seen_once_and_its_url_follows_the_version() -> None:
    """The pinned upstream CRDs (kubeconform's schemas) get version updates like the tool pins."""
    text = CRD_SOURCES.read_text(encoding="utf-8")
    managers = _managers("ci/crds/sources")
    assert len(managers) == 1
    (manager,) = managers
    assert manager["datasourceTemplate"] == "github-releases"
    rows = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    assert rows
    for row in rows:
        name, version, url, _sha, _kinds = row.split("\t")
        hits = [m for ms in manager["matchStrings"] for m in _compile(ms).finditer(row + "\n")]
        assert len(hits) == 1, f"{name}: matched {len(hits)} times"
        match = hits[0]
        assert match.group("depName") == name
        assert match.group("currentValue") == version
        assert url in match.group(0), f"{name}: the match stops short of the url"
        assert re.fullmatch(r"[\w.-]+/[\w.-]+", match.group("packageName")), name
        assert not re.search(r"[0-9a-f]{64}", match.group(0)), f"{name}: the sha256 is not matched"
        assert version not in match.group(0).replace(version, "99.9.9")
    extract = re.compile(
        re.sub(r"\(\?<([A-Za-z]+)>", r"(?P<\1>", manager["extractVersionTemplate"])
    )
    assert extract.fullmatch("v1.7.0")
    assert not extract.fullmatch("helm-chart-2.15.0"), "a chart release is not a CRD version"


def test_the_drift_note_covers_the_crd_sources() -> None:
    rule = next(
        r for r in renovate()["packageRules"] if "matchFileNames" in r and "prBodyNotes" in r
    )
    assert "charts/your-service-name/ci/crds/sources.tsv" in rule["matchFileNames"]


ADMISSION_IMAGE_FILES = ("deploy/policies/test/kyverno-values.yaml",)
# tests/k8s/_kube.py holds the throwaway registry the admission and deploy tests share.
KIND_IMAGE_FILES = (*ADMISSION_IMAGE_FILES, "tests/k8s/_kube.py", "tests/k8s/test_tracing_redis.py")


def _file_managers(rel: str) -> list[dict[str, Any]]:
    """The managers whose file patterns (JavaScript regexes between slashes) match the path."""
    return [
        m
        for m in renovate()["customManagers"]
        if any(re.search(p.strip("/"), rel) for p in m["managerFilePatterns"])
    ]


def test_every_kind_test_file_with_an_image_digest_is_listed() -> None:
    pinned = {
        f"tests/k8s/{p.name}"
        for p in (ROOT / "tests" / "k8s").glob("*.py")
        if re.search(r"sha256:[a-f0-9]{64}", p.read_text(encoding="utf-8"))
    }
    assert pinned <= set(KIND_IMAGE_FILES), pinned - set(KIND_IMAGE_FILES)


@pytest.mark.parametrize("rel", KIND_IMAGE_FILES)
def test_every_image_digest_of_the_kind_tests_is_seen_by_one_manager(rel: str) -> None:
    """Each pinned digest is matched once, with its tag, so a tag never moves without its digest."""
    text = (ROOT / rel).read_text(encoding="utf-8")
    digests = re.findall(r"sha256:[a-f0-9]{64}", text)
    assert digests, rel
    matches = [
        m
        for mgr in _file_managers(rel)
        for ms in mgr["matchStrings"]
        for m in _compile(ms).finditer(text)
        if "currentDigest" in m.groupdict()
    ]
    assert sorted(m.group("currentDigest") for m in matches) == sorted(digests), rel
    for match in matches:
        assert match.group("currentValue"), match.group(0)
        assert "/" in match.group("depName"), match.group(0)


def test_the_kyverno_group_holds_the_cli_the_chart_and_the_images() -> None:
    rule = next(r for r in renovate()["packageRules"] if r.get("groupName") == "kyverno")
    values = (ROOT / ADMISSION_IMAGE_FILES[0]).read_text(encoding="utf-8")
    images = re.findall(r"^\s*# renovate: image=([\w./-]+)$", values, re.MULTILINE)
    assert images
    assert set(rule["matchPackageNames"]) == {"kyverno/kyverno", "kyverno", *images}


def _title_pattern() -> re.Pattern[str]:
    """The Conventional Commit pattern pr-title.yml enforces, taken from its step."""
    run = yaml.safe_load((ROOT / ".github" / "workflows" / "pr-title.yml").read_text())["jobs"][
        "title"
    ]["steps"][0]["run"]
    types = re.search(r'types="([^"]+)"', run)
    assert types
    return re.compile(rf"^({types[1]})(\([a-z0-9][a-z0-9,._/-]*\))?!?: [^ ].*$")


def test_renovate_titles_are_always_fix_deps_conventional_commits() -> None:
    cfg = renovate()
    assert cfg["semanticCommits"] == "enabled"  # never auto-detected
    assert (cfg["semanticCommitType"], cfg["semanticCommitScope"]) == ("fix", "deps")
    assert cfg["lockFileMaintenance"]["semanticCommitType"] == "fix"
    last = cfg["packageRules"][-1]  # applied after every preset and earlier rule
    assert last["matchPackageNames"] == ["*"]
    assert (last["semanticCommitType"], last["semanticCommitScope"]) == ("fix", "deps")
    others = [r for r in cfg["packageRules"][:-1] if "semanticCommitType" in r]
    assert not others, [r["description"] for r in others]
    title = f"{cfg['semanticCommitType']}({cfg['semanticCommitScope']}): update dependency x to v2"
    assert _title_pattern().match(title)


def test_every_npm_lockfile_gets_dependabot_security_updates() -> None:
    """Without an entry, a security pull request for that lockfile misses the `fix(deps)` title."""
    tracked = subprocess.run(
        [GIT, "ls-files", "*package-lock.json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    lockdirs = {"/" + str(Path(p).parent) for p in tracked if "node_modules/" not in p}
    assert lockdirs
    npm = [
        u
        for u in yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))["updates"]
        if u["package-ecosystem"] == "npm"
    ]
    dirs = {d for u in npm for d in u.get("directories", [u.get("directory")])}
    assert lockdirs <= dirs


def test_dependabot_security_titles_are_fix_deps_conventional_commits() -> None:
    for update in yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))["updates"]:
        assert update["commit-message"] == {"prefix": "fix(deps)"}, update["package-ecosystem"]
        # Dependabot inserts ": " after a prefix that ends in ")".
        title = f"{update['commit-message']['prefix']}: bump x from 1.0.0 to 1.0.1"
        assert _title_pattern().match(title)


def test_scorecard_action_never_proposes_the_unpullable_v2_6_0() -> None:
    rules = [
        r
        for r in renovate()["packageRules"]
        if r.get("matchPackageNames") == ["ossf/scorecard-action"]
    ]
    assert len(rules) == 1
    assert "scorecard-action#1752" in rules[0]["description"]
    allowed = rules[0]["allowedVersions"]
    assert allowed.startswith("!/")
    assert allowed.endswith("/")
    excluded = re.compile(allowed[2:-1])
    for version in ("v2.6.0", "2.6.0"):
        assert excluded.search(version), version
    for version in ("v2.6.1", "v2.7.0", "v2.5.0"):
        assert not excluded.search(version), version
