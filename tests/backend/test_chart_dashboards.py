# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The chart's Grafana dashboards (charts/your-service-name/dashboards/*.json).

Each parses, has a stable unique uid, takes its datasource from the `datasource` variable, carries
no exported-dashboard noise, and queries only series the backend serves (the runbook's "Prometheus
Metrics" list) or the chart records (the rendered recording rules), each selector scoped to the
cluster and namespace variables. Every rendered expression, with the Grafana variables substituted,
must pass the pinned `promtool check rules`.
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
from your_service_name.backend.build_info import BUILD_INFO_METRIC

DASHBOARDS = CHART / "dashboards"
RUNBOOK = ROOT / "wiki" / "operations-runbook.md"
PROMTOOL = ROOT / ".tools" / "bin" / "promtool"
UIDS = {
    "database-pool.json": "your-service-name-db",
    "rate-limiting.json": "your-service-name-rate",
    "service-red.json": "your-service-name-red",
    "slo.json": "your-service-name-slo",
}
# The render guard in templates/dashboards.yaml (under 1 MiB, with room for the metadata).
GUARD_BYTES = 1_000_000
# Grafana's limit on a dashboard uid.
UID_MAX = 40
DATASOURCE = {"type": "prometheus", "uid": "${datasource}"}
NOISE = {"id", "version", "iteration"}
KEYWORDS = {"and", "or", "unless", "bool", "offset", "group_left", "group_right", "inf", "nan"}
GROUPING = r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)"
STRINGS = r'"(?:[^"\\]|\\.)*"|`[^`]*`'
SELECTOR = r"([a-zA-Z_:][\w:]*)\{([^}]*)\}"
SCOPE = ('cluster=~"$cluster"', 'namespace="$namespace"')
# The chart replaces this with monitoring.slo.excludeHandlers (templates/dashboards.yaml).
EXCLUDE = "handler!~`@exclude_handlers@`"
# What each Grafana variable becomes for promtool.
GRAFANA_VARS = {
    "__rate_interval": "5m",
    "__interval": "1m",
    "__range": "1h",
    "__range_s": "3600",
    "namespace": "app",
    "cluster": ".*",
}
# The variables list the labels of the service's own info metric, not a series every service
# built from the template exports (wiki/operations-runbook.md, "Grafana Dashboards": Variables).
VARIABLE_QUERIES = {
    "cluster": f"label_values({BUILD_INFO_METRIC}, cluster)",
    "namespace": f'label_values({BUILD_INFO_METRIC}{{cluster=~"$cluster"}}, namespace)',
}
# A regex with a backslash: the replacement must escape it for JSON, and PromQL must read it back.
BACKSLASH_EXCLUDE = r".*/(health|healthz|readyz|livez|metrics)$|^/static/.*\.map$"
# --set reads a backslash as an escape: pass the value as JSON.
BACKSLASH_ARGS = ("--set-json", f"monitoring.slo.excludeHandlers={json.dumps(BACKSLASH_EXCLUDE)}")


def _load(directory: Path) -> dict[str, dict[str, Any]]:
    return {
        p.name: json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))
    }


def _panels(dashboard: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for p in dashboard.get("panels", []):
        yield p
        yield from p.get("panels", [])


def _variables(dashboard: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {v["name"]: v for v in dashboard["templating"]["list"]}


def _exprs(dashboards: dict[str, dict[str, Any]]) -> list[tuple[str, str]]:
    """Every panel expression, and each query variable's series selector."""
    out = [
        (f"{name}: {p['title']}", t["expr"])
        for name, d in dashboards.items()
        for p in _panels(d)
        for t in p.get("targets", [])
    ]
    for name, d in dashboards.items():
        for var in _variables(d).values():
            found = re.fullmatch(r"label_values\((.+), *\w+\)", str(var.get("query", "")))
            if var["type"] == "query" and found:
                out.append((f"{name}: variable {var['name']}", found.group(1)))
    return out


def _substitute(expr: str) -> str:
    return re.sub(
        r"\$\{(\w+)\}|\$(\w+)",
        lambda m: GRAFANA_VARS.get(m.group(1) or m.group(2), m.group(0)),
        expr,
    )


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


def _metric_list(expr: str) -> list[str]:
    bare = re.sub(STRINGS, '""', expr)
    bare = re.sub(r"\{[^}]*\}|\[[^\]]*\]", " ", bare)
    bare = re.sub(GROUPING, " ", bare)
    found = re.findall(r"(?<![\w:.$])([a-zA-Z_:][\w:]*)(?![\w:]|\s*\()", bare)
    return [n for n in found if n.lower() not in KEYWORDS]


def metric_names(expr: str) -> set[str]:
    """The metric and recording-rule names a PromQL expression selects."""
    return set(_metric_list(expr))


def _rendered(chart: Path, helm: str, *args: str) -> tuple[dict[str, str], set[str]]:
    """The dashboards ConfigMap's data and the recorded rule names, from one render of ``chart``."""
    result = subprocess.run(
        [
            *(helm, "template", "svc", str(chart), "-n", "app"),
            *(
                "--set",
                "monitoring.dashboards.enabled=true",
                "--set",
                "monitoring.prometheusRule=true",
            ),
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    docs = [d for d in yaml.safe_load_all(result.stdout) if d]
    (cm,) = [d for d in docs if d["metadata"]["name"].endswith("-dashboards")]
    recorded = {
        r["record"]
        for d in docs
        if d["kind"] == "PrometheusRule"
        for g in d["spec"]["groups"]
        for r in g["rules"]
        if "record" in r
    }
    return cm["data"], recorded


def check(chart: Path, helm: str, work: Path, *args: str) -> list[str]:
    """Every problem with the dashboards ``chart`` renders: unknown names, then promtool's."""
    data, recorded = _rendered(chart, helm, *args)
    exprs = _exprs({k: json.loads(v) for k, v in data.items()})
    served, prefixes = _allowed()
    errors = []
    for where, expr in exprs:
        unknown = {
            n
            for n in metric_names(expr)
            if n not in served | recorded and not n.startswith(prefixes)
        }
        if unknown:
            errors.append(f"{where}: neither served nor recorded: {sorted(unknown)}")
        if re.search(r"\$\{?\w", re.sub(STRINGS, "", _substitute(expr))) or "@" in expr:
            errors.append(f"{where}: an unknown variable or placeholder in {expr!r}")
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


def selector_problems(expr: str) -> list[str]:
    """Selectors in ``expr`` without the cluster and namespace scope (an ``http_*`` one without the
    handler exclusion), and series named without a selector."""
    selectors = re.findall(SELECTOR, expr)
    bad = [
        f"{name}{{{matchers}}}"
        for name, matchers in selectors
        if not all(s in matchers for s in SCOPE)
        or (name.startswith("http_") and EXCLUDE not in matchers)
    ]
    if len(selectors) != len(_metric_list(expr)):
        bad.append(f"a series without a selector in {expr!r}")
    return bad


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
        # Runs after adaptation too: an adopter's long service name fails here, not in Grafana.
        assert len(d["uid"]) <= UID_MAX, f"{name}: uid {d['uid']!r} is over {UID_MAX} characters"
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
    # No constant: a copied objective or regex would be wrong in any release that overrides it.
    assert list(variables) == ["datasource", "cluster", "namespace"]
    assert variables["datasource"]["type"] == "datasource"
    assert variables["datasource"]["query"] == "prometheus"
    assert variables["cluster"]["includeAll"] is True
    assert variables["cluster"]["allValue"] == ".*"
    for var in ("cluster", "namespace"):
        assert variables[var]["datasource"] == DATASOURCE
        assert variables[var]["query"] == VARIABLE_QUERIES[var], var
    panels = list(_panels(d))
    assert panels
    for p in panels:
        assert p["datasource"] == DATASOURCE, p["title"]
        assert p.get("description"), p["title"]
        assert p["fieldConfig"]["defaults"].get("unit"), p["title"]
        assert p["targets"], p["title"]
        for t in p["targets"]:
            assert t["datasource"] == DATASOURCE, p["title"]
            assert selector_problems(t["expr"]) == [], p["title"]
            # A rate's window is Grafana's; only the 30-day budget averages a fixed range.
            for window in re.findall(r"\[([^\]]+)\]", re.sub(STRINGS, '""', t["expr"])):
                assert window == "$__rate_interval" or (
                    window == "30d" and "avg_over_time" in t["expr"]
                )
            assert "/ 100" not in t["expr"], "an objective constant: use slo:objective:ratio"


@pytest.mark.parametrize("name", sorted(UIDS))
def test_no_dashboard_query_or_variable_selects_on_job(name: str) -> None:
    """The job name follows the release's name, so a selector on it breaks with an override."""
    text = (DASHBOARDS / name).read_text(encoding="utf-8")
    assert not re.search(r"[{,]\s*job\s*(=~|!~|!=|=)", text.replace('\\"', '"')), name


def test_the_slo_budget_comes_from_the_objective_recording() -> None:
    budgets = [
        t["expr"]
        for p in _panels(_load(DASHBOARDS)["slo.json"])
        for t in p["targets"]
        if "budget" in t["legendFormat"] or "burn rate" in p["title"]
    ]
    assert budgets
    for expr in budgets:
        assert "1 - slo:objective:ratio{" in expr, expr


def test_the_30_day_budget_panels_are_instant_with_thresholds() -> None:
    panels = [p for p in _panels(_load(DASHBOARDS)["slo.json"]) if "30d" in p["title"]]
    assert [p["title"].split(":")[0] for p in panels] == ["Availability", "Latency"]
    for p in panels:
        assert all(t.get("instant") is True for t in p["targets"]), p["title"]
        steps = p["fieldConfig"]["defaults"]["thresholds"]["steps"]
        assert [s["color"] for s in steps] == ["red", "orange", "green"], p["title"]
        assert steps[1]["value"] == 0, p["title"]


def test_metric_names_extracts_the_selected_series_only() -> None:
    expr = (
        'histogram_quantile(0.5, sum by (le, handler) (rate(a_bucket{x="b c"}[5m]))) '
        "/ on (pod) group_left b and slo:c:d > bool 1"
    )
    assert metric_names(expr) == {"a_bucket", "b", "slo:c:d"}


def test_selector_problems_reads_every_selector() -> None:
    scoped = 'cluster=~"$cluster", namespace="$namespace"'
    good = f'a{{{scoped}}} / http_x{{{scoped}, {EXCLUDE}, status=~"[45].."}}'
    assert selector_problems(good) == []
    assert selector_problems(f'a{{{scoped}}} / b{{namespace="$namespace"}}') == [
        'b{namespace="$namespace"}'
    ]
    assert selector_problems(f"http_x{{{scoped}}}") == [f"http_x{{{scoped}}}"]
    assert selector_problems(f"a{{{scoped}}} / b") == [
        f"a series without a selector in 'a{{{scoped}}} / b'"
    ]


def test_substitute_replaces_whole_variable_names() -> None:
    assert _substitute("[$__range] $__range_s ${__range_s} $__rate_interval") == (
        "[1h] 3600 3600 5m"
    )


@pytest.mark.usefixtures("promtool")
def test_every_query_is_served_or_recorded_and_valid_promql(helm: str, tmp_path: Path) -> None:
    assert check(CHART, helm, tmp_path) == []
    # A backslash in the regex: JSON-escaped by the chart, read back by PromQL.
    assert check(CHART, helm, tmp_path, *BACKSLASH_ARGS) == []


def test_the_exclusion_regex_reaches_promql_unchanged(helm: str) -> None:
    data, _ = _rendered(CHART, helm, *BACKSLASH_ARGS)
    red = json.loads(data["your-service-name-service-red.json"])
    exprs = [t["expr"] for p in _panels(red) for t in p["targets"]]
    assert exprs
    assert all(f"handler!~`{BACKSLASH_EXCLUDE}`" in e for e in exprs)


def _broken(tmp: Path, old: str, new: str) -> Path:
    chart = tmp / "chart"
    shutil.copytree(CHART, chart)
    red = chart / "dashboards" / "service-red.json"
    text = red.read_text(encoding="utf-8")
    assert old in text
    red.write_text(text.replace(old, new, 1), encoding="utf-8")
    return chart


@pytest.mark.usefixtures("promtool")
def test_a_syntax_error_fails_promtool(helm: str, tmp_path: Path) -> None:
    chart = _broken(tmp_path, "sum(rate(", "sum(rate((")
    errors = check(chart, helm, tmp_path)
    assert any(e.startswith("promtool check rules") for e in errors), errors


@pytest.mark.usefixtures("promtool")
def test_a_metric_the_backend_does_not_serve_fails(helm: str, tmp_path: Path) -> None:
    chart = _broken(tmp_path, "http_requests_total{", "http_request_total{")
    errors = check(chart, helm, tmp_path)
    assert any("http_request_total" in e for e in errors), errors


def test_the_size_guard_measures_the_dashboards_after_the_replacement(
    helm: str, tmp_path: Path
) -> None:
    """Under the limit as files, over it once the regex replaces each placeholder: refused."""
    chart = tmp_path / "chart"
    shutil.copytree(CHART, chart)
    huge = '"' + "@exclude_handlers@" * 40_000 + '"\n'
    (chart / "dashboards" / "huge.json").write_text(huge, encoding="utf-8")
    assert sum(p.stat().st_size for p in (chart / "dashboards").glob("*.json")) < GUARD_BYTES
    result = subprocess.run(
        [helm, "template", "svc", str(chart), "--set", "monitoring.dashboards.enabled=true"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "1 MiB" in result.stderr


def test_the_configmap_holds_every_dashboard_with_the_regex_in_place(helm: str) -> None:
    """Keyed <chart>-<file> (the sidecar writes every key into one folder: bare names collide)."""
    docs = render(helm, "values.yaml", "--set", "monitoring.dashboards.enabled=true")
    (cm,) = [d for d in docs if d["metadata"]["name"].endswith("-dashboards")]
    regex = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))["monitoring"][
        "slo"
    ]["excludeHandlers"]
    escaped = json.dumps(regex)[1:-1]
    files = {
        f"your-service-name-{p.name}": p.read_text(encoding="utf-8").replace(
            "@exclude_handlers@", escaped
        )
        for p in DASHBOARDS.glob("*.json")
    }
    assert cm["data"] == files


@pytest.mark.parametrize("name", sorted(UIDS))
def test_the_variables_key_on_the_services_own_info_metric(name: str) -> None:
    """Not on a series every service built from the template exports."""
    assert BUILD_INFO_METRIC.endswith("_build_info")
    assert re.fullmatch(r"[a-zA-Z_:][a-zA-Z0-9_:]*", BUILD_INFO_METRIC)
    for var in ("cluster", "namespace"):
        query = _variables(_load(DASHBOARDS)[name])[var]["query"]
        assert query.count(BUILD_INFO_METRIC) == 1, (name, var)
        assert "rate_limit_rejections_total" not in query, (name, var)
