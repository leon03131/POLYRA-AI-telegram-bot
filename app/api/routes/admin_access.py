"""Admin: управление доступом (grant/extend/suspend/revoke) и баны."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.dependencies import OwnerDep, SessionDep, require_owner
from app.services import admin as admin_service

router = APIRouter(prefix="/admin", dependencies=[Depends(require_owner)])


class AccessGrantRequest(BaseModel):
    """Тело POST /api/admin/access/grant; expires_at null = permanent."""

    telegram_user_id: int
    expires_at: datetime | None = None
    requests_per_day: int | None = None
    token_limit: int | None = None
    max_concurrent_generations: int | None = None
    can_use_web_search: bool | None = None
    can_use_memory: bool | None = None
    note: str | None = None


class AccessExtendRequest(BaseModel):
    """Тело POST /api/admin/access/extend."""

    telegram_user_id: int
    expires_at: datetime


class AccessTargetRequest(BaseModel):
    """Тело suspend/revoke/ban/unban — только telegram_user_id."""

    telegram_user_id: int


@router.post("/access/grant")
async def grant_access(
    body: AccessGrantRequest, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Выдать/обновить грант (upsert, status=active); пользователь создаётся при нужде."""
    actor, _ = current
    await admin_service.grant_access(
        session,
        actor_id=actor.telegram_user_id,
        telegram_user_id=body.telegram_user_id,
        expires_at=body.expires_at,
        requests_per_day=body.requests_per_day,
        token_limit=body.token_limit,
        max_concurrent_generations=body.max_concurrent_generations,
        can_use_web_search=body.can_use_web_search,
        can_use_memory=body.can_use_memory,
        note=body.note,
    )
    await session.commit()
    return {"ok": True}


@router.post("/access/extend")
async def extend_access(
    body: AccessExtendRequest, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Продлить грант; 404, если пользователя/гранта нет."""
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
    body: AccessTargetRequest, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Приостановить грант; 404, если гранта нет."""
    actor, _ = current
    updated = await admin_service.suspend_access(
        session, actor_id=actor.telegram_user_id, telegram_user_id=body.telegram_user_id
    )
    if not updated:
        raise HTTPException(status_code=404, detail="grant not found")
    await session.commit()
    return {"ok": True}


@router.post("/access/revoke")
async def revoke_access(
    body: AccessTargetRequest, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Отозвать грант; 404, если гранта нет."""
    actor, _ = current
    updated = await admin_service.revoke_access(
        session, actor_id=actor.telegram_user_id, telegram_user_id=body.telegram_user_id
    )
    if not updated:
        raise HTTPException(status_code=404, detail="grant not found")
    await session.commit()
    return {"ok": True}


@router.post("/users/ban")
async def ban_user(
    body: AccessTargetRequest, current: OwnerDep, session: SessionDep
) -> dict[str, Any]:
    """Забанить пользователя (status=banned); 404, если не найден."""
    actor, _ = current
    updated = await admin_service.ban_user(
        session, actor_id=actor.telegram_user_id, telegram_user_id=body.telegram_user_id
    )
    if not updated:
        raise HTTPException(status_code=404, detail="user not found")
    await session.commit()
    return {"ok": True}


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
