# polyra-telegram — wave2 (A02, A18, A20, A21, A22)

Дата: 2026-09-24. Исполнитель: субагент polyra-telegram.
Scope соблюдён: только `app/bot/**` + три файла тестов + этот отчёт.
`app/main.py`, `app/services/**`, `app/api/**`, `app/llm/**` не тронуты.

## Файлы

- `app/bot/routers/commands.py` — A02: helper `build_admin_url()` + `/admin` на hash-route.
- `app/bot/dispatcher.py` — A02: `setup_menu_button(bot, settings)`.
- `app/bot/routers/photos.py` — A18: `telegram_file_id` + `metadata_json` в image part.
- `app/bot/streaming/draft.py` — A20/A21/A22: parse_mode=None, not-modified=успех, throttle v2.
- `tests/unit/test_draft_streamer.py` — +9 тестов (A20×2, A21×3, A22×4).
- `tests/unit/test_bot_helpers.py` — +2 теста (build_admin_url).
- `tests/unit/test_bot_wiring.py` — +3 теста (A18 photos handler, A02 menu button ×2).

## Решения

### A02 — /admin 404 + menu button
- `build_admin_url(app_base_url) -> "…/#/admin"` (rstrip('/') — защита от `//`), используется в `cmd_admin`. `/settings` и `/start` ведут на корень Mini App — без изменений (корректно для HashRouter).
- Новая `dispatcher.setup_menu_button(bot, settings)`: глобальный `set_chat_menu_button` БЕЗ `chat_id`, `MenuButtonWebApp(text="⚙️ Настройки", web_app=app_base_url)`, try/except `TelegramAPIError` → warning, запуск не прерывается.
- Per-chat кнопка в `cmd_start` оставлена (best-effort для клиентов, где глобальная ещё/уже не применилась; конфликта нет — per-chat переопределяет глобальную с тем же URL).

### A18 — фото сохраняет file_id
- Image part теперь: `type, telegram_file_id, mime_type, data_base64, metadata_json{file_unique_id, width, height, file_size}` (порядок/ключи по контракту).
- Совместимость проверена: `MessageRepository._PART_COLUMNS = {type, text, telegram_file_id, mime_type, metadata_json}` — `telegram_file_id`/`metadata_json` сохраняются в колонки, `data_base64` отбрасывается (bytes в БД не храним, rehydration уже есть в `generation._rehydrate`). Согласуется с `chats.save_user_photo_message`.

### A20 — parse_mode=None в plain-тиерах
- Явный `parse_mode=None` во всех plain-text вызовах: `send_message_draft` (tier2), `send_message`/`edit_message_text` (tier3 + fallback в `finalize`/`_finalize_existing_message` + `fail`). Rich-вызовы (`send_rich_message_draft`, `send_rich_message` через `InputRichMessage(markdown=…)`) не тронуты — там parse_mode неприменим.

### A21 — not-modified как успех
- Helper `_is_not_modified(exc)`: `TelegramBadRequest` + подстрока «not modified» (case-insensitive).
- `_flush_message_edit`: not-modified → `_mark_flushed()` (успех, без fallback и без backoff). Прочий BadRequest → backoff, НЕ успех (покрыто тестом).
- `_finalize_existing_message`: not-modified на финальном edit → успех без дублей (`edited=None`, хвост `parts[1:]`, если есть, всё равно досылается). Прочие ошибки — прежний fallback на свежие сообщения.

### A22 — throttle: первый упавший flush не глушит поток
- Новое состояние: `_next_attempt_at` (cooldown) + `_backoff` (0.5→1→2→4→5 с, bounded `_BACKOFF_MAX=5.0`). `_last_flush` = последний УСПЕШНЫЙ flush (контракт §8 «last_success»).
- `_due()`: cooldown не истёк → False (коалесцирование и для `flush()`, и для авто-flush из `append`); `force=True` шлёт всегда.
- Успех: `_last_flush=now`, `_next_attempt_at=None`, backoff сброшен.
- `TelegramRetryAfter` (поле `retry_after`): `_next_attempt_at = now + retry_after`, tier НЕ меняется — в т.ч. на tier2 (ранее 429 на `send_message_draft` давал ложный downgrade в tier3!).
- Прочая `TelegramAPIError`: `_next_attempt_at = now + min(backoff, 5.0)`, tier не меняется.
- Корень «тишины после первого упавшего force-flush»: `append` авто-флашил только при `_last_flush is not None`. Теперь guard — `( _last_flush is not None or _next_attempt_at is not None )`: первая попытка (даже неудачная) «открывает» авто-flush, cooldown гасит flood. Инвариант «append не шлёт до interval» для свежего стримера сохранён.

## Тесты / проверки

pytest НЕ запускал (по инструкции). Вместо этого:
- `py_compile` всех изменённых файлов — OK.
- Inline-драйвер на venv-python (без сети/БД, FakeBot из тестового модуля): 12 сценариев стримера, включая ВСЕ существующие тест-кейсы логики (append-no-send, throttle, downgrade-цепочка, retry, finalize tier3 edit/split) + новые (retry_after cooldown/tier unchanged, backoff growth 0.5→5.0 bounded, auto-resume через append, parse_mode=None ×3, not-modified успех, other-BadRequest ≠ успех, finalize not-modified) — ALL OK.
- Inline-драйвер photos handler: keys part'а в точности `[type, telegram_file_id, mime_type, data_base64, metadata_json]`, metadata значения совпали — OK.
- Inline-драйвер `setup_menu_button`: глобальный вызов без chat_id, текст/URL верны, `TelegramAPIError` проглочена — OK.
- `ruff check` + `ruff format --check` на scope — OK. `mypy` по scope — 0 ошибок (остаточные ошибки в `app/llm/gemini/*` — параллельные правки другого субагента, видны в `git status`, не мои).

## Что нужно от lead

1. **Wiring menu button (A02):** в `app/main.py` после `await setup_bot_commands(bot)` (строка ~124) добавить:
   ```python
   await setup_menu_button(bot, settings)
   ```
   и импорт `setup_menu_button` из `app.bot.dispatcher`. Сам `main.py` вне моего scope — не трогал.
2. Конфликтный риск: `app/llm/gemini/quota.py` / `store_db.py` сейчас изменены параллельно и дают mypy-ошибки — на integration-этапе убедиться, что владелец доводит их до зелёного.

## Blockers

Нет.
