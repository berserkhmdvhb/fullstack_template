---
title: The CI/CD pipeline
description: What runs on a pull request, on main and on a release tag.
---

This page is a stub; its content is coming.

```mermaid
flowchart LR
  pr[Pull request] --> ci[CI OK]
  main[Push to main] --> ci
  main --> pages[Docs site deploy]
```

The pipeline is described in [CI/CD](../../../../wiki/ci-cd.md).
