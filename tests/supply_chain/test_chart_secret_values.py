"""No committed chart values file holds a credential; the secret example holds placeholders only."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent.parent
CHART = ROOT / "charts" / "your-service-name"
SECRET_KEYS = ("dbPassword", "statsPass")
GIT: str = shutil.which("git") or "/usr/bin/git"


def test_the_secret_example_holds_placeholders_only() -> None:
    example = yaml.safe_load((CHART / "values.secret.yaml.example").read_text(encoding="utf-8"))
    secrets = example["secrets"]
    assert set(secrets) >= set(SECRET_KEYS)
    for key in SECRET_KEYS:
        assert str(secrets[key]).startswith("change-me-"), key


def test_the_real_secret_values_file_is_never_tracked() -> None:
    path = "charts/your-service-name/values.secret.yaml"
    ignored = subprocess.run(
        [GIT, "check-ignore", "-q", path], cwd=ROOT, capture_output=True, check=False
    )
    assert ignored.returncode == 0, f"{path} is not gitignored"
    tracked = subprocess.run(
        [GIT, "ls-files", "--error-unmatch", path], cwd=ROOT, capture_output=True, check=False
    )
    assert tracked.returncode != 0, f"{path} is tracked"


def test_no_committed_values_file_sets_a_password() -> None:
    for path in [CHART / "values.yaml", *sorted(CHART.glob("values.*.yaml"))]:
        secrets = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("secrets") or {}
        for key in SECRET_KEYS:
            assert not secrets.get(key), f"{path.name}: secrets.{key} must stay empty"
