# polyra-alibaba — ревалидация против текущего кода + N04 research

**Дата:** 2026-09-24
**Субагент:** polyra-alibaba (R-alibaba, read-only; production код не менялся)
**Baseline:** HEAD 4e07eb0, `pytest tests/unit/test_alibaba_provider.py tests/unit/test_registry.py` = **29 passed** (`.venv`, Python 3.14).
**Контракты:** `.agents/POLYRA_FIX_V2_CONTRACTS.md` (2026-09-24). Writable scope этой задачи — только `.agents/reports/fix-v2/`; файлы `app/services/generation.py`, `app/services/credentials.py`, `app/main.py` — чужие scope, по ним только предложения lead/владельцам.

---

## A23. SSE-контракт (stream / usage / EOF)

Код: `app/llm/providers/alibaba.py` (`parse_chat_completions_sse` L264-289, `_events_from_choice` L243-261, `stream_chat` L317-351, `_cancellable_lines` L353-363), потребитель `app/services/generation.py` `_consume` L595-648. Контракт FIX_V2 §1: ровно один терминальный `Done`; `Usage` ДО `Done`, никогда после; отсутствие `Done` к концу потока = unexpected EOF → ошибка.

| Подпункт | Verdict | Evidence |
|---|---|---|
| Тихий конец без `[DONE]`/без `finish_reason` | **FAIL** | Парсер при исчерпании строк просто завершает генератор (нет `Done` — нет ошибки); `stream_chat` после цикла делает `return` (alibaba.py:335). `_consume` завершает цикл с `finish_reason=None`, `cancelled=False` (generation.py:632-648) → `_stream_loop` трактует как успех (текст сохраняется, run=completed). Тихий обрыв = «успех», прямое нарушение §1. Дополнительно usage-чанк при обрыве теряется молча. |
| Malformed JSON-строки | PARTIAL | `json.JSONDecodeError` → `logger.debug` + `continue` (alibaba.py:277-279), truncate 200. Per-line — допустимо, но при потоке из одних битых строк получаем пустой «успех» (см. выше) — на уровне сервиса спасает только «пустой ответ»-фолбэк (generation.py:365-375). |
| Fragmented tool arguments | PARTIAL | Аккумуляция по `index` корректна (alibaba.py:217-226, тест `test_tool_calls_incremental`). Но pending вызовы флашатся ТОЛЬКО при `finish_reason=="tool_calls"` (L257-258). Обрыв/EOF до finish → накопленные tool calls молча теряются; `finish_reason="stop"` при непустом pending → тоже потеря. Битый JSON аргументов после обрыва доезжает до ToolRunner как `invalid_args` — это ок (runner.py:79-89). |
| Error frame после текста | OK | `chunk["error"]` → `classify_stream_error` raise (alibaba.py:282-283); `events_started=True` → retry запрещён, ошибка уходит в `_stream_loop` → `streamer.fail` + `save_failed` (generation.py:440-444). Покрыто `test_stream_error_throttling_raises_rate_limit`. |
| Пустой `choices` + usage trailer | OK (парсер) | `_extract_usage` вызывается до цикла по choices (alibaba.py:284-286), пустой `choices: []` обрабатывается; тест `test_usage_final_chunk` зелёный. |
| Порядок Done vs Usage | **FAIL (критично)** | Документация: usage-чанк с `choices: []` приходит ПОСЛЕ чанка с `finish_reason` (alibaba.md §6, подтверждено chat-ref 2026-09-22). Текущий парсер эмитит `Done` на чанке с finish_reason (alibaba.py:259-260), а `Usage` — следующим чанком, т.е. порядок событий `[TextDelta, Done, Usage]` (зафиксирован в тесте `test_usage_final_chunk`, test_alibaba_provider.py:115-139). Это нарушение §1 «Usage ДО Done». Хуже того, `_consume` делает `break` на `Done` (generation.py:632-634) и **никогда не видит Usage**: для всех Alibaba-генераций `outcome.usage=None` → `generation_runs`/`messages` пишут NULL-токены (generation.py:849-862), дневной token_limit по факту не работает для alibaba. 100% потеря usage-ledger. |

**Fix-план A23** (владелец `alibaba.py` — этот профиль; `generation.py` — предложение lead):
1. В `parse_chat_completions_sse` буферизовать `finish_reason` (и флаг seen_done) вместо немедленной эмиссии `Done`; эмитить `Done` последним — после того, как получен и эмитнут usage-trailer, при `[DONE]`/EOF. Тогда порядок станет `[TextDelta..., Usage?, Done]`, и `break` в `_consume` безопасен.
2. EOF/`[DONE]` без `finish_reason` → raise `NetworkError("unexpected EOF")` (retryable только до первого события — существующая логика `events_started` это уже обеспечивает). Исключение: отмена — `stream_chat` после цикла проверяет `request.cancellation.is_set()` и завершает тихо (иначе cancel-путь `_cancellable_lines` L359-362 превратится в ложную EOF-ошибку).
3. Несброшенные pending tool calls при EOF без finish → часть той же EOF-ошибки (не молчать).
4. `_consume` (чужой файл): после фикса парсера `break` на `Done` корректен; альтернативно убрать `break` и дочитывать генератор (идемпотентно, устойчивее к будущим провайдерам) — решение владельцу generation.py.
5. Тесты: переупорядочить `test_usage_final_chunk` на `[TextDelta, Usage, Done]`; добавить: EOF без finish → NetworkError; EOF с pending tool calls → NetworkError; error frame после текста → RateLimitError без retry; malformed lines игнорируются.

---

## A27. Probe completeness (`scripts/smoke_providers.py`)

| Подпункт | Verdict | Evidence |
|---|---|---|
| `_GEMINI_MODELS` | **FAIL: пропуск** | `("gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite")` (smoke_providers.py:67). **`gemini-3.7-flash` пропущен** (есть в registry, capabilities.py:67-76). |
| `_ALIBABA_MODELS` | **FAIL: пропуск** | `("qwen3.8-flash", "deepseek-v4.1-flash", "glm-5.3", "kimi-k3")` (smoke_providers.py:68). **`qwen3.8-max` пропущен** (capabilities.py:110-117). |
| Enumeration | **FAIL vs профиль** | Хардкод-списки (7 моделей) вместо enumeration из registry; профиль требует «probe enumeration из registry, минимум 10 baseline IDs». В registry сейчас 9 (test_registry.py:9-20); после добавления `deepseek-v4-pro` будет 10. Хардкод гарантированно расходится при добавлении модели (как и произошло с 3.7-flash/3.8-max). |
| `--strict` режим | **FAIL: отсутствует** | Только `--provider/--alibaba-key/--gemini-project` (L608-629). `main()` возвращает 0 всегда (L659-690, docstring «это отчёт, а не тест»); nonzero только KeyboardInterrupt→130 (L696-697). Для gating нужен `--strict` → exit 1 при любом `fail` (и опционально при `skipped` без ключей — отдельный флаг). |
| Exit codes | PARTIAL | 0 всегда / 130 на Ctrl+C. Нет различения «есть fail». Задокументировано, но недостаточно для CI/gate. |
| Влияние на runtime cache/UI | OK (отсутствует by design) + GAP | Probe пишет только stdout + JSON в `.agents/reports/probe_*.json` (L587-594); в БД/registry/UI не пишет, runtime-кеши не трогает. `provider_health`-кеша, упомянутого в ALIBABA.md:65, в коде НЕТ (grep по `app/` — 0). `probe_required` фильтруется в UI статически из capabilities (me.py:58); снятие флага — только правкой кода. Сейчас `probe_required` пуст у всех моделей — probe-результаты 2026-09-18 уже зашиты комментариями. |
| Секреты в логах/JSON | OK + замечание | stdout/JSON: только `mask_secret` + source (L488-491, 675-677); ключи в JSON не попадают. Детали ошибок обрезаны (160 в таблице, 500 в провайдере). httpx-логи заглушены до WARNING (L663). Замечание: `--alibaba-key` через argv виден в `ps`/history shell — принять как известный риск ручного запуска либо читать из stdin/file. |
| Поведение при ошибках | OK | `_run_check` изолирует сбои (L163-177); fail `text_stream` → уровни «не оценены», модель не удаляется (L524-530). Соответствует инварианту «не стирать evidence из-за timeout/429/5xx/no-key». |

**Fix-план A27** (файл в моём scope):
1. Enumeration из registry: `ModelRegistry().list_all()` (исключить `internal_only` из полного прогона либо прогонять с пометкой — сейчас gemini-3.5-flash-lite прогоняется с notes; сохранить поведение). Убрать хардкод `_GEMINI_MODELS/_ALIBABA_MODELS` (или держать их только как opt-in `--model` фильтр).
2. Добавить `--strict` (exit 1 если любой check fail) и зафиксировать exit codes в docstring: 0 ok, 1 fail (strict), 2 no keys/config, 130 interrupt.
3. После добавления `deepseek-v4-pro` моделей станет 10 → требование «минимум 10 baseline IDs» выполнится автоматически через enumeration.
4. (Опционально, lead) Запись результатов в БД-кеш для снятия `probe_required` — отдельная задача, сейчас механизма нет.

---

## A39. Tool loop (app/services/generation.py — чужой scope, предложения lead)

| Подпункт | Verdict | Evidence |
|---|---|---|
| Текст ассистента между раундами | **FAIL** | `_assistant_tool_message` (generation.py:529-544) кладёт в историю ТОЛЬКО `tool_call` parts; `outcome.text` раунда в `messages` не попадает (хотя в финальный ответ пользователю аккумулируется через `text_parts`, L456/497). Контракт §4: «Assistant turn в истории: текст раунда + tool_call parts». Последствие: модель во 2+ раунде не видит собственный текст 1-го раунда (деградация многошаговых ответов; для qwen3.8 с `preserve_thinking` тем более важна целостность хода). Fix: `_assistant_tool_message(outcome)` — добавить `{"type":"text","text": outcome.text}` первым part при непустом тексте. `alibaba.py:_map_assistant_message` (L96-110) текст+tool_calls уже умеет — изменений на стороне провайдера не требуется. |
| Iterations limit | OK (с оговоркой) | `max_tool_iterations` default 8 (config.py:53), проверка в `_should_run_tools` (generation.py:524-526). Оговорка: читается из env-settings, а не из DB system_settings на каждый запрос — ключ `max_tool_iterations` в admin_system.py:19/45/59 пишется в БД, но generation.py его не читает (A13, владелец R-db-api; cross-ref). |
| Max calls/round (4) | **FAIL: отсутствует** | Все `outcome.tool_calls` исполняются подряд (generation.py:473-491), лимита на число вызовов за раунд нет. Контракт §4 требует max calls/round = 4. |
| Общий deadline (240s) | **FAIL: отсутствует** | Дедлайна на весь tool loop нет; только per-tool `timeout` (registry.py:30, default 20s) внутри `ToolRunner._invoke` (runner.py:119). Контракт §4: общий deadline, настраиваемый, default 240s. |
| Cancel перед каждым вызовом | **FAIL** | `cancellation.is_set()` проверяется только ПОСЛЕ исполнения всех calls раунда (generation.py:492-494) и в `_consume` между событиями. Внутри `for call in outcome.tool_calls` проверки нет. Контракт §4/§8: cancel ПЕРЕД каждым вызовом (A39/A11). |
| Result budget | PARTIAL | Per-result обрезка есть: `max_result_size` default 4000 (runner.py:141-143); аудит: args 4000 / result 500 (runner.py:25-26). Общего бюджета на раунд/цикл нет — контракт явно не требует, но при max calls/round=4 + 4000/result худший случай ~16KB/раунд — приемлемо, отдельный бюджет не обязателен. |
| Runner robustness | OK | `execute()` не бросает исключений (кроме cancel), unknown/disabled/denied/invalid_args/timeout покрыты; аудит в `tool_calls` с гашением ошибок БД. |

**Fix-план A39 (владелец generation.py/config.py):** текст раунда в assistant message; `max_tool_calls_per_round=4` (config); `tool_loop_deadline_s=240` (config) с проверкой перед каждой итерацией и каждым вызовом; `if cancellation.is_set(): break` внутри цикла по `outcome.tool_calls` до `tool_runner.execute`.

---

## A30. Lifecycle клиентов (app/services/llm_factory.py)

| Подпункт | Verdict | Evidence |
|---|---|---|
| Кеш по ключу | OK (с замечанием) | `alibaba_providers: dict[str, AlibabaProvider]`, ключ — сам api_key (llm_factory.py:37, 54-57). Переиспользование httpx-клиента работает; создание ленивое, per-request. Замечание: dict растёт unbounded при сменах ключа (старые записи не вытесняются). |
| Закрытие при shutdown | **FAIL** | Ни `alibaba_providers`, ни `gemini_provider` (llm_factory.py:35 — владеет своим клиентом) никогда не закрываются: `main()` finally закрывает только `bot.session` и `engine` (main.py:119-121). `AlibabaProvider.aclose` существует (alibaba.py:312-315), но не вызывается нигде, кроме теста. Нарушение §10 «HTTP-клиенты — lifecycle-owned, закрыты при shutdown» (утечка сокетов/предупреждения при остановке). |
| Закрытие при смене ключа | **FAIL** | Смена ключа в `provider_credentials` → новый провайдер добавляется в кеш, старый живёт вечно с открытым клиентом. §10: «смена конфига закрывает cached client» — не реализовано (нет ни invalidation, ни подписки на изменение credentials). |
| Disabled credential = абсолютный запрет (A14 cross-ref) | **FAIL** (чужой файл) | `get_provider_api_key` (credentials.py:20-23): запись существует, но `enabled=false` → тихий fallback на env. §10: «env fallback только когда записи нет вовсе». Т.е. админ не может реально выключить alibaba при заданном `ALIBABA_API_KEY`. Владелец — R-db-api (A14); фабрика тогда должна получать сигнал «disabled» и не обслуживать alibaba-модели. |
| trust_env / endpoint | OK | Провайдер создаётся с `base_url=settings.alibaba_base_url` (default dashscope compatible-mode), `trust_env=False` (alibaba.py:305-310; тест `test_default_client_trust_env_false`). |

**Fix-план A30 (мой scope — предложение по llm_factory.py, владелец файла подтверждает):**
1. `build_llm_stream` возвращает (или регистрирует) `aclose()`-handle: закрыть все cached `AlibabaProvider` + `gemini_provider`; вызвать из `main()` finally (main.py — владелец lead).
2. Eviction при смене ключа: ключ кеша — не сам api_key, а fingerprint (см. A29 HMAC) + при miss старые записи для provider закрывать; минимум — `if len(alibaba_providers) > N: close+clear stale`. Правильный вариант: credentials-сервис возвращает (key, version/fingerprint), смена версии → aclose старого.
3. A14: `get_provider_api_key` → различать «нет записи» (env fallback) и «disabled» (None/отказ); вызывающая сторона маппит в AuthError.

---

## N04-RESEARCH. `deepseek-v4-pro` — актуальная документация (webfetch 2026-09-24)

### Источники и статус

| URL | Статус | Last Updated |
|---|---|---|
| `https://www.alibabacloud.com/help/en/model-studio/deepseek-v4` | **404** (Aliyun error page) | — |
| `https://www.alibabacloud.com/help/en/model-studio/deepseek-v4-pro` | **OK, существует** | **Sep 20, 2026** |
| `https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions` (chat-ref) | OK | **Sep 22, 2026** |
| `https://www.alibabacloud.com/help/en/model-studio/deep-thinking` | OK | **Sep 22, 2026** |
| `https://www.alibabacloud.com/help/en/model-studio/deepseek-v4-1-flash` (контрольная) | OK | Sep 21, 2026 |

### Факты по `deepseek-v4-pro` (стабильный ID; страница модели, 2026-09-20)

- **Существование:** страница есть; модель описана как flagship MoE 1.6T параметров / 49B активных, native 1M context. Провайдер инференса — Alibaba Cloud Model Studio. Снапшот: `deepseek-v4-pro-0813` (та же страница, секция Snapshot Versions).
- **Context/output ceilings:** Max Input 1 000 000; Context Window 1 000 000; **Max Output 393 216 (384K)**. У стабильного ID на странице НЕТ отдельных строк «(Thinking Mode)»/«Max Chain-of-Thought» — они есть только у снапшота `-0813` (Thinking: input 1M / output 393 216 / CoT 393 216). Интерпретация: ceilings одинаковые, но для стабильного ID это документально не размечено — зафиксировать как легкий doc-gap, не противоречие.
- **Vision: НЕТ.** Input Modality = **Text** во ВСЕХ региональных таблицах (Beijing, Singapore, Frankfurt, Virginia Global+US, Tokyo). Output = Text. → В registry `input_modalities=frozenset({"text"})` (как у glm-5.3), НЕ копировать image из deepseek-v4.1-flash!
- **Function Calling:** Supported (все регионы). Structured Outputs: Supported (кроме scope US в Virginia — там Unsupported; у нас Beijing legacy-endpoint — Supported). Web Search: Supported (Beijing/Singapore/Tokyo; Frankfurt/Virginia-Global — Unsupported). Prefix Completion: Unsupported. Context Caching: Supported. Batch: Unsupported. Fine-tuning: Unsupported.
- **Thinking-параметры (chat-ref 2026-09-22, раздел `reasoning_effort`):**
  - `deepseek-v4-pro` (стабильный) входит в группу **«DeepSeek-V4 and GLM series», default `high`**: native значения только **`high` и `max`**; **`low` и `medium` маппятся в `high`, `xhigh` — в `max`**. Т.е. **LOW для стабильного ID = alias на HIGH** (нативного low нет). Подтверждён список применимости: «glm-5.2, glm-5.1, glm-5, deepseek-v4-pro, deepseek-v4-flash (excluding deepseek-v4-flash-0731)».
  - Снапшот `deepseek-v4-pro-0813` (вместе с `deepseek-v4-flash-0731`): native `low`/`high`/`max`, default `high`; `medium`→high, `xhigh`→high. **Внутреннее противоречие страницы сохраняется**: заголовок «Default value: high», а в списке значений «`max` (default)» — то же, что зафиксировано в `.agents/reports/alibaba.md` §8.3.
  - `enable_thinking`: применим (hybrid), см. список «DeepSeek-V4.1-Flash, DeepSeek-V4-Pro/V4-Flash series…»; **«The DeepSeek-V4 series enables thinking by default»**.
  - **Дефолт thinking — ПРОТИВОРЕЧИЕ СНЯТО:** deep-thinking (2026-09-22) теперь: «DeepSeek — Hybrid thinking mode, **thinking mode enabled by default**: deepseek-v4.1-flash, **deepseek-v4-pro**, deepseek-v4-flash». Ранее (research 2026-09-18, `.agents/reports/alibaba.md` §8.1) та же страница говорила «disabled by default» — **документация обновлена и теперь обе страницы согласованы: thinking ON by default**. Старое противоречие №1 закрыть.
  - `thinking_budget`: **НЕ применим к DeepSeek** (список применимости: qwen3.8/3.7/3.6/3.5/Qwen3-VL/Qwen3/GLM/Kimi except kimi-k3). Не слать.
  - `preserve_thinking`: `deepseek-v4-pro` **отсутствует** в списке поддержки (только qwen3.x + kimi-k2.6/k2.7-code). Не слать.
  - `max_tokens` semantics для `deepseek-v4-pro`/`-0813`: лимит на **сумму ответ+CoT** (как у v4.1-flash и glm-5.3). `max_completion_tokens` поддержан (DeepSeek-список включает `deepseek-v4-pro`). Проект шлёт только `max_completion_tokens` (alibaba.py:159) — совместимо.
  - `reasoning_effort` «неподдерживаемые значения → ошибка» (chat-ref) — т.е. для стабильного ID слать только high/max (или mapped low/medium/xhigh, которые не ошибка, а alias); `none`/`minimal` для deepseek-v4-pro не задокументированы.
- **Цены (Beijing, для справки):** стабильный ID: input $1.65 / output $3.301 за 1M (flat), implicit cache input $0.138. Снапшот -0813: idle/busy $0.636/$1.272 in, $1.908/$3.816 out. Rate limits Beijing: 15000 RPM / 1.2M TPM.

### Расхождения с текущим research-доком (`.agents/reports/alibaba.md`, 2026-09-18)

1. §8.1 (thinking default DeepSeek-V4) — **устарело**: deep-thinking 2026-09-22 теперь «enabled by default» для deepseek-v4-pro/v4-flash/v4.1-flash; согласовано с chat-ref. Обновить ALIBABA.md и alibaba.md при следующем доке-pass.
2. §8.10 (нет thinking-строк у deepseek-v4.1-flash) — **устарело**: страница v4.1-flash обновлена 2026-09-21, строки Thinking Mode (1M/393216/CoT 393216) добавлены.
3. §4 `reasoning_effort` для deepseek-v4-pro — **подтверждено и уточнено**: default `high`, native high/max, low→high (alias), medium→high, xhigh→max. Снапшот -0813: native low/high/max. Внутреннее противоречие «max (default)» в списке -0813 — актуально.

### Последствия для добавления модели (вход для dev-pass, НЕ менять слепо)

- `ModelDefinition`: `provider="alibaba"`, `model_id="deepseek-v4-pro"`, `input_modalities={"text"}` (vision НЕТ), `max_context=1_000_000`, `max_output=393_216`, `function_calling=True`.
- Thinking: default провайдера = thinking ON, effort default `high`. UI-уровни по проектной шкале: OFF (`enable_thinking:false` — hybrid, документально применим; acceptance — probe), HIGH (native default), MAX (native max). **LOW не выдумывать**: у стабильного ID low — alias high (инвариант профиля «Pro LOW не изобретать по аналогии с Flash»). Предложение: `thinking_modes=(OFF, HIGH, MAX)`, `default_thinking=None` (= provider default high); решение по UI-набору — за lead.
- `_THINKING_TABLE` (alibaba.py): добавить запись `deepseek-v4-pro`: `off → {"enable_thinking": False}`, `high → {"reasoning_effort": "high"}`, `max → {"reasoning_effort": "max"}`; БЕЗ `preserve_thinking`/`clear_thinking`/`thinking_budget`.
- Probe (smoke_providers): после перехода на registry-enumeration модель попадёт в прогон автоматически; image-check будет skipped по `input_modalities` (как glm-5.3).
- Acceptance не подтверждено live (probe не запускался — read-only задача, live-бюджет lead): `enable_thinking:false` acceptance и реальный alias low→high — на runtime probe.

---

## Сводка вердиктов

| Item | Verdict | Критичность |
|---|---|---|
| A23 usage после Done / break в _consume | FAIL | **Критично** — 100% потеря usage-ledger для alibaba |
| A23 тихий EOF = успех | FAIL | Высокая |
| A23 pending tool calls при EOF | FAIL (часть EOF-фикса) | Средняя |
| A23 malformed lines / error frame / empty choices | OK/PARTIAL | — |
| A27 пропуски моделей (3.7-flash, qwen3.8-max), хардкод вместо registry | FAIL | Высокая |
| A27 нет --strict / exit codes | FAIL | Средняя |
| A27 секреты, изоляция, БД-не-трогание | OK | — |
| A39 текст ассистента в истории | FAIL | Высокая |
| A39 calls/round, deadline, cancel-per-call | FAIL | Средняя |
| A39 iterations, per-result budget | OK/PARTIAL | — |
| A30 shutdown-close, смена ключа | FAIL | Средняя |
| A30/A14 disabled → env fallback | FAIL (чужой scope) | Высокая (безопасность) |
| N04 deepseek-v4-pro | RESEARCH DONE | страница существует (2026-09-20); text-only; 1M/393216; effort high/max (low=alias high); thinking ON default (противоречие снято); FC yes |

**Порядок fixes (предложение lead):** 1) A23 parser reorder + EOF-ошибка (alibaba.py, мой scope) + `_consume` (generation.py); 2) A39 текст в истории + лимиты (generation.py/config.py); 3) A27 registry-enumeration + --strict (smoke_providers.py); 4) A30 lifecycle (llm_factory.py + main.py + credentials.py с владельцем A14); 5) N04-add model (capabilities.py/alibaba.py/test_registry.py/test_alibaba_provider.py + docs) после подтверждения UI-набора thinking.
