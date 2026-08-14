"""Tier-2 database harness (T016) — a real PostGIS, migrated, clean between tests.

Where the database comes from, in order:

1. **``SIYUR_DATABASE_URL`` is set** → use a ``_test`` database derived from it, created on
   demand. That is the CI service container (``ci.yml`` job 3) and the documented local
   ``docker compose up postgis`` workflow. **The configured database itself is never
   touched**: :func:`db_session` truncates every table, which is correct against a database
   made to be thrown away and destroys the work of anyone sharing the server otherwise
   (FAIL-011 — see :func:`_disposable_url`).
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
from collections.abc import Generator, Iterator
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


#: Suffix appended to the configured database's name to get the one tests may destroy.
TEST_DB_SUFFIX = "_test"


class UndisposableDatabase(RuntimeError):
    """The configured database cannot be made disposable, so no test may truncate it."""


def _derive_disposable_url(configured: str) -> str:
    """The **pure** half of :func:`_disposable_url`: string work only, never connects.

    Split out so the derivation is testable in Tier 1, which has no database at all
    (``ci.yml`` job 2). Keeping it inside the connecting function meant the guardrail's own
    unit tests needed a server — they failed in CI for want of one, which is a poor way for a
    test about not touching databases to behave.
    """
    from sqlalchemy.engine import make_url

    url = make_url(configured)
    name = url.database
    if not name:
        raise UndisposableDatabase(f"{DATABASE_URL_ENV} names no database: {configured!r}")
    if name.endswith(TEST_DB_SUFFIX):
        return configured
    return url.set(database=f"{name}{TEST_DB_SUFFIX}").render_as_string(hide_password=False)


def _disposable_url(configured: str) -> str:
    """Derive ``<name>_test`` from ``configured``, creating it if absent (FAIL-011).

    **Why this exists.** :func:`db_session` deletes every row in every table before each
    test. That is correct against a database created to be thrown away and catastrophic
    against one someone is working in — and until now the two were the same database.
    ``docker-compose.yml`` binds a fixed ``${SIYUR_DB_PORT:-5432}``, so two checkouts land on
    one container by default; a worktree isolates *files* and isolates nothing else. On
    2026-08-14 a Tier-2 run truncated a concurrently-running dev stack's data mid-session,
    and the other operator spent half an hour diagnosing it as a persistence bug in
    ``POST /areas`` — the endpoint was committing correctly and the rows were being deleted
    out from under a live server. The symptom pointed at innocent code.

    **Why a derived database rather than an opt-in variable.** Requiring a
    ``SIYUR_TEST_DATABASE_URL`` (or refusing a name without ``_test``) would work, but CI's
    database is *also* called ``siyur`` (``ci.yml`` job 3), so it would need a workflow edit
    and would leave everyone a variable to remember — and the failure mode of forgetting is
    silent data loss. Deriving needs no configuration anywhere and cannot be bypassed by
    being in a hurry: "delete every row" becomes structurally unable to reach a database
    anyone works in, rather than merely discouraged from doing so.

    **Every source comes through here**, including the testcontainer — which is already
    disposable by construction, so its `CREATE DATABASE` is ceremony. That is deliberate: the
    container branch used to skip this, the two paths disagreed, and nothing noticed because
    ``ci.yml`` job 3 always sets ``SIYUR_DATABASE_URL`` and therefore only ever exercises the
    configured one. One invariant with one code path is worth a redundant statement.
    """
    from sqlalchemy.engine import make_url

    disposable = _derive_disposable_url(configured)
    if disposable == configured:
        return configured

    name = make_url(configured).database
    target = make_url(disposable).database
    assert target is not None  # _derive_disposable_url refuses a URL naming no database
    import psycopg
    from psycopg import sql

    admin = configured.replace("postgresql+psycopg://", "postgresql://")
    try:
        with psycopg.connect(admin, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (target,))
            if cur.fetchone() is None:
                # Identifier(), never an f-string: the name is derived, but a quoted
                # identifier is the difference between a derived name and an injected one.
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target)))
    except psycopg.Error as exc:  # no CREATEDB, or the server went away
        raise UndisposableDatabase(
            f"cannot create the disposable database {target!r}: {exc}. Tests refuse to "
            f"truncate {name!r}, which is a database someone may be using (FAIL-011). Grant "
            f"CREATEDB, or point {DATABASE_URL_ENV} at a database whose name already ends in "
            f"{TEST_DB_SUFFIX!r}."
        ) from exc

    with (
        psycopg.connect(
            disposable.replace("postgresql+psycopg://", "postgresql://"), autocommit=True
        ) as conn,
        conn.cursor() as cur,
    ):
        # The migrations assume PostGIS; CI enables it on the configured database only.
        cur.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    return disposable


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
    """A **disposable** PostGIS URL, or a skip. See the module docstring for the order.

    The single place the disposable database is derived, so every source funnels through one
    rule rather than each remembering to apply it. That is not stylistic: until 2026-08-14 the
    derivation lived on the configured branch only, the testcontainer branch handed out its URL
    undisposed, and CI never noticed because job 3 always sets ``SIYUR_DATABASE_URL`` and
    therefore only ever executes the configured branch.
    """
    source = _postgis_source()
    raw = next(source)
    try:
        # Derived for the container too, though it is already disposable by construction and
        # the `CREATE DATABASE` is therefore ceremony. One invariant with one code path beats
        # two rules that happen to agree — they did not agree, and nothing caught it.
        yield _disposable_url(raw)
    finally:
        # Propagates into `_postgis_source`'s `finally`, stopping the container if it started.
        source.close()


def _postgis_source() -> Generator[str, None, None]:
    """The raw URL and its lifecycle, undisposed. Yields exactly once, then cleans up.

    Split from :func:`postgis_url` so the **testcontainer branch is directly reachable from a
    test**. It previously was not, which is why an undisposed URL from that branch reached
    ``main``: nothing exercised the path, and CI structurally could not.
    """
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
