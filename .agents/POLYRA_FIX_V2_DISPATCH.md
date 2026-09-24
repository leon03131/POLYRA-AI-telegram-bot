# POLYRA FIX V2 — Dispatch Log

Дата: 2026-09-24. Среда: OpenCode (эта сессия), tool `task`, штатные агенты
`general`/`explore` с профильными scope из `.opencode/agents/polyra-*.md`
(профили установлены install_opencode_agents.py --apply; в текущей сессии
task tool не перечисляет кастомные имена → используются штатные агенты
с идентичными scope и инструкциями — зафиксировано по N02).

Git baseline: последний коммит 4e07eb0, дерево чистое, 342 теста зелёные.
Ограничение среды: PostgreSQL/Docker локально недоступны → PostgreSQL-зависимые
приёмки выполняются на VPS (176.108.245.225) либо помечаются blocked/live-only.
Git-операции общей папки — только lead. Live probes — ограниченный бюджет,
координация lead.

| Task | Agent (real) | Scope (writable) | Items | Start | End | Status |
|---|---|---|---|---|---|---|
| R-miniapp | general (task ses_f2c366a8) | отчёт revalidate.md | A01,A24,A25,A26,A28 | 2026-09-24 | 2026-09-24 | done (1st attempt failed: provider 400, retried OK) |
| R-alibaba | general (task ses_f2c469a4) | отчёт revalidate.md | A23,A27,A30,A39,N03,N04 | 2026-09-24 | 2026-09-24 | done |
| R-context | general (task ses_f2c46492) | отчёт revalidate.md | A15,A16,A17,A18,A40 | 2026-09-24 | 2026-09-24 | done |
| R-db-api | general (task ses_f2c45e79) | отчёт revalidate.md | A03,A04,A05,A08,A13,A14,A26,A29,A34,A35 + API side | 2026-09-24 | 2026-09-24 | done |

## Coding wave 1 (2026-09-24)

| Task | Agent | Writable scope | Items |
|---|---|---|---|
| W1-alibaba | general (profile polyra-alibaba) | app/llm/providers/alibaba.py; app/llm/capabilities.py; app/llm/registry.py; scripts/smoke_providers.py; tests/unit/test_alibaba_provider.py; tests/unit/test_registry.py; docs/vendor/ALIBABA.md | A23(alibaba), A27, N04 |
| W1-context | general (profile polyra-context) | app/context/**; app/memory/**; tests/unit/test_context.py; test_compactor.py; test_memory.py | A15,A16,A17,A40(memory) |
| W1-search | general (profile polyra-search-security) | app/search/**; app/security/**; app/llm/tools/**; tests/unit/test_search.py; test_ssrf.py; test_tools.py | A19,A30(search),A31,A32,A33,A12(runner side) |
| W1-db-api | general (profile polyra-db-api) | app/db/**; app/api/**; app/services/{admin,access,credentials,chats,users,settings}.py; tests/unit/test_access.py; test_api.py; test_api_auth.py | A03,A04,A05(mig),A08,A13,A14,A24-API,A25-API,A26,A29,A35,A28-API,A40(GIN) |

Lead (главный агент): app/services/generation.py, app/services/llm_factory.py,
app/llm/base.py, app/llm/events.py, app/main.py, app/config.py, app/bot/** (временно,
до wave 2), tests/unit/test_generation.py — A05, A06-consume, A07, A11, A12,
A25-runtime, A26-revoke-wiring, A30(llm_factory), A31-injection, A34, A37, N03.
