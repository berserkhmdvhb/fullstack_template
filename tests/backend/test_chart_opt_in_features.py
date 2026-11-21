# your-service-name — adaptable service template (see wiki/getting-started.md)
"""Chart wiring of the opt-in features: tracing, CSP reporting, extra backend egress."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any, ClassVar

import pytest
import yaml

from tests.backend.test_chart_production_guard import CHART, find_helm, require_helm

ENDPOINT = "http://collector.monitoring.svc:4318"
ENVS = ["dev", "qa", "local", "production"]
BACKEND = "svc-your-service-name-backend"
FRONTEND = "svc-your-service-name-frontend"
NEW_NAMES = ("TRACING_ENABLED", "OTEL_", "CSP_REPORT", "RATE_LIMIT_STORAGE")


@pytest.fixture(scope="module")
def helm() -> str:
    return require_helm(find_helm(), ci=bool(os.environ.get("CI")))


def _run(helm: str, *args: str, env: str = "dev") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [helm, "template", "svc", str(CHART), "-f", str(CHART / f"values.{env}.yaml"), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _docs(helm: str, *args: str, env: str = "dev") -> list[dict[str, Any]]:
    result = _run(helm, *args, env=env)
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def _find(docs: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    return next(d for d in docs if d["kind"] == kind and d["metadata"]["name"] == name)


def _env(docs: list[dict[str, Any]], deployment: str) -> dict[str, str]:
    spec = _find(docs, "Deployment", deployment)["spec"]["template"]["spec"]
    container = next(c for c in spec["containers"] if c["name"] != "wait-for-db")
    return {e["name"]: e.get("value", "") for e in container.get("env", [])}


def _backend_egress(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    policy = _find(docs, "NetworkPolicy", "svc-your-service-name-allow-backend-to-db")
    egress: list[dict[str, Any]] = policy["spec"]["egress"]
    return egress


@pytest.mark.parametrize("env", ENVS)
def test_off_renders_nothing_new(helm: str, env: str) -> None:
    result = _run(helm, env=env)
    assert result.returncode == 0, result.stderr
    for name in NEW_NAMES:
        assert name not in result.stdout, name
    docs = _docs(helm, env=env)
    ports = [p["port"] for rule in _backend_egress(docs) for p in rule["ports"]]
    assert ports[:1] == [5432]
    assert sorted(set(ports)) == [53, 5432]


class TestTracing:
    ARGS = (
        "--set",
        "observability.tracing.enabled=true",
        "--set",
        f"observability.tracing.endpoint={ENDPOINT}",
    )

    def test_env_on_the_backend_only(self, helm: str) -> None:
        docs = _docs(helm, *self.ARGS, "--set-json", "observability.tracing.sampleRatio=0.25")
        backend = _env(docs, BACKEND)
        assert backend["TRACING_ENABLED"] == "true"
        assert backend["OTEL_EXPORTER_OTLP_ENDPOINT"] == ENDPOINT
        assert backend["TRACING_SAMPLE_RATIO"] == "0.25"
        assert "OTEL_SERVICE_NAME" not in backend  # unset: the backend default
        assert "TRACING_ENABLED" not in _env(docs, FRONTEND)

    def test_service_name_is_passed_when_set(self, helm: str) -> None:
        docs = _docs(helm, *self.ARGS, "--set", "observability.tracing.serviceName=orders")
        assert _env(docs, BACKEND)["OTEL_SERVICE_NAME"] == "orders"

    def test_the_collector_headers_are_never_rendered(self, helm: str) -> None:
        """They come from the app Secret (envFrom), not from a chart value."""
        result = _run(helm, *self.ARGS)
        assert "OTEL_EXPORTER_OTLP_HEADERS" not in result.stdout

    def test_the_endpoint_is_required(self, helm: str) -> None:
        result = _run(helm, "--set", "observability.tracing.enabled=true")
        assert result.returncode != 0
        assert "observability.tracing.endpoint" in result.stderr

    @pytest.mark.parametrize("ratio", ["2", "-1"])
    def test_the_ratio_is_a_share(self, helm: str, ratio: str) -> None:
        result = _run(helm, *self.ARGS, "--set-json", f"observability.tracing.sampleRatio={ratio}")
        assert result.returncode != 0


class TestCspReporting:
    def test_one_value_two_env_vars(self, helm: str) -> None:
        docs = _docs(helm, "--set", "cspReporting.enabled=true")
        assert _env(docs, BACKEND)["CSP_REPORT_ENABLED"] == "true"
        assert _env(docs, FRONTEND)["CSP_REPORTING_ENABLED"] == "true"

    def test_off_sets_neither(self, helm: str) -> None:
        docs = _docs(helm, "--set", "cspReporting.enabled=false")
        assert "CSP_REPORT_ENABLED" not in _env(docs, BACKEND)
        assert "CSP_REPORTING_ENABLED" not in _env(docs, FRONTEND)


class TestExtraEgress:
    COLLECTOR: ClassVar[dict[str, Any]] = {
        "to": [
            {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "monitoring"}}}
        ],
        "ports": [{"protocol": "TCP", "port": 4318}],
    }
    REDIS: ClassVar[dict[str, Any]] = {
        "to": [{"ipBlock": {"cidr": "10.20.0.0/24"}}],
        "ports": [{"protocol": "TCP", "port": 6379}],
    }

    def test_rules_are_appended_after_the_defaults(self, helm: str) -> None:
        base = _backend_egress(_docs(helm))
        docs = _docs(
            helm,
            "--set-json",
            "backend.networkPolicy.extraEgress=" + json.dumps([self.COLLECTOR, self.REDIS]),
        )
        egress = _backend_egress(docs)
        assert egress[: len(base)] == base
        assert [p["port"] for rule in egress[len(base) :] for p in rule["ports"]] == [4318, 6379]
        assert egress[-1]["to"] == [{"ipBlock": {"cidr": "10.20.0.0/24"}}]

    def test_the_default_is_an_empty_list(self) -> None:
        values = yaml.safe_load((CHART / "values.yaml").read_text("utf-8"))
        assert values["backend"]["networkPolicy"]["extraEgress"] == []

    def test_it_must_be_a_list(self, helm: str) -> None:
        result = _run(helm, "--set-json", 'backend.networkPolicy.extraEgress={"to": []}')
        assert result.returncode != 0


@pytest.mark.parametrize("name", ["RATE_LIMIT_STORAGE", "OTEL_EXPORTER_OTLP_HEADERS"])
def test_secret_bearing_settings_are_refused_as_plain_config(helm: str, name: str) -> None:
    result = _run(helm, "--set", f"config.{name}=redis://:x@cache:6379")
    assert result.returncode != 0
    assert "Secret" in result.stderr
    assert "redis://:x@cache" not in result.stderr


@pytest.mark.parametrize("env", ENVS)
def test_every_values_file_lints_with_the_features_on(helm: str, env: str) -> None:
    result = _run(
        helm,
        "--set",
        "observability.tracing.enabled=true",
        "--set",
        f"observability.tracing.endpoint={ENDPOINT}",
        "--set",
        "cspReporting.enabled=true",
        env=env,
    )
    assert result.returncode == 0, result.stderr
