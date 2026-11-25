"""Dev toolchain: `scripts/tools.sh` installs only what matches `mise.lock` and `tools/pins.tsv`.

Network-independent: pins point at local file:// artifacts (TOOLS_PROTO allows file://); the
mise tools are covered offline by tests/scripts/tools.bats and test_mise_config.py.
"""

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

from tests.supply_chain._pins import listing, pin

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts/tools.sh"
PINS = ROOT / "tools/pins.tsv"
SRC = SCRIPT.read_text()
BASH: str = shutil.which("bash") or "/usr/bin/bash"
PLATFORM = "linux-amd64"
REQUIRED = {
    "mise",
    "python",
    "schemathesis",
    "pre-commit",
    "yamale",
    "yamllint",
    "pip-audit",
}
LEFT = REQUIRED | {"k8s-openapi", "kyverno-chart"}  # what stays in pins.tsv


def rows() -> list[list[str]]:
    lines = [ln for ln in PINS.read_text().splitlines() if ln and not ln.startswith("#")]
    return [ln.split("\t") for ln in lines]


def sandbox(
    tmp: Path, sha: str, payload: bytes = b"#!/bin/sh\necho fake\n"
) -> tuple[dict[str, str], Path]:
    art = tmp / "fixture-linux-amd64"
    art.write_bytes(payload)
    pins = tmp / "pins.tsv"
    pins.write_text(f"fixture\t9.9.9\t{PLATFORM}\tfile://{art}\t{sha}\n")
    tools = tmp / ".tools"
    env = {**os.environ, "PINS": str(pins), "TOOLS_DIR": str(tools), "TOOLS_PROTO": "=file"}
    return env, tools


def run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, str(SCRIPT), *args], capture_output=True, text=True, env=env, check=False
    )


def tar_sandbox(tmp: Path, sha: str | None) -> tuple[dict[str, str], Path, Path]:
    """A fixture pin whose artifact is a tarball; TMPDIR is private so leftovers are visible."""
    art = tmp / "fixture-linux-amd64.tar.gz"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"#!/bin/sh\necho fake\n"
        info = tarfile.TarInfo("fixture")
        info.size = len(data)
        info.mode = 0o755
        tf.addfile(info, io.BytesIO(data))
    art.write_bytes(buf.getvalue())
    real = hashlib.sha256(buf.getvalue()).hexdigest()
    env, tools = sandbox(tmp, sha or real)
    (tmp / "pins.tsv").write_text(f"fixture\t9.9.9\t{PLATFORM}\tfile://{art}\t{sha or real}\n")
    scratch = tmp / "scratch"
    scratch.mkdir()
    env["TMPDIR"] = str(scratch)
    return env, tools, scratch


def test_checksum_mismatch_is_refused_and_installs_nothing(tmp_path: Path) -> None:
    env, tools = sandbox(tmp_path, "0" * 64)
    r = run(["install", "fixture"], env)
    assert r.returncode != 0
    assert "checksum mismatch" in r.stderr
    assert not (tools / "bin").exists()
    assert not (tools / "opt").exists()


def test_tar_checksum_mismatch_is_refused_and_leaves_nothing_behind(tmp_path: Path) -> None:
    env, tools, scratch = tar_sandbox(tmp_path, "0" * 64)
    r = run(["install", "fixture"], env)
    assert r.returncode != 0
    assert "checksum mismatch" in r.stderr
    assert not (tools / "bin").exists()
    assert not (tools / "opt").exists()
    assert list(scratch.iterdir()) == [], "temp download must be cleaned up on failure"


def test_tar_installs_and_stale_part_dir_is_not_promoted(tmp_path: Path) -> None:
    env, tools, _ = tar_sandbox(tmp_path, None)
    stale = tools / "opt/fixture-9.9.9.part"
    stale.mkdir(parents=True)
    (stale / "evil").write_text("stale")
    r = run(["install", "fixture"], env)
    assert r.returncode == 0, r.stderr
    assert (tools / "bin/fixture").is_symlink()
    assert not (tools / "opt/fixture-9.9.9/evil").exists()


def test_tar_with_escaping_member_is_refused(tmp_path: Path) -> None:
    art = tmp_path / "fixture-linux-amd64.tar.gz"
    with tarfile.open(art, "w:gz") as tf:
        info = tarfile.TarInfo("../escape")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))
    pins = tmp_path / "pins.tsv"
    pins.write_text(
        f"fixture\t9.9.9\t{PLATFORM}\tfile://{art}\t{hashlib.sha256(art.read_bytes()).hexdigest()}\n"
    )
    tools = tmp_path / ".tools"
    env = {**os.environ, "PINS": str(pins), "TOOLS_DIR": str(tools), "TOOLS_PROTO": "=file"}
    r = run(["install", "fixture"], env)
    assert r.returncode != 0
    assert "unsafe archive" in r.stderr
    assert not (tools / "bin").exists()
    assert not (tmp_path / "escape").exists()


def test_matching_checksum_installs_and_second_run_skips(tmp_path: Path) -> None:
    payload = b"#!/bin/sh\necho fake\n"
    env, tools = sandbox(tmp_path, hashlib.sha256(payload).hexdigest(), payload)
    r = run(["install", "fixture"], env)
    assert r.returncode == 0, r.stderr
    assert (tools / "bin/fixture").resolve().read_bytes() == payload
    env["TOOLS_PROTO"] = "=https"  # a second download would now fail on the file:// URL
    r2 = run(["install", "fixture"], env)
    assert r2.returncode == 0, r2.stderr
    assert "already installed" in r2.stdout


def test_uv_row_without_matching_hashed_requirements_is_refused(tmp_path: Path) -> None:
    reqs = tmp_path / "reqs"
    reqs.mkdir()
    pins = tmp_path / "pins.tsv"
    pins.write_text("yamllint\t1.0.0\tany\tuv:yamllint\t-\n")
    tools = tmp_path / ".tools"
    (tools / "bin").mkdir(
        parents=True
    )  # the pinned uv is there already: the requirements are checked
    (tools / "bin/uv").symlink_to(shutil.which("uv") or "/usr/bin/uv")
    env = {**os.environ, "PINS": str(pins), "TOOLS_DIR": str(tools), "REQS": str(reqs)}
    r = run(["install", "yamllint"], env)  # no requirements file
    assert r.returncode != 0
    assert "requirements" in r.stderr
    (reqs / "yamllint.txt").write_text("yamllint==2.0.0 \\\n    --hash=sha256:" + "0" * 64 + "\n")
    r = run(["install", "yamllint"], env)  # wrong version pinned
    assert r.returncode != 0
    assert "does not pin yamllint==1.0.0" in r.stderr
    (reqs / "yamllint.txt").write_text("yamllint==1.0.0\n")
    r = run(["install", "yamllint"], env)  # no hashes
    assert r.returncode != 0
    assert "no hashes" in r.stderr
    assert not (tools / "uv-tools").exists()


def test_dry_run_installs_nothing_and_never_calls_curl(tmp_path: Path) -> None:
    env, tools = sandbox(tmp_path, "0" * 64)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    calls = tmp_path / "calls.log"
    for tool in ("curl", "uv", "tar", "wget"):
        stub = stubs / tool
        stub.write_text(f"#!/bin/sh\necho {tool} >> {calls}\nexit 1\n")
        stub.chmod(0o755)
    env["PATH"] = f"{stubs}{os.pathsep}{env['PATH']}"
    r = run(["--dry-run", "install", "fixture"], env)
    assert r.returncode == 0, r.stderr
    assert "nothing was installed" in r.stdout
    assert not tools.exists()
    assert not calls.exists(), calls.read_text() if calls.exists() else ""


def test_dry_run_env_does_not_enable_dry_run(tmp_path: Path) -> None:
    env, tools = sandbox(tmp_path, "0" * 64)
    env["DRY_RUN"] = "true"
    assert run(["install", "fixture"], env).returncode != 0  # real run: mismatch refused
    assert not tools.exists()


def test_malformed_pins_are_refused(tmp_path: Path) -> None:
    for bad in (
        "fixture\t1\tlinux-amd64\turl\n",
        "fixture\t\tlinux-amd64\turl\t" + "0" * 64 + "\n",
    ):
        pins = tmp_path / "pins.tsv"
        pins.write_text(bad)
        env = {**os.environ, "PINS": str(pins), "TOOLS_DIR": str(tmp_path / ".tools")}
        r = run(["install"], env)
        assert r.returncode != 0
        assert "malformed pin" in r.stderr


def test_last_row_without_trailing_newline_is_read(tmp_path: Path) -> None:
    payload = b"x"
    env, tools = sandbox(tmp_path, hashlib.sha256(payload).hexdigest(), payload)
    pins = Path(env["PINS"])
    pins.write_text(pins.read_text().rstrip("\n"))
    assert run(["install", "fixture"], env).returncode == 0
    assert (tools / "bin/fixture").is_symlink()


PIPE_TO_SHELL = [
    r"(curl|wget)[^\n]*\|\s*(sudo\s+)?(ba|z|da)?sh\b",
    r"\b(ba|z|da)?sh\s+(-\w+\s+)*[\"']?<\(\s*(curl|wget)",
    r"\b(ba|z|da)?sh\s+-c\s+[\"']?\$\(\s*(curl|wget)",
    r"\b(source|eval|\.)\s+[\"']?(<|\$)\(\s*(curl|wget)",
]


def pipes_into_shell(src: str) -> bool:
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    code = re.sub(r"\\\n\s*", " ", code)
    code = re.sub(r"\|\s*\n\s*", "| ", code)
    return any(re.search(p, code) for p in PIPE_TO_SHELL)


def test_pipe_to_shell_detector_catches_the_known_forms() -> None:
    for bad in (
        "curl -fsSL https://x | bash",
        "curl -fsSL https://x |\n  sh",
        "curl https://x \\\n  | sudo bash",
        "bash <(curl -fsSL https://x)",
        'sh -c "$(curl -fsSL https://x)"',
        "source <(curl https://x)",
    ):
        assert pipes_into_shell(bad), bad
    assert not pipes_into_shell("curl -o f https://x\nsha256sum -c f")


def test_never_pipes_into_a_shell() -> None:
    assert not pipes_into_shell(SRC)
    code = "\n".join(ln for ln in SRC.splitlines() if not ln.lstrip().startswith("#"))
    assert "sha256sum" in SRC
    assert "helm plugin install" not in code
    assert "set -Eeuo pipefail" in SRC
    assert "--proto-redir =https" in SRC


def test_pins_hold_only_the_rows_mise_does_not_manage_each_with_a_checksum() -> None:
    by_name = {r[0]: r for r in rows()}
    assert set(by_name) == LEFT
    for name, _ver, plat, url, sha in rows():
        assert plat in (PLATFORM, "any") or (name, plat) == ("mise", "darwin-arm64"), name
        if url.startswith("uv:"):
            assert sha == "-", name
            if url != "uv:python":
                req = (ROOT / f"tools/requirements/{name}.txt").read_text()
                assert re.search(rf"^{re.escape(name)}=={re.escape(_ver)}\b", req, re.MULTILINE), (
                    name
                )
                assert "--hash=sha256:" in req, name
        else:
            assert url.startswith("https://"), name
            assert re.fullmatch(r"[0-9a-f]{64}", sha), name


def test_node_major_matches_frontend_engines_and_helm_default_is_recorded() -> None:
    engines = json.loads((ROOT / "frontend/package.json").read_text())["engines"]["node"]
    major = re.match(r">=\s*(\d+)", engines)
    assert major, engines
    assert pin("node").split(".")[0] == major.group(1)
    assert re.search(r"^#default-helm: [34]$", PINS.read_text(), re.MULTILINE)


def test_the_listing_covers_every_required_tool_with_its_version() -> None:
    listed = listing()
    assert set(listed) >= {
        "helm3", "helm4", "kind", "kubeconform", "promtool", "ct", "helm-unittest", "actionlint",
        "hadolint", "gitleaks", "squawk", "oasdiff", "cosign", "kyverno", "yq", "bats", "node",
        "kubectl", "kubectl-floor", "uv", "lychee", "shellcheck", "zizmor",
    } | REQUIRED  # fmt: skip


def link_tar(tmp: Path, members: list[tarfile.TarInfo]) -> tuple[dict[str, str], Path]:
    art = tmp / "fixture-linux-amd64.tar.gz"
    with tarfile.open(art, "w:gz") as tf:
        for info in members:
            tf.addfile(info, io.BytesIO(b"x") if info.isfile() else None)
    pins = tmp / "pins.tsv"
    sha = hashlib.sha256(art.read_bytes()).hexdigest()
    pins.write_text(f"fixture\t9.9.9\t{PLATFORM}\tfile://{art}\t{sha}\n")
    tools = tmp / ".tools"
    env = {**os.environ, "PINS": str(pins), "TOOLS_DIR": str(tools), "TOOLS_PROTO": "=file"}
    return env, tools


def link(name: str, target: str, kind: bytes = tarfile.SYMTYPE) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = target
    return info


def regular(name: str) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = 1
    info.mode = 0o755
    return info


def assert_refused(tmp: Path, members: list[tarfile.TarInfo], needle: str) -> None:
    env, tools = link_tar(tmp, members)
    r = run(["install", "fixture"], env)
    assert r.returncode != 0
    assert needle in r.stderr, r.stderr
    assert not (tools / "bin").exists()
    assert not list((tools / "opt").glob("*")), "nothing (not even a .part) may remain in opt"


def test_outside_symlink_is_refused(tmp_path: Path) -> None:
    for i, target in enumerate(("/etc", "../..")):
        sub = tmp_path / str(i)
        sub.mkdir()
        assert_refused(sub, [link("x", target)], "unsafe archive")


def test_symlink_to_outside_parent_then_nested_file_is_refused(tmp_path: Path) -> None:
    assert_refused(tmp_path, [link("x", "../.."), regular("x/f")], "unsafe archive")
    assert not (tmp_path.parent / "f").exists()


def test_outside_hardlink_is_refused(tmp_path: Path) -> None:
    assert_refused(tmp_path, [link("h", "/etc/passwd", tarfile.LNKTYPE)], "unsafe archive")


def test_in_tree_relative_links_are_allowed(tmp_path: Path) -> None:
    members = [regular("fixture"), link("bin-link", "fixture"), link("sub/up", "../fixture")]
    env, tools = link_tar(tmp_path, members)
    r = run(["install", "fixture"], env)
    assert r.returncode == 0, r.stderr
    assert (tools / "opt/fixture-9.9.9/sub/up").is_symlink()


def test_names_containing_arrows_cannot_smuggle_a_link(tmp_path: Path) -> None:
    (tmp_path / "etc").mkdir()
    members = [link("a -> b", "../.."), regular("a -> b/f")]
    assert_refused(tmp_path, members, "unsafe archive")
    assert not (tmp_path.parent / "f").exists()


def test_hardlink_with_link_to_in_its_name_is_refused(tmp_path: Path) -> None:
    sub = tmp_path / "rel"
    sub.mkdir()
    assert_refused(sub, [link("x link to y", "../etc/passwd", tarfile.LNKTYPE)], "unsafe archive")
    sub2 = tmp_path / "abs"
    sub2.mkdir()
    assert_refused(sub2, [link("h", "/etc/passwd", tarfile.LNKTYPE)], "unsafe archive")


def test_device_and_fifo_members_are_refused(tmp_path: Path) -> None:
    for i, kind in enumerate((tarfile.CHRTYPE, tarfile.FIFOTYPE)):
        sub = tmp_path / str(i)
        sub.mkdir()
        info = tarfile.TarInfo("dev")
        info.type = kind
        assert_refused(sub, [info], "unsafe archive")


def test_setuid_bit_is_stripped(tmp_path: Path) -> None:
    info = regular("fixture")
    info.mode = 0o4755
    env, tools = link_tar(tmp_path, [info])
    r = run(["install", "fixture"], env)
    assert r.returncode == 0, r.stderr
    mode = (tools / "opt/fixture-9.9.9/fixture").stat().st_mode
    assert mode & 0o7000 == 0


def test_the_node_mise_installed_is_the_pinned_one_and_its_npm_runs() -> None:
    """`scripts/tools.sh install node` links the pinned Node, and the npm bundled with it."""
    node = ROOT / ".tools/bin/node"
    assert node.is_file(), f"{node} is missing: run `scripts/tools.sh install node`"
    out = subprocess.run([str(node), "--version"], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == f"v{pin('node')}"
    env = {**os.environ, "PATH": f"{node.parent}{os.pathsep}{os.environ['PATH']}"}
    npm = subprocess.run(
        [str(node.parent / "npm"), "--version"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert npm.returncode == 0, npm.stderr


def test_the_ci_unit_job_installs_what_the_tool_tests_read_before_the_tests() -> None:
    """The tests above run the installed Node and read the helm-unittest plugin, so the unit job
    that runs them (`make test-unit`) installs both first."""
    backend = ROOT / ".github" / "workflows" / "reusable-backend.yml"
    steps = yaml.safe_load(backend.read_text(encoding="utf-8"))["jobs"]["unit"]["steps"]
    runs = [str(s.get("run", "")) for s in steps]
    install = next(i for i, r in enumerate(runs) if "scripts/tools.sh install" in r)
    named = runs[install].split("scripts/tools.sh install", 1)[1].split()
    assert {"node", "helm-unittest"} <= set(named)
    assert install < next(i for i, r in enumerate(runs) if "make test-unit" in r)


def test_zizmor_is_a_mise_tool_and_python_stays_a_uv_row() -> None:
    assert listing()["zizmor"][1] == "aqua:zizmorcore/zizmor"
    assert "zizmor" not in {r[0] for r in rows()}
    assert ("python", "uv:python") in {(r[0], r[3]) for r in rows()}
    assert not (ROOT / "tools/requirements/zizmor.txt").exists()


def test_uv_is_installed_before_a_uv_tool_and_a_dry_run_says_so(tmp_path: Path) -> None:
    env = {**os.environ, "TOOLS_DIR": str(tmp_path / ".tools")}
    r = run(["--dry-run", "install", "yamllint"], env)
    assert r.returncode == 0, r.stderr
    assert listing()["uv"][1] == "aqua:astral-sh/uv"
    assert r.stdout.index("==> uv ") < r.stdout.index("==> yamllint ")
    assert not (tmp_path / ".tools").exists()


def test_a_dry_run_installs_zizmor_through_mise(tmp_path: Path) -> None:
    env = {**os.environ, "TOOLS_DIR": str(tmp_path / ".tools")}
    r = run(["--dry-run", "install", "zizmor"], env)
    assert r.returncode == 0, r.stderr
    assert "mise install --locked aqua:zizmorcore/zizmor\n" in r.stdout
    assert "uv, --require-hashes" not in r.stdout


def test_the_pinned_uv_is_the_one_used_and_keeps_its_cache_and_pythons_in_the_repository(
    tmp_path: Path,
) -> None:
    """A stub uv in .tools/bin records what it is called with; a different uv first on PATH is
    never called, and uv's cache and its Python directory are inside .tools. The Makefile's uv
    settings for the project venv (a Python request, managed-only, downloads off) do not reach
    it: it installs the pinned Python itself, and `uv python install` needs downloads allowed."""
    tools = tmp_path / ".tools"
    (tools / "bin").mkdir(parents=True)
    log = tmp_path / "uv.log"
    stub = (
        '#!/bin/sh\necho "$0 $UV_CACHE_DIR $UV_PYTHON_INSTALL_DIR [$UV_PYTHON] [$UV_PYTHON_PREFERENCE]'
        f' $UV_PYTHON_DOWNLOADS" >> {log}\necho /fake/python\n'
    )
    (tools / "bin/uv").write_text(stub)
    (tools / "bin/uv").chmod(0o755)
    other = tmp_path / "other"
    other.mkdir()
    (other / "uv").write_text(f"#!/bin/sh\necho WRONG >> {log}\n")
    (other / "uv").chmod(0o755)
    reqs = tmp_path / "reqs"
    reqs.mkdir()
    (reqs / "yamllint.txt").write_text("yamllint==1.0.0 \\\n    --hash=sha256:" + "0" * 64 + "\n")
    pins = tmp_path / "pins.tsv"
    pins.write_text("yamllint\t1.0.0\tany\tuv:yamllint\t-\n")
    env = {
        **os.environ,
        "PINS": str(pins),
        "TOOLS_DIR": str(tools),
        "REQS": str(reqs),
        "UV_PYTHON": "3.13",
        "UV_PYTHON_PREFERENCE": "only-managed",
        "UV_PYTHON_DOWNLOADS": "never",
        "PATH": f"{other}{os.pathsep}{os.environ['PATH']}",
    }
    r = run(["install", "yamllint"], env)
    assert r.returncode == 0, r.stderr
    calls = log.read_text().splitlines()
    assert calls
    assert "WRONG" not in calls
    for call in calls:
        assert call == f"{tools}/bin/uv {tools}/uv/cache {tools}/uv/python [] [] manual", call


def test_a_dry_run_names_exactly_the_requested_mise_tools(tmp_path: Path) -> None:
    pins = tmp_path / "pins.tsv"
    pins.write_text("")
    env = {**os.environ, "PINS": str(pins), "TOOLS_DIR": str(tmp_path / ".tools")}
    r = run(["--dry-run", "install", "helm4", "kubeconform"], env)
    assert r.returncode == 0, r.stderr
    assert "mise install --locked helm4 aqua:yannh/kubeconform\n" in r.stdout
    assert not (tmp_path / ".tools").exists()


# --- mise: the first pin, installed through the same verified path as every other tool ---


def mise_sandbox(tmp: Path, sha: str | None) -> tuple[dict[str, str], Path]:
    """A mise pin whose artifact is a bare executable, as the release ships it."""
    data = b"#!/bin/sh\necho fake mise\n"
    art = tmp / "mise-v9.9.9-linux-x64"
    art.write_bytes(data)
    sha = sha or hashlib.sha256(data).hexdigest()
    pins = tmp / "pins.tsv"
    pins.write_text(f"mise\t9.9.9\t{PLATFORM}\tfile://{art}\t{sha}\n")
    tools = tmp / ".tools"
    env = {**os.environ, "PINS": str(pins), "TOOLS_DIR": str(tools), "TOOLS_PROTO": "=file"}
    return env, tools


def test_mise_is_the_first_row_with_a_linux_and_a_macos_asset() -> None:
    mise = [r for r in rows() if r[0] == "mise"]
    assert rows()[0][0] == "mise", "mise installs first"
    assert {r[2] for r in mise} == {"linux-amd64", "darwin-arm64"}
    assert len({r[1] for r in mise}) == 1
    for r in mise:
        assert r[3].startswith(f"https://github.com/jdx/mise/releases/download/v{r[1]}/"), r
        assert re.search(r"/mise-v[0-9.]+-(linux|macos)-(x64|arm64)$", r[3]), "the bare executable"
        assert re.fullmatch(r"[0-9a-f]{64}", r[4]), r


def test_a_changed_mise_checksum_is_refused_and_links_nothing(tmp_path: Path) -> None:
    env, tools = mise_sandbox(tmp_path, "0" * 64)
    r = run(["install", "mise"], env)
    assert r.returncode != 0
    assert "checksum mismatch for mise" in r.stderr
    assert not (tools / "bin").exists()
    assert not (tools / "opt").exists()


def test_mise_installs_from_a_verified_bare_executable_to_bin(tmp_path: Path) -> None:
    env, tools = mise_sandbox(tmp_path, None)
    r = run(["install", "mise"], env)
    assert r.returncode == 0, r.stderr
    assert (tools / "bin/mise").is_symlink()
    out = subprocess.run([str(tools / "bin/mise")], capture_output=True, text=True, check=True)
    assert out.stdout == "fake mise\n"


def test_the_installed_helm_unittest_plugin_has_the_binary_its_plugin_yaml_runs() -> None:
    plugin = ROOT / ".tools/helm-plugins/helm-unittest"
    if not plugin.is_dir():
        msg = "helm-unittest is not installed (run: scripts/tools.sh install helm-unittest)"
        if os.environ.get("CI"):
            pytest.fail(msg)  # CI installs it first (the unit job, checked above): no silent skip
        pytest.skip(msg)
    commands = yaml.safe_load((plugin / "plugin.yaml").read_text())["platformCommand"]
    linux = next(
        c["command"] for c in commands if (c.get("os"), c.get("arch")) == ("linux", "amd64")
    )
    assert (plugin / linux.removeprefix("$HELM_PLUGIN_DIR/")).is_file()
