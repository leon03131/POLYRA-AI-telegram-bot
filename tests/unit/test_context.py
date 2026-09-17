"""Unit-тесты контекстного блока (app.context): token budget и builder."""

from __future__ import annotations

from types import SimpleNamespace

from app.context import (
    ContextBuilder,
    TokenBudget,
    TokenBudgetManager,
    estimate_text_tokens,
)
from app.llm.capabilities import ModelDefinition

# --- estimate_text_tokens ------------------------------------------------------


def test_estimate_text_tokens_thresholds() -> None:
    assert estimate_text_tokens("") == 1  # пустое — минимум 1
    assert estimate_text_tokens("abc") == 1  # ceil(3/3.5) = 1
    assert estimate_text_tokens("abcd") == 2  # ceil(4/3.5) = 2
    assert estimate_text_tokens("abcdefg") == 2  # ceil(7/3.5) = 2
    assert estimate_text_tokens("abcdefgh") == 3  # ceil(8/3.5) = 3


def test_estimate_text_tokens_monotonic() -> None:
    values = [estimate_text_tokens("x" * length) for length in range(0, 60)]
    assert all(a <= b for a, b in zip(values, values[1:], strict=False))


def test_estimate_text_tokens_custom_chars_per_token() -> None:
    # Кириллица плотнее: chars_per_token=1.0 → 1 токен на символ.
    assert estimate_text_tokens("привет", chars_per_token=1.0) == 6


# --- TokenBudget / TokenBudgetManager -------------------------------------------


def test_token_budget_available() -> None:
    budget = TokenBudget(model_context=1000, reserved_output=200, safety_margin=50)
    assert budget.available == 750


def test_budget_for_uses_default_and_explicit_reserved_output() -> None:
    manager = TokenBudgetManager(reserved_output=4096, safety_margin=512)
    model = ModelDefinition(
        provider="gemini", model_id="test-model", display_name="Test", max_context=100_000
    )
    default_budget = manager.budget_for(model, None)
    assert default_budget.reserved_output == 4096
    assert default_budget.available == 100_000 - 4096 - 512
    explicit = manager.budget_for(model, 10_000)
    assert explicit.reserved_output == 10_000


def test_estimate_message_text_and_image_parts() -> None:
    manager = TokenBudgetManager(chars_per_token=1.0, image_tokens=1032)
    text_message = {"role": "user", "parts": [{"type": "text", "text": "abcde"}]}
    assert manager.estimate_message(text_message) == 5
    image_message = {
        "role": "user",
        "parts": [{"type": "image", "mime_type": "image/jpeg"}, {"type": "text", "text": "ab"}],
    }
    assert manager.estimate_message(image_message) == 1032 + 2
    assert manager.estimate_message({"role": "user", "parts": []}) == 1  # минимум 1


# --- ContextBuilder --------------------------------------------------------------


def _msg(role: str, text: str) -> SimpleNamespace:
    return SimpleNamespace(role=role, parts=[SimpleNamespace(type="text", text=text)])


def _model(max_context: int) -> ModelDefinition:
    return ModelDefinition(
        provider="gemini", model_id="test-model", display_name="Test", max_context=max_context
    )


def _builder(keep_recent: int = 2, trigger_ratio: float = 0.7) -> ContextBuilder:
    # chars_per_token=1.0: 1 токен = 1 символ — удобно считать бюджет в тестах.
    manager = TokenBudgetManager(chars_per_token=1.0, reserved_output=0, safety_margin=0)
    return ContextBuilder(manager, keep_recent=keep_recent, trigger_ratio=trigger_ratio)


def test_builder_no_history() -> None:
    built = _builder().build(
        model=_model(100),
        base_system_prompt="база",
        summary_json=None,
        memories=[],
        history=[],
    )
    assert built.messages == []
    assert built.system_prompt == "база"
    assert built.needs_compaction is False
    assert built.dropped_oldest == 0


def test_builder_short_history_fits_recent() -> None:
    history = [_msg("user", "привет"), _msg("assistant", "здравствуй")]
    built = _builder(keep_recent=10).build(
        model=_model(1000),
        base_system_prompt="",
        summary_json=None,
        memories=[],
        history=history,
    )
    assert built.messages == [
        {"role": "user", "parts": [{"type": "text", "text": "привет"}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "здравствуй"}]},
    ]
    assert built.needs_compaction is False
    assert built.dropped_oldest == 0


def test_builder_drops_oldest_without_summary_by_budget() -> None:
    # threshold = 0.7 * 100 = 70; system("") = 1; 6 сообщений по 20 токенов.
    # recent=2 (40) → used=41; older от свежих: +20=61 ≤ 70 берём; +20=81 > 70 стоп.
    history = [_msg("user", "x" * 20) for _ in range(6)]
    built = _builder(keep_recent=2).build(
        model=_model(100),
        base_system_prompt="",
        summary_json=None,
        memories=[],
        history=history,
    )
    assert len(built.messages) == 3  # 1 older + 2 recent
    assert (
        built.messages
        == [
            {"role": "user", "parts": [{"type": "text", "text": "x" * 20}]},
        ]
        * 3
    )
    assert built.dropped_oldest == 3
    assert built.needs_compaction is True


def test_builder_recent_never_dropped_even_over_budget() -> None:
    # recent не влезает совсем — всё равно сохраняется (keep_recent священен).
    history = [_msg("user", "x" * 100) for _ in range(4)]
    built = _builder(keep_recent=2).build(
        model=_model(100),
        base_system_prompt="",
        summary_json=None,
        memories=[],
        history=history,
    )
    assert len(built.messages) == 2
    assert built.dropped_oldest == 2
    assert built.needs_compaction is True


def test_builder_with_summary_uses_recent_only() -> None:
    summary = {"conversation_summary": "короткая сводка", "important_facts": ["факт"]}
    history = [_msg("user", "x" * 20) for _ in range(6)]
    built = _builder(keep_recent=2).build(
        model=_model(200),  # threshold = 140; system ≈ 80 + recent 40 → влезает
        base_system_prompt="",
        summary_json=summary,
        memories=[],
        history=history,
    )
    assert len(built.messages) == 2  # только recent, older покрыты сводкой
    assert built.dropped_oldest == 0
    assert built.needs_compaction is False
    assert "## Сводка предыдущего разговора" in built.system_prompt
    assert "короткая сводка" in built.system_prompt
    assert "- факт" in built.system_prompt


def test_builder_with_summary_budget_pressure_needs_compaction() -> None:
    summary = {"conversation_summary": "короткая сводка"}
    history = [_msg("user", "x" * 20) for _ in range(6)]
    built = _builder(keep_recent=2).build(
        model=_model(100),  # threshold = 70; system ≈ 60 + recent 40 > 70
        base_system_prompt="",
        summary_json=summary,
        memories=[],
        history=history,
    )
    assert built.messages  # recent сохранены
    assert built.needs_compaction is True
    assert built.dropped_oldest == 0


def test_builder_renders_memories_block() -> None:
    built = _builder().build(
        model=_model(1000),
        base_system_prompt="база",
        summary_json=None,
        memories=["любит кошек", "пишет на Python"],
        history=[],
    )
    assert "## Долговременная память о пользователе" in built.system_prompt
    assert "- любит кошек" in built.system_prompt
    assert "- пишет на Python" in built.system_prompt
    assert built.system_prompt.startswith("база")


def test_builder_skips_image_only_messages_and_has_no_current() -> None:
    history = [
        SimpleNamespace(
            role="user",
            parts=[SimpleNamespace(type="image", text=None, mime_type="image/jpeg")],
        ),
        _msg("user", "подпись"),
    ]
    built = _builder(keep_recent=10).build(
        model=_model(1000),
        base_system_prompt="",
        summary_json=None,
        memories=[],
        history=history,
    )
    # image-only выпало; current-сообщения в builder не передаётся и не появляется.
    assert built.messages == [{"role": "user", "parts": [{"type": "text", "text": "подпись"}]}]
