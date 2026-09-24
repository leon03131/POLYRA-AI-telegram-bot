"""Admin: просмотр долговременной памяти пользователей (read-only, owner)."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.api.dependencies import OwnerDep, SessionDep, require_owner
from app.db.models import Memory, User
from app.db.repositories import MemoryRepository, UserRepository

router = APIRouter(prefix="/admin/memory", dependencies=[Depends(require_owner)])

_MAX_LIMIT = 200


def _memory_out(memory: Memory, telegram_user_id: int | None) -> dict[str, Any]:
    return {
        "id": str(memory.id),
        "user_id": str(memory.user_id),
        "telegram_user_id": telegram_user_id,
        "text": memory.text,
        "category": memory.category,
        "importance": memory.importance,
        "updated_at": memory.updated_at,
        "last_used_at": memory.last_used_at,
    }


@router.get("")
async def list_memories(
    current: OwnerDep,
    session: SessionDep,
    telegram_user_id: int | None = Query(default=None),
    limit: int = Query(default=50),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Память конкретного пользователя (telegram_user_id) или всех (без фильтра)."""
    limit = max(1, min(limit, _MAX_LIMIT))
    repo = MemoryRepository(session)
    tg_by_user_id: dict[uuid.UUID, int] = {}
    if telegram_user_id is not None:
        user = await UserRepository(session).get_by_telegram_id(telegram_user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="user not found")
        memories = await repo.list_for_user(user.id, limit=limit, offset=offset)
        total = await repo.count_for_user(user.id)
        tg_by_user_id[user.id] = telegram_user_id
    else:
        memories = await repo.list_all(limit=limit, offset=offset)
        total = await repo.count_all()
        user_ids = {memory.user_id for memory in memories}
        if user_ids:
            rows = (
                await session.execute(
                    select(User.id, User.telegram_user_id).where(User.id.in_(user_ids))
                )
            ).all()
            tg_by_user_id = {row.id: row.telegram_user_id for row in rows}
    return {
        "memories": [_memory_out(memory, tg_by_user_id.get(memory.user_id)) for memory in memories],
        "total": total,
    }
