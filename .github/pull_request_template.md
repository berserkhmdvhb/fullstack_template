<!--
Title: a Conventional Commit, `type(scope)!: subject` (the PR title check; the squash merge makes it the commit on
main, and release-please reads it). Types: build, chore, ci, docs, feat, fix, perf, refactor, revert, style, test.
`!` marks a breaking change, and is required when the pull request carries the `breaking-change` label.
See wiki/releasing.md, "Pull request titles".
-->

## What and why

<!-- What changes, and why. Link the issue it closes (Closes #123). -->

## How it was tested

<!-- The commands you ran and their result, for example `make check-all-dry && make test`, the frontend gates,
     `bats tests/scripts/`. Say what you did not run, and why. -->

## Checklist

- [ ] I walked the [pull-request checklist](https://github.com/your-org/your-repo/blob/main/CONTRIBUTING.md#appendix-a-pull-request-checklist-copy-paste) in CONTRIBUTING.
- [ ] The title is a Conventional Commit, with `!` if this is a breaking change (and the `breaking-change` label if it breaks the API contract).
