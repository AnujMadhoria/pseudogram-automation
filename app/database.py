from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


def _engine_url() -> str:
    return get_settings().database_url


def _connect_args() -> dict[str, object]:
    if _engine_url().startswith("sqlite"):
        return {"check_same_thread": False}
    return {"connect_timeout": 10}


engine = create_engine(
    _engine_url(),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
    connect_args=_connect_args(),
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

