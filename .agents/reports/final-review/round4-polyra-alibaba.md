# Round 4 — финальный баг-хантинг: POLYRA / Alibaba + registry + probe-контракт

- **HEAD:** `4925c40` (рабочее дерево чистое)
- **Агент:** polyra-alibaba (bug-hunter, READ-ONLY)
- **Scope:** `app/llm/providers/alibaba.py`, `app/llm/capabilities.py`, `app/llm/registry.py`, `app/llm/base.py`, `app/llm/events.py`, `app/llm/errors.py`, `scripts/smoke_providers.py`, `app/api/routes/me.py`, `tests/unit/test_alibaba_provider.py`, `tests/unit/test_registry.py`
- **Метод:** полное чтение файлов, offline-прогоны (pytest/mypy/ruff), runtime-симуляция A23 через `httpx.MockTransport`, inspect-сверка сигнатур, git-археология 0be0524.

## Executive summary

**P0 не найдено.** Тип ошибки из 0be0524 (TYPE_CHECKING-импорт в рантайме) в текущем scope
воспроизведён НЕ был: `SafetyError` импортируется в `alibaba.py` обычным runtime-импортом
(строки 21–31), модули `me.py` и `smoke_providers.py` реально импортируются (exec_module
без выполнения `argparse`/`main`), все вызываемые имена/сигнатуры существуют
(`set_value`, `CAPABILITY_PREFIX`, `create_engine_from_url`, `make_session_factory`,
`get_provider_api_key`, `CryptoBox.decrypt`, `mask_secret`, `GeminiProjectRepository.list_all`,
`GeminiProvider(**)`, `PoolExhaustedError`). `app.state.registry`/`app.state.settings`
инициализируются в `app/api/app.py:38,41`.

Найдено: **1×P1 (семантика probe→UI контракта, стирает evidence при transient-ошибках)**,
**3×P2, 4×P3**. A23 (`content_filter` → `SafetyError`) работает верно, но не покрыт тестами.

---

## P0 — несуществующие имена/методы в рантайме

**Не найдено.** Целевая проверка ошибки класса 0be0524:

- `alibaba.py:21-31` — `SafetyError` в обычном import-блоке (не TYPE_CHECKING):
  ```python
  from app.llm.errors import (
      AuthError, ... RateLimitError, SafetyError, ServerError, ...
  )
  ```
  `errors.py:92-94` — класс существует, категория `SAFETY`, `retryable=False` по умолчанию.
- Runtime-прогон `python -c "import app.api.routes.me"` + `exec_module(smoke_providers.py)` — **IMPORT OK**;
  `parse_args`/`main` не выполняются при импорте (код argparse целиком внутри функций,
  `smoke_providers.py:685-717,761`).
- Inspect-сверка: `set_value(self, key: str, value: Any)`, `CAPABILITY_PREFIX="capability_probe:"`,
  `get_provider_api_key(session, crypto, provider, env_fallback="")` — вызов в smoke
  (`smoke_providers.py:438`) совпадает.
- mypy: 7 файлов + `scripts/smoke_providers.py` — `Success: no issues`; ruff по 10 файлам —
  `All checks passed`.

## P1 — probe→UI контракт: transient-ошибка стирает capability evidence

**Файлы:** `scripts/smoke_providers.py:615-642` (`_runtime_entries`), читатель
`app/api/routes/me.py:43-53` + `app/services/settings.py:50-76` (контракт).

**Механика.** `_runtime_entries` включает в запись модели все checks со статусом `ok`
и пишет `upsert` по ключу `capability_probe:<model_id>` безусловно, если секция провайдера
`status=="ok"` (это значит лишь «ключ получен», не «все checks честные»):

```python
accepted = [name.removeprefix("thinking:") for name, check in checks.items()
            if name.startswith("thinking:") and check.get("status") == "ok"]
entries[model_id] = {"accepted_thinking": accepted, "text_ok": ..., "at": ..., "endpoint": ...}
```

При этом «fail» в `_run_check` (`smoke_providers.py:176-190`) **конфлюэнтен**: и «API отверг
параметр» (InvalidRequestError), и «transient» (429/5xx/timeout — тоже ProviderError → `_fail`).
`_check_thinking_level` различает только первый случай (`except InvalidRequestError → rejected`).

**Прод-проявление (воспроизведено offline, MockTransport не нужен — чистая логика):**

```
report: qwen3.8-flash checks: text_stream=ok, thinking:off=ok, thinking:low=FAIL(429)
→ запись: accepted_thinking=["off"], text_ok=true
→ me.py: thinking_modes=['off']           # low/medium/max скрыты на 7 дней (TTL)
```

Старая запись (где low/medium/max были accepted) **заменяется** upsert'ом → прежнее успешное
evidence стерто из-за 429. Это прямое нарушение инварианта «Старые успешные capability
evidence не стирать из-за timeout/429/5xx/no-key» (skipped-модели не затираются — это
соблюдено; затираются модели с transient-fail на отдельных checks).

**Сопутствующая несогласованность:** `_alibaba_recommendations` (`smoke_providers.py:538-543`)
при `text_stream != ok` честно пишет «базовый запрос fail — уровни не оценены», но
`_runtime_entries` этот guard **не имеет**: модель с fail text_stream и ok thinking-уровнями
получает запись, и `me.py` показывает эти режимы (см. P2-2).

**Рекомендация (владельцу smoke/registry):** не включать уровень в accepted при
категориях rate_limit/server/network/timeout (fail ≠ rejected); при text_stream fail —
не писать accepted вовсе (только text_ok=false) либо пропускать запись модели.

## P2

### P2-1. A23 content_filter → SafetyError: нулевое тестовое покрытие

`alibaba.py:265-267` — единственное поведение-изменение 0be0524 в провайдере:

```python
if finish_reason == "content_filter":
    # A23: модерация контента — отдельная категория, не сетевой сбой.
    raise SafetyError("alibaba finish_reason=content_filter")
```

`git log -S "content_filter" -- tests/` — **пусто**. У Gemini SafetyError покрыт в
3 файлах (`test_gemini_provider.py:140,146`, `test_gemini_pool.py:472`, `test_generation.py:121`),
у Alibaba — ни одного теста. Поведение я проверил runtime-симуляцией (см. ниже) и оно верно,
но рефактор except-цепочки в `stream_chat` (например, расширение кортежа
`(RateLimitError, ServerError, NetworkError)` до `ProviderError`) молча сделает safety
ретраебельным — тест-сетки нет.

**Runtime-проверка (offline, MockTransport):**

| Сценарий | События до ошибки | Итог | HTTP-вызовов |
|---|---|---|---|
| partial text + content_filter | `TextDelta('partial answer')` | `SafetyError` | 1 (ретрая НЕТ) |
| pending tool_calls + content_filter | `[]` (pending молча теряется) | `SafetyError` | 1 |
| usage + content_filter в одном чанке | `[]` (usage чанка потерян, см. P3-1) | `SafetyError` | 1 |

Ретрай не происходит, т.к. `SafetyError` не входит ни в один except в `stream_chat`
(`alibaba.py:384-389`) — корректно: safety не повторяем. Ротации нет: у Alibaba нет пула,
`generation.py:689-693` → `user_error_message` → «Запрос отклонён модерацией провайдера.»
(`generation.py:182-183`). Пользователь видит частичный текст драфта + сообщение об ошибке
(паритет с Gemini, `test_fix_v2_stream_contract.py:385`).

### P2-2. me.py `_model_out` игнорирует `text_ok` (и `endpoint`)

`me.py:43-53` — фильтр по `accepted_thinking` применяется при любой свежей записи:

```python
accepted = probe.get("accepted_thinking") if probe else None
if isinstance(accepted, list) and accepted:
    thinking_modes = [mode for mode in model.thinking_modes if mode in accepted]
```

`text_ok=False` (базовый text_stream не прошёл) не проверяется — вопреки логике
рекомендаций smoke. Запись с `accepted_thinking=["low"], text_ok=false` → UI покажет
`['low']`, хотя базовая способность модели не подтверждена. Ключи `text_ok`/`endpoint`
пишутся (`smoke_providers.py:638,640`) и **никем не читаются** — половина write-контракта A27
мертва (endpoint-валидация была бы дёшевым защитным фильтром).

### P2-3. `_model_out`: `default_thinking` не фильтруется по accepted

`me.py:63` — `"default_thinking": model.default_thinking` отдаётся как есть. Если probe
исключил дефолтный режим (GLM-5.3 default `max` не попал в accepted), UI получает
`thinking_modes=['low']` + `default_thinking='max'` — режим, которого нет в списке.
Роутинг это переживёт (`resolve_model_and_thinking` проверяет по registry, не по probe,
`generation.py:209-210`), но Mini App может пре-выбрать недоступный режим.

## P3

### P3-1. Usage в том же чанке, что content_filter, теряется

`alibaba.py:296-304` — `usage` извлекается и кладётся в `events` ДО разбора choices, но
raise внутри `_events_from_choice` выбрасывает до возврата `events` из `_process_sse_line`.
Реально usage идёт trailer-чанком после finish — путь редкий; счёт токенов конкретного
заблокированного запроса занижается (usage учтётся как None).

### P3-2. Механизм `probe_required` мёртв

`capabilities.py` — ни одна модель не задаёт `probe_required` (все frozenset() по умолчанию).
Следствия: `smoke_providers.py:391-392` (`if THINKING_OFF in definition.probe_required`) —
never-fired ветка; фильтр в `me.py:53` — no-op. При этом `docs/vendor/ALIBABA.md:89`
говорит про deepseek-v4-pro: «Acceptance OFF … — на runtime probe» — до первого
`--write-runtime` UI покажет неподтверждённый OFF. Соглашусь, что прецедент (kimi OFF
подтверждён probe 2026-09-18 и снят) — рабочий процесс, но для Pro поле не задействовано
ни разу. Либо задать `probe_required={"off"}` для Pro до первого probe, либо удалить
механизм.

### P3-3. `_PNG_1X1_BASE64` содержит 32×32 PNG

`smoke_providers.py:72-76` — имя константы «1X1», комментарий «32x32 PNG». Данные —
действительно 32×32 (`iVBORw0KGgoAAAANSUhEUgAAACAAAAAg` → IHDR 0x20×0x20). Имя вводит
в заблуждение при сопровождении (история: 1×1 отвергался Alibaba с invalid_parameter).

### P3-4. `_write_runtime`: создание engine вне try

`smoke_providers.py:654` — `engine = create_engine_from_url(...)` до `try:`. Если URL
масштаба «не парсится» (sqlalchemy.ArgumentError при создании), исключение вылетит из
`_write_runtime` → `main()` упадёт трейсбеком ДО `_print_table`/`_write_report` —
JSON-отчёт probe (стоимость реальных запросов уже оплачена) потерян. Edge-case
(создание engine не ходит в сеть и почти никогда не падает), но guard дёшев.

## OK — проверено и чисто

1. **SafetyError-таксономия и ротация.** `errors.py:92-94` — `SAFETY`, `retryable=False`;
   docstring-политика `errors.py:9` («safety — не ротировать пул, вернуть пользователю»)
   соблюдена: alibaba-провайдер не ловит SafetyError в retry-цепочке
   (`alibaba.py:384-389`, симуляция: 1 HTTP-вызов); Gemini-пул не ставит cooldown
   (`pool.py:296`); пользовательское сообщение — `generation.py:182-183`.
2. **Write/read-контракт A27.** smoke пишет `accepted_thinking/text_ok/at/endpoint`
   (`smoke_providers.py:636-641`); me.py читает `accepted_thinking`/`at` (`me.py:46,55`).
   `at` = `datetime.now(UTC).isoformat(timespec="seconds")` → `datetime.fromisoformat`
   на Python 3.14.7 парсит `+00:00`; TTL 7 суток, tz-naive нормализуется
   (`settings.py:69-73`); колонка `SystemSetting.value` — **JSONB** (dict round-trip),
   key `String(64)` — длиннейший `capability_probe:deepseek-v4.1-flash` = 36 ≤ 64.
   `SessionDep` → `AsyncSession` (`dependencies.py:30`) совместим с
   `get_probe_capabilities(session)`. Стороны ключей совпадают.
3. **Alibaba SSE.** Ровно один Done; Usage строго до Done (буферизация pending_done,
   `alibaba.py:299-329`); EOF без finish/[DONE] → `NetworkError("unexpected EOF")`;
   недособранные tool_calls при EOF — та же ошибка; fragmented args копятся по index
   (`_accumulate_tool_call`), id/name из первого чанка, фолбэк `call_{index}`; порядок
   «retого» — Done последним. Покрыто unit + integration
   (`test_fix_v2_stream_contract.py:149-283,415`).
4. **Wire-инварианты.** `max_completion_tokens` (не `max_tokens`) — `alibaba.py:167`,
   соответствует `docs/vendor/ALIBABA.md:83`; `trust_env=False` — `alibaba.py:347` (default
   client), `smoke_providers.py:507`; `base_url` только из `settings.alibaba_base_url`
   (`llm_factory.py:89`, `admin_providers.py:127`, smoke:506/512) — нигде не переопределяется
   на не-dashscope; endpoint всегда `/chat/completions` от compatible-mode/v1.
5. **Thinking-маппинги vs docs (§9.1 + update 2026-09-24).** `_THINKING_TABLE`
   (`alibaba.py:45-82`) согласован с доком по всем 6 alibaba-моделям: qwen3.8
   off/low/medium/xhigh + `preserve_thinking=False` всегда; deepseek-v4.1-flash
   off/low/high/max; deepseek-v4-pro **off/high/max без low/medium** (native high/max,
   low — alias; LOW не «изобретён» по аналогии с Flash); glm-5.3 low/high/max +
   `clear_thinking=True` (только при явном thinking); kimi off/low/high/max. Запрещённые
   параметры (`thinking_budget`, `preserve_thinking` вне qwen, `clear_thinking` вне GLM)
   не отправляются — parametrized-тесты `test_build_payload_thinking` /
   `test_build_payload_deepseek_v4_pro_thinking` (absent-списки).
6. **Registry/capabilities.** 10 моделей (4 gemini + 6 alibaba), Pro — дополнение,
   Flash не заменён (`test_registry.py:7-25`); deepseek-v4-pro text-only
   (`input_modalities=frozenset({"text"})`, max_context 1M, max_output 393216,
   modes (off,high,max), default None); internal gemini-3.5-flash-lite скрыт из UI.
   Probe enumeration из registry (`smoke_providers.py:91-93`), минимум 10 ID
   выполняется автоматически.
7. **Retry-политика провайдера.** Retry только network/timeout/429/5xx и только до
   первого события (`alibaba.py:357-399`); 400/401/403/404 не ретраятся (проверено
   тестами `test_no_retry_on_400`, симуляцией SafetyError); отмена — тихий выход без Done.
8. **Тесты: что мокается.** `test_alibaba_provider.py` — MockTransport, реальный SSE-парсер
   и retry-логика (не скрывает прод); `test_api.py:196` мокает `get_probe_capabilities`
   на уровне роута, но сам сервис TTL/parse протестирован напрямую
   (`test_api.py:340-369`). Пробелы покрытия — P2-1 и отсутствие тестов на
   `_runtime_entries`/`_write_runtime` (мой P1 попал бы именно туда).
9. **Прогоны.** `pytest tests/unit/test_alibaba_provider.py tests/unit/test_registry.py` —
   **38 passed**; расширенный прогон (+test_api, +stream-contract integration) —
   **93 passed in 8.07s**; mypy — чисто; ruff — чисто. Git/live-вызовы не выполнялись.

## Сводка действий

| # | Находка | Серьёзность | Файлы:строки | Действие |
|---|---|---|---|---|
| 1 | --write-runtime стирает accepted-evidence при 429/5xx/timeout на отдельных checks; text_ok игнорируется при записи | P1 | scripts/smoke_providers.py:615-642,176-190 | proposal владельцу: fail≠rejected, guard text_stream |
| 2 | A23 content_filter→SafetyError без единого теста | P2 | app/llm/providers/alibaba.py:265-267; tests: — | добавить 3 теста (partial/pending/no-retry) |
| 3 | _model_out не проверяет text_ok/endpoint записи | P2 | app/api/routes/me.py:43-53 | читать text_ok, скрывать режимы при false |
| 4 | default_thinking не фильтруется по accepted | P2 | app/api/routes/me.py:63 | дефолт вне accepted → null/первый режим |
| 5 | usage+content_filter в одном чанке теряется | P3 | app/llm/providers/alibaba.py:251-305 | принять/задокументировать |
| 6 | probe_required мёртв; OFF Pro не гейтится до probe | P3 | app/llm/capabilities.py:44-45; smoke:391 | задать для Pro или удалить механизм |
| 7 | _PNG_1X1_BASE64 = 32×32 | P3 | scripts/smoke_providers.py:73 | переименовать |
| 8 | create_engine_from_url вне try в _write_runtime | P3 | scripts/smoke_providers.py:654 | обернуть |
