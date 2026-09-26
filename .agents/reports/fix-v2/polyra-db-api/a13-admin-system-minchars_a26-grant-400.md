# A13 + A26: admin system memory_extraction_min_chars / grant ValueError → 400

Дата: 2026-09-26. База: HEAD 0be0524, чистое дерево (мои файлы — правки поверх HEAD;
в `git diff` также видны правки других агентов в `app/context/compactor.py` и
`tests/unit/test_memory.py` — мною не тронуты).

## Изменённые файлы

### app/api/routes/admin_system.py (A13)
- Добавлен `_KEY_MEMORY_EXTRACTION_MIN_CHARS = "memory_extraction_min_chars"`:
  в `_ALL_KEYS`, в `_INT_KEYS`, в `SystemPutRequest` (`int | None`), в `_system_out`
  (`stored.get(...) → fallback settings.memory_extraction_min_chars`).
- Валидация «must be positive» покрывается существующим общим циклом по `_INT_KEYS`
  в `put_system` (проверено тестом на 0 и -5 → 400).
- Комментарий со ссылкой на A13 у новой записи в `_system_out`.

### app/api/routes/admin_access.py (A26)
- `grant_access` (POST /api/admin/access/grant): вызов `admin_service.grant_access`
  обёрнут в `try/except ValueError` → `HTTPException(400, detail=str(exc)) from exc`.
  Комментарий со ссылкой на A26.
- Другие роуты НЕ обёрнуты, т.к. ValueError оттуда недостижим (проверено чтением
  `app/services/admin.py` и grep `raise ValueError` по `app/`):
  extend/suspend/revoke/ban/unban → `_update_grant`/`_set_user_status` (возвращают bool);
  admin_users `put_user_models` → `set_model_permissions` (ValueError не бросает;
  `UserModelAccessRepository.set_mode` бросает только на невалидный mode — сервис
  передаёт лишь "all"/"list"). Изменений в admin_users.py не потребовалось.

### miniapp/src/api/types.ts (A13)
- `SystemSettings` + поле `memory_extraction_min_chars: number` (с комментарием A13).

### miniapp/src/admin/AdminSystemPage.tsx (A13)
- В секцию «Лимиты и контекст» добавлено числовое поле «Мин. объём сообщения для
  извлечения памяти (memory_extraction_min_chars)» по образцу
  memory_retrieval_limit/max_tool_iterations: `Input type="number" min={1}`,
  загрузка из GET (общий form → useSystemSettings), сохранение в PUT (общий save).
  Фронтовая валидация > 0 — `min={1}`, ровно как у соседних числовых полей.

### tests/unit/test_api.py (A13 + A26, +5 тестов = 43 всего)
- Фейки: `_FakeApiSession` (async commit no-op), `_FakeAdminSystemRepository`
  (get_many/set_value над общим dict), `_fake_audit` (no-op подмена
  `admin_service.audit`), хелпер `_override_admin_system` (dependency_overrides
  `get_db_session` + monkeypatch `admin_system_routes.SystemSettingRepository` и
  `admin_service.audit`).
- `test_admin_system_get_without_stored_returns_env_default` — GET без записи →
  env-дефолт (200).
- `test_admin_system_put_memory_extraction_min_chars_roundtrip` — PUT 300 → `{"ok": true}`,
  отражается в GET, в store записано ровно `{"memory_extraction_min_chars": 300}`.
- `test_admin_system_put_memory_extraction_min_chars_not_positive_400` (параметризовано
  0 и -5) — 400, detail содержит "must be positive", store не тронут.
- `test_admin_grant_access_not_positive_limit_400` — POST grant с
  `requests_per_day=-5` → 400 (до правки — необработанный 500), detail содержит
  "must be positive". Owner через `_override_user(is_owner=True)`, сессия — фейк.

## Отклонения от задания

- app/services/settings.py НЕ менялся: задача требовала добавить валидацию ключа
  «если её нет» — она уже есть (KEY/ALL_KEYS/EffectiveSystemSettings/`_positive_int`,
  строки 31, 41, 93, 161–165). Условие не наступило.
- admin_users.py НЕ менялся: ValueError-путей из его сервисов нет (см. выше).
- PUT-тест использует фейк-репозиторий по образцу `_FakeSystemSettingRepository`
  (existing-фейк из того же файла), расширенный до `set_value`, т.к. отдельного
  admin-system-харнесса в unit-наборе не существовало (integration-тестов admin
  system нет — проверено grep по tests/integration).

## Прогоны (реальные, этой сессии)

| Команда | Exit code | Результат |
|---|---|---|
| `.venv\Scripts\python -m pytest tests/unit/test_api.py -q` | 0 | 43 passed in 2.17s (было 38, +5 новых) |
| `.venv\Scripts\python -m ruff check app/api` | 0 | All checks passed! |
| `.venv\Scripts\python -m mypy app` | 0 | Success: no issues found in 129 source files |
| `npm run typecheck` (miniapp/) | 0 | tsc --noEmit без ошибок |
| `.venv\Scripts\python -m ruff check tests/unit/test_api.py` | 0 | All checks passed! |

## Блокеры

Нет.
