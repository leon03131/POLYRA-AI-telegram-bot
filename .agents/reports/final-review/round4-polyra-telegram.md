# Round 4 — bug hunt: Telegram-слой / streaming / Stop (POLYRA)

- HEAD: `4925c40` (плюс `0be0524`) — рабочее дерево чистое.
- Scope: `app/bot/**`, `app/services/generation.py`, `app/bot/streaming/draft.py`, `tests/unit/test_bot_helpers.py`, `tests/unit/test_bot_wiring.py`, `tests/unit/test_draft_streamer.py`, `tests/integration/test_fix_v2_reaudit.py`.
- Метод: полное чтение файлов scope + трассировка всех внешних вызовов (aiogram 3.31.0 из .venv, репозитории, context/tools/registry), офлайн-репро скриптами в temp (`round4_cancel_trace.py`, `round4_draft_tiers.py`). Read-only, git-операций и live-вызовов не было.
- Бейслайн воспроизведён: `pytest tests -q` → 476 unit + 27 integration = **503 passed**.

## Сводка

| # | Находка | Серьёзность | Файлы:строки |
|---|---|---|---|
| 1 | Stop в tool-раунде: CancelledError улетает из `generate`, partial теряется, run навсегда `running` | **P1** | generation.py:688-698, 713-724, 738-788; tools/runner.py:126; registry.stop:121-131 |
| 2 | `/chats`: label кнопки = title(≤256) + «·» + дата > лимита InlineKeyboardButton.text 64 → TelegramBadRequest, команда ломается | **P1** | commands.py:112-118; api/routes/chats.py:27,33,122-123,171 |
| 3 | `open_chat` подтверждение с user-title в HTML-режиме (default parse_mode бота) → 400 на `<`/`&` | P2 | commands.py:139-140; dispatcher.py:40 |
| 4 | Двойной Stop: второй `task.cancel()` рвёт `_save_cancelled` → run `running` | P2 | generation.py:1195-1243, 487-488; registry:121-131 |
| 5 | Tier2: транзиентная ошибка → перманентный downgrade в tier3 (противоречит доктрине A22 «сети tier не меняют») | P2 | draft.py:300-303 vs 10-12, 282-299 |
| 6 | Первый пустой flush 1→2→3 без записи состояния: авто-flush в `append` не взводится → стрим молчит до финала | P2 | draft.py:134-144, 305-308, 431 |
| 7 | Неизвестный global default model → `UnknownModelError` пробрасывается из `_prepare` → generic «внутренняя ошибка» (A25-дыра для default) | P2 | generation.py:1012-1027, 207 |
| 8 | A13: выборка истории по env-`recent_history_limit`, builder — по effective DB `context_keep_recent` → недобор контекста при больших DB-настройках | P2 | generation.py:990-992 vs 592-596 |
| 9 | Лимиты 4096/32700 считаются в Python-символах; astral-символы (emoji) = 2 UTF-16 юнита → граничные части чуть длиннее лимита Telegram | P2 | draft.py:95-107, 178-185; generation.py:1212 |
| 10 | `stop_all_for_user` (ban/revoke) не делает `task.cancel()` — только event → зависший стрим не прерывается | P2 | generation.py:112-119 vs 121-131 |
| 11 | Легаси-путь без builder не делает rehydration картинок (комментарий «A18: rehydration ВСЕГДА» верен только для builder-пути) | P3 | generation.py:575-578, 588-590 |
| 12 | Deadline `max_generation_seconds` помечает run `completed` (не cancelled/failed) | P3 | generation.py:673-676, 446-485 |
| OK | P0-класс «несуществующие имена» (как ContextBuilder): все проверены — чисто | OK | см. раздел «Проверено чисто» |

---

## Детальные находки

### 1. P1 — Stop в tool-раунде: partial теряется, run остаётся `running` (A11-остаток)

Единственное место, где `asyncio.CancelledError` гасится — `_consume` (generation.py:911-912). Ни `generate` (только try/finally, 430-488), ни `_stream_loop` (689-698: `except (ProviderError, PoolExhaustedError)` / `except Exception` — CancelledError = BaseException, не ловится), ни `_run_tool_round` его не ловят.

Трассировка `registry.stop` (generation.py:121-131):
```python
gen.cancellation.set()
gen.task.cancel()
```
- cancel внутри `async for event in stream` → **ловится** в `_consume`, outcome.cancelled=True → `_save_cancelled` → partial уходит сообщениями (A11 работает). Подтверждено репро CASE 2.
- cancel внутри `_run_tool_round` — `await tool_runner.execute(...)` (generation.py:770) или `await self._resolve_jina_reader()` (758) → `ToolRunner._invoke` (`await asyncio.wait_for(tool.handler(...))`, tools/runner.py:126) не гасит CancelledError (докстринг runner.py:62 честно предупреждает: «Исключения наружу не выходят (кроме отмены задачи)») → CancelledError сквозь `_stream_loop` → `generate` → finally pop → handler-таск умирает cancelled.

Доказательство (репро, temp\round4_cancel_trace.py):
```
=== CASE 1: Stop pressed while tool is executing ===
ESCAPED: CancelledError propagated OUT of _stream_loop
  -> generate() has no except for it: _save_cancelled NEVER runs,
     partial text 'partial text before tool call' is LOST, run row stays status='running'
```

Как проявится в проде: пользователь жмёт Stop, пока модель ждёт web_search (типичный tool-раунд — секунды-десятки секунд окна). Драфт исчезает (~30 c эфемерности), **partial не приходит**, run навсегда `running` (исправится только `abort_stale` на рестарте, main.py:115-119). Пользователь не получает даже generic-ошибки: aiogram `ErrorsMiddleware` ловит только `except Exception` (.venv aiogram/dispatcher/middlewares/error.py:31), `_process_update` — тоже `except Exception` (dispatcher.py:331) — CancelledError уходит молча.

Рекомендация владельцу generation.py: ловить `asyncio.CancelledError` в `generate()` вокруг `_stream_loop` (и, при повторном cancel, вокруг `_save_*`), сохранять partial через `_save_cancelled` и завершаться штатно (или `shield` сохранения). Плюс: `_run_tool_round` может проверять `cancellation.is_set()` до/после execute (сделано ДО — generation.py:768, но не во время долгого execute).

### 2. P1 — `/chats`: label inline-кнопки превышает лимит 64 символа

commands.py:112-118:
```python
marker = "✅ " if chat.id == current_id else ""
label = f"{marker}{chat.title or 'Без названия'} · {chat.created_at:%d.%m.%Y}"
buttons.append([InlineKeyboardButton(text=label, callback_data=build_open_chat_callback(chat.id))])
```
Источники title:
- Mini App: `POST/PATCH /api/chats` пишет `title` без санитайза, `max_length=256` (api/routes/chats.py:27, 33, 122-123, 171 → `update_settings(**data)`), колонка 256 (models/chat.py:23).
- Авто-title: `sanitize_title` допускает ≤60 символов (context/titles.py:34-47).

Арифметика: label = 3 (marker) + title + 3 (« · ») + 10 (дата) = до 76 при авто-title, до 272 при Mini App. Bot API документирует `InlineKeyboardButton.text` как 0-64 символа (aiogram client-side не валидирует — проверено: у установленного типа лимита нет, отказ приходит от Telegram как TelegramBadRequest). Уже авто-title ≥ 52 символов даёт label > 64.

Как проявится: `message.answer(..., reply_markup=...)` в `_send_chats_list` кидает TelegramBadRequest → on_error → «⚠️ Произошла внутренняя ошибка» — `/chats` для этого пользователя сломан, пока title не переименуют. Ни один handler команд не покрыт тестами (см. раздел покрытия), поэтому не поймано.

Оговорка: точное серверное поведение (валидация длины text) — live-проверка, в моём сандбоксе запрещена; базируется на документированном лимите Bot API. Независимо от лимита длины, HTML-инъекция в open_chat (находка 3) уже достаточна для 400.

Рекомендация: обрезать/экранировать label (например, `title[:40]` + дата), для open_chat — parse_mode=None или escape.

### 3. P2 — open_chat: user-title в HTML-режиме

commands.py:139-140:
```python
await callback.answer("Чат выбран")
if isinstance(callback.message, Message):
    await callback.message.answer(f"Открыт чат: {chat.title or 'Без названия'}")
```
Бот создаётся с `parse_mode=ParseMode.HTML` (dispatcher.py:38-42); `answer` наследует default. Title из Mini App не экранирован (HTML-entities не обязателен, но `<`/`&` в title → TelegramBadRequest «can't parse entities»). Выбор чата к этому моменту уже закоммичен (136-137), т.е. ломается только подтверждение; on_error покажет generic-alert (двойной `callback.answer` глушится suppress в errors.py:24-28). Это ровно класс A20 (plain-текст без parse_mode) — тот же фикс: `parse_mode=None`.

### 4. P2 — двойной Stop рвёт `_save_cancelled`

Registry-запись снимается только в `finally` generate (generation.py:487-488). Пока `_save_cancelled` (1195-1243) шлёт partial и пишет БД, запись ещё в реестре → второй `stopped_message_generation` (двойной тап) снова находит её → `task.cancel()` (129-130) → CancelledError в await `bot.send_message`/БД → пробрасывается (ловится только TelegramAPIError, 1214).

Репро CASE 3:
```
ESCAPED: second CancelledError escaped _save_cancelled
  -> run stays 'running'; saved messages: 0 finish calls: 0
```
Прод: run `running` до рестарта; partial может уйти частично (в зависимости от того, какой await был прерван). Тот же принцип: `_save_completed`/`finalize` тоже беззащитны (485 → 478-486). Фикс — как в находке 1 (гашение CancelledError в generate + `uncancel`-семантика), либо pop записи из реестра ДО начала `_save_cancelled`.

### 5. P2 — tier2: транзиентная ошибка = перманентный downgrade в tier3

Докстринг draft.py:10-12 (контракт FIX_V2/A22): «сетевые/прочие ошибки не меняют tier и не роняют flush — следующий вызов ретраит тот же tier». Реализовано только для tier1 (267-280: BadRequest → downgrade; RetryAfter → cooldown; прочее → backoff). Tier2 (300-303):
```python
except TelegramAPIError as exc:
    logger.info("message draft failed (%s), downgrade to throttled edit", exc)
    self._tier = 3
    await self._flush_message_edit()
```
TelegramNetworkError/TelegramServerError — тоже TelegramAPIError → один 502/таймаут в момент message-draft flush навсегда переводит генерацию в tier3 (drafts заброшены, создаётся персистентное сообщение с partial-текстом). Репро CASE (a):
```
after first flush: ['send_rich_message_draft', 'send_message_draft', 'send_message']
tier now: 3 (docstring: 'network errors do NOT change tier')
```
Прод-эффект: деградация UX (ранний message + edit), не потеря данных. Фикс: в tier2区分 BadRequest (downgrade) от прочих API-ошибок (backoff), как в tier1.

### 6. P2 — первый пустой flush: 1→2→3 без состояния → стрим молчит

`generate` делает стартовый `await streamer.flush(force=True)` с пустым `_text` (generation.py:431). Если tier1 отвергнут (BadRequest: пустой markdown) и tier2 тоже (например метод недоступен) → `_flush_message_edit` выходит по `if not text: return` (draft.py:306-308) **не записав ни `_last_flush`, ни `_next_attempt_at`**. Гейт авто-flush в `append` (143):
```python
if (self._last_flush is not None or self._next_attempt_at is not None) and self._due():
```
не взводится никогда → все `append` копят текст без отправки до `finalize`. Репро CASE (b): за всю генерацию — 0 отправок до финала (`finalize` вытащил текст через `send_rich_message`). Сценарий: сервер/чат без поддержки drafts (vendor doc: «только приватные чаты»; группа → drafts падают → но там tier3 создаст сообщение с первым непустым текстом, так что реальный триггер именно «оба драфт-метода отвергнуты на пустом flush»). Эффект: пользователь не видит стрим, ответ приходит одним финальным сообщением. Фикс: в `_flush_message_edit` при пустом тексте вызвать `_schedule_backoff()`/`_mark_flushed()`-эквивалент для взвода гейта (или в `append` гейт по `self._tier is not None`).

### 7. P2 — неизвестный global default model: A25-дыра

A25-проверка в `_prepare` (generation.py:1012-1020) валидирует только `chat.model_id or user_settings.default_model_id`. Эффективный `settings.default_model` (env или DB system_settings, A13) не проверяется: `resolve_model_and_thinking` (207) → `registry.get(model_id)` кидает `UnknownModelError` прямо из `_prepare` (1027) — `except` нет, run ещё не создан, до `send_message` с внятным текстом дело не доходит → on_error → «⚠️ Произошла внутренняя ошибка». Дифф 0be0524 подтвердил: fallback из `resolve_model_and_thinking` удалён намеренно («registry.get below упадёт с UnknownModelError, если просочится»), но «ниже» нет обработчика. Прод: админ удалил модель, не обновив system_settings.default_model → все пользователи без override получают generic-500 на каждом запросе (без stuck-run: user-сообщение не закоммичено — сессия закрывается без commit при исключении). Фикс: валидировать и effective default (как saved_model) с явным отказом.

### 8. P2 — A13: недобор истории при DB context_keep_recent > env

`_prepare` тянет историю по **env**-настройке (generation.py:990-992, `self._settings.recent_history_limit * 2`), а builder работает по **effective DB** `context_keep_recent` (592-596). Без сводки builder получает максимум `env_limit*2` сообщений, тогда как keep_recent может быть больше (A13-минимальный смысл: «на effective (DB) настройках текущего запроса, не env-снимке»). Прод: админ поднял context_keep_recent → контекст меньше сконфигурированного (тихо, без ошибок). Мелкая семантическая неконсистентность.

### 9. P2 — UTF-16 vs code points на границах 4096/32700

`_split_text`/`_tail`/лимиты считают Python-символы (draft.py:95-107, 86-93); Telegram считает длины в UTF-16 code units — astral-символы (эмодзи вне BMP, редкие CJK-ext) = 2 юнита. Часть ровно 4096 code points с достаточной долей эмодзи → 4097+ юнитов → TelegramBadRequest. В `finalize` (176-185) и `_save_cancelled` (1211-1215) исключение на одной части прерывает ВСЕ оставшиеся части (try обёрнут вокруг цикла): пользователь теряет хвост уже полностью сгенерированного ответа. Вероятность низкая (нужно попасть в границу), но LLM-тексты с эмодзи часты. Фикс: делить с запасом (напр. limit-64) или по UTF-16 длине.

### 10. P2 — `stop_all_for_user` не отменяет таск

generation.py:112-119 (вызывается из API при revoke/ban): только `gen.cancellation.set()`, без `gen.task.cancel()` (в отличие от `stop`, 121-131, где cancel обязателен по A11). Зависшее чтение HTTP не прервётся до следующего события стрима/провайдерного таймаута — забаненный пользователь может дожать свой ответ. Стоит вынести общий `cancel`-хелпер.

### 11. P3 — легаси-путь без builder: картинки не rehydrate'ятся

`_build_context` (575-578) выходит ДО `_rehydrate_images` (588-590) — комментарий «A18: rehydration ВСЕГДА» верен только для builder-пути. В проде main.py:83 всегда передаёт builder → пути нет; тесты `_build_context` гоняют только builder-ветку. Только документационная/легаси-неконсистентность.

### 12. P3 — deadline = `completed`

`max_generation_seconds` (generation.py:673-676) выходит через `break` без флага → финальный текст (non-empty) идёт по пути `completed` (472-486): run помечается completed, частичный ответ выглядит как полный. Спорно семантически (expected: cancelled/timed_out), но поведение детерминированное и пользователю ответ приходит.

---

## Проверено чисто (P0-охота: имена/методы/атрибуты в рантайме)

- **aiogram 3.31.0** (`.venv`): `Bot.send_rich_message_draft(chat_id, draft_id, rich_message=, can_stop=, keep_on_stop=)`, `Bot.send_message_draft(chat_id, draft_id, text=, parse_mode=, ...)`, `Bot.send_rich_message(chat_id, rich_message=)`, `edit_message_text`, `get_file`, `download_file` → `BinaryIO|None` (photos.py:50 обрабатывает None), `set_chat_menu_button`, `set_my_commands`, `delete_webhook`, `AiohttpSession(proxy=...)` — все существуют, сигнатуры совпадают с вызовами в draft.py/photos.py/commands.py/main.py. `MessageGenerationStopped` имеет поля `chat`/`draft_id`/`message_thread_id` — stop.py:25 валиден. `Router.stopped_message_generation()` — наблюдатель существует (проверено интроспекцией).
- **ContextBuilder runtime-импорт** (P0 4925c40) — `from app.context import ContextBuilder, TokenBudgetManager` (generation.py:29) работает, `python -c import app.services.generation` чист; TYPE_CHECKING-блок (60-63) теперь лишь дублирует имя — безвредно. Guard-тест в reaudit (422-425) удержит регресс.
- **pool.py → store** (вопрос из брифа): `DbProjectStore` имеет `mark_error`, `set_cooldown`, `mark_success`, `mark_unhealthy`, `list_all` (store_db.py:32-76) — все вызовы pool.py валидны. Zone gemini — чисто.
- **Все вызовы из generation.py**: `ChatRepository.touch`, `ChatService.get_or_create_current_chat/create_chat/list_chats/get_current_chat_id/set_current_chat`, `ChatSummaryRepository.get_for_chat`, `MessageRepository.add_message/list_recent/list_all`, `GenerationRunRepository.create/finish/count_since/tokens_since/abort_stale`, `ModelOverrideRepository.get_all`, `UserSettingsRepository.get_or_create`, `UserRepository.upsert_telegram_user`, `ModelRegistry.get/get_or_none/filter_by_permissions`, `ToolRegistry.list_enabled`, `ToolRunner(registry, session_factory=)` + `execute(call, ctx, generation_run_id=)`, `ToolContext(user_id=,chat_id=,permissions=,session_factory=,settings=,search_manager=,jina_reader=,allowed_tool_names=)`, `make_llm_tools`, `get_effective_system_settings` (все 7 полей EffectiveSystemSettings совпадают с model_copy-ключами), `MemoryExtractor.extract_and_store(user_id=,chat_id=,user_text=,assistant_text=,min_chars=)`, `PostgresFtsRetriever.retrieve(user_id, text, limit=)`, `TitleGenerator.generate_and_set(chat_id, user_text, assistant_text)`, `ContextCompactor.maybe_compact(chat_id)`, `ContextBuilder.build(...)` → BuiltContext(.messages/.system_prompt/.needs_compaction/.max_output_tokens/.fits), `TokenBudgetManager.estimate_text/budget_for/estimate_message/estimate_current` — всё существует, сигнатуры совпадают.
- `_split_text` (приватный импорт из draft.py в generation.py:27) — существует, рантайм-OK; стилистическая пометка only. `LLMStreamFn`/`ManagedLLMStream.__call__` (plain def → async-gen) — контракт `async for` + `aclose` корректен.
- **A20** (plain без HTML-парсинга): `send_message_draft`/`send_message`/`edit_message_text`/`fail`/`finalize`-parts/`_finalize_existing_message`/`_save_cancelled` — везде `parse_mode=None` ✓; HTML-опасные служебные тексты `_BUSY_MESSAGE`/лимит-отказы/отказы модели — статичные, без `<` ✓.
- **A21**: `_tail` держит итог ≤ limit (32700+2 ✓, тест 250-262); «message is not modified» идемпотентен без дублей (draft.py:321-325, 203-206; тесты 345-399) ✓; финал tier3 — edit без дублей ✓.
- **A22**: RetryAfter не меняет tier (275-277, 296-299); backoff 0.5→5 bounded (251-254); авто-resume через `append` (143-144); force минует cooldown намеренно (147-149) — всё покрыто 14 тестами ✓.
- **A18**: photos.py — устойчивый `telegram_file_id` + metadata_json, `data_base64` отбрасывается при персисте (`_PART_COLUMNS`, messages.py:11), rehydrate по требованию; ad-hoc `part.data_base64` на MessagePart валиден (нет `__slots__`, колонкой не является); размер проверяется до и после скачивания; ошибки скачивания → дружелюбное сообщение ✓.
- **A11 (рабочая часть)**: cancel во время стрима гасится в `_consume` (911-912), partial возвращается, run → cancelled, usage сохраняется (B2) ✓ — репро CASE 2. Pop в finally (488) + A05 partial unique index + стартовый `abort_stale` (A34) как страховка рестарта ✓.
- **A39**: `_assistant_tool_message(round_calls, outcome.text)` — полный assistant turn; сверх `max_tool_calls_per_round` вызовы не попадают в историю ✓ (тест 329-387).
- **A15**: `exclude_message_id` фильтрует только что записанное сообщение ✓ (тест 393-471).
- **Ошибки/секреты**: `user_error_message` — фиксированные строки + `model_display` из registry; ключи/URL/стек в пользователя не утекают; `ForbiddenError` падает в generic — ок. Raw-исключения — только в лог.
- **Конкурентность апдейтов**: aiogram `start_polling(handle_as_tasks=True)` (dispatcher.py:359, 399-407) — stop-апдейт обрабатывается параллельно генерации; design-допущение chat.py:29-31 валидно. `ToolRunner._invoke` `except TimeoutError` ловит `asyncio.TimeoutError` на Python 3.14 (алиас builtin — проверено) ✓.
- `run.id` после закрытия сессии (`_prepare`, 1114-1117) — `expire_on_commit=False` (session.py:21) → DetachedInstanceError нет ✓. `_attempt_fields`: `attempt_ids` — `str(UUID-колонки)` → `uuid.UUID(...)` валиден ✓ (pool.py:316, models/gemini.py:23).
- `new_draft_id` (draft.py:57-63): положительный int63, ≠0, уникален ✓.

## Покрытие тестами: прод-пути, которые мокается-но-не-проверяется / не покрыты

- `GenerationService.generate()` **не вызывается ни одним тестом** (rg `\.generate\(` по tests/ — 0 совпадений): регистрация в реестре, busy-отказ, стартовый flush, `finally`-pop, связка loop→finalize→save — не покрыто. Все генерационные тесты бьют в приватные `_stream_loop/_consume/_save_*/_build_context` с фейками.
- Хендлеры команд (`cmd_start/help/new/chats/settings/admin`, `open_chat`, `_send_chats_list`) — не покрыты вовсе (test_bot_helpers тестирует только чистые хелперы) → находки 2-3 структурно невидимы для suite.
- `_prepare` полностью не покрыт: busy, A25-отказ по saved_model, per-user лимиты, IntegrityError-гонка, `_rehydrate_images`.
- photos.py: покрыт happy-path (file_id/metadata/caption); не покрыты oversize, ошибка скачивания, пустые данные.
- stop.py: покрыт прямой вызов `on_generation_stopped` с фейком; реальная регистрация observer'а и поля `MessageGenerationStopped` проверены мной вручную (OK).
- dispatcher.py: `create_dispatcher` покрыт; `create_bot` (proxy-сессия), `setup_bot_commands` — нет.
- DraftStreamer: не покрыты транзиент tier2→tier3 (находка 5) и пустой первый flush (находка 6).
- Интеграционный reaudit-тест A20 (251-323) отменяет через `cancellation.set()` внутри стрима без `task.cancel()` — реальный путь `task.cancel` (registry.stop) не моделируется ни одним тестом (находки 1/4).

## Итог

Рантайм-P0 класса «ContextBuilder» в scope не найден: все имена/методы существуют (503 теста тому не противоречат, но и не гарантируют — главные дыры именно там, куда тесты не смотрят). Ключевые прод-риски: A11-остаток (Stop в tool-раунде теряет partial и оставляет run `running`) и Telegram-400 на user-контенте в `/chats`/`open_chat`. Фиксы требует координации с lead (generation.py — его зона; commands.py — зона telegram-агента).
