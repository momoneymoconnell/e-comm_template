"""Alembic environment for the catalog service.

Each service owns its migration history independently: its own
``migrations/versions`` directory and its own ``alembic_version`` table inside
its own schema. That is what lets you deploy a change to one service without
coordinating a release across all of them.

Migrations run synchronously (``psycopg``) even though the service serves
traffic asynchronously (``asyncpg``). Alembic's runner is synchronous, and
there is nothing to gain from concurrency in a one-shot schema change.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from ecom_catalog.config import CatalogSettings
from ecom_catalog.models import Base
from sqlalchemy import engine_from_config, pool, text

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = CatalogSettings()

# Injected at runtime rather than written into alembic.ini, so the database
# password never sits in a file that is committed to git.
config.set_main_option("sqlalchemy.url", settings.sync_database_url)

# What autogenerate compares the live database against.
target_metadata = Base.metadata

#: This service's schema. Every table, and the version table itself, lives here.
SCHEMA = settings.db_schema


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Restrict autogenerate to this service's own schema.

    Without this, Alembic reflects every schema the connection can see and
    proposes dropping tables that belong to other services — which is exactly
    as destructive as it sounds.

    Args:
        obj: The reflected or declared schema object.
        name: Its name.
        type_: ``"table"``, ``"column"``, ``"index"`` and so on.
        reflected: Whether it came from the live database.
        compare_to: The object it is being compared against.

    Returns:
        ``True`` if Alembic should consider this object.
    """
    if type_ == "table":
        # Alembic's own bookkeeping table is not part of the model. Without
        # this it is reflected as an "extra" table and autogenerate cheerfully
        # proposes dropping it, which would erase the migration history.
        if name == "alembic_version":
            return False
        # `obj.schema` is None for tables declared without an explicit schema,
        # which in our metadata means they inherit ours.
        return obj.schema in (SCHEMA, None)
    return True


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of executing it (``alembic upgrade --sql``).

    Useful when a DBA must review and apply changes by hand, which is common
    in regulated environments.
    """
    context.configure(
        url=settings.sync_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=SCHEMA,
        include_schemas=True,
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect and apply migrations."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        # search_path is set to `public` ONLY — deliberately not to our own
        # schema.
        #
        # `public` must be present so DDL can resolve the shared extension
        # types (CITEXT, and pgcrypto's gen_random_uuid) which are installed
        # there; without it every CREATE TABLE fails with "type citext does
        # not exist".
        #
        # Our own schema must be ABSENT. Alembic reflects whatever sits on the
        # search path as the unqualified default schema, so including `auth`
        # here makes the live `auth.users` come back as `users` with
        # schema=None, while the model declares schema="auth". Autogenerate
        # then sees two different tables and emits a migration that drops the
        # real one and recreates it. Every table in this project is explicitly
        # schema-qualified in its metadata, so nothing needs the shortcut.
        connection.execute(text("SET search_path TO public"))
        connection.commit()

        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=SCHEMA,
            include_schemas=True,
            include_object=include_object,
            # Detects a column changing from VARCHAR(50) to VARCHAR(200).
            # Off by default, and its absence is a common source of
            # "the migration ran but the column is still wrong".
            compare_type=True,
            compare_server_default=True,
            # Wrap each migration in its own transaction. Postgres supports
            # transactional DDL, so a migration that fails halfway leaves the
            # schema exactly as it was rather than in an undefined state.
            transaction_per_migration=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
