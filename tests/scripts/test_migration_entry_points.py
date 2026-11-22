# your-service-name — adaptable service template (see wiki/getting-started.md)
"""The app does not migrate at startup, so every entry point that serves it migrates first."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
MODULE = "your_service_name.backend.db.run_migrations"


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_the_dev_launcher_migrates_before_serving() -> None:
    script = _text("run_backend.sh")
    assert f"python -m {MODULE}" in script
    assert script.index(f"python -m {MODULE}") < script.index(
        "exec python -m your_service_name.backend.serve"
    )


def test_the_runtime_entrypoint_migrates_with_the_module() -> None:
    assert f"python -m {MODULE}" in _text("infra/runtime/entrypoint.sh")


def test_the_image_launcher_does_not_migrate() -> None:
    """The chart's Job (and compose's migrate service) migrates; Pods only serve."""
    assert "run_migrations" not in _text("src/your_service_name/backend/serve.py")


def test_compose_runs_a_migration_before_the_backend() -> None:
    services = yaml.safe_load(_text("docker-compose.yml"))["services"]
    assert services["migrate"]["command"][-1] == MODULE
    assert services["backend"]["depends_on"]["migrate"] == {
        "condition": "service_completed_successfully"
    }


def test_the_ci_job_that_boots_the_backend_migrates_first() -> None:
    workflow = _text(".github/workflows/reusable-backend.yml")
    assert workflow.index(f"python -m {MODULE}") < workflow.index(
        "python -m your_service_name.backend.serve"
    )
