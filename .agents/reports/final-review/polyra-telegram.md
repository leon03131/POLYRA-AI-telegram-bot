# Final review — профиль polyra-telegram (A: Telegram / streaming / Stop)

- Дата: 2026-09-25
- Аудитор: субагент polyra-telegram (READ-ONLY аудит)
- Scope: `app/bot/**`, связанные части `app/services/generation.py` (финализация, Stop, streaming consumer), `tests/unit/test_bot_helpers.py`, `tests/unit/test_bot_wiring.py`, `tests/unit/test_draft_streamer.py`; справочно `app/api/app.py`, `app/main.py`, `miniapp/src/App.tsx`, `app/llm/providers/*`, `app/context/builder.py`.
- Исходные требования: `docs/PROMT.md` §15/16/33/44; фикс-лист `docs/POLYRA_KIMI_HANDOFF_V2/KIMI_FIX_PROMPT.md` (A02, A11, A18, A20, A21, A22).

## Runtime-проверки (offline, безопасные)

| Проверка | Результат |
|---|---|
| `.venv\Scripts\python -m pytest tests/unit/test_draft_streamer.py tests/unit/test_bot_helpers.py tests/unit/test_bot_wiring.py -q` | **40 passed in 2.88s, exit code 0** |
| `.venv\Scripts\python -m pytest tests/unit/test_generation.py -q` (смежный, для Stop/_consume) | **32 passed, exit code 0** |
| Offline ASGI-проверка `create_app` + StaticFiles(index.html): `GET /` → 200, `GET /admin` → 404, `GET /health` → 200 | подтверждает: hash-URL не требует SPA fallback, прямой `/admin` даёт 404 |
| `_tail('x'*40000, RICH_LIMIT=32768)` → длина **32770 > 32768** | подтверждает превышение лимита rich-сообщения в финальной отправке (A21) |
| `aiogram 3.31.0`: `MessageGenerationStopped`, `Router.stopped_message_generation` существуют | API-контракт stop-роутера валиден |
| `miniapp/dist` существует (`Test-Path` → True) | StaticFiles-маунт активен в проде |

Живые Telegram API / запуск приложения / сеть не выполнялись (запрещено) — runtime-поведение Bot API 10.x (drafts, rich limits, retry_after) оценено статически + по vendored docs (`docs/vendor/TELEGRAM_BOT_API.md`: rich 32768 симв.; draft text 0–4096; `MessageGenerationStopped = {chat, draft_id}`).

---

## A02 — /admin → 404; menu button при startup

**Статус: FIXED_VERIFIED** (с маленькой оговоркой о прямых deep-link).

Доказательства:
- `app/bot/routers/commands.py:31-33` — `build_admin_url` → `f"{app_base_url.rstrip('/')}/#/admin"` (hash-маршрут под HashRouter). Единственное место формирования admin-URL.
- `miniapp/src/App.tsx:153` — `<HashRouter>`; хеш-фрагмент никогда не уходит на сервер, запрашивается только `/`.
- `app/api/app.py:87-89` — `app.mount("/", StaticFiles(directory=..., html=True))` — `/` отдаёт index.html (проверено offline: 200).
- `app/main.py:124-125` — при startup: `await setup_bot_commands(bot)` **и** `await setup_menu_button(bot, settings)  # A02: глобальная кнопка Mini App`.
- `app/bot/dispatcher.py:74-89` — `setup_menu_button` ставит глобальную `MenuButtonWebApp` **без chat_id** (действует на все чаты), ошибка Telegram API не роняет startup (warning).
- `app/bot/routers/commands.py:59-68` — per-chat кнопка в `/start` сохранена как best-effort.

Тесты: `tests/unit/test_bot_helpers.py:14-20` (hash URL, трейлинг-слэш); `tests/unit/test_bot_wiring.py:173-195` (глобальная кнопка без chat_id, тип `MenuButtonWebApp`, подавление `TelegramAPIError`). Тесты мокают Bot, но ассертят фактические kwargs вызова — поведение, а не сам мок; прод-колл-сайт подтверждён в `main.py`.

Проблемы/оговорки:
- Прямой заход на `/admin` (закладка, внешний линк) всё ещё 404 — SPA fallback не добавлен. По KIMI-фиксу это допустимо («Для HashRouter использовать /#/admin»), ни один код-путь такого URL не генерирует. При желании — добавить catch-all fallback на index.html.
- `AiohttpSession` без proxy не создаётся вовсе (dispatcher.py:37) — корректно.

ТЗ §15/§49: команды и menu button соответствуют (см. секцию ТЗ ниже).

---

## A11 — Stop не гарантирует немедленного прерывания HTTP stream и tools

**Статус: FIXED_PARTIAL.**

Исправлено (реально в production-пути):
- `app/services/generation.py:121-131` — `GenerationRegistry.stop`: `gen.cancellation.set()` **и `gen.task.cancel()`** — немедленное прерывание, в т.ч. зависшего чтения HTTP:
  ```python
  gen.cancellation.set()
  gen.task.cancel()
  ```
- Проверка отмены **перед каждым** tool side effect: `generation.py:712-715` — `for call in round_calls: if cancellation.is_set(): return True` (перед каждым вызовом, не после пачки; A39-лимит batch).
- Провайдеры проверяют cancellation на каждой SSE-строке и закрывают response: `app/llm/providers/gemini.py:343-350` (`_lines_with_cancellation`), `app/llm/providers/alibaba.py:397-406` (`_cancellable_lines` + `response.aclose()`). `CancelledError` нигде в провайдерах не глотается (проверено: `except NetworkError`/`except httpx.TimeoutException` его не ловят — он BaseException).
- `_consume` ловит `asyncio.CancelledError` (generation.py:856-857), помечает `cancelled=True` и возвращает накопленный partial; в `finally` стрим всегда закрывается (`aclose`, generation.py:858-864).
- Баундид-ожидания: инструменты — `asyncio.wait_for(..., timeout=tool.timeout)` (`app/llm/tools/runner.py:126`, таймауты 10–40 с); общий deadline генерации `max_generation_seconds=240` (`config.py:57`, `generation.py:618`).
- Роутер stop-апдейтов: `app/bot/routers/stop.py:19-31` — `@router.stopped_message_generation()` → `registry.stop(event.chat.id, event.draft_id)`; unknown → noop. aiogram 3.31 поддерживает observer (проверено импортом).

НЕ исправлено / остаточные дефекты (это ядро «фикс проходит тест, но ломается в проде»):

1. **Идемпотентный finalizer при CancelledError отсутствует.** `asyncio.CancelledError` ловится ТОЛЬКО в `_consume` (generation.py:856). Греп по `app/**`: `CancelledError` встречается лишь в `generation.py:856` и `gemini/pool.py:280` (проброс). Если `task.cancel()` «приземлится» не в стриме, а:
   - во время ожидания tool (`runner.py:126` → `_invoke` ловит только `TimeoutError|ToolExecutionError|Exception`; CancelledError = BaseException — проходит насквозь),
   - во время стартового `streamer.flush(force=True)` (generation.py:408),
   - во время `_save_cancelled`/`finalize`/`_save_completed`,
   то CancelledError поднимается сквозь `_stream_loop` (`except (ProviderError, PoolExhaustedError)` / `except Exception` — generation.py:636-645, его не ловят) до `generate()`, где есть только `finally: pop` (generation.py:446-447). Итог: **partial НЕ сохраняется ни в Telegram, ни в БД; `generation_run` остаётся в статусе `running`** до следующего рестарта (`abort_stale`, main.py:114-119). KIMI-требование «Сделать идемпотентный finalizer, сохраняющий partial и cancelled status даже при CancelledError» — не реализовано (нет `except asyncio.CancelledError`/`asyncio.shield` вокруг финализации). Практический сценарий: Stop нажат во время web_search-раунда — частый кейс.
2. **Повторный Stop убивает финализацию.** Каждый `stopped_message_generation` → `registry.stop()` → новый `task.cancel()`. Вторая отмена, пришедшая во время `_save_cancelled`, рвёт `await bot.send_message` / DB-коммит (там ловится только `TelegramAPIError`/`IntegrityError`, generation.py:1147-1150, 1152-1177).
3. **Deadline зависшего чтения.** httpx `read=300.0` в обоих провайдерах (`gemini.py:303`, `alibaba.py:344`) не уменьшен; общий deadline 240 с проверяется только **между** раундами (`generation.py:620-623`), поэтому «тихий» зависший стрим без Stop живёт до 300 с (плюс retry в alibaba). Прерывание по Stop теперь есть (task.cancel), но «short deadline без ожидания read timeout» из acceptance-теста достигается только при наличии Stop.
4. **Тесты не ассертят task.cancel.** `tests/unit/test_bot_wiring.py:64-74` (`test_stop_handler_cancels_registered_generation`) и `tests/unit/test_generation.py:70-90` проверяют только `cancel.is_set()` (событие); `task` в wiring-тесте в конце явно до-канселяется руками (строка 74). Тест проходил бы и без `task.cancel()` в `registry.stop` — ключевое поведение A11 не покрыто.

---

## A18 (bot-часть) — photo router: telegram_file_id; image-capable guard

**Статус: FIXED_PARTIAL** (роутер и guard — FIXED; rehydration — только в одной ветке).

Исправлено:
- `app/bot/routers/photos.py:59-74` — image part несёт **`telegram_file_id`** (строка 64), `mime_type`, `data_base64` (runtime-only), `metadata_json` (file_unique_id/width/height/file_size); caption — вторым part. Размер проверяется и до (`file_size`), и после скачивания (строки 41, 56-58). Ошибки скачивания → понятный текст пользователю.
- Персистенс: `app/db/repositories/messages.py:11` — `_PART_COLUMNS = {"type","text","telegram_file_id","mime_type","metadata_json"}` (base64 отбрасывается by design); колонка `telegram_file_id` — `app/db/models/message.py:58`, миграция `0002_chat_core.py:94`.
- Guard: `app/services/generation.py:475-486` — при `has_image and not model_def.supports_images` отказ с понятным текстом и **списком image-capable моделей** из `registry.filter_by_permissions(...)` (registry.py:43-51; при пустом списке — «нет доступных»). Проверяется в `_prepare` до запуска (строки 974-983).
- Тесты: `tests/unit/test_bot_wiring.py:120-156` — реальные входы хендлера (fake Bot/Message), ассертится структура parts, `telegram_file_id`, metadata, b64 — покрывает production-путь роутера (хендлер — чистая оркестрация).

НЕ исправлено:
- **Rehydration только при наличии compaction-summary.** `app/services/generation.py:538-543`:
  ```python
  summary_row = await ChatSummaryRepository(session).get_for_chat(chat_id)
  if summary_row is not None:
      history = await messages_repo.list_all(chat_id)
      if model_def.supports_images:
          await self._rehydrate_images(history, bot)
  ```
  Если summary нет (типичный короткий/новый чат — а именно там follow-up по фото), `_rehydrate_images` **не вызывается**, parts истории без `data_base64`, и `app/context/builder.py:58-67` превращает их в текстовый плейсхолдер `"[изображение]"`. Acceptance-сценарий KIMI «перезапуск → follow-up получает ImagePart» выполняется только у чатов со сводкой. Это паттерн «исправлено в одной ветке, в другой отсутствует».
- Тестов на `_rehydrate_images`/плейсхолдер-ветку нет (греп `rehydrate` в tests — 0 попаданий).
- Наблюдение: `ChatService.save_user_photo_message` (`app/services/chats.py:89-117`) — мёртвый код, никем не вызывается (роутер формирует parts сам).

---

## A20 — raw LLM text с глобальным HTML parse_mode

**Статус: FIXED_PARTIAL** — паттерн «фикс в одном файле, прод-баг в другом, тесты мокают и не видят».

Исправлено:
- Все plain-вызовы в `app/bot/streaming/draft.py` идут с **`parse_mode=None`**: `send_message_draft` (289), `send_message` в finalize (177), `edit_message_text` (199), tail-сообщения (206, 213), `fail()` (223), tier-3 send/edit (309, 316). Docstring модуля (строки 20-23) прямо фиксирует контракт A20.
- Тесты: `tests/unit/test_draft_streamer.py:306-341` — `test_plain_tiers_pass_parse_mode_none` и `test_finalize_plain_fallback_passes_parse_mode_none` ассертят `parse_mode is None` во всех трёх tiers. Тесты честные: вызывают реальный `DraftStreamer` (FakeBot — только транспорт).
- Служебные `bot.send_message`/`message.answer` в generation.py/commands.py — статические тексты без HTML-спецсимволов (лимиты, отказы, welcome) — безопасны при default HTML.

НЕ исправлено (найден реальный прод-баг):
- **`app/services/generation.py:1148`** — отправка Stop-partial **без `parse_mode=None`**:
  ```python
  text = partial if len(partial) <= MESSAGE_LIMIT else partial[: MESSAGE_LIMIT - 1] + "…"
  try:
      await bot.send_message(tg_chat_id, text)
  ```
  У бота default `parse_mode=HTML` (`app/bot/dispatcher.py:40`). `text` — сырой partial LLM: незакрытый `<tag>`, `<div>` в коде и т.п. → `TelegramBadRequest` → ловится `except TelegramAPIError` (1149-1150) → `logger.exception` → **пользователь не получает partial вовсе** (хотя он сохраняется в БД). Именно этот call был в исходном аудите A20 (`generation.py:880-885` «отправка partial не задают parse_mode=None») — правка выполнена только в draft.py, в `_save_cancelled` — нет. Тест `tests/integration/test_fix_v2_usage_ledger.py:320-325` мокает `_FakeBot.send_message(chat_id, text)` без всякого parse-контракта — баг невидим для тестов.
- Мелочь: `app/bot/routers/commands.py:140` — `callback.message.answer(f"Открыт чат: {chat.title …}")` — заголовок чата генерится LLM (`titles.py:34-47` не вырезает `<>`), при default HTML возможен BadRequest. Исходный аудит упоминал «неэкранированное название чата» — не закрыто.

---

## A21 — обрезка финального/partial, дубль при not-modified

**Статус: FIXED_PARTIAL** (1 из 3 под-пунктов полностью).

Исправлено:
- **«message is not modified» = идемпотентный успех.** `app/bot/streaming/draft.py:52-54` `_is_not_modified`; в tier-3 flush (319-323) — `_mark_flushed()` без fallback-сообщения; в tier-3 finalize (201-204) — `edited = None`, дублей нет; общий BadRequest-путь НЕ считается успехом (324-326). Тесты: `test_draft_streamer.py:344-398` — три теста, включая «not modified → успех без новых сообщений» и «другой BadRequest → backoff, не успех». Проверки реальные (реальный streamer + FakeBot с инжектируемыми исключениями TelegramBadRequest).
- Fallback-разбиение при отказе rich: `_split_text(text, 4096)` (draft.py:176, 192, 212) — полный текст, порядок сохранён; тест `test_finalize_fallback_splits_long_text` (186-199).

НЕ исправлено:
- **Финальная rich-отправка по-прежнему через `_tail`:** `draft.py:168-171` — `send_rich_message(..., InputRichMessage(markdown=_tail(text, RICH_LIMIT)))`. Требование KIMI: «Tail допустим только для временного draft. Final … разбивать без потерь». Подтверждено расчётом: `_tail(text, 32768)` → `«…\n» + text[-32768:]` = **32770 символов > лимит 32768** (vendored Bot API: rich ≤ 32768). То есть ответ длиннее 32 К: гарантированный `TelegramBadRequest` → тихий downgrade на plain-разбиение (текст не теряется, но rich-форматирование теряется); если бы лимит был мягче — потеря начала ответа. Тестов на финал > 32768 нет.
- **Stop-partial обрезан первым фрагментом:** `generation.py:1146` — `partial[: MESSAGE_LIMIT - 1] + "…"` — всё после первых 4096 символов **теряется для пользователя** (в БД сохраняется полный partial, в Telegram — обрезка: расхождение БД↔Telegram). Требование: «cancelled partial разбивать без потерь». Не сделано; тестов нет.

---

## A22 — throttle / RetryAfter / backoff

**Статус: FIXED_VERIFIED.**

Доказательства (`app/bot/streaming/draft.py`):
- Возобновление после первого упавшего flush: `append` (141) — условие `(self._last_flush is not None or self._next_attempt_at is not None) and self._due()`: после первой ошибки выставляется `_next_attempt_at`, авто-поток не умирает; успех сбрасывает cooldown/backoff (`_mark_flushed`, 236-240).
- `TelegramRetryAfter` обработан во всех трёх tiers: tier1 (273-275), tier2 (294-297), tier3 (327-329) — `_schedule_retry_after(exc.retry_after)` (242-247), **tier НЕ меняется** (комментарий «Временный rate limit — НЕ downgrade»).
- Bounded экспоненциальный backoff: `_schedule_backoff` (249-252), `_BACKOFF_START=0.5`, `_BACKOFF_MAX=5.0` (47-49); коалесцирование в `_due()` (227-234).
- Cooldown обходится только force-flush (144-146) — стартовый плейсхолдер и финализация не подвисают.

Тесты (`tests/unit/test_draft_streamer.py`) — поведение реального стримера, обширно и по делу:
- 401-418: retry_after задерживает ретрай тем же tier; 421-437: 429 на tier2 не даёт downgrade в tier3; 440-463: первый упавший flush → backoff → авто-возобновление через `append`, без flood; 466-476: рост backoff 0.5→1→2→4→5→5 (bounded); 478-488: force игнорирует cooldown.
- Граничные: tail-префикс для draft (250-261), tier-3 финал без дублей (264-303), санитайзер fence (210-216).

Мелкие оговорки (не блокеры):
- `TelegramRetryAfter` наследует `TelegramNetworkError`? Нет — в aiogram это отдельный класс; порядок except-ветвей корректен (RetryAfter проверяется раньше TelegramAPIError).
- jitter отсутствует (KIMI упоминал «backoff/jitter» опционально); функционально не критично.

---

## Соответствие исходному ТЗ (docs/PROMT.md)

### §15 TELEGRAM BOT — СООТВЕТСТВУЕТ
- Команды `/start /new /chats /settings /help` — `dispatcher.py:26-32` (BOT_COMMANDS) + хендлеры `commands.py:51-157`; `/help` перечисляет команды (не содержит /admin — ок).
- `/admin` только owner: `commands.py:160-165` — сравнение `message.from_user.id != settings.owner_telegram_id` (numeric id; A04-совместимо, флаг БД не используется).
- Menu button Mini App: при startup (main.py:125, глобальная) и на `/start` (per-chat, best-effort).
- Ввод text (`chat.py:19-41`) и photo+caption (`photos.py:27-83`). PDF/audio/video не поддерживаются — как в ТЗ.

### §16 TELEGRAM STREAMING — СООТВЕТСТВУЕТ В ОСНОВНОМ, см. дефекты A20/A21
- Rich Message Draft (`send_rich_message_draft`, tier 1) → `send_message_draft` (tier 2, пустой text = «Thinking…») → `send_message`+`edit_message_text` (tier 3) — реализовано (draft.py:144-153, 254-332).
- Только финальный ответ: `ReasoningDelta` не стримится (только счётчик, generation.py:292-295).
- `can_stop=True` — draft.py:261, 290 (+`keep_on_stop=True`).
- `stopped_message_generation` обрабатывается (stop.py). Но ТЗ-шаги 5 («если уже есть видимый partial — сохранить как обычное сообщение») и 6 («generation_run пометить cancelled») выполняются **только** если отмена поймалась в `_consume` (см. A11 п.1) — при отмене в tool-раунде partial теряется, run зависает в `running`.
- Throttling: не каждый token — `throttle_interval=1.0` (append коалесцирует).
- После окончания: draft → persistent rich message (`finalize`), tier-3 — edit существующего (без дублей).

### §33 SEARCH CITATIONS — СООТВЕТСТВУЕТ
- `generation.py:438-443`: при `show_sources=True` (default, config.py:37; выключается SHOW_SOURCES=0) и наличии web_search-результатов к финальному тексту добавляется суффикс.
- Формат: `build_sources_suffix` (256-261) → `**Источники:**\n1. [Title](url)` — соответствует «Источники: 1. Title…», кликабельные ссылки в rich (markdown). В plain-tier — некликабельный markdown-текст, что ТЗ допускает («при Rich Messages можно»). Dedup по URL, только не-ошибочные web_search вызовы (collect_sources, 242-253). `show_sources` — не скрытый env-флаг: включён по умолчанию, в .env.example отсутствует (не требуется, дефолт True).

### §44 ERROR UX — СООТВЕТСТВУЕТ
- `user_error_message` (generation.py:171-187): русские тексты по категориям ошибок, без traceback/URL/ключей.
- Глобальный errors-middleware (`app/bot/middleware/errors.py:13-29`): лог + «⚠️ Произошла внутренняя ошибка. Попробуйте позже.», без техдеталей.
- `DraftStreamer.fail(user_message)` — parse_mode=None, ошибки гасятся с логом. Raw traceback пользователю не уходит ни в одном проверенном пути.

---

## Сводная таблица

| Пункт | Статус | Ключевые файлы:строки | Главная проблема |
|---|---|---|---|
| A02 | FIXED_VERIFIED | commands.py:31-33; main.py:125; dispatcher.py:74-89; App.tsx:153 | прямой deep-link /admin → 404 (вне контракта, спека-совместимо) |
| A11 | FIXED_PARTIAL | generation.py:121-131,712-715,856; gemini.py:343-350; alibaba.py:397-406 | CancelledError ловится только в _consume: Stop в tool-раунде → partial потерян, run зависает «running»; finalizer не идемпотентен к повторной отмене; тесты не ассертят task.cancel |
| A18 | FIXED_PARTIAL | photos.py:59-74; generation.py:475-486,538-543,566-589; builder.py:58-67 | rehydration только при наличии summary: follow-up без сводки получает «[изображение]» вместо ImagePart |
| A20 | FIXED_PARTIAL | draft.py:177,199,206,213,223,289,309,316; generation.py:1148; dispatcher.py:40 | Stop-partial отправляется без parse_mode=None при default HTML → BadRequest на незакрытом теге → пользователь не получает partial (моки в тестах это скрывают) |
| A21 | FIXED_PARTIAL | draft.py:52-54,168-171,319-326; generation.py:1146 | финальный rich всё ещё _tail(RICH_LIMIT) → 32770>32768 → downgrade/truncation; Stop-partial обрезается первым 4096-фрагментом; not-modified — исправлено |
| A22 | FIXED_VERIFIED | draft.py:141,227-252,273-329 | — (jitter отсутствует, не критично) |

## Паттерн «фикс есть в тестах, но ломается в проде» — найденные экземпляры

1. **A20/A21 Stop-partial (`generation.py:1146-1150`)**: единственное место A20-фикса вне draft.py не закрыто — `bot.send_message` без `parse_mode=None` + обрезка первым фрагментом; тесты `test_fix_v2_usage_ledger.py` мокают Bot так, что ошибка невозможна.
2. **A11 task.cancel**: production-код отменяет task, но оба stop-теста (`test_bot_wiring.py:64-74`, `test_generation.py:70-90`) ассертят только event — регрессия «удалить task.cancel()» не была бы поймана.
3. **A18 rehydration**: реализовано и работает, но вызывается лишь в summary-ветке `_build_context`; acceptance-сценарий (follow-up с ImagePart после рестарта) не покрыт тестом и не проходит в типовом чате без сводки.
4. **A21 финальный rich**: `_tail` в finalize формально сохранён; тестов на >32768 нет; фактически длинный ответ тихо теряет rich-формат (или начало при мягком лимите).
