"""PostgreSQL wiring (foundation). Domain tables land in Phase 2; this module only
provides the engine/session factory and the DeclarativeBase that migrations track.
"""

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Naming root for all ACI tables."""


def make_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
