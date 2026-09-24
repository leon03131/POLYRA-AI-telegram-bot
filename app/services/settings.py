"""Эффективные системные настройки: DB system_settings поверх env-дефолтов (A13).

Runtime читает настройки на КАЖДЫЙ запрос (короткая сессия, без кеша на старте):
admin → System меняет поведение со следующего LLMRequest (контракт FIX V2 §3).
DB-значения перекрывают env-дефолты; невалидное DB-значение (тип/диапазон) →
env-дефолт + warning в лог.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.db.repositories import SystemSettingRepository

logger = logging.getLogger(__name__)

KEY_DEFAULT_MODEL = "default_model"
KEY_DEFAULT_THINKING = "default_thinking"
KEY_DEFAULT_SYSTEM_PROMPT = "default_system_prompt"
KEY_MAX_TOOL_ITERATIONS = "max_tool_iterations"
KEY_CONTEXT_KEEP_RECENT = "context_keep_recent"
KEY_CONTEXT_TRIGGER_RATIO = "context_trigger_ratio"
KEY_MEMORY_RETRIEVAL_LIMIT = "memory_retrieval_limit"
KEY_MEMORY_EXTRACTION_MIN_CHARS = "memory_extraction_min_chars"

ALL_KEYS: tuple[str, ...] = (
    KEY_DEFAULT_MODEL,
    KEY_DEFAULT_THINKING,
    KEY_DEFAULT_SYSTEM_PROMPT,
    KEY_MAX_TOOL_ITERATIONS,
    KEY_CONTEXT_KEEP_RECENT,
    KEY_CONTEXT_TRIGGER_RATIO,
    KEY_MEMORY_RETRIEVAL_LIMIT,
    KEY_MEMORY_EXTRACTION_MIN_CHARS,
)

_INVALID: Any = object()  # sentinel «невалидно» (None может быть валидным значением)


@dataclass(frozen=True, slots=True)
class EffectiveSystemSettings:
    """Типизированный снимок системных настроек для generation path."""

    default_model: str
    default_thinking: str | None
    default_system_prompt: str
    max_tool_iterations: int
    context_keep_recent: int
    context_trigger_ratio: float
    memory_retrieval_limit: int
    memory_extraction_min_chars: int


def _positive_int(value: Any) -> Any:
    """int > 0 (bool отвергается); иначе _INVALID."""
    if isinstance(value, bool) or not isinstance(value, int):
        return _INVALID
    return value if value > 0 else _INVALID


def _ratio(value: Any) -> Any:
    """float в (0, 1) (bool отвергается); иначе _INVALID."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return _INVALID
    result = float(value)
    return result if 0 < result < 1 else _INVALID


def _non_empty_str(value: Any) -> Any:
    """Непустая строка; иначе _INVALID."""
    return value if isinstance(value, str) and value else _INVALID


def _optional_str(value: Any) -> Any:
    """None или непустая строка; иначе _INVALID."""
    if value is None:
        return None
    return value if isinstance(value, str) and value else _INVALID


async def get_effective_system_settings(
    session: AsyncSession, settings: Settings
) -> EffectiveSystemSettings:
    """Прочитать system_settings и слить с env-дефолтами (DB перекрывает env)."""
    stored = await SystemSettingRepository(session).get_many(ALL_KEYS)

    def pick(key: str, env_default: Any, coerce: Callable[[Any], Any]) -> Any:
        if key not in stored:
            return env_default
        coerced = coerce(stored[key])
        if coerced is _INVALID:
            logger.warning(
                "system_settings[%r]=%r невалидно; используется env-дефолт %r",
                key,
                stored[key],
                env_default,
            )
            return env_default
        return coerced

    return EffectiveSystemSettings(
        default_model=pick(KEY_DEFAULT_MODEL, settings.default_model, _non_empty_str),
        default_thinking=pick(KEY_DEFAULT_THINKING, None, _optional_str),
        default_system_prompt=pick(
            KEY_DEFAULT_SYSTEM_PROMPT, settings.default_system_prompt, _non_empty_str
        ),
        max_tool_iterations=pick(
            KEY_MAX_TOOL_ITERATIONS, settings.max_tool_iterations, _positive_int
        ),
        context_keep_recent=pick(
            KEY_CONTEXT_KEEP_RECENT, settings.context_keep_recent, _positive_int
        ),
        context_trigger_ratio=pick(
            KEY_CONTEXT_TRIGGER_RATIO, settings.context_trigger_ratio, _ratio
        ),
        memory_retrieval_limit=pick(
            KEY_MEMORY_RETRIEVAL_LIMIT, settings.memory_retrieval_limit, _positive_int
        ),
        memory_extraction_min_chars=pick(
            KEY_MEMORY_EXTRACTION_MIN_CHARS,
            settings.memory_extraction_min_chars,
            _positive_int,
        ),
    )
