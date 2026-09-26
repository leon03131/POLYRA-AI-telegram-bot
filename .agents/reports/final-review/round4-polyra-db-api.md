# Round 4 — bug hunt: POLYRA DB / API / services (кроме generation.py, llm_factory.py)

- HEAD: `4925c40` (только чтение; git/live не трогал; к БД не подключался)
- Scope: `app/db/**`, `app/api/**`, `app/services/**` (access, admin, chats, credentials, settings), `app/main.py`, `tests/unit/test_access.py`, `test_api.py`, `test_api_auth.py`
- Метод: полное чтение файлов + статические проверки (AST cross-check вызовов репозиториев, offline-компиляция SQL, alembic `upgrade head --sql`, дифф ORM↔миграции) + offline pytest.

## Прогоны (реальные, сегодня)

| Команда | Результат |
|---|---|
| `.venv\Scripts\python -m pytest tests/unit/test_access.py tests/unit/test_api.py tests/unit/test_api_auth.py -q` | **85 passed** (exit 0) |
| `.venv\Scripts\python -m pytest tests/unit -q` | **476 passed** (exit 0) |
| `.venv\python -m pytest tests/integration -q` | **27 passed** (exit 0) — все на фейках, без PostgreSQL |
| AST-скан 82 вызовов repo-методов в app/api+app/services+main | **0 отсутствующих методов** |
| `alembic upgrade head --sql` (offline, dummy URL) | 385 строк DDL, цепочка 0001→0009 линейна, ошибок нет |
| Дифф колонок ORM (`Base.metadata`) ↔ миграции | **21/21 таблиц совпадают** (в SQL только лишняя `alembic_version`) |
| Компиляция `avg_ttft_today` для PostgreSQL | валидный SQL (см. OK-3) |

---

## P1

### P1-1. POST /api/admin/access/grant: явный `null` в NOT NULL полях гранта → IntegrityError → HTTP 500 (нарушение контракта A26)

Контракт A26 задокументирован в трёх местах: «поле НЕ передано → не меняется; поле = null → записывается NULL». Для nullable-полей (`expires_at`, `requests_per_day`, `token_limit`, `note`) это работает. Но три поля гранта **NOT NULL**:

- `app/db/models/access.py:40` — `max_concurrent_generations: Mapped[int] = mapped_column(Integer, default=1)` (NOT NULL);
- `app/db/models/access.py:41-42` — `can_use_web_search` / `can_use_memory: Mapped[bool] = mapped_column(Boolean, default=True)` (NOT NULL);
- миграция `0001_initial.py:72-74` — `max_concurrent_generations INTEGER NOT NULL`, `can_use_web_search BOOLEAN NOT NULL`, `can_use_memory BOOLEAN NOT NULL`.

Путь пробоя:
1. `app/api/routes/admin_access.py:66` — `fields = body.model_dump(exclude_unset=True)` — `{"can_use_memory": None}` попадает в fields;
2. `app/services/admin.py:104-111` — bounds-проверка **пропускает None**: `if value is not UNSET and value is not None and value <= 0` (комментарий «A26: bounds для числовых лимитов»);
3. `app/services/admin.py:123-127` — `fields.update(...)` → `upsert_grant(user.id, can_use_memory=None, ...)`;
4. `app/db/repositories/access.py:37/40-41` — новый грант: `AccessGrant(user_id=..., **fields)` (ORM-`default` не срабатывает на явно переданный None), апдейт: `setattr(grant, key, None)`;
5. flush → `NotNullViolation` → `IntegrityError` → роут ловит только `ValueError` (`admin_access.py:75-78`) → unhandled → 500 `{"detail": "internal error"}`.

Прод-проявление: владелец сохраняет в админке форму гранта с очищенным чекбоксом/полем «одновременных генераций» (FE отправляет `null`, а не опускает ключ) → 500, грант не сохраняется, в логе traceback NotNullViolation. Ровно тот класс, который A26-фикс должен был закрыть (400 вместо 500), но для Value-границ, а не для null-границ.
Фикс-предложение: в сервисе/роуте 400 на `value is None` для трёх NOT NULL полей (или маппить null → «не передано»).

Проверено тестом? Нет: `test_admin_grant_access_not_positive_limit_400` (test_api.py:753-768) проверяет только `-5`. На `null` кейса нет.

---

## P2

### P2-1. PATCH /api/memory/{id}: явный `null` в NOT NULL полях → 500; длинный `category` → 500

`app/api/routes/memory.py:54-70`:

```python
data = body.model_dump(exclude_unset=True)
importance = data.get("importance")
if importance is not None and not 1 <= importance <= 10: ...   # None пропускается
if "text" in data and data["text"] is not None:
    data["normalized_text"] = normalize_memory_text(data["text"])
memory = await MemoryRepository(session).update_fields(memory_id, user.id, **data)
```

`MemoryPatchRequest` (`memory.py:19-24`) допускает `text/category/importance: str|None|int|None = None`. Запрос `PATCH {"text": null}` / `{"category": null}` / `{"importance": null}`:
- `update_fields` (`app/db/repositories/memories.py:75-85`) делает `setattr(memory, "text", None)`;
- `app/db/models/memory.py:29-32` — `text`, `category` — `Mapped[str]` NOT NULL, `importance: Mapped[int]` NOT NULL (миграция 0006:46-49 `Text NOT NULL / VARCHAR(32) NOT NULL / INTEGER NOT NULL`);
- flush → NotNullViolation → 500.

Дополнительно: `category: str` без `max_length` → `PATCH {"category": "<33+ символов>"}` → `StringDataRightTruncation` (String(32)) → 500.

Прод-проявление: правка памяти в Mini App с очисткой поля или длинной категорией → 500. Прямая аналогия: `app/api/routes/settings.py:59-62` для своих NOT NULL полей (`web_mode`, `memory_enabled`) эту валидацию **имеет** (`if ... is None: raise 400`) — memory.py про неё забыли.

### P2-2. GET /api/models не применяет model_overrides (A28): выключенная модель остаётся в списке пользователя

`app/api/routes/me.py:68-81`:

```python
probe = await get_probe_capabilities(session)
models = registry.filter_by_permissions(permissions.allowed_models)
return {"models": [_model_out(model, probe.get(model.model_id)) for model in models]}
```

`ModelRegistry.filter_by_permissions` (`app/llm/registry.py:43-51`) фильтрует только `m.enabled` **кода реестра**; DB-override из `model_overrides` (A28) не читается (в `app/api/routes/me.py` вообще нет `ModelOverrideRepository`). При этом admin-роут показывает effective-значение (`app/api/routes/admin_models.py:29`: `"enabled": overrides.get(model_def.model_id, model_def.enabled)`), а путь генерации override применяет и отказывает (`app/services/generation.py:1028-1034`, `_model_denial`).

Прод-проявление: владелец выключает модель в Admin → Models (запись в model_overrides), пользователь в Mini App всё ещё видит и выбирает её → каждая генерация отклоняется сообщением «модель отключена». UX-рассинхрон admin-UI ↔ user-UI; A28 «выглядит не работает».
Сюда же: `app/api/routes/chats.py:71-78` (`_validate_model`) и `app/api/routes/settings.py:67-72` при сохранении model_id не проверяют DB-override (сохранение пройдёт, генерация откажет) — тот же класс.

### P2-3. A24: нет admin-эндпоинта для enable/disable provider credential — «disabled = абсолютный запрет» недостижим через API

Семантика A14/A24 реализована: `app/services/credentials.py:22-25` — `if not credential.enabled: return None` (env не подставляется). Но переключить `enabled` может только ручной SQL:
- `app/db/repositories/credentials.py:45-52` — `ProviderCredentialRepository.set_enabled` существует и **не вызывается никем** (grep по app: вызовов нет);
- `app/api/routes/admin_providers.py` — только `GET /alibaba`, `POST /alibaba/key`, `POST /alibaba/smoke`. Роута disable нет; miniapp (`AdminProvidersPage.tsx`) тоже не вызывает.

Прод-проявление: «экстренно отключить скомпрометированный ключ Alibaba кнопкой» невозможно — только удалить запись/менять БД руками; при этом `GET /api/admin/providers/alibaba` честно показывает `enabled`, подразумевая управляемость.

### P2-4. (известное, подтверждено) Effective (DB) настройки не доходят до compactor

`app/main.py:52-59`:

```python
compactor = ContextCompactor(
    ..., keep_recent=settings.context_keep_recent, min_segment=settings.compaction_min_segment,
)
```

Env-снимок на старте процесса. Путь генерации: `generation.py:835-837` — `self._compactor.maybe_compact(prepared.chat_id)` без параметров; per-request effective-настройки (A13, `generation.py:998-1009`) компактору не передаются (в отличие от `ContextBuilder`, который пересоздаётся на запрос с effective-значениями — `generation.py:592-596`, и extractor, получающего `min_chars=prepared.memory_min_chars` — `generation.py:864`). Смена `context_keep_recent` в Admin → System меняет сборку контекста, но сегментацию компакции — нет (до рестарта).

---

## P3

### P3-1. admin_stats.py (переписан в 0be0524) — нулевое покрытие тестами

- `/api/admin/stats` отсутствует в `_ADMIN_V2_ENDPOINTS` (`tests/unit/test_api.py:421-428`) — даже гейт-теста (403/500-регистрации) нет, в отличие от `/api/admin/audit`;
- запросы `count_today_by_status` / `avg_ttft_today` / `list_recent_failed` не выполняет ни один тест: unit — «сломанная» session factory (`test_api.py:49-53`, RuntimeError), integration — фейки без БД, `RUN_API_INTEGRATION=1` в коде не подключён (grep: упомянут только в докстринге test_api.py:5-6). Т.е. A35/A28-метрики в прод выходят непрогнанными ни разу.

### P3-2. `stop_all_for_user` не делает `task.cancel()` — «остановка генераций» при ban/revoke может длиться до 240 с

`app/services/generation.py:112-119` (вызывается из `app/api/routes/admin_access.py:14-24`): ставит только `gen.cancellation.set()`. Пользовательская команда /stop использует `stop()` (`generation.py:121-131`), который дополнительно делает `task.cancel()` (A11 — «прерывает даже зависшее чтение HTTP»). При зависшем стрим-чтении флаг cancellation между событиями не проверяется — генерация живёт до `max_generation_seconds` (240 c). Роуты suspend/revoke/ban при этом уже ответили `{"stopped_generations": N}` — число завышено относительно фактической остановки.

### P3-3. GET-роуты с get_or_create без commit — запись создаётся и отбрасывается каждый запрос

`app/api/routes/settings.py:38-43` (GET /api/settings → `UserSettingsRepository.get_or_create`), `app/services/chats.py:34-51` (get_current_chat_id из GET /api/chats, GET /api/chats/{id}, PATCH перед commit): flush есть, commit нет; сессия закрывается (`get_db_session` только close) → INSERT откатывается. Функционально безопасно (значения дефолтов корректны), но каждый GET дергает лишний INSERT+rollback.

### P3-4. PUT /api/admin/system с явным `null` int-ключа: GET возвращает null вместо env-дефолта

`app/api/routes/admin_system.py:116-119` — валидация пропускает None (`value is not None and value <= 0`) → `repo.set_value(key, None)` пишет **JSON null** (не SQL NULL — колонка JSONB NOT NULL пропускает json-null). `_system_out` (`:59-83`) — `stored.get(key, env_default)` возвращает None, т.к. ключ в store есть → админ-UI показывает пустое поле вместо эффективного значения. Runtime при этом корректно падает в env-дефолт с warning (`app/services/settings.py:129-141`, `_positive_int(None) → _INVALID`). Косметика + шум в логах.

### P3-5. Валидация thinking в chats/settings считает дефолтную модель из env, а не из DB (A13)

`app/api/routes/chats.py:161-167` — `base_model_id ... or settings.default_model`; `app/api/routes/settings.py:74-76` — то же. DB `default_model` (Admin → System, A13) не читается: возможен лишний 400 (режим валиден для DB-дефолта) или пропуск (невалиден для env-дефолта). Сама генерация резолвит по effective — расхождение только на этапе валидации.

### P3-6. admin_search: мутации без audit и GET с записью

- `app/api/routes/admin_search.py:114-125` (`POST .../test`) — health_check пишет `search_backend_configs.health_status/last_error` (commit внутри `manager._record_health`, `app/search/manager.py:316-324`) — без записи в audit_log (все прочие admin-мутации аудируются; полный список — см. OK-7);
- `admin_search.py:47-59` (`GET /backends`) — создаёт стаб-строки и коммитит в GET; при параллельном первом заходе двух запросов get→insert гонится к `uq_search_backend_configs_backend_id` → возможен разовый 500 (IntegrityError).

### P3-7. Мелочь по конфигу и входам

- `app/config.py:59` — `session_token_ttl_seconds: int = 900` без валидации >0: `SESSION_TOKEN_TTL_SECONDS=0` → `create_session_token` ValueError (`app/api/auth.py:108-110`) → 500 на каждом /api/auth/telegram;
- `admin_access.py:37` и др. — `telegram_user_id: int` без `ge=1` (можно завести пользователя с отрицательным id);
- `admin_users.py:57-81` — N+1: на пользователя 2 запроса (get_grant + allowed_model_ids, внутри ещё get_mode) при limit≤200 → до ~600 запросов на страницу;
- удаление чата с активной генерацией: `generation_runs.chat_id` уходит в NULL (A08 ✓), но финальный INSERT assistant-сообщения в удалённый чат упадёт по FK `messages.chat_id` → генерация завершится ошибкой записи (грейсфул-деградация, сообщается как ошибка).

---

## OK (проверено, проблем нет)

1. **Класс P0 (несуществующие методы/атрибуты) в admin_stats.py и по всему scope — чисто.** AST-скан 82 вызовов репозиториев в app/api/**, app/services/** (кроме generation/llm_factory), app/main.py: 0 отсутствующих. Конкретно: `GenerationRunRepository.count_today_by_status` / `avg_ttft_today` / `list_recent_failed` — есть (`app/db/repositories/generation_runs.py:178-186, 163-176, 152-161`); `AuditLogRepository.list(limit, offset, action)` / `count(action)` — есть (`audit_logs.py:44-61`). Сериализаторы `_out`-функций обращаются только к существующим колонкам/полям (включая `ModelDefinition.supports_images` — property, `capabilities.py:48-50`). Все модули импортируются (import-check 27 модулей, exit 0).
2. **`GenerationRun.attempts` / `gemini_project_id`**: колонки есть — `gemini_project_id` в 0002 (`0002_chat_core.py:129`), `attempts` в 0009 (`0009_fix_v2.py:136-139`); модель — `generation_run.py:64-65`. Модель↔миграции: автодифф 21/21 таблиц по колонкам — совпадение.
3. **`func.extract("epoch", ...)` — валиден на проде, тестами не выполняется (см. P3-1).** Прод — PostgreSQL asyncpg (`config.py:18`). Offline-компиляция PostgreSQL-диалектом даёт корректный SQL: `SELECT avg(EXTRACT(epoch FROM generation_runs.first_token_at - generation_runs.started_at)) ...`. Тесты этот запрос не гоняют: unit — сломанная session factory, integration — фейки. SQLite-проблема не проявится (SQLite в проекте для API-слоя не используется).
4. **Миграции**: цепочка 0001→0009 линейна (по одной на ревизию, offline SQL сгенерирован целиком). Имена для `ON CONFLICT` существуют и совпадают: `uq_quota_minute_usage_project_id` (0003:87-92 ↔ `gemini.py:250-260`), `uq_quota_daily_usage_project_id` (0003:111-116 ↔ `gemini.py:261-271`), `uq_quota_policies_model_id` (0003:69 ↔ 0004:74). Partial unique `uq_generation_runs_active_chat` (0009:107-113) идентичен модели (`generation_run.py:33-40`); SET NULL для `generation_runs.chat_id` (0009:94-104) соответствует FK модели. Downgrade 0009 корректно документирует необратимость is_owner-чистки/backfill (не «молчаливая» потеря).
5. **auth**: подпись initData — HMAC-SHA256 по спецификации, `compare_digest`, freshness 24 ч + будущее с допуском 60 с (`api/auth.py:84-90`); session token — отдельный производный signing-ключ (`_signing_key`), TTL 900 с, exp проверяется, malformed-пути закрыты. 401 до БД, 403 с reason после evaluate (`dependencies.py:42-93`) — порядок верный; `expunge(user)` + `expire_on_commit=False` — чтение после закрытия сессии безопасно.
6. **owner-guard (A04)**: все 9 admin-роутеров имеют router-level `dependencies=[Depends(require_owner)]` (admin_users:15, admin_access:27, admin_gemini:30, admin_models:14, admin_memory:13, admin_providers:22, admin_search:15, admin_system:14, admin_stats:14). **Роутов без require_owner нет.** Identity — только numeric `telegram_user_id` (`dependencies.py:33-39`); флаг `is_owner` только UI-подсказка (тесты test_api.py:383-416 закрепляют). A08/A04-миграционная чистка (0009:116) согласована с default `_OWNER_TELEGRAM_ID` = 795063564 = `config.py:17`.
7. **audit на мутациях**: гранты (granted/extended/suspended/revoked), баны, model permissions, model enabled, все gemini-операции (add/bulk/enable/disable/move/delete/quotas/test/reset-counters), provider key/smoke, search backend put/key, system settings/prompt — всё аудируется через `admin_service.audit`. Без аудита — только: search health-test и стабы GET /backends (P3-6); пользовательские мутации (chats/settings/memory) — по дизайну не аудируются.
8. **A03 (mode all/list)**: единая точка чтения — `ModelPermissionRepository.allowed_model_ids` (`repositories/access.py:95-106`, внутри `UserModelAccessRepository.get_mode`); все три потребителя (API `dependencies.py:81`, `/api/auth/telegram` `auth.py:70`, bot-middleware `bot/middleware/access.py:78`) идут через неё. Пустой set ≠ None, owner всегда unrestricted (`services/access.py:76-86, 100`), тесты A03 зелёные. Backfill 0009 корректен (ON CONFLICT по PK).
9. **ban/suspend → stop**: suspend/revoke/ban вызывают `_stop_user_generations` после commit, в отдельной сессии (`admin_access.py:113-146`); генерации/грант коммитятся до stop (отказ stop не откатывает санкцию). Ограничение — P3-2.
10. **Лимиты concurrent/daily — check-then-act in-memory + DB-счётчики** (`generation.py:933-966`: `count_since`/`tokens_since` + `GenerationRegistry.count_active_for_user`): гонка двух одновременных запросов может пропустить оба через счётчик; известно/документировано. Атомарный admission на чат — partial unique index 0009 (A05/A12) на уровне БД.
11. **main.py**: startup `abort_stale` (A34) — `main.py:114-119`, commit есть; wiring: A13-цепочка полная для builder/extractor/retriever (per-request effective), SearchManager singleton передан в app (A30), uvicorn + polling supervisor и единый teardown (A30/A34) корректны; единственный env-снимок — compactor (P2-4).
12. **A13/A26-правки 4925c40 — ревью**: `memory_extraction_min_chars` — валидация `int>0` в PUT (`admin_system.py:116-119`, ключ в `_INT_KEYS`), GET-дефолт (`:80-82`), runtime-коэрсинг с env-fallback (`settings.py:161-165`), wiring до extractor per-call — всё на месте, тесты-регрессии зелёные. grant ValueError→400 (`admin_access.py:75-78`) — работает; дыра только в null-семантике (P1-1). Другие ValueError-источники: `UserModelAccessRepository.set_mode` — вызывается только с литералами 'all'/'list'; `SearchConfigRepository.upsert` — роуты передают только валидные ключи; `create_session_token` — P3-7; `crypto.decrypt` в `admin_gemini.test_project:343` — повреждённый Fernet-токен → 500 (edge, данные не теряются).

## Резюме

P0 не найден (класс из 0be0524 — несуществующие методы/атрибуты — в текущем HEAD чист; его единственный реальный P0, runtime-import ContextBuilder, закрыт в 4925c40). Найдены: **P1×1** (null в NOT NULL полях гранта → 500, нарушение контракта A26), **P2×4** (null/oversize в PATCH /api/memory → 500; /api/models игнорирует model_overrides; A24-тумблер провайдера недостижим; compactor на env-снимке — известное, подтверждено), **P3×7** (нулевое покрытие admin_stats; «мягкая» остановка при бане; get_or_create без commit; null в admin/system GET-семантике; thinking-валидация по env-дефолту; неаудируемые search-мутации; конфиг-мелочь).
