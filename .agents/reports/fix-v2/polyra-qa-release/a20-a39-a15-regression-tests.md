# polyra-qa-release — re-audit регрессии A20/A39/A15 (commit 0be0524)

Дата: 2026-09-26. Исполнитель: polyra-qa-release (subagent). База: HEAD `0be0524`
+ незакоммиченные изменения других агентов в shared worktree (`app/api/**`,
`app/context/compactor.py`, `tests/unit/test_api.py`, `tests/unit/test_memory.py`,
`miniapp/**` — модифицированы не мной и мной не тронуты).

## Scope (выдан lead)

Writable: один файл — новый `tests/integration/test_fix_v2_reaudit.py`
(выбран вариант «новый файл»; `test_fix_v2_usage_ledger.py` не трогал — тема §2).
Production-код `app/services/generation.py` НЕ менялся (временно подставлял
старую версию только для локальной проверки красноты тестов, восстановил
byte-в- byte: `git status` подтверждает, файл чист).

## ⚠ BLOCKER для владельца generation.py (найден тестом, НЕ починен мной)

**P0: `NameError: name 'ContextBuilder' is not defined`** —
`app/services/generation.py:592` использует `ContextBuilder(...)` в рантайме,
но он импортирован ТОЛЬКО под `if TYPE_CHECKING:` (строка 61; рантайм-импорт
из `app.context` есть только для `TokenBudgetManager`). Production-wiring
`app/main.py:47,83` ВСЕГДА передаёт реальный `context_builder` в
`GenerationService` → builder-путь `_build_context` выполняется на каждом
запросе → необработанный `NameError` из `_prepare` (try/except нет) —
**каждый ответ модели падает**. Регресс внесён самим коммитом 0be0524
(замена `self._context_builder.build(...)` на локальный `ContextBuilder(...)`
без переноса импорта из TYPE_CHECKING). Репро:
`python -c "import app.services.generation as g; hasattr(g, 'ContextBuilder')"`
→ `False` (проверено). Единственный тест, который это ловит, — мой A15-тест
(до шима он падал именно с этим NameError).

**Предлагаемый фикс владельцу** (одна строка, у меня нет прав на этот файл):
в `app/services/generation.py` перенести `ContextBuilder` из TYPE_CHECKING-блока
в рантайм-импорт: `from app.context import ContextBuilder, TokenBudgetManager`.
После этого мой тестовый шим (см. ниже) станет no-op и его можно удалить.

В моём тесте A15 стоит документированный monkeypatch-шим
`monkeypatch.setattr(generation_module, "ContextBuilder", ContextBuilder, raising=False)`
— инъекция настоящего класса в namespace модуля, чтобы тест проверял контракт
A15, а не падал на чужом импорт-баге. Шим не лечит production и чистится
pytest'ом после теста.

## Файл

| Файл | Изменение |
|---|---|
| `tests/integration/test_fix_v2_reaudit.py` | НОВЫЙ — 3 теста (A20/A39/A15) |
| `.agents/reports/fix-v2/polyra-qa-release/a20-a39-a15-regression-tests.md` | этот отчёт |

Harness переиспользован из `tests/integration/test_fix_v2_usage_ledger.py` /
`test_fix_v2_stream_contract.py`: `FakeStreamer`, `_stream_of`, `_empty_stream`,
`_permissions_stub`, `_prepared_stub`, `_echo_engine`, `_FakeSession(Factory)`,
fake-репозитории через `monkeypatch.setattr(generation_module, ...)`,
`_RecordingBot` — новый фейк-бот, пишущий вызовы как `(chat_id, text, kwargs)`.

## Тесты и что ассертится

### 1. `test_save_cancelled_partial_plain_parse_mode_none_and_split` (A20)

Сценарий: `_stream_loop` с фейковым стримом (TextDelta с `<div>…</div>` +
5000 символов, затем `cancellation.set()` — Stop на середине; хвост стрима
не доезжает) → `outcome.cancelled=True`, partial 5032 симв. → `_save_cancelled`
с `_RecordingBot`. Ассерты:
- (a) хотя бы один `send_message`;
- (b) у КАЖДОГО вызова `kwargs["parse_mode"]` явно передан и `is None`
  (default бота — HTML: `<div>` без этого = BadRequest, partial терялся бы);
- (c) partial > 4096 разбит: частей > 1, каждая ≤ `MESSAGE_LIMIT` (4096),
  `"".join(parts) == outcome.text` (полный текст, порядок сохранён);
- (d) `<div>` реально уезжает в отправленном тексте — обоснование
  обязательности `parse_mode=None`;
- персистенс отмены не потерян (`status="cancelled"`, `finish` вызван).

### 2. `test_tool_round_batch_over_limit_keeps_only_executed_calls_in_history` (A39)

Сценарий: РОВНО 5 `ToolCall` (`call_0..call_4`) одним раундом при
`Settings(max_tool_calls_per_round=4)`; фейковый стрим записывает все
`LLMRequest`; второй раунд — текст + Done. Ассерты на второй запрос
(`calls[1].messages`):
- (a) в assistant-сообщении ровно 4 tool_call-части;
- (b) ровно 4 tool-сообщения / 4 tool_result-части;
- (c) `set(id tool_call) == set(id tool_result) == {call_0..call_3}` —
  нет «висящих» вызовов без результата;
- (d) `"call_4" not in json.dumps(second_messages)` — 5-й вызов не
  протекает ни в id, ни в arguments, ни в содержимое;
- sanity: `len(calls) == 2`, `len(tool_records) == 4`, финал не отменён.

### 3. `test_build_context_with_summary_does_not_duplicate_current_message` (A15)

Сценарий: `_build_context` напрямую (приватный метод, как в соседях) с
фейковым `ChatSummaryRepository` (сводка есть, `covered_until_message_id`
на первое сообщение) и `_FakeMessagesRepo.list_all`, возвращающим историю
ВКЛЮЧАЯ только что добавленное user-сообщение (имитация `add_message →
list_all` в одной сессии); `exclude_message_id` = id этого сообщения.
Ассерты на итоговые `llm_messages`:
- (a) маркер `MARKER-CURRENT-MSG` встречается в user-сообщениях ровно
  ОДИН раз (только как отдельно добавленный current, не из history);
- (b) предпоследнее сообщение истории (не current) присутствует;
- sanity: `list_all` действительно вызван (путь со сводкой), сводка
  отрендерена в system prompt, отказа «контекст слишком большой» не было.

## Результаты прогонов (HEAD `0be0524` + shared worktree)

| Команда | Результат |
|---|---|
| `.venv\Scripts\python -m pytest tests/integration/test_fix_v2_reaudit.py -q` | **3 passed** (3.85s), exit 0 |
| `.venv\Scripts\python -m pytest tests/integration -q` | **27 passed** (24 базовых + 3 новых), exit 0 |
| `.venv\Scripts\python -m ruff check tests` | **All checks passed**, exit 0 |
| `.venv\Scripts\python -m mypy tests` | **Success: no issues found in 25 source files**, exit 0 |

Базлайн до моих правок: `tests/integration` — 24 passed, mypy — 24 файла, exit 0.

## Проверка «упал бы до фикса» (реальный прогон, не рассуждение)

Временно подставил `git show 0be0524^:app/services/generation.py` в рабочую
копию, прогнал свой файл, восстановил HEAD-версию (git status: файл чист).
Все 3 упали, причины — целевые:

| Тест | Падение на `0be0524^` |
|---|---|
| A20 | `AssertionError: assert 'parse_mode' in {}` — старый код звал `send_message(tg_chat_id, text)` вообще без parse_mode (и обрезал partial до 4095+«…» — concat-ассерт тоже красный) |
| A39 | `AssertionError: assert 5 == 4` — старый код клал в assistant turn ВСЕ 5 вызовов, исполняя только 4 (call_4 без tool_result → (c)/(d) тоже красные) |
| A15 | `TypeError: _build_context() got an unexpected keyword argument 'exclude_message_id'` — параметра не существовало; без фильтра маркер встречался бы дважды |

## Ограничения / не сделано

- P0 NameError (см. BLOCKER выше) не починен — вне моего writable scope;
  нужен фикс владельца `app/services/generation.py`. Мой шим в тесте —
  временная мера, задокументирована в докстринге теста.
- `pytest tests` целиком (включая unit) не гейт моего отчёта: unit-файлы
  (`tests/unit/test_api.py`, `tests/unit/test_memory.py`) в этот момент
  правят другие агенты параллельно.
- Живых API-вызовов не делал (всё offline, контракт FIX_V2).
