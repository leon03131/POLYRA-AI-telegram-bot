# Матрица соответствия исходному ТЗ

Проверены разделы **0–57** приложенного ТЗ. «Есть» означает наличие соответствующей реализации в снимке и не означает live-проверку всех провайдеров. «Частично» отражает конкретные разрывы ниже. «Процесс не проверяется» — по снимку нельзя установить последовательность прошлой работы Kimi. Optional/future требования не считаются обязательными отсутствующими функциями.

Критерий — поведение всей цепочки, не название файла. Номера Axx отсылают к AUDIT.md/findings.json.

| № | Раздел исходного ТЗ | Статус снимка | Основание / замечания |
|---|---|---|---|
| 0 | Критическое правило работы / research и агенты | Процесс не проверяется | Отчёты есть; полную последовательность прошлой работы восстановить нельзя |
| 1 | Документация перед разработкой | Частично | Vendor docs и agent reports есть; актуальность и probe/runtime расходятся: A27, A38 |
| 2 | Stack | Есть, runtime не проверен полностью | Python/async/FastAPI/aiogram/PostgreSQL/React/Vite/Docker; установки и build ограничены средой |
| 3 | Owner и доступ | Частично | A03, A04, A05, A08, A26 — fail-open модели, owner flag, лимиты/гонки |
| 4 | Общая архитектура | Частично | Модули выделены, но generation orchestration перегружен и межслойные контракты нарушены: A06, A13, A39 |
| 5 | Project structure | В основном есть | Реальная модульная структура; отсутствие каждого рекомендованного имени файла само по себе не баг |
| 6 | Единый LLM interface | Частично | A06, A11, A23, A39; события есть, terminal/usage/cancel/tool-turn contract неполон |
| 7 | Model registry | Частично | A25, A27, A28 — enabled/effective model/capability cache/admin model management |
| 8 | Gemini | Частично | Custom proxy сохранён; A06/A27. Все модели и режимы здесь live не проверялись |
| 9 | Gemini project pool | Частично | A09, A10, A29 — атомарность, reconcile window, cooldown/retry, project identity |
| 10 | Внутренняя Gemini Flash-Lite | Частично | Internal summary/memory/title есть и используют pool; A06, A17 нарушают завершение/summary |
| 11 | Alibaba Cloud | Частично | Требуемый endpoint/direct transport есть; A06, A14, A23 |
| 12 | Alibaba models | Частично | Registry/mapping есть; A27/A39 — probe coverage/runtime и protocol verification |
| 13 | Никакого cross-model fallback | Частично | Штатные adapters сохраняют модель; A25 — неизвестный сохранённый model_id подменяется default |
| 14 | Network routing | В основном есть | Независимые настройки; Alibaba trust_env=False. Lifecycle и SSRF см. A19/A30 |
| 15 | Telegram bot | Частично | Команды/text/photo есть; A02, A18 |
| 16 | Telegram streaming | Частично | Rich/plain tiers есть; Stop, final длина, parse_mode и throttle: A11, A20–A22 |
| 17 | Photos | Частично | Первичный image request есть, persistence/history ломаются: A18 |
| 18 | Chat system | Частично | CRUD есть; one-active-chat/архив/pagination: A05, A24 |
| 19 | Messages | Частично | ORM/parts есть; image references и tool-turn context: A18, A39 |
| 20 | Chat title | В основном есть | NULL-title tool и внутренний fallback реализованы; live не проверен, A06 касается collect_text |
| 21 | История и context compaction | Частично | A15, A16 — непокрытая история теряется и конечный бюджет не соблюдается |
| 22 | Structured chat summary | Частично | A17 — empty JSON, неограниченный prompt, concurrent overwrite |
| 23 | Automatic memory | Частично | Extractor/retriever/dedup/UI есть; A12, A40 — off policy и укрепление дедупликации/FTS |
| 24 | Tool engine | Частично | Registry/schema/timeouts/logging есть; A12, A39 — effective tools и общие limits |
| 25 | Kimi Dynamic Tool Loading | Optional, не блокер сам по себе | Есть исторический probe, состояние docs/runtime нужно согласовать: A38 |
| 26 | Web search abstraction | Частично | Manager/backends есть; A30, A33 — lifecycle и fallback на malformed payload |
| 27 | Serper | Реализован, live не проверен здесь | Параметры и запрос есть; общие ошибки обработчиков — A33 |
| 28 | Google AI Overview | Частично | A32 — AUTO порядок, structured references, гарантии final sources |
| 29 | Jina | Частично | Search/Reader написаны, Reader не внедрён в рабочий open_url: A31 |
| 30 | Brave | Реализован, optional | Отсутствие ключа не обязано ронять startup; malformed JSON/fallback: A33 |
| 31 | Experimental Google browser | Optional, частично | Challenge abort есть; cooldown/AIO полнота ограничены: A33. CAPTCHA bypass не нужен |
| 32 | Open_url | Частично | Проверки scheme/private/redirect есть, resolve/connect TOCTOU: A19 |
| 33 | Search citations | Частично | A32 — show_sources=False и неполный контракт доказанных references |
| 34 | Mini App | Реализован статически | React/TS/Vite/theme/mobile UI есть; реальный Telegram/Desktop/Android/iOS E2E не выполнен |
| 35 | Mini App auth | Частично | HMAC/freshness/backend session есть; точный owner guard нарушен A04 |
| 36 | Mini App user UI | Частично | A01, A24, A25 — UUID/settings/archive/effective thinking |
| 37 | Mini App admin UI | Неполон | A13, A28, A35 — реальные system settings, models/health/test/reset/counters/metrics |
| 38 | Database | Частично | 8 миграций и основные entities есть; A05, A08, A09, A28 — constraints/ledger/provider health |
| 39 | Secrets | В основном есть | Encrypted DB keys, masked responses, redaction; ограниченный pattern scan без кандидатов; A14 — disable semantics |
| 40 | Audit log | Частично | Таблица и часть mutation events есть; полнота/пагинация: A35 |
| 41 | Generation runs | Частично | A07, A08, A34 — aggregate usage/project attribution, ledger lifetime, stale recovery |
| 42 | Observability | Частично | A35 — totals есть, полные latency/TTFT/model/project/search/tool metrics отсутствуют |
| 43 | Concurrency | Частично | In-memory registry не атомарен, межпроцессная защита/stale recovery: A05, A34 |
| 44 | Error UX | Частично | Safe user-facing errors есть; owner diagnostics и SSE completeness: A23, A28, A35 |
| 45 | Provider capability probe | Частично | A27 — две обязательные модели пропущены, cache/runtime не связан, критерии успеха слабые |
| 46 | Tests | Частично | A36 — доступные 285 cases проходят, но ошибочные инварианты закреплены и важные интеграции отсутствуют |
| 47 | Integration tests | Не доведены | Opt-in инструкции не заменяют реализованный gated integration suite/CI: A36 |
| 48 | Deployment | Частично, запуск не проверен | Multi-stage и Compose есть; A37 — readiness/signals/exposure; нужна реальная PostgreSQL/Docker приёмка |
| 49 | Telegram menu | Частично | A02 — menu устанавливается через /start, не startup; /admin неверный URL |
| 50 | UX | В основном есть | Минимальный chat UI реализован; regenerate/continue опциональны, не считать пропуском обязательного scope |
| 51 | Никакого visible thinking | В основном есть | Reasoning отделён и не показывается; сохранить это при A06/A39 и live contract tests |
| 52 | Качество кода | Частично | Type hints/modules/tests есть; A36/A39/A40 — проверки, перегруженный orchestration, lifecycle/config validation |
| 53 | Порядок реализации | Процесс не проверяется полностью | По снимку нельзя доказать запуск команд после каждого milestone; противоречия статуса: A38 |
| 54 | Subagent rules | Процесс не проверяется полностью | Отчёты есть; нельзя без истории утверждать число/порядок субагентов или отсутствие ревью |
| 55 | Не останавливаться на псевдокоде | Выполнено по форме, функционально не всё | Это реальный код, не набор critical pass-stubs. Реальные ошибки описаны A01–A40 |
| 56 | Definition of Done | Не выполнено | P1 блокеры, неполные обязательные admin/probe/integration сценарии; live-приёмка отдельно не проведена |
| 57 | Первое сообщение разработчика | Не оценивается по ZIP | Требование к прошлому общению Kimi, а не к поведению текущего приложения |

Матрица не сводится к проценту готовности: один блокер доступа, учёта или сохранности истории важнее десятков присутствующих файлов. Требование Definition of Done в целом не выполнено; полная live-приёмка дополнительно не проводилась.
