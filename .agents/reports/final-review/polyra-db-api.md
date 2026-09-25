# Final review — POLYRA DB / API / access (profile G, read-only audit)

- Дата: 2026-09-25. Аудитор: polyra-db-api (subagent, READ-ONLY).
- Checkout: `O:\work\aibot`, HEAD `e4ac383` («fix: correct max_output ceilings…»), рабочее дерево чистое (кроме данного каталога отчёта).
- Scope: app/db/**, app/api/**, app/services/** (кроме generation.py/llm_factory.py — читались только как потребители контрактов), app/security/crypto.py, tests/unit/test_access.py, test_api.py, test_api_auth.py, test_crypto.py.

## Прогон офлайн-тестов (реальные команды/результаты)

```
.venv\Scripts\python.exe -m pytest tests/unit/test_access.py tests/unit/test_api.py \
  tests/unit/test_api_auth.py tests/unit/test_crypto.py -q
→ 83 passed in 2.03s                        (exit 0)

.venv\Scripts\python.exe -m pytest tests/unit -q        → 464 passed in 8.60s  (exit 0)
.venv\Scripts\python.exe -m pytest tests/integration -q → 24 passed in 3.71s  (exit 0)
```

Все integration-тесты (`tests/integration/test_fix_v2_*.py`) — на in-memory фикстурах/MockTransport; **ни один тест не поднимает реальную PostgreSQL** (grep `postgres|asyncpg|sqlite` в tests/integration — пусто). DB-инварианты ниже подтверждены статически (код+миграции), но не протестированы против живой БД.

## Целостность миграций (общая проверка)

- Цепочка alembic линейная: `0001→0002→…→0009` (revision/down_revision проверены grep'ом по всем файлам versions/). `alembic.ini: script_location = app/db/migrations`; `env.py` подхватывает `DATABASE_URL` → app.config → ini; Dockerfile: `CMD ["sh","-c","alembic upgrade head && exec python -m app.main"]` (exec после миграций — A37-фикс на месте). 0009 в цепочке — паттерн «миграция не в head» отсутствует.
- Покрытие моделей: 21 create_table в 0001–0009 закрывает все 21 имя из `app/db/models/__init__.py` (users, access_grants, user_model_permissions, user_settings, chats, messages, message_parts, generation_runs, gemini_projects, quota_policies, quota_minute_usage, quota_daily_usage, provider_credentials, chat_summaries, memories, search_backend_configs, tool_calls, system_settings, audit_log, user_model_access, model_overrides). `provider_health` из ТЗ §38 не создана (см. A28).
- Naming convention (base.py:8-14 `uq_%(table_name)s_%(column_0_name)s`) совпадает с именами констрейнтов в миграциях 0003 (`uq_quota_minute_usage_project_id`, `uq_quota_daily_usage_project_id`) — ON CONFLICT в quota-reserve ссылается на существующие имена (иначе был бы прод-слом при живой БД).
- 0009 upgrade честно документирует риск: при >1 active run на чат в живой БД `CREATE UNIQUE INDEX` упадёт и потребует ручного дедупа (docstring п.3). Откат удаляет orphaned ledger-строки — с предупреждением в docstring. Это осознанный и задокументированный риск, не скрытый.

---

## A01 (backend) — FIXED_VERIFIED

- `app/api/routes/chats.py:49` — `"id": str(chat.id)`; `memory.py:29` — `str(memory.id)`; admin-роуты (`admin_gemini.py:70`, `admin_stats.py:147`, `admin_users.py:44`, `admin_memory.py:19-22`) — все public id строковые UUID.
- Frontend: `miniapp/src/api/types.ts:51` (`Chat.id: string`), `:130` (`MemoryItem.id`), `:231` (AuditEntry). `grep Number(|parseInt(` по miniapp/src — конверсий id чатов/памяти нет (Number только для числовых полей форм).
- Тест: `tests/unit/test_api.py:303-321` `test_chat_out_includes_system_prompt_override_and_string_id` — `isinstance(out["id"], str)` ✓.
- E2E-приёмка (создать→открыть→изменить→reload) — вне данного прогона (miniapp/E2E — scope B/H); на backend-уровне согласованность полная.

## A03 — FIXED_VERIFIED (юнит-уровень; DB-интеграционной проверки нет)

- Семантика трихотомии: `app/db/repositories/access.py:95-106` `allowed_model_ids` — mode `'all'`/нет записи → `None` (unrestricted); mode `'list'` → `set(...)`; **пустой set ≠ None** (запрет всех). `app/services/access.py:109-114` `is_model_allowed`: `None → True`; `model_id in frozenset()` → False.
- Сервис: `app/services/admin.py:239-276` `set_model_permissions` — `None → 'all'` + очистка allowlist; `[] → 'list'` без строк (запрет всех); `[m…] → 'list'` + строки. В audit metadata `mode` и `allowed_models` различают `[]` и `None` (строки 272-275).
- Миграция: `0009_fix_v2.py:72-92` — таблица `user_model_access` + backfill «пользователи со строками permissions → 'list'». Безопасное направление: прежнее «пусто после бага» → `'all'` (не封锁 ложных запретов); задокументировано (docstring строки 85-88).
- Одна политика: `app/api/dependencies.py:81-88`, `app/api/routes/auth.py:70-77`, `app/bot/middleware/access.py:78-85` — везде `ModelPermissionRepository.allowed_model_ids` + `evaluate_access`; генерация — тот же `is_model_allowed` через `_model_denial` (generation.py:473). API-валидация unknown моделей: `admin_users.py:106-110` (400).
- Тесты: `test_access.py:125` (empty set → no models), `:332`, `:349`, `:364` — чистая логика. Приёмка «через настоящий admin API + фактический вызов провайдера» не выполнялась: unit-набор сознательно без БД (`_BrokenSessionFactory`, test_api.py:46-50), RUN_API_INTEGRATION-прогона в репо нет.
- Note: `UserModelPermission.allowed=False` строки никогда не пишутся (set_permission вызывается только с True) — режим 'list' однозначен, конфликтов нет.

## A04 — FIXED_VERIFIED

- `app/api/dependencies.py:33-39`: `is_owner_user` = `user.telegram_user_id == settings.owner_telegram_id`; docstring явно: «Флаг User.is_owner в БД — информационный… прав НЕ даёт». `require_owner` (99-107) — единый gate; все admin-роутеры под `Depends(require_owner)`.
- Bot: `app/bot/routers/commands.py:160-163` — `/admin` только `message.from_user.id != settings.owner_telegram_id → отказ`.
- Миграция: `0009_fix_v2.py:116` — `UPDATE users SET is_owner = false WHERE telegram_user_id <> 795063564` (захардкожено и продублировано константой `_OWNER_TELEGRAM_ID = 795063564`, строка 49; config default `config.py:17 owner_telegram_id=795063564`).
- Синхронизация флага как UI-подсказки: `auth.py:50-54`, `bot/middleware/access.py:59-62` — флаг выставляется/снимается по numeric id, прав не даёт.
- Username нигде не участвует в решении.
- Тесты: `test_api.py:232-241` (stale `is_owner=True` у tg_id=111 → 403), `:244-253` (numeric id без флага → gate пройден), `:256-265` (GET /me is_owner по numeric id), `:280-297` (все v2 admin-роуты: non-owner 403 / owner 500=gate ok).

## A05 — FIXED_PARTIAL

**Per-chat — сделано (DB-инвариант):**
- Partial unique index: `app/db/models/generation_run.py:33-40` + `0009_fix_v2.py:107-113` — `uq_generation_runs_active_chat (chat_id) WHERE status IN ('queued','running')`.
- `app/services/generation.py:1041-1058`: run создаётся со status="running" и commit; `except IntegrityError` → rollback + «⏳ Дождитесь завершения» — гонка между процессами закрыта базой. До этого быстрый in-memory предчек (строка 928).

**Per-user concurrency и daily — НЕ атомарны (check-then-act):**
- `generation.py:905-910`: `count_active_for_user` — обход **in-memory** `GenerationRegistry` (популяция — только после commit run'а, регистрация в `generate()` строка 397). Окно гонки: два параллельных запроса одного пользователя в разных чатах оба видят 0 → оба создают run (per-chat index их не останавливает). Реестр per-process — при multi-worker деплое лимит не межпроцессный.
- `generation.py:887-904`: requests/day и token_limit — `count_since`/`tokens_since` читают закоммиченные runs ДО INSERT run'а; атомарной резервации нет (двух-соединениевый барьер даст double-pass на границе лимита).
- Требовавшийся приёмочный «Barrier-тест с двумя соединениями PostgreSQL» отсутствует (integration-тесты — фиксы без БД).
- Note: `count_since` (repo:69-77) считает runs без фильтра статуса — aborted/failed тоже расходуют дневной лимит (консервативная политика; последовательна с A08-ledger, но не документирована как политика).

## A08 — FIXED_VERIFIED (схема+код; live-PG теста нет)

- `app/db/models/generation_run.py:43-47`: `chat_id: nullable`, FK `ondelete="SET NULL"`; комментарий модели явно про A08.
- `0009_fix_v2.py:94-104`: drop FK → `alter_column nullable=True` → FK `ondelete="SET NULL"` — ledger строка переживает удаление чата.
- Суточный учёт не завязан на чат: `generation_runs.py:69-101` `count_since`/`tokens_since` — по `user_id`+`started_at`. Удаление чата не обнуляет лимиты (требование п.4 общих критериев).
- Удаление чата во время генерации: `_save_completed`/`_save_cancelled` ловят IntegrityError на FK сообщений и не роняют ответ (generation.py:1128-1133, 1178-1182); run финализируется (chat_id уже NULL).
- Downgrade зеркален, с документированным удалением orphaned ledger при откате NOT NULL (0009 docstring + код 152-156) — потери маскируются под «не удаляем данные»: строки удаляются только при осознанном откате схемы, что зафиксировано.

## A09 (db-часть) — FIXED_VERIFIED (с документированным отклонением TPM)

- Атомарный check-and-reserve: `app/llm/gemini/store_db.py:125-225` `DbQuotaStore.check_and_reserve_atomic` — обе таблицы (quota_minute_usage/quota_daily_usage) в одной сессии; INSERT…ON CONFLICT DO UPDATE с WHERE по лимитам + RETURNING; отказ любого окна → rollback обоих инкрементов (156-169). Констрейнты `uq_quota_minute_usage_project_id`/`uq_quota_daily_usage_project_id` существуют в миграции 0003:91/115 и совпадают с naming convention.
- Reconcile в окна резервации (не «сейчас») — реализовано в tracker'е и покрыто `test_gemini_pool.py:694-790` (store-level, включая parallel rpm=1 → ровно один допуск, reconcile через границу минуты).
- Отклонение, зафиксированное в docstring (144-148): TPM-допуск сверх лимита после факта не блокируется (tokens довносятся post-factum) — «bounded token reservation» из приёмки не реализован, но честно задокументирован (report wave2).
- Реального PostgreSQL-прогона нет (см. шапку); PG-специфика (`pg_insert`) в SQLite вообще не исполняема — тесты корректно обходятся фейками, т.е. семантики SQLite-vs-PG не расходятся (проверяется только логика tracker'а).

## A13 — FIXED_PARTIAL (главный разрыв — memory_extraction_min_chars)

- Единый SettingsService: `app/services/settings.py` `get_effective_system_settings` — чтение **на каждый запрос** (кеша нет → нет проблемы инвалидации), DB поверх env, коэрс-валидация с fallback+warning (87-129). Тесты: `test_api.py:431-500` (override/invalid/empty).
- Потребление в генерации: `generation.py:942-954` — `effective_settings` из DB; далее используются `default_model`, `default_system_prompt` (1011-1013), `memory_retrieval_limit` (1005), `context_*`, `max_tool_iterations` (в _build_context/_stream_loop). Приоритет chat → user → DB system → env соблюдён (resolve_model_and_thinking + system_prompt_override).
- **РАЗРЫВ 1**: `admin_system.py:16-32` (набор ключей) и `SystemPutRequest` (39-48) **не содержат `memory_extraction_min_chars`** — через Admin API его нельзя ни прочитать, ни изменить.
- **РАЗРЫВ 2**: extractor построен один раз из env (`main.py:67-74` `min_chars=settings.memory_extraction_min_chars`); `MemoryExtractor.extract_and_store` (`app/memory/extractor.py:198-211`) использует `self._min_chars` и **не принимает min_chars per-call**. Значение из DB, попавшее в `effective_settings` (generation.py:950), дальше никуда не передаётся — вычисляется и выбрасывается. Т.е. пункт исходного аудита «memory_extraction_min_chars не передан» исправлен только на уровне env→конструктор, но не DB→runtime: DB-значение не влияет на извлечение памяти.
- Admin GET/PUT runtime-эффект для остальных ключей подтверждён кодом пути _prepare; интеграционного теста (изменение через API → следующий LLMRequest) нет.

## A14 (db/api-часть) — FIXED_VERIFIED

- `app/services/credentials.py:21-26`: запись с `enabled=False` → `return None` (абсолютный запрет, env НЕ подставляется); env_fallback — только если записи нет; enabled → расшифровка DB-ключа.
- Полная матрица покрыта тестами: `test_api.py:348-398` (absent×env±, enabled×env±, disabled×env±).
- Потребители: `admin_providers.py:119-124` (smoke: disabled → «not configured (or disabled)», HTTP-вызова нет); generation path через llm_factory (вне scope, но вызов `get_provider_api_key` един).
- Note (зона A30, вне моего writable scope): llm_factory кеширует клиентов по ключу — при disable→enable ротации старые клиенты не закрываются явно; отмечено как смежное, не влияет на A14-семантику функции.

## A24 (backend-часть) — FIXED_VERIFIED

- `chats.py:81-101` GET /chats: `include_archived`, `limit` (clamp 1..200), `offset`, `total`; репозиторий `chats.py:29-53` (list_for_user/count_for_user с тем же фильтром archived).
- `chats.py:104-112` GET /chats/{id} — отдельное получение (в т.ч. архивного), 404 на чужой.
- Memory API: `memory.py:38-51` — пагинация limit/offset/total (clamp 200). Admin memory: `admin_memory.py:31-63` — фильтр по telegram_user_id + пагинация.
- Current-chat при archive/delete/open: `ChatService.get_or_create_current_chat` (services/chats.py:53-64) — current валиден только если чат существует и не архивирован, иначе fallback к latest active, иначе новый чат; `is_current` в `_chat_out` (chats.py:59). Активная генерация при удалении — см. A08 (SET NULL + безопасный пропуск персистенса).
- Pagination — offset, не cursor (допустимо по формулировке «cursor/pagination»). Мелочь: `admin_users.py:67-69` search добирает limit+offset и режет в памяти — работает, но неэффективно.
- Юнит-тестов на пагинацию нет (БД-gated); логика репозитория тривиальна и проверена статически.

## A25 (backend-часть) — FIXED_PARTIAL

- `_chat_out` возвращает `system_prompt_override` (chats.py:55) ✓ + тест `test_api.py:303-321`. Frontend читает/пишет его (`ChatSettingsPage.tsx:36,154`; types.ts:57,80).
- PATCH-валидация: `chats.py:154-169` — `exclude_unset`; model_id проверяется только при non-null (registry + is_model_allowed: 400 unknown/403 not allowed); **явный null в model_id + thinking** в одном PATCH: `base_model_id = data.get("model_id", chat.model_id)` — при явном null берётся user default (163-166), т.е. «or»-ловушка старого кода (null → старая модель) устранена.
- Runtime: неизвестная сохранённая модель — явный отказ пользователю, не fallback (generation.py:955-965); отключённая (registry enabled=False **или** DB override) — отказ (459-472); не разрешённая permissions — отказ (473-474). Image-модели — отказ со списком альтернатив (475-485).
- **Остаток 1**: `resolve_model_and_thinking` (generation.py:190-211) по-прежнему содержит ветку «неизвестная model_id → fallback на settings.default_model», и `tests/unit/test_generation.py:187 test_resolve_unknown_model_falls_back_to_default` всё ещё кодирует старую семантику. В прод-пути ветка мертва (unknown проверяется в _prepare до вызова, а fallback-цель при unset chat/user совпадает с исходным model_id → registry.get всё равно бросает UnknownModelError), но тест-ожидание «unknown → fallback» из аудита не исправлено — пункт приёмки «исправить тест» не выполнен формально.
- **Остаток 2**: `_validate_model` (chats.py:71-78) и PATCH /settings (settings.py:67-79) не сверяются с `model_overrides` — администраторски отключённую модель можно сохранить в чат/настройки (generation тогда откажет). Валидация API и runtime расходится в Disabled-кейсе (unknown-кейс согласован).

## A26 (backend-часть) — FIXED_PARTIAL

- Unset vs explicit null: sentinel `UNSET` (admin.py:46-48); `grant_access` (85-131) — только переданные поля (`model_fields_set`, admin_access.py:66-73); `None` записывает NULL (лимит снят/permanent); extend с null = permanent (admin_access.py:47-51, admin.py:162-173). GET возвращает null честно (`_grant_out`).
- Немедленный revoke: `GenerationRegistry.stop_all_for_user` (generation.py:112-119) — suspend/revoke/ban вызывают его и возвращают `stopped_generations` (admin_access.py:108, 124, 140). Реестр доступен через `app.state.generation_registry`.
- **GAP 1 (bounds)**: `AccessGrantRequest` (admin_access.py:30-44) — `requests_per_day/token_limit/max_concurrent_generations` без `Field(ge=…/le=…)`: принимаются отрицательные/абсурдные значения (QuotaPutRequest в admin_gemini имеет `ge=0`; importance 1..10 в memory — там bounds есть). Требование «добавить bounds числовых полей» не выполнено для грантов.
- **GAP 2**: stop — per-process in-memory (допустимо при текущем однопроцессном main.py, но не межпроцессно); «перепроверка прав перед новым внешним вызовом/side effect» внутри стрима зависит от cancellation-контракта A11 (вне данного scope; событие выставляется и провайдеры его проверяют — частично).
- Тестов на «лимит → очистить → null → unlimited» нет (БД-gated); семантика UNSET покрыта кодом, unit-теста тоже нет.

## A28 (backend-часть) — FIXED_PARTIAL

Реализовано (все — owner-gated + audit, ключи не возвращаются):
- Models: GET /admin/models (effective enabled = registry ∪ model_overrides, admin_models.py:23-44) + PUT /{model_id} enabled-override (47-62, audit MODEL_ENABLED_CHANGED; unknown → 404).
- Provider: GET /admin/providers/alibaba (configured/enabled/key_hint, base_url read-only), POST key (audit PROVIDER_KEY_SET, key_hint), POST smoke — реальный дешёвый вызов qwen3.8-flash ≤16 output tokens (84-106), disabled → 0 HTTP-вызовов, audit PROVIDER_SMOKE_TEST (109-137).
- Gemini: test project (334-356, ≤16 tokens, audit), reset-counters (359-372, audit, minute+daily), GET usage — фактические minute/day счётчики (375-381, последние 50 окон), quotas GET/PUT (303-331, unknown model → 400, bounds ge=0).
- Admin memory: GET /admin/memory (admin_memory.py, фильтр по пользователю + пагинация).
- Регистрация/gate всех новых роутов проверена тестом `test_api.py:270-297`.
Не реализовано (против текста аудита/ТЗ §38):
- **Generation errors diagnostics**: нет эндпоинта со списком failed runs (category/code/latency) — в /stats только количества.
- **Provider/model health runtime storage**: таблицы `provider_health` из ТЗ нет; health ограничен gemini_projects.health_status и search_backend_configs.health_status; отдельного model-health эндпоинта нет (только enabled).
- Models «CRUD» — только enable-override (для кодового registry create/delete N/A, но capabilities-просмотр есть в GET).
- **Audit-пропуск**: POST /admin/search/backends/{id}/test мутирует health_status в конфиге, но audit-запись не пишет (admin_search.py:114-126) — см. A35.

## A29 — FIXED_VERIFIED (с двумя мелкими пробелами)

- Дедуп по ПОЛНОМУ ключу: API — `_existing_plaintext_keys` (admin_gemini.py:127-138) decrypt'ит существующие и сравнивает plaintext; bulk (168-218) и одиночное добавление (141-165, 409 на дубль ключа) используют один и тот же set. Два разных ключа с одинаковым last-4 импортируются; повтор того же ключа пропускается; `key_hint` остаётся только UI-маской.
- CLI `scripts/import_gemini_keys.py:80-98` — тот же full-key дедуп; имена продолжаются от 01 с пропуском занятых; `--dry-run` (64-70) не открывает БД и не пишет (логи с mask_secret).
- API/CLI согласованы по дедуп-семантике (нумерация имён различается: API продолжает от max, CLI стартует с 01 — поведенческое расхождение, не влияющее на идемпотентность).
- Пробелы: (1) явной Google project identity нет — в `gemini_projects` нет колонки project id/number (модель gemini.py:23-40); «одинаковый Google project не обходит quota policy» не обеспечивается схемой; (2) dry-run не сверяет ключи с БД (печатает всех как «будет добавлено»); dedup реализован decrypt-compare, а не «keyed fingerprint» — секрет не логируется и не хранится открыто, эффект эквивалентен.
- Юнит-тестов на bulk-логику нет (в test_api — только gate/регистрация роута).

## A34 — FIXED_PARTIAL

- Startup recovery: `main.py:114-119` — `abort_stale()` (queued/running → aborted, finished_at=now) + commit + warning; `generation_runs.py:79-90`. «Освобождение reservations»: user-лимиты reservation не имеют (см. A05); Gemini-квоты — оконные, истекают естественно (aborted runs не удаляются из окон; консервативно).
- `main.py:123` — `delete_webhook(drop_pending_updates=False)` ✓ (pending updates сохраняются).
- Меню/команды при старте: `setup_bot_commands` + `setup_menu_button` (124-125, A02).
- **GAP 1 (drain)**: фоновые задачи (compaction/title/memory: `_spawn_background` generation.py:814-826 — `asyncio.create_task` fire-and-forget, не трекаются) не отменяются/не дожидаются при shutdown; активные генерации не останавливаются на выходе.
- **GAP 2 (supervisor/SIGTERM)**: `asyncio.gather(dp.start_polling, uvicorn.serve())` без обработчика сигналов (только KeyboardInterrupt); SIGTERM убьёт процесс без finally-очистки. finally закрывает llm_stream/search_manager/bot.session/engine (133-137) — аккуратный exit есть только при «мягком» завершении. Общего supervisor'а (stop intake → cancel/drain → persist → close) нет.
- readiness отражает БД (`/ready` 503, api/app.py:76-85) — деградация видна, но graceful-drain нет.
- Тестов на kill/restart нет (нужен живой процесс+БД).

## A35 — FIXED_PARTIAL

Реализовано:
- Audit: модель (audit_log.py: append-only, actor/action/target/metadata/created_at — поля ТЗ §40), записи на ВСЕХ admin-mutations: grant/extend/suspend/revoke/ban/unban (admin.py), model enable, gemini add/bulk/enable/disable/move/delete/quota/test/reset (admin_gemini.py — каждый роут вызывает audit), providers key/smoke, search enable/priority/key, system settings (SYSTEM_SETTINGS_UPDATED / SYSTEM_PROMPT_UPDATED). Пагинация+фильтр: `admin_stats.py:131-158` (limit≤500, offset, action) + `audit_logs.py:44-61`.
- Stats: requests_by_model_today, generations_by_status, errors_today, rate_limit_429_today (error_category=rate_limit), gemini_usage_today по проектам (Pacific-day, join имён), tool_calls_today, tokens in/out (admin_stats.py:72-128).
- Секреты: redaction-фильтр в логах (observability/logging.py:19-39, форматирование 49-58); в audit metadata только key_hint/latency/ok, без ключей.
Не реализовано:
- **Latency/TTFT distribution**: `first_token_at`/`started_at` хранятся (generation_run.py:57-58), но ни один эндпоинт не считает latency/TTFT; «latency/TTFT distribution» из аудита отсутствует (есть точечный latency_ms только в smoke-тестах).
- **Error rate** как метрика (только абсолютные counts), **usage по search backend / tool** (tool_calls_today — только общее число), **диагностика error runs** (список фейлов с error_category/code — нет эндпоинта).
- **Correlation IDs**: `draft_id` связывает run↔Telegram draft (generation_run.py:68), но request-скоped correlation ID в API-логах/запросах отсутствует (нет middleware, нет run-id в лог-строках).
- **Audit-пропуск на search test** (admin_search.py:114-126 — мутация health без audit, см. A28).

## Кросс-паттерн «фикс проходит тест, но ломается в проде» — сводка

1. **A05 per-user лимиты**: per-chat закрыт DB-инвариантом, но concurrency/daily — check-then-act на in-memory реестре (однопроцессное предположение; multi-worker UVICORN разнесёт лимит). Никакой PG-барьер-тест этого не проверял.
2. **A13 memory_extraction_min_chars**: DB→runtime разорван (значение читается в _prepare и не потребляется; Admin API ключа не имеет) — «фикс» существует только в сервисе чтения настроек,_extractor живёт на env-значении.
3. **Все DB-гарантии** (partial unique index, SET NULL ledger, atomic quota upsert, миграции) проверены статически; тестов на реальной PostgreSQL нет вообще (integration = фиксы). При этом SQLite-семантика в тестах не подменяет PG (таблицы не создаются; pg_insert не исполняется) — ложнопозитивов от SQLite нет, но и доказательств PG-поведения нет.
4. **A26 bounds**: отрицательные лимиты принимаются API.
5. **A25**: PATCH принимает admin-disabled модель (validation не видит model_overrides); тест old-fallback-семантики остался.
6. **A34**: фоновые задачи не дрейнятся, SIGTERM не обрабатывается.
7. **0009**: на живой БД с >1 active run на чат индекс не создастся (риск задокументирован в самой миграции; нужен ручной дедуп при апгрейде).

## Статусы (итог)

| Пункт | Статус |
|---|---|
| A01 backend | FIXED_VERIFIED |
| A03 | FIXED_VERIFIED (DB-интеграционной проверки нет) |
| A04 | FIXED_VERIFIED |
| A05 | FIXED_PARTIAL (per-chat ok; per-user check-then-act, нет барьер-теста) |
| A08 | FIXED_VERIFIED (схема/код; live-PG теста нет) |
| A09 db | FIXED_VERIFIED (TPM post-factum — документированное отклонение) |
| A13 | FIXED_PARTIAL (memory_extraction_min_chars не читается runtime'ом и не редактируется через Admin API) |
| A14 db/api | FIXED_VERIFIED |
| A24 backend | FIXED_VERIFIED |
| A25 backend | FIXED_PARTIAL (disabled-модель проходит PATCH; старый fallback-тест жив) |
| A26 backend | FIXED_PARTIAL (нет bounds; revoke — per-process) |
| A28 backend | FIXED_PARTIAL (нет error-диагностики, provider_health, audit на search-test) |
| A29 | FIXED_VERIFIED (нет project identity; dry-run без сверки с БД) |
| A34 | FIXED_PARTIAL (recovery+webhook ok; нет drain/supervisor/SIGTERM) |
| A35 | FIXED_PARTIAL (audit+базовые метрики ok; нет TTFT/latency, correlation IDs, error-run диагностики) |
