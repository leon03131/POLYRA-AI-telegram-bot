"""Репозиторий DB-override включённости моделей (model_overrides)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ModelOverride


class ModelOverrideRepository:
    """Операции над ModelOverride. Commit/rollback — уровень сервисов/uow."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_all(self) -> dict[str, bool]:
        """Все override-записи как {model_id: enabled}."""
        stmt = select(ModelOverride)
        result = await self._session.execute(stmt)
        return {row.model_id: row.enabled for row in result.scalars().all()}

    async def set_enabled(self, model_id: str, enabled: bool) -> ModelOverride:
        """Upsert override (model_id, enabled)."""
        override = await self._session.get(ModelOverride, model_id)
        if override is None:
            override = ModelOverride(model_id=model_id, enabled=enabled)
            self._session.add(override)
        else:
            override.enabled = enabled
        await self._session.flush()
        return override
