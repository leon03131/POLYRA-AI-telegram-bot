"""Оценка токенов и бюджет контекста.

Точный count_tokens провайдера на hot path недоступен (ADR-013), поэтому
эвристика chars/token. Кириллица плотнее латиницы — множитель конфигурируется
через TokenBudgetManager(chars_per_token=...).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.llm.capabilities import ModelDefinition


def estimate_text_tokens(text: str, *, chars_per_token: float = 3.5) -> int:
    """Грубая оценка числа токенов в тексте: max(1, ceil(len / chars_per_token))."""
    return max(1, math.ceil(len(text) / chars_per_token))


@dataclass(frozen=True, slots=True)
class TokenBudget:
    """Бюджет контекста модели на один запрос."""

    model_context: int
    reserved_output: int
    safety_margin: int

    @property
    def available(self) -> int:
        """Токены, доступные под system prompt + историю."""
        return self.model_context - self.reserved_output - self.safety_margin


class TokenBudgetManager:
    """Считает бюджеты и оценивает стоимость сообщений (provider-neutral)."""

    def __init__(
        self,
        *,
        chars_per_token: float = 3.5,
        reserved_output: int = 4096,
        safety_margin: int = 512,
        image_tokens: int = 1032,
    ) -> None:
        self._chars_per_token = chars_per_token
        self._reserved_output = reserved_output
        self._safety_margin = safety_margin
        self._image_tokens = image_tokens

    def budget_for(self, model: ModelDefinition, max_output_tokens: int | None) -> TokenBudget:
        """Бюджет для модели; явный max_output_tokens переопределяет reserved_output."""
        return TokenBudget(
            model_context=model.max_context,
            reserved_output=max_output_tokens or self._reserved_output,
            safety_margin=self._safety_margin,
        )

    def estimate_text(self, text: str) -> int:
        """Оценка текста с учётом chars_per_token менеджера."""
        return estimate_text_tokens(text, chars_per_token=self._chars_per_token)

    def estimate_message(self, message: dict[str, Any]) -> int:
        """Оценка нормализованного сообщения: text parts + image (фикс. цена)."""
        total = 0
        for part in message.get("parts") or []:
            part_type = part.get("type")
            if part_type == "text":
                total += self.estimate_text(part.get("text") or "")
            elif part_type == "image":
                total += self._image_tokens
        return max(1, total)
