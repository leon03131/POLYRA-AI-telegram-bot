"""Централизованный реестр возможностей моделей (ModelDefinition).

Источник истины о моделях. Telegram handlers / Mini App НЕ содержат
"if model == ..." — только capabilities из реестра.

Факты подтверждены research 2026-09-18 (docs/vendor/GEMINI.md, ALIBABA.md).
Спорные места (Kimi OFF и др.) решает runtime capability probe — поле
`probe_required` помечает такие опции.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ProviderId = Literal["gemini", "alibaba"]

# UI-уровни thinking, которые МОЖЕТ показать Mini App (после probe-фильтрации).
THINKING_OFF = "off"
THINKING_MINIMAL = "minimal"
THINKING_LOW = "low"
THINKING_MEDIUM = "medium"
THINKING_HIGH = "high"
THINKING_MAX = "max"


@dataclass(frozen=True, slots=True)
class ModelDefinition:
    """Полное описание модели для registry/UI/роутинга."""

    provider: ProviderId
    model_id: str
    display_name: str
    enabled: bool = True
    input_modalities: frozenset[str] = frozenset({"text"})
    max_context: int = 1_000_000
    max_output: int = 131_072
    function_calling: bool = True
    structured_output: bool = True
    thinking_modes: tuple[str, ...] = ()  # пусто = thinking не поддержан
    default_thinking: str | None = None  # None = дефолт провайдера
    native_web_search: bool = False
    internal_only: bool = False  # скрытая служебная модель (не показывать в UI)
    # UI-опции, которые требуют подтверждения runtime probe (например "off" у kimi-k3)
    probe_required: frozenset[str] = frozenset()
    provider_options: dict[str, Any] = field(default_factory=dict)

    @property
    def supports_images(self) -> bool:
        return "image" in self.input_modalities


_GEMINI_CONTEXT = 1_048_576
_GEMINI_MAX_OUTPUT = 65_536

GEMINI_MODELS: tuple[ModelDefinition, ...] = (
    ModelDefinition(
        provider="gemini",
        model_id="gemini-3.8-flash",
        display_name="Gemini 3.8 Flash",
        input_modalities=frozenset({"text", "image"}),
        max_context=_GEMINI_CONTEXT,
        max_output=_GEMINI_MAX_OUTPUT,
        thinking_modes=(THINKING_LOW, THINKING_MEDIUM, THINKING_HIGH),
        default_thinking=THINKING_MEDIUM,
    ),
    ModelDefinition(
        provider="gemini",
        model_id="gemini-3.7-flash",
        display_name="Gemini 3.7 Flash",
        input_modalities=frozenset({"text", "image"}),
        max_context=_GEMINI_CONTEXT,
        max_output=_GEMINI_MAX_OUTPUT,
        thinking_modes=(THINKING_LOW, THINKING_MEDIUM, THINKING_HIGH),
        default_thinking=THINKING_MEDIUM,
    ),
    ModelDefinition(
        provider="gemini",
        model_id="gemini-3.6-flash",
        display_name="Gemini 3.6 Flash",
        input_modalities=frozenset({"text", "image"}),
        max_context=_GEMINI_CONTEXT,
        max_output=_GEMINI_MAX_OUTPUT,
        thinking_modes=(THINKING_MINIMAL, THINKING_LOW, THINKING_MEDIUM, THINKING_HIGH),
        default_thinking=THINKING_MEDIUM,
    ),
    # Внутренняя служебная модель: compaction, memory extraction, titles.
    ModelDefinition(
        provider="gemini",
        model_id="gemini-3.5-flash-lite",
        display_name="Gemini 3.5 Flash-Lite (internal)",
        input_modalities=frozenset({"text", "image"}),
        max_context=_GEMINI_CONTEXT,
        max_output=_GEMINI_MAX_OUTPUT,
        thinking_modes=(THINKING_MINIMAL, THINKING_LOW, THINKING_MEDIUM, THINKING_HIGH),
        default_thinking=THINKING_MINIMAL,
        internal_only=True,
    ),
)

ALIBABA_MODELS: tuple[ModelDefinition, ...] = (
    ModelDefinition(
        provider="alibaba",
        model_id="qwen3.8-flash",
        display_name="Qwen 3.8 Flash",
        input_modalities=frozenset({"text", "image"}),
        thinking_modes=(THINKING_OFF, THINKING_LOW, THINKING_MEDIUM, THINKING_MAX),
        default_thinking=None,  # provider default = xhigh
    ),
    ModelDefinition(
        provider="alibaba",
        model_id="qwen3.8-max",
        display_name="Qwen 3.8 Max",
        input_modalities=frozenset({"text", "image"}),
        thinking_modes=(THINKING_OFF, THINKING_LOW, THINKING_MEDIUM, THINKING_MAX),
        default_thinking=None,
    ),
    ModelDefinition(
        provider="alibaba",
        model_id="deepseek-v4.1-flash",
        display_name="DeepSeek V4.1 Flash",
        input_modalities=frozenset({"text", "image"}),
        thinking_modes=(THINKING_OFF, THINKING_LOW, THINKING_HIGH, THINKING_MAX),
        default_thinking=None,
        # probe 2026-09-18: OFF (enable_thinking=false) принят endpoint'ом.
    ),
    ModelDefinition(
        provider="alibaba",
        model_id="glm-5.3",
        display_name="GLM 5.3",
        input_modalities=frozenset({"text"}),  # vision НЕТ
        thinking_modes=(
            THINKING_LOW,
            THINKING_HIGH,
            THINKING_MAX,
        ),  # OFF невозможен (thinking-only)
        default_thinking=THINKING_MAX,
        provider_options={"clear_thinking": True},
    ),
    ModelDefinition(
        provider="alibaba",
        model_id="kimi-k3",
        display_name="Kimi K3",
        input_modalities=frozenset({"text", "image"}),
        thinking_modes=(THINKING_OFF, THINKING_LOW, THINKING_HIGH, THINKING_MAX),
        default_thinking=THINKING_MAX,
        # probe 2026-09-18: OFF принят; DTL (system+tools без content) работает
        # на MS endpoint. max_output=1M на странице модели — не доверяем, 131072.
        provider_options={"image_url_requires_base64": True},  # Moonshot: публичные URL запрещены
    ),
)

ALL_MODELS: tuple[ModelDefinition, ...] = GEMINI_MODELS + ALIBABA_MODELS
