"""kubeconform validates against pinned, local schemas only; the schema generator is reproducible.

scripts/ci/kubeconform.sh reads the Kubernetes kinds from schemas generated from the pinned OpenAPI
spec (tools/pins.tsv, k8s-openapi) and the CRDs from the committed charts/your-service-name/ci/crds.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts" / "ci" / "kubeconform.sh"
GENERATOR = ROOT / "scripts" / "ci" / "kube_schemas.py"
CHART = ROOT / "charts" / "your-service-name"
CRDS = CHART / "ci" / "crds"
WORKFLOW = ROOT / ".github" / "workflows" / "reusable-chart.yml"
BASH: str = shutil.which("bash") or "/usr/bin/bash"
TEXT = SCRIPT.read_text(encoding="utf-8")


def _pin(name: str) -> list[str]:
    rows = (ROOT / "tools" / "pins.tsv").read_text(encoding="utf-8").splitlines()
    return next(r.split("\t") for r in rows if r.startswith(f"{name}\t"))


def _kubeconform_args() -> list[str]:
    """The script's own KUBECONFORM_ARGS array, evaluated by bash (no copy of it lives here)."""
    block = re.search(r"^KUBECONFORM_ARGS=\(\n.*?^\)$", TEXT, re.MULTILINE | re.DOTALL)
    assert block, "kubeconform.sh no longer defines KUBECONFORM_ARGS"
    program = (
        "SCHEMAS=/schemas CHART=/chart KUBE_VERSION=1.0.0\n"
        f'{block[0]}\nprintf "%s\\n" "${{KUBECONFORM_ARGS[@]}}"'
    )
    out = subprocess.run([BASH, "-c", program], capture_output=True, text=True, check=True)
    return out.stdout.splitlines()


def test_the_kubeconform_command_reads_only_local_schema_locations() -> None:
    args = _kubeconform_args()
    locations = [args[i + 1] for i, a in enumerate(args) if a == "-schema-location"]
    assert len(locations) == 2  # noqa: PLR2004 (the Kubernetes kinds, the CRDs)
    for location in locations:
        assert "://" not in location
        assert location != "default"
        assert location.startswith(("/schemas/", "/chart/ci/crds/")), location
    assert "-strict" in args
    assert "-kubernetes-version" in args


def test_every_kubeconform_call_uses_that_one_argument_list() -> None:
    code = "\n".join(ln for ln in TEXT.splitlines() if not ln.lstrip().startswith("#"))
    calls = re.findall(r"\bkubeconform ([\"'-]\S*)", code)
    assert calls
    assert set(calls) == {'"${KUBECONFORM_ARGS[@]}"'}, calls


def test_the_script_never_reaches_the_network_or_skips_a_missing_schema() -> None:
    code = "\n".join(ln for ln in TEXT.splitlines() if not ln.lstrip().startswith("#"))
    assert "-ignore-missing-schemas" not in code
    assert "https://" not in TEXT
    for tool in ("curl", "wget", "crds fetch"):
        assert tool not in TEXT, tool


def test_the_schemas_follow_the_charts_kube_version_floor() -> None:
    floor = yaml.safe_load((CHART / "Chart.yaml").read_text(encoding="utf-8"))["kubeVersion"]
    match = re.fullmatch(r">=(\d+)\.(\d+)\.\d+(-0)?", floor)
    assert match, floor
    _name, version, _plat, url, _sha = _pin("k8s-openapi")
    assert version.split(".")[:2] == [match[1], match[2]]
    assert url == (
        "https://raw.githubusercontent.com/kubernetes/kubernetes/"
        f"v{version}/api/openapi-spec/swagger.json"
    )


def test_every_crd_kind_the_chart_renders_has_a_vendored_schema() -> None:
    pairs = set()
    for template in (CHART / "templates").glob("*.yaml"):
        text = template.read_text(encoding="utf-8")
        for api, kind in re.findall(r"^apiVersion: (\S+)\nkind: (\S+)", text, re.MULTILINE):
            if "/" in api and "." in api.split("/")[0] and not api.endswith(".k8s.io/v1"):
                pairs.add((api, kind))
    # Gateway API is a CRD although its group ends in k8s.io.
    pairs.add(("gateway.networking.k8s.io/v1", "HTTPRoute"))
    assert len(pairs) >= 9  # noqa: PLR2004 (the CRD kinds the chart renders today)
    for api, kind in sorted(pairs):
        group, version = api.split("/")
        assert (CRDS / group / f"{kind.lower()}_{version}.json").is_file(), (api, kind)


def test_the_sources_list_each_pinned_crd_once_by_https_and_sha256() -> None:
    lines = (CRDS / "sources.tsv").read_text(encoding="utf-8").splitlines()
    rows = [ln.split("\t") for ln in lines if ln and not ln.startswith("#")]
    assert rows
    kinds = [k for r in rows for k in r[4].split(",")]
    assert len(kinds) == len(set(kinds))
    for name, version, url, sha, _kinds in rows:
        assert url.startswith("https://"), name
        assert version in url, name
        assert re.fullmatch(r"[0-9a-f]{64}", sha), name


def _run(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-I", str(GENERATOR), *args],
        capture_output=True,
        text=True,
        env={**os.environ, **env},
        check=False,
    )


INSTALL = "scripts/tools.sh install crd-sources"


def test_the_committed_crd_schemas_are_what_the_pinned_sources_generate() -> None:
    """Regenerate from the cached pinned sources (.tools/crd-sources, put there by the tool install)
    and compare. Offline: without the cache this fails and names the install command."""
    checked = _run("crds", "check", env={})
    assert checked.returncode == 0, checked.stderr


def test_without_the_cache_the_check_fails_and_names_the_install(tmp_path: Path) -> None:
    result = _run("crds", "check", env={"CRD_CACHE": str(tmp_path / "empty")})
    assert result.returncode == 1
    assert INSTALL in result.stderr


def test_this_module_never_fetches() -> None:
    """No test here may reach the network: no call passes the fetch subcommand."""
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            consts = [a.value for a in node.args if isinstance(a, ast.Constant)]
            assert "fetch" not in consts, ast.unparse(node)
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in (
            node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")]
        )
    }
    assert not imported & {"urllib", "http", "requests", "httpx", "socket"}, imported


def test_the_tool_install_fetches_the_crd_sources() -> None:
    result = subprocess.run(
        [BASH, str(ROOT / "scripts" / "tools.sh"), "--dry-run", "install", "crd-sources"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "scripts/ci/kube_schemas.py crds fetch" in result.stdout


def test_make_tools_fetches_the_crd_sources_too() -> None:
    result = subprocess.run(
        [shutil.which("make") or "/usr/bin/make", "-n", "tools"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert INSTALL in result.stdout


def test_the_ci_unit_job_installs_the_crd_sources_before_the_tests() -> None:
    backend = ROOT / ".github" / "workflows" / "reusable-backend.yml"
    steps = yaml.safe_load(backend.read_text(encoding="utf-8"))["jobs"]["unit"]["steps"]
    runs = [str(s.get("run", "")) for s in steps]
    install = next(i for i, r in enumerate(runs) if "scripts/tools.sh install" in r)
    assert "crd-sources" in runs[install].split("scripts/tools.sh install", 1)[1].split("\n")[0]
    tests = next(i for i, r in enumerate(runs) if "make test-unit" in r)
    assert install < tests


CRD = {
    "apiVersion": "apiextensions.k8s.io/v1",
    "kind": "CustomResourceDefinition",
    "spec": {
        "group": "example.test",
        "names": {"kind": "Widget"},
        "versions": [
            {
                "name": "v1",
                "served": True,
                "schema": {
                    "openAPIV3Schema": {
                        "type": "object",
                        "properties": {
                            "spec": {
                                "type": "object",
                                "properties": {
                                    "port": {"format": "int-or-string"},
                                    "labels": {"type": "object", "additionalProperties": True},
                                    "raw": {
                                        "type": "object",
                                        "properties": {"a": {"type": "string"}},
                                        "x-kubernetes-preserve-unknown-fields": True,
                                    },
                                },
                            }
                        },
                    }
                },
            },
            {"name": "v0", "served": False, "schema": {"openAPIV3Schema": {"type": "object"}}},
        ],
    },
}


def _crd_sandbox(tmp: Path) -> dict[str, str]:
    data = yaml.safe_dump(CRD).encode()
    sha = hashlib.sha256(data).hexdigest()
    (tmp / "cache").mkdir()
    (tmp / "cache" / sha).write_bytes(data)
    (tmp / "crds").mkdir()
    sources = tmp / "sources.tsv"
    sources.write_text(f"# comment\nexample\t1.0.0\thttps://x.test/1.0.0/w.yaml\t{sha}\tWidget\n")
    return {
        "CRD_SOURCES": str(sources),
        "CRD_DIR": str(tmp / "crds"),
        "CRD_CACHE": str(tmp / "cache"),
    }


def test_a_crd_becomes_a_strict_schema_per_served_version(tmp_path: Path) -> None:
    env = _crd_sandbox(tmp_path)
    assert _run("crds", "generate", env=env).returncode == 0
    files = sorted(p.relative_to(tmp_path / "crds") for p in (tmp_path / "crds").rglob("*.json"))
    assert files == [Path("example.test/widget_v1.json")]
    schema = json.loads((tmp_path / "crds" / files[0]).read_text())
    spec = schema["properties"]["spec"]
    assert schema["additionalProperties"] is False
    assert spec["additionalProperties"] is False
    assert spec["properties"]["port"] == {"oneOf": [{"type": "string"}, {"type": "integer"}]}
    assert spec["properties"]["labels"]["additionalProperties"] is True
    assert "additionalProperties" not in spec["properties"]["raw"]
    assert _run("crds", "check", env=env).returncode == 0


def test_check_fails_on_an_edited_or_extra_schema(tmp_path: Path) -> None:
    env = _crd_sandbox(tmp_path)
    assert _run("crds", "generate", env=env).returncode == 0
    path = tmp_path / "crds" / "example.test" / "widget_v1.json"
    path.write_text(path.read_text().replace('"additionalProperties": false', '"x": 1', 1))
    edited = _run("crds", "check", env=env)
    assert edited.returncode == 1
    assert "example.test/widget_v1.json" in edited.stderr
    assert _run("crds", "generate", env=env).returncode == 0
    (tmp_path / "crds" / "example.test" / "gadget_v1.json").write_text("{}\n")
    assert _run("crds", "check", env=env).returncode == 1


def test_a_cached_source_that_does_not_match_its_sha256_is_refused(tmp_path: Path) -> None:
    env = _crd_sandbox(tmp_path)
    cached = next((tmp_path / "cache").iterdir())
    cached.write_bytes(cached.read_bytes() + b"# changed\n")
    result = _run("crds", "generate", env=env)
    assert result.returncode == 1
    assert "checksum mismatch" in result.stderr


def test_a_listed_kind_missing_from_its_source_is_refused(tmp_path: Path) -> None:
    env = _crd_sandbox(tmp_path)
    sources = Path(env["CRD_SOURCES"])
    sources.write_text(sources.read_text().replace("\tWidget\n", "\tWidget,Gadget\n"))
    result = _run("crds", "generate", env=env)
    assert result.returncode == 1
    assert "no CRD for Gadget" in result.stderr


SPEC = {
    "definitions": {
        "io.k8s.api.apps.v1.Thing": {
            "type": "object",
            "required": ["spec"],
            "properties": {
                "spec": {"$ref": "#/definitions/io.k8s.api.apps.v1.ThingSpec"},
                "note": {"type": "string"},
            },
            "x-kubernetes-group-version-kind": [
                {"group": "apps", "version": "v1", "kind": "Thing"}
            ],
        },
        "io.k8s.api.apps.v1.ThingSpec": {
            "type": "object",
            "properties": {
                "port": {"$ref": "#/definitions/io.k8s.apimachinery.pkg.util.intstr.IntOrString"},
                "cpu": {"$ref": "#/definitions/io.k8s.apimachinery.pkg.api.resource.Quantity"},
                "self": {"$ref": "#/definitions/io.k8s.api.apps.v1.ThingSpec"},
            },
        },
        "io.k8s.apimachinery.pkg.util.intstr.IntOrString": {
            "type": "string",
            "format": "int-or-string",
        },
        "io.k8s.apimachinery.pkg.api.resource.Quantity": {"type": "string"},
        "io.k8s.api.core.v1.Note": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "x-kubernetes-group-version-kind": [{"group": "", "version": "v1", "kind": "Note"}],
        },
    }
}


def test_the_core_schemas_are_standalone_strict_and_named_like_kubeconforms_default(
    tmp_path: Path,
) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(SPEC))
    result = _run("core", str(spec), str(tmp_path / "out"), env={})
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == [
        "note-v1.json",
        "thing-apps-v1.json",
    ]
    thing = json.loads((tmp_path / "out" / "thing-apps-v1.json").read_text())
    assert "$ref" not in json.dumps(thing)
    assert thing["additionalProperties"] is False
    assert thing["properties"]["note"]["type"] == ["string", "null"]
    spec_schema = thing["properties"]["spec"]
    assert spec_schema["type"] == "object"  # required: not nullable
    assert spec_schema["additionalProperties"] is False
    props = spec_schema["properties"]
    assert props["port"] == {"oneOf": [{"type": "string"}, {"type": "integer"}]}
    assert props["cpu"] == {"oneOf": [{"type": "string"}, {"type": "number"}]}
    assert props["self"] == {}  # a recursive definition is cut, not inlined forever


def test_tools_sh_installs_the_spec_as_a_data_file_only(tmp_path: Path) -> None:
    art = tmp_path / "swagger.json"
    art.write_text("{}")
    sha = hashlib.sha256(art.read_bytes()).hexdigest()
    pins = tmp_path / "pins.tsv"
    pins.write_text(f"k8s-openapi\t9.9.9\tany\tfile://{art}\t{sha}\n")
    tools = tmp_path / ".tools"
    env = {**os.environ, "PINS": str(pins), "TOOLS_DIR": str(tools), "TOOLS_PROTO": "=file"}
    result = subprocess.run(
        [BASH, str(ROOT / "scripts" / "tools.sh"), "install", "k8s-openapi"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert (tools / "opt" / "k8s-openapi-9.9.9" / "k8s-openapi").read_text() == "{}"
    assert not list((tools / "bin").iterdir())


def test_the_chart_workflow_installs_the_sources_then_runs_both_checks_offline() -> None:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["chart"]["steps"]
    runs = [str(s.get("run", "")) for s in steps]
    install = next(i for i, r in enumerate(runs) if "scripts/tools.sh install helm4" in r)
    assert all(t in runs[install] for t in ("kubeconform", "yq", "k8s-openapi", "crd-sources"))
    check = next(i for i, r in enumerate(runs) if "kube_schemas.py crds check" in r)
    validate = next(i for i, r in enumerate(runs) if "scripts/ci/kubeconform.sh" in r)
    assert install < check < validate
    for offline in (runs[check], runs[validate]):
        assert "fetch" not in offline
        assert "install" not in offline


@pytest.mark.parametrize("target", ["kubeconform", "crd-schemas", "crd-schemas-check"])
def test_the_make_targets_exist(target: str) -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert re.search(rf"^{target}:", makefile, re.MULTILINE), target
