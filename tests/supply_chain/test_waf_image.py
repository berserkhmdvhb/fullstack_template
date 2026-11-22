"""Dockerfile.waf: Envoy plus the Coraza Wasm, every input pinned, nothing fetched at runtime.

The WAF image is built, scanned and signed like the two app images. Its base is the distroless
Envoy image by its index digest, the Coraza release asset is checked against a pinned sha256 in the
build, and the final stage only copies the Debian fixes and the Wasm in (no RUN, no shell). The
chart loads the module from the same local path the Dockerfile writes.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
DOCKERFILE = (ROOT / "Dockerfile.waf").read_text(encoding="utf-8")
WAF_TEMPLATE = (ROOT / "charts/your-service-name/templates/waf-proxy.yaml").read_text(
    encoding="utf-8"
)
MANIFEST = yaml.safe_load((ROOT / ".github/container-images.yml").read_text(encoding="utf-8"))
SECURITY_CONFIG = yaml.safe_load((ROOT / "infra/security/config.yaml").read_text(encoding="utf-8"))
DIGEST = r"@sha256:[0-9a-f]{64}"
# The base image's configured user (docker buildx imagetools inspect ... Config.User), distroless
# nonroot. The final stage names it with its group, and the chart runs the Pod as the same ids.
ENVOY_USER = "65532"


def _stages() -> list[str]:
    return re.split(r"^(?=FROM )", DOCKERFILE, flags=re.MULTILINE)[1:]


def _froms() -> dict[str, str]:
    """Stage name -> the image it starts FROM; every FROM names its stage."""
    froms = re.findall(r"^FROM (\S+) AS ([\w-]+)$", DOCKERFILE, re.MULTILINE)
    assert len(froms) == len(re.findall(r"^FROM ", DOCKERFILE, re.MULTILINE))
    return {name: image for image, name in froms}


def _arg(name: str) -> str:
    match = re.search(rf"^ARG {name}=(\S+)$", DOCKERFILE, re.MULTILINE)
    assert match, f"Dockerfile.waf must declare ARG {name}=<value>"
    return match.group(1)


def test_every_base_image_is_pinned_by_digest() -> None:
    froms = _froms()
    assert set(froms) == {"runtime-base", "debian-fixes", "wasm", "runtime"}
    assert froms.pop("runtime") == "runtime-base"
    for ref in froms.values():
        assert re.search(DIGEST + "$", ref), ref


def test_the_runtime_is_the_distroless_envoy_image() -> None:
    assert re.fullmatch(
        r"envoyproxy/envoy:distroless-v\d+\.\d+\.\d+" + DIGEST, _froms()["runtime-base"]
    )
    assert _stages()[-1].startswith("FROM runtime-base AS runtime\n")
    assert DOCKERFILE.count("FROM envoyproxy/envoy:") == 1


# A value to bump, not derived: the oldest Envoy minor still supported. Envoy supports each minor
# for a year (https://github.com/envoyproxy/envoy/blob/main/RELEASES.md, "Release schedule");
# 1.36 to 1.39 in 2026-11. Raise it when a minor reaches end of life; Renovate moves the pin.
OLDEST_SUPPORTED_ENVOY_MINOR = 36


def test_the_envoy_base_is_a_supported_release() -> None:
    match = re.search(
        r"^FROM envoyproxy/envoy:distroless-v1\.(\d+)\.\d+@", DOCKERFILE, re.MULTILINE
    )
    assert match
    assert int(match.group(1)) >= OLDEST_SUPPORTED_ENVOY_MINOR


def test_the_builder_is_the_debian_image_the_backend_uses() -> None:
    backend = (ROOT / "Dockerfile.backend").read_text(encoding="utf-8")
    debian = re.search(r"^FROM (debian:13-slim" + DIGEST + ")", backend, re.MULTILINE)
    assert debian
    assert _froms()["wasm"] == debian.group(1)


def test_the_coraza_asset_is_a_pinned_release_checked_against_a_pinned_sha256() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", _arg("CORAZA_WASM_VERSION"))
    assert re.fullmatch(r"[0-9a-f]{64}", _arg("CORAZA_WASM_SHA256"))
    add = re.search(r"^ADD (.+)$", DOCKERFILE, re.MULTILINE)
    assert add
    assert "--checksum=sha256:${CORAZA_WASM_SHA256}" in add.group(1)
    assert (
        "https://github.com/corazawaf/coraza-proxy-wasm/releases/download/"
        "${CORAZA_WASM_VERSION}/coraza-proxy-wasm-${CORAZA_WASM_VERSION}.zip"
    ) in add.group(1)


def test_renovate_tracks_the_coraza_version() -> None:
    annotation = "# renovate: datasource=github-releases depName=corazawaf/coraza-proxy-wasm\n"
    assert annotation + "ARG CORAZA_WASM_VERSION=" in DOCKERFILE
    text = (ROOT / "renovate.json5").read_text(encoding="utf-8")
    config = json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE))
    hits = []
    for manager in config["customManagers"]:
        if not any(
            re.search(p.strip("/"), "Dockerfile.waf") for p in manager["managerFilePatterns"]
        ):
            continue
        for ms in manager["matchStrings"]:
            pattern = re.compile(re.sub(r"\(\?<([A-Za-z]+)>", r"(?P<\1>", ms))
            hits += [m.groupdict() for m in pattern.finditer(DOCKERFILE)]
    assert hits == [
        {
            "datasource": "github-releases",
            "depName": "corazawaf/coraza-proxy-wasm",
            "currentValue": _arg("CORAZA_WASM_VERSION"),
        }
    ]


def test_the_final_stage_only_copies_the_debian_fixes_and_the_wasm_and_runs_nothing() -> None:
    final = _stages()[-1]
    assert not re.search(r"^RUN ", final, re.MULTILINE)
    assert not re.search(r"^(ADD|SHELL) ", final, re.MULTILINE)
    copies = re.findall(r"^COPY .+$", final, re.MULTILINE)
    assert copies[0] == "COPY --from=debian-fixes /out/ /"
    assert len(copies) == 2  # noqa: PLR2004 (the Debian fixes, then the Wasm)
    assert copies[1].startswith("COPY --from=wasm ")
    # --chmod would also set the mode of the directory COPY creates; 0444 there leaves Envoy unable
    # to enter it ("Invalid path" at start-up). The builder sets the file's mode instead.
    assert "--chmod" not in copies[1]


def test_the_image_runs_as_the_base_images_numeric_user() -> None:
    assert re.search(rf"^USER {ENVOY_USER}:{ENVOY_USER}$", _stages()[-1], re.MULTILINE)
    assert f"runAsUser: {ENVOY_USER}" in WAF_TEMPLATE
    assert f"runAsGroup: {ENVOY_USER}" in WAF_TEMPLATE


def test_the_chart_loads_the_wasm_from_the_path_the_image_writes() -> None:
    copy = re.search(r"^COPY --from=wasm \S+ (\S+)$", _stages()[-1], re.MULTILINE)
    assert copy
    assert f"filename: {copy.group(1)}" in WAF_TEMPLATE
    assert "remote:" not in WAF_TEMPLATE
    assert "oci://" not in WAF_TEMPLATE


def test_the_manifest_builds_and_checks_the_waf_image() -> None:
    entry = next(i for i in MANIFEST["images"] if i["id"] == "waf")
    assert entry["dockerfile"] == "Dockerfile.waf"
    assert entry["distroless"] is True
    assert entry["platforms"] == ["linux/amd64", "linux/arm64"]
    assert "Kubernetes" in entry["healthcheck_exempt"]
    assert entry["build_args"] == {"VERSION": "${VERSION}"}


def test_the_waf_image_is_scanned_like_the_app_images() -> None:
    services = {s["name"]: s for s in SECURITY_CONFIG["image"]["services"]}
    assert services["waf"] == {
        "name": "waf",
        "env_var": "WAF_IMAGE",
        "docker_env_var": "WAF_IMAGE_DOCKER",
        "match": "waf",
    }
    assert set(services) == {i["id"] for i in MANIFEST["images"]}
