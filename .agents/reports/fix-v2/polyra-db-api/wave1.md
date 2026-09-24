# polyra-db-api — wave1 (FIX V2 реализация)

Дата: 2026-09-24. Исполнитель: субагент polyra-db-api.
Scope соблюдён строго: `app/db/**`, `app/api/**`, `app/services/{admin,access,credentials,chats,settings}.py`,
`tests/unit/{test_access,test_api,test_api_auth}.py`. Чужие файлы (generation.py, llm/**, bot/**,
search/**, miniapp/**, main.py) НЕ тронуты. Грязный worktree (parallel agents) — мои диффы
проверены point-wise (git diff по файлам моего scope), чужих правок в них нет.

## Что сделано (по findings)

| Finding | Фикс |
|---|---|
| A03 | Таблица `user_model_access` (mode 'all'/'list') + backfill; `allowed_model_ids` новой семантики; `set_model_permissions` пишет mode; audit различает [] и null |
| A04 | `is_owner_user` → только numeric `telegram_user_id == settings.owner_telegram_id`; auth login синхронизирует флаг как UI-хинт; миграция чистит чужие `is_owner` |
| A05 (DB-часть) | Partial unique index `uq_generation_runs_active_chat (chat_id) WHERE status IN ('queued','running')` |
| A08 | `generation_runs.chat_id` → NULL + FK ON DELETE SET NULL |
| A13 | Новый `app/services/settings.py` (`EffectiveSystemSettings` + `get_effective_system_settings`), чтение DB на каждый запрос, env-fallback + warning |
| A14 | `credentials.get_provider_api_key`: disabled-запись → None БЕЗ env fallback; env только при отсутствии записи |
| A26 (grant-часть) | `grant_access`: sentinel `UNSET` vs explicit None; роут шлёт `model_fields_set`; `extend`: expires_at nullable (null=permanent) |
| A24 | `GET /chats?limit&offset&include_archived` → `{chats,total}`; `GET /chats/{id}` |
| A25 (API) | `_chat_out` += `system_prompt_override` |
| A35 | stats += `requests_by_model_today`, `errors_today`, `rate_limit_429_today`, `gemini_usage_today`; audit: limit/offset/action + total |
| A28 | `admin_models.py`, `admin_memory.py`; gemini test/reset-counters/usage; alibaba smoke |
| контракт §2 | `generation_runs.attempts JSONB NULL` |

## Миграция `app/db/migrations/versions/0009_fix_v2.py` (revision 0009, down 0008)

Проверена: `alembic heads` → `0009 (head)` (одна голова); офлайн-рендер
`upgrade 0008:0009 --sql` и `downgrade 0009:0008 --sql` — валидный PG SQL (ниже).

Операции upgrade:
1. `CREATE TABLE user_model_access (user_id UUID PK FK users.id ON DELETE CASCADE, mode VARCHAR(8) NOT NULL DEFAULT 'all', created_at/updated_at server now())`.
2. Backfill: `INSERT INTO user_model_access (user_id, mode) SELECT DISTINCT user_id, 'list' FROM user_model_permissions ON CONFLICT (user_id) DO NOTHING`.
3. `generation_runs`: DROP FK `fk_generation_runs_chat_id_chats` → `ALTER COLUMN chat_id DROP NOT NULL` → FK заново `ON DELETE SET NULL`.
4. `CREATE UNIQUE INDEX uq_generation_runs_active_chat ON generation_runs (chat_id) WHERE status IN ('queued', 'running')`.
5. `UPDATE users SET is_owner = false WHERE telegram_user_id <> 795063564` (id зашит по контракту §3).
6. `CREATE TABLE model_overrides (model_id VARCHAR(64) PK, enabled BOOLEAN NOT NULL, created_at/updated_at)`.
7. `CREATE INDEX ix_memories_text_fts ON memories USING gin (to_tsvector('simple', text))`.
8. `ALTER TABLE generation_runs ADD COLUMN attempts JSONB NULL`.

Downgrade зеркален, КРОМЕ: backfill (п.2) и очистка is_owner (п.5) НЕ откатываются
(задокументировано в docstring миграции). Откат NOT NULL chat_id: `DELETE FROM
generation_runs WHERE chat_id IS NULL` (строки появляются только после удаления чатов
на схеме 0009 — orphaned ledger; иначе NOT NULL не восстановить).

Работает на пустой и существующей БД (все data-опы на пустых таблицах — no-op).
**Предположение**: в живой БД ≤1 active (queued/running) run на чат, иначе п.4 упадёт —
нужен ручной дедуп до upgrade.

## Новые/изменённые сигнатуры (ТОЧНО, для lead и miniapp-агента)

### Модели (app/db/models/)
- `UserModelAccess(user_id: UUID PK FK users.id CASCADE, mode: str('all'|'list') default 'all')` — отсутствие записи = 'all'.
- `ModelOverride(model_id: str PK, enabled: bool)`.
- `GenerationRun.chat_id: UUID | None` (FK SET NULL); `GenerationRun.attempts: list[str] | None` (JSONB; имена gemini-проектов по попыткам).

### Репозитории (app/db/repositories/)
- `UserModelAccessRepository(session).get_mode(user_id) -> str` ('all' default);
  `.set_mode(user_id, mode) -> UserModelAccess` (ValueError при mode ∉ {'all','list'}).
- `ModelOverrideRepository(session).get_all() -> dict[str, bool]`;
  `.set_enabled(model_id, enabled) -> ModelOverride`.
- `ModelPermissionRepository.allowed_model_ids(user_id) -> set[str] | None` — **НОВАЯ
  СЕМАНТИКА**: mode 'all' → None; mode 'list' → set разрешённых (ПУСТОЙ set = запрет
  всех; пустой set ≠ None). Сигнатура прежняя — bot middleware (вне моего scope)
  получает новую семантику автоматически.
- `ChatRepository.count_for_user(owner_user_id, *, include_archived=False) -> int`.
- `MemoryRepository.list_for_user(user_id, *, limit=100, offset=0)` (offset был);
  `.count_for_user(user_id) -> int`; `.list_all(*, limit=50, offset=0) -> list[Memory]`
  (updated_at desc); `.count_all() -> int`.
- `AuditLogRepository.list(*, limit=50, offset=0, action=None) -> list[AuditLog]`;
  `.count(*, action=None) -> int` (`list_recent` сохранён).
- `QuotaUsageRepository.list_recent_minute(*, limit=50) -> list[dict]` —
  `{project_name, model_id, minute_ts, requests_count, tokens_in}`;
  `.list_recent_daily(*, limit=50) -> list[dict]` — `{project_name, model_id, day, ...}`;
  `.delete_all() -> tuple[int, int]` (minute_deleted, daily_deleted).

### Сервисы
- `credentials.get_provider_api_key(session, crypto, provider, env_fallback="")` — сигнатура
  прежняя; disabled-запись → None (env НЕ подставляется); env только при отсутствии записи.
- `admin.UNSET` — sentinel «поле не передано».
- `admin.grant_access(session, *, actor_id, telegram_user_id, expires_at=UNSET,
  requests_per_day=UNSET, token_limit=UNSET, max_concurrent_generations=UNSET,
  can_use_web_search=UNSET, can_use_memory=UNSET, note=UNSET)` — UNSET = не трогать
  колонку; explicit None = записать NULL. **ВНИМАНИЕ miniapp**: grant без `expires_at`
  больше НЕ сбрасывает срок в permanent — для permanent шлите явный `"expires_at": null`.
- `admin.extend_access(..., expires_at: datetime | None)` — null = permanent.
- `admin.set_model_permissions(...)`: None → mode 'all' + clear; [] → mode 'list' + clear
  (запрет всех); [m…] → mode 'list' + строки. Audit meta: `{mode, allowed_models}` ([]≠null).
- `admin.set_model_enabled(session, *, actor_id, model_id, enabled)`.
- Новые audit-константы: `MODEL_ENABLED_CHANGED`, `GEMINI_PROJECT_TESTED`,
  `GEMINI_COUNTERS_RESET`, `PROVIDER_SMOKE_TEST`.
- `app/services/settings.py` (НОВЫЙ): `@dataclass(frozen=True, slots=True)
  EffectiveSystemSettings(default_model: str, default_thinking: str|None,
  default_system_prompt: str, max_tool_iterations: int, context_keep_recent: int,
  context_trigger_ratio: float, memory_retrieval_limit: int,
  memory_extraction_min_chars: int)`; `async get_effective_system_settings(session,
  settings) -> EffectiveSystemSettings` — DB поверх env, невалидное → env + warning.
  **Handoff lead**: wiring в generation path/main.py — вне моего scope (файлы чужие).

### API (все admin-роуты — owner-gate только по numeric id)
- `GET /api/chats?limit=50&offset=0&include_archived=false` → `{chats, total}`;
  chat-объект += `system_prompt_override`. `GET /api/chats/{id}` → `{chat}` (404 чужой).
- `GET /api/memory?limit=100&offset=0` → `{memories, total}`.
- `GET /api/admin/models` → `{models: [{model_id, display_name, provider, enabled,
  internal_only, supports_images, thinking_modes, default_thinking, max_context,
  max_output}]}` (enabled = registry ∪ override). `PUT /api/admin/models/{model_id}`
  `{enabled: bool}` → `{ok}` (404 unknown; audit MODEL_ENABLED_CHANGED).
- `POST /api/admin/gemini/projects/{id}/test` → `{ok, latency_ms, error}` (реальный вызов
  gemini-3.5-flash-lite, thinking=minimal, ≤16 output tokens; audit GEMINI_PROJECT_TESTED).
- `POST /api/admin/gemini/reset-counters` → `{ok, deleted}` (audit GEMINI_COUNTERS_RESET).
- `GET /api/admin/gemini/usage` → `{minute: [...50], daily: [...50]}` (с project_name).
- `POST /api/admin/providers/alibaba/smoke` → `{ok, latency_ms, error}` (qwen3.8-flash,
  thinking=off, ≤16 tokens; нет ключа/disabled → `ok=false` без вызова; audit PROVIDER_SMOKE_TEST).
- `GET /api/admin/memory?telegram_user_id=<int?>&limit=50&offset=0` → `{memories, total}`;
  элемент += `user_id`, `telegram_user_id`; 404 при неизвестном telegram_user_id.
- `GET /api/admin/audit?limit=50&offset=0&action=<str?>` → `{entries, total}`.
- `GET /api/admin/stats` += `requests_by_model_today: {model_id: int}`,
  `errors_today` (status='failed'), `rate_limit_429_today` (error_category='rate_limit'),
  `gemini_usage_today: [{project_name, requests, tokens_in}]` (день = pacific_day, как RPD).

## Решения/допущения
- LLM smoke-хелперы живут в route-модулях (admin.py остаётся DB-only; мутация там = audit).
  httpx-клиент создаётся/закрывается на ручной admin-вызов (A30 касается пути генерации).
- `/api/models` (user) НЕ фильтруется по `model_overrides`: registry.py для меня read-only,
  enforcement effective-enabled в runtime — handoff для lead (таблица и admin API готовы).
- auth login синхронизирует `user.is_owner = (numeric match)` — флаг становится самоочищающимся
  UI-хинтом; bot `/admin` и `bot/middleware/access.py` — вне моего scope (пометить bot-агенту:
  flag-only проверка в commands.py:155-159 всё ещё есть).
- auth: `is_owner` в ответе `/api/auth/telegram` теперь строго numeric-based.
- `AccessExtendRequest.expires_at`: `datetime | None` без default (обязателен, null=permanent).

## Проверки (без pytest и без live БД — по инструкции)
- `py_compile`/`compileall` всего app — OK; `ruff check` моих файлов — OK
  (2 pre-existing ошибки ruff в `app/context/builder.py`, `app/llm/providers/alibaba.py` —
  чужие in-flight изменения, не трогал).
- `alembic heads` → `0009 (head)`; офлайн-SQL upgrade и downgrade отрендерены (валидный PG SQL).
- Ручной раннер (не pytest) всех тестов `test_access.py` + `test_api.py`: **58 PASS / 0 FAIL**
  (включая новые: пустой frozenset deny-all / None allow-all / owner always; owner-gate
  stale-flag 403, numeric-only; /api/me is_owner; регистрация V2 роутов (owner→500 на
  сломанной БД, non-owner→403, аноним→401); `_chat_out` с system_prompt_override; матрица
  credentials absent/enabled/disabled × env present/absent; effective settings
  override/invalid-fallback/empty-store).
- Ручная верификация admin-сервиса через фейки: UNSET не трогает поля, explicit None
  пишет NULL, mode-семантика set_model_permissions (None/[]/[m…]) — 18/18 OK.
- ORM-metadata ↔ миграция: DDL-рендер новых таблиц/индексов из моделей совпадает с SQL
  миграции (GIN, partial unique, SET NULL, JSONB).

## Не проверено (нужен live-контур)
- `alembic upgrade` против живой PostgreSQL (локальной БД нет); partial unique index на
  данных с дублирующимися active runs не тестировался (см. предположение).
- Реальные smoke-вызовы Gemini/Alibaba (нет ключей/сети); endpoint-код компилируется,
  gate/аудит-путь проверен, wire-вызов — integration.
- DB-интеграция новых репозиторных методов (pagination/count/delete_all rowcount) —
  только SQL-рендер и типы; RUN_API_INTEGRATION=1 прогон — за lead.
- pytest полный прогон не запускался (инструкция); ручной раннер покрыл мои 2 файла тестов.

## Файлы
Изменены: `app/db/models/{__init__,access,generation_run,memory}.py`,
`app/db/repositories/{__init__,access,audit_logs,chats,gemini,memories}.py`,
`app/services/{admin,credentials}.py`, `app/api/{app,dependencies}.py`,
`app/api/routes/{__init__,admin_access,admin_gemini,admin_providers,admin_stats,auth,chats,memory}.py`,
`tests/unit/{test_access,test_api}.py`.
Созданы: `app/db/migrations/versions/0009_fix_v2.py`, `app/db/models/model_override.py`,
`app/db/repositories/model_overrides.py`, `app/services/settings.py`,
`app/api/routes/admin_models.py`, `app/api/routes/admin_memory.py`.
