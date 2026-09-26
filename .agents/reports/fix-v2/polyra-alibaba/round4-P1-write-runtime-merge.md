# Fix: P1 round4 — --write-runtime стирает accepted-evidence при transient-сбое

Task: round4-P1 runtime capability merge (N03/A27)
Scope: `scripts/smoke_providers.py` (правка), `tests/unit/test_smoke_helpers.py` (новый).
Файлы других агентов (generation.py / commands.py / gemini.py / miniapp / app/services/settings.py)
не тронуты: settings.py только читался.

## Диагноз

`_write_runtime` формировал запись по ВСЕМ чекам модели (status ok в секции провайдера ok) и
делал upsert `set_value` → полная замена. Один transient 429/timeout на `thinking:<level>`
давал `status=fail` → режим выпадал из `accepted_thinking` → прежнее подтверждение стиралось.
Чек-структура (`status`/`detail`) не различала rejection от transient.

## Реализация

### 1. Классификация сбоев чеков (CheckResult, без смены статусов ok/fail/skipped)

`_fail(detail, *, kind=None)` — опциональное поле `kind`:
- `rejected` — провайдер явно отверг параметр: `ErrorCategory.INVALID_REQUEST`
  (400/404 → `InvalidRequestError`, проверено по `classify_http_status` в alibaba/gemini);
  проставляется в `_check_thinking_level` (catch `InvalidRequestError`) и `_provider_error_kind`.
- `transient` — 429/5xx/timeout/network/auth/forbidden/pool-exhausted/unexpected
  (`_run_check` по категории исключения) — «не смогли проверить», вердикта о параметре нет.
- без `kind` — функциональный сбой (стрим прошёл, события не сошлись) — негативный вердикт.

`status` остаётся ok/fail/skipped → `_count_failures`/`_print_table`/`--strict`-семантика
не изменилась (transient по-прежнему считается fail для `--strict`).

### 2. `_runtime_entries(report)` — теперь выдает ПРОМЕЖУТОЧНЫЕ вердикты (не итоговую запись)

`{accepted_thinking: [ok-режимы], rejected_thinking: [rejected+функциональные fail-режимы],
text_ok: True|False|None (None = transient/чека нет), at, endpoint}`.
Модели без результатов / секция провайдера не ok — по-прежнему не попадают (старые записи не затираются).
Извлечение разложено на `_mode_verdicts(checks)` и `_text_verdict(checks)` (C901).

### 3. `_merge_runtime_entry(old: dict | None, new: dict) -> dict` — чистая функция (P1-фикс)

Семантика merge (N03):
- свежий **ok** → режим в `accepted_thinking`;
- свежий **негативный вердикт** (rejected/функциональный fail) → режим убран;
- **transient** или нет свежего вердикта → как в `old` (positive evidence сохраняется);
- `old=None`/битый → только свежие ok-режимы (поведение до merge);
- `text_ok`: `None` (transient) не сбрасывает прежний `true`; битый old → `False`;
- `at`/`endpoint` всегда из `new` (свежесть записи обновляется — упрощение по brief п.4,
  в сценарии «ни одного ok» меняются только at/endpoint, accepted/text_ok не трогаются).

Валидация старого значения: `_clean_modes` (только строки; мусорные элементы отбрасываются,
валидные сохраняются), `text_ok` — только bool. Итоговая запись — те же 4 ключа
(`accepted_thinking/text_ok/at/endpoint`), что читают `GET /api/models` и admin (формат не менялся).

### 4. `_write_runtime` — merge перед upsert

Одним запросом читает прежние записи `repo.get_many([capability_probe:<id>...])` в той же сессии
до upsert; невалидные (не dict) старые значения трактуются как отсутствующие.

### 5. Сопутствующее

`_alibaba_recommendations`: transient-сбои больше не показываются как «отклонены» — отдельная
строка «не оценены из-за transient-сбоя». Хелп `--write-runtime` и модульный docstring дописаны.

## Тесты

`tests/unit/test_smoke_helpers.py` — 19 юнит-тестов, БД/сеть не нужны. Импорт скрипта —
`importlib.util.spec_from_file_location` (scripts — не пакет; регистрация в sys.modules).
Покрыто: transient не стирает старое accepted/text_ok (P1-репро с qwen low=429);
rejected убирает; функциональный fail убирает; ok добавляет без дублей; old=None;
битый old (не те типы; мусор в списке режимов); text transient → old False остаётся False /
без old → False; all-transient → меняются только at/endpoint; формат записи не расширен;
`_runtime_entries` классификация (ok/transient/rejected/functional/skipped), секции не-ok
пропускаются, битые model_report/чеки не роняют; `_provider_error_kind` по категориям;
`_fail` без kind не содержит поле.

## Прогоны (реальные, exit codes)

- `.venv\Scripts\python -m pytest tests\unit -q` → **521 passed in 8.07s, exit 0**
  (включает правки других агентов в рабочих файлах — регрессий нет);
- `.venv\Scripts\python -m ruff check scripts tests` → **All checks passed, exit 0**;
- `.venv\Scripts\python -m mypy app tests scripts` → **Success: no issues in 157 files, exit 0**;
- `.venv\Scripts\python scripts\smoke_providers.py --help` → usage напечатан, **exit 0**.

## Blockers / заметки

- Blockers нет. Live-проверка `--write-runtime` на реальной БД сознательно не выполнялась
  (запрет неконтролируемых live-вызовов); merge-логика покрыта юнит-тестами, включая
  оффлайн-репро из аудита.
- «Rejected» для AUTH/FORBIDDEN намеренно НЕ назначается (не вердикт о параметре) — такие
  сбои классифицированы transient: conservative-preserving, соответствует N03.
- Запись `at` при all-transient обновляется (TTL продлевается) — согласовано с brief (п.4, упрощение).
