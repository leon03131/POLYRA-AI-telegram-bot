# fix-v2 / A (Telegram) — round4-P1 /chats label + round4-P2 open_chat parse_mode

Task: два бага из round4-аудита, HEAD 4925c40.
Scope: `app/bot/routers/commands.py`, `tests/unit/test_bot_helpers.py`. Более ничего не тронуто
(git diff --stat подтверждает только эти 2 файла от меня).

## БАГ 1 (P1): /chats — label кнопки > 64 символов

`chat.title` приходит из Mini App (app/api/routes/chats.py: Field(max_length=256), без
санитизации), плюс маркер «✅ » и суффикс «· дата» → `InlineKeyboardButton.text` превышал
лимит Bot API 1-64 → `TelegramBadRequest` → команда /chats ломалась целиком.

Фикс: чистая функция `_chat_button_label(title, date_str, marker="") -> str`
(app/bot/routers/commands.py:55-69):

- считает итоговую длину с учётом суффикса « · дата» и маркера;
- короткий title — без изменений; длинный — режется с «…», дата/маркер сохраняются целиком;
- `title or "Без названия"` — None/пустой title покрыты заглушкой (константа
  `_NO_TITLE_LABEL`, переиспользована и в open_chat);
- лимит по символам (len), как в Bot API; `rstrip()` перед «…» — без висящего пробела.

`_send_chats_list` (commands.py:132-135) теперь строит label через хелпер:
`_chat_button_label(chat.title, f"{chat.created_at:%d.%m.%Y}", marker)`.

## БАГ 2 (P2): open_chat подтверждение с default parse_mode=HTML

У бота default `parse_mode=HTML` (dispatcher.py:40), а `chat.title` не экранирован →
'<' / незакрытый тег в title давали BadRequest. Фикс — одна строка
(commands.py:159-165): `parse_mode=None` в `callback.message.answer(...)` (по образцу
draft.py, A20). Комментарий со ссылкой на round4-P2.

## Тесты (tests/unit/test_bot_helpers.py)

round4-P1, `_chat_button_label`:
- (a) короткий title — без изменений;
- (b) title 256 симв → len ≤64, endswith «… · 01.02.2026»;
- (c) None и "" → «Без названия»;
- (d) эмодзи-титул («😀🎉🚀»×30) → len ≤64;
- (e) маркер «✅ » учитывается в общем лимите (len ≤64).

round4-P2, `test_open_chat_confirmation_sent_with_parse_mode_none`:
прямой вызов `open_chat` с фейками (паттерн test_bot_wiring: без Telegram API/БД/сети);
`ChatRepository`/`ChatService`/`Message` подменены через `monkeypatch` на глобалах модуля
`app.bot.routers.commands` (isinstance-ветка проходит на фейке). Title «Чат <про> углы».
Assert: `kwargs["parse_mode"] is None`, текст без экранирования, callback.answer отработал.

## Прогоны (реальные, .venv)

| Команда | Результат | Exit |
|---|---|---|
| `.venv\Scripts\python -m pytest tests/unit/test_bot_helpers.py tests/unit/test_bot_wiring.py -q` | 18 passed in 2.82s | 0 |
| `.venv\Scripts\python -m ruff check app/bot tests/unit/test_bot_helpers.py` | All checks passed! | 0 |
| `.venv\Scripts\python -m mypy app` | Success: no issues found in 129 source files | 0 |

## Blockers

Нет. Инварианты соблюдены: model IDs/generation.py/gemini/miniapp не тронуты, секреты не
выводились, БД/permissions/история не сбрасывались, git-операций не проводил (только
read-only `git diff`).
