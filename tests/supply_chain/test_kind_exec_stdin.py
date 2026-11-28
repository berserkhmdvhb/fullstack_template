"""The kind harness verifies that data piped through ``kubectl exec -i`` arrived intact and retries
when it did not (kubernetes/kubernetes#112834), tests/k8s/_kube.py exec_stdin. Offline: a fake
runner stands in for kubectl, and the in-pod check runs under /bin/sh."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.k8s import _kube as _kube_module
from tests.k8s._kube import SH_STDIN_CHECK, STDIN_ATTEMPTS, STDIN_LOST, Kube, exec_stdin


class _Runner:
    def __init__(self, *codes: int) -> None:
        self.codes = list(codes)
        self.sent: list[str | None] = []

    def __call__(
        self, cmd: list[str], stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        self.sent.append(stdin)
        code = self.codes.pop(0)
        return subprocess.CompletedProcess(cmd, code, "out" if code == 0 else "", "err")


def _kube() -> Kube:
    kube = Kube.__new__(Kube)  # no cluster: only its kubectl prefix is used
    kube.base = ["kubectl"]
    return kube


def _run(runner: _Runner, sizes: list[str]) -> str:
    def command(size: str) -> list[str]:
        sizes.append(size)
        return ["exec", size]

    return exec_stdin(_kube(), "a site", "payload\n", command, runner)


def test_the_size_passed_is_the_payloads_byte_count() -> None:
    sizes: list[str] = []
    assert _run(_Runner(0), sizes) == "out"
    assert sizes == ["8"]  # "payload\n": the trailing newline counts


def test_a_lost_stdin_is_retried_with_a_note_that_holds_no_data(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _Runner(STDIN_LOST, 0)
    assert _run(runner, []) == "out"
    assert runner.sent == ["payload\n", "payload\n"]
    printed = capsys.readouterr().out
    assert "a site" in printed
    assert "attempt 1 of 3" in printed
    assert "payload" not in printed


def test_three_lost_stdins_fail_naming_the_upstream_issue(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = _Runner(STDIN_LOST, STDIN_LOST, STDIN_LOST)
    with pytest.raises(pytest.fail.Exception, match=r"kubernetes/kubernetes#112834") as failed:
        _run(runner, [])
    assert len(runner.sent) == STDIN_ATTEMPTS
    assert "payload" not in str(failed.value) + capsys.readouterr().out


def test_another_failure_is_not_retried() -> None:
    runner = _Runner(1)
    with pytest.raises(pytest.fail.Exception, match=r"failed \(1\): err"):
        _run(runner, [])
    assert len(runner.sent) == 1


def test_the_size_is_in_bytes_and_is_what_run_both_sends(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        sent.update(kwargs)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("subprocess.run", fake_run)
    sizes: list[str] = []

    def command(size: str) -> list[str]:
        sizes.append(size)
        return ["exec"]

    exec_stdin(_kube(), "a site", "caf\u00e9\n", command, _kube_module._run_both)
    assert sent["encoding"] == "utf-8"
    assert sizes == [str(len(sent["input"].encode(sent["encoding"])))]
    assert sizes == ["6"]  # five characters, six bytes


# The in-pod check itself, as the helper sends it: sh -c '<SH_STDIN_CHECK> <command>' sh <count> ...
SH = shutil.which("sh")
needs_sh = pytest.mark.skipif(SH is None, reason="no sh")
DATA = "caf\u00e9\npolicy\n".encode()  # multi-byte character and a trailing newline


def _in_pod(
    tmp_path: Path, count: int | str, data: bytes = DATA, stubs: dict[str, str] | None = None
) -> tuple[int, bytes | None]:
    """Run the check with a stand-in command (``cat >$OUT``): its exit code, and the bytes the
    command received (None if it never ran). ``stubs`` put a script of that name first on PATH."""
    assert SH is not None
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (stubs or {}).items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    out = tmp_path / "out"
    env = {**os.environ, "OUT": str(out), "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    cmd = [SH, "-c", SH_STDIN_CHECK + 'cat >"$OUT"', "sh", str(count)]
    done = subprocess.run(cmd, input=data, capture_output=True, env=env, check=False)
    return done.returncode, out.read_bytes() if out.exists() else None


@needs_sh
def test_the_right_count_reaches_the_command_byte_for_byte(tmp_path: Path) -> None:
    assert _in_pod(tmp_path, len(DATA)) == (0, DATA)


@needs_sh
@pytest.mark.parametrize(
    ("data", "count"),
    [(DATA[:-1], len(DATA)), (b"", len(DATA)), (DATA + b"x", len(DATA))],
    ids=["short", "empty", "one extra byte"],
)
def test_a_wrong_count_exits_lost_and_the_command_never_runs(
    tmp_path: Path, data: bytes, count: int
) -> None:
    assert _in_pod(tmp_path, count, data) == (STDIN_LOST, None)


@needs_sh
def test_a_padded_wc_count_still_matches(tmp_path: Path) -> None:
    real = shutil.which("wc")
    pad = f'#!/bin/sh\nn=$({real} "$@")\nprintf "%8s\\n" "$n"\n'
    assert _in_pod(tmp_path, len(DATA), stubs={"wc": pad}) == (0, DATA)


@needs_sh
def test_a_failing_mktemp_exits_one_and_the_command_never_runs(tmp_path: Path) -> None:
    assert _in_pod(tmp_path, len(DATA), stubs={"mktemp": "#!/bin/sh\nexit 1\n"}) == (1, None)
