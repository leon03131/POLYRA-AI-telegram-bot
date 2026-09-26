# Round 4 fix — P1/P2 из аудита db-api (grant-null, /api/models DB-override, memory PATCH)

- Профиль: polyra-db-api (coding субагент).
- Дата: 2026-09-26. База: рабочий checkout O:\work\aibot, HEAD 4925c40 + незакоммиченные
  правки других агентов (не тронуты: generation.py, commands.py, gemini.py,
  smoke_providers.py, miniapp/**, tests/integration/**, chats.py).
- Writable scope этой волны: app/services/admin.py, app/api/routes/me.py,
  app/api/routes/memory.py, tests/unit/test_api.py — изменены только они.

## Изменённые файлы и суть правок

### 1. app/services/admin.py — P1-1 аудита (lead: «БАГ 1»)

`grant_access`: для NOT NULL-колонок гранта
(`max_concurrent_generations: Integer NOT NULL`, `can_use_web_search/can_use_memory:
Boolean NOT NULL` — app/db/models/access.py:40-42, миграция 0001) явный `None`
теперь отклоняется `ValueError(f"{field} cannot be null")` ДО обращения к БД
(цикл валидации стоит до `_get_or_create_user`). Роут
`POST /api/admin/access/grant` уже маппит ValueError → 400
(admin_access.py:75-78), поэтому вместо IntegrityError/500 клиент получает 400.
Контракт A26 «explicit null → записать NULL» сохранён только для nullable-полей
(expires_at, requests_per_day, token_limit, note) — их семантика не изменена,
докстринг сервиса уточнён соответственно.

### 2. app/api/routes/me.py — P2-2 аудита (lead: «БАГ 2»)

`GET /api/models` (`list_models`): добавлено чтение DB-override
`ModelOverrideRepository(session).get_all()` и фильтр
`overrides.get(model.model_id) is not False` поверх
`registry.filter_by_permissions(...)`.

Формат overrides (не менялся, только чтение): `dict[str, bool]`
`{model_id: enabled}` из таблицы `model_overrides`; пишется
`PUT /api/admin/models/{model_id}` (admin_models.py, `ModelPutRequest.enabled: bool`
→ `admin_service.set_model_enabled` → `ModelOverrideRepository.set_enabled`,
upsert по model_id). Потребление совпадает с:
- `generation.py` `_model_denial` (строка ~555): `overrides.get(model_id) is False` → отказ;
- `admin_models.py` `_model_out`: `overrides.get(model_id, model_def.enabled)` — override побеждает.

Семантика фильтра в me.py та же, что в `_model_denial`: override `False` скрывает
модель; override `True` НЕ «включает» обратно registry-disabled (модели с
`enabled=False` в реестре и так отсекаются `filter_by_permissions`). Мёртвый круг
«генерация отклоняет отключённую модель, а Mini App предлагает её выбрать» закрыт.

### 3. app/api/routes/memory.py — P2-1 аудита (lead: «БАГ 3»)

`PATCH /api/memory/{id}` (`patch_memory`): валидация до обращения к БД по образцу
`patch_settings` (settings.py:59-62):
- явный `null` в `text`/`category`/`importance` (NOT NULL-колонки,
  app/db/models/memory.py:29-32, миграция 0006) → 400 `«{field} cannot be null»`
  (раньше None шёл в `setattr` → NotNullViolation → 500);
- `category` длиннее 32 символов (String(32)) или пустая → 400
  `«category length must be in 1..32»` (раньше StringDataRightTruncation → 500);
- `text` из одних пробелов → 400 `«text must be non-empty»` (Mini App шлёт
  `editText.trim()`, пустое значение бессмысленно для normalized_text);
- прежняя проверка `importance` 1..10 сохранена.
Выбран вариант «HTTPException 400 в роуте», а не Pydantic Field-констрейнты:
Pydantic отдал бы 422 (не требуемые 400), а `str | None` всё равно пропускает
null мимо констрейнтов. Схема/Pydantic-модель полей не менялась
(`MemoryPatchRequest` остался `str | None` / `int | None`).

### 4. tests/unit/test_api.py — 13 новых тестов

- `test_admin_grant_access_null_in_not_null_fields_400` (×3 параметром):
  grant с `{"max_concurrent_generations": null}` / `can_use_web_search: null` /
  `can_use_memory: null` → 400 «cannot be null».
- `test_models_hides_admin_disabled_by_db_override`: модель с DB-override
  `enabled=False` (kimi-k3) отсутствует в `/api/models`, модель без override
  (qwen3.8-flash) остаётся.
- `test_models_override_true_keeps_model_visible`: override `True` не фильтрует.
- `test_memory_patch_null_field_400` (×3): PATCH `{"text": null}` и др. → 400.
- `test_memory_patch_category_length_out_of_bounds_400` (×2): 33 симв. и "" → 400.
- `test_memory_patch_whitespace_text_400`: `{"text": "   "}` → 400.
- `test_memory_patch_valid_fields_update_and_renormalize`: валидный PATCH → 200,
  поля обновлены, `normalized_text` пересчитан (`normalize_memory_text`).
- `test_memory_patch_invalid_fields_do_not_reach_repository`: importance=99 → 400,
  запись не тронута.
- Хелпер `_override_probe_capabilities` расширен параметром `overrides` и
  подменой `me_routes.ModelOverrideRepository` (иначе все прежние /api/models-тесты
  падали бы на реальном репозитории с fake-сессией); добавлены
  `_FakeModelOverrideRepository`, `_FakeMemoryRepository`, `_fake_memory`,
  `_override_memory_routes` и импорты `memory_routes`, `normalize_memory_text`.

Верификация «тесты ловят баг»: временный `git stash push` только трёх моих app-файлов
(с backup-patch; чужие файлы не тронуты) → на старом коде 10 из 12 новых
фиксирующих тестов FAILED (null/override/длина), 2 success-path-теста прошли и там
и там (ожидаемо — валидный ввод работал всегда) → `git stash pop`, правки
восстановлены, бэкап удалён. Итоговое состояние рабочего дерева проверено
(`git status`), чужие изменения не затронуты.

## Прогоны (реальные, сегодня, O:\work\aibot)

| Команда | Результат |
|---|---|
| `.venv\Scripts\python -m pytest tests\unit\test_api.py -q` | **56 passed** (exit 0; было 43 до правок: +13) |
| `.venv\Scripts\python -m pytest tests -q` | **567 passed** (exit 0; чужие правки живы, ничего не сломано) |
| `.venv\Scripts\python -m ruff check app tests` | **All checks passed!** (exit 0) |
| `.venv\Scripts\python -m mypy app tests scripts` | **Success: no issues found in 157 source files** (exit 0) |
| `-k` выборка новых тестов на старом коде (stash) | 10 failed / 2 passed — регрессионная ценность подтверждена |

## Рекомендации lead'у (вне моего scope, не делал)

1. **chats.py** (чужой в этой волне): `_validate_model` (chats.py ~:71-78) и
   `settings.py:67-72` при сохранении `model_id` не проверяют DB-override
   `model_overrides` — сохранение отключённой модели пройдёт (200), а генерация
   потом откажет. Рекомендуется тот же фильтр `ModelOverrideRepository.get_all()`
   → 400 «model disabled by admin» (класс P2-2 аудита, второй абзац).
2. P2-3 аудита (admin-тумблер `enabled` у provider credential —
   `ProviderCredentialRepository.set_enabled` существует, но не вызывается никем)
   и P3-пункты аудита (compactor env-снимок P2-4, admin_stats покрытие P3-1 и др.)
   — не входили в эту задачу, остаются открытыми.

## Инварианты

Model IDs, Gemini proxy, Alibaba endpoint/DIRECT/trust_env, cross-model fallback,
visible reasoning, БД/миграции — не тронуты (app-дифф только 3 файла выше).
Схема БД не менялась; данные/история/permissions не сбрасывались; explicit
allowlist не расширялся; формат model_overrides не менялся (только чтение).
