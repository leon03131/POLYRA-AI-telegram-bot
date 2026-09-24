"""Оркестрация admin-операций Mini App: доступ, баны, per-model разрешения.

Все функции flush-only (commit — уровень роута) и пишут запись в audit_log
через общий хелпер ``audit``. Секреты в metadata не кладём — только key_hint.
"""

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.repositories import (
    AccessRepository,
    AuditLogRepository,
    ModelOverrideRepository,
    ModelPermissionRepository,
    UserModelAccessRepository,
    UserRepository,
)

# --- audit action constants --------------------------------------------------
ACCESS_GRANTED = "access_granted"
ACCESS_EXTENDED = "access_extended"
ACCESS_SUSPENDED = "access_suspended"
ACCESS_REVOKED = "access_revoked"
USER_BANNED = "user_banned"
USER_UNBANNED = "user_unbanned"
MODEL_PERMISSION_CHANGED = "model_permission_changed"
MODEL_ENABLED_CHANGED = "model_enabled_changed"
GEMINI_KEY_ADDED = "gemini_key_added"
GEMINI_KEY_BULK_ADDED = "gemini_key_bulk_added"
GEMINI_KEY_ENABLED = "gemini_key_enabled"
GEMINI_KEY_DISABLED = "gemini_key_disabled"
GEMINI_KEY_MOVED = "gemini_key_moved"
GEMINI_KEY_DELETED = "gemini_key_deleted"
GEMINI_QUOTA_UPDATED = "gemini_quota_updated"
GEMINI_PROJECT_TESTED = "gemini_project_tested"
GEMINI_COUNTERS_RESET = "gemini_counters_reset"
PROVIDER_KEY_SET = "provider_key_set"
PROVIDER_SMOKE_TEST = "provider_smoke_test"
SEARCH_BACKEND_CHANGED = "search_backend_changed"
SYSTEM_SETTINGS_UPDATED = "system_settings_updated"
SYSTEM_PROMPT_UPDATED = "system_prompt_updated"

# Sentinel «поле не передано» (A26): отличается от explicit None (записать NULL).
# Роуты передают только ключи из body.model_fields_set.
UNSET: Any = object()


async def audit(
    session: AsyncSession,
    *,
    actor_id: int,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Записать событие в audit_log (flush-only)."""
    await AuditLogRepository(session).add(
        actor_telegram_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        metadata=metadata,
    )


async def _get_or_create_user(session: AsyncSession, telegram_user_id: int) -> User:
    """Найти пользователя; если нет — создать минимальную запись (только tg id)."""
    repo = UserRepository(session)
    user = await repo.get_by_telegram_id(telegram_user_id)
    if user is None:
        user = await repo.upsert_telegram_user(
            telegram_user_id,
            username=None,
            first_name="",
            last_name=None,
            language_code=None,
        )
    return user


async def grant_access(
    session: AsyncSession,
    *,
    actor_id: int,
    telegram_user_id: int,
    expires_at: Any = UNSET,
    requests_per_day: Any = UNSET,
    token_limit: Any = UNSET,
    max_concurrent_generations: Any = UNSET,
    can_use_web_search: Any = UNSET,
    can_use_memory: Any = UNSET,
    note: Any = UNSET,
) -> None:
    """Выдать/обновить грант (status=active); пользователь создаётся при отсутствии.

    Семантика опциональных полей (A26): UNSET (не передано) — колонку не
    трогаем; explicit None — записать NULL (снять лимит / permanent).
    Новый грант: непереданные поля получают дефолты колонок (лимиты NULL).
    """
    user = await _get_or_create_user(session, telegram_user_id)
    repo = AccessRepository(session)
    provided: dict[str, Any] = {
        "expires_at": expires_at,
        "requests_per_day": requests_per_day,
        "token_limit": token_limit,
        "max_concurrent_generations": max_concurrent_generations,
        "can_use_web_search": can_use_web_search,
        "can_use_memory": can_use_memory,
        "note": note,
    }
    fields: dict[str, Any] = {"status": "active", "revoked_at": None}
    fields.update({key: value for key, value in provided.items() if value is not UNSET})
    if await repo.get_grant(user.id) is None:
        fields["created_by"] = actor_id
    await repo.upsert_grant(user.id, **fields)
    await audit(
        session,
        actor_id=actor_id,
        action=ACCESS_GRANTED,
        target_type="user",
        target_id=str(telegram_user_id),
        metadata={
            key: (value.isoformat() if isinstance(value, datetime) else value)
            for key, value in provided.items()
            if value is not UNSET
        },
    )


async def _update_grant(
    session: AsyncSession,
    *,
    actor_id: int,
    telegram_user_id: int,
    action: str,
    metadata: dict[str, Any] | None = None,
    **fields: Any,
) -> bool:
    """Обновить существующий грант + audit; False, если пользователь/грант не найден."""
    user = await UserRepository(session).get_by_telegram_id(telegram_user_id)
    if user is None:
        return False
    repo = AccessRepository(session)
    if await repo.get_grant(user.id) is None:
        return False
    await repo.upsert_grant(user.id, **fields)
    await audit(
        session,
        actor_id=actor_id,
        action=action,
        target_type="user",
        target_id=str(telegram_user_id),
        metadata=metadata,
    )
    return True


async def extend_access(
    session: AsyncSession, *, actor_id: int, telegram_user_id: int, expires_at: datetime | None
) -> bool:
    """Продлить грант (expires_at); explicit None = permanent. False, если гранта нет."""
    return await _update_grant(
        session,
        actor_id=actor_id,
        telegram_user_id=telegram_user_id,
        action=ACCESS_EXTENDED,
        metadata={"expires_at": expires_at.isoformat() if expires_at is not None else None},
        expires_at=expires_at,
    )


async def suspend_access(session: AsyncSession, *, actor_id: int, telegram_user_id: int) -> bool:
    """Приостановить грант (status=suspended); False, если гранта нет."""
    return await _update_grant(
        session,
        actor_id=actor_id,
        telegram_user_id=telegram_user_id,
        action=ACCESS_SUSPENDED,
        status="suspended",
    )


async def revoke_access(session: AsyncSession, *, actor_id: int, telegram_user_id: int) -> bool:
    """Отозвать грант (status=revoked, revoked_at=now); False, если гранта нет."""
    return await _update_grant(
        session,
        actor_id=actor_id,
        telegram_user_id=telegram_user_id,
        action=ACCESS_REVOKED,
        status="revoked",
    )


async def _set_user_status(
    session: AsyncSession, *, actor_id: int, telegram_user_id: int, status: str, action: str
) -> bool:
    """Установить User.status + audit; False, если пользователь не найден."""
    repo = UserRepository(session)
    user = await repo.get_by_telegram_id(telegram_user_id)
    if user is None:
        return False
    await repo.set_status(user.id, status)
    await audit(
        session,
        actor_id=actor_id,
        action=action,
        target_type="user",
        target_id=str(telegram_user_id),
    )
    return True


async def ban_user(session: AsyncSession, *, actor_id: int, telegram_user_id: int) -> bool:
    """Забанить пользователя (status=banned); False, если не найден."""
    return await _set_user_status(
        session,
        actor_id=actor_id,
        telegram_user_id=telegram_user_id,
        status="banned",
        action=USER_BANNED,
    )


async def unban_user(session: AsyncSession, *, actor_id: int, telegram_user_id: int) -> bool:
    """Разбанить пользователя (status=active); False, если не найден."""
    return await _set_user_status(
        session,
        actor_id=actor_id,
        telegram_user_id=telegram_user_id,
        status="active",
        action=USER_UNBANNED,
    )


async def set_model_permissions(
    session: AsyncSession,
    *,
    actor_id: int,
    telegram_user_id: int,
    allowed_models: list[str] | None,
) -> None:
    """Заменить per-model разрешения и режим доступа (A03).

    None → mode 'all' + очистка allowlist (без ограничений);
    []   → mode 'list' + пустой allowlist (ЗАПРЕТ всех моделей);
    [m…] → mode 'list' + указанные модели.

    Пользователь создаётся при отсутствии (преднастройка до первого логина).
    """
    user = await _get_or_create_user(session, telegram_user_id)
    permission_repo = ModelPermissionRepository(session)
    access_repo = UserModelAccessRepository(session)
    await permission_repo.clear_for_user(user.id)
    if allowed_models is None:
        await access_repo.set_mode(user.id, "all")
        meta_models: list[str] | None = None
    else:
        await access_repo.set_mode(user.id, "list")
        for model_id in sorted(set(allowed_models)):
            await permission_repo.set_permission(user.id, model_id, True)
        meta_models = sorted(set(allowed_models))
    await audit(
        session,
        actor_id=actor_id,
        action=MODEL_PERMISSION_CHANGED,
        target_type="user",
        target_id=str(telegram_user_id),
        metadata={
            "mode": "all" if allowed_models is None else "list",
            "allowed_models": meta_models,  # [] и None различимы в аудите
        },
    )


async def set_model_enabled(
    session: AsyncSession,
    *,
    actor_id: int,
    model_id: str,
    enabled: bool,
) -> None:
    """DB-override enabled модели (model_overrides) + audit."""
    await ModelOverrideRepository(session).set_enabled(model_id, enabled)
    await audit(
        session,
        actor_id=actor_id,
        action=MODEL_ENABLED_CHANGED,
        target_type="model",
        target_id=model_id,
        metadata={"enabled": enabled},
    )
