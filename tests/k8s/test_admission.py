# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
The image admission policy on the kind cluster fst (wiki/admission-control.md, "Proven on kind").

Run with ``make kind-admission-test`` after ``make kind-up``; ``make test`` never collects it
(tests/conftest.py ignores tests/k8s). It talks to fst only, through .local/kind/kubeconfig, and
refuses any other context. As cluster admin it installs the pinned Kyverno (mise.toml;
tools/pins.tsv, kyverno-chart; deploy/policies/test/kyverno-values.yaml) and applies the policy's
kind variant (deploy/policies/test/kyverno-verify-images-kind.yaml), which matches only
``NAMESPACE``, so the
main release is never judged by it. Two ephemeral cosign key pairs are generated in a temporary
directory (a random password in the environment), with an empty signing config: no transparency
log, no signing service. The images live in a throwaway registry container, ``fst-registry`` on the
kind network, published on 127.0.0.1 only, serving TLS from a throwaway CA that the kind node's
containerd (a hosts.toml) and Kyverno (its CA bundle) trust, so both pull the same reference.
Everything is removed at the end: the policy, the namespaces, Kyverno and its webhooks and CRDs,
the registry, the node's registry config, the local tags and the temporary directory.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.k8s import _kube
from tests.k8s._kube import (
    CONTEXT,
    KUBECONFIG,
    REGISTRY,
    REGISTRY_IMAGE,
    ROOT,
    Kube,
    _run,
    _tool,
    manifest_digest,
    registry_tls,
)
from tests.supply_chain._pins import pin as pinned

NAMESPACE = "fst-admission"
KYVERNO_NAMESPACE = "fst-kyverno"
KYVERNO_RELEASE = "kyverno"
POLICY = "verify-signed-images-kind"
VARIANT = ROOT / "deploy" / "policies" / "test" / "kyverno-verify-images-kind.yaml"
VALUES = ROOT / "deploy" / "policies" / "test" / "kyverno-values.yaml"
TAG = "test"
# Repository -> (key that signs it or None, attested): one image, pushed under each.
IMAGES: dict[str, tuple[str | None, bool]] = {
    "fst/signed": ("trusted", True),
    "fst/unsigned": (None, False),
    "fst/no-attestation": ("trusted", False),
    "fst/other-key": ("other", True),
    "outside/unsigned": (None, False),  # outside the variant's glob (fst-registry:5000/fst/*)
}
SIGNATURE_DENIED = "Images must be signed by this repository's build workflow"
ATTESTATION_DENIED = "Images must carry a SLSA build provenance attestation"
PREDICATE = {
    "buildDefinition": {
        "buildType": "https://example.invalid/kind-admission-test",
        "externalParameters": {},
    },
    "runDetails": {"builder": {"id": "https://example.invalid/kind-admission-test"}},
}


# The ephemeral keys' password: random per run, held by this process only.
PASSWORD = secrets.token_urlsafe(24)


def _pin(name: str) -> str:
    return pinned(name)


def _docker(*args: str, check: bool = True) -> str:
    return _kube.docker(*args, check=check)


def _closed_port() -> int:
    """A loopback port nothing listens on (bound, then released)."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# cosign's HTTP goes through a proxy on a closed loopback port, so any call beyond the loopback
# registry fails the run (Go never proxies loopback; it reaches the registry directly).
_PROXY = f"http://127.0.0.1:{_closed_port()}"
NO_EGRESS = {"HTTPS_PROXY": _PROXY, "HTTP_PROXY": _PROXY, "NO_PROXY": "127.0.0.1", "ALL_PROXY": ""}


def _cosign(keys: Path, *args: str) -> None:
    """cosign with the keys' password in its environment only (never in a file or an argument),
    and no way out but the loopback registry (NO_EGRESS)."""
    env = {**os.environ, **NO_EGRESS, "COSIGN_PASSWORD": PASSWORD}
    done = subprocess.run(
        [_tool("cosign"), *args], cwd=keys, env=env, capture_output=True, text=True, check=False
    )
    if done.returncode:
        pytest.fail(f"cosign {args[0]} failed ({done.returncode}): {done.stderr.strip()}")


def _create(kube: Kube, obj: dict[str, Any], *dry_run: str) -> subprocess.CompletedProcess[str]:
    """kubectl create of one object: the result, admitted or not (stderr holds a denial)."""
    return subprocess.run(
        [*kube.base, "create", *dry_run, "-f", "-"],
        input=json.dumps(obj),
        capture_output=True,
        text=True,
        env={**os.environ, "KUBECONFIG": str(KUBECONFIG)},
        check=False,
    )


def _pod_spec(image: str) -> dict[str, Any]:
    return {
        "terminationGracePeriodSeconds": 0,
        "containers": [{"name": "app", "image": image, "imagePullPolicy": "IfNotPresent"}],
    }


def _pod(name: str, image: str) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": name, "namespace": NAMESPACE},
        "spec": _pod_spec(image),
    }


def _deployment(name: str, image: str) -> dict[str, Any]:
    labels = {"app": name}
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": NAMESPACE},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": labels},
            "template": {"metadata": {"labels": labels}, "spec": _pod_spec(image)},
        },
    }


def _image(repository: str) -> str:
    return f"{REGISTRY}/{repository}:{TAG}"


def _assert_denied(done: subprocess.CompletedProcess[str], message: str) -> None:
    """Denied by the policy itself, with that validation's message, not by an unrelated error."""
    assert done.returncode != 0, f"admitted: {done.stdout.strip()}"
    assert f"Policy {POLICY} failed: {message}" in done.stderr, done.stderr


@pytest.fixture(scope="module")
def kube() -> Kube:
    assert KUBECONFIG.is_file(), f"no {KUBECONFIG}: run make kind-up first"
    return Kube(NAMESPACE)


@pytest.fixture(scope="module")
def work() -> Iterator[Path]:
    """The test's temporary directory (keys, password, CA): deleted on every exit path."""
    path = Path(tempfile.mkdtemp(prefix="fst-admission-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture(scope="module")
def tls(work: Path) -> Path:
    """A throwaway CA and the registry's certificate (fst-registry and 127.0.0.1)."""
    return registry_tls(work / "tls")


@pytest.fixture(scope="module")
def registry(kube: Kube, tls: Path) -> Iterator[str]:
    """fst-registry on the kind network, and its host address (127.0.0.1:<port>)."""
    with _kube.registry(kube, tls) as host:
        yield host


@pytest.fixture(scope="module")
def images(registry: str, tls: Path) -> Iterator[dict[str, str]]:
    """The registry image, pushed under each repository of IMAGES: repository -> digest."""
    tags = [f"{registry}/{repository}:{TAG}" for repository in IMAGES]
    digests = {}
    try:
        _docker("pull", "--quiet", REGISTRY_IMAGE)
        for repository, tag in zip(IMAGES, tags, strict=True):
            _docker("tag", REGISTRY_IMAGE, tag)
            _docker("push", "--quiet", tag)
            digests[repository] = manifest_digest(registry, repository, TAG, tls)
        assert len(set(digests.values())) == 1, digests  # one image under every name
        yield digests
    finally:
        for tag in tags:
            _docker("image", "rm", tag, check=False)  # the local tags only (the image stays)


@pytest.fixture(scope="module")
def keys(work: Path) -> Path:
    """Two ephemeral key pairs (trusted, other) under one random password, and a signing config
    with no services: cosign then signs with the key only, uploads to no log, calls no service."""
    out = work / "keys"
    out.mkdir(mode=0o700)
    for name in ("trusted", "other"):
        _cosign(out, "generate-key-pair", "--output-key-prefix", name)
    _cosign(out, "signing-config", "create", "--out", "signing-config.json")
    services = json.loads((out / "signing-config.json").read_text())
    assert not any(k.endswith("Urls") for k in services), services  # no Fulcio, Rekor or TSA
    return out


@pytest.fixture(scope="module")
def signed(images: dict[str, str], keys: Path, registry: str, tls: Path) -> dict[str, str]:
    """Each image of IMAGES signed and attested as listed (from the host, 127.0.0.1)."""
    (keys / "predicate.json").write_text(json.dumps(PREDICATE))
    common = ["--yes", "--signing-config", "signing-config.json", "--registry-cacert"]
    common.append(str(tls / "ca.crt"))
    for repository, (key, attested) in IMAGES.items():
        if key is None:
            continue
        ref = f"{registry}/{repository}@{images[repository]}"
        _cosign(keys, "sign", *common, "--key", f"{key}.key", ref)
        if attested:
            _cosign(
                keys,
                "attest",
                *common,
                "--key",
                f"{key}.key",
                "--type",
                "slsaprovenance1",
                "--predicate",
                "predicate.json",
                ref,
            )
    return images


def _dump(admin: Kube) -> None:
    """Kyverno's admission controller log and the policy's status, before they are removed."""
    print(
        admin(
            "logs",
            "deploy/kyverno-admission-controller",
            "-c",
            "kyverno",
            "--tail=200",
            check=False,
        )
    )
    print(admin("get", "imagevalidatingpolicies", "-o", "yaml", check=False))


@pytest.fixture(scope="module")
def kyverno(request: pytest.FixtureRequest, tls: Path) -> Iterator[Kube]:
    """The pinned Kyverno, admission controller only, trusting the throwaway CA; then removed
    with its webhooks (the chart's cleanup hook is off) and its CRDs (some carry keep)."""
    version = _pin("kyverno-chart")
    chart = ROOT / ".tools" / "opt" / f"kyverno-chart-{version}" / "kyverno"
    assert chart.is_dir(), f"no {chart}: scripts/tools.sh install kyverno-chart"
    meta = yaml.safe_load((chart / "Chart.yaml").read_text())
    assert (meta["version"], meta["appVersion"]) == (version, f"v{_pin('kyverno')}"), meta
    helm = [_tool("helm"), "--kubeconfig", str(KUBECONFIG), "--kube-context", CONTEXT]
    helm += ["-n", KYVERNO_NAMESPACE]
    admin = Kube(KYVERNO_NAMESPACE)
    try:
        _run(
            [
                *helm,
                "install",
                KYVERNO_RELEASE,
                str(chart),
                "--create-namespace",
                "-f",
                str(VALUES),
                "--set-file",
                f"admissionController.caCertificates.data={tls / 'ca.crt'}",
                "--wait",
                "--timeout",
                "5m",
            ]
        )
        # The controller's own startup line names its version: an image digest left behind by a
        # tag bump (deploy/policies/test/kyverno-values.yaml) shows here.
        log = re.sub(
            r"\x1b\[[0-9;]*m",
            "",
            admin("logs", "deploy/kyverno-admission-controller", "-c", "kyverno"),
        )
        running = re.findall(r"logger=setup/version\b.*\bversion=(\S+)", log)
        assert running == [f"v{_pin('kyverno')}"], f"the controller reports {running}"
        yield admin
    finally:
        if request.session.testsfailed:
            _dump(admin)
        _run([*helm, "uninstall", KYVERNO_RELEASE, "--wait", "--timeout", "5m"], check=False)
        label = "webhook.kyverno.io/managed-by=kyverno"
        admin(
            "delete",
            "validatingwebhookconfigurations,mutatingwebhookconfigurations",
            "-l",
            label,
            "--ignore-not-found",
            check=False,
        )
        admin(
            "delete",
            "crd",
            "-l",
            f"app.kubernetes.io/instance={KYVERNO_RELEASE}",
            "--ignore-not-found",
            check=False,
        )
        admin("delete", "namespace", KYVERNO_NAMESPACE, "--ignore-not-found", check=False)
        left = admin(
            "get",
            "validatingwebhookconfigurations,mutatingwebhookconfigurations",
            "-l",
            label,
            "-o",
            "name",
            check=False,
        )
        assert not left.strip(), f"Kyverno webhooks left behind: {left}"


@pytest.fixture(scope="module")
def policy(request: pytest.FixtureRequest, kube: Kube, kyverno: Kube, keys: Path) -> Iterator[str]:
    """The kind variant trusting the trusted key, ready and denying; its namespace created."""
    doc = yaml.safe_load(VARIANT.read_text())
    assert doc["metadata"]["name"] == POLICY
    (attestor,) = doc["spec"]["attestors"]
    assert attestor["cosign"]["key"]["data"] == "<public-key>"
    attestor["cosign"]["key"]["data"] = (keys / "trusted.pub").read_text()
    manifest = json.dumps(doc)
    ready = False
    try:
        kube("create", "namespace", NAMESPACE)
        # Kyverno validates the policy through its own webhook, which may not answer yet.
        end = time.time() + 120
        while kube("apply", "-f", "-", stdin=manifest, check=time.time() > end) == "":
            time.sleep(2)
        kube(
            "wait",
            f"imagevalidatingpolicy/{POLICY}",
            "--timeout=120s",
            "--for=jsonpath={.status.conditionStatus.ready}=true",
        )
        # Ready is the webhook's configuration; wait until the API server calls it.
        probe = _pod("probe", _image("fst/unsigned"))
        end = time.time() + 120
        while (done := _create(kube, probe, "--dry-run=server")).returncode == 0:
            assert time.time() < end, "the policy never denied the unsigned probe"
            time.sleep(1)
        _assert_denied(done, SIGNATURE_DENIED)
        ready = True
        yield POLICY
    finally:
        # Its status, while it still exists: a failed setup, or a failed test.
        if not ready or request.session.testsfailed:
            print(kyverno("get", "imagevalidatingpolicy", POLICY, "-o", "yaml", check=False))
        kyverno("delete", "-f", "-", "--ignore-not-found", stdin=manifest, check=False)
        kyverno("delete", "namespace", NAMESPACE, "--ignore-not-found", check=False)


# The images first (signed: the registry, pushed, signed), then Kyverno and the policy.
pytestmark = pytest.mark.usefixtures("signed", "policy")


def _running(kube: Kube, pod: str) -> dict[str, Any]:
    kube("wait", "--for=condition=Ready", f"pod/{pod}", "--timeout=120s")
    return kube.get("pod", pod)


def test_signed_and_attested_pod_is_admitted_pinned_to_its_digest_and_runs(
    kube: Kube, signed: dict[str, str]
) -> None:
    done = _create(kube, _pod("signed", _image("fst/signed")))
    assert done.returncode == 0, done.stderr
    pod = _running(kube, "signed")
    # mutateDigest: the tag the Pod named, resolved to the digest that was verified.
    assert pod["spec"]["containers"][0]["image"] == f"{_image('fst/signed')}@{signed['fst/signed']}"


def test_signed_and_attested_deployment_is_admitted_and_its_pod_runs(
    kube: Kube, signed: dict[str, str]
) -> None:
    done = _create(kube, _deployment("signed", _image("fst/signed")))
    assert done.returncode == 0, done.stderr
    kube("rollout", "status", "deployment/signed", "--timeout=120s")
    deployment = kube.get("deployment", "signed")
    image = deployment["spec"]["template"]["spec"]["containers"][0]["image"]
    assert image == f"{_image('fst/signed')}@{signed['fst/signed']}"  # autogen's mutateDigest


def test_unsigned_deployment_is_denied_by_the_autogen_rule(kube: Kube) -> None:
    _assert_denied(_create(kube, _deployment("unsigned", _image("fst/unsigned"))), SIGNATURE_DENIED)


def test_unsigned_pod_is_denied(kube: Kube) -> None:
    _assert_denied(_create(kube, _pod("unsigned", _image("fst/unsigned"))), SIGNATURE_DENIED)


def test_signed_pod_without_attestation_is_denied_by_the_attestation_rule(kube: Kube) -> None:
    done = _create(kube, _pod("no-attestation", _image("fst/no-attestation")))
    _assert_denied(done, ATTESTATION_DENIED)
    assert SIGNATURE_DENIED not in done.stderr  # the signature itself verified


def test_pod_signed_and_attested_by_another_key_is_denied(kube: Kube) -> None:
    _assert_denied(_create(kube, _pod("other-key", _image("fst/other-key"))), SIGNATURE_DENIED)


def test_unsigned_image_outside_the_policy_match_is_admitted_untouched(kube: Kube) -> None:
    done = _create(kube, _pod("outside", _image("outside/unsigned")))
    assert done.returncode == 0, done.stderr
    pod = _running(kube, "outside")
    assert pod["spec"]["containers"][0]["image"] == _image("outside/unsigned")  # no digest added
