# polyra-qa-release — wave2: offline contract tests (stream / pool / usage ledger)

Дата: 2026-09-24/25. Исполнитель: polyra-qa-release (subagent). Среда: Windows,
CPython 3.14.7 (.venv), pytest 9.1.1 + pytest-asyncio 1.4.0 (asyncio_mode=auto),
httpx 0.28.1. База: HEAD `a704923` + незакоммиченные изменения wave-2 других
агентов (shared worktree, см. примечание ниже).

## Scope (выдан lead)

Только новые файлы: `tests/integration/**` (4 файла) + этот отчёт.
Существующие и production-файлы НЕ редактировались (git status: мой след —
только untracked `tests/integration/`).

Все тесты offline: HTTP — `httpx.MockTransport`, сторы — in-memory фейки,
БД нет, сети нет.

## Новые файлы

- `tests/integration/__init__.py` (пустой)
- `tests/integration/test_fix_v2_stream_contract.py` (12 тестов)
- `tests/integration/test_fix_v2_pool.py` (5 тестов)
- `tests/integration/test_fix_v2_usage_ledger.py` (7 тестов)
- `.agents/reports/fix-v2/polyra-qa-release/wave2.md` (этот файл)

## Покрытие контракта (`.agents/POLYRA_FIX_V2_CONTRACTS.md`)

### §1 Stream contract — test_fix_v2_stream_contract.py

Полный поток provider→consumer: MockTransport → SSE-парсер → события →
`GenerationService._consume` (фейковый streamer).

| Тест | Проверка |
|---|---|
| `test_alibaba_full_stream_usage_before_done_single_done` | text×2 → finish → usage-trailer (`choices: []`) → [DONE]: события `TextDelta*, Usage, Done`, Usage строго ДО Done, Done ровно 1; consumer: текст склеен, usage сохранён, finish_reason="stop" |
| `test_alibaba_eof_without_done_raises_network_error` | EOF без finish/[DONE] → `NetworkError("unexpected EOF")`; partial TextDelta дошёл до ошибки; retry нет (events_started), Done не фабрикуется |
| `test_alibaba_malformed_json_frame_skipped_stream_alive` | битый JSON-фрейм и не-data строка в середине пропускаются, поток жив до Done |
| `test_alibaba_fragmented_tool_args_single_tool_call` | args в 3 чанках → ровно один ToolCall с полным JSON `{"q": "polyra", "n": 1}`; порядок ToolCall, Usage, Done("tool_calls") |
| `test_alibaba_cancellation_before_iteration_yields_nothing` | cancellation.set() до итерации → `[]`, без ошибок |
| `test_gemini_thought_part_becomes_reasoning_delta_never_text` | part `thought=True` → ReasoningDelta (не TextDelta); consumer: мысль не в тексте/драфте, reasoning_chunks=1 |
| `test_gemini_function_call_with_thought_signature_tool_call` | functionCall + thoughtSignature → ToolCall с `provider_meta["thought_signature"]` |
| `test_gemini_usage_and_stop_same_chunk_usage_before_done` | usageMetadata + STOP одним чанком → Usage перед Done; consumer: usage сохранён |
| `test_gemini_prompt_feedback_block_reason_raises_safety` | promptFeedback.blockReason → SafetyError, событий до ошибки нет |
| `test_gemini_cancellation_before_iteration_yields_nothing` | cancellation.set() до итерации → `[]`, без ошибок |
| `test_done_emitted_exactly_once_per_stream` | оба провайдера: ровно один терминальный Done, последним событием |
| `test_gemini_eof_without_done_is_error_contract` | **XFAIL(strict)** — blocker B1, см. ниже |

### §5 Pool — test_fix_v2_pool.py

Фейки in-memory по стилю `tests/unit/test_gemini_pool.py` (FakeProjectStore с
журналом `calls`, FakeQuotaStore, сценарный FakeProvider).

| Тест | Проверка |
|---|---|
| `test_usage_trailer_survives_consumer_break_after_done` | потребитель читает до Done и break + `aclose()` (дисциплина `_consume`) → `mark_success` вызван ровно один раз, reconcile получил input_tokens=7 из usage-trailer |
| `test_429_cooldown_is_scoped_per_project_and_model` | 429 на модели A → `acquire(A)` None, `acquire(B)` тот же проект доступен; после истечения 60s A снова доступна; project-level `set_cooldown` НЕ трогается, `mark_error` записан |
| `test_server_error_bounded_retry_same_project_then_rotation` | ServerError → повтор ТОГО ЖЕ проекта (api_key p1 дважды) → ротация на p2 → успех; `retry_delay=0` для offline-скорости |
| `test_attempts_recorded_in_request_metadata` | попытки пишутся в `metadata["attempts"]`/`["attempt_ids"]` (["p1","p2"] + UUID) |
| `test_pool_exhausted_when_all_projects_fail` | оба проекта 429 → ровно один полный проход (2 вызова) → `PoolExhaustedError(model)`; mark_success пуст; для другой модели проекты доступны |

Примечание к первому тесту: при break+`aclose()` внешнего генератора finally
внутреннего `_stream_attempt` (там `report_success`) выполняет asyncgen-
finalizer event loop'а — тест ждёт его bounded-циклом `asyncio.sleep(0)`
(≤20 итераций). Механика опробована отдельно на CPython 3.14.7 под
pytest-asyncio: хуки `sys.set_asyncgen_hooks` установлены loop'ом, finalizer
отрабатывает за 2 итерации. Тот же механизм — в production под asyncio.run.

### §2 Usage ledger — test_fix_v2_usage_ledger.py

| Тест | Проверка |
|---|---|
| `test_sum_usage_adds_all_components` | (100/20/5/125)+(200/30/7/237) = 300/50/12/362 |
| `test_sum_usage_partial_none_components` | None-компонент = 0 в сумме при наличии значения у другого слагаемого |
| `test_sum_usage_none_plus_value` | Usage()+Usage(input=5) → только input=5, остальное None |
| `test_sum_usage_none_plus_none` | Usage()+Usage() → все поля None (unknown ≠ 0) |
| `test_stream_loop_sums_usage_across_tool_rounds` | раунд 1 tool_call (100/20/5) + раунд 2 text (200/30/7) → итог 300/50/12; реальный ToolRunner с echo-tool, llm_tools через make_llm_tools; ровно 2 LLM-вызова |
| `test_stream_loop_cancelled_run_keeps_known_usage` | отмена во время tool-раунда (handler ставит event) → `outcome.cancelled=True`, `outcome.usage == Usage(100/20/5/125)` — известный usage не теряется |
| `test_save_cancelled_persists_known_usage_to_run` | **XFAIL(strict)** — blocker B2, см. ниже |

## Реальный вывод pytest

Команда: `.venv\Scripts\python.exe -m pytest tests/integration -q`
(рабочий каталог O:\work\aibot). Прогнан 4 раза (в т.ч. после ruff-чистки) —
стабильно, без флаков. Финальный прогон:

```
................x......x                                                 [100%]
22 passed, 2 xfailed in 3.07s
```

exit code: 0. Дополнительно: `ruff check tests/integration` →
`All checks passed!` (exit 0).

Детальный листинг (-v, тот же прогон зелёный):

```
tests/integration/test_fix_v2_pool.py::test_usage_trailer_survives_consumer_break_after_done PASSED
tests/integration/test_fix_v2_pool.py::test_429_cooldown_is_scoped_per_project_and_model PASSED
tests/integration/test_fix_v2_pool.py::test_server_error_bounded_retry_same_project_then_rotation PASSED
tests/integration/test_fix_v2_pool.py::test_attempts_recorded_in_request_metadata PASSED
tests/integration/test_fix_v2_pool.py::test_pool_exhausted_when_all_projects_fail PASSED
tests/integration/test_fix_v2_stream_contract.py::test_alibaba_full_stream_usage_before_done_single_done PASSED
tests/integration/test_fix_v2_stream_contract.py::test_alibaba_eof_without_done_raises_network_error PASSED
tests/integration/test_fix_v2_stream_contract.py::test_alibaba_malformed_json_frame_skipped_stream_alive PASSED
tests/integration/test_fix_v2_stream_contract.py::test_alibaba_fragmented_tool_args_single_tool_call PASSED
tests/integration/test_fix_v2_stream_contract.py::test_alibaba_cancellation_before_iteration_yields_nothing PASSED
tests/integration/test_fix_v2_stream_contract.py::test_gemini_thought_part_becomes_reasoning_delta_never_text PASSED
tests/integration/test_fix_v2_stream_contract.py::test_gemini_function_call_with_thought_signature_tool_call PASSED
tests/integration/test_fix_v2_stream_contract.py::test_gemini_usage_and_stop_same_chunk_usage_before_done PASSED
tests/integration/test_fix_v2_stream_contract.py::test_gemini_prompt_feedback_block_reason_raises_safety PASSED
tests/integration/test_fix_v2_stream_contract.py::test_gemini_cancellation_before_iteration_yields_nothing PASSED
tests/integration/test_fix_v2_stream_contract.py::test_done_emitted_exactly_once_per_stream PASSED
tests/integration/test_fix_v2_stream_contract.py::test_gemini_eof_without_done_is_error_contract XFAIL
tests/integration/test_fix_v2_usage_ledger.py::test_sum_usage_adds_all_components PASSED
tests/integration/test_fix_v2_usage_ledger.py::test_sum_usage_partial_none_components PASSED
tests/integration/test_fix_v2_usage_ledger.py::test_sum_usage_none_plus_value PASSED
tests/integration/test_fix_v2_usage_ledger.py::test_sum_usage_none_plus_none PASSED
tests/integration/test_fix_v2_usage_ledger.py::test_stream_loop_sums_usage_across_tool_rounds PASSED
tests/integration/test_fix_v2_usage_ledger.py::test_stream_loop_cancelled_run_keeps_known_usage PASSED
tests/integration/test_fix_v2_usage_ledger.py::test_save_cancelled_persists_known_usage_to_run XFAIL
```

## Blockers (дефекты production-кода, НЕ исправлены мной — владение за lead)

Оба помечены `xfail(strict=True)`: когда дефект будет исправлен, тест даст
XPASS → strict-ошибку, что заставит снять маркер. XFAIL ≠ passed — это
зафиксированный несоответствующий контракту код.

### B1. Gemini: EOF без терминального Done — тихий «успех» вместо ошибки

- Файл: `app/llm/providers/gemini.py`, `parse_generate_content_sse`.
- Факт: при обрыве SSE без `finishReason`/`[DONE]` генератор просто
  завершается — ни Done, ни ошибки. Alibaba-парсер в той же ситуации бросает
  `NetworkError("unexpected EOF")` (`parse_chat_completions_sse`).
- Нарушение: FIX_V2 §1 «Отсутствие Done к концу потока = unexpected EOF →
  ошибка, а не успех». Последствие: `_consume` вернёт partial-текст как
  обычный outcome с `finish_reason=None`; при непустом partial пользователь
  получит обрезанный ответ как успешный (N03: «оборванный SSE», «partial →
  ошибка без дублирования»).
- Sentinel-тест: `test_gemini_eof_without_done_is_error_contract`.

### B2. `_save_cancelled` не сохраняет известный usage в generation_runs

- Файл: `app/services/generation.py`, `_save_cancelled`.
- Факт: в assistant message токены пишутся (`input_tokens=...` и т.д.), а в
  `GenerationRunRepository.finish()` для status="cancelled" передаются только
  `first_token_at`/`tool_calls_count` — usage не передаётся (в отличие от
  `_save_completed`, где передаётся). `finish()` такие параметры принимает.
- Нарушение: FIX_V2 §2 «Cancelled/failed runs тоже сохраняют известные usage;
  unknown ≠ 0 (NULL)»: у cancelled-запусков ledger останется NULL даже когда
  usage известен → per-user лимиты (`tokens_since`) и статистика
  занижаются.
- Sentinel-тест: `test_save_cancelled_persists_known_usage_to_run`
  (monkeypatch-фейки репозиториев; assert на kwargs finish()).
- Смежно (без отдельного теста): у `_save_failed` partial usage недоступен
  в принципе (`_stream_loop` возвращает None без накопленного usage_total) —
  если §2 требует usage и для failed, нужна передача накопленного usage в
  `_save_failed`. Решение за lead/владельцем файла.

## Примечания

1. **Worktree менялся в процессе.** Первичное чтение `app/llm/gemini/pool.py`
   дало wave-1 состояние (cooldown 429 project-wide, без bounded retry); к
   моменту прогонов wave-2 уже приземлил A10: in-memory cooldown per
   (project, model) (`_model_cooldowns`) и bounded retry того же проекта для
   server/network/timeout (`retry_delay`, до первого события). Мои pool-тесты
   §5 финально проверяют ИМЕННО текущее (контрактное) поведение — оно
   подтверждено зелёным прогоном. Аналогично `quota.py` обновлён до
   `QuotaReservation` — фейкам это совместимо.
2. Провайдерские unit-файлы (`test_alibaba_provider.py`, `test_gemini_pool.py`
   и др.) я не запускал как evidence — они вне моего scope; мои integration
   файлы самодостаточны.
3. Ограничение: offline-контракт не проверяет реальную сеть/БД; live-статусы
   моделей — вне этой волны (по ACCEPTANCE_ADDENDUM skipped/no-key ≠ passed).
