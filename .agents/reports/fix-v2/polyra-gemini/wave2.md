# polyra-gemini — wave2: A09 (atomic check-and-reserve + reservation windows) и A10 (model-scoped 429 cooldown + bounded retry)

**Дата:** 2026-09-24
**Субагент:** polyra-gemini (C — Gemini/pool/квоты)
**Контракты:** `.agents/POLYRA_FIX_V2_CONTRACTS.md` §5 (A09/A10)
**Baseline:** lead переписал `stream_with_failover` на `_stream_attempt`/finally — работа велась поверх этой версии, структура сохранена.

---

## Scope (строго)

Изменены только файлы выданного writable scope; `app/db/**`, `app/services/**`, `app/llm/base.py`, `app/llm/events.py` не тронуты. Git-операций не было. pytest не запускался (ограничение задачи); верификация — `py_compile`, ruff, SQL compile-check без БД, ручной in-memory прогон тестов через asyncio-harness (см. «Тесты»).

| Файл | Изменение |
|---|---|
| `app/llm/gemini/quota.py` | +`QuotaReservation`; `QuotaStore` Protocol: optional `check_and_reserve_atomic`; `QuotaTracker.check_and_reserve` → `QuotaReservation \| None` (atomic-path через `getattr`, иначе legacy read+reserve); `reconcile(reservation, *, input_tokens)` — окна резервации |
| `app/llm/gemini/store_db.py` | `DbQuotaStore.check_and_reserve_atomic` — одна сессия/транзакция, `INSERT ... ON CONFLICT DO UPDATE ... WHERE ... RETURNING` для обоих окон, rollback при отказе любого окна |
| `app/llm/gemini/pool.py` | in-memory `dict[(UUID, model), datetime]` для 429-cooldown; `retry_delay=0.5` и bounded same-project retry; `PooledCredential.quota_reservation`; `report_success(..., reservation=...)` |
| `app/llm/gemini/__init__.py` | экспорт `QuotaReservation` |
| `tests/unit/test_gemini_pool.py` | адаптация 5 существующих тестов + 12 новых; +`AtomicFakeQuotaStore`, +`RacyLegacyQuotaStore` |

Новый файл `test_gemini_quota_atomic.py` НЕ создан: секция «ТЕСТЫ» его допускала, но writable scope ограничен двум существующими test-файлами — все тесты помещены в `test_gemini_pool.py`.

## Решения

### A09.1 — атомарный check-and-reserve (`DbQuotaStore.check_and_reserve_atomic`)

- Одна сессия = одна транзакция на оба окна. Каждое окно — `pg_insert(...).on_conflict_do_update(constraint=..., set_={requests_count: +1}, where=..., ).returning(id)`:
  - minute: `WHERE requests_count < :rpm AND tokens_in < :tpm` (только заданные лимиты);
  - daily: `WHERE requests_count < :rpd`;
  - строки нет → INSERT (свежее окно при положительном лимите всегда допустимо);
  - строка есть → UPDATE только при непрошедшем лимите; конкурентные транзакции сериализуются row-lock'ом `ON CONFLICT`, WHERE переоценивается на залоченной строке → check-and-increment атомарен при READ COMMITTED;
  - `RETURNING` пуст при непрошедшем WHERE → `False`.
- Отказ любого окна → `session.rollback()` обоих инкрементов (счётчики не «протекают» отказанными запросами); оба прошли → `commit`, `True`.
- Лимит `<= 0` — запрет ДО записи: INSERT свежего окна обходит WHERE (у PG нет WHERE на insert-ветке `ON CONFLICT`), поэтому `rpm/tpm/rpd <= 0` отсекаются ранним `return False` — семантика совпадает с legacy-путём (`0 >= 0 → блок`), строки не создаются.
- **TPM — post-fact (зафиксировано как документированное поведение):** на момент reserve токены запроса неизвестны; резервируется только request count. TPM проверяется по уже учтённым `tokens_in`; reconcile довносит фактические токены позже и допуск сверх TPM после факта НЕ блокируется — это local-accounting (реальные 429 от Google остаются защитой последней линии, обрабатываются A10-cooldown).
- `where=None` при отсутствии условий: проверено compile-check — WHERE не рендерится, unconditional upsert.
- Legacy `reserve/get_*` в `DbQuotaStore` сохранены (fallback для store без atomic + reconcile через `add_tokens`).

### A09.2 — окна резервации для reconcile

Выбран вариант **«возвращать окна»**, а не «хранить на инстансе tracker'а»: в прод-разводке (`build_gemini_pool`) `quota_for_model` создаёт НОВЫЙ `QuotaTracker` и на acquire, и на `report_success` — состояние инстанса между ними терялось бы. Поэтому:

- `check_and_reserve(...) -> QuotaReservation | None` (dataclass: project_id, model_id, minute_ts, day).
- Пул протягивает резервацию: `PooledCredential.quota_reservation` → `_stream_attempt.finally` → `report_success(..., reservation=...)` → `tracker.reconcile(reservation, input_tokens=...)`.
- `report_success` без `reservation` (внешний вызов вне acquire) — fallback на окна `now` (обратная совместимость сигнатуры).
- Граница минуты между reserve и reconcile больше не уводит токены в чужое окно — покрыто тестом на уровне tracker'а и сквозным тестом пула (now_fn: t_reserve → t_finish через минутную границу).

### A09.3 — идемпотентность reconcile

По контракту вызывающей стороны (документировано в `QuotaTracker.reconcile` и `_stream_attempt`): store повторный вызов не дедуплицирует; пул вызывает reconcile **ровно один раз на успешную попытку** — `finally` в `_stream_attempt` срабатывает однократно и только при `terminal_seen` (Done). Попытки без Done (ошибка/cancel/retry-проигрыш) reconcile не вызывают. Существующий `test_success_reconciles_input_tokens` фиксирует ровно один `add_tokens`.

### A10.1 — cooldown scope

- `429` (RATE_LIMIT) → in-memory `self._model_cooldowns[(project_id, model_id)] = now + retry_after|cooldown_429`; БД `set_cooldown` больше НЕ вызывается для 429 (DB-колонка остаётся project-level — схема владельца G, не трогаем). `mark_error` в БД сохраняется.
- `acquire` фильтрует оба уровня: `cooldown_until` из БД (project) и in-memory (project+model, протухшие записи удаляются лениво при проверке). Размер dict ограничен projects×models, роста вне cooldown-окон нет.
- 403 (не PERMISSION_DENIED) → DB project cooldown 300s (без изменений); 401/PERMISSION_DENIED → `mark_unhealthy` disable (без изменений); 5xx/network/timeout → DB transient cooldown 30s (без изменений, но теперь выставляется ПОСЛЕ неудачного retry, см. ниже).

### A10.2 — bounded retry

- `stream_with_failover`: per-attempt флаг `retried`; категории SERVER/NETWORK/TIMEOUT, ошибка ДО первого события → `asyncio.sleep(retry_delay)` (default 0.5s, параметр конструктора — тесты работают с 0.0) → один повтор того же проекта (тот же `cred`/`req2`, без повторного acquire и без дубля в `attempts`/`attempt_ids`).
- `report_error` вызывается один раз по ФИНАЛЬНОЙ ошибке: если retry успешен — ошибка не репортится вовсе (проект не наказывается cooldown'ом за самоизлечившийся сбой); если retry упал с другой категорией (напр. 5xx → 429) — обработка по финальной категории.
- Ошибка после первого события (partial stream) — по-прежнему без retry и без ротации (N03); 400/safety — сразу наверх; CancelledError — проброс без report_error (в т.ч. из `asyncio.sleep`).

## Тесты (верификация)

Ограничение задачи: pytest не запускать. Выполнено:

1. `py_compile` всех 5 изменённых файлов — OK.
2. `ruff check app/llm/gemini/ tests/unit/test_gemini_pool.py` — **All checks passed** (exit 0).
3. SQL compile-check (SQLAlchemy → строка PostgreSQL, без подключения): оба statement рендерят `ON CONFLICT ON CONSTRAINT ... DO UPDATE SET requests_count = quota_*_usage.requests_count + %s WHERE ... RETURNING id`; `where=None` не рендерит WHERE — OK.
4. Ручной in-memory прогон всех тестов `test_gemini_pool.py` через asyncio-harness (без pytest; DB/сеть не затрагиваются; shim для monkeypatch-фикстуры): **36/36 PASSED, exit=0**.

Покрытие новым/адаптированным тестами:

- Атомарность: `test_atomic_check_and_reserve_allows_exactly_one_parallel_at_rpm1` (gather 2×reserve, rpm=1 → ровно 1, уровень store), `test_concurrent_acquire_rpm1_allows_exactly_one` (уровень пула), `test_tracker_prefers_atomic_store_when_available` (legacy reserve не вызывается), `test_atomic_store_respects_rpd_tpm_and_zero_limits`, `test_legacy_store_race_demonstrates_need_for_atomic` (контрастный: legacy-путь с suspension-точкой после read допускает обоих при rpm=1 — фиксирует мотивацию A09; legacy fallback при этом сохранён и покрыт остальными тестами на `FakeQuotaStore`).
- 429 model-scoped: `test_429_cooldown_is_scoped_to_model` (модель B того же проекта доступна), `test_429_rotates_to_next_project_and_reports_success` (БД cooldown не ставится, in-memory = NOW+60s), `test_429_retry_after_overrides_default_cooldown`, `test_model_cooldown_expires_lazily`.
- Bounded retry: `test_transient_error_retried_once_same_project_then_succeeds` (p1→p1 успех, без mark_error/cooldown), `test_5xx_cooldown_30s_and_next_project` (p1→p1→p2, cooldown 30s, один mark_error), `test_network_error_also_retried_once`, `test_transient_retry_happens_at_most_once_per_project`, `test_transient_retry_uses_configured_delay` (default 0.5s), `test_retry_failure_with_new_category_uses_final_error` (5xx→429: финальная категория), `test_error_after_first_event_not_rotated` (после события — без retry, без ротации).
- Reconcile в окнах резервации: `test_reconcile_uses_reservation_windows`, `test_success_reconciles_into_reservation_window_across_minute_boundary`.
- attempts/attempt_ids (lead): `test_attempts_metadata_recorded_per_project_attempt` — ["p1","p2"]/ids, retry не создаёт новых записей.

## Не проверено / риски

- **Live PostgreSQL не прогонялся** (pytest запрещён, БД нет): `check_and_reserve_atomic` проверен на уровне SQL-рендера и семантики PG (`ON CONFLICT` row-lock + WHERE + RETURNING), но не на реальной БД. Рекомендуется integration-тест владельцу G: два параллельных `check_and_reserve_atomic` при rpm=1 против тестовой PG → ровно один `True`; отказ daily при успешном minute → оба счётчика не изменились.
- Конкурентность in-memory `_model_cooldowns`: process-local by design (задача); при multi-worker деплое cooldown 429 не разделяется между процессами — принято как допущение A10 (DB-схема per (project,model) — у G).
- `docs/.../audit_original/regressions/reproduce_findings.py` (исторический audit-скрипт, вне pytest/ruff) использует старый bool-контракт `check_and_reserve` — не мой scope, не трогал; truthiness reservation-dataclass сохраняет его смысл («допущен»).
- Первый transient-сбой, «зажёванный» успешным retry, не пишет `last_error_*` в БД — осознанное решение (отчёт только по финальной ошибке); если нужна телеметрия промежуточных сбоев — отдельное решение lead.
