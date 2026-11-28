# your-service-name — adaptable service template (see wiki/getting-started.md)
"""A production release must not render with ``ENV`` left at its dev default (validators off)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import get_args

import pytest
import yaml

from tests.supply_chain.test_chart_image_digests import _images
from tests.utils.template_tree import is_template
from your_service_name.backend.settings import PRODUCTION_ENVS, AuthMode

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "your-service-name"


def find_helm(root: Path = ROOT) -> str | None:
    """The pinned helm (``.tools/bin/helm``) when installed, else the one on PATH."""
    pinned = root / ".tools" / "bin" / "helm"
    return str(pinned) if pinned.exists() else shutil.which("helm")


def require_helm(helm: str | None, *, ci: bool) -> str:
    """The helm binary; a missing one skips locally but must fail loudly in CI."""
    if helm:
        return helm
    msg = "helm is not installed (scripts/tools.sh install helm4); the production guard is untested"
    if ci:
        pytest.fail(msg)
    pytest.skip(msg)


@pytest.fixture(scope="module")
def helm() -> str:
    return require_helm(find_helm(), ci=bool(os.environ.get("CI")))


def test_a_missing_helm_fails_in_ci_and_skips_locally() -> None:
    with pytest.raises(pytest.fail.Exception, match="not installed"):
        require_helm(None, ci=True)
    with pytest.raises(pytest.skip.Exception):
        require_helm(None, ci=False)
    assert require_helm("/x/helm", ci=True) == "/x/helm"


def _template(helm: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [helm, "template", "svc", str(CHART), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_production_environment_with_dev_env_is_refused(helm: str) -> None:
    """`environment: production` without `values.production.yaml` leaves ENV=dev."""
    result = _template(helm, "--set", "environment=production")
    assert result.returncode != 0
    assert "config.ENV" in result.stderr


def test_production_values_render(helm: str) -> None:
    result = _template(helm, "-f", str(CHART / "values.production.yaml"))
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("env", ["production", "prod"])
def test_production_environment_with_a_production_env_renders(helm: str, env: str) -> None:
    """Both spellings the backend reads as production pass the ENV guard (the other production
    rules take values.production.yaml, which meets them)."""
    result = _template(
        helm, "-f", str(CHART / "values.production.yaml"), "--set", f"config.ENV={env}"
    )
    assert result.returncode == 0, result.stderr


def test_production_without_an_auth_mode_is_refused(helm: str, tmp_path: Path) -> None:
    """An unset AUTH_MODE is the backend default, none: refused like an explicit none."""
    values = yaml.safe_load((CHART / "values.production.yaml").read_text(encoding="utf-8"))
    del values["config"]["AUTH_MODE"]
    path = tmp_path / "production-without-auth.yaml"
    path.write_text(yaml.safe_dump(values), encoding="utf-8")
    result = _template(helm, "-f", str(path))
    assert result.returncode != 0
    assert 'config.AUTH_MODE is "none"' in result.stderr


PRODUCTION_IMAGES = ("backend", "frontend", "waf", "clientImage", "cloudSqlProxy")
# Every image mapping in values.yaml, found by the same walker that checks the third-party pins.
VALUES_IMAGES = {
    path: value
    for path, value in _images(yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8")))
    if isinstance(value, dict)
}
# Renders that run every image of values.yaml between them; each image gets its own repository so
# a rendered reference names exactly one values path.
RENAMED = {"postgresql.image": "pg-server", "postgresql.clientImage": "pg-client"}
DISTINCT = tuple(
    a for path, repo in RENAMED.items() for a in ("--set", f"{path}.repository={repo}")
)
EVERY_IMAGE_RENDERS = (
    ("--set", "waf.enabled=true", "--set", "postgresql.backup.enabled=true", *DISTINCT),
    (
        *("--set", "postgresql.enabled=false", "--set", "config.DB_SSLMODE=disable"),
        *("--set", "cloudSqlProxy.enabled=true"),
        *("--set", "cloudSqlProxy.egressCIDRs={199.36.153.8/30}", *DISTINCT),
    ),
)


def _rendered_image_paths(helm: str) -> set[str]:
    """The values path of every container image the chart renders, from the rendered output."""
    found: set[str] = set()
    for args in EVERY_IMAGE_RENDERS:
        result = _template(helm, "--set", "secrets.dbPassword=x", *args)
        assert result.returncode == 0, result.stderr
        refs = re.findall(r"^\s*(?:- )?image: \"?([^\"\s]+)", result.stdout, re.MULTILINE)
        for ref in refs:
            paths = [
                path
                for path, image in VALUES_IMAGES.items()
                if re.fullmatch(
                    rf"{re.escape(RENAMED.get(path, str(image['repository'])))}[:@].+", ref
                )
            ]
            assert len(paths) == 1, (ref, paths)
            found.add(paths[0])
    return found


def test_the_values_walker_finds_every_image_including_the_proxy() -> None:
    """The proxy renders through a helper variable ($p.image), so no template regex sees it."""
    assert set(VALUES_IMAGES) == {
        "images.backend",
        "images.frontend",
        "images.waf",
        "postgresql.image",
        "postgresql.clientImage",
        "cloudSqlProxy.image",
    }


def test_every_rendered_image_is_a_values_image_and_every_values_image_renders(
    helm: str,
) -> None:
    assert _rendered_image_paths(helm) == set(VALUES_IMAGES)


def test_the_digest_rule_lists_every_values_image() -> None:
    """validate-values.yaml names each image it requires a digest for; an image missing from that
    list would deploy by tag in production."""
    rules = (CHART / "templates" / "validate-values.yaml").read_text(encoding="utf-8")
    for path in sorted(VALUES_IMAGES):
        assert f'"{path}" .Values.{path}' in rules, path


def test_every_container_of_a_production_render_runs_by_digest(helm: str) -> None:
    """values.production.yaml with every optional image on: no container runs by tag."""
    result = _template(
        helm,
        *("-f", str(CHART / "values.production.yaml")),
        *("--set", "waf.enabled=true", "--set", "cloudSqlProxy.enabled=true"),
        *(
            "--set",
            "cloudSqlProxy.egressCIDRs={199.36.153.8/30}",
            "--set",
            "config.DB_SSLMODE=disable",
        ),
    )
    assert result.returncode == 0, result.stderr
    images = re.findall(r"^\s*(?:- )?image: \"?([^\"\s]+)", result.stdout, re.MULTILINE)
    # backend, frontend, waf, the database client, the proxy
    assert len(set(images)) == len(PRODUCTION_IMAGES), images
    assert all(re.search(r"@sha256:[0-9a-f]{64}$", ref) for ref in images), images


@pytest.mark.parametrize("values", ["values.dev.yaml", "values.qa.yaml", "values.local.yaml"])
def test_other_environments_render(helm: str, values: str) -> None:
    assert _template(helm, "-f", str(CHART / values)).returncode == 0


def _template_list(name: str) -> set[str]:
    """A `list "a" "b"` the production rules in validate-values.yaml compare with."""
    rules = (CHART / "templates" / "validate-values.yaml").read_text(encoding="utf-8")
    match = re.search(rf"\${name} := list ((?:\"[^\"]+\" ?)+)", rules)
    assert match, name
    return set(re.findall(r'"([^"]+)"', match[1]))


def test_the_chart_accepts_exactly_the_backends_auth_modes() -> None:
    assert _template_list("authModes") == set(get_args(AuthMode))


def test_the_chart_reads_production_from_the_backends_env_names() -> None:
    rules = (CHART / "templates" / "validate-values.yaml").read_text(encoding="utf-8")
    match = re.search(
        r'\(trim \(toString \.Values\.config\.ENV\)\)\) \(list ((?:"[^"]+" ?)+)\)', rules
    )
    assert match
    assert set(re.findall(r'"([^"]+)"', match[1])) == set(PRODUCTION_ENVS)


# The placeholder hosts and issuer, in pieces: infra/adapt/adopt.sh stamps the adopter's domain over
# every whole copy, so these stay placeholders once adopted.
_PLACEHOLDER = "your-domain" + ".example"
PLACEHOLDER_HOSTS = (
    *("--set", f"edge.hosts.api=api.{_PLACEHOLDER}"),
    *("--set", f"edge.hosts.root={_PLACEHOLDER}"),
    *("--set", f"config.OIDC_ISSUER=https://idp.{_PLACEHOLDER}"),
)
# The template's placeholders, in pieces so the adapter leaves them alone.
PLACEHOLDER_DOMAIN = re.compile(r"(?<![\w-])your-domain[.]example\b")
PLACEHOLDER_REPO = "github.com/your-org/" + "your-repo/"
GATEWAY = "edge.gateway.parentRefs[0].name=edge"


@pytest.mark.parametrize("env", ["dev", "qa", "production"])
def test_the_committed_hosts_are_refused_exactly_while_they_hold_the_placeholder(
    helm: str, env: str
) -> None:
    """The template ships placeholder hosts, which the deploy guard refuses; once
    infra/adapt/adopt.sh has stamped the adopter's domain, the guard lets them through."""
    values = CHART / f"values.{env}.yaml"
    held = bool(PLACEHOLDER_DOMAIN.search(values.read_text(encoding="utf-8")))
    template = is_template(ROOT)
    assert held or not template, f"the template's values.{env}.yaml lost its placeholder hosts"
    args = ("-f", str(values), "--set", "guards.rejectPlaceholders=true", "--set", GATEWAY)
    result = _template(helm, *args)
    assert ("still holds the placeholder domain" in result.stderr) is held, result.stderr


def test_the_committed_runbook_link_is_refused_exactly_while_it_holds_the_placeholder(
    helm: str,
) -> None:
    """The same for the runbook link every alert carries, in production with the rules on."""
    default = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    held = PLACEHOLDER_REPO in default["monitoring"]["runbookUrl"]
    template = is_template(ROOT)
    assert held or not template, "the template's monitoring.runbookUrl lost its placeholder"
    args = ("-f", str(CHART / "values.production.yaml"), "--set", "guards.rejectPlaceholders=true")
    result = _template(helm, *args, "--set", "monitoring.prometheusRule=true")
    assert ("monitoring.runbookUrl is the placeholder" in result.stderr) is held, result.stderr


def test_one_guard_refusal_lists_one_placeholder_per_line_and_the_count_matches(
    helm: str,
) -> None:
    """The production values with the placeholder hosts and issuer, the proxy and the ExternalSecret
    on, under the deploy guard: one refusal whose header count equals the item lines below it."""
    result = _template(
        helm,
        *("-f", str(CHART / "values.production.yaml"), "--set", "guards.rejectPlaceholders=true"),
        *("--set", "externalDatabase.enabled=false", "--set", "config.DB_SSLMODE=disable"),
        *("--set", "cloudSqlProxy.enabled=true"),
        *("--set", "cloudSqlProxy.egressCIDRs={199.36.153.8/30}"),
        *("--set", "secrets.existingSecret=", "--set", "secrets.externalSecret.enabled=true"),
        *PLACEHOLDER_HOSTS,
    )
    assert result.returncode != 0
    header = re.search(
        r"guards\.rejectPlaceholders found (\d+) placeholders? to replace", result.stderr
    )
    assert header, result.stderr
    tail = result.stderr[header.end() :]
    block = tail[: tail.index("\nSet them in values.<env>.yaml.")]
    items = [ln for ln in block.splitlines() if ln.startswith("  - ")]
    assert len(items) == int(header[1]) == 7, block  # noqa: PLR2004 (the committed placeholders)
    assert all(ln.startswith("  - ") for ln in block.splitlines()[1:]), block


@pytest.mark.parametrize("env", ["dev", "qa", "production"])
def test_a_host_composed_at_deploy_time_passes_the_placeholder_guard(
    helm: str, env: str, tmp_path: Path
) -> None:
    """scripts/ci/deploy-env.sh passes the EDGE_DOMAIN host as one more values file after the
    committed ones: the committed placeholder stays, and the guard has nothing left to refuse."""
    hosts = tmp_path / "edge-hosts.yaml"
    hosts.write_text("edge:\n  hosts:\n    root: svc-env.lab.example.org\n", encoding="utf-8")
    args = ("-f", str(CHART / f"values.{env}.yaml"), "-f", str(hosts))
    result = _template(helm, *args, "--set", "guards.rejectPlaceholders=true", "--set", GATEWAY)
    assert "still holds the placeholder domain" not in result.stderr, result.stderr
