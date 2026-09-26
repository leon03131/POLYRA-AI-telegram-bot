# polyra-gemini — round4-P1: терминальная классификация немаппированных finishReason

**Дата:** 2026-09-26
**Субагент:** C — Gemini/pool/квоты (polyra-gemini)
**Task:** fix P1 из `.agents/reports/final-review/round4-polyra-gemini.md` (F1)
**Baseline:** HEAD `4925c40`, файлы scope чистые (чужие изменения commands.py/miniapp/** не тронуты).

---

## Scope (строго)

Writable scope по заданию lead — только два файла, оба изменены; git-операций не было.

| Файл | Изменение |
|---|---|
| `app/llm/providers/gemini.py` | `_SAFETY_FINISH_REASONS` расширен (RECITATION/LANGUAGE/IMAGE_*); +`_INVALID_REQUEST_FINISH_REASONS` (MALFORMED_FUNCTION_CALL); `_finish_reason_event` — терминальный fallback SafetyError + warning-лог + str()-коэрция против нехешируемых значений; докстринги (round4-P1) |
| `tests/unit/test_gemini_provider.py` | +20 тестов (6 функций, 2 параметризованные) — блок «round4-P1» |

`pool.py`, `errors.py`, `events.py`, `generation.py`, `smoke_providers.py` — только читал (read-only, чужие scope).

## Баг (round4-F1)

`_finish_reason_event` возвращал `[]` для значений вне `_FINISH_REASON_MAP`/`_SAFETY_FINISH_REASONS`
(RECITATION, LANGUAGE, OTHER, MALFORMED_FUNCTION_CALL, IMAGE_SAFETY, UNEXPECTED_TOOL_CALL, …)
→ стрим завершался без Done → `parse_generate_content_sse` поднимал
`NetworkError("unexpected EOF")` для ПОТНОК, штатно завершённого с finishReason → пул
делал bounded retry + ротацию + transient-cooldown 30с здорового проекта; пользователь
видел «сетевую» ошибку. Offline-репро аудита: `RECITATION -> NetworkError unexpected EOF`.

## Решение

Любое значение finishReason теперь даёт терминальный исход. Источник списка значений —
enum `FinishReason` google-genai SDK 2.24.0 (offline, `.venv`), 18 значений.

### Маппинг «finishReason → событие/ошибка»

| finishReason | Исход | Категория | Retry/Rot/Cooldown | Сообщение пользователю (generation.py) |
|---|---|---|---|---|
| `STOP` | `Done("stop")` | — | — (успех, report_success) | — |
| `MAX_TOKENS` | `Done("length")` | — | — (успех) | — |
| `SAFETY`, `BLOCKLIST`, `PROHIBITED_CONTENT`, `SPII` (как было) + `RECITATION`, `LANGUAGE`, `IMAGE_SAFETY`, `IMAGE_PROHIBITED_CONTENT`, `IMAGE_RECITATION` | `SafetyError(raw_code=<reason>)` | `safety` | нет/нет/нет (pool.py:296 — raise сразу) | «Запрос отклонён модерацией провайдера.» |
| `MALFORMED_FUNCTION_CALL` | `InvalidRequestError(raw_code=…)` | `invalid_request` | нет/нет/нет (raise сразу) | «Запрос некорректен для этой модели.» |
| Всё прочее: `OTHER`, `UNEXPECTED_TOOL_CALL`, `TOO_MANY_TOOL_CALLS`, `NO_IMAGE`, `IMAGE_OTHER`, `FINISH_REASON_UNSPECIFIED`, будущие/неизвестные строки | `SafetyError("gemini unmapped finishReason=…")` + `logger.warning` | `safety` (fallback) | нет/нет/нет (raise сразу) | «Запрос отклонён модерацией провайдера.» |

Инварианты (a)–(d) из задания:

- (a) НЕ NetworkError: `unexpected EOF` остаётся только для потока, реально оборвавшегося
  без finishReason (истинно сетевой случай, контракт FIX_V2 §1 сохранён).
- (b) не триггерит retry/ротацию: выбраны ровно те категории, которые pool.py
  `stream_with_failover` поднимает сразу без ротации — `SAFETY`/`INVALID_REQUEST`
  (pool.py:296-297); `_RETRY_SAME_PROJECT` = {SERVER, NETWORK, TIMEOUT} не задействован;
  `report_error` → только `mark_error` (без cooldown/disable — pool.py:244-246).
  `UNKNOWN`-категорию НЕ использовал: она ротирует проект (violates (b)).
- (c) осмысленное сообщение: `user_error_message` (generation.py:182-185) даёт фиксированные
  русские тексты для safety/invalid_request категорий.
- (d) детали в логе: finishReason внутри str(exc) (generation.py:690 логирует
  `generation failed: %s`; mark_error пишет в БД) + явный `logger.warning` в провайдере
  на fallback-ветке.

Прочие решения:

- `OTHER` → SafetyError-fallback консервативно по заданию («SafetyError-подобное поведение
  БЕЗ ротации»): единственная категория, дающая (a)+(b)+(c) одновременно без правки pool.py.
- Не-строковый finishReason из кривого прокси: `str()` до membership-проверки — иначе
  нехешируемое значение (`{"weird": 1}`) упало бы TypeError'ом вне классификации
  (в духе round4-F2, отдельной находки — она НЕ в этом фиксе).
- Частичные события до терминального finishReason доходят до потребителя; ошибка —
  строго в терминальной позиции (тест это ассертит).

## Тесты (новые, все реальные прогоны)

- `test_finish_reason_recitation_raises_safety_not_network` — репро round4-F1: RECITATION →
  SafetyError (safety, retryable=False, raw_code, reason в str(exc)).
- `test_finish_reason_malformed_function_call_invalid_request` — InvalidRequestError
  (invalid_request, retryable=False) — без ротации ключей.
- `test_finish_reason_safety_class_raises_safety` (9 параметров) — весь safety-класс.
- `test_finish_reason_unmapped_terminal_safety_fallback` (7 параметров: OTHER,
  UNEXPECTED_TOOL_CALL, TOO_MANY_TOOL_CALLS, NO_IMAGE, IMAGE_OTHER,
  FINISH_REASON_UNSPECIFIED, «SOME_FUTURE_REASON») — терминальная SafetyError, НЕ EOF.
- `test_unmapped_finish_reason_error_raised_at_terminal_position` — TextDelta до OTHER
  доставлен; ошибка в терминальной позиции.
- `test_non_string_finish_reason_classified_not_type_error` — нехешируемый finishReason
  → SafetyError, не TypeError.
- Инвариант «пул НЕ делает retry»: на уровне провайдера — ассерты
  `category not in {SERVER, NETWORK, TIMEOUT}` (retry-набор pool.py) и
  `category == SAFETY|INVALID_REQUEST` (no-rotate набор pool.py:296), как задано lead'ом.

## Прогоны (реальные, сегодня, exit codes)

| Команда | Результат | Exit |
|---|---|---|
| `.venv\Scripts\python -m pytest tests/unit/test_gemini_provider.py tests/unit/test_gemini_pool.py -q` | `82 passed in 0.22s` (26+36 было → 46+36: +20 provider-тестов) | **0** |
| `.venv\Scripts\python -m ruff check app/llm/providers/gemini.py tests/unit/test_gemini_provider.py` | `All checks passed!` (после исправления 1×E501) | **0** |
| `.venv\Scripts\python -m mypy app` | `Success: no issues found in 129 source files` | **0** |
| смежные (sanity, сверх задания): `pytest tests/unit/test_generation.py tests/integration/test_fix_v2_pool.py tests/integration/test_fix_v2_stream_contract.py -q` | `49 passed in 2.92s` | **0** |
| офлайн-репро round4 (MockTransport, сеть не задействована; скрипт удалён после прогона) | `RECITATION -> SafetyError / category: safety / retryable: False`; `MALFORMED_FUNCTION_CALL -> InvalidRequestError / invalid_request`; `OTHER/UNEXPECTED_TOOL_CALL/SOME_FUTURE_REASON -> SafetyError (unmapped) + warning-лог` | 0 |

## Блокеры / передачи другим агентам

- Нет блокеров. Пул/ротация не менялись — классификация даёт нужное поведение через
  существующие категории pool.py.
- Примечание для lead: (1) текст `user_error_message` для InvalidRequestError —
  «Запрос некорректен для этой модели.» — семантически неточен для
  MALFORMED_FUNCTION_CALL (сломан call модели, не запрос пользователя); если захочется
  честнее, правка в generation.py (владелец другого scope); (2) round4-F2
  (не-dict/не-UTF8 error-тело в `_error_from_response`) остаётся открытым — файл
  gemini.py в моём scope, могу взять следующим task'ом.
