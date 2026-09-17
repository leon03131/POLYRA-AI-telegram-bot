# TASKS — трекер

Легенда: [ ] todo, [~] in progress, [x] done. Дата старта: 2026-09-18.

## M0 — Research + vendor docs

- [x] R-TG: Bot API 10.3 подтверждён, aiogram 3.31.0 покрывает drafts/rich/stop нативно
- [x] R-GEMINI: все 4 модели подтверждены; thinking_level; raw httpx адаптер (base_url риск)
- [x] R-ALIBABA: все 5 моделей подтверждены; маппинг thinking; противоречия зафиксированы -> probe
- [x] R-SEARCH: Serper primary, SerpApi AIO 2-step, Brave, Jina; таймауты/маппинг
- [x] docs/vendor/TELEGRAM_BOT_API.md
- [x] docs/vendor/TELEGRAM_MINI_APPS.md
- [x] docs/vendor/AIOGRAM.md
- [x] docs/vendor/GEMINI.md
- [x] docs/vendor/ALIBABA.md
- [x] docs/vendor/SEARCH_BACKENDS.md

## M1 — Skeleton + users/access  ✅ DONE (2026-09-18)

- [x] pyproject.toml (deps, ruff, mypy, build-system), .env.example, .gitignore
- [x] app/config.py (pydantic-settings), app/observability/logging.py (redaction)
- [x] app/security/crypto.py (Fernet CryptoBox + mask_secret)
- [x] app/db: base/session, модели users/access_grants/user_model_permissions/user_settings,
      Alembic + миграция 0001
- [x] repositories (users/access) + services/access.py (чистая логика)
- [x] tests: test_access.py (18), test_crypto.py (7) — 25+ тестов зелёные
- Гейты: ruff ✓, mypy strict ✓, pytest ✓

## M2 — Telegram basic bot + chat persistence  ✅ DONE (2026-09-18)

- [x] Модели chats/messages/message_parts/generation_runs + миграция 0002 (alembic heads ✓)
- [x] ChatRepository, MessageRepository; app/services/chats.py (ChatService, current chat)
- [x] AccessMiddleware (numeric id, owner=795063564, GrantView/evaluate_access, session-per-update)
- [x] Errors handler (без утечки traceback пользователю)
- [x] /start (menu button ⚙️ Настройки), /help, /new, /chats (inline switch), /settings, /admin (owner)
- [x] chat router (text → save, заглушка M3), photos router (file_id+metadata, лимит 15 МБ, M3)
- [x] stop router (stopped_message_generation зарегистрирован, отмена — M5)
- [x] app/main.py: long polling, delete_webhook, setup commands
- [x] tests: test_bot_helpers.py (+4) — всего 32 зелёные; ruff ✓, mypy ✓ (42 файла)
- Исправлено при интеграции: shadowing `text` в MessagePart (sql_text), alembic.ini → ASCII

## M3 — LLM abstractions + providers  ✅ DONE (2026-09-18)

- [x] app/llm: base/events/errors (единая таксономия ProviderError)/capabilities/registry/router
- [x] ModelRegistry: 9 моделей из ТЗ, thinking_modes, internal_only для 3.5-flash-lite
- [x] providers/gemini.py: raw httpx SSE :streamGenerateContent, thought→ReasoningDelta,
      thoughtSignature capture, usage, safety, 429 RetryInfo.retry_after
- [x] providers/alibaba.py: OpenAI-compatible, trust_env=false, thinking-таблица по 5 моделям,
      tool_calls accumulation, include_usage, bounded retry (2 попытки до первого события)
- [x] tests: 18 gemini + 19 alibaba + 16 registry/access — 85 всего; ruff ✓ mypy ✓
- Интеграционные правки: рефакторинг SSE-парсеров (C901), фиксы тестовых helpers

## M4 — Gemini project pool  ✅ DONE (2026-09-18)

- [x] Модели gemini_projects/quota_policies/quota_minute_usage/quota_daily_usage + миграция 0003
- [x] repositories/gemini.py (projects CRUD, move, health, cooldown, quota pg_insert ON CONFLICT)
- [x] llm/gemini/quota.py (UTC minute window, Pacific day, QuotaTracker) + pool.py
      (round-robin, cooldowns по ADR-005, stream_with_failover, PoolExhaustedError)
- [x] llm/gemini/store_db.py (DB-адаптеры ProjectStore/QuotaStore, session_factory)
- [x] tzdata добавлена (Windows ZoneInfo)
- [x] tests: 19 pool-тестов (все правила ADR-005 + race + cancel) — 104 всего; ruff/mypy ✓

## M5 — streaming + cancellation  ✅ DONE (2026-09-18)

- [x] bot/streaming/draft.py: DraftStreamer (rich→plain→edit fallback, throttle, sanitize, tail-режим)
- [x] services/generation.py: GenerationService + GenerationRegistry + user_error_message
- [x] provider_credentials + миграция 0004 + seed quota_policies (4/249999/19 для 3.8/3.7/3.6)
- [x] credentials service (DB → env fallback), llm_factory (gemini pool / alibaba cached)
- [x] wiring: chat/photos → реальная генерация; stop.py → cancellation по (chat,draft)
- [x] AccessMiddleware refactor: сессия БД закрывается ДО хендлера (долгие генерации)
- [x] scripts/import_gemini_keys.py (--file/--keys, --dry-run)
- [x] tests: 16 draft + 13 generation + 3 wiring — 147 всего; ruff ✓ mypy ✓ (76 файлов)

## M6 — context builder + compactor + titles  ✅ DONE (2026-09-18)

- [x] TokenBudgetManager (estimate chars/token, reserve output, safety margin, image=1032)
- [x] context/builder.py (SYSTEM + memories + summary + recent raw; current — у вызывающего)
- [x] context/compactor.py (gemini-3.5-flash-lite, structured JSON summary, 1 repair)
- [x] chat_summaries таблица + миграция 0005 + ChatSummaryRepository + MessageRepository.list_all
- [x] auto title: background TitleGenerator + sanitize_title (tool set_chat_title — в M8)
- [x] integration: GenerationService (builder в _prepare, фон compaction/title), main.py wiring
- [x] tests: test_context.py + test_compactor.py (recent preserved, budget, covered_until, repair)
- [x] прогон главным агентом: 184 теста ✓, ruff ✓, mypy ✓ (86 файлов), alembic head 0005 ✓
- Исправлено при интеграции: str.format с JSON-скобками в title prompt (KeyError)

## M7 — automatic memory  ✅ DONE (2026-09-18)

- [x] memories таблица + миграция 0006; MemoryRepository (FTS, изоляция по user_id)
- [x] memory/{normalizer,deduplicator,retriever,extractor}: 3.5-flash-lite MEDIUM,
      exact/near-dup dedup, FTS fallback на топ-важные, Protocol для будущего pgvector
- [x] wiring в generation (retrieval в builder, extraction фоном); флаги can_use_memory
- [x] tests: 24 memory-теста — 211 всего; ruff/mypy ✓

## M8 — tool engine + web_search + open_url  ✅ DONE (2026-09-18)

- [x] llm/tools: registry/runner/schemas (своя JSON Schema валидация), 5 builtin tools
- [x] search/: base + Serper/Brave/SerpApi AIO/Jina/Playwright(experimental), manager
      с fallback, modes normal/ai_overview/auto; fetcher с SSRF (redirect-into-private блок)
- [x] search_backend_configs + tool_calls + миграция 0007; ToolCallRecord + репозиторий
- [x] Tool loop в GenerationService (max 8 итераций, per-chat cancel работает),
      citations «Источники» в финале; tool parts в истории сообщений
- [x] tests: 28 search + 12 ssrf + 30+ tools + 6 tool-loop — 307 всего; ruff/mypy ✓

## M9/M10 — Mini App + Admin panel  ✅ DONE (2026-09-18)

- [x] FastAPI backend: initData auth (HMAC + auth_date), HMAC session tokens,
      deps (401/403), все роуты по docs/API.md, /health, static miniapp
- [x] Модели system_settings + audit_log (миграция 0008); services/admin.py с аудитом
- [x] miniapp/: React+TS+Vite (HashRouter, theme vars, BackButton), user UI
      (Home/Chats/ChatSettings/Settings/Memory) + admin UI (Dashboard/Users/Gemini Pool/
      Providers/Search/System/Audit); npm build ✓ (264 kB JS)
- [x] main.py: bot polling + uvicorn Mini App API в одном процессе
- [x] tests: api_auth (initData valid/invalid/stale, session tokens) + api smoke — 334 всего

## M11-M12 — observability + security review + Docker + README  ✅ DONE (2026-09-18)

- [x] scripts/smoke_providers.py (Gemini: text/thinking levels/image/error body; Alibaba:
      text/thinking acceptance/FC/image/Kimi DTL raw; отчёт JSON + рекомендации UI уровней)
- [x] Security review (0 critical/high; 4 medium → все исправлены: redaction в форматтере
      и extra-поля, per-user лимиты enforced, auth_date из будущего отклонён,
      session-signing key отделён от master, фото size после скачивания, open_url
      untrusted-маркеры + system prompt)
- [x] Dockerfile (multi-stage node→python, non-root, alembic upgrade head в CMD),
      docker-compose.yml (app+postgres+caddy profile), Caddyfile, .dockerignore
- [x] README.md (deploy, BotFather, env, dev, безопасность)
- [x] Финальные гейты: 334 теста ✓, ruff ✓, mypy strict ✓ (145 файлов), alembic 0008 ✓
- Осталось на владельце: живой запуск с ключами (smoke_providers), `alembic upgrade head`
  против продовой PostgreSQL, BotFather Main Mini App, наблюдение за rate limits драфтов.

## Бэклог (future, не scope)

- audio/video/documents parts; export чатов; pgvector embeddings; webhook режим;
  Playwright search (experimental); Kimi Dynamic Tool Loading (после probe).

- [ ] memories таблица + миграция 0006 (text/normalized/category/importance/source/embedding?)
- [ ] memory/extractor.py (3.5-flash-lite MEDIUM, JSON, dedupe)
- [ ] memory/retriever.py (PostgreSQL FTS fallback; pgvector — optional позже)
- [ ] memory/deduplicator.py (normalize + near-dup Jaccard)
- [ ] wiring в generation: retrieval в context, extraction в фоне
- [ ] tests: extraction, dedup, deletion, disabled memory, изоляция по user_id

## M8..M12

См. PLAN.md §5. Детализация добавляется перед стартом каждого milestone.
