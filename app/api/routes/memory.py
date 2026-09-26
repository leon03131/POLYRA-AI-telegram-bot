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
    """Поля PATCH /api/memory/{id}; при смене text пересчитывается normalized_text.

    round4-P2: text/category/importance — NOT NULL-колонки (app/db/models/memory.py),
    поэтому явный null в запросе невалиден (роут отвечает 400, а не пишет NULL);
    category дополнительно ограничена длиной String(32) — 1..32 символов.
    """

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
    """Обновить свою запись памяти; чужая/отсутствующая → 404.

    round4-P2: валидация до обращения к БД (по образцу patch_settings):
    явный null в NOT NULL-полях → 400 (не NotNullViolation/500 на flush);
    category длиннее String(32) → 400 (не StringDataRightTruncation/500);
    пустой/whitespace-only text → 400 (normalized_text теряет смысл).
    """
    user, _ = current
    data = body.model_dump(exclude_unset=True)
    # round4-P2: NOT NULL-поля — null запрещён («сбросить значение» нельзя).
    for field_name in ("text", "category", "importance"):
        if field_name in data and data[field_name] is None:
            raise HTTPException(status_code=400, detail=f"{field_name} cannot be null")
    category = data.get("category")
    if category is not None and not 1 <= len(category) <= 32:
        raise HTTPException(status_code=400, detail="category length must be in 1..32")
    text = data.get("text")
    if text is not None and not text.strip():
        raise HTTPException(status_code=400, detail="text must be non-empty")
    importance = data.get("importance")
    if importance is not None and not 1 <= importance <= 10:
        raise HTTPException(status_code=400, detail="importance must be in 1..10")
    if text is not None:
        data["normalized_text"] = normalize_memory_text(text)
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
