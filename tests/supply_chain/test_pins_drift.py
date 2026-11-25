"""Pin drift: tools/pins.tsv and config.yaml structure, and scripts/pins_refresh.py (no network).

The vendor fetch layer is stubbed; the real network run is the blocking `pins` job in ci.yml.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

from tests.supply_chain._workflows import WORKFLOWS, workflow

ROOT = Path(__file__).resolve().parent.parent.parent
PINS = ROOT / "tools" / "pins.tsv"
CONFIG = ROOT / "infra" / "security" / "config.yaml"
REQS = ROOT / "tools" / "requirements"
WRAPPER = ROOT / "scripts" / "pins-refresh.sh"
BASH: str = shutil.which("bash") or "/usr/bin/bash"
SHA = re.compile(r"[0-9a-f]{64}")
FIELDS = 5
USAGE_EXIT = 2


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "pins_refresh", ROOT / "scripts" / "pins_refresh.py"
    )
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["pins_refresh"] = module
    spec.loader.exec_module(module)
    return module


pr = _load()
PRODUCTION_SOURCES = dict(pr.SOURCES)
# The rows that moved to mise leave their checksum sources unused. The kinds stay for a row that has
# to come back to tools/pins.tsv (a tool mise cannot verify as well): one sample row per kind (kind
# stands in as the generic row of most tests; gitleaks has the {dir} and {ver} placeholders).
SAMPLE_SOURCES = {
    "helm4": ("sumfile", "{url}.sha256sum", "https://get.helm.sh/"),
    "kind": ("sumfile", "{url}.sha256sum", "https://github.com/kubernetes-sigs/kind/releases/download/"),
    "kubectl": ("hashfile", "{url}.sha256", "https://dl.k8s.io/release/"),
    "yq": ("yq-columns", "{dir}/checksums", "https://github.com/mikefarah/yq/releases/download/"),
    "squawk": ("gh-digest", "", "https://github.com/sbdchd/squawk/releases/download/"),
    "gitleaks": ("sumfile", "{dir}/gitleaks_{ver}_checksums.txt", "https://github.com/gitleaks/gitleaks/releases/download/"),
}  # fmt: skip


@pytest.fixture(autouse=True)
def _sample_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, source in SAMPLE_SOURCES.items():
        monkeypatch.setitem(pr.SOURCES, name, source)


def rows() -> list[list[str]]:
    lines = [ln for ln in PINS.read_text().splitlines() if ln and not ln.startswith("#")]
    return [ln.split("\t") for ln in lines]


# --- structure of the real files ---


@pytest.mark.parametrize("row", rows(), ids=lambda r: f"{r[0]}-{r[2]}")
def test_row_shape(row: list[str]) -> None:
    assert len(row) == FIELDS
    name, version, _plat, url, sha = row
    if url.startswith("uv:"):
        assert sha == "-"
        return
    assert version in url, f"{name}: version {version} is not in its url"
    assert pr.url_problem(url) is None, f"{name}: {pr.url_problem(url)}"
    assert SHA.fullmatch(sha), f"{name}: sha256 is not 64 hex"


@pytest.mark.parametrize(
    "row", [r for r in rows() if r[3].startswith("uv:") and r[0] != "python"], ids=lambda r: r[0]
)
def test_uv_rows_have_a_lock_pinning_the_version(row: list[str]) -> None:
    name, version, _plat, url, _sha = row
    lock = REQS / f"{name}.txt"
    assert lock.is_file(), f"{name}: missing {lock}"
    ver, hashes = pr.parse_lock(lock.read_text(), url[len("uv:") :])
    assert ver == version
    assert hashes


def test_the_source_mapping_covers_every_pinned_tool_and_nothing_else() -> None:
    pinned = {r[0] for r in rows()}
    assert pinned == set(PRODUCTION_SOURCES), (
        "a pin with no checksum source (or a stale source entry)"
    )


def test_config_versions_are_well_formed_and_mapped() -> None:
    tools = pr.parse_config_tools(CONFIG.read_text())
    assert tools
    for name, version in tools.items():
        assert re.fullmatch(r"v\d+\.\d+\.\d+", version), f"{name}: {version}"
    assert set(tools) == set(pr.CONFIG_SOURCES), (
        "a config tool with no checksum source (or a stale one)"
    )


# --- mapping and parsing ---


def test_parse_sumfile_formats() -> None:
    h1, h2 = "a" * 64, "b" * 64
    text = f"{h1}  other.tar.gz\n{h2} *./_dist/tool-1.tgz\n"
    assert pr.sha_from_sumfile(text, "tool-1.tgz") == h2
    assert pr.sha_from_sumfile(text, "other.tar.gz") == h1
    with pytest.raises(pr.PinError):
        pr.sha_from_sumfile(text, "missing")


def test_yq_column_follows_the_published_order() -> None:
    order = "CRC32\nMD5\nSHA-256\nSHA-512\n"
    text = "yq_linux_amd64.tar.gz  c1  m5  " + "c" * 64 + "  s5\nyq_other  x  x  x  x\n"
    assert pr.sha_from_yq(text, order, "yq_linux_amd64.tar.gz") == "c" * 64


def _stub(monkeypatch: pytest.MonkeyPatch, files: dict[str, bytes]) -> list[str]:
    seen: list[str] = []

    def fake(url: str) -> bytes:
        seen.append(url)
        if url not in files:
            pr.fail(f"404 {url}")
        return files[url]

    monkeypatch.setattr(pr, "fetch", fake)
    return seen


def _pin(name: str, version: str, url: str, sha: str) -> object:
    return pr.Pin(name, version, "linux-amd64", url, sha)


@pytest.mark.parametrize(
    ("pin", "sums_url"),
    [
        (
            ("helm4", "4.3.2", "https://get.helm.sh/helm-v4.3.2-linux-amd64.tar.gz"),
            "https://get.helm.sh/helm-v4.3.2-linux-amd64.tar.gz.sha256sum",
        ),
        (
            (
                "kind",
                "0.33.0",
                "https://github.com/kubernetes-sigs/kind/releases/download/v0.33.0/kind-linux-amd64",
            ),
            "https://github.com/kubernetes-sigs/kind/releases/download/v0.33.0/kind-linux-amd64.sha256sum",
        ),
        (
            (
                "gitleaks",
                "8.1.0",
                "https://github.com/gitleaks/gitleaks/releases/download/v8.1.0/gitleaks_8.1.0_linux_x64.tar.gz",
            ),
            "https://github.com/gitleaks/gitleaks/releases/download/v8.1.0/gitleaks_8.1.0_checksums.txt",
        ),
    ],
)
def test_checksum_url_per_tool(
    monkeypatch: pytest.MonkeyPatch, pin: tuple[str, str, str], sums_url: str
) -> None:
    asset = pin[2].rsplit("/", 1)[1]
    seen = _stub(monkeypatch, {sums_url: f"{'d' * 64}  {asset}\n".encode()})
    assert pr.vendor_sha(_pin(*pin, "x")) == "d" * 64
    assert seen == [sums_url]


def test_kubectl_hash_only_file(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://dl.k8s.io/release/v1.0.0/bin/linux/amd64/kubectl"
    _stub(monkeypatch, {url + ".sha256": b"e" * 64})
    assert pr.vendor_sha(_pin("kubectl", "1.0.0", url, "x")) == "e" * 64


def test_github_release_digest_for_squawk(monkeypatch: pytest.MonkeyPatch) -> None:
    api = "https://api.github.com/repos/sbdchd/squawk/releases/tags/v2.0.0"
    body = (
        '{"assets": [{"name": "squawk-linux-x64", "digest": "sha256:' + "f" * 64 + '"}]}'
    ).encode()
    _stub(monkeypatch, {api: body})
    url = "https://github.com/sbdchd/squawk/releases/download/v2.0.0/squawk-linux-x64"
    assert pr.vendor_sha(_pin("squawk", "2.0.0", url, "x")) == "f" * 64


def test_a_data_file_at_a_tag_is_hashed_on_download(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://raw.githubusercontent.com/kubernetes/kubernetes/v1.0.0/api/openapi-spec/swagger.json"
    _stub(monkeypatch, {url: b"archive"})
    row = _pin("k8s-openapi", "1.0.0", url, "x")
    assert pr.vendor_sha(row) == hashlib.sha256(b"archive").hexdigest()


def test_uv_row_checks_the_version_against_its_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock = f"zizmor==1.0.0 \\\n    --hash=sha256:{'1' * 64} \\\n    --hash=sha256:{'2' * 64}\n"
    (tmp_path / "zizmor.txt").write_text(lock)
    monkeypatch.setenv("REQS", str(tmp_path))
    body = (
        '{"urls": [{"digests": {"sha256": "'
        + "1" * 64
        + '"}}, {"digests": {"sha256": "'
        + "2" * 64
        + '"}}]}'
    ).encode()
    _stub(monkeypatch, {"https://pypi.org/pypi/zizmor/1.0.0/json": body})
    pin = _pin("zizmor", "1.0.0", "uv:zizmor", "-")
    assert (
        pr.problem(pin) is None
    )  # the row only has to agree with its lock; PyPI is checked per lock
    assert "version" in (pr.problem(_pin("zizmor", "1.0.1", "uv:zizmor", "-")) or "")


# --- --check and update over a scratch pins file ---


def _scratch(tmp_path: Path, sha: str) -> Path:
    pins = tmp_path / "pins.tsv"
    url = "https://get.helm.sh/helm-v4.3.2-linux-amd64.tar.gz"
    pins.write_text(f"# header\n\nhelm4\t4.3.2\tlinux-amd64\t{url}\t{sha}\n")
    return pins


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pins: Path) -> None:
    monkeypatch.setenv("PINS", str(pins))
    monkeypatch.setenv("CRD_SOURCES", str(tmp_path / "no-crd-sources.tsv"))
    monkeypatch.setenv("SECURITY_CONFIG", str(tmp_path / "none.yaml"))
    monkeypatch.setenv("REQS", str(tmp_path / "reqs"))


def test_check_passes_names_the_drifting_pin_and_fails_on_fetch_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sums = "https://get.helm.sh/helm-v4.3.2-linux-amd64.tar.gz.sha256sum"
    _stub(monkeypatch, {sums: f"{'a' * 64}  helm-v4.3.2-linux-amd64.tar.gz\n".encode()})
    _env(monkeypatch, tmp_path, _scratch(tmp_path, "a" * 64))
    assert pr.main(["--check"]) == 0
    _env(monkeypatch, tmp_path, _scratch(tmp_path, "b" * 64))
    assert pr.main(["--check"]) == 1
    assert "DRIFT helm4 4.3.2" in capsys.readouterr().out
    _stub(monkeypatch, {})
    assert pr.main(["--check"]) == 1
    assert "ERROR helm4" in capsys.readouterr().out


def test_update_writes_the_vendor_checksum_and_keeps_the_rest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sums = "https://get.helm.sh/helm-v4.3.2-linux-amd64.tar.gz.sha256sum"
    _stub(monkeypatch, {sums: f"{'a' * 64}  helm-v4.3.2-linux-amd64.tar.gz\n".encode()})
    pins = _scratch(tmp_path, "b" * 64)
    _env(monkeypatch, tmp_path, pins)
    assert pr.main([]) == 0
    text = pins.read_text()
    assert text.startswith("# header\n\nhelm4\t4.3.2\tlinux-amd64\t")
    assert text.endswith(f"\t{'a' * 64}\n")
    assert pr.main(["--check"]) == 0


def test_check_with_a_base_only_looks_at_changed_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pins = _scratch(tmp_path, "b" * 64)
    _env(monkeypatch, tmp_path, pins)
    old = pins.read_text()

    def fake_show(_base: str, rel: str) -> str:
        if rel in pr.WATCHED:
            return (ROOT / rel).read_text() if (ROOT / rel).is_file() else ""
        return old if rel.endswith("pins.tsv") else ""

    monkeypatch.setattr(pr, "git_show", fake_show)
    seen = _stub(monkeypatch, {})
    assert pr.main(["--check", "--base", "origin-sha"]) == 0  # unchanged: nothing is fetched
    assert seen == []
    old = old.replace("4.3.2", "4.3.1")  # the base had another version: the row changed
    assert pr.main(["--check", "--base", "origin-sha"]) == 1
    assert seen


# --- the wrapper ---


def _wrapper(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, str(WRAPPER), *args],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **env},
    )


def test_wrapper_rejects_unknown_and_incomplete_arguments() -> None:
    bad = _wrapper("--bogus")
    assert bad.returncode == USAGE_EXIT
    assert "usage" in bad.stderr.lower()
    assert _wrapper("--check", "--base").returncode == USAGE_EXIT


def test_wrapper_check_with_no_rows_needs_no_network(tmp_path: Path) -> None:
    empty = tmp_path / "pins.tsv"
    empty.write_text("# nothing\n")
    run = _wrapper(
        "--check",
        PINS=str(empty),
        SECURITY_CONFIG=str(tmp_path / "none.yaml"),
        REQS=str(tmp_path / "reqs"),
        CRD_SOURCES=str(tmp_path / "none.tsv"),
    )
    assert run.returncode == 0, run.stderr


# --- the CI wiring ---


def _pins_job() -> dict[str, object]:
    return workflow("reusable-pins.yml")["jobs"]["pins"]  # type: ignore[no-any-return]


def test_the_drift_job_is_blocking_and_filtered_on_pull_requests() -> None:
    ci = workflow("ci.yml")
    assert "pins" in ci["jobs"]["ci-ok"]["needs"]
    job = ci["jobs"]["pins"]
    assert job["uses"] == "./.github/workflows/reusable-pins.yml"
    assert job["permissions"] == {"contents": "read"}
    steps = ci["jobs"]["changes"]["steps"]
    run = next(s["run"] for s in steps if s.get("id") == "filter")
    pattern = next(m for m in re.findall(r"grep -Eq '([^']+)'", run) if "pins" in m)
    for path in (
        "tools/pins.tsv",
        "mise.toml",
        "mise.lock",
        "tools/requirements/zizmor.txt",
        "infra/security/config.yaml",
        "scripts/pins-refresh.sh",
        "scripts/pins_refresh.py",
        ".github/workflows/reusable-pins.yml",
        ".github/workflows/pins-drift.yml",
        "charts/your-service-name/ci/crds/sources.tsv",
    ):
        assert re.search(pattern, path), f"the pins filter misses {path}"
    assert not re.search(pattern, "src/app.py")


def test_the_drift_job_is_read_only_pinned_and_runs_the_check() -> None:
    job = _pins_job()
    assert job["permissions"] == {"contents": "read"}
    assert job["timeout-minutes"]
    text = (WORKFLOWS / "reusable-pins.yml").read_text(encoding="utf-8")
    assert "scripts/pins-refresh.sh" in text
    assert "--check" in text
    assert "--base" in text
    assert "GITHUB_PATH" not in text


def test_a_weekly_run_checks_every_row() -> None:
    doc = workflow("pins-drift.yml")
    assert doc["on"]["schedule"]
    call = doc["jobs"]["pins"]
    assert call["uses"] == "./.github/workflows/reusable-pins.yml"
    assert "with" not in call  # no base: every row


# --- round 1: anchoring, config.yaml, locks, fetch ---

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
OLD = "2026-01-01T00:00:00.000000Z"
YOUNG = "2026-11-20T00:00:00.000000Z"


def _pypi(files: list[tuple[str, str]]) -> bytes:
    urls = [{"digests": {"sha256": h}, "upload_time_iso_8601": t} for h, t in files]
    return json.dumps({"urls": urls}).encode()


def _lock(name: str, version: str, hashes: list[str]) -> str:
    return f"{name}=={version} \\\n" + " \\\n".join(f"    --hash=sha256:{h}" for h in hashes) + "\n"


@pytest.mark.parametrize("row", rows(), ids=lambda r: f"{r[0]}-{r[2]}")
def test_every_url_is_under_its_expected_vendor_prefix(row: list[str]) -> None:
    assert row[3].startswith(pr.SOURCES[row[0]][2]), f"{row[0]}: url outside the vendor prefix"


def test_config_sources_pin_the_repo_the_annotation_names() -> None:
    notes = dict(
        re.findall(r"datasource=github-releases depName=(\S+)\n\s+([a-z-]+): v", CONFIG.read_text())
    )
    by_tool = {tool: repo for repo, tool in notes.items()}
    for tool, (repo, *_rest) in pr.CONFIG_SOURCES.items():
        assert by_tool[tool] == repo, f"{tool}: the annotation and CONFIG_SOURCES disagree"


@pytest.mark.parametrize(
    ("name", "url"),
    [
        ("kind", "https://github.com/evil/kind/releases/download/v0.33.0/kind-linux-amd64"),
        ("helm4", "https://evil.example/helm-v4.3.2-linux-amd64.tar.gz"),
        ("squawk", "https://github.com/evil/squawk/releases/download/v2.0.0/squawk-linux-x64"),
        ("k8s-openapi", "https://github.com/evil/kubernetes/v1.0.0/swagger.json"),
        ("zizmor", "uv:zizmor-lookalike"),
    ],
)
def test_a_look_alike_vendor_is_refused_before_any_fetch(
    monkeypatch: pytest.MonkeyPatch, name: str, url: str
) -> None:
    seen = _stub(monkeypatch, {})
    with pytest.raises(pr.PinError, match="expected vendor prefix"):
        pr.problem(_pin(name, "1", url, "a" * 64))
    assert seen == []


def test_a_look_alike_row_fails_the_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pins = tmp_path / "pins.tsv"
    url = "https://github.com/evil/kind/releases/download/v0.33.0/kind-linux-amd64"
    pins.write_text(f"kind\t0.33.0\tlinux-amd64\t{url}\t{'a' * 64}\n")
    _env(monkeypatch, tmp_path, pins)
    seen = _stub(monkeypatch, {})
    assert pr.main(["--check"]) == 1
    assert "ERROR kind" in capsys.readouterr().out
    assert seen == []


def test_negative_vendor_files(monkeypatch: pytest.MonkeyPatch) -> None:
    kubectl = "https://dl.k8s.io/release/v1.0.0/bin/linux/amd64/kubectl"
    _stub(monkeypatch, {kubectl + ".sha256": b"not-hex"})
    with pytest.raises(pr.PinError, match="sha256"):
        pr.vendor_sha(_pin("kubectl", "1.0.0", kubectl, "x"))
    api = "https://api.github.com/repos/sbdchd/squawk/releases/tags/v2.0.0"
    url = "https://github.com/sbdchd/squawk/releases/download/v2.0.0/squawk-linux-x64"
    for body in (
        b'{"assets": []}',
        b'{"assets": [{"name": "squawk-linux-x64", "digest": null}]}',
        b'{"assets": [{"name": "squawk-linux-x64", "digest": "sha1:abc"}]}',
    ):
        _stub(monkeypatch, {api: body})
        with pytest.raises(pr.PinError, match="digest"):
            pr.vendor_sha(_pin("squawk", "2.0.0", url, "x"))
    yq = "https://github.com/mikefarah/yq/releases/download/v4.0.0/yq_linux_amd64.tar.gz"
    base = "https://github.com/mikefarah/yq/releases/download/v4.0.0"
    _stub(
        monkeypatch,
        {
            f"{base}/checksums": b"yq_linux_amd64.tar.gz a b",
            f"{base}/checksums_hashes_order": b"MD5\nSHA-1\n",
        },
    )
    with pytest.raises(pr.PinError, match="SHA-256"):
        pr.vendor_sha(_pin("yq", "4.0.0", yq, "x"))


def _cfg_base(tool: str, version: str) -> str:
    repo = pr.CONFIG_SOURCES[tool][0]
    return f"https://github.com/{repo}/releases/download/{version}"


def test_config_sha256_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    base = _cfg_base("syft", "v1.0.0")
    listing = f"{'a' * 64}  syft_1.0.0_linux_amd64.tar.gz\n".encode()
    _stub(monkeypatch, {f"{base}/syft_1.0.0_checksums.txt": listing})
    assert pr.config_problem("syft", "v1.0.0") is None
    _stub(monkeypatch, {f"{base}/syft_1.0.0_checksums.txt": f"{'a' * 64}  other.tar.gz\n".encode()})
    with pytest.raises(pr.PinError, match="not listed"):
        pr.config_problem("syft", "v1.0.0")


def test_config_md5_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    base = _cfg_base("sbom-utility", "v0.1.0")
    md5 = f"{base}/sbom-utility-v0.1.0-linux-amd64.tar.gz.md5"
    _stub(monkeypatch, {md5: b"d" * 32 + b"\n"})
    assert pr.config_problem("sbom-utility", "v0.1.0") is None
    _stub(monkeypatch, {md5: b"nope"})
    assert pr.config_problem("sbom-utility", "v0.1.0")


def test_run_tool_reports_ok_drift_and_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    base = _cfg_base("sbom-utility", "v0.1.0")
    md5 = f"{base}/sbom-utility-v0.1.0-linux-amd64.tar.gz.md5"
    _stub(monkeypatch, {md5: b"d" * 32})
    assert pr.run_tool("sbom-utility", "v0.1.0") is False
    assert "ok sbom-utility v0.1.0 (config.yaml)" in capsys.readouterr().out
    _stub(monkeypatch, {md5: b"nope"})
    assert pr.run_tool("sbom-utility", "v0.1.0") is True
    assert "DRIFT sbom-utility" in capsys.readouterr().out
    _stub(monkeypatch, {})
    assert pr.run_tool("sbom-utility", "v0.1.0") is True
    assert "ERROR sbom-utility" in capsys.readouterr().out


def test_a_config_tool_with_no_vendor_checksum_fails_the_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("tools:\n  syft: v1.0.0\n")
    (tmp_path / "pins.tsv").write_text("# none\n")
    _env(monkeypatch, tmp_path, tmp_path / "pins.tsv")
    monkeypatch.setenv("SECURITY_CONFIG", str(config))
    _stub(monkeypatch, {})
    assert pr.main(["--check"]) == 1
    assert "ERROR syft" in capsys.readouterr().out


# --- locks ---


def _lock_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, files: dict[str, str]) -> None:
    reqs = tmp_path / "reqs"
    reqs.mkdir(exist_ok=True)
    for name, text in files.items():
        (reqs / name).write_text(text)
    monkeypatch.setenv("REQS", str(reqs))
    monkeypatch.setattr(pr, "utcnow", lambda: NOW)


def test_every_lock_entry_is_checked_against_pypi_hashes_and_age(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock = _lock("zizmor", "1.0.0", ["1" * 64]) + _lock("colorama", "0.4.6", ["2" * 64, "3" * 64])
    _lock_dir(monkeypatch, tmp_path, {"zizmor.txt": lock})
    pypi = "https://pypi.org/pypi/{}/{}/json"
    _stub(
        monkeypatch,
        {
            pypi.format("zizmor", "1.0.0"): _pypi([("1" * 64, OLD)]),
            pypi.format("colorama", "0.4.6"): _pypi(
                [("2" * 64, OLD), ("3" * 64, OLD), ("4" * 64, OLD)]
            ),
        },
    )
    assert pr.lock_problems(tmp_path / "reqs" / "zizmor.txt") == []
    _stub(
        monkeypatch,
        {
            pypi.format("zizmor", "1.0.0"): _pypi([("9" * 64, OLD)]),
            pypi.format("colorama", "0.4.6"): _pypi([("2" * 64, OLD), ("3" * 64, OLD)]),
        },
    )
    assert any(
        "zizmor" in p and "hash" in p for p in pr.lock_problems(tmp_path / "reqs" / "zizmor.txt")
    )


def test_a_transitive_package_younger_than_seven_days_is_named(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    lock = _lock("zizmor", "1.0.0", ["1" * 64]) + _lock("pyyaml", "6.0.4", ["2" * 64])
    _lock_dir(monkeypatch, tmp_path, {"zizmor.txt": lock})
    _stub(
        monkeypatch,
        {
            "https://pypi.org/pypi/zizmor/1.0.0/json": _pypi([("1" * 64, OLD)]),
            "https://pypi.org/pypi/pyyaml/6.0.4/json": _pypi([("2" * 64, YOUNG)]),
        },
    )
    problems = pr.lock_problems(tmp_path / "reqs" / "zizmor.txt")
    assert len(problems) == 1
    assert "pyyaml 6.0.4" in problems[0]
    assert "7 days" in problems[0]


def test_the_check_runs_the_lock_check_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "pins.tsv").write_text("# none\n")
    _env(monkeypatch, tmp_path, tmp_path / "pins.tsv")
    _lock_dir(monkeypatch, tmp_path, {"pyyaml.txt": _lock("pyyaml", "6.0.3", ["2" * 64])})
    _stub(monkeypatch, {"https://pypi.org/pypi/pyyaml/6.0.3/json": _pypi([("2" * 64, OLD)])})
    assert pr.main(["--check"]) == 0
    assert "ok lock pyyaml" in capsys.readouterr().out
    _stub(monkeypatch, {})
    assert pr.main(["--check"]) == 1
    assert "ERROR lock pyyaml" in capsys.readouterr().out


# --- regeneration ---


def test_the_regeneration_command_is_the_documented_one_with_a_cutoff() -> None:
    header = " ".join(ln.lstrip("# ") for ln in PINS.read_text().splitlines() if ln.startswith("#"))
    documented = (
        "uv pip compile --generate-hashes --no-header --python-version 3.13"
        " --python-platform x86_64-unknown-linux-gnu"
    )
    assert documented in header
    cmd = pr.compile_command(Path("lock.txt"), Path("c.txt"), NOW)
    assert cmd[0] == pr.UV
    assert " ".join(["uv", *(c for c in cmd[1:] if c != "--quiet")]).startswith(documented)
    assert cmd[cmd.index("--exclude-newer") + 1] == "2026-11-14T12:00:00Z"
    assert cmd[cmd.index("-c") + 1] == "c.txt"
    assert cmd[cmd.index("-o") + 1] == "lock.txt"  # relative to the lock's directory
    assert "--upgrade" not in cmd
    assert "--no-annotate" not in cmd


def test_regeneration_constrains_pyyaml_and_writes_only_where_told(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _lock_dir(monkeypatch, tmp_path, {"pyyaml.txt": _lock("pyyaml", "6.0.3", ["2" * 64])})
    calls: list[tuple[list[str], str, str]] = []

    def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        constraint = Path(cmd[cmd.index("-c") + 1]).read_text()
        calls.append((cmd, str(kw["input"]), constraint))
        (Path(str(kw["cwd"])) / cmd[cmd.index("-o") + 1]).write_text("zizmor==1.2.3\n")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(pr.subprocess, "run", fake_run)
    pr.regenerate_lock(pr.Pin("zizmor", "1.2.3", "any", "uv:zizmor", "-"))
    (cmd, stdin, constraint) = calls[0]
    assert stdin == "zizmor==1.2.3\n"
    assert constraint.strip() == "pyyaml==6.0.3"
    assert cmd[cmd.index("-o") + 1] == "zizmor.txt"


# --- --base selection ---


def _base_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, old: dict[str, str]) -> None:
    pins = _scratch(tmp_path, "b" * 64)
    _env(monkeypatch, tmp_path, pins)
    monkeypatch.setattr(pr, "utcnow", lambda: NOW)
    old = {**old, "tools/pins.tsv": pins.read_text()}

    def fake_show(_base: str, rel: str) -> str:
        if rel in pr.WATCHED:
            return (ROOT / rel).read_text() if (ROOT / rel).is_file() else ""
        if rel.endswith("pins.tsv"):
            return old["tools/pins.tsv"]
        return old.get("lock", "") if rel.endswith(".txt") else ""

    monkeypatch.setattr(pr, "git_show", fake_show)


def test_base_selects_a_changed_config_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("tools:\n  syft: v1.0.0\n")
    _base_env(monkeypatch, tmp_path, {})
    monkeypatch.setenv("SECURITY_CONFIG", str(config))
    _stub(monkeypatch, {})
    assert (
        pr.main(["--check", "--base", "x"]) == 1
    )  # syft is new against the base: checked, and unreachable
    assert "ERROR syft" in capsys.readouterr().out


def test_base_selects_a_lock_only_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _base_env(
        monkeypatch,
        tmp_path,
        {"lock": "old\n"},
    )
    _lock_dir(monkeypatch, tmp_path, {"zizmor.txt": _lock("zizmor", "1.0.0", ["1" * 64])})
    _stub(monkeypatch, {"https://pypi.org/pypi/zizmor/1.0.0/json": _pypi([("1" * 64, OLD)])})
    assert pr.main(["--check", "--base", "x"]) == 0
    assert "ok lock zizmor" in capsys.readouterr().out


@pytest.mark.parametrize(
    "watched", ["scripts/pins_refresh.py", ".github/workflows/reusable-pins.yml"]
)
def test_a_change_to_the_checker_checks_every_row(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, watched: str
) -> None:
    pins = _scratch(tmp_path, "b" * 64)
    _env(monkeypatch, tmp_path, pins)
    same = pins.read_text()

    def fake_show(_base: str, rel: str) -> str:
        if rel == watched:
            return "an older version\n"
        return (
            same
            if rel.endswith("pins.tsv")
            else (ROOT / rel).read_text()
            if (ROOT / rel).is_file()
            else ""
        )

    monkeypatch.setattr(pr, "git_show", fake_show)
    seen = _stub(monkeypatch, {})
    assert pr.main(["--check", "--base", "x"]) == 1
    assert seen, "the unchanged row was not checked although the checker changed"


# --- fetch ---


def test_authorization_is_not_copied_to_redirects_and_http_redirects_are_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    request = pr.build_request("https://api.github.com/x")
    assert not request.headers.get("Authorization")
    assert request.unredirected_hdrs.get("Authorization") == "Bearer t"
    assert "Authorization" not in pr.build_request("https://example.org/x").unredirected_hdrs
    handler = pr.HttpsOnlyRedirect()
    with pytest.raises(pr.PinError, match="non-https redirect"):
        handler.redirect_request(request, None, 302, "Found", {}, "http://example.org/x")


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", code, "err", None, None)  # type: ignore[arg-type]


@pytest.mark.parametrize("transient", [429, 500, 503])
def test_transient_failures_are_retried_with_backoff(
    monkeypatch: pytest.MonkeyPatch, transient: int
) -> None:
    outcomes: list[object] = [_http_error(transient), TimeoutError("slow"), b"data"]
    outcomes_made = outcomes[:-1]
    slept: list[float] = []

    def fake_open(_request: object) -> bytes:
        item = outcomes.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item  # type: ignore[return-value]

    monkeypatch.setattr(pr, "open_url", fake_open)
    monkeypatch.setattr(pr.time, "sleep", slept.append)
    assert pr.fetch("https://x/y") == b"data"
    assert len(slept) == len(outcomes_made)
    assert slept[1] > slept[0]


def test_a_404_is_never_retried_and_retries_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts: list[int] = []

    def always(code: int) -> object:
        def fake_open(_request: object) -> bytes:
            attempts.append(code)
            raise _http_error(code)

        return fake_open

    monkeypatch.setattr(pr.time, "sleep", lambda _s: None)
    monkeypatch.setattr(pr, "open_url", always(404))
    with pytest.raises(pr.PinError):
        pr.fetch("https://x/y")
    assert attempts == [404]
    attempts.clear()
    monkeypatch.setattr(pr, "open_url", always(503))
    with pytest.raises(pr.PinError):
        pr.fetch("https://x/y")
    assert 1 < len(attempts) <= pr.ATTEMPTS


def test_the_constraint_source_is_removed_from_the_via_lines() -> None:
    raw = (
        "pyyaml==6.0.3 \\\n    --hash=sha256:aa\n"
        "    # via\n    #   -c /tmp/x/constraints.txt\n    #   yamale\n"
        "six==1 \\\n    --hash=sha256:bb\n"
        "    # via\n    #   -c /tmp/x/constraints.txt\n    #   a\n    #   b\n"
    )
    tidy = pr.tidy_annotations(raw)
    assert "constraints.txt" not in tidy
    assert "    # via yamale\n" in tidy
    assert "    # via\n    #   a\n    #   b\n" in tidy


def test_only_the_files_a_lock_records_are_age_checked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _lock_dir(monkeypatch, tmp_path, {"zizmor.txt": _lock("pyyaml", "6.0.4", ["2" * 64])})
    path = tmp_path / "reqs" / "zizmor.txt"
    url = "https://pypi.org/pypi/pyyaml/6.0.4/json"
    _stub(monkeypatch, {url: _pypi([("2" * 64, OLD), ("5" * 64, YOUNG)])})
    assert pr.lock_problems(path) == []  # the recent file is not one the lock installs
    _stub(monkeypatch, {url: _pypi([("2" * 64, YOUNG), ("5" * 64, OLD)])})
    problems = pr.lock_problems(path)
    assert len(problems) == 1
    assert "7 days" in problems[0]


# --- round 2: unparsed lock lines, URL normalisation, host paths ---

GOOD_LOCK = _lock("zizmor", "1.0.0", ["1" * 64, "2" * 64])


def test_a_well_formed_lock_has_no_syntax_problems_and_the_real_locks_too() -> None:
    assert pr.lock_syntax_problems(GOOD_LOCK) == []
    commented = GOOD_LOCK.replace("\n", "\n    # via yamale\n", 1)
    assert pr.lock_syntax_problems("# note\n\n" + commented) == []
    for path in sorted(REQS.glob("*.txt")):
        assert pr.lock_syntax_problems(path.read_text()) == [], path.name


@pytest.mark.parametrize(
    "extra",
    [
        "--extra-index-url https://evil.example/simple\n",
        "--index-url https://evil.example/simple\n",
        "-f https://evil.example/wheels\n",
        "-r other.txt\n",
        "-c constraints.txt\n",
        "--trusted-host evil.example\n",
        f"foo==1 --hash=sha256:{'3' * 64}\n",
        "foo>=1\n",
        "git+https://evil.example/foo.git\n",
    ],
)
def test_any_unparsed_or_option_line_in_a_lock_is_drift(extra: str) -> None:
    for text in (GOOD_LOCK + extra, extra + GOOD_LOCK):
        problems = pr.lock_syntax_problems(text)
        assert problems, extra
        assert "line" in problems[0] or "no " in problems[0]


def test_an_empty_lock_or_an_entry_without_hashes_is_drift() -> None:
    assert pr.lock_syntax_problems("")
    assert pr.lock_syntax_problems("# only a comment\n")
    assert pr.lock_syntax_problems("foo==1 \\\n")


def test_the_check_fails_a_lock_with_an_index_option(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "pins.tsv").write_text("# none\n")
    _env(monkeypatch, tmp_path, tmp_path / "pins.tsv")
    lock = "--extra-index-url https://evil.example/simple\n" + GOOD_LOCK
    _lock_dir(monkeypatch, tmp_path, {"zizmor.txt": lock})
    _stub(
        monkeypatch,
        {"https://pypi.org/pypi/zizmor/1.0.0/json": _pypi([("1" * 64, OLD), ("2" * 64, OLD)])},
    )
    assert pr.main(["--check"]) == 1
    assert "DRIFT lock zizmor" in capsys.readouterr().out


GOOD = "https://github.com/kubernetes-sigs/kind/releases/download/v0.33.0/kind-linux-amd64"


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/kubernetes-sigs/kind/releases/download/../../../../evil/kind/releases/download/v0.33.0/kind-linux-amd64",
        "https://github.com/kubernetes-sigs/kind/releases/download/%2e%2e/%2e%2e/evil/x",
        "https://github.com/kubernetes-sigs/kind/releases/download/v0.33.0/kind-linux-amd64?x=1",
        "https://github.com/kubernetes-sigs/kind/releases/download/v0.33.0/kind-linux-amd64#frag",
        "https://user@github.com/kubernetes-sigs/kind/releases/download/v0.33.0/kind-linux-amd64",
        "https://github.com:443/kubernetes-sigs/kind/releases/download/v0.33.0/kind-linux-amd64",
        "https://github.com/kubernetes-sigs/kind/releases/download//v0.33.0/kind-linux-amd64",
        "http://github.com/kubernetes-sigs/kind/releases/download/v0.33.0/kind-linux-amd64",
    ],
)
def test_a_url_that_escapes_the_vendor_prefix_is_refused(url: str) -> None:
    assert pr.url_problem(url)
    with pytest.raises(pr.PinError, match=r"expected vendor prefix|plain https"):
        pr.check_prefix(_pin("kind", "0.33.0", url, "a" * 64))


def test_a_plain_vendor_url_is_accepted() -> None:
    assert pr.url_problem(GOOD) is None
    pr.check_prefix(_pin("kind", "0.33.0", GOOD, "a" * 64))


@pytest.mark.parametrize(
    "row", [r for r in rows() if not r[3].startswith("uv:")], ids=lambda r: r[0]
)
def test_every_row_url_is_plain(row: list[str]) -> None:
    assert pr.url_problem(row[3]) is None


def test_no_absolute_path_is_ever_written_into_a_lock() -> None:
    raw = (
        "yamale==6.1.0 \\\n    --hash=sha256:aa\n"
        "pyyaml==6.0.3 \\\n    --hash=sha256:bb\n    # via\n    #   -c /tmp/x/constraints.txt\n"
        "    #   -r /work/in.txt\n    #   yamale\n"
        "six==1 \\\n    --hash=sha256:cc\n    # via -r /work/in.txt\n"
        "idna==3 \\\n    --hash=sha256:dd\n"
        "    # via\n    #   -r /scratch/in.txt\n    #   a\n    #   b\n"
    )
    tidy = pr.tidy_annotations(raw)
    assert not re.search(r"-[rc] /", tidy)
    assert "    # via yamale\n" in tidy
    assert "    # via a\n" not in tidy
    assert "    # via\n    #   a\n    #   b\n" in tidy
    assert "six==1" in tidy
    assert "# via -r" not in tidy


def test_the_command_uses_repo_relative_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _lock_dir(monkeypatch, tmp_path, {"pyyaml.txt": _lock("pyyaml", "6.0.3", ["2" * 64])})
    seen: dict[str, object] = {}

    def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        seen["cmd"], seen["cwd"] = cmd, kw.get("cwd")
        (Path(str(kw["cwd"])) / cmd[cmd.index("-o") + 1]).write_text("zizmor==1.2.3\n")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(pr.subprocess, "run", fake_run)
    pr.regenerate_lock(pr.Pin("zizmor", "1.2.3", "any", "uv:zizmor", "-"))
    cmd = seen["cmd"]
    assert isinstance(cmd, list)
    assert cmd[cmd.index("-o") + 1] == "zizmor.txt"
    assert seen["cwd"] == tmp_path / "reqs"


def test_pyyaml_lock_pins_only_pyyaml() -> None:
    assert list(pr.lock_entries((REQS / "pyyaml.txt").read_text())) == ["pyyaml"]


# --- the pinned CRD sources (charts/your-service-name/ci/crds/sources.tsv) ---

CRD_URL = (
    "https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.7.0/standard-install.yaml"
)


def _crd_sources(tmp_path: Path, sha: str, url: str = CRD_URL) -> Path:
    path = tmp_path / "sources.tsv"
    path.write_text(f"# header\ngateway-api\t1.7.0\t{url}\t{sha}\tHTTPRoute\n")
    return path


def test_the_real_crd_sources_are_well_formed_and_under_a_known_vendor() -> None:
    for row in pr.parse_crd_sources(pr.CRD_SOURCES_PATH.read_text()):
        assert row.url.startswith(pr.CRD_PREFIXES), row.url
        assert f"v{row.version}/" in row.url, row.name
        assert SHA.fullmatch(row.sha256), row.name


def test_a_crd_source_is_checked_against_the_file_at_its_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _env(monkeypatch, tmp_path, _scratch(tmp_path, "a" * 64))
    sums = "https://get.helm.sh/helm-v4.3.2-linux-amd64.tar.gz.sha256sum"
    body = b"crds"
    _stub(
        monkeypatch, {sums: f"{'a' * 64}  helm-v4.3.2-linux-amd64.tar.gz\n".encode(), CRD_URL: body}
    )
    sources = _crd_sources(tmp_path, hashlib.sha256(body).hexdigest())
    monkeypatch.setenv("CRD_SOURCES", str(sources))
    assert pr.main(["--check"]) == 0
    assert "ok crd gateway-api 1.7.0" in capsys.readouterr().out
    sources = _crd_sources(tmp_path, "b" * 64)
    assert pr.main(["--check"]) == 1
    assert "DRIFT crd gateway-api 1.7.0" in capsys.readouterr().out
    assert sources.read_text().count("b" * 64) == 1  # --check writes nothing


def test_an_update_writes_the_crd_sources_sha256(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path, tmp_path / "pins.tsv")
    (tmp_path / "pins.tsv").write_text("# none\n")
    _stub(monkeypatch, {CRD_URL: b"crds"})
    sources = _crd_sources(tmp_path, "b" * 64)
    monkeypatch.setenv("CRD_SOURCES", str(sources))
    assert pr.main([]) == 0
    sha = hashlib.sha256(b"crds").hexdigest()
    assert sources.read_text() == f"# header\ngateway-api\t1.7.0\t{CRD_URL}\t{sha}\tHTTPRoute\n"


def test_a_crd_source_outside_the_known_vendors_is_refused_before_any_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _env(monkeypatch, tmp_path, tmp_path / "pins.tsv")
    (tmp_path / "pins.tsv").write_text("# none\n")
    seen = _stub(monkeypatch, {})
    monkeypatch.setenv(
        "CRD_SOURCES", str(_crd_sources(tmp_path, "a" * 64, "https://evil.test/v1.7.0/x.yaml"))
    )
    assert pr.main(["--check"]) == 1
    assert "ERROR crd gateway-api" in capsys.readouterr().out
    assert seen == []


def test_with_a_base_only_changed_crd_rows_are_checked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path, tmp_path / "pins.tsv")
    (tmp_path / "pins.tsv").write_text("# none\n")
    sources = _crd_sources(tmp_path, "b" * 64)
    monkeypatch.setenv("CRD_SOURCES", str(sources))
    old = sources.read_text()

    def fake_show(_base: str, rel: str) -> str:
        if rel in pr.WATCHED:
            return (ROOT / rel).read_text() if (ROOT / rel).is_file() else ""
        return old if rel.endswith("sources.tsv") else "# none\n"

    monkeypatch.setattr(pr, "git_show", fake_show)
    seen = _stub(monkeypatch, {})
    assert pr.main(["--check", "--base", "origin-sha"]) == 0
    assert seen == []
    old = old.replace("1.7.0", "1.6.3")
    assert pr.main(["--check", "--base", "origin-sha"]) == 1
    assert seen


# --- mise.toml and mise.lock: the lock must follow the config, for both platforms ---

MISE_TOML = ROOT / "mise.toml"
MISE_LOCK = ROOT / "mise.lock"


def _mise_files(tmp_path: Path, config: str, lock: str) -> None:
    (tmp_path / "mise.toml").write_text(config)
    (tmp_path / "mise.lock").write_text(lock)


def test_the_real_lock_follows_the_real_config() -> None:
    assert pr.mise_problems(MISE_TOML.read_text(), MISE_LOCK.read_text()) == []


def test_a_bump_in_mise_toml_without_its_lock_is_a_problem() -> None:
    config = MISE_TOML.read_text().replace(
        '"aqua:kubernetes-sigs/kind" = "0.33.0"', '"aqua:kubernetes-sigs/kind" = "0.34.0"'
    )
    found = pr.mise_problems(config, MISE_LOCK.read_text())
    assert found == ["aqua:kubernetes-sigs/kind: mise.toml pins 0.34.0, mise.lock has 0.33.0"]


def test_a_lock_entry_missing_a_platform_checksum_or_a_stale_entry_is_a_problem() -> None:
    lock = MISE_LOCK.read_text()
    no_mac = re.sub(
        r'(\[tools\.helm4\."platforms\.macos-arm64"\]\n)checksum = "[^"]*"\n', r"\1", lock
    )
    assert no_mac != lock
    assert pr.mise_problems(MISE_TOML.read_text(), no_mac) == [
        "helm4: no sha256 for macos-arm64 in mise.lock"
    ]
    config = MISE_TOML.read_text().replace('"aqua:kubernetes-sigs/kind" = "0.33.0"\n', "")
    assert pr.mise_problems(config, lock) == [
        "aqua:kubernetes-sigs/kind: in mise.lock, not in mise.toml"
    ]


def test_check_names_a_lock_that_lags_the_config_and_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = MISE_TOML.read_text().replace(
        '"aqua:kubernetes-sigs/kind" = "0.33.0"', '"aqua:kubernetes-sigs/kind" = "0.34.0"'
    )
    _mise_files(tmp_path, config, MISE_LOCK.read_text())
    monkeypatch.setattr(pr, "MISE_TOML", tmp_path / "mise.toml")
    monkeypatch.setattr(pr, "MISE_LOCK", tmp_path / "mise.lock")
    _env(monkeypatch, tmp_path, _scratch(tmp_path, "a" * 64))
    ran: list[list[str]] = []

    def record(platforms: list[str]) -> bool:
        ran.append(platforms)
        return True

    monkeypatch.setattr(pr, "mise_lock", record)
    assert pr.main(["--check"]) == 1
    assert (
        "DRIFT mise.lock: aqua:kubernetes-sigs/kind: mise.toml pins 0.34.0"
        in capsys.readouterr().out
    )
    assert ran == []


def test_refresh_runs_mise_lock_for_both_platforms_only_when_the_lock_lags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = MISE_TOML.read_text().replace(
        '"aqua:kubernetes-sigs/kind" = "0.33.0"', '"aqua:kubernetes-sigs/kind" = "0.34.0"'
    )
    _mise_files(tmp_path, config, MISE_LOCK.read_text())
    monkeypatch.setattr(pr, "MISE_TOML", tmp_path / "mise.toml")
    monkeypatch.setattr(pr, "MISE_LOCK", tmp_path / "mise.lock")
    _env(monkeypatch, tmp_path, _scratch(tmp_path, "a" * 64))
    sums = "https://get.helm.sh/helm-v4.3.2-linux-amd64.tar.gz.sha256sum"
    _stub(monkeypatch, {sums: f"{'a' * 64}  helm-v4.3.2-linux-amd64.tar.gz\n".encode()})
    ran: list[list[str]] = []

    def lock(platforms: list[str]) -> bool:
        ran.append(platforms)
        (tmp_path / "mise.lock").write_text(
            MISE_LOCK.read_text().replace('version = "0.33.0"', 'version = "0.34.0"')
        )
        return True

    monkeypatch.setattr(pr, "mise_lock", lock)
    assert pr.main([]) == 0
    assert ran == [["linux-x64", "macos-arm64"]]
    assert "UPDATED mise.lock" in capsys.readouterr().out
    assert pr.main([]) == 0  # in step now: nothing more to lock
    assert len(ran) == 1


def test_refresh_fails_when_mise_lock_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = MISE_TOML.read_text().replace(
        '"aqua:kubernetes-sigs/kind" = "0.33.0"', '"aqua:kubernetes-sigs/kind" = "0.34.0"'
    )
    _mise_files(tmp_path, config, MISE_LOCK.read_text())
    monkeypatch.setattr(pr, "MISE_TOML", tmp_path / "mise.toml")
    monkeypatch.setattr(pr, "MISE_LOCK", tmp_path / "mise.lock")
    _env(monkeypatch, tmp_path, _scratch(tmp_path, "a" * 64))
    monkeypatch.setattr(pr, "mise_lock", lambda _platforms: False)
    assert pr.main([]) == 1
