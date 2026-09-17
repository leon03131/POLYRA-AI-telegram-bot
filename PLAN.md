# PLAN — Telegram AI Assistant

Дата создания: 2026-09-18
Владелец продукта: Telegram user_id `795063564` (абсолютный owner).

## 1. Цель

Production-ready персональный AI-ассистент уровня Gemini/ChatGPT, где:

- основной интерфейс диалога — Telegram-чат (text, photo+ caption, streaming, Stop);
- вся конфигурация и админ-панель — Telegram Mini App (React+TS+Vite);
- LLM-провайдеры: Google Gemini (пул ~30 проектов с квотами и failover по ключам)
  и Alibaba Cloud Model Studio (OpenAI-compatible, direct connection);
- web_search / open_url как Function Calling tools;
- история чатов, compaction, automatic long-term memory;
- никогда не показывать reasoning пользователю;
- никогда не делать cross-model fallback.

## 2. Stack

Backend: Python 3.12+, asyncio, aiogram 3.31.x, FastAPI, Uvicorn, httpx,
openai SDK (Alibaba), google-genai (Gemini), PostgreSQL 16+, SQLAlchemy 2 async,
Alembic, Pydantic v2, pytest, Ruff, mypy.
Frontend: React, TypeScript, Vite, Telegram WebApp JS API.
Deploy: Docker, Docker Compose, Caddy/nginx, HTTPS обязателен.
Без Redis (locks/state/persistence — PostgreSQL).

## 3. Архитектура

```
Telegram Bot (aiogram, long polling; webhook — optional позже)
  -> Access Middleware (numeric user_id only)
  -> Chat Service
  -> Context Builder (system + memories + summary + recent raw + current)
  -> LLM Router (ModelRegistry, capabilities)
       -> GeminiProvider (GeminiProjectPool: rotation, quota, failover по проектам)
       -> AlibabaProvider (OpenAI-compatible, trust_env=false)
  -> Tool Engine (web_search, open_url, set_chat_title, remember, forget_memory)
  -> Persistence (PostgreSQL)

FastAPI (тот же процесс): Mini App static + REST API + auth по Telegram initData.
```

Правила изоляции: Telegram handlers не знают про HTTP провайдеров, SQL и квоты;
провайдеры не знают про Telegram; всё через сервисный слой.

## 4. Network routing

- Telegram: configurable proxy transport (env).
- Gemini: ТОЛЬКО custom proxy владельца `extraordinary-piroshki-4e3b92.netlify.app`.
- Alibaba: direct, httpx `trust_env=false`.
- Независимые httpx transports, default route машины не трогаем.

## 5. Milestones (каждый: tests -> ruff -> mypy -> fix -> TASKS.md -> commit*)

- M0 Research + vendor docs (`.agents/reports/*` -> `docs/vendor/*`)
- M1 Skeleton: pyproject, config, logging, crypto, PostgreSQL, Alembic, users/access
- M2 Telegram basic bot: access middleware, /start /new /chats /settings /help /admin, text+photo
- M3 LLM abstractions: LLMProvider/events/registry/capabilities; Alibaba + Gemini providers
- M4 Gemini project pool: rotation, quota (RPM/TPM/RPD per project+model), failover rules
- M5 Streaming + cancellation (Rich Draft -> sendMessageDraft -> throttled edit; stopped_message_generation)
- M6 Chat history + context builder + compactor (gemini-3.5-flash-lite) + auto titles
- M7 Automatic memory (extract/dedup/retrieve FTS; pgvector optional)
- M8 Tool engine + web_search (Serper primary) + open_url (SSRF-safe)
- M9 Mini App auth + user UI
- M10 Mini App admin panel (users/access/models/gemini pool/alibaba/search/system/stats/audit)
- M11 Observability, audit, provider health
- M12 Security review, full tests, Docker deploy, README

\* git-коммиты — только после подтверждения владельца (правило среды исполнения).

## 6. Subagents

Research (M0, параллельно, максимум 4): R-TG, R-GEMINI, R-ALIBABA, R-SEARCH
-> пишут только `.agents/reports/*.md`, production code не трогают.

Coding (после M0), строгое владение файлами:
- A: `app/bot/**` (aiogram, streaming, cancellation)
- B: `miniapp/**`
- C: `app/llm/providers/gemini.py`, `app/llm/gemini/**`
- D: `app/llm/providers/alibaba.py`, capability probes
- E: `app/context/**`, `app/memory/**`, `app/services/chats|messages|generation.py`
- F: `app/search/**`, `app/llm/tools/**`
- G: `app/db/**`, `alembic`, repositories
- H: tests, security review, integration review

Главный агент: архитектура, интеграция, прогон тестов/линтеров, контроль ТЗ.
