"""Admin: список пользователей и per-user разрешения моделей."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.api.dependencies import OwnerDep, SessionDep, require_owner
from app.db.models import AccessGrant, User
from app.db.repositories import AccessRepository, ModelPermissionRepository, UserRepository
from app.llm.registry import ModelRegistry
from app.services import admin as admin_service

router = APIRouter(prefix="/admin/users", dependencies=[Depends(require_owner)])

_MAX_LIMIT = 200


class ModelPermissionsPutRequest(BaseModel):
    """Тело PUT /api/admin/users/{telegram_user_id}/models; None = без ограничений."""

    allowed_models: list[str] | None = None


def _grant_out(grant: AccessGrant | None) -> dict[str, Any] | None:
    if grant is None:
        return None
    return {
        "status": grant.status,
        "expires_at": grant.expires_at,
        "requests_per_day": grant.requests_per_day,
        "token_limit": grant.token_limit,
        "max_concurrent_generations": grant.max_concurrent_generations,
        "can_use_web_search": grant.can_use_web_search,
        "can_use_memory": grant.can_use_memory,
    }


def _user_out(
    user: User, grant: AccessGrant | None, allowed_models: set[str] | None
) -> dict[str, Any]:
    return {
        "id": str(user.id),
        "telegram_user_id": user.telegram_user_id,
        "username": user.username,
        "first_name": user.first_name,
        "status": user.status,
        "is_owner": user.is_owner,
        "first_seen_at": user.first_seen_at,
        "last_seen_at": user.last_seen_at,
        "grant": _grant_out(grant),
        "allowed_models": sorted(allowed_models) if allowed_models is not None else None,
    }


@router.get("")
async def list_users(
    current: OwnerDep,
    session: SessionDep,
    query: str = "",
    limit: int = Query(default=50),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Пользователи: поиск (цифры — tg id/username, иначе username ilike) или свежие."""
    limit = max(1, min(limit, _MAX_LIMIT))
    if query:
        # UserRepository.search без offset — добираем limit+offset и режем в памяти
        users = (await UserRepository(session).search(query, limit=limit + offset))[offset:]
    else:
        stmt = select(User).order_by(User.last_seen_at.desc()).limit(limit).offset(offset)
        users = list((await session.execute(stmt)).scalars().all())

    access_repo = AccessRepository(session)
    permission_repo = ModelPermissionRepository(session)
    result: list[dict[str, Any]] = []
    for user in users:
        grant = await access_repo.get_grant(user.id)
        allowed_models = await permission_repo.allowed_model_ids(user.id)
        result.append(_user_out(user, grant, allowed_models))
    return {"users": result}


@router.get("/{telegram_user_id}/models")
async def get_user_models(
    telegram_user_id: int, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Разрешённые модели пользователя (None = без ограничений)."""
    user = await UserRepository(session).get_by_telegram_id(telegram_user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    allowed = await ModelPermissionRepository(session).allowed_model_ids(user.id)
    return {"allowed_models": sorted(allowed) if allowed is not None else None}


@router.put("/{telegram_user_id}/models")
async def put_user_models(
    telegram_user_id: int,
    body: ModelPermissionsPutRequest,
    request: Request,
    current: OwnerDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Заменить разрешения моделей; None = снять все, неизвестные model_id → 400."""
    actor, _ = current
    if body.allowed_models is not None:
        registry: ModelRegistry = request.app.state.registry
        unknown = sorted({m for m in body.allowed_models if registry.get_or_none(m) is None})
        if unknown:
            raise HTTPException(status_code=400, detail=f"unknown models: {unknown}")
    await admin_service.set_model_permissions(
        session,
        actor_id=actor.telegram_user_id,
        telegram_user_id=telegram_user_id,
        allowed_models=body.allowed_models,
    )
    await session.commit()
    return {"ok": True}
