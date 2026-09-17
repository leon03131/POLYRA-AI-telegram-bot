# R-GEMINI: исследование Gemini API для Telegram AI Assistant

- **Дата проверки:** 2026-09-18
- **Исполнитель:** research-субагент R-GEMINI
- **Область:** Gemini как основной LLM-провайдер (модели, thinking, streaming, function calling, images, tokens, Interactions API, rate limits, custom base_url, ~30 ключей из разных AI Studio-проектов)

---

## 1. Источники и статус доступности

| Источник | Статус | Примечание |
|---|---|---|
| `https://ai.google.dev/gemini-api/docs/*` (прямой доступ) | **НЕДОСТУПЕН** | Transport error на все попытки (models, thinking, rate-limits, interactions, корень домена). Из этой среды домен не открывается |
| `https://web.archive.org/web/2026/https://ai.google.dev/gemini-api/docs/*` | **OK** | Снапшоты Wayback Machine от 13–17.09.2026 (т.е. 1–5 дней назад): models (upd. 2026-09-15), rate-limits (upd. 2026-09-02), tokens (upd. 2026-09-04), function-calling (2026-09-15), image-understanding (upd. 2026-09-02), streaming (2026-09-16), thinking (upd. 2026-09-09), api-errors (upd. 2026-08-26), troubleshooting (upd. 2026-09-04) |
| `https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/*` (бывш. Vertex AI) | **OK** | Страницы моделей 3.8/3.7/3.6 Flash, 3.5 Flash-Lite, thinking, thought-signatures |
| `https://github.com/googleapis/python-genai` (README) | **OK** | Актуальный README: base_url, proxy, streaming, FC, count_tokens, Interactions, предупреждение о SDK 3.0 |
| `https://googleapis.github.io/python-genai/` | **OK** | Сгенерированная SDK-документация; раздел Custom base url содержит критичное ограничение (см. §10) |
| `https://pypi.org/pypi/google-genai/json` | **OK** | Метаданные пакета |
| `https://ai.google.dev/gemini-api/docs/interactions` | не проверен напрямую | В ТЗ-URL; фактический URL раздела — `/docs/interactions-overview` (есть в навигации всех страниц) |

**Вывод по доступности:** прямой ai.google.dev из рабочей среды заблокирован/недоступен — все факты по Gemini Developer API восстановлены по архивным копиям 13–17.09.2026 и зеркалу в документации Google Cloud (Gemini Enterprise Agent Platform). Расхождения маловероятны, но перед релизом стоит перепроверить с рабочей сети.

## 2. Версии

- **google-genai Python SDK (PyPI): 2.24.0** (проверено через PyPI JSON 2026-09-18). `requires_python >= 3.10`.
- **SDK 3.0.0 — на подходе, ломающие изменения.** README: «To avoid unexpected updates, pin the SDK version to `< 3.0.0`». В 3.0 уберут AFC из `Models.generate_content` (только через `Chats`), удалят `GenerationConfigThinkingConfig` (использовать `ThinkingConfig`), ряд методов Live API.
- **Поколения моделей:** актуальное поколение — **Gemini 3.x** (3.8 — флагман Flash; 3.5 — уже «legacy»). Gemini 2.5 поддерживается, Gemini 2.0 — **shut down**.

## 3. Модели из ТЗ — таблица верификации

Все четыре модели ТЗ **найдены в документации** (и в Gemini Developer API / AI Studio, и в Agent Platform). Страница models (ai.google.dev, upd. 2026-09-15) перечисляет endpoint-строки явно.

| Модель ТЗ | В доках? | model_id | Статус | Контекст | Max output | Images input | Thinking (AI Studio docs) |
|---|---|---|---|---|---|---|---|
| gemini-3.8-flash | **Да** | `gemini-3.8-flash` | Stable, «New» | 1,048,576 | 65,536 | Да (PNG/JPEG/WEBP/HEIC/HEIF) | low / medium / high, default **medium** |
| gemini-3.7-flash | **Да** | `gemini-3.7-flash` | Stable («previous-generation») | 1,048,576 | 65,536 | Да | low / medium / high, default **medium** |
| gemini-3.6-flash | **Да** | `gemini-3.6-flash` | Stable («previous-generation») | 1,048,576 | 65,536 | Да | **minimal** / low / medium / high, default **medium** |
| gemini-3.5-flash-lite | **Да** | `gemini-3.5-flash-lite` | Stable | 1,048,576 | 65,536 | Да | minimal / low / medium / high, default **minimal** |

Соответствие ТЗ полное, включая «MINIMAL для 3.6» и роль 3.5-flash-lite как дешёвой внутренней модели (fastest/cost-effective, default minimal).

Прочее с модельной страницы (может пригодиться): `gemini-3.5-flash` (legacy, default medium), `gemini-3.1-flash-lite` (default minimal), `gemini-3.1-pro-preview` (default high), `gemini-3-flash-preview`, `gemini-flash-latest` (alias, hot-swap с 2-недельным уведомлением), image-модели `gemini-3.1-flash-image` (Nano Banana 2) и т.д. У 3.6 Flash заявлены: thinking, system instructions, structured output, context caching (implicit+explicit), count tokens, URL context; **Live API — не поддерживается** (для всей линейки Flash 3.x, судя по страницам).

## 4. Thinking: точный механизм для Gemini 3.x

**Для Gemini 3+ актуален `thinking_level` (НЕ `thinking_budget`).**

- **generateContent API / SDK:** `config=types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.LOW|MEDIUM|HIGH|MINIMAL))` (примеры из Vertex-доков; `types.ThinkingLevel` — enum в SDK).
- **Interactions API (REST):** поле запроса `generation_config.thinking_level: "low"`; суммаризации включаются `generation_config.thinking_summaries: "auto"`.
- Таблица поддерживаемых уровней по моделям — см. §3 (источники: ai.google.dev/docs/thinking и Vertex thinking — идентичны).
- **Несовместимость:** `thinking_level` + `thinking_budget` в одном запросе к Gemini 3 → ошибка. `thinking_level` к модели старше Gemini 3 → ошибка.
- **`thinking_budget` — legacy для 2.5 и ранее** (дефолт — динамический до 8192; `-1` = dynamic; `0` = выключить для 2.5 Flash/Lite; у 2.5 Pro не выключается). Для 3.x **не использовать**. В SDK 3.0 `GenerationConfigThinkingConfig` удаляется.
- `MINIMAL` — почти нулевой бюджет рассуждений, **но thought signatures всё равно требуются** (иначе 400).
- Thought summaries: в generateContent — `ThinkingConfig(include_thoughts=True)`; мысли приходят как `part.thought == True` в `candidates[0].content.parts`; счётчик — `usage_metadata.thoughts_token_count`.
- **`max_output_tokens` включает thinking-токены** — при малом лимите ответ обрезается со статусом `"incomplete"` (биллинг за мысли идёт). Снижать стоимость надо через `thinking_level`, а не через `max_output_tokens`.
- Рекомендация Google: для Gemini 3.x **не трогать temperature/top_p/top_k** (оставлять дефолт 1.0) — иначе петли и деградация.

### Thought signatures (критично для function calling в 3.x)

- `thought_signature` — зашифрованное «состояние рассуждения» внутри content-part'ов (text, functionCall и др.).
- Gemini 3: подписи **обязаны** возвращаться в следующих запросах многошагового диалога, **даже при MINIMAL**; пропуск обязательной подписи → **HTTP 400**.
- Правила: parallel FC — подпись только у первого `functionCall`-part; sequential FC — у каждого; при parallel ответах порядок `FC1+sig, FC2, FR1, FR2`, чередование → 400. Эскейп: `thought_signature="skip_thought_signature_validator"` (деградирует качество, last resort).
- Non-FC: подпись может быть в последнем part; рекомендовано возвращать, но не enforced. **При стриминге подпись может прийти в part с пустым text — парсить все parts до finish_reason.**
- Официальные SDK обрабатывают подписи автоматически, если в историю класть полный `response.candidates[0].content` / использовать Chats.
- В **Interactions API** подписи изолированы в dedicated `thought`-steps (и в server-side tool steps); при stateful-режиме (`store: true` + `previous_interaction_id`) их ведёт сервер.

## 5. Streaming API

### 5a. generateContent (текущий «классический» путь)

- SDK: `client.models.generate_content_stream(model, contents, config)`; async: `client.aio.models.generate_content_stream(...)`; chunk — `GenerateContentResponse`: `chunk.text`, `chunk.candidates[0].content.parts[]`, `chunk.function_calls`, `chunk.usage_metadata`.
- Мысли в стриме: parts с `part.thought == True` (паттерн из офиц. доков: `if part and part.thought: print(part.text)`).
- REST: `POST /v1beta/models/{model}:streamGenerateContent` (SSE при `alt=sse`).

### 5b. Interactions API (новый рекомендуемый путь, `stream: true`, SSE)

Поток событий (подтверждено страницей streaming):

1. `interaction.created` (id, model, status=in_progress) → `interaction.status_update` →
2. циклы `step.start` (type: `model_output` | `thought` | `function_call` | server-side tools) → `step.delta` → `step.stop` →
3. `interaction.completed` (финальный объект с `usage`) → `event: done` / `data: [DONE]`. Ошибки в стриме: `event: error` с `{error:{code,message}}`.

Типы дельт: `text`, `image` (base64), `audio`, `thought_summary` (content.text), `thought_signature` (последняя дельта перед `step.stop`), `arguments_delta` (JSON-куски аргументов FC — **накапливать строкой**), `google_search_call/result` и др.

Usage в `interaction.completed`: `total_tokens`, `total_input_tokens`, `total_output_tokens`, `total_thought_tokens`, `total_cached_tokens`, `total_tool_use_tokens`, `input_tokens_by_modality`. Статус при FC: `requires_action`.

## 6. Function calling

### Схема (оба API)

- **generateContent/SDK:** `types.Tool(function_declarations=[types.FunctionDeclaration(name, description, parameters_json_schema={...})])` в `GenerateContentConfig.tools`; ответ — `response.function_calls` (part'ы `functionCall` с `name`/`args`); результат возвращается `types.Part.from_function_response(name=..., response={...})` в content с `role='tool'`. AFC (automatic function calling) включён по умолчанию при передаче python-функций; отключение: `automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)`; режимы `FunctionCallingConfig(mode='ANY'|...)`. **В SDK 3.0 AFC из `generate_content` уберут** — проекту ручной цикл (AFC off) безопаснее.
- **Interactions API:** tools: `[{"type": "function", "name", "description", "parameters": {...}}]`; ответ — `interaction.steps[]` со step `type="function_call"` (`id`, `name`, `arguments`); возврат результата: input `[{"type": "function_result", "name", "call_id", "result": ...}]` + `previous_interaction_id` (stateful) или `store: false` + полная история (user_input → все model steps включая thought/function_call → function_result).
- Parallel и compositional (sequential) FC поддерживаются. Ошибки генерации: `malformed_function_call`, `unexpected_tool_call`, `too_many_tool_calls`, `missing_thought_signature` (коды с api-errors).
- Официально: «Gemini 3 series models use an internal thinking process that improves function calling. The SDKs automatically handle thought signatures for you.»

## 7. Token counting

- SDK: `client.models.count_tokens(model=..., contents=...)` → `.total_tokens` (входные токены; async-вариант есть). REST: `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:countTokens` с `x-goog-api-key`.
- Локально (офлайн): `google.genai.local_tokenizer.LocalTokenizer(model_name=...)`.
- `client.models.get(model=...)` → `input_token_limit`, `output_token_limit`.
- Токенизация медиа: изображение ≤384px = 258 токенов; больше — тайлы 768×768 по 258; аудио 32 ток/с; видео ~100–300 ток/с (1 FPS, зависит от разрешения; «agentic» режим экономит до 88%).
- Usage по факту — из ответа (`usage_metadata` в generateContent / `interaction.usage` в Interactions): input/output/thought/cached/tool_use.

## 8. Rate limits

Источник: ai.google.dev/docs/rate-limits (снапшот 2026-09-15).

- Измерения: **RPM** (requests/min), **TPM** (input tokens/min), **RPD** (requests/day; ресет в полночь Pacific). Отдельные лимиты для Batch API и для image-моделей (IPM).
- **«Rate limits are applied per project, not per API key»** — т.е. схема проекта (30 ключей из 30 разных AI Studio-проектов = 30 независимых наборов квот) **корректна и подтверждена документацией**.
- Usage tiers: Free → Tier 1 (billing, cap $250) → Tier 2 (оплачено $100+ и 3 дня) → Tier 3 ($1000+ и 30 дней). Конкретные RPM/TPM/RPD по моделям на странице не публикуются — смотреть в AI Studio (aistudio.google.com/rate-limit); «Specified rate limits are not guaranteed».
- Дополнительно: **spend-based лимиты** (rolling 10 мин): Tier1 $10/10мин, Tier2 $50, Tier3 $200 → превышение даёт `429 RESOURCE_EXHAUSTED`.
- Тело 429 (Interactions API, подтверждено api-errors): HTTP 429 + `{"error": {"code": "rate_limit_exceeded" | "quota_exceeded" | "too_many_requests", "message": "..."}}`; `rate_limit_exceeded` — минутные/секундные, `quota_exceeded` — дневная квота. В SSE — `event_type: "error"` с той же структурой.
- Для классического generateContent-эндпоинта документированная в этой сессии структура 429 не получена (ожидаемый google.rpc.Status-подобный формат с `status: "RESOURCE_EXHAUSTED"` и RetryInfo — **требует runtime probe**, см. §13).
- Retry: официальные SDK ретраят 429/5xx автоматически до 4 раз (старт ~1 с, макс 60 с, exponential backoff). Для ротации ключей это надо учитывать (или отключать/оборачивать).

## 9. Image input

- Способы: **inline** (`{"type":"image","data": base64, "mime_type"}` в Interactions; `types.Part.from_bytes(data, mime_type)` в SDK/generateContent) и **по ссылке** (`uri`: File API upload или публичный URL; `types.Part.from_uri`).
- **Inline-лимит: суммарный размер запроса (текст + system instructions + inline bytes) ≤ 20 MB** — иначе через Files API (`client.files.upload`, только Gemini Developer API).
- Форматы: `image/png`, `image/jpeg`, `image/webp`, `image/heic`, `image/heif`.
- Кол-во: до **3,600 изображений на запрос** (AI Studio image-understanding); на Vertex-странице 3.8 Flash указано **3,000** — расхождение источников, брать меньшее.
- Gemini 3: параметр `media_resolution` — гранулярный контроль токенов на изображение/кадр.

## 10. Custom base_url в SDK — ТОЧНЫЙ СПОСОБ И РИСКИ

Задокументированный механизм (README + SDK docs):

```python
client = genai.Client(
    enterprise=True,  # «Currently only enterprise=True is supported.» (SDK docs!)
    http_options=types.HttpOptionsDict(
        base_url="https://test-api-gateway-proxy.com",
        base_url_resource_scope=types.ResourceScope.COLLECTION,
        # headers={...}
    ),
)
```

- **Ключевой риск:** в сгенерированной SDK-документации прямо написано, что custom base url **«currently only enterprise=True is supported»** (пример для Agent Platform). Для Gemini Developer API (`api_key=...`) подмена base_url **официально не задокументирована** — технически `HttpOptions.base_url` применяется при построении запроса в обоих бэкендах (судя по коду SDK), но это нужно проверять **runtime probe** до принятия архитектурного решения.
- Смежные механизмы: `http_options=types.HttpOptions(api_version='v1'|'v1alpha')`, `HttpOptions(client_args={...}, async_client_args={...})` для прокси на уровне httpx (`proxy: 'socks5://...'`), env `HTTPS_PROXY` + `SSL_CERT_FILE`, `HttpOptions(extra_body={...})` для недокументированных полей запроса.
- Внимание: base_url-подмена ≠ прокси. Если владельцу нужен именно forward-proxy к `generativelanguage.googleapis.com`, достаточно `client_args={'proxy': ...}`; если нужен **прозрачный шлюз, принимающий Google-совместимые пути** — тогда base_url. При base_url + ResourceScope.COLLECTION путь формируется без версии API (`{base}/publishers/google/models/{model}` в enterprise-примере) — для MLDev-бэкенда итоговый путь проверять probe'ом.
- Ошибки SDK: `google.genai.errors.APIError` с `.code`/`.message`.

## 11. Interactions API — статус

**Существует, официально и активно продвигается.** Навигация ai.google.dev содержит раздел Guides → «Interactions API» (`/docs/interactions-overview`), плюс страницы «Migrate to Interactions API» и «Interactions breaking changes (May 2026)». На снапшотах сентября 2026 все гайды (function calling, image understanding, tokens, streaming, thinking) переписаны на Interactions-примеры — это де-факто новый основной интерфейс Gemini API.

- Что это: унифицированный интерфейс «взаимодействий» с моделями и агентами: server-side state (`store: true`, `previous_interaction_id`), оркестровка инструментов, long-running задачи (`background=True`, polling `client.interactions.get(id)`), агенты (`deep-research-preview-04-2026`, `antigravity-...`), мультимодальный ввод/вывод, built-in tools (`google_search`, `code_execution`, `mcp_server`, `file_search`, `url_context`, `computer_use`, `bash`, `filesystem`, `google_maps`).
- REST: `POST https://generativelanguage.googleapis.com/v1beta/interactions` (SSE при `stream: true`).
- SDK: `client.interactions.create(...)` (sync/async; есть в reference: `Client.interactions`, `AsyncClient.interactions`).
- Совместимость с generateContent существует параллельно (SDK `models.generate_content*` живёт; миграционные гайды описывают переход). Есть задокументированные **breaking changes мая 2026** — читать перед использованием в проде.

## 12. Противоречия и пробелы

1. **ai.google.dev недоступен напрямую из рабочей среды** — все факты по AI Studio восстановлены через Wayback (снапшоты 13–17.09.2026) и Vertex-зеркало. Требуется перепроверка из нормальной сети.
2. **Custom base_url для api_key-клиента официально «only enterprise=True»** — главный архитектурный риск (см. §10).
3. **Лимит изображений:** 3,600 (AI Studio image-understanding) vs 3,000 (Vertex model pages) — не сведено.
4. **Конкретные числа RPM/TPM/RPD** по моделям/тирам на публичной странице отсутствуют (только в AI Studio UI) — capacity planning требует снятия фактических лимитов с каждого из 30 проектов.
5. **Тело 429 для generateContent-эндпоинта** в этой сессии документально не подтверждено (подтверждено только для Interactions API).
6. Vertex-страницы говорят «Live API: Not supported» для Flash 3.x — для ТЗ не критично, но заметить.
7. Даты «last updated» разных страниц различаются (26.08–15.09.2026) — возможны мелкие рассинхроны.
8. Страница `/docs/interactions` из ТЗ не подтверждена; фактический раздел — `/docs/interactions-overview` + API reference `/api/interactions-api`.

## 13. Рекомендации проекту

**Архитектура адаптера:**

1. **Основной вариант — raw httpx адаптер.** Причины: (а) критический риск §10 — custom base_url для api_key в SDK официально не поддержан; (б) трафик идёт через свой HTTPS-шлюз — контроль путей/заголовков/`x-goog-api-key` полный; (в) 30 ключей-проектов — тонкая ротация без переинициализации SDK-клиентов; (г) SSE-парсинг для обоих API тривиален. REST-контракты подтверждены: `POST {base}/v1beta/models/{model}:streamGenerateContent?alt=sse` или `POST {base}/v1beta/interactions` (`stream: true`).
2. **Альтернатива — SDK (pin `google-genai<3.0.0`, текущая 2.24.0)**, если runtime probe покажет, что `HttpOptions(base_url=...)` работает с `api_key`. Плюсы: типы, auto-retry 429/5xx, авто-обработка thought signatures в истории, локальный токенизатор. Минусы: скрытые ретраи при ротации ключей, грядущий AFC-лом в 3.0.
3. **Выбор API:** для нового кода рассмотреть **Interactions API** (stateful `store:true` избавляет от ведения истории и подписей; стрим-события чище маппятся). generateContent остаётся рабочим и лучше документирован на уровне частей (`part.thought`, `functionCall`). Решить после probe; в ТЗ Interactions упомянут — документация это поддерживает.

**Маппинг событий стрима в унифицированные типы проекта:**

| Проектный тип | generateContent stream | Interactions stream |
|---|---|---|
| `TextDelta` | `part.text` при `part.thought != True` | `step.delta` type=`text` в step `model_output` |
| `ReasoningDelta` | `part.text` при `part.thought == True` (нужен `include_thoughts`) | `step.delta` type=`thought_summary` (нужен `thinking_summaries: "auto"`) |
| `ToolCall` | `part.function_call` (приходит целиком в чанке) + сохранить `part.thought_signature` | step `function_call`: `step.start` (id, name) + накопленные `arguments_delta` (JSON-строка собирается по кускам) |
| `Usage` | последний чанк: `usage_metadata` (`prompt_token_count`, `candidates_token_count`, `thoughts_token_count`, `total_token_count`) | `interaction.completed.interaction.usage` (`total_input_tokens`, `total_output_tokens`, `total_thought_tokens`, ...) |
| Ошибка | HTTP-код + тело / исключение | `event: error` (`error.code`, `error.message`), статусы `failed`/`incomplete`/`requires_action` |

**Прочее:**

4. Thinking: маппинг уровней ТЗ 1:1 — LOW/MEDIUM/HIGH для 3.8/3.7; MINIMAL разрешён только где поддерживается (3.6 — да; 3.8/3.7 — **нет**, по таблице!). Дефолты: medium у 3.6–3.8, minimal у 3.5-flash-lite. `thinking_budget` для 3.x не слать.
5. FC: вести ручной цикл (AFC off), хранить `functionCall`-part'ы с подписями «как есть», не мерджить/не переставлять; parallel FC — ответы всем пакетом после всех вызовов.
6. Изображения: inline до ~20 MB суммарного запроса; при большем — Files API (не забудьте: Files API есть только в Gemini Developer API — у нас он и есть).
7. Ротация 30 ключей: квоты per-project ⇒ ключ=проект, ротировать по 429 (`rate_limit_exceeded` — минутный: backoff+следующий ключ; `quota_exceeded` — дневной: ключ выключается до полуночи PT). Учитывать spend-based 10-минутные лимиты на платных тирах.
8. Не менять temperature/top_p/top_k у 3.x без нужды; `max_output_tokens` не использовать как «экономию» (режет вместе с мыслями).

## 14. Открытые вопросы для runtime probe

1. Работает ли `genai.Client(api_key=..., http_options=HttpOptions(base_url=...))` против шлюза владельца (или только `enterprise=True`)? Какой путь реально формируется (`/v1beta/models/{m}:streamGenerateContent` сохраняется?) — прологировать.
2. Тело 429 от generateContent и от /interactions через шлюз: коды, поля, наличие RetryInfo/`retryDelay`, заголовки.
3. Формат SSE generateContent на 3.x: приходят ли `thought=true` parts и `thoughtSignature` в стриме; в каком чанке `usageMetadata`.
4. Поведение `MINIMAL` на 3.6-flash и дефолта на 3.5-flash-lite: приходит ли 400 без thought signatures в multi-turn FC (проверить границу «turn»).
5. Поддерживает ли шлюз endpoint `/v1beta/interactions` и `countTokens` (или только generateContent-пути) — от этого зависит выбор API.
6. Фактические RPM/TPM/RPD/tier по каждому из 30 проектов (aistudio.google.com/rate-limit) + spend-лимиты.
7. `gemini-flash-latest` алиас — что сейчас за ним стоит (для потенциального fallback).
8. Files API через шлюз (upload, uri в запросе) — если понадобятся большие изображения.
9. Таймауты/keep-alive SSE через шлюз (idle-timeout прокси, буферизация!) — критично для стриминга.
10. SDK 3.0: когда выйдет и что ещё сломается — следить; пиновать `< 3.0.0`.
