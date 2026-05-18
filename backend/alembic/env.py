"""
alembic/env.py — Alembic migration environment.

Uses SQLAlchemy async engine so migrations run with the same driver
(asyncpg) as the application. This ensures migration SQL is tested
with the actual production driver, not a sync fallback.

Usage:
    alembic upgrade head          # apply all pending migrations
    alembic downgrade -1          # roll back one migration
    alembic revision --autogenerate -m "add column X"
    alembic current               # show current revision
    alembic history               # show migration history
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

# Import the metadata from our models so autogenerate can diff it
from app.db.models import Base
from app.config import get_settings

settings = get_settings()

# Alembic Config object — access to alembic.ini
config = context.config

# Set up logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The metadata our models define — autogenerate compares this to the DB
target_metadata = Base.metadata


def get_url() -> str:
    """
    Override the URL from alembic.ini with the real app setting.
    This ensures we always migrate the correct database.
    """
    return settings.DATABASE_URL


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    Configures the context with just a URL and not an Engine.
    Used to generate a SQL script without connecting to the database.

        alembic upgrade head --sql > migration.sql
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # Detect foreign key drops on PostgreSQL correctly
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    Run migrations using an async engine.
    asyncpg requires this path — it cannot run via the sync runner.
    """
    connectable = create_async_engine(
        get_url(),
        poolclass=pool.NullPool,  # No pooling for migration connections
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connecting to a live database)."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
