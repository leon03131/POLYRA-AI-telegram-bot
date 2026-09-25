# polyra-db-api — wave3 (FIX V2 догон: stats extras A35/A28 + проверка пагинации A24)

Дата: 2026-09-25. Исполнитель: субагент polyra-db-api.
Scope соблюдён строго: изменены только `app/api/routes/admin_stats.py` и
`app/db/repositories/generation_runs.py`; `app/api/routes/admin_users.py` — только чтение
(проверка, правок не потребовалось). Чужие in-flight правки (`app/db/repositories/system_settings.py`
и пр. в грязном worktree) НЕ тронуты.

## Задача 1 — A35/A28: расширение GET /api/admin/stats

Новые поля ответа (существующие не изменены по форме и семантике):

- `recent_failed_runs`: последние 10 runs со статусом failed/aborted (started_at desc):
  `[{id, chat_id, model_id, status, error_category, error_code, started_at, duration_s}]`.
  `id`/`chat_id` — str (chat_id может быть None, FK SET NULL). `duration_s` =
  `(finished_at − started_at).total_seconds()` округл. до 1 знака; None, если run не завершён.
- `avg_ttft_s`: среднее `EXTRACT(epoch FROM first_token_at − started_at)` за сегодня (UTC)
  по `status='completed'` И `first_token_at IS NOT NULL`; float, 1 знак; None, если данных нет.
- `error_rate_today`: `(failed + aborted) / всего_сегодня`, float 0..1, 2 знака; 0.0 при пустом дне.

### Реализация

`GenerationRunRepository` (все — select/func, только чтение, flush не нужен):

- `list_recent_failed(limit: int = 10) -> list[GenerationRun]` —
  `WHERE status IN ('failed','aborted') ORDER BY started_at DESC LIMIT n`.
- `avg_ttft_today() -> float | None` — `avg(EXTRACT(epoch FROM first_token_at - started_at))`
  с фильтрами `started_at >= utc_midnight`, `status='completed'`, `first_token_at IS NOT NULL`;
  округление до 1 знака внутри метода.
- `count_today_by_status() -> dict[str, int]` — `GROUP BY status` за сегодня (UTC).
- Общий приватный хелпер `_utc_today_start()` (полночь UTC-дня) в модуле репозитория.

В роуте `get_stats` переиспользован `count_today_by_status()`: им заменены два прежних
inline-запроса (`COUNT(*)` за сегодня и `GROUP BY status`) — значения `generations_today`
(= sum по статусам; status NOT NULL, покрытие полное) и `generations_by_status` идентичны прежним.
`errors_today` теперь деривируется (`status_counts["failed"]`, семантика прежняя), лишний запрос
убран. Остальные блоки (tokens, gemini, tool_calls, by-model, rate_limit, gemini_usage) не тронуты.

Допущение: «сегодня» в repo-методах считается своим `datetime.now(UTC)` — расхождение с
`today_start` роута возможно только при попадании запроса ровно на полночь UTC (пренебрежимо).

## Задача 2 — A24: лимиты пагинации (проверка, кода не касался)

### admin_users.py `GET /api/admin/users`
- `limit = max(1, min(limit, _MAX_LIMIT=200))`, `offset = Query(ge=0)` — offset применяется
  в SQL независимо от cap: записи за 200 доступны (limit=200&offset=200 → строки 201–400).
- Ветка с `query`: `UserRepository.search(query, limit=limit + offset)[offset:]` — LIMIT в SQL
  уже включает offset, срез корректен при любом offset.
- Вывод: **cap 200 не мешает пагинации → _MAX_LIMIT НЕ поднимал**. Аргумент против поднятия:
  в list_users per-user N+1 (`get_grant` + `allowed_model_ids` = 2 запроса на строку); страница
  1000 → ~2000 запросов. (N+1 отмечен как замечание; фикс — отдельной задачей, вне scope.)
- Замечание: в ответе нет `total` (соответствует docs/API.md) — не добавлял (не требовалось).

### chats.py `GET /api/chats` (read-only проверка)
- `limit` cap 200 / `offset` ge 0 / `include_archived` — фильтр `archived_at IS NULL`
  консистентен в `ChatRepository.list_for_user` и `count_for_user` (одинаковое условие),
  `total` соответствует страницам, offset работает за пределами 200. ✓ Изменений не требуется.

## Проверено

- `python -m py_compile` + `compileall` трёх файлов scope — OK.
- `ruff check` (из .venv): 1 ошибка C901 в `GenerationRunRepository.finish` — **pre-existing**
  (метод не трогал; подтверждено git diff — мои добавления это `_utc_today_start`,
  `list_recent_failed`, `avg_ttft_today`, `count_today_by_status`).
- SQL-рендер под PostgreSQL dialect (без live БД): `EXTRACT(epoch FROM a - b)` → валидный
  `avg(EXTRACT(epoch FROM generation_runs.first_token_at - generation_runs.started_at))`;
  `IN ('failed','aborted') ... ORDER BY started_at DESC LIMIT` — корректен.
- Чужие файлы не изменялись: `git status` по моему scope — только 2 моих файла
  (`system_settings.py` modified — чужая правка parallel-агента, не трогал).

## Не проверено (нужен live-контур / за lead)

- Live-запросы к PostgreSQL (значения avg/rate на реальных данных) и HTTP-прогон endpoint'а —
  pytest по инструкции не запускался.
- `docs/API.md` НЕ обновлял (вне scope): handoff lead — в секцию Stats extras добавить
  `recent_failed_runs`, `avg_ttft_s`, `error_rate_today` и сообщить miniapp-агенту
  (AdminDashboardPage) форму новых полей (см. Задачу 1).
- Производительность `/api/admin/stats`: теперь ~9 запросов на вызов (было ~10,
  консолидацией group-by убран 1) — без индексного анализа; поля под фильтры уже индексированы
  частично (`started_at` индекса нет — при росте таблицы рассмотреть).

## Файлы

- Изменены: `app/db/repositories/generation_runs.py` (+3 метода, +хелпер),
  `app/api/routes/admin_stats.py` (+3 поля ответа, консолидация status-запросов).
- Создан: `.agents/reports/fix-v2/polyra-db-api/wave3.md` (этот отчёт).
- Проверены без изменений: `app/api/routes/admin_users.py`, `app/api/routes/chats.py`.
