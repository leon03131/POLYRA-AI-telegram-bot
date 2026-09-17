# Telegram Bot API — vendor doc

- **Дата проверки:** 2026-09-18
- **Актуальная версия API:** Bot API **10.3** (2026-08-24)
- **Источники:** core.telegram.org/bots/api (полный текст), /bots/api-changelog, /bots/features.
  Полный research-отчёт: `.agents/reports/telegram.md`.

## Что реально использует проект

| Возможность | Версия | Использование |
|---|---|---|
| `sendRichMessageDraft` | 10.1+ (can_stop с 10.3) | Основной streaming final answer (только text/markdown-контент) |
| `sendMessageDraft` | 9.3+ (всем ботам с 9.5) | Fallback-стриминг (plain text) |
| `can_stop=true` + update `stopped_message_generation` | 10.3 | Кнопка Stop, отмена генерации |
| `sendRichMessage` / `sendMessage` | 10.1 / базовый | Финализация драфта в персистентное сообщение |
| `editMessageText(rich_message=...)` | 10.1 | Tier-3 fallback (throttled edit) |
| `getUpdates` (long polling) | базовый | Основной режим получения updates (ADR-001) |
| `setChatMenuButton` (MenuButtonWebApp) | 6.0+ | Кнопка меню «⚙️ Настройки» |
| `InlineKeyboardButton.web_app` / `KeyboardButton.web_app` | 6.x | Запуск Mini App (inline — для owner admin, reply — не используем) |
| Фото: `getFile` + download | базовый | Image input (Telegram file_id храним, bytes не храним) |

## Ключевые контракты (подтверждены первоисточником)

- `sendMessageDraft(chat_id:int, draft_id:int, text?:0–4096, parse_mode?, entities?, can_stop?, keep_on_stop?)` → `True`.
- `sendRichMessageDraft(chat_id:int, draft_id:int, rich_message:InputRichMessage, can_stop?, keep_on_stop?)` → `True`.
- **Только приватные чаты.** `draft_id != 0`; тот же `draft_id` = анимированное обновление.
- Драфт эфемерен (~30 с превью); финал **обязательно** отдельным `sendMessage`/`sendRichMessage`. Метода удаления драфта нет.
- `InputRichMessage`: ровно одно из `markdown` | `html` | `blocks`. Лимиты rich: 32768 символов, 500 блоков, 16 уровней вложенности.
- `MessageGenerationStopped = {chat, message_thread_id?, draft_id}` → корреляция генерации по `(chat_id, draft_id)`.
- Пустой `text` в sendMessageDraft = плейсхолдер «Thinking…» (Bot API 10.0+).
- `keep_on_stop` не гарантирует сохранение — partial answer сохраняем отдельным сообщением (если нужно).

## Противоречия/пробелы документации

1. Rate limits обновлений драфта **не документированы** → свой throttle (~1 обн./сек) + обработка 429 `retry_after`.
2. «30 seconds» и «short time» (keep_on_stop) не уточнены → не полагаться, финализировать явно.
3. Partial markdown в драфте может дать BadRequest (незакрытый fence/таблица) → санитизация снапшота или fallback на plain.

## Решение проекта

Fallback-цепочка стриминга: **Rich Draft → Message Draft → throttled edit**; финал — `sendRichMessage` (или `sendMessage`).
Отмена: хендлер `stopped_message_generation` → cancel по `(chat_id, draft_id)`. Подробности: DECISIONS.md ADR-009.
