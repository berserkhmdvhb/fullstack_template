"""Whether a tree is the template itself or a copy adopted from it: the template's placeholder
package still holds Python. The one test-side check; scripts/ci/check-placeholder-leftovers.sh,
the `Adopted copy` CI job and tests/scripts/adopt-e2e.bats ask it of the same directory."""

from __future__ import annotations

from pathlib import Path

# In pieces, so the adapter never rewrites it.
PLACEHOLDER_PACKAGE = "your_" + "service_name"


def is_template(root: Path) -> bool:
    """True in the template, False in an adopted copy (the package was renamed)."""
    return any((root / "src" / PLACEHOLDER_PACKAGE).glob("*.py"))
