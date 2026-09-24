"""Репозитории доступа: гранты, per-model разрешения, настройки."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AccessGrant, UserModelAccess, UserModelPermission, UserSettings

_USER_MODEL_ACCESS_MODES = frozenset({"all", "list"})


class AccessRepository:
    """Операции над AccessGrant (одна запись на пользователя)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_grant(self, user_id: uuid.UUID) -> AccessGrant | None:
        """Получить грант пользователя."""
        stmt = select(AccessGrant).where(AccessGrant.user_id == user_id)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def upsert_grant(self, user_id: uuid.UUID, **fields: Any) -> AccessGrant:
        """Создать грант или обновить указанные поля.

        При status="revoked" автоматически выставляет revoked_at=now(),
        если revoked_at не передан явно.
        """
        if fields.get("status") == "revoked" and "revoked_at" not in fields:
            fields["revoked_at"] = datetime.now(UTC)
        grant = await self.get_grant(user_id)
        if grant is None:
            grant = AccessGrant(user_id=user_id, **fields)
            self._session.add(grant)
        else:
            for key, value in fields.items():
                setattr(grant, key, value)
        await self._session.flush()
        return grant

    async def list_grants(self, *, limit: int = 50, offset: int = 0) -> list[AccessGrant]:
        """Список грантов (новые первыми) с пагинацией."""
        stmt = (
            select(AccessGrant).order_by(AccessGrant.created_at.desc()).limit(limit).offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())


class UserModelAccessRepository:
    """Режим доступа к моделям (user_model_access): 'all' | 'list' (A03)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_mode(self, user_id: uuid.UUID) -> str:
        """Режим пользователя; 'all' при отсутствии записи (default)."""
        access = await self._session.get(UserModelAccess, user_id)
        return access.mode if access is not None else "all"

    async def set_mode(self, user_id: uuid.UUID, mode: str) -> UserModelAccess:
        """Upsert режима; mode обязан быть 'all' или 'list'."""
        if mode not in _USER_MODEL_ACCESS_MODES:
            raise ValueError(f"invalid user_model_access mode: {mode!r}")
        access = await self._session.get(UserModelAccess, user_id)
        if access is None:
            access = UserModelAccess(user_id=user_id, mode=mode)
            self._session.add(access)
        else:
            access.mode = mode
        await self._session.flush()
        return access


class ModelPermissionRepository:
    """Per-user разрешения на модели."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_for_user(self, user_id: uuid.UUID) -> list[UserModelPermission]:
        """Все записи разрешений пользователя."""
        stmt = (
            select(UserModelPermission)
            .where(UserModelPermission.user_id == user_id)
            .order_by(UserModelPermission.model_id)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def allowed_model_ids(self, user_id: uuid.UUID) -> set[str] | None:
        """Разрешённые model_id с учётом режима user_model_access (A03).

        mode 'all' (или записи нет) → None (без ограничений);
        mode 'list' → set разрешённых моделей; ПУСТОЙ set = запрет всех
        (пустой set ≠ None!).
        """
        mode = await UserModelAccessRepository(self._session).get_mode(user_id)
        if mode == "all":
            return None
        rows = await self.get_for_user(user_id)
        return {row.model_id for row in rows if row.allowed}

    async def set_permission(
        self,
        user_id: uuid.UUID,
        model_id: str,
        allowed: bool,
    ) -> UserModelPermission:
        """Upsert разрешения (user_id, model_id)."""
        stmt = select(UserModelPermission).where(
            UserModelPermission.user_id == user_id,
            UserModelPermission.model_id == model_id,
        )
        result = await self._session.execute(stmt)
        permission = result.scalar_one_or_none()
        if permission is None:
            permission = UserModelPermission(user_id=user_id, model_id=model_id, allowed=allowed)
            self._session.add(permission)
        else:
            permission.allowed = allowed
        await self._session.flush()
        return permission

    async def clear_for_user(self, user_id: uuid.UUID) -> None:
        """Удалить все записи разрешений пользователя (режим задаётся в user_model_access)."""
        stmt = delete(UserModelPermission).where(UserModelPermission.user_id == user_id)
        await self._session.execute(stmt)
        await self._session.flush()


class UserSettingsRepository:
    """Настройки пользователя (1:1)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_or_create(self, user_id: uuid.UUID) -> UserSettings:
        """Получить настройки, создав запись с дефолтами при отсутствии."""
        settings = await self._session.get(UserSettings, user_id)
        if settings is None:
            settings = UserSettings(user_id=user_id)
            self._session.add(settings)
            await self._session.flush()
        return settings

    async def update(self, user_id: uuid.UUID, **fields: Any) -> UserSettings:
        """Обновить указанные поля настроек (создаёт запись при отсутствии)."""
        settings = await self.get_or_create(user_id)
        for key, value in fields.items():
            setattr(settings, key, value)
        await self._session.flush()
        return settings
