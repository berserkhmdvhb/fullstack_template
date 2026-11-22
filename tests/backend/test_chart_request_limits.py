# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The chart renders ALLOWED_HOSTS; the body limit lives in the backend, not at the edge."""

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
def test_allowed_hosts_are_the_edge_hosts_and_the_backend_service_names(env: str) -> None:
    rendered = rendered_config(env)["ALLOWED_HOSTS"].split(",")
    base = yaml.safe_load((CHART / "values.yaml").read_text("utf-8"))["edge"]["hosts"]
    hosts = {**base, **_values(env).get("edge", {}).get("hosts", {})}
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
    kwargs |= {
        "AUTH_MODE": "gateway",
        "AUTH_PROXY_CIDRS": "10.20.0.0/16",
        "DATABASE_URL": SecretStr("postgresql://u:p@db.example/x"),
    }
    settings = Settings(**kwargs)
    assert settings.is_production
    assert "*" not in settings.ALLOWED_HOSTS


def test_the_body_limit_lives_in_the_backend_and_no_values_file_sets_an_edge_annotation() -> None:
    """The nginx annotations are gone: the 10 MiB body limit is the backend setting's default."""
    for path in sorted(CHART.glob("values*.yaml")):
        assert "nginx.ingress.kubernetes.io" not in path.read_text("utf-8"), path.name
    assert Settings().MAX_REQUEST_BODY_BYTES == DEFAULT_MAX_REQUEST_BODY_BYTES == 10 * 1024**2


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


@pytest.mark.parametrize("env", ENVS)
def test_the_backend_and_the_waf_share_one_body_limit(env: str) -> None:
    """config.MAX_REQUEST_BODY_BYTES feeds the backend (ConfigMap) and the WAF's Coraza limits."""
    assert rendered_config(env)["MAX_REQUEST_BODY_BYTES"] == str(DEFAULT_MAX_REQUEST_BODY_BYTES)
