"""CRUD чатов пользователя: /api/chats. Все операции — только над своими чатами."""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import CurrentUserDep, SessionDep
from app.config import Settings
from app.db.models import Chat, User
from app.db.repositories import ChatRepository, UserSettingsRepository
from app.llm.registry import ModelRegistry
from app.services.access import EffectivePermissions, is_model_allowed
from app.services.chats import ChatService

router = APIRouter()

_WEB_MODES = frozenset({"off", "auto", "on"})
_MAX_LIMIT = 200


class ChatCreateRequest(BaseModel):
    """Тело POST /api/chats."""

    title: str | None = Field(default=None, max_length=256)


class ChatPatchRequest(BaseModel):
    """Поля PATCH /api/chats/{id}; null/отсутствие значения = inherit (NULL)."""

    title: str | None = Field(default=None, max_length=256)
    model_id: str | None = None
    thinking_setting: str | None = None
    web_mode: str | None = None
    memory_enabled: bool | None = None
    system_prompt_override: str | None = None


class ChatArchiveRequest(BaseModel):
    """Тело POST /api/chats/{id}/archive."""

    archived: bool


def _chat_out(chat: Chat, *, current_chat_id: uuid.UUID | None) -> dict[str, Any]:
    return {
        "id": str(chat.id),
        "title": chat.title,
        "model_id": chat.model_id,
        "thinking_setting": chat.thinking_setting,
        "web_mode": chat.web_mode,
        "memory_enabled": chat.memory_enabled,
        "system_prompt_override": chat.system_prompt_override,
        "created_at": chat.created_at,
        "updated_at": chat.updated_at,
        "archived_at": chat.archived_at,
        "is_current": chat.id == current_chat_id,
    }


async def _get_own_chat(session: AsyncSession, chat_id: uuid.UUID, user: User) -> Chat:
    """Чат по id; 404, если не найден или чужой (без раскрытия существования)."""
    chat = await ChatRepository(session).get(chat_id)
    if chat is None or chat.owner_user_id != user.id:
        raise HTTPException(status_code=404, detail="chat not found")
    return chat


def _validate_model(
    registry: ModelRegistry, permissions: EffectivePermissions, model_id: str
) -> None:
    model_def = registry.get_or_none(model_id)
    if model_def is None or model_def.internal_only:
        raise HTTPException(status_code=400, detail="unknown model_id")
    if not is_model_allowed(permissions, model_id):
        raise HTTPException(status_code=403, detail="model not allowed")


@router.get("/chats")
async def list_chats(
    current: CurrentUserDep,
    session: SessionDep,
    limit: int = Query(default=50),
    offset: int = Query(default=0, ge=0),
    include_archived: bool = Query(default=False),
) -> dict[str, Any]:
    """Чаты пользователя (свежие первыми) с пагинацией; include_archived добавляет архив."""
    user, _ = current
    limit = max(1, min(limit, _MAX_LIMIT))
    repo = ChatRepository(session)
    chats = await repo.list_for_user(
        user.id, include_archived=include_archived, limit=limit, offset=offset
    )
    total = await repo.count_for_user(user.id, include_archived=include_archived)
    current_chat_id = await ChatService(session).get_current_chat_id(user.id)
    return {
        "chats": [_chat_out(chat, current_chat_id=current_chat_id) for chat in chats],
        "total": total,
    }


@router.get("/chats/{chat_id}")
async def get_chat(
    chat_id: uuid.UUID, current: CurrentUserDep, session: SessionDep
) -> dict[str, Any]:
    """Один чат по id (в т.ч. архивный); 404 для чужого/отсутствующего."""
    user, _ = current
    chat = await _get_own_chat(session, chat_id, user)
    current_chat_id = await ChatService(session).get_current_chat_id(user.id)
    return {"chat": _chat_out(chat, current_chat_id=current_chat_id)}


@router.post("/chats", status_code=201)
async def create_chat(
    body: ChatCreateRequest, current: CurrentUserDep, session: SessionDep
) -> dict[str, Any]:
    """Создать чат и сделать его текущим."""
    user, _ = current
    chat = await ChatService(session).create_chat(user.id)
    if body.title is not None:
        chat.title = body.title
        await session.flush()
    await session.commit()
    return {"chat": _chat_out(chat, current_chat_id=chat.id)}


@router.post("/chats/{chat_id}/open")
async def open_chat(
    chat_id: uuid.UUID, current: CurrentUserDep, session: SessionDep
) -> dict[str, Any]:
    """Сделать чат текущим."""
    user, _ = current
    chat = await _get_own_chat(session, chat_id, user)
    await ChatService(session).set_current_chat(user.id, chat.id)
    await session.commit()
    return {"ok": True}


@router.patch("/chats/{chat_id}")
async def patch_chat(
    chat_id: uuid.UUID,
    body: ChatPatchRequest,
    request: Request,
    current: CurrentUserDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Обновить per-chat настройки; null/пропуск поля = inherit."""
    user, permissions = current
    settings: Settings = request.app.state.settings
    registry: ModelRegistry = request.app.state.registry
    chat = await _get_own_chat(session, chat_id, user)
    data = body.model_dump(exclude_unset=True)

    if "model_id" in data and data["model_id"] is not None:
        _validate_model(registry, permissions, data["model_id"])
    web_mode = data.get("web_mode")
    if "web_mode" in data and web_mode is not None and web_mode not in _WEB_MODES:
        raise HTTPException(status_code=400, detail="web_mode must be off|auto|on")
    thinking = data.get("thinking_setting")
    if "thinking_setting" in data and thinking is not None:
        base_model_id = data.get("model_id", chat.model_id)
        if base_model_id is None:
            user_settings = await UserSettingsRepository(session).get_or_create(user.id)
            base_model_id = user_settings.default_model_id
        model_def = registry.get_or_none(base_model_id or settings.default_model)
        if model_def is not None and thinking not in model_def.thinking_modes:
            raise HTTPException(status_code=400, detail="thinking mode not supported by model")

    updated = await ChatRepository(session).update_settings(chat_id, **data)
    if updated is None:  # pragma: no cover — чат только что проверен выше
        raise HTTPException(status_code=404, detail="chat not found")
    await session.commit()
    await session.refresh(updated)  # гарантированно свежие значения для ответа
    current_chat_id = await ChatService(session).get_current_chat_id(user.id)
    return {"chat": _chat_out(updated, current_chat_id=current_chat_id)}


@router.post("/chats/{chat_id}/archive")
async def archive_chat(
    chat_id: uuid.UUID,
    body: ChatArchiveRequest,
    current: CurrentUserDep,
    session: SessionDep,
) -> dict[str, Any]:
    """Архивировать/разархивировать чат."""
    user, _ = current
    chat = await _get_own_chat(session, chat_id, user)
    await ChatRepository(session).set_archived(chat.id, body.archived)
    await session.commit()
    return {"ok": True}


@router.delete("/chats/{chat_id}")
async def delete_chat(
    chat_id: uuid.UUID, current: CurrentUserDep, session: SessionDep
) -> dict[str, Any]:
    """Удалить чат."""
    user, _ = current
    chat = await _get_own_chat(session, chat_id, user)
    await ChatRepository(session).delete(chat.id)
    await session.commit()
    return {"ok": True}
