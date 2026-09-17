"""ModelRegistry — доступ к ModelDefinition. Единственный путь узнать о модели."""

from __future__ import annotations

from collections.abc import Iterable

from app.llm.capabilities import ALL_MODELS, ModelDefinition


class UnknownModelError(KeyError):
    """Запрошена неизвестная model_id."""


class ModelRegistry:
    """Иммутабельный реестр моделей. DB-overrides (enabled) добавятся в M10."""

    def __init__(self, models: Iterable[ModelDefinition] = ALL_MODELS) -> None:
        self._by_id: dict[str, ModelDefinition] = {m.model_id: m for m in models}

    def get(self, model_id: str) -> ModelDefinition:
        try:
            return self._by_id[model_id]
        except KeyError as exc:
            raise UnknownModelError(model_id) from exc

    def get_or_none(self, model_id: str) -> ModelDefinition | None:
        return self._by_id.get(model_id)

    def list_all(self) -> list[ModelDefinition]:
        return list(self._by_id.values())

    def list_user_models(self, *, include_disabled: bool = False) -> list[ModelDefinition]:
        """Модели для выбора в UI: не internal, enabled (если не include_disabled)."""
        return [
            m
            for m in self._by_id.values()
            if not m.internal_only and (include_disabled or m.enabled)
        ]

    def list_internal_models(self) -> list[ModelDefinition]:
        return [m for m in self._by_id.values() if m.internal_only]

    def filter_by_permissions(
        self,
        allowed_model_ids: set[str] | frozenset[str] | None,
    ) -> list[ModelDefinition]:
        """UI-список с учётом per-user permissions (None = без ограничений)."""
        models = self.list_user_models()
        if allowed_model_ids is None:
            return models
        return [m for m in models if m.model_id in allowed_model_ids]


def default_registry() -> ModelRegistry:
    return ModelRegistry()
