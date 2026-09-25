# MODEL REGRESSION MATRIX — заполнена (FIX V2, commit 478a172, 2026-09-24)

Offline = pytest contract/unit (MockTransport/fakes, без сети). Live = реальные вызовы
на VPS 176.108.245.225 (2026-09-24, `probe_20260924_190515.json`, флаг --strict).
Бюджет live: один прогон smoke на модель/режим (дешёвые prompts).

| Exact model ID | Provider | Вид | Offline tests | Live test (2026-09-24) |
|---|---|---|---|---|
| `gemini-3.8-flash` | gemini | user | pass (payload/SSE/tools/EOF/cancel/no-reasoning) | not_run (бережём квоту пула) |
| `gemini-3.7-flash` | gemini | user | pass (тот же контракт) | not_run |
| `gemini-3.6-flash` | gemini | user | pass | **passed** (pool E2E 18–19.09 + VPS: text/stream/usage/rotate) |
| `qwen3.8-flash` | alibaba | user | pass (payload thinking off/low/medium/max, SSE, EOF, FC) | **passed** (text/off/low/medium/max/FC/image) |
| `qwen3.8-max` | alibaba | user | pass | **passed** (text/off/low/medium/max/FC/image) |
| `deepseek-v4.1-flash` | alibaba | user | pass | **passed** (text/off/low/high/max/FC/image) |
| `glm-5.3` | alibaba | user | pass | **passed** (text/low/high/max/FC; image=skipped by design) |
| `kimi-k3` | alibaba | user | pass | **passed** (text/off/low/high/max/FC/image/DTL) |
| `deepseek-v4-pro` | alibaba | user (новая, N04) | pass (payload DEFAULT/OFF/HIGH/MAX, text-only guard, FC, без лишних параметров) | **passed** (text/off/high/max/FC; image skipped=text-only) |
| `gemini-3.5-flash-lite` | gemini | internal | pass (не в user selector) | **passed** (title/compaction вызовы в проде 18–24.09, 200 OK) |

## Заметки по нестабильности (N03 — причины найдены и устранены)

1. **Gemini 400 «additionalProperties»** — tool-схемы с неподдерживаемыми ключами →
   санитайзер (уже с 18.09). Нестабильность была 100% при tools, не «flaky».
2. **Gemini «молчание»** — functionCall с finishReason=STOP терялся → пустой ответ.
   Fixed: tool loop по факту вызовов.
3. **gemini-3.7-flash «high demand» 503** — транзиентная ёмкость Google; pool ротирует
   (проверено live 18–19.09: ~8 проектов за ~13 с → ответ).
4. **gemini-09/11 PERMISSION_DENIED** — мёртвые проекты Google; теперь auto-disable.
5. **Alibaba usage терялся** (usage после finish_reason) — parser буферизует Done.
6. **Silent EOF** — обе модели принимали обрыв как успех; теперь NetworkError.

## Прочее

- Photos при Pro (text-only): явный отказ с перечнем image-capable моделей (без потери
  файла, без cross-model fallback) — покрыто offline; live — не запускал (по бюджету).
- TTL/stale: health ≠ capability; transient ошибка не выключает модель (N03 реализовано:
  enabled отдельно, cooldown временный, capability evidence не стирается).
- Ключи/секреты: ни один не попадает в логи/отчёты (только key_hint-маски).
