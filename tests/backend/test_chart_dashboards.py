# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The chart's Grafana dashboards (charts/your-service-name/dashboards/*.json).

Each parses, has a stable unique uid, takes its datasource from the `datasource` variable, carries
no exported-dashboard noise, and queries only series the backend serves (the runbook's "Prometheus
Metrics" list) or the chart records (the rendered recording rules). Every expression, with the
Grafana variables substituted, must pass the pinned `promtool check rules`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.backend.test_chart_pod_security import render
from tests.backend.test_chart_production_guard import CHART, ROOT, find_helm, require_helm

DASHBOARDS = CHART / "dashboards"
RUNBOOK = ROOT / "wiki" / "operations-runbook.md"
PROMTOOL = ROOT / ".tools" / "bin" / "promtool"
UIDS = {
    "database-pool.json": "your-service-name-db-pool",
    "rate-limiting.json": "your-service-name-rate-limit",
    "service-red.json": "your-service-name-red",
    "slo.json": "your-service-name-slo",
}
DATASOURCE = {"type": "prometheus", "uid": "${datasource}"}
NOISE = {"id", "version", "iteration"}
KEYWORDS = {"and", "or", "unless", "bool", "offset", "group_left", "group_right", "inf", "nan"}
GROUPING = r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)"


def _slo() -> dict[str, Any]:
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    slo: dict[str, Any] = values["monitoring"]["slo"]
    return slo


def _grafana_vars() -> dict[str, str]:
    """What each Grafana variable becomes for promtool; the constants come from values.yaml."""
    slo = _slo()
    return {
        "__rate_interval": "5m",
        "__interval": "1m",
        "__range": "1h",
        "namespace": "app",
        "exclude_handlers": slo["excludeHandlers"],
        "availability_objective": str(slo["availability"]["objective"]),
        "latency_objective": str(slo["latency"]["objective"]),
    }


def _load(directory: Path) -> dict[str, dict[str, Any]]:
    return {
        p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))
    }


def _panels(dashboard: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for p in dashboard.get("panels", []):
        yield p
        yield from p.get("panels", [])


def _exprs(dashboards: dict[str, dict[str, Any]]) -> list[tuple[str, str]]:
    return [
        (f"{name}: {p['title']}", t["expr"])
        for name, d in dashboards.items()
        for p in _panels(d)
        for t in p.get("targets", [])
    ]


def _variables(dashboard: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {v["name"]: v for v in dashboard["templating"]["list"]}


def _substitute(expr: str) -> str:
    out = expr
    for name, value in sorted(_grafana_vars().items(), key=lambda kv: -len(kv[0])):
        out = out.replace("${" + name + "}", value).replace("$" + name, value)
    return out


def _allowed() -> tuple[set[str], tuple[str, ...]]:
    """The served names (the runbook's list, each histogram's and summary's series) and prefixes."""
    text = RUNBOOK.read_text(encoding="utf-8")
    section = text.split("### Prometheus Metrics", 1)[1].split("\n### ", 1)[0]
    names: set[str] = set()
    prefixes: list[str] = []
    for line in section.splitlines():
        if not line.startswith("- `"):
            continue
        head, _, description = line.partition(" — ")
        for token in re.findall(r"`([^`]+)`", head):
            name = token.split("{", 1)[0]
            if name.endswith("_*"):
                prefixes.append(name[:-1])
            elif "histogram" in description:
                names |= {f"{name}_bucket", f"{name}_count", f"{name}_sum"}
            elif "Summaries" in description:
                names |= {f"{name}_count", f"{name}_sum"}
            else:
                names.add(name)
    return names, tuple(prefixes)


def _recorded(helm: str) -> set[str]:
    docs = render(helm, "values.yaml", "--set", "monitoring.prometheusRule=true")
    return {
        r["record"]
        for d in docs
        if d["kind"] == "PrometheusRule"
        for g in d["spec"]["groups"]
        for r in g["rules"]
        if "record" in r
    }


def metric_names(expr: str) -> set[str]:
    """The metric and recording-rule names a PromQL expression selects."""
    bare = re.sub(r'"(?:[^"\\]|\\.)*"', '""', expr)
    bare = re.sub(r"\{[^}]*\}|\[[^\]]*\]", " ", bare)
    bare = re.sub(GROUPING, " ", bare)
    found = re.findall(r"(?<![\w:.$])([a-zA-Z_:][\w:]*)(?![\w:]|\s*\()", bare)
    return {n for n in found if n.lower() not in KEYWORDS}


def check(directory: Path, helm: str, work: Path) -> list[str]:
    """Every problem with the dashboards in ``directory``: unknown names, then promtool's."""
    dashboards = _load(directory)
    served, prefixes = _allowed()
    known = served | _recorded(helm)
    errors = []
    exprs = _exprs(dashboards)
    for name, d in dashboards.items():
        query = str(_variables(d).get("namespace", {}).get("query", ""))
        exprs += [
            (f"{name}: namespace variable", m) for m in re.findall(r"label_values\((\w+)", query)
        ]
    for where, expr in exprs:
        unknown = {n for n in metric_names(expr) if n not in known and not n.startswith(prefixes)}
        if unknown:
            errors.append(f"{where}: neither served nor recorded: {sorted(unknown)}")
        if re.search(r"\$\{?\w", _substitute(expr)):
            errors.append(f"{where}: an unknown Grafana variable in {expr!r}")
    rules = {
        "groups": [
            {
                "name": "dashboards",
                "rules": [
                    {"record": f"dashboard:expr:{i}", "expr": _substitute(e)}
                    for i, (_, e) in enumerate(exprs)
                ],
            }
        ]
    }
    path = work / "dashboard-rules.yaml"
    path.write_text(yaml.safe_dump(rules), encoding="utf-8")
    result = subprocess.run(
        [str(PROMTOOL), "check", "rules", str(path)], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        errors.append(f"promtool check rules: {result.stdout}{result.stderr}")
    return errors


@pytest.fixture(scope="module")
def helm() -> str:
    return require_helm(find_helm(), ci=bool(os.environ.get("CI")))


@pytest.fixture(scope="module")
def promtool() -> None:
    if not PROMTOOL.exists():
        msg = "promtool is not installed (scripts/tools.sh install promtool)"
        if os.environ.get("CI"):
            pytest.fail(msg)
        pytest.skip(msg)


def test_the_dashboards_parse_with_their_stable_uids() -> None:
    dashboards = _load(DASHBOARDS)
    assert {n: d["uid"] for n, d in dashboards.items()} == UIDS
    for name, d in dashboards.items():
        assert d["title"].startswith("your-service-name / "), name


def _keys(node: object) -> Iterator[str]:
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from _keys(v)


@pytest.mark.parametrize("name", sorted(UIDS))
def test_each_dashboard_is_hand_readable_and_takes_the_datasource_variable(name: str) -> None:
    d = _load(DASHBOARDS)[name]
    assert not NOISE & set(_keys(d)), f"exported-dashboard noise: {NOISE & set(_keys(d))}"
    variables = _variables(d)
    assert variables["datasource"]["type"] == "datasource"
    assert variables["datasource"]["query"] == "prometheus"
    assert variables["namespace"]["datasource"] == DATASOURCE
    panels = list(_panels(d))
    assert panels
    for p in panels:
        assert p["datasource"] == DATASOURCE, p["title"]
        assert p.get("description"), p["title"]
        assert p["fieldConfig"]["defaults"].get("unit"), p["title"]
        assert p["targets"], p["title"]
        for t in p["targets"]:
            assert t["datasource"] == DATASOURCE, p["title"]
            assert 'namespace="$namespace"' in t["expr"] or not metric_names(t["expr"]), t["expr"]
            # A rate's window is Grafana's; only the 30-day budget averages a fixed range.
            for window in re.findall(r"\[([^\]]+)\]", t["expr"]):
                assert window == "$__rate_interval" or (
                    window == "30d" and "avg_over_time" in t["expr"]
                )


def test_the_constants_match_the_charts_slo_values() -> None:
    """The dashboards cannot read values.yaml: their constants must say what the chart does."""
    expected = _grafana_vars()
    for name, d in _load(DASHBOARDS).items():
        for var in _variables(d).values():
            if var["type"] == "constant":
                assert var["query"] == expected[var["name"]], f"{name}: {var['name']}"


def test_metric_names_extracts_the_selected_series_only() -> None:
    expr = (
        'histogram_quantile(0.5, sum by (le, handler) (rate(a_bucket{x="b c"}[5m]))) '
        "/ on (pod) group_left b and slo:c:d > bool 1"
    )
    assert metric_names(expr) == {"a_bucket", "b", "slo:c:d"}


@pytest.mark.usefixtures("promtool")
def test_every_query_is_served_or_recorded_and_valid_promql(helm: str, tmp_path: Path) -> None:
    assert _exprs(_load(DASHBOARDS))
    assert check(DASHBOARDS, helm, tmp_path) == []


def _broken(tmp: Path, old: str, new: str) -> Path:
    copy = tmp / "dashboards"
    shutil.copytree(DASHBOARDS, copy)
    red = copy / "service-red.json"
    text = red.read_text(encoding="utf-8")
    assert old in text
    red.write_text(text.replace(old, new, 1), encoding="utf-8")
    return copy


@pytest.mark.usefixtures("promtool")
def test_a_syntax_error_fails_promtool(helm: str, tmp_path: Path) -> None:
    copy = _broken(tmp_path, "sum(rate(", "sum(rate((")
    errors = check(copy, helm, tmp_path)
    assert any(e.startswith("promtool check rules") for e in errors), errors


@pytest.mark.usefixtures("promtool")
def test_a_metric_the_backend_does_not_serve_fails(helm: str, tmp_path: Path) -> None:
    copy = _broken(tmp_path, "http_requests_total{", "http_request_total{")
    errors = check(copy, helm, tmp_path)
    assert any("http_request_total" in e for e in errors), errors


def test_the_configmap_refuses_dashboards_over_the_size_limit(helm: str, tmp_path: Path) -> None:
    chart = tmp_path / "chart"
    shutil.copytree(CHART, chart)
    (chart / "dashboards" / "huge.json").write_text("{}" + " " * (1024 * 1024), encoding="utf-8")
    result = subprocess.run(
        [helm, "template", "svc", str(chart), "--set", "monitoring.dashboards.enabled=true"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "1 MiB" in result.stderr


def test_the_configmap_holds_every_dashboard_file_verbatim(helm: str) -> None:
    docs = render(helm, "values.yaml", "--set", "monitoring.dashboards.enabled=true")
    (cm,) = [d for d in docs if d["metadata"]["name"].endswith("-dashboards")]
    files = {p.name: p.read_text(encoding="utf-8") for p in DASHBOARDS.glob("*")}
    assert cm["data"] == files
