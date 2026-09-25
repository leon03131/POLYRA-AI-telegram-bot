# Final review — polyra-gemini (Gemini / pool / квоты)

Аудитор: polyra-gemini (read-only аудит).
Дата: 2026-09-25. Checkout: `O:\work\aibot`, HEAD `e4ac383` (clean, untracked — только этот отчёт).
Scope: `app/llm/gemini/**`, `app/llm/providers/gemini.py`, `app/llm/base.py` (LLMEvent), связанные части `app/services/generation.py`, `app/services/llm_factory.py`, `app/db/repositories/gemini.py`, `tests/unit/test_gemini_pool.py`, `tests/unit/test_gemini_provider.py`, `tests/integration/test_fix_v2_*.py`, `scripts/smoke_providers.py` (gemini-часть).

Статусы: FIXED_VERIFIED / FIXED_PARTIAL / NOT_FIXED / TEST_ONLY / CANNOT_VERIFY.

---

## Выполненные проверки (реальные запуски)

1. `.venv\Scripts\python -m pytest tests/unit/test_gemini_pool.py tests/unit/test_gemini_provider.py tests/integration/test_fix_v2_pool.py tests/integration/test_fix_v2_stream_contract.py -q` → **79 passed in 2.93s, exit 0**.
2. `.venv\Scripts\python -m pytest tests/integration/test_fix_v2_usage_ledger.py tests/unit/test_generation.py -q` → **39 passed in 2.97s, exit 0**.
3. Весь набор: `.venv\Scripts\python -m pytest tests -q` → **488 passed in 7.71s, exit 0** (offline, без сети/БД/live-ключей).
4. Дополнительные эмпирические offline-пробы (сценарии, которых нет в репо-тестах; httpx.MockTransport, сети нет; скрипты во временной папке вне проекта):
   - **break-on-Done через реальный GeminiProvider + пул** (продакшн-цепочка `top gen → pool.stream_with_failover → _stream_attempt → provider.stream_chat → SSE-парсер → httpx`): после break потребителя по Done и `aclose` верхнего генератора — `mark_success` ровно 1 раз, reconcile `input_tokens=7` ровно 1 раз, генератор провайдера финализирован (HTTP-контекст закрыт). Тот же результат для стиля `collect_text` (без явного aclose, генератор умирает по refcount при выходе из функции).
   - **Висящий SSE + Stop** (`HangingStream`, чтение никогда не заканчивается; `cancellation.set()` + `task.cancel()`, как `GenerationRegistry.stop`): CancelledError через **0.0001 s** (не read-timeout 300s), провайдер финализирован. **PASS**.
   - Попутно обнаружено: `contextlib.suppress(Exception)` в `pool.py:344` молча проглотил ошибку в `report_success` в моей первой пробе (сломанный фейк дал TypeError) — в проде ошибки БД в report_success/reconcile теряются без лога (см. A06).

---

## A06 (gemini-часть) — FIXED_VERIFIED (с оговорками)

Контракт: Done ровно один раз ПОСЛЕ usage; пул финализируется; aclose/finally закрывает все уровни итератора и HTTP response.

**Доказательства:**

- Порядок событий в финальном чанке: `usageMetadata` → parts → finishReason (`app/llm/providers/gemini.py:207-226`, `_events_from_chunk`): Usage эмитится раньше Done. Тест: `tests/integration/test_fix_v2_stream_contract.py:350-382` (`test_gemini_usage_and_stop_same_chunk_usage_before_done`, `usage_idx < done_idx`), `tests/unit/test_gemini_provider.py:113-127`.
- Потребитель ломается на Done, но `finally` всегда закрывает стрим: `app/services/generation.py:841-864` — `_consume` в finally вызывает `aclose()` (контракт FIX_V2 §1, комментарий в коде).
- Финализация пула: `app/llm/gemini/pool.py:318-351` — `_stream_attempt` в `finally` при `terminal_seen` делает ровно один `report_success(..., reservation=cred.quota_reservation, input_tokens=last_usage.input_tokens if last_usage else None)`. Ровно один раз на попытку; попытки без Done (ошибка/cancel) не репортят успех.
- «После последнего yield» side effects не теряются: потребительский aclose + asyncgen-finalizers event loop'а закрывают всю цепочку генераторов; проверено эмпирически на реальном провайдере (проба 1): `mark_success` ×1, reconcile ×1, HTTP-контекст закрыт. И для `_consume`-стиля (aclose), и для `collect_text`-стиля (compactor/titles/extractor: `app/context/compactor.py:163-174` — break на Done без aclose; refcount-drop при выходе из функции запускает ту же цепочку финализаторов).
- Тесты: `tests/integration/test_fix_v2_pool.py:199-221` (`test_usage_trailer_survives_consumer_break_after_done`) — с честным комментарием про `_drain_loop` (finalizer'ам нужны итерации цикла); `tests/unit/test_gemini_pool.py:513-523` (reconcile input-токенов); `test_429_rotates_to_next_project_and_reports_success` (mark_success на успешной ротации).

**Оговорки (не ломают контракт, но важны в проде):**

1. Финализация **отложенная**: report_success/reconcile выполняются асинхронно event-loop finalizer'ами ПОСЛЕ того, как потребитель ушёл (возможно уже после `_save_completed`). В долгоживущем цикле бота это завершается за миллисекунды; при падении процесса до запуска finalizer'а reservation остаётся с requests_count без токенов (консервативно), а mark_success теряется.
2. `pool.py:343-345`: `with contextlib.suppress(Exception):` вокруг `report_success` — ошибки БД (недоступность, конфликт) глотаются молча, без лога. Наблюдено эмпирически.
3. Usage, приходящий ПОСЛЕ Done (в отдельном чанке после finishReason), был бы потерян — но Gemini по vendor-докам шлёт usageMetadata в том же финальном чанке, что и finishReason; для Alibaba потребовалась бы буферизация Done (реализована у D, вне моего scope).
4. `parse_generate_content_sse` после первого Done продолжает читать строки и может эмитить второй Done при повторном finishReason в потоке (не встречается у Google). Мелочь.
5. `QuotaMinuteUsage`/`QuotaDailyUsage` растут без очистки/TTL (строка на (project, model, minute)); ручной admin reset-counters есть (`app/api/routes/admin_gemini.py:359-368`).

**Паттерн «фикс проходит тест, но ломается в проде»:** не найден для основного пути; но интеграционный тест пула сам признаёт зависимость от `_drain_loop` (итерации цикла под finalizer'ы) — я подтвердил поведение на реальном провайдере, включая вариант без aclose.

---

## A09 — FIXED_PARTIAL

Требование: единая транзакция check-and-reserve, reservation_id, idempotent reconcile по reservation, bounded token reservation, освобождение при cancel.

**Сделано (код):**

- Атомарный check-and-reserve: `app/llm/gemini/store_db.py:125-225` — `check_and_reserve_atomic`, ОДНА сессия/транзакция на оба окна; каждое окно — `INSERT ... ON CONFLICT DO UPDATE ... WHERE <лимиты> ... RETURNING id`; отказ любого окна → `session.rollback()` обоих инкрементов. Имена констрейнтов (`uq_quota_minute_usage_project_id`, `uq_quota_daily_usage_project_id`) совпадают с миграцией 0003 (строки 87-91, 111-115) и naming convention `app/db/base.py`.
- SQL-семантика корректна для PostgreSQL READ COMMITTED: конкурентные INSERT конфликта сериализуются row-lock'ом ON CONFLICT, WHERE переоценивается на закоммиченной строке; RETURNING пуст → отказ. Порядок блокировок minute→daily однообразен (дедлока нет). Лимиты `<= 0` отсечены заранее (INSERT свежего окна обходит WHERE).
- Окна резервации: `QuotaReservation` (`app/llm/gemini/quota.py:54-65`) несёт `minute_ts`/`day` МОМЕНТА резервации; `reconcile` (`quota.py:166-180`) пишет строго в эти окна; пул протягивает reservation: `pool.py:160-167` (acquire) → `pool.py:343-351` (report_success). Граница минуты/суток между reserve и reconcile не уводит токены: `tests/unit/test_gemini_pool.py:763-802` (два теста, включая сквозной с `now_fn` через границу минуты).
- `QuotaTracker.check_and_reserve` использует atomic-store через `getattr` (`quota.py:144-150`), legacy read+reserve остаётся fallback'ом.
- Дневные окна — Pacific day (`quota.py:32-34`), минутные — UTC-минута: тесты `test_pacific_day_uses_los_angeles_date`, `test_current_minute_truncates_to_utc_minute` (DST-граница 23:59→00:00 PDT покрыта в `test_pacific_day...`).

** НЕ сделано / риски:**

1. **Нет проверки на реальном PostgreSQL.** Все тесты — in-memory фейки (`AtomicFakeQuotaStore` в `tests/unit/test_gemini_pool.py:155-188`; `tests/integration/test_fix_v2_pool.py` — «Offline: фейки in-memory, сети и БД нет»). Атомарность фейка тривиальна («без suspension-точек» — комментарий `test_gemini_pool.py:156-158`); реальная гонка защищена только SQL-семантикой PG, живого barrier-теста нет. `FIX_REPORT_V2.md:91-93` честно признаёт: «локально PostgreSQL недоступен... формальный barrier-тест не прогнан». Приёмка A09 требовала PostgreSQL-тесты. → главный residual risk.
2. **Нет персистентного reservation_id / идемпотентности reconcile на уровне хранилища**: `quota.py:168-173` — «Store повторный вызов не дедуплицирует — дубль задвоит tokens_in... повторный reconcile запрещён контрактом». Идемпотентность только «по вызову» (пул вызывает один раз). Сбой/рестарт между reserve и reconcile не задвоит (reconcile просто не случится), но любой повторный вызов — задвоит.
3. **Нет освобождения reservation при cancel/ошибке**: `grep release|reservation` по app/ — метода release нет вообще. Отменённый запрос оставляет +1 requests_count навсегда (консервативно, но RPD/RPM «протекают» от отмен; при частых Stop'ах окно RPD=19 может быть исчерпано отменёнными запросами). Аудит явно требовал «освобождение/сверку при отмене».
4. **Bounded token reservation не реализован** (резервируется только requests_count; TPM проверяется по уже учтённым tokens_in, допуск сверх TPM после факта не блокируется — задокументировано в `store_db.py:144-148`). Прим.: исходное ТЗ (docs/PROMT.md:566 «optional token reservation») допускало отсутствие, аудит A09 требовал — считаю отклонением от аудита, но соответствием ТЗ.
5. Ретрай того же проекта (A10) не делает повторный reserve — фактических HTTP-запросов может быть на 1 больше, чем requests_count (bounded, осознанно).
6. In-memory `_model_cooldowns` и `_rr_counter` — process-local (multi-worker не разделяет; задокументировано в wave2.md:85).

Статус: код-уровень атомарности и окна резервации — корректный и покрытый offline-тестами; идемпотентность/освобождение отсутствуют; реальной PG-верификации нет. **FIXED_PARTIAL**.

---

## A10 — FIXED_PARTIAL

- **429 → cooldown (project, model), не весь проект**: `pool.py:235-238` — in-memory `self._model_cooldowns[(project_id, model_id)] = now + retry_after|60`; БД `cooldown_until` для 429 не трогается; `acquire` фильтрует оба уровня (`pool.py:144-152, 173-181`, ленивое протухание). Тесты: `test_429_cooldown_is_scoped_to_model` (модель B доступна), `test_429_retry_after_overrides_default_cooldown`, `test_model_cooldown_expires_lazily`, интеграционный `test_429_cooldown_is_scoped_per_project_and_model`. ✓
- **401 → disable ключ**: `pool.py:221-222` (`mark_unhealthy` → set_enabled(False) + health unhealthy, `store_db.py:51-60`). ТЗ разрешает («disable или mark unhealthy»). ✓
- **PERMISSION_DENIED → авто-отключение навсегда — ОТКАОНЕНИЕ от аудита и ТЗ**: `pool.py:224-230` — любой 403 c raw_code=PERMISSION_DENIED без разбора сообщения тут же `mark_unhealthy` (disable). Аудит A10: «PERMISSION_DENIED не должен автоматически навсегда disable credential»; исходное ТЗ (docs/PROMT.md:599-601): «403 ... поставить cooldown; попробовать следующий project». Классификации «по документированным признакам» (анализ сообщения, напр. только «project denied access») нет. Транзиентные PERMISSION_DENIED (IAM/локация/API не включён) навсегда выключают проект до ручного re-enable. Это осознанное решение (комментарий в коде, тест `test_403_permission_denied_disables_project` закрепляет его), но оно противоречит обоим документам. **Это главный A10-residual.**
- Прочие 403 → cooldown 300s + mark_error, проект остаётся enabled (`pool.py:231-234`, тест `test_403_generic_gets_cooldown_not_disable`). ✓
- **400/safety → не перебирать пул**: `pool.py:296-297` — INVALID_REQUEST/SAFETY сразу `raise` (после report_error без cooldown); тесты `test_400_invalid_request_no_rotation`, `test_safety_no_rotation` (ровно один call провайдера). ✓
- **5xx/network/timeout → bounded retry того же проекта + переход**: `pool.py:40-43, 272-294` — один повтор (`retried`), только до первого события; затем ротация; transient cooldown 30s по финальной ошибке. Тесты: `test_5xx_cooldown_30s_and_next_project` (p1→p1→p2), `test_transient_error_retried_once_same_project_then_succeeds`, `test_network_error_also_retried_once`, `test_transient_retry_happens_at_most_once_per_project`, `test_transient_retry_uses_configured_delay`, `test_retry_failure_with_new_category_uses_final_error`, `test_error_after_first_event_not_rotated`. **Нет jitter** (требование «bounded retry с jitter»; `asyncio.sleep(retry_delay)` c фиксированным 0.5s). Нет собственного deadline прохода пула (общий deadline 240s есть на уровне `_stream_loop` — `generation.py:618-622`; одна попытка может висеть до read-timeout 300s).
- **Один проход пула**: `tried` + `acquire(exclude=tried)` → `PoolExhaustedError` (`pool.py:263-267`); тесты `test_pool_exhausted_raises`, `test_pool_exhausted_when_all_projects_fail` (ровно N вызовов). ✓
- **model_id никогда не меняется**: `dataclasses.replace(request, metadata={...})` — только метаданные; `request.model` константен на всех ротациях/ретраях (`pool.py:269-271`). Cross-model fallback отсутствует (раздел 13 ✓).
- CancelledError — проброс без report_error (`pool.py:280-281`), тест `test_cancelled_error_propagates_without_report`. ✓

**Итог: FIXED_PARTIAL** — всё, кроме PERMISSION_DENIED (перманентный disable вопреки требованию) и отсутствия jitter.

---

## A11 (gemini-часть) — FIXED_VERIFIED

- `GenerationRegistry.stop` = `cancellation.set()` + `task.cancel()` (`app/services/generation.py:121-131`): task.cancel прерывает зависшее HTTP-чтение немедленно. Тесты: `tests/unit/test_generation.py:70-91`, `tests/unit/test_bot_wiring.py:64-75`.
- Adapter проверяет cancellation: `app/llm/providers/gemini.py:343-350` (`_lines_with_cancellation` — проверка на каждой строке); тихое завершение без Done при отмене (`gemini.py:333-337`: NetworkError при `cancellation.is_set()` → return). Тесты: `test_cancellation_stops_without_done` (unit), `test_gemini_cancellation_before_iteration_yields_nothing` (integration).
- **Эмпирическая проба (моя)**: висящий навсегда SSE-поток + `cancellation.set()+task.cancel()` → CancelledError за **0.0001 s** (не 300 s), генератор провайдера финализирован, HTTP закрыт. Механизм работает на production-пути.
- `_consume` глотает CancelledError → partial сохраняется, run → cancelled (`generation.py:856-866`), тест `test_consume_cancelled_error_returns_partial`.
- Отмена перед каждым side effect в tool batch: `generation.py:711-715` (A11/A39), тест `test_stream_loop_cancelled_run_keeps_known_usage` (integration, отменa во время tool).
- Дыр в покрытии: нет автоматического теста «висящее чтение прерывается за ≤2s» в репо (моя проба закрывает пробел вручную); тесты registry проверяют механику, не тайминги на реальном транспорте.

Статус: **FIXED_VERIFIED** (механизм + эмпирика; в репо-тестах — только предустановленная отмена, без hang-сценария на транспорте).

---

## A23 (gemini-часть) — FIXED_PARTIAL

- **Terminal Done обязателен**: `parse_generate_content_sse` (`gemini.py:229-256`) — EOF без finishReason/[DONE] → `NetworkError("unexpected EOF: gemini stream ended without finishReason")`. Оборванный поток НЕ принимается как успешный ответ. Тесты: `tests/integration/test_fix_v2_stream_contract.py:428-436` (`test_gemini_eof_without_done_is_error_contract`, бывший xfail-blocker), unit `test_fix...` в provider-наборе нет отдельного, но EOF покрыт интеграционным; `test_done_sentinel_stops_stream` ([DONE]).
- Safety-финишы (SAFETY/BLOCKLIST/...) и promptFeedback.blockReason → SafetyError (`gemini.py:189-204`), тесты ✓.
- Cancel → тихое завершение без Done, не «successful» (см. A11) ✓.
- **Malformed payload НЕ классифицируется**: битые JSON-строки `data:` молча пропускаются с debug-логом (`gemini.py:246-249`); не-словарные JSON (`data: [1,2]`) — тоже. Если после битых фреймов приходит валидный Done — поток считается успешным (потерянные фреймы не детектируются). Gemini-специфичного теста malformed-фрейма нет (единственный malformed-тест в интеграции — Alibaba, и он фиксирует skip-and-continue как желаемое поведение: `test_fix_v2_stream_contract.py:220-237`).
- Структурная валидация чанков отсутствует: `candidates` не-list / `content` не-dict → сырой TypeError/AttributeError пробросится наверх как внутренняя ошибка (fail, не тихий успех — приемлемо, но не классифицировано как malformed_response).
- Неизвестный finishReason (напр. RECITATION) не маппится → нет Done → EOF-ошибка (легитимное завершение классифицируется как network-ошибка; безопасно, но неточно).
- Error-фрейм в 200-SSE (`{"error": ...}`) игнорируется парсером; если Done после него нет → EOF-ошибка (не принят как успех) ✓.

Статус: **FIXED_PARTIAL** — state machine с обязательным терминальным Done и unexpected-EOF есть; классификация malformed-фреймов отсутствует (пропуск молча).

---

## A27 (gemini-часть) — FIXED_PARTIAL

- **Перебор ВСЕХ моделей из registry**: `scripts/smoke_providers.py:85-87, 472-474` — `_registry_models(registry, "gemini")` = gemini-3.8-flash, gemini-3.7-flash, gemini-3.6-flash, gemini-3.5-flash-lite (internal — помечается в notes, `smoke_providers.py:370`). Новые модели попадают автоматически (hardcoded-списков нет). ✓
- **Exit code --strict**: `smoke_providers.py:615-720` — `--strict` → exit 1 при любом check fail; 0 — ok; 130 — KeyboardInterrupt; skipped за fail НЕ считается. No-key/skip — отдельный статус «skipped» (≠ ok ≠ pass) ✓.
- **Реальные вызовы выключены по умолчанию**: запуск только вручную (docstring «Запуск ТОЛЬКО ВРУЧНУЮ», не в CI/старте приложения); ключи не в логах/JSON (project label с `***key_hint`, alibaba `mask_secret`). ✓
- Thinking-check больше не может вернуть «accepted» без Done: парсер поднимает NetworkError на EOF → `_run_check` пометит fail. TextDelta для acceptance не требуется (осознанно; отдельный `text_stream`-check требует TextDelta+Done+Usage). Частично закрывает старую дыру.
- **НЕ реализовано: «результаты кешируются с TTL и влияют на runtime registry/API/UI».** Поиск по app/: никакого probe-cache/TTL нет; результаты пишутся только в `.agents/reports/probe_*.json`; runtime-механизм влияния — единственный статический `probe_required` в `app/llm/capabilities.py:45` + фильтр в `app/api/routes/me.py:45-58` (DB-override этих режимов не меняется прогонами probe). Recommendations печатаются в stdout для Alibaba.
- Live-прогон gemini в актуальных отчётах отсутствует: `.agents/reports/probe_20260924_190515.json` — `"gemini": {"status": "skipped", "error": "не выбран (--provider)"}` (alibaba-only). Более ранние probe JSON — gemini skipped тоже. CANNOT_VERIFY live-часть (запрещено мне live-пробами).

Статус: **FIXED_PARTIAL** (перечисление/strict/ручной режим/no-key≠pass — да; TTL-кеш и влияние на runtime — нет; live — не проверено).

---

## A30 (gemini-часть) — FIXED_VERIFIED

- Один shared Gemini-клиент, lifecycle приложения: `app/services/llm_factory.py:48` (`GeminiProvider(base_url=settings.gemini_base_url)` — провайдер владеет клиентом, `_owns_client=True`, `gemini.py:298-309`), `aclose()` закрывает ровно один раз (идемпотентный `self._closed`, `llm_factory.py:92-100`).
- `app/main.py` finally: `await llm_stream.aclose()` → `search_manager.aclose()` → `bot.session.close()` → `engine.dispose()` (закрытие всех уровней на shutdown). ✓
- «Cached clients с eviction»: для Gemini кеша нет (один клиент — нечему evict'ить); Alibaba — кеш по api_key с закрытием устаревших при ротации (`llm_factory.py:71-90`). Для Gemini-пула ключи меняются per-request через `request.metadata["api_key"]` (ADR-015) — клиент ключенезависим, ротация ключей не требует пересоздания клиента. Дизайн корректен.
- Замечание (за рамками gemini): `await asyncio.gather(dp.start_polling(bot), uvicorn_server.serve())` без coordinated-cancel — при падении одной задачи aclose выполнится в finally, но вторая задача не будет корректно погашена (зона A34/A37, lead).

Статус: **FIXED_VERIFIED**.

---

## A39 (gemini-часть) — FIXED_VERIFIED

- Parse thoughtSignature → `ToolCall.provider_meta["thought_signature"]`: `gemini.py:169-181`. Тесты: `tests/unit/test_gemini_provider.py:84-110`, интеграционный `test_fix_v2_stream_contract.py:319-347`.
- Возврат в следующий request при tool rounds: `gemini.py:59-70` (`_message_to_content`: `wire_part["thoughtSignature"] = signature` для assistant tool_call-part). Тест: `test_payload_assistant_tool_call_returns_thought_signature` (`tests/unit/test_gemini_provider.py:223-267`) — payload содержит `{"functionCall": ..., "thoughtSignature": "sig123"}`.
- Полный assistant turn (text+calls+provider_meta) сохраняется в истории раунда: `app/services/generation.py:759-777` (`_assistant_tool_message(tool_calls, round_text)` — текст раунда + вызовы с provider_meta); вызов с текстом раунда: `generation.py:709`.
- Reasoning не показывается: `ReasoningDelta` → только счётчик в `_consume` (`generation.py:294-295`), тесты `test_gemini_thought_part_becomes_reasoning_delta_never_text` (integration) — мысль не в драфт/текст.
- Лимиты tool-loop: `max_tool_calls_per_round=4` (`config.py`, `generation.py:711`), общий deadline `max_generation_seconds=240` (`generation.py:618-622`), `max_tool_iterations=8`.
- Оговорка: в DB-историю tool-parts не пишутся вовсе (`build_messages` берёт из истории только text-parts, `generation.py:309-328`; `_save_completed` пишет tool_call/text без provider_meta) — signatures живут только в рамках текущей генерации (tool rounds). Для требования «передаются в следующий request при tool rounds» — достаточно; сквозь рестарты история tool-вызовов всё равно не реконструируется (текст-only контекст) — потеря signatures не создаёт некорректных запросов.

Статус: **FIXED_VERIFIED** (в рамках требования «не сломать signatures при tool rounds»).

---

## Исходное ТЗ (docs/PROMT.md)

### Раздел 8 (thinking, custom proxy) — FIXED

- Thinking-уровни: `app/llm/capabilities.py:76-110` — gemini-3.8-flash / 3.7-flash: (low, medium, high); gemini-3.6-flash: (minimal, low, medium, high) — ровно как ТЗ (PROMT.md:483-499). Wire: `thinkingConfig.thinkingLevel` (`gemini.py:134-138`), legacy `thinking_budget` не используется ✓ (PROMT.md:503). MINIMAL для 3.6 ✓.
- Custom proxy: `app/config.py` — `gemini_base_url: str = "https://extraordinary-piroshki-4e3b92.netlify.app"` (default); провайдер получает его через `ManagedLLMStream` (`llm_factory.py:48`) и шлёт `POST {base_url}/v1beta/models/{model}:streamGenerateContent?alt=sse` (`gemini.py:319-323`) — прямой Google endpoint не используется. Raw httpx adapter (HttpOptions/SDK не применён — ТЗ разрешало raw adapter при проблемах SDK) ✓; `trust_env=False` ✓ (`gemini.py:302`).
- «Создать integration smoke test» (PROMT.md:522): автоматизированного proxy smoke-теста нет — есть только ручной `scripts/smoke_providers.py`; последний live-отчёт gemini «skipped». **ЧАСТИЧНО** (manual-инструмент есть, автоматического/прогнанного нет).

### Раздел 9 (pool ~30 keys, rotation, health, cooldown, классификация, один проход, квоты через admin) — FIXED (кроме 403-отклонения)

- Pool: `gemini_projects` (encrypted keys, rotation_order, enabled, health, cooldown, last_error) + round-robin (`pool.py:135-171`) ✓; health/cooldown/last_error через `ProjectStore` → `DbProjectStore` → `GeminiProjectRepository` ✓.
- Классификация: 400/401/403/429/5xx/timeout/safety — `pool.py:209-246` + `app/llm/errors.py:97-113` (см. A10; 403-PERMISSION_DENIED — отклонение).
- Один проход + user-facing сообщение: `PoolExhaustedError` → `user_error_message` («Все Gemini projects для модели ... сейчас недоступны. Попробуйте позже.») — `generation.py:171-175`; тест `test_user_error_message_pool_exhausted_includes_model`. ✓
- Квоты RPM=4/TPM=249999/RPD=19: НЕ hardcoded — seed в миграции `app/db/migrations/versions/0004_credentials_quotas.py:24-28` (ON CONFLICT DO NOTHING, редактируемы), в app-коде не встречаются (grep 249999 — только миграция). Редактирование через admin Mini App: `PUT /api/admin/gemini/quotas` → `QuotaPolicyRepository.upsert` (`app/api/routes/admin_gemini.py:303-332`), с audit entry. Лимиты читаются на каждый acquire (`store_db.py:236-247`) — изменения вступают в силу без рестарта. ✓

### Раздел 10 (internal 3.5-flash-lite) — FIXED

- `internal_only=True` для gemini-3.5-flash-lite (`capabilities.py:113-124`); не показывается в UI (`registry.list_user_models`, `me.py`).
- Тот же pool: `ManagedLLMStream._stream` маршрутизирует по `provider=="gemini"` независимо от internal-флага (`llm_factory.py:57-63`) — compactor/titles/memory extractor идут через тот же пул ✓.
- Отдельные quota policies по model_id: `QuotaPolicy.model_id` unique; политика для internal-модели не засеяна → unlimited local accounting — в точности по ТЗ (PROMT.md:658-661 «Если quota limits неизвестны — НЕ придумывать их»). ✓
- Configurable thinking для summary/extraction/title: `config.py` `summary_thinking="medium"`, `memory_thinking="medium"`, `title_thinking="low"` — значения совпадают с ТЗ (PROMT.md:644-651), configurable через env; НЕ редактируются через admin DB (system_settings их не включает — A13-зона). Мелкое несоответствие «все значения должны быть configurable» — env-only.

### Раздел 13 (никакого cross-model fallback) — FIXED_VERIFIED

- Пул ротирует только проекты ТОЙ ЖЕ модели; `request.model` неизменен (см. A10). `PoolExhaustedError` не переключает модель.
- Неизвестная/отключённая сохранённая модель → явный отказ пользователю без fallback (`generation.py:956-965`), disabled → отказ (`_model_denial`, `generation.py:468-474`). Остаток: `resolve_model_and_thinking` (`generation.py:202-207`) формально содержит fallback unknown→default_model, но в прод-пути unknown отсекается раньше в `_prepare`; unit-тест `test_resolve_unknown_model_falls_back_to_default` закрепляет легаси-поведение резолвера (не генерации).

---

## Cross-boundary наблюдения (владелец — lead, вне моего writable scope)

1. **attempts/attempt_ids собираются, но не персистятся**: пул пишет их в `request.metadata` (`pool.py:308-316`), `_stream_loop` возвращает (`generation.py:616-631, 683`), но `_save_completed/_save_cancelled/_save_failed` не передают их в `GenerationRunRepository.finish` (параметров нет, `generation_runs.py:31-67`), и колонки `generation_runs.attempts`/`gemini_project_id` (`app/db/models/generation_run.py:52-53`, миграция 0009) никогда не заполняются. A07 «Хранить project selection/attempts» — собрано, но не сохранено.
2. `contextlib.suppress(Exception)` в `pool.py:344` — молчаливое глотание ошибок report_success/reconcile (см. A06-оговорка 2).
3. `.agents/reports/fix-v2/polyra-gemini/` содержит только wave2.md — отчёты по A06/A11/A23/A27/A30/A39 gemini-части лежат в lead/QA-файлах; ссылка из `store_db.py:148` на wave2.md существует — ок.

---

## Сводная таблица

| Пункт | Статус | Ключевое доказательство | Ключевые остатки |
|---|---|---|---|
| A06 gemini | FIXED_VERIFIED | pool.py:318-351 finally; generation.py:841-864 aclose; интеграция test_fix_v2_pool.py:199-221; моя offline-проба на реальном провайдере (mark_success×1, reconcile×1, HTTP closed) | Финализация отложена (asyncgen finalizers); suppress(Exception) без лога; usage-после-Done не поддержан (для Gemini неактуально) |
| A09 | FIXED_PARTIAL | store_db.py:125-225 atomic ON CONFLICT+WHERE+RETURNING; quota.py:54-65,166-180 reservation-окна; тесты минутной границы | Нет PG-теста (признано FIX_REPORT_V2.md:91-93); нет idempotent reconcile в store; нет release при cancel; нет token reservation |
| A10 | FIXED_PARTIAL | pool.py:235-238 (429 per project+model), 272-294 (bounded retry), 296-297 (400/safety), 263-267 (один проход); 14 тестов | PERMISSION_DENIED → перманентный disable вопреки аудиту/ТЗ; нет jitter; нет deadline пула |
| A11 gemini | FIXED_VERIFIED | generation.py:121-131 (event+task.cancel); gemini.py:343-350 (per-line cancel); моя hang-проба: отмена за 0.0001s | В репо нет hang-теста на транспорте (только предустановленная отмена) |
| A23 gemini | FIXED_PARTIAL | gemini.py:229-256 (EOF без Done → NetworkError); интеграция test_fix_v2_stream_contract.py:428-436 | Malformed JSON-фреймы пропускаются молча; нет структурной валидации; нет gemini-malformed-теста |
| A27 gemini | FIXED_PARTIAL | smoke_providers.py:85-87,472-474 (все модели из registry, internal помечен); :615-720 (--strict exit codes) | TTL-кеш probe-результатов и влияние на runtime registry/API/UI отсутствуют; live gemini-probe не прогнан (последний JSON — skipped) |
| A30 gemini | FIXED_VERIFIED | llm_factory.py:33-100 (shared client, идемпотентный aclose); main.py finally | — (координация shutdown gather — зона A34/A37) |
| A39 gemini | FIXED_VERIFIED | gemini.py:67-69,169-181 (thoughtSignature roundtrip); generation.py:759-777 (полный assistant turn); тесты provider:223-267 | DB-история не хранит tool-parts (signatures только в рамках tool rounds — достаточно для требования) |
| ТЗ §8 | FIXED | capabilities.py:76-110 (уровни thinking); config.py gemini_base_url=netlify; gemini.py:319-323 (URL через proxy) | Автоматического proxy smoke-теста нет (только ручной скрипт) |
| ТЗ §9 | FIXED | pool.py + errors.py классификация; seed 0004 + admin PUT /quotas; PoolExhausted → user_error_message | 403-PERMISSION_DENIED отклоняется от ТЗ (disable vs cooldown) |
| ТЗ §10 | FIXED | capabilities.py internal_only; llm_factory.py:57-63 тот же пул; quota_policies per model_id; env thinking summary/title/memory | thinking внутренних задач не редактируется через admin DB |
| ТЗ §13 | FIXED_VERIFIED | request.model неизменен во всех ротациях; PoolExhausted не меняет модель; явные отказы для unknown/disabled | Легаси-ветка resolve_model_and_thinking (недостижима в прод-пути) |

## Тесты

- 79 passed (указанные 4 gemini-файла), 39 passed (ledger + generation), 488 passed (весь tests/) — exit 0 везде, skip'ов нет.
- Все тесты offline: in-memory фейки + httpx.MockTransport; реальная БД/сеть/live-ключи не задействованы. PostgreSQL-семантика (A09) и live-Gemini (A27) остаются непроверенными в этой среде — CANNOT_VERIFY в этих двух узких местах, отражено в статусах FIXED_PARTIAL.
