# Round 4 bug-hunt — Gemini pool / quota / provider / factory

- Дата: 2026-09-26
- HEAD: `4925c40` (working tree clean)
- Режим: READ-ONLY аудит, код не менялся. Git/live не трогал.
- Scope: `app/llm/gemini/**` (pool.py, quota.py, store_db.py, `__init__.py`; errors.py фактически `app/llm/errors.py` — в `app/llm/gemini/` файла errors.py нет), `app/llm/providers/gemini.py`, `app/services/llm_factory.py`, `scripts/smoke_providers.py` (gemini-часть), `tests/unit/test_gemini_pool.py`, `tests/unit/test_gemini_provider.py`, `tests/integration/test_fix_v2_pool.py`.

## Метод

Полное чтение всех файлов scope, трассировка вызовов store-методов против реального DB-стора
(`app/llm/gemini/store_db.py` → `app/db/repositories/gemini.py` → `app/db/models/gemini.py`),
статические проверки `hasattr`/`inspect.signature`, офлайн-прогоны pytest и изолированные
репродукции на `httpx.MockTransport` (сеть не задействована). Никаких live API вызовов.

## Прогоны (реальные, сегодня)

- `.venv\Scripts\python -m pytest tests/unit/test_gemini_pool.py tests/unit/test_gemini_provider.py tests/integration/test_fix_v2_pool.py -q`
  → `67 passed in 0.24s`, **EXIT_CODE=0**.
- Статическая проверка стора:
  `DbProjectStore.set_cooldown: True (self, project_id: uuid.UUID, until: datetime | None) -> None`,
  `DbProjectStore.mark_error: True (self, project_id: uuid.UUID, *, error_code: str | None, error_message: str) -> None`,
  `mark_unhealthy/mark_success/list_all: True`, `DbQuotaStore.check_and_reserve_atomic/add_tokens/get_minute_usage: True`.
- Репозиторий: `GeminiProjectRepository.{list_all,set_cooldown,set_enabled,set_health,set_last_error}: True`,
  `QuotaUsageRepository.{get_minute_usage,get_daily_usage,reserve,add_tokens}: True`.
- Constraint-имена фактические из метаданных SQLAlchemy:
  `quota_minute_usage -> UniqueConstraint 'uq_quota_minute_usage_project_id'`,
  `quota_daily_usage -> UniqueConstraint 'uq_quota_daily_usage_project_id'` — совпадают с
  `on_conflict_do_update(constraint=...)` в store_db.py:193/220 и repositories/gemini.py:258/269.
- Колонки `cooldown_until/minute_ts` — `DateTime(timezone=True)`, `day` — `Date`:
  aware-`datetime` из `now_fn=lambda: datetime.now(UTC)` (llm_factory.py:61) совместим, naive/aware-мисматча нет.

## Главный вопрос round-4: P0-класс «вызов несуществующих методов» — ОТРИЦАЕТСЯ

Коммит `0be0524` в pool.py:224-234 для PERMISSION_DENIED вызывает
`self._store.set_cooldown(project_id, now + timedelta(hours=24))` и
`self._store.mark_error(project_id, error_code=code, error_message=message)`.

**Оба метода существуют во всех трёх слоях, прод-путь безопасен:**

| слой | set_cooldown | mark_error | источник |
|---|---|---|---|
| Protocol ProjectStore | pool.py:64 | pool.py:72-76 | сигнатуры совпадают с вызовами позиционно/kw-only |
| Реальный DB-стор | store_db.py:46-49 | store_db.py:62-69 | `DbProjectStore` — то, что строит прод-фабрика |
| Репозиторий | repositories/gemini.py:130-136 | repositories/gemini.py:114-128 (`set_last_error`) | существуют, `error_code[:64]`/`error_message[:256]` |

Прод-wiring: `llm_factory.py:49` → `build_gemini_pool(session_factory, crypto)` (store_db.py:228-255)
→ `store = DbProjectStore(session_factory)`. Никакого in-memory стора в прод-wiring нет —
фейки существуют только в тестах, и их набор методов идентичен протоколу
(`tests/unit/test_gemini_pool.py:73-104`, `tests/integration/test_fix_v2_pool.py:59-90`).
Тот же коммит проверен по всем store-вызовам pool/quota:

- `list_all` (pool.py:147) ↔ store_db.py:32 ✓; `mark_unhealthy` (pool.py:222) ↔ store_db.py:51 ✓;
  `mark_success` (pool.py:197) ↔ store_db.py:71 ✓; `set_cooldown` (pool.py:228/231/240) ✓.
- QuotaTracker → `check_and_reserve_atomic` (quota.py:144-150, через getattr) ↔ store_db.py:125 ✓;
  `reserve` (quota.py:161) ↔ store_db.py:101 ✓; `add_tokens` (quota.py:174) ↔ store_db.py:110 ✓;
  `get_minute_usage`/`get_daily_usage` (quota.py:153-154, legacy-путь) ↔ store_db.py:83/92 ✓.
- Кросс-проверка wiring: `get_provider_api_key(session, crypto, "alibaba", env_fallback)` —
  llm_factory.py:75-77 (4 позиционных) и smoke_providers.py:438 (`env_fallback=""`) —
  оба соответствуют credentials.py:9-14 ✓. `CryptoBox.decrypt` — sync (crypto.py:36), pool.py:165
  вызывает синхронно ✓.

**P0 в моём scope не найден.** (P0 из 0be0524 — TYPE_CHECKING-импорт в generation.py — уже закрыт
в 4925c40; в scope-файлах TYPE_CHECKING-блоков нет вовсе — grep по `app/` дал 13 попаданий, ни одного
в gemini/factory/smoke.)

## Находки

### F1. P1 — немаппированный finishReason превращает завершённый ответ в NetworkError

`app/llm/providers/gemini.py:189-196`:

```python
_FINISH_REASON_MAP = {"STOP": "stop", "MAX_TOKENS": "length"}
mapped = _FINISH_REASON_MAP.get(finish_reason)
return [Done(finish_reason=mapped)] if mapped else []
```

`RECITATION`, `LANGUAGE`, `OTHER`, `MALFORMED_FUNCTION_CALL`, `IMAGE_SAFETY`,
`UNEXPECTED_TOOL_CALL` не входят ни в map, ни в `_SAFETY_FINISH_REASONS` (gemini.py:34) →
событие Done не эмитится → `parse_generate_content_sse` (gemini.py:255-256) поднимает
`NetworkError("unexpected EOF: gemini stream ended without finishReason")` **для потока,
который штатно завершился с finishReason**.

Репродукция (offline, MockTransport):

```
1) RECITATION -> NetworkError - unexpected EOF: gemini stream ended without finishReason | category: network
```

Прод-проявление: ответ модели дошёл до конца (токены потрачены), но классифицирован как обрыв сети →
пул по ADR-005 делает bounded-retry того же проекта и ротацию (pool.py:239/283-306), повторный запрос
дублируется; при partial stream пользователь получает «сетевую» ошибку; здоровый проект получает
transient-cooldown 30с + mark_error (pool.py:240-243). RECITATION детерминирован контентом — все
проекты пула «падают» одинаково → PoolExhaustedError вместо честного финала.
Рекомендация (владельцу scope): маппить немаппированные finishReason в Done(finish_reason="stop")
либо выделить категорию, не провоцирующую retry/ротацию.

### F2. P2 — `_error_from_response` падает не-ProviderError на не-dict JSON error-теле

`app/llm/providers/gemini.py:277`:

```python
error = (json.loads(body) or {}).get("error") or {}
```

Если proxy на HTTP-ошибке отдаёт JSON-массив или JSON-строку (или невалидный UTF-8), метод бросает
сырые AttributeError / UnicodeDecodeError — они не перехватываются ни `except httpx.*` в
stream_chat (gemini.py:338-341), ни `except ProviderError` в пуле (pool.py:282).

Репродукции (offline):

```
2) array error body  -> AttributeError("'list' object has no attribute 'get'")
3a) invalid-utf8 (no BOM) -> UnicodeDecodeError('utf-8', b'<html>caf\xe9 ...', 9, 10, ...)
3b) JSON string body -> AttributeError("'str' object has no attribute 'get'")
```

(Замечание: тело с BOM `\xff\xfe` декодируется как UTF-16-мусор и корректно уходит в fallback
`ServerError('gemini http 502')`; details-не-list безопасен — isinstance-фильтр gemini.py:283.)

Прод-проявление: нештатная страница/тело шлюза на 5xx → в generation.py срабатывает
generic `except Exception` (generation.py:694) → пользователь получает «неизвестную ошибку»,
а ADR-005-учёт (cooldown/mark_error/ротация) полностью пропускается. Фикс-паттерн: обернуть
парсинг тела в `except (json.JSONDecodeError, UnicodeDecodeError, AttributeError, TypeError)`
и `isinstance(..., dict)`-проверку, оставив fallback-сообщение `f"gemini http {status}"`.

### F3. P2 — 429-cooldown per (project, model) живёт только в памяти процесса

`app/llm/gemini/pool.py:125-126`:

```python
# A10: 429-cooldown per (project, model) — process-local, БД-схему не меняем.
self._model_cooldowns: dict[tuple[UUID, str], datetime] = {}
```

Перезапуск процесса (deploy/restart) обнуляет cooldown'ы: получивший 429 c `retry_after=3600`
проект сразу после рестарта снова берётся в ротацию и снова ловит 429. Отмечено заданием как
известный P2 (схему БД меняет владелец G) — подтверждаю, деградация мягкая (лишний один 429),
не блокер. Дополнительно: словарь чистится лениво и только по запрашиваемому ключу
(pool.py:173-181) — за долгое аптайм-время растёт монотонно (мелочь, P3).

### F4. P2 — реальный DB-стор (`DbProjectStore`/`DbQuotaStore`) не покрыт ни одним тестом

Все 67 тестов идут на fakes (unit: FakeProjectStore/FakeQuotaStore/AtomicFakeQuotaStore;
integration: те же fakes). Слой store_db.py и его склейка с repositories/models не исполняются
нигде (нет ни SQLite/PG-прогона, ни теста на `build_gemini_pool`). Конкретные риски, которые
сегодня держатся только на «совпадении»:

- строковые constraint-имена `uq_quota_minute_usage_project_id` /
  `uq_quota_daily_usage_project_id` (store_db.py:193, 220; repositories/gemini.py:258, 269)
  работают лишь потому, что `NAMING_CONVENTION["uq"] = "uq_%(table_name)s_%(column_0_name)s"`
  (base.py:10) генерирует ровно эти имена из `UniqueConstraint("project_id", "model_id", "minute_ts")`
  (models/gemini.py:60/77). Любая правка конвенции/колонок без правки строк → asyncpg
  `UndefinedObject` в рантайме, юнит-тесты останутся зелёными (я проверил имена статически —
  сейчас совпадают, но регресс-теста нет).
- wire-поведение `check_and_reserve_atomic` (ON CONFLICT + WHERE + RETURNING) воспроизведено в
  `AtomicFakeQuotaStore` «по описанию», а не по SQL. Гонки/изоляция реального запроса не покрыты.

Рекомендация: интеграционный тест на PG (testcontainers) или хотя бы AST/метадата-тест,
сверяющий constraint-имена моделей со строками в store_db/repositories.

### F5. P3 — thoughtSignature на text-part теряется (контракт FC-parts соблюдён)

`_events_from_part` (gemini.py:164-186) возвращает `[TextDelta(text)]` для text-part;
`TextDelta` не имеет `provider_meta` (events.py:13-16), поэтому `thoughtSignature`,
прикреплённый Gemini 3.x к текстовой части, молча отбрасывается (пустой text-part с сигнатурой
также пропускается — test_gemini_provider.py:167-184 закрепляет это намеренно).
Vendor-док требует сохранения только для `functionCall`-parts
(docs/vendor/GEMINI.md:22 «хранить functionCall-parts с thought_signature as-is и возвращать
в историю; нарушение → HTTP 400») — этот путь сохранён и в приёме (gemini.py:171-181), и в
отправке (gemini.py:67-69). Нарушения инварианта «сохранить thought signatures» нет;
риск — качество multi-turn (Google рекомендует возвращать сигнатуры и с текстом).
Репродукция: `4) text+signature events: [TextDelta(text='real'), Done(finish_reason='stop')] |
signature preserved anywhere: False`.

### F6. P3 — SSE-сентинел `[DONE]` глотает хвост потока

`parse_generate_content_sse` (gemini.py:242-244): `data: [DONE]` → `terminal_seen=True; break`
без эмитации Done. Для passthrough-прокси Gemini ветка мертва (Gemini не шлёт `[DONE]`), но если
шлюз начнёт вставлять OpenAI-стиль сентинел ДО последнего чанка с finishReason/usageMetadata —
поток тихо обрежется без Done и Usage (и без NetworkError: `terminal_seen` уже True). Закреплено
тестом `test_done_sentinel_stops_stream` (test_gemini_provider.py:187-194). Низкий приоритет:
поведение probe подтверждено доком (GEMINI.md:59, «SSE-формат 3.x»).

### F7. P3 — report_error в except-цепочке может замаскировать исходную ProviderError

pool.py:295: `await self.report_error(...)` вызывается внутри `except ProviderError` без
`contextlib.suppress` (в отличие от report_success в `_stream_attempt`, pool.py:344 — там
suppress есть). Если БД недоступна именно в момент ошибки провайдера, DB-исключение заменит
исходную ProviderError: ротация не случится, в generation.py уйдёт не классифицированная ошибка
(поймается generic-веткой). Асимметрия осознанно не задокументирована; при недоступной БД деградация
и так тотальна — потому P3.

### F8. P3 — ManagedLLMStream может создать провайдера после aclose()

llm_factory.py:72-91: `_alibaba_provider()` не проверяет `self._closed`; вызов после shutdown
создаст новый клиент, который никто никогда не закроет (graveyard уже очищен). Ровно один
аккуратный `aclose` (llm_factory.py:93-104, идемпотентный, `_closed`-флаг) нарушается в этом
edge-case. Окно: запросы, стартующие во время shutdown.

## Что проверено и чисто (OK)

- **trust_env / custom proxy**: прод-клиент `httpx.AsyncClient(base_url=…, trust_env=False,
  timeout=…)` (gemini.py:298-304); smoke-клиент `trust_env=False` (smoke_providers.py:470-474);
  `settings.gemini_base_url` читается, не переприсваивается (llm_factory.py:48; smoke:454-475).
  Путь `:streamGenerateContent?alt=sse` (gemini.py:319-323) соответствует доку. Прокси не менялся.
- **aclose идемпотентность**: `GeminiProvider.aclose` (gemini.py:306-309) — двойной вызов
  безопасен (проверено offline: «5) aclose twice: ok»); внешний клиент не закрывается
  (`_owns_client`), smoke закрывает свой клиент сам в finally (smoke_providers.py:481-482).
- **SSE**: EOF без Done → NetworkError (контракт FIX_V2 §1, gemini.py:255-256); malformed JSON
  skip (gemini.py:246-249); safety-finish (SAFETY/BLOCKLIST/PROHIBITED_CONTENT/SPII) и
  promptFeedback.blockReason → SafetyError (gemini.py:193-194, 201-204) — вне ротации
  (pool.py:296-297). Multi-line SSE data не поддерживается — Gemini так не шлёт.
- **Cancellation между чанками**: `_lines_with_cancellation` (gemini.py:343-350) тихо завершает
  генератор; «EOF без Done» при is_set проглатывается в stream_chat (gemini.py:333-337);
  `asyncio.CancelledError` пробрасывается пулом без report_error (pool.py:280-281);
  тест `test_cancelled_error_propagates_without_report` зелёный.
- **Ротация**: ровно один проход (`tried` + PoolExhausted, pool.py:263-306, тест
  `test_pool_exhausted_raises`/`test_transient_retry_happens_at_most_once_per_project`);
  модель не меняется — везде `request.model` (pool.py:265, 295; acquire→quota_for_model),
  req2 отличается только `metadata["api_key"]` (pool.py:269-271); cross-model fallback отсутствует;
  bounded-retry (SERVER/NETWORK/TIMEOUT) один и до первого события (pool.py:40-43, 283-294);
  429 — без retry, per (project, model) (pool.py:235-238).
- **report_success/reconcile после Done**: finally в `_stream_attempt` (pool.py:339-351) — и при
  штатном конце, и при break потребителя после Done (интеграционный тест
  `test_usage_trailer_survives_consumer_break_after_done` + `_drain_loop`; прод-потребитель
  generation.py:883-931 держит ту же дисциплину `aclose` в finally). Reconcile идёт в окна
  РЕЗЕРВАЦИИ (quota.py:166-180, тест на пересечение минутной границы).
- **Квоты A09**: atomic check+reserve одной транзакцией (store_db.py:125-169, оба окна ON CONFLICT
  с WHERE, rollback при отказе, лимит<=0 — запрет без записи); лимиты перечитываются из
  `quota_policies` на каждый acquire (admin-изменения без рестарта, store_db.py:239-247);
  `pacific_day`/`current_minute` — UTC/Pacific по доку (quota.py:27-34), tzdata в зависимостях
  (pyproject.toml:23).
- **model IDs сохранены**: gemini-3.8-flash / 3.7-flash / 3.6-flash / 3.5-flash-lite (internal) —
  читал registry, ничего не менял.
- **llm_factory**: один shared Gemini-провайдер/клиент (llm_factory.py:48); graveyard для
  устаревших Alibaba-клиентов закрывается в aclose (0be0524) — активные стримы не рвутся
  (llm_factory.py:83-91).
- **smoke_providers (gemini)**: ключ из БД + `--gemini-project` (LookupError на disabled,
  smoke_providers.py:404-423); изоляция сбоев per-check (`_run_check`, smoke:176-190); маски
  ключей только через `mask_secret`; secrets не светятся; `--write-runtime` не затирает модели
  без результатов (smoke:615-642).

## Сводка

| # | Находка | Серьёзность | Файлы:строки | Доказательство |
|---|---|---|---|---|
| F1 | Немаппированный finishReason → NetworkError → ложный retry/ротация | P1 | app/llm/providers/gemini.py:33,189-196,255-256; pool.py:239-243,283-306 | offline-репродукция RECITATION → NetworkError «unexpected EOF» |
| F2 | Не-dict/не-UTF8 error-тело → AttributeError/UnicodeDecodeError без классификации | P2 | app/llm/providers/gemini.py:277-286 | offline: AttributeError/UnicodeDecodeError на MockTransport 500/502 |
| F3 | 429-cooldown per (project, model) process-local (теряется при рестарте) | P2 (известный) | pool.py:125-126,173-181 | чтение кода; заданием уже помечено P2 |
| F4 | DbProjectStore/DbQuotaStore и constraint-имена ON CONFLICT без тестового покрытия | P2 | store_db.py:125-225; repositories/gemini.py:250-273; models/gemini.py:60,77; base.py:10 | все 67 тестов на фейках; имена сверены статически, регресс-теста нет |
| F5 | thoughtSignature на text-part отбрасывается | P3 | gemini.py:182-186; events.py:13-16 | offline: signature preserved anywhere: False |
| F6 | `[DONE]`-сентинел обрезает хвост без Done/Usage | P3 | gemini.py:242-244 | test_done_sentinel_stops_stream (закреплено намеренно) |
| F7 | report_error в except-цепочке может замаскировать ProviderError при сбое БД | P3 | pool.py:282-295 vs 339-351 | чтение кода (асимметрия suppress) |
| F8 | Провайдер Alibaba может создаваться после aclose() (утечка клиента) | P3 | llm_factory.py:72-104 | чтение кода (нет проверки _closed) |

P0-класс (несуществующие методы) — **не подтверждён**: `set_cooldown` и `mark_error` существуют
и в реальном `DbProjectStore`, и в прод-фабрике `build_gemini_pool`, сигнатуры совпадают с
вызовами пула и с Protocol; фейки тестов расходений с реальным стором по методам не имеют.
