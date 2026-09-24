"""Admin: управление доступом (grant/extend/suspend/revoke) и баны."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.api.dependencies import OwnerDep, SessionDep, require_owner
from app.db.repositories import UserRepository
from app.services import admin as admin_service


async def _stop_user_generations(request: Any, telegram_user_id: int) -> int:
    """A26: suspend/revoke/ban останавливают запущенные генерации пользователя."""
    registry = getattr(request.app.state, "generation_registry", None)
    if registry is None:
        return 0
    async with request.app.state.session_factory() as session:
        user = await UserRepository(session).get_by_telegram_id(telegram_user_id)
        if user is None:
            return 0
        stopped: int = await registry.stop_all_for_user(user.id)
        return stopped


router = APIRouter(prefix="/admin", dependencies=[Depends(require_owner)])


class AccessGrantRequest(BaseModel):
    """Тело POST /api/admin/access/grant; expires_at null = permanent.

    Семантика (A26): поле НЕ передано → в гранте не меняется; поле = null →
    записывается NULL (лимит снят). Роут передаёт только model_fields_set.
    """

    telegram_user_id: int
    expires_at: datetime | None = None
    requests_per_day: int | None = None
    token_limit: int | None = None
    max_concurrent_generations: int | None = None
    can_use_web_search: bool | None = None
    can_use_memory: bool | None = None
    note: str | None = None


class AccessExtendRequest(BaseModel):
    """Тело POST /api/admin/access/extend; expires_at обязателен, null = permanent."""

    telegram_user_id: int
    expires_at: datetime | None


class AccessTargetRequest(BaseModel):
    """Тело suspend/revoke/ban/unban — только telegram_user_id."""

    telegram_user_id: int


@router.post("/access/grant")
async def grant_access(
    body: AccessGrantRequest, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Выдать/обновить грант; только переданные поля меняются (null снимает лимит)."""
    actor, _ = current
    fields = body.model_dump(exclude_unset=True)
    telegram_user_id = fields.pop("telegram_user_id")
    await admin_service.grant_access(
        session,
        actor_id=actor.telegram_user_id,
        telegram_user_id=telegram_user_id,
        **fields,
    )
    await session.commit()
    return {"ok": True}


@router.post("/access/extend")
async def extend_access(
    body: AccessExtendRequest, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Продлить грант (expires_at; explicit null = permanent); 404, если гранта нет."""
    actor, _ = current
    updated = await admin_service.extend_access(
        session,
        actor_id=actor.telegram_user_id,
        telegram_user_id=body.telegram_user_id,
        expires_at=body.expires_at,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="grant not found")
    await session.commit()
    return {"ok": True}


@router.post("/access/suspend")
async def suspend_access(
    body: AccessTargetRequest, request: Request, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Приостановить грант + остановить активные генерации; 404, если гранта нет."""
    actor, _ = current
    updated = await admin_service.suspend_access(
        session, actor_id=actor.telegram_user_id, telegram_user_id=body.telegram_user_id
    )
    if not updated:
        raise HTTPException(status_code=404, detail="grant not found")
    await session.commit()
    stopped = await _stop_user_generations(request, body.telegram_user_id)
    return {"ok": True, "stopped_generations": stopped}


@router.post("/access/revoke")
async def revoke_access(
    body: AccessTargetRequest, request: Request, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Отозвать грант + остановить активные генерации; 404, если гранта нет."""
    actor, _ = current
    updated = await admin_service.revoke_access(
        session, actor_id=actor.telegram_user_id, telegram_user_id=body.telegram_user_id
    )
    if not updated:
        raise HTTPException(status_code=404, detail="grant not found")
    await session.commit()
    stopped = await _stop_user_generations(request, body.telegram_user_id)
    return {"ok": True, "stopped_generations": stopped}


@router.post("/users/ban")
async def ban_user(
    body: AccessTargetRequest, request: Request, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Забанить пользователя + остановить его активные генерации; 404, если нет."""
    actor, _ = current
    updated = await admin_service.ban_user(
        session, actor_id=actor.telegram_user_id, telegram_user_id=body.telegram_user_id
    )
    if not updated:
        raise HTTPException(status_code=404, detail="user not found")
    await session.commit()
    stopped = await _stop_user_generations(request, body.telegram_user_id)
    return {"ok": True, "stopped_generations": stopped}


@router.post("/users/unban")
async def unban_user(
    body: AccessTargetRequest, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Разбанить пользователя (status=active); 404, если не найден."""
    actor, _ = current
    updated = await admin_service.unban_user(
        session, actor_id=actor.telegram_user_id, telegram_user_id=body.telegram_user_id
    )
    if not updated:
        raise HTTPException(status_code=404, detail="user not found")
    await session.commit()
    return {"ok": True}
