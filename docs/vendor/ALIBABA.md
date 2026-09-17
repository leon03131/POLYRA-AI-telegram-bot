# Alibaba Cloud Model Studio — vendor doc

- **Дата проверки:** 2026-09-18
- **Endpoint (hard requirement):** `https://dashscope.aliyuncs.com/compatible-mode/v1` — legacy-домен Beijing, официально «remains fully functional». Workspace-домены НЕ используем. Ключ региональный: mismatch региона → 401 `invalid_api_key`.
- **Источники:** alibabacloud.com/help/en/model-studio/* (16 страниц, 11–17.09.2026), platform.kimi.ai/docs. Полный отчёт: `.agents/reports/alibaba.md`.

## Модели проекта (все 5 подтверждены страницами Model Studio)

| model_id | Контекст | Max output | Vision | FC | Thinking |
|---|---|---|---|---|---|
| `qwen3.8-flash` | 1M | 131 072 | image+video | ✅ | гибрид, default ON; effort low/medium/xhigh (default xhigh) |
| `qwen3.8-max` | 1M | 131 072 | image+video | ✅ | как flash |
| `deepseek-v4.1-flash` | 1M | 393 216 | image | ✅ | effort low/high/max (default по docs противоречив — probe) |
| `glm-5.3` | 1M | 131 072 | **нет** | ✅ (tool_stream default true) | **thinking-only**, effort low/high/max (default max), `clear_thinking` default true |
| `kimi-k3` | 1M | заявлено 1M (подозрительно, probe) | image | ✅ (страница модели) | effort low/high/max (default max); `thinking_budget` НЕ поддержан |

## Параметры, реально используемые проектом (Chat Completions)

- Всегда `stream: true` + `stream_options.include_usage: true` (usage в последнем чанке с `choices: []`;
  включает `completion_tokens_details.reasoning_tokens`).
- Thinking-параметры top-level (extra_body в SDK): `enable_thinking`, `reasoning_effort`,
  `preserve_thinking` (только qwen3.8; тогда reasoning истории возвращаем в поле `reasoning_content`),
  `clear_thinking` (только GLM). **Никогда не смешивать `reasoning_effort` с `thinking_budget` (Qwen3.8 → 400).**
  `thinking_budget` в проекте не используем вообще. `minimal`/`ultra` не используем.
- FC: стандартный OpenAI `tools`; `tool_calls` в delta инкрементально (id/name в первом чанке, arguments конкатенируются);
  `tool_choice:"required"` Qwen не поддерживает — не слать.
- Images: OpenAI-формат `image_url`; для kimi-k3 — только base64 data URI (у Moonshot публичные URL запрещены; probe).
- Нестримовые вызовы thinking-моделей могут требовать stream (400) → всегда стримим.

## Маппинг UI-уровней thinking (принят, см. ADR и отчёт §9.1)

| UI | qwen3.8-* | deepseek-v4.1-flash | glm-5.3 | kimi-k3 |
|---|---|---|---|---|
| OFF | `enable_thinking:false` | `enable_thinking:false` *(probe)* | — (не показывать OFF) | `enable_thinking:false` *(probe)* |
| LOW | effort=low | effort=low | effort=low | effort=low |
| MEDIUM | effort=medium | effort=high | effort=high | effort=high |
| MAX (UI «MAX»/«HIGH») | effort=xhigh | effort=max | effort=max | effort=max |

UI-модель проекта: qwen3.8: OFF/LOW/MEDIUM/MAX; deepseek: OFF/LOW/HIGH/MAX; glm-5.3: LOW/HIGH/MAX; kimi-k3: по результатам probe (OFF показывать только если `enable_thinking:false` принят). До probe — provider default (thinking on).

## Kimi Dynamic Tool Loading

- Задокументирован ТОЛЬКО у Moonshot (platform.kimi.ai): system-сообщение `{"role":"system","tools":[...]}` **без `content`**, append-only; `$ToolSearch` API не существует; только kimi-k3 (у прочих `tokenization failed`).
- На Model Studio endpoint **не подтверждено** → runtime probe; в v1 DTL не включаем, используем обычный tools-массив.

## Ошибки (retryable-классификация)

- Retryable: 429 (Throttling.RateQuota/BurstRate/AllocationQuota), 500 (InternalError*, RequestTimeOut, ResponseTimeout), 503 (ModelUnavailable/ServiceUnavailable), network/timeout.
- НЕ retryable: 400 (параметры/контекст/Arrearage/DataInspectionFailed), 401 (ключ/регион), 403, 404.
- Формат: OpenAI-style `{"error":{message,type,param,code}}`.

## Противоречия документации (зафиксированы, НЕ угадываем)

1. DeepSeek-V4 thinking default: «disabled» (deep-thinking) vs «enabled by default» (chat-ref) → probe.
2. `ultra` для deepseek-v4.1-flash: «mapped to max» vs «returns an error» → не использовать.
3. kimi-k3 `enable_thinking:false`: MS допускает vs Moonshot «always thinks» → probe.
4. kimi-k3 Max Output = 1M (страница модели) vs Moonshot default 131 072 → не передавать max_tokens без нужды.
5. Старая страница: «tools нельзя со stream» — устарела (новые страницы описывают tool streaming) → probe.
6. `preserve_thinking` для kimi-k3 (Alibaba-deployed) не подтверждён → не использовать.

## Решение проекта

- Клиент: OpenAI Python SDK с `base_url` проекта + httpx `trust_env=false` (ADR-004); либо raw httpx при проблемах с extra_body/stream — решить при реализации M3.
- Без proactive квот; реактивная обработка 429/5xx/timeout с bounded retry; ошибки маппим в user-facing сообщения.
- Capability probe на каждую модель (acceptance thinking-параметров, images, FC, DTL) с кешем в provider_health.
