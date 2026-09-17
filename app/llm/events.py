"""Унифицированные события стрима LLM.

ReasoningDelta существует (провайдеры могут присылать мысли), но НИКОГДА
не показывается пользователю и не сохраняется как контент сообщения.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class TextDelta:
    """Видимый фрагмент финального ответа."""

    text: str


@dataclass(frozen=True, slots=True)
class ReasoningDelta:
    """Скрытое рассуждение модели. Только для внутреннего учёта, не для UI."""

    text: str


@dataclass(frozen=True, slots=True)
class ToolCall:
    """Запрос модели на вызов инструмента (аргументы — сырой JSON-строкой)."""

    id: str
    name: str
    arguments_json: str
    # vendor-специфичные данные (например gemini thought_signature) — вернуть в историю as-is
    provider_meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Результат исполнения инструмента (для повторной передачи модели)."""

    call_id: str
    name: str
    content: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class Usage:
    """Фактическое потребление токенов (по данным провайдера)."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class Done:
    """Стрим завершён штатно."""

    finish_reason: str  # "stop" | "length" | "tool_calls"


LLMEvent = TextDelta | ReasoningDelta | ToolCall | ToolResult | Usage | Done
