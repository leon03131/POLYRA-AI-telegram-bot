"""Единый интерфейс LLM-провайдера и нормализованный формат запроса.

Нормализованные сообщения (provider-neutral), list[MessageDict]:

    {"role": "system" | "user" | "assistant" | "tool", "parts": [MessagePartDict, ...]}

MessagePartDict — один из:
    {"type": "text", "text": str}
    {"type": "image", "mime_type": str, "data_base64": str}
    {"type": "tool_call", "id": str, "name": str, "arguments_json": str,
     "provider_meta": dict}          # assistant only; provider_meta вернуть as-is
    {"type": "tool_result", "call_id": str, "name": str, "content": str,
     "is_error": bool}               # role="tool"

Провайдер адаптирует этот формат к своему wire-протоколу.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from app.llm.events import LLMEvent

MessagePartDict = dict[str, Any]
MessageDict = dict[str, Any]

Role = Literal["system", "user", "assistant", "tool"]
ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "max"]


@dataclass(slots=True)
class LLMTool:
    """Описание инструмента для function calling."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema (object)


@dataclass(slots=True)
class LLMRequest:
    """Единый запрос к LLM. Провайдеры обязаны уважать cancellation."""

    model: str
    messages: list[MessageDict]
    system_prompt: str | None = None
    thinking: str | None = None  # UI-уровень из ModelDefinition.thinking_modes
    tools: list[LLMTool] | None = None
    tool_choice: Literal["auto", "none"] | None = None  # "required" намеренно нет (Qwen)
    max_output_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    cancellation: asyncio.Event = field(default_factory=asyncio.Event)


class LLMProvider(Protocol):
    """Единый контракт провайдера. Ошибки — исключения app.llm.errors.ProviderError."""

    provider_id: str

    def stream_chat(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        """Стрим событий. ReasoningDelta не предназначен для показа пользователю."""
        ...
