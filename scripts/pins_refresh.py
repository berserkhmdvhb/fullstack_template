#!/usr/bin/env python3
"""Refresh or check the pinned tool checksums against what each vendor publishes.

  scripts/pins-refresh.sh [name...]
      write the vendor checksum for every pin that differs
  scripts/pins-refresh.sh --check [--base REF] [name...]
      read-only: exit 1, naming each pin, on any difference

Three pin sources are covered:
  * tools/pins.tsv (dev toolchain, read by scripts/tools.sh): the recorded sha256 must equal the
    vendor's published one for that exact version and url; `uv:` rows must have a hashed lock in
    tools/requirements/ pinning the same version whose hashes PyPI publishes.
  * mise.toml and mise.lock (the rest of the dev toolchain, installed by scripts/tools.sh): the lock
    must pin every tool at the version mise.toml says, with a sha256 for each locked platform. The
    checksums come from mise itself (`mise lock`: the vendor's file, a signature or an attestation
    where the recipe has one), so a refresh runs `mise lock` for both platforms when the lock lags.
  * infra/security/config.yaml (security scanners, read by the frozen tools-install.sh): that script
    reads the vendor checksum file at install time, so nothing is recorded; the check is that the
    vendor still publishes a checksum for the pinned version's asset.
  * charts/your-service-name/ci/crds/sources.tsv (the upstream CRDs of the kubeconform schemas):
    the recorded sha256 must be that of the file at the row's version (hashed on download).
  * Dockerfile.frontend's pnpm (PNPM_VERSION, PNPM_SHA256_AMD64, PNPM_SHA256_ARM64): mise.toml's
    version, mise.lock's linux-x64 sha256 and the release's linux-arm64 asset digest (mise.lock
    holds no linux-arm64; pnpm publishes no checksum file, so the digest is GitHub's). A refresh
    writes them.

SOURCES is the one place that says where each tool's checksum comes from. With --base REF only rows
that differ from REF are checked (a pull request); without it every row is (main, schedule).
Env: PINS, SECURITY_CONFIG, REQS, CRD_SOURCES, MISE_TOML, MISE_LOCK, PNPM_DOCKERFILE (paths, used by
the tests; the mise and pnpm checks are skipped when their file is missing), GITHUB_TOKEN (read-only,
API only).
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import astuple, dataclass, replace
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path
from typing import IO, NoReturn

ROOT = Path(__file__).resolve().parent.parent
HEX64 = re.compile(r"[0-9a-f]{64}")
MISE_TOML = Path(os.environ.get("MISE_TOML", str(ROOT / "mise.toml")))
MISE_LOCK = Path(os.environ.get("MISE_LOCK", str(ROOT / "mise.lock")))
# The documented regeneration command (tools/pins.tsv header), plus --quiet.
# The pinned uv (tools/pins.tsv, installed by scripts/tools.sh) when present, like tools.sh itself.
UV = str(ROOT / ".tools" / "bin" / "uv") if (ROOT / ".tools" / "bin" / "uv").is_file() else "uv"
UV_COMPILE = [
    *(UV, "pip", "compile", "--quiet", "--generate-hashes", "--no-header"),
    *("--python-version", "3.13", "--python-platform", "x86_64-unknown-linux-gnu"),
]
MIN_AGE = timedelta(
    days=7
)  # a release must be this old before it is pinned (Renovate minimumReleaseAge)
ATTEMPTS = 4  # fetch tries for a transient failure
TRANSIENT = {429, 500, 502, 503, 504}
PIN_FIELDS = 5  # name version platform url sha256
MIN_SUM_FIELDS = 2  # a hash and a name
USAGE = "usage: pins-refresh.sh [--check] [--base REF] [name...]"

# tool -> (kind, checksum-file url template, expected url prefix).
# The prefix anchors the vendor: a row whose url points elsewhere (a look-alike org) is refused
# before anything is fetched.
# Fields: {url} the pin url, {dir} its directory, {ver} the version.
#   sumfile    a vendor file of "<sha256>  <asset>" lines (any of the usual path/star decorations)
#   hashfile   a file holding the bare sha256, beside the asset
#   yq-columns yq's `checksums` (one column per algorithm, ordered by checksums_hashes_order)
#   gh-digest  the release-asset `digest` from the GitHub releases API (no vendor file exists)
#   archive    a GitHub tag archive or a file at a tag (no vendor checksum): hashed on download
#   uv         a Python tool: hashed lock in tools/requirements/, checked against PyPI
#   none       uv downloads and verifies it itself
SOURCES: dict[str, tuple[str, str, str]] = {
    "mise": ("sumfile", "{dir}/SHASUMS256.txt", "https://github.com/jdx/mise/releases/download/"),
    "k8s-openapi": ("archive", "", "https://raw.githubusercontent.com/kubernetes/kubernetes/"),
    "kyverno-chart": ("archive", "", "https://kyverno.github.io/kyverno/"),
    "external-secrets-chart": (
        "archive",
        "",
        "https://github.com/external-secrets/external-secrets/releases/download/helm-chart-",
    ),
    "python": ("none", "", "uv:python"),
    "schemathesis": ("uv", "", "uv:schemathesis"),
    "yamale": ("uv", "", "uv:yamale"),
    "yamllint": ("uv", "", "uv:yamllint"),
    "pip-audit": ("uv", "", "uv:pip-audit"),
}

# config.yaml tool -> (github repo, checksum file, asset, algorithm).
# Mirrors tools-install.sh (linux, amd64).
# {v} is the version as pinned (with its "v"), {ver} without it.
CONFIG_SOURCES: dict[str, tuple[str, str, str, str]] = {
    "cosign": ("sigstore/cosign", "cosign_checksums.txt", "cosign-linux-amd64", "sha256"),
    "syft": ("anchore/syft", "syft_{ver}_checksums.txt", "syft_{ver}_linux_amd64.tar.gz", "sha256"),
    "grype": (
        "anchore/grype",
        "grype_{ver}_checksums.txt",
        "grype_{ver}_linux_amd64.tar.gz",
        "sha256",
    ),
    "grant": (
        "anchore/grant",
        "grant_{ver}_checksums.txt",
        "grant_{ver}_linux_amd64.tar.gz",
        "sha256",
    ),
    "osv-scanner": (
        "google/osv-scanner",
        "osv-scanner_SHA256SUMS",
        "osv-scanner_linux_amd64",
        "sha256",
    ),
    "sbomqs": (
        "interlynk-io/sbomqs",
        "checksums.txt",
        "sbomqs_{ver}_Linux_x86_64.tar.gz",
        "sha256",
    ),
    "sbom-utility": (
        "CycloneDX/sbom-utility",
        "sbom-utility-{v}-linux-amd64.tar.gz.md5",
        "sbom-utility-{v}-linux-amd64.tar.gz",
        "md5",
    ),
    "kubescape": (
        "kubescape/kubescape",
        "checksums.sha256",
        "kubescape_{ver}_linux_amd64",
        "sha256",
    ),
}


class PinError(Exception):
    """A pin could not be verified (fetch failed, asset not listed, malformed vendor file)."""


def fail(message: str) -> NoReturn:
    raise PinError(message)


# The upstream CRDs kubeconform's schemas come from (scripts/ci/kube_schemas.py): a release asset or
# a file at a tag, so the sha256 is the file's own (hashed on download), like a tag archive.
CRD_SOURCES_PATH = Path(
    os.environ.get("CRD_SOURCES", str(ROOT / "charts/your-service-name/ci/crds/sources.tsv"))
)
CRD_PREFIXES = ("https://github.com/", "https://raw.githubusercontent.com/")
CRD_FIELDS = 5  # name version url sha256 kinds


@dataclass(frozen=True)
class CrdSource:
    name: str
    version: str
    url: str
    sha256: str
    kinds: str


@dataclass(frozen=True)
class Pin:
    name: str
    version: str
    platform: str
    url: str
    sha256: str


def out(text: str) -> None:
    sys.stdout.write(text + "\n")


class HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but never to anything but https."""

    def redirect_request(  # noqa: PLR0913, PLR0917
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> urllib.request.Request | None:
        if not newurl.startswith("https://"):
            fail(f"non-https redirect to {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_request(url: str) -> urllib.request.Request:
    request = urllib.request.Request(url, headers={"User-Agent": "pins-refresh"})  # noqa: S310
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        # Unredirected: urllib would copy a normal header to wherever a redirect points.
        request.add_unredirected_header("Authorization", f"Bearer {token}")
    return request


def open_url(request: urllib.request.Request) -> bytes:
    opener = urllib.request.build_opener(HttpsOnlyRedirect)
    with opener.open(request, timeout=60) as resp:
        data: bytes = resp.read()
    return data


def fetch(url: str) -> bytes:
    """GET with bounded retries (timeouts, 429, 5xx); a 404 or any other answer is final."""
    if not url.startswith("https://"):
        fail(f"refusing non-https url {url}")
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return open_url(build_request(url))
        except urllib.error.HTTPError as exc:
            transient = exc.code in TRANSIENT
            error: Exception = exc
        except (TimeoutError, urllib.error.URLError, ConnectionError) as exc:
            transient, error = True, exc
        if not transient or attempt == ATTEMPTS:
            fail(f"cannot fetch {url}: {error}")
        time.sleep(2 ** (attempt - 1))
    fail(f"cannot fetch {url}")  # unreachable; keeps the type checker honest


def utcnow() -> datetime:
    return datetime.now(UTC)


def exclude_newer(now: datetime) -> str:
    return (now - MIN_AGE).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_show(base: str, rel: str) -> str:
    run = subprocess.run(  # noqa: S603
        ["git", "show", f"{base}:{rel}"],  # noqa: S607
        cwd=ROOT, capture_output=True, text=True, check=False,
    )  # fmt: skip
    return run.stdout if run.returncode == 0 else ""


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_pins(text: str) -> list[Pin]:
    pins = []
    for line in text.splitlines():
        if line and not line.startswith("#"):
            fields = line.split("\t")
            if len(fields) != PIN_FIELDS:
                fail(f"malformed pin row: {line}")
            pins.append(Pin(*fields))
    return pins


def parse_crd_sources(text: str) -> list[CrdSource]:
    rows = []
    for line in text.splitlines():
        if line and not line.startswith("#"):
            fields = line.split("\t")
            if len(fields) != CRD_FIELDS:
                fail(f"malformed CRD source row: {line}")
            rows.append(CrdSource(*fields))
    return rows


def crd_sha(row: CrdSource) -> str:
    """The sha256 of the file at the row's url; only a known vendor's https url is fetched."""
    if not row.url.startswith(CRD_PREFIXES) or f"v{row.version}/" not in row.url:
        fail(f"{row.url} is not a GitHub file at the tag v{row.version}")
    return hashlib.sha256(fetch(row.url)).hexdigest()


def run_crd(row: CrdSource, text: str, *, check: bool) -> tuple[bool, str]:
    """Check or update one CRD source: (failed, the sources text with its sha256 updated)."""
    try:
        got = crd_sha(row)
    except (PinError, OSError, ValueError) as exc:
        out(f"ERROR crd {row.name} {row.version}: {exc}")
        return True, text
    if got == row.sha256:
        out(f"ok crd {row.name} {row.version}")
    elif check:
        out(f"DRIFT crd {row.name} {row.version}: recorded {row.sha256}, the file is {got}")
        return True, text
    else:
        text = text.replace("\t".join(astuple(row)), "\t".join(astuple(replace(row, sha256=got))))
        out(f"UPDATED crd {row.name} {row.version}")
    return False, text


def parse_config_tools(text: str) -> dict[str, str]:
    block = re.search(r"^tools:\n((?:[ #].*\n|\n)*)", text, re.MULTILINE)
    if not block:
        return {}
    return dict(re.findall(r"^  ([a-z][a-z0-9-]*): (v\S+)\s*$", block.group(1), re.MULTILINE))


def parse_lock(text: str, pkg: str) -> tuple[str, set[str]]:
    """The version and hashes of `pkg` in a hash-pinned lock (the requirements are alphabetical)."""
    for match in re.finditer(
        r"^([A-Za-z0-9_.-]+)==(\S+) \\\n((?:[ \t]+--hash=sha256:[0-9a-f]{64}(?: \\)?\n?)+)",
        text,
        re.MULTILINE,
    ):
        if norm(match[1]) == norm(pkg):
            return match[2], set(HEX64.findall(match[3]))
    fail(f"lock has no hash-pinned {pkg}")


def sha_from_sumfile(text: str, asset: str) -> str:
    for line in text.splitlines():
        parts = line.split()
        if (
            len(parts) >= MIN_SUM_FIELDS
            and parts[-1].lstrip("*").rsplit("/", 1)[-1] == asset
            and HEX64.fullmatch(parts[0])
        ):
            return parts[0]
    fail(f"{asset} is not listed in the vendor checksum file")


def sha_from_yq(text: str, order: str, asset: str) -> str:
    if "SHA-256" not in order.split():
        fail("the vendor's checksums_hashes_order lists no SHA-256 column")
    column = order.split().index("SHA-256")
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0] == asset:
            if len(parts) > 1 + column and HEX64.fullmatch(parts[1 + column]):
                return parts[1 + column]
            break
    fail(f"no SHA-256 for {asset} in the vendor checksums")


def url_problem(url: str) -> str | None:
    """Why a vendor url is not a plain https url (a prefix match needs one), or None."""
    parts = urllib.parse.urlsplit(url)
    plain = (
        parts.scheme == "https"
        and parts.netloc == parts.hostname
        and not parts.query
        and not parts.fragment
        and not any(c in url for c in "%?#\\")
        and posixpath.normpath(parts.path) == parts.path
        and "//" not in parts.path
    )
    return None if plain else "not a plain https url (userinfo, port, query, fragment, %, .. or //)"


def check_prefix(pin: Pin) -> None:
    prefix = SOURCES[pin.name][2]
    if prefix.startswith("uv:"):
        ok = pin.url == prefix
    else:
        why = url_problem(pin.url)
        if why:
            fail(f"{pin.name}: url is {why}")
        ok = pin.url.startswith(prefix)
    if not ok:
        fail(f"{pin.name}: url is not under the expected vendor prefix {prefix}")


def vendor_sha(pin: Pin) -> str:
    kind, template, _prefix = SOURCES[pin.name]
    check_prefix(pin)
    directory, asset = pin.url.rsplit("/", 1)
    source = template.format(url=pin.url, dir=directory, ver=pin.version)
    if kind == "sumfile":
        return sha_from_sumfile(fetch(source).decode(), asset)
    if kind == "hashfile":
        value = (fetch(source).decode().split() or [""])[0]
        if not HEX64.fullmatch(value):
            fail(f"{source} does not hold a sha256")
        return value
    if kind == "yq-columns":
        return sha_from_yq(
            fetch(source).decode(), fetch(f"{directory}/checksums_hashes_order").decode(), asset
        )
    if kind == "gh-digest":
        match = re.fullmatch(
            r"https://github\.com/([^/]+/[^/]+)/releases/download/([^/]+)/(.+)", pin.url
        )
        if not match:
            fail(f"{pin.url} is not a GitHub release asset url")
        release = json.loads(
            fetch(f"https://api.github.com/repos/{match[1]}/releases/tags/{match[2]}")
        )
        for item in release.get("assets", []):
            digest = str(item.get("digest") or "")
            if item.get("name") == match[3] and re.fullmatch(f"sha256:{HEX64.pattern}", digest):
                return digest.removeprefix("sha256:")
        fail(f"the release lists no sha256 digest for {match[3]}")
    if kind == "archive":
        return hashlib.sha256(fetch(pin.url)).hexdigest()
    fail(f"{pin.name}: no checksum source of kind {kind}")


def lock_path(pin: Pin) -> Path:
    return Path(os.environ.get("REQS", str(ROOT / "tools/requirements"))) / f"{pin.name}.txt"


def problem(pin: Pin) -> str | None:
    """None when the pin matches its vendor, else what differs. PinError if that cannot be told."""
    check_prefix(pin)
    kind = SOURCES[pin.name][0]
    if kind == "none":
        return None
    if kind == "uv":
        # The PyPI hashes and release age of every lock entry are checked per lock (lock_problems).
        pkg = pin.url.removeprefix("uv:")
        lock_ver, _hashes = parse_lock(lock_path(pin).read_text(), pkg)
        if lock_ver != pin.version:
            return f"lock pins {pkg}=={lock_ver}, the pin version is {pin.version}"
        return None
    vendor = vendor_sha(pin)
    return None if vendor == pin.sha256 else f"recorded {pin.sha256}, vendor {vendor}"


ENTRY_LINE = re.compile(r"([A-Za-z0-9_.-]+)==(\S+) \\")
HASH_LINE = re.compile(r"\s+--hash=sha256:([0-9a-f]{64})(?: \\)?")


def parse_lock_lines(text: str) -> tuple[dict[str, tuple[str, set[str]]], list[str]]:
    """Every `name==version` of a hash-pinned lock with its hashes, and the lines that fit no entry.

    uv writes one layout; anything else (an index option, a one-line requirement, `-r`, a URL) is
    reported, because `uv pip install -r` would honour it while the checks below would not see it.
    """
    entries: dict[str, tuple[str, set[str]]] = {}
    problems: list[str] = []
    current = ""
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entry, hashed = ENTRY_LINE.fullmatch(line), HASH_LINE.fullmatch(line)
        if entry:
            current = norm(entry[1])
            entries[current] = (entry[2], set())
        elif hashed and current:
            entries[current][1].add(hashed[1])
        else:
            kind = "option" if stripped.startswith("-") and not hashed else "unparsed"
            problems.append(f"line {number}: {kind} line {stripped[:60]!r}")
    problems += [f"{n} {v}: no hashes" for n, (v, h) in entries.items() if not h]
    if not entries:
        problems.append("no hash-pinned entries")
    return entries, problems


def lock_entries(text: str) -> dict[str, tuple[str, set[str]]]:
    return parse_lock_lines(text)[0]


def lock_syntax_problems(text: str) -> list[str]:
    return parse_lock_lines(text)[1]


def lock_problems(path: Path) -> list[str]:
    """Recorded hashes must be on PyPI for that version, and those files 7+ days old."""
    text = path.read_text()
    found = lock_syntax_problems(text)
    cutoff = utcnow() - MIN_AGE
    for name, (version, hashes) in sorted(lock_entries(text).items()):
        files = json.loads(fetch(f"https://pypi.org/pypi/{name}/{version}/json"))["urls"]
        published = {f["digests"]["sha256"] for f in files}
        if hashes - published:
            found.append(
                f"{name} {version}: {len(hashes - published)} hash(es) PyPI does not publish"
            )
        recorded = [f for f in files if f["digests"]["sha256"] in hashes]
        young = [f for f in recorded if datetime.fromisoformat(f["upload_time_iso_8601"]) > cutoff]
        if young:
            found.append(f"{name} {version}: uploaded less than 7 days ago")
    return found


def config_problem(name: str, version: str) -> str | None:
    repo, sums, asset, algo = CONFIG_SOURCES[name]
    fields = {"v": version, "ver": version.removeprefix("v")}
    base = f"https://github.com/{repo}/releases/download/{version}"
    text = fetch(f"{base}/{sums.format(**fields)}").decode()
    if algo == "md5":
        return (
            None
            if re.fullmatch(r"[0-9a-f]{32}", text.strip())
            else "vendor md5 file does not hold a digest"
        )
    sha_from_sumfile(text, asset.format(**fields))
    return None


def compile_command(lock: Path, constraints: Path, now: datetime) -> list[str]:
    """Regenerate in place (the old lock is the preference: no upgrades), with a cutoff."""
    return [
        *UV_COMPILE,
        "--exclude-newer",
        exclude_newer(now),
        "-c",
        str(constraints),
        "-o",
        lock.name,  # relative: run from the lock's directory, so no absolute path reaches the file
        "-",
    ]


def tidy_annotations(text: str) -> str:
    """Remove uv's `-c`/`-r <absolute path>` sources from the `# via` lines.

    Those paths belong to the machine that ran uv (a temp file, a scratch dir): they must never be
    committed, and dropping them keeps a regenerated lock identical to the committed one.
    """
    text = re.sub(r"^ +# via -[cr] /\S*\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"^ +#   -[cr] /\S*\n", "", text, flags=re.MULTILINE)
    text = re.sub(r"^ +# via\n(?! +#   )", "", text, flags=re.MULTILINE)
    return re.sub(
        r"^( +)# via\n +#   (\S[^\n]*)\n(?! +#   )", r"\1# via \2\n", text, flags=re.MULTILINE
    )


def regenerate_lock(pin: Pin) -> None:
    """Rewrite the pin's lock; pyyaml stays at the version of pyyaml.txt."""
    lock = lock_path(pin)
    constraints = ""
    pyyaml = lock.parent / "pyyaml.txt"
    if pyyaml.is_file():
        constraints = f"pyyaml=={parse_lock(pyyaml.read_text(), 'pyyaml')[0]}\n"
    with tempfile.TemporaryDirectory() as tmp:
        constraint_file = Path(tmp) / "constraints.txt"
        constraint_file.write_text(constraints)
        result = subprocess.run(  # noqa: S603
            compile_command(lock, constraint_file, utcnow()),
            input=f"{pin.url.removeprefix('uv:')}=={pin.version}\n", text=True, check=False,
            cwd=lock.parent,
        )  # fmt: skip
    if result.returncode:
        fail(f"uv pip compile failed for {pin.name}")
    tidy = tidy_annotations(lock.read_text())
    if re.search(r"^\s*#.*\s-[rc]\s+/", tidy, re.MULTILINE):
        fail(f"{pin.name}: the regenerated lock still names an absolute path")
    lock.write_text(tidy)


# When the checker itself changes, every row is checked: a row diff would hide a broken checker.
WATCHED = (
    "scripts/pins_refresh.py",
    "scripts/pins-refresh.sh",
    ".github/workflows/reusable-pins.yml",
    ".github/workflows/pins-drift.yml",
)


def changed(
    base: str,
    selection: tuple[list[Pin], dict[str, str], list[Path]],
    rels: tuple[str, str],
) -> tuple[list[Pin], dict[str, str], list[Path]]:
    """Rows, config tools and locks that differ from `base` (all of them if the checker changed)."""
    pins, tools, locks = selection
    for rel in WATCHED:
        current = (ROOT / rel).read_text() if (ROOT / rel).is_file() else ""
        if git_show(base, rel) != current:
            return selection
    old_pins = {(p.name, p.platform): p for p in parse_pins(git_show(base, rels[0]))}
    old_tools = parse_config_tools(git_show(base, rels[1]))

    def lock_differs(path: Path) -> bool:
        return git_show(base, os.path.relpath(path, ROOT)) != path.read_text()

    picked = [
        p
        for p in pins
        if old_pins.get((p.name, p.platform)) != p
        or (SOURCES[p.name][0] == "uv" and lock_differs(lock_path(p)))
    ]
    return (
        picked,
        {n: v for n, v in tools.items() if old_tools.get(n) != v},
        [path for path in locks if lock_differs(path)],
    )


def parse_args(argv: list[str]) -> tuple[bool, str, list[str]]:
    """(check, base, names); ValueError carries the message for a bad command line."""
    check, base, names = False, "", []
    args = iter(argv)
    for arg in args:
        if arg == "--check":
            check = True
        elif arg == "--base":
            base = next(args, "")
            if not base:
                msg = "--base needs a value"
                raise ValueError(msg)
        elif arg.startswith("-"):
            msg = f"unknown option {arg}"
            raise ValueError(msg)
        else:
            names.append(arg)
    if base and not check:
        msg = "--base only applies with --check"
        raise ValueError(msg)
    return check, base, names


def run_pin(pin: Pin, text: str, *, check: bool) -> tuple[bool, str]:
    """Check or update one pin: (failed, the pins file text with this pin's checksum updated)."""
    try:
        found = problem(pin)
        if not found:
            out(f"ok {pin.name} {pin.version}")
        elif check:
            out(f"DRIFT {pin.name} {pin.version}: {found}")
            return True, text
        else:
            if SOURCES[pin.name][0] == "uv":
                regenerate_lock(pin)
            else:
                new = replace(pin, sha256=vendor_sha(pin))
                text = text.replace("\t".join(astuple(pin)), "\t".join(astuple(new)))
            out(f"UPDATED {pin.name} {pin.version}: {found}")
    except (PinError, OSError, KeyError, ValueError) as exc:
        out(f"ERROR {pin.name} {pin.version}: {exc}")
        return True, text
    return False, text


def run_tool(name: str, version: str) -> bool:
    try:
        found = config_problem(name, version)
    except (PinError, ValueError) as exc:
        out(f"ERROR {name} {version} (config.yaml): {exc}")
        return True
    out(f"DRIFT {name} {version}: {found}" if found else f"ok {name} {version} (config.yaml)")
    return bool(found)


def run_lock(path: Path) -> bool:
    try:
        found = lock_problems(path)
    except (PinError, OSError, KeyError, ValueError) as exc:
        out(f"ERROR lock {path.stem}: {exc}")
        return True
    for line in found:
        out(f"DRIFT lock {path.stem}: {line}")
    if not found:
        out(f"ok lock {path.stem}")
    return bool(found)


def mise_problems(config: str, lock: str) -> list[str]:
    """Where mise.lock does not follow mise.toml: a tool at another version, without a sha256 for a
    locked platform, missing, or no longer configured."""
    cfg = tomllib.loads(config)
    platforms = cfg["settings"]["lockfile_platforms"]
    # A value is a version, or a table of options with the version in it.
    wanted = {
        key.removeprefix("core:"): (key, v["version"] if isinstance(v, dict) else v)
        for key, v in cfg.get("tools", {}).items()
    }
    locked = tomllib.loads(lock).get("tools", {})
    found = []
    for short, (key, version) in wanted.items():
        entry = (locked.get(short) or [{}])[0]
        if not entry:
            found.append(f"{key}: not in mise.lock")
            continue
        if entry.get("version") != version:
            found.append(f"{key}: mise.toml pins {version}, mise.lock has {entry.get('version')}")
        for platform in platforms:
            sha = entry.get(f"platforms.{platform}", {}).get("checksum", "")
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", sha):
                found.append(f"{key}: no sha256 for {platform} in mise.lock")
    found += [f"{short}: in mise.lock, not in mise.toml" for short in locked if short not in wanted]
    return found + mise_url_problems(config, lock)


# Where each mise.lock entry may download from: its key (resolved through mise.toml's
# [tool_alias]) -> (url, url_api, or None where the lock must hold no url_api). {v} is the locked
# version, {tag} a release tag ending in it, {file} one path segment, {id} a number, {repo} the
# key's <owner>/<repo>. An aqua: or github: <owner>/<repo> key with no row here downloads from that
# repository's releases (GITHUB_ORIGIN); any other key has no rule and is a problem, so a new tool
# is anchored before it is locked.
MISE_ORIGINS: dict[str, tuple[str, str | None]] = {
    "core:node": ("https://nodejs.org/dist/v{v}/{file}", None),
    "aqua:helm/helm": ("https://get.helm.sh/helm-v{v}-{file}", None),
    "aqua:kubernetes/kubernetes/kubectl": (
        "https://dl.k8s.io/v{v}/bin/{file}/{file}/kubectl",
        None,
    ),
    # Its recipe downloads the tag archive.
    "aqua:bats-core/bats-core": (
        "https://github.com/{repo}/archive/refs/tags/v{v}.tar.gz",
        "https://api.github.com/repos/{repo}/tarball/v{v}",
    ),
}
GITHUB_ORIGIN = (
    "https://github.com/{repo}/releases/download/{tag}/{file}",
    "https://api.github.com/repos/{repo}/releases/assets/{id}",
)
GITHUB_KEY = re.compile(r"(?:aqua|github):([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")
ORIGIN_FIELD = re.compile(r"(\{(?:v|tag|file|id|repo)\})")


def origin_matches(template: str, url: str, repo: str, version: str) -> bool:
    """Whether a plain https url fits a MISE_ORIGINS template (its literal parts match exactly)."""
    fields = {
        "{v}": re.escape(version),
        "{tag}": f"[A-Za-z0-9_.+-]*{re.escape(version)}",
        "{file}": "[A-Za-z0-9_.+-]+",
        "{id}": "[0-9]+",
        "{repo}": re.escape(repo),
    }
    pattern = "".join(fields.get(p, re.escape(p)) for p in ORIGIN_FIELD.split(template))
    return url_problem(url) is None and re.fullmatch(pattern, url) is not None


def mise_url_problems(config: str, lock: str) -> list[str]:
    """Where a mise.lock url or url_api is not under the origin its key implies (offline). The
    sha256 alone proves nothing when a pull request swaps both the url and the checksum."""
    cfg = tomllib.loads(config)
    aliases, tools = cfg.get("tool_alias", {}), cfg.get("tools", {})
    found: list[str] = []
    for short, entries in tomllib.loads(lock).get("tools", {}).items():
        entry = entries[0]
        key = aliases.get(short) or (f"core:{short}" if f"core:{short}" in tools else short)
        if entry.get("backend") != key:
            found.append(f"{short}: backend {entry.get('backend')}, mise.toml says {key}")
            continue
        repo = GITHUB_KEY.fullmatch(key)
        rule = MISE_ORIGINS.get(key) or (GITHUB_ORIGIN if repo else None)
        if rule is None:
            found.append(f"{short}: no origin rule for {key}")
            continue
        owner_repo, version = (repo[1] if repo else ""), str(entry.get("version"))
        for platform in cfg["settings"]["lockfile_platforms"]:
            locked = entry.get(f"platforms.{platform}", {})
            for field, template in (("url", rule[0]), ("url_api", rule[1])):
                url = locked.get(field)
                if url is None and (field == "url_api" or not locked):
                    continue  # no url_api is fine; a missing platform is mise_problems' finding
                if template is None or not origin_matches(template, url or "", owner_repo, version):
                    where = template.replace("{repo}", owner_repo) if template else "nowhere"
                    found.append(f"{short}: {platform} {field} {url} is not under {where}")
    return found


# github: tool in mise.lock -> the vendor's checksum file beside the asset ({dir} is its directory),
# or "" where the vendor publishes none (the lock then holds GitHub's release-asset digest alone).
# `mise lock` writes the lock, so each entry is compared with the vendor, not just trusted.
MISE_VENDOR_SUMS = {
    "github:helm-unittest/helm-unittest": "{dir}/helm-unittest-checksum.sha",
    "github:oasdiff/oasdiff": "{dir}/checksums.txt",
    "github:prometheus/prometheus": "{dir}/sha256sums.txt",
    "github:sbdchd/squawk": "",
}


def mise_vendor_problems(config: str, lock: str) -> list[str]:
    """Where a github: tool's locked checksum is not the vendor's published one (network). A
    github: tool with no entry in MISE_VENDOR_SUMS is a problem too: say where its checksum is."""
    cfg = tomllib.loads(config)
    locked = tomllib.loads(lock).get("tools", {})
    found: list[str] = []
    files: dict[str, str] = {}
    for key in cfg.get("tools", {}):
        if not key.startswith("github:"):
            continue
        template = MISE_VENDOR_SUMS.get(key)
        if template is None:
            found.append(f"{key}: no vendor checksum source in MISE_VENDOR_SUMS")
            continue
        for platform in cfg["settings"]["lockfile_platforms"]:
            entry = (locked.get(key) or [{}])[0].get(f"platforms.{platform}", {})
            url, sha = entry.get("url", ""), entry.get("checksum", "").removeprefix("sha256:")
            prefix = f"https://github.com/{key.removeprefix('github:')}/releases/download/"
            if not template or not sha:
                continue  # no vendor file; a missing checksum is mise_problems' finding
            if not url.startswith(prefix) or url_problem(url):
                found.append(f"{key}: {platform} url is not a plain url under {prefix}")
                continue
            directory, asset = url.rsplit("/", 1)
            source = template.format(dir=directory)
            if source not in files:
                files[source] = fetch(source).decode()
            vendor = sha_from_sumfile(files[source], asset)
            if vendor != sha:
                found.append(f"{key}: {platform} lock has {sha}, vendor {vendor}")
    return found


def mise_lock(platforms: list[str]) -> bool:
    """Run the pinned mise's `mise lock` for these platforms (tools.sh drops every token)."""
    cmd = [str(ROOT / "scripts/tools.sh"), "mise", "lock", "--platform", ",".join(platforms)]
    return subprocess.run(cmd, check=False).returncode == 0  # noqa: S603


def run_mise(*, check: bool) -> bool:
    """Check mise.lock against mise.toml; refreshing runs `mise lock` first. True on a problem."""
    if not (MISE_TOML.is_file() and MISE_LOCK.is_file()):
        return False
    config = MISE_TOML.read_text()
    found = mise_problems(config, MISE_LOCK.read_text())
    if found and check:
        for line in found:
            out(f"DRIFT mise.lock: {line}")
        return True
    if found:
        platforms = tomllib.loads(config)["settings"]["lockfile_platforms"]
        out(f"UPDATED mise.lock: {len(found)} difference(s), running mise lock")
        if not mise_lock(platforms):
            out("ERROR mise.lock: mise lock failed")
            return True
        found = mise_problems(config, MISE_LOCK.read_text())
    try:  # in step with the config: now the vendors' own checksum files
        found += mise_vendor_problems(config, MISE_LOCK.read_text())
    except (PinError, ValueError) as exc:
        out(f"ERROR mise.lock: {exc}")
        return True
    for line in found:
        out(f"DRIFT mise.lock: {line}")
    if not found:
        out("ok mise.lock")
    return bool(found)


PNPM_DOCKERFILE = Path(os.environ.get("PNPM_DOCKERFILE", str(ROOT / "Dockerfile.frontend")))
PNPM_KEY = "aqua:pnpm/pnpm"


def pnpm_image_problems(
    dockerfile: str, config: str, lock: str
) -> tuple[list[str], list[str], str]:
    """(vendor problems, Dockerfile drift, the fixed Dockerfile) for the image's pnpm.
    Network: the pnpm release's asset digests."""
    version = str(tomllib.loads(config)["tools"][PNPM_KEY])
    entry = tomllib.loads(lock)["tools"][PNPM_KEY][0]
    amd64 = str(entry["platforms.linux-x64"]["checksum"]).removeprefix("sha256:")
    release = json.loads(fetch(f"https://api.github.com/repos/pnpm/pnpm/releases/tags/v{version}"))
    digests = {
        str(a.get("name")): str(a.get("digest") or "").removeprefix("sha256:")
        for a in release.get("assets", [])
    }
    vendor: list[str] = []
    if digests.get("pnpm-linux-x64.tar.gz") != amd64:
        vendor.append(f"mise.lock's linux-x64 sha256 {amd64} is not the release's digest")
    arm64 = digests.get("pnpm-linux-arm64.tar.gz", "")
    if not HEX64.fullmatch(arm64):
        vendor.append(f"the v{version} release lists no sha256 digest for pnpm-linux-arm64.tar.gz")
    want = {"PNPM_VERSION": version, "PNPM_SHA256_AMD64": amd64, "PNPM_SHA256_ARM64": arm64}
    drift: list[str] = []
    for name, value in want.items():
        match = re.search(rf"^ARG {name}=(\S*)$", dockerfile, re.MULTILINE)
        if not match:
            drift.append(f"no ARG {name}")
        elif match[1] != value:
            drift.append(f"{name} is {match[1]}, want {value}")
            dockerfile = dockerfile.replace(match[0], f"ARG {name}={value}")
    return vendor, drift, dockerfile


def run_pnpm_image(*, check: bool) -> bool:
    """Check (and on a refresh, write) Dockerfile.frontend's pnpm. True on a problem."""
    if not (PNPM_DOCKERFILE.is_file() and MISE_TOML.is_file() and MISE_LOCK.is_file()):
        return False
    text = PNPM_DOCKERFILE.read_text()
    try:
        vendor, drift, new = pnpm_image_problems(text, MISE_TOML.read_text(), MISE_LOCK.read_text())
    except (PinError, ValueError, KeyError) as exc:
        out(f"ERROR Dockerfile.frontend pnpm: {exc}")
        return True
    if drift and not check and not vendor and new != text:
        PNPM_DOCKERFILE.write_text(new)
        out(f"UPDATED Dockerfile.frontend pnpm: {'; '.join(drift)}")
        drift = [d for d in drift if d.startswith("no ARG")]
    for line in vendor + drift:
        out(f"DRIFT Dockerfile.frontend pnpm: {line}")
    if not vendor and not drift:
        out("ok Dockerfile.frontend pnpm")
    return bool(vendor or drift)


def run_crds(names: list[str], base: str, *, check: bool) -> tuple[bool, int]:
    """Check or update the CRD sources (the named ones, or the rows changed since `base`):
    (failed, how many were looked at). Rows that are unchanged since `base` are skipped, unless the
    checker itself changed."""
    path = Path(os.environ.get("CRD_SOURCES", str(CRD_SOURCES_PATH)))
    text = path.read_text() if path.is_file() else ""
    rows = [c for c in parse_crd_sources(text) if not names or c.name in names]
    if base and all(
        git_show(base, rel) == ((ROOT / rel).read_text() if (ROOT / rel).is_file() else "")
        for rel in WATCHED
    ):
        old = set(parse_crd_sources(git_show(base, os.path.relpath(path, ROOT))))
        rows = [c for c in rows if c not in old]
    failed, new = False, text
    for row in rows:
        bad, new = run_crd(row, new, check=check)
        failed = failed or bad
    if new != text:
        path.write_text(new)
    return failed, len(rows)


def main(argv: list[str]) -> int:
    try:
        check, base, names = parse_args(argv)
    except ValueError as exc:
        sys.stderr.write(f"pins-refresh: {exc}\n{USAGE}\n")
        return 2
    pins_path = Path(os.environ.get("PINS", str(ROOT / "tools/pins.tsv")))
    config_path = Path(os.environ.get("SECURITY_CONFIG", str(ROOT / "infra/security/config.yaml")))
    text = pins_path.read_text()
    pins = parse_pins(text)
    tools = parse_config_tools(config_path.read_text()) if config_path.is_file() else {}
    unknown = [p.name for p in pins if p.name not in SOURCES]
    unknown += [n for n in tools if n not in CONFIG_SOURCES]
    if unknown:
        sys.stderr.write(f"pins-refresh: no checksum source for {', '.join(unknown)}\n")
        return 1
    locks = sorted(Path(os.environ.get("REQS", str(ROOT / "tools/requirements"))).glob("*.txt"))
    if names:
        pins = [p for p in pins if p.name in names]
        tools = {n: v for n, v in tools.items() if n in names}
        locks = [path for path in locks if path.stem in names]
    if base:
        rels = (os.path.relpath(pins_path, ROOT), os.path.relpath(config_path, ROOT))
        pins, tools, locks = changed(base, (pins, tools, locks), rels)
    failed, crds = run_crds(names, base, check=check)
    new_text = text
    for pin in pins:
        bad, new_text = run_pin(pin, new_text, check=check)
        failed = failed or bad
    for name, version in tools.items():
        failed = run_tool(name, version) or failed
    for path in locks:
        failed = run_lock(path) or failed
    failed = (not names and run_mise(check=check)) or failed
    failed = (not names and run_pnpm_image(check=check)) or failed
    if names and not pins and not tools and not locks and not crds:
        out("no pins to check")
    if new_text != text:
        pins_path.write_text(new_text)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
