"""Unit-тесты контекстного блока (app.context): token budget и builder."""

from __future__ import annotations

import uuid
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


def test_estimate_current_text_and_image() -> None:
    manager = TokenBudgetManager(chars_per_token=1.0, image_tokens=1032)
    assert manager.estimate_current(None) == 0
    assert manager.estimate_current([]) == 0
    assert manager.estimate_current([{"type": "text", "text": "abc"}]) == 3
    # image в current — фиксированная цена, как и в истории
    assert manager.estimate_current([{"type": "image", "mime_type": "image/jpeg"}]) == 1032
    parts = [{"type": "image"}, {"type": "text", "text": "ab"}]
    assert manager.estimate_current(parts) == 1032 + 2


# --- ContextBuilder --------------------------------------------------------------


def _msg(role: str, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        role=role,
        parts=[SimpleNamespace(type="text", text=text)],
    )


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
        covered_until_message_id=history[3].id,  # older (0..3) покрыты сводкой
    )
    assert len(built.messages) == 2  # только recent, покрытое не дублируется
    assert built.dropped_oldest == 0
    assert built.needs_compaction is False
    assert built.fits is True
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
        covered_until_message_id=history[3].id,
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


def test_builder_image_part_without_bytes_becomes_placeholder() -> None:
    # Image-part БЕЗ bytes не выбрасывается молча — текстовый плейсхолдер (A18).
    history = [
        SimpleNamespace(
            id=uuid.uuid4(),
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
    # current-сообщения в builder не передаётся и не появляется.
    assert built.messages == [
        {"role": "user", "parts": [{"type": "text", "text": "[изображение]"}]},
        {"role": "user", "parts": [{"type": "text", "text": "подпись"}]},
    ]


def test_builder_image_part_with_bytes_kept_as_image() -> None:
    # Image-part С bytes (data_base64) проходит как image — модель видит фото (A18).
    history = [
        SimpleNamespace(
            id=uuid.uuid4(),
            role="user",
            parts=[
                SimpleNamespace(
                    type="image", text=None, mime_type="image/jpeg", data_base64="QUJD"
                ),
                SimpleNamespace(type="text", text="что на фото?"),
            ],
        ),
    ]
    built = _builder(keep_recent=10).build(
        model=_model(1000),
        base_system_prompt="",
        summary_json=None,
        memories=[],
        history=history,
    )
    assert built.messages == [
        {
            "role": "user",
            "parts": [
                {"type": "image", "data_base64": "QUJD", "mime_type": "image/jpeg"},
                {"type": "text", "text": "что на фото?"},
            ],
        }
    ]


# --- A15: покрытие истории ----------------------------------------------------


def test_builder_uncovered_segment_included_with_summary() -> None:
    # 30 сообщений, сводка покрывает первые 10 → 20 непокрытых: все влезают.
    history = [_msg("user", f"m{i}") for i in range(30)]
    built = _builder(keep_recent=10).build(
        model=_model(100_000),
        base_system_prompt="",
        summary_json={"conversation_summary": "старая сводка"},
        memories=[],
        history=history,
        covered_until_message_id=history[9].id,
    )
    assert len(built.messages) == 20  # весь непокрытый сегмент, не только recent
    assert built.messages[0]["parts"][0]["text"] == "m10"  # boundary строго после m9
    assert built.messages[-1]["parts"][0]["text"] == "m29"
    assert built.dropped_oldest == 0
    # есть непокрытый материал за пределами recent → compaction (даже если всё влезло)
    assert built.needs_compaction is True
    assert built.fits is True


def test_builder_boundary_strictly_at_covered_until() -> None:
    # covered_until внутри recent-окна: включать только сообщения ПОСЛЕ него.
    history = [_msg("user", f"m{i}") for i in range(10)]
    built = _builder(keep_recent=10).build(
        model=_model(100_000),
        base_system_prompt="",
        summary_json={"conversation_summary": "s"},
        memories=[],
        history=history,
        covered_until_message_id=history[7].id,
    )
    assert [m["parts"][0]["text"] for m in built.messages] == ["m8", "m9"]
    assert built.dropped_oldest == 0
    assert built.needs_compaction is False  # непокрытого за пределами recent нет


def test_builder_uncovered_dropped_oldest_by_budget() -> None:
    # threshold = 0.7*100 = 70; system ≈ 40 (рендер сводки) + recent 2×20 = 40.
    # Непокрытые older по 20: первый 40+40+20 = ~100 > 70 → не влезает никто.
    history = [_msg("user", "x" * 20) for _ in range(6)]
    built = _builder(keep_recent=2).build(
        model=_model(10_000),  # большой бюджет: всё влезает
        base_system_prompt="",
        summary_json={"conversation_summary": "s"},
        memories=[],
        history=history,
        covered_until_message_id=history[0].id,  # покрыто ровно 1 → 5 непокрытых
    )
    assert len(built.messages) == 5
    assert built.dropped_oldest == 0
    assert built.needs_compaction is True

    tight = _builder(keep_recent=2).build(
        model=_model(100),  # threshold = 70: older не влезают
        base_system_prompt="",
        summary_json={"conversation_summary": "s"},
        memories=[],
        history=history,
        covered_until_message_id=history[0].id,
    )
    assert len(tight.messages) == 2  # только recent
    assert tight.dropped_oldest == 3  # непокрытые older отброшены
    assert tight.needs_compaction is True


def test_builder_unknown_covered_id_treats_all_as_uncovered() -> None:
    # covered_until не найден в выборке (старше окна) → вся выборка непокрыта.
    history = [_msg("user", f"m{i}") for i in range(15)]
    built = _builder(keep_recent=10).build(
        model=_model(100_000),
        base_system_prompt="",
        summary_json={"conversation_summary": "s"},
        memories=[],
        history=history,
        covered_until_message_id=uuid.uuid4(),
    )
    assert len(built.messages) == 15
    assert built.messages[0]["parts"][0]["text"] == "m0"
    assert built.needs_compaction is True


# --- A16: полный бюджет ---------------------------------------------------------


def test_builder_current_and_tools_counted_in_budget() -> None:
    history = [_msg("user", "x" * 20) for _ in range(6)]
    base = _builder(keep_recent=2).build(
        model=_model(100),  # threshold = 70; system=1 + recent 40 = 41
        base_system_prompt="",
        summary_json=None,
        memories=[],
        history=history,
    )
    assert len(base.messages) == 3  # один older влезает (41+20=61 ≤ 70)
    assert base.dropped_oldest == 3

    with_extras = _builder(keep_recent=2).build(
        model=_model(100),
        base_system_prompt="",
        summary_json=None,
        memories=[],
        history=history,
        current_parts=[{"type": "text", "text": "y" * 20}],  # +20
        tools_token_estimate=20,  # +20 → used = 81 > 70
    )
    assert len(with_extras.messages) == 2  # older больше не влезают
    assert with_extras.dropped_oldest == 4
    assert with_extras.needs_compaction is True


def test_builder_current_image_counted_in_budget() -> None:
    # image в current = 1032 токена — само по себе больше маленького бюджета.
    built = _builder(keep_recent=2).build(
        model=_model(1000),
        base_system_prompt="",
        summary_json=None,
        memories=[],
        history=[_msg("user", "привет")],
        current_parts=[{"type": "image", "mime_type": "image/jpeg"}],
    )
    assert built.fits is False  # 1 + 6 + 1032 > 1000
    assert built.messages  # recent не отброшены


def test_builder_fits_false_when_minimum_exceeds_available() -> None:
    # Даже system + recent не влезают в available → fits=False (lead решит отказ).
    history = [_msg("user", "x" * 100) for _ in range(2)]
    built = _builder(keep_recent=2).build(
        model=_model(100),
        base_system_prompt="",
        summary_json=None,
        memories=[],
        history=history,
    )
    assert built.fits is False
    assert len(built.messages) == 2  # recent священны — не режем


def test_builder_propagates_max_output_tokens() -> None:
    # Явный max_output_tokens → резервируется и возвращается в BuiltContext.
    built = _builder(keep_recent=2).build(
        model=_model(1000),
        base_system_prompt="",
        summary_json=None,
        memories=[],
        history=[],
        max_output_tokens=1234,
    )
    assert built.max_output_tokens == 1234

    # Без явного — дефолтный reserved_output менеджера.
    manager = TokenBudgetManager(chars_per_token=1.0, reserved_output=4096, safety_margin=512)
    builder = ContextBuilder(manager, keep_recent=2)
    default_built = builder.build(
        model=_model(100_000),
        base_system_prompt="",
        summary_json=None,
        memories=[],
        history=[],
    )
    assert default_built.max_output_tokens == 4096
    assert default_built.fits is True
