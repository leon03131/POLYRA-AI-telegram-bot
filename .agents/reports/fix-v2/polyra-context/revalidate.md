# polyra-context — ревалидация A15–A18, A40 против текущего кода

Дата: 2026-09-24. Режим: read-only (production код не менялся).
Метод: чтение кода + сверка SHA256(first 12) с хэшами аудита (`audit_original/FILE_REVIEW.md`).

## Общая сверка с аудированным снапшотом

Все файлы scope **байт-идентичны** аудированному состоянию — по этим пунктам после аудита ничего не исправлялось:

| Файл | Хэш сейчас | Хэш в аудите | Совпадает |
|---|---|---|---|
| app/context/builder.py | `4a9be3854d2f` | `4a9be3854d2f` | да |
| app/context/compactor.py | `d4fda3bde967` | `d4fda3bde967` | да |
| app/context/token_budget.py | `ff80d2a643d6` | `ff80d2a643d6` | да |
| app/services/generation.py | `fb6922c65e3f` | `fb6922c65e3f` | да |
| app/bot/routers/photos.py | `7ca21df3d27f` | `7ca21df3d27f` | да |
| app/services/chats.py | `b9fd48287d94` | `b9fd48287d94` | да |
| app/db/repositories/messages.py | `5f45a953efec` | `5f45a953efec` | да |
| app/db/repositories/memories.py | `5d31c8b6c539` | `5d31c8b6c539` | да |
| app/db/migrations/versions/0006_memories.py | `577b6bcd81ea` | `577b6bcd81ea` | да |
| app/memory/deduplicator.py | `e01d70cfdbb8` | `e01d70cfdbb8` | да |
| app/config.py | `203423d71d41` | `203423d71d41` | да |
| app/db/models/chat_summary.py | `11328444b969` | `11328444b969` | да |

Следствие: номера строк из оригинального аудита валидны 1:1 для текущего checkout.

---

## A15 [P1] Покрытие истории — **confirmed-broken**

**Обрыв доказан по коду, три точки:**

1. `app/services/generation.py:707-709` — окно выборки жёстко ограничено:
   `list_recent(chat_id, limit=settings.recent_history_limit * 2)` → по умолчанию 40 сообщений (`config.py:36` `recent_history_limit=20`). Всё, что старше 40-го сообщения от хвоста, в `_prepare` вообще не попадает.

2. `app/services/generation.py:774-784` — builder получает `summary_json` (dict), но **не получает `covered_until_message_id`**. Сигнатура `ContextBuilder.build` (`builder.py:77-86`): `model, base_system_prompt, summary_json, memories, history, max_output_tokens` — параметра boundary нет. Репозиторий его читает (`ChatSummaryRepository.get_for_chat`, `chat_summaries.py:18-22`, поле есть в модели `chat_summary.py:35`), но из row в builder передаётся только `summary_row.summary`.

3. `app/context/builder.py:101-105, 118-128` — при наличии сводки builder возвращает **только `recent`** (последние `keep_recent=10`, `config.py:38`); весь `older` отбрасывается молча: `dropped_oldest=0`, а `needs_compaction = used > threshold` (только бюджетное давление).

**Где именно дыра:** сообщения в интервале `(covered_until, N-keep_recent]`:
- если они внутри выбранных 40 — активно выбрасываются веткой `builder.py:118-128`;
- если старше окна 40 — не читаются вообще (`generation.py:707-709`).

**Усилитель:** при маленьком свежем контексте `used <= 0.7*available` → `needs_compaction=False` → `_schedule_maintenance` (`generation.py:548-551`) даже не запускает compactor → непокрытый сегмент растёт неограниченно и не попадает ни в контекст, ни в сводку. Compactor boundary знает (`compactor.py:264-268`), но его никто не зовёт.

**Что чинить:** передавать `covered_until_message_id`/boundary в builder (или вычислять непокрытый хвост в `_prepare`); включать непокрытые сообщения сверх keep_recent в контекст до предела бюджета; триггерить compaction по факту наличия непокрытого сегмента ≥ min_segment, а не только по бюджету. Raw history не трогать (инвариант профиля).

---

## A16 [P1] TokenBudget — **confirmed-broken**

**Что учитывается сейчас** (`builder.py:115-139`): `system_prompt` (включает memories и summary — это корректно, `builder.py:88-94, 121`) + `recent` (+ `older` только когда сводки нет).

**Что НЕ учитывается:**
- **current message** — добавляется после builder (`generation.py:782` `[ *built.messages, {"role":"user","parts":current_parts} ]`), в оценку не входит.
- **images текущего сообщения** — `estimate_message` умеет считать image (1032 токена, `token_budget.py:45,71-72`), но current вообще не оценивается.
- **tools / tool results** — LLMRequest собирается в `generation.py:430-437` без учёта схем tools; tool_call/tool_result сообщения докидываются в цикле (`generation.py:472-491`) уже после всех бюджетных решений.

**max_output_tokens в LLMRequest:** НЕ передаётся. `generation.py:430-437` создаёт `LLMRequest(model, messages, system_prompt, thinking, tools, cancellation)` — поле `max_output_tokens` (`llm/base.py:53`) остаётся None; провайдеры ставят лимит только если он не None (`gemini.py:139-140`, `alibaba.py:158-159`). Симметрично builder вызывается без `max_output_tokens` (`generation.py:775-781`) → бюджет резервирует дефолтные 4096 (`token_budget.py:43,52-58`), а реальный вывод модели не ограничен. Рассинхрон в обе стороны.

**current > бюджета:** проверки нет нигде; запрос уходит как есть. `threshold = 0.7*available` (`builder.py:116`) — мягкий триггер compaction, не жёсткая гарантия перед LLM-вызовом.

**Что чинить:** считать полный payload (system+memories+summary+recent+current+images+tools+tool results reserve); прокидывать `max_output_tokens` и в `budget_for`, и в `LLMRequest`; явная ветка «current один превышает бюджет» (честный отказ/усечение, а не молчаливая отправка).

---

## A17 [P1] Compactor — **confirmed-broken** (оба подпункта)

**1) `{}` как успех + движение boundary.** `extract_json_object("{}")` вернёт `{}` (dict) → `maybe_compact` проверяет только `data is None` (`compactor.py:245-248`) → `_normalize_summary({})` (`compactor.py:91-106`) молча даёт каноническую пустышку `{"conversation_summary": "", все списки []}` → `save(... covered_until_message_id=segment[-1].id ...)` (`compactor.py:250-255`). **Пустая/вычищенная сводка считается успехом, covered_until продвигается.** То же для wrong-typed JSON (например `{"conversation_summary": 123}` → normalize в пустышку).

**2) Сериализация / откат boundary.** Per-chat блокировки нет: `maybe_compact` (`compactor.py:236-256`) — голый read-modify-write; grep по `asyncio.Lock`/`_compaction_lock` в `app/` — пусто. Запуск — fire-and-forget `asyncio.create_task` (`generation.py:581-593`); GenerationRegistry блокирует параллельные *генерации* чата, но compaction-task переживает генерацию: следующая генерация того же чата может завершиться и породить вторую compaction, пока первая ещё ждёт LLM. Обе читают state до записи друг друга; `ChatSummaryRepository.upsert` (`chat_summaries.py:24-47`) безусловно перезаписывает `summary` и `covered_until_message_id` — ни версии, ни CAS, ни monotonic-guard. Если таск с более ранним snapshot (меньшим boundary) финиширует последним — **boundary откатывается назад** и свежая сводка затирается старой.

**Что чинить:** семантическая валидация результата (пустой/нулевой summary → неудача, boundary не двигаем; при неудаче — сохранять прежнюю сводку); per-chat сериализация (lock/worker) или версионный CAS с монотонным `covered_until` в upsert; snapshot boundary на момент чтения сегмента.

---

## A18 [P1] Фото / telegram_file_id / история — **confirmed-broken**

- **photos.py НЕ передаёт file_id в parts:** `photos.py:59-65` — parts содержат только `type/mime_type/data_base64`. `photo.file_id` доступен в скопе (`photos.py:40-45`), но в parts не кладётся; `file_unique_id`, размеры — тоже нет.
- **В БД file_id не сохраняется:** `generation.py:710` → `MessageRepository.add_message(parts=current_parts)`; фильтр `_PART_COLUMNS` (`messages.py:11,34-36`) *пропустил бы* `telegram_file_id` (колонка есть, `message.py:58`, миграция `0002_chat_core.py:94`), но ключа в parts нет → в БД image-part с `telegram_file_id=NULL, metadata_json={}`. `data_base64` отбрасывается by design (комментарий `messages.py:30-32`).
- **`save_user_photo_message` (`chats.py:89-117`) — мёртвый код:** корректно кладёт `telegram_file_id` и metadata, но grep по репо — 0 вызовов (только определение и упоминание в audit evidence).
- **Старое фото при следующем запросе исчезает:** `builder._normalize_message` (`builder.py:41-46`) берёт только text-parts; image-only сообщение → `parts=[]` → `None` → сообщение **полностью выпадает из истории без плейсхолдера**. Legacy-ветка `build_messages` (`generation.py:259-269`) — то же самое. Регидрации по file_id нет нигде (и file_id не сохранён — восстановить нечего; legacy-записи без file_id невосстановимы — отдельно отметить в отчёте исправлений, как требует профиль).

**Что чинить:** photos.py → класть `telegram_file_id` (+file_unique_id, mime, размеры в metadata_json) в parts; builder/build_messages → image-part истории как минимум в текстовый плейсхолдер («[фото]»), опционально bounded rehydration для image-capable модели; задействовать или удалить мёртвый `save_user_photo_message`.

---

## A40 [P3] Memory FTS/dedup/limits — **confirmed-broken** (допустим optional-deferred по ACCEPTANCE_ADDENDUM:97)

- **GIN index: нет.** FTS — выражение на лету: `func.to_tsvector("simple", Memory.text).op("@@")(plainto_tsquery(...))` (`repositories/memories.py:92-102`). Миграция `0006_memories.py:63-64` создаёт только btree `ix_memories_user_id` и `ix_memories_normalized_text`; tsvector-колонки и GIN нет → scan внутри user scope.
- **Dedup fingerprint: нет.** Ни колонки-fingerprint, ни unique-констрейнта (`models/memory.py:28-35` — `normalized_text` Text с обычным btree index). Дедуп чисто прикладной: exact normalized + Jaccard ≥ 0.85 по последним 200 записям (`extractor.py:29-31,242-248`, `deduplicator.py:20-37`, `config.py:50`). `find_by_normalized` (`memories.py:78-85`) существует, но extractor его не использует. Конкурентные insert точных дублей гонкой не защищены.
- **Ограничения длин: частично.** Есть: cap источника 2000 символов на сторону (`extractor.py:29`), ≤10 кандидатов (`extractor.py:30,70`), importance clamp 1..10 (`extractor.py:87`), валидные категории (`extractor.py:32,76-78`). Нет: лимита длины **самого кандидата** (`parse_candidates`, `extractor.py:62-90` — только non-empty), `Memory.text` — unbounded Text. Отдельные лимиты длин для memory в `config.py` отсутствуют.

**Что чинить (при решении не defer):** generated tsvector + GIN index (новая миграция — владелец G/DB), user-scoped unique fingerprint (например hash normalized_text) для exact dedup, max length кандидата в `parse_candidates` + конфиг.

---

## Сводная таблица

| Item | Verdict | Ключевое evidence |
|---|---|---|
| A15 | **confirmed-broken** | generation.py:707-709, 774-784; builder.py:101-105, 118-128; boundary не передаётся |
| A16 | **confirmed-broken** | generation.py:430-437 (нет max_output_tokens), 782 (current вне бюджета); token_budget.py:52-58 |
| A17 | **confirmed-broken** | compactor.py:91-106, 245-256 ({} → успех + boundary); chat_summaries.py:42-45 (upsert без CAS); локов нет |
| A18 | **confirmed-broken** | photos.py:59-65 (нет file_id); messages.py:11,34-36; builder.py:41-46; chats.py:89-117 — dead code |
| A40 | **confirmed-broken** (P3, deferrable) | memories.py:92-102 (FTS без GIN); 0006_memories.py:63-64; extractor.py:29-31,62-90 |

Ни один пункт не «already-fixed»: файлы scope байт-идентичны аудированному снапшоту (таблица хэшей выше).
