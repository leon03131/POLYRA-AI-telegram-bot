"""Сборка контекста для LLM: system + память + сводка + recent raw.

PostgreSQL хранит полную историю — builder лишь выбирает хвост, который
помещается в бюджет модели. Текущее (current) сообщение сюда НЕ входит:
его добавляет вызывающий код. Самые свежие keep_recent сообщений никогда
не отбрасываются и не компактятся.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.context.token_budget import TokenBudgetManager
from app.llm.capabilities import ModelDefinition

if TYPE_CHECKING:
    from app.db.models import Message

_SUMMARY_SECTIONS: tuple[tuple[str, str], ...] = (
    ("important_facts", "Важные факты"),
    ("decisions", "Решения"),
    ("open_threads", "Открытые вопросы и задачи"),
    ("user_preferences", "Предпочтения пользователя"),
    ("entities", "Сущности"),
)


@dataclass(frozen=True, slots=True)
class BuiltContext:
    """Результат сборки контекста (без current-сообщения)."""

    system_prompt: str
    messages: list[dict[str, Any]]  # хвост истории (normalized), БЕЗ current
    needs_compaction: bool  # старые сообщения не поместились / бюджет под давлением
    dropped_oldest: int  # сколько старых сообщений не вошло и не покрыто сводкой


def _normalize_message(message: Message) -> dict[str, Any] | None:
    """Message → {"role", "parts"}; только text parts. None, если текста нет."""
    parts = [
        {"type": "text", "text": part.text or ""} for part in message.parts if part.type == "text"
    ]
    if not parts:
        return None
    return {"role": message.role, "parts": parts}


def _render_summary(summary: dict[str, Any]) -> str:
    """Компактный рендер summary_json в секцию system prompt."""
    lines = ["## Сводка предыдущего разговора"]
    text = summary.get("conversation_summary")
    if isinstance(text, str) and text.strip():
        lines.append(text.strip())
    for key, header in _SUMMARY_SECTIONS:
        items = summary.get(key)
        if isinstance(items, list) and items:
            lines.append(f"{header}:")
            lines.extend(f"- {item}" for item in items if item)
    return "\n".join(lines)


class ContextBuilder:
    """Собирает BuiltContext под бюджет модели."""

    def __init__(
        self,
        budget: TokenBudgetManager,
        *,
        keep_recent: int = 10,
        trigger_ratio: float = 0.7,
    ) -> None:
        self._budget = budget
        self._keep_recent = keep_recent
        self._trigger_ratio = trigger_ratio

    def build(
        self,
        *,
        model: ModelDefinition,
        base_system_prompt: str,
        summary_json: dict[str, Any] | None,
        memories: list[str],
        history: list[Message],
        max_output_tokens: int | None = None,
    ) -> BuiltContext:
        """Собрать контекст: system (base + память + сводка) + хвост истории."""
        system_prompt = base_system_prompt
        if memories:
            system_prompt += "\n\n## Долговременная память о пользователе\n" + "\n".join(
                f"- {memory}" for memory in memories
            )
        if summary_json:
            system_prompt += "\n\n" + _render_summary(summary_json)

        normalized = [
            item
            for item in (_normalize_message(message) for message in history)
            if item is not None
        ]
        if self._keep_recent > 0:
            recent = normalized[-self._keep_recent :]
            older = normalized[: len(normalized) - len(recent)]
        else:
            recent, older = [], normalized

        if not older:
            return BuiltContext(
                system_prompt=system_prompt,
                messages=recent,
                needs_compaction=False,
                dropped_oldest=0,
            )

        budget = self._budget.budget_for(model, max_output_tokens)
        threshold = self._trigger_ratio * budget.available

        if summary_json is not None:
            # Старые сообщения считаются покрытыми сводкой; compactor сам решит
            # по covered_until, нужна ли догрузка. Здесь — только давление бюджета.
            used = self._budget.estimate_text(system_prompt)
            used += sum(self._budget.estimate_message(message) for message in recent)
            return BuiltContext(
                system_prompt=system_prompt,
                messages=recent,
                needs_compaction=used > threshold,
                dropped_oldest=0,
            )

        # Сводки нет: включаем older, пока влезает бюджет; отбрасываем самые старые.
        used = self._budget.estimate_text(system_prompt)
        used += sum(self._budget.estimate_message(message) for message in recent)
        kept: list[dict[str, Any]] = []
        for message in reversed(older):
            cost = self._budget.estimate_message(message)
            if used + cost > threshold:
                break
            kept.append(message)
            used += cost
        kept.reverse()
        dropped = len(older) - len(kept)
        return BuiltContext(
            system_prompt=system_prompt,
            messages=[*kept, *recent],
            needs_compaction=dropped > 0,
            dropped_oldest=dropped,
        )
