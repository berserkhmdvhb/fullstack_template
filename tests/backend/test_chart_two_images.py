# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The chart ships two workloads, backend and frontend: the Next.js server serves its own assets."""

from __future__ import annotations

import pytest

from tests.backend.test_chart_opt_in_features import ENVS, _docs, helm  # noqa: F401  (fixture)

STATIC_TIER_PORT = 8081
APP_COMPONENTS = {"backend", "frontend"}


@pytest.mark.parametrize("env", ENVS)
def test_the_app_runs_as_backend_and_frontend_only(helm: str, env: str) -> None:  # noqa: F811
    docs = _docs(helm, env=env)
    assert [d["metadata"]["name"] for d in docs if "nginx" in d["metadata"]["name"]] == []
    components = {
        d["metadata"]["labels"]["app.kubernetes.io/component"]
        for d in docs
        if d["kind"] == "Deployment"
    }
    assert components >= APP_COMPONENTS
    assert not {c for c in components if "nginx" in c or c == "static"}


@pytest.mark.parametrize("env", ENVS)
def test_the_ingress_sends_every_ui_path_to_the_frontend_and_nothing_to_a_static_port(
    helm: str,  # noqa: F811
    env: str,
) -> None:
    ingress = next(d for d in _docs(helm, env=env) if d["kind"] == "Ingress")
    backends = [
        (p["path"], p["backend"]["service"]["name"], p["backend"]["service"]["port"]["number"])
        for rule in ingress["spec"]["rules"]
        for p in rule["http"]["paths"]
    ]
    assert STATIC_TIER_PORT not in {port for _, _, port in backends}
    assert not [b for b in backends if b[1].endswith("-nginx")]
    assert ("/", "svc-your-service-name-frontend", 3000) in backends


@pytest.mark.parametrize("env", ENVS)
def test_no_network_policy_opens_the_static_tier_port(helm: str, env: str) -> None:  # noqa: F811
    for doc in _docs(helm, env=env):
        if doc["kind"] != "NetworkPolicy":
            continue
        ports = [
            p.get("port")
            for rule in doc["spec"].get("ingress", []) + doc["spec"].get("egress", [])
            for p in rule.get("ports", [])
        ]
        assert STATIC_TIER_PORT not in ports, doc["metadata"]["name"]
