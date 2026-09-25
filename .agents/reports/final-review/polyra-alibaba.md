# Final review — polyra-alibaba (READ-ONLY аудит)

**Аудитор:** polyra-alibaba (final review, read-only)
**Дата:** 2026-09-25
**Checkout:** O:\work\aibot (текущий, не исторический ZIP)
**Scope:** app/llm/providers/alibaba.py; app/llm/capabilities.py; app/llm/registry.py; app/llm/base.py; app/llm/events.py; app/services/llm_factory.py (alibaba); app/services/credentials.py; app/services/generation.py (alibaba-части); scripts/smoke_providers.py; tests/unit/test_alibaba_provider.py; tests/unit/test_registry.py; tests/integration/test_fix_v2_*.py (alibaba-части).
**Метод:** чтение кода с file:line и цитатами; сверка тестов с production-путём; offline-прогон pytest (без сети, без live-вызовов). Приложение не запускалось, git-состояние не менялось.

## Прогон тестов (реальный, сегодняшние логи)

```
.venv\Scripts\python -m pytest tests/unit/test_alibaba_provider.py tests/unit/test_registry.py \
  tests/integration/test_fix_v2_usage_ledger.py tests/integration/test_fix_v2_stream_contract.py -q
57 passed in 6.36s        EXITCODE=0

.venv\Scripts\python -m pytest tests/unit/test_generation.py tests/unit/test_api.py tests/unit/test_bot_wiring.py -q
72 passed in 4.01s        EXITCODE=0
```

Живых вызовов Alibaba не выполнялось (запрещено заданием). Live-свидетельства берутся только из сохранённых артефактов probe (см. A27/N04).

---

## A06 (alibaba-часть) — FIXED_VERIFIED

**Требование:** Alibaba может отдать Usage следующим SSE-чанком после finish_reason; consumer должен дождаться usage; порядок yield.

**Код:**
- `app/llm/providers/alibaba.py:271-301` — `_process_sse_line` возвращает `(events, done, sentinel)`: `Usage` извлекается и кладётся в `events` немедленно (строки 292-294), а `Done` из `_events_from_choice` **откладывается** (`done = event  # Done эмитим последним, после usage-trailer`, строка 298).
- `app/llm/providers/alibaba.py:304-325` — `parse_chat_completions_sse`: буферизует `pending_done`, читает строки до EOF/`[DONE]`, и только потом `yield pending_done` последним:
  ```python
  if pending_done is None:
      raise NetworkError("unexpected EOF: stream ended without finish")
  yield pending_done
  ```
  Чанк `finish_reason` + usage в одном чанке тоже корректен: usage попадает в `events` раньше отложенного Done.
- Consumer: `app/services/generation.py:842-853` — `break` на `Done` теперь безопасен: Done — гарантированно последнее событие, Usage уже доставлен (`_ConsumeState.apply_simple`, строки 292-299: `elif isinstance(event, Usage): self.usage = event`).
- Закрытие стрима всегда: `app/services/generation.py:858-864` — `finally: aclose(stream)` (break/ошибка/отмена).

**Тесты (реальные, прошли):**
- `tests/unit/test_alibaba_provider.py:116-142` `test_usage_final_chunk` — usage-trailer (choices: []) после finish-чанка → порядок `[TextDelta, Usage, Done]`.
- `tests/integration/test_fix_v2_stream_contract.py:149-197` `test_alibaba_full_stream_usage_before_done_single_done` — provider→parser→consumer `_consume`: `outcome.usage == Usage(10/5/3/15)`, `finish_reason == "stop"`, Done ровно один, usage строго до Done.
- `tests/integration/test_fix_v2_usage_ledger.py:163-207` — usage по двум tool-раундам суммируется 300/50.

**Мелкие замечания (не ломают контракт):**
1. Строка usage, приходящая ПОСЛЕ sentinel `data: [DONE]`, отбрасывается (`break` на sentinel, alibaba.py:321-322). По wire-протоколу `[DONE]` терминален, так что это не воспроизводимая проблема.
2. Гонка отмены: `_consume` проверяет `cancellation.is_set()` в начале каждой итерации (generation.py:843); отмена, случившаяся между текстом и usage-trailer, теряет usage раунда (usage=None, «unknown ≠ 0» — не фальсифицируется). Best-effort, приемлемо.
3. Внутренние потребители (`collect_text`, app/context/compactor.py:163-174) не делают явный `aclose` — полагаются на asyncgen-финализатор CPython. Основной путь (`_consume`) закрывает явно.

**Паттерн «тест проходит — прод ломается»:** не найден для A06. Done эмитится только после реального EOF/`[DONE]`, т.е. буферизация не зависит от моков.

---

## A11 (alibaba-часть) — FIXED_PARTIAL

**Требование:** Stop: adapter проверяет cancellation; read timeout; прерывание HTTP stream.

**Код:**
- Проверка отмены на каждой строке + закрытие response: `app/llm/providers/alibaba.py:397-407`:
  ```python
  async for line in response.aiter_lines():
      if request.cancellation.is_set():
          await response.aclose()
          return
  ```
- Тихая отмена: `alibaba.py:375-378` — `except NetworkError: if request.cancellation.is_set(): return` (отмена ≠ EOF-ошибка).
- Прерывание зависшего чтения: `app/services/generation.py:121-131` — `GenerationRegistry.stop`: `gen.cancellation.set(); gen.task.cancel()`; wired к Telegram `stopped_message_generation` (`app/bot/routers/stop.py:19-25`).
- CancelledError → partial: `generation.py:856-857` (`except asyncio.CancelledError: state.cancelled = True`), сохранение `_save_cancelled` (generation.py:1135-1179).
- Отмена перед каждым tool side effect: `generation.py:712-715` (`if cancellation.is_set(): return True` внутри цикла по вызовам, не после пачки).
- Read timeout: `alibaba.py:344` — `httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=30.0)` — ограничен.

**Тесты:**
- `tests/unit/test_alibaba_provider.py:425-431` — cancellation до итерации → 0 событий.
- `tests/integration/test_fix_v2_stream_contract.py:277-285` — то же provider-уровнем.
- `tests/unit/test_generation.py:327-353` — отмена по флагу с partial; CancelledError из стрима → partial.
- `tests/unit/test_generation.py:70-90` + `tests/unit/test_bot_wiring.py:74-83` — stop() → cancellation set + task.cancel.

**Почему FIXED_PARTIAL:**
1. **Нет приёмочного теста «зависший стрим прерывается за короткий deadline»** (критерий: «прекращается за локально заданный короткий deadline без ожидания read timeout»). Механизм `task.cancel()` реален, но ни один тест не подаёт вечно молчащий HTTP-read и не меряет время отмены. Проверка отмены в `_cancellable_lines` срабатывает только при приходе новой строки; чистый wait прерывается только `task.cancel()` (пользовательский Stop) либо read=300s.
2. `except asyncio.CancelledError` в `_consume` глотает отмену задачи (намеренно, ради partial), после чего задача дорабатывает `_save_cancelled` — это работает, но в Python 3.11+ отмена задачи считается «поглощённой»; при повторном cancel-запросе поведение остаётся корректным. Отмечено как осознанный трейд-офф.
3. Во время retry-backoff (`alibaba.py:394`, 0.5-1.0s) отмена ловится только через `task.cancel()` (CancelledError пройдёт сквозь sleep) — ок, но не покрыто тестом.

**Прод-риск:** низкий. Основной сценарий Stop (Telegram) задействует task.cancel немедленно.

---

## A14 — FIXED_VERIFIED

**Требование:** отключённый credential (enabled=False) не активируется через env fallback; env fallback только при отсутствии DB-записи; cached clients закрываются/инвалидируются.

**Код:**
- `app/services/credentials.py:21-26`:
  ```python
  credential = await ProviderCredentialRepository(session).get(provider)
  if credential is not None:
      if not credential.enabled:
          return None
      return crypto.decrypt(credential.encrypted_api_key)
  return env_fallback or None
  ```
  env применяется ТОЛЬКО при отсутствии записи; disabled → None (абсолютный запрет).
- `app/services/llm_factory.py:71-90` — при None ключе `raise AuthError("Alibaba API key не настроен")` → ноль HTTP-вызовов; смена ключа → `aclose()` всех stale-провайдеров + очистка кеша (строки 82-87).
- Admin smoke также уважает disabled: `app/api/routes/admin_providers.py:119-124` — `api_key is None` → `ok=False, "alibaba api key not configured (or disabled)"`, без HTTP.

**Тесты:** полная матрица absent/enabled/disabled × env present/absent — `tests/unit/test_api.py:348-398` (6 тестов, все прошли в моём прогоне). В частности `test_credentials_disabled_record_env_present_returns_none` (строки 382-389).

**Замечания (не понижают статус):**
1. Инвалидация кеша factory на уровне `ManagedLLMStream` не покрыта юнит-тестами (см. A30).
2. Диагностический скрипт `scripts/smoke_providers.py:429-439` (`_resolve_alibaba_key`) сначала зовёт `get_provider_api_key(..., env_fallback="")`, а при None (в т.ч. **disabled**) отдельным ветвлением берёт env `ALIBABA_API_KEY`. Для ручного probe это не «0 HTTP при disabled». Production-путь корректен; расхождение существует только в диагностике.
3. Если ключ отключён без подстановки нового, закешированный старый `AlibabaProvider` не закрывается до shutdown (но и не используется — AuthError до провайдера).

---

## A23 (alibaba-часть) — FIXED_VERIFIED

**Требование:** SSE state machine: malformed frames, unexpected EOF, error frame после text, fragmented tool args, [DONE]; не принимать незавершённый поток за успех.

**Код (`app/llm/providers/alibaba.py`):**
- Malformed JSON / не-data строки: строки 274-287 — не-`data:` строки игнорируются; битый JSON → `logger.debug` + skip (поток живёт).
- Error frame: строки 288-289 — `if chunk.get("error"): raise classify_stream_error(chunk["error"])` — поднимается в ЛЮБОЙ точке потока, в т.ч. после текста; классификация по vendor code (строки 185-195: `Throttling.*`/`limit_*` → RateLimit, `InvalidApiKey` → Auth, `ModelUnavailable`/`ServiceUnavailable`/`InternalError*` → Server).
- Fragmented tool args: строки 224-247 — аккумуляция по `index` (id/name из первого чанка, аргументы конкатенируются), flush при `finish_reason=="tool_calls"`.
- [DONE]: строки 279-280 — sentinel; `[DONE]` без finish_reason → `pending_done is None` → EOF-ошибка.
- Unexpected EOF: строки 323-324 — `raise NetworkError("unexpected EOF: stream ended without finish")`. EOF с недособранными tool_calls — та же ошибка (pending не флашится без finish).
- Ровно один терминальный Done; после partial НЕТ тихого повтора: `alibaba.py:384-387` — `if events_started or attempt + 1 >= _MAX_ATTEMPTS: raise error` (retry только до первого события, максимум 1 повтор, только 429/5xx/network/timeout).

**Тесты (прошли):**
- `tests/unit/test_alibaba_provider.py:145-177` — EOF без finish / EOF с pending tool_calls / `[DONE]` без finish → `NetworkError("unexpected EOF")`.
- `tests/integration/test_fix_v2_stream_contract.py:200-237` — EOF без Done: partial дошёл, `calls == 1` (нет retry после partial), Done не фабрикуется; malformed JSON + comment-frame → skip, поток жив.
- `:240-274` — фрагментированные в 3 чанка аргументы → ОДИН ToolCall с валидным JSON; `:407-425` — ровно один Done.
- `tests/unit/test_alibaba_provider.py:414-422` — error frame `Throttling.RateQuota` → RateLimitError.

**Замечания:**
1. Теста «error frame ПОСЛЕ текста» нет, но кодовая точка одна и та же (raise внутри `_process_sse_line` независимо от позиции); поведение: partial сохраняется, исключение уходит потребителю, повторов нет (`events_started=True`).
2. `finish_reason` вне множества {stop, length, tool_calls} (например `content_filter`) не порождает Done → на EOF классифицируется как NetworkError «unexpected EOF». Fail-closed (не успех), но пользователю уйдёт «Провайдер временно недоступен» вместо явной причины. Косметика.
3. HTTP 429: `Retry-After` не парсится (поле `retry_after` у ProviderError для Alibaba не заполняется) — есть bounded backoff 0.5/1.0s, джиттера нет. Мелочь.
4. Толерантность к битым фреймам (skip) — осознанный выбор, зафиксированный тестом; «определённый ожидаемый статус» из приёмки соблюдён.

**Прод-паттерн «тест-绿-прод-ломается»:** не найден. И EOF-семантика, и буферизация Done реализованы на уровне парсера, а не мока: моки подают реальные SSE-строки через `httpx.MockTransport`.

---

## A25 (alibaba-часть) — FIXED_PARTIAL

**Требование:** strict model validation: unknown/disabled model_id в сохранённых настройках → явная ошибка, не silent fallback.

**Код:**
- Runtime-gate: `app/services/generation.py:955-965`:
  ```python
  saved_model = chat.model_id or user_settings.default_model_id
  if saved_model is not None and self._registry.get_or_none(saved_model) is None:
      ... bot.send_message("⛔ Сохранённая модель «{...}» больше недоступна. ...")
      return None
  ```
  Явный отказ, другая модель НЕ вызывается.
- Disabled: `generation.py:459-472` `_model_denial` — `not model_def.enabled or overrides.get(...) is False` → «⛔ Модель ... отключена администратором».
- API: `app/api/routes/chats.py:71-78` `_validate_model` (unknown/internal → 400; not allowed → 403); `:156-169` PATCH-валидация model_id+thinking_setting (в т.ч. наследуемая базовая модель при model_id=null); admin default_model/thinking валидируются (`app/api/routes/admin_system.py:92-102`), allowlist PUT отклоняет unknown ids (`admin_users.py:106-110`).

**Тесты:** `tests/unit/test_api.py:195-218` (models endpoint), `:303-321` (`_chat_out` с override). Матрица A25-runtime как тестов нет.

**Почему FIXED_PARTIAL:**
1. **Осталась мёртвая, но живая ветка silent fallback:** `app/services/generation.py:190-211` `resolve_model_and_thinking` всё ещё содержит «Неизвестная model_id → fallback на settings.default_model» (строки 204-207). В production-пути она теперь недостижима (gate в `_prepare` стоит раньше), но функция экспортируется и является латентным возвратом старого контракта.
2. **Тест, который A25 прямо требовал исправить, НЕ исправлен:** `tests/unit/test_generation.py:187-194` `test_resolve_unknown_model_falls_back_to_default` — всё ещё ассертит silent fallback (`assert model_id == "gemini-3.8-flash"` для `chat=_chat("no-such-model")`). KIMI_FIX_PROMPT A25: «Исправить тест, который сейчас ожидает unknown-model fallback».
3. Нет прямого теста пользовательского сообщения gate'а (`«больше недоступна»`) и `_model_denial`.
4. PATCH `/chats` не отклоняет model_id, отключённый admin-ом (проверяет только unknown/permissions) — runtime откажет явно, но настройки сохранить можно. Minor.

---

## A27 (alibaba-часть) — FIXED_PARTIAL

**Требование:** probe: перебор из registry, не hardcoded; ≥10 baseline (9 user + 1 internal) + Pro; --strict; cache с TTL; skip/no-key ≠ pass; ключи не в логах.

**Код (`scripts/smoke_providers.py`):**
- Enumeration из registry: строки 85-87 `_registry_models` = `[m for m in registry.list_all() if m.provider == provider]`; docstring строки 19-20. Hardcoded списки `_GEMINI_MODELS`/`_ALIBABA_MODELS` удалены (grep подтверждает отсутствие). Baseline: `default_registry()` = 10 моделей (9 пользовательских + `gemini-3.5-flash-lite` internal, помечается в notes строкой 370).
- `--strict`: строки 636-641, 716-719 — exit 1 при `fail`-чеках; `_count_failures` (671-682) не считает skipped за fail; docstring фиксирует коды 0/1/130.
- Секреты: строка 496 `key_mask=mask_secret(api_key)` + `key_source`; в JSON/таблице только маска; CLI печатает маски. Проверено по сохранённому probe-артефакту: `key_mask: "sk-4...4054"`.
- Проверки чеков реального поведения: `_missing_events` (148-157) требует TextDelta + Done + Usage; thinking-уровень различает accepted (стрим с Done) vs rejected (400) — «не только accepted параметр». `_collect` (130-145) теперь идентичен production-потреблению (Done после A06-фикса доезжает).
- Live-артефакт: `.agents/reports/probe_20260924_190515.json` — 2026-09-24, `--strict`, base_url dashscope compatible-mode/v1, все 6 alibaba-моделей (включая deepseek-v4-pro отдельно: text/off/high/max/FC ok, image=skipped text-only) — `status: ok` по каждому чеку. MODEL_REGRESSION_MATRIX.md (fix-v2) описывает прогон.

**Почему FIXED_PARTIAL:**
1. **Cache с TTL / versioned capability store не реализован.** Результаты пишутся в JSON-отчёты с timestamp, но нет runtime-хранилища (endpoint/credential/model + TTL + stale marker), и «Изменение результата probe меняет API capabilities/UI» НЕ реализовано: `/api/models` (`app/api/routes/me.py:41-63`) читает только статический registry; механизм `probe_required` существует (capabilities.py:45, фильтрация в me.py:57-58), но ни у одной модели не выставлен. Ни один runtime-путь не потребляет probe-результаты.
2. `--strict` при полном skip (нет ключей) возвращает 0 — «skipped ≠ fail» документировано, но строгий прогон, где ничего не проверено, выходит зелёным.
3. Cache key не разделяет credential-версию (ключей просто нет в отчёте — только маска; для runtime-кеша это не строилось).
4. Артефакт `probe_20260924_190515.json` физически сохранён в UTF-16 с транскрипт-хвостом `"\r\n\r\n[exit 0]"` (json.load UTF-8 падает) — файл вставлен из консоли VPS, а не писан скриптом (скрипт пишет UTF-8, `smoke_providers.py:600`). Содержимое выглядит подлинным и согласуется с кодом, но как машиночитаемое свидетельство файл повреждён форматом.

**Прод-паттерн:** enumeration реально из registry (новые модели автоматически попадают в прогон) — не сломается при добавлении моделей.

---

## A30 (alibaba-часть) — FIXED_PARTIAL

**Требование:** cached clients по ключу: eviction/close при ротации ключа; закрытие при shutdown.

**Код:**
- `app/services/llm_factory.py:50, 71-90` — кеш `dict[api_key → AlibabaProvider]`; новый ключ → `aclose()` всех stale + `clear()`; провайдер создаётся с `trust_env=False`.
- Shutdown: `app/services/llm_factory.py:92-100` `aclose()` (идемпотентно, `_closed`-флаг) + `app/main.py:132-137` — `finally: await llm_stream.aclose()` вместе с search/bot/DB.
- Admin smoke (ручной) создаёт и закрывает провайдер на вызов: `admin_providers.py:104-105` (`finally: await provider.aclose()`), singleton-инвариант на него не распространяется (комментарий 87-89).
- Поставляемый извне `http_client` не закрывается провайдером (`alibaba.py:340-351`, `_owns_client`).

**Почему FIXED_PARTIAL:**
1. **Нет тестов lifecycle** (приёмка: «тесты проверяют counters закрытия и отмену активных запросов»). Ни один тест не проверяет: ротацию ключа → aclose старого; shutdown → aclose один раз; «многократные вызовы не оставляют открытых клиентов». Код читается корректно, но не заперт тестами.
2. Ротация ключа при активном стриме: `stale_provider.aclose()` закрывает общий `AsyncClient`, даже если им стримит параллельная генерация — in-flight запрос упадёт с NetworkError (user увидит «Провайдер временно недоступен»). Edge-case, не покрыт.
3. Отключение credential без нового ключа не закрывает старый кеш (см. A14-замечание) — закрытие отложено до shutdown; HTTP-трафика нет.

---

## A39 (alibaba-часть) — FIXED_PARTIAL

**Требование:** полный assistant turn (text + tool calls + reasoning protocol metadata) в следующий request; общий deadline/лимиты tool-loop.

**Код:**
- Полный turn: `app/services/generation.py:708-709` — `messages.append(self._assistant_tool_message(outcome.tool_calls, outcome.text))`; `:759-777` — parts = [text (round_text)] + tool_call parts (id/name/arguments_json/**provider_meta**). Wire: `app/llm/providers/alibaba.py:103-117` `_map_assistant_message` — `content = text or None` + `tool_calls[]` (OpenAI-формат), т.е. текст раунда не теряется.
- Лимиты: `app/config.py:55-57` — `max_tool_iterations=8`, `max_tool_calls_per_round=4`, `max_generation_seconds=240`; `generation.py:618-623` — общий deadline (`time.monotonic()`), проверяется на каждой итерации цикла; `:711` — обрезка пачки до 4; `:749-757` — стоп по iterations.
- Reasoning не покидает сервис: `_ConsumeState.apply_simple` считает только `reasoning_chunks`; ReasoningDelta → никогда не TextDelta (`alibaba.py:257` — «НИКОГДА не TextDelta»); в историю не пишется.
- Для Alibaba reasoning-protocol metadata у tool calls не существует в wire (thought signatures — Gemini-only); `provider_meta={}` передаётся as-is и в mapping не уходит — корректно для этого провайдера. `preserve_thinking=False` у qwen (alibaba.py:91-92) исключает возврат reasoning в историю.

**Тесты (прошли):** `tests/integration/test_fix_v2_usage_ledger.py:163-207` — 2 раунда, второй request содержит tool results, usage 300/50; `:210-268` — отмена после раунда. `tests/unit/test_generation.py:502-541` — max iterations; `:544-592` — tool при finish=stop. `test_alibaba_provider.py:281-343` — wire-mapping полного turn (assistant content+tool_calls, tool result с tool_call_id).

**Почему FIXED_PARTIAL — включая реальный прод-риск:**
1. **[ПРОД-РИСК] Рассинхрон turn'а и результатов при >4 вызовах в раунде:** `generation.py:709` кладёт в assistant-сообщение ВСЕ `outcome.tool_calls`, а исполняет и отвечает tool-результатами только `outcome.tool_calls[:max_tool_calls_per_round]` (строка 711). Если модель выдаст 5+ параллельных вызовов (web_search/open_url/remember/... — инструменты размечены), следующий request будет содержать `assistant.tool_calls` с id, для которых нет `role:"tool"` сообщений. Строгие OpenAI-compatible endpoint'ы (включая dashscope compatible-mode) обычно отклоняют это 400 («each tool_call must be followed by a tool message») → user получит «Запрос некорректен для этой модели», раунд потерян. Тесты используют 1-2 вызова и этот случай не покрывают. Типичный «фикс проходит тест, но ломается в проде».
2. **Общий deadline проверяется только МЕЖДУ раундами** (`generation.py:620-623` — верх цикла). Один раунд может занять read=300s + 1 retry + backoff (~600s), превысив `max_generation_seconds=240` без Stop. Без отмены пользователя цикл завершится только по истечении стрима.
3. Aggregate result/token budget tool-loop отсутствует (только per-result `max_result_size` в runner.py:148-150 и per-tool timeout). Приёмка просила «result budget».
4. Нет теста на `max_tool_calls_per_round` (grep: упоминаний в тестах нет).

---

## N03 — FIXED_PARTIAL

**Требование:** все модели сохранены; capability states supported/unsupported/unknown; transient (timeout/429/5xx/skip) не удаляют модель и не стирают capability; TTL/stale marker; retry/failover только в рамках той же модели.

**Что подтверждено:**
- **Все 10 ID на месте:** `app/llm/capabilities.py:56-99` (gemini-3.8/3.7/3.6-flash, gemini-3.5-flash-lite internal) + `:101-165` (qwen3.8-flash, qwen3.8-max, deepseek-v4.1-flash, deepseek-v4-pro, glm-5.3, kimi-k3). Тест-замок: `tests/unit/test_registry.py:7-25` — точное множество из 10 ID, `len >= 10`, Flash не заменён. IDs не переименовывались.
- **Transient-безопасность by construction:** registry статичен; probe/ошибки не пишут в registry; admin enable/disable — отдельный явный слой (`model_overrides`, `admin_models.py:47-59`); cache-secrets не хранятся.
- **Retry только той же моделью:** `alibaba.py:359-395` — retry с тем же `payload` (тот же model), фиксированный endpoint; cross-model fallback отсутствует (grep «fallback» по alibaba-пути — только повтор той же попытки). `_stream` в llm_factory.py:56-69 не переключает модель.
- **Unknown ≠ 0:** `_sum_usage`/`usage_total=None` при отсутствии usage (generation.py:614-615, 264-277); tests `test_sum_usage_none_plus_none`.
- Parted: enabled/credential/health/capability не смешаны (credentials.py; admin_providers; registry).

**Чего нет (почему PARTIAL):**
- **Capability states / TTL / stale marker не реализованы как runtime-хранилище.** Состояния ok/fail/skipped живут только в JSON probe-отчётах; нет версии по endpoint/credential/model, нет TTL, нет «устаревшего» маркера, нет стирания-невзирания — просто потому, что runtime ничего не хранит. «Старые успешные capability evidence не стираются» выполняется тривиально (нечего стирать), но и не управляет UI (см. A27).
- 429 без Retry-After/jitter (см. A23-замечания); «bounded retry с общим deadline» для ОДНОГО вызова Alibaba ограничен только попытками (2), не временем — deadline есть лишь на уровне tool-loop (между раундами, см. A39).

---

## N04 — FIXED_VERIFIED (с одним буквальным отклонением от spec-текста)

**Требование:** exact ID `deepseek-v4-pro`; provider alibaba; internal_only=false; без image capability; DEFAULT/OFF/HIGH/MAX mapping; нет LOW; нет thinking_budget/preserve_thinking/clear_thinking; max_completion_tokens; photo→text-only guard; admin allowlist отдельной галочкой; probe отдельно от Flash; endpoint dashscope compatible-mode/v1, DIRECT, trust_env=False.

**Подтверждено по коду:**
- Registry: `app/llm/capabilities.py:128-139` — `model_id="deepseek-v4-pro"`, `display_name="DeepSeek V4 Pro"`, `input_modalities=frozenset({"text"})` (комментарий «vision НЕТ (docs 2026-09-24)» — image НЕ скопирован с Flash), `max_context=1_000_000`, `max_output=393_216`, `thinking_modes=(THINKING_OFF, THINKING_HIGH, THINKING_MAX)` (LOW отсутствует — low=alias high, capabilities.py:136-137), `default_thinking=None`, `internal_only` по умолчанию False. Тест: `tests/unit/test_registry.py:28-41` (все поля, включая `supports_images is False` и `thinking_modes == ("off","high","max")`), `:44-49` (Pro в user list).
- Payload: `app/llm/providers/alibaba.py:63-69` `_THINKING_TABLE["deepseek-v4-pro"]`: off → `{"enable_thinking": False}`; high → `{"reasoning_effort": "high"}`; max → `{"reasoning_effort": "max"}`; DEFAULT (thinking=None) — ни одного ключа. Pro не входит в `_QWEN_MODELS` (нет `preserve_thinking`) и не `_GLM_MODEL` (нет `clear_thinking`); `thinking_budget` не используется нигде (grep). Тесты: `test_alibaba_provider.py:245-270` (параметризованные 4 состояния + полный forbidden-набор), `:273-278` (`max_completion_tokens`, не `max_tokens`).
- **Отклонение от буквального текста приёмки:** N04-spec требует HIGH/MAX = `enable_thinking=true` + `reasoning_effort`. Реализация шлёт только `reasoning_effort` (без явного `enable_thinking=true`). Функционально эквивалентно: у Pro thinking ON по умолчанию (docs/vendor/ALIBABA.md:80, «DeepSeek-V4 series enables thinking by default»), и live-probe 2026-09-24 (`probe_20260924_190515.json`: `thinking:high/max` → ok с ReasoningDelta) подтверждает активное thinking. Отмечаю как документированную реализацией семантику, не как баг.
- Text-only guard: `app/services/generation.py:475-485` — фото при Pro → «⛔ ... не принимает изображения. Модели с поддержкой изображений: <список из разрешённых>» — явная ошибка, без потери фото, без запроса в другую модель, с перечнем альтернатив в рамках permissions (N04-п.7).
- Admin allowlist: `/api/models` включает Pro как user-модель; Mini App `AdminUsersPage.tsx:287-303` — отдельный checkbox на каждую модель (Pro в их числе), «Все модели»-тоггл отдельно; PUT отклоняет unknown ids (`admin_users.py:106-110`). Пустой allowlist = запрет всех (`app/services/access.py:112-114`).
- Probe отдельно от Flash: отдельная запись в registry-enumeration, свои checks (off/high/max, без low; image skipped — text-only) — видно в probe-артефакте 2026-09-24.
- Endpoint/transport: `app/config.py:23` — default `https://dashscope.aliyuncs.com/compatible-mode/v1`; локальный `.env` override указывает на тот же host/path (проверено: host=dashscope.aliyuncs.com, path=/compatible-mode/v1); `alibaba.py:341-346` — `trust_env=False` (ADR-004, DIRECT); тест `test_default_client_trust_env_false` (test_alibaba_provider.py:434-437). Кастомных `base_url` из request нет.
- Live E2E (выдать Pro → выбрать → сохранить → стрим → usage/run) в моих рамках не проверяем (запрет live); offline-контракты и probe-артефакт закрывают всё, кроме полного Telegram-флоу, который остаётся на приёмке владельца. MATRIX (fix-v2) заявляет live-pass по всем режимам Pro, но это чужой артефакт — принимаю как свидетельство с оговоркой формата файла (см. A27).

---

## Сводка «фикс проходит тест, но ломается в проде»

| # | Паттерн | Пункт | Серьёзность |
|---|---|---|---|
| 1 | Assistant-turn содержит ВСЕ tool_calls, tool-результаты только первые 4 → dangling tool_call_ids → вероятный 400 следующего request'а | A39 | Средняя-высокая (при ≥5 параллельных вызовах) |
| 2 | `resolve_model_and_thinking` сохраняет silent-fallback ветку + старый тест `test_resolve_unknown_model_falls_back_to_default` закрепляет её | A25 | Низкая (gate в `_prepare` стоит раньше), латентная |
| 3 | Общий deadline tool-loop проверяется только между раундами; один раунд может идти ~300-600s (read timeout + retry) | A39/A11 | Средняя |
| 4 | Ротация ключа Alibaba при активном стриме убивает in-flight запрос (aclose общего клиента) | A30/A14 | Низкая (edge) |
| 5 | `--strict` без ключей = exit 0; probe-результаты не влияют на runtime capabilities/UI (нет кеша с TTL) | A27 | Средняя (функциональный пробел, не поломка) |
| 6 | `finish_reason` вне {stop,length,tool_calls} (напр. content_filter) → NetworkError «unexpected EOF» вместо явной причины | A23 | Низкая (fail-closed) |
| 7 | Probe-артефакт сохранён как UTF-16 + хвост консоли — не машиночитаем; свидетельство живое, но формат повреждён | A27/N04-evidence | Низкая (документальная) |

## Не проверяемо в рамках аудита (CANNOT_VERIFY)

- Live-поведение endpoint'а для `enable_thinking:false` / `reasoning_effort:{high,max}` у Pro (беру из артефакта probe 2026-09-24, не из собственного прогона).
- Полный Telegram E2E Pro-сценария (выдача права → выбор → стрим → история), Mini App UI в браузере.
- Поведение реального dashscope на dangling tool_call_ids (риск №1 — по спецификации OpenAI-протокола, не live-проверено).

## Итоговая таблица

| Пункт | Статус | Ключевые доказательства |
|---|---|---|
| A06 (alibaba) | FIXED_VERIFIED | alibaba.py:292-325 (буферизация Done, usage до Done); test_usage_final_chunk; integration test 149-197 |
| A11 (alibaba) | FIXED_PARTIAL | alibaba.py:397-407 + generation.py:121-131 (task.cancel), 712-715; нет теста hung-read ≤ deadline |
| A14 | FIXED_VERIFIED | credentials.py:21-26; llm_factory.py:71-90; test_api.py:348-398 (полная матрица) |
| A23 (alibaba) | FIXED_VERIFIED | alibaba.py:271-325, 384-395; unit+integration EOF/malformed/fragment/error-frame тесты; minor: content_filter-миссклассификация |
| A25 (alibaba) | FIXED_PARTIAL | generation.py:955-965 (gate) ✓; НО resolve_model_and_thinking:204-207 fallback жив + старый тест:187-194 не исправлен |
| A27 (alibaba) | FIXED_PARTIAL | smoke_providers.py:85-87, 636-641, 716-719; probe-артефакт 2026-09-24; НЕТ runtime-кеша с TTL / probe→UI |
| A30 (alibaba) | FIXED_PARTIAL | llm_factory.py:50, 82-100; main.py:132-137; НЕТ lifecycle-тестов |
| A39 (alibaba) | FIXED_PARTIAL | generation.py:708-711, 618-623, 759-777; config.py:55-57; ПРОД-РИСК dangling tool_call_ids; deadline только между раундами |
| N03 | FIXED_PARTIAL | capabilities.py:56-165 (все 10 ID); retry той же модели (alibaba.py:359-395); НЕТ capability-store/TTL/stale |
| N04 | FIXED_VERIFIED | capabilities.py:128-139; alibaba.py:63-69; generation.py:475-485; AdminUsersPage.tsx:287-303; probe-артефакт; отклонение: HIGH/MAX без явного enable_thinking=true (функционально эквивалентно, live-probe ok) |
