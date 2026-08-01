"""Alembic environment — Spec 001 T003 (setup only; no migration generated here).

Two rules this file exists to satisfy:

1. **The DB URL comes from the process environment only** (`SIYUR_DATABASE_URL`) —
   never from `alembic.ini`, never from a `.env*` file (AGENTS.md: "Never read or
   write `.env*` or `secrets/`"). `alembic.ini`'s `sqlalchemy.url` is intentionally
   left as the library's inert placeholder; it is never consulted.
2. **`commons.db` does not exist yet** (another agent builds it later in the
   slice). Import it lazily, inside the migration-running functions, not at
   module scope — so merely importing this module (e.g. incidental collection
   by a tool that walks the repo) can never fail, and the metadata is only
   required at the moment a migration actually needs it. Once `commons/db.py`
   lands, this file needs no changes: the import just starts succeeding.
"""

import os
from logging.config import fileConfig
from typing import Any

from sqlalchemy import engine_from_config, pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _database_url() -> str:
    """The one and only source of the DB URL: the process environment.

    Never `alembic.ini`'s `sqlalchemy.url`, never a `.env*` file — see module
    docstring rule 1. Raises with a clear message rather than silently falling
    back to a placeholder DSN, so a misconfigured shell fails loudly instead of
    Alembic trying to connect to `driver://user:pass@localhost/dbname`.
    """
    url = os.environ.get("SIYUR_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "SIYUR_DATABASE_URL is not set in the process environment. "
            "Alembic reads the DB URL from this variable only (never alembic.ini, "
            "never a .env* file — AGENTS.md). Example for the local docker-compose "
            "PostGIS service: "
            "export SIYUR_DATABASE_URL='postgresql+psycopg://siyur:siyur@localhost:5432/siyur'"
        )
    return url


def _target_metadata() -> Any:
    """`commons.db`'s SQLAlchemy metadata, imported defensively.

    `commons/db.py` (the `site` / `site_source` / `site_conflict` SQLAlchemy
    models — data-model.md §4) does not exist yet as of this setup commit; a
    later task in this slice adds it. Until then, autogenerate has nothing to
    diff against (`None` is a valid Alembic `target_metadata`) and a hand-written
    revision (T015, out of this task's scope) works regardless. Import is
    module-local and lazy — see module docstring rule 2.
    """
    try:
        from commons.db import metadata  # noqa: PLC0415
    except ImportError:
        return None
    else:
        return metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    context.configure(
        url=_database_url(),
        target_metadata=_target_metadata(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=_target_metadata())

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
