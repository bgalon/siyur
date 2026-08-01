"""Tier-2 database harness (T016) — a real PostGIS, migrated, clean between tests.

Where the database comes from, in order:

1. **``SIYUR_DATABASE_URL`` is set** → use it verbatim. That is the CI service container
   (``ci.yml`` job 3) and the documented local ``docker compose up postgis`` workflow.
2. **Not set, and running in CI** → skip. CI's Tier-1 lane (job 2) deliberately has no
   database; Tier-2 runs in job 3, which *does* set the variable.
3. **Not set, local** → start ``postgis/postgis:16-3.4`` with testcontainers. If Docker is
   not running, skip.

Tier-2 tests **skip, never fail**, when no database is reachable — a Tier-1-only
``uv run pytest`` stays green on a laptop with Docker off. The one exception is a URL that
is explicitly configured but dead *in CI*: that is a broken gate, not an absent
dependency, so it fails loudly rather than turning job 3 into a silent no-op.

CI selects these tests with ``-m integration``; the marker is registered below (not in
``pyproject.toml``).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from commons.db import DATABASE_URL_ENV, Base, create_db_engine

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Same image as ``docker-compose.yml`` and the ``ci.yml`` job-3 service container.
POSTGIS_IMAGE = "postgis/postgis:16-3.4"

_COMPOSE_HINT = (
    f"no PostGIS reachable: {DATABASE_URL_ENV} is unset and Docker is unavailable. "
    "Start one with `docker compose up -d postgis` and export "
    f"{DATABASE_URL_ENV}='postgresql+psycopg://siyur:siyur@localhost:5432/siyur'."
)


def pytest_configure(config: pytest.Config) -> None:
    """Register the Tier-2 marker CI selects on (``pytest -m integration``)."""
    config.addinivalue_line(
        "markers",
        "integration: Tier-2 test against a real dependency (PostGIS); "
        "skipped when none is reachable.",
    )


@contextmanager
def _database_url_env(url: str) -> Iterator[None]:
    """Expose ``url`` as ``SIYUR_DATABASE_URL`` — the only channel ``alembic/env.py`` reads."""
    previous = os.environ.get(DATABASE_URL_ENV)
    os.environ[DATABASE_URL_ENV] = url
    try:
        yield
    finally:
        if previous is None:
            del os.environ[DATABASE_URL_ENV]
        else:
            os.environ[DATABASE_URL_ENV] = previous


def _run_migrations(url: str, revision: str = "head") -> None:
    """Run Alembic against ``url`` — the same migration path production uses, not create_all."""
    from alembic.config import Config

    from alembic import command

    config = Config(str(REPO_ROOT / "alembic.ini"))
    with _database_url_env(url):
        command.upgrade(config, revision)


def _reachable(url: str) -> bool:
    engine = create_db_engine(url)
    try:
        with engine.connect():
            return True
    except OperationalError:
        return False
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def postgis_url() -> Iterator[str]:
    """A URL for a live PostGIS, or a skip. See the module docstring for the order."""
    configured = os.environ.get(DATABASE_URL_ENV)
    if configured:
        if not _reachable(configured):
            message = f"{DATABASE_URL_ENV} is set but no database answers at that URL."
            # In CI an unreachable-yet-configured DB means the service container broke;
            # skipping would leave job 3 green with zero tests run.
            if os.environ.get("CI"):
                pytest.fail(message)
            pytest.skip(message)
        yield configured
        return

    if os.environ.get("CI"):
        pytest.skip(f"Tier-1 CI lane: {DATABASE_URL_ENV} is unset (Tier 2 runs in job 3).")

    try:
        # `testcontainers.community.*` — the 4.15 home of the module-specific containers
        # (the old `testcontainers.postgres` path still works but is deprecated).
        from testcontainers.community.postgres import PostgresContainer

        container = PostgresContainer(
            POSTGIS_IMAGE, username="siyur", password="siyur", dbname="siyur", driver="psycopg"
        )
        container.start()
    except Exception as exc:  # docker absent/unreachable, image pull failure, …
        pytest.skip(f"{_COMPOSE_HINT} ({type(exc).__name__}: {exc})")

    try:
        yield container.get_connection_url()
    finally:
        container.stop()


@pytest.fixture(scope="session")
def db_engine(postgis_url: str) -> Iterator[Engine]:
    """A migrated database (``alembic upgrade head``) and an engine onto it."""
    _run_migrations(postgis_url)
    engine = create_db_engine(postgis_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    """A session over an **empty** commons — every table is cleared before the test.

    Cleared with ORM deletes in reverse dependency order rather than raw SQL, so no query
    string is ever built by string formatting.
    """
    with db_engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    with Session(db_engine) as session:
        yield session
