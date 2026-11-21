#!/usr/bin/env python3
"""CI-hardening lint for this repo's GitHub workflows.

The pipeline that builds the software is its own attack surface, so the workflows are linted for the
controls that protect them. Scans ``.github/workflows/*.{yml,yaml}`` and
``.github/actions/*/action.{yml,yaml}``.

Output: one line per finding, ``file:line: CHECK-ID message``, then a summary. Exit 1 on any
finding, 0 when clean, 2 on a usage or parse error (``file:line: parse-error ...``). ``--json``
prints a structured report instead (used by ``check-ci-hardening.sh``).

Checks (ids are stable):
  pinned-uses, top-permissions, no-prt-checkout, no-inline-secrets, image-digest,
  image-push-isolation (only reusable-build.yml pushes, signs or attests images), provenance
  (when a workflow calls reusable-build.yml, it must sign, verify and attest), osv-coverage,
  environment-for-secrets, timeouts, no-dry-run-env, documented-permissions, no-release-cache
  (the release path uses no build cache, no actions/cache and no setup-* cache).

Known limitation: image-digest accepts a value that is one whole ``${{ }}`` expression (a digest
computed at run time, verified by the image-verify step); it cannot see through the expression.
"""

# ruff: noqa: ANN401  (parsed YAML is dynamically typed: LDict / LList / str)
from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

USAGE_ERROR = 2
REUSABLE_BUILD = ".github/workflows/reusable-build.yml"
SIGNING_JOB = "sign-attest"
# The per-platform SBOM attestation runs in its own matrix job, so it holds the same keyless grants.
ATTESTING_JOBS = (SIGNING_JOB, "attest-sbom")
# Publishing Scorecard results needs id-token: write (OIDC) and no cloud credential; the action's
# workflow restrictions allow no environment-free job but this one.
SCORECARD_WORKFLOW = ".github/workflows/scorecard.yml"
SCORECARD_JOB = "analysis"
_REUSABLE_REF = "./" + REUSABLE_BUILD
_CLOUD_LOGIN_REF = (
    "./.github/workflows/deploy.yml"  # its caller grants id-token for the cloud login
)

_SHA_REF = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
_VERSION_COMMENT = re.compile(r"#\s*v\d+(\.\d+)*\S*\s*$")
_DOCKER_PIN = re.compile(r"^docker://\S+@sha256:[0-9a-f]{64}$")
_IMAGE_KEY = re.compile(r"(?i)(^|[_-])image$")
_IMAGE_PIN = re.compile(r"@sha256:[0-9a-f]{64}")
_WHOLE_EXPR = re.compile(r"^\$\{\{[^}]*\}\}$")
_SECRET_REF = re.compile(r"(?<![\w.-])secrets\b(?:\s*\.\s*(\w+))?")
_QUOTED = re.compile(r"'(?:[^']|'')*'")
_OSV_RUN = re.compile(r"osv-scanner|scan-osv\.sh")
_IMAGE_PUSH_RUN = re.compile(
    r"\bdocker\s+(?:buildx\s+)?(?:build|bake)\b(?:\\\n|[^\n])*--push\b"
    r"|\bdocker\s+(?:--[\w-]+(?:=|\s+)\S+\s+)*(?:image\s+)?push\b"
    r"|\bdocker(?:\s+compose|-compose)\b[^\n]*\bpush\b"
    r"|\bdocker\s+buildx\s+imagetools\s+create\b|\bskopeo\s+copy\b"
    r"|\bcosign\s+(?:sign|attest)\b"
    r"|\b(?:buildah|podman|oras)\s+push\b|\bcrane\s+(?:push|copy|cp)\b"
)
_REGISTRY_OUTPUT = re.compile(r"type=registry|\bpush=true\b")
_PUSH_ACTION = re.compile(r"^(?:[\w.-]+/)?[\w.-]*(?:build-push-action|bake-action)@")
_COSIGN_SIGN = re.compile(r"\bcosign\s+sign\b")
_COSIGN_VERIFY = re.compile(r"\bcosign\s+verify\b")


class LDict(dict[str, Any]):
    """A mapping that remembers the line of each key (1-based)."""

    def __init__(self, line: int) -> None:
        super().__init__()
        self.line = line
        self.key_lines: dict[str, int] = {}


class LList(list[Any]):
    """A sequence that remembers the line of each item (1-based)."""

    def __init__(self, line: int) -> None:
        super().__init__()
        self.line = line
        self.item_lines: list[int] = []


def _convert(node: yaml.Node) -> Any:
    """Turn a composed YAML node into LDict / LList / str (scalars stay strings)."""
    line = node.start_mark.line + 1
    if isinstance(node, yaml.MappingNode):
        mapping = LDict(line)
        for key_node, value_node in node.value:
            key = str(key_node.value)
            mapping[key] = _convert(value_node)
            mapping.key_lines[key] = key_node.start_mark.line + 1
        return mapping
    if isinstance(node, yaml.SequenceNode):
        seq = LList(line)
        for item in node.value:
            seq.append(_convert(item))
            seq.item_lines.append(item.start_mark.line + 1)
        return seq
    return str(node.value)


@dataclass(frozen=True)
class Finding:
    file: str
    line: int
    check: str
    message: str

    def render(self) -> str:
        return f"{self.file}:{self.line}: {self.check} {self.message}"


@dataclass
class Doc:
    """One parsed workflow or composite action."""

    name: str
    text: str
    data: LDict
    lines: list[str] = field(default_factory=list)

    @property
    def is_workflow(self) -> bool:
        return "jobs" in self.data

    def jobs(self) -> Iterator[tuple[str, LDict]]:
        jobs = self.data.get("jobs")
        if isinstance(jobs, LDict):
            for job_id, job in jobs.items():
                if isinstance(job, LDict):
                    yield job_id, job

    def steps(self) -> Iterator[LDict]:
        """Every step of every job, or of a composite action."""
        groups: list[Any] = [j.get("steps") for _, j in self.jobs()]
        runs = self.data.get("runs")
        if isinstance(runs, LDict):
            groups.append(runs.get("steps"))
        for group in groups:
            if isinstance(group, LList):
                for step in group:
                    if isinstance(step, LDict):
                        yield step


def _scalars(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for value in node.values():
            yield from _scalars(value)
    elif isinstance(node, list):
        for value in node:
            yield from _scalars(value)
    elif isinstance(node, str):
        yield node


def _expressions(text: str) -> Iterator[str]:
    """Yield the body of each ``${{ ... }}``, honouring quoted strings (a ``}}`` inside is text)."""
    pos = text.find("${{")
    while pos >= 0:
        end, quoted = pos + 3, False
        while end < len(text) and not (not quoted and text.startswith("}}", end)):
            quoted ^= text[end] == "'"
            end += 1
        yield text[pos + 3 : end]
        pos = text.find("${{", end)


def _secret_names(text: str) -> list[str]:
    """Names of `secrets` references inside expressions; `*` for bare `secrets` or `secrets[..]`."""
    return [
        m[1] or "*"
        for expr in _expressions(text)
        for m in _SECRET_REF.finditer(_QUOTED.sub("''", expr))
    ]


def _other_secrets(node: Any) -> bool:
    return any(n != "GITHUB_TOKEN" for t in _scalars(node) for n in _secret_names(t))


def _line(node: Any, key: str, default: int) -> int:
    return node.key_lines.get(key, default) if isinstance(node, LDict) else default


def _env_maps(doc: Doc) -> Iterator[LDict]:
    """Every `env:` mapping: workflow, job and step level."""
    candidates: list[Any] = [doc.data.get("env")]
    candidates += [j.get("env") for _, j in doc.jobs()]
    candidates += [s.get("env") for s in doc.steps()]
    for env in candidates:
        if isinstance(env, LDict):
            yield env


Check = Callable[[Doc], list[Finding]]


def check_pinned_uses(doc: Doc) -> list[Finding]:
    found: list[Finding] = []
    holders: list[LDict] = [j for _, j in doc.jobs()] + list(doc.steps())
    for holder in holders:
        ref = holder.get("uses")
        if not isinstance(ref, str):
            continue
        line = holder.key_lines["uses"]
        ref = ref.strip()
        if ref.startswith("./"):
            continue  # local: the same commit
        if ref.startswith("docker://"):
            if not _DOCKER_PIN.match(ref):
                found.append(
                    Finding(
                        doc.name,
                        line,
                        "pinned-uses",
                        f"docker reference {ref} must carry @sha256:<64 hex>",
                    )
                )
            continue
        if not _SHA_REF.match(ref):
            found.append(
                Finding(
                    doc.name, line, "pinned-uses", f"{ref} is not pinned to a 40-hex commit SHA"
                )
            )
        elif not _VERSION_COMMENT.search(doc.lines[line - 1]):
            found.append(
                Finding(doc.name, line, "pinned-uses", f"{ref} has no '# vX.Y.Z' version comment")
            )
    return found


def check_top_permissions(doc: Doc) -> list[Finding]:
    if doc.is_workflow and "permissions" not in doc.data:
        return [
            Finding(
                doc.name,
                1,
                "top-permissions",
                "no top-level permissions (use `permissions: {}` or contents: read)",
            )
        ]
    return []


def check_no_prt_checkout(doc: Doc) -> list[Finding]:
    triggers = doc.data.get("on")
    names = list(triggers) if isinstance(triggers, (LDict, LList)) else [triggers]
    if "pull_request_target" not in names:
        return []
    checks_out = any(str(s.get("uses", "")).startswith("actions/checkout@") for s in doc.steps())
    line = _line(triggers, "pull_request_target", _line(doc.data, "on", 1))
    note = " and checks out code" if checks_out else ""
    return [
        Finding(
            doc.name,
            line,
            "no-prt-checkout",
            f"pull_request_target is banned in this repo (it is used here{note})",
        )
    ]


def check_no_inline_secrets(doc: Doc) -> list[Finding]:
    return [
        Finding(
            doc.name,
            step.key_lines["run"],
            "no-inline-secrets",
            "secrets inline in run: pass it through env:",
        )
        for step in doc.steps()
        if isinstance(step.get("run"), str) and _secret_names(step["run"])
    ]


def _image_findings(doc: Doc, value: Any, line: int, what: str) -> list[Finding]:
    if not isinstance(value, str) or not value.strip():
        return []
    value = value.strip()
    if _IMAGE_PIN.search(value) or _WHOLE_EXPR.match(value):
        return []  # pinned, or computed whole (a digest output checked at run time)
    return [
        Finding(
            doc.name,
            line,
            "image-digest",
            f"{what} image {value} is not pinned by @sha256:<64 hex>",
        )
    ]


def _job_image_findings(doc: Doc, job_id: str, job: LDict) -> list[Finding]:
    found: list[Finding] = []
    container = job.get("container")
    what = f"job {job_id} container"
    if isinstance(container, LDict):
        found += _image_findings(
            doc, container.get("image"), container.key_lines.get("image", 1), what
        )
    else:
        found += _image_findings(doc, container, _line(job, "container", 1), what)
    services = job.get("services")
    if isinstance(services, LDict):
        for name, svc in services.items():
            if isinstance(svc, LDict):
                found += _image_findings(
                    doc, svc.get("image"), svc.key_lines.get("image", 1), f"service {name}"
                )
    return found


def check_image_digest(doc: Doc) -> list[Finding]:
    found: list[Finding] = []
    for job_id, job in doc.jobs():
        found += _job_image_findings(doc, job_id, job)
    for env in _env_maps(doc):
        for key, value in env.items():
            if _IMAGE_KEY.search(key):
                found += _image_findings(doc, value, env.key_lines[key], f"env {key}")
    for step in doc.steps():
        with_ = step.get("with")
        if isinstance(with_, LDict):
            for key, value in with_.items():
                if _IMAGE_KEY.search(key):
                    found += _image_findings(doc, value, with_.key_lines[key], f"with {key}")
    return found


def _grants(perms: Any, scope: str) -> bool:
    return perms == "write-all" or (isinstance(perms, LDict) and perms.get(scope) == "write")


def _effective_permissions(doc: Doc, job: LDict) -> Any:
    return job["permissions"] if "permissions" in job else doc.data.get("permissions")


def _pushes_images(step: LDict) -> str | None:
    """Why this step is an image operation (push, sign or attest), or None."""
    ref = str(step.get("uses", ""))
    with_ = step.get("with")
    if _PUSH_ACTION.match(ref) and (
        not isinstance(with_, LDict)
        or str(with_.get("push", "false")).strip().lower() != "false"
        or _REGISTRY_OUTPUT.search(str(with_.get("outputs", "")))
    ):
        return f"{ref.split('@', maxsplit=1)[0]} with push"
    if ref.startswith("actions/attest"):
        return ref.split("@", maxsplit=1)[0]
    if _IMAGE_PUSH_RUN.search(str(step.get("run", ""))):
        return "a docker, cosign or registry push"
    return None


def _isolation(doc: Doc, line: int, message: str) -> Finding:
    return Finding(doc.name, line, "image-push-isolation", message)


def _triggers(doc: Doc) -> list[str]:
    on = doc.data.get("on")
    return [str(n) for n in on] if isinstance(on, (LDict, LList)) else [str(on)]


def _caller_isolation(doc: Doc, job_id: str, job: LDict, perms: Any) -> list[Finding]:
    """A job that calls a workflow: what it may grant to that callee."""
    callee = str(job.get("uses", "")).strip()
    found: list[Finding] = []
    pr = [n for n in _triggers(doc) if n.startswith("pull_request")]
    if callee == _REUSABLE_REF and pr:
        msg = f"job {job_id} calls reusable-build.yml from a {pr[0]} workflow; push only"
        found.append(_isolation(doc, job.line, msg))
    if callee != _REUSABLE_REF:
        found += [
            _isolation(doc, job.line, f"job {job_id} grants {scope}: write to {callee}")
            for scope in ("packages", "attestations")
            if _grants(perms, scope)
        ]
    if callee not in {_REUSABLE_REF, _CLOUD_LOGIN_REF} and _grants(perms, "id-token"):
        msg = f"job {job_id} grants id-token: write to {callee}; only signing and cloud login may"
        found.append(_isolation(doc, job.line, msg))
    return found


def _runner_isolation(doc: Doc, job_id: str, job: LDict, perms: Any) -> list[Finding]:
    """A job that runs steps, outside reusable-build.yml."""
    found = [
        _isolation(doc, job.line, f"job {job_id} holds {scope}: write; only reusable-build.yml may")
        for scope in ("packages", "attestations")
        if _grants(perms, scope)
    ]
    for step in job.get("steps", []):
        what = _pushes_images(step) if isinstance(step, LDict) else None
        if what:
            msg = f"job {job_id} runs {what}; only reusable-build.yml may push, sign or attest"
            found.append(_isolation(doc, job.line, msg))
    return found


def check_image_push_isolation(doc: Doc) -> list[Finding]:
    """Only reusable-build.yml pushes, signs or attests images; only its signing job attests."""
    if not doc.is_workflow:  # a composite action: its steps run inside whatever job calls it
        return [
            _isolation(doc, step.line, f"composite action runs {what}; only reusable-build.yml may")
            for step in doc.steps()
            if (what := _pushes_images(step))
        ]
    found: list[Finding] = []
    for job_id, job in doc.jobs():
        perms = _effective_permissions(doc, job)
        if doc.name.endswith(REUSABLE_BUILD):
            if job_id not in ATTESTING_JOBS and _grants(perms, "attestations"):
                who = " and ".join(ATTESTING_JOBS)
                msg = f"job {job_id} holds attestations: write; only {who} may"
                found.append(_isolation(doc, job.line, msg))
        elif "uses" in job:
            found += _caller_isolation(doc, job_id, job, perms)
        else:
            found += _runner_isolation(doc, job_id, job, perms)
    return found


def _step_index(steps: list[Any], match: Callable[[LDict], bool]) -> int | None:
    return next((i for i, s in enumerate(steps) if isinstance(s, LDict) and match(s)), None)


ATTEST_GUARD = (
    "github.event.repository.visibility == 'public' || vars.ATTESTATIONS_ENABLED == 'true'"
)


def _guard_text(step: LDict) -> str:
    """The step's `if:` without `${{ }}` and with single spaces; empty when it has none."""
    expr = str(step.get("if", "")).strip()
    expr = re.sub(r"^\$\{\{(.*)\}\}$", r"\1", expr, flags=re.DOTALL)
    return " ".join(expr.split())


def _attest_pushes(step: LDict, action: str) -> bool:
    """The step attests with push-to-registry, guarded by exactly ATTEST_GUARD."""
    with_ = step.get("with")
    return (
        str(step.get("uses", "")).startswith(f"actions/{action}@")
        and isinstance(with_, LDict)
        and str(with_.get("push-to-registry", "")).strip().lower() == "true"
        and _guard_text(step) == ATTEST_GUARD
    )


def _sbom_attested(doc: Doc) -> bool:
    """Some job of the file attests the SBOM with push-to-registry and the exact guard."""
    return any(
        _step_index(list(j.get("steps", [])), lambda s: _attest_pushes(s, "attest-sbom"))
        is not None
        for _, j in doc.jobs()
    )


def _signing_gaps(doc: Doc, job_id: str, job: LDict) -> list[Finding]:
    steps = list(job.get("steps", []))
    sign = _step_index(steps, lambda s: bool(_COSIGN_SIGN.search(str(s.get("run", "")))))
    gaps: list[str] = []
    if sign is None:
        return [Finding(doc.name, job.line, "provenance", f"job {job_id} has no cosign sign step")]
    verify = _step_index(
        steps[sign + 1 :], lambda s: bool(_COSIGN_VERIFY.search(str(s.get("run", ""))))
    )
    if verify is None:
        gaps.append("a cosign verify step after the cosign sign step")
    gaps += [
        f"actions/{action} with push-to-registry: true and the guard `if: {ATTEST_GUARD}`"
        for action in ("attest-build-provenance",)
        if _step_index(steps, lambda s, a=action: _attest_pushes(s, a)) is None  # type: ignore[misc]
    ]
    if not _sbom_attested(doc):
        gaps.append(
            f"actions/attest-sbom with push-to-registry: true and the guard `if: {ATTEST_GUARD}`"
        )
    perms = job.get("permissions")
    gaps += [
        f"{scope}: write" for scope in ("id-token", "attestations") if not _grants(perms, scope)
    ]
    return [Finding(doc.name, job.line, "provenance", f"job {job_id} lacks {gap}") for gap in gaps]


def check_provenance(docs: list[Doc]) -> list[Finding]:
    """When a workflow calls reusable-build.yml, that workflow must sign, verify and attest."""
    called = any(
        str(job.get("uses", "")).strip() == _REUSABLE_REF for d in docs for _, job in d.jobs()
    )
    if not called:
        return []
    build = next((d for d in docs if d.name.endswith(REUSABLE_BUILD)), None)
    if build is None:
        return [Finding(REUSABLE_BUILD, 1, "provenance", f"{REUSABLE_BUILD} is called but missing")]
    signer = [
        (i, j)
        for i, j in build.jobs()
        if any(
            _COSIGN_SIGN.search(str(s.get("run", "")))
            for s in j.get("steps", [])
            if isinstance(s, LDict)
        )
    ]
    if not signer:
        return [
            Finding(build.name, 1, "provenance", "no job in reusable-build.yml runs cosign sign")
        ]
    return [f for i, j in signer for f in _signing_gaps(build, i, j)]


def _job_runs_osv(job: LDict) -> bool:
    steps = [s for s in job.get("steps", []) if isinstance(s, LDict)]
    if any(_OSV_RUN.search(str(s.get("run", ""))) for s in steps):
        return True
    if any("osv-scanner" in str(s.get("uses", "")) for s in steps):
        return True
    # A matrix leg that names the wrapper, run through the local wrapper directory.
    matrix = "\n".join(_scalars(job.get("strategy")))
    return "scan-osv.sh" in matrix and any(
        "infra/security/local/" in str(s.get("run", "")) for s in steps
    )


def check_osv_coverage(docs: list[Doc]) -> list[Finding]:
    for doc in docs:
        if not re.search(r"workflows/security\.ya?ml$", doc.name):
            continue
        if any(_job_runs_osv(job) for _, job in doc.jobs()):
            return []
        return [
            Finding(doc.name, 1, "osv-coverage", "security.yml has no job that runs osv-scanner")
        ]
    return [
        Finding(
            ".github/workflows/security.yml",
            1,
            "osv-coverage",
            "security.yml is missing, so there is no osv-scanner coverage",
        )
    ]


def _grants_id_token(perms: Any) -> bool:
    if isinstance(perms, LDict):
        return perms.get("id-token") == "write"
    return bool(perms == "write-all")


def _job_secret_reasons(job: LDict, *, top_idtoken: bool) -> list[str]:
    reasons: list[str] = []
    if _grants_id_token(job.get("permissions")) or (top_idtoken and "permissions" not in job):
        reasons.append("id-token: write")
    for step in job.get("steps", []):
        ref = str(step.get("uses", "")) if isinstance(step, LDict) else ""
        if ref.startswith("google-github-actions/auth@"):
            reasons.append("google-github-actions/auth")
        if ref.rstrip("/").endswith(".github/actions/cluster-access"):
            reasons.append("the cluster-access action")
    if _other_secrets(job):
        reasons.append("a secret other than GITHUB_TOKEN")
    return reasons


def check_environment_for_secrets(doc: Doc) -> list[Finding]:
    found: list[Finding] = []
    env = doc.data.get("env")
    if _other_secrets(env):
        line = _line(doc.data, "env", 1)
        msg = "workflow-level env uses a secret, so no job can scope it to an environment"
        found.append(Finding(doc.name, line, "environment-for-secrets", msg))
    top_idtoken = _grants_id_token(doc.data.get("permissions"))
    for job_id, job in doc.jobs():
        if "uses" in job:  # a caller cannot declare environment:, so it must not pass secrets
            if job.get("secrets") == "inherit" or _other_secrets(job):
                msg = f"job {job_id} passes secrets to a called workflow (no environment)"
                found.append(Finding(doc.name, job.line, "environment-for-secrets", msg))
            continue
        if "environment" in job:
            continue
        reasons = _job_secret_reasons(job, top_idtoken=top_idtoken)
        # Keyless signing needs no cloud credential; only this one job is exempt.
        if doc.name.endswith(REUSABLE_BUILD) and job_id in ATTESTING_JOBS:
            reasons = [r for r in reasons if r != "id-token: write"]
        # Publishing Scorecard results (OIDC, no cloud credential): only this one job is exempt.
        if doc.name.endswith(SCORECARD_WORKFLOW) and job_id == SCORECARD_JOB:
            reasons = [r for r in reasons if r != "id-token: write"]
        if reasons:
            found.append(
                Finding(
                    doc.name,
                    job.line,
                    "environment-for-secrets",
                    f"job {job_id} uses {', '.join(dict.fromkeys(reasons))} with no environment:",
                )
            )
    return found


# The release path is everything reachable from release.yml: the reusable workflows its jobs call
# and the local composite actions their steps use, transitively. It builds, signs, attests and
# deploys, so it must start cold: a cache that another job on main can write could inject a layer,
# a tool or a dependency that the signature and provenance then vouch for.
RELEASE_ROOT = ".github/workflows/release.yml"
NO_CACHE_REASON = (
    "the release path must use no cache (SLSA Build L3 isolation: one build must not be able to "
    "inject into a cache another build reads, and the output must not depend on one)"
)
_CACHE_WORDS = re.compile(r"--cache-(?:from|to)\b|\btype=(?:gha|registry|local|s3|azblob)\b")

# Fail closed: an action may appear on the release path only if it is listed here. Each entry is
# (inputs that must be the literal `false`, inputs that must be absent); an empty entry means the
# action has no cache. To add an action, read its action.yml at the pinned SHA, find every cache
# input and its default, and record both in the comment. Matching is case-insensitive (owner and
# repo names are). Not seen by this check: cache configuration read from a bake file, or from
# environment variables such as BUILDX_* or BUILDKIT_* set inside run: scripts or env: blocks.
_FREE: tuple[tuple[str, ...], tuple[str, ...]] = ((), ())
RELEASE_ACTIONS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # No cache input at the pinned SHA (action.yml read):
    # actions/checkout 3d3c42e5aac5ba805825da76410c181273ba90b1
    "actions/checkout": _FREE,
    # actions/download-artifact 9000827ccba6bdab643e8b6fd33ac0654aef8333 (the artifact store)
    "actions/download-artifact": _FREE,
    # actions/upload-artifact cf430e030ddbb5b0abf93d22962f4752f3646cd9 (the artifact store)
    "actions/upload-artifact": _FREE,
    # docker/login-action 8aa5831fb8d48d8f76594c7ddde61ae58019f550
    "docker/login-action": _FREE,
    # actions/attest-sbom c604332985a26aa8cf1bdc465b92731239ec6b9e
    "actions/attest-sbom": _FREE,
    # actions/attest-build-provenance 4d101475d8b20a2381f78447822ac1eab6504dd8
    "actions/attest-build-provenance": _FREE,
    # github/codeql-action/upload-sarif 33d7b85da1a04171e211befe2c739303b9a4c066
    "github/codeql-action/upload-sarif": _FREE,
    # google-github-actions/auth 7c6bc770dae815cd3e89ee6cdf493a5fab2cc093
    "google-github-actions/auth": _FREE,
    # google-github-actions/get-gke-credentials 3da1e46a907576cefaa90c484278bb5b259dd395
    "google-github-actions/get-gke-credentials": _FREE,
    # docker/setup-buildx-action f87e5991a6d7451dcb8d9637bfbc97413f497069: the input cache-binary
    # (default 'true') caches the buildx binary in the GitHub Actions cache, so it must be false.
    "docker/setup-buildx-action": (("cache-binary",), ()),
    # docker/build-push-action c3c9e263c25d99ce0380d002d59b67737d91b0dc: cache-from and cache-to
    # are the build cache inputs (no default); they must not be set.
    "docker/build-push-action": ((), ("cache-from", "cache-to")),
}


def _release_docs(docs: list[Doc]) -> list[Doc]:
    """The docs reachable from release.yml through called workflows and local composite actions."""
    by_name = {d.name: d for d in docs}
    root = next((d for d in docs if d.name.endswith(RELEASE_ROOT)), None)
    if root is None:
        return []
    prefix = root.name[: -len(RELEASE_ROOT)]
    reached: dict[str, Doc] = {}
    todo = [root]
    while todo:
        doc = todo.pop()
        if doc.name in reached:
            continue
        reached[doc.name] = doc
        refs = [str(j.get("uses", "")) for _, j in doc.jobs()]
        refs += [str(st.get("uses", "")) for st in doc.steps()]
        for ref in refs:
            ref = ref.strip().removeprefix("./").rstrip("/")  # noqa: PLW2901
            if ref.startswith(".github/workflows/"):
                names = [prefix + ref]
            elif ref.startswith(".github/actions/"):
                names = [f"{prefix}{ref}/action.yml", f"{prefix}{ref}/action.yaml"]
            else:
                continue
            todo += [by_name[n] for n in names if n in by_name]
    return list(reached.values())


def _action_problem(ref: str, with_: Any) -> str | None:
    """Why an external action may not run on the release path, or None."""
    name = ref.split("@", maxsplit=1)[0].strip().lower()
    if name.startswith("actions/cache"):
        return f"{name} is a cache"
    if name not in RELEASE_ACTIONS:
        return (
            f"{name} is not in RELEASE_ACTIONS: check whether it caches (an input, or by default) "
            "and add it to the table in ci_hardening_lint.py"
        )
    must_be_false, must_be_absent = RELEASE_ACTIONS[name]
    # Action inputs are case-insensitive, so are the keys here.
    values = {str(k).lower(): v for k, v in (with_.items() if isinstance(with_, LDict) else [])}
    for key in must_be_false:
        if str(values.get(key, "")).strip().lower() != "false":
            return f"{name} caches by default: set `{key}: false`"
    for key in must_be_absent:
        if key in values:
            return f"{name} sets `{key}`"
    return None


def _step_cache(step: LDict) -> str | None:
    """What cache this step reads or writes, or None."""
    ref = str(step.get("uses", "")).strip()
    with_ = step.get("with")
    if ref.startswith("docker://"):
        return (
            f"{ref.split('@', maxsplit=1)[0]} is a container step; the lint cannot see its caching"
        )
    if ref and not ref.startswith("./"):
        problem = _action_problem(ref, with_)
        if problem:
            return problem
    # Backstop for every step: no cache text at all on the release path, whatever the action.
    texts = [str(step.get("run", ""))]
    if isinstance(with_, LDict):
        texts += list(_scalars(with_))
    return "a build cache type or flag" if any(_CACHE_WORDS.search(t) for t in texts) else None


def _local_target_missing(ref: str, prefix: str, names: set[str]) -> bool:
    ref = ref.strip().removeprefix("./").rstrip("/")
    if not ref.startswith(".github/actions/"):
        return False
    return not {f"{prefix}{ref}/action.yml", f"{prefix}{ref}/action.yaml"} & names


def check_no_release_cache(docs: list[Doc]) -> list[Finding]:
    reached = _release_docs(docs)
    if not reached:
        return []
    root = next(d for d in reached if d.name.endswith(RELEASE_ROOT))
    prefix = root.name[: -len(RELEASE_ROOT)]
    names = {d.name for d in docs}
    found: list[Finding] = []

    def add(doc: Doc, line: int, what: str) -> None:
        found.append(Finding(doc.name, line, "no-release-cache", f"{what}: {NO_CACHE_REASON}"))

    for doc in reached:
        for _, job in doc.jobs():
            ref = str(job.get("uses", "")).strip()
            if ref and not ref.startswith("./"):
                add(
                    doc, job.line, f"{ref} is a remote workflow; the lint cannot verify its caching"
                )
        runs = doc.data.get("runs")
        if isinstance(runs, LDict) and str(runs.get("using", "")).strip().lower() != "composite":
            add(
                doc,
                1,
                f"{doc.name} is a {runs.get('using')} action; only composites can be checked",
            )
        for step in doc.steps():
            if _local_target_missing(str(step.get("uses", "")), prefix, names):
                add(doc, step.line, f"{step.get('uses')} has no readable action.yml")
            if what := _step_cache(step):
                add(doc, step.line, what)
    return found


def check_timeouts(doc: Doc) -> list[Finding]:
    return [
        Finding(doc.name, job.line, "timeouts", f"job {job_id} has no timeout-minutes")
        for job_id, job in doc.jobs()
        if "uses" not in job and "timeout-minutes" not in job
    ]


def check_no_dry_run_env(doc: Doc) -> list[Finding]:
    return [
        Finding(
            doc.name,
            number,
            "no-dry-run-env",
            "DRY_RUN appears here; the scanners treat it as skip-everything",
        )
        for number, line in enumerate(doc.lines, start=1)
        if re.search(r"\bDRY_RUN\b", line)
    ]


def _documented(doc: Doc, line: int) -> bool:
    here = doc.lines[line - 1]
    above = doc.lines[line - 2].strip() if line > 1 else ""
    return "#" in here or above.startswith("#")


def check_documented_permissions(doc: Doc) -> list[Finding]:
    found: list[Finding] = []
    blocks: list[Any] = [doc.data]
    blocks += [j for _, j in doc.jobs()]
    for holder in blocks:
        perms = holder.get("permissions")
        if isinstance(perms, str):
            if perms.strip() not in ("", "{}"):
                line = holder.key_lines["permissions"]
                if not _documented(doc, line):
                    found.append(
                        Finding(
                            doc.name,
                            line,
                            "documented-permissions",
                            f"permissions: {perms} needs a comment saying why",
                        )
                    )
        elif isinstance(perms, LDict):
            for scope, level in perms.items():
                line = perms.key_lines[scope]
                if (scope, level) != ("contents", "read") and not _documented(doc, line):
                    found.append(
                        Finding(
                            doc.name,
                            line,
                            "documented-permissions",
                            f"{scope}: {level} needs a comment (same or previous line)",
                        )
                    )
    return found


PER_DOC_CHECKS: tuple[Check, ...] = (
    check_pinned_uses,
    check_top_permissions,
    check_no_prt_checkout,
    check_no_inline_secrets,
    check_image_digest,
    check_image_push_isolation,
    check_environment_for_secrets,
    check_timeouts,
    check_no_dry_run_env,
    check_documented_permissions,
)


def gather(root: Path) -> tuple[list[Doc], list[str]]:
    """Parse every workflow and composite action; return (docs, parse-error lines)."""
    wf, ac = root / ".github" / "workflows", root / ".github" / "actions"
    paths = sorted(
        [
            *wf.glob("*.yml"),
            *wf.glob("*.yaml"),
            *ac.glob("**/action.yml"),
            *ac.glob("**/action.yaml"),
        ]
    )
    docs: list[Doc] = []
    errors: list[str] = []
    for path in paths:
        name = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        try:
            node = yaml.compose(text)
        except yaml.YAMLError as err:
            mark = getattr(err, "problem_mark", None)
            line = mark.line + 1 if mark else 1
            problem = str(getattr(err, "problem", None) or "invalid YAML")
            errors.append(f"{name}:{line}: parse-error {problem}")
            continue
        data = _convert(node) if node is not None else LDict(1)
        if isinstance(data, LDict):
            docs.append(Doc(name, text, data, text.splitlines()))
        else:
            errors.append(f"{name}:1: parse-error the top level is not a mapping")
    return docs, errors


def lint(docs: list[Doc]) -> list[Finding]:
    findings: list[Finding] = []
    for doc in docs:
        for check in PER_DOC_CHECKS:
            findings += check(doc)
    findings += check_provenance(docs) + check_osv_coverage(docs) + check_no_release_cache(docs)
    return sorted(findings, key=lambda f: (f.file, f.line, f.check))


def main(argv: list[str]) -> int:
    as_json = "--json" in argv[1:]
    positional = [a for a in argv[1:] if a != "--json"]
    root = Path(positional[0]) if positional else Path()
    if not root.is_dir():
        print(f"ci_hardening_lint: not a directory: {root}", file=sys.stderr)
        return USAGE_ERROR
    docs, errors = gather(root)
    if not docs and not errors:
        print("ci_hardening_lint: no workflows or actions found under .github/", file=sys.stderr)
        return USAGE_ERROR
    findings = lint(docs)
    if as_json:
        print(
            json.dumps(
                {
                    "scan": "ci-hardening",
                    "findings": [f.__dict__ for f in findings],
                    "errors": errors,
                    "status": "error" if errors else "red" if findings else "green",
                },
                indent=2,
            )
        )
    else:
        for finding in findings:
            print(finding.render())
        for error in errors:
            print(error)
        bad_files = len({f.file for f in findings})
        print(
            f"ci-hardening: {len(findings)} finding(s) in {bad_files} of {len(docs)} file(s), "
            f"{len(errors)} parse error(s)"
        )
    if errors:
        return USAGE_ERROR
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
