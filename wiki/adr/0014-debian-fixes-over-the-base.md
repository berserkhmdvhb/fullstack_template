# ADR-0014: Images may take Debian's fix before the base image ships it

## Status

Accepted.

## Date

2026-11-22

## Context

The release's Grype gate blocks on a fixable High or Critical CVE. In November 2026 it failed on glibc CVEs present
in every distroless Debian 13 base: Debian had published the fix, but the distroless publisher had not rebuilt.
The template cannot rebuild the base, and waiting would block every release on someone else's schedule.

## Decision

Each Dockerfile has a `debian-fixes` stage. Its `DEBIAN_FIXES` list names `<package>=<version>` entries;
`scripts/docker/debian-fixes.sh` downloads each `.deb` from the Debian archive, checked against its signed indices,
and unpacks it with its dpkg record; the runtime stage copies it over the base, so the image and its SBOM carry the
fixed version. The script refuses an entry the base already has, so the entry is removed in the Renovate pull
request that moves the base digest past it, and a fix never downgrades the base. It refuses any change beyond the
package's own files.

## Consequences

- Good: a fixable High is fixed in the next build, not accepted.
- Good: the SBOM tells the truth about the version that runs.
- Bad: the image differs from its published base; the maintainer owns the entries until the base catches up.
- Bad: the package's maintainer scripts never run, and a package that would replace a symlink, change a file type,
  a directory's mode or a conffile, or drop a file is refused, so not every fix can be laid over.

## Alternatives considered

- A time-boxed Grype ignore or VEX entry: rejected, it would accept a fixable High.
- Switch to a base with a package manager: rejected, it undoes [ADR-0013](./0013-distroless-runtime-one-interpreter.md).

## Links

- [`scripts/docker/debian-fixes.sh`](../../scripts/docker/debian-fixes.sh), [`Dockerfile.backend`](../../Dockerfile.backend)
- [Containerization: Debian fixes](../containerization.md#debian-fixes-the-base-has-not-shipped-yet)
- [`tests/supply_chain/test_debian_fixes.py`](../../tests/supply_chain/test_debian_fixes.py)
