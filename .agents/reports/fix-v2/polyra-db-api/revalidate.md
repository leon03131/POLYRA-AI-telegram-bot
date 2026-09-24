# polyra-db-api — ревалидация A- findings против текущего кода

Дата: 2026-09-24. Субагент: polyra-db-api (read-only ревалидация, production код не менялся).
База сравнения: `docs/POLYRA_KIMI_HANDOFF_V2/audit_original/AUDIT.md` (A01–A35), актуальный
checkout `O:\work\aibot`. Легенда verdict: **CONFIRMED** — дефект воспроизводится в текущем
коде; **FIXED** — исправлено; **PARTIAL** — частично.

---

## A03 [P1] Пустой allowlist [] → unrestricted — **CONFIRMED**

Цепочка доказательства (все три звена на месте):

1. `app/services/admin.py:222-246` `set_model_permissions`: `clear_for_user()` вызывается
   всегда, вставка — только `if allowed_models is not None`. При `[]` цикл не выполняется →
   все строки удалены, новых нет. Audit пишет `metadata={"allowed_models": None}` при пустом
   списке (строка 245: `if allowed_models else None`) — т.е. даже в аудите [] и null
   неразличимы.
2. `app/db/repositories/access.py:68-73` `allowed_model_ids`: `if not rows: return None` —
   отсутствие строк трактуется как None.
3. `app/services/access.py:100` `evaluate_access`: `allowed_models=None` →
   `EffectivePermissions.allowed_models=None`; `is_model_allowed` (109-114): `None → True`
   для любой модели.

API-путь: `app/api/routes/admin_users.py:96-118` принимает `allowed_models: list[str] | None`,
`[]` валиден и уходит в сервис без изменений.

Что чинить: разделить «unrestricted» (нет записей) и «allowlist» (таблица имеет sentinel или
отдельный режим, пустой allowlist = запрет всех моделей). Варианты: явный sentinel-ряд
(`model_id='*'`/`__none__`) или колонка-режим на гранте; плюс data-миграция существующих
пользователей, у которых владелец ранее сохранял [] (сейчас неотличимы от unrestricted).

## A04 [P1] Owner identity: флаг is_owner доверен — **CONFIRMED**

- `app/api/dependencies.py:33-35` `is_owner_user(user, settings)` =
  `user.is_owner or user.telegram_user_id == settings.owner_telegram_id` — флаг БД
  самостоятельно даёт права owner (используется в `get_current_user:79` и `require_owner:101`).
- `app/bot/routers/commands.py:155-159` `/admin` — проверяет **только флаг** `user.is_owner`,
  numeric id не сверяется.
- Третья семантика: `app/bot/middleware/access.py:59-61,80` — в `evaluate_access` передаётся
  только numeric-совпадение (`is_owner=is_owner`), флаг при этом **записывается** в БД.
  Четвёртая: `app/api/routes/auth.py:50-52,71` — `is_owner or user.is_owner` + запись флага.
  Итого три разных правила в трёх слоях (API runtime: flag OR id; bot middleware: id only;
  bot /admin: flag only; auth login: OR + persist).
- Путь повышения прав: прямой self-escalation для произвольного пользователя не найден —
  флаг выставляется только при совпадении с `settings.owner_telegram_id` (auth.py:50-52,
  middleware/access.py:59-61). Риск — **stale privilege**: после смены `OWNER_TELEGRAM_ID`
  прежний владелец сохраняет `is_owner=True` навсегда и проходит и API `require_owner`,
  и `/admin`. `KNOWN_ISSUES.md:41-43` (#10) это признаёт: «is_owner не отзывается
  автоматически».
- Миграция очистки: **нужна**. Колонка есть с 0001 (`0001_initial.py:51`, без server_default),
  ни одна миграция 0002–0008 флаг не трогает. Требуется: (а) единая owner identity —
  только `telegram_user_id == settings.owner_telegram_id` (или явно согласованный конфиг),
  флаг не даёт полномочий; (б) data-миграция `UPDATE users SET is_owner = FALSE WHERE
  telegram_user_id <> <owner>` либо полное удаление колонки; (в) убрать запись флага из
  auth.py/middleware или оставить его чисто информационным.

## A05 [P1] Гонка one-active-generation — **CONFIRMED**

- Check: `app/services/generation.py:700` `find_active_for_chat(chat_id)` внутри `_prepare`;
  register: `generation.py:338-347` — уже **после** возврата `_prepare` (между ними await
  истории, записи user-message, лимитов, retrieval памяти, создания run, commit). Классический
  check-then-act по in-memory dict (`GenerationRegistry._by_draft`, register — обычное
  присваивание, generation.py:83-85).
- DB-уникальность отсутствует: модель `app/db/models/generation_run.py` без `__table_args__`;
  миграция `0002_chat_core.py:148` создаёт только неуникальный `ix_generation_runs_chat_id`;
  grep по всем версиям 0001–0008 — partial unique index на `(chat_id) WHERE status IN
  ('queued','running')` не существует.
- Per-user лимиты тоже до reservation: `count_since`/`tokens_since` (generation.py:662,670)
  читаются до создания run (787) и до регистрации в реестре; `count_active_for_user` (677)
  читает тот же in-memory dict. Мультипроцессный деплой не защищён в принципе.

Что чинить: partial unique index + обработка `IntegrityError` как «чат занят»; per-chat lock
до чтения истории; атомарная reservation per-user concurrency/суточных лимитов.

## A08 [P1] generation_runs.chat_id ON DELETE CASCADE — **CONFIRMED**

- Модель: `app/db/models/generation_run.py:18-22` —
  `ForeignKey("chats.id", ondelete="CASCADE")`.
- Миграция: `app/db/migrations/versions/0002_chat_core.py:134-139` —
  `fk_generation_runs_chat_id_chats ... ondelete="CASCADE"`. Поздних миграций, меняющих FK, нет.
- Эффект: `app/api/routes/chats.py:170-179` `DELETE /chats/{id}` → `ChatRepository.delete`
  стирает чат → каскадно стираются все generation_runs → `count_since`/`tokens_since`
  (`app/db/repositories/generation_runs.py:68-87`), считающие дневные лимиты по
  generation_runs, обнуляются. Удаление чатов = сброс суточного учёта запросов/токенов.
  (Дополнительно теряется usage-история для stats.)

Что чинить: durable usage ledger вне каскада чата (отдельная таблица по user_id с датой) либо
ослабление FK (`SET NULL` на chat_id + NOT NULL снять / tombstone). Миграция обязательна.

## A13 [P1] Admin system settings не читаются runtime — **CONFIRMED**

- Писатель есть: `app/api/routes/admin_system.py:81-127` PUT пишет `system_settings` через
  `SystemSettingRepository.set_value`; GET читает с fallback на env.
- Читателя в runtime **нет**: grep `SystemSettingRepository` по `*.py` — только
  `app/api/routes/admin_system.py` и сам репозиторий/`__init__`. Путь генерации использует
  env-Settings напрямую: `generation.py:766` (`default_system_prompt`), `:770`
  (`recent_history_limit`), `:760` (`memory_retrieval_limit`), `:524`
  (`max_tool_iterations`), `resolve_model_and_thinking` — `settings.default_model` (181);
  `ContextBuilder`/`ContextCompactor` конструируются из env в `main.py:46-58`. Рестарт тоже
  не подхватывает DB-значения.
- `memory_extraction_min_chars` **не передан**: `app/main.py:66-72` создаёт
  `MemoryExtractor(session_factory=..., llm_stream=..., memory_model=..., memory_thinking=...,
  dedup_threshold=...)` — аргумента `min_chars` нет, хотя `Settings.memory_extraction_min_chars`
  существует (`app/config.py:47`) и конструктор его принимает
  (`app/memory/extractor.py:182`). Env-override игнорируется (работает только потому, что
  дефолты совпадают = 200).

Что чинить: единый SettingsService (chat → user → DB system → env) с типизированными effective
settings и инвалидацией кеша; минимум — прокинуть `min_chars=settings.memory_extraction_min_chars`
в main.py и читать system_settings в generation path.

## A14 [P1] Disabled credential + env fallback возвращает ключ — **CONFIRMED**

`app/services/credentials.py:20-23` дословно:

```python
credential = await ProviderCredentialRepository(session).get(provider)
if credential is not None and credential.enabled:
    return crypto.decrypt(credential.encrypted_api_key)
return env_fallback or None
```

Запись существует, `enabled=False` → условие ложно → `return env_fallback or None` → при
заданном `ALIBABA_API_KEY` ключ возвращается. Потребитель: `app/services/llm_factory.py:48-53`
(`get_provider_api_key(session, crypto, "alibaba", settings.alibaba_api_key)`), т.е. бот
продолжает ходить в Alibaba после «отключения» в Mini App. Примечание: endpoints
enable/disable для provider credential вообще отсутствуют (`admin_providers.py` имеет только
GET status и POST key) — выставить enabled=False через API нельзя, но защита обязана работать
на уровне чтения. Дополнительно: `alibaba_providers` кешируется по api_key без eviction
(llm_factory.py:54-57) — после смены/отключения ключа старый client живёт.

Что чинить: `if credential is not None: return decrypt(...) if credential.enabled else None`;
env fallback — только при отсутствии записи; eviction кеша клиентов при смене ключа.

## A26 [P2] Grant update: null-лимиты не снимаются — **CONFIRMED**

`app/services/admin.py:91-100` в `grant_access`: `fields.update({key: value for key, value in
optional.items() if value is not None})` — `requests_per_day/token_limit/
max_concurrent_generations/can_use_*/note`, переданные как `None` (explicit null из UI —
`admin_access.py:15-25` допускает все поля `None`), отбрасываются → в `upsert_grant` старые
значения не затираются. Снять ранее установленный числовой лимит через API невозможно.
Вторая половина finding тоже актуальна: revoke/suspend/ban меняют только строки БД
(`admin.py:159-197`); права передаются в генерацию snapshot-ом при старте update
(middleware), работающая генерация не отменяется — немедленной остановки нет.

Что чинить: различать `unset` и `explicit null` (pydantic `model_fields_set` /
`exclude_unset` уже частично есть в роутах — пронести в сервис); policy немедленного revoke
(cancellation активных генераций пользователя + перепроверка перед side effects).

## A29 [P2] Gemini dedup по key_hint=last4 — **CONFIRMED**

- Bulk: `app/api/routes/admin_gemini.py:136-139` — `key_hint = api_key[-4:]`, `if key_hint in
  existing_hints: skipped += 1`. Два разных ключа с общим суффиксом → второй молча пропущен.
- Single add: `admin_gemini.py:83-104` — дедуп только по **имени** (409), по ключу/hint не
  проверяет → один и тот же ключ можно добавить под разными именами (фиктивные quota buckets).
- CLI: `scripts/import_gemini_keys.py:79-81` — `key_hint=key[-4:]`, дедуп отсутствует вовсе
  (ни по hint, ни по полному ключу); атомарность — только по конфликту уникального имени.
- Google project identity не проверяется нигде.

Что чинить: keyed fingerprint полного ключа (например `hmac_sha256(master_key, api_key)[:N]`
hex) в отдельной колонке с unique constraint; согласовать API и CLI; осмысленные причины skip.

## A34 [P1] Startup — **PARTIAL → CONFIRMED по обеим частям**

- `delete_webhook(drop_pending_updates=True)`: **присутствует**, `app/main.py:111` —
  выполняется при каждом старте; pending updates за время простоя уничтожаются (аудит
  требовал по умолчанию сохранять, deliberate discard — явный режим).
- Stale generation_runs recovery: **отсутствует**. В `main()` нет reconciliation записей
  `status IN ('queued','running')`, оставшихся от прошлого процесса (нет ни пометки
  aborted/failed, ни освобождения reservations). `GenerationRunRepository` имеет только
  create/get/finish/count_since/tokens_since — метода массовой финализации нет.
  Background-задачи (`_spawn_background`, generation.py:581-593) создаются без drain на
  shutdown; `asyncio.gather(dp.start_polling, uvicorn.serve)` (main.py:118) без общего
  supervisor.

Что чинить: startup-проход `UPDATE generation_runs SET status='aborted', finished_at=now()
WHERE status IN ('queued','running')` до начала polling; `drop_pending_updates=False` по
умолчанию (или явный конфиг); lifecycle supervisor с cancel/drain.

## A35 [P2] Admin stats/audit — **CONFIRMED (неполнота сохраняется)**

Есть (`app/api/routes/admin_stats.py:18-78`): `users_total`, `users_active_7d`,
`generations_today`, `generations_by_status`, `tokens_today{input,output}`,
`gemini_projects{total,enabled,healthy}`, `tool_calls_today`.

Нет:
- per-model разрез (model_id есть в `GenerationRun`, не агрегируется);
- TTFT/latency (`first_token_at`/`finished_at` в модели есть — distribution/percentiles не
  считаются);
- error rate / 429 rate (`error_category`/`error_code` есть — не агрегируются, breakdown по
  категориям отсутствует);
- per-Gemini-project usage и фактические minute/day counters (`quota_minute_usage`/
  `quota_daily_usage` не отдаются ни одним endpoint);
- per-user/per-search-backend разрезов; диагностики error runs.

Audit route (`admin_stats.py:81-101` + `repositories/audit_logs.py:38-42`): только
`limit` (default 50, cap 500). **Нет offset** (пагинация невозможна дальше первой страницы),
**нет фильтров** по action/actor/дате. Репозиторий поддерживает лишь `list_recent(limit)`.

## API-сторона A01 — **OK (со стороны backend)**

`app/api/routes/chats.py:46-58` `_chat_out`: `"id": str(chat.id)` — строковый UUID на всех
ответах (list/create/patch). Path-параметры типизированы `uuid.UUID` (`chats.py:105,117,...`).
Замечание аудита касалось frontend (`Number(id)` в miniapp) — вне writable scope этого
профиля; backend-контракт консистентен. Аналогично `admin_users.py:44` (`"id": str(user.id)`)
и `admin_gemini.py:52`.

## A24 [P2] list_chats без пагинации/archived — **CONFIRMED (на уровне API)**

- Роут `app/api/routes/chats.py:79-87` `GET /chats` вызывает
  `ChatRepository(session).list_for_user(user.id)` **без аргументов** → дефолты
  `include_archived=False, limit=50, offset=0` (`repositories/chats.py:29-43`). Query-параметров
  `limit/offset/include_archived` в сигнатуре роута нет; `total`/cursor в ответе нет.
- Репозиторий при этом уже умеет `include_archived/limit/offset` — проброшено не будет до
  тех пор, пока роут не проксирует параметры.
- Отдельного `GET /chats/{chat_id}` (получение чата по UUID, в т.ч. архивного) не существует
  — есть только open/patch/archive/delete.

Что чинить: query params + archived filter (+cursor/offset), endpoint получения одного чата;
согласовать current-chat поведение при archive/delete.

## A25 [P2] _chat_out без system_prompt_override — **CONFIRMED (на уровне API)**

`_chat_out` (`chats.py:46-58`) возвращает id/title/model_id/thinking_setting/web_mode/
memory_enabled/created_at/updated_at/archived_at/is_current — **`system_prompt_override`
отсутствует**, хотя PATCH его принимает (`ChatPatchRequest.system_prompt_override`,
chats.py:37) и хранит (`Chat.system_prompt_override`, модель chat.py:28). Прочитать
существующий override через API невозможно → UI сбрасывает поле в пустую строку (риск затереть
при сохранении). Часть finding про unknown-model fallback (`resolve_model_and_thinking`,
generation.py:181-186 — молчаливый fallback на `settings.default_model`) также на месте.

Что чинить: добавить `system_prompt_override` в `_chat_out` (и при желании effective-поля);
для unknown saved model — явный отказ/сигнал вместо silent fallback (см. A25 полный текст).

## A28 [P2] Недостающие admin endpoints — **CONFIRMED**

Фактический реестр admin-роутов (`app/api/app.py:45-58`): admin_users (list, GET/PUT models),
admin_access (grant/extend/suspend/revoke/ban/unban), admin_gemini (projects
list/add/bulk/enable/disable/move/delete, quotas get/put), admin_providers (alibaba GET +
POST key), admin_search (backends list/put/key/test), admin_system (GET/PUT),
admin_stats (stats, audit).

Отсутствуют vs ТЗ/acceptance:
- **Models CRUD / enable / capabilities** — роутера нет вовсе (реестр моделей кодовый,
  `default_registry()`); управление моделями из Mini App невозможно.
- **Gemini: Test project** (живой smoke выбранного проекта) — нет; **Reset Local Counters** —
  нет; просмотр фактических `quota_minute_usage`/`quota_daily_usage` — нет endpoint.
- **Alibaba Run Smoke Test** — нет (только GET status + set key).
- **Provider health runtime storage/endpoints** — нет (у GeminiProject есть поля
  health_status/last_error_*, но ручного trigger/просмотра health по провайдерам нет;
  для Alibaba enabled-флаг даже не выставляется через API).
- Admin Memory (модерация памяти пользователей) и диагностика generation errors — нет.

Примечание: `docs/API.md` сам не описывает эти endpoints — т.е. расхождение с ТЗ (A28), а не
с внутренней докой; при реализации docs/API.md нужно расширить синхронно.

---

## Сводная таблица

| Item | Verdict | Ключевое доказательство |
|---|---|---|
| A03 | CONFIRMED | admin.py:236-238; repositories/access.py:71-72; services/access.py:112-114 |
| A04 | CONFIRMED | dependencies.py:33-35; commands.py:157; KNOWN_ISSUES #10; миграции очистки нет |
| A05 | CONFIRMED | generation.py:700 vs 338; 0002:148 (нет unique); нет partial index в 0001–0008 |
| A08 | CONFIRMED | models/generation_run.py:20; 0002_chat_core.py:134-139 |
| A13 | CONFIRMED | grep SystemSettingRepository → только admin_system.py; main.py:66-72 без min_chars |
| A14 | CONFIRMED | credentials.py:21-23 (disabled → env_fallback); llm_factory.py:49-51 |
| A26 | CONFIRMED | admin.py:100 (drop None); revoke без остановки генераций |
| A29 | CONFIRMED | admin_gemini.py:136-139; import_gemini_keys.py:80; single-add без key dedup |
| A34 | CONFIRMED | main.py:111 (drop_pending_updates=True); recovery отсутствует |
| A35 | CONFIRMED | admin_stats.py: нет per-model/TTFT/429/per-project; audit — только limit |
| A01 (API) | OK | chats.py:48 `"id": str(...)`; frontend часть — к polyra-miniapp |
| A24 (API) | CONFIRMED | chats.py:83 без параметров; нет GET /chats/{id} |
| A25 (API) | CONFIRMED | chats.py:46-58 без system_prompt_override |
| A28 | CONFIRMED | нет models CRUD, gemini test/reset, alibaba smoke, provider health |

## Приоритетные фиксы (предложение к dispatch)

P1-блокеры целостности/безопасности: A14 (3-строчный фикс в credentials.py + тест матрицы),
A04 (единая owner identity + data-миграция is_owner), A03 (режим unrestricted vs allowlist +
миграция), A08 (ledger/FK-миграция), A05 (partial unique index + lock), A13 (min_chars в
main.py — однострочник; полный SettingsService — отдельная задача), A34 (startup recovery).
P2: A26 (unset vs null протокол), A29 (fingerprint + unique), A24/A25 (API-контракты),
A35 (агрегации + audit pagination), A28 (новые admin endpoints вертикально).
