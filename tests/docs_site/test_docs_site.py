"""The documentation site's project (docs-site/): pinned like every other tool, its pages loaded.

The build itself (links, anchors, front matter, diagrams) is checked by `pnpm run build` in CI's
docs-site job; these tests hold the configuration around it, offline.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import yaml

from tests.supply_chain.test_pnpm_config import locked

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


def test_one_node_one_pnpm_and_the_frontends_playwright() -> None:
    """The release age, the build-script review and the trust policy are in
    tests/supply_chain/test_pnpm_config.py."""
    site, front = _json(SITE / "package.json"), _json(ROOT / "frontend/package.json")
    assert site["engines"] == front["engines"]
    assert site["packageManager"] == front["packageManager"]
    site_lock, front_lock = locked("docs-site"), locked("frontend")
    for name in ("playwright", "@playwright/test"):
        versions = {v for n, v in site_lock if n == name}
        assert len(versions) == 1, name
        assert versions == {v for n, v in front_lock if n == name}, name


def test_github_alerts_run_before_starlights_asides() -> None:
    """Starlight appends its aside plugins after the configured ones: the alerts run first."""
    config = (SITE / "astro.config.mjs").read_text(encoding="utf-8")
    assert "import { remarkGithubAlerts } from './src/lib/github-alerts.mjs';" in config
    plugins = re.search(r"remarkPlugins: \[([^\]]*)\]", config)
    assert plugins
    assert "remarkGithubAlerts" in [p.strip() for p in plugins[1].split(",")]


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
        "wiki/github-pages-setup.md",
        "wiki/pr-agent-setup.md",
    }
    tracked = subprocess.run(
        [GIT, "ls-files", *loaded], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    assert sorted(tracked) == sorted(loaded)
    config = (SITE / "astro.config.mjs").read_text(encoding="utf-8")
    for path, slug in loaded.items():
        assert _h1((ROOT / path).read_text(encoding="utf-8")), path
        assert f"'{slug}'" in config, slug
        for suffix in (".md", ".mdx"):
            assert not (SITE / "src/content/docs" / f"{slug}{suffix}").exists(), (
                f"{slug}{suffix}: copied, not loaded"
            )


PAGES_DIR = SITE / "src/content/docs"
# A relative Markdown link whose target is a file or directory under wiki/. A JSX href does not
# count: the resolver (src/lib/repo.mjs) rewrites Markdown links and definitions only.
WIKI_LINK = re.compile(r"\]\(((?:\.\./)+wiki/[^)\s]*)")


def _own_pages() -> list[Path]:
    """The site's own pages; the loaded walkthroughs live in the repository, not here."""
    return sorted(p for p in PAGES_DIR.rglob("*") if p.suffix in {".md", ".mdx"})


def page_problems(path: Path, text: str) -> list[str]:
    """What a page lacks: a front-matter description, and a link that resolves into the wiki."""
    found = []
    front = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    meta = yaml.safe_load(front[1]) if front else None
    if not isinstance(meta, dict) or not str(meta.get("description") or "").strip():
        found.append("no description")
    targets = [(path.parent / link.split("#")[0]).resolve() for link in WIKI_LINK.findall(text)]
    if not any(t.is_relative_to(ROOT / "wiki") and t.exists() for t in targets):
        found.append("no wiki link")
    return found


def test_every_page_has_a_description_and_links_into_the_wiki() -> None:
    pages = _own_pages()
    assert len(pages) > 1
    assert {p.name: page_problems(p, p.read_text(encoding="utf-8")) for p in pages} == {
        p.name: [] for p in pages
    }


def test_the_page_guard_flags_a_missing_description_or_wiki_link() -> None:
    page = PAGES_DIR / "x.md"
    good = "---\ntitle: X\ndescription: Y.\n---\n\nSee [CI/CD](../../../../wiki/ci-cd.md#caches).\n"
    assert page_problems(page, good) == []
    assert page_problems(page, good.replace("description: Y.\n", "")) == ["no description"]
    assert page_problems(page, good.replace("description: Y.", "description: ''")) == [
        "no description"
    ]
    assert page_problems(page, good.replace("wiki/ci-cd.md", "README.md")) == ["no wiki link"]
    assert page_problems(page, good.replace("ci-cd.md", "no-such-page.md")) == ["no wiki link"]
    assert page_problems(page, good.replace("../../../../", "../")) == ["no wiki link"]
    jsx = good.replace(
        "[CI/CD](../../../../wiki/ci-cd.md#caches)", '<a href="../../../../wiki/ci-cd.md">CI/CD</a>'
    )
    assert page_problems(page, jsx) == ["no wiki link"]


def test_the_design_page_indexes_every_adr() -> None:
    adrs = {p.name for p in (ROOT / "wiki/adr").glob("[0-9][0-9][0-9][0-9]-*.md")} - {
        "0000-template.md"
    }
    linked = set(re.findall(r"adr/(\d{4}-[a-z0-9-]+\.md)", (PAGES_DIR / "design.md").read_text()))
    assert adrs == linked


def test_the_repository_value_is_the_adapt_placeholder() -> None:
    assert _json(SITE / "site.json") == {"repository": "your-org/your-repo", "branch": "main"}


def test_the_build_output_is_ignored() -> None:
    for path in ("docs-site/node_modules/x", "docs-site/dist/index.html", "docs-site/.astro/x"):
        done = subprocess.run([GIT, "check-ignore", "-q", path], cwd=ROOT, check=False)
        assert done.returncode == 0, path
