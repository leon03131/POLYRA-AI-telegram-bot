# polyra-context — wave1: A15, A16, A17, A18(context), A40(context-часть)

Дата: 2026-09-24. Исполнитель: polyra-context (subagent).
Основание: revalidate.md (все 5 пунктов confirmed-broken), POLYRA_FIX_V2_CONTRACTS.md §6–§7.

## Scope

Изменены только файлы выделенного writable scope. `app/services/generation.py`,
`app/bot/**`, `app/db/**`, `app/config.py` — НЕ тронуты (интеграция — lead, см.
«Новые сигнатуры» ниже). Raw history не удаляется и не изменяется (инвариант).

## Файлы

| Файл | Изменение |
|---|---|
| `app/context/builder.py` | A15 (покрытие истории по covered_until), A16 (current/tools в бюджете, `fits`, `max_output_tokens`), A18 (image → плейсхолдер/image-part) |
| `app/context/token_budget.py` | A16: `TokenBudgetManager.estimate_current(parts)` |
| `app/context/compactor.py` | A17: строгая `_normalize_summary` (→ None при невалидности), per-chat lock, monotonic guard, порционный prompt (~12000 символов, резка середины) |
| `app/memory/extractor.py` | A40: cap candidate.text = 500 символов |
| `tests/unit/test_context.py` | адаптация под новые сигнатуры + новые тесты A15/A16/A18 |
| `tests/unit/test_compactor.py` | новые тесты A17 (валидация, сериализация, monotonic, порционность) |
| `tests/unit/test_memory.py` | тест A40 (cap 500) |

## Новые точные сигнатуры (для lead)

### `ContextBuilder.build` (app/context/builder.py)

```python
def build(
    self,
    *,
    model: ModelDefinition,
    base_system_prompt: str,
    summary_json: dict[str, Any] | None,
    memories: list[str],
    history: list[Message],
    max_output_tokens: int | None = None,
    covered_until_message_id: uuid.UUID | str | None = None,   # НОВОЕ: ChatSummary.covered_until_message_id
    current_parts: list[dict[str, Any]] | None = None,         # НОВОЕ: parts текущего user-сообщения (текст+image)
    tools_token_estimate: int = 0,                             # НОВОЕ: оценка токенов effective tools-схем
) -> BuiltContext
```

### `BuiltContext` (добавлены 2 поля в конце — frozen dataclass, slots)

```python
@dataclass(frozen=True, slots=True)
class BuiltContext:
    system_prompt: str
    messages: list[dict[str, Any]]      # БЕЗ current; непокрытый сегмент включён
    needs_compaction: bool
    dropped_oldest: int
    max_output_tokens: int              # НОВОЕ: effective reserved output → LLMRequest.max_output_tokens
    fits: bool                          # НОВОЕ: False → даже system+tools+current+recent > available
```

### `TokenBudgetManager.estimate_current` (app/context/token_budget.py)

```python
def estimate_current(self, parts: list[dict[str, Any]] | None) -> int
# text → chars-per-token; image → image_tokens (1032); пустой ввод → 0.
```

### Что должен сделать lead в generation.py (контракт)

1. `build(...)`: передавать `covered_until_message_id=summary_row.covered_until_message_id if summary_row else None`, `current_parts=current_parts`, `tools_token_estimate=<оценка effective tools>` (0 допустимо до появления подсчёта схем).
2. `LLMRequest.max_output_tokens = built.max_output_tokens` (A16, контракт §6).
3. `if not built.fits: <честный отказ/усечение>` — builder гарантирует только флаг.
4. Окно выборки `list_recent(limit=recent_history_limit*2)` по-прежнему ограничивает
   непокрытый сегмент сверху — для полного закрытия дыры A15 lead может поднять лимит
   или читать от covered_until; builder корректно работает с любым окном
   (неизвестный covered_until = «вся выборка непокрыта»).
5. Rehydration image-bytes по `telegram_file_id` (A18 server-часть) — lead в
   `_prepare`; builder уже умеет принимать part с `data_base64` (см. ниже).
6. photos.py: parts должны нести `telegram_file_id` (+file_unique_id/mime/size в
   metadata_json) — фильтр `_PART_COLUMNS` в messages.py это уже пропускает.

## Решения

### A15 — покрытие истории (builder.py)
- History делится по позиции `covered_until_message_id` в ASC-списке: покрытый
  префикс (≤ covered_until) игнорируется; непокрытое = (после covered_until) минус
  keep_recent; recent = последние keep_recent непокрытых.
- covered_until не найден в выборке (старше окна/историю чистили) → вся выборка
  считается непокрытой (безопасный пересчёт). covered_until без summary_json
  игнорируется (нет сводки — нет покрытия).
- В messages: recent всегда + максимум непокрытых под бюджет (добор от свежих,
  старейшие отбрасываются → `dropped_oldest > 0`).
- `needs_compaction = True` при наличии summary и ЛЮБОГО непокрытого материала за
  пределами recent (даже если всё влезло — compactor решит по min_segment), либо
  при бюджетном давлении (`used > threshold`, включая ранний выход без older).
- Без summary — поведение «как раньше»: older добираются под бюджет,
  `needs_compaction = dropped_oldest > 0`.

### A16 — полный бюджет (builder.py, token_budget.py)
- `used = system + tools_token_estimate + estimate_current(current_parts) + recent`
  (+добираемые older). Image в current/history = 1032 токена/шт.
- `fits = used_минимальный <= budget.available` (без trigger_ratio): system + tools +
  current + recent. `fits=False` возможен и при непустом messages — recent священны
  и не режутся, решение об отказе за lead.
- `BuiltContext.max_output_tokens = budget.reserved_output` — явный параметр
  `max_output_tokens` переопределяет дефолтный `reserved_output` менеджера (4096).

### A17 — строгая summary + сериализация (compactor.py)
- `_normalize_summary(data) -> dict | None`: отклоняет `{}`, отсутствие любого из 6
  ключей, `conversation_summary` не-строку/пустую, list-ключи не-list. None →
  `maybe_compact` возвращает False, covered_until НЕ двигается. Repair — ровно один,
  как раньше (валидация применяется и к repair-ответу).
- Сериализация: модульный `_COMPACTION_LOCKS: dict[str, asyncio.Lock]` +
  `_chat_lock(chat_id)`; весь `maybe_compact` под `async with` — две compaction
  одного чата не идут параллельно (локи per-process; чатов мало, рост dict
  пренебрежим). Разные чаты не блокируют друг друга.
- Monotonic guard: перед `save` состояние перечитывается; новый covered_until обязан
  быть СТРОГО позже текущего по позициям в ASC-истории, иначе skip (False, без
  отката boundary). Текущий id, не найденный в истории → новый валиден (безопасный
  пересчёт). Новый id вне истории → skip (защита от мусора).
- Порционный prompt: `_cap_dialog` ограничивает рендер диалога `_MAX_DIALOG_CHARS =
  12_000` (константа модуля; в config не выносилась — config.py read-only в этой
  волне): голова и хвост получают по ≤ половине лимита, середина вырезается с
  маркером `… [середина фрагмента пропущена: N сообщ.] …`. Boundary продвигается на
  весь сегмент независимо от усечения prompt (сознательное решение по ТЗ: хвост
  сегмента не теряется).

### A18 (context-часть) — image в истории
- `_normalize_message`: image-part БЕЗ `data_base64` → `{"type":"text","text":"[изображение]"}`
  (факт фото не теряется); image-part С `data_base64` → `{"type":"image","data_base64":...,"mime_type":...}`.
  Порядок parts сохраняется. Реальная rehydration bytes — lead (generation._prepare).
- Legacy-записи без `telegram_file_id` невосстановимы (bytes в БД by design не
  хранятся) — для них плейсхолдер и есть корректная деградация. Отмечено по требованию
  профиля.

### A40 (context-часть)
- `parse_candidates`: `text.strip()[:500]` (`_MAX_CANDIDATE_CHARS = 500`); cap
  10 кандидатов сохранён. GIN index / fingerprint — владелец G (миграция), не трогал.

## Тесты

НЕ запускались pytest'ом (инструкция: только py_compile). Выполнено:
- `python -m py_compile` всех 7 изменённых файлов — OK.
- import-graph через `.venv` — OK.
- standalone smoke-скрипт (временный, удалён) на venv-python, воспроизводящий
  арифметику ВСЕХ новых и изменённых тестов (builder: 13 сценариев; compactor:
  {}-отказ, monotonic skip, orphan recompute, сериализация с asyncio.Event-gate,
  порционный prompt; memory: cap 500) — все assert'ы прошли («SMOKE OK»).

Новые/адаптированные тесты:
- test_context.py: `test_estimate_current_text_and_image`;
  `test_builder_with_summary_uses_recent_only` и `..._budget_pressure_needs_compaction`
  адаптированы (передают `covered_until_message_id`); бывший
  `test_builder_skips_image_only_messages_and_has_no_current` заменён на
  `test_builder_image_part_without_bytes_becomes_placeholder` (семантика A18: сообщение
  больше НЕ выпадает); `test_builder_image_part_with_bytes_kept_as_image`;
  A15: `test_builder_uncovered_segment_included_with_summary`,
  `test_builder_boundary_strictly_at_covered_until`,
  `test_builder_uncovered_dropped_oldest_by_budget`,
  `test_builder_unknown_covered_id_treats_all_as_uncovered`;
  A16: `test_builder_current_and_tools_counted_in_budget`,
  `test_builder_current_image_counted_in_budget`,
  `test_builder_fits_false_when_minimum_exceeds_available`,
  `test_builder_propagates_max_output_tokens`.
- test_compactor.py: `test_normalize_summary_strict_validation`,
  `test_empty_json_object_rejected_boundary_not_moved`,
  `test_wrong_typed_summary_rejected_boundary_not_moved`,
  `test_missing_keys_summary_rejected_boundary_not_moved`,
  `test_invalid_json_then_valid_repair_still_saved`,
  `test_concurrent_compaction_same_chat_serialized` (asyncio.Event gate: вторая
  compaction ждёт lock, LLM вызван один раз, saved == 1),
  `test_boundary_never_moves_backwards` (stale snapshot + advanced fresh state),
  `test_unknown_existing_boundary_allows_safe_recompute`,
  `test_long_segment_prompt_capped_middle_elided` (голова+хвост есть, середина
  вырезана, boundary на весь сегмент). Существующие тесты не ослаблены.
- test_memory.py: `test_parse_candidates_caps_text_length`.

## Не проверено / ограничения

- pytest-прогон всего набора не выполнялся (инструкция); арифметика верифицирована
  smoke-скриптом, но финальный `pytest tests/unit/...` — за lead/CI.
- Интеграция с generation.py (передача covered_until/current_parts/tools, проброс
  max_output_tokens в LLMRequest, реакция на fits=False, rehydration image-bytes,
  telegram_file_id в photos.py) — НЕ сделана, ждёт lead по сигнатурам выше.
- `_COMPACTION_LOCKS` — process-wide; межпроцессная гонка compaction не закрыта
  (в проекте один worker; CAS в upsert — при необходимости отдельной задачей G).
- Окно выборки истории `recent_history_limit*2` в generation._prepare по-прежнему
  может обрезать длинный непокрытый сегмент до builder'а — пункт 4 контракта для lead.
