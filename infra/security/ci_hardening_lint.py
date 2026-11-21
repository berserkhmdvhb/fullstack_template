#!/usr/bin/env python3
"""CI-hardening lint for this repo's GitHub workflows.

The pipeline that builds the software is its own attack surface, so the workflows are linted for the
controls that protect them. Scans ``.github/workflows/*.{yml,yaml}`` and
``.github/actions/*/action.{yml,yaml}``.

Output: one line per finding, ``file:line: CHECK-ID message``, then a summary. A SKIP is printed and
never fails. Exit 1 on any finding, 0 when clean, 2 on a usage or parse error (``file:line:
parse-error ...``). ``--json`` prints a structured
report instead (used by ``check-ci-hardening.sh``).

Checks (ids are stable):
  pinned-uses, top-permissions, no-prt-checkout, no-inline-secrets, image-digest, provenance
  (SKIP until phase 2), osv-coverage, environment-for-secrets, timeouts, no-dry-run-env,
  documented-permissions.

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

PROVENANCE_ENABLED = (
    False  # Switch on once release.yml signs and attests images; the provenance check then gates.
)
USAGE_ERROR = 2
PROVENANCE_SKIP = "SKIP provenance: not enforced yet (set PROVENANCE_ENABLED once release.yml attests)"

_SHA_REF = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40}$")
_VERSION_COMMENT = re.compile(r"#\s*v\d+(\.\d+)*\S*\s*$")
_DOCKER_PIN = re.compile(r"^docker://\S+@sha256:[0-9a-f]{64}$")
_IMAGE_KEY = re.compile(r"(?i)(^|[_-])image$")
_IMAGE_PIN = re.compile(r"@sha256:[0-9a-f]{64}")
_WHOLE_EXPR = re.compile(r"^\$\{\{[^}]*\}\}$")
_SECRET_REF = re.compile(r"(?<![\w.-])secrets\b(?:\s*\.\s*(\w+))?")
_QUOTED = re.compile(r"'(?:[^']|'')*'")
_OSV_RUN = re.compile(r"osv-scanner|scan-osv\.sh")
_PROVENANCE = re.compile(
    r"attest-build-provenance|slsa-github-generator|attest-provenance|cosign attest"
)


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


def check_provenance(
    docs: list[Doc], *, enabled: bool = PROVENANCE_ENABLED
) -> tuple[list[Finding], list[str]]:
    """Return (findings, skips). Until phase 2 it only skips; enabled, release.yml must attest."""
    if not enabled:
        return [], [PROVENANCE_SKIP]
    for doc in docs:
        if re.search(r"workflows/release\.ya?ml$", doc.name) and any(
            _PROVENANCE.search(str(s.get("uses", "")) + " " + str(s.get("run", "")))
            for s in doc.steps()
        ):
            return [], []
    release = next(
        (d.name for d in docs if re.search(r"workflows/release\.ya?ml$", d.name)),
        ".github/workflows/release.yml",
    )
    return [
        Finding(release, 1, "provenance", "the release workflow has no provenance attestation step")
    ], []


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
    check_environment_for_secrets,
    check_timeouts,
    check_no_dry_run_env,
    check_documented_permissions,
)


def gather(root: Path) -> tuple[list[Doc], list[str]]:
    """Parse every workflow and composite action; return (docs, parse-error lines)."""
    wf, ac = root / ".github" / "workflows", root / ".github" / "actions"
    paths = sorted(
        [*wf.glob("*.yml"), *wf.glob("*.yaml"), *ac.glob("*/action.yml"), *ac.glob("*/action.yaml")]
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


def lint(
    docs: list[Doc], *, provenance: bool = PROVENANCE_ENABLED
) -> tuple[list[Finding], list[str]]:
    findings: list[Finding] = []
    for doc in docs:
        for check in PER_DOC_CHECKS:
            findings += check(doc)
    prov, skips = check_provenance(docs, enabled=provenance)
    findings += prov + check_osv_coverage(docs)
    return sorted(findings, key=lambda f: (f.file, f.line, f.check)), skips


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
    findings, skips = lint(docs)
    if as_json:
        print(
            json.dumps(
                {
                    "scan": "ci-hardening",
                    "findings": [f.__dict__ for f in findings],
                    "errors": errors,
                    "skipped": skips,
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
        for skip in skips:
            print(skip)
        bad_files = len({f.file for f in findings})
        print(
            f"ci-hardening: {len(findings)} finding(s) in {bad_files} of {len(docs)} file(s), "
            f"{len(errors)} parse error(s), {len(skips)} skipped"
        )
    if errors:
        return USAGE_ERROR
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
