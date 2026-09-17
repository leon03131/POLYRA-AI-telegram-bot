"""Реестр инструментов: определение, контекст исполнения, фильтрация по правам."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.services.access import EffectivePermissions

# required_permission: None — всем; иначе имя флага EffectivePermissions.
_PERMISSION_FLAGS: dict[str, str] = {
    "web_search": "can_use_web_search",
    "memory": "can_use_memory",
}


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Описание инструмента: схема аргументов + async-хендлер (текст для модели)."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema (object)
    handler: Callable[[dict[str, Any], ToolContext], Awaitable[str]]
    timeout: float = 20.0
    required_permission: str | None = None  # "web_search" | "memory" | None
    enabled: bool = True
    max_result_size: int = 4000


@dataclass(slots=True)
class ToolContext:
    """Контекст исполнения инструмента (одна генерация ответа)."""

    user_id: uuid.UUID
    chat_id: uuid.UUID
    permissions: EffectivePermissions
    session_factory: async_sessionmaker[AsyncSession]
    settings: Settings
    search_manager: Any | None = None  # SearchManager (app.search не импортируем)
    jina_reader: Any | None = None


def has_permission(permissions: EffectivePermissions, required: str | None) -> bool:
    """Есть ли у permissions право на инструмент с данным required_permission."""
    if required is None:
        return True
    flag = _PERMISSION_FLAGS.get(required)
    if flag is None:
        return False  # неизвестное право — запрещаем (fail-closed)
    return bool(getattr(permissions, flag, False))


class ToolRegistry:
    """Реестр инструментов по имени."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        """Зарегистрировать инструмент (перезапись по имени допустима)."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        """Инструмент по имени; None — не зарегистрирован."""
        return self._tools.get(name)

    def list_enabled(self, permissions: EffectivePermissions) -> list[ToolDefinition]:
        """Включённые инструменты, доступные данным permissions."""
        return [
            tool
            for tool in self._tools.values()
            if tool.enabled and has_permission(permissions, tool.required_permission)
        ]
