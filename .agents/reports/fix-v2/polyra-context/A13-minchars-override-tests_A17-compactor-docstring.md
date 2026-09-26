# Task: A13 (per-call min_chars override tests) + A17 (_cap_segment_prefix docstring)

Agent: polyra-context (subagent E)
Date: 2026-09-26
Writable scope по заданию lead: `tests/unit/test_memory.py`, `app/context/compactor.py` (только комментарий/докстринг).

## Изменённые файлы

| Файл | Тип изменения |
|---|---|
| `tests/unit/test_memory.py` | +3 новых регрессионных теста (A13), логика существующих не тронута |
| `app/context/compactor.py` | только докстринг `_cap_segment_prefix` + inline-комментарий fallback-ветки (A17, косметика); логика НЕ менялась |

## Задача 1 — регрессионные тесты per-call override min_chars (A13)

Фикс (commit 0be0524, уже в кодовой базе): `MemoryExtractor.extract_and_store(..., min_chars: int | None = None)`
— per-call override бьёт ctor-значение; `None` → ctor-значение (app/memory/extractor.py:212).

Новые тесты (по образцу соседних тестов экстрактора: фейки `_stream_fn`/`FakeMemoryStore`, хелпер `_extractor`):

1. `test_extractor_per_call_min_chars_overrides_ctor_upward` — ctor `min_chars=10`, вызов с `min_chars=200`,
   короткий обмен («короткий вопрос» + «короткий ответ», 29 симв.) → `added == 0`,
   LLM-стрим НЕ вызывался (`calls == []`), `store.add_calls == []`.
2. `test_extractor_per_call_min_chars_overrides_ctor_downward` — ctor `min_chars=200`, вызов с `min_chars=10`,
   тот же обмен → извлечение выполнено: `added == 2`, `len(calls) == 1`, `len(store.add_calls) == 2`
   (валидный JSON `_VALID_JSON` → 2 новых записи, стора пустая).
3. `test_extractor_per_call_min_chars_none_falls_back_to_ctor` (граница) — ctor `min_chars=200`, вызов с
   `min_chars=None` → применяется ctor-значение: `added == 0`, `calls == []`.

У всех трёх — докстринги со ссылкой на A13. Размещены сразу после
`test_extractor_skips_short_exchange_by_min_chars` (группировка по теме).

Примечание: lead в задании назвал метод `extract(...)`, реальное имя в кодовой базе —
`extract_and_store(...)` (keyword-only); тесты используют реальный API.

## Задача 2 — докстринг `_cap_segment_prefix` (A17)

Проблема: докстринг обещал «они принудительно включаются с усечением текста каждого
сообщения (лучше грубая сводка, чем никакой)», но fallback-ветка реально возвращает
`segment[: max(min_segment, 1)]` БЕЗ какого-либо усечения текстов. Тот же ложный тезис
был в inline-комментарии («берём min_segment с грубым усечением текстов»).

Исправлено (только текст, `return segment[: max(min_segment, 1)]` без изменений):
- докстринг теперь честно описывает: сообщения включаются КАК ЕСТЬ, без усечения;
  сверхбольшой prompt уходит во внутреннюю модель; при неудаче сводки boundary
  не двигается — безопасный отказ, сегмент повторится на следующем запуске
  (соответствует поведению `maybe_compact`: `summary is None` → return False,
  boundary не сохраняется);
- inline-комментарий fallback-ветки приведён в соответствие с кодом.

## Результаты прогонов

| Команда | Результат | Exit code |
|---|---|---|
| `.venv\Scripts\python -m pytest tests/unit/test_memory.py tests/unit/test_compactor.py -q` | 63 passed in 0.84s | 0 |
| `.venv\Scripts\python -m pytest tests/unit/test_memory.py -q -k "per_call_min_chars" -v` | 3 passed, 28 deselected | 0 |
| `.venv\Scripts\python -m ruff check app/context tests/unit/test_memory.py` | All checks passed! | 0 |
| `.venv\Scripts\python -m mypy app tests` | Success: no issues found in 153 source files | 0 |

## Инварианты

- Файлы вне scope не тронуты (app/api/**, tests/unit/test_api.py, tests/integration/**, miniapp/** — не трогал).
- Логика `_cap_segment_prefix` и всего `app/context/compactor.py` не изменена (только докстринг + комментарий).
- Live API вызовы, git-операции — не выполнялись.
- Model IDs, endpoints, история/БД — не затронуты.
