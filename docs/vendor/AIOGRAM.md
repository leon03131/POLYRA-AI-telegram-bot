# aiogram — vendor doc

- **Дата проверки:** 2026-09-18
- **Версия:** aiogram **3.31.0** (PyPI, 2026-08-25; «Full support of Bot API 10.3», PR #1888). Python ≥3.9 (мы на 3.12+). Зависимость pydantic `>=2.4.1,<2.14` — учесть при резолве версий.
- **Источники:** GitHub releases, PyPI, raw-код тега v3.31.0 и dev-3.x, docs.aiogram.dev/en/dev-3.x.
  Полный отчёт: `.agents/reports/telegram.md` (таблица покрытия).

## Покрытие нужных фич (проверено по коду)

| Фича | Статус в 3.31.0 |
|---|---|
| `bot.send_message_draft` (с can_stop/keep_on_stop) | ✅ |
| `bot.send_rich_message_draft` | ✅ |
| `bot.send_rich_message` | ✅ |
| Типы RichMessage/RichText*/RichBlock*/InputRich* | ✅ полный набор |
| `Update.stopped_message_generation`, тип MessageGenerationStopped | ✅ |
| Observer `router.stopped_message_generation` | ✅ |
| `edit_message_text(rich_message=...)` | ✅ |
| `bot.set_chat_menu_button(MenuButtonWebApp)` | ✅ |
| `aiogram.utils.web_app.check_webapp_signature` / `safe_parse_webapp_init_data` | ✅ (алгоритм HMAC совпадает с доками; auth_date проверяем сами) |

## Что использует проект

- Long polling: `Dispatcher.start_polling(bot)` (allowed_updates: при регистрации хендлера
  `stopped_message_generation` aiogram сам добавит тип апдейта).
- `DefaultBotProperties(parse_mode=ParseMode.HTML)` — только для простых service-сообщений;
  стрим и финалы — через Rich Messages (markdown).
- Middleware на `update`/`message` уровне для access control (numeric user_id).
- Официальный пример web_app (aiohttp + routes + `safe_parse_webapp_init_data`) изучен;
  наш backend — FastAPI вместо aiohttp, алгоритм валидации тот же.

## Противоречия/пробелы

- Доки живут под `/dev-3.x/`, но помечены как «3.31.0 documentation»; ключевые методы сверены с git-тегом v3.31.0 — совпадают.
- Raw Bot API вызовы не нужны (запасной механизм кастомного `TelegramMethod` описан в отчёте).

## Решение проекта

Пиновать `aiogram>=3.31,<4.0`. Streaming через нативные draft-методы aiogram 3.31.
Следить за 3.32+ (changelog) при обновлениях.
