"""Управление долговременной памятью пользователя: /api/memory."""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.api.dependencies import CurrentUserDep, SessionDep
from app.db.models import Memory
from app.db.repositories import MemoryRepository
from app.memory.normalizer import normalize_memory_text

router = APIRouter()

_MAX_LIMIT = 200


class MemoryPatchRequest(BaseModel):
    """Поля PATCH /api/memory/{id}; при смене text пересчитывается normalized_text."""

    text: str | None = None
    category: str | None = None
    importance: int | None = None


def _memory_out(memory: Memory) -> dict[str, Any]:
    return {
        "id": str(memory.id),
        "text": memory.text,
        "category": memory.category,
        "importance": memory.importance,
        "updated_at": memory.updated_at,
        "last_used_at": memory.last_used_at,
    }


@router.get("/memory")
async def list_memories(
    current: CurrentUserDep,
    session: SessionDep,
    limit: int = Query(default=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Память пользователя (важность desc, затем свежесть) с пагинацией."""
    user, _ = current
    limit = max(1, min(limit, _MAX_LIMIT))
    repo = MemoryRepository(session)
    memories = await repo.list_for_user(user.id, limit=limit, offset=offset)
    total = await repo.count_for_user(user.id)
    return {"memories": [_memory_out(memory) for memory in memories], "total": total}


@router.patch("/memory/{memory_id}")
async def patch_memory(
    memory_id: uuid.UUID, body: MemoryPatchRequest, current: CurrentUserDep, session: SessionDep
) -> dict[str, Any]:
    """Обновить свою запись памяти; чужая/отсутствующая → 404."""
    user, _ = current
    data = body.model_dump(exclude_unset=True)
    importance = data.get("importance")
    if importance is not None and not 1 <= importance <= 10:
        raise HTTPException(status_code=400, detail="importance must be in 1..10")
    if "text" in data and data["text"] is not None:
        data["normalized_text"] = normalize_memory_text(data["text"])
    memory = await MemoryRepository(session).update_fields(memory_id, user.id, **data)
    if memory is None:
        raise HTTPException(status_code=404, detail="memory not found")
    await session.commit()
    return {"memory": _memory_out(memory)}


@router.delete("/memory/{memory_id}")
async def delete_memory(
    memory_id: uuid.UUID, current: CurrentUserDep, session: SessionDep
) -> dict[str, Any]:
    """Удалить свою запись памяти; чужая/отсутствующая → 404."""
    user, _ = current
    deleted = await MemoryRepository(session).delete(memory_id, user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="memory not found")
    await session.commit()
    return {"ok": True}
