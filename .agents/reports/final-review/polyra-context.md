# POLYRA — финальный аудит scope «context / memory / compaction»

- Аудитор: polyra-context (read-only final review)
- Дата: 2026-09-25
- Checkout: `O:\work\aibot` (актуальный, не архивный снимок)
- Scope: `app/context/**`, `app/memory/**`, связанные части `app/services/generation.py`, `app/db/models/chat_summary.py`, `app/db/models/memory.py`, `app/db/repositories/chat_summaries.py`, `app/db/repositories/memories.py`, `tests/unit/test_context.py`, `tests/unit/test_compactor.py`, `tests/unit/test_memory.py`
- Метод: чтение кода с file:line и цитатами; сверка тестов с production-путём; запуск offline-тестов.
- Изменений в коде проекта не производилось (read-only аудит).

## Запуск тестов (offline, из O:\work\aibot)

```
.venv\Scripts\python -m pytest tests/unit/test_context.py tests/unit/test_compactor.py tests/unit/test_memory.py -q
→ 84 passed in 0.50s, EXIT_CODE=0

.venv\Scripts\python -m pytest tests/unit/test_generation.py -q
→ 32 passed in 2.88s, EXIT_CODE=0
```

Важно: `tests/unit/test_generation.py` **не содержит ни одного теста `_build_context`/ContextBuilder** (grep: 0 упоминаний) — production-путь сборки контекста в сервисе генерации не покрыт тестами. Все builder-тесты вызывают `ContextBuilder.build` напрямую с идеальными входными данными.

---

## A15 — контекст по границе покрытия summary: **FIXED_PARTIAL**

### Что исправлено (подтверждено кодом и тестами)

1. Builder делит историю по `covered_until_message_id`, покрытый префикс исключается:
   `app/context/builder.py:130-137`
   ```python
   uncovered = history
   if summary_json is not None and covered_until_message_id is not None:
       covered_id = str(covered_until_message_id)
       for index, message in enumerate(history):
           if str(message.id) == covered_id:
               uncovered = history[index + 1 :]
               break
   ```
2. Весь непокрытый сегмент добирается под бюджет (старейшие отбрасываются), `needs_compaction` поднимается при любом непокрытом материале при наличии сводки:
   `app/context/builder.py:171-174`
   ```python
   kept, used, dropped = self._fit_older(older, used, threshold)
   needs_compaction = summary_json is not None or dropped > 0
   ```
3. При наличии summary сервис грузит ВСЮ историю, а не окно:
   `app/services/generation.py:538-543`
   ```python
   summary_row = await ChatSummaryRepository(session).get_for_chat(chat_id)
   if summary_row is not None:
       # A15: вся непокрытая история, а не только хвост лимита
       history = await messages_repo.list_all(chat_id)
   ```
4. `covered_until`, не найденный в выборке → безопасный пересчёт (вся выборка непокрыта): `builder.py:137`, `compactor.py:351-356`.
5. Тесты A15: `tests/unit/test_context.py:281-359` (непокрытый сегмент включён целиком, граница строго после covered_until, dropped_oldest, неизвестный covered id).

### Остающиеся проблемы (production)

**P1-a) Чаты БЕЗ summary: окно выборки `recent_history_limit*2` = 40 сообщений по-прежнему тихо теряет старую историю.**
`app/services/generation.py:935-938`:
```python
history = await messages_repo.list_recent(
    chat_id, limit=self._settings.recent_history_limit * 2
)
await messages_repo.add_message(chat_id, "user", parts=current_parts)
```
`app/config.py:38`: `recent_history_limit: int = 20` → 40 сообщений. Расширение до `list_all` (generation.py:541) выполняется ТОЛЬКО при наличии `summary_row`. Для чата без сводки с >40 сообщениями: старые сообщения вообще не попадают в builder → `dropped_oldest=0`, `needs_compaction=False` (если в окне нет бюджетного давления — а с max_context=1M у моделей по умолчанию его не будет) → compaction никогда не триггерится → сообщения №1..(N−40) выпадают из контекста молча, без суммаризации и без сигнала. Это ровно исходный дефект A15 («в _prepare берутся только recent_history_limit*2 сообщений независимо от полного объёма ещё не покрытой истории»), исправленный наполовину — только для ветки со сводкой. Критерий приёмки «Диалоги на 60–100 сообщений: факт из раннего непокрытого сообщения остаётся в raw context или summary» для fresh-чата 60 сообщений без сводки НЕ выполняется.

**P1-b) НОВЫЙ баг, внесённый фиксом: дублирование текущего сообщения при наличии summary.**
Порядок в `_prepare`: user-сообщение записывается и.flush() **до** вызова `_build_context` (generation.py:938), а `_build_context` при наличии сводки перечитывает историю `list_all(chat_id)` (generation.py:541) **в той же сессии** → перечитанная история УЖЕ содержит только что записанное текущее сообщение. Builder включает его в `recent` (builder.py:144-148), после чего `_build_context` снова добавляет current:
`app/services/generation.py:563`:
```python
llm_messages = [*built.messages, {"role": "user", "parts": current_parts}]
```
Итог: в каждом LLM-запросе чата со сводкой текущее сообщение присутствует ДВАЖДЫ (для фото — изображение отправляется дважды: rehydrated-копия из истории + current_parts; удвоение стоимости/токенов). Контракт builder это прямо нарушает: `app/context/builder.py:8-9` — «Текущее (current) сообщение в messages НЕ включается — его добавляет вызывающий код». В ветке без сводки дубля нет (history прочитана ДО add_message), т.е. баг именно в добавленной A15-ветке `list_all`. Тестами не покрыто вообще (в test_generation.py нет тестов `_build_context` с builder).

**P1-c) Effective context-настройки из БД (A13) не доходят до builder.**
`_prepare` вычисляет `effective_settings` с `context_keep_recent`/`context_trigger_ratio` из DB (generation.py:944-954), но `ContextBuilder` сконструирован один раз со значениями env (app/main.py:47-51) и `build()` не получает per-request переопределений. `effective_settings.context_keep_recent` используется только в legacy-ветке (generation.py:534), `context_trigger_ratio` — нигде. Смена этих значений через Admin→System не влияет на production-путь сборки контекста (до рестарта — и после рестарта тоже, т.к. читается env, а DB читается только в effective_settings).

**P1-d)** Нет PostgreSQL-integration теста контекста/compaction (критерий приёмки A15 «проверить реальные выборки PostgreSQL»); `tests/integration/` содержит только pool/stream_contract/usage_ledger.

---

## A16 — TokenBudget ограничивает фактический запрос: **FIXED_PARTIAL**

### Исправлено (подтверждено)

1. Полный счёт в builder: system (включая memories+summary, отрендеренные в system_prompt, builder.py:122-128) + tools (builder.py:153) + current с изображениями (builder.py:154, `estimate_current` token_budget.py:75-87) + recent (builder.py:155); `fits` → честный отказ пользователю:
   `app/services/generation.py:556-562`:
   ```python
   if not built.fits:
       await session.commit()
       await bot.send_message(
           tg_chat_id,
           "⛔ Контекст слишком большой даже после свёртки. Начните новый чат (/new).",
       )
   ```
2. Current добавляется ПОСЛЕ проверки, но учитывается ДО неё (`estimate_current`) — тихой потери нет; изображения считаются фиксированной ценой 1032 (token_budget.py:45, 71-72, 85-86).
3. `max_output_tokens` реально доходит до провайдера:
   - builder.py:165/181 `max_output_tokens=budget.reserved_output`;
   - generation.py:630 `max_output_tokens=prepared.max_output_tokens` в LLMRequest;
   - app/llm/providers/gemini.py:139-140 `generation_config["maxOutputTokens"]`;
   - app/llm/providers/alibaba.py:165-166 `payload["max_completion_tokens"]`.
   Тесты: test_context.py:419-441, test_gemini_provider.py:270, test_alibaba_provider.py:273.
4. recent не отбрасывается даже сверх бюджета, но `fits=False` → отказ (нет тихой потери минимума): test_context.py:157-170, 405-416.

### Остающиеся проблемы

**P2-a) Нет повторной проверки бюджета перед каждым LLM call в tool-loop (прямое требование A16).**
`_stream_loop` (generation.py:620-683) на каждой итерации строит новый `LLMRequest` по растущему `messages` (assistant turn + tool results добавляются в `_run_tool_round`, generation.py:709, 720-733) — без какого-либо перебюджетирования. Одиночный tool result ограничен `max_result_size` (app/llm/tools/runner.py:148-150, default 4000 chars), но 8 итераций × 4 вызова × ~4000 chars ≈ 37k токенов прироста никто не проверяет. При заполненном исходном контексте запрос молча раздувается → провайдер вернёт 400 → пользователю общее сообщение «Запрос некорректен для этой модели» вместо контролируемого уменьшения. Тестов на перебюджетирование между раундами нет (критерий приёмки «много tool results не обходят лимит» не покрыт).

**P2-b)** Дубль current (A15 P1-b) также искажает бюджет в ветке со сводкой: current считается дважды (в `recent` из list_all и в `estimate_current`) — согласовано с фактическим задвоенным payload, но это симптом бага, а не корректный учёт.

**P2-c)** `tools_token_estimate` считается fresh `TokenBudgetManager()` с дефолтами (generation.py:528-532), а не сконфигурированным менеджером builder — в production дефолты совпадают, но при кастомном `chars_per_token` оценки разойдутся.

**P2-d)** Legacy-путь (`context_builder is None`, generation.py:533-536) вообще без бюджета и без max_output_tokens — в production не используется (main.py всегда передаёт builder), но остаётся мёртвым риском.

---

## A17 — пустая JSON summary / перезапись фоновой compaction: **FIXED_PARTIAL**

### Исправлено (подтверждено)

1. Строгая валидация сводки: `app/context/compactor.py:106-125` — `{}` → None, отсутствие обязательных ключей → None, не-строка/пустой `conversation_summary` → None, списки не list → None. Тесты: test_compactor.py:255-285.
2. Невалидная сводка НЕ продвигает boundary: `compactor.py:299-302` (return False без save). Тесты: test_compactor.py:288-323 (включая `"{}"`, неверные типы, отсутствие ключей).
3. Ровно один repair: `compactor.py:376-396` (`_ask_json`). Тесты: 176-207, 326-339.
4. Сериализация фоновых compaction per chat_id: process-wide `asyncio.Lock` (compactor.py:65-77, 291). Тест конкурентности: test_compactor.py:345-376 (вторая compaction ждёт lock, LLM не вызывается повторно).
5. Monotonic boundary guard с перечитыванием свежего состояния перед save: compactor.py:304-321 + `_boundary_is_forward` 323-343. Тесты: 394-439 (boundary не откатывается; неизвестный boundary → безопасный пересчёт).
6. Бюджет внутреннего prompt: `_MAX_DIALOG_CHARS = 12_000` (compactor.py:71, `_cap_dialog` 133-160), `max_output_tokens=2048` (compactor.py:403). Raw history не удаляется (отдельная таблица chat_summaries; compactor.py:1-8; app/db/models/chat_summary.py:14-20; миграция 0005 — upsert одна запись на чат, unique chat_id).

### Остающиеся проблемы

**P3-a) «Порционный compaction» реализован как вырезание СЕРЕДИНЫ сегмента, а не порционная обработка — фактическая потеря данных, закреплённая тестом.**
`_cap_dialog` (compactor.py:133-160) сохраняет голову и хвост сегмента, середину заменяет маркером `… [середина фрагмента пропущена: N сообщ.] …`. Но граница покрытия продвигается на ВЕСЬ сегмент:
`compactor.py:304`:
```python
new_covered_until = segment[-1].id
```
Сообщения из вырезанной середины НИКОГДА не попадают ни в сводку, ни в будущий контекст (raw-строки остаются в БД, но покрыты и больше не включаются) — их факты теряются навсегда. Тест `test_compactor.py:445-467` не просто не ловит это, а прямо закрепляет как ожидаемое поведение:
```python
# boundary продвигается на весь сегмент, несмотря на усечение prompt
assert summaries.saved[0]["covered_until_message_id"] == messages[37].id
```
Требование A17 «порционный compaction с бюджетом» означало многоходовую обработку (сводка порции i → порция i+1 → …), а не элизию с фиксированным лимитом 12k. Классический паттерн «тест фиксирует баг как норму».

**P3-b) Нет DB-level CAS/версий.** `app/db/models/chat_summary.py` не содержит колонки версии; `ChatSummaryRepository.upsert` (app/db/repositories/chat_summaries.py:24-47) — read-modify-write без optimistic locking. Защита от перезаписи — только per-process asyncio.Lock + перечитывание `fresh` (compactor.py:307) с остающимся TOCTOU-окном между load и save. Для текущего деплоя допустимо (docker-compose: один сервис `app`, без replicas), но при масштабировании на несколько процессов гонка возвращается.

---

## A18 (context-часть) — image parts в истории: **FIXED_PARTIAL**

### Исправлено (подтверждено)

1. Builder не отбрасывает старые image parts: с `data_base64` → полноценный image part; без → текстовый плейсхолдер `[изображение]` (чтобы модель знала о факте фото):
   `app/context/builder.py:47-70`. Тесты: test_context.py:222-275.
2. Photo-роутер сохраняет устойчивый file_id + mime + metadata_json, base64 не хранится: `app/bot/routers/photos.py:59-74` («A18: устойчивый file_id — bytes в БД не храним»); `app/db/repositories/messages.py:11,30-39` — `telegram_file_id`/`mime_type`/`metadata_json` в `_PART_COLUMNS`, `data_base64` отбрасывается. Тест записи: test_bot_wiring.py:120-150.
3. Rehydration по telegram_file_id при построении multimodal контекста: `app/services/generation.py:566-589` (`_rehydrate_images`: bot.get_file → download → transient `part.data_base64`, bounded `photo_max_bytes`, ошибки → плейсхолдер).
4. Понятная ошибка для не-image модели со списком image-capable альтернатив: `generation.py:475-485` («⛔ Модель … не принимает изображения. Модели с поддержкой изображений: …»).

### Остающиеся проблемы

**P4-a) Rehydration вызывается ТОЛЬКО при наличии summary — главный сценарий A18 не закрыт.**
`app/services/generation.py:539-543`:
```python
if summary_row is not None:
    history = await messages_repo.list_all(chat_id)
    if model_def.supports_images:
        await self._rehydrate_images(history, bot)
```
В чате БЕЗ сводки (молодой чат — типичный сценарий «фото → ответ → follow-up») изображения недавней истории НЕ реhydrate'ятся → в следующем запросе фото представлено плейсхолдером `[изображение]` вместо ImagePart. Исходная формулировка аудита («фото… исчезает из последующей истории LLM») исправлена только для чатов со сводкой. Тестов на `_rehydrate_images` нет вообще: grep по `tests/` — 0 упоминаний «rehydrat».

**P4-b)** При наличии сводки rehydration выполняется по ВСЕЙ истории (`list_all`), включая покрытый префикс, который builder затем отбросит — лишние Telegram `get_file` вызовы (rate limit/латентность для фото-тяжёлых чатов), без ограничения числа загрузок.

**P4-c)** Legacy `build_messages` по-прежнему отбрасывает image parts истории: `generation.py:309-328` («Из истории берутся только text-пarts»). В production не используется (main.py всегда передаёт builder), однако пункт A18 называл эту функцию явно; тесты test_generation.py:232-248 всё ещё закрепляют отбрасывание (в мёртвом пути).

**P4-d)** Невосстановимые legacy-записи (image part без file_id и без bytes) деградируют в плейсхолдер молча — честно (данные не выдумываются), но нигде не логируются/не помечаются как невосстановимые (требование моей специализации — явно указать такие записи).

**P4-e)** Взаимодействие с багом A15 P1-b: при наличии сводки текущее фото-сообщение присутствует и в истории (rehydrated), и в current_parts → изображение отправляется дважды за запрос.

---

## A40 — FTS / dedup / миграции: **FIXED_PARTIAL**

### Исправлено (подтверждено)

1. GIN FTS-индекс добавлен и соответствует запросу чтения:
   - миграция `app/db/migrations/versions/0009_fix_v2.py:127-133`:
     ```python
     op.create_index(
         "ix_memories_text_fts", "memories",
         [sa.text("to_tsvector('simple', text)")],
         postgresql_using="gin",
     )
     ```
   - модель `app/db/models/memory.py:38-46` — идентичная DDL-форма (`Index("ix_memories_text_fts", sa_text("to_tsvector('simple', text)"), postgresql_using="gin")`).
   - запрос `app/db/repositories/memories.py:110-117` использует то же выражение `func.to_tsvector("simple", Memory.text).op("@@")(func.plainto_tsquery("simple", query))` → expression index применим (тот же regconfig-литерал 'simple', тот же столбец). Исходная претензия «to_tsvector на чтении без GIN index» снята на уровне DDL.
2. Bounded dedup: экстрактор сканирует максимум 200 существующих записей (`_EXISTING_SCAN_LIMIT`, extractor.py:32, 243); дедуп внутри batch (extractor.py:261); merge точных/near-дубликатов (extractor.py:265-286).
3. Миграция 0006_memories создаёт таблицу + `ix_memories_user_id`, `ix_memories_normalized_text` (0006_memories.py:63-64); GIN добавлен отдельной миграцией 0009 (без переделки 0006) — схема консистентна.

### Остающиеся проблемы

**P5-a) Нет DB unique fingerprint для exact-dedup — конкурентные вставки дают дубликаты.**
`app/db/models/memory.py:30` — `normalized_text` обычный (неуникальный) index; unique constraint на `(user_id, normalized_text)` отсутствует и в модели, и в миграциях (0006, 0009). `MemoryRepository.find_by_normalized` (memories.py:96-103) существует, но экстрактор его НЕ использует — читает `list_for_user(200)` и делает INSERT (extractor.py:243-263). Memory extraction задачи не сериализованы по user_id (два чата одного пользователя → параллельные фоновые задачи, generation.py:797-812) → точные дубликаты возможны даже в одном процессе. Требование A40 «атомарный user-scoped fingerprint» не реализовано; теста на конкурентные вставки без дублей нет.

**P5-b)** Ranking по-прежнему только importance/last_used (memories.py:118) — ts_rank не введён, EXPLAIN-измерений нет. A40 (P3) прямо разрешал не задерживать P1 этим, поэтому это отмечается, а не блокирует.

**P5-c)** PostgreSQL-тестов search_fts/dedup нет (unit-тесты на фейках: FakeRetrievalStore/FakeMemoryStore). Фактическое использование GIN-индекса планировщиком не проверено (CANNOT_VERIFY без БД, но DDL/запрос структурно совпадают).

---

## Memory extraction trigger (часть scope-проверки generation.py)

- Триггер после успешного ответа: `_schedule_maintenance` → `extract_and_store` (generation.py:797-812), только при `memory_extraction_enabled` (permissions.can_use_memory ∩ chat/user настройка, A12), только при непустых user_text/outcome.text. Ошибки LLM/БД гасятся в лог, ответ не роняют (extractor.py:220-234). Внутренняя модель `gemini-3.5-flash-lite`, thinking medium, max_output_tokens=1024 — соответствует ТЗ §23.
- **Остаток A13 в моём scope:** `effective_settings.memory_extraction_min_chars` из DB вычисляется (generation.py:950), но НЕ используется — экстрактор сконструирован один раз с env-значением (main.py:67-74), per-call override отсутствует. Смена `memory_extraction_min_chars` через Admin→System не влияет на фактический триггер экстракции. `memory_retrieval_limit`, напротив, используется корректно (generation.py:1005).

## Compaction scheduling (проверка)

- `needs_compaction` → `_spawn_background(compactor.maybe_compact(chat_id))` (generation.py:781-784); fire-and-forget с логированием ошибок; пер-chat блокировка в compactor. Планирование только после успешной генерации — соответствует «compaction только когда нужно». Проблемы покрытия истории см. A15 P1-a и A17 P3-a.

## Паттерн «фикс проходит тест, но ломается в проде» — сводка

| # | Где | Суть |
|---|-----|------|
| 1 | A15/A18 | `list_all`-рефетч в `_build_context` происходит ПОСЛЕ записи текущего сообщения (generation.py:938 vs 541) → текущее сообщение дублируется в каждом запросе чата со сводкой (фото — дважды). Тестов `_build_context` в test_generation.py нет — дубль невидим. |
| 2 | A18 | `_rehydrate_images` вызывается только при наличии summary (generation.py:539-543) — типичный молодой чат получает плейсхолдеры вместо фото. Тестов rehydrate нет (0 упоминаний). |
| 3 | A17 | Тест test_compactor.py:445-467 закрепляет продвижение boundary через никогда не суммаризированную середину сегмента (элизия вместо порционной обработки). |
| 4 | A15/A13 | Effective context_keep_recent/context_trigger_ratio вычисляются, но не передаются в builder (env-конструкция в main.py:47-51); DB-override не действует. |
| 5 | A16 | Budget-проверка есть только на первом call; tool-раунды растят payload без перебюджетирования. |

## Итоговая таблица

| Пункт | Статус | Ключевые файлы | Главная проблема |
|---|---|---|---|
| A15 | FIXED_PARTIAL | builder.py:101-197; generation.py:538-543, 935-938, 563 | Окно 40 сообщений без сводки теряет историю молча; дубль current при сводке; DB-настройки не доходят |
| A16 | FIXED_PARTIAL | builder.py:150-183; generation.py:556-562, 620-683, 630; gemini.py:139-140; alibaba.py:165-166 | Нет re-check бюджета в tool-loop; дубль current; legacy-путь без бюджета |
| A17 | FIXED_PARTIAL | compactor.py:106-125, 285-321, 133-160, 304; chat_summaries.py:24-47 | Элизия середины сегмента с продвижением boundary = потеря фактов (закреплена тестом); нет DB CAS (ок для одного процесса) |
| A18 (context) | FIXED_PARTIAL | builder.py:47-70; generation.py:539-543, 566-589; photos.py:59-74 | Rehydration только при сводке (молодые чаты — плейсхолдеры); нет тестов rehydrate; legacy build_messages всё ещё режет images |
| A40 | FIXED_PARTIAL | memories.py:105-122; memory.py:30,38-46; 0009_fix_v2.py:127-133; extractor.py:243-263 | GIN-индекс добавлен; но нет DB unique fingerprint — конкурентные дубли; FTS-ranking не внедрён |

Тесты scope зелёные (84 passed, exit 0; test_generation.py 32 passed, exit 0), но покрытие production-путя `_build_context`/rehydration/tool-loop-бюджета отсутствует — зелёный статус не является доказательством отсутствия перечисленных прод-дефектов.
