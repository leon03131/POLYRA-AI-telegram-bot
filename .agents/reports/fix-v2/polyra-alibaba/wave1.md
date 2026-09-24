# polyra-alibaba — wave1 (dev-pass)

**Дата:** 2026-09-24
**Субагент:** polyra-alibaba
**Baseline:** HEAD 4e07eb0 (как в revalidate.md); задачи N04, A23, A27 + тесты + docs.
**Ограничения:** live API-вызовы и pytest НЕ запускались (интеграция у lead); проверено `py_compile` + офлайн-прогон парсера/payload/registry на stdlib-скрипте (удалён после прогона). Git-операций не было.

## Scope (фактически изменённые файлы)

| Файл | Изменение |
|---|---|
| `app/llm/capabilities.py` | + `ModelDefinition` deepseek-v4-pro (N04) |
| `app/llm/providers/alibaba.py` | + `_THINKING_TABLE["deepseek-v4-pro"]`; переработан `parse_chat_completions_sse` (A23); `stream_chat`: отмена ≠ EOF-ошибка, NetworkError в retry-цепочке |
| `scripts/smoke_providers.py` | registry-enumeration вместо `_GEMINI_MODELS/_ALIBABA_MODELS` (удалены); `--strict`; `_count_failures`; docstring exit codes |
| `tests/unit/test_alibaba_provider.py` | `test_usage_final_chunk` переупорядочен; +3 SSE/EOF-теста; +payload-тесты deepseek-v4-pro; import NetworkError |
| `tests/unit/test_registry.py` | expected-set + deepseek-v4-pro, ≥10 моделей; + `test_deepseek_v4_pro_definition`; Pro в user list |
| `docs/vendor/ALIBABA.md` | + секция «Update 2026-09-24» |
| `.agents/reports/fix-v2/polyra-alibaba/wave1.md` | этот отчёт |

`app/llm/registry.py` — в scope, но изменений не потребовал (enumeration работает через `default_registry().list_all()`).

## ЗАДАЧА 1 — N04 (deepseek-v4-pro)

- `capabilities.py`: provider=alibaba, `display_name="DeepSeek V4 Pro"`, `input_modalities=frozenset({"text"})` (text-only, image НЕ копировался с flash), `max_context=1_000_000`, `max_output=393_216`, `function_calling=True`, `structured_output=True`, `thinking_modes=("off","high","max")` (low/medium нет — у стабильного ID low/medium = alias на high по chat-ref 2026-09-22), `default_thinking=None` (provider default = thinking ON), `internal_only=False`. НЕ default-модель (default-роутинга в этом файле нет — модель просто добавлена в `ALIBABA_MODELS`).
- `alibaba.py::_THINKING_TABLE["deepseek-v4-pro"]`: off→`{"enable_thinking": False}`, high→`{"reasoning_effort": "high"}`, max→`{"reasoning_effort": "max"}`. `thinking_budget`/`preserve_thinking`/`clear_thinking` не шлются (модель не в `_QWEN_MODELS`, не `_GLM_MODEL` — side-эффекты `_thinking_params` её не касаются).
- Существующие модели не тронуты. Проверено офлайн: payload для 4 состояний thinking ровно как в спецификации.

## ЗАДАЧА 2 — A23 (SSE-контракт)

Решение: `parse_chat_completions_sse` буферизует `Done` (`pending_done`) вместо немедленной эмиссии; `Usage` и прочие события эмитятся сразу; после `[DONE]`/EOF эмитится буферизованный `Done` последним и генератор завершается (`return`). Итоговый порядок: `[..., Usage?, Done]` — соответствует FIX_V2 §1.

- **Usage до Done**: finish-чанк больше не эмитит Done досрочно; usage-trailer (`choices: []`) успевает эмититься до терминального Done.
- **Unexpected EOF**: ни finish_reason, ни `[DONE]` → `NetworkError("unexpected EOF: stream ended without finish")`. `[DONE]` без finish_reason — тоже EOF-ошибка (терминального Done нет). Недособранные `tool_calls` при EOF — та же ошибка (pending не флашится без finish, `pending_done` остаётся None).
- **Malformed JSON**: `json.JSONDecodeError` → `logger.debug` + skip — сохранено.
- **Отмена**: `_cancellable_lines` обрывает поток без Done → парсер бросает NetworkError; `stream_chat` глушит его, если `request.cancellation.is_set()` (тихий cancel-путь сохранён, регрессии `test_cancellation_before_iteration_yields_nothing` нет — события пустые).
- **Retry**: NetworkError добавлен в except-цепочку `stream_chat` → retry 1 раз только если ни одно событие не эмитнуто (`events_started`), иначе raise. Поведение для 429/5xx/timeout не изменено.

Проверено офлайн-скриптом: (1) `[TextDelta, Usage, Done]`; (2) EOF без finish → NetworkError; (3) malformed skip; (4) EOF с pending tool_calls → NetworkError; (5) fragmented `{"a":` + `1}` → `{"a":1}`, Done последним.

## ЗАДАЧА 3 — A27 (probe)

- `_GEMINI_MODELS`/`_ALIBABA_MODELS` **удалены**; enumeration через `_registry_models(registry, provider)` = `default_registry().list_all()` по провайдеру, internal_only включаются в прогон и помечаются в notes (`"internal_only — служебная модель"` — существующее поведение `_probe_gemini_model`). Покрытие теперь автоматически включает `gemini-3.7-flash`, `qwen3.8-max`, `deepseek-v4-pro` (10 моделей total ≥ 10 baseline IDs).
- `--strict`: exit 1 если `_count_failures(report) > 0`; fail уровня провайдера-секции (нет ключей/status != ok) и `skipped` за fail НЕ считаются. Без `--strict` — 0 как раньше. Docstring обновлён (0 ok / 1 strict-fail / 130 interrupt).
- Check текстового стрима уже требовал TextDelta + Done (+ Usage) через `_missing_events` — оставлено, требование «не только accepted» выполнено. После фикса A23 Usage для alibaba снова доезжает до потребителя.
- Секреты: проверено — stdout/JSON содержат только `mask_secret` + source; `key_mask`/`key_source` в JSON, самих ключей нет. `_alibaba_recommendations` переведён на registry-enumeration.

## ЗАДАЧА 4 — тесты (offline, MockTransport)

`test_alibaba_provider.py`:
- `test_usage_final_chunk` — обновлён на порядок `[TextDelta, Usage, Done]`.
- `test_unexpected_eof_without_finish_raises` — EOF без finish/`[DONE]` → NetworkError("unexpected EOF").
- `test_unexpected_eof_with_pending_tool_calls_raises` — EOF с недособранными tool_calls → NetworkError.
- `test_done_marker_without_finish_is_eof_error` — `[DONE]` без finish → NetworkError.
- `test_build_payload_deepseek_v4_pro_thinking` (parametrize None/off/high/max) — ровно ожидаемые ключи, запрет `thinking_budget/preserve_thinking/clear_thinking` (и cross-запрет enable_thinking↔reasoning_effort).
- `test_build_payload_deepseek_v4_pro_max_tokens` — `max_output_tokens` → `max_completion_tokens`, `max_tokens` отсутствует.
- Регрессия fragmented tool arguments — существующий `test_tool_calls_incremental` (не тронут, логика подтверждена офлайн-прогоном).

`test_registry.py`:
- `test_registry_contains_all_spec_models` — +deepseek-v4-pro, `len >= 10`, deepseek-v4.1-flash на месте (Flash не заменён).
- `test_deepseek_v4_pro_definition` — text-only (`supports_images is False`), `thinking_modes == ("off","high","max")`, `default_thinking is None`, не internal, ceilings 1M/393216, FC/SO True.
- `test_internal_model_hidden_from_user_list` — + Pro в `list_user_models`.

**ВАЖНО для lead:** pytest НЕ запускался (по инструкции — интеграция у lead). Логика верифицирована: `py_compile` всех 5 файлов = OK; офлайн-прогон парсера/payload/registry (stdlib asyncio + json, без сети и httpx) — все 7 сценариев сошлись с ожиданиями (см. выше). Ожидаемые риски при прогоне pytest: нет — тесты используют существующие helper'ы (`_chunk`, `_sse_response`, `_make_provider`).

## ЗАДАЧА 5 — docs

`docs/vendor/ALIBABA.md` + секция «Update 2026-09-24»: факты deepseek-v4-pro (text-only, 1M/393216, FC/SO, thinking ON default, effort high/max native, low/medium→alias high, запрет thinking_budget/preserve_thinking/clear_thinking, max_completion_tokens=ответ+CoT), источники с датами (model page Sep 20 2026, chat-ref/deep-thinking Sep 22 2026, проверено 2026-09-24), снятие противоречия №1 (обе страницы теперь «thinking enabled by default»), остаточный doc-gap по снапшоту -0813.

## НЕ проверено (live)

- Live acceptance `enable_thinking:false` для deepseek-v4-pro и фактический alias low→high — только на runtime probe lead'ом (`scripts/smoke_providers.py --strict`).
- Live-прогон probe по всем 10 моделям (стоимость/ключи — у lead).
- pytest полной suite — у lead.

## Blockers / предложения lead

1. **`app/services/generation.py::_consume`** (чужой scope): после фикса парсера `break` на `Done` корректен (Usage теперь до Done и доезжает). Опционально убрать `break` и дочитывать генератор — устойчивее, но не обязательно. **Решение за владельцем generation.py.**
2. `generation_runs` usage для alibaba ранее писал NULL (usage терялся из-за `break` на Done) — после фикса парсера данные появятся; backfill исторических NULL не требуется (unknown ≠ 0 по контракту §2).
3. A39 (текст ассистента в истории, max calls/round=4, deadline 240s, cancel-per-call) и A30/A14 (lifecycle клиентов, disabled-credential ≠ env fallback) — чужие scope, фикс-планы в `revalidate.md`; ждут владельцев.
4. `probe_required` для deepseek-v4-pro не выставлялся: OFF задокументирован (hybrid, `enable_thinking` применим); если lead хочет gated OFF до live-probe — добавить `probe_required=frozenset({"off"})` (одна строка, мой scope, по запросу).
