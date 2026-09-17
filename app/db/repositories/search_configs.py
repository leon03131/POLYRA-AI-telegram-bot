"""Репозиторий конфигураций поисковых бэкендов."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SearchBackendConfig

_MAX_ERROR_LEN = 256
_UPDATABLE_FIELDS = frozenset({"enabled", "priority", "encrypted_api_key", "key_hint"})


class SearchConfigRepository:
    """Операции над SearchBackendConfig. Commit/rollback — уровень сервисов/uow."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self) -> list[SearchBackendConfig]:
        """Все конфиги по приоритету (asc)."""
        stmt = select(SearchBackendConfig).order_by(SearchBackendConfig.priority)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get(self, backend_id: str) -> SearchBackendConfig | None:
        """Найти конфиг по backend_id; None, если не заведён."""
        stmt = select(SearchBackendConfig).where(SearchBackendConfig.backend_id == backend_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert(self, backend_id: str, **fields: Any) -> SearchBackendConfig:
        """Создать конфиг или обновить переданные поля (enabled/priority/key)."""
        unknown = set(fields) - _UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"unknown SearchBackendConfig fields: {sorted(unknown)}")
        config = await self.get(backend_id)
        if config is None:
            config = SearchBackendConfig(backend_id=backend_id, **fields)
            self._session.add(config)
        else:
            for name, value in fields.items():
                setattr(config, name, value)
        await self._session.flush()
        return config

    async def set_enabled(self, backend_id: str, enabled: bool) -> SearchBackendConfig | None:
        """Включить/выключить бэкенд; None, если не найден."""
        config = await self.get(backend_id)
        if config is None:
            return None
        config.enabled = enabled
        await self._session.flush()
        return config

    async def set_key(
        self, backend_id: str, encrypted: str | None, hint: str | None
    ) -> SearchBackendConfig | None:
        """Установить/снять зашифрованный ключ и UI-маску; None, если не найден."""
        config = await self.get(backend_id)
        if config is None:
            return None
        config.encrypted_api_key = encrypted
        config.key_hint = hint
        await self._session.flush()
        return config

    async def set_health(
        self, backend_id: str, status: str, error: str | None = None
    ) -> SearchBackendConfig | None:
        """Обновить health_status/last_error; None, если не найден."""
        config = await self.get(backend_id)
        if config is None:
            return None
        config.health_status = status
        config.last_error = error[:_MAX_ERROR_LEN] if error else None
        await self._session.flush()
        return config
