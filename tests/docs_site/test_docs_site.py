"""The documentation site's project (docs-site/): pinned like every other tool, its pages loaded.

The build itself (links, anchors, front matter, diagrams) is checked by `npm run build` in CI's
docs-site job; these tests hold the configuration around it, offline.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

from tests.supply_chain.test_npm_install_scripts import DOC, problems

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "docs-site"
EXACT = re.compile(r"\d+\.\d+\.\d+")
GIT: str = shutil.which("git") or "/usr/bin/git"


def _json(path: Path) -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))


def _loaded() -> dict[str, str]:
    """The LOADED map of src/lib/repo.mjs: repository file -> page slug."""
    text = (SITE / "src/lib/repo.mjs").read_text(encoding="utf-8")
    block = re.search(r"export const LOADED = \{(.*?)\};", text, re.DOTALL)
    assert block
    return dict(re.findall(r"'([^']+)': '([^']+)'", block[1]))


def _h1(text: str) -> str | None:
    """The first ATX H1 outside a code fence, as the site's loader reads it (not a line grep)."""
    fence = ""
    for line in text.splitlines():
        stripped = line.lstrip()
        marker = re.match(r"(`{3,}|~{3,})", stripped)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = ""
        elif marker:
            fence = marker[1]
        elif heading := re.match(r" {0,3}# +(\S.*?)\s*#*\s*$", line):
            return heading[1]
    return None


def test_the_h1_ignores_a_hash_line_in_a_code_fence() -> None:
    assert _h1("```bash\n# a comment\n```\n\n# Title\n") == "Title"
    assert _h1("~~~\n# a comment\n~~~\n") is None


def test_every_dependency_is_an_exact_version() -> None:
    package = _json(SITE / "package.json")
    deps = {**package["dependencies"], **package["devDependencies"]}
    assert deps
    assert {n: v for n, v in deps.items() if not EXACT.fullmatch(v)} == {}


def test_npm_keeps_the_seven_day_release_age() -> None:
    assert "min-release-age=7" in (SITE / ".npmrc").read_text(encoding="utf-8").splitlines()


def test_the_lockfile_install_scripts_are_reviewed() -> None:
    lock = _json(SITE / "package-lock.json")
    allow = _json(SITE / "package.json").get("allowScripts", {})
    assert problems(lock, allow, DOC) == []


def test_one_node_one_npm_and_the_frontends_playwright() -> None:
    site, front = _json(SITE / "package.json"), _json(ROOT / "frontend/package.json")
    assert site["engines"] == front["engines"]
    assert site["packageManager"] == front["packageManager"]
    site_lock = _json(SITE / "package-lock.json")["packages"]
    front_lock = _json(ROOT / "frontend/package-lock.json")["packages"]
    for name in ("node_modules/playwright", "node_modules/@playwright/test"):
        assert site_lock[name]["version"] == front_lock[name]["version"], name


def test_diagrams_render_at_build_time_with_no_client_renderer() -> None:
    config = (SITE / "astro.config.mjs").read_text(encoding="utf-8")
    assert "strategy: 'inline-svg'" in config
    deps = _json(SITE / "package.json")["dependencies"]
    assert "rehype-mermaid" in deps
    assert not {"mermaid", "astro-mermaid"} & set(deps)


def test_every_loaded_file_is_tracked_has_an_h1_and_is_in_the_sidebar() -> None:
    loaded = _loaded()
    assert set(loaded) == {
        "CONTRIBUTING.md",
        "wiki/cloudflare-setup.md",
        "wiki/gke-cluster-setup.md",
    }
    tracked = subprocess.run(
        [GIT, "ls-files", *loaded], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    assert sorted(tracked) == sorted(loaded)
    config = (SITE / "astro.config.mjs").read_text(encoding="utf-8")
    for path, slug in loaded.items():
        assert _h1((ROOT / path).read_text(encoding="utf-8")), path
        assert f"'{slug}'" in config, slug
        assert not (SITE / "src/content/docs" / f"{slug}.md").exists(), (
            f"{slug}: copied, not loaded"
        )


def test_the_repository_value_is_the_adapt_placeholder() -> None:
    assert _json(SITE / "site.json") == {"repository": "your-org/your-repo", "branch": "main"}


def test_the_build_output_is_ignored() -> None:
    for path in ("docs-site/node_modules/x", "docs-site/dist/index.html", "docs-site/.astro/x"):
        done = subprocess.run([GIT, "check-ignore", "-q", path], cwd=ROOT, check=False)
        assert done.returncode == 0, path
