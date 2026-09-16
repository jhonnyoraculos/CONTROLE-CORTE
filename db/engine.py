"""SQLAlchemy engine and transactional session factory."""

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from config.settings import get_settings


@lru_cache(maxsize=4)
def make_engine(url: str | None = None):
    url = url or get_settings().database_url
    if not url:
        raise RuntimeError("DATABASE_URL não configurada. Consulte o README.")
    return create_engine(url, pool_pre_ping=True, pool_recycle=300)


def session_factory(url: str | None = None):
    return sessionmaker(make_engine(url), expire_on_commit=False)
