"""Репозиторий системных настроек (key-value)."""

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SystemSetting


class SystemSettingRepository:
    """Операции над SystemSetting. Commit/rollback — уровень сервисов/uow."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_value(self, key: str) -> Any | None:
        """JSON-значение по ключу; None, если ключ не заведён."""
        setting = await self._session.get(SystemSetting, key)
        return setting.value if setting is not None else None

    async def get_many(self, keys: Iterable[str]) -> dict[str, Any]:
        """Значения по списку ключей: {key: value} только для существующих ключей."""
        stmt = select(SystemSetting).where(SystemSetting.key.in_(list(keys)))
        result = await self._session.execute(stmt)
        return {row.key: row.value for row in result.scalars().all()}

    async def set_value(self, key: str, value: Any) -> SystemSetting:
        """Upsert значения по ключу."""
        setting = await self._session.get(SystemSetting, key)
        if setting is None:
            setting = SystemSetting(key=key, value=value)
            self._session.add(setting)
        else:
            setting.value = value
        await self._session.flush()
        return setting
