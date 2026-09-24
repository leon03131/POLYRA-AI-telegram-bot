# Реестр проверки всех файлов

Все **215 файлов** исходного ZIP включены ниже; полный хеш и размер каждого — в evidence/inventory.json. Повторная проверка SHA-256 подтверждает, что исходные файлы не исправлялись. Файлы node_modules/pycache, появившиеся во время локальных команд, не выдаются за исходники ZIP.

Таблица показывает назначение, реально доступный вид проверки и прямые привязки к замечаниям. «Нет отдельного замечания» НЕ означает сертификат безопасности/полное покрытие ветвей. Изменение общего контракта может затронуть и не перечисленных среди прямых привязок потребителей. Синтаксический анализ всех исходников дополнялся проверкой контрактов и целевыми воспроизведениями, но не заменяет полноценные integration/E2E.

| № | Файл | Строки | Назначение / предмет проверки | Проверка | Замечания | SHA-256, первые 12 |
|---|---|---:|---|---|---|---|
| 1 | `.agents/reports/alibaba.md` | 226 | Исследование/ревью агента: утверждения сверены с кодом | Текст/конфигурация/соответствие runtime | Нет отдельного замечания | `9e148cc592ff` |
| 2 | `.agents/reports/gemini.md` | 201 | Исследование/ревью агента: утверждения сверены с кодом | Текст/конфигурация/соответствие runtime | Нет отдельного замечания | `36f884d9dc68` |
| 3 | `.agents/reports/probe_20260918_123651.json` | 158 | Исторический probe JSON: список моделей, статусы, границы доказательств | JSON parse + проверка назначения | Нет отдельного замечания | `02c6c9087bdf` |
| 4 | `.agents/reports/probe_20260918_130436.json` | 155 | Исторический probe JSON: список моделей, статусы, границы доказательств | JSON parse + проверка назначения | A27, A38 | `a10708464c62` |
| 5 | `.agents/reports/search.md` | 365 | Исследование/ревью агента: утверждения сверены с кодом | Текст/конфигурация/соответствие runtime | Нет отдельного замечания | `4a2cd782bae5` |
| 6 | `.agents/reports/security_review.md` | 84 | Исследование/ревью агента: утверждения сверены с кодом | Текст/конфигурация/соответствие runtime | A38 | `315bb6ed2736` |
| 7 | `.agents/reports/telegram.md` | 164 | Исследование/ревью агента: утверждения сверены с кодом | Текст/конфигурация/соответствие runtime | Нет отдельного замечания | `a8ce2bf44774` |
| 8 | `.dockerignore` | 41 | Состав build context и исключение локальных данных | Текст/конфигурация/соответствие runtime | Нет отдельного замечания | `2e6928673a28` |
| 9 | `.env.example` | 9 | Bootstrap env, endpoints, limits, secrets placeholders | Текст/конфигурация/соответствие runtime | Нет отдельного замечания | `33b88cb5e8ea` |
| 10 | `.gitignore` | 30 | Исключение secrets/build/cache | Текст/конфигурация/соответствие runtime | Нет отдельного замечания | `ec8ee7b9644c` |
| 11 | `Caddyfile` | 6 | HTTPS reverse proxy и внешнее экспонирование | Текст/конфигурация/соответствие runtime | A37 | `390a4c6aa7ef` |
| 12 | `DECISIONS.md` | 87 | Архитектурные решения и актуальность после probes | Текст/конфигурация/соответствие runtime | A38 | `92490267a80c` |
| 13 | `Dockerfile` | 60 | Multi-stage frontend/backend, runtime user, startup | Текст/конфигурация/соответствие runtime | A37 | `b49f890efcc1` |
| 14 | `KNOWN_ISSUES.md` | 45 | Признанные ограничения и противоречия | Текст/конфигурация/соответствие runtime | A04, A19, A38 | `025ebb8b1cee` |
| 15 | `PLAN.md` | 87 | Этапы проекта и заявленный результат | Текст/конфигурация/соответствие runtime | A38 | `1b3099d0f3bb` |
| 16 | `README.md` | 263 | Deployment, probes, заявленные сценарии | Текст/конфигурация/соответствие runtime | A37, A38 | `8a79c9563233` |
| 17 | `TASKS.md` | 144 | Отметки DONE против действительных инвариантов | Текст/конфигурация/соответствие runtime | A38 | `b37834987bb8` |
| 18 | `alembic.ini` | 53 | Конфигурация запуска миграций | Текст/конфигурация/соответствие runtime | Нет отдельного замечания | `a355e586b86c` |
| 19 | `app/__init__.py` | 0 | Package/module wiring | Python syntax/compile + статический анализ | Нет отдельного замечания | `e3b0c44298fc` |
| 20 | `app/api/__init__.py` | 5 | Package/module wiring | Python syntax/compile + статический анализ | Нет отдельного замечания | `ba1de63f1324` |
| 21 | `app/api/app.py` | 68 | FastAPI mount/router/readiness | Python syntax/compile + статический анализ | A02, A28, A37 | `cbf0c565b598` |
| 22 | `app/api/auth.py` | 146 | Подпись initData, freshness, server token | Python syntax/compile + статический анализ | Нет отдельного замечания | `990bcaccb9ea` |
| 23 | `app/api/dependencies.py` | 106 | Access/owner policy на API | Python syntax/compile + статический анализ | A04 | `3a86790c4f68` |
| 24 | `app/api/routes/__init__.py` | 31 | API validation, authorization, CRUD и runtime effect | Python syntax/compile + статический анализ | Нет отдельного замечания | `d36b43316bd4` |
| 25 | `app/api/routes/admin_access.py` | 138 | API validation, authorization, CRUD и runtime effect | Python syntax/compile + статический анализ | A26 | `30235bcf8962` |
| 26 | `app/api/routes/admin_gemini.py` | 270 | API validation, authorization, CRUD и runtime effect | Python syntax/compile + статический анализ | A28, A29 | `f4c61c968cf0` |
| 27 | `app/api/routes/admin_providers.py` | 68 | API validation, authorization, CRUD и runtime effect | Python syntax/compile + статический анализ | A14, A28 | `1fedc72158b0` |
| 28 | `app/api/routes/admin_search.py` | 126 | API validation, authorization, CRUD и runtime effect | Python syntax/compile + статический анализ | A35 | `2b88eb4fdc10` |
| 29 | `app/api/routes/admin_stats.py` | 101 | API validation, authorization, CRUD и runtime effect | Python syntax/compile + статический анализ | A35 | `70d310e01e62` |
| 30 | `app/api/routes/admin_system.py` | 127 | API validation, authorization, CRUD и runtime effect | Python syntax/compile + статический анализ | A13 | `c6a5b76407b0` |
| 31 | `app/api/routes/admin_users.py` | 118 | API validation, authorization, CRUD и runtime effect | Python syntax/compile + статический анализ | Нет отдельного замечания | `b94475ef0529` |
| 32 | `app/api/routes/auth.py` | 94 | API validation, authorization, CRUD и runtime effect | Python syntax/compile + статический анализ | A04 | `41a2ec5d2c2f` |
| 33 | `app/api/routes/chats.py` | 179 | API validation, authorization, CRUD и runtime effect | Python syntax/compile + статический анализ | A01, A08, A24, A25 | `3c0b8ae970f7` |
| 34 | `app/api/routes/me.py` | 64 | API validation, authorization, CRUD и runtime effect | Python syntax/compile + статический анализ | Нет отдельного замечания | `06b06d6a291b` |
| 35 | `app/api/routes/memory.py` | 73 | API validation, authorization, CRUD и runtime effect | Python syntax/compile + статический анализ | A24 | `5effed6923a8` |
| 36 | `app/api/routes/settings.py` | 83 | API validation, authorization, CRUD и runtime effect | Python syntax/compile + статический анализ | A25 | `38a51ec4bcab` |
| 37 | `app/bot/__init__.py` | 0 | Package/module wiring | Python syntax/compile + статический анализ | Нет отдельного замечания | `e3b0c44298fc` |
| 38 | `app/bot/dispatcher.py` | 67 | aiogram defaults/middleware/router wiring | Python syntax/compile + статический анализ | A20 | `2a5ce93bbb4f` |
| 39 | `app/bot/middleware/__init__.py` | 0 | Доступ и безопасная обработка ошибок updates | Python syntax/compile + статический анализ | Нет отдельного замечания | `e3b0c44298fc` |
| 40 | `app/bot/middleware/access.py` | 105 | Доступ и безопасная обработка ошибок updates | Python syntax/compile + статический анализ | Нет отдельного замечания | `16ea8901f2b0` |
| 41 | `app/bot/middleware/errors.py` | 29 | Доступ и безопасная обработка ошибок updates | Python syntax/compile + статический анализ | Нет отдельного замечания | `b9f0b2782e35` |
| 42 | `app/bot/routers/__init__.py` | 0 | Telegram entrypoint: text/photo/commands/cancel | Python syntax/compile + статический анализ | Нет отдельного замечания | `e3b0c44298fc` |
| 43 | `app/bot/routers/chat.py` | 41 | Telegram entrypoint: text/photo/commands/cancel | Python syntax/compile + статический анализ | Нет отдельного замечания | `327abefd9c22` |
| 44 | `app/bot/routers/commands.py` | 172 | Telegram entrypoint: text/photo/commands/cancel | Python syntax/compile + статический анализ | A02, A04, A20 | `aa2fbd19a6e7` |
| 45 | `app/bot/routers/photos.py` | 74 | Telegram entrypoint: text/photo/commands/cancel | Python syntax/compile + статический анализ | A18 | `7ca21df3d27f` |
| 46 | `app/bot/routers/stop.py` | 31 | Telegram entrypoint: text/photo/commands/cancel | Python syntax/compile + статический анализ | Нет отдельного замечания | `2c692a808af3` |
| 47 | `app/bot/streaming/__init__.py` | 5 | Package/module wiring | Python syntax/compile + статический анализ | Нет отдельного замечания | `53affbff2e76` |
| 48 | `app/bot/streaming/draft.py` | 251 | Rich/plain fallback, throttling, finalization | Python syntax/compile + статический анализ | A20, A21, A22 | `86ee5ce14270` |
| 49 | `app/config.py` | 79 | Bootstrap settings и валидация | Python syntax/compile + статический анализ | A13, A32, A40 | `203423d71d41` |
| 50 | `app/context/__init__.py` | 17 | Package/module wiring | Python syntax/compile + статический анализ | Нет отдельного замечания | `6b658a991aef` |
| 51 | `app/context/builder.py` | 147 | Summary coverage, recent window, context construction | Python syntax/compile + статический анализ | A15, A16, A18 | `4a9be3854d2f` |
| 52 | `app/context/compactor.py` | 308 | JSON validation, background summary, collect_text | Python syntax/compile + статический анализ | A06, A17 | `d4fda3bde967` |
| 53 | `app/context/titles.py` | 96 | Первичный title и internal model fallback | Python syntax/compile + статический анализ | Нет отдельного замечания | `1be009e197f3` |
| 54 | `app/context/token_budget.py` | 73 | Расчёт окна и reserves | Python syntax/compile + статический анализ | A16 | `ff80d2a643d6` |
| 55 | `app/db/__init__.py` | 17 | DB engine/session/base и import wiring | Python syntax/compile + статический анализ | Нет отдельного замечания | `ea0bc19aa5d8` |
| 56 | `app/db/base.py` | 42 | DB engine/session/base и import wiring | Python syntax/compile + статический анализ | Нет отдельного замечания | `09608c025f23` |
| 57 | `app/db/migrations/env.py` | 98 | Alembic environment/template | Python syntax/compile + статический анализ | Нет отдельного замечания | `2893cdf9ce4e` |
| 58 | `app/db/migrations/script.py.mako` | 26 | Alembic environment/template | Текст/конфигурация/соответствие runtime | Нет отдельного замечания | `52c5a230257e` |
| 59 | `app/db/migrations/versions/0001_initial.py` | 142 | DDL, constraints, foreign keys и связь с runtime | Python syntax/compile + статический анализ; Включена в успешный offline SQL; без реальной PostgreSQL | Нет отдельного замечания | `583a21015935` |
| 60 | `app/db/migrations/versions/0002_chat_core.py` | 159 | DDL, constraints, foreign keys и связь с runtime | Python syntax/compile + статический анализ; Включена в успешный offline SQL; без реальной PostgreSQL | A08 | `eb7fab03585b` |
| 61 | `app/db/migrations/versions/0003_gemini_pool.py` | 125 | DDL, constraints, foreign keys и связь с runtime | Python syntax/compile + статический анализ; Включена в успешный offline SQL; без реальной PostgreSQL | Нет отдельного замечания | `abf8e9a9a842` |
| 62 | `app/db/migrations/versions/0004_credentials_quotas.py` | 100 | DDL, constraints, foreign keys и связь с runtime | Python syntax/compile + статический анализ; Включена в успешный offline SQL; без реальной PostgreSQL | Нет отдельного замечания | `6b33293eb588` |
| 63 | `app/db/migrations/versions/0005_chat_summaries.py` | 67 | DDL, constraints, foreign keys и связь с runtime | Python syntax/compile + статический анализ; Включена в успешный offline SQL; без реальной PostgreSQL | Нет отдельного замечания | `359aebf5ba43` |
| 64 | `app/db/migrations/versions/0006_memories.py` | 68 | DDL, constraints, foreign keys и связь с runtime | Python syntax/compile + статический анализ; Включена в успешный offline SQL; без реальной PostgreSQL | A40 | `577b6bcd81ea` |
| 65 | `app/db/migrations/versions/0007_search_tools.py` | 85 | DDL, constraints, foreign keys и связь с runtime | Python syntax/compile + статический анализ; Включена в успешный offline SQL; без реальной PostgreSQL | Нет отдельного замечания | `cad5a5f88c4c` |
| 66 | `app/db/migrations/versions/0008_system_audit.py` | 76 | DDL, constraints, foreign keys и связь с runtime | Python syntax/compile + статический анализ; Включена в успешный offline SQL; без реальной PostgreSQL | Нет отдельного замечания | `8e29c0c612e9` |
| 67 | `app/db/models/__init__.py` | 37 | ORM schema: ownership, UUID, relationships, constraints | Python syntax/compile + статический анализ | Нет отдельного замечания | `f816e3ad227a` |
| 68 | `app/db/models/access.py` | 83 | ORM schema: ownership, UUID, relationships, constraints | Python syntax/compile + статический анализ | Нет отдельного замечания | `a211d48d7de0` |
| 69 | `app/db/models/audit_log.py` | 36 | ORM schema: ownership, UUID, relationships, constraints | Python syntax/compile + статический анализ | Нет отдельного замечания | `30b3836aaba0` |
| 70 | `app/db/models/chat.py` | 29 | ORM schema: ownership, UUID, relationships, constraints | Python syntax/compile + статический анализ | Нет отдельного замечания | `868f8bdc1785` |
| 71 | `app/db/models/chat_summary.py` | 36 | ORM schema: ownership, UUID, relationships, constraints | Python syntax/compile + статический анализ | A15 | `11328444b969` |
| 72 | `app/db/models/credential.py` | 20 | ORM schema: ownership, UUID, relationships, constraints | Python syntax/compile + статический анализ | Нет отдельного замечания | `ae2f4896ed15` |
| 73 | `app/db/models/gemini.py` | 87 | ORM schema: ownership, UUID, relationships, constraints | Python syntax/compile + статический анализ | A10, A29 | `22b18661b6b2` |
| 74 | `app/db/models/generation_run.py` | 42 | ORM schema: ownership, UUID, relationships, constraints | Python syntax/compile + статический анализ | A05, A07, A08, A35 | `163e0694dd8c` |
| 75 | `app/db/models/memory.py` | 35 | ORM schema: ownership, UUID, relationships, constraints | Python syntax/compile + статический анализ | Нет отдельного замечания | `d09c63ba8a6a` |
| 76 | `app/db/models/message.py` | 66 | ORM schema: ownership, UUID, relationships, constraints | Python syntax/compile + статический анализ | Нет отдельного замечания | `aa9cdbba717f` |
| 77 | `app/db/models/search_config.py` | 23 | ORM schema: ownership, UUID, relationships, constraints | Python syntax/compile + статический анализ | Нет отдельного замечания | `77ecab81e690` |
| 78 | `app/db/models/system_setting.py` | 18 | ORM schema: ownership, UUID, relationships, constraints | Python syntax/compile + статический анализ | Нет отдельного замечания | `63731eb45667` |
| 79 | `app/db/models/tool_call.py` | 31 | ORM schema: ownership, UUID, relationships, constraints | Python syntax/compile + статический анализ | Нет отдельного замечания | `bf7147d2917d` |
| 80 | `app/db/models/user.py` | 26 | ORM schema: ownership, UUID, relationships, constraints | Python syntax/compile + статический анализ | Нет отдельного замечания | `735ef06b4272` |
| 81 | `app/db/repositories/__init__.py` | 43 | Queries, транзакции, pagination, persistence contracts | Python syntax/compile + статический анализ | Нет отдельного замечания | `d6405a588240` |
| 82 | `app/db/repositories/access.py` | 124 | Queries, транзакции, pagination, persistence contracts | Python syntax/compile + статический анализ | A03 | `99e7df1bc718` |
| 83 | `app/db/repositories/audit_logs.py` | 42 | Queries, транзакции, pagination, persistence contracts | Python syntax/compile + статический анализ | Нет отдельного замечания | `ae2ae02bb54c` |
| 84 | `app/db/repositories/chat_summaries.py` | 47 | Queries, транзакции, pagination, persistence contracts | Python syntax/compile + статический анализ | A17 | `2125d4b9e434` |
| 85 | `app/db/repositories/chats.py` | 99 | Queries, транзакции, pagination, persistence contracts | Python syntax/compile + статический анализ | A24 | `6d351cf429e6` |
| 86 | `app/db/repositories/credentials.py` | 61 | Queries, транзакции, pagination, persistence contracts | Python syntax/compile + статический анализ | Нет отдельного замечания | `aa4a3eb14404` |
| 87 | `app/db/repositories/gemini.py` | 306 | Queries, транзакции, pagination, persistence contracts | Python syntax/compile + статический анализ | A09 | `67b891a8e6a2` |
| 88 | `app/db/repositories/generation_runs.py` | 87 | Queries, транзакции, pagination, persistence contracts | Python syntax/compile + статический анализ | A07, A08, A34 | `ee9de9dbc359` |
| 89 | `app/db/repositories/memories.py` | 112 | Queries, транзакции, pagination, persistence contracts | Python syntax/compile + статический анализ | A40 | `5d31c8b6c539` |
| 90 | `app/db/repositories/messages.py` | 74 | Queries, транзакции, pagination, persistence contracts | Python syntax/compile + статический анализ | A18 | `5f45a953efec` |
| 91 | `app/db/repositories/search_configs.py` | 78 | Queries, транзакции, pagination, persistence contracts | Python syntax/compile + статический анализ | Нет отдельного замечания | `7bdbcf666130` |
| 92 | `app/db/repositories/system_settings.py` | 38 | Queries, транзакции, pagination, persistence contracts | Python syntax/compile + статический анализ | Нет отдельного замечания | `f638bb041fcf` |
| 93 | `app/db/repositories/tool_calls.py` | 51 | Queries, транзакции, pagination, persistence contracts | Python syntax/compile + статический анализ | Нет отдельного замечания | `81fe75d99f36` |
| 94 | `app/db/repositories/users.py` | 83 | Queries, транзакции, pagination, persistence contracts | Python syntax/compile + статический анализ | Нет отдельного замечания | `0eb0a24a480c` |
| 95 | `app/db/session.py` | 33 | DB engine/session/base и import wiring | Python syntax/compile + статический анализ | Нет отдельного замечания | `650b23899b0a` |
| 96 | `app/llm/__init__.py` | 1 | LLM abstractions, registry/capabilities/router/events | Python syntax/compile + статический анализ | Нет отдельного замечания | `88bb3bdf0661` |
| 97 | `app/llm/base.py` | 65 | LLM abstractions, registry/capabilities/router/events | Python syntax/compile + статический анализ | A39 | `fa905054b613` |
| 98 | `app/llm/capabilities.py` | 153 | LLM abstractions, registry/capabilities/router/events | Python syntax/compile + статический анализ | Нет отдельного замечания | `c47fd1ff75c0` |
| 99 | `app/llm/errors.py` | 113 | LLM abstractions, registry/capabilities/router/events | Python syntax/compile + статический анализ | Нет отдельного замечания | `4ffc51cac6e3` |
| 100 | `app/llm/events.py` | 65 | LLM abstractions, registry/capabilities/router/events | Python syntax/compile + статический анализ | Нет отдельного замечания | `3410311ea1d2` |
| 101 | `app/llm/gemini/__init__.py` | 36 | Pool/quota/failover/store consistency | Python syntax/compile + статический анализ | Нет отдельного замечания | `34d63be044a9` |
| 102 | `app/llm/gemini/pool.py` | 244 | Pool/quota/failover/store consistency | Python syntax/compile + статический анализ | A06, A10 | `0d2e36dfd4df` |
| 103 | `app/llm/gemini/quota.py` | 119 | Pool/quota/failover/store consistency | Python syntax/compile + статический анализ | A09 | `a842c7b532ee` |
| 104 | `app/llm/gemini/store_db.py` | 148 | Pool/quota/failover/store consistency | Python syntax/compile + статический анализ | A09 | `260bd5a279cc` |
| 105 | `app/llm/providers/__init__.py` | 0 | Provider request/SSE mapping, errors, tools, cancellation | Python syntax/compile + статический анализ | Нет отдельного замечания | `e3b0c44298fc` |
| 106 | `app/llm/providers/alibaba.py` | 363 | Provider request/SSE mapping, errors, tools, cancellation | Python syntax/compile + статический анализ | A06, A11, A23 | `1dba33339961` |
| 107 | `app/llm/providers/gemini.py` | 334 | Provider request/SSE mapping, errors, tools, cancellation | Python syntax/compile + статический анализ | A11, A23 | `b03776268812` |
| 108 | `app/llm/registry.py` | 55 | LLM abstractions, registry/capabilities/router/events | Python syntax/compile + статический анализ | A27 | `676177bef9a7` |
| 109 | `app/llm/router.py` | 33 | LLM abstractions, registry/capabilities/router/events | Python syntax/compile + статический анализ | Нет отдельного замечания | `56a2c9892cb9` |
| 110 | `app/llm/tools/__init__.py` | 18 | Tool definition/validation/policy/side effects/logging | Python syntax/compile + статический анализ | Нет отдельного замечания | `82bc3a863fe2` |
| 111 | `app/llm/tools/builtin.py` | 307 | Tool definition/validation/policy/side effects/logging | Python syntax/compile + статический анализ | A31, A32 | `b7a245d1c6a7` |
| 112 | `app/llm/tools/registry.py` | 79 | Tool definition/validation/policy/side effects/logging | Python syntax/compile + статический анализ | A31, A39 | `5ba9be253e45` |
| 113 | `app/llm/tools/runner.py` | 191 | Tool definition/validation/policy/side effects/logging | Python syntax/compile + статический анализ | A12, A39 | `210442274374` |
| 114 | `app/llm/tools/schemas.py` | 130 | Tool definition/validation/policy/side effects/logging | Python syntax/compile + статический анализ | Нет отдельного замечания | `cc0a26c27cb0` |
| 115 | `app/main.py` | 128 | Composition root, DI, startup/shutdown, задачи | Python syntax/compile + статический анализ | A02, A13, A30, A34, A37 | `f32f889fb4ae` |
| 116 | `app/memory/__init__.py` | 18 | Extraction/retrieval/dedup/user isolation | Python syntax/compile + статический анализ | Нет отдельного замечания | `2238bef75e45` |
| 117 | `app/memory/deduplicator.py` | 37 | Extraction/retrieval/dedup/user isolation | Python syntax/compile + статический анализ | A40 | `e01d70cfdbb8` |
| 118 | `app/memory/extractor.py` | 285 | Extraction/retrieval/dedup/user isolation | Python syntax/compile + статический анализ | Нет отдельного замечания | `cf00dfa69166` |
| 119 | `app/memory/normalizer.py` | 18 | Extraction/retrieval/dedup/user isolation | Python syntax/compile + статический анализ | Нет отдельного замечания | `0e593bbf9f70` |
| 120 | `app/memory/retriever.py` | 94 | Extraction/retrieval/dedup/user isolation | Python syntax/compile + статический анализ | Нет отдельного замечания | `b2943f26eb70` |
| 121 | `app/observability/__init__.py` | 0 | Структурирование/редакция логов | Python syntax/compile + статический анализ | Нет отдельного замечания | `e3b0c44298fc` |
| 122 | `app/observability/logging.py` | 70 | Структурирование/редакция логов | Python syntax/compile + статический анализ | A35 | `cf00ab9b33b3` |
| 123 | `app/search/__init__.py` | 40 | Search backend/fallback/Reader/ошибки | Python syntax/compile + статический анализ | Нет отдельного замечания | `8092ee5e2b35` |
| 124 | `app/search/base.py` | 149 | Search backend/fallback/Reader/ошибки | Python syntax/compile + статический анализ | Нет отдельного замечания | `e45068e94f16` |
| 125 | `app/search/brave.py` | 78 | Search backend/fallback/Reader/ошибки | Python syntax/compile + статический анализ | A30, A33 | `d2f5975a8d5b` |
| 126 | `app/search/fetcher.py` | 163 | Fetch/redirect/bytes/content extraction/Reader | Python syntax/compile + статический анализ | A19, A31 | `216b73b4e493` |
| 127 | `app/search/jina.py` | 140 | Search backend/fallback/Reader/ошибки | Python syntax/compile + статический анализ | A30, A31 | `39b9f0c76555` |
| 128 | `app/search/manager.py` | 217 | Search backend/fallback/Reader/ошибки | Python syntax/compile + статический анализ | A30, A32, A33 | `5632540d2127` |
| 129 | `app/search/playwright_google.py` | 104 | Search backend/fallback/Reader/ошибки | Python syntax/compile + статический анализ | A33 | `398644bb5505` |
| 130 | `app/search/reader.py` | 84 | Search backend/fallback/Reader/ошибки | Python syntax/compile + статический анализ | Нет отдельного замечания | `57a811a98e88` |
| 131 | `app/search/serpapi_ai_overview.py` | 161 | Search backend/fallback/Reader/ошибки | Python syntax/compile + статический анализ | A32 | `a6b902edd15f` |
| 132 | `app/search/serper.py` | 73 | Search backend/fallback/Reader/ошибки | Python syntax/compile + статический анализ | A30, A33 | `7e0dc865a00d` |
| 133 | `app/security/__init__.py` | 0 | Package/module wiring | Python syntax/compile + статический анализ | Нет отдельного замечания | `e3b0c44298fc` |
| 134 | `app/security/crypto.py` | 54 | Authenticated encryption, masking, token helpers | Python syntax/compile + статический анализ | Нет отдельного замечания | `0ade5ddfc52a` |
| 135 | `app/security/ssrf.py` | 71 | URL/IP/DNS validation и threat model | Python syntax/compile + статический анализ | A19 | `31372c032dd8` |
| 136 | `app/services/__init__.py` | 0 | Business logic и межмодульные контракты | Python syntax/compile + статический анализ | Нет отдельного замечания | `e3b0c44298fc` |
| 137 | `app/services/access.py` | 114 | Business logic и межмодульные контракты | Python syntax/compile + статический анализ | A03 | `1c5a8f2294a5` |
| 138 | `app/services/admin.py` | 246 | Business logic и межмодульные контракты | Python syntax/compile + статический анализ | A03, A26 | `abe5ae924015` |
| 139 | `app/services/chats.py` | 117 | Business logic и межмодульные контракты | Python syntax/compile + статический анализ | A18 | `b9fd48287d94` |
| 140 | `app/services/credentials.py` | 23 | Business logic и межмодульные контракты | Python syntax/compile + статический анализ | A14 | `7a7dca28c705` |
| 141 | `app/services/generation.py` | 934 | Полная цепочка generation/context/tools/usage/Stop | Python syntax/compile + статический анализ | A05, A06, A07, A11, A12, A13, A15, A16, A17, A18, A20, A21, A23, A25, A26, A31, A32, A34, A39 | `fb6922c65e3f` |
| 142 | `app/services/llm_factory.py` | 63 | Маршрутизация и lifecycle HTTP clients | Python syntax/compile + статический анализ | A14, A30 | `f661c72523be` |
| 143 | `docker-compose.yml` | 74 | Сервисы, volumes, healthchecks, порты | Текст/конфигурация/соответствие runtime | A37 | `4099535fee1d` |
| 144 | `docs/API.md` | 96 | Описание API и функций | Текст/конфигурация/соответствие runtime | A38 | `55f66ae5c3b1` |
| 145 | `docs/vendor/AIOGRAM.md` | 40 | Локальная vendor documentation: параметры и актуальность | Текст/конфигурация/соответствие runtime | Нет отдельного замечания | `6b8e24c9b3f5` |
| 146 | `docs/vendor/ALIBABA.md` | 65 | Локальная vendor documentation: параметры и актуальность | Текст/конфигурация/соответствие runtime | A38 | `4442cbcc36ae` |
| 147 | `docs/vendor/GEMINI.md` | 60 | Локальная vendor documentation: параметры и актуальность | Текст/конфигурация/соответствие runtime | Нет отдельного замечания | `5daedbb494e3` |
| 148 | `docs/vendor/SEARCH_BACKENDS.md` | 52 | Локальная vendor documentation: параметры и актуальность | Текст/конфигурация/соответствие runtime | Нет отдельного замечания | `2f4bdc82b888` |
| 149 | `docs/vendor/TELEGRAM_BOT_API.md` | 42 | Локальная vendor documentation: параметры и актуальность | Текст/конфигурация/соответствие runtime | Нет отдельного замечания | `4188ea568098` |
| 150 | `docs/vendor/TELEGRAM_MINI_APPS.md` | 46 | Локальная vendor documentation: параметры и актуальность | Текст/конфигурация/соответствие runtime | Нет отдельного замечания | `aa412655a57e` |
| 151 | `miniapp/.gitignore` | 3 | Frontend configuration | Текст/конфигурация/соответствие runtime | Нет отдельного замечания | `d7aa963f7f0e` |
| 152 | `miniapp/index.html` | 16 | Bootstrap Mini App JS SDK | Текст/конфигурация/соответствие runtime | Нет отдельного замечания | `d62855bfd74e` |
| 153 | `miniapp/package-lock.json` | 1861 | Lockfile JSON; package metadata, не аудит кода npm-зависимостей | JSON parse + проверка назначения | Нет отдельного замечания | `3ef6857dd34a` |
| 154 | `miniapp/package.json` | 25 | React/Vite/TS scripts и зависимости | JSON parse + проверка назначения | A36 | `6a08170fad5d` |
| 155 | `miniapp/src/App.tsx` | 155 | HashRouter, admin navigation, Telegram BackButton | TS/TSX syntax + статический контракт; без build/E2E | A02 | `53e6131e5f25` |
| 156 | `miniapp/src/admin/AdminAuditPage.tsx` | 83 | Admin UI, API mutations, ownership и реальная полнота | TS/TSX syntax + статический контракт; без build/E2E | A35 | `877ff80262a5` |
| 157 | `miniapp/src/admin/AdminDashboardPage.tsx` | 75 | Admin UI, API mutations, ownership и реальная полнота | TS/TSX syntax + статический контракт; без build/E2E | A35 | `07514d121346` |
| 158 | `miniapp/src/admin/AdminGeminiPage.tsx` | 347 | Admin UI, API mutations, ownership и реальная полнота | TS/TSX syntax + статический контракт; без build/E2E | A28 | `bd1250fac5da` |
| 159 | `miniapp/src/admin/AdminLayout.tsx` | 36 | Admin UI, API mutations, ownership и реальная полнота | TS/TSX syntax + статический контракт; без build/E2E | A28 | `84338095bf01` |
| 160 | `miniapp/src/admin/AdminProvidersPage.tsx` | 99 | Admin UI, API mutations, ownership и реальная полнота | TS/TSX syntax + статический контракт; без build/E2E | A28 | `ba12e27e18af` |
| 161 | `miniapp/src/admin/AdminSearchPage.tsx` | 167 | Admin UI, API mutations, ownership и реальная полнота | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `9e3714c22fe0` |
| 162 | `miniapp/src/admin/AdminSystemPage.tsx` | 147 | Admin UI, API mutations, ownership и реальная полнота | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `c12311347287` |
| 163 | `miniapp/src/admin/AdminUsersPage.tsx` | 481 | Admin UI, API mutations, ownership и реальная полнота | TS/TSX syntax + статический контракт; без build/E2E | A03, A26 | `6337807cedff` |
| 164 | `miniapp/src/api/auth.tsx` | 105 | InitData session/bootstrap и состояние доступа | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `0ccee461ee70` |
| 165 | `miniapp/src/api/client.ts` | 126 | HTTP headers/errors/requests | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `b8c3acd9b537` |
| 166 | `miniapp/src/api/hooks.ts` | 114 | Query cache и mutations | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `46ccd70b3396` |
| 167 | `miniapp/src/api/types.ts` | 230 | Соответствие API response/request типов, UUID | TS/TSX syntax + статический контракт; без build/E2E | A01 | `6c79d5f74a0f` |
| 168 | `miniapp/src/components/Button.tsx` | 28 | UI controls/forms, значения и accessibility | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `1d3a73301039` |
| 169 | `miniapp/src/components/ChatSettingsForm.tsx` | 100 | UI controls/forms, значения и accessibility | TS/TSX syntax + статический контракт; без build/E2E | A25 | `17b4cbef33b5` |
| 170 | `miniapp/src/components/Chip.tsx` | 12 | UI controls/forms, значения и accessibility | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `0bbe98359a5d` |
| 171 | `miniapp/src/components/ConfirmDialog.tsx` | 41 | UI controls/forms, значения и accessibility | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `8ca2fcf4de81` |
| 172 | `miniapp/src/components/EmptyState.tsx` | 17 | UI controls/forms, значения и accessibility | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `fb918b29cb52` |
| 173 | `miniapp/src/components/Input.tsx` | 11 | UI controls/forms, значения и accessibility | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `d286423a206c` |
| 174 | `miniapp/src/components/List.tsx` | 39 | UI controls/forms, значения и accessibility | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `1f792139a27b` |
| 175 | `miniapp/src/components/Modal.tsx` | 39 | UI controls/forms, значения и accessibility | TS/TSX syntax + статический контракт; без build/E2E | A40 | `57f754fe4c8f` |
| 176 | `miniapp/src/components/Section.tsx` | 18 | UI controls/forms, значения и accessibility | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `8ab03524ffec` |
| 177 | `miniapp/src/components/Select.tsx` | 28 | UI controls/forms, значения и accessibility | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `a929327caa5d` |
| 178 | `miniapp/src/components/Spinner.tsx` | 10 | UI controls/forms, значения и accessibility | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `148350375a9a` |
| 179 | `miniapp/src/components/Toggle.tsx` | 21 | UI controls/forms, значения и accessibility | TS/TSX syntax + статический контракт; без build/E2E | A40 | `c957d233d9c8` |
| 180 | `miniapp/src/components/index.ts` | 13 | UI controls/forms, значения и accessibility | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `4469e665cdee` |
| 181 | `miniapp/src/main.tsx` | 14 | Frontend bootstrap/helpers | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `9a0f13ca9b0d` |
| 182 | `miniapp/src/pages/ChatSettingsPage.tsx` | 185 | User UI и контракт с API/settings/current chat | TS/TSX syntax + статический контракт; без build/E2E | A01, A25 | `4ad02a0ff03d` |
| 183 | `miniapp/src/pages/ChatsPage.tsx` | 252 | User UI и контракт с API/settings/current chat | TS/TSX syntax + статический контракт; без build/E2E | A24 | `cfb4a5f3b458` |
| 184 | `miniapp/src/pages/HomePage.tsx` | 93 | User UI и контракт с API/settings/current chat | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `1d9395dcc460` |
| 185 | `miniapp/src/pages/MemoryPage.tsx` | 185 | User UI и контракт с API/settings/current chat | TS/TSX syntax + статический контракт; без build/E2E | A24 | `4892ea4d0cb7` |
| 186 | `miniapp/src/pages/SettingsPage.tsx` | 148 | User UI и контракт с API/settings/current chat | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `001884bb73fa` |
| 187 | `miniapp/src/styles.css` | 737 | Темизация, mobile layout, controls | Текст/конфигурация/соответствие runtime | Нет отдельного замечания | `98e4d3b46fde` |
| 188 | `miniapp/src/telegram/telegram-web-app.d.ts` | 62 | Telegram JS integration и declarations | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `5418e36da63d` |
| 189 | `miniapp/src/telegram/webapp.ts` | 88 | Telegram JS integration и declarations | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `97215d7af32d` |
| 190 | `miniapp/src/utils.ts` | 99 | Frontend bootstrap/helpers | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `f279d2d5a4b0` |
| 191 | `miniapp/tsconfig.json` | 23 | TypeScript project options | JSON parse + проверка назначения | Нет отдельного замечания | `5fc8ad5c6954` |
| 192 | `miniapp/vite.config.ts` | 18 | Frontend build configuration | TS/TSX syntax + статический контракт; без build/E2E | Нет отдельного замечания | `cbfa30dc9ac2` |
| 193 | `pyproject.toml` | 69 | Python зависимости, Ruff/mypy/pytest конфигурация | TOML parse + настройки зависимостей/проверок | A36, A40 | `b6a7c7b66134` |
| 194 | `scripts/import_gemini_keys.py` | 102 | Key import/dedup/project identity | Python syntax/compile + статический анализ | A29 | `bdc3cf50bbd7` |
| 195 | `scripts/smoke_providers.py` | 697 | Покрытие моделей и критерии реального probe success | Python syntax/compile + статический анализ | A27 | `aa44dcbf41eb` |
| 196 | `tests/__init__.py` | 0 | Тестовые assertions, fixtures и соответствие ТЗ | Python syntax/compile + статический анализ | Нет отдельного замечания | `e3b0c44298fc` |
| 197 | `tests/unit/__init__.py` | 0 | Тестовые assertions, fixtures и соответствие ТЗ | Python syntax/compile + статический анализ | Нет отдельного замечания | `e3b0c44298fc` |
| 198 | `tests/unit/test_access.py` | 326 | Тестовые assertions, fixtures и соответствие ТЗ | Python syntax/compile + статический анализ; В доступном pytest-запуске; 21 определений test-функций | Нет отдельного замечания | `76f37ce33e0c` |
| 199 | `tests/unit/test_alibaba_provider.py` | 362 | Тестовые assertions, fixtures и соответствие ТЗ | Python syntax/compile + статический анализ; В доступном pytest-запуске; 13 определений test-функций | A36 | `4558d26241be` |
| 200 | `tests/unit/test_api.py` | 207 | Тестовые assertions, fixtures и соответствие ТЗ | Python syntax/compile + статический анализ; В доступном pytest-запуске; 9 определений test-функций | A36 | `86f014d8e23c` |
| 201 | `tests/unit/test_api_auth.py` | 138 | Тестовые assertions, fixtures и соответствие ТЗ | Python syntax/compile + статический анализ; В доступном pytest-запуске; 14 определений test-функций | Нет отдельного замечания | `48c12f324802` |
| 202 | `tests/unit/test_bot_helpers.py` | 29 | Тестовые assertions, fixtures и соответствие ТЗ | Python syntax/compile + статический анализ; Не собран без aiogram; 4 определений test-функций | Нет отдельного замечания | `07cb83a766ab` |
| 203 | `tests/unit/test_bot_wiring.py` | 77 | Тестовые assertions, fixtures и соответствие ТЗ | Python syntax/compile + статический анализ; Не собран без aiogram; 3 определений test-функций | Нет отдельного замечания | `76ba7c2ec3bb` |
| 204 | `tests/unit/test_compactor.py` | 365 | Тестовые assertions, fixtures и соответствие ТЗ | Python syntax/compile + статический анализ; В доступном pytest-запуске; 14 определений test-функций | Нет отдельного замечания | `043ea6c5296d` |
| 205 | `tests/unit/test_context.py` | 219 | Тестовые assertions, fixtures и соответствие ТЗ | Python syntax/compile + статический анализ; В доступном pytest-запуске; 14 определений test-функций | A36 | `ca838abf67f4` |
| 206 | `tests/unit/test_crypto.py` | 46 | Тестовые assertions, fixtures и соответствие ТЗ | Python syntax/compile + статический анализ; В доступном pytest-запуске; 7 определений test-функций | Нет отдельного замечания | `78406764510e` |
| 207 | `tests/unit/test_draft_streamer.py` | 297 | Тестовые assertions, fixtures и соответствие ТЗ | Python syntax/compile + статический анализ; Не собран без aiogram; 18 определений test-функций | A22 | `9d89897805a9` |
| 208 | `tests/unit/test_gemini_pool.py` | 471 | Тестовые assertions, fixtures и соответствие ТЗ | Python syntax/compile + статический анализ; В доступном pytest-запуске; 21 определений test-функций | A36 | `09ff9923d675` |
| 209 | `tests/unit/test_gemini_provider.py` | 389 | Тестовые assertions, fixtures и соответствие ТЗ | Python syntax/compile + статический анализ; В доступном pytest-запуске; 21 определений test-функций | Нет отдельного замечания | `68c61696a272` |
| 210 | `tests/unit/test_generation.py` | 600 | Тестовые assertions, fixtures и соответствие ТЗ | Python syntax/compile + статический анализ; Не собран без aiogram; 26 определений test-функций | A36 | `d1684fef73f6` |
| 211 | `tests/unit/test_memory.py` | 519 | Тестовые assertions, fixtures и соответствие ТЗ | Python syntax/compile + статический анализ; В доступном pytest-запуске; 22 определений test-функций | Нет отдельного замечания | `bbf73e25669c` |
| 212 | `tests/unit/test_registry.py` | 59 | Тестовые assertions, fixtures и соответствие ТЗ | Python syntax/compile + статический анализ; В доступном pytest-запуске; 6 определений test-функций | Нет отдельного замечания | `60591c69c7bb` |
| 213 | `tests/unit/test_search.py` | 605 | Тестовые assertions, fixtures и соответствие ТЗ | Python syntax/compile + статический анализ; В доступном pytest-запуске; 32 определений test-функций | Нет отдельного замечания | `9442887998f5` |
| 214 | `tests/unit/test_ssrf.py` | 93 | Тестовые assertions, fixtures и соответствие ТЗ | Python syntax/compile + статический анализ; В доступном pytest-запуске; 8 определений test-функций | Нет отдельного замечания | `55584c63ab4f` |
| 215 | `tests/unit/test_tools.py` | 652 | Тестовые assertions, fixtures и соответствие ТЗ | Python syntax/compile + статический анализ; В доступном pytest-запуске; 37 определений test-функций | Нет отдельного замечания | `283cd99d2feb` |
