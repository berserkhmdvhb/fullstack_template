# Security policy

## Supported versions

Only the latest release (the newest `v*` tag, see [Releasing](wiki/releasing.md)) gets security fixes. Fixes land on
`main` and ship in the next release; older releases are not patched. Upgrade to the latest release first.

## Reporting a vulnerability

Report it **privately**, through GitHub's private vulnerability reporting: the repository's **Security** tab, then
**Report a vulnerability**
([direct link](https://github.com/your-org/your-repo/security/advisories/new)). Please don't open a public issue, pull
request or discussion for it.

<!-- Adopters: to offer an email address as well, add it here. Private vulnerability reporting stays the first
     route; scripts/github-settings.sh turns it on. -->

Include what you can: the affected version or commit, the component, steps to reproduce, and the impact you see.

## What to expect

- An acknowledgement within 5 working days.
- An assessment (accepted or not, and the severity) within 10 working days, discussed in the private advisory.
- A fix in the time the [vulnerability management SLA](wiki/vulnerability-management.md#issues-and-the-sla) gives its
  severity, then a release and a published advisory crediting you, unless you prefer not to be named.

This is a volunteer-maintained template: these are targets, not a contract.

## Scope and background

- [Threat model](wiki/threat-model.md): the trust boundaries, the mitigations and what is not mitigated.
- [Security](wiki/security.md): the application, container and Kubernetes controls.
- [Supply-chain security](wiki/supply-chain-security.md) and [vulnerability management](wiki/vulnerability-management.md):
  how images are built, signed and re-scanned.

A service built from this template has its own owner: report a vulnerability in that service to its repository.
