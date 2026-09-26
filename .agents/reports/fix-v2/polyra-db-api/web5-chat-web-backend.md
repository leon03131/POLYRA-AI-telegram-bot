# web5 — backend веб-чата Mini App (chat_web.py)

Task: SSE-генерация + история сообщений + Stop для Mini App (web5).
Дата: 2026-09-26. Субагент: polyra-db-api (G). База: defaed0 + незакоммиченные
изменения lead'а в app/services/generation.py и app/services/chats.py
(WebChannel/WebStreamer, generate(channel, chat_id), ChatService.get_chat_for_user) —
не тронуты мной.

## Файлы (только writable scope)

| Файл | Тип | Суть |
|---|---|---|
| `app/api/routes/chat_web.py` | новый (269 строк) | 3 роута web5 + SSE-генератор + sentinel + stop-обёртка |
| `app/api/app.py` | правка | `create_app(generation_service=None, bot=None)` → `app.state`; роутер `chat_web.router` регистрируется ВСЕГДА |
| `app/db/repositories/messages.py` | правка | +`list_page(chat_id, *, limit, offset)` (ASC от старейшего) и +`get_last(chat_id, *, role=None)` |
| `tests/unit/test_chat_web.py` | новый (21 тест) | 503/401/404/400/422, SSE полный цикл, cancelled, crash, busy-notify, disconnect×2, stop×4, пагинация/сериализация |

`app/api/dependencies.py` — НЕ менялся: `CurrentUserDep` уже даёт `(User, EffectivePermissions)` тем же расчётом, что `AccessMiddleware` бота (status+grant+allowed_models → evaluate_access, 403 при неактивном доступе). `app/api/routes/__init__.py` — не трогал (вне scope); подключение через `from app.api.routes import chat_web` в app.py.

## Эндпоинты и точный SSE-контракт для фронта

### `GET /api/chats/{chat_id}/messages?limit=50&offset=0`
- Owner-check через `ChatService.get_chat_for_user` (архивный чат читается; чужой → 404 `chat not found`).
- `limit` cap 100 (`max(1, min(limit, 100))` по образцу chats.py), `offset` ge=0.
- Ответ: `{"messages": [...], "total": int}`; сообщения ASC внутри страницы, offset от старейшего.
- Каждое сообщение: `{"id": str, "role": "user"|"assistant", "status": "done"|"cancelled"|..., "created_at": isoformat (UTC, +00:00), "model_id": str|null, "text": str, "has_image": bool}`.
- `text` — склейка text-parts через пробел со strip (образец `extract_user_text`, любая роль); `has_image` — наличие image-part.

### `POST /api/chats/{chat_id}/messages` → SSE (`text/event-stream; charset=utf-8`)
- Body: `{"text": str}`. Пустой/пробельный → 400 `text must not be empty`; >20000 симв → 400 `text too long (max 20000 chars)`; без поля → 422.
- ДО стрима (обычный JSON): 401/403 (CurrentUserDep — тот же расчёт прав, что бот), 503 `web chat disabled` (нет сервиса или бота), 400 (text), 404 (чужой чат). Архивный чат допустим.
- Генерация: `queue=asyncio.Queue()` (неограниченная), `WebChannel(queue, bot)`, `before_id` = id последнего assistant-сообщения до старта (может None), `asyncio.create_task(service.generate(bot=bot, tg_chat_id=user.telegram_user_id, user=user, permissions=..., current_parts=[{"type":"text","text":...}], channel=channel, chat_id=chat_id))`.
- Отключение клиента НЕ отменяет генерацию: задача fire-and-forget, живёт до персистенса (как Telegram); очередь не блокирует её (put_nowait sentinel).
- Соединение БД отпускается на время стрима (`session.rollback()` после pre-checks и после финального чтения) — как паттерн AccessMiddleware.

**SSE-кадр: `event: <name>\ndata: <json>\n\n` (UTF-8, ensure_ascii=False). События по порядку:**

```
event: meta
data: {"chat_id": "<uuid>", "model_hint": "<chat.model_id | null>"}

event: delta          (×N, из WebStreamer; как приходит)
data: {"text": "<фрагмент>"}

event: error          (0..N; из WebChannel.notify/WebStreamer.fail: занят/лимит/
data: {"message": "<текст для пользователя>"}   модель/контекст/отказ провайдера)

event: done           (финал, ровно 0 или 1, ПОСЛЕДНИЙ кадр)
data: {"message_id": "<uuid>", "status": "done"}

event: cancelled      (вместо done, при остановке)
data: {"message_id": "<uuid>", "status": "cancelled"}
```

Гарантии: `meta` — первый кадр; финальный кадр один (`done` XOR `cancelled`) и только если появилось НОВОЕ assistant-сообщение (id != before_id); после `error` без нового сообщения финала нет (клиент показывает error). Стрим закрывается после финального кадра. Sentinel `None` в очередь ставит done-callback задачи ПОСЛЕ её завершения — финальные done/cancelled ищутся в БД, когда ответ уже персистентен.

### `POST /api/chats/{chat_id}/stop`
- Owner-check; ответ `{"stopped": bool}`; нет активной генерации → `{"stopped": false}` (идемпотентно).
- Реализация: обёртка `_stop_active_generation(service, chat_id)` — СНАЧАЛА пробует публичный `GenerationService.stop_chat(chat_id)` (его сейчас НЕТ — grep "def stop" подтверждает; задача lead'а), иначе fallback `service._generations.stop_for_chat(chat_id)`. Когда lead добавит `stop_chat`, обёртка переключится сама (тест `test_web_stop_prefers_public_stop_chat` фиксирует приоритет).

### 503 «web chat disabled»
Роуты web5 регистрируются ВСЕГДА; `generation_service is None` → все три отвечают 503 `{"detail": "web chat disabled"}`; POST дополнительно 503 при `bot is None`.

## Wiring для lead (main.py)

`create_app(..., generation_service=generation_service, bot=bot)` — оба опциональны, кладутся в `app.state.generation_service` / `app.state.bot`. `generation_registry` передаётся как раньше (не используется chat_web напрямую).

## Решения/компромиссы

- **GET /messages тоже гейтится на generation_service (503)** — «web chat disabled» трактовал как фичу целиком; если фронт должен читать историю при отключённой генерации — снять проверку в одном месте (`list_messages`).
- **Sentinel через done-callback (`queue.put_nowait(None)`)**, а не pump-таску: sentinel гарантированно ПОСЛЕ финализации задачи, без лишней задачи; `await task` в генераторе оставлен явно (по контракту), исключение подавлено (уже залогировано в callback).
- **Валидация text в хендлере (400), не pydantic** — пустой текст это бизнес-правило; тесты ТЗ ожидают 400.
- **`list_page`/`get_last` в repo** — сортировка по `created_at` (asc/desc) без id-tiebreak, консистентно с `list_recent`/`list_all`; в чате одна INSERT-транзакция = одно сообщение, коллизий таймстампов на практике нет.
- **SessionDep + rollback вместо коротких сессий из app.state.session_factory** — переиспользует DI и override-паттерн тестов; rollback возвращает соединение в пул на время стрима.

## Тесты

`tests/unit/test_chat_web.py`, 21 тест (ASGITransport + dependency_overrides + monkeypatch `chat_web.ChatService/MessageRepository`, по образцу test_api.py):
- 503 без сервиса (GET/POST/stop) и без бота (POST); 401 без токена.
- GET: пагинация total/offset/limit-cap 100, склейка text-parts («част ями»), has_image, cancelled-статус, точный набор полей, isoformat created_at; чужой чат 404.
- POST SSE: полный цикл meta→delta×2→error→done (с точными байтами кадров, порядком и message_id); cancelled-финал; crash generate() без финала; busy через `WebChannel.notify`; контракт kwargs generate() (channel=WebChannel, chat_id, tg_chat_id, current_parts, bot, permissions, user).
- Валидация: пустой/пробельный text 400, 20001 симв 400, без поля 422, чужой чат 404 JSON (не SSE).
- Disconnect: `aclose()` (GeneratorExit) и `athrow(CancelledError)` в `_stream_events` — задача генерации не отменяется и завершается.
- Stop: true/false, приоритет stop_chat, чужой чат 404.

## Результаты прогонов (реальные)

- `.venv\Scripts\python -m pytest tests -q` → **588 passed** (база 567 + 21 новых), ~9.4s
- `.venv\Scripts\python -m ruff check app tests` → **All checks passed!**, exit 0
- `.venv\Scripts\python -m mypy app tests scripts` → **Success: no issues found in 159 source files**, exit 0

## Blockers/заметки для lead

1. `GenerationService.stop_chat()` — добавить публичный accessor (файл lead'а); chat_web подхватит автоматически.
2. Live-проверки не выполнялись (запрещено); SSE-контракт проверен unit-уровнем до байтов.
3. `app/api/routes/__init__.py` не обновлён (вне scope) — при желании добавить `chat_web` в `__all__` для единообразия (не влияет на работу).
