"""Helm is pinned: every job that runs it installs the checksum-verified `helm4` row of
tools/pins.tsv, never a Helm container image or whatever Helm the runner carries."""

from __future__ import annotations

import re

from tests.supply_chain._workflows import REPO, WORKFLOWS, load

# A helm command line: `helm <subcommand>` at the start of a command.
HELM = re.compile(r"(^|[\s;&|(\"])helm\s+[a-z]", re.MULTILINE)
INSTALL = re.compile(r"scripts/tools\.sh install[^\n]*\bhelm4\b")


def _code(text: str) -> str:
    return "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))


def helm_scripts() -> set[str]:
    """The repo's shell scripts that run helm themselves."""
    return {
        str(p.relative_to(REPO))
        for p in (REPO / "scripts").rglob("*.sh")
        if HELM.search(_code(p.read_text(encoding="utf-8")))
    }


def jobs_without_the_pin(scripts: set[str]) -> list[str]:
    out = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for name, job in (load(path).get("jobs") or {}).items():
            steps = job.get("steps", [])
            runs = "\n".join(str(s.get("run", "")) for s in steps)
            images = [str(job.get("container", "")), *(str(s.get("uses", "")) for s in steps)]
            runs_helm = HELM.search(runs) or any(s in runs for s in scripts)
            if any("helm" in i for i in images):
                out.append(f"{path.name}/{name}: a Helm image or action")
            elif runs_helm and not INSTALL.search(runs):
                out.append(f"{path.name}/{name}: runs helm without installing the helm4 pin")
    return out


def test_every_job_that_runs_helm_installs_the_pinned_binary() -> None:
    scripts = helm_scripts()
    assert "scripts/ci/deploy-env.sh" in scripts  # the detection sees the deploy
    assert jobs_without_the_pin(scripts) == []


def test_the_helm_pins_are_exact_versions_with_a_checksum() -> None:
    rows = [
        ln.split("\t")
        for ln in (REPO / "tools" / "pins.tsv").read_text(encoding="utf-8").splitlines()
        if ln.startswith("helm")
    ]
    assert {r[0] for r in rows} >= {"helm3", "helm4"}
    for name, version, _plat, url, sha in rows:
        assert re.fullmatch(r"\d+\.\d+\.\d+", version), name
        assert f"v{version}" in url, name
        assert re.fullmatch(r"[0-9a-f]{64}", sha), name
