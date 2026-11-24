---
title: Practices adopted
description: The engineering and supply-chain practices the template adopts, each with one line on why and where it is written down.
---

Each practice below is enforced by a gate, a script or a test, not by goodwill. The link holds the rule in full.

## Engineering

| Practice | Why | Where |
|---|---|---|
| The service name stays a placeholder | a literal name or port breaks the next adopter | [Conventions §1](../../../../CONVENTIONS.md#1-the-service-name-is-a-placeholder-never-a-literal) |
| One concern per layer | reaching past a layer makes the service hard to swap | [Conventions §2](../../../../CONVENTIONS.md#2-one-concern-per-layer-backend) |
| One source of truth per fact | a copied version or command drifts from the original | [Conventions §3](../../../../CONVENTIONS.md#3-one-source-of-truth-single-source--dry) |
| Configuration, not constants | ports, hosts and URLs differ per environment | [Conventions §4](../../../../CONVENTIONS.md#4-config-driven-not-hard-coded) |
| The version comes from Git tags | the tag is the release, so nothing else can disagree with it | [Conventions §6](../../../../CONVENTIONS.md#6-version-comes-from-git-never-from-a-literal), [Versioning](../../../../wiki/versioning.md) |
| Test first, then the gates, then the pull request | a failing test proves the change is needed and done | [Contributing](../../../../CONTRIBUTING.md#the-workflow-tdd--gates--pr), [Testing](../../../../wiki/testing.md) |
| Two required checks, `CI OK` and the title | a skipped or matrix job reports no stable name to require, so one gate job stands for them; the title check runs on a title edit, which `CI OK` does not | [Repository settings](../../../../wiki/github-repository-settings.md#required-status-checks) |
| Conventional Commit titles, squash merges | the title becomes the commit on `main`, and the release notes come from it | [Pull request titles](../../../../wiki/releasing.md#pull-request-titles) |
| The API contract is checked on every pull request | a breaking change must be deliberate and move the major version | [ADR-0027](../../../../wiki/adr/0027-api-contract-and-versioning.md), [API lifecycle](../../../../wiki/api-lifecycle.md) |
| Decisions are recorded | an adopter can keep a choice knowingly or replace it on purpose | [ADR index](../../../../wiki/adr/README.md) |
| Docs link, never restate, and a broken link fails CI | a second copy of a fact goes stale | [Conventions §3](../../../../CONVENTIONS.md#3-one-source-of-truth-single-source--dry), [ADR-0041](../../../../wiki/adr/0041-documentation-site.md) |
| Agents follow the same rules | one instruction file for every coding agent, pointing at the same sources | [`AGENTS.md`](../../../../AGENTS.md) |
| Generic names only | the template and its guides carry no one's names or private paths | [ADR-0038](../../../../wiki/adr/0038-generic-names-only.md) |

## Supply chain and delivery

| Practice | Why | Where |
|---|---|---|
| Image operations on `main` and tags only | a pull request's author must not be able to forge the chain | [Conventions §5](../../../../CONVENTIONS.md#5-image-level-ops-run-on-main-and-release-tags-only) |
| Pin everything: actions by SHA, images by digest, tools by checksum | a moved tag changes what runs without a review | [Dependency updates](../../../../wiki/security.md#dependency-updates-and-pinned-checksums) |
| Updates wait for the release to age | the ecosystem gets time to catch a malicious release before it reaches a lock | [ADR-0025](../../../../wiki/adr/0025-renovate-for-updates.md), [ADR-0015](../../../../wiki/adr/0015-uv-for-python.md) |
| No build cache on the release path | a poisoned cache layer would be signed as ours | [ADR-0017](../../../../wiki/adr/0017-release-builds-use-no-cache.md) |
| Keyless signing and provenance | no signing key to leak; the certificate names the workflow | [ADR-0018](../../../../wiki/adr/0018-cosign-keyless-and-kyverno-admission.md), [ADR-0016](../../../../wiki/adr/0016-slsa-build-l3-for-release-images.md) |
| Deploy by digest | what runs is byte for byte what was scanned and signed | [ADR-0020](../../../../wiki/adr/0020-deploy-by-digest.md) |
| Keyless deploys, one deployer per environment | no cloud key in GitHub, and dev's deployer cannot touch production | [ADR-0021](../../../../wiki/adr/0021-keyless-deploys-per-environment.md) |
| Security gates block pull requests | a known vulnerability or a disallowed licence is cheapest to stop before the merge | [ADR-0026](../../../../wiki/adr/0026-blocking-pr-security-gates.md) |
| The core supply-chain scripts are frozen | what a release claims must not change in passing | [ADR-0028](../../../../wiki/adr/0028-frozen-supply-chain-scripts.md) |
| Repository settings as a script | the gate, the environments and the policies are reviewed and repeatable | [ADR-0022](../../../../wiki/adr/0022-repository-settings-as-a-script.md) |
| A weekly re-scan of what runs | a vulnerability published after the release still gets an issue and a deadline | [ADR-0037](../../../../wiki/adr/0037-weekly-rescan-scope.md) |
| An AI reviewer, advisory only | a hint costs little; a verdict from an injectable model would be a hole | [ADR-0040](../../../../wiki/adr/0040-optional-ai-pr-review.md) |

## Go deeper

- [Conventions](../../../../CONVENTIONS.md): each rule with the smell that shows you are breaking it, and the fix.
- [Contributing](../../../../CONTRIBUTING.md): the workflow and the gates, by kind of change.
- [Code quality](../../../../wiki/code-quality.md) and [Supply-chain security](../../../../wiki/supply-chain-security.md).
- [`AGENTS.md`](../../../../AGENTS.md): the rules every coding agent follows here.
