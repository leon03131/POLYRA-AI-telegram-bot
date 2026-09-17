# Google Gemini — vendor doc

- **Дата проверки:** 2026-09-18
- **Версии:** google-genai SDK **2.24.0** (Python ≥3.10); SDK 3.0.0 на подходе с ломающими изменениями → пин `<3.0.0`. Актуальное поколение моделей — Gemini 3.x.
- **Источники:** ai.google.dev (через Wayback Machine 13–17.09.2026 — прямой доступ из среды разработки заблокирован), docs.cloud.google.com (Vertex/Agent Platform-зеркало), github.com/googleapis/python-genai, PyPI. Полный отчёт: `.agents/reports/gemini.md`.
- ⚠️ Факты по AI Studio восстановлены по архивам — перепроверить с рабочей сети перед релизом.

## Модели проекта (все подтверждены в документации)

| model_id | Статус | Контекст | Max output | Images | Thinking levels | Default |
|---|---|---|---|---|---|---|
| `gemini-3.8-flash` | stable, new | 1 048 576 | 65 536 | ✅ | low / medium / high | medium |
| `gemini-3.7-flash` | stable | 1 048 576 | 65 536 | ✅ | low / medium / high | medium |
| `gemini-3.6-flash` | stable | 1 048 576 | 65 536 | ✅ | minimal / low / medium / high | medium |
| `gemini-3.5-flash-lite` (internal only) | stable | 1 048 576 | 65 536 | ✅ | minimal / low / medium / high | minimal |

## Параметры, реально используемые проектом

- **Thinking: ТОЛЬКО `thinking_level`** (никакого `thinking_budget` для 3.x — вместе = ошибка; budget — legacy 2.5).
- Streaming: `POST {base}/v1beta/models/{model}:streamGenerateContent?alt=sse` (SSE).
- Function calling: `tools=[{function_declarations:[...]}]`, ручной цикл (AFC off), `function_response` parts.
- **Thought signatures ОБЯЗАТЕЛЬНЫ в multi-turn FC** (даже при MINIMAL): хранить `functionCall`-parts с `thought_signature` as-is и возвращать в историю; нарушение → HTTP 400.
- Token counting: `POST :countTokens` (используем для TokenBudgetManager; локальный токенизатор SDK — опционально).
- Images: inline base64 (`inline_data`), суммарный запрос ≤20 MB; форматы png/jpeg/webp/heic/heif.
- Usage из ответа: `usageMetadata` (prompt/candidates/thoughts/total token counts).
- **НЕ трогаем temperature/top_p/top_k** у 3.x (рекомендация Google); max_output_tokens не использовать как «экономию» (режет вместе с мыслями).

## Rate limits / пул проектов

- **Квоты per-project, не per-key** (подтверждено) → схема 30 ключей из 30 проектов корректна.
- Измерения: RPM / TPM(input) / RPD (ресет в полночь Pacific) + spend-based лимиты (rolling 10 мин).
- 429 (Interactions API): `{"error":{"code":"rate_limit_exceeded"|"quota_exceeded"|"too_many_requests"}}`;
  для generateContent тело 429 не подтверждено документально → **runtime probe** (ожидаем RESOURCE_EXHAUSTED + RetryInfo).
- Числа RPM/TPM/RPD публично не публикуются → наши initial-лимиты (4/249999/19) — конфиг в БД, редактируется в админке; реальные лимиты узнаём из 429.

## Главный риск и решение

- **Custom base_url в google-genai SDK официально задокументирован только для `enterprise=True`** → для api_key-клиента не гарантирован.
- **РЕШЕНИЕ: raw httpx адаптер как основной** (полный контроль base_url/прокси/ротации ключей/SSE).
  SDK `google-genai<3.0.0` остаётся в зависимостях для `count_tokens`/локального токенизатора и как запасной путь.
- База: `https://extraordinary-piroshki-4e3b92.netlify.app` (proxy владельца, НЕ менять).
  Пути запросов: `{base}/v1beta/models/{model}:streamGenerateContent?alt=sse`, `:countTokens` — probe обязан подтвердить, что шлюз проксирует эти пути.

## Interactions API

Существует (`POST /v1beta/interactions`, stateful `store:true`), активно продвигается Google.
**Решение: НЕ использовать в v1** — историю мы ведём сами в PostgreSQL, generateContent
лучше документирован на уровне parts (`part.thought`, `functionCall`, `thoughtSignature`).
Пересмотр — только если probe покажет проблемы со стримом через шлюз.

## Противоречия/пробелы

1. Лимит изображений: 3600 (AI Studio) vs 3000 (Vertex) → берём меньшее.
2. Тело 429 для generateContent не подтверждено документально → probe.
3. `ai.google.dev` недоступен из dev-среды → факты из Wayback 13–17.09.2026.

## Runtime probe (scripts/smoke_providers.py)

base_url+пути через шлюз; тело 429; SSE-формат 3.x (thought/signature parts, usageMetadata-чанк);
MINIMAL на 3.6 + multi-turn FC граница 400; фактические лимиты проектов; Files API (на будущее).
