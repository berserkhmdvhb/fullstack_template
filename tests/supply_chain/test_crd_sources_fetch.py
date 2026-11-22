"""`scripts/tools.sh install crd-sources` repairs a partial or missing cache (network stubbed).

The cache is keyed by sha256: a present, matching file is kept, a missing one is fetched and
verified against the pinned sha256, and a mismatch is refused and never cached.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent


def _load(monkeypatch: pytest.MonkeyPatch, tmp: Path, rows: str) -> ModuleType:
    sources = tmp / "sources.tsv"
    sources.write_text(rows)
    monkeypatch.setenv("CRD_SOURCES", str(sources))
    monkeypatch.setenv("CRD_CACHE", str(tmp / "cache"))
    spec = importlib.util.spec_from_file_location(
        "kube_schemas", ROOT / "scripts/ci/kube_schemas.py"
    )
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["kube_schemas"] = module
    spec.loader.exec_module(module)
    return module


def _serve(
    monkeypatch: pytest.MonkeyPatch, module: ModuleType, files: dict[str, bytes]
) -> list[str]:
    seen: list[str] = []

    def urlopen(url: str, timeout: int) -> io.BytesIO:  # noqa: ARG001
        seen.append(url)
        return io.BytesIO(files[url])

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    return seen


def test_only_the_missing_source_is_fetched_and_verified(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    a, b = b"crd a", b"crd b"
    sha_a, sha_b = hashlib.sha256(a).hexdigest(), hashlib.sha256(b).hexdigest()
    rows = (
        f"a\t1.0.0\thttps://github.com/o/a/releases/download/v1.0.0/a.yaml\t{sha_a}\tA\n"
        f"b\t1.0.0\thttps://github.com/o/b/releases/download/v1.0.0/b.yaml\t{sha_b}\tB\n"
    )
    module = _load(monkeypatch, tmp_path, rows)
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / sha_a).write_bytes(a)  # the state of a cache that lost one file
    seen = _serve(
        monkeypatch, module, {"https://github.com/o/b/releases/download/v1.0.0/b.yaml": b}
    )
    assert module.fetch() == 0
    assert seen == ["https://github.com/o/b/releases/download/v1.0.0/b.yaml"]
    assert (tmp_path / "cache" / sha_b).read_bytes() == b
    assert module.fetch() == 0
    assert len(seen) == 1  # a complete cache fetches nothing


def test_a_fetched_source_that_does_not_match_its_pin_is_refused_and_not_cached(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    url = "https://github.com/o/a/releases/download/v1.0.0/a.yaml"
    module = _load(monkeypatch, tmp_path, f"a\t1.0.0\t{url}\t{'0' * 64}\tA\n")
    _serve(monkeypatch, module, {url: b"something else"})
    with pytest.raises(SystemExit):
        module.fetch()
    assert not (tmp_path / "cache" / ("0" * 64)).exists()


def test_tools_sh_runs_that_fetch_for_crd_sources() -> None:
    text = (ROOT / "scripts" / "tools.sh").read_text(encoding="utf-8")
    assert 'kube_schemas.py" crds fetch' in text
