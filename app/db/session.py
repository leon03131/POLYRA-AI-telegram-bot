"""Engine/session фабрики. Никаких глобальных engine на import time."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine_from_url(url: str) -> AsyncEngine:
    """Создать AsyncEngine по URL (pool_pre_ping=True)."""
    return create_async_engine(url, pool_pre_ping=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Создать фабрику AsyncSession (expire_on_commit=False)."""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def get_session(
    factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Контекстный хелпер: открыть сессию и гарантированно закрыть.

    Commit/rollback — ответственность сервисного слоя (uow), здесь только close.
    """
    async with factory() as session:
        yield session
