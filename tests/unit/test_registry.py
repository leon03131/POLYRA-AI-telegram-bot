"""Тесты ModelRegistry и seed-данных capabilities."""

from app.llm.capabilities import ALL_MODELS
from app.llm.registry import UnknownModelError, default_registry


def test_registry_contains_all_spec_models() -> None:
    registry = default_registry()
    expected = {
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
        "qwen3.8-flash",
        "qwen3.8-max",
        "deepseek-v4.1-flash",
        "glm-5.3",
        "kimi-k3",
    }
    assert {m.model_id for m in registry.list_all()} == expected


def test_internal_model_hidden_from_user_list() -> None:
    registry = default_registry()
    user_ids = {m.model_id for m in registry.list_user_models()}
    assert "gemini-3.5-flash-lite" not in user_ids
    assert "gemini-3.5-flash-lite" in {m.model_id for m in registry.list_internal_models()}


def test_get_unknown_model_raises() -> None:
    registry = default_registry()
    try:
        registry.get("no-such-model")
    except UnknownModelError:
        return
    raise AssertionError("expected UnknownModelError")


def test_thinking_modes_per_model() -> None:
    registry = default_registry()
    assert registry.get("gemini-3.8-flash").thinking_modes == ("low", "medium", "high")
    assert registry.get("gemini-3.6-flash").thinking_modes == ("minimal", "low", "medium", "high")
    assert registry.get("glm-5.3").thinking_modes == ("low", "high", "max")  # без OFF
    assert "off" in registry.get("qwen3.8-flash").thinking_modes


def test_images_capability() -> None:
    registry = default_registry()
    assert registry.get("glm-5.3").supports_images is False
    assert registry.get("kimi-k3").supports_images is True
    assert registry.get("gemini-3.8-flash").supports_images is True


def test_filter_by_permissions() -> None:
    registry = default_registry()
    all_user_models = registry.filter_by_permissions(None)
    restricted = registry.filter_by_permissions({"kimi-k3"})
    assert [m.model_id for m in restricted] == ["kimi-k3"]
    assert len(all_user_models) == len(ALL_MODELS) - 1  # минус internal
