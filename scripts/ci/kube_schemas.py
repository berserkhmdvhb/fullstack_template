#!/usr/bin/env python3
"""JSON schemas for kubeconform, generated from pinned upstream sources (offline after a fetch).

  kube_schemas.py core SPEC OUTDIR
      the Kubernetes kinds, from the pinned OpenAPI v2 spec (tools/pins.tsv, k8s-openapi):
      standalone (every $ref inlined) and strict (no unknown field), in kubeconform's
      `<kind>[-<group>]-<version>.json` layout. Standard library only.
  kube_schemas.py crds fetch
      download each pinned CRD source (charts/your-service-name/ci/crds/sources.tsv) into the
      cache (.tools/crd-sources), refusing a sha256 mismatch. The only step that uses the network.
  kube_schemas.py crds generate | check
      from the cache only: write the CRD schemas to charts/your-service-name/ci/crds/<group>/
      <kind>_<version>.json (every served version of each listed kind), or compare them with the
      committed files and exit 1 on a difference. Needs PyYAML.

Env (for the tests): CRD_SOURCES (the sources file), CRD_DIR (the schema tree), CRD_CACHE.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
CRD_DIR = Path(os.environ.get("CRD_DIR", ROOT / "charts" / "your-service-name" / "ci" / "crds"))
SOURCES = Path(os.environ.get("CRD_SOURCES", CRD_DIR / "sources.tsv"))
CACHE = Path(os.environ.get("CRD_CACHE", ROOT / ".tools" / "crd-sources"))
SOURCE_FIELDS = 5  # name version url sha256 kinds
REF = "#/definitions/"
# Kubernetes serialises these as a string or a number; the OpenAPI spec says only "string".
QUANTITY = "io.k8s.apimachinery.pkg.api.resource.Quantity"
STRING_OR_NUMBER = {"oneOf": [{"type": "string"}, {"type": "number"}]}
STRING_OR_INTEGER = {"oneOf": [{"type": "string"}, {"type": "integer"}]}
USAGE = "usage: kube_schemas.py core SPEC OUTDIR | crds fetch|generate|check"

Schema = dict[str, Any]


def die(msg: str) -> None:
    sys.stderr.write(f"kube_schemas: {msg}\n")
    sys.exit(1)


def strict(node: Any) -> Any:  # noqa: ANN401
    """Refuse unknown fields: every object schema with properties gets additionalProperties false
    (kubectl's behaviour), unless it already says otherwise or preserves unknown fields.
    int-or-string formats become a oneOf, as kubeconform's own CRD converter does."""
    if isinstance(node, list):
        return [strict(x) for x in node]
    if not isinstance(node, dict):
        return node
    if node.get("format") == "int-or-string":
        return dict(STRING_OR_INTEGER)
    out = {
        k: {p: strict(s) for p, s in v.items()}
        if k == "properties" and isinstance(v, dict)
        else strict(v)
        for k, v in node.items()
    }
    if (
        isinstance(node.get("properties"), dict)
        and "additionalProperties" not in node
        and not node.get("x-kubernetes-preserve-unknown-fields")
    ):
        out["additionalProperties"] = False
    return out


def _inline(node: Any, defs: dict[str, Schema], stack: tuple[str, ...]) -> Any:  # noqa: ANN401
    """Replace every $ref with its definition; a recursive one (JSONSchemaProps) becomes {}."""
    if isinstance(node, list):
        return [_inline(x, defs, stack) for x in node]
    if not isinstance(node, dict):
        return node
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith(REF):
        name = ref.removeprefix(REF)
        if name in stack:
            return {}
        if name == QUANTITY:
            return dict(STRING_OR_NUMBER)
        target = _inline(copy.deepcopy(defs[name]), defs, (*stack, name))
        extra = {k: v for k, v in node.items() if k != "$ref"}
        return {**target, **extra}
    return {k: _inline(v, defs, stack) for k, v in node.items()}


def _nullable_optional(node: Any) -> Any:  # noqa: ANN401
    """An optional field may be null (helm renders `key: null`), as the upstream schemas allow."""
    if isinstance(node, list):
        return [_nullable_optional(x) for x in node]
    if not isinstance(node, dict):
        return node
    out = {k: _nullable_optional(v) for k, v in node.items() if k != "properties"}
    if isinstance(node.get("properties"), dict):
        required = set(node.get("required") or [])
        props = {}
        for key, value in node["properties"].items():
            prop = _nullable_optional(value)
            if key not in required and isinstance(prop.get("type"), str):
                prop = {**prop, "type": [prop["type"], "null"]}
            props[key] = prop
        out["properties"] = props
    return out


def core_file_name(group: str, version: str, kind: str) -> str:
    """kubeconform's default naming: <kind>[-<first group label>]-<version>.json, lower case."""
    suffix = f"-{group.split('.', maxsplit=1)[0]}" if group else ""
    return f"{kind}{suffix}-{version}.json".lower()


def core(spec_path: Path, out_dir: Path) -> int:
    defs: dict[str, Schema] = json.loads(spec_path.read_text(encoding="utf-8"))["definitions"]
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for name, definition in sorted(defs.items()):
        gvks = definition.get("x-kubernetes-group-version-kind") or []
        if not gvks:
            continue
        schema = strict(_nullable_optional(_inline(copy.deepcopy(definition), defs, (name,))))
        schema["$schema"] = "http://json-schema.org/schema#"
        for gvk in gvks:
            group, version, kind = gvk.get("group", ""), gvk["version"], gvk["kind"]
            path = out_dir / core_file_name(group, version, kind)
            path.write_text(json.dumps(schema, sort_keys=True), encoding="utf-8")
            count += 1
    sys.stdout.write(f"core: {count} schemas from {spec_path.name} in {out_dir}\n")
    return 0


def read_sources() -> list[list[str]]:
    rows = []
    for line in SOURCES.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != SOURCE_FIELDS or not all(fields):
            die(f"malformed source (need {SOURCE_FIELDS} tab-separated fields): {line}")
        rows.append(fields)
    return rows


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def cached(sha: str) -> bytes:
    path = CACHE / sha
    if not path.is_file():
        die(f"{path} is missing: run `scripts/tools.sh install crd-sources` first")
    data = path.read_bytes()
    if _sha256(data) != sha:
        die(f"{path}: checksum mismatch; delete it and fetch again")
    return data


def fetch() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    for name, version, url, sha, _kinds in read_sources():
        if (CACHE / sha).is_file() and _sha256((CACHE / sha).read_bytes()) == sha:
            continue
        if not url.startswith("https://"):
            die(f"{name}: only https sources are fetched: {url}")
        with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 (https only, above)
            data = response.read()
        got = _sha256(data)
        if got != sha:
            die(f"{name} {version}: checksum mismatch for {url}: expected {sha}, got {got}")
        with tempfile.NamedTemporaryFile(dir=CACHE, delete=False) as part:
            part.write(data)
        Path(part.name).replace(CACHE / sha)
        sys.stdout.write(f"fetched {name} {version}\n")
    return 0


def crd_schemas() -> dict[str, str]:
    """Relative path -> file content, for every served version of every listed kind."""
    import yaml  # noqa: PLC0415 (only the CRD steps need PyYAML)

    class Loader(yaml.SafeLoader):
        """SafeLoader, but a bare `=` (a YAML 1.1 value tag, in Gateway API enums) is a string."""

    Loader.add_constructor(
        "tag:yaml.org,2002:value", lambda loader, node: loader.construct_scalar(node)
    )
    out: dict[str, str] = {}
    for name, version, _url, sha, kinds in read_sources():
        wanted = {k.strip() for k in kinds.split(",")}
        seen = set()
        for doc in yaml.load_all(cached(sha), Loader=Loader):
            if not doc or doc.get("kind") != "CustomResourceDefinition":
                continue
            spec = doc["spec"]
            kind = spec["names"]["kind"]
            if kind not in wanted:
                continue
            seen.add(kind)
            for ver in spec["versions"]:
                if not ver.get("served"):
                    continue
                schema = strict(ver["schema"]["openAPIV3Schema"])
                # The root keeps unknown fields out too (kind, apiVersion, metadata, spec, status).
                rel = f"{spec['group']}/{kind.lower()}_{ver['name']}.json"
                out[rel] = json.dumps(schema, indent=2, sort_keys=True) + "\n"
        if missing := wanted - seen:
            die(f"{name} {version}: no CRD for {', '.join(sorted(missing))}")
    return out


def generate(*, check: bool) -> int:
    want = crd_schemas()
    have = {
        str(p.relative_to(CRD_DIR)): p.read_text(encoding="utf-8")
        for p in sorted(CRD_DIR.glob("*/*.json"))
    }
    if check:
        diff = sorted(set(want) ^ set(have) | {k for k in want if have.get(k) != want[k]})
        for rel in diff:
            sys.stderr.write(f"differs from the pinned sources: {rel}\n")
        if diff:
            sys.stderr.write("run `make crd-schemas` and commit the result\n")
            return 1
        sys.stdout.write(f"crds: {len(want)} schemas match the pinned sources\n")
        return 0
    for rel in set(have) - set(want):
        (CRD_DIR / rel).unlink()
    for rel, text in want.items():
        (CRD_DIR / rel).parent.mkdir(parents=True, exist_ok=True)
        (CRD_DIR / rel).write_text(text, encoding="utf-8")
    sys.stdout.write(f"crds: wrote {len(want)} schemas to {CRD_DIR}\n")
    return 0


def main(argv: list[str]) -> int:
    match argv:
        case ["core", spec, out]:
            return core(Path(spec), Path(out))
        case ["crds", "fetch"]:
            return fetch()
        case ["crds", "generate"]:
            return generate(check=False)
        case ["crds", "check"]:
            return generate(check=True)
    sys.stderr.write(USAGE + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
