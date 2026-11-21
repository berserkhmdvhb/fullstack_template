"""Dev toolchain: `scripts/tools.sh` installs only what matches `tools/pins.tsv`.

Network-independent: pins point at local file:// artifacts (TOOLS_PROTO allows file://).
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

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts/tools.sh"
PINS = ROOT / "tools/pins.tsv"
SRC = SCRIPT.read_text()
BASH: str = shutil.which("bash") or "/usr/bin/bash"
PLATFORM = "linux-amd64"
REQUIRED = {
    "helm3", "helm4", "kind", "kubeconform", "ct", "helm-unittest", "actionlint", "hadolint",
    "gitleaks", "squawk", "oasdiff", "cosign", "kyverno", "yq", "bats", "node",
    "zizmor", "schemathesis", "pre-commit", "python", "kubectl", "uv",
}  # fmt: skip


def rows() -> list[list[str]]:
    lines = [ln for ln in PINS.read_text().splitlines() if ln and not ln.startswith("#")]
    return [ln.split("\t") for ln in lines]


def sandbox(
    tmp: Path, sha: str, payload: bytes = b"#!/bin/sh\necho fake\n"
) -> tuple[dict[str, str], Path]:
    art = tmp / "kind-linux-amd64"
    art.write_bytes(payload)
    pins = tmp / "pins.tsv"
    pins.write_text(f"kind\t9.9.9\t{PLATFORM}\tfile://{art}\t{sha}\n")
    tools = tmp / ".tools"
    env = {**os.environ, "PINS": str(pins), "TOOLS_DIR": str(tools), "TOOLS_PROTO": "=file"}
    return env, tools


def run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [BASH, str(SCRIPT), *args], capture_output=True, text=True, env=env, check=False
    )


def tar_sandbox(tmp: Path, sha: str | None) -> tuple[dict[str, str], Path, Path]:
    """A kind pin whose artifact is a tarball; TMPDIR is private so leftovers are visible."""
    art = tmp / "kind-linux-amd64.tar.gz"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"#!/bin/sh\necho fake\n"
        info = tarfile.TarInfo("kind")
        info.size = len(data)
        info.mode = 0o755
        tf.addfile(info, io.BytesIO(data))
    art.write_bytes(buf.getvalue())
    real = hashlib.sha256(buf.getvalue()).hexdigest()
    env, tools = sandbox(tmp, sha or real)
    (tmp / "pins.tsv").write_text(f"kind\t9.9.9\t{PLATFORM}\tfile://{art}\t{sha or real}\n")
    scratch = tmp / "scratch"
    scratch.mkdir()
    env["TMPDIR"] = str(scratch)
    return env, tools, scratch


def test_checksum_mismatch_is_refused_and_installs_nothing(tmp_path: Path) -> None:
    env, tools = sandbox(tmp_path, "0" * 64)
    r = run(["install", "kind"], env)
    assert r.returncode != 0
    assert "checksum mismatch" in r.stderr
    assert not (tools / "bin").exists()
    assert not (tools / "opt").exists()


def test_tar_checksum_mismatch_is_refused_and_leaves_nothing_behind(tmp_path: Path) -> None:
    env, tools, scratch = tar_sandbox(tmp_path, "0" * 64)
    r = run(["install", "kind"], env)
    assert r.returncode != 0
    assert "checksum mismatch" in r.stderr
    assert not (tools / "bin").exists()
    assert not (tools / "opt").exists()
    assert list(scratch.iterdir()) == [], "temp download must be cleaned up on failure"


def test_tar_installs_and_stale_part_dir_is_not_promoted(tmp_path: Path) -> None:
    env, tools, _ = tar_sandbox(tmp_path, None)
    stale = tools / "opt/kind-9.9.9.part"
    stale.mkdir(parents=True)
    (stale / "evil").write_text("stale")
    r = run(["install", "kind"], env)
    assert r.returncode == 0, r.stderr
    assert (tools / "bin/kind").is_symlink()
    assert not (tools / "opt/kind-9.9.9/evil").exists()


def test_tar_with_escaping_member_is_refused(tmp_path: Path) -> None:
    art = tmp_path / "kind-linux-amd64.tar.gz"
    with tarfile.open(art, "w:gz") as tf:
        info = tarfile.TarInfo("../escape")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))
    pins = tmp_path / "pins.tsv"
    pins.write_text(
        f"kind\t9.9.9\t{PLATFORM}\tfile://{art}\t{hashlib.sha256(art.read_bytes()).hexdigest()}\n"
    )
    tools = tmp_path / ".tools"
    env = {**os.environ, "PINS": str(pins), "TOOLS_DIR": str(tools), "TOOLS_PROTO": "=file"}
    r = run(["install", "kind"], env)
    assert r.returncode != 0
    assert "unsafe archive" in r.stderr
    assert not (tools / "bin").exists()
    assert not (tmp_path / "escape").exists()


def test_matching_checksum_installs_and_second_run_skips(tmp_path: Path) -> None:
    payload = b"#!/bin/sh\necho fake\n"
    env, tools = sandbox(tmp_path, hashlib.sha256(payload).hexdigest(), payload)
    r = run(["install", "kind"], env)
    assert r.returncode == 0, r.stderr
    assert (tools / "bin/kind").resolve().read_bytes() == payload
    env["TOOLS_PROTO"] = "=https"  # a second download would now fail on the file:// URL
    r2 = run(["install", "kind"], env)
    assert r2.returncode == 0, r2.stderr
    assert "already installed" in r2.stdout


def test_uv_row_without_matching_hashed_requirements_is_refused(tmp_path: Path) -> None:
    reqs = tmp_path / "reqs"
    reqs.mkdir()
    pins = tmp_path / "pins.tsv"
    pins.write_text("zizmor\t1.0.0\tany\tuv:zizmor\t-\n")
    tools = tmp_path / ".tools"
    env = {**os.environ, "PINS": str(pins), "TOOLS_DIR": str(tools), "REQS": str(reqs)}
    r = run(["install", "zizmor"], env)  # no requirements file
    assert r.returncode != 0
    assert "requirements" in r.stderr
    (reqs / "zizmor.txt").write_text("zizmor==2.0.0 \\\n    --hash=sha256:" + "0" * 64 + "\n")
    r = run(["install", "zizmor"], env)  # wrong version pinned
    assert r.returncode != 0
    assert "does not pin zizmor==1.0.0" in r.stderr
    (reqs / "zizmor.txt").write_text("zizmor==1.0.0\n")
    r = run(["install", "zizmor"], env)  # no hashes
    assert r.returncode != 0
    assert "no hashes" in r.stderr
    assert not tools.exists()


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
    r = run(["--dry-run", "install", "kind"], env)
    assert r.returncode == 0, r.stderr
    assert "nothing was installed" in r.stdout
    assert not tools.exists()
    assert not calls.exists(), calls.read_text() if calls.exists() else ""


def test_dry_run_env_does_not_enable_dry_run(tmp_path: Path) -> None:
    env, tools = sandbox(tmp_path, "0" * 64)
    env["DRY_RUN"] = "true"
    assert run(["install", "kind"], env).returncode != 0  # real run: mismatch refused
    assert not tools.exists()


def test_malformed_pins_are_refused(tmp_path: Path) -> None:
    for bad in ("kind\t1\tlinux-amd64\turl\n", "kind\t\tlinux-amd64\turl\t" + "0" * 64 + "\n"):
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
    assert run(["install"], env).returncode == 0
    assert (tools / "bin/kind").is_symlink()


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


def test_pins_cover_every_required_tool_with_a_checksum() -> None:
    by_name = {r[0]: r for r in rows()}
    assert set(by_name) >= REQUIRED
    for name, _ver, plat, url, sha in rows():
        assert plat in (PLATFORM, "any"), name
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
    node = next(r for r in rows() if r[0] == "node")
    assert node[1].split(".")[0] == major.group(1)
    assert re.search(r"^#default-helm: [34]$", PINS.read_text(), re.MULTILINE)


def link_tar(tmp: Path, members: list[tarfile.TarInfo]) -> tuple[dict[str, str], Path]:
    art = tmp / "kind-linux-amd64.tar.gz"
    with tarfile.open(art, "w:gz") as tf:
        for info in members:
            tf.addfile(info, io.BytesIO(b"x") if info.isfile() else None)
    pins = tmp / "pins.tsv"
    sha = hashlib.sha256(art.read_bytes()).hexdigest()
    pins.write_text(f"kind\t9.9.9\t{PLATFORM}\tfile://{art}\t{sha}\n")
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
    r = run(["install", "kind"], env)
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
    members = [regular("kind"), link("bin-link", "kind"), link("sub/up", "../kind")]
    env, tools = link_tar(tmp_path, members)
    r = run(["install", "kind"], env)
    assert r.returncode == 0, r.stderr
    assert (tools / "opt/kind-9.9.9/sub/up").is_symlink()


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
    info = regular("kind")
    info.mode = 0o4755
    env, tools = link_tar(tmp_path, [info])
    r = run(["install", "kind"], env)
    assert r.returncode == 0, r.stderr
    mode = (tools / "opt/kind-9.9.9/kind").stat().st_mode
    assert mode & 0o7000 == 0


def test_real_node_tarball_still_installs_and_npm_works(tmp_path: Path) -> None:
    tools = tmp_path / ".tools"
    env = {**os.environ, "TOOLS_DIR": str(tools)}
    r = run(["install", "node"], env)
    if r.returncode != 0 and "curl" in r.stderr:
        pytest.skip("node tarball not reachable")
    assert r.returncode == 0, r.stderr
    env["PATH"] = f"{tools / 'bin'}{os.pathsep}{os.environ['PATH']}"
    out = subprocess.run(
        [str(tools / "bin/npm"), "--version"], capture_output=True, text=True, check=False, env=env
    )
    assert out.returncode == 0, out.stderr


def test_uv_is_pinned_from_the_vendor_release_and_installed_before_every_uv_row() -> None:
    names = [r[0] for r in rows()]
    uv = next(r for r in rows() if r[0] == "uv")
    assert uv[2] == PLATFORM
    assert uv[3] == (
        f"https://github.com/astral-sh/uv/releases/download/{uv[1]}/"
        "uv-x86_64-unknown-linux-gnu.tar.gz"
    )
    first_uv_row = next(i for i, r in enumerate(rows()) if r[3].startswith("uv:"))
    assert names.index("uv") < first_uv_row, "uv must be installed before the rows that use it"


def test_tools_sh_prefers_the_pinned_uv() -> None:
    assert 'PATH="$TOOLS_DIR/bin:$PATH"' in SRC
