# R-ALIBABA: Alibaba Cloud Model Studio (OpenAI-compatible) — исследование

**Дата проверки:** 2026-09-18
**Субагент:** R-ALIBABA
**Область:** только исследование документации. Ничего не выдумано; всё, что не найдено, помечено «не найдено» или «на probe».

> Жёсткое требование проекта: единственный endpoint `https://dashscope.aliyuncs.com/compatible-mode/v1`. Документация Alibaba с сентября 2026 активно продвигает workspace-specific домены (`https://{WorkspaceId}.{region}.maas.aliyuncs.com/compatible-mode/v1`), но прямо пишет: «The legacy `https://dashscope.aliyuncs.com` domain remains available» / «The existing domain remains fully functional» — т.е. требование проекта выполнимо, это legacy-домен региона China (Beijing). Важно: API-ключ привязан к региону; ключ региона должен совпадать с регионом base_url, иначе HTTP 401 `invalid_api_key` (см. раздел ошибок).

---

## 1. Источники и статус доступности

| # | URL | Статус | Дата обновления страницы |
|---|-----|--------|--------------------------|
| 1 | `https://www.alibabacloud.com/help/en/model-studio/developer-reference/compatibility-of-openai-with-dashscope` («OpenAI compatible — Chat», tutorial) | OK | 2026-09-11 |
| 2 | `https://www.alibabacloud.com/help/en/model-studio/getting-started/models` (→ «Recommended models») | OK | 2026-09-14 |
| 3 | `https://www.alibabacloud.com/help/en/model-studio/text-generation-model` | OK | 2026-09-11 |
| 4 | `https://www.alibabacloud.com/help/en/model-studio/kimi` | **404** (страница ошибки Aliyun) | — |
| 5 | `https://www.alibabacloud.com/help/en/model-studio/deep-thinking` | OK | 2026-09-15 |
| 6 | `https://www.alibabacloud.com/help/en/model-studio/kimi-k3` | OK | 2026-09-11 |
| 7 | `https://www.alibabacloud.com/help/en/model-studio/qwen3-8-max` | OK | 2026-09-11 |
| 8 | `https://www.alibabacloud.com/help/en/model-studio/qwen3-8-flash` | OK | 2026-09-11 |
| 9 | `https://www.alibabacloud.com/help/en/model-studio/glm-5-3` | OK | 2026-09-15 |
| 10 | `https://www.alibabacloud.com/help/en/model-studio/deepseek-v4-1-flash` | OK | 2026-09-14 |
| 11 | `https://www.alibabacloud.com/help/en/model-studio/compatibility-with-openai-responses-api` | OK | 2026-09-16 |
| 12 | `https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-responses` (Responses API reference) | OK | 2026-09-17 |
| 13 | `https://www.alibabacloud.com/help/en/model-studio/error-code` | OK | 2026-09-17 |
| 14 | `https://www.alibabacloud.com/help/en/model-studio/tool-calls` | OK (только список ссылок) | 2026-09-02 |
| 15 | `https://www.alibabacloud.com/help/en/model-studio/qwen-function-calling` | OK | 2026-09-17 |
| 16 | `https://www.alibabacloud.com/help/en/model-studio/qwen-api-via-openai-chat-completions` (Chat Completions reference) | OK | 2026-09-17 |
| 17 | `https://platform.moonshot.ai/docs/...` | Редирект на `platform.kimi.ai` | — |
| 18 | `https://platform.kimi.ai/docs/guide/kimi-k3-quickstart` | OK | n/a |
| 19 | `https://platform.kimi.ai/docs/guide/use-dynamic-tool-loading` | OK | n/a |
| 20 | `https://platform.kimi.ai/docs/guide/use-reasoning-effort` | OK | n/a |

Все 5 моделей из ТЗ присутствуют в Model Plaza (источник #2) и имеют персональные страницы.

---

## 2. Таблица по 5 моделям

| Модель | Страница | model_id | Контекст / Max input | Max output | Max CoT | Vision | Function Calling | Thinking |
|---|---|---|---|---|---|---|---|---|
| Qwen3.8-Max | `/qwen3-8-max` OK | `qwen3.8-max`, snapshot `qwen3.8-max-0902` (= `qwen3.8-max-2026-09-02`) | 1 000 000 / 991 808 (в thinking: 983 616) | 131 072 | 262 144 | **Image + Text + Video** (вход) | Supported | Гибрид, **thinking включён по умолчанию** (deep-thinking); `enable_thinking`, `reasoning_effort`, `thinking_budget`, `preserve_thinking` (default true) |
| Qwen3.8-Flash | `/qwen3-8-flash` OK | `qwen3.8-flash` | 1 000 000 / 991 808 (в thinking: 983 616) | 131 072 | 262 144 | **Image + Text + Video** (вход) | Supported | Гибрид, **включён по умолчанию**; параметры как у 3.8-max |
| DeepSeek-V4.1-Flash | `/deepseek-v4-1-flash` OK | `deepseek-v4.1-flash` | 1 000 000 / 1 000 000 | **393 216 (384K)** | **нет строки CoT на странице модели** | **Text + Image** | Supported | Гибрид (по chat-ref: DeepSeek-V4 «enables thinking by default» — противоречие, см. §8); `reasoning_effort` low/high/max |
| GLM-5.3 | `/glm-5-3` OK | `glm-5.3`; также существует `ZHIPU/GLM-5.3` (поставщик Zhipu) | 1 048 576 / 1 048 576 | 131 072 | 131 072 | **НЕТ (только Text)** | Supported (для glm-5.3 `tool_stream` по умолчанию true) | **Thinking-only**: `enable_thinking=false` → ошибка запроса; `reasoning_effort` low/high/max (default `max`, рекомендуют `max`); **`thinking_budget` игнорируется**; `clear_thinking` (default true для 5.3) |
| Kimi-K3 | `/kimi-k3` OK | `kimi-k3` (поставщик Alibaba Cloud Model Studio); отдельно упоминается `kimi/kimi-k3` (поставщик Moonshot AI) | 1 048 576 / 1 048 576 | **1 048 576** (так на странице модели; подозрительно = контексту, см. противоречия; у Moonshot: `max_completion_tokens` default 131 072, макс 1 048 576) | 1 048 576 | **Text + Image** (страница MS); у Moonshot: text/image/video | Supported (страница модели); но страница Function Calling перечисляет только kimi-k2.x — **kimi-k3 там не указан** (лаг документации) | `reasoning_effort` low/high/max (default `max`) для `kimi-k3`; для `kimi/kimi-k3` (Moonshot) — **только `max`**; MS chat-ref: kimi-k3 допускает `enable_thinking=false`; Moonshot: «K3 always thinks» (противоречие); **`thinking_budget` НЕ поддерживается**; Dynamic Tool Loading — только в доках Moonshot |

Цены (Singapore, для справки): qwen3.8-max $2/$6 за 1M in/out; qwen3.8-flash $0.15/$0.47; deepseek-v4.1-flash $0.15–0.3/$0.6–1.2 (idle/busy); glm-5.3 $1.4/$4.4; kimi-k3 $3/$15.

**Responses API** (`POST /compatible-mode/v1/responses`): все 5 моделей в списке поддерживаемых (источники #11, #12, обновлены 16–17.09.2026): `qwen3.8-max`, `qwen3.8-max-0902`, `qwen3.8-flash`, `deepseek-v4.1-flash`, `glm-5.3`, `kimi-k3` и др. Там же built-in tools (web_search, code_interpreter, web_extractor, mcp, file_search), `previous_response_id` (7 дней), session cache через заголовок `x-dashscope-session-cache: enable`.

---

## 3. OpenAI-compatible endpoint

- **Base URL (проектный):** `https://dashscope.aliyuncs.com/compatible-mode/v1` — legacy-домен China (Beijing), заявлен как работающий. Актуальная рекомендация Alibaba: workspace-домены `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1` (Beijing), `...ap-southeast-1...` (Singapore), US: `https://dashscope-us.aliyuncs.com/compatible-mode/v1` (без workspace), Frankfurt: `...eu-central-1...`, Tokyo: `...ap-northeast-1...`, HK: `...cn-hongkong...`.
- **Auth:** `Authorization: Bearer sk-...` (DASHSCOPE_API_KEY). Ключ региональный: ключ одного региона + endpoint другого региона = HTTP 401 `invalid_api_key` («Incorrect API key provided»). Плановые ключи `sk-sp-` требуют своих base_url.
- **Отличия от чистого OpenAI:**
  - Кастомные параметры верхнего уровня (в Python SDK — через `extra_body`; в Node.js/HTTP — top-level): `enable_thinking`, `thinking_budget`, `reasoning_effort`, `preserve_thinking`, `clear_thinking`, `tool_stream`, `enable_search`, `search_options`, `top_k`, `repetition_penalty`, `vl_high_resolution_images`, `enable_code_interpreter`, `ocr_options`.
  - В ответе/чанках есть нестандартное поле `reasoning_content`.
  - `stream_options.include_usage: true` — usage приходит **только в последнем чанке**, у которого `choices: []`.
  - `n`: 1–4, только часть моделей (по старой странице — только qwen-plus; по chat-ref — Qwen3 non-thinking); с `tools` — n фиксировано 1.
  - `tool_choice`: Qwen не поддерживает `"required"`; thinking-модели не поддерживают принудительный выбор конкретного инструмента.
  - Таймаут нестримингового вызова ≥300 с; при превышении сервис обрывает запрос и **возвращает уже сгенерированный контент** (не ошибку) — проекту стримить всегда.
  - Qwen-Audio не поддерживает OpenAI-протокол (нам не нужен).
  - Responses API: параметр `background` не поддержан, только синхронно/стрим; видео/аудио input не поддержаны (только `input_text`/`input_image`/`input_file`); «неперечисленные параметры игнорируются».
  - Ошибка в OpenAI-формате: `{"error": {"message": ..., "type": "invalid_request_error", "param": null, "code": "invalid_api_key"}}`.

---

## 4. Thinking-параметры: сводная таблица

Источники: chat-completions reference (#16, 2026-09-17), deep-thinking (#5, 2026-09-15), Responses reference (#12, 2026-09-17).

| Параметр | Тип / где передавать | Модели и значения |
|---|---|---|
| `enable_thinking` | bool, top-level (extra_body в Py) | Гибридные модели Qwen3.5–3.8, DeepSeek-V3.1/V3.2/V4, Kimi-K2.x, GLM. Qwen3.8: default **true**. DeepSeek-V4: default — **противоречие** (см. §8). glm-5.3: допустимо **только true**, false → 400 («The value of the enable_thinking parameter is restricted to True» / request fails). kimi-k3 (MS): false допустимо (chat-ref), но Moonshot пишет «нельзя отключить». Устаревает в пользу `reasoning_effort` («enable_thinking will be deprecated», Responses API). |
| `reasoning_effort` | string, top-level (extra_body в Py) | **Вселенная из 7 уровней: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`** с per-model маппингом. Qwen3.8: default `xhigh`; native `low`/`medium`/`xhigh`; `max`→xhigh, `high`→xhigh, `minimal`→low, `none`→enable_thinking=false. deepseek-v4.1-flash: default `high`; native `low`/`high`/`max`; `minimal`→low, `medium`/`xhigh`→high, `ultra`→max (но в Responses API doc — «passing `ultra` returns an error», противоречие). glm-5.3 / ZHIPU/GLM-5.3 / kimi-k3 (Alibaba): default `max`; native `low`/`high`/`max`. kimi/kimi-k3 (Moonshot): **только `max`**. glm-5.2/5.1/5, deepseek-v4-pro/v4-flash: default `high`; native `high`/`max` (low/medium→high, xhigh→max). Неподдерживаемые значения → ошибка. |
| `thinking_budget` | int, top-level (extra_body) | Применимо к Qwen3.x/Qwen3-VL/GLM/Kimi, **кроме kimi-k3** (явно: «except kimi-k3, which does not support this parameter»). **glm-5.3 его игнорирует** (раздел max_tokens chat-ref). Default = Max CoT модели. Должен быть >0 и ≤ Max CoT, иначе 400. Для Qwen3.8 **взаимоисключаем с `reasoning_effort`** (оба в одном запросе → ошибка); автоконвертация: low↔4096, medium↔16384, xhigh↔262144; дефолты одновременно: budget 131072 + effort xhigh. |
| `preserve_thinking` | bool, top-level (extra_body), default false | Возвращать ли `reasoning_content` истории во вход. Default **true** для qwen3.8-max/qwen3.8-flash (и тогда историю reasoning надо возвращать **в поле `reasoning_content`, НЕ конкатенируя в `content`**, иначе деградация). Списки поддержки на #5 и #16 различаются (см. §8): kimi-k3 фигурирует только как `kimi/kimi-k3` (Moonshot) на странице deep-thinking. |
| `clear_thinking` | bool, top-level (extra_body), default false | **Только GLM**: glm-5.3 (default **true**), glm-5.2, glm-5.1, glm-5, glm-4.7. true = не подавать `reasoning_content` прошлых ходов во вход (экономия контекста); false = подавать, и тогда reasoning_content обязан быть полным/неизменным/в исходном порядке. Баг документации: пример вызова написан как `extra_body={"skill": [...]}` (copy-paste). |
| `reasoning.effort` (Responses API) | object `reasoning: {"effort": ...}` | Значения `none`/`minimal`/`low`/`medium`/`high`/`xhigh`/`max`; таблица маппингов как выше (Qwen3.8: none/low/medium/xhigh, default xhigh; deepseek-v4.1-flash: none/low/high/max, default high; glm-5.3: low/high/max, default max — **`none` недоступен**, что согласуется с thinking-only). `reasoning.effort` приоритетнее `enable_thinking`; `thinking_budget` в Responses API недоступен. **kimi-k3 в этой таблице отсутствует** (хотя в списке поддерживаемых Responses-моделей есть). |

Прочее:
- Промпт-суффиксы `/no_think` и `/think` — только для Qwen3 open-source hybrid + qwen-plus-2025-04-28 (не для наших 5).
- В usage стрима/ответа есть `completion_tokens_details.reasoning_tokens` и `prompt_tokens_details.cached_tokens`.
- `max_tokens` объявлен «to be deprecated» в пользу `max_completion_tokens`. Семантика max_tokens: для deepseek-v4.x и glm-5.3 — сумма (ответ+CoT); для glm-5.2 — сумма, если не передан thinking_budget; для прочих — только ответ. `max_completion_tokens` = всегда ответ+CoT (поддержан: Qwen3.5+/3.7-Max+, Kimi k2.5+, GLM-5+, DeepSeek v3+ и новее).

---

## 5. Kimi K3: подтверждено / не подтверждено

**Подтверждено на Model Studio (страница `/kimi-k3`, 2026-09-11):**
- model_id `kimi-k3`; провайдер инференса — Alibaba Cloud Model Studio; 2.8T параметров, KDA + Attention Residuals.
- Вход: **Text + Image**; выход: Text. Function Calling: Supported. Structured Outputs: Supported. Prefix Completion: Supported. Context Caching: Supported. Web Search: **Unsupported**. Batch: Unsupported.
- Контекст 1 048 576 во всех полях (вкл. Max Output и Max CoT — см. противоречие в §8).
- Доступен в регионах Beijing/Singapore/Frankfurt/Virginia/Tokyo/Hong Kong.
- В списке моделей Responses API присутствует.
- Chat-ref (#16): `reasoning_effort` low/high/max (default max); допускает `enable_thinking=false`; `thinking_budget` не поддерживается.

**Подтверждено только в доках Moonshot (platform.kimi.ai) — поведение на Model Studio НЕ задокументировано, на probe:**
- **Dynamic Tool Loading**: НЕТ отдельного `$ToolSearch` API. Механика: в `messages` вставляется сообщение `{"role": "system", "tools": [...полные определения...]}` **без поля `content`** (с `content` → 400 «cannot be used with content»). Инструменты видны модели начиная с позиции этого сообщения; сосуществуют с top-level `tools`; сервер их не хранит — надо сохранять в истории последующих запросов. Поддерживается **только kimi-k3**; на других моделях (kimi-k2.6) ошибка `tokenization failed`. Паттерн «tool search» — DIY: свой инструмент `search_tools` + динамическая подгрузка найденных определений; append-only, чтобы не ломать prefix cache (порог кэша — prompt > 256 токенов).
- `reasoning_effort` top-level: low/high/max (default max); «K3 always thinks», отключить нельзя (противоречит chat-ref MS).
- Vision: `content` обязан быть массивом объектов; **публичные URL изображений не поддерживаются** — только base64 data URI или `ms://<file-id>` (через Files API); видео — через загрузку файла.
- Фиксированные `temperature=1.0`, `top_p=0.95`, `n=1`, `presence_penalty=0`, `frequency_penalty=0`.
- Multi-turn/tool-calls: возвращать assistant message целиком as-is (включая `reasoning_content` и `tool_calls`).
- Responses API у Moonshot существует, но к нашему endpoint отношения не имеет.

**Не подтверждено нигде:**
- Dynamic Tool Loading на Model Studio endpoint (на страницах tool-calls/function-calling/deep-thinking/chat-ref не упоминается).
- `preserve_thinking` для `kimi-k3` (Alibaba-deployed): на #16 в списке нет; на #5 есть только `kimi/kimi-k3` (Moonshot).
- `reasoning.effort` для kimi-k3 в Responses API (модель в списке есть, в таблице effort — нет).

---

## 6. Формат streaming-чанков (Chat Completions, OpenAI-совместимый)

- SSE: строки `data: {json}`, финал `data: [DONE]`.
- Чанк: `{"id": "chatcmpl-...", "object": "chat.completion.chunk", "created": ..., "model": ..., "choices": [{"index": 0, "delta": {...}, "finish_reason": null, "logprobs": null}], "usage": null}`.
- Первый чанк: `delta.role="assistant"`, `delta.content=""`; при thinking — `delta.reasoning_content` приходит **до** `content`; поля взаимно чередуются (сначала весь reasoning, потом ответ). В последнем «текстовом» чанке `finish_reason` ∈ `stop | length | tool_calls`.
- При `stream_options.include_usage: true`: финальный чанк имеет **`choices: []`** и заполненный `usage` (в т.ч. `completion_tokens_details.reasoning_tokens`, `prompt_tokens_details.cached_tokens`). Парсер обязан обрабатывать пустой `choices` (в офиц. примерах: `if not chunk.choices: ...`).
- `tool_calls` в delta: массив; `index` — индекс вызова; `id` и `function.name` приходят **только в первом чанке** данного вызова; `function.arguments` — инкрементальные строки, конкатенируются. `finish_reason="tool_calls"` в конце. Для GLM параметры инструментов по умолчанию приходят целиком; `tool_stream: true` (для glm-5.3 — default true) включает потоковую выдачу «сложных» (array/object) аргументов.
- `reasoning_content` в нестримовом ответе: поле в `message` рядом с `content` (у thinking-моделей).

Responses API (для справки): события `response.created` → `response.in_progress` → `response.output_item.added` → `response.content_part.added` → `response.output_text.delta` × N → `...done` → `response.output_item.done` → `response.completed` (в последнем — полный `response` с `usage`). Thinking приходит как item `type: "reasoning"` с `summary[].text`.

---

## 7. Ошибки и retryable-классификация

Формат: OpenAI-style `{"error": {message, type, param, code}}`; часть кодов — DashScope-style (`InvalidParameter`, `Throttling.RateQuota` …).

| HTTP / код | Пример message | Смысл | Retryable? |
|---|---|---|---|
| 400 `InvalidParameter` | `parameter.enable_thinking must be set to false for non-streaming calls` | thinking-модель без стрима | Нет (менять запрос) |
| 400 `InvalidParameter` | `The thinking_budget parameter must be a positive integer and not greater than xxx` | budget вне диапазона | Нет |
| 400 `InvalidParameter.NotSupportEnableThinking` | `The model xxx does not support enable_thinking` | параметр не поддержан моделью | Нет (probe/фолбэк) |
| 400 | `The value of the enable_thinking parameter is restricted to True` | thinking-only (glm-5.3 и др.) | Нет |
| 400 | `Json mode response is not supported when enable_thinking is true` | structured output + thinking | Нет |
| 400 | `The tool call is not supported` | модель без FC | Нет |
| 400 | `Range of input length should be [1, xxx]` / `Range of max_tokens...` | превышение контекста/лимитов | Нет |
| 400 `Arrearage` | `Access denied... account is in good standing` / `isv.OUT_OF_SERVICE` | нет денег | Нет (алерт пользователю) |
| 400 `DataInspectionFailed` | `Input or output data may contain inappropriate content` | модерация (Green Net) | Нет (бессмысленно без изменения контента) |
| 401 `InvalidApiKey` / `invalid_api_key` | `Incorrect API key provided` | ключ невалиден **или регион ключа ≠ регион endpoint** | Нет |
| 403 `AccessDenied` / `access_denied` | `Access denied.` | нет доступа к модели / плановые ограничения / deprecated | Нет |
| 404 `ModelNotFound` / `model_not_found` | `The model xxx does not exist...` | нет модели/не активирована/не в этом регионе | Нет |
| 404 `model_not_supported` | `Unsupported model xxx for OpenAI compatibility mode` | модель не по OpenAI-протоколу | Нет |
| **429 `Throttling.RateQuota` / `limit_requests`** | `Requests rate limit exceeded...` | RPM/RPS | **Да (backoff)** |
| **429 `Throttling.BurstRate` / `limit_burst_rate`** | `Request rate increased too quickly...` | всплеск | **Да (сглаживание, exp backoff)** |
| **429 `Throttling.AllocationQuota` / `insufficient_quota`** | `You exceeded your current quota...` | TPM/TPS/квота | **Да (backoff; если постоянно — квота/биллинг)** |
| 429 `CommodityNotPurchased` / `PrepaidBillOverdue` / `PostpaidBillOverdue` | — | биллинг | Нет |
| **500 `InternalError` / `internal_error`**, `InternalError.Algo`, `SystemError`, `ModelServiceFailed`, `RequestTimeOut`, `ResponseTimeout` | `An internal error has occured...`, `inference internal error` | внутренние | **Да (retry, ограниченное число раз)** |
| **500/503 `ServiceUnavailable`** | `The engine is currently overloaded...` / `Too many requests... throttled due to system capacity` | перегруз | **Да** |
| **503 `ModelUnavailable`** | `Model is unavailable, please try again later` | модель недоступна | **Да** |
| — (client) `APIConnectionError` | `Connection error.` | сеть/прокси | Да (сеть) |

Особые случаи: 400 `current user api does not support http call` — модель не поддерживает OpenAI-вызов; 429 при единственном запросе может означать лишний заголовок `X-DashScope-Async: enable` (снять). «All models are temporarily rate-limited» — клиентская агрегация 429.

---

## 8. Противоречия документации (дословно)

1. **DeepSeek-V4 thinking по умолчанию.** deep-thinking (#5): «Hybrid thinking mode, **thinking disabled by default**: deepseek-v4-pro, deepseek-v4-flash, deepseek-v3.2…». Chat-completions ref (#16), параметр enable_thinking: «**The DeepSeek-V4 series enables thinking by default.** You can adjust the inference intensity with the `reasoning_effort` parameter.» → Противоречие; deepseek-v4.1-flash на странице deep-thinking вообще не listed.
2. **`ultra` для deepseek-v4.1-flash.** #16 (reasoning_effort): «`minimal` is mapped to `low`, `medium` and `xhigh` are mapped to `high`, and **`ultra` is mapped to `max`**». #12 (Responses API): «`minimal` maps to `low`; `medium` and `xhigh` map to `high`; **passing `ultra` returns an error**». → `ultra` не слать.
3. **Default для deepseek-v4-flash-0731 / v4-pro-0813.** #16: заголовок «Default value: `high`», а список значений: «`max` (**default**): Maximum-intensity inference». → Внутреннее противоречие на одной странице.
4. **kimi-k3: отключаемость thinking.** #16: «kimi-k3 supports passing `false` to disable thinking». Moonshot (#18/#20): «K3 always has thinking mode enabled… You can't — K3 always thinks.» → Разные провайдеры деплоя; для нашего endpoint верить #16, подтвердить probe.
5. **preserve_thinking и kimi-k3.** #5: список включает «kimi/kimi-k3 (deployed on Moonshot AI)» — и НЕ включает kimi-k3 (Alibaba). #16: kimi-k3 отсутствует в списке preserve_thinking вообще, зато перечислены kimi-k2.6/k2.7-code. → preserve_thinking для нашего kimi-k3 — не подтверждён.
6. **tools + stream.** Старая страница совместимости (#1, 2026-09-11): «The tools parameter cannot be used with stream=True simultaneously» и «Currently supported models: qwen-turbo, qwen-plus, and qwen-max». Новые страницы (#15/#16, 2026-09-17) описывают стриминг tool_calls и `tool_stream`, FC у всех наших моделей. → #1 устарела; верить #15/#16, подтвердить probe.
7. **Function Calling для kimi-k3 и qwen3.8-flash.** Страницы моделей: FC Supported. Страница #15 перечисляет Kimi только k2.x и Qwen-Flash только до 3.7. → Лаг документации; probe.
8. **kimi-k3 Max Output = 1 048 576** на странице модели (равно контексту и Max CoT). У Moonshot: `max_completion_tokens` default 131 072, максимум 1 048 576. → Похоже на «технический максимум»; реальный дефолт уточнить probe (max tokens не передавать или передавать разумное).
9. **`clear_thinking` пример кода.** #16: «Configuration: `extra_body={"skill": [...]}`» — явный copy-paste из другого параметра.
10. **deepseek-v4.1-flash: thinking-лимиты.** На странице модели нет строк «Max Input/Output (Thinking Mode)» и «Max Chain-of-Thought Length» (есть у qwen/glm/kimi). При этом reasoning_effort для него задокументирован. → Лимиты CoT неизвестны; probe/консоль.
11. **glm-5.3 «три уровня reasoning effort»** (описание модели: «three reasoning effort levels») vs таблица: low/high/max — сходится, но `none` отсутствует, что согласуется с thinking-only; при этом в общей вселенной Responses API `none` существует — для glm-5.3 не применимо.

---

## 9. Рекомендации проекту

### 9.1. Маппинг UI-уровней thinking → параметры (Chat Completions)

Предлагаемые UI-уровни: `off | low | medium | high | max`.

| UI | qwen3.8-max / qwen3.8-flash | deepseek-v4.1-flash | glm-5.3 | kimi-k3 |
|---|---|---|---|---|
| off | `enable_thinking: false` (или `reasoning_effort: "none"` — маппится туда же) | `enable_thinking: false` *(probe!)* | **невозможно** (thinking-only; false → 400). Ближайшее: `reasoning_effort: "low"` | `enable_thinking: false` *(probe — противоречие с Moonshot)* |
| low | `reasoning_effort: "low"` (≈budget 4096) | `reasoning_effort: "low"` | `reasoning_effort: "low"` | `reasoning_effort: "low"` |
| medium | `reasoning_effort: "medium"` (≈16384) | `reasoning_effort: "high"` (medium маппится в high) | `reasoning_effort: "high"` (medium→high) | `reasoning_effort: "high"` (medium нет) |
| high | `reasoning_effort: "xhigh"` (≈262144) | `reasoning_effort: "high"` | `reasoning_effort: "high"` | `reasoning_effort: "high"` |
| max | `reasoning_effort: "xhigh"` (дефолт; «max» маппится в xhigh) | `reasoning_effort: "max"` | `reasoning_effort: "max"` (дефолт, рекомендован) | `reasoning_effort: "max"` (дефолт) |

Правила:
- Никогда не слать `reasoning_effort` вместе с `thinking_budget` для Qwen3.8 (ошибка).
- `thinking_budget` не слать kimi-k3 (не поддержан) и glm-5.3 (игнорируется); остальным — опционально как точный override бюджета.
- Значения `minimal`/`ultra` не использовать (маппинги/ошибки разнятся между страницами).
- GLM: помнить про `clear_thinking` (у glm-5.3 default true — история reasoning отбрасывается) и что FC у GLM требует `tool_stream` (у 5.3 default true — ничего не слать).
- Qwen3.8: `preserve_thinking` по умолчанию true → в многоходовых диалогах возвращать `reasoning_content` истории как отдельное поле, НЕ склеивать в `content`.
- Параметры передавать top-level в JSON (HTTP), в Python SDK — через `extra_body`.
- Всегда `stream: true` + `stream_options.include_usage: true` (лимиты на нестрим thinking + таймауты).
- Image input: формат OpenAI `{"type":"image_url","image_url":{"url":"... или data:image/...;base64,..."}}`; для kimi-k3 держать в уме запрет публичных URL у Moonshot — на probe; glm-5.3 — изображения не поддерживает (400).

### 9.2. Вынести в runtime capability probe (на модель)

1. thinking: дефолтное состояние (есть ли `reasoning_content` без параметров); принимается ли `enable_thinking: false`; accepted-значения `reasoning_effort` (прогнать low/medium/high/max/xhigh/none); принимается ли `thinking_budget`; одновременная передача effort+budget → ошибка?
2. image input: поддержка `image_url` (URL и base64) через compatible endpoint (особенно kimi-k3, deepseek-v4.1-flash).
3. FC: `tools` в stream и не-stream; формат `tool_calls` в delta; `tool_choice: "required"` (Qwen не поддерживает — проверить текст ошибки); для kimi-k3 — Dynamic Tool Loading (system-сообщение с `tools`, без `content`): работает / 400 / `tokenization failed` / игнорируется.
4. `preserve_thinking` для kimi-k3; `clear_thinking` поведение у glm-5.3.
5. Реальный дефолт/максимум max output у kimi-k3 (1048576 — подозрительно).
6. Работоспособность legacy base_url `https://dashscope.aliyuncs.com/compatible-mode/v1` с текущим ключом (регион ключа!) — 401 при mismatch.
7. Responses API: kimi-k3 — есть ли `reasoning.effort`; впрочем, проекту Responses API не нужен (Chat Completions достаточно).

---

## 10. Открытые вопросы для runtime probe

1. deepseek-v4.1-flash: thinking включён или выключен по умолчанию? (прямое противоречие #5 vs #16)
2. deepseek-v4.1-flash: поддерживает ли `thinking_budget` и каков Max CoT (на странице модели нет)?
3. kimi-k3 на MS-endpoint: отключается ли thinking (`enable_thinking:false`)? Что вернёт `reasoning_effort:"low"` — реальное изменение глубины?
4. kimi-k3 на MS-endpoint: Dynamic Tool Loading через system+tools — работает ли (у Moonshot — только k3, у прочих `tokenization failed`)?
5. kimi-k3 на MS-endpoint: публичные URL изображений принимаются или только base64 (как у Moonshot)?
6. glm-5.3: точный текст/код 400 при `enable_thinking:false`; подтвердить default `clear_thinking=true` (reasoning истории реально отбрасывается?).
7. qwen3.8-*: подтвердить ошибку при `reasoning_effort`+`thinking_budget` вместе; подтвердить, что `reasoning_effort:"none"` эквивалентен `enable_thinking:false`.
8. Все модели: есть ли `reasoning_content` в **нестримовом** ответе (для fallback-режима), и требует ли включённый thinking стриминг (400 «only support stream call»)?
9. Все модели: `finish_reason="tool_calls"` и инкрементальность `function.arguments` в stream — подтвердить на реальных чанках (особенно GLM с tool_stream и Kimi).
10. Legacy-домен dashscope.aliyuncs.com: актуальный статус для нашего региона/ключа; нет ли скрытого редиректа/депрекации на уровне сети.
