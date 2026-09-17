"""LLMRouter — выбор провайдера по ModelRegistry. БЕЗ cross-model fallback (ADR-005)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

from app.llm.base import LLMProvider, LLMRequest
from app.llm.events import LLMEvent
from app.llm.registry import ModelRegistry


class LLMRouter:
    """Маршрутизирует запрос к провайдеру выбранной модели.

    НИКОГДА не подменяет модель: если модель упала — ошибка уходит наверх,
    пользователь решает, повторять ли. Failover допустим только внутри
    провайдера между credentials одной модели (Gemini pool, M4).
    """

    def __init__(self, registry: ModelRegistry, providers: Mapping[str, LLMProvider]) -> None:
        self._registry = registry
        self._providers = providers

    def provider_for(self, model_id: str) -> LLMProvider:
        model = self._registry.get(model_id)
        try:
            return self._providers[model.provider]
        except KeyError as exc:
            raise RuntimeError(f"provider not configured: {model.provider}") from exc

    def stream_chat(self, request: LLMRequest) -> AsyncIterator[LLMEvent]:
        provider = self.provider_for(request.model)
        return provider.stream_chat(request)
