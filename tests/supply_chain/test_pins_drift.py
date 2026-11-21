"""Pin drift: tools/pins.tsv and config.yaml structure, and scripts/pins_refresh.py (no network).

The vendor fetch layer is stubbed; the real network run is the blocking `pins` job in ci.yml.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

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
    assert pinned == set(pr.SOURCES), "a pin with no checksum source (or a stale source entry)"


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
                "https://github.com/o/kind/releases/download/v0.33.0/kind-linux-amd64",
            ),
            "https://github.com/o/kind/releases/download/v0.33.0/kind-linux-amd64.sha256sum",
        ),
        (
            ("node", "22.1.0", "https://nodejs.org/dist/v22.1.0/node-v22.1.0-linux-x64.tar.xz"),
            "https://nodejs.org/dist/v22.1.0/SHASUMS256.txt",
        ),
        (
            (
                "gitleaks",
                "8.1.0",
                "https://github.com/o/g/releases/download/v8.1.0/gitleaks_8.1.0_linux_x64.tar.gz",
            ),
            "https://github.com/o/g/releases/download/v8.1.0/gitleaks_8.1.0_checksums.txt",
        ),
        (
            (
                "hadolint",
                "2.1.0",
                "https://github.com/o/h/releases/download/v2.1.0/hadolint-linux-x86_64",
            ),
            "https://github.com/o/h/releases/download/v2.1.0/checksums.sha256",
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


def test_github_release_digest_for_squawk_and_shellcheck(monkeypatch: pytest.MonkeyPatch) -> None:
    api = "https://api.github.com/repos/sbdchd/squawk/releases/tags/v2.0.0"
    body = (
        '{"assets": [{"name": "squawk-linux-x64", "digest": "sha256:' + "f" * 64 + '"}]}'
    ).encode()
    _stub(monkeypatch, {api: body})
    url = "https://github.com/sbdchd/squawk/releases/download/v2.0.0/squawk-linux-x64"
    assert pr.vendor_sha(_pin("squawk", "2.0.0", url, "x")) == "f" * 64
    assert pr.SOURCES["shellcheck"][0] == "gh-digest"


def test_bats_hashes_the_tag_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    url = "https://github.com/bats-core/bats-core/archive/refs/tags/v1.0.0.tar.gz"
    _stub(monkeypatch, {url: b"archive"})
    assert pr.vendor_sha(_pin("bats", "1.0.0", url, "x")) == hashlib.sha256(b"archive").hexdigest()


def test_uv_row_checks_the_lock_against_pypi(
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
    assert pr.problem(pin) is None
    assert "version" in (pr.problem(_pin("zizmor", "1.0.1", "uv:zizmor", "-")) or "")
    _stub(monkeypatch, {"https://pypi.org/pypi/zizmor/1.0.0/json": b'{"urls": []}'})
    assert "PyPI" in (pr.problem(pin) or "")


# --- --check and update over a scratch pins file ---


def _scratch(tmp_path: Path, sha: str) -> Path:
    pins = tmp_path / "pins.tsv"
    url = "https://get.helm.sh/helm-v4.3.2-linux-amd64.tar.gz"
    pins.write_text(f"# header\n\nhelm4\t4.3.2\tlinux-amd64\t{url}\t{sha}\n")
    return pins


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pins: Path) -> None:
    monkeypatch.setenv("PINS", str(pins))
    monkeypatch.setenv("SECURITY_CONFIG", str(tmp_path / "none.yaml"))


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
    run = _wrapper("--check", PINS=str(empty), SECURITY_CONFIG=str(tmp_path / "none.yaml"))
    assert run.returncode == 0, run.stderr
