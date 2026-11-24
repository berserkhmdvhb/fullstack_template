# ADR-0039: Template upgrades are not Copier-based yet

## Status

Deferred.

## Date

2026-11-20

## Context

Adopters copy the template once and then want its later fixes. Copier (and tools like it) can re-apply a template
to a generated project and merge the differences. To do that, the template's files become Jinja templates with the
service name and other answers as variables.

## Decision

Copier is not adopted now. The template must stay a runnable, tested repository as it is: every test, workflow and
chart render runs on the template itself, with the placeholder name. Templating names across files would make the
repository a template source that cannot run or be tested until rendered. Upgrades follow an upstream-sync path
instead: an `upstream` remote and a merge or cherry-pick per template release, and adopter-action notes in each
changelog entry (the changelog's "Upgrading" list). A recorded template version and an upgrade guide are planned.

Revisit when one of these holds: several adopters drift far enough that merges stop being practical; Copier (or a
similar tool) can update a project generated from a plain, runnable repository; or the template grows options that
generate different file sets.

## Consequences

- Good: the template stays green on its own CI; an adopter's repository is ordinary Git.
- Bad: upgrades are merges, with conflicts where an adopter changed the same lines.
- Bad: the adoption tool's renames show up as conflicts on files the upstream changes.

## Alternatives considered

- Copier now: deferred for the reasons above.
- Cookiecutter: one-shot generation with no update path.

## Links

- [Adaptation](../adaptation.md), [`infra/adapt/replace_service_name.sh`](../../infra/adapt/replace_service_name.sh)
- [Changelog](../changelog.md)
