"""renovate.json5 and dependabot.yml: who owns version updates; the regexes see every pin."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
PINS = (ROOT / "tools" / "pins.tsv").read_text(encoding="utf-8")
CONFIG = (ROOT / "infra" / "security" / "config.yaml").read_text(encoding="utf-8")
DEPENDABOT = ROOT / ".github" / "dependabot.yml"
MAX_PINS_MANAGERS = 5


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
    for row in (ln for ln in PINS.splitlines() if ln and not ln.startswith("#")):
        name, version, _plat, url, _sha = row.split("\t")
        hits = [
            m
            for mgr in managers
            for ms in mgr["matchStrings"]
            for m in _compile(ms).finditer(PINS)
            if m.group("depName") == name
        ]
        assert len(hits) == 1, f"{name}: matched by {len(hits)} managers"
        matched = hits[0].group(0)
        assert hits[0].group("currentValue") == version
        assert url in matched or url.startswith("uv:"), f"{name}: the match stops short of the url"
        assert not re.search(r"[0-9a-f]{64}", matched), f"{name}: the sha256 must not be matched"
        bumped = matched.replace(version, "99.9.9")
        assert version not in bumped
        assert bumped.count("99.9.9") >= 1


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
    major = (
        next(r for r in PINS.splitlines() if r.startswith("node\t")).split("\t")[1].split(".")[0]
    )
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
    assert not pattern.search(values), "an unpinned (empty digest) image must not be looked up"
    digest = "sha256:" + "a" * 64
    pinned = values.replace(
        '    repository: envoyproxy/envoy\n    tag: v1.31.0\n    digest: ""',
        f'    repository: envoyproxy/envoy\n    tag: v1.31.0\n    digest: "{digest}"',
    )
    assert pinned != values
    match = pattern.search(pinned)
    assert match
    assert (match.group("depName"), match.group("currentValue"), match.group("currentDigest")) == (
        "envoyproxy/envoy",
        "v1.31.0",
        digest,
    )


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
