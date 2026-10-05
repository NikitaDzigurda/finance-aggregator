from __future__ import annotations

import os

import pytest
from sqlalchemy.engine import make_url


def pytest_sessionstart(session: pytest.Session) -> None:
    """Refuse to run PostgreSQL tests against the local application database."""
    database_url = os.getenv("FINANCE_TEST_DATABASE_URL")
    if database_url is None:
        return
    database_name = make_url(database_url).database or ""
    if "test" not in database_name.casefold():
        raise pytest.UsageError(
            "FINANCE_TEST_DATABASE_URL must point to a dedicated test database "
            "whose name contains 'test'"
        )
