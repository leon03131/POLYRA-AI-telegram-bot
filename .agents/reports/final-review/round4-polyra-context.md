# POLYRA — round 4 баг-хант: context / memory / compaction

- Аудитор: polyra-context (read-only bug hunt, subagent)
- Дата: 2026-09-26
- Checkout: `O:\work\aibot`, HEAD `4925c40` (clean tree)
- Scope: `app/context/**` (builder.py, compactor.py, token_budget.py, titles.py), `app/memory/**`, контекстные пути `app/services/generation.py` (`_build_context`, `_rehydrate_images`, `_schedule_maintenance`, compaction-триггер, memory extraction), `app/db/repositories/chat_summaries.py`, `memories.py`, `app/db/models/chat_summary.py`, `memory.py`, `tests/unit/test_context.py`, `test_compactor.py`, `test_memory.py`
- Метод: полное чтение scope, трассировка математики/потоков, offline-запуски, воспроизведение компиляции SQL и multi-run compaction. Код не менялся (кроме данного отчёта). Git/live API не использовались.
- Базлайн: `pytest tests -q` → **503 passed, exit 0**; `tests/unit/test_context.py tests/unit/test_compactor.py tests/unit/test_memory.py -q` → **87 passed, exit 0**; `tests/unit/test_generation.py -q` → **32 passed, exit 0**.

---

## P1-1. `_rehydrate_images` ловит только `TelegramAPIError` — «сырые» aiohttp-ошибки убивают генерацию целиком (усилено A18 «rehydrate всегда»)

`app/services/generation.py:641-642`:

```python
                except TelegramAPIError:
                    logger.info("rehydrate image failed (file_id=%s)", part.telegram_file_id[:24])
```

Но `bot.download_file()` в aiogram 3.31 идёт через `session.stream_content()`, который **не оборачивает** исключения (`.venv/.../aiogram/client/session/aiohttp.py:185-205`):

```python
        async with session.get(
            url, timeout=timeout, headers=headers, raise_for_status=raise_for_status,
        ) as resp:
            async for chunk in resp.content.iter_chunked(chunk_size):
                yield chunk
```

Оборачивание `ClientError`/`TimeoutError` → `TelegramNetworkError` есть только в `make_request` (aiohttp.py:173-176) — т.е. `bot.get_file(...)` (generation.py:632) защищён, а `bot.download_file(...)` (generation.py:637) — НЕТ. Из блока try экранируют:

- `aiohttp.ClientConnectorError` / `ClientResponseError` (raise_for_status=True) — любой сетевой сбой;
- `TimeoutError` (total timeout 30s) — не является `ClientError` и не TelegramAPIError.

Цепочка прод-проявления: `_rehydrate_images` → `_build_context` (generation.py:590) → `_prepare` падает **до** создания run и драфта: `async with self._session_factory()` завершается исключением → uncommitted `add_message` (generation.py:993) откатывается; пользовательское сообщение **не сохраняется в историю**, `run` не создаётся, exception выходит из `generate()` (вокруг `_prepare` нет try) → глобальный `on_error` (app/bot/middleware/errors.py:13-28) отвечает «⚠️ Произошла внутренняя ошибка. Попробуйте позже.».

До коммита 0be0524 rehydrate выполнялся только в ветке со сводкой; A18 («rehydration ВСЕГДА для image-capable моделей», generation.py:588-590) сделал этот путь горячим: **каждый запрос любой image-capable модели** теперь зависит от отсутствия сетевой ошибки при скачивании произвольного старого фото. Класс ошибки — та же, что у P0 из 0be0524 (runtим-путь не покрыт тестами): тестов `_rehydrate_images` — 0 (grep по tests/ — 0 упоминаний), поэтому 503 зелёных это не ловят.

Фикс-направление: `except (TelegramAPIError, aiohttp.ClientError, TimeoutError)` или широкий `except Exception` с логом — фото деградирует в плейсхолдер (контракт docstring «Ошибки/oversize — молча пропускаем», generation.py:623). Аналогичная (менее критичная — до драфта и с аккуратным ответом) узкая ловля в `app/bot/routers/photos.py:51`.

---

## P1-2. A18 «rehydrate всегда» + A15 `list_all` — закачка ВСЕЙ истории фото на каждый запрос (serial, без лимита)

`app/services/generation.py:581-590`:

```python
        if summary_row is not None:
            # A15: вся непокрытая история, а не только хвост лимита
            history = await messages_repo.list_all(chat_id)
            ...
        if model_def.supports_images:
            # A18: rehydration ВСЕГДА для image-capable моделей, не только при summary.
            await self._rehydrate_images(history, bot)
```

В ветке со сводкой `history` = **весь чат** (`list_all`), и `_rehydrate_images` (generation.py:625-642) последовательно (await в цикле) делает `get_file` + `download_file` для **каждого** image part с `telegram_file_id`. При этом:

- `data_base64` — transient, в БД не хранится (messages.py:30-31) → **повторная закачка на каждом запросе**, кеша нет;
- builder затем отбрасывает **покрытый сводкой префикс** (builder.py:130-137) — эти фото скачаны и никогда не используются;
- без сводки окно = `recent_history_limit*2` = 40 сообщений (generation.py:990-992) — до 40 сообщений фото на каждый запрос при том, что в LLM-запрос реально попадают recent(10)+влезающие older.

Прод-проявление: фото-тяжёлый чат из сотни фото со сводкой → 2 Telegram API вызова × N фото, serial, на КАЖДОЙ генерации; при ~200-400 мс на закачку это десятки секунд латентности до первого токена, flood-control Telegram, плюс **DB-сессия `_prepare` держится открытой** всё время закачек (пул соединений (default) исчерпывается несколькими параллельными фото-чатами). Частично предсказано в прошлом раунде (P4-b), но фикс A18 расширил поражённую поверхность с «только summary-чаты» на «все image-capable запросы».

Фикс-направление: rehydrate только после определения включаемых builder'ом сообщений (передавать `built.messages`-ids), либо cap на число закачек, либо параллелить с общим таймаутом.

---

## P1-3. A15-1 остаток: первая compaction в текстовом чате недостижима — история старше 40 сообщений теряется молча (подтверждено, стоит с прошлого раунда)

Точное условие (запрошено заданием): **нет записи `chat_summaries` для чата** И `count(messages) > recent_history_limit*2` (default 40). Тогда:

1. `_prepare` выбирает только окно: `list_recent(chat_id, limit=self._settings.recent_history_limit * 2)` (generation.py:990-992) — параметр `history` больше нигде не расширяется (расширение `list_all` — только при `summary_row is not None`, generation.py:581-583);
2. builder без сводки: `needs_compaction = summary_json is not None or dropped > 0` (builder.py:171-174) — `dropped_oldest` считается **только внутри выборки** (builder.py:196: `len(older) - len(kept)`), сообщения за пределами окна невидимы → `dropped=0`;
3. бюджетное давление в окне недостижимо: все модели реестра имеют `max_context ≥ 1_000_000` (capabilities.py:36, 53, 133) → threshold ≈ 0.7·(1M−4096−512) ≈ 730k токенов; 40 сообщений текста/фото дают ≪ 100k;
4. → `_schedule_maintenance` не вызывает `maybe_compact` (generation.py:835) → **первая сводка не создаётся никогда** → сообщения №1..(N−40) не входят ни в контекст, ни в сводку — молча.

Контрпроверка: как только сводка существует (например, вручную), `needs_compaction=True` на каждый запрос и compaction догоняет (см. трассу в OK-1). Дыра именно вbootstrap-триггере. Ассерт приёмки A15 «факт из раннего непокрытого сообщения остаётся в raw context или summary» для fresh-чата 60+ сообщений НЕ выполняется. (Соответствует P1-a прошлого раунда — не исправлено в 0be0524/4925c40.)

Фикс-направление: давать builder'у знать, что выборка усечена (count_for_chat/флаг truncated → `needs_compaction=True`), или триггерить compaction от `count > recent*2 + min_segment`.

---

## P2-1. A17: `covered_messages_count` расходится с `covered_until_message_id` при порционном (capped) compaction

`app/context/compactor.py:293-322`:

```python
            segment, covered_count = self._segment(messages, state)   # covered_count = end
            if len(segment) < self._min_segment:
                return False
            segment = _cap_segment_prefix(segment, self._min_segment) # сегмент СЖИМАЕТСЯ
            ...
            new_covered_until = segment[-1].id                         # граница capped-префикса
            ...
            await self._summaries.save(
                chat_id,
                summary=summary,
                covered_until_message_id=new_covered_until,
                covered_messages_count=covered_count,                  # а счётчик — от ПОЛНОГО сегмента
            )
```

`_segment` возвращает `end = max(0, len(messages) - keep_recent)` (compactor.py:352,362) — счёт конца несжатого сегмента. После `_cap_segment_prefix` фактическое покрытие = `start + len(capped)`, но в БД пишется `end`. Воспроизведено офлайн (реальный `_cap_segment_prefix` + `maybe_compact`, 40→48 сообщений, по ~1000 симв., keep_recent=2, min_segment=3):

```
run 0: True boundary_pos=10 saved_count=38 actual_covered=11
run 1: True boundary_pos=21 saved_count=40 actual_covered=22
run 2: True boundary_pos=32 saved_count=42 actual_covered=33
run 3: True boundary_pos=43 saved_count=44 actual_covered=44   (uncapped — сходится)
```

Т.е. во всех capped-прогонах счётчик завышен на `end − start − len(capped)` (здесь на 27/18/9). Колонка `chat_summaries.covered_messages_count` (models/chat_summary.py:36, миграция 0005) сегодня в app-коде не читается (только тесты), поэтому дефект латентный, но это неверные данные в БД для любых будущих читателей (статистика/admin/отладка) и семантическое нарушение пары «boundary/count» из docstring модели. Тесты это не ловят: `test_compactor.py:170,229` ассертят count только в uncapped-сценариях; в capped-сценарии (`test_long_segment_portioned_prefix_no_data_loss`, 445-473) count вообще не ассертится.

Фикс-направление: `covered_count = start + len(segment)` после capping (`_segment` должен вернуть `start`).

---

## P2-2. A13 неполный: compactor построен на env-настройках, builder — на effective (DB). Инвариант keep_recent ломается на шве

`app/services/generation.py:592-596` — builder per-request из effective (исправлено в 0be0524):

```python
        builder = ContextBuilder(
            TokenBudgetManager(),
            keep_recent=effective_settings.context_keep_recent,
            trigger_ratio=effective_settings.context_trigger_ratio,
        )
```

Но compactor строится один раз на старте из env (`app/main.py:52-59`):

```python
    compactor = ContextCompactor(
        ...
        keep_recent=settings.context_keep_recent,
        min_segment=settings.compaction_min_segment,
    )
```

`_schedule_maintenance` передаёт только `chat_id` (generation.py:836-837) — per-request effective значения до compactor'а не доходят. Прод-проявление: админ ставит в DB `context_keep_recent=30` (валидно, `_positive_int`, settings.py:152-154):

- builder защищает от отбрасывания последние 30 непокрытых сообщений (builder.py:144-148);
- compactor с env=10 продвигает `covered_until` до `len−10` (compactor.py:352) → **компактирует 20 сообщений, которые админ объявил «recent никогда не компактятся»** (docstring compactor.py:5-7); окно модели молча сжимается до ~10, DB-ручка фактически ограничена env-значением;
- обратное направление (DB=5 < env=10): compactor держит 10 незакрытых, builder считает 5 из них «older» — кандидаты на отбрасывание по бюджету, вопреки intents админа.

Аналогично `context_trigger_ratio` (effective) влияет только на триггер needs_compaction, а решение compactor'а (`min_segment`, env=6) не связано с ним. Тестов на рассинхрон нет.

---

## P2-3. A40: FTS-запрос НЕ совпадает с GIN-индексом 0009 — regconfig уходит bind-параметром, индекс мёртв

`app/db/repositories/memories.py:110-117`:

```python
            .where(
                Memory.user_id == user_id,
                func.to_tsvector("simple", Memory.text).op("@@")(
                    func.plainto_tsquery("simple", query)
                ),
            )
```

Индекс (миграция 0009 и модель) — иммутабельное выражение с литералом:

```python
        [sa.text("to_tsvector('simple', text)")],   # 0009_fix_v2.py:131
```

Проверено компиляцией реального выражения (dialect postgresql):

```
WHERE memories.user_id = %(user_id_1)s::UUID
  AND (to_tsvector(%(to_tsvector_1)s, memories.text) @@ plainto_tsquery(...))
```

`'simple'` рендерится **параметром**, а не литералом. Для expression-index planner сравнивает выражение предиката с выражением индекса; `Param ≠ Const('simple')` → `to_tsvector($1, text)` никогда не матчится с `to_tsvector('simple'::regconfig, text)` → **GIN-индекс `ix_memories_text_fts` не используется** ни в custom, ни в generic-плане: каждое `search_fts` — seq/bitmap-скан по строкам пользователя с почленным вычислением tsvector.

Не падает: asyncpg 0.31 имеет codec для regconfig (OID 3734, `protocol/codecs/pgproto.pyx:252-262` — reg*-типы кодируются как text), так что запрос исполняется корректно — деградация только индексная. Поэтому юнит-тесты на фейках (FakeRetrievalStore, test_memory.py:76-95) и отсутствие PG-интеграционного теста это скрывают.

Важно: отчёт прошлого раунда (polyra-context.md:195) утверждает «запрос использует то же выражение → expression index применим (тот же regconfig-литерал 'simple')» — **это неверно**: литерала в запросе нет. Основная точка фикса A40 №1 (индекс под запрос) на рантайме не работает.

Экспонирующие точки: `retrieve_memories` на каждом сообщении memory-пользователя (generation.py:1057-1061 → retriever.py:56) + tool `memory_search` (`app/llm/tools/builtin.py:251`, тот же метод).

Фикс-направление: `sa.literal("simple")` (или `sqlalchemy.text` с литералом) в обоих вызовах `to_tsvector`/`plainto_tsquery`.

---

## P3 (мелкие, с координатами)

| # | Где | Суть |
|---|-----|------|
| 1 | compactor.py:67,74-76 | `_COMPACTION_LOCKS` растёт неограниченно (один Lock на чат навсегда, не чистится) — утечка памяти process-lifetime. |
| 2 | generation.py:869-881 | `_spawn_background` не сохраняет ссылку на task; asyncio держит только weak refs (документированная ловушка) — фоновая compaction/memory/title задача теоретически может быть GC'нута на паузе → триггер теряется молча (done_callback тоже исчезает). Низкая вероятность, но это ответ на «не теряется ли триггер». |
| 3 | compactor.py:159 | Гигантский путь `_cap_segment_prefix`: `segment[:min_segment]` routinely превышает `_MAX_DIALOG_CHARS` в 3-6× (трейс: 66k-72k chars) — безопасно для внутреннего gemini-3.5-flash-lite (1M ctx, 2048 out), лимит 12k фактически не бюджет при min_segment×len(msg) > 12k. Принято docstring'ом («как есть, безопасный отказ»). |
| 4 | generation.py:575-578, 319-351 | Legacy-ветка (`context_builder is None`): без rehydration, без бюджета, `needs_compaction=False` всегда (compaction в ней не стартует никогда), `build_messages` режет image parts до плейсхолдеров. В production не используется (main.py:47,83 всегда передаёт builder) — мёртвый риск. |
| 5 | generation.py:570-574 | `TokenBudgetManager()` инстанцируется 2 раза на каждый tool в comprehension — косметика (по дефолту оценки совпадают с builder'ным менеджером; рассинхрон только при кастомном chars_per_token — P2-c прошлого раунда остаётся). |
| 6 | extractor.py:32,246 | Дедуп-скан ограничен топ-200 записей по importance/updated — у пользователя с >200 memories старые записи не участвуют в дедупе → возможны дубли (задокументированный bound, A40). |
| 7 | chat_summary.py:35 + compactor.py:354-359 | Legacy-строка сводки с `covered_until IS NULL` (колонка nullable, миграция 0005): builder трактует «вся выборка непокрыта» (сводка отрендерена + полная история — дублирование контента), compactor — start=0, пересчёт. Edge, только для pre-fix данных. |
| 8 | compactor.py:308-316 | TOCTOU между `fresh = load()` и `save()` при multi-process деплое (boundary может откатиться: «не найден в истории» = «историю чистили» = True). Для текущего single-process деплоя (main.py — один процесс) — не проявляется; per-chat lock корректно сериализует (load внутри lock, compactor.py:290-292). |

---

## OK — проверено и подтверждено (по пунктам охоты)

### 1. A17 `_cap_segment_prefix` — математика и «зависание» остатка

- **Остаток не виснет вечно**: multi-run трасса (выше) — boundary 10→21→32→43, потом `False` (остаток 2 < min_segment=3, без LLM-вызова); с приходом новых сообщений `end = len−keep_recent` растёт и остаток покрывается. Если новых сообщений нет — остаток < min_segment, `maybe_compact` возвращает False **до** LLM-вызова (compactor.py:294-295), а сами непокрытые сообщения остаются видимыми в контексте через builder (uncovered → older → budget-fit) — потерь нет.
- **Потерь середины нет**: capped = строгий префикс (compactor.py:146-153, break до append), `boundary = segment[-1].id` только включённых (compactor.py:307); `_build_prompt` рендерит ровно capped-сегмент без элизии (compactor.py:364-378). Тест `test_long_segment_portioned_prefix_no_data_loss` (test_compactor.py:445-473) ассертит согласованность prompt↔boundary.
- **Гигантские сообщения** (min_segment не влезает): `segment[:max(min_segment,1)]` (compactor.py:159) — покрыто min_segment сообщений как есть; при неудаче сводки boundary не двигается (compactor.py:303-305) — безопасный повтор. Реалистичный потолок: Telegram text ≤ 4096 симв./сообщение → 6×4097 ≈ 25k chars ≪ 1M ctx внутренней модели — вечного fail-loop'а в практике нет.
- **`maybe_compact=True` без сохранения невозможен**: True возвращается только после `await self._summaries.save(...)` (compactor.py:318-324); DbSummaryStore.save — upsert+commit в отдельной сессии (compactor.py:227-242); исключение пробрасывается → логируется в `_spawn_background` (generation.py:874-879).
- **Двойные запуски**: per-chat `asyncio.Lock` (compactor.py:74-76, 290) + `load` внутри lock + monotonic guard с перечитыванием `fresh` ПОСЛЕ LLM-вызова и сравнением позиций в ASC-истории `_boundary_is_forward` (compactor.py:311, 326-346; тесты 345-439). Внутри одного процесса гонки нет; сериализация проверена тестом (test_compactor.py:345-376). `list_all` ASC по created_at (messages.py:56-60), `_segment` и `_boundary_is_forward` работают на одном снапшоте — согласовано.

### 2. A15 `exclude_message_id`

- **(a) без summary дубля нет**: `list_recent` (generation.py:990) выполняется **до** `add_message` (generation.py:993) — current в history отсутствует, добавляется один раз (generation.py:616). В ветке summary — `list_all` после flush, фильтр по id (584-587), затем current один раз. Интеграционный тест test_fix_v2_reaudit.py:393-471 покрывает ровно один маркер в запросе.
- **(b) UUID vs str**: фильтр — `m.id != exclude_message_id` (UUID==UUID, generation.py:587); builder — `str(message.id) == str(covered_until_message_id)` (builder.py:132-135) — оба типо-стойки.
- **(c)** — качает лишнее: да, см. P1-2.
- **(d)** — подтверждено и описано точно: см. P1-3.

### 3. A13 wiring

- Builder per-request: `keep_recent`/`trigger_ratio` из `effective_settings` (generation.py:592-596) ✓; `effective_settings` собран из DB `system_settings` поверх env с валидацией (generation.py:998-1009, settings.py:123-166) ✓.
- Memory extractor получает effective `min_chars` **per-call**: `extract_and_store(..., min_chars=prepared.memory_min_chars)` (generation.py:859-865), `prepared.memory_min_chars = effective_settings.memory_extraction_min_chars` (generation.py:1133); параметр keyword-only (extractor.py:198-206) — вызов keyword ✓; override/None-fallback покрыты тестами (test_memory.py:509-575).
- `memory_retrieval_limit` — effective (generation.py:1060) ✓.
- **Пробел**: compactor — env (P2-2).

### 4. Memory

- Extractor: валидация кандидатов, clamp 1..10, cap 10×500 (extractor.py:63-91) ✓; ошибки LLM/БД → 0 без исключений (extractor.py:223-237) ✓; batch-internal dedup через `existing.append(memory)` (extractor.py:264) ✓ (expire_on_commit=False — session.py:21, detached-атрибуты доступны).
- Retriever FTS: выражение **не** матчит GIN-индекс — P2-3 (главная находка пункта).
- Dedup-scan cap: 200 (P3-6).

### 5. TokenBudget

- `budget_for`: `reserved_output = max_output_tokens or 4096` (token_budget.py:52-58); generation **никогда не передаёт** `max_output_tokens` в `builder.build` (generation.py:597-608) → reserved всегда 4096. Согласовано с capabilities: `min(max_output)` по реестру = 65_536 (gemini), все Alibaba ≥ 393_216 (capabilities.py:54,123,134,162) → потолки не нарушаются; `available = 1M − 4096 − 512 > 0` для всех моделей ✓. Зафиксировано тестом (test_context.py:419-441).
- Image estimation: фикс 1032 в `estimate_message`/`estimate_current` (token_budget.py:71-72, 85-86), byte-less image → текстовый плейсхолдер → считается как текст (builder.py:66-67) — согласовано с фактическим payload ✓; `estimate_current` аддитивен (0 для пустого) ✓ (тесты test_context.py:57-77).

### 6. Compaction-триггер

`needs_compaction` → `_schedule_maintenance` (generation.py:833-838) → `_spawn_background` (create_task + done_callback с логом, 869-881) ✓; исключения фоновых задач логируются и не роняют ответ ✓. Планирование только после успешного ответа ✓. Пробелы: (а) GC-хазард task-ссылок (P3-2); (б) first-compaction bootstrap недостижим (P1-3); (в) wiring без тестов (см. ниже).

### 7. Тесты — что НЕ покрыто (503 зелёных не видят P1-1/P1-2/P2-1/P2-3)

- `_rehydrate_images` — **0 тестов** (grep: 0 упоминаний в tests/): ни happy-path, ни oversize, ни сетевых ошибок (P1-1 невидим).
- `_schedule_maintenance` / `_spawn_background` — **0 тестов**: ни один тест не строит GenerationService с `compactor=`/`title_generator=`/`memory_extractor=` (grep: только None) — триггерная обвязка не покрыта.
- `_cap_segment_prefix` giant-путь (`len(included) < min_segment`, compactor.py:154-159) — 0 тестов; multi-run сходимость порций (остаток → следующий запуск) — 0 тестов; `covered_messages_count` в capped-прогоне — не ассертится (P2-1 невидим).
- `_build_context` — только summary-ветка (test_fix_v2_reaudit.py:393-471); non-summary/legacy ветки, `fits=False`-ветка — 0 прямых тестов.
- `search_fts`/индекс — юниты на фейках; SQL-компиляция/план против реального PG не проверяется (P2-3 невидим).
- `test_generation.py` (32) — не касается builder/compaction вообще.

---

## Итоговая таблица

| # | Находка | Серьёзность | Файлы:строки | Доказательство |
|---|---------|-------------|--------------|----------------|
| 1 | Сетевая ошибка `bot.download_file` (aiohttp `ClientError`/`TimeoutError`) экранирует узкий `except TelegramAPIError` → генерация падает до драфта, user-сообщение откатывается, ответ — generic «внутренняя ошибка» | **P1** | generation.py:641-642 (вызов 632-639); aiogram aiohttp.py:185-205 (нет оборачивания) | чтение исходника aiogram 3.31; обвязка `make_request` (173-176) есть только для get_file; A18 сделал путь горячим |
| 2 | Rehydration ВСЕЙ истории фото на каждый запрос (list_all при сводке; 40-окно без): serial Telegram API, покрытый префикс качается впустую, DB-сессия держится | **P1** | generation.py:581-590, 619-642 | list_all→history→_rehydrate до builder-отбора; data_base64 transient (messages.py:30-31) |
| 3 | A15-1: первая compaction недостижима для текстового чата >40 сообщений без сводки — история молча теряется | **P1** (standing) | generation.py:990-992, 835; builder.py:171-174, 196 | needs_compaction без сводки = dropped>0 (внутри окна) / used>threshold (~730k токенов) — недостижимо при max_context≥1M у всех моделей |
| 4 | A17: `covered_messages_count` завышен в каждом capped-прогоне (пишется `end` несжатого сегмента, граница — capped-префикс) | **P2** | compactor.py:293,300,307,318-322; _segment 352,362 | офлайн-трейс: saved 38/40/42 при факте 11/22/33; тесты ассертят count только uncapped |
| 5 | A13: compactor на env keep_recent/min_segment, builder на effective DB — DB-ручка context_keep_recent каппится env-значением, «recent никогда не компактится» нарушается на шве | **P2** | main.py:52-59; generation.py:592-596, 836-837; compactor.py:352 | сравнение wiring; per-request effective до compactor не доходит |
| 6 | A40: FTS-запрос рендерит regconfig bind-параметром → выражение ≠ GIN-индексу 0009, seq scan на каждый retrieval + memory_search tool; индекс мёртв | **P2** | memories.py:110-117; 0009_fix_v2.py:127-133; models/memory.py:42-46; builtin.py:251 | компиляция: `to_tsvector(%(to_tsvector_1)s, memories.text)`; asyncpg 0.31 regconfig-codec подтверждён (исполняется, но без индекса); прошлый отчёт (polyra-context.md:195) ошибочен |
| 7 | Фоновые task'и без сохранения ссылок (asyncio weak-ref GC) — возможная тихая потеря compaction-триггера | P3 | generation.py:869-881 | документированная ловушка asyncio.create_task |
| 8 | `_COMPACTION_LOCKS` не чистится (утечка); giant-path превышает 12k-«бюджет» в 3-6× (безопасно для 1M-модели); legacy-ветка без rehydrate/бюджета; dedup-scan cap 200; TOCTOU при multi-process | P3 | compactor.py:67,74-76,159; generation.py:575-578; extractor.py:32,246 | чтение кода |
| 9 | A15 exclude_message_id: дубля нет (UUID/str корректны); A17 порции: потерь нет, сходимость подтверждена; maybe_compact=True только после save; блокировка/monotonic guard корректны; extractor min_chars wiring корректен; reserved_output=4096 ≤ всех max_output | OK | generation.py:990-993,616; builder.py:132-135; compactor.py:284-324; token_budget.py:52-58 | трассы + 87/32/503 зелёных |
