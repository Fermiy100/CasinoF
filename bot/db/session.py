from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from bot.config import Settings


def create_engine_and_sessionmaker(settings: Settings) -> tuple[AsyncEngine, async_sessionmaker]:
    engine = create_async_engine(settings.database_url, future=True, echo=False)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    return engine, session_factory

