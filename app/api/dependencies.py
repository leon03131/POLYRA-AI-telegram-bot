"""FastAPI dependencies: DB-сессия, текущий пользователь, owner-gate.

Порядок проверок в get_current_user: валидация session token ДО обращения к БД
(401/403 не требуют живой PostgreSQL). Для чтения user/grant/permissions
открывается короткая отдельная сессия; роуты получают свою через SessionDep
(commit мутаций — на уровне роута).
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.auth import verify_session_token
from app.config import Settings
from app.db.models import User
from app.db.repositories import AccessRepository, ModelPermissionRepository, UserRepository
from app.services.access import EffectivePermissions, GrantView, evaluate_access


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Сессия БД на время запроса; commit/rollback — ответственность роута."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


def is_owner_user(user: User, settings: Settings) -> bool:
    """Effective owner: флаг в БД или совпадение с settings.owner_telegram_id."""
    return user.is_owner or user.telegram_user_id == settings.owner_telegram_id


async def get_current_user(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> tuple[User, EffectivePermissions]:
    """Bearer session token → (User, EffectivePermissions).

    401 — нет/невалиден/истёк токен или неизвестный пользователь;
    403 — доступ не активен (banned/no_grant/suspended/revoked/expired).
    """
    settings: Settings = request.app.state.settings
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        telegram_user_id = verify_session_token(token, secret=settings.master_encryption_key)
    except ValueError:
        raise HTTPException(status_code=401, detail="invalid or expired session token") from None

    session_factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with session_factory() as session:
        user = await UserRepository(session).get_by_telegram_id(telegram_user_id)
        if user is None:
            raise HTTPException(status_code=401, detail="unknown user")
        grant = await AccessRepository(session).get_grant(user.id)
        grant_view = (
            GrantView(
                status=grant.status,
                expires_at=grant.expires_at,
                requests_per_day=grant.requests_per_day,
                token_limit=grant.token_limit,
                max_concurrent_generations=grant.max_concurrent_generations,
                can_use_web_search=grant.can_use_web_search,
                can_use_memory=grant.can_use_memory,
            )
            if grant is not None
            else None
        )
        allowed_models = await ModelPermissionRepository(session).allowed_model_ids(user.id)
        permissions = evaluate_access(
            is_owner=is_owner_user(user, settings),
            user_status=user.status,
            grant=grant_view,
            allowed_models=allowed_models,
            now=datetime.now(UTC),
        )
        if not permissions.allowed:
            raise HTTPException(status_code=403, detail=f"access denied: {permissions.reason}")
        # commit нет → атрибуты не expire; user читается после закрытия сессии безопасно
        session.expunge(user)
        return user, permissions


CurrentUserDep = Annotated[tuple[User, EffectivePermissions], Depends(get_current_user)]


async def require_owner(
    request: Request, current: CurrentUserDep
) -> tuple[User, EffectivePermissions]:
    """Пропустить только владельца бота; иначе 403."""
    user, _ = current
    settings: Settings = request.app.state.settings
    if not is_owner_user(user, settings):
        raise HTTPException(status_code=403, detail="owner only")
    return current


OwnerDep = Annotated[tuple[User, EffectivePermissions], Depends(require_owner)]
