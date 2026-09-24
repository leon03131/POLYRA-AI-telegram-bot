# MODEL_REGRESSION_MATRIX — шаблон, НЕ live-отчёт

Существующий список взят из baseline-кода/ТЗ; эксплуатация — по сообщению владельца.
При пересборке пакета ни одна модель не вызывалась. Дополнить модели нового checkout.

| Exact model ID | Provider | Вид | Исходная информация | Offline tests | Новый live test |
|---|---|---|---|---|---|
| `gemini-3.8-flash` | gemini | user | владелец сообщает о рабочих ответах | pending | not_run |
| `gemini-3.7-flash` | gemini | user | владелец сообщает о рабочих ответах | pending | not_run |
| `gemini-3.6-flash` | gemini | user | владелец сообщает о рабочих ответах | pending | not_run |
| `qwen3.8-flash` | alibaba | user | владелец сообщает о рабочих ответах | pending | not_run |
| `qwen3.8-max` | alibaba | user | владелец сообщает о рабочих ответах | pending | not_run |
| `deepseek-v4.1-flash` | alibaba | user | владелец сообщает о рабочих ответах | pending | not_run |
| `glm-5.3` | alibaba | user | владелец сообщает о рабочих ответах | pending | not_run |
| `kimi-k3` | alibaba | user | владелец сообщает о рабочих ответах | pending | not_run |
| `deepseek-v4-pro` | alibaba | user | новая модель по N04; docs checked, live не проверено | pending | not_run |
| `gemini-3.5-flash-lite` | gemini | internal | существующая внутренняя модель; отдельно проверить | pending | not_run |

Для каждой строки создать подробности:
- commit, версия adapter/schema и effective settings;
- text/stream/thinking/tools/structured output/images (по применимости);
- usage/finish/EOF/cancel/errors/no-reasoning/no-fallback regression tests;
- live status: passed/failed/skipped/blocked/limited_budget, дата и количество запросов;
- точный endpoint label и credential record ID/version БЕЗ ключа;
- request mode, finish_reason, actual model label из ответа (если provider присылает),
  видимый final answer присутствует или нет, usage и latency;
- observed health отдельно от capability verdict и admin enabled;
- accepted parameter отдельно от evidence его самостоятельной семантики;
- limitations/next action, отдельная пометка stale для прежних результатов.

Photos для Pro: text-only отказ, а не перенос vision flag от Flash.
Публичный Pro ID не подменять snapshot `-0813`.
Internal Gemini проверять для compaction/title/memory, а не выводить в обычный selector.

Несколько успешных проб не доказывают абсолютную надёжность. No-key/timeout не
означает unsupported. Новое подтверждение не должно стирать старые evidence бесследно.
