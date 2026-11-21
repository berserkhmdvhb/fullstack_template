# your-service-name — adaptable service template (see wiki/getting-started.md)
"""
Schemathesis property tests: the app's live OpenAPI document against the app, in process (ASGI).

Checks (Schemathesis 4 names): no server error, status-code conformance, content-type conformance
and response-schema conformance. Problem responses (RFC 9457) are documented in the OpenAPI
document, so they must conform too.

Deterministic: ``derandomize=True`` fixes the examples (the seed) and ``max_examples`` bounds
each operation. Runs with ``make test-contract`` on whichever database the fixtures selected
(PostgreSQL in CI). Schemathesis starts the app's lifespan itself; the schema is migrated first.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Final

import pytest
import schemathesis
from hypothesis import HealthCheck, settings
from schemathesis.checks import not_a_server_error
from schemathesis.python import asgi
from schemathesis.specs.openapi.checks import (
    content_type_conformance,
    response_schema_conformance,
    status_code_conformance,
)

from tests.conftest import POSTGRES_TEST_URL, migrate_database
from your_service_name.backend.main import create_app
from your_service_name.backend.settings import Settings

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.contract

MAX_EXAMPLES: Final[int] = 50
# Far above what the examples send, so the documented per-IP limit (429) never hides a real answer.
RELAXED_RATE_LIMIT: Final[str] = "1000000/minute"
CHECKS: Final = (
    not_a_server_error,
    status_code_conformance,
    content_type_conformance,
    response_schema_conformance,
)


@pytest.fixture(scope="module")
def contract_app(tmp_path_factory: pytest.TempPathFactory) -> Iterator[object]:
    """The app on the test database, with only the documented rate-limit setting relaxed."""
    if POSTGRES_TEST_URL:
        url = POSTGRES_TEST_URL
    else:
        db: Path = tmp_path_factory.mktemp("contract") / "test.db"
        url = f"sqlite+aiosqlite:///{db}"
    mp = pytest.MonkeyPatch()
    mp.setenv("TEST_DATABASE_URL", url)
    try:
        migrate_database()
        yield create_app(Settings(RATE_LIMIT_DEFAULT=RELAXED_RATE_LIMIT))
    finally:
        # The lifespan Schemathesis started holds the database engine (a module global): end it.
        asgi.shutdown_lifespans()
        mp.undo()


@pytest.fixture
def api_schema(contract_app: object) -> schemathesis.schemas.BaseSchema:
    return schemathesis.openapi.from_asgi("/openapi.json", contract_app)


schema = schemathesis.pytest.from_fixture("api_schema")


@schema.parametrize()
@settings(
    max_examples=MAX_EXAMPLES,
    derandomize=True,
    deadline=None,
    database=None,
    suppress_health_check=list(HealthCheck),
)
def test_operation_conforms_to_the_openapi_document(case: schemathesis.Case[Any]) -> None:
    case.call_and_validate(checks=list(CHECKS))
