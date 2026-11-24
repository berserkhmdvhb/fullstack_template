# ADR-0038: The template and its guides use generic names only

## Status

Accepted.

## Date

2026-11-22

## Context

The template was first written alongside real deployments, so its guides were tempted to name real sites,
repositories, domains and plans. Every such name leaks private context into a public template and is wrong for the
next adopter.

## Decision

Public files name placeholders only: "your site", "your site repository", "your infrastructure-as-code repository",
`your-domain.example`, `<owner>/<repo>`, and the service name `your_service_name`, which the adoption tool replaces.
No file names the owner's other projects or repositories, links to them, or cites another repository's plan ids.
Tests enforce it: one holds only the hashes of the banned names, so the test itself does not carry them; others keep
host paths and non-public working directories out of tracked files.

## Consequences

- Good: the template is reusable as is, and nothing private leaks through a doc.
- Bad: examples are less concrete; an adopter fills in their own names.

## Alternatives considered

- Real names with a "replace these" note: rejected, they leak and they rot.

## Links

- [`tests/supply_chain/test_docs_generic_names.py`](../../tests/supply_chain/test_docs_generic_names.py),
  [`tests/supply_chain/test_no_host_paths.py`](../../tests/supply_chain/test_no_host_paths.py),
  [`tests/supply_chain/test_no_private_dirs.py`](../../tests/supply_chain/test_no_private_dirs.py)
- [Adaptation](../adaptation.md), [Platform setup](../platform-setup.md)
