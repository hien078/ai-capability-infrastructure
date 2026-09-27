"""Alembic environment. Migrations from day one (plan §41)."""
import os
import sys
from logging.config import fileConfig

from alembic import context

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from aci.adapters.outbound.postgres.base import Base  # noqa: E402
from aci.adapters.outbound.postgres import orm as _orm  # noqa: E402,F401  # register tables
from aci.adapters.outbound import pgvector as _pgvector_orm  # noqa: E402,F401  # register vector tables

config = context.config
if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except KeyError:
        pass  # minimal alembic.ini without [loggers]/[formatters] sections

target_metadata = Base.metadata


def _database_url() -> str:
    env_url = os.environ.get("ACI_DATABASE_URL")
    if env_url:
        return env_url
    url = config.get_main_option("sqlalchemy.url")
    assert url is not None
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(), target_metadata=target_metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import create_engine

    engine = create_engine(_database_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
