# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The backend's keep-alive must outlive the gateway's upstream idle timeout.

If Uvicorn closes an idle connection first, the proxy can reuse it in the same instant and
return a 502. The launcher owns the backend value; the chart owns the Envoy (WAF) one.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, cast

import pytest
import yaml

from tests.backend.test_chart_production_guard import CHART, ROOT, find_helm, require_helm
from your_service_name.backend import serve


def _backend_keepalive() -> int:
    """The launcher's default keep-alive (what the image runs when the chart sets nothing)."""
    return int(serve.build_options({})["timeout_keep_alive"])


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((CHART / name).read_text(encoding="utf-8")) or {}


def _gateway_idle(env: str | None = None) -> int:
    """The Envoy upstream idle timeout: values.yaml, or the override in values.<env>.yaml."""
    waf = {
        **_load("values.yaml")["waf"],
        **(_load(f"values.{env}.yaml").get("waf", {}) if env else {}),
    }
    idle = waf["upstreamIdleTimeoutSeconds"]
    assert isinstance(idle, int)
    return idle


@pytest.mark.parametrize("env", [None, "dev", "qa", "production", "local"])
def test_the_gateway_idle_timeout_is_shorter_than_the_backend_keep_alive(env: str | None) -> None:
    """Checked for the default and for every values file, which may override the chart default."""
    assert _gateway_idle(env) < _backend_keepalive()


@pytest.mark.parametrize("env", ["dev", "qa", "production", "local"])
def test_no_environment_sets_a_keep_alive_below_the_gateway_timeout(env: str) -> None:
    """A BACKEND_KEEPALIVE_TIMEOUT in any values file still has to clear the gateway's."""
    config = {**_load("values.yaml")["config"], **_load(f"values.{env}.yaml").get("config", {})}
    assert int(config.get("BACKEND_KEEPALIVE_TIMEOUT", _backend_keepalive())) > _gateway_idle(env)


def test_the_rendered_envoy_cluster_carries_the_idle_timeout() -> None:
    helm = require_helm(find_helm(), ci=bool(os.environ.get("CI")))
    out = subprocess.run(
        [helm, "template", "svc", str(CHART), "--set", "waf.enabled=true"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    assert f"idle_timeout: {_gateway_idle()}s" in out.stdout


def test_the_envoy_config_leaves_the_trusted_hops_at_the_default() -> None:
    """``xff_num_trusted_hops`` also picks Envoy's trusted client address (the (N+1)th XFF entry
    from the right). The ingress writes one entry, so N=1 would make Envoy fall back to the ingress
    Pod and hide the client from Coraza and Envoy's logs. Do not add it without that trade-off."""
    helm = require_helm(find_helm(), ci=bool(os.environ.get("CI")))
    out = subprocess.run(
        [helm, "template", "svc", str(CHART), "--set", "waf.enabled=true"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    assert "http_connection_manager" in out.stdout
    assert not re.search(r"xff_num_trusted_hops:\s*[1-9]", out.stdout)


WRAP_WIDTH = 100


def test_no_line_of_the_values_file_runs_past_the_wrap_width() -> None:
    lines = (CHART / "values.yaml").read_text(encoding="utf-8").splitlines()
    assert [n for n, ln in enumerate(lines, 1) if len(ln) > WRAP_WIDTH] == []


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PREFIXES = {"/api", "/docs", "/openapi.json"}


def _envoy_config() -> dict[str, Any]:
    """The rendered WAF Envoy's bootstrap (passed in --config-yaml)."""
    helm = require_helm(find_helm(), ci=bool(os.environ.get("CI")))
    out = subprocess.run(
        [helm, "template", "svc", str(CHART), "--set", "waf.enabled=true"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, out.stderr
    waf = next(
        d
        for d in yaml.safe_load_all(out.stdout)
        if d and d["kind"] == "Deployment" and d["metadata"]["name"].endswith("-waf")
    )
    args = waf["spec"]["template"]["spec"]["containers"][0]["args"]
    config: dict[str, Any] = yaml.safe_load(args[args.index("--config-yaml") + 1])
    return config


def _hcm(config: dict[str, Any]) -> dict[str, Any]:
    hcm: dict[str, Any] = config["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][
        0
    ]["typed_config"]
    return hcm


def _clusters(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["name"]: c for c in config["static_resources"]["clusters"]}


def _http_options(cluster: dict[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = cluster["typed_extension_protocol_options"][
        "envoy.extensions.upstreams.http.v3.HttpProtocolOptions"
    ]["common_http_protocol_options"]
    return options


def test_a_reset_upstream_connection_is_retried_only_where_it_is_safe() -> None:
    """A backend Pod closes its idle keep-alive connections on SIGTERM, and Envoy can have just
    written a request to one (503, UC). A safe method is resent on a reset; any other method goes
    to a cluster that never reuses a connection, and is resent only when nothing was sent yet."""
    vhost = _hcm(_envoy_config())["route_config"]["virtual_hosts"][0]
    assert vhost["retry_policy"]["retry_on"] == "reset-before-request"
    safe, other = {}, {}
    for route in vhost["routes"]:
        match, action = route["match"], route["route"]
        # Coraza and the buffer run for every route: nothing switches a filter off per route.
        assert "typed_per_filter_config" not in route
        assert "typed_per_filter_config" not in action
        if "headers" in match:
            (header,) = match["headers"]
            assert header["name"] == ":method"
            assert set(header["string_match"]["safe_regex"]["regex"].split("|")) == SAFE_METHODS
            safe[match["prefix"]] = action
        else:
            # No route-level policy: the virtual host's (before the request was sent) applies.
            assert "retry_policy" not in action, route
            other[match["prefix"]] = action
    assert set(safe) == set(other) == PREFIXES
    assert all(a["cluster"] == "upstream" for a in safe.values()), safe
    assert all(a["retry_policy"]["retry_on"] == "reset" for a in safe.values()), safe
    assert all(a["cluster"] == "upstream_writes" for a in other.values()), other
    # Envoy takes the first matching route: the method-restricted ones must come first.
    first_plain = next(i for i, r in enumerate(vhost["routes"]) if "headers" not in r["match"])
    assert all("headers" in r["match"] for r in vhost["routes"][:first_plain])
    assert len(vhost["routes"]) == 2 * len(PREFIXES)


def test_the_write_cluster_never_reuses_a_connection() -> None:
    """The two clusters reach the same upstream with the same idle timeout; only the write one
    limits a connection to one request, so a write never lands on a pooled connection."""
    clusters = _clusters(_envoy_config())
    assert set(clusters) == {"upstream", "upstream_writes"}
    pool, writes = clusters["upstream"], clusters["upstream_writes"]
    assert pool["load_assignment"]["endpoints"] == writes["load_assignment"]["endpoints"]
    assert _http_options(pool) == {"idle_timeout": f"{_gateway_idle()}s"}
    assert _http_options(writes) == {
        "idle_timeout": f"{_gateway_idle()}s",
        "max_requests_per_connection": 1,
    }


def test_the_waf_filters_run_before_the_router_for_every_route() -> None:
    names = [f["name"] for f in _hcm(_envoy_config())["http_filters"]]
    assert names == [f"envoy.filters.http.{n}" for n in ("wasm", "buffer", "router")]


def test_envoy_adds_no_server_or_x_envoy_headers_of_its_own() -> None:
    """PASS_THROUGH: Envoy keeps the backend's `server` header as it is and adds none when the
    backend sends none; the router sends no x-envoy-* headers (x-envoy-upstream-service-time)."""
    hcm = _hcm(_envoy_config())
    assert hcm["server_header_transformation"] == "PASS_THROUGH"
    router = next(f for f in hcm["http_filters"] if f["name"] == "envoy.filters.http.router")
    assert router["typed_config"]["suppress_envoy_headers"] is True


def _directives(config: dict[str, Any]) -> list[str]:
    wasm = _hcm(config)["http_filters"][0]["typed_config"]["config"]["configuration"]["value"]
    parsed = json.loads(wasm)
    return cast("list[str]", parsed["directives_map"][parsed["default_directives"]])


def test_crs_allows_the_configured_methods_between_its_setup_and_its_rules() -> None:
    directives = _directives(_envoy_config())
    methods = " ".join(_load("values.yaml")["waf"]["allowedMethods"])
    setvar = f"setvar:'tx.allowed_methods={methods}'"
    action = f'SecAction "id:900200,phase:1,pass,t:none,nolog,{setvar}"'
    setup = directives.index("Include @crs-setup-conf")
    assert directives[setup + 1 : setup + 3] == [action, "Include @owasp_crs/*.conf"]


def test_the_waf_allows_exactly_the_apis_methods_plus_head_and_options() -> None:
    """The API's methods are the committed OpenAPI spec's (make openapi-drift-check)."""
    spec = json.loads((ROOT / "frontend" / "lib" / "api" / "openapi.json").read_text("utf-8"))
    served = {m.upper() for path in spec["paths"].values() for m in path}
    allowed = _load("values.yaml")["waf"]["allowedMethods"]
    assert len(allowed) == len(set(allowed))
    assert set(allowed) == served | {"HEAD", "OPTIONS"}
    assert not {"TRACE", "CONNECT"} & set(allowed)
