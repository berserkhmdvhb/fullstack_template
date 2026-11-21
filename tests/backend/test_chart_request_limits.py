# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The chart renders ALLOWED_HOSTS, and the body limit default is the ingress limit."""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any

import pytest
import yaml
from pydantic import SecretStr

from tests.backend.test_chart_production_guard import CHART, find_helm, require_helm
from your_service_name.backend.settings import (
    DEFAULT_MAX_REQUEST_BODY_BYTES,
    Settings,
)

ENVS = ["dev", "qa", "local", "production"]
NAMESPACE = "team-ns"
RELEASE = "svc"
BACKEND_SERVICE = f"{RELEASE}-your-service-name-backend"
_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in Settings.model_fields:
        monkeypatch.delenv(name, raising=False)


def rendered_config(env: str, *extra: str, release: str = RELEASE) -> dict[str, str]:
    """The backend ConfigMap data helm renders for ``values.<env>.yaml``."""
    helm = require_helm(find_helm(), ci=bool(os.environ.get("CI")))
    out = subprocess.run(
        [
            helm,
            "template",
            release,
            str(CHART),
            "-n",
            NAMESPACE,
            "-f",
            str(CHART / f"values.{env}.yaml"),
            "-s",
            "templates/configmap.yaml",
            *extra,
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    data: dict[str, str] = yaml.safe_load(out)["data"]
    return data


def _values(env: str) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load((CHART / f"values.{env}.yaml").read_text("utf-8")) or {}
    return loaded


@pytest.mark.parametrize("env", ENVS)
def test_allowed_hosts_are_the_ingress_hosts_and_the_backend_service_names(env: str) -> None:
    rendered = rendered_config(env)["ALLOWED_HOSTS"].split(",")
    base = yaml.safe_load((CHART / "values.yaml").read_text("utf-8"))["ingress"]["hosts"]
    hosts = {**base, **_values(env).get("ingress", {}).get("hosts", {})}
    extra = _values(env).get("backend", {}).get("extraAllowedHosts", "")
    assert rendered == [
        hosts["api"],
        hosts["app"],
        hosts["root"],
        BACKEND_SERVICE,
        f"{BACKEND_SERVICE}.{NAMESPACE}",
        f"{BACKEND_SERVICE}.{NAMESPACE}.svc",
        f"{BACKEND_SERVICE}.{NAMESPACE}.svc.cluster.local",
        *[h for h in extra.split(",") if h],
    ]
    assert "*" not in rendered


@pytest.mark.parametrize("env", ENVS)
def test_the_frontends_server_side_url_host_is_allowed(env: str) -> None:
    """BACKEND_URL (frontend -> backend by Service name) must pass the host check."""
    config = rendered_config(env)
    host = re.sub(r"^https?://", "", config["BACKEND_URL"]).split(":")[0]
    assert host in config["ALLOWED_HOSTS"].split(",")


def test_a_values_override_takes_over_the_list() -> None:
    config = rendered_config("dev", "--set", "config.ALLOWED_HOSTS=only.example.com")
    assert config["ALLOWED_HOSTS"] == "only.example.com"


def test_the_production_render_passes_the_production_validator_once_auth_is_chosen() -> None:
    config = rendered_config("production")
    kwargs: dict[str, Any] = {k: v for k, v in config.items() if k in Settings.model_fields}
    kwargs |= {"AUTH_MODE": "gateway", "DATABASE_URL": SecretStr("postgresql://u:p@db.example/x")}
    settings = Settings(**kwargs)
    assert settings.is_production
    assert "*" not in settings.ALLOWED_HOSTS


def _bytes(size: str) -> int:
    match = re.fullmatch(r"(\d+)([kKmMgG]?)", size)
    assert match, size
    return int(match[1]) * _UNITS[match[2].lower()]


def test_the_body_limit_default_is_the_ingress_body_size() -> None:
    """One fact with a checked copy: nginx `proxy-body-size` (m = MiB) and the setting default."""
    key = "nginx.ingress.kubernetes.io/proxy-body-size"
    sizes = {}
    for path in sorted(CHART.glob("values*.yaml")):
        loaded = yaml.safe_load(path.read_text("utf-8")) or {}
        value = ((loaded.get("ingress") or {}).get("annotations") or {}).get(key)
        if value is not None:
            sizes[path.name] = _bytes(str(value))
    assert sizes, "the chart no longer sets proxy-body-size"
    assert set(sizes.values()) == {DEFAULT_MAX_REQUEST_BODY_BYTES}, sizes
    assert Settings().MAX_REQUEST_BODY_BYTES == DEFAULT_MAX_REQUEST_BODY_BYTES


def test_extra_allowed_hosts_are_appended_for_local_clusters_only() -> None:
    local = rendered_config("local")["ALLOWED_HOSTS"].split(",")
    assert local[-2:] == ["localhost", "127.0.0.1"]
    for env in ("dev", "qa", "production"):
        hosts = rendered_config(env)["ALLOWED_HOSTS"].split(",")
        assert "localhost" not in hosts
        assert "127.0.0.1" not in hosts


def test_extra_allowed_hosts_value_is_appended_after_the_service_names() -> None:
    config = rendered_config("production", "--set", "backend.extraAllowedHosts=x.example.com")
    hosts = config["ALLOWED_HOSTS"].split(",")
    assert hosts[-1] == "x.example.com"
    assert hosts[:3] == [
        "api.your-domain.example",
        "app.your-domain.example",
        "your-service-name.your-domain.example",
    ]
