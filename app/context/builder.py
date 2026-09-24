"""Сборка контекста для LLM: system + память + сводка + непокрытая история.

PostgreSQL хранит полную историю — builder лишь выбирает хвост, который
помещается в бюджет модели. Сводка покрывает префикс истории ДО
covered_until_message_id: покрытые сообщения в контекст не включаются,
непокрытые — включаются в пределах бюджета (старейшие отбрасываются).
Самые свежие keep_recent сообщений никогда не отбрасываются и не компактятся.
Текущее (current) сообщение в messages НЕ включается — его добавляет
вызывающий код — но учитывается в бюджете (current_parts), как и tools.
"""

from __future__ import annotations

import uuid
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

_IMAGE_PLACEHOLDER = "[изображение]"


@dataclass(frozen=True, slots=True)
class BuiltContext:
    """Результат сборки контекста (без current-сообщения в messages)."""

    system_prompt: str
    messages: list[dict[str, Any]]  # хвост истории (normalized), БЕЗ current
    needs_compaction: bool  # непокрытый материал за пределами recent / бюджет под давлением
    dropped_oldest: int  # сколько старых сообщений не вошло и не покрыто сводкой
    max_output_tokens: int  # reserved output из бюджета → LLMRequest.max_output_tokens
    fits: bool  # system+tools+recent+current влезают в available; False → честный отказ


def _normalize_message(message: Message) -> dict[str, Any] | None:
    """Message → {"role", "parts"}; None, если ни одного text/image part.

    Image-part БЕЗ bytes (data_base64) не теряется: заменяется текстовым
    плейсхолдером "[изображение]", чтобы модель знала о факте фото.
    Image-part С bytes проходит как image (rehydration — на стороне вызывающего).
    """
    parts: list[dict[str, Any]] = []
    for part in message.parts:
        if part.type == "text":
            parts.append({"type": "text", "text": part.text or ""})
        elif part.type == "image":
            data_base64 = getattr(part, "data_base64", None)
            if data_base64:
                image_part: dict[str, Any] = {"type": "image", "data_base64": data_base64}
                mime_type = getattr(part, "mime_type", None)
                if mime_type:
                    image_part["mime_type"] = mime_type
                parts.append(image_part)
            else:
                parts.append({"type": "text", "text": _IMAGE_PLACEHOLDER})
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
        covered_until_message_id: uuid.UUID | str | None = None,
        current_parts: list[dict[str, Any]] | None = None,
        tools_token_estimate: int = 0,
    ) -> BuiltContext:
        """Собрать контекст: system (base + память + сводка) + хвост истории.

        При наличии сводки история делится по covered_until_message_id:
        покрытый префикс игнорируется, непокрытые сообщения старше keep_recent
        добираются под бюджет (старейшие отбрасываются). covered_until, не
        найденный в history (старше окна выборки / историю чистили), трактуется
        как «вся выборка непокрыта» — безопасный пересчёт.
        """
        system_prompt = base_system_prompt
        if memories:
            system_prompt += "\n\n## Долговременная память о пользователе\n" + "\n".join(
                f"- {memory}" for memory in memories
            )
        if summary_json:
            system_prompt += "\n\n" + _render_summary(summary_json)

        uncovered = history
        if summary_json is not None and covered_until_message_id is not None:
            covered_id = str(covered_until_message_id)
            for index, message in enumerate(history):
                if str(message.id) == covered_id:
                    uncovered = history[index + 1 :]
                    break
            # id не найден → вся выборка считается непокрытой

        normalized = [
            item
            for item in (_normalize_message(message) for message in uncovered)
            if item is not None
        ]
        if self._keep_recent > 0:
            recent = normalized[-self._keep_recent :]
            older = normalized[: len(normalized) - len(recent)]
        else:
            recent, older = [], normalized

        budget = self._budget.budget_for(model, max_output_tokens)
        threshold = self._trigger_ratio * budget.available
        used = self._budget.estimate_text(system_prompt)
        used += max(0, tools_token_estimate)
        used += self._budget.estimate_current(current_parts)
        used += sum(self._budget.estimate_message(message) for message in recent)
        # Минимальный обязательный payload: system + tools + current + recent.
        fits = used <= budget.available

        if not older:
            return BuiltContext(
                system_prompt=system_prompt,
                messages=recent,
                needs_compaction=used > threshold,
                dropped_oldest=0,
                max_output_tokens=budget.reserved_output,
                fits=fits,
            )

        # older — непокрытые (при сводке) или просто старые (без сводки):
        # добираем от свежих, пока влезает бюджет; старейшие отбрасываем.
        kept, used, dropped = self._fit_older(older, used, threshold)
        # Есть сводка → непокрытый материал сам по себе триггерит compaction
        # (compactor сам решит по min_segment); без сводки — только реальные потери.
        needs_compaction = summary_json is not None or dropped > 0

        return BuiltContext(
            system_prompt=system_prompt,
            messages=[*kept, *recent],
            needs_compaction=needs_compaction,
            dropped_oldest=dropped,
            max_output_tokens=budget.reserved_output,
            fits=fits,
        )

    def _fit_older(
        self, older: list[dict[str, Any]], used: int, threshold: float
    ) -> tuple[list[dict[str, Any]], int, int]:
        """Добрать старые сообщения от свежих под порог; вернуть kept/used/dropped."""
        kept: list[dict[str, Any]] = []
        for message in reversed(older):
            cost = self._budget.estimate_message(message)
            if used + cost > threshold:
                break
            kept.append(message)
            used += cost
        kept.reverse()
        return kept, used, len(older) - len(kept)
