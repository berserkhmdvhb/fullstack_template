"""The kind harness verifies that data piped through ``kubectl exec -i`` arrived intact and retries
when it did not (kubernetes/kubernetes#112834), tests/k8s/_kube.py exec_stdin. Offline: a fake
runner stands in for kubectl."""

from __future__ import annotations

import hashlib
import subprocess

import pytest

from tests.k8s._kube import STDIN_ATTEMPTS, STDIN_LOST, Kube, exec_stdin


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


def _run(runner: _Runner, digests: list[str]) -> str:
    def command(digest: str) -> list[str]:
        digests.append(digest)
        return ["exec", digest]

    return exec_stdin(_kube(), "a site", "payload\n", command, runner)


def test_the_digest_passed_is_the_payloads_sha256() -> None:
    digests: list[str] = []
    assert _run(_Runner(0), digests) == "out"
    assert digests == [hashlib.sha256(b"payload\n").hexdigest()]


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
