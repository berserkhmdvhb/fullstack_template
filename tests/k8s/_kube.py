# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The helpers the kind tests share: fst's kubeconfig only, its one context, and a kubectl pinned
for the server (tools/kind-nodes.tsv). Not a test module (pytest collects test_*.py only)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, NamedTuple

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
KUBECONFIG = ROOT / ".local" / "kind" / "kubeconfig"
CONTEXT = "kind-fst"
# The release and namespace scripts/kind-deploy.sh installs.
RELEASE = "your-service-name"
NAMESPACE = f"{RELEASE}-local"
GROUP = "system:authenticated"
# The throwaway TLS registry the kind tests push to: fst-registry on the kind network, published on
# 127.0.0.1 only, trusted by the kind node's containerd through a throwaway CA (registry()).
REGISTRY_CONTAINER = "fst-registry"
REGISTRY = f"{REGISTRY_CONTAINER}:5000"  # the reference the node (and Kyverno) resolve
NODE = "fst-control-plane"
NODE_CERTS = f"/etc/containerd/certs.d/{REGISTRY}"
# renovate: image=docker.io/library/registry  # noqa: ERA001 - an annotation, not code
REGISTRY_TAG = "3.1.2@sha256:82f6ff4a7f982960c8aa51617fea3aabc485df6e55cce80d22deb1de36f1bc3c"
REGISTRY_IMAGE = f"docker.io/library/registry:{REGISTRY_TAG}"
MANIFEST_TYPES = ", ".join(
    f"application/vnd.{t}"
    for t in (
        "oci.image.manifest.v1+json",
        "docker.distribution.manifest.v2+json",
        "oci.image.index.v1+json",
        "docker.distribution.manifest.list.v2+json",
    )
)


def _run_both(cmd: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """``cmd`` with its stdout and stderr, never failing on its status (``_run`` checks it)."""
    # KUBECONFIG is always fst's file, and helm's own context variables are dropped.
    env = {k: v for k, v in os.environ.items() if not k.startswith("HELM_KUBE")}
    env["KUBECONFIG"] = str(KUBECONFIG)
    return subprocess.run(
        cmd, input=stdin, capture_output=True, text=True, encoding="utf-8", env=env, check=False
    )


def _run(cmd: list[str], stdin: str | None = None, *, check: bool = True) -> str:
    done = _run_both(cmd, stdin)
    if check and done.returncode:
        pytest.fail(f"{' '.join(cmd[:6])} ... failed ({done.returncode}): {done.stderr.strip()}")
    return done.stdout


def _tool(name: str) -> str:
    path = shutil.which(name)
    assert path, f"no {name} on PATH (scripts/tools.sh install {name}; make puts .tools/bin first)"
    return path


def deployer(env: str = "local") -> str:
    """The bootstrap's deployer (infra/k8s/bootstrap, the local env by default), as kind-deploy.sh
    reads it."""
    boot = ROOT / "infra" / "k8s" / "bootstrap"
    name = ""
    for f in ("values.yaml", f"values.{env}.yaml"):
        name = (yaml.safe_load((boot / f).read_text()) or {}).get("deployer", {}).get("name", name)
    assert name, "no deployer name in infra/k8s/bootstrap"
    return name


def helm_as_deployer() -> list[str]:
    """helm on fst as the deployer, as kind-deploy.sh's helm_as."""
    helm = [_tool("helm"), "--kubeconfig", str(KUBECONFIG), "--kube-context", CONTEXT]
    return [*helm, "--kube-as-user", deployer(), "--kube-as-group", GROUP]


@contextmanager
def upgraded(*flags: str) -> Iterator[None]:
    """The main release upgraded as the deployer (``--reuse-values`` plus these flags) for the
    block, then rolled back to the revision it had, on every exit path, so later steps see it
    unchanged."""
    helm = helm_as_deployer()
    history = json.loads(_run([*helm, "history", RELEASE, "-n", NAMESPACE, "-o", "json"]))
    revision = str(next(h for h in reversed(history) if h["status"] == "deployed")["revision"])
    chart = str(ROOT / "charts" / "your-service-name")
    wait = ["--wait", "--wait-for-jobs", "--timeout", "9m"]
    try:
        _run(
            [
                *helm,
                "upgrade",
                RELEASE,
                chart,
                "-n",
                NAMESPACE,
                "--reuse-values",
                "--server-side=true",
                "--force-conflicts",
                *flags,
                *wait,
            ]
        )
        yield
    finally:
        _run(
            [
                *helm,
                "rollback",
                RELEASE,
                revision,
                "-n",
                NAMESPACE,
                "--server-side=true",
                "--force-conflicts",
                *wait,
            ]
        )


class Kube:
    """kubectl on one namespace of fst (the release's by default), pinned for the server."""

    def __init__(self, namespace: str = NAMESPACE) -> None:
        any_kubectl = _tool("kubectl")
        config = [any_kubectl, "--kubeconfig", str(KUBECONFIG), "config"]
        current = _run([*config, "current-context"]).strip()
        assert current == CONTEXT, f"refusing: the current context is {current!r}, not {CONTEXT}"
        contexts = _run([*config, "get-contexts", "-o", "name"]).split()
        assert contexts == [CONTEXT], f"refusing: {KUBECONFIG} holds other contexts: {contexts}"
        server = json.loads(_run([any_kubectl, "--context", CONTEXT, "version", "-o", "json"]))
        minor = "".join(c for c in server["serverVersion"]["minor"] if c.isdigit())
        rows = (ROOT / "tools" / "kind-nodes.tsv").read_text().splitlines()
        pins = [r.split("\t")[1] for r in rows if not r.startswith("#") and f":v1.{minor}." in r]
        assert pins, f"no kubectl pin for a 1.{minor} server in tools/kind-nodes.tsv"
        self.base = [_tool(pins[0]), "--kubeconfig", str(KUBECONFIG), "--context", CONTEXT]
        self.base += ["-n", namespace]

    def __call__(self, *args: str, stdin: str | None = None, check: bool = True) -> str:
        return _run([*self.base, *args], stdin, check=check)

    def get(self, *args: str) -> dict[str, Any]:
        out = self("get", *args, "--ignore-not-found", "-o", "json")
        return json.loads(out) if out else {}


# kubectl exec streams over WebSockets since 1.31, and the API server can process a close before the
# stdin it already received (kubernetes/kubernetes#112834): the process in the container then reads
# fewer bytes than were sent (an empty policy, an empty value). A container-side check exits
# STDIN_LOST when its digest of the input differs from the one passed as an argument, before the
# input is used; exec_stdin retries then.
STDIN_LOST = 75
STDIN_ATTEMPTS = 3
# The check for a container with sh and sha256sum: the digest is the first argument, and stdin is
# the verified input afterwards (a temp file keeps trailing newlines; unlinked once it is stdin).
SH_STDIN_CHECK = (
    'f=$(mktemp) || exit 1; cat >"$f" || { rm -f "$f"; exit 75; }; '
    '[ "$(sha256sum <"$f" | cut -d" " -f1)" = "$1" ] || { rm -f "$f"; exit 75; }; '
    'shift; exec <"$f"; rm -f "$f"; '
)


def exec_stdin(
    kube: Kube,
    site: str,
    payload: str,
    command: Callable[[str], list[str]],
    run: Callable[..., subprocess.CompletedProcess[str]] = _run_both,
) -> str:
    """``kube`` runs the kubectl arguments ``command(digest)`` with ``payload`` on stdin, where
    ``digest`` is the payload's SHA-256 (hex) for the container-side check to compare. Exit
    STDIN_LOST retries (3 attempts, a note naming ``site`` and never the data); any other
    failure fails as _run does."""
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    cmd = [*kube.base, *command(digest)]
    for attempt in range(1, STDIN_ATTEMPTS + 1):
        done = run(cmd, payload)
        if done.returncode == 0:
            return done.stdout
        if done.returncode != STDIN_LOST:
            pytest.fail(
                f"{' '.join(cmd[:6])} ... failed ({done.returncode}): {done.stderr.strip()}"
            )
        print(f"{site}: stdin lost in kubectl exec (attempt {attempt} of {STDIN_ATTEMPTS})")
    pytest.fail(
        f"{site}: stdin lost in kubectl exec {STDIN_ATTEMPTS} times (kubernetes/kubernetes#112834)"
    )


def docker(*args: str, stdin: str | None = None, check: bool = True) -> str:
    return _run([_tool("docker"), *args], stdin, check=check)


def registry_tls(out: Path) -> Path:
    """A throwaway CA (its key deleted once used) and the registry's certificate for
    fst-registry and 127.0.0.1, in ``out``."""
    out.mkdir()
    openssl = _tool("openssl")
    ec = ["-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes"]
    ca, cert, key = (str(out / n) for n in ("ca.crt", "tls.crt", "tls.key"))
    days = ["-days", "1"]
    # The extensions a strict verifier (Python's, Go's) requires of a CA and a server certificate.
    ca_ext = ["-addext", "basicConstraints=critical,CA:TRUE"]
    ca_ext += ["-addext", "keyUsage=critical,keyCertSign,cRLSign"]
    req = [openssl, "req", *ec, *days, "-x509", *ca_ext, "-keyout", f"{ca}.key", "-out", ca]
    _run([*req, "-subj", "/CN=fst-ca"])
    subj = ["-subj", f"/CN={REGISTRY_CONTAINER}"]
    _run([openssl, "req", *ec, "-keyout", key, "-out", f"{cert}.csr", *subj])
    ext = out / "ext.cnf"
    ext.write_text(
        f"subjectAltName=DNS:{REGISTRY_CONTAINER},IP:127.0.0.1\nbasicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature\nextendedKeyUsage=serverAuth\n"
        "authorityKeyIdentifier=keyid\nsubjectKeyIdentifier=hash\n"
    )
    x509 = [openssl, "x509", "-req", "-in", f"{cert}.csr", "-CA", ca, "-CAkey", f"{ca}.key", *days]
    _run([*x509, "-CAcreateserial", "-extfile", str(ext), "-out", cert])
    Path(f"{ca}.key").unlink()  # nothing else is signed with it
    (out / "tls.key").chmod(0o644)  # read by the registry container's user
    return out


@contextmanager
def registry(kube: Kube, tls: Path) -> Iterator[str]:
    """fst-registry on the kind network for the block, and its host address (127.0.0.1:<port>).
    The node's containerd trusts it (a hosts.toml with the CA). The container and the node's
    registry config are removed on every exit."""
    kube("get", "node", NODE)  # the node container below is fst's
    docker("rm", "-f", REGISTRY_CONTAINER, check=False)  # one a killed run left behind
    try:
        docker(
            *("run", "-d", "--name", REGISTRY_CONTAINER, "--network", "kind"),
            *("-p", "127.0.0.1::5000", "-v", f"{tls}:/certs:ro"),
            *("-e", "REGISTRY_HTTP_TLS_CERTIFICATE=/certs/tls.crt"),
            *("-e", "REGISTRY_HTTP_TLS_KEY=/certs/tls.key"),
            REGISTRY_IMAGE,
        )
        host = docker("port", REGISTRY_CONTAINER, "5000/tcp").split()[0]
        assert host.startswith("127.0.0.1:"), host
        # containerd on the node: the registry's https endpoint and the CA that signed it.
        hosts = (
            f'server = "https://{REGISTRY}"\n\n[host."https://{REGISTRY}"]\n'
            f'  capabilities = ["pull", "resolve"]\n  ca = "{NODE_CERTS}/ca.crt"\n'
        )
        docker("exec", NODE, "mkdir", "-p", NODE_CERTS)
        ca = (tls / "ca.crt").read_text()
        docker("exec", "-i", NODE, "cp", "/dev/stdin", f"{NODE_CERTS}/ca.crt", stdin=ca)
        docker("exec", "-i", NODE, "cp", "/dev/stdin", f"{NODE_CERTS}/hosts.toml", stdin=hosts)
        yield host
    finally:
        docker("exec", NODE, "rm", "-rf", NODE_CERTS, check=False)
        docker("exec", NODE, "rmdir", "/etc/containerd/certs.d", check=False)  # if now empty
        docker("rm", "-f", REGISTRY_CONTAINER, check=False)


def manifest_digest(host: str, repository: str, tag: str, tls: Path) -> str:
    """The digest the registry serves for repository:tag (read from the host, 127.0.0.1)."""
    context = ssl.create_default_context(cafile=str(tls / "ca.crt"))
    url = f"https://{host}/v2/{repository}/manifests/{tag}"
    request = urllib.request.Request(url, method="HEAD", headers={"Accept": MANIFEST_TYPES})
    with urllib.request.urlopen(request, context=context, timeout=30) as answer:
        return str(answer.headers["Docker-Content-Digest"])


# The OCI architecture of each `uname -m` a kind node reports (its images are the host's).
OCI_ARCH = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}


def oci_platform(machine: str) -> str:
    """The OCI platform (linux/<arch>) for a node's `uname -m`."""
    arch = OCI_ARCH.get(machine.strip())
    assert arch, f"no OCI architecture for the node's machine {machine.strip()!r}"
    return f"linux/{arch}"


def push_from_node(image: str, ref: str) -> None:
    """An image the node's containerd holds (one kind loaded) pushed to fst-registry as ``ref``
    (127.0.0.1:<port>/<repository>:<tag>), with a label of its own: the node then holds nothing
    else with its config, so a Pod that pulls it reports the registry's digest as its imageID
    (a shared config reports the first of its names, such as the loaded copy's). The local tags
    and the committed container's are removed; nothing leaves the loopback."""
    ctr = f"{_tool('docker')} exec {NODE} ctr -n k8s.io images"
    load = f"{_tool('docker')} load --quiet"
    platform = oci_platform(docker("exec", NODE, "uname", "-m"))
    _run(["bash", "-c", f"set -o pipefail; {ctr} export --platform {platform} - {image} | {load}"])
    container = f"fst-push-{ref.rsplit('/', 1)[-1].replace(':', '-')}"
    docker("rm", "-f", container, check=False)
    try:
        docker("create", "--name", container, image)
        docker("commit", "--change", f"LABEL fst.kind-test={ref}", container, ref)
        docker("push", "--quiet", ref)
    finally:
        docker("rm", "-f", container, check=False)
        docker("image", "rm", ref, image, check=False)  # the tags made here only


# ---- scripts/ci/deploy-env.sh itself on fst (its test-only DEPLOY_ENV=kind) --------------------

DEPLOY_ENV = ROOT / "scripts" / "ci" / "deploy-env.sh"
CHART = ROOT / "charts" / "your-service-name"
# The kind cluster's generated secrets (scripts/kind-deploy.sh) and the kind values the deploy's
# placeholder guard accepts; both go to the script's KIND_VALUES (a preview takes the Secrets the
# bootstrap creates instead, as values.preview.yaml names them, so only the kind values).
SECRETS = ROOT / ".local" / "kind" / "values.yaml"
KIND_VALUES = ROOT / "tests" / "k8s" / "deploy-env-kind.yaml"
# The images the deploy workflow deploys (deploy.yml's DEPLOY_IMAGES); the WAF renders only with
# waf.enabled, so it is not pushed and goes without a digest, as an older release's would.
DEPLOY_IMAGES = ("backend", "frontend", "waf")


class Pushed(NamedTuple):
    """The kind job's images in fst-registry: the matrix the script reads, and their digests."""

    tag: str
    matrix: dict[str, Any]
    digests: dict[str, str]  # <ID>_IMAGE_DIGEST -> sha256:...


def helm_timeout() -> str:
    """The deploy workflow's default HELM_TIMEOUT (`vars.HELM_TIMEOUT || '<default>'`)."""
    text = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()
    found: set[str] = set(re.findall(r"vars\.HELM_TIMEOUT \|\| '([^']+)'", text))
    assert len(found) == 1, f"deploy.yml has no single default HELM_TIMEOUT: {found}"
    return found.pop()


def push_release_images(host: str, tls: Path, prefix: str) -> Pushed:
    """The main release's images (the ones kind-deploy.sh built and loaded) pushed to fst-registry
    under <prefix>/<id>, so the script deploys them by registry digest, as release.yml's are."""
    helm = [_tool("helm"), "--kubeconfig", str(KUBECONFIG), "--kube-context", CONTEXT]
    values = json.loads(_run([*helm, "get", "values", RELEASE, "-n", NAMESPACE, "-o", "json"]))
    images = values.get("images", {})
    tag = "deploy-env"
    include, digests = [], {}
    for image_id in DEPLOY_IMAGES:
        repository = f"{prefix}/{image_id}"
        include.append({"id": image_id, "repository": f"{REGISTRY}/{repository}"})
        if image_id == "waf":
            continue
        image = images.get(image_id)
        assert image, f"the release {RELEASE} has no {image_id} image: run make kind-deploy first"
        node_image = f"docker.io/{image['repository']}:{image['tag']}"
        push_from_node(node_image, f"{host}/{repository}:{tag}")
        digests[f"{image_id.upper()}_IMAGE_DIGEST"] = manifest_digest(host, repository, tag, tls)
    return Pushed(tag, {"include": include}, digests)


def deployer_kubectl(bin_dir: Path, kubectl: str, user: str = "") -> Path:
    """A kubectl on fst as the deployer (the deploy path's kubectl; ``user``, else the local
    deployer), first on the PATH it gets."""
    bin_dir.mkdir(exist_ok=True)
    wrapper = bin_dir / "kubectl"
    wrapper.write_text(
        "#!/usr/bin/env bash\n"
        f'exec "{kubectl}" --kubeconfig "{KUBECONFIG}" --context {CONTEXT} '
        f'--as "{user or deployer()}" --as-group {GROUP} "$@"\n'
    )
    wrapper.chmod(0o755)
    return bin_dir


def deployer_env(work: Path, user: str = "") -> dict[str, str]:
    """The environment of a deploy-path script on fst: fst's kubeconfig, helm as the deployer
    (``user``, else the local deployer), and the deployer kubectl in ``work``/bin first on the PATH
    (deployer_kubectl writes it); no other helm context, no GitHub variable."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("HELM_KUBE", "GITHUB_")) and k != "RUNNER_TEMP"
    }
    return env | {
        "KUBECONFIG": str(KUBECONFIG),
        "PATH": f"{work / 'bin'}:{env['PATH']}",
        "HELM_KUBECONTEXT": CONTEXT,
        "HELM_KUBEASUSER": user or deployer(),
        "HELM_KUBEASGROUPS": GROUP,
    }


def deploy_env(  # noqa: PLR0913 - the script's inputs, each optional but the first three
    release: str,
    work: Path,
    pushed: Pushed,
    *values: Path,
    timeout: str = "",
    verify: Path | None = None,
    user: str = "",
    preview_pr: int = 0,
    render: Path | None = None,
) -> tuple[int, str]:
    """scripts/ci/deploy-env.sh as deploy.yml runs it, with DEPLOY_ENV=kind: helm and kubectl as
    the deployer on fst (``user``, else the local deployer), the pushed images by digest, the
    deploy workflow's default HELM_TIMEOUT, KIND_VALUES the secrets, the kind values and these
    files. ``verify`` replaces deploy-verify.sh (VERIFY_SCRIPT); ``preview_pr`` deploys that pull
    request's preview release (PREVIEW_PR), without the kind secrets: its values file names the
    bootstrap's Secrets. ``render`` writes the release's render there instead of deploying
    (DEPLOY_RENDER_ONLY, the live-drift check's). Returns the exit code and the script's output."""
    run = work / "run"
    run.mkdir(exist_ok=True)
    matrix = work / "matrix.sh"
    matrix.write_text(f"#!/usr/bin/env bash\necho '{json.dumps(pushed.matrix)}'\n")
    matrix.chmod(0o755)
    env = deployer_env(work, user)
    config = yaml.safe_load((CHART / "values.yaml").read_text())["config"]
    env |= {
        "DEPLOY_ENV": "kind",
        "DEPLOY_ENV_KIND_TEST": "1",
        "KIND_VALUES": ":".join(
            str(f) for f in ((() if preview_pr else (SECRETS,)) + (KIND_VALUES, *values))
        ),
        "SERVICE_NAME": release,
        "CHART_DIR": str(CHART),
        "DEPLOY_IMAGES": ",".join(DEPLOY_IMAGES),
        "DIGESTS": json.dumps(pushed.digests),
        "IMAGE_TAG": pushed.tag,
        "HELM_TIMEOUT": timeout or helm_timeout(),
        "BACKEND_PORT": str(config["BACKEND_PORT"]),
        "FRONTEND_PORT": str(config["FRONTEND_PORT"]),
        "API_VERSION": str(config["API_VERSION"]),
        "WORK_DIR": str(run),
        "MATRIX_CMD": str(matrix),
    }
    if verify:
        env["VERIFY_SCRIPT"] = str(verify)
    if preview_pr:
        env["PREVIEW_PR"] = str(preview_pr)
    if render:
        env["DEPLOY_RENDER_ONLY"] = str(render)
    cmd = ["bash", str(DEPLOY_ENV)]
    done = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", env=env, check=False
    )
    return done.returncode, done.stdout + done.stderr
